"""Content-aware media import behavior.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from media_import_utils import accepted_media_paths, import_media_files


class MediaImportTests(unittest.TestCase):
    def test_identical_same_name_is_reused(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "photo.jpeg"
            target = Path(target_dir) / source.name
            source.write_bytes(b"same photo")
            target.write_bytes(b"same photo")

            results = import_media_files([source], target.parent)

            self.assertEqual(results[0].action, "reused")
            self.assertEqual(accepted_media_paths(results), [target.resolve()])
            self.assertFalse((target.parent / "photo-2.jpeg").exists())

    def test_different_same_name_is_renamed(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "photo.jpeg"
            target = Path(target_dir) / source.name
            source.write_bytes(b"new photo")
            target.write_bytes(b"old")

            result = import_media_files([source], target.parent)[0]

            self.assertEqual(result.action, "renamed")
            self.assertEqual(result.target.name, "photo-2.jpeg")
            self.assertEqual(result.target.read_bytes(), b"new photo")

    def test_same_size_different_content_is_renamed(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "photo.jpeg"
            target = Path(target_dir) / source.name
            source.write_bytes(b"new bytes")
            target.write_bytes(b"old bytes")

            result = import_media_files([source], target.parent)[0]

            self.assertEqual(result.action, "renamed")
            self.assertEqual(result.target.name, "photo-2.jpeg")

    def test_existing_numbered_identical_candidate_is_reused(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "photo.jpeg"
            root = Path(target_dir)
            source.write_bytes(b"new photo")
            (root / "photo.jpeg").write_bytes(b"different")
            (root / "photo-2.jpeg").write_bytes(b"new photo")

            result = import_media_files([source], root)[0]

            self.assertEqual(result.action, "reused")
            self.assertEqual(result.target.name, "photo-2.jpeg")
            self.assertFalse((root / "photo-3.jpeg").exists())

    def test_comparison_failure_does_not_create_a_copy(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "photo.jpeg"
            target = Path(target_dir) / source.name
            source.write_bytes(b"same size")
            target.write_bytes(b"same size")
            source.chmod(0)
            try:
                # Running as an owner may still permit reads on some systems;
                # a missing source exercises the same safe failure contract.
                source.unlink()
                result = import_media_files([source], target.parent)[0]
            finally:
                source.chmod(0o600) if source.exists() else None

            self.assertEqual(result.action, "failed")
            self.assertFalse((target.parent / "photo-2.jpeg").exists())

    def test_hash_failure_at_same_name_does_not_create_a_copy(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "photo.jpeg"
            target = Path(target_dir) / source.name
            source.write_bytes(b"same size")
            target.write_bytes(b"same size")

            with patch(
                "media_import_utils.media_file_identity",
                side_effect=OSError("cannot read"),
            ):
                result = import_media_files([source], target.parent)[0]

            self.assertEqual(result.action, "failed")
            self.assertIn("could not compare", result.error)
            self.assertFalse((target.parent / "photo-2.jpeg").exists())


if __name__ == "__main__":
    unittest.main()
