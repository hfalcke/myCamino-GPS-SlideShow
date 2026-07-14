"""Regression tests for non-destructive Track Map synchronization."""

from __future__ import annotations

import json
import io
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

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
    control_table_search_indexes,
    control_table_recovery_path,
    media_viewer_control_row_index,
    next_control_table_search_position,
    parse_slideshow_control_line,
    serialize_slideshow_control_row,
    update_slideshow_control_row_cell,
    write_text_atomic,
)


class ControlFileTrackSyncTests(unittest.TestCase):
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
