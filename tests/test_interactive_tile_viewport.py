# SPDX-License-Identifier: GPL-3.0-or-later

import io
import unittest

from PIL import Image

from interactive_tile_viewport import (
    TileCoordinate,
    WEB_MERCATOR_HALF_WORLD_M,
    aspect_preserving_rect,
    clamp_extent_to_web_mercator,
    is_complete_world_extent,
    panned_extent_to_include_point,
    retained_tile_draw_order,
    sample_world_ocean_rgb_from_png,
    shifted_extent,
    scroll_pan_deltas,
    tile_bounds_mercator,
    tile_intersects_extent,
    tile_zoom_after_scale,
    tile_zoom_for_viewport,
    visible_tiles,
    world_ocean_sample_pixels,
    mouse_wheel_zoom_factor,
    zoomed_extent,
)


class InteractiveTileViewportTests(unittest.TestCase):
    def test_complete_world_extent_is_distinguished_from_a_zoomed_view(self):
        limit = WEB_MERCATOR_HALF_WORLD_M
        world = {
            "min_x": -limit,
            "max_x": limit,
            "min_y": -limit,
            "max_y": limit,
        }
        self.assertTrue(is_complete_world_extent(world))
        self.assertFalse(
            is_complete_world_extent({**world, "min_x": -limit * 0.95})
        )

    def test_ocean_samples_use_only_world_edge_tiles_near_equator(self):
        self.assertTrue(world_ocean_sample_pixels(TileCoordinate(2, 0, 1), 256, 256))
        self.assertTrue(world_ocean_sample_pixels(TileCoordinate(2, 3, 2), 256, 256))
        self.assertEqual(
            world_ocean_sample_pixels(TileCoordinate(2, 1, 1), 256, 256),
            [],
        )
        self.assertEqual(
            world_ocean_sample_pixels(TileCoordinate(2, 0, 0), 256, 256),
            [],
        )

    def test_zoom_zero_samples_both_antimeridian_edges(self):
        self.assertEqual(
            world_ocean_sample_pixels(TileCoordinate(0, 0, 0), 256, 256),
            [(2, 128), (253, 128)],
        )

    def test_ocean_color_is_sampled_from_candidate_png_only(self):
        output = io.BytesIO()
        Image.new("RGB", (16, 16), (51, 128, 204)).save(output, format="PNG")

        samples = sample_world_ocean_rgb_from_png(
            output.getvalue(),
            TileCoordinate(0, 0, 0),
        )

        self.assertEqual(len(samples), 2)
        for sample in samples:
            self.assertAlmostEqual(sample[0], 0.2)
            self.assertAlmostEqual(sample[1], 128 / 255.0)
            self.assertAlmostEqual(sample[2], 0.8)
        self.assertEqual(
            sample_world_ocean_rgb_from_png(
                output.getvalue(),
                TileCoordinate(2, 1, 1),
            ),
            [],
        )

        transparent = io.BytesIO()
        Image.new("RGBA", (16, 16), (51, 128, 204, 0)).save(
            transparent,
            format="PNG",
        )
        self.assertEqual(
            sample_world_ocean_rgb_from_png(
                transparent.getvalue(),
                TileCoordinate(0, 0, 0),
            ),
            [],
        )

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

    def test_retained_tiles_are_drawn_coarse_to_exact_target(self):
        target = tile_bounds_mercator(TileCoordinate(4, 8, 8))
        keys = {
            (2, 2, 2),
            (4, 8, 8),
            (6, 32, 32),
            (4, 0, 0),
        }

        ordered = retained_tile_draw_order(keys, target, 4)

        self.assertEqual(ordered, [(2, 2, 2), (6, 32, 32), (4, 8, 8)])

    def test_retained_tile_intersection_excludes_unrelated_regions(self):
        extent = tile_bounds_mercator(TileCoordinate(3, 4, 4))

        self.assertTrue(tile_intersects_extent(TileCoordinate(2, 2, 2), extent))
        self.assertTrue(tile_intersects_extent(TileCoordinate(4, 8, 8), extent))
        self.assertFalse(tile_intersects_extent(TileCoordinate(3, 0, 0), extent))

    def test_point_navigation_pans_only_when_target_leaves_safe_view(self):
        extent = {"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 50.0}

        self.assertEqual(
            panned_extent_to_include_point(extent, 50.0, 25.0),
            extent,
        )
        shifted = panned_extent_to_include_point(extent, 130.0, -20.0)
        self.assertEqual(shifted["max_x"] - shifted["min_x"], 100.0)
        self.assertEqual(shifted["max_y"] - shifted["min_y"], 50.0)
        self.assertEqual(shifted["max_x"], 140.0)
        self.assertEqual(shifted["min_y"], -25.0)

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
