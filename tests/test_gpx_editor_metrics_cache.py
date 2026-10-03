#!/usr/bin/env python3
"""Tests for the compact GPX Editor startup cache."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gpx_editor_metrics_cache import (
    MetricsCacheLookup,
    cache_path,
    display_geometry_for_processed_track,
    load_metrics_cache,
    match_cached_tracks,
    write_metrics_cache,
)
from gpx_processing import ProcessingOptions


class GpxEditorMetricsCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "tracks.gpx"
        self.source.write_text("<gpx version='1.1'/>", encoding="utf-8")
        self.cache_dir = self.root / "cache"
        self.options = ProcessingOptions().normalized()
        self.start = datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
        self.metrics = {
            "time": self.start,
            "start_time": self.start,
            "end_time": self.start + timedelta(hours=2),
            "duration": timedelta(hours=2),
            "length_km": 12.345678,
            "distance_km": 2.5,
            "speed_kmh": 6.172839,
            "moving_speed_kmh": 6.4,
            "ascent_m": 123.456,
            "descent_m": 98.765,
            "npoints": 100,
            "raw_npoints": 120,
            "retained_npoints": 100,
            "rejection_counts": {"horizontal": {"retained": 100}},
            "processing_options": self.options.as_dict(),
            "first_lat": 50.0,
            "first_lon": 7.0,
            "last_lat": 50.1,
            "last_lon": 7.1,
            "_anchor_key": (50.0, 7.0),
        }
        self.display_geometry = {
            "levels": {
                "64": [[[50.0, 7.0, 0], [50.1, 7.1, 99]]],
                "160": [[[50.0, 7.0, 0], [50.1, 7.1, 99]]],
                "320": [[[50.0, 7.0, 0], [50.1, 7.1, 99]]],
            },
            "extent_mercator": {
                "min_x": 7.0,
                "max_x": 7.1,
                "min_y": 50.0,
                "max_y": 50.1,
            },
        }

    def write_cache(self):
        return write_metrics_cache(
            self.source,
            self.options,
            [{
                "source_index": 0,
                "fingerprint": "abc",
                "metrics": self.metrics,
                "display_geometry": self.display_geometry,
            }],
            self.cache_dir,
        )

    def test_exact_cache_round_trip_preserves_unrounded_metrics(self):
        self.write_cache()
        result = load_metrics_cache(self.source, self.options, self.cache_dir)
        self.assertIsNotNone(result)
        self.assertTrue(result.exact_source)
        restored = result.tracks[0]["metrics"]
        self.assertEqual(restored["duration"], timedelta(hours=2))
        self.assertEqual(restored["start_time"], self.start)
        self.assertEqual(restored["length_km"], 12.345678)
        self.assertEqual(result.tracks[0]["fingerprint"], "abc")
        self.assertEqual(
            result.tracks[0]["display_geometry"]["levels"]["64"][0][0],
            [50.0, 7.0, 0],
        )

    def test_changed_source_retains_fingerprint_candidates_but_is_not_exact(self):
        self.write_cache()
        self.source.write_text("<gpx version='1.1'><trk/></gpx>", encoding="utf-8")
        result = load_metrics_cache(self.source, self.options, self.cache_dir)
        self.assertIsNotNone(result)
        self.assertFalse(result.exact_source)
        self.assertEqual(result.tracks[0]["fingerprint"], "abc")

    def test_mtime_only_change_is_an_exact_content_match(self):
        self.write_cache()
        stat_result = self.source.stat()
        os.utime(
            self.source,
            ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000),
        )
        result = load_metrics_cache(self.source, self.options, self.cache_dir)
        self.assertIsNotNone(result)
        self.assertTrue(result.exact_source)

    def test_identical_copy_uses_content_addressed_cache(self):
        self.write_cache()
        copied = self.root / "copied-project" / "renamed.gpx"
        copied.parent.mkdir()
        shutil.copy2(self.source, copied)
        result = load_metrics_cache(copied, self.options, self.cache_dir)
        self.assertIsNotNone(result)
        self.assertTrue(result.exact_source)
        self.assertEqual(result.tracks[0]["metrics"]["length_km"], 12.345678)

    def test_compact_geometry_preserves_segments_and_endpoints(self):
        from types import SimpleNamespace

        first = [
            SimpleNamespace(lat=50.0 + index / 1000, lon=7.0, source_index=index)
            for index in range(400)
        ]
        second = [
            SimpleNamespace(lat=51.0 + index / 1000, lon=8.0, source_index=400 + index)
            for index in range(200)
        ]
        processed = SimpleNamespace(
            segments=[SimpleNamespace(points=first), SimpleNamespace(points=second)]
        )
        geometry = display_geometry_for_processed_track(processed)

        self.assertEqual(len(geometry["levels"]["64"]), 2)
        self.assertEqual(geometry["levels"]["64"][0][0], [50.0, 7.0, 0])
        self.assertEqual(geometry["levels"]["64"][0][-1], [50.399, 7.0, 399])
        self.assertEqual(geometry["levels"]["64"][1][0], [51.0, 8.0, 400])
        self.assertEqual(geometry["levels"]["64"][1][-1], [51.199, 8.0, 599])

    def test_changed_processing_options_are_a_cache_miss(self):
        self.write_cache()
        changed = ProcessingOptions(minimum_point_spacing_m=25).normalized()
        self.assertIsNone(load_metrics_cache(self.source, changed, self.cache_dir))

    def test_malformed_cache_is_ignored(self):
        destination = cache_path(self.source, self.cache_dir)
        destination.parent.mkdir(parents=True)
        destination.write_text("{broken", encoding="utf-8")
        self.assertIsNone(load_metrics_cache(self.source, self.options, self.cache_dir))

    def test_cache_contains_only_compact_display_geometry(self):
        destination = self.write_cache()
        payload = json.loads(destination.read_text(encoding="utf-8"))
        serialized = json.dumps(payload)
        self.assertNotIn("timed_track_points", serialized)
        self.assertNotIn("processed_segments", serialized)
        self.assertIn("display_geometry", serialized)
        self.assertNotIn("running_speed_kmh", serialized)

    def test_exact_match_does_not_recalculate_fingerprints(self):
        self.write_cache()
        lookup = load_metrics_cache(self.source, self.options, self.cache_dir)
        result = match_cached_tracks(
            [object()],
            lookup,
            lambda _element: self.fail("exact cache hit recalculated a fingerprint"),
        )
        self.assertEqual(result[0][0], "abc")
        self.assertEqual(result[0][1]["length_km"], 12.345678)
        self.assertIsNotNone(result[0][2])

    def test_changed_source_matches_duplicate_fingerprints_once_each(self):
        lookup = MetricsCacheLookup(
            exact_source=False,
            tracks=(
                {
                    "source_index": 0,
                    "fingerprint": "same",
                    "metrics": self.metrics,
                    "display_geometry": self.display_geometry,
                },
                {
                    "source_index": 1,
                    "fingerprint": "same",
                    "metrics": self.metrics,
                    "display_geometry": self.display_geometry,
                },
            ),
        )
        calls = []
        result = match_cached_tracks(
            ["first", "second", "new"],
            lookup,
            lambda element: calls.append(element) or ("new" if element == "new" else "same"),
        )
        self.assertEqual(calls, ["first", "second", "new"])
        self.assertIsNotNone(result[0][1])
        self.assertIsNotNone(result[1][1])
        self.assertIsNone(result[2][1])


if __name__ == "__main__":
    unittest.main()
