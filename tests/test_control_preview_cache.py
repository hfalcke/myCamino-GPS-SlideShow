# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import inspect
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

import GPSTrackShowGUI as gui
from control_preview_cache import (
    ThumbnailIdentity,
    ThumbnailMemoryCache,
    create_or_reuse_thumbnail,
    prune_thumbnail_cache,
    thumbnail_path,
)


def make_image(path, size=(1200, 800), color="navy"):
    Image.new("RGB", size, color).save(path)


class ControlPreviewCacheTests(unittest.TestCase):
    def test_thumbnail_is_downsampled_and_warm_cache_reuses_png(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "large.jpg"
            cache = root / "cache"
            make_image(source)
            identity, generated, created = create_or_reuse_thumbnail(source, cache_directory=cache)
            self.assertTrue(created)
            self.assertEqual(generated, thumbnail_path(identity, cache))
            with Image.open(generated) as thumbnail:
                self.assertEqual(max(thumbnail.size), 144)
            repeated_identity, repeated, repeated_created = create_or_reuse_thumbnail(
                source, cache_directory=cache
            )
            self.assertEqual(repeated_identity, identity)
            self.assertEqual(repeated, generated)
            self.assertFalse(repeated_created)

    def test_thumbnail_identity_changes_with_source_signature(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "photo.png"
            make_image(source, size=(20, 20))
            original = ThumbnailIdentity.from_path(source)
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertNotEqual(ThumbnailIdentity.from_path(source).key, original.key)

    def test_malformed_cached_thumbnail_is_rebuilt_atomically(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "photo.jpg"
            cache = root / "cache"
            make_image(source)
            identity = ThumbnailIdentity.from_path(source)
            destination = thumbnail_path(identity, cache)
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"not a png")
            _identity, generated, created = create_or_reuse_thumbnail(source, cache_directory=cache)
            self.assertEqual(generated, destination)
            self.assertTrue(created)
            with Image.open(generated) as thumbnail:
                thumbnail.verify()
            self.assertFalse(list(cache.glob("*.tmp")))

    def test_memory_cache_is_bounded_lru(self):
        cache = ThumbnailMemoryCache(maximum_entries=2)
        cache.put("one", object())
        cache.put("two", object())
        self.assertIsNotNone(cache.get("one"))
        cache.put("three", object())
        self.assertIsNone(cache.get("two"))
        self.assertEqual(len(cache), 2)

    def test_disk_cache_prunes_oldest_to_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = Path(temporary_directory)
            for index in range(4):
                path = cache / f"{index}.png"
                path.write_bytes(b"x" * 10)
                os.utime(path, ns=(index + 1, index + 1))
            removed = prune_thumbnail_cache(cache, maximum_bytes=30, target_bytes=20)
            self.assertEqual(removed, 2)
            self.assertEqual(
                sorted(path.name for path in cache.glob("*.png")), ["2.png", "3.png"]
            )

    def test_preview_sizes_and_one_pass_media_dates(self):
        self.assertEqual(gui.normalize_control_preview_size(None), "off")
        self.assertEqual(gui.normalize_control_preview_size("LARGE"), "large")
        self.assertEqual(
            gui.CONTROL_PREVIEW_SIZES["small"],
            {"label": "Small", "pixels": 32, "row_height": 36.0, "column_width": 44.0},
        )
        rows = [
            {"type": "DAT", "name": "Tuesday 23.07.2024"},
            {"type": "IMG", "name": "one.jpg", "time": "10:12"},
            {"type": "MUS", "name": "#ON"},
            {"type": "VID", "name": "two.mov", "time": "11:13"},
            {"type": "DAT", "name": "24.07.2024"},
            {"type": "IMG", "name": "three.jpg", "time": ""},
        ]
        self.assertEqual(
            gui.precompute_control_media_datetimes(rows),
            {1: "23.07.2024 10:12", 3: "23.07.2024 11:13", 5: "24.07.2024"},
        )

    def test_table_datasource_never_decodes_original_preview_synchronously(self):
        source = inspect.getsource(gui.GPXTrackerController.preview_image_for_control_row)
        self.assertNotIn("initWithContentsOfFile_", source)
        self.assertIn("_request_control_preview_neighborhood", source)
        module_source = Path(gui.__file__).read_text(encoding="utf-8")
        self.assertIn("reloadDataForRowIndexes_columnIndexes_", module_source)

    def test_map_media_selection_reveals_anchor_and_selects_all_matches(self):
        window = Mock()
        controller = SimpleNamespace(
            control_table_window=window,
            control_table_path=Path("control.lst"),
            control_table_rows=[
                {"type": "IMG", "name": "one.jpg"},
                {"type": "DAT", "name": "2026-01-01"},
                {"type": "VID", "name": "two.mov"},
            ],
            control_table_filter_key="media",
            resolve_control_row_path=lambda row: Path(row["name"]),
            _sync_control_table_filter_popup=Mock(),
            _reload_control_table=Mock(),
            _select_control_table_indexes=Mock(),
            _scroll_control_table_model_row_to_visible=Mock(),
            set_status=Mock(),
        )
        result = gui.GPXTrackerController.show_control_file_media_selection(
            controller,
            [Path("one.jpg"), Path("two.mov")],
            Path("two.mov"),
        )
        self.assertTrue(result)
        self.assertEqual(controller.control_table_filter_key, "all")
        controller._select_control_table_indexes.assert_called_once_with([0, 2])
        controller._scroll_control_table_model_row_to_visible.assert_called_once_with(2)
        window.makeKeyAndOrderFront_.assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()
