import time
import unittest
from collections import OrderedDict
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSMakeRect, NSEventModifierFlagShift
from GPXEditor import ElevationProfileView, GPXEditorController, PlotView
from interactive_tile_viewport import visible_tiles, zoomed_extent


def invoke(method, *args):
    return getattr(method, "callable", method)(*args)


def key_event(key, flags=0):
    return SimpleNamespace(
        charactersIgnoringModifiers=lambda: key, characters=lambda: key,
        modifierFlags=lambda: flags, keyCode=lambda: 0, isARepeat=lambda: False,
    )


class MarkerNavigationTests(unittest.TestCase):
    def plot(self):
        points = [object() for _ in range(10)]
        track = SimpleNamespace(nr=22, display_label="Track #22", points=lambda: points)
        plot = SimpleNamespace(
            cursor=(8, points[8], track), marker=None, show_info=False,
            controller=SimpleNamespace(refresh_elevation_profile_for_plot_view=Mock(), set_status=Mock()),
            sync_inspector_selection=Mock(), update_window_title=Mock(), setNeedsDisplay_=Mock(),
        )
        return plot, track, points

    def test_set_marker_and_swap_twice_preserve_selected_range(self):
        plot, track, points = self.plot()
        PlotView.set_selection_marker(plot)
        self.assertEqual(plot.marker, 8)
        plot.cursor = (2, points[2], track)
        PlotView.set_selection_marker(plot, swap=True)
        self.assertEqual(plot.marker, 2)
        self.assertEqual(plot.cursor, (8, points[8], track))
        PlotView.set_selection_marker(plot, swap=True)
        self.assertEqual(plot.marker, 8)
        self.assertEqual(plot.cursor, (2, points[2], track))
        self.assertFalse(plot.show_info)
        self.assertEqual(plot.sync_inspector_selection.call_count, 3)

    def test_swap_without_marker_sets_one(self):
        plot, _track, _points = self.plot()
        PlotView.set_selection_marker(plot, swap=True)
        self.assertEqual(plot.marker, 8)
        self.assertEqual(plot.cursor[0], 8)

    def test_stale_marker_is_not_used_as_an_index(self):
        plot, _track, _points = self.plot()
        plot.marker = 20
        PlotView.set_selection_marker(plot, swap=True)
        self.assertEqual(plot.cursor[0], 8)
        plot.sync_inspector_selection.assert_not_called()

    def test_short_click_inside_marked_range_updates_endpoint_on_release(self):
        track = object()
        plot = SimpleNamespace(
            info_overlay_dragging=False, workspace_media_selection_active=False,
            long_press_timer=None, long_press_target=("point", track, 4, 0),
            pan_active=False, dragged_point_index=None, dragged_point_track=None,
            dragged_segment_rows=[], adventure_workspace_delegate=None,
            selected_range_at_target=Mock(return_value=(track, 2, 8)),
            move_cursor_to_event=Mock(), mouse_down_started_at=time.monotonic(),
            mouse_down_click_count=1,
        )
        event = SimpleNamespace(modifierFlags=lambda: 0)
        invoke(PlotView.mouseUp_, plot, event)
        plot.move_cursor_to_event.assert_called_once_with(event)

    def test_pan_does_not_move_the_selection_endpoint(self):
        plot = SimpleNamespace(
            info_overlay_dragging=False, workspace_media_selection_active=False,
            long_press_timer=None, long_press_target=("point", object(), 4, 0),
            pan_active=True, schedule_interactive_tiles=Mock(), move_cursor_to_event=Mock(),
            controller=SimpleNamespace(sync_elevation_profile_to_plot_view=Mock(), set_status=Mock()),
        )
        invoke(PlotView.mouseUp_, plot, SimpleNamespace())
        plot.move_cursor_to_event.assert_not_called()
        plot.schedule_interactive_tiles.assert_called_once_with(immediate=True)

    def test_map_square_has_same_14_point_size_as_cursor(self):
        plot, _track, points = self.plot()
        plot.marker = 2
        transform = Mock(return_value=(40, 60))
        with patch("GPXEditor.NSBezierPath") as paths, patch("GPXEditor.NSColor"):
            PlotView.draw_selection_marker(plot, transform)
        transform.assert_called_once_with(points[2])
        rect = paths.bezierPathWithRect_.call_args.args[0]
        self.assertEqual((rect.origin.x, rect.origin.y, rect.size.width, rect.size.height), (33, 53, 14, 14))

    def test_profile_square_has_same_8_point_size_as_cursor(self):
        plot, track, _points = self.plot()
        plot.marker = 2
        rows = [{"track": track, "index": 2, "distance": 3, "elevation": 80}]
        transform = Mock(return_value=(40, 60))
        with patch("GPXEditor.NSBezierPath") as paths, patch("GPXEditor.NSColor"):
            invoke(ElevationProfileView.draw_selection_marker, SimpleNamespace(plot_view=plot), rows, transform, 0, 5)
        rect = paths.bezierPathWithRect_.call_args.args[0]
        self.assertEqual((rect.origin.x, rect.origin.y, rect.size.width, rect.size.height), (36, 56, 8, 8))

    def test_profile_click_requests_marker_preservation_without_focusing_map(self):
        track = object()
        plot = SimpleNamespace(move_cursor_to_track_point=Mock(), sync_inspector_selection=Mock(), setNeedsDisplay_=Mock())
        profile = SimpleNamespace(
            plot_view=plot, nearest_row_for_event=lambda event: {"track": track, "index": 4},
            controller=SimpleNamespace(select_track_in_table=Mock()), setNeedsDisplay_=Mock(),
        )
        track = SimpleNamespace(nr=22)
        invoke(ElevationProfileView.move_cursor_to_event, profile, object())
        plot.move_cursor_to_track_point.assert_called_once_with(track, 4, preserve_marker=True, focus_plot=False)

    def test_profile_M_swaps_and_m_sets_marker(self):
        plot = SimpleNamespace(mode="track", cursor=object(), set_selection_marker=Mock())
        profile = SimpleNamespace(plot_view=plot, setNeedsDisplay_=Mock())
        invoke(ElevationProfileView.keyDown_, profile, key_event("m"))
        plot.set_selection_marker.assert_called_once_with(swap=False)
        plot.set_selection_marker.reset_mock()
        invoke(ElevationProfileView.keyDown_, profile, key_event("m", NSEventModifierFlagShift))
        plot.set_selection_marker.assert_called_once_with(swap=True)


