"""Comparison keys must never replace real paths or persistent identities."""

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unicodedata
import unittest
from unittest.mock import patch

import GetGeoLocations as geo
from control_media_inventory import (
    build_control_media_inventory_payload,
    classify_project_media,
    control_media_inventory_path,
    load_control_media_inventory,
    mark_imported_media,
    normalize_media_name,
)
from filename_matching import (
    AmbiguousFilenameError,
    FilenameIndex,
    filename_comparison_key,
    unique_filename_mapping,
)
from gpx_adventure_identity import _normalized_name


NFC = "M\u00fcnchen.jpeg"
NFD = unicodedata.normalize("NFD", NFC)


class FilenameMatchingTests(unittest.TestCase):
    def test_unicode_equivalence_without_implicit_case_or_compatibility_folding(self):
        self.assertEqual(filename_comparison_key(NFC), filename_comparison_key(NFD))
        self.assertNotEqual(filename_comparison_key("A.jpg"), filename_comparison_key("a.jpg"))
        self.assertEqual(filename_comparison_key("A.jpg", case_sensitive=False), "a.jpg")
        self.assertNotEqual(filename_comparison_key("\u2460.jpg"), "1.jpg")
        self.assertEqual(filename_comparison_key(" name.jpg"), " name.jpg")

    def test_lookup_is_pure_and_returns_original_path(self):
        actual = Path("/original") / NFD
        with (
            patch.object(Path, "resolve", side_effect=AssertionError("no resolution")),
            patch.object(Path, "stat", side_effect=AssertionError("no probing")),
        ):
            match = FilenameIndex([actual]).match(NFC)
        self.assertEqual(match.status, "normalized")
        self.assertIs(match.path, actual)
        self.assertEqual(match.path.name, NFD)
        self.assertEqual(FilenameIndex([actual]).match("missing.jpeg").status, "missing")

    def test_unicode_directories_and_relative_names(self):
        actual = Path("/copied") / unicodedata.normalize("NFD", "K\u00f6ln") / NFD
        index = FilenameIndex([actual], name=lambda path: path.relative_to("/copied").as_posix())
        self.assertIs(index.match("K\u00f6ln/" + NFC).path, actual)

    def test_collisions_are_not_last_wins(self):
        first, second = Path("/one") / NFC, Path("/two") / NFD
        index = FilenameIndex([first, second, first])
        match = index.match(NFC)
        self.assertEqual(match.status, "ambiguous")
        self.assertIsNone(match.path)
        self.assertEqual(match.candidates, (first, second))
        with self.assertRaises(AmbiguousFilenameError) as raised:
            index.require_unique()
        self.assertEqual(raised.exception.candidates, (first, second))
        self.assertIs(FilenameIndex([first, first]).match(NFD).path, first)

    def test_case_collisions_require_explicit_insensitive_lookup(self):
        first, second = Path("/one/PHOTO.jpg"), Path("/one/photo.jpg")
        self.assertIs(FilenameIndex([first, second]).match(first.name).path, first)
        self.assertEqual(FilenameIndex([first, second], case_sensitive=False).match(first.name).status, "ambiguous")

    def test_legacy_mapping_rejects_normalized_collision(self):
        with self.assertRaises(AmbiguousFilenameError):
            unique_filename_mapping([(NFC, "one"), (NFD, "two")])

    def test_existing_key_formats_are_unchanged(self):
        for name in (NFC, NFD, " path/" + NFD + " ", "MiXeD.JPG"):
            expected = unicodedata.normalize("NFC", Path(name.strip()).name)
            self.assertEqual(normalize_media_name(name), expected)
            self.assertEqual(geo.normalize_filename_for_match(name), expected)
            self.assertEqual(_normalized_name(name), unicodedata.normalize("NFC", name).casefold())

    def test_lookup_leaves_source_and_cache_identities_unchanged(self):
        from gpx_editor_metrics_cache import cache_path
        from plot_metadata_utils import media_file_signature

        with tempfile.TemporaryDirectory() as directory:
            actual = Path(directory) / (NFD + ".gpx")
            actual.write_bytes(b"unchanged source")
            signature = media_file_signature(actual)
            source_hash = hashlib.sha256(actual.read_bytes()).hexdigest()
            cache_dir = Path(directory) / "cache"
            before = cache_path(actual, cache_dir)
            self.assertEqual(before.name, hashlib.sha256(str(actual.resolve()).encode("utf-8")).hexdigest() + ".json")
            found = FilenameIndex([actual]).match(NFC + ".gpx").path
            self.assertIs(found, actual)
            self.assertEqual(media_file_signature(found), signature)
            self.assertEqual(cache_path(found, cache_dir), before)
            self.assertEqual(hashlib.sha256(found.read_bytes()).hexdigest(), source_hash)


