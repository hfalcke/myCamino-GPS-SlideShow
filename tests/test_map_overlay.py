import unittest

from map_overlay import (
    MAP_CONTENT_VERSION,
    map_uses_dynamic_overlays,
    normalize_overlay_segments,
    placement_obstacle_points,
    scene_from_metadata,
)


class MapOverlayTests(unittest.TestCase):
    def test_legacy_map_is_not_drawn_twice(self):
        self.assertFalse(map_uses_dynamic_overlays({"track_points": [[1.0, 2.0], [3.0, 4.0]]}))
        self.assertTrue(
            map_uses_dynamic_overlays(
                {"map_content_version": 3, "background_only": True}
            )
        )
        self.assertTrue(
            map_uses_dynamic_overlays(
                {"map_content_version": MAP_CONTENT_VERSION, "background_only": True}
            )
        )

    def test_track_segments_preserve_boundaries(self):
        scene = scene_from_metadata(
            {
                "map_content_version": 2,
                "background_only": True,
                "stage_kind": "gpx_track",
                "track_segments": [
                    [[50.0, 7.0], [50.1, 7.1]],
                    [[50.2, 7.2], [50.3, 7.3]],
                ],
                "header_lines": ["Stage", "01.01.2026"],
            }
        )
        self.assertEqual(scene.mode, "line")
        self.assertEqual([len(segment) for segment in scene.segments], [2, 2])
        self.assertEqual(scene.header_lines, ("Stage", "01.01.2026"))

    def test_media_scene_supports_dots_line_and_hidden(self):
        metadata = {
            "stage_kind": "media_stage",
            "media_points": [
                {"lat": 50.0, "lon": 7.0, "source_name": "a.jpeg"},
                {"lat": 50.1, "lon": 7.1, "source_name": "b.jpeg"},
            ],
        }
        self.assertEqual(scene_from_metadata(metadata).mode, "dots")
        self.assertEqual(scene_from_metadata(metadata, media_mode="interpolated").mode, "interpolated")
        self.assertEqual(scene_from_metadata(metadata, media_mode="hidden").mode, "hidden")
        self.assertEqual(len(scene_from_metadata(metadata).points), 2)

    def test_overview_uses_embedded_geometry(self):
        scene = scene_from_metadata(
            {
                "stage_kind": "overview",
                "overlay_geometry": {
                    "segments": [
                        [{"lat": 50.0, "lon": 7.0}, {"lat": 50.1, "lon": 7.1}],
                        [{"lat": 51.0, "lon": 8.0}, {"lat": 51.1, "lon": 8.1}],
                    ]
                },
            }
        )
        self.assertEqual(len(scene.segments), 2)

    def test_flat_point_list_becomes_one_segment(self):
        segments = normalize_overlay_segments([[50.0, 7.0], [50.1, 7.1]])
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0]), 2)

    def test_media_points_are_placement_obstacles_when_route_is_empty(self):
        points = placement_obstacle_points(
            {
                "stage_kind": "media_stage",
                "media_points": [
                    {"lat": 50.0, "lon": 7.0},
                    {"lat": 50.1, "lon": 7.1},
                ],
            },
            [],
        )
        self.assertEqual([(point.latitude, point.longitude) for point in points], [(50.0, 7.0), (50.1, 7.1)])

    def test_animated_route_takes_precedence_for_placement(self):
        points = placement_obstacle_points(
            {"stage_kind": "media_stage", "media_points": [{"lat": 1.0, "lon": 2.0}]},
            [{"lat": 3.0, "lon": 4.0}],
        )
        self.assertEqual((points[0].latitude, points[0].longitude), (3.0, 4.0))


if __name__ == "__main__":
    unittest.main()
