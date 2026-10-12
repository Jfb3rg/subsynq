import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import cloud_gpu


class BuildOnstartScriptTests(unittest.TestCase):
    def test_embeds_the_given_source_verbatim(self):
        script = cloud_gpu.build_onstart_script("print('hello')")
        self.assertIn("print('hello')", script)
        self.assertIn("SUBSYNQ_GPU_SERVICE_EOF", script)
        self.assertIn("pip install", script)
        self.assertIn("python3 -u /root/gpu_service.py", script)

    def test_qwen_asr_installs_in_its_own_pip_step(self):
        # A single combined "pip install accelerate==... qwen-asr==..." was
        # observed live to fail outright (ResolutionImpossible: qwen-asr
        # hard-requires a different accelerate pin than the one requested
        # here), aborting the whole onstart script under set -e before
        # gpu_service.py was ever written. Two separate pip commands, one
        # per install, avoids that resolver conflict -- see the inline
        # comment in build_onstart_script for the full explanation.
        script = cloud_gpu.build_onstart_script("print('hello')")
        lines = [line for line in script.splitlines() if line.startswith("pip install")]
        self.assertEqual(len(lines), 2)
        self.assertIn("accelerate==1.10.1", lines[0])
        self.assertNotIn("qwen-asr", lines[0])
        self.assertIn("qwen-asr==0.0.6", lines[1])

    def test_service_runs_backgrounded_not_foreground(self):
        # A foreground last line was observed live to block the base image's
        # own ready-detection (its SSH daemon never started), leaving the
        # instance stuck reporting "loading" forever -- see build_onstart_script.
        script = cloud_gpu.build_onstart_script("print('hello')")
        self.assertIn("nohup python3 -u /root/gpu_service.py", script)
        self.assertIn(" &", script)
        lines = [line for line in script.splitlines() if line.strip()]
        self.assertNotEqual(lines[-1].strip(), "python3 -u /root/gpu_service.py")


class PublicServiceUrlTests(unittest.TestCase):
    def test_none_when_not_running_yet(self):
        self.assertIsNone(cloud_gpu.public_service_url(None))
        self.assertIsNone(cloud_gpu.public_service_url({"actual_status": "loading"}))

    def test_none_until_port_is_mapped(self):
        instance = {"actual_status": "running", "public_ipaddr": "1.2.3.4", "ports": {}}
        self.assertIsNone(cloud_gpu.public_service_url(instance))

    def test_builds_url_once_mapped(self):
        instance = {
            "actual_status": "running", "public_ipaddr": "1.2.3.4",
            "ports": {"8766/tcp": [{"HostIp": "0.0.0.0", "HostPort": "41000"}]},
        }
        self.assertEqual(cloud_gpu.public_service_url(instance), "http://1.2.3.4:41000")


class CreateInstanceTests(unittest.TestCase):
    def test_sends_expected_payload_and_returns_contract_id(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"success": True, "new_contract": 999}).encode()

        def fake_urlopen(request, timeout=30):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data)
            return FakeResponse()

        with patch("cloud_gpu.urlopen", side_effect=fake_urlopen):
            contract_id = cloud_gpu.create_instance("secret-key", 12345, "echo hi")
        self.assertEqual(contract_id, 999)
        self.assertIn("/asks/12345", captured["url"])
        self.assertEqual(captured["body"]["onstart"], "echo hi")
        self.assertEqual(captured["body"]["target_state"], "running")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")

    def test_raises_on_provider_rejection(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"success": False}).encode()

        with patch("cloud_gpu.urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(RuntimeError, "avvisade"):
                cloud_gpu.create_instance("secret-key", 1, "echo hi")


class RequestLogsTests(unittest.TestCase):
    def test_returns_the_result_url(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"success": True, "result_url": "https://s3/x.log"}).encode()

        with patch("cloud_gpu.urlopen", return_value=FakeResponse()):
            url = cloud_gpu.request_logs("secret-key", 1)
        self.assertEqual(url, "https://s3/x.log")


class ExecuteCommandTests(unittest.TestCase):
    def test_rejects_overlong_commands_without_calling_the_api(self):
        with patch("cloud_gpu.urlopen") as mock_urlopen:
            with self.assertRaises(ValueError):
                cloud_gpu.execute_command("secret-key", 1, "x" * 600)
            mock_urlopen.assert_not_called()

    def test_returns_the_result_url(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"success": True, "result_url": "https://s3/cmd.log"}).encode()

        with patch("cloud_gpu.urlopen", return_value=FakeResponse()):
            url = cloud_gpu.execute_command("secret-key", 1, "cat /root/gpu_service.log")
        self.assertEqual(url, "https://s3/cmd.log")


class SetInstanceStateTests(unittest.TestCase):
    def test_rejects_unknown_state_without_calling_the_api(self):
        with patch("cloud_gpu.urlopen") as mock_urlopen:
            with self.assertRaises(ValueError):
                cloud_gpu.set_instance_state("secret-key", 1, "paused")
            mock_urlopen.assert_not_called()

    def test_sends_the_requested_state(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"success": True}).encode()

        def fake_urlopen(request, timeout=30):
            captured["body"] = json.loads(request.data)
            return FakeResponse()

        with patch("cloud_gpu.urlopen", side_effect=fake_urlopen):
            self.assertTrue(cloud_gpu.set_instance_state("secret-key", 1, "stopped"))
        self.assertEqual(captured["body"], {"state": "stopped"})


class DestroyInstanceTests(unittest.TestCase):
    def test_returns_true_on_success(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"success": True, "msg": "ok"}).encode()

        with patch("cloud_gpu.urlopen", return_value=FakeResponse()):
            self.assertTrue(cloud_gpu.destroy_instance("secret-key", 999))


if __name__ == "__main__":
    unittest.main()
