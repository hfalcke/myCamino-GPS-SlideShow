"""Tests for cached slide-show elevation profiles."""

from __future__ import annotations

import unittest
from pathlib import Path

from elevation_profile_cache import (
    elevation_profile_cache_is_current,
    elevation_profile_cache_paths,
    elevation_profile_manifest,
    elevation_profile_ranges,
    elevation_profile_segments,
)


class ElevationProfileCacheTests(unittest.TestCase):
    def metadata(self):
        return {
            "track_fingerprint": "track-v1",
            "gpx_processing": {"elevation_smoothing_distance_m": 50.0},
            "retained_point_count": 3,
            "processed_track_segments": [
                [
                    {"cumulative_distance_km": 0.0, "elevation_m": 100.0},
                    {"cumulative_distance_km": 5.0, "elevation_m": 200.0},
                    {"cumulative_distance_km": 10.0, "elevation_m": 150.0},
                ]
            ],
        }

    def test_extracts_processed_profile_and_uses_min_max_headroom(self):
        segments = elevation_profile_segments(self.metadata())
        self.assertEqual(
            segments,
            [[(0.0, 100.0), (5.0, 200.0), (10.0, 150.0)]],
        )
        self.assertEqual(
            elevation_profile_ranges(segments),
            ((0.0, 10.0), (95.0, 205.0)),
        )

    def test_standard_and_time_lapse_maps_share_one_profile_cache(self):
        standard = Path("/project/trackimages/0001_track.png")
        time_lapse = Path("/project/trackimages/0001_track-timelapse.png")
        self.assertEqual(
            elevation_profile_cache_paths(standard),
            elevation_profile_cache_paths(time_lapse),
        )
        image, manifest = elevation_profile_cache_paths(standard)
        self.assertEqual(image.name, "0001_track-elevation.png")
        self.assertEqual(manifest.name, "0001_track-elevation.json")
        self.assertEqual(image.parent.name, "elevation-profiles")

    def test_cache_becomes_stale_for_track_or_processing_change(self):
        metadata = self.metadata()
        manifest = elevation_profile_manifest(metadata)
        self.assertTrue(elevation_profile_cache_is_current(manifest, metadata))
        changed_track = {**metadata, "track_fingerprint": "track-v2"}
        self.assertFalse(elevation_profile_cache_is_current(manifest, changed_track))
        changed_processing = {
            **metadata,
            "gpx_processing": {"elevation_smoothing_distance_m": 25.0},
        }
        self.assertFalse(elevation_profile_cache_is_current(manifest, changed_processing))
        changed_elevation = self.metadata()
        changed_elevation["processed_track_segments"][0][1]["elevation_m"] = 210.0
        self.assertFalse(elevation_profile_cache_is_current(manifest, changed_elevation))


if __name__ == "__main__":
    unittest.main()
