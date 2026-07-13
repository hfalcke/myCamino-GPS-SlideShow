"""Tests for standard/time-lapse track-map variants and cached free boxes."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gpx_tracks_table import extent_for_image, prepare_with_options
from track_map_layout_utils import (
    CORNER_ORDER,
    build_media_clear_boxes_metadata,
    cached_clear_box_options,
    clear_box_options_for_extent,
    optimized_track_extent,
    resolve_track_map_variant,
    time_lapse_track_map_name,
    track_map_variant_names,
)


class TrackMapLayoutTests(unittest.TestCase):
    def test_variant_names_keep_standard_canonical(self):
        self.assertEqual(time_lapse_track_map_name("0001_stage_trip.png"), "0001_stage_trip-timelapse.png")
        self.assertEqual(
            track_map_variant_names("0001_stage_trip-timelapse.png", prefer_time_lapse=False),
            ["0001_stage_trip.png", "0001_stage_trip-timelapse.png"],
        )

    def test_variant_resolution_uses_preference_then_fallback(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            standard = root / "0001_stage_trip.png"
            time_lapse = root / "0001_stage_trip-timelapse.png"
            standard.touch()
            self.assertEqual(resolve_track_map_variant(standard, True), standard)
            time_lapse.touch()
            self.assertEqual(resolve_track_map_variant(standard, True), time_lapse)
            self.assertEqual(resolve_track_map_variant(time_lapse, False), standard)

    def test_optimized_extent_preserves_scale_margin_and_free_area(self):
        points = [(20.0, 10.0), (30.0, 50.0), (40.0, 90.0)]
        image_size = (1920.0, 1080.0)
        axes_box = (0.0, 0.0, 1.0, 0.9)
        standard = extent_for_image([item[0] for item in points], [item[1] for item in points], (1920.0, 972.0))
        centered_options = clear_box_options_for_extent(points, standard, image_size, axes_box, 0.05)
        centered_area = max(rect[2] * rect[3] for corner in CORNER_ORDER for rect in centered_options[corner])
        optimized, _corner, _shift, options = optimized_track_extent(
            standard, points, image_size, axes_box, 0.05
        )
        self.assertAlmostEqual(optimized[1] - optimized[0], standard[1] - standard[0])
        self.assertAlmostEqual(optimized[3] - optimized[2], standard[3] - standard[2])
        width, height = optimized[1] - optimized[0], optimized[3] - optimized[2]
        for x, y in points:
            self.assertGreaterEqual((x - optimized[0]) / width, 0.05 - 1e-9)
            self.assertLessEqual((x - optimized[0]) / width, 0.95 + 1e-9)
            self.assertGreaterEqual((y - optimized[2]) / height, 0.05 - 1e-9)
            self.assertLessEqual((y - optimized[2]) / height, 0.95 + 1e-9)
        optimized_area = max(rect[2] * rect[3] for corner in CORNER_ORDER for rect in options[corner])
        self.assertGreaterEqual(optimized_area, centered_area)

    def test_cached_frontiers_scale_to_current_image_rect(self):
        image_size = (1000.0, 500.0)
        options = {
            corner: [(0.0, 0.0, 250.0, 200.0), (0.0, 0.0, 400.0, 100.0)]
            for corner in CORNER_ORDER
        }
        cache = build_media_clear_boxes_metadata(options, image_size, 0.05, "fingerprint")
        metadata = {
            "image_size_px": {"width": 1000, "height": 500},
            "track_fingerprint": "fingerprint",
            "track_edge_margin_fraction": 0.05,
            "media_clear_boxes": cache,
        }
        converted = cached_clear_box_options(metadata, (100.0, 50.0, 500.0, 250.0), image_size)
        self.assertIsNotNone(converted)
        self.assertEqual(converted["top_right"][0], (100.0, 50.0, 125.0, 100.0))

    def test_malformed_or_stale_cache_falls_back(self):
        metadata = {
            "image_size_px": {"width": 1000, "height": 500},
            "track_fingerprint": "new",
            "track_edge_margin_fraction": 0.05,
            "media_clear_boxes": {
                "version": 1,
                "coordinate_space": "image_fraction_bottom_left",
                "margin_fraction": 0.05,
                "image_size_px": {"width": 1000, "height": 500},
                "track_fingerprint": "old",
                "corners": {},
            },
        }
        self.assertIsNone(cached_clear_box_options(metadata, (0.0, 0.0, 1000.0, 500.0)))

    def test_prepare_context_exposes_both_filenames(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gpx = root / "trip.gpx"
            gpx.write_text(
                """<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Stage</name><trkseg><trkpt lat="50" lon="7"><time>2024-01-01T10:00:00Z</time></trkpt><trkpt lat="50.1" lon="7.1"><time>2024-01-01T11:00:00Z</time></trkpt></trkseg></trk></gpx>""",
                encoding="utf-8",
            )
            context = prepare_with_options(
                gpx,
                output_dir=root,
                output_base="Trip",
                plot_tracks="all",
                map_layout="time-lapse",
                create_output_dir=False,
            )
            track = context["tracks"][0]
            self.assertTrue(track["track_plot_image_filename"].endswith("_Trip.png"))
            self.assertTrue(track["track_plot_time_lapse_image_filename"].endswith("_Trip-timelapse.png"))
            self.assertTrue(context["track_plot_paths"][0]["output_image"].endswith("-timelapse.png"))


if __name__ == "__main__":
    unittest.main()