class FilenameIntegrationTests(unittest.TestCase):
    def test_sidecar_matching_preserves_payload_and_real_sidecar_path(self):
        from plot_metadata_utils import media_sidecar_matches_media, media_sidecar_path

        actual = Path("/moved") / NFD
        payload = {"source_filename": NFC, "photo_path": "/old/" + NFC}
        original = dict(payload)
        self.assertTrue(media_sidecar_matches_media(payload, actual))
        self.assertEqual(payload, original)
        self.assertEqual(media_sidecar_path(actual).name, NFD + ".json")
        self.assertFalse(media_sidecar_matches_media({"source_filename": "different.jpeg"}, actual))

    def test_control_media_track_order_matches_unicode_without_changing_paths(self):
        from media_track_builder import MediaTrackPoint, _control_stage_groups

        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "Trip.lst"
            control.write_text(NFC + " | 12:00 | 50, 8 | Place\n", encoding="utf-8")
            point = MediaTrackPoint(Path(directory) / NFD, 50.0, 8.0, None)
            stages = _control_stage_groups(control, [point])
            self.assertIs(stages[0].points[0], point)
            with self.assertRaises(AmbiguousFilenameError):
                _control_stage_groups(control, [point, MediaTrackPoint(Path("/other") / NFC, 50.0, 8.0, None)])

    def test_viewer_control_order_uses_unicode_directory_then_unique_basename(self):
        from adventure_map_workspace import MediaMapItem, ordered_media_viewer_paths

        root = Path("/copied")
        actual = root / unicodedata.normalize("NFD", "K\u00f6ln") / NFD
        other = root / "different" / NFC
        earlier = root / "earlier.jpg"
        media = [MediaMapItem(earlier, exposure_time="2000"), MediaMapItem(actual, exposure_time="2001"),
                 MediaMapItem(other, exposure_time="2002")]
        paths = ordered_media_viewer_paths(media, ["K\u00f6ln/" + NFC], project_directory=root)
        self.assertEqual(paths, [actual, earlier, other])
        # An ambiguous basename must not select either file by accident.
        self.assertEqual(ordered_media_viewer_paths(media, [NFC], project_directory=root),
                         [earlier, actual, other])

    def test_ambiguous_legacy_inventory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / "Trip.lst"
            control.write_text("", encoding="utf-8")
            inventory_path = control_media_inventory_path(control)
            inventory_path.write_text(json.dumps({"format_version": 1, "control_file": control.name, "media": {
                NFC: {"name": NFC, "state": "included"},
                NFD: {"name": NFD, "state": "excluded"},
            }}), encoding="utf-8")
            original = inventory_path.read_bytes()
            inventory = load_control_media_inventory(control)
            self.assertIn(NFC, inventory.ambiguous_names)
            self.assertIsNone(inventory.entry(NFC))
            media = root / NFD
            media.write_bytes(b"media")
            self.assertEqual(classify_project_media(inventory, [media], [NFC])[0].state, "unclassified")
            with self.assertRaises(AmbiguousFilenameError):
                mark_imported_media(control, [media])
            self.assertEqual(inventory_path.read_bytes(), original)

    def test_duplicate_basenames_stop_analysis_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "one" / NFC, root / "two" / NFD]
            for path in paths:
                path.parent.mkdir()
                path.write_bytes(b"media")
            inventory = load_control_media_inventory(root / "Trip.lst")
            self.assertEqual([item.state for item in classify_project_media(inventory, paths, [NFC])],
                             ["unclassified", "unclassified"])
            with self.assertRaises(AmbiguousFilenameError):
                build_control_media_inventory_payload(inventory, paths, [NFC])
            with patch.object(geo, "prefetch_media_metadata") as extractor:
                with self.assertRaises(AmbiguousFilenameError):
                    geo.analyze_media_updates(root, paths)
            extractor.assert_not_called()

    def test_colliding_control_actions_stop_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(geo, "prefetch_media_metadata") as extractor:
                with self.assertRaises(AmbiguousFilenameError):
                    geo.analyze_media_updates(Path(directory), [], actions={NFC: "skip", NFD: "refresh"})
            extractor.assert_not_called()

    def test_browser_keeps_original_paths_and_marks_ambiguous_inclusion(self):
        from GPSTrackShowGUI import GPXTrackerController

        items = [{"path": Path("/one") / NFD}]
        controller = SimpleNamespace(
            included_control_media_names=lambda: {filename_comparison_key(NFC, case_sensitive=False)},
            project_media_items=lambda: items,
            sorted_media_browser_rows_and_items=lambda rows, values: (rows, values),
        )
        rows, returned = GPXTrackerController.media_browser_table_rows(controller)
        self.assertEqual(rows[0]["included"], "yes")
        self.assertIs(returned[0]["path"], items[0]["path"])
        items.append({"path": Path("/two") / NFC})
        rows, _ = GPXTrackerController.media_browser_table_rows(controller)
        self.assertEqual([row["included"] for row in rows], ["ambiguous", "ambiguous"])

    def test_browser_control_membership_uses_explicit_unicode_and_case_matching(self):
        from GPSTrackShowGUI import GPXTrackerController

        controller = SimpleNamespace(
            _control_file_path=lambda: None,
            control_table_rows=[{"type": "IMG", "name": NFD}],
        )
        self.assertEqual(GPXTrackerController.included_control_media_names(controller),
                         {filename_comparison_key(NFC, case_sensitive=False)})


if __name__ == "__main__":
    unittest.main()
