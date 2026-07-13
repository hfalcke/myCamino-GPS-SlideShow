"""Deterministic coverage for shared GPX timing and time-lapse sampling."""

from __future__ import annotations

import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from GPSTrackShow import (
    PhotoListEntry,
    align_datetime_timezone,
    build_time_lapse_media_queue,
    interpolate_timeline_point,
    parse_control_datetime,
    parse_iso_datetime,
    timed_points_from_metadata,
    timeline_sample_count,
)
from gpx_tracks_table import upgrade_timed_track_sidecars
from track_timing_utils import haversine_km, repair_timed_points


class TrackTimingTests(unittest.TestCase):
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
                        {"lat": 50.0, "lon": 7.0, "time": start},
                        {"lat": 50.1, "lon": 7.1, "time": start + timedelta(hours=1)},
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


if __name__ == "__main__":
    unittest.main()
