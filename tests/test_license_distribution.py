# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from importlib import util
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from license_resources import license_document_path, read_license_document


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "prepare_license_bundle.py"
SPEC_FILES = (
    ROOT / "GPSTrackShow.spec",
    ROOT / "myCamino GPX Editor.spec",
    ROOT / "myCamino GPS Track Show.spec",
)


def load_bundle_module():
    spec = util.spec_from_file_location("prepare_license_bundle", SCRIPT_PATH)
    module = util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LicenseResourceTests(unittest.TestCase):
    def test_repository_contains_complete_gpl_and_copyright(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        copyright_text = (ROOT / "COPYRIGHT").read_text(encoding="utf-8")
        self.assertIn("Copyright (C) 2026 Heino Falcke", copyright_text)
        self.assertIn("GPL-3.0-or-later", copyright_text)

    def test_source_tree_fallback_documents(self):
        self.assertEqual(
            license_document_path("license", source_root=ROOT), ROOT / "LICENSE"
        )
        self.assertIn(
            "Third-Party Notices",
            read_license_document("third_party", source_root=ROOT),
        )

    def test_packaged_document_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaged = root / "licenses" / "myCamino" / "GPL-3.0.txt"
            packaged.parent.mkdir(parents=True)
            packaged.write_text("packaged license\n", encoding="utf-8")
            self.assertEqual(
                read_license_document("license", bundle_root=root),
                "packaged license\n",
            )

    def test_unknown_document_is_rejected(self):
        with self.assertRaises(ValueError):
            license_document_path("unknown", source_root=ROOT)


class LicenseBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle_module()

    def test_runtime_dependency_licenses_are_collectable(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "licenses"
            inventory = self.bundle.collect_dependency_licenses(destination)
            names = {item["name"].casefold() for item in inventory}
            self.assertIn("python", names)
            self.assertIn("numpy", names)
            self.assertIn("matplotlib", names)
            self.assertIn("pyobjc-core", names)
            self.assertIn("setuptools", names)
            self.assertTrue((destination / "Python" / "LICENSE.txt").is_file())
            self.assertTrue(any((destination / "numpy").iterdir()))

    def test_missing_required_distribution_fails_closed(self):
        missing = self.bundle.metadata.PackageNotFoundError("not-installed")
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            self.bundle, "RUNTIME_DISTRIBUTIONS", ("not-installed",)
        ), mock.patch.object(
            self.bundle.metadata, "distribution", side_effect=missing
        ):
            with self.assertRaises(self.bundle.LicenseBundleError):
                self.bundle.collect_dependency_licenses(Path(temporary))

    def test_source_archive_uses_actual_worktree_and_excludes_build_products(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "app.py").write_text("print('tracked')\n", encoding="utf-8")
            (root / "README.md").write_text("source\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "app.py", "README.md"], cwd=root, check=True
            )
            (root / "app.py").write_text("print('modified')\n", encoding="utf-8")
            (root / "new_module.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "generated.bin").write_bytes(b"generated")
            (root / "vendor" / "ffmpeg").mkdir(parents=True)
            (root / "vendor" / "ffmpeg" / "ffmpeg").write_bytes(b"binary")

            destination = root / "output"
            destination.mkdir()
            archive = self.bundle.create_source_archive(
                root, destination, "1234567890abcdef"
            )
            with tarfile.open(archive, "r:gz") as source:
                members = source.getnames()
                app_member = next(name for name in members if name.endswith("/app.py"))
                self.assertEqual(source.extractfile(app_member).read(), b"print('modified')\n")
            self.assertTrue(any(name.endswith("/new_module.py") for name in members))
            self.assertFalse(any("/build/" in name for name in members))
            self.assertFalse(any(name.endswith("/vendor/ffmpeg/ffmpeg") for name in members))

    def test_ffmpeg_checksum_is_pinned(self):
        self.assertEqual(
            self.bundle.FFMPEG_SHA256,
            "b6863adde98898f42602017462871b5f6333e65aec803fdd7a6308639c52edf3",
        )

    def test_all_pyinstaller_targets_embed_license_resources(self):
        for path in SPEC_FILES:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("build/license_bundle/app_resources/licenses", text)

    def test_dmg_build_mounts_and_checks_license_documents(self):
        text = (ROOT / "build_dmg.sh").read_text(encoding="utf-8")
        self.assertIn("prepare_license_bundle.py", text)
        self.assertIn("hdiutil attach -nobrowse -readonly", text)
        self.assertIn("License — GPL-3.0.txt", text)
        self.assertIn("myCamino-source-*.tar.gz", text)
        self.assertIn("ffmpeg-8.1.1.tar.xz", text)


if __name__ == "__main__":
    unittest.main()
