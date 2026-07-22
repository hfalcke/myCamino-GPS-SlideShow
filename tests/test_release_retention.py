import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRUNER = ROOT / "scripts" / "prune_website_releases.sh"


class ReleaseRetentionTests(unittest.TestCase):
    def test_keeps_active_and_actual_previously_active_release(self):
        names = [
            "myCamino-GPS-Track-Show-20260718T100000Z.dmg",
            "myCamino-GPS-Track-Show-20260719T100000Z.dmg",
            "myCamino-GPS-Track-Show-20260720T100000Z.dmg",
            "myCamino-GPS-Track-Show-20260721T100000Z.dmg",
            "myCamino-GPS-Track-Show-20260722T100000Z.dmg",
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            release_dir = Path(temporary_directory)
            for index, name in enumerate(names):
                path = release_dir / name
                path.write_bytes(str(index).encode("ascii"))
                os.utime(path, (index + 1, index + 1))
            unexpected = release_dir / "manually-preserved.dmg"
            unexpected.write_bytes(b"keep")
            latest_link = release_dir / "latest.dmg"
            latest_link.symlink_to(names[-1])

            result = subprocess.run(
                [
                    str(PRUNER),
                    str(release_dir),
                    str(release_dir / names[-1]),
                    str(release_dir / names[0]),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                sorted(path.name for path in release_dir.glob("myCamino-GPS-Track-Show-*.dmg")),
                [names[0], names[-1]],
            )
            self.assertEqual(
                latest_link.resolve(),
                (release_dir / names[-1]).resolve(),
            )
            self.assertTrue(unexpected.is_file())
            self.assertIn(names[1], result.stdout)
            self.assertIn(names[-2], result.stdout)

    def test_refuses_an_active_file_outside_the_release_directory(self):
        name = "myCamino-GPS-Track-Show-20260721T100000Z.dmg"
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            active = Path(second) / name
            active.write_bytes(b"active")
            result = subprocess.run(
                [str(PRUNER), first, str(active)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("directly inside", result.stderr)


if __name__ == "__main__":
    unittest.main()
