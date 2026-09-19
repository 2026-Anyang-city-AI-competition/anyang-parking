import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts/deploy/deploy_release.sh"


class DeployReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.app = base / "app"
        self.web = base / "www"
        self.deployments = base / "deployments"
        self.mockbin = base / "bin"
        for path in (
            self.app / "src/serve", self.app / "scripts/deploy", self.app / "web",
            self.app / ".venv/bin", self.app / "data", self.app / "logs",
            self.web, self.mockbin,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.app / "src/serve/api.py").write_text("old-api\n")
        (self.app / "requirements-api.txt").write_text("old requirements\n")
        (self.web / "index.html").write_text('<div id="old"></div>\n')
        self._executable(self.app / ".venv/bin/python", "#!/bin/sh\nexit 0\n")
        for name in ("systemctl", "nginx", "chown"):
            self._executable(self.mockbin / name, "#!/bin/sh\nexit 0\n")
        self._executable(self.mockbin / "curl", """#!/bin/sh
if [ "${SMOKE_FAIL:-0}" = 1 ]; then exit 1; fi
case "$*" in
  *api/v1/health*) printf '%s\\n' '{"checks":{"model":true,"model_bundle_current":true},"background_refresh":{"running":true,"last_error":null}}' ;;
  *) printf '%s\\n' '<div id="root"></div>' ;;
esac
""")
        self.archive = base / "release.tgz"
        subprocess.run(
            ["tar", "-czf", str(self.archive), "src", "scripts", "web/dist",
             "requirements-api.txt"],
            cwd=ROOT, check=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def _run(self, revision, fail=False):
        env = {
            **os.environ,
            "PATH": f"{self.mockbin}:{os.environ['PATH']}",
            "APP_DIR": str(self.app),
            "WEB_ROOT": str(self.web),
            "DEPLOY_ROOT": str(self.deployments),
            "DEPLOY_OWNER": "tester",
            "ATTEMPTS": "1",
            "WAIT_SEC": "0",
            "SMOKE_FAIL": "1" if fail else "0",
        }
        return subprocess.run(
            ["bash", str(DEPLOY), str(self.archive), revision],
            cwd=ROOT, env=env, text=True, capture_output=True,
        )

    def test_success_then_failed_release_rolls_back_code_and_web(self):
        good = "a" * 40
        result = self._run(good)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app / ".deploy-revision").read_text().strip(), good)
        self.assertIn("안양 주차 추천 HTTP API",
                      (self.app / "src/serve/api.py").read_text())
        self.assertIn('<div id="root"></div>', (self.web / "index.html").read_text())

        (self.app / "src/serve/api.py").write_text("rollback-source\n")
        (self.web / "index.html").write_text('<div id="rollback-web"></div>\n')
        failed = self._run("b" * 40, fail=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual((self.app / "src/serve/api.py").read_text(), "rollback-source\n")
        self.assertIn("rollback-web", (self.web / "index.html").read_text())
        self.assertEqual((self.app / ".deploy-revision").read_text().strip(), good)


if __name__ == "__main__":
    unittest.main()
