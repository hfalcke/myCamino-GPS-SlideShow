#!/usr/bin/env python3
"""Tests for the compact GPX Editor startup cache."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gpx_editor_metrics_cache import (
    MetricsCacheLookup,
    cache_path,
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

    def write_cache(self):
        return write_metrics_cache(
            self.source,
            self.options,
            [{"source_index": 0, "fingerprint": "abc", "metrics": self.metrics}],
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

    def test_changed_source_retains_fingerprint_candidates_but_is_not_exact(self):
        self.write_cache()
        self.source.write_text("<gpx version='1.1'><trk/></gpx>", encoding="utf-8")
        result = load_metrics_cache(self.source, self.options, self.cache_dir)
        self.assertIsNotNone(result)
        self.assertFalse(result.exact_source)
        self.assertEqual(result.tracks[0]["fingerprint"], "abc")

    def test_changed_processing_options_are_a_cache_miss(self):
        self.write_cache()
        changed = ProcessingOptions(minimum_point_spacing_m=25).normalized()
        self.assertIsNone(load_metrics_cache(self.source, changed, self.cache_dir))

    def test_malformed_cache_is_ignored(self):
        destination = cache_path(self.source, self.cache_dir)
        destination.parent.mkdir(parents=True)
        destination.write_text("{broken", encoding="utf-8")
        self.assertIsNone(load_metrics_cache(self.source, self.options, self.cache_dir))

    def test_cache_contains_no_processed_geometry(self):
        destination = self.write_cache()
        payload = json.loads(destination.read_text(encoding="utf-8"))
        serialized = json.dumps(payload)
        self.assertNotIn("timed_track_points", serialized)
        self.assertNotIn("processed_segments", serialized)

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

    def test_changed_source_matches_duplicate_fingerprints_once_each(self):
        lookup = MetricsCacheLookup(
            exact_source=False,
            tracks=(
                {"source_index": 0, "fingerprint": "same", "metrics": self.metrics},
                {"source_index": 1, "fingerprint": "same", "metrics": self.metrics},
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
