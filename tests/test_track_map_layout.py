"""Tests for standard/time-lapse track-map variants and cached free boxes."""

from __future__ import annotations

import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpx_tracks_table import (
    MINIMUM_MAP_SHORT_DIMENSION_M,
    extent_for_image,
    extent_with_minimum_short_dimension,
    fitted_zoom_level,
    execute_map_variants_from_context,
    media_overview_fingerprint,
    media_coordinates_fingerprint,
    media_coordinates_fingerprint_matches,
    media_map_metadata_matches_coordinates,
    prepare_with_options,
    render_media_location_map,
)
from track_map_layout_utils import (
    CORNER_ORDER,
    MEDIA_CLEAR_BOX_VERSION,
    PLACEMENT_ORDER,
    best_media_corner_layout,
    build_media_clear_boxes_metadata,
    cached_clear_box_options,
    clear_corner_rect_options,
    clear_box_options_for_extent,
    optimized_track_extent,
    resolve_track_map_variant,
    time_lapse_track_map_name,
    track_map_variant_names,
)


class TrackMapLayoutTests(unittest.TestCase):
    def test_media_map_fingerprint_is_captured_before_renderer_mutates_geometry(self):
        coordinates = [(50.0, 7.0), (50.1, 7.1)]
        rendered_names = []

        def mutating_renderer(tracks, *_args, **_kwargs):
            rendered_names.append(tracks[0]["name"])
            tracks[0]["points"].append((99.0, 99.0))
            return 15, 10.0, {}

        with TemporaryDirectory() as temp_dir, patch(
            "gpx_tracks_table.render_track_plot", side_effect=mutating_renderer
        ), patch("gpx_tracks_table.image_origin_metadata", return_value={}), patch(
            "gpx_tracks_table.write_plot_metadata"
        ):
            result = render_media_location_map(
                coordinates,
                date(2024, 7, 23),
                Path(temp_dir) / "map.png",
                stage_name="Cologne - Bonn",
            )
        self.assertEqual(
            result["metadata"]["media_fingerprint"],
            media_coordinates_fingerprint(date(2024, 7, 23), coordinates),
        )
        self.assertEqual(rendered_names, ["Cologne - Bonn"])
        self.assertEqual(result["metadata"]["track_name"], "Cologne - Bonn")

    def test_media_overview_fingerprint_is_ordered_and_coordinate_based(self):
        first = media_overview_fingerprint([(50.0, 7.0), (51.0, 8.0)])
        self.assertEqual(first, media_overview_fingerprint([(50, 7), (51, 8)]))
        self.assertNotEqual(first, media_overview_fingerprint([(51.0, 8.0), (50.0, 7.0)]))

    def test_media_map_fingerprint_uses_control_file_coordinate_precision(self):
        media_day = date(2024, 7, 25)
        precise = [(42.41805333333333, -3.20223)]
        control_file_value = [(42.418053, -3.20223)]

        self.assertNotEqual(
            media_coordinates_fingerprint(media_day, precise),
            media_coordinates_fingerprint(media_day, control_file_value),
        )
        self.assertTrue(
            media_coordinates_fingerprint_matches(
                media_coordinates_fingerprint(media_day, control_file_value),
                media_day,
                precise,
            )
        )

    def test_media_map_geometry_allows_equal_time_row_reordering(self):
        media_day = date(2024, 7, 25)
        coordinates = [(42.3759, -3.437), (42.430367, -3.500503)]
        metadata = {
            "media_fingerprint": "legacy-order",
            "media_clear_boxes": {"version": MEDIA_CLEAR_BOX_VERSION},
            "media_points": [
                {"lat": coordinates[1][0], "lon": coordinates[1][1]},
                {"lat": coordinates[0][0], "lon": coordinates[0][1]},
            ],
        }

        self.assertTrue(
            media_map_metadata_matches_coordinates(
                metadata,
                media_day,
                coordinates,
            )
        )

    def test_legacy_clear_box_cache_does_not_make_the_map_image_stale(self):
        media_day = date(2024, 7, 25)
        coordinates = [(42.418053, -3.20223)]
        metadata = {
            "media_fingerprint": media_coordinates_fingerprint(
                media_day,
                coordinates,
            ),
            "media_clear_boxes": {"version": 2},
        }
        self.assertTrue(
            media_map_metadata_matches_coordinates(
                metadata,
                media_day,
                coordinates,
            )
        )

    def test_short_map_extent_is_expanded_without_changing_its_center(self):
        expanded = extent_with_minimum_short_dimension((100.0, 1100.0, 200.0, 700.0), 2_250.0)
        self.assertAlmostEqual((expanded[0] + expanded[1]) / 2.0, 600.0)
        self.assertAlmostEqual((expanded[2] + expanded[3]) / 2.0, 450.0)
        self.assertAlmostEqual(min(expanded[1] - expanded[0], expanded[3] - expanded[2]), 2_250.0)

    def test_default_short_extent_keeps_default_tile_zoom_without_enlargement(self):
        expanded = extent_with_minimum_short_dimension(
            (0.0, 1000.0, 0.0, 500.0),
            MINIMUM_MAP_SHORT_DIMENSION_M,
        )
        self.assertEqual(
            fitted_zoom_level(16, expanded, (1916.0, 973.0)),
            16,
        )

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
            position: [(0.0, 0.0, 250.0, 200.0), (0.0, 0.0, 400.0, 100.0)]
            for position in PLACEMENT_ORDER
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
        self.assertEqual(set(converted), set(PLACEMENT_ORDER))

    def test_center_anchors_are_calculated_from_the_same_obstruction_grid(self):
        placement = (0.0, 0.0, 900.0, 600.0)
        route = [(200.0, 0.0), (200.0, 600.0), (700.0, 600.0), (700.0, 0.0)]
        options = clear_corner_rect_options(placement, route)
        self.assertEqual(set(options), set(PLACEMENT_ORDER))
        self.assertTrue(options["center"])
        self.assertTrue(options["top_center"])
        self.assertTrue(options["bottom_center"])

    def test_center_can_win_when_media_points_constrain_every_corner(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        points = [
            (100.0, 100.0),
            (900.0, 100.0),
            (100.0, 500.0),
            (900.0, 500.0),
        ]
        options = clear_corner_rect_options(
            placement,
            points,
            connect_points=False,
        )
        position, _outer, content = best_media_corner_layout(
            options,
            (1000.0, 600.0),
            0.5,
            (1600.0, 900.0),
        )
        self.assertEqual(position, "center")
        self.assertGreater(content[2], 700.0)
        self.assertGreater(content[3], 400.0)

    def test_media_point_clusters_do_not_create_a_false_diagonal_obstacle(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        points = [(100.0, 100.0), (120.0, 120.0), (880.0, 480.0), (900.0, 500.0)]
        connected = clear_corner_rect_options(
            placement,
            points,
            connect_points=True,
        )
        isolated = clear_corner_rect_options(
            placement,
            points,
            connect_points=False,
        )
        _corner, _outer, connected_content = best_media_corner_layout(
            connected,
            (1000.0, 600.0),
            0.5,
            (1600.0, 900.0),
        )
        _corner, _outer, isolated_content = best_media_corner_layout(
            isolated,
            (1000.0, 600.0),
            0.5,
            (1600.0, 900.0),
        )
        self.assertGreater(
            isolated_content[2] * isolated_content[3],
            2.0 * connected_content[2] * connected_content[3],
        )

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

    def test_paired_map_execution_writes_summary_once_then_renders_both_layouts(self):
        context = {
            "args": Namespace(),
            "tracks": [{"table_number": 1, "name": "Stage"}],
            "output_base": "Trip",
            "output_dir": "/tmp",
            "overview_path": "/tmp/Trip.png",
            "selected_numbers": [1],
            "track_plot_paths": [],
        }
        with patch(
            "gpx_tracks_table.execute_run_context",
            return_value={"overview_created": True, "created_track_plot_paths": []},
        ) as execute:
            execute_map_variants_from_context(
                context,
                selected_track_numbers=[1],
                plot_overview=True,
            )
        self.assertEqual(execute.call_count, 4)
        self.assertTrue(execute.call_args_list[0].kwargs["write_summary"])
        self.assertFalse(execute.call_args_list[1].kwargs["write_summary"])
        rendered_layouts = [
            call.args[0]["args"].map_layout
            for call in execute.call_args_list[2:]
        ]
        self.assertEqual(rendered_layouts, ["standard", "time-lapse"])


if __name__ == "__main__":
    unittest.main()
