"""Regression tests for non-destructive Track Map synchronization."""

from __future__ import annotations

import json
import io
import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from GetGeoLocations import (
    PhotoRecord,
    TrackInfo,
    TracksSummary,
    insert_classified_media_entry,
    parse_control_file_entries,
    remove_control_track_map_entries,
    run_with_options,
    update_control_special_map_entries,
)
from GPSTrackShowGUI import (
    CONTROL_TABLE_FILTERS,
    GeoLocationsOutputWriter,
    GPXTrackerController,
    control_file_update_requires_review,
    control_file_recovery_is_newer,
    control_file_signature,
    control_table_follow_selection_action,
    control_table_filter_anchor_index,
    control_table_search_indexes,
    control_table_recovery_path,
    display_control_row_type,
    media_viewer_control_row_index,
    next_control_table_search_position,
    parse_slideshow_control_line,
    required_main_window_height,
    serialize_slideshow_control_row,
    slideshow_process_is_running,
    track_endpoint_place_completeness,
    update_slideshow_control_row_cell,
    visible_control_row_indexes,
    write_text_atomic,
)


class ControlFileTrackSyncTests(unittest.TestCase):
    def test_following_selection_jumps_only_for_one_different_row(self):
        self.assertEqual(
            control_table_follow_selection_action(False, [4], 3),
            "inactive",
        )
        self.assertEqual(
            control_table_follow_selection_action(True, [4], 3),
            "jump",
        )
        self.assertEqual(
            control_table_follow_selection_action(True, [3], 3),
            "follow",
        )
        self.assertEqual(
            control_table_follow_selection_action(True, [3, 4], 3),
            "stop",
        )
        self.assertEqual(
            control_table_follow_selection_action(True, [], 3),
            "stop",
        )

    def test_track_endpoint_place_completeness_requires_both_map_variants(self):
        track = {
            "track_fingerprint": "fingerprint",
            "first_point": (50.0, 8.0),
            "last_point": (50.1, 8.1),
        }
        complete = {
            "track_fingerprint": "fingerprint",
            "track_endpoint_places": {
                "start": {"latitude": 50.0, "longitude": 8.0, "place": "Start"},
                "end": {"latitude": 50.1, "longitude": 8.1, "place": "End"},
            },
        }
        incomplete = {
            "track_fingerprint": "fingerprint",
            "track_endpoint_places": {
                "start": {"latitude": 50.0, "longitude": 8.0, "place": "Start"},
            },
        }
        self.assertEqual(
            track_endpoint_place_completeness(
                track,
                [complete, complete],
                150.0,
            ),
            (2, 2),
        )
        self.assertEqual(
            track_endpoint_place_completeness(
                track,
                [complete, incomplete],
                150.0,
            ),
            (2, 1),
        )

    def test_map_summary_flags_missing_endpoint_place_names(self):
        status = {
            "overview_exists": True,
            "overview_out_of_date": False,
            "track_total": 1,
            "standard_count": 1,
            "time_lapse_count": 1,
            "stale_track_count": 0,
            "summary_exists": True,
            "summary_out_of_date": False,
            "media_map_total": 0,
            "media_standard_count": 0,
            "media_time_lapse_count": 0,
            "media_stale_count": 0,
            "endpoint_place_missing_count": 2,
        }
        summary = GPXTrackerController._format_track_maps_summary_from_status(
            SimpleNamespace(),
            status,
        )
        self.assertIn("2 track endpoint place names missing", summary)
        self.assertIn("Update Metadata Extraction", summary)

    def test_music_help_explains_control_rows_playlists_and_albums(self):
        controller = SimpleNamespace(music_source=None)
        text = GPXTrackerController._music_directive_help_content(controller)
        self.assertIn("#MUSIC: Parameters", text)
        self.assertIn("$LABEL", text)
        self.assertIn("audio subdirectory", text)
        self.assertIn("folders containing songs", text)

    def test_control_table_filter_order_and_categories(self):
        self.assertEqual(
            [label for _key, label in CONTROL_TABLE_FILTERS],
            [
                "All Rows", "No Media", "Media", "Maps",
                "MUS – Music control", "IMG – Image", "VID – Video",
                "MAP – Overview map", "TRK – Track map",
                "BEF – Day before map", "AFT – Day after map",
                "LOC – Media location map", "DAT – Date",
            ],
        )
        rows = [{"type": value} for value in ("DAT", "MUS", "IMG", "TRK", "VID", "LOC")]
        self.assertEqual(visible_control_row_indexes(rows, "media"), [2, 4])
        self.assertEqual(visible_control_row_indexes(rows, "maps"), [3, 5])
        self.assertEqual(visible_control_row_indexes(rows, "mus"), [1])
        self.assertEqual(control_table_filter_anchor_index(rows, [4], "mus"), 1)

    def test_jump_to_show_requires_a_running_player(self):
        self.assertFalse(slideshow_process_is_running(None))
        self.assertTrue(
            slideshow_process_is_running(SimpleNamespace(poll=lambda: None))
        )
        self.assertFalse(
            slideshow_process_is_running(SimpleNamespace(poll=lambda: 0))
        )

    def test_main_window_reserves_space_for_video_normalization(self):
        self.assertGreater(
            required_main_window_height(True),
            required_main_window_height(False),
        )
        self.assertGreaterEqual(required_main_window_height(False), 780.0)

    def test_processing_output_does_not_wait_synchronously_for_main_thread(self):
        calls = []

        class Controller:
            def performSelectorOnMainThread_withObject_waitUntilDone_(
                self, selector, value, wait
            ):
                calls.append((selector, value, wait))

        writer = GeoLocationsOutputWriter(Controller())
        writer.write("first\nsecond\n")
        writer.flush()
        self.assertEqual([value for _selector, value, _wait in calls], ["first", "second"])
        self.assertTrue(all(wait is False for _selector, _value, wait in calls))

    def test_media_update_review_is_required_only_for_existing_row_moves(self):
        def plan(*items):
            return SimpleNamespace(media=SimpleNamespace(items=list(items)))

        metadata_only = SimpleNamespace(
            apply_update=True,
            included_count=1,
            reposition=False,
        )
        new_media = SimpleNamespace(
            apply_update=True,
            included_count=0,
            reposition=True,
        )
        existing_move = SimpleNamespace(
            apply_update=True,
            included_count=1,
            reposition=True,
        )
        self.assertFalse(control_file_update_requires_review(plan(metadata_only)))
        self.assertFalse(control_file_update_requires_review(plan(new_media)))
        self.assertTrue(control_file_update_requires_review(plan(existing_move)))

    def test_summary_path_is_derived_without_preparing_the_gpx(self):
        with TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            controller = SimpleNamespace(
                track_map_base="Camino",
                _track_images_dir=lambda: project_dir / "trackimages",
                _current_project_name=lambda: "Unused title",
            )
            with patch(
                "GPSTrackShowGUI.prepare_with_options",
                side_effect=AssertionError("GPX preparation must not run"),
            ):
                summary_path = GPXTrackerController._tracks_summary_json_path(controller)
            self.assertEqual(
                summary_path,
                (project_dir / "trackimages" / "Camino-summary.json").resolve(),
            )

    def test_control_file_signature_detects_file_changes(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "Camino-sorted.lst"
            path.write_text("photo.jpg\n", encoding="utf-8")
            original = control_file_signature(path)
            path.write_text("photo.jpg\nvideo.mov\n", encoding="utf-8")
            changed = control_file_signature(path)
            self.assertIsNotNone(original)
            self.assertIsNotNone(changed)
            self.assertNotEqual(original, changed)

    def test_unchanged_control_file_reuses_retained_editor_state(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "Camino-sorted.lst"
            path.write_text("photo.jpg | 12:00 | kein GPS | kein Ort\n", encoding="utf-8")
            rows = [{"type": "IMG", "name": "photo.jpg"}]
            preview_cache = {"photo.jpg": object()}
            raised = []
            controller = SimpleNamespace(
                control_table_path=path.resolve(),
                control_table_window=object(),
                control_table_dirty=False,
                control_table_file_signature=control_file_signature(path),
                control_table_rows=rows,
                control_table_preview_cache=preview_cache,
                _show_control_table_window=lambda requested: raised.append(Path(requested)),
            )
            result = GPXTrackerController.load_slideshow_control_file(controller, path)
            self.assertTrue(result)
            self.assertEqual(raised, [path.resolve()])
            self.assertIs(controller.control_table_rows, rows)
            self.assertIs(controller.control_table_preview_cache, preview_cache)

    def test_only_newer_recovery_copy_takes_precedence(self):
        with TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            control_path = project_dir / "Camino-sorted.lst"
            recovery_path = control_table_recovery_path(control_path)
            control_path.write_text("saved\n", encoding="utf-8")
            recovery_path.parent.mkdir(parents=True, exist_ok=True)
            recovery_path.write_text("recovered\n", encoding="utf-8")
            os.utime(control_path, ns=(1_000_000_000, 1_000_000_000))
            os.utime(recovery_path, ns=(2_000_000_000, 2_000_000_000))
            self.assertTrue(control_file_recovery_is_newer(control_path, recovery_path))
            os.utime(recovery_path, ns=(500_000_000, 500_000_000))
            self.assertFalse(control_file_recovery_is_newer(control_path, recovery_path))

    def test_media_filter_exposes_only_non_media_model_indexes(self):
        rows = [
            {"type": "DAT"},
            {"type": "IMG"},
            {"type": "TRK"},
            {"type": "VID"},
            {"type": "AFT"},
            {"type": "MUS"},
        ]
        self.assertEqual(
            visible_control_row_indexes(rows, hide_media=True),
            [0, 2, 4, 5],
        )
        self.assertEqual(visible_control_row_indexes(rows, hide_media=False), [0, 1, 2, 3, 4, 5])
        self.assertEqual(control_table_filter_anchor_index(rows, [2], hide_media=True), 2)
        self.assertEqual(control_table_filter_anchor_index(rows, [3], hide_media=True), 2)
        self.assertEqual(control_table_filter_anchor_index(rows, [4], hide_media=False), 4)

    def test_control_table_search_matches_serialized_text_and_numbers(self):
        rows = [
            parse_slideshow_control_line("#Map: 0075_stage.png"),
            parse_slideshow_control_line("IMG_0782.jpeg | 12:34 | 50.123456, 7.123456 | Cologne"),
            parse_slideshow_control_line("IMG_0791.jpeg | 14:00 | kein GPS | kein Ort"),
        ]
        self.assertEqual(control_table_search_indexes(rows, "0782"), [1])
        self.assertEqual(control_table_search_indexes(rows, "50.123"), [1])
        self.assertEqual(control_table_search_indexes(rows, "cologne"), [1])
        self.assertEqual(control_table_search_indexes(rows, "#map:"), [0])
        self.assertEqual(control_table_search_indexes(rows, ""), [])

    def test_control_table_search_navigation_starts_at_expected_end(self):
        self.assertEqual(next_control_table_search_position(-1, 4, 1), 0)
        self.assertEqual(next_control_table_search_position(-1, 4, -1), 3)
        self.assertEqual(next_control_table_search_position(3, 4, 1), 0)
        self.assertEqual(next_control_table_search_position(0, 4, -1), 3)

    def test_media_viewer_maps_visible_item_back_to_control_row(self):
        items = [{"index": 7}, {"index": 12}]
        self.assertEqual(media_viewer_control_row_index("control", items, 1), 12)
        self.assertIsNone(media_viewer_control_row_index("project", items, 1))
        self.assertIsNone(media_viewer_control_row_index("control", items, 3))

    def test_control_table_recovery_is_atomic_and_kept_outside_project_media(self):
        with TemporaryDirectory() as temporary:
            control = Path(temporary) / "Trip-sorted.lst"
            recovery = control_table_recovery_path(control)
            write_text_atomic(recovery, "#Datum: Montag, 01.01.2024\n")
            self.assertEqual(recovery.parent.name, ".mycamino-control-backups")
            self.assertEqual(recovery.read_text(encoding="utf-8"), "#Datum: Montag, 01.01.2024\n")

    def test_gui_round_trips_special_map_directives(self):
        before = parse_slideshow_control_line("#MapBefore: 0001_stage.png")
        after = parse_slideshow_control_line("#MapAfter: 0001_stage.png")
        self.assertEqual(before["type"], "BEF")
        self.assertEqual(after["type"], "AFT")
        self.assertEqual(serialize_slideshow_control_row(before), "#MapBefore: 0001_stage.png")
        self.assertEqual(serialize_slideshow_control_row(after), "#MapAfter: 0001_stage.png")

    def test_gui_round_trips_music_directive_as_its_own_row(self):
        row = parse_slideshow_control_line("#MUSIC: #ON, #JUMP $STAGE")
        self.assertEqual(row["type"], "MUS")
        self.assertEqual(row["name"], "#ON, #JUMP $STAGE")
        self.assertEqual(serialize_slideshow_control_row(row), "#MUSIC: #ON, #JUMP $STAGE")
        merge_entry = parse_control_file_entries(["#MUSIC: #ON, #JUMP $STAGE"])[0]
        self.assertEqual((merge_entry["type"], merge_entry["line"]), ("music", "#MUSIC: #ON, #JUMP $STAGE"))

    def test_gui_round_trips_media_map_directive(self):
        row = parse_slideshow_control_line("#MediaMap: trip-media-2024-07-14.png")
        self.assertEqual(row["type"], "LOC")
        self.assertEqual(serialize_slideshow_control_row(row), "#MediaMap: trip-media-2024-07-14.png")

    def test_editing_track_type_to_aft_updates_the_saved_keyword(self):
        row = parse_slideshow_control_line("#Map: 0075_stage.png")
        update_slideshow_control_row_cell(row, "type", "Aft")
        self.assertEqual(row["type"], "AFT")
        self.assertEqual(row["keyword"], "MapAfter")
        self.assertEqual(serialize_slideshow_control_row(row), "#MapAfter: 0075_stage.png")

    def test_descriptive_type_choice_is_stored_as_canonical_short_type(self):
        row = parse_slideshow_control_line("#Map: 0075_stage.png")
        update_slideshow_control_row_cell(row, "type", "AFT - Day after map")
        self.assertEqual(row["type"], "AFT")
        self.assertEqual(row["keyword"], "MapAfter")
        self.assertEqual(display_control_row_type(row["type"]), "AFT - Day after map")

    def test_long_type_name_without_prefix_is_accepted(self):
        row = parse_slideshow_control_line("#Map: 0075_stage.png")
        update_slideshow_control_row_cell(row, "type", "Music control")
        self.assertEqual(row["type"], "MUS")
        self.assertEqual(serialize_slideshow_control_row(row), "#MUSIC: 0075_stage.png")

    def test_editing_special_type_back_to_track_updates_the_saved_keyword(self):
        row = parse_slideshow_control_line("#MapAfter: 0075_stage.png")
        update_slideshow_control_row_cell(row, "type", "TRK")
        self.assertEqual(serialize_slideshow_control_row(row), "#Map: 0075_stage.png")

    def test_special_map_does_not_satisfy_normal_track_map(self):
        with TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            control_path = project_dir / "Camino-sorted.lst"
            control_path.write_text(
                "#Datum: Sonntag, 14.07.2024\n#MapBefore: 0001_stage.png\nphoto.jpg | 12:00 | kein GPS | kein Ort\n",
                encoding="utf-8",
            )
            summary_path = project_dir / "Camino-summary.json"
            summary_path.write_text(
                json.dumps({"tracks": [{"nr": 1, "start_time": "15.07.2024 08:00", "track_plot_image_filename": "0001_stage.png"}]}),
                encoding="utf-8",
            )
            run_with_options(project_dir, photolist=control_path, merge_tracks=summary_path, stdout=io.StringIO())
            self.assertIn("#Map: 0001_stage.png", control_path.read_text(encoding="utf-8"))

    def test_special_map_filename_update_preserves_keyword(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "list.lst"
            path.write_text("#MapBefore: 0001_old.png\n#MapAfter: 0002_old.png\n", encoding="utf-8")
            changed = update_control_special_map_entries(path, {"0001_old.png": "0001_new.png"})
            self.assertEqual(changed, 1)
            self.assertEqual(path.read_text(), "#MapBefore: 0001_new.png\n#MapAfter: 0002_old.png\n")

    def test_special_map_filename_update_preserves_neighboring_music_directive(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "list.lst"
            path.write_text("#MUSIC: #JUMP $MORNING\n#MapBefore: 0001_old.png\n", encoding="utf-8")
            update_control_special_map_entries(path, {"0001_old.png": "0001_new.png"})
            self.assertEqual(path.read_text(), "#MUSIC: #JUMP $MORNING\n#MapBefore: 0001_new.png\n")

    def test_merged_adjacent_media_creates_special_section_without_moving_existing_rows(self):
        track = TrackInfo(
            datetime(2024, 7, 15, 8).astimezone(),
            "0001_stage.png",
            1,
            10.0,
            50.0,
            8.0,
            50.1,
            8.1,
        )
        entries = parse_control_file_entries(
            [
                "#Datum: Montag, 15.07.2024",
                "#Map: 0001_stage.png",
                "existing.jpg | 12:00 | kein GPS | kein Ort",
            ]
        )
        path = Path("new.jpg")
        record = PhotoRecord(
            "new.jpg",
            "new.jpg",
            path,
            path.with_suffix(".jpg.json"),
            datetime(2024, 7, 14, 9).astimezone(),
            None,
            None,
            None,
            None,
            "test",
            False,
            False,
        )
        insert_classified_media_entry(entries, record, TracksSummary(None, [track], set()), True)
        lines = [entry["line"] for entry in entries]
        self.assertLess(lines.index("#MapBefore: 0001_stage.png"), lines.index("#Map: 0001_stage.png"))
        self.assertIn("existing.jpg | 12:00 | kein GPS | kein Ort", lines)
        self.assertTrue(any(line.startswith("new.jpg | 09:00") for line in lines))

    def test_replacing_stale_2024_map_preserves_date_and_media(self):
        with TemporaryDirectory() as temporary:
            project_dir = Path(temporary)
            control_path = project_dir / "Camino-sorted.lst"
            stale_map = "0089_Old name_Camino.png"
            current_map = "0089_Current name_Camino.png"
            media_line = "IMG_20240714.jpeg | 12:34 | 42.000000, -4.000000 | Place"
            control_path.write_text(
                "#Overviewmap: Camino.png\n"
                "#Datum: Sonntag, 14.07.2024\n"
                f"#Map: {stale_map}\n"
                f"{media_line}\n",
                encoding="utf-8",
            )
            (project_dir / current_map).write_bytes(b"map")
            summary_path = project_dir / "Camino-summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "output_image": str(project_dir / "Camino.png"),
                        "tracks": [
                            {
                                "nr": 89,
                                "original_sequence_number": 89,
                                "start_time": "14.07.2024 08:00:00",
                                "track_plot_image_filename": current_map,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            removed = remove_control_track_map_entries(control_path, [stale_map])
            run_with_options(
                project_dir,
                photolist=control_path,
                merge_tracks=summary_path,
                sort_date_sections_by_tracks=True,
                stdout=io.StringIO(),
            )

            self.assertEqual(removed, 1)
            text = control_path.read_text(encoding="utf-8")
            self.assertNotIn(stale_map, text)
            self.assertIn(f"#Map: {current_map}", text)
            self.assertIn("#Datum: Sonntag, 14.07.2024", text)
            self.assertIn(media_line, text)
            entries = parse_control_file_entries(text.splitlines())
            map_entry = next(entry for entry in entries if entry["type"] == "map")
            self.assertEqual(map_entry["date"].year, 2024)


if __name__ == "__main__":
    unittest.main()
