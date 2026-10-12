"""Optional password login for the web interface.

The password is stored only as a salted PBKDF2 hash. A login gives a signed, expiring cookie; changing or removing the
password rotates the signing secret, which ends every session at once. Nothing here talks to the network or the
database: server.py reads and writes the two settings (`auth_password_hash`, `auth_secret`).
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from html import escape
from http.cookies import CookieError, SimpleCookie

from i18n import t

COOKIE_NAME = "subsynq_session"
SESSION_SECONDS = 14 * 24 * 3600
ITERATIONS = 600_000            # OWASP's 2023 guidance for PBKDF2-HMAC-SHA256
MAX_ITERATIONS = 5_000_000      # a stored value above this is treated as corrupt, never computed
MIN_LENGTH = 8
MAX_LENGTH = 200


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def hash_password(password, salt=None, iterations=None):
    salt = salt or secrets.token_bytes(16)
    iterations = iterations or ITERATIONS
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        iterations = int(iterations)
        salt, expected = base64.b64decode(salt), base64.b64decode(digest)
    except (ValueError, AttributeError):
        return False
    if scheme != "pbkdf2_sha256" or not 1 <= iterations <= MAX_ITERATIONS or len(password) > MAX_LENGTH:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def new_secret():
    return secrets.token_hex(32)


def _sign(secret, payload):
    return hmac.new(secret.encode("ascii"), payload.encode("ascii"), hashlib.sha256).hexdigest()


def make_session(secret, now=None, ttl=SESSION_SECONDS):
    expires = int((time.time() if now is None else now) + ttl)
    payload = f"{expires}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(secret, payload)}"


def session_valid(secret, cookie, now=None):
    if not secret or not cookie:
        return False
    parts = cookie.split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return False
    payload = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_sign(secret, payload), parts[2]):
        return False
    return int(parts[0]) > (time.time() if now is None else now)


def cookie_from_header(header):
    try:
        jar = SimpleCookie()
        jar.load(header or "")
    except CookieError:
        return ""
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else ""


def set_cookie_header(value, secure=False, max_age=SESSION_SECONDS):
    return f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}" + ("; Secure" if secure else "")


def clear_cookie_header(secure=False):
    return set_cookie_header("", secure, 0)


def safe_next(value):
    """Where to go after logging in: a path on this site, never another address."""
    value = value or "/"
    if (not value.startswith("/") or value.startswith("//") or "\\" in value or len(value) > 500
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return "/"
    return value


class LoginLimiter:
    """Locks a client out for a while after too many wrong passwords."""

    def __init__(self, limit=5, window=600, lock=300, now=time.time):
        self.limit, self.window, self.lock, self.now = limit, window, lock, now
        self._failures = {}
        self._guard = threading.Lock()

    def blocked(self, key):
        """Seconds left of the lock for this client, 0 when it may try."""
        with self._guard:
            entry = self._failures.get(key)
            if not entry:
                return 0
            left = entry["until"] - self.now()
            return int(left) + 1 if left > 0 else 0

    def failure(self, key):
        with self._guard:
            moment = self.now()
            if len(self._failures) > 5000:      # never let a flood of addresses grow the table without bound
                self._failures = {k: v for k, v in self._failures.items() if v["until"] > moment or v["first"] > moment - self.window}
            entry = self._failures.get(key)
            if not entry or entry["first"] < moment - self.window:
                entry = {"count": 0, "first": moment, "until": 0}
            entry["count"] += 1
            if entry["count"] >= self.limit:
                entry["until"] = moment + self.lock
                entry["count"] = 0
                entry["first"] = moment
            self._failures[key] = entry

    def success(self, key):
        with self._guard:
            self._failures.pop(key, None)


LOGIN_STYLE = """
:root{color-scheme:light dark;--bg:#0e1116;--sur:#151a21;--tx:#e8eaee;--sec:#9ba3af;--ac:#7aa7ff;--dg:#f2777a;--bd:rgba(255,255,255,.12)}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--sur:#fff;--tx:#111827;--sec:#4b5563;--ac:#2f6bff;--dg:#b91c1c;--bd:rgba(15,23,42,.14)}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--tx);font:16px/1.5 system-ui,sans-serif;padding:16px}
main{width:100%;max-width:380px;background:var(--sur);border:1px solid var(--bd);border-radius:12px;padding:28px}
h1{margin:0 0 4px;font-size:22px}p{color:var(--sec);margin:0 0 18px}label{display:block;margin-bottom:6px;font-weight:600}
input[type=password]{width:100%;padding:11px 12px;border-radius:8px;border:1px solid var(--bd);background:transparent;color:var(--tx);font:inherit}
button{width:100%;margin-top:14px;padding:11px;border:0;border-radius:8px;background:var(--ac);color:#fff;font:inherit;font-weight:700;cursor:pointer}
.error{color:var(--dg);margin:0 0 14px}
"""


def render_login_page(lang="sv", error="", next_path="/"):
    message = f"<p class='error' role='alert'>{escape(error)}</p>" if error else ""
    return (
        f"<!doctype html><html lang='{escape(lang, quote=True)}'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>SubSynq · {escape(t('login_title', lang))}</title><style>{LOGIN_STYLE}</style></head><body><main>"
        f"<h1>SubSynq</h1><p>{escape(t('login_intro', lang))}</p>{message}"
        "<form action='/login' method='post'>"
        f"<input type='hidden' name='next' value='{escape(safe_next(next_path), quote=True)}'>"
        f"<label for='password'>{escape(t('login_password', lang))}</label>"
        "<input id='password' name='password' type='password' autocomplete='current-password' "
        f"maxlength='{MAX_LENGTH}' autofocus required>"
        f"<button type='submit'>{escape(t('login_button', lang))}</button></form></main></body></html>")


def render_security_content(token, enabled, lang="sv", saved="", error=""):
    notices = ""
    if saved:
        notices += f"<p class='success' role='status'>{escape(t('security_saved_' + saved, lang))}</p>"
    if error:
        notices += f"<p class='error' role='alert'>{escape(t('security_error_' + error, lang))}</p>"
    token_field = f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
    state = t("security_state_on" if enabled else "security_state_off", lang)
    current = (f"<label>{escape(t('security_current', lang))}<br>"
               "<input type='password' name='current_password' autocomplete='current-password' "
               f"maxlength='{MAX_LENGTH}' required></label>") if enabled else ""
    forms = (
        "<form action='/settings/security' method='post'>" + token_field
        + "<input type='hidden' name='action' value='set'>" + current
        + f"<label>{escape(t('security_new', lang))}<br><input type='password' name='new_password' "
          f"autocomplete='new-password' minlength='{MIN_LENGTH}' maxlength='{MAX_LENGTH}' required></label> "
        + f"<label>{escape(t('security_repeat', lang))}<br><input type='password' name='repeat_password' "
          f"autocomplete='new-password' minlength='{MIN_LENGTH}' maxlength='{MAX_LENGTH}' required></label>"
        + f"<p><button type='submit'>{escape(t('security_change' if enabled else 'security_set', lang))}</button></p></form>")
    extra = ""
    if enabled:
        extra = (
            "<form action='/settings/security' method='post' "
            f"onsubmit=\"return confirm('{escape(t('security_remove_confirm', lang), quote=True)}')\">" + token_field
            + "<input type='hidden' name='action' value='remove'>"
            + f"<label>{escape(t('security_current', lang))}<br><input type='password' name='current_password' "
              f"autocomplete='current-password' maxlength='{MAX_LENGTH}' required></label>"
            + f"<p><button type='submit' class='small-button danger'>{escape(t('security_remove', lang))}</button></p></form>"
            "<form action='/settings/security' method='post'>" + token_field
            + "<input type='hidden' name='action' value='logout_all'>"
            + f"<p class='muted'>{escape(t('security_logout_all_note', lang))}</p>"
            + f"<p><button type='submit' class='small-button'>{escape(t('security_logout_all', lang))}</button></p></form>")
    return (
        f"<div class='card'><h2>{escape(t('security_heading', lang))}</h2>{notices}"
        f"<p><strong>{escape(state)}</strong></p>"
        f"<p class='muted'>{escape(t('security_intro', lang))}</p>"
        f"<p class='muted'>{escape(t('security_cloudflare', lang))}</p>{forms}</div>"
        + (f"<div class='card'>{extra}</div>" if extra else ""))