class SettledTileResolutionTests(unittest.TestCase):
    def plot(self):
        extent = {"min_x": 500000, "max_x": 510000, "min_y": 5900000, "max_y": 5907200}
        controller = SimpleNamespace(
            maximum_map_zoom=19, maximum_map_tiles=48, map_provider="osm",
            tile_cache_dir=None, map_request_timeout_seconds=5, custom_map_url="",
            custom_map_attribution="", map_credential_id="default",
        )
        controller.tile_diagnostics = lambda *args: GPXEditorController.tile_diagnostics(controller, *args)
        controller.effective_tile_zoom_for_limit = lambda *args: GPXEditorController.effective_tile_zoom_for_limit(controller, *args)
        controller.effective_tile_zoom = lambda *args: GPXEditorController.effective_tile_zoom(controller, *args)
        plot = SimpleNamespace(
            controller=controller, mode="track", interactive_tiles_active=True, pan_active=False,
            plot_info={"tile_zoom_level": 2}, interactive_tile_generation=0,
            interactive_tiles=OrderedDict([((2, 2, 1), object())]), interactive_tile_futures={},
            cancel_interactive_tile_requests=Mock(), ensure_interactive_tile_store_provider=lambda: "osm",
            current_extent=lambda: extent, bounds=lambda: NSMakeRect(0, 0, 1000, 720),
            interactive_tile_executor=SimpleNamespace(submit=Mock(side_effect=lambda *args, **kwargs: Future())),
            deliver_interactive_tile=Mock(),
        )
        plot.current_tile_zoom = lambda: PlotView.current_tile_zoom(plot)
        plot.recommended_tile_zoom = lambda value: PlotView.recommended_tile_zoom(plot, value)
        return plot, extent

    def test_requests_upgrade_coarse_saved_zoom_from_live_viewport(self):
        plot, extent = self.plot()
        old_tile = plot.interactive_tiles[(2, 2, 1)]
        invoke(PlotView.requestInteractiveTiles_, plot, None)
        self.assertGreater(plot.current_tile_zoom(), 2)
        self.assertEqual(plot.current_tile_zoom(), plot.recommended_tile_zoom(extent))
        self.assertLessEqual(len(plot.interactive_tile_target_keys), 48)
        self.assertIs(plot.interactive_tiles[(2, 2, 1)], old_tile)
        self.assertTrue(all(key[0] == plot.current_tile_zoom() for key in plot.interactive_tile_target_keys))
        self.assertEqual(plot.interactive_tile_executor.submit.call_count, len(plot.interactive_tile_target_keys))

    def test_zooming_increases_requested_resolution_without_unbounded_tile_count(self):
        plot, extent = self.plot()
        levels = []
        for _ in range(4):
            invoke(PlotView.requestInteractiveTiles_, plot, None)
            levels.append(plot.current_tile_zoom())
            self.assertLessEqual(len(visible_tiles(extent, plot.current_tile_zoom())), 48)
            smaller = zoomed_extent(extent, 2)
            extent.update(smaller)
        self.assertEqual(levels, list(range(levels[0], levels[0] + 4)))

    def test_repeated_settled_request_deduplicates_pending_tiles(self):
        plot, _extent = self.plot()
        invoke(PlotView.requestInteractiveTiles_, plot, None)
        count = plot.interactive_tile_executor.submit.call_count
        invoke(PlotView.requestInteractiveTiles_, plot, None)
        self.assertEqual(plot.interactive_tile_executor.submit.call_count, count)

    def test_settled_request_is_not_lost_after_an_interrupted_pan(self):
        plot, _extent = self.plot()
        plot.pan_active = True
        invoke(PlotView.requestInteractiveTiles_, plot, None)
        self.assertTrue(plot.interactive_tile_executor.submit.called)

    def test_cancelled_pan_timer_cannot_start_obsolete_requests(self):
        plot, _extent = self.plot()
        plot.interactive_tile_timer = None
        plot.pan_active = True
        invoke(PlotView.requestInteractiveTiles_, plot, object())
        plot.interactive_tile_executor.submit.assert_not_called()

    def test_pan_preview_cancels_requests_without_scheduling_downloads(self):
        plot, extent = self.plot()
        timer = Mock()
        pending = Future()
        plot.interactive_tile_timer = timer
        plot.interactive_tile_futures[(0, (14, 0, 0))] = pending
        plot.cancel_interactive_tile_requests = lambda **kwargs: PlotView.cancel_interactive_tile_requests(plot, **kwargs)
        plot.schedule_interactive_tiles = Mock()
        plot.setNeedsDisplay_ = Mock()
        PlotView.set_interactive_viewport(plot, extent, request_tiles=False)
        timer.invalidate.assert_called_once()
        self.assertTrue(pending.cancelled())
        plot.schedule_interactive_tiles.assert_not_called()
        self.assertIsNone(plot.interactive_tile_timer)


if __name__ == "__main__":
    unittest.main()
