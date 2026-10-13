"""Keeps the Unraid templates, the publishing workflow and compose.yaml in step.

Run from the repository root (python -m unittest tests.test_unraid_templates). Inside the normal test
image only app/, ai/ and tests/ are mounted, so these tests skip themselves there.
"""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
UNRAID = ROOT / "unraid"
NAMES = ("subsynq", "subsynq-worker", "subsynq-qwen-asr", "subsynq-translate-engine")
OURS = {"subsynq": "subsynq", "subsynq-worker": "subsynq-worker", "subsynq-qwen-asr": "subsynq-qwen-asr"}
MEDIA = {"/movies", "/tv", "/extra"}


def compose_default(raw):
    """compose.yaml may write a variable as ${NAME:-default}; the template carries the default."""
    match = re.fullmatch(r"\$\{[A-Z0-9_]+:-(.*)\}", str(raw))
    return match.group(1) if match else str(raw)


def load(name):
    root = ET.parse(UNRAID / f"{name}.xml").getroot()
    configs = [dict(c.attrib, value=(c.text or "")) for c in root.findall("Config")]
    return root, configs


class UnraidTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not UNRAID.is_dir() or yaml is None:
            raise unittest.SkipTest("unraid/ eller PyYAML finns inte här; körs från arkivets rot")
        cls.compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]
        cls.workflow_text = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)

    def targets(self, name, kind):
        return {c["Target"] for c in load(name)[1] if c["Type"] == kind}

    def value(self, name, target):
        return next(c["value"] for c in load(name)[1] if c["Target"] == target)

    def test_every_template_is_well_formed_and_on_the_subsynq_network(self):
        for name in NAMES:
            root, configs = load(name)
            self.assertEqual(root.tag, "Container")
            self.assertEqual(root.get("version"), "2")
            self.assertEqual(root.findtext("Name"), name)
            self.assertEqual(root.findtext("Network"), "subsynq", name)
            self.assertEqual(root.findtext("Privileged"), "false", name)
            self.assertTrue(root.findtext("Overview"), name)
            self.assertTrue(root.findtext("Icon", "").startswith("https://"), name)
            self.assertTrue(root.findtext("TemplateURL", "").endswith(f"/unraid/{name}.xml"), name)
            seen = set()
            for config in configs:
                self.assertIn(config["Type"], ("Path", "Port", "Variable"), (name, config["Name"]))
                self.assertIn(config["Display"], ("always", "advanced"), (name, config["Name"]))
                self.assertEqual(config["Mask"], "false", (name, config["Name"]))
                self.assertEqual(config["value"], config["Default"], (name, config["Name"]))
                self.assertNotIn(config["Target"], seen, (name, config["Target"]))
                seen.add(config["Target"])
                if config["Type"] == "Path":
                    self.assertEqual(config["Mode"], "rw", (name, config["Target"]))
                    self.assertTrue(config["Default"].startswith("/mnt/user/"), (name, config["Target"]))
                self.assertNotRegex(config["Target"], r"(?i)token|password|secret|api_?key", name)

    def test_images_are_ours_on_docker_hub_with_one_account_and_ollama_is_ollamas_own(self):
        users = set()
        for name, image in OURS.items():
            root, _ = load(name)
            repository = root.findtext("Repository")
            match = re.fullmatch(r"([a-z0-9][a-z0-9_-]*)/" + re.escape(image), repository)
            self.assertIsNotNone(match, repository)
            users.add(match.group(1))
            self.assertEqual(root.findtext("Registry"), f"https://hub.docker.com/r/{repository}")
        self.assertEqual(len(users), 1, users)
        self.assertEqual(load("subsynq-translate-engine")[0].findtext("Repository"), "ollama/ollama:latest")

    def test_folders_and_port_match_compose(self):
        shared = {"/config", "/models"} | MEDIA
        self.assertEqual(self.targets("subsynq", "Path"), shared)
        self.assertEqual(self.targets("subsynq-worker", "Path"), shared)
        self.assertEqual(self.targets("subsynq-qwen-asr", "Path"), {"/models"})
        self.assertEqual(self.targets("subsynq-translate-engine", "Path"), {"/root/.ollama"})
        self.assertEqual(self.targets("subsynq", "Port"), {"8080"})
        self.assertEqual(load("subsynq")[0].findtext("WebUI"), "http://[IP]:[PORT:8080]/")
        for name in ("subsynq-worker", "subsynq-qwen-asr", "subsynq-translate-engine"):
            self.assertEqual(self.targets(name, "Port"), set(), f"{name} ska inte publicera någon port")
        for service in ("subsynq", "worker"):
            targets = {v["target"] if isinstance(v, dict) else v.split(":")[1] for v in self.compose[service]["volumes"]}
            self.assertEqual(targets, shared, service)

    def test_the_same_host_folders_are_used_everywhere(self):
        for target in ("/config", "/models", "/movies", "/tv", "/extra"):
            self.assertEqual(self.value("subsynq", target), self.value("subsynq-worker", target), target)
        self.assertEqual(self.value("subsynq", "/models"), self.value("subsynq-qwen-asr", "/models"))
        self.assertEqual(self.value("subsynq-translate-engine", "/root/.ollama"),
                         self.value("subsynq", "/models") + "/ollama")

    def test_worker_finds_the_other_containers_by_their_unraid_names(self):
        engine = re.fullmatch(r"http://([a-z0-9-]+):11434/api/generate", self.value("subsynq-worker", "SUBSYNQ_TRANSLATE_URL"))
        qwen = re.fullmatch(r"http://([a-z0-9-]+):8091", self.value("subsynq-worker", "SUBSYNQ_QWEN_ASR_URL"))
        self.assertEqual(engine.group(1), "subsynq-translate-engine")
        self.assertEqual(qwen.group(1), "subsynq-qwen-asr")
        self.assertEqual(self.compose["worker"]["environment"]["SUBSYNQ_QWEN_ASR_URL"], "http://qwen-asr:8091")
        self.assertEqual(self.value("subsynq-translate-engine", "OLLAMA_HOST"), "0.0.0.0:11434")

    def test_variables_are_the_ones_compose_sets(self):
        for name, service in (("subsynq-worker", "worker"), ("subsynq-qwen-asr", "qwen-asr"),
                              ("subsynq-translate-engine", "translate-engine")):
            compose_env = self.compose[service].get("environment", {})
            for config in load(name)[1]:
                if config["Type"] == "Variable" and config["Target"] != "TZ" and not config["Target"].endswith("_URL"):
                    self.assertEqual(compose_default(compose_env.get(config["Target"])), config["value"], (name, config["Target"]))
        # The Windows-only GPU service must not be wired up on Unraid.
        self.assertNotIn("SUBSYNQ_GPU_ASR_URL", self.targets("subsynq-worker", "Variable"))

    def test_start_commands_and_safety_flags_follow_compose(self):
        worker = load("subsynq-worker")[0]
        self.assertEqual(self.compose["worker"]["entrypoint"], ["python", "-u", "/ai/worker.py"])
        self.assertIn("--entrypoint python", worker.findtext("ExtraParams"))
        self.assertEqual(worker.findtext("PostArgs"), "-u /ai/worker.py")
        self.assertIn("--no-healthcheck", worker.findtext("ExtraParams"))
        qwen = load("subsynq-qwen-asr")[0]
        self.assertEqual(self.compose["qwen-asr"]["entrypoint"], ["python", "-u", "/ai/qwen_asr_service.py"])
        self.assertIn("--entrypoint python", qwen.findtext("ExtraParams"))
        self.assertEqual(qwen.findtext("PostArgs"), "-u /ai/qwen_asr_service.py")
        for name in ("subsynq", "subsynq-worker"):
            flags = load(name)[0].findtext("ExtraParams")
            for flag in ("--user 99:100", "--read-only", "--tmpfs /tmp", "--cap-drop=ALL", "--security-opt=no-new-privileges:true"):
                self.assertIn(flag, flags, name)
        self.assertIn(f"--memory={self.compose['qwen-asr']['mem_limit']}", load("subsynq-qwen-asr")[0].findtext("ExtraParams"))
        self.assertIn(f"--memory={self.compose['translate-engine']['mem_limit']}",
                      load("subsynq-translate-engine")[0].findtext("ExtraParams"))
        for name in ("subsynq-qwen-asr", "subsynq-translate-engine"):
            self.assertIn("--cpus=6", load(name)[0].findtext("ExtraParams"))

    def test_the_workflow_tests_first_then_publishes_the_three_images_from_main_only(self):
        workflow = self.workflow
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertNotIn("pull_request", triggers)
        self.assertEqual(workflow["jobs"]["publish"]["needs"], "test")
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        # Only the publishing job may write (to create the GitHub release).
        self.assertEqual(workflow["jobs"]["publish"]["permissions"], {"contents": "write"})
        self.assertNotIn("permissions", workflow["jobs"]["test"])
        self.assertIn("gh release create", self.workflow_text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.workflow_text)
        # Names that say what this is when seen in the GitHub Actions list.
        self.assertIn("SubSynq", workflow["name"])
        self.assertIn("main", workflow["name"])
        for job in ("test", "publish"):
            self.assertTrue(workflow["jobs"][job]["name"].startswith("SubSynq"), job)
        self.assertEqual(triggers["push"]["paths-ignore"], ["unraid/**", "**/*.md", "LICENSE"])
        text = self.workflow_text
        for image in ("subsynq", "subsynq-worker", "subsynq-qwen-asr"):
            self.assertIn("${{ env.HUB_USER }}/" + image + ":latest", text)
            self.assertIn("${{ env.HUB_USER }}/" + image + ":${{ steps.version.outputs.number }}", text)
        self.assertIn("SUBSYNQ_BASE_IMAGE=${{ env.HUB_USER }}/subsynq:${{ steps.version.outputs.number }}", text)
        for dockerfile in ("Dockerfile", "Dockerfile.ai", "Dockerfile.qwen3-asr"):
            self.assertIn(f"file: {dockerfile}\n", text)
            self.assertTrue((ROOT / dockerfile).is_file(), dockerfile)
        self.assertIn("password: ${{ secrets.DOCKERHUB_TOKEN }}", text)
        # A missing secret must fail with a clear message BEFORE the login step, never a vague login error.
        self.assertLess(text.index("Kontrollera att Docker Hub-nyckeln når fram"), text.index("docker/login-action"))
        self.assertIn("HUB_USER: ${{ secrets.DOCKERHUB_USERNAME || vars.DOCKERHUB_USERNAME || 'fb3rg' }}", text)
        self.assertIn("SECRET_NAMES: ${{ toJSON(secrets) }}", text)
        self.assertIn("HUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}", text)
        self.assertNotRegex(text, r"echo[^\n]*secrets\.")
        for action in re.findall(r"uses: ([^\s]+)", text):
            self.assertRegex(action, r"@v\d+$", action)
        # The version the images are tagged with must be readable the way the workflow reads it.
        self.assertRegex((ROOT / "app" / "server.py").read_text(encoding="utf-8"), r'VERSION = "[0-9.]+"')

    def test_the_readme_for_unraid_exists_and_names_every_template(self):
        readme = (UNRAID / "README.md").read_text(encoding="utf-8")
        for name in NAMES:
            self.assertIn(f"{name}.xml", readme)
        self.assertIn("docker network create subsynq", readme)
        self.assertIn("chown 99:100 /mnt/user/appdata/subsynq", readme)
        for name in ("subsynq", "subsynq-worker", "subsynq-qwen-asr"):
            description = next(c["Description"] for c in (dict(e.attrib) for e in load(name)[0].findall("Config")) if c.get("Target") == "/models")
            self.assertIn("chown 99:100", description, name)


    def test_larger_cloud_models_are_optional_and_empty_by_default(self):
        for target in ("SUBSYNQ_CLOUD_TRANSLATION_MODEL", "SUBSYNQ_CLOUD_REVIEW_MODEL"):
            config = next(c for c in load("subsynq-worker")[1] if c["Target"] == target)
            self.assertEqual((config["Default"], config["value"], config["Required"], config["Display"]),
                             ("", "", "false", "advanced"))
            self.assertEqual(compose_default(self.compose["worker"]["environment"][target]), "")


if __name__ == "__main__":
    unittest.main()
