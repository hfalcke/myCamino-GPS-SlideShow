import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import NSRunLoopCommonModes, PlotView, TrackElementsUndoEntry, TrackInspectorController, TrackRecord, qname
from workspace_mutations import MutationKind


def invoke(method, *args):
    return getattr(method, "callable", method)(*args)


class InspectorMapReuseTests(unittest.TestCase):
    def test_plot_uses_explicit_track_and_detaches_previous_waypoint_table(self):
        old = SimpleNamespace(owns_plot_window=True)
        view = SimpleNamespace(inspector=old, setNeedsDisplay_=Mock())
        old.plot_view = view
        track = SimpleNamespace(nr=33)
        parent = SimpleNamespace(
            update_selection_field=Mock(), highlight_selected_rows=Mock(),
            existing_plot_view=Mock(return_value=(object(), view)),
            open_plot_window=Mock(return_value=view),
        )
        inspector = SimpleNamespace(parent=parent, track=track, owns_plot_window=False)
        invoke(TrackInspectorController.plot_, inspector, None)
        parent.open_plot_window.assert_called_once_with("track", tracks_override=[track])
        self.assertEqual(parent.selected_nrs, [33])
        self.assertIs(view.inspector, inspector)
        self.assertIs(inspector.plot_view, view)
        self.assertIsNone(old.plot_view)
        self.assertFalse(old.owns_plot_window)

    def test_reusing_adventure_overview_does_not_make_inspector_own_main_map(self):
        view = SimpleNamespace(inspector=None, setNeedsDisplay_=Mock())
        parent = SimpleNamespace(
            update_selection_field=Mock(), highlight_selected_rows=Mock(),
            existing_plot_view=Mock(return_value=None), open_plot_window=Mock(return_value=view),
            adventure_workspace_delegate=SimpleNamespace(plot_view=view),
        )
        inspector = SimpleNamespace(parent=parent, track=SimpleNamespace(nr=33), owns_plot_window=True)
        invoke(TrackInspectorController.plot_, inspector, None)
        self.assertFalse(inspector.owns_plot_window)

    def test_new_standalone_inspector_map_keeps_existing_ownership_behavior(self):
        view = SimpleNamespace(inspector=None, setNeedsDisplay_=Mock())
        parent = SimpleNamespace(
            update_selection_field=Mock(), highlight_selected_rows=Mock(),
            existing_plot_view=Mock(return_value=None), open_plot_window=Mock(return_value=view),
        )
        inspector = SimpleNamespace(parent=parent, track=SimpleNamespace(nr=33), owns_plot_window=False)
        invoke(TrackInspectorController.plot_, inspector, None)
        self.assertTrue(inspector.owns_plot_window)


