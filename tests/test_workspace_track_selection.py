"""Map gestures publish the same selection as the table, without document work."""

import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from AppKit import NSMakePoint, NSMakeRect, NSEventModifierFlagCommand, NSEventModifierFlagOption, NSEventModifierFlagShift
from GPXEditor import GPXEditorController, PlotView, ElevationProfileView
from cocoa_adventure_map import AdventureMapController


def invoke(method, *args):
    return getattr(method, 'callable', method)(*args)


def event(x=10, y=10, flags=0, clicks=1):
    return SimpleNamespace(locationInWindow=lambda: NSMakePoint(x, y), modifierFlags=lambda: flags,
                           clickCount=lambda: clicks)


class WorkspaceTrackSelectionTests(unittest.TestCase):
    def setup_workspace(self, mode='overview'):
        tracks = [SimpleNamespace(nr=101, name='First'), SimpleNamespace(nr=205, name='Second'),
                  SimpleNamespace(nr=900, name='Third')]
        controller = SimpleNamespace(
            tracks=tracks, selected_nrs=[101], track_table=Mock(), suppress_selection_change=False,
            update_selection_field=Mock(), highlight_selected_rows=Mock(), redraw_open_plot_views=Mock(),
            refresh_elevation_profile_for_plot_view=Mock(), sync_elevation_profile_to_plot_view=Mock(),
            set_status=Mock(), mark_dirty=Mock(), compute_metrics=Mock(), render_summary_plot=Mock())
        controller.selected_tracks = lambda: [track for track in tracks if track.nr in controller.selected_nrs]
        controller.set_track_selection = lambda ids, **kwargs: GPXEditorController.set_track_selection(controller, ids, **kwargs)
        plot = SimpleNamespace(
            controller=controller, mode=mode, inspector=None,
            plot_info={'current_track_nr': 101, 'metadata': {'extent_mercator': {'min_x': 0, 'max_x': 100, 'min_y': 0, 'max_y': 100}}},
            cursor=(2, object(), tracks[0]), marker=1, context_overlay_coordinate=None,
            show_info=False, photo_thumbnail_hit_targets=[], long_press_timer=None,
            info_overlay_dragging=False, workspace_media_selection_active=False,
            dragged_point_index=None, dragged_point_track=None, dragged_segment_rows=[],
            setNeedsDisplay_=Mock(), update_window_title=Mock(), update_pointer_location=Mock(),
            convertPoint_fromView_=lambda point, view: point,
            current_extent=lambda: {'min_x': 0, 'max_x': 100, 'min_y': 0, 'max_y': 100},
            bounds=lambda: NSMakeRect(0, 0, 100, 100), interactive_map_rect=lambda bounds: bounds,
            set_interactive_viewport=Mock(), schedule_interactive_tiles=Mock(),
            move_cursor_to_event=Mock(), move_cursor_to_track_point=Mock(), set_selection_marker=Mock(),
            schedule_point_editing_long_press=Mock(), selected_range_at_target=Mock(return_value=None),
            nearest_track_for_location=Mock(return_value=(2.0, tracks[1], 0)),
            nearest_edit_target=Mock(return_value=('point', tracks[0], 2, None)))
        workspace = SimpleNamespace(
            gpx_controller=controller, plot_view=plot, map_state=mode, focused_track_nr=101,
            selected_track_identity='101', selected_media_path=Path('/media/photo.jpeg'),
            selected_media_paths={Path('/media/photo.jpeg')},
            workspace_media_target_at_location=Mock(return_value=None), _select_media_target=Mock(),
            _show_adventure_media=Mock(), openTrackEditor_=Mock(), show_workspace_overview=Mock(),
            beginWorkspaceMediaSelectionAt_modifierFlags_=Mock(), updateWorkspaceMediaSelectionTo_=Mock(),
            finishWorkspaceMediaSelectionAt_=Mock())
        workspace.workspaceTrackSelectionChanged_ = lambda values: AdventureMapController.workspaceTrackSelectionChanged_(workspace, values)
        workspace.workspaceMapClicked_clickCount_modifierFlags_ = lambda *args: AdventureMapController.workspaceMapClicked_clickCount_modifierFlags_(workspace, *args)
        controller.adventure_workspace_delegate = plot.adventure_workspace_delegate = workspace
        plot.waypoint_editing_enabled = lambda: PlotView.waypoint_editing_enabled(plot)
        plot.edit_target_for_mouse_down = lambda location: PlotView.edit_target_for_mouse_down(plot, location)
        return workspace, controller, plot, tracks

    def click(self, plot, flags=0, clicks=1):
        e = event(flags=flags, clicks=clicks)
        invoke(PlotView.mouseDown_, plot, e)
        invoke(PlotView.mouseUp_, plot, e)

    def assert_no_document_work(self, controller, plot):
        controller.mark_dirty.assert_not_called()
        controller.compute_metrics.assert_not_called()
        controller.render_summary_plot.assert_not_called()
        controller.track_table.reloadData.assert_not_called()
        plot.set_interactive_viewport.assert_not_called()
        plot.schedule_interactive_tiles.assert_not_called()

    def test_short_click_selects_track_on_release_and_synchronizes_table_and_profile(self):
        for mode in ('overview', 'track'):
            with self.subTest(mode=mode):
                workspace, controller, plot, tracks = self.setup_workspace(mode)
                before = copy.deepcopy(plot.plot_info['metadata'])
                invoke(PlotView.mouseDown_, plot, event())
                self.assertEqual(controller.selected_nrs, [101])
                controller.highlight_selected_rows.assert_not_called()
                plot.schedule_point_editing_long_press.assert_not_called()
                invoke(PlotView.mouseUp_, plot, event())
                self.assertEqual(controller.selected_nrs, [205])
                self.assertEqual(workspace.selected_track_identity, '205')
                self.assertEqual(plot.plot_info['current_track_nr'], 205)
                self.assertIsNone(plot.cursor)
                self.assertIsNone(plot.marker)
                self.assertEqual(plot.plot_info['metadata'], before)
                controller.highlight_selected_rows.assert_called_once()
                controller.track_table.scrollRowToVisible_.assert_called_once_with(1)
                controller.refresh_elevation_profile_for_plot_view.assert_called_once_with(plot)
                workspace.openTrackEditor_.assert_not_called()
                self.assert_no_document_work(controller, plot)

    def test_command_click_adds_removes_and_clears_track_selection(self):
        workspace, controller, plot, tracks = self.setup_workspace()
        self.click(plot, NSEventModifierFlagCommand)
        self.assertEqual(controller.selected_nrs, [101, 205])
        self.assertEqual(workspace.selected_track_identity, '')
        self.assertIsNone(plot.cursor)
        self.click(plot, NSEventModifierFlagCommand)
        self.assertEqual(controller.selected_nrs, [101])
        plot.nearest_track_for_location.return_value = (2.0, tracks[0], 0)
        self.click(plot, NSEventModifierFlagCommand)
        self.assertEqual(controller.selected_nrs, [])
        plot.schedule_point_editing_long_press.assert_not_called()
        workspace.beginWorkspaceMediaSelectionAt_modifierFlags_.assert_not_called()
        self.assert_no_document_work(controller, plot)

    def test_plain_and_command_drag_pan_without_selecting_track(self):
        for flags in (0, NSEventModifierFlagCommand, NSEventModifierFlagOption):
            with self.subTest(flags=flags):
                workspace, controller, plot, _ = self.setup_workspace()
                invoke(PlotView.mouseDown_, plot, event(flags=flags))
                invoke(PlotView.mouseDragged_, plot, event(35, 40, flags))
                invoke(PlotView.mouseUp_, plot, event(35, 40, flags))
                self.assertEqual(controller.selected_nrs, [101])
                plot.set_interactive_viewport.assert_called_once()
                plot.schedule_interactive_tiles.assert_called_once_with(immediate=True)
                controller.highlight_selected_rows.assert_not_called()
                workspace.beginWorkspaceMediaSelectionAt_modifierFlags_.assert_not_called()
                if flags == NSEventModifierFlagOption:
                    plot.nearest_track_for_location.assert_not_called()

    def test_selected_track_keeps_waypoint_and_marker_editing_but_not_on_mouse_down(self):
        for flags in (0, NSEventModifierFlagShift):
            workspace, controller, plot, tracks = self.setup_workspace()
            plot.nearest_track_for_location.return_value = (1.0, tracks[0], 2)
            invoke(PlotView.mouseDown_, plot, event(flags=flags))
            plot.schedule_point_editing_long_press.assert_called_once()
            plot.move_cursor_to_event.assert_not_called()
            invoke(PlotView.mouseUp_, plot, event(flags=flags))
            plot.move_cursor_to_event.assert_called_once()
            self.assertEqual(controller.selected_nrs, [101])
            if flags:
                plot.set_selection_marker.assert_called_once()
            else:
                plot.set_selection_marker.assert_not_called()
            self.assert_no_document_work(controller, plot)

    def test_holding_selected_waypoint_arms_existing_drag_without_selecting_another_track(self):
        from test_map_edit_responsiveness import track_record
        from GPXEditor import TrackRecord

        workspace, controller, plot, tracks = self.setup_workspace()
        tracks[0] = TrackRecord(101, track_record().element)
        plot.cursor = (2, tracks[0].points()[2], tracks[0])
        plot.marker = None
        plot.nearest_track_for_location.return_value = (1.0, tracks[0], 2)
        plot.nearest_edit_target.return_value = ('point', tracks[0], 2, None)
        controller.existing_inspector_for_track = Mock(return_value=None)
        invoke(PlotView.mouseDown_, plot, event())
        invoke(PlotView.beginPointEditingLongPress_, plot, None)
        self.assertEqual(plot.dragged_point_index, 2)
        self.assertIs(plot.dragged_point_track, tracks[0])
        invoke(PlotView.mouseUp_, plot, event())
        self.assertEqual(controller.selected_nrs, [101])
        controller.highlight_selected_rows.assert_not_called()
        controller.mark_dirty.assert_not_called()
        workspace.openTrackEditor_.assert_not_called()

    def test_cancelled_pan_does_not_become_a_delayed_track_click(self):
        workspace, controller, plot, _ = self.setup_workspace()
        invoke(PlotView.mouseDown_, plot, event())
        invoke(PlotView.mouseDragged_, plot, event(35, 40))
        # Escape cancels the pan before the mouse is released.
        plot.pan_active = False
        invoke(PlotView.mouseUp_, plot, event(35, 40, clicks=2))
        self.assertEqual(controller.selected_nrs, [101])
        workspace.openTrackEditor_.assert_not_called()
        workspace.show_workspace_overview.assert_not_called()

    def test_media_has_priority_over_track_and_command_click_remains_additive(self):
        workspace, controller, plot, _ = self.setup_workspace()
        target = (object(), False, Path('/media/photo.jpeg'))
        workspace.workspace_media_target_at_location.return_value = target
        self.click(plot, NSEventModifierFlagCommand, clicks=2)
        self.assertEqual(controller.selected_nrs, [101])
        workspace._select_media_target.assert_called_once_with(target, additive=True)
        workspace._show_adventure_media.assert_called_once()
        plot.nearest_track_for_location.assert_not_called()
        plot.schedule_point_editing_long_press.assert_not_called()
        plot.move_cursor_to_event.assert_not_called()
        self.assert_no_document_work(controller, plot)

    def test_command_drag_empty_space_still_selects_media(self):
        workspace, controller, plot, _ = self.setup_workspace()
        plot.nearest_track_for_location.return_value = None
        plot.nearest_edit_target.return_value = None
        invoke(PlotView.mouseDown_, plot, event(flags=NSEventModifierFlagCommand))
        invoke(PlotView.mouseDragged_, plot, event(35, 40, NSEventModifierFlagCommand))
        invoke(PlotView.mouseUp_, plot, event(35, 40, NSEventModifierFlagCommand))
        workspace.beginWorkspaceMediaSelectionAt_modifierFlags_.assert_called_once()
        workspace.updateWorkspaceMediaSelectionTo_.assert_called_once()
        workspace.finishWorkspaceMediaSelectionAt_.assert_called_once()
        self.assertEqual(controller.selected_nrs, [101])
        self.assert_no_document_work(controller, plot)

    def test_panning_over_linked_thumbnail_does_not_select_or_raise_the_medium(self):
        workspace, controller, plot, tracks = self.setup_workspace()
        plot.active_media_path = Path('/media/previous.jpeg')
        plot.photo_thumbnail_hit_targets = [(NSMakeRect(0, 0, 20, 20), tracks[0], 2, Path('/media/new.jpeg'))]
        invoke(PlotView.mouseDown_, plot, event(flags=NSEventModifierFlagOption))
        invoke(PlotView.mouseDragged_, plot, event(35, 40, NSEventModifierFlagOption))
        invoke(PlotView.mouseUp_, plot, event(35, 40, NSEventModifierFlagOption))
        self.assertEqual(plot.active_media_path, Path('/media/previous.jpeg'))
        self.assertEqual(workspace.selected_media_path, Path('/media/photo.jpeg'))
        self.assertEqual(controller.selected_nrs, [101])
        plot.move_cursor_to_track_point.assert_not_called()

    def test_empty_click_clears_media_without_using_stale_waypoint_cursor(self):
        workspace, controller, plot, _ = self.setup_workspace()
        plot.nearest_track_for_location.return_value = None
        plot.nearest_edit_target.return_value = None
        self.click(plot)
        self.assertEqual(workspace.selected_media_paths, set())
        self.assertIsNone(workspace.selected_media_path)
        self.assertEqual(controller.selected_nrs, [101])
        self.assert_no_document_work(controller, plot)

    def test_double_click_preserves_existing_track_table_action_without_zooming(self):
        workspace, controller, plot, _ = self.setup_workspace()
        self.click(plot, clicks=2)
        workspace.openTrackEditor_.assert_called_once_with(None)
        self.assertEqual(controller.selected_nrs, [205])
        self.assert_no_document_work(controller, plot)

    def test_table_selection_updates_main_map_and_profile_in_the_other_direction(self):
        workspace, controller, plot, _ = self.setup_workspace('track')
        indexes = SimpleNamespace(count=lambda: 1, firstIndex=lambda: 2, lastIndex=lambda: 2,
                                  containsIndex_=lambda i: i == 2)
        controller.track_table.selectedRowIndexes.return_value = indexes
        GPXEditorController.table_selection_changed(controller)
        self.assertEqual(controller.selected_nrs, [900])
        self.assertEqual(workspace.selected_track_identity, '900')
        self.assertEqual(plot.plot_info['current_track_nr'], 900)
        controller.refresh_elevation_profile_for_plot_view.assert_called_once_with(plot)
        self.assert_no_document_work(controller, plot)

    def test_repeated_selection_is_a_noop_and_uses_stable_internal_ids(self):
        workspace, controller, plot, _ = self.setup_workspace()
        controller.set_track_selection([101, 101, -1])
        controller.highlight_selected_rows.assert_not_called()
        controller.refresh_elevation_profile_for_plot_view.assert_not_called()
        self.assertEqual(controller.selected_nrs, [101])
        self.assert_no_document_work(controller, plot)

    def test_multi_selection_disables_waypoint_editing_and_profile_has_no_arbitrary_subject(self):
        workspace, controller, plot, _ = self.setup_workspace('track')
        controller.set_track_selection([101, 205])
        self.assertFalse(plot.waypoint_editing_enabled())
        profile = SimpleNamespace(plot_view=plot, controller=controller, profile_scope=None)
        self.assertTrue(ElevationProfileView.shows_all_tracks(profile))
        controller.set_track_selection([205])
        self.assertTrue(plot.waypoint_editing_enabled())
        self.assertFalse(ElevationProfileView.shows_all_tracks(profile))

    def test_invisible_track_cannot_supply_a_waypoint_target_under_empty_map_space(self):
        hidden = SimpleNamespace(points=Mock(side_effect=AssertionError('must not inspect invisible track')))
        plot = SimpleNamespace(display_tracks=lambda: [object()], current_track_for_title=lambda: hidden)
        self.assertIsNone(PlotView.nearest_edit_target(plot, NSMakePoint(10, 10)))
        hidden.points.assert_not_called()


if __name__ == '__main__':
    unittest.main()
