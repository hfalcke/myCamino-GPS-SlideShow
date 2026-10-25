"""Selected tracks use live editing geometry, not the coarse overview cache."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSMakeRect

from GPXEditor import (
    GPXEditorController, PlotView, TrackRecord, lonlat_to_web_mercator,
    visible_simplified_polyline_runs,
)
from gpx_processing import ProcessingOptions, process_track_element


class SelectedTrackRenderingTests(unittest.TestCase):
    def make_view(self, mode="overview"):
        tracks = [SimpleNamespace(nr=1, hidden=False), SimpleNamespace(nr=2, hidden=False)]
        full = SimpleNamespace(segments=[SimpleNamespace(points=list(range(2408)))])
        coarse = SimpleNamespace(segments=[SimpleNamespace(points=list(range(320)))])
        controller = SimpleNamespace(
            tracks=tracks, selected_nrs=[1], track_load_timer=None,
            display_processed_track=Mock(return_value=full),
            display_track_geometry=Mock(return_value=coarse),
        )
        view = SimpleNamespace(
            mode=mode, adventure_workspace_delegate=object(), controller=controller,
            plot_info={}, interactive_tiles_active=True,
            draw_track=Mock(), draw_segment_end_caps=Mock(),
        )
        view.draw_selected_tracks_overlay = (
            lambda transformer, prepared: PlotView.draw_selected_tracks_overlay(view, transformer, prepared)
        )
        return view, tracks, full, coarse

    def test_only_unselected_overview_tracks_use_compact_geometry(self):
        view, tracks, full, coarse = self.make_view()
        prepared = PlotView.display_geometry_for_tracks(view, tracks)
        self.assertEqual(prepared, [(tracks[0], full), (tracks[1], coarse)])
        view.controller.display_processed_track.assert_called_once_with(tracks[0])
        view.controller.display_track_geometry.assert_called_once_with(tracks[1], 320)

    def test_fitted_editing_view_uses_full_geometry_for_all_tracks(self):
        view, tracks, full, _ = self.make_view(mode="track")
        prepared = PlotView.display_geometry_for_tracks(view, tracks)
        self.assertTrue(all(geometry is full for _, geometry in prepared))
        view.controller.display_track_geometry.assert_not_called()

    def test_selection_change_promotes_new_selection_to_full_geometry(self):
        view, tracks, full, coarse = self.make_view()
        PlotView.display_geometry_for_tracks(view, tracks)
        view.controller.selected_nrs = [2]
        self.assertEqual(
            PlotView.display_geometry_for_tracks(view, tracks),
            [(tracks[0], coarse), (tracks[1], full)],
        )

    def test_selected_line_drawn_once_last_with_identical_prepared_geometry(self):
        for mode in ("track", "overview"):
            with self.subTest(mode=mode):
                view, tracks, full, _ = self.make_view(mode)
                prepared = PlotView.display_geometry_for_tracks(view, tracks)
                view.controller.display_processed_track.reset_mock()
                with patch("GPXEditor.NSColor") as colors:
                    PlotView.draw_track_layers(view, prepared, Mock())
                    gray = 119 / 255
                    colors.colorWithSRGBRed_green_blue_alpha_.assert_any_call(gray, gray, gray, 1)
                self.assertEqual(view.draw_track.call_count, 3)
                selected_call = view.draw_track.call_args_list[-1]
                self.assertIs(selected_call.args[0], full.segments[0].points)
                self.assertEqual(len(selected_call.args[0]), 2408)
                self.assertEqual(selected_call.args[3], 6.0)
                # Reuse exactly the geometry prepared for this frame.
                view.controller.display_processed_track.assert_not_called()

    def test_hidden_selection_is_not_drawn(self):
        view, tracks, _, _ = self.make_view()
        tracks[0].hidden = True
        PlotView.draw_selected_tracks_overlay(view, Mock())
        view.draw_track.assert_not_called()

    def test_separate_segments_are_never_joined(self):
        view, tracks, full, _ = self.make_view()
        full.segments.append(SimpleNamespace(points=[9000, 9001]))
        PlotView.draw_selected_tracks_overlay(view, Mock(), [(tracks[0], full)])
        self.assertEqual(view.draw_track.call_count, 4)
        self.assertEqual(view.draw_track.call_args_list[-1].args[0], [9000, 9001])

    def test_zoom_and_resize_recompute_screen_space_detail(self):
        points = [(0.0, 0.0), (0.2, 0.2), (2.0, 0.0)]
        view = SimpleNamespace(bounds=lambda: NSMakeRect(0, 0, 100, 100))
        for scale, width, expected in ((1, 100, 2), (10, 100, 3), (1, 100, 2), (10, 1000, 3)):
            with self.subTest(scale=scale, width=width):
                view.bounds = lambda: NSMakeRect(0, 0, width, width)
                with patch("GPXEditor.NSBezierPath") as paths:
                    PlotView.draw_track(
                        view, points, lambda p: (10 + p[0] * scale, 10 + p[1] * scale), Mock(), 6.0
                    )
                path = paths.bezierPath.return_value
                self.assertEqual(1 + path.lineToPoint_.call_count, expected)

    def test_screen_space_simplification_keeps_visible_bends(self):
        # A fixed-index budget can miss this single hairpin between samples.
        points = [(index / 10, 50.0) for index in range(1001)]
        points[501] = (50.1, 55.0)
        for width in (400, 1000, 1920):
            runs = visible_simplified_polyline_runs(
                points, dict(min_x=0, max_x=100, min_y=0, max_y=100), (width, width)
            )
            self.assertIn(points[501], runs[0])
            self.assertEqual(runs[0][0], points[0])
            self.assertEqual(runs[0][-1], points[-1])

    def test_demo_geometry_and_point_edits_invalidate_cached_outline(self):
        source = (Path(__file__).resolve().parents[1]
                  / "demo/St-Jean-Pied-de-Port-to-Roncesvalles/1 - GPX Track"
                  / "St-Jean-Pied-de-Port-to-Roncesvalles.gpx")
        original = source.read_bytes()
        track = TrackRecord(nr=1, element=ET.fromstring(original).find("{*}trk"))
        options = ProcessingOptions()
        controller = SimpleNamespace(
            processing_parameter_signature=lambda: tuple(options.as_dict().items()),
            processing_options=lambda: options,
        )
        controller.processed_track = lambda item: GPXEditorController.processed_track(controller, item)
        before = GPXEditorController.display_processed_track(controller, track)
        self.assertGreater(len(before.points), 320)
        # Full processed geometry is the same reference used by the editor.
        self.assertIs(before, process_track_element(track.element, options))
        points = [(lonlat_to_web_mercator(p.lon, p.lat)) for p in before.points]
        extent = dict(min_x=min(p[0] for p in points), max_x=max(p[0] for p in points),
                      min_y=min(p[1] for p in points), max_y=max(p[1] for p in points))
        for width in (400, 1000, 1920):
            runs = visible_simplified_polyline_runs(points, extent, (width, width * 9 / 16))
            self.assertTrue(runs)
            self.assertEqual(runs[0][0], points[0])
            self.assertEqual(runs[-1][-1], points[-1])
        # Modify only the in-memory XML; preserve the user's demo on disk.
        track.element.find(".//{*}trkpt").set("lon", "-1.236")
        GPXEditorController.invalidate_track_metrics(controller, track)
        self.assertIsNone(track.display_geometry)
        self.assertEqual(track.display_geometry_cache, {})
        after = GPXEditorController.display_processed_track(controller, track)
        self.assertIsNot(before, after)
        self.assertNotEqual(before.points[0].lon, after.points[0].lon)
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
