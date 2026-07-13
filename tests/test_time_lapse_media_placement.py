"""Tests for track-aware media placement in stage time-lapse mode."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from GPSTrackShow import (
    GPSTrackShowApp,
    advance_time_lapse_progress,
    best_media_corner_layout,
    clear_corner_rect_options,
    config_from_options,
    derive_clock_date_text,
    endpoint_tangent,
    inset_rect,
    largest_clear_corner_rects,
    map_plot_rect,
    previous_displayable_playlist_index,
    time_lapse_media_minimum_pending,
)


class TimeLapseMediaPlacementTests(unittest.TestCase):
    def test_arrow_navigation_resumes_only_in_previous_automatic_mode(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.manual_mode = False
        app.paused = False
        app.time_lapse_stage = object()
        app.time_lapse_last_tick = None
        ticks = []
        app._time_lapse_tick = lambda: ticks.append("tick")
        app._continue_time_lapse_after_navigation()
        self.assertEqual(ticks, ["tick"])
        self.assertFalse(app.manual_mode)

        app.manual_mode = True
        app._continue_time_lapse_after_navigation()
        self.assertEqual(ticks, ["tick"])
        self.assertTrue(app.manual_mode)

    def test_axes_metadata_excludes_header_from_map_placement(self):
        image_rect = (100.0, 50.0, 800.0, 400.0)
        metadata = {
            "axes_box_fraction": {
                "left": 0.10,
                "bottom": 0.20,
                "width": 0.80,
                "height": 0.60,
            }
        }
        for actual, expected in zip(map_plot_rect(image_rect, metadata), (180.0, 130.0, 640.0, 240.0)):
            self.assertAlmostEqual(actual, expected)

    def test_missing_axes_metadata_uses_full_image_and_all_corners(self):
        image_rect = (100.0, 50.0, 800.0, 400.0)
        placement = inset_rect(map_plot_rect(image_rect, {}), 0.05)
        candidates = clear_corner_rect_options(placement, [])
        self.assertEqual(map_plot_rect(image_rect, {}), image_rect)
        self.assertEqual(set(candidates), {"top_right", "top_left", "bottom_right", "bottom_left"})

    def test_margin_uses_map_dimensions_not_window_dimensions(self):
        self.assertEqual(inset_rect((100.0, 200.0, 400.0, 200.0), 0.05), (120.0, 210.0, 360.0, 180.0))

    def test_clear_frontier_does_not_depend_on_point_density(self):
        placement = (0.0, 0.0, 100.0, 100.0)
        sparse = [(0.0, 50.0), (100.0, 50.0)]
        dense = [(0.0, 50.0), (20.0, 50.0), (40.0, 50.0), (60.0, 50.0), (80.0, 50.0), (100.0, 50.0)]
        self.assertEqual(clear_corner_rect_options(placement, sparse), clear_corner_rect_options(placement, dense))

    def test_stage_stores_largest_clear_box_for_every_corner(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        route = [(300.0, 0.0), (300.0, 600.0)]
        clear_rects = largest_clear_corner_rects(placement, route)
        self.assertEqual(set(clear_rects), {"top_right", "top_left", "bottom_right", "bottom_left"})
        self.assertGreater(clear_rects["top_right"][2], clear_rects["top_left"][2])
        self.assertGreater(clear_rects["bottom_right"][2], clear_rects["bottom_left"][2])

    def test_stage_keeps_width_height_frontier_for_different_aspect_ratios(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        route = [(200.0, 500.0), (800.0, 100.0)]
        options = clear_corner_rect_options(placement, route)
        self.assertGreater(len(options["top_left"]), 1)
        self.assertTrue(any(rect[2] < 250.0 and rect[3] > 500.0 for rect in options["top_left"]))

    def test_each_image_uses_corner_allowing_largest_display(self):
        clear_rects = {
            "top_right": (200.0, 200.0, 800.0, 400.0),
            "top_left": (0.0, 0.0, 400.0, 600.0),
            "bottom_right": (600.0, 0.0, 400.0, 150.0),
            "bottom_left": (0.0, 0.0, 200.0, 400.0),
        }
        landscape_corner, _outer, landscape = best_media_corner_layout(
            clear_rects, (1000.0, 600.0), 0.5, (1600.0, 900.0)
        )
        portrait_corner, _outer, portrait = best_media_corner_layout(
            clear_rects, (1000.0, 600.0), 0.5, (900.0, 1600.0)
        )
        self.assertEqual(landscape_corner, "top_right")
        self.assertEqual(portrait_corner, "top_left")
        self.assertGreater(landscape[2], 500.0)
        self.assertGreater(portrait[3], 300.0)

    def test_backward_navigation_uses_playlist_rows_without_image_history(self):
        lines = [
            "#Overviewmap: overview.png",
            "#Datum: 01.01.2024",
            "#Map: track-1.png",
            "photo-1.jpeg 10:00 - - -",
            "photo-2.jpeg 10:10 - - -",
        ]
        self.assertEqual(previous_displayable_playlist_index(lines, 4), 3)
        self.assertEqual(previous_displayable_playlist_index(lines, 3), 2)
        self.assertIsNone(previous_displayable_playlist_index(lines, 2))

    def test_arrow_moves_while_media_is_visible_without_overlap(self):
        progress, event_due, blocked = advance_time_lapse_progress(0.2, 3.0, 30.0, 0.8, True)
        self.assertAlmostEqual(progress, 0.3)
        self.assertFalse(event_due)
        self.assertFalse(blocked)

    def test_arrow_waits_at_next_media_until_previous_media_finishes(self):
        progress, event_due, blocked = advance_time_lapse_progress(0.2, 3.0, 30.0, 0.25, True)
        self.assertEqual(progress, 0.25)
        self.assertFalse(event_due)
        self.assertTrue(blocked)
        progress, event_due, blocked = advance_time_lapse_progress(progress, 0.02, 30.0, 0.25, False)
        self.assertEqual(progress, 0.25)
        self.assertTrue(event_due)
        self.assertFalse(blocked)

    def test_media_remains_replaceable_after_its_minimum_duration(self):
        self.assertTrue(time_lapse_media_minimum_pending(True, 103.0, 102.0))
        self.assertFalse(time_lapse_media_minimum_pending(True, 103.0, 104.0))
        progress, event_due, blocked = advance_time_lapse_progress(0.2, 3.0, 30.0, 0.25, False)
        self.assertEqual(progress, 0.25)
        self.assertTrue(event_due)
        self.assertFalse(blocked)

    def test_stage_arrow_uses_one_start_to_end_orientation(self):
        tangent = endpoint_tangent([(10.0, 20.0), (50.0, 60.0), (110.0, 20.0)])
        self.assertEqual(tangent, (1.0, 0.0))

    def test_default_and_validation_for_media_fraction(self):
        with TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            control_file = project_dir / "slides.lst"
            control_file.write_text("#Datum: 01.01.2024\n", encoding="utf-8")
            config = config_from_options(project_dir, inputlist=control_file)
            self.assertEqual(config.time_lapse_media_min_fraction, 0.5)
            self.assertEqual(config.time_lapse_media_max_fraction, 0.5)
            with self.assertRaises(ValueError):
                config_from_options(
                    project_dir,
                    inputlist=control_file,
                    time_lapse_media_min_fraction=1.1,
                )

    def test_time_lapse_clock_accepts_plain_date_context(self):
        self.assertEqual(derive_clock_date_text({}, "23.05.2020"), "23.05.2020")
        self.assertEqual(
            derive_clock_date_text({}, "Samstag, 23.05.2020"),
            "23.05.2020",
        )


if __name__ == "__main__":
    unittest.main()
