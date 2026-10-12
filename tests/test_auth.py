import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import auth
import server


class PasswordTests(unittest.TestCase):
    def test_hash_is_salted_and_verified(self):
        first = auth.hash_password("correct horse", iterations=1000)
        second = auth.hash_password("correct horse", iterations=1000)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("pbkdf2_sha256$1000$"))
        self.assertTrue(auth.verify_password("correct horse", first))
        self.assertFalse(auth.verify_password("correct horsf", first))
        self.assertFalse(auth.verify_password("", first))
        self.assertNotIn("correct horse", first)

    def test_corrupt_or_hostile_stored_values_never_verify(self):
        for stored in ("", "nonsense", "a$b$c", "pbkdf2_sha256$x$AA==$AA==", "md5$1000$AA==$AA==",
                       f"pbkdf2_sha256${auth.MAX_ITERATIONS + 1}$AA==$AA==", "pbkdf2_sha256$0$AA==$AA=="):
            self.assertFalse(auth.verify_password("whatever", stored), stored)
        self.assertFalse(auth.verify_password("x" * (auth.MAX_LENGTH + 1), auth.hash_password("x", iterations=1000)))


class SessionTests(unittest.TestCase):
    def test_a_session_is_valid_until_it_expires_and_cannot_be_forged(self):
        secret = auth.new_secret()
        cookie = auth.make_session(secret, now=1000, ttl=60)
        self.assertTrue(auth.session_valid(secret, cookie, now=1030))
        self.assertFalse(auth.session_valid(secret, cookie, now=1061))
        self.assertFalse(auth.session_valid(auth.new_secret(), cookie, now=1030), "another secret ends all sessions")
        expires, nonce, signature = cookie.split(".")
        self.assertFalse(auth.session_valid(secret, f"{int(expires) + 999999}.{nonce}.{signature}", now=1030))
        for bad in ("", "a.b.c", "1.2", "1.2.3.4", None):
            self.assertFalse(auth.session_valid(secret, bad, now=1))
        self.assertFalse(auth.session_valid("", cookie, now=1030))

    def test_cookie_header_round_trip_and_flags(self):
        header = auth.set_cookie_header("abc.def.ghi", secure=True)
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/", "Secure"):
            self.assertIn(flag, header)
        self.assertNotIn("Secure", auth.set_cookie_header("v"))
        self.assertIn("Max-Age=0", auth.clear_cookie_header())
        self.assertEqual(auth.cookie_from_header("other=1; subsynq_session=abc.def.ghi; x=2"), "abc.def.ghi")
        self.assertEqual(auth.cookie_from_header("garbage;;=="), "")
        self.assertEqual(auth.cookie_from_header(None), "")

    def test_next_is_always_a_path_on_this_site(self):
        self.assertEqual(auth.safe_next("/library?kind=movie"), "/library?kind=movie")
        for bad in ("//evil.example", "https://evil.example", "/\\evil", "library", "/a\nb", "/" + "x" * 600):
            self.assertEqual(auth.safe_next(bad), "/", bad)
        self.assertEqual(auth.safe_next(None), "/")


class LimiterTests(unittest.TestCase):
    def test_lock_after_too_many_failures_and_release_after_the_time(self):
        clock = [1000.0]
        limiter = auth.LoginLimiter(limit=3, window=100, lock=60, now=lambda: clock[0])
        for _ in range(2):
            limiter.failure("a")
        self.assertEqual(limiter.blocked("a"), 0)
        limiter.failure("a")
        self.assertGreater(limiter.blocked("a"), 0)
        self.assertEqual(limiter.blocked("b"), 0, "another client is not affected")
        clock[0] += 61
        self.assertEqual(limiter.blocked("a"), 0)
        limiter.failure("a")
        limiter.success("a")
        limiter.failure("a")
        limiter.failure("a")
        self.assertEqual(limiter.blocked("a"), 0, "a success clears the count")

    def test_old_failures_expire(self):
        clock = [0.0]
        limiter = auth.LoginLimiter(limit=3, window=100, lock=60, now=lambda: clock[0])
        limiter.failure("a")
        limiter.failure("a")
        clock[0] = 500
        limiter.failure("a")
        self.assertEqual(limiter.blocked("a"), 0)


class LoginFlowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = Path(self.folder.name)
        patchers = [mock.patch.object(server, "CONFIG", config), mock.patch.object(server, "DATABASE", config / "subsynq.db"),
                    mock.patch.object(server, "LOGIN_LIMITER", auth.LoginLimiter()),
                    mock.patch.object(auth, "ITERATIONS", 1000)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.cookie = ""

    def request(self, method, path, fields=None, cookie=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=15)
        sent = dict(headers or {})
        use = self.cookie if cookie is None else cookie
        if use:
            sent["Cookie"] = f"{auth.COOKIE_NAME}={use}"
        body = None
        if fields is not None:
            body = urllib.parse.urlencode(fields, doseq=True)
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body, sent)
        response = connection.getresponse()
        data = response.read().decode("utf-8", "replace")
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def post(self, path, fields, **kwargs):
        return self.request("POST", path, {"token": server.SCAN_TOKEN, **fields}, **kwargs)

    def take_cookie(self, headers):
        value = auth.cookie_from_header(headers.get("Set-Cookie", ""))
        return value

    def turn_on(self, password="hemligt lösenord"):
        status, headers, _ = self.post("/settings/security", {"action": "set", "new_password": password,
                                                              "repeat_password": password})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/settings/security?saved=set")
        self.cookie = self.take_cookie(headers)
        self.assertTrue(self.cookie)

    def test_without_a_password_nothing_changes(self):
        self.assertEqual(self.request("GET", "/library")[0], 200)
        self.assertEqual(self.request("GET", "/api/status")[0], 200)
        status, headers, _ = self.request("GET", "/login")
        self.assertEqual((status, headers["Location"]), (303, "/"))
        self.assertIn("Inloggning är AV", self.request("GET", "/settings/security")[2])

    def test_with_a_password_every_page_and_api_needs_the_login_but_health_and_webhooks_do_not(self):
        self.turn_on()
        anonymous = {"cookie": ""}
        status, headers, _ = self.request("GET", "/library?kind=movie", **anonymous)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login?next=%2Flibrary%3Fkind%3Dmovie")
        for path in ("/", "/queue", "/settings/media", "/settings/security", "/cleanup"):
            self.assertEqual(self.request("GET", path, **anonymous)[0], 303, path)
        for path in ("/api/status", "/api/library", "/api/jobs", "/api/log", "/api/models", "/api/scan-status"):
            self.assertEqual(self.request("GET", path, **anonymous)[0], 401, path)
        self.assertEqual(self.request("GET", "/subtitles/file?media=/movies/x.mkv", **anonymous)[0], 303)
        status, _, body = self.request("GET", "/health", **anonymous)
        self.assertEqual((status, json.loads(body)), (200, {"status": "ok"}), "health reveals nothing but that it is alive")
        self.assertEqual(self.request("GET", "/login", **anonymous)[0], 200)
        self.assertEqual(self.request("POST", "/scan", {"token": server.SCAN_TOKEN, "library": "movie"}, **anonymous)[0], 403)
        self.assertEqual(self.post("/jobs/bulk", {"action": "start", "media_path": "/movies/a.mkv"}, **anonymous)[0], 403)
        with sqlite3.connect(server.DATABASE) as db:
            token = db.execute("SELECT value FROM metadata WHERE key='webhook_token'").fetchone()[0]
        status, _, body = self.request("POST", "/webhooks/radarr?token=" + urllib.parse.quote(token, safe=""),
                                       headers={"Content-Type": "application/json"}, cookie="")
        self.assertNotEqual(status, 303, "webhooks keep their own token and are never sent to the login page")

    def test_the_logged_in_session_can_use_everything_and_log_out(self):
        self.turn_on()
        self.assertEqual(self.request("GET", "/library")[0], 200)
        status, _, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn("pbkdf2", body)
        self.assertNotIn("auth_secret", body)
        page = self.request("GET", "/settings/security")[2]
        self.assertIn("Inloggning är PÅ", page)
        self.assertIn("/logout", self.request("GET", "/")[2])
        status, headers, _ = self.request("GET", "/logout")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/library", cookie="")[0], 303)

    def test_login_with_the_right_password_and_a_safe_return_address(self):
        self.turn_on("mitt lösenord")
        status, headers, page = self.request("POST", "/login", {"password": "fel", "next": "/queue"}, cookie="")
        self.assertEqual(status, 401)
        self.assertIn("Fel lösenord", page)
        self.assertNotIn("Set-Cookie", headers)
        status, headers, _ = self.request("POST", "/login", {"password": "mitt lösenord", "next": "/queue"}, cookie="")
        self.assertEqual((status, headers["Location"]), (303, "/queue"))
        session = self.take_cookie(headers)
        for flag in ("HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/queue", cookie=session)[0], 200)
        status, headers, _ = self.request("POST", "/login", {"password": "mitt lösenord", "next": "//evil.example"}, cookie="")
        self.assertEqual(headers["Location"], "/")
        status, headers, _ = self.request("POST", "/login", {"password": "mitt lösenord"}, cookie="",
                                          headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", headers["Set-Cookie"])

    def test_too_many_wrong_passwords_lock_the_client_out_even_for_the_right_one(self):
        self.turn_on("mitt lösenord")
        for _ in range(5):
            self.assertEqual(self.request("POST", "/login", {"password": "fel"}, cookie="")[0], 401)
        status, _, page = self.request("POST", "/login", {"password": "mitt lösenord"}, cookie="")
        self.assertEqual(status, 429)
        self.assertIn("För många felaktiga försök", page)

    def test_changing_and_removing_the_password_needs_the_current_one_and_ends_old_sessions(self):
        self.turn_on("första lösenordet")
        old_session = self.cookie
        status, headers, _ = self.post("/settings/security", {"action": "set", "current_password": "fel",
                                                              "new_password": "nytt lösenord", "repeat_password": "nytt lösenord"})
        self.assertEqual(headers["Location"], "/settings/security?error=current")
        status, headers, _ = self.post("/settings/security", {"action": "set", "current_password": "första lösenordet",
                                                              "new_password": "nytt lösenord", "repeat_password": "annat"})
        self.assertEqual(headers["Location"], "/settings/security?error=mismatch")
        status, headers, _ = self.post("/settings/security", {"action": "set", "current_password": "första lösenordet",
                                                              "new_password": "kort", "repeat_password": "kort"})
        self.assertEqual(headers["Location"], "/settings/security?error=short")
        status, headers, _ = self.post("/settings/security", {"action": "set", "current_password": "första lösenordet",
                                                              "new_password": "nytt lösenord", "repeat_password": "nytt lösenord"})
        self.assertEqual(headers["Location"], "/settings/security?saved=set")
        new_session = self.take_cookie(headers)
        self.assertEqual(self.request("GET", "/queue", cookie=old_session)[0], 303, "the secret changed: old sessions end")
        self.assertEqual(self.request("GET", "/queue", cookie=new_session)[0], 200)
        self.assertEqual(self.request("POST", "/login", {"password": "första lösenordet"}, cookie="")[0], 401)
        self.assertEqual(self.request("POST", "/login", {"password": "nytt lösenord"}, cookie="")[0], 303)
        status, headers, _ = self.post("/settings/security", {"action": "remove", "current_password": "fel"}, cookie=new_session)
        self.assertEqual(headers["Location"], "/settings/security?error=current")
        status, headers, _ = self.post("/settings/security", {"action": "remove", "current_password": "nytt lösenord"}, cookie=new_session)
        self.assertEqual(headers["Location"], "/settings/security?saved=removed")
        self.assertEqual(self.request("GET", "/library", cookie="")[0], 200, "no password: open again")
        with sqlite3.connect(server.DATABASE) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'auth_%'").fetchone()[0], 0)

    def test_log_out_everywhere_rotates_the_secret(self):
        self.turn_on()
        other = auth.make_session(auth_secret := server.auth_settings()[1])
        self.assertEqual(self.request("GET", "/queue", cookie=other)[0], 200)
        status, headers, _ = self.post("/settings/security", {"action": "logout_all"})
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertEqual(self.request("GET", "/queue", cookie=other)[0], 303)
        self.assertNotEqual(server.auth_settings()[1], auth_secret)

    def test_the_stored_password_is_a_hash_and_the_settings_page_hides_nothing_secret(self):
        self.turn_on("hemligt lösenord")
        with sqlite3.connect(server.DATABASE) as db:
            stored = db.execute("SELECT value FROM metadata WHERE key='auth_password_hash'").fetchone()[0]
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("hemligt", stored)
        self.assertNotIn(stored, self.request("GET", "/settings/security")[2])

    def test_every_new_text_exists_in_both_languages(self):
        from i18n import STRINGS
        for key, entry in STRINGS.items():
            if key.startswith(("login_", "security_")) or key in ("nav_settings_security", "nav_logout",
                                                                 "page_title_settings_security"):
                self.assertTrue(entry.get("sv") and entry.get("en"), key)


if __name__ == "__main__":
    unittest.main()
