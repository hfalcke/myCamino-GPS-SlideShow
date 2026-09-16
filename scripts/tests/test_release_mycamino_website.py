import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ops_control.server import ControlPanel


ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPT = ROOT / "scripts" / "ops" / "release_mycamino_website.sh"


class MyCaminoWebsiteReleaseTests(unittest.TestCase):
    def test_control_panel_exposes_guarded_all_steps_action(self):
        action = ControlPanel("test-token").actions()["release_mycamino_website"]
        self.assertEqual(action.label, "Release myCamino Website (All Steps)")
        self.assertEqual(action.confirmation, "RELEASE MYCAMINO WEBSITE")
        self.assertEqual(action.requires_runner, "notebook")
        self.assertIn("release_mycamino_website.sh", action.command)
        self.assertGreaterEqual(action.timeout, 2400)

    def _write_executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _fixture(self, directory: str, *, watch_exit: int = 0, publish_changes: bool = True):
        root = Path(directory)
        repo = root / "mycamino"
        (repo / ".git").mkdir(parents=True)
        log = root / "operations.log"
        marker = root / "published"
        old_sha = "1" * 40
        new_sha = "2" * 40

        git = root / "git"
        self._write_executable(
            git,
            f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"branch --show-current"* ]]; then
  echo main
elif [[ "$*" == *"ls-remote origin refs/heads/main"* ]]; then
  if [[ -f {marker!s} ]]; then echo "{new_sha} refs/heads/main"; else echo "{old_sha} refs/heads/main"; fi
elif [[ "$*" == *"rev-parse HEAD"* ]]; then
  if [[ -f {marker!s} ]]; then echo {new_sha}; else echo {old_sha}; fi
else
  echo "Unexpected git command: $*" >&2
  exit 9
fi
""",
        )

        publish = root / "publish"
        publish_marker = f"touch {marker!s}" if publish_changes else ":"
        self._write_executable(
            publish,
            f"""#!/usr/bin/env bash
set -euo pipefail
echo publish >> {log!s}
{publish_marker}
""",
        )

        deploy = root / "deploy"
        self._write_executable(
            deploy,
            f"""#!/usr/bin/env bash
set -euo pipefail
echo deploy >> {log!s}
""",
        )

        gh = root / "gh"
        self._write_executable(
            gh,
            f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "auth status" ]]; then
  exit 0
elif [[ "$1 $2" == "run list" ]]; then
  [[ "$*" == *"--commit"* ]] || exit 8
  printf '314\\tcompleted\\tsuccess\\thttps://example.test/run/314\\n'
elif [[ "$1 $2" == "run watch" ]]; then
  echo watch >> {log!s}
  exit {watch_exit}
else
  echo "Unexpected gh command: $*" >&2
  exit 9
fi
""",
        )

        env = os.environ.copy()
        env.update(
            {
                "MYCAMINO_GIT_BIN": str(git),
                "MYCAMINO_CONFIG_DIR": str(root / 'private'),
                "MYCAMINO_CONTROL_STATE": str(root / 'state'),
                "MYCAMINO_GH_BIN": str(gh),
                "MYCAMINO_PUBLISH_SCRIPT": str(publish),
                "MYCAMINO_DEPLOY_SCRIPT": str(deploy),
                "MYCAMINO_BUILD_DISCOVERY_SECONDS": "2",
                "MYCAMINO_BUILD_POLL_SECONDS": "1",
            }
        )
        return repo, log, env

    def test_matching_successful_build_is_deployed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, log, env = self._fixture(directory)
            result = subprocess.run(
                [str(RELEASE_SCRIPT), str(repo)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["publish", "deploy"])
            self.assertIn("myCamino website release complete", result.stdout)
            self.assertIn("2" * 40, result.stdout)

    def test_failed_build_prevents_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, log, env = self._fixture(directory, watch_exit=1)
            gh = Path(env["MYCAMINO_GH_BIN"])
            text = gh.read_text(encoding="utf-8").replace(
                "314\\tcompleted\\tsuccess",
                "314\\tcompleted\\tfailure",
            )
            gh.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [str(RELEASE_SCRIPT), str(repo)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["publish", "watch"])

    def test_no_source_change_deploys_latest_successful_image(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, log, env = self._fixture(directory, publish_changes=False)
            result = subprocess.run(
                [str(RELEASE_SCRIPT), str(repo)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["publish", "deploy"])
            self.assertIn("No new website commit was needed", result.stdout)


if __name__ == "__main__":
    unittest.main()
