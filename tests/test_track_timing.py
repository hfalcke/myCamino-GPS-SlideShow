"""Deterministic coverage for shared GPX timing and time-lapse sampling."""

from __future__ import annotations

import unittest
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from GPSTrackShow import (
    GPSTrackShowApp,
    PhotoListEntry,
    TimeLapseStage,
    adjacent_stage_map_index,
    align_datetime_timezone,
    build_time_lapse_media_queue,
    format_place_for_time_lapse,
    format_time_lapse_metrics,
    interpolate_timeline_point,
    interpolate_timeline_state,
    parse_map_directive,
    parse_photo_entry,
    parse_control_datetime,
    parse_iso_datetime,
    timed_points_from_metadata,
    time_lapse_clock_datetime,
    timeline_sample_count,
)
from gpx_tracks_table import extract_track_points, upgrade_timed_track_sidecars
from track_timing_utils import haversine_km, repair_timed_points, timed_points_payload


class TrackTimingTests(unittest.TestCase):
    def test_stage_navigation_uses_media_maps_without_gpx_tracks(self):
        lines = [
            "#Datum: Monday, 15.07.2024",
            "#MediaMap: trip-media-2024-07-15.png",
            "one.jpeg | 09:00 | 50.0, 7.0 | Cologne",
            "#Datum: Tuesday, 16.07.2024",
            "#MediaMap: trip-media-2024-07-16.png",
            "two.jpeg | 10:00 | 50.1, 7.1 | Bonn",
        ]
        self.assertEqual(adjacent_stage_map_index(lines, 2, True), 4)
        self.assertEqual(adjacent_stage_map_index(lines, 5, False), 1)

    def test_adjacent_day_map_directives_remain_distinct(self):
        before = parse_map_directive("#MapBefore: 0001_stage.png")
        normal = parse_map_directive("#Map: 0001_stage.png")
        after = parse_map_directive("#MapAfter: 0001_stage.png")
        media = parse_map_directive("#MediaMap: trip-media-2024-07-14.png")
        self.assertEqual((before.filename, before.relation), ("0001_stage.png", "Day before"))
        self.assertEqual((normal.filename, normal.relation), ("0001_stage.png", None))
        self.assertEqual((after.filename, after.relation), ("0001_stage.png", "Day after"))
        self.assertEqual((media.filename, media.relation), ("trip-media-2024-07-14.png", ""))
        self.assertTrue(media.is_special)

    def test_music_directive_is_not_treated_as_a_map(self):
        self.assertIsNone(parse_map_directive("#MUSIC: #JUMP $EVENING"))

    def test_photo_entry_keeps_four_column_place_text(self):
        entry = parse_photo_entry("photo.jpg | 12:00 | 50.0, 8.0 | Cologne")
        self.assertEqual(entry.place, "Cologne")

    def test_special_stage_keeps_its_date_and_carries_the_next_date_separately(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.current_date = "Sunday, 14.07.2024"
        app.playlist_lines = [
            "#MapBefore: stage.png",
            "before.jpeg | 09:30 | - | -",
            "#Datum: Monday, 15.07.2024",
            "#Map: stage.png",
        ]
        stage = app._collect_time_lapse_stage(0, "stage.png", "Day before")
        self.assertEqual(stage.date_text, "Sunday, 14.07.2024")
        self.assertEqual(stage.media_date_texts, ["Sunday, 14.07.2024"])
        self.assertEqual(stage.next_date_text, "Monday, 15.07.2024")

    def test_haversine_returns_kilometres(self):
        self.assertAlmostEqual(haversine_km(0.0, 0.0, 1.0, 0.0), 111.2, delta=0.2)

    def test_missing_middle_time_is_distance_interpolated(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        repaired = repair_timed_points(
            [
                {"lat": 50.0, "lon": 8.0, "time": start},
                {"lat": 50.0, "lon": 8.01, "time": None},
                {"lat": 50.0, "lon": 8.03, "time": start + timedelta(hours=3)},
            ]
        )
        self.assertTrue(repaired[1]["estimated"])
        self.assertGreater(repaired[1]["time"], start)
        self.assertLess(repaired[1]["time"], repaired[2]["time"])
        self.assertGreater((repaired[1]["time"] - start).total_seconds(), 3600)

    def test_absent_times_use_walking_speed_and_are_monotonic(self):
        repaired = repair_timed_points(
            [{"lat": 50.0, "lon": 8.0}, {"lat": 50.0, "lon": 8.01}, {"lat": 50.0, "lon": 8.02}]
        )
        self.assertTrue(all(point["estimated"] for point in repaired))
        self.assertLess(repaired[0]["time"], repaired[1]["time"])
        self.assertLess(repaired[1]["time"], repaired[2]["time"])

    def test_backwards_timestamp_is_repaired_to_monotonic_sequence(self):
        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        repaired = repair_timed_points(
            [
                {"lat": 50.0, "lon": 7.0, "time": start},
                {"lat": 50.01, "lon": 7.0, "time": start - timedelta(minutes=1)},
                {"lat": 50.02, "lon": 7.0, "time": start + timedelta(minutes=10)},
            ]
        )
        self.assertGreater(repaired[1]["time"], repaired[0]["time"])
        self.assertLess(repaired[1]["time"], repaired[2]["time"])
        self.assertTrue(repaired[1]["estimated"])

    def test_time_lapse_uses_50hz_samples_and_elapsed_time(self):
        self.assertEqual(timeline_sample_count(30.0), 1500)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        points = [
            {"lat": 0.0, "lon": 0.0, "time": start},
            {"lat": 10.0, "lon": 0.0, "time": start + timedelta(seconds=90)},
            {"lat": 20.0, "lon": 0.0, "time": start + timedelta(seconds=100)},
        ]
        lat, _lon = interpolate_timeline_point(points, 0.5)
        self.assertAlmostEqual(lat, 5.56, places=1)

    def test_timed_payload_and_interpolation_include_distance_and_height(self):
        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        payload = timed_points_payload(
            [
                {"lat": 50.0, "lon": 8.0, "time": start, "elevation_m": 100.0},
                {"lat": 50.0, "lon": 8.02, "time": start + timedelta(hours=1), "elevation_m": 200.0},
            ]
        )
        self.assertEqual(payload[0]["cumulative_distance_km"], 0.0)
        self.assertGreater(payload[1]["cumulative_distance_km"], 1.0)
        points = timed_points_from_metadata({"timed_track_points": payload})
        state = interpolate_timeline_state(points, 0.5)
        self.assertAlmostEqual(state["elevation_m"], 150.0)
        self.assertAlmostEqual(state["stage_distance_km"], payload[1]["cumulative_distance_km"] / 2.0)
        self.assertEqual(state["time"], start + timedelta(minutes=30))

    def test_gpx_elevation_is_preserved_for_track_sidecars(self):
        track = ET.fromstring(
            '<trk xmlns="http://www.topografix.com/GPX/1/1"><trkseg>'
            '<trkpt lat="50.0" lon="8.0"><ele>123.4</ele><time>2024-01-01T10:00:00Z</time></trkpt>'
            '</trkseg></trk>'
        )
        points = extract_track_points(track)
        self.assertEqual(points[0]["elevation_m"], 123.4)

    def test_time_lapse_metric_and_place_rows_are_compact(self):
        self.assertEqual(
            format_time_lapse_metrics(1234.4, 12.34, 456.7),
            ("Total traveled: 1234 km", "Stage traveled: 12,3 km", "Height: 457 m"),
        )
        self.assertEqual(
            format_place_for_time_lapse("Köln-Innenstadt (Nordrhein-Westfalen), Dom"),
            "Köln-Innenstadt (Nordrhein-Westfalen) - Dom",
        )
        self.assertIsNone(format_place_for_time_lapse("kein Ort"))

    def test_time_lapse_clock_uses_late_media_time_only_after_track_end(self):
        marker_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        late_media_time = marker_time + timedelta(hours=2)
        early_media_time = marker_time - timedelta(hours=1)
        self.assertEqual(time_lapse_clock_datetime(marker_time, 0.99, late_media_time), marker_time)
        self.assertEqual(time_lapse_clock_datetime(marker_time, 1.0, late_media_time), late_media_time)
        self.assertEqual(time_lapse_clock_datetime(marker_time, 1.0, early_media_time), marker_time)

    def test_resume_state_captures_time_lapse_stage_and_visible_media(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.completed_naturally = False
        app.config = SimpleNamespace(inputlist=Path("/tmp/adventure-sorted.lst"))
        app.time_lapse_active = True
        app.time_lapse_stage = TimeLapseStage(3, 8, "map.png", None, [5], [], [])
        app.time_lapse_progress = 0.625
        entry = PhotoListEntry("photo.jpeg", "12:00", None, None, None)
        app.time_lapse_current_media = (5, entry)
        app.current_display_index = 3
        app.playlist_index = 6
        app.playlist_lines = ["#Overviewmap: map.png", "#Datum: 01.01.2024", "#Comment: test", "#Map: stage.png", "#Datum: 01.01.2024", "photo.jpeg"]
        state = app._resume_state_payload()
        self.assertEqual(state["playlist_index"], 3)
        self.assertEqual(state["media_index"], 5)
        self.assertEqual(state["time_lapse_progress"], 0.625)
        self.assertEqual(state["mode"], "time-lapse")

    def test_time_lapse_resume_rewinds_media_row_to_its_stage_map(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(resume_index=5, start_track=1)
        app.playlist_lines = [
            "#Overviewmap: overview.png",
            "#Datum: 01.01.2024",
            "#Comment: test",
            "#Map: stage.png",
            "#Datum: 01.01.2024",
            "photo.jpeg",
        ]
        app.time_lapse_active = True
        app.resume_media_index_pending = None
        app.resume_standard_map_index_pending = None
        app.resume_start_pending = True
        primed = []
        app._prime_context_before_index = primed.append
        app._apply_start_track()
        self.assertEqual(app.playlist_index, 3)
        self.assertEqual(app.resume_media_index_pending, 5)
        self.assertEqual(primed, [3])

    def test_time_lapse_resume_rewinds_adjacent_media_to_special_map(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(resume_index=4, start_track=1)
        app.playlist_lines = [
            "#Overviewmap: overview.png",
            "#Datum: 31.12.2023",
            "#MapBefore: stage.png",
            "#Datum: 31.12.2023",
            "photo.jpeg",
            "#Datum: 01.01.2024",
            "#Map: stage.png",
        ]
        app.time_lapse_active = True
        app.resume_media_index_pending = None
        app.resume_standard_map_index_pending = None
        app.resume_start_pending = True
        primed = []
        app._prime_context_before_index = primed.append
        app._apply_start_track()
        self.assertEqual(app.playlist_index, 2)
        self.assertEqual(app.resume_media_index_pending, 4)
        self.assertEqual(primed, [2])

    def test_time_lapse_resume_starts_music_directive_at_exact_row(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(resume_index=4, start_track=1)
        app.playlist_lines = [
            "#Overviewmap: overview.png",
            "#Datum: 01.01.2024",
            "#Map: stage.png",
            "photo.jpeg",
            "#MUSIC: #ON, $STAGE",
            "next.jpeg",
        ]
        app.time_lapse_active = True
        app.resume_media_index_pending = None
        app.resume_standard_map_index_pending = None
        app.resume_start_pending = True
        primed = []
        app._prime_context_before_index = primed.append
        app._apply_start_track()
        self.assertEqual(app.playlist_index, 4)
        self.assertIsNone(app.resume_media_index_pending)
        self.assertEqual(primed, [4])

    def test_iso_timestamp_and_metadata_payload_are_parsed(self):
        first = "2020-10-17T11:00:00+00:00"
        second = "2020-10-17T12:00:00+00:00"
        self.assertEqual(parse_iso_datetime(first), datetime(2020, 10, 17, 11, 0, tzinfo=timezone.utc))
        points = timed_points_from_metadata(
            {
                "timed_track_points": [
                    {"lat": 50.0, "lon": 7.0, "time_iso": first, "estimated": False},
                    {"lat": 50.1, "lon": 7.1, "time_iso": second, "estimated": False},
                ]
            }
        )
        self.assertEqual([point["time"] for point in points], [parse_iso_datetime(first), parse_iso_datetime(second)])

    def test_interpolation_falls_back_when_times_are_missing(self):
        points = [
            {"lat": 0.0, "lon": 0.0, "time": None},
            {"lat": 10.0, "lon": 10.0, "time": None},
        ]
        self.assertEqual(interpolate_timeline_point(points, 0.25), (2.5, 2.5))

    def test_all_untimed_media_remain_reachable_at_stage_end(self):
        entry_a = PhotoListEntry("a.jpeg", "10:00", None, None, None)
        entry_b = PhotoListEntry("b.jpeg", None, None, None, None)
        entry_c = PhotoListEntry("c.jpeg", "11:00", None, None, None)
        queue = build_time_lapse_media_queue([(None, 4, entry_a), (0.5, 2, entry_c), (None, 5, entry_b)])
        self.assertEqual([(fraction, row) for fraction, row, _entry in queue], [(0.5, 2), (1.0, 4), (1.0, 5)])

    def test_control_list_date_and_time_fallback_keeps_track_timezone(self):
        reference = datetime(2020, 10, 17, 10, 0, tzinfo=timezone.utc)
        parsed = parse_control_datetime("Samstag, 17.10.2020", "11:03", reference)
        self.assertEqual(parsed, datetime(2020, 10, 17, 11, 3, tzinfo=timezone.utc))
        naive = datetime(2020, 10, 17, 11, 3)
        self.assertEqual(align_datetime_timezone(naive, reference), parsed)

    def test_sidecar_upgrade_finds_existing_map_by_fingerprint(self):
        start = datetime(2020, 10, 17, 11, 0, tzinfo=timezone.utc)
        context = {
            "tracks": [
                {
                    "table_number": 1,
                    "track_fingerprint": "track-one",
                    "point_records": [
                        {"lat": 50.0, "lon": 7.0, "time": start, "elevation_m": 100.0},
                        {"lat": 50.1, "lon": 7.1, "time": start + timedelta(hours=1), "elevation_m": 180.0},
                    ],
                }
            ]
        }
        with TemporaryDirectory() as temp_dir:
            sidecar = Path(temp_dir) / "existing-track-map.json"
            sidecar.write_text(json.dumps({"track_fingerprint": "track-one", "preserved": True}), encoding="utf-8")
            with patch("gpx_tracks_table.prepare_with_options", return_value=context):
                report = upgrade_timed_track_sidecars("unused.gpx", temp_dir)
            updated = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(report["updated"], [1])
        self.assertTrue(updated["preserved"])
        self.assertEqual(len(updated["timed_track_points"]), 2)
        self.assertIn("cumulative_distance_km", updated["timed_track_points"][1])
        self.assertEqual(updated["timed_track_points"][1]["elevation_m"], 180.0)

    def test_sidecar_upgrade_safely_matches_legacy_identity_fields(self):
        start = datetime(2020, 10, 17, 11, 0, tzinfo=timezone.utc)
        context = {
            "tracks": [
                {
                    "table_number": 1,
                    "name": "Legacy Track",
                    "start_time": start,
                    "first_point": (50.0, 7.0),
                    "last_point": (50.1, 7.1),
                    "track_fingerprint": "legacy-track-fingerprint",
                    "point_records": [
                        {"lat": 50.0, "lon": 7.0, "time": start, "elevation_m": 100.0},
                        {"lat": 50.1, "lon": 7.1, "time": start + timedelta(hours=1), "elevation_m": 180.0},
                    ],
                }
            ]
        }
        metadata = {
            "track_number": 1,
            "track_name": "Legacy Track",
            "track_start_time": "17.10.2020 13:00:00",
            "start_point": {"lat": 50.0, "lon": 7.0},
            "end_point": {"lat": 50.1, "lon": 7.1},
        }
        with TemporaryDirectory() as temp_dir:
            sidecar = Path(temp_dir) / "legacy-track-map.json"
            with patch("gpx_tracks_table.format_datetime_local_seconds", return_value="17.10.2020 13:00:00"):
                sidecar.write_text(json.dumps(metadata), encoding="utf-8")
                with patch("gpx_tracks_table.prepare_with_options", return_value=context):
                    report = upgrade_timed_track_sidecars("unused.gpx", temp_dir)
            updated = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(report["updated"], [1])
        self.assertEqual(updated["track_fingerprint"], "legacy-track-fingerprint")
        self.assertEqual(len(updated["timed_track_points"]), 2)

    def test_sidecar_upgrade_rejects_legacy_identity_mismatch(self):
        context = {
            "tracks": [
                {
                    "table_number": 1,
                    "name": "Current Track",
                    "start_time": None,
                    "first_point": (50.0, 7.0),
                    "last_point": (50.1, 7.1),
                    "track_fingerprint": "current-track-fingerprint",
                    "point_records": [],
                }
            ]
        }
        metadata = {
            "track_number": 1,
            "track_name": "Different Track",
            "track_start_time": "-",
            "start_point": {"lat": 50.0, "lon": 7.0},
            "end_point": {"lat": 50.1, "lon": 7.1},
        }
        with TemporaryDirectory() as temp_dir:
            sidecar = Path(temp_dir) / "legacy-track-map.json"
            sidecar.write_text(json.dumps(metadata), encoding="utf-8")
            with patch("gpx_tracks_table.prepare_with_options", return_value=context):
                report = upgrade_timed_track_sidecars("unused.gpx", temp_dir)
            unchanged = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(report["updated"], [])
        self.assertIn(
            ("legacy-track-map.json", "legacy track identity does not match current GPX"),
            report["skipped"],
        )
        self.assertIn((1, "matching track-map metadata missing"), report["skipped"])
        self.assertNotIn("timed_track_points", unchanged)


if __name__ == "__main__":
    unittest.main()
