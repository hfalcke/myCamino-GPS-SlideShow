# SPDX-License-Identifier: GPL-3.0-or-later

import unittest

from interactive_tile_viewport import (
    TileCoordinate,
    WEB_MERCATOR_HALF_WORLD_M,
    aspect_preserving_rect,
    clamp_extent_to_web_mercator,
    shifted_extent,
    scroll_pan_deltas,
    tile_bounds_mercator,
    tile_coverage_complete,
    tile_zoom_after_scale,
    tile_zoom_for_viewport,
    visible_tiles,
    mouse_wheel_zoom_factor,
    zoomed_extent,
)


class InteractiveTileViewportTests(unittest.TestCase):
    def test_mouse_wheel_uses_one_stable_zoom_step(self):
        self.assertAlmostEqual(mouse_wheel_zoom_factor(1.0), 2.0 ** 0.25)
        self.assertAlmostEqual(mouse_wheel_zoom_factor(-1.0), 2.0 ** -0.25)
        self.assertEqual(mouse_wheel_zoom_factor(20.0), 2.0)
        self.assertEqual(mouse_wheel_zoom_factor(-20.0), 0.5)
        self.assertEqual(mouse_wheel_zoom_factor(0.0), 1.0)
        self.assertAlmostEqual(mouse_wheel_zoom_factor(-1.0) ** 4, 0.5)

    def test_tile_zoom_matches_native_pixels_to_viewport(self):
        world = WEB_MERCATOR_HALF_WORLD_M * 2.0
        world_extent = {
            "min_x": -WEB_MERCATOR_HALF_WORLD_M,
            "max_x": WEB_MERCATOR_HALF_WORLD_M,
            "min_y": -WEB_MERCATOR_HALF_WORLD_M,
            "max_y": WEB_MERCATOR_HALF_WORLD_M,
        }
        self.assertEqual(tile_zoom_for_viewport(world_extent, 1024, 1024), 2)
        track_extent = {
            "min_x": 0.0,
            "max_x": world / (2 ** 14) * (1000 / 256),
            "min_y": 0.0,
            "max_y": world / (2 ** 14) * (720 / 256),
        }
        self.assertEqual(tile_zoom_for_viewport(track_extent, 1000, 720), 14)
        zoomed_out = {
            key: value * 16.0
            for key, value in track_extent.items()
        }
        self.assertEqual(tile_zoom_for_viewport(zoomed_out, 1000, 720), 10)
        self.assertEqual(tile_zoom_for_viewport(track_extent, 1000, 720), 14)

    def test_map_rect_letterboxes_without_stretching_extent(self):
        extent = {"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0}
        self.assertEqual(
            aspect_preserving_rect(1600.0, 900.0, extent),
            (350.0, 0.0, 900.0, 900.0),
        )
        self.assertEqual(
            aspect_preserving_rect(600.0, 900.0, extent),
            (0.0, 150.0, 600.0, 600.0),
        )

    def test_extent_is_clamped_to_finite_web_mercator_world(self):
        limit = WEB_MERCATOR_HALF_WORLD_M
        clamped = clamp_extent_to_web_mercator(
            {
                "min_x": -limit * 2.0,
                "max_x": limit * 2.0,
                "min_y": -limit * 1.5,
                "max_y": limit * 1.5,
            }
        )
        self.assertEqual(
            clamped,
            {"min_x": -limit, "max_x": limit, "min_y": -limit, "max_y": limit},
        )

    def test_zoom_zero_tile_covers_web_mercator_world(self):
        bounds = tile_bounds_mercator(TileCoordinate(0, 0, 0))
        self.assertEqual(
            bounds,
            {
                "min_x": -WEB_MERCATOR_HALF_WORLD_M,
                "max_x": WEB_MERCATOR_HALF_WORLD_M,
                "min_y": -WEB_MERCATOR_HALF_WORLD_M,
                "max_y": WEB_MERCATOR_HALF_WORLD_M,
            },
        )

    def test_visible_tiles_are_prioritized_from_viewport_center(self):
        extent = {
            "min_x": -WEB_MERCATOR_HALF_WORLD_M,
            "max_x": WEB_MERCATOR_HALF_WORLD_M,
            "min_y": -WEB_MERCATOR_HALF_WORLD_M,
            "max_y": WEB_MERCATOR_HALF_WORLD_M,
        }
        tiles = visible_tiles(extent, 2)
        self.assertEqual(len(tiles), 16)
        self.assertIn((tiles[0].x, tiles[0].y), {(1, 1), (1, 2), (2, 1), (2, 2)})
        self.assertEqual(
            {(tile.x, tile.y) for tile in tiles},
            {(x, y) for x in range(4) for y in range(4)},
        )

    def test_transition_fallback_retires_only_after_complete_coverage(self):
        expected = {(12, 1, 2), (12, 2, 2), (12, 1, 3)}
        self.assertFalse(tile_coverage_complete(expected, {(12, 1, 2), (12, 2, 2)}))
        self.assertTrue(tile_coverage_complete(expected, expected))
        self.assertTrue(tile_coverage_complete(set(), set()))

    def test_shifted_extent_uses_viewport_fraction(self):
        extent = {"min_x": 0.0, "max_x": 1000.0, "min_y": 0.0, "max_y": 500.0}
        shifted = shifted_extent(extent, 20.0, -10.0, 100.0, 50.0)
        self.assertEqual(
            shifted,
            {
                "min_x": -200.0,
                "max_x": 800.0,
                "min_y": 100.0,
                "max_y": 600.0,
            },
        )

    def test_adventure_map_can_reverse_only_vertical_scroll_delta(self):
        self.assertEqual(
            scroll_pan_deltas(12.0, -8.0, reverse_vertical=True),
            (12.0, 8.0),
        )
        self.assertEqual(
            scroll_pan_deltas(12.0, -8.0, reverse_vertical=False),
            (12.0, -8.0),
        )

    def test_zoom_keeps_focus_at_same_viewport_fraction(self):
        extent = {"min_x": 0.0, "max_x": 1000.0, "min_y": 0.0, "max_y": 500.0}
        zoomed = zoomed_extent(extent, 2.0, 250.0, 375.0)
        self.assertEqual(
            zoomed,
            {
                "min_x": 125.0,
                "max_x": 625.0,
                "min_y": 187.5,
                "max_y": 437.5,
            },
        )
        self.assertEqual(
            (250.0 - zoomed["min_x"]) / (zoomed["max_x"] - zoomed["min_x"]),
            0.25,
        )
        self.assertEqual(
            (375.0 - zoomed["min_y"]) / (zoomed["max_y"] - zoomed["min_y"]),
            0.75,
        )

    def test_many_small_pinch_events_produce_one_real_tile_level(self):
        accumulated_scale = 1.02 ** 20
        self.assertEqual(tile_zoom_after_scale(12, accumulated_scale), 13)

    def test_substantial_pinch_out_reduces_tile_level(self):
        self.assertEqual(tile_zoom_after_scale(12, 0.48), 11)


if __name__ == "__main__":
    unittest.main()
