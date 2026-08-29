# SPDX-License-Identifier: GPL-3.0-or-later

import json
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

from historical_weather import (
    WeatherOptions,
    _WeatherCandidate,
    enrich_media_weather,
    group_weather_candidates,
    sample_hourly_weather,
    weather_is_current,
)
from GPSTrackShow import retained_weather_at_time, weather_cloud_cover_text


def _candidate(tmp_path, name, when, latitude=50.0, longitude=7.0):
    media = tmp_path / name
    media.write_bytes(b"media")
    payload = {
        "source_filename": name,
        "photo_path": str(media),
        "datetime_iso": when.isoformat(),
        "date_german": "",
        "time": when.strftime("%H:%M"),
        "latitude": latitude,
        "longitude": longitude,
        "place": None,
        "has_gps": True,
        "unknown_field": {"preserve": True},
    }
    sidecar = media.with_name(media.name + ".json")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return _WeatherCandidate(media, sidecar, payload, when, latitude, longitude)


def _response(start_epoch, locations=1):
    rows = []
    for offset in range(locations):
        rows.append(
            {
                "latitude": 50.0,
                "longitude": 7.0,
                "elevation": 100.0,
                "hourly_units": {
                    "temperature_2m": "°C",
                    "relative_humidity_2m": "%",
                    "precipitation": "mm",
                    "pressure_msl": "hPa",
                    "wind_speed_10m": "km/h",
                    "cloud_cover": "%",
                    "weather_code": "wmo code",
                },
                "hourly": {
                    "time": [start_epoch, start_epoch + 3600, start_epoch + 7200],
                    "temperature_2m": [10.0 + offset, 14.0 + offset, 18.0 + offset],
                    "relative_humidity_2m": [80.0, 60.0, 40.0],
                    "precipitation": [0.0, 1.2, 2.4],
                    "pressure_msl": [1000.0, 1002.0, 1004.0],
                    "wind_speed_10m": [5.0, 9.0, 13.0],
                    "cloud_cover": [100.0, 50.0, 0.0],
                    "weather_code": [3, 61, 0],
                },
            }
        )
    return rows[0] if locations == 1 else rows


class HistoricalWeatherTests(unittest.TestCase):
    def test_cloud_cover_text_is_a_compact_percentage(self):
        self.assertEqual(
            weather_cloud_cover_text({"values": {"cloud_cover": 64.6}}),
            "65%",
        )
        self.assertEqual(weather_cloud_cover_text({"values": {}}), "")

    def test_mixed_hourly_sampling_uses_containing_precipitation(self):
        start = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        values, sampling = sample_hourly_weather(
            _response(start.timestamp()), start + timedelta(minutes=30)
        )
        self.assertAlmostEqual(values["temperature_2m"], 12.0)
        self.assertAlmostEqual(values["relative_humidity_2m"], 70.0)
        self.assertAlmostEqual(values["precipitation"], 1.2)
        self.assertEqual(values["weather_code"], 3)
        self.assertAlmostEqual(sampling["fraction"], 0.5)

    def test_grouping_requires_every_member_inside_both_limits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            root = Path(directory)
            start = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
            candidates = [
                _candidate(root, "a.jpg", start, 50.0, 7.0),
                _candidate(root, "b.jpg", start + timedelta(minutes=9), 50.0004, 7.0),
                _candidate(root, "c.jpg", start + timedelta(minutes=18), 50.0008, 7.0),
            ]
            groups = group_weather_candidates(candidates, WeatherOptions())
            self.assertEqual([len(group.members) for group in groups], [2, 1])

    def test_enrichment_patches_only_weather_and_then_is_current(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            candidate = _candidate(
                Path(directory), "photo.jpg",
                datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc),
            )
            calls = []
            def request_json(url, _timeout):
                calls.append(url)
                return _response(datetime(2026, 8, 3, 12, tzinfo=timezone.utc).timestamp())
            report = enrich_media_weather([candidate.media_path], request_json=request_json)
            self.assertEqual(report.updated, 1)
            self.assertEqual(report.requests, 1)
            payload = json.loads(candidate.sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["unknown_field"], {"preserve": True})
            self.assertAlmostEqual(payload["weather"]["values"]["temperature_2m"], 12.0)
            self.assertTrue(weather_is_current(payload))
            def unexpected(*_args):
                self.fail("current weather must not be downloaded again")
            second = enrich_media_weather([candidate.media_path], request_json=unexpected)
            self.assertEqual(second.current, 1)
            self.assertEqual(second.requests, 0)
            self.assertTrue(calls)

    def test_multi_location_request_is_batched(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            when = datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)
            first = _candidate(root, "a.jpg", when, 50.0, 7.0)
            second = _candidate(root, "b.jpg", when, 51.0, 8.0)
            counts = []
            def request_json(url, _timeout):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                count = len(query["latitude"][0].split(","))
                counts.append(count)
                return _response(datetime(2026, 8, 3, 12, tzinfo=timezone.utc).timestamp(), count)
            report = enrich_media_weather([first.media_path, second.media_path], request_json=request_json)
            self.assertEqual(report.updated, 2)
            self.assertEqual(counts, [2])

    def test_persistent_rate_limit_stops_remaining_batches_once(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _candidate(
                root, "a.jpg", datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)
            )
            second = _candidate(
                root, "b.jpg", datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
            )
            calls = []
            messages = []

            def rate_limited(url, _timeout):
                calls.append(url)
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

            with patch("historical_weather._cancelable_wait"):
                report = enrich_media_weather(
                    [first.media_path, second.media_path],
                    request_json=rate_limited,
                    detail_callback=messages.append,
                )

            self.assertEqual(len(calls), 4)
            self.assertTrue(report.rate_limited)
            self.assertEqual(report.failed, 2)
            self.assertEqual(len(report.warnings), 1)
            self.assertEqual(len(messages), 1)
            self.assertIn("run Update Metadata later", messages[0])

    def test_time_lapse_weather_retention_uses_media_time(self):
        source = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        weather = {"timestamp": source, "values": {"temperature_2m": 20.0}}
        self.assertIs(retained_weather_at_time(weather, source + timedelta(minutes=59)), weather)
        self.assertIs(retained_weather_at_time(weather, source + timedelta(hours=1)), weather)
        self.assertIsNone(retained_weather_at_time(weather, source + timedelta(hours=1, seconds=1)))
        self.assertIsNone(retained_weather_at_time(weather, source - timedelta(seconds=1)))


if __name__ == "__main__":
    unittest.main()