class PlotReplacementTests(unittest.TestCase):
    def test_tile_timer_runs_in_common_modes_while_other_controls_are_active(self):
        timer = Mock()
        old_timer = Mock()
        loop = Mock()
        plot = SimpleNamespace(interactive_tile_timer=old_timer)
        with patch('GPXEditor.NSTimer') as timers, patch('GPXEditor.NSRunLoop') as loops:
            timers.timerWithTimeInterval_target_selector_userInfo_repeats_.return_value = timer
            loops.mainRunLoop.return_value = loop
            PlotView.schedule_interactive_tiles(plot)
        old_timer.invalidate.assert_called_once()
        self.assertIs(plot.interactive_tile_timer, timer)
        loop.addTimer_forMode_.assert_called_once_with(timer, NSRunLoopCommonModes)

    def test_keyboard_zoom_cancels_both_pending_gesture_completions(self):
        wheel, pinch = Mock(), Mock()
        plot = SimpleNamespace(
            wheel_finish_timer=wheel, pinch_finish_timer=pinch, cursor=None,
            last_gesture_location=None, zoomed_extent=Mock(return_value={'extent': True}),
            render_extent=Mock(),
        )
        PlotView.change_view_zoom(plot, 2)
        wheel.invalidate.assert_called_once()
        pinch.invalidate.assert_called_once()
        self.assertIsNone(plot.wheel_finish_timer)
        self.assertIsNone(plot.pinch_finish_timer)
        plot.render_extent.assert_called_once()

    def test_track_replacement_cancels_stale_pan_pinch_wheel_and_hold(self):
        timers = [Mock(), Mock(), Mock()]
        plot = SimpleNamespace(
            pan_active=True, pan_origin=object(), pan_restore_extent={"old": True},
            pinch_start_tile_zoom=2, pinch_accumulated_scale=.25,
            pinch_finish_timer=timers[0], wheel_finish_timer=timers[1], long_press_timer=timers[2],
            long_press_target=object(), last_gesture_location=object(),
        )
        PlotView.reset_plot_interaction(plot)
        self.assertFalse(plot.pan_active)
        self.assertIsNone(plot.pan_restore_extent)
        self.assertIsNone(plot.pinch_start_tile_zoom)
        self.assertEqual(plot.pinch_accumulated_scale, 1)
        self.assertIsNone(plot.last_gesture_location)
        self.assertIsNone(plot.long_press_target)
        for timer in timers:
            timer.invalidate.assert_called_once()

    def test_replacement_sets_live_resolution_in_current_and_per_track_models(self):
        extent = {"min_x": 1, "max_x": 2, "min_y": 3, "max_y": 4}
        info = {"current_track_nr": 33, "metadata": {"extent_mercator": extent},
                "tile_zoom_level": 2, "tracks": {33: {"tile_zoom_level": 2}}}
        plot = SimpleNamespace(
            reset_plot_interaction=Mock(), ensure_interactive_tile_store_provider=Mock(),
            clone_plot_info=lambda value: value.copy(), interactive_tile_generation=0,
            cancel_interactive_tile_requests=Mock(), interactive_tile_target_keys=set(),
            schedule_interactive_tiles=Mock(), setNeedsDisplay_=Mock(),
            controller=SimpleNamespace(sync_elevation_profile_to_plot_view=Mock()),
            current_extent=lambda: extent, recommended_tile_zoom=Mock(return_value=16),
        )
        plot.refresh_interactive_tile_zoom = lambda: PlotView.refresh_interactive_tile_zoom(plot)
        PlotView.replace_plot_info(plot, info)
        plot.reset_plot_interaction.assert_called_once()
        self.assertEqual(info["tile_zoom_level"], 16)
        self.assertEqual(info["tracks"][33]["tile_zoom_level"], 16)
        plot.schedule_interactive_tiles.assert_called_once_with(immediate=True)

    def test_switching_to_cached_track_also_recalculates_its_resolution(self):
        track = SimpleNamespace(nr=33)
        info = {"tracks": {33: {"tile_zoom_level": 2, "metadata": {"extent_mercator": {"new": True}}}},
                "current_track_nr": 22}
        plot = SimpleNamespace(
            plot_info=info, reset_plot_interaction=Mock(), refresh_interactive_tile_zoom=Mock(),
            interactive_tiles_active=True, interactive_tile_generation=0,
            cancel_interactive_tile_requests=Mock(), schedule_interactive_tiles=Mock(), update_window_title=Mock(),
        )
        PlotView.update_track_plot_info(plot, track)
        self.assertEqual(info["current_track_nr"], 33)
        plot.reset_plot_interaction.assert_called_once()
        plot.refresh_interactive_tile_zoom.assert_called_once()
        plot.schedule_interactive_tiles.assert_called_once()

    def edit(self, owner="controller"):
        element = ET.Element(qname("trk"))
        segment = ET.SubElement(element, qname("trkseg"))
        point = ET.SubElement(segment, qname("trkpt"), lat="47", lon="5")
        track = TrackRecord(22, element)
        original = ET.tostring(element, encoding="unicode")
        point.set("lat", "48")
        entry = TrackElementsUndoEntry({22: original}, MutationKind.GEOMETRY)
        inspector = SimpleNamespace(track=track, undo_stack=[original], reload_rows=Mock())
        plot = SimpleNamespace(
            dragged_point_track=track, drag_original_xml=original, drag_undo_owner=owner,
            inspector=inspector, controller=SimpleNamespace(undo_stack=[entry], invalidate_track_metrics=Mock()),
        )
        return plot, track, original

    def test_replacing_map_rolls_back_unreleased_point_edits(self):
        for owner in ("controller", "inspector"):
            with self.subTest(owner=owner):
                plot, track, original = self.edit(owner)
                PlotView.reset_plot_interaction(plot)
                self.assertEqual(ET.tostring(track.element, encoding="unicode"), original)
                self.assertIsNone(plot.drag_original_xml)
                history = plot.controller.undo_stack if owner == "controller" else plot.inspector.undo_stack
                self.assertEqual(history, [])
                plot.controller.invalidate_track_metrics.assert_called_once_with(track)
                plot.inspector.reload_rows.assert_called_once()

    def test_cancel_does_not_pop_an_unrelated_undo_entry(self):
        plot, _track, _original = self.edit()
        other = object()
        plot.controller.undo_stack.append(other)
        PlotView.reset_plot_interaction(plot)
        self.assertIs(plot.controller.undo_stack[-1], other)


if __name__ == "__main__":
    unittest.main()
