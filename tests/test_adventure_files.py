"""Tests for format-2 Adventure discovery and project-file transactions."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adventure_files import (
    ADVENTURE_FORMAT_VERSION,
    AdventureFormatError,
    discover_adventures,
    load_adventure,
    rename_or_copy_adventure,
)
from json_storage import atomic_write_json


def adventure_payload(directory: Path, name: str) -> dict:
    return {
        "adventure_format_version": ADVENTURE_FORMAT_VERSION,
        "project_name": name,
        "project_directory": str(directory),
        "description": "Test",
        "gpx_file": f"{name}.gpx",
        "control_file": f"{name}-sorted.lst",
        "track_map_base": name,
        "parameters": {"version": 1, "values": {}},
        "slideshow_resume_position": {"playlist_index": 2},
    }


class AdventureFileTests(unittest.TestCase):
    def test_discovery_accepts_only_format_two_and_sorts_by_mtime(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            older = directory / "Older.adv"
            newer = directory / "Newer.adv"
            for name, path in (("Older", older), ("Newer", newer)):
                (directory / f"{name}.gpx").write_text("<gpx/>")
                (directory / f"{name}-sorted.lst").write_text("")
                atomic_write_json(path, adventure_payload(directory, name))
            os.utime(older, (100.0, 100.0))
            os.utime(newer, (200.0, 200.0))
            atomic_write_json(directory / "Legacy.adv", {"project_name": "Legacy"})

            records, errors = discover_adventures(directory)

            self.assertEqual([record.project_name for record in records], ["Newer", "Older"])
            self.assertEqual(len(errors), 1)
            self.assertIn("format 2", errors[0])

    def test_discovery_uses_filename_as_tie_breaker(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("Zulu", "Alpha"):
                path = directory / f"{name}.adv"
                atomic_write_json(path, adventure_payload(directory, name))
                os.utime(path, (100.0, 100.0))

            records, errors = discover_adventures(directory)

            self.assertEqual(errors, [])
            self.assertEqual([record.project_name for record in records], ["Alpha", "Zulu"])

    def test_copy_rewrites_complete_track_family_and_keeps_media(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            track_dir = directory / "trackimages"
            track_dir.mkdir()
            old_name = "Old Trip"
            new_name = "New Trip"
            source_adv = directory / f"{old_name}.adv"
            source_gpx = directory / f"{old_name}.gpx"
            source_control = directory / f"{old_name}-sorted.lst"
            source_gpx.write_text("<gpx/>")
            source_control.write_text(
                f"#Overviewmap: {track_dir / (old_name + '.png')}\n"
                f"#Map: 0001_Stage_{old_name}.png\n"
                "photo.jpeg | 12:00 | - | -\n"
            )
            (directory / "photo.jpeg").write_bytes(b"media")
            (track_dir / f"{old_name}.png").write_bytes(b"overview")
            atomic_write_json(
                track_dir / f"{old_name}.json",
                {
                    "source_gpx": str(source_gpx),
                    "output_image": str(track_dir / f"{old_name}.png"),
                    "header": old_name,
                    "tracks": [{"track_plot_image_filename": f"0001_Stage_{old_name}.png"}],
                },
            )
            (track_dir / f"0001_Stage_{old_name}.png").write_bytes(b"map")
            atomic_write_json(
                track_dir / f"0001_Stage_{old_name}.json",
                {"source_gpx": str(source_gpx), "output_image": str(track_dir / f"0001_Stage_{old_name}.png")},
            )
            atomic_write_json(
                track_dir / f"{old_name}-summary.json",
                {
                    "source_gpx": str(source_gpx),
                    "tracks": [{"track_plot_image_filename": f"0001_Stage_{old_name}.png"}],
                },
            )
            payload = adventure_payload(directory, old_name)
            atomic_write_json(source_adv, payload)

            target_adv, target_payload = rename_or_copy_adventure(
                source_adv, payload, new_name, "copy", include_related=True
            )

            self.assertTrue(source_adv.exists())
            self.assertTrue(target_adv.exists())
            self.assertEqual(target_payload["gpx_file"], f"{new_name}.gpx")
            self.assertIsNone(target_payload["slideshow_resume_position"])
            copied_list = (directory / f"{new_name}-sorted.lst").read_text()
            self.assertIn(f"#Map: 0001_Stage_{new_name}.png", copied_list)
            self.assertIn(str(track_dir / f"{new_name}.png"), copied_list)
            copied_summary = json.loads((track_dir / f"{new_name}-summary.json").read_text())
            self.assertEqual(
                Path(copied_summary["source_gpx"]).resolve(strict=False),
                (directory / f"{new_name}.gpx").resolve(strict=False),
            )
            self.assertEqual(copied_summary["tracks"][0]["track_plot_image_filename"], f"0001_Stage_{new_name}.png")
            self.assertEqual((directory / "photo.jpeg").read_bytes(), b"media")

    def test_related_copy_preserves_music_directives_and_copies_playlist(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old_name = "Old"
            new_name = "New"
            source_adv = directory / "Old.adv"
            track_dir = directory / "trackimages"
            track_dir.mkdir()
            (directory / "Old.gpx").write_text("<gpx/>")
            (directory / "Old-sorted.lst").write_text(
                "#MUSIC: #JUMP $EVENING\n"
                "#MapAfter: 0001_Old.png\n"
                "photo.jpeg | 12:00 | - | -\n"
            )
            audio_dir = directory / "audio"
            audio_dir.mkdir()
            (audio_dir / "Old.playlist").write_text("$EVENING\nsong.m4a\n")
            (track_dir / "0001_Old.png").write_bytes(b"map")
            payload = adventure_payload(directory, old_name)
            payload["music_source"] = "audio"
            payload["music_playlist"] = "audio/Old.playlist"
            atomic_write_json(source_adv, payload)

            _target_adv, copied = rename_or_copy_adventure(
                source_adv, payload, new_name, "copy", include_related=True
            )

            self.assertEqual(copied["music_playlist"], "audio/New.playlist")
            self.assertTrue((audio_dir / "New.playlist").is_file())
            copied_control = (directory / "New-sorted.lst").read_text()
            self.assertIn("#MUSIC: #JUMP $EVENING", copied_control)
            self.assertIn("#MapAfter: 0001_New.png", copied_control)
            self.assertIn("photo.jpeg | 12:00 | - | -", copied_control)

    def test_rename_without_related_files_preserves_explicit_references(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = adventure_payload(directory, "Shared")
            source = directory / "Shared.adv"
            atomic_write_json(source, payload)

            target, renamed = rename_or_copy_adventure(
                source, payload, "Alternative", "rename", include_related=False
            )

            self.assertFalse(source.exists())
            self.assertTrue(target.exists())
            self.assertEqual(renamed["gpx_file"], "Shared.gpx")
            self.assertEqual(renamed["control_file"], "Shared-sorted.lst")
            self.assertEqual(renamed["track_map_base"], "Shared")

    def test_conflict_does_not_modify_source(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = adventure_payload(directory, "Old")
            source = directory / "Old.adv"
            atomic_write_json(source, payload)
            atomic_write_json(directory / "New.adv", adventure_payload(directory, "New"))

            with self.assertRaises(FileExistsError):
                rename_or_copy_adventure(source, payload, "New", "copy", include_related=False)

            self.assertEqual(load_adventure(source).project_name, "Old")

    def test_missing_version_is_rejected(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "Old.adv"
            atomic_write_json(path, {"project_name": "Old"})
            with self.assertRaises(AdventureFormatError):
                load_adventure(path)

    def test_missing_explicit_parameters_are_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = adventure_payload(directory, "Incomplete")
            del payload["parameters"]
            path = directory / "Incomplete.adv"
            atomic_write_json(path, payload)

            with self.assertRaisesRegex(AdventureFormatError, "parameters"):
                load_adventure(path)

    def test_case_only_rename_uses_transactional_intermediate_path(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = adventure_payload(directory, "Camino")
            source = directory / "Camino.adv"
            atomic_write_json(source, payload)

            target, renamed = rename_or_copy_adventure(
                source, payload, "camino", "rename", include_related=False
            )

            self.assertEqual(target.name, "camino.adv")
            self.assertEqual(renamed["project_name"], "camino")
            self.assertTrue(target.exists())
            self.assertEqual(load_adventure(target).project_name, "camino")

    def test_case_only_copy_never_replaces_source(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = adventure_payload(directory, "Camino")
            source = directory / "Camino.adv"
            atomic_write_json(source, payload)

            try:
                same_destination = source.samefile(directory / "camino.adv")
            except OSError:
                same_destination = False
            if not same_destination:
                self.skipTest("The test filesystem is case-sensitive")

            with self.assertRaises(FileExistsError):
                rename_or_copy_adventure(
                    source, payload, "camino", "copy", include_related=False
                )
            self.assertEqual(load_adventure(source).project_name, "Camino")

    def test_absolute_project_file_references_are_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = adventure_payload(directory, "Absolute")
            payload["gpx_file"] = str(directory / "Absolute.gpx")
            path = directory / "Absolute.adv"
            atomic_write_json(path, payload)

            with self.assertRaisesRegex(AdventureFormatError, "project-relative"):
                load_adventure(path)

    def test_related_rename_rejects_assets_shared_by_another_adventure(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = adventure_payload(directory, "First")
            second = adventure_payload(directory, "Second")
            second["gpx_file"] = first["gpx_file"]
            second["control_file"] = first["control_file"]
            second["track_map_base"] = first["track_map_base"]
            first_path = directory / "First.adv"
            second_path = directory / "Second.adv"
            atomic_write_json(first_path, first)
            atomic_write_json(second_path, second)

            with self.assertRaisesRegex(ValueError, "shared by other Adventures"):
                rename_or_copy_adventure(
                    first_path, first, "Renamed", "rename", include_related=True
                )

            self.assertTrue(first_path.exists())
            self.assertTrue(second_path.exists())


if __name__ == "__main__":
    unittest.main()
