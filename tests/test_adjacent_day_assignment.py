"""Tests for adjacent-day media assignment and section ordering."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from GetGeoLocations import (
    PhotoRecord,
    TrackInfo,
    TracksSummary,
    assign_adjacent_day_track,
    build_control_sections,
    add_media_maps_to_control_entries,
    media_map_output_filename,
    parse_control_file_entries,
    render_media_map_specs,
    write_sorted_output,
)


def photo(name, value, latitude=None, longitude=None):
    path = Path(name)
    return PhotoRecord(
        source_filename=name,
        display_filename=name,
        photo_path=path,
        json_path=path.with_suffix(path.suffix + ".json"),
        photo_datetime=value.astimezone(),
        latitude=latitude,
        longitude=longitude,
        place=None,
        place_details=None,
        source="test",
        geocode_requested=False,
        place_updated=False,
    )


def track(number, value, name=None, length_km=10.0, start=(0.0, 0.0), end=(0.0, 0.1)):
    return TrackInfo(
        start_time=value.astimezone(),
        track_plot_image_filename=name or f"{number:04d}_track.png",
        original_sequence_number=number,
        length_km=length_km,
        start_latitude=start[0],
        start_longitude=start[1],
        end_latitude=end[0],
        end_longitude=end[1],
    )


class AdjacentDayAssignmentTests(unittest.TestCase):
    def test_day_before_uses_start_and_day_after_uses_end(self):
        stage = track(1, datetime(2024, 7, 15, 8), length_km=10.0, start=(50.0, 8.0), end=(50.1, 8.1))
        before = assign_adjacent_day_track(photo("before.jpg", datetime(2024, 7, 14, 12), 50.0, 8.0), [stage])
        after = assign_adjacent_day_track(photo("after.jpg", datetime(2024, 7, 16, 12), 50.1, 8.1), [stage])
        self.assertEqual(before.relation, "before")
        self.assertEqual(after.relation, "after")

    def test_radius_boundary_is_inclusive_and_outside_is_rejected(self):
        stage = track(1, datetime(2024, 7, 15, 8), length_km=2.0, start=(0.0, 0.0))
        inside = photo("inside.jpg", datetime(2024, 7, 14, 12), 0.0, 0.0089)
        outside = photo("outside.jpg", datetime(2024, 7, 14, 12), 0.0, 0.0091)
        self.assertIsNotNone(assign_adjacent_day_track(inside, [stage]))
        self.assertIsNone(assign_adjacent_day_track(outside, [stage]))

    def test_no_gps_prefers_next_track_when_both_sides_exist(self):
        previous = track(1, datetime(2024, 7, 13, 8))
        following = track(2, datetime(2024, 7, 15, 8))
        assignment = assign_adjacent_day_track(photo("unknown.jpg", datetime(2024, 7, 14, 12)), [previous, following])
        self.assertEqual(assignment.relation, "before")
        self.assertEqual(assignment.track.original_sequence_number, 2)

    def test_gps_chooses_nearest_qualifying_endpoint(self):
        previous = track(1, datetime(2024, 7, 13, 8), length_km=100.0, end=(0.0, 0.0))
        following = track(2, datetime(2024, 7, 15, 8), length_km=100.0, start=(0.0, 0.2))
        assignment = assign_adjacent_day_track(
            photo("near-previous.jpg", datetime(2024, 7, 14, 12), 0.0, 0.01),
            [previous, following],
        )
        self.assertEqual(assignment.relation, "after")
        self.assertEqual(assignment.track.original_sequence_number, 1)

    def test_missing_track_geometry_rejects_gps_but_accepts_no_gps(self):
        stage = track(1, datetime(2024, 7, 15, 8), length_km=None, start=(None, None))
        self.assertIsNone(assign_adjacent_day_track(photo("gps.jpg", datetime(2024, 7, 14), 1.0, 1.0), [stage]))
        self.assertIsNotNone(assign_adjacent_day_track(photo("no-gps.jpg", datetime(2024, 7, 14)), [stage]))

    def test_sections_split_mixed_date_and_leave_unassigned_at_end(self):
        stage = track(1, datetime(2024, 7, 15, 8), length_km=2.0, start=(0.0, 0.0))
        records = [
            photo("before.jpg", datetime(2024, 7, 14, 9), 0.0, 0.001),
            photo("far.jpg", datetime(2024, 7, 14, 10), 10.0, 10.0),
            photo("stage.jpg", datetime(2024, 7, 15, 11), 0.0, 0.0),
        ]
        sections = build_control_sections(records, TracksSummary("overview.png", [stage], set()), True)
        self.assertEqual([section["maps"] for section in sections], [[], [("MapBefore", "0001_track.png")], [("Map", "0001_track.png")]])
        self.assertEqual([item.source_filename for item in sections[0]["records"]], ["far.jpg"])
        self.assertEqual([item.source_filename for item in sections[1]["records"]], ["before.jpg"])

    def test_leftover_date_receives_media_map_directive(self):
        stage = track(1, datetime(2024, 7, 15, 8), "0001_track.png")
        records = [photo("far.jpg", datetime(2024, 7, 12, 10), 10.0, 10.0)]
        sections = build_control_sections(
            records,
            TracksSummary("overview.png", [stage], set()),
            False,
            {date(2024, 7, 12): "trip-media-2024-07-12.png"},
        )
        self.assertEqual(sections[0]["maps"], [("MediaMap", "trip-media-2024-07-12.png")])
        self.assertEqual(sections[0]["relation"], "media")

    def test_writer_creates_and_references_media_location_map(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "My Trip-sorted.lst"
            record = photo("far.jpg", datetime(2024, 7, 12, 10), 50.0, 7.0)
            with patch("GetGeoLocations.render_media_location_map") as render:
                write_sorted_output(
                    [record],
                    output,
                    None,
                    False,
                    {"output_dir": root / "trackimages", "filename_base": "My Trip"},
                )
            self.assertEqual(render.call_count, 1)
            self.assertIn("#MediaMap: My_Trip-media-2024-07-12.png", output.read_text(encoding="utf-8"))

    def test_media_map_variant_keeps_canonical_control_name(self):
        canonical = "Trip-media-2024-07-12.png"
        self.assertEqual(media_map_output_filename(canonical, "standard"), canonical)
        self.assertEqual(
            media_map_output_filename(canonical, "time-lapse"),
            "Trip-media-2024-07-12-timelapse.png",
        )

    def test_media_map_renderer_passes_selected_layout_to_shared_renderer(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            specs = [
                {
                    "date": date(2024, 7, 12),
                    "filename": "Trip-media-2024-07-12.png",
                    "coordinates": [(50.0, 7.0), (50.1, 7.1)],
                }
            ]
            with patch("GetGeoLocations.render_media_location_map") as render:
                render_media_map_specs(
                    specs,
                    root / "Trip-sorted.lst",
                    {
                        "output_dir": root / "trackimages",
                        "map_layout": "time-lapse",
                        "track_edge_margin_fraction": 0.07,
                    },
                )
        args, kwargs = render.call_args
        self.assertEqual(args[2].name, "Trip-media-2024-07-12-timelapse.png")
        self.assertEqual(kwargs["map_layout"], "time-lapse")
        self.assertEqual(kwargs["track_edge_margin_fraction"], 0.07)

    def test_merge_regenerates_existing_media_map_from_complete_section(self):
        entries = parse_control_file_entries(
            [
                "#Datum: Freitag, 12.07.2024",
                "#MediaMap: Trip-media-2024-07-12.png",
                "one.jpg | 10:00 | 50.0, 7.0 | -",
                "two.jpg | 11:00 | 50.1, 7.1 | -",
            ]
        )
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "Trip-sorted.lst"
            with patch("GetGeoLocations.render_media_map_specs") as render:
                inserted = add_media_maps_to_control_entries(
                    entries,
                    output,
                    {"output_dir": Path(temporary) / "trackimages", "map_layout": "time-lapse"},
                )
        self.assertEqual(inserted, 0)
        specs = render.call_args.args[0]
        self.assertEqual(specs[0]["coordinates"], [(50.0, 7.0), (50.1, 7.1)])


if __name__ == "__main__":
    unittest.main()
