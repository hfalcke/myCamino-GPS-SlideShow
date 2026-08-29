# SPDX-License-Identifier: GPL-3.0-or-later

import unittest

from interactive_tile_viewport import (
    TileCoordinate,
    WEB_MERCATOR_HALF_WORLD_M,
    shifted_extent,
    tile_bounds_mercator,
    tile_zoom_after_scale,
    visible_tiles,
    zoomed_extent,
)


class InteractiveTileViewportTests(unittest.TestCase):
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
