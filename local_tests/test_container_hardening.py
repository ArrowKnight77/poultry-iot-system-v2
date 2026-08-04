from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SERVICES = ("api", "dashboard", "mqtt_subscriber")
DOCKERFILES = (
    "Dockerfile.api",
    "Dockerfile.dashboard",
    "Dockerfile.mqtt",
)


def compose_service_block(service_name):
    lines = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    marker = f"  {service_name}:"
    start = lines.index(marker)
    end = len(lines)

    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break

    return "\n".join(lines[start:end])


class DockerfileHardeningTests(unittest.TestCase):
    def test_python_images_use_non_root_user_and_healthcheck(self):
        for dockerfile_name in DOCKERFILES:
            with self.subTest(dockerfile=dockerfile_name):
                content = (ROOT / dockerfile_name).read_text(encoding="utf-8")
                self.assertIn("USER 10001:10001", content)
                self.assertIn("COPY --chown=app:app", content)
                self.assertIn("HEALTHCHECK", content)
                self.assertIn("PIP_NO_CACHE_DIR=1", content)
                self.assertIn("PYTHONDONTWRITEBYTECODE=1", content)
                self.assertIn("--require-hashes", content)
                self.assertNotIn("apt-get install", content)

    def test_web_images_use_hardened_gunicorn_runtime(self):
        for dockerfile_name in ("Dockerfile.api", "Dockerfile.dashboard"):
            with self.subTest(dockerfile=dockerfile_name):
                content = (ROOT / dockerfile_name).read_text(encoding="utf-8")
                self.assertIn('"gunicorn"', content)
                self.assertIn('"--worker-tmp-dir", "/tmp"', content)
                self.assertIn('"--no-control-socket"', content)


class ComposeHardeningTests(unittest.TestCase):
    def test_python_services_apply_runtime_restrictions(self):
        required_fragments = (
            'user: "10001:10001"',
            "read_only: true",
            "init: true",
            "pids_limit: 128",
            "cap_drop:",
            "- ALL",
            "security_opt:",
            "- no-new-privileges:true",
            "tmpfs:",
            "noexec",
        )

        for service_name in PYTHON_SERVICES:
            with self.subTest(service=service_name):
                block = compose_service_block(service_name)
                for fragment in required_fragments:
                    self.assertIn(fragment, block)

    def test_mqtt_broker_has_healthcheck(self):
        block = compose_service_block("mqtt")
        self.assertIn("healthcheck:", block)
        self.assertIn('"nc", "-z", "127.0.0.1", "1883"', block)


if __name__ == "__main__":
    unittest.main()
