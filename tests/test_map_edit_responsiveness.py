import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSMakePoint, NSMakeRect
from GPXEditor import GPXEditorController, PlotView, TrackRecord, qname
from gpx_processing import ProcessingOptions, extract_raw_track_points, process_raw_points


def invoke(method, *args):
    return getattr(method, "callable", method)(*args)


def track_record():
    element = ET.Element(qname("trk"))
    segment = ET.SubElement(element, qname("trkseg"))
    for index in range(16):
        point = ET.SubElement(segment, qname("trkpt"), lat=str(47 + index * .001), lon="5")
        ET.SubElement(point, qname("ele")).text = str(100 + index * 2)
    return TrackRecord(22, element)


class InfoOverlayMouseTests(unittest.TestCase):
    def plot(self):
        plot = SimpleNamespace(
            convertPoint_fromView_=lambda point, view: point,
            point_inside_rect=PlotView.point_inside_rect,
            show_info=True, info_overlay_close_rect=NSMakeRect(90, 90, 20, 20),
            info_overlay_rect=NSMakeRect(0, 0, 120, 120),
            info_overlay_dragging=False, info_overlay_origin=None,
            marker=2, cursor=(8, object(), object()),
            long_press_timer=Mock(), long_press_target=object(), long_press_origin=object(),
            setNeedsDisplay_=Mock(), move_cursor_to_event=Mock(),
            adventure_workspace_delegate=SimpleNamespace(workspaceMapClicked_clickCount_modifierFlags_=Mock()),
        )
        plot.dismiss_info_overlay = lambda: PlotView.dismiss_info_overlay(plot)
        return plot

    def test_close_consumes_down_drag_and_up_with_a_marked_range(self):
        plot = self.plot()
        timer = plot.long_press_timer
        event = SimpleNamespace(locationInWindow=lambda: NSMakePoint(100, 100))
        invoke(PlotView.mouseDown_, plot, event)
        invoke(PlotView.mouseDragged_, plot, event)
        invoke(PlotView.mouseUp_, plot, event)
        self.assertFalse(plot.show_info)
        self.assertTrue(plot.info_overlay_dismissed)
        self.assertEqual(plot.marker, 2)
        self.assertEqual(plot.cursor[0], 8)
        self.assertIsNone(plot.long_press_target)
        timer.invalidate.assert_called_once()
        plot.move_cursor_to_event.assert_not_called()
        plot.adventure_workspace_delegate.workspaceMapClicked_clickCount_modifierFlags_.assert_not_called()

    def test_moving_overlay_also_consumes_mouse_release(self):
        plot = self.plot()
        down = SimpleNamespace(locationInWindow=lambda: NSMakePoint(20, 20))
        drag = SimpleNamespace(locationInWindow=lambda: NSMakePoint(35, 40))
        invoke(PlotView.mouseDown_, plot, down)
        invoke(PlotView.mouseDragged_, plot, drag)
        invoke(PlotView.mouseUp_, plot, drag)
        self.assertEqual(plot.info_overlay_origin, NSMakePoint(15, 20))
        self.assertTrue(plot.show_info)
        plot.move_cursor_to_event.assert_not_called()
        plot.adventure_workspace_delegate.workspaceMapClicked_clickCount_modifierFlags_.assert_not_called()

    def test_help_and_startup_hint_do_not_erase_info_hit_rectangles(self):
        plot = SimpleNamespace(info_overlay_origin=None, draw_text=Mock())
        with patch("GPXEditor.NSColor"), patch("GPXEditor.NSBezierPath"):
            PlotView.draw_overlay_panel(plot, ["Point info"], NSMakeRect(0, 0, 900, 600), closable=True)
            close, panel = plot.info_overlay_close_rect, plot.info_overlay_rect
            PlotView.draw_overlay_panel(plot, ["Press h for help"], NSMakeRect(0, 0, 900, 600))
        self.assertIs(plot.info_overlay_close_rect, close)
        self.assertIs(plot.info_overlay_rect, panel)

    def test_hovering_overlay_does_not_hit_test_all_track_points(self):
        plot = self.plot()
        plot.update_pointer_location = Mock()
        invoke(PlotView.mouseMoved_, plot, SimpleNamespace(locationInWindow=lambda: NSMakePoint(20, 20)))
        plot.update_pointer_location.assert_not_called()


class LongPressCostTests(unittest.TestCase):
    def plot(self, kind="point", selected=False):
        track = track_record()
        controller = SimpleNamespace(
            undo_stack=[], redo_stack=["keep redo until actual edit"],
            existing_inspector_for_track=Mock(return_value=None),
            set_status=Mock(), invalidate_track_metrics=Mock(), mark_dirty=Mock(),
            push_undo=Mock(side_effect=AssertionError("Must not snapshot the entire Adventure")),
        )
        controller.push_track_elements_undo = lambda tracks: GPXEditorController.push_track_elements_undo(controller, tracks)
        plot = SimpleNamespace(
            controller=controller, inspector=None,
            long_press_origin=NSMakePoint(10, 10), long_press_target=(kind, track, 2, .5),
            selected_range_at_target=Mock(return_value=(track, 1, 5) if selected else None),
            coordinate_for_view_point=lambda location: (5.0, 47.0025),
            dragged_point_index=None, dragged_point_track=None,
            dragged_segment_rows=[], drag_inserted_point=False,
            update_window_title=Mock(), setNeedsDisplay_=Mock(),
            info_overlay_dragging=False, workspace_media_selection_active=False,
            pan_active=False, adventure_workspace_delegate=None,
        )
        plot.push_map_edit_undo = lambda *args: PlotView.push_map_edit_undo(plot, *args)
        plot.prepare_drag_undo = lambda target: PlotView.prepare_drag_undo(plot, target)
        return plot, track

    def test_hold_point_then_release_creates_no_undo_dirty_state_or_table_reload(self):
        plot, track = self.plot()
        inspector = SimpleNamespace(track=track, reload_rows=Mock(), push_undo=Mock())
        plot.controller.existing_inspector_for_track.return_value = inspector
        with patch("GPXEditor.ET.tostring", side_effect=AssertionError("No snapshot just to hold a point")):
            invoke(PlotView.beginPointEditingLongPress_, plot, None)
            invoke(PlotView.mouseUp_, plot, SimpleNamespace())
        self.assertEqual(plot.controller.redo_stack, ["keep redo until actual edit"])
        self.assertEqual(plot.controller.undo_stack, [])
        plot.controller.mark_dirty.assert_not_called()
        inspector.reload_rows.assert_not_called()
        inspector.push_undo.assert_not_called()

    def test_hold_range_then_release_creates_no_undo_or_dirty_state(self):
        plot, _track = self.plot(selected=True)
        with patch("GPXEditor.ET.tostring", side_effect=AssertionError("No snapshot just to hold a range")):
            invoke(PlotView.beginPointEditingLongPress_, plot, None)
            self.assertEqual(plot.dragged_segment_rows, [1, 2, 3, 4, 5])
            invoke(PlotView.mouseUp_, plot, SimpleNamespace())
        plot.controller.mark_dirty.assert_not_called()
        self.assertEqual(plot.controller.undo_stack, [])
        self.assertFalse(plot.dragged_segment_rows)

    def test_insert_uses_one_track_snapshot_reused_for_escape(self):
        plot, track = self.plot(kind="segment")
        with patch("GPXEditor.ET.tostring", wraps=ET.tostring) as serialize:
            invoke(PlotView.beginPointEditingLongPress_, plot, None)
            plot.prepare_drag_undo(track)
        serialize.assert_called_once_with(track.element, encoding="unicode")
        self.assertEqual(len(track.points()), 17)
        self.assertEqual(len(plot.controller.undo_stack), 1)
        self.assertIs(plot.drag_original_xml, plot.controller.undo_stack[0].elements[22])
        plot.controller.push_undo.assert_not_called()

    def test_first_movement_snapshot_is_only_for_edited_track(self):
        plot, track = self.plot()
        invoke(PlotView.beginPointEditingLongPress_, plot, None)
        with patch("GPXEditor.ET.tostring", wraps=ET.tostring) as serialize:
            plot.prepare_drag_undo(track)
            plot.prepare_drag_undo(track)
        self.assertEqual(serialize.call_count, 1)
        self.assertEqual(set(plot.controller.undo_stack[0].elements), {22})
        self.assertEqual(plot.controller.redo_stack, [])


class SelectedRangeMetricsCacheTests(unittest.TestCase):
    def controller(self):
        controller = SimpleNamespace(options=ProcessingOptions())
        controller.processing_options = lambda: controller.options
        controller.processing_parameter_signature = lambda: tuple(controller.options.as_dict().items())
        return controller

    def test_same_selection_repaint_neither_extracts_nor_processes_points(self):
        controller, track = self.controller(), track_record()
        with patch("GPXEditor.extract_raw_track_points", wraps=extract_raw_track_points) as extract, \
                patch("GPXEditor.process_raw_points", wraps=process_raw_points) as process:
            first = GPXEditorController.compute_point_range_metrics(controller, track, [(1, 10)])
            second = GPXEditorController.compute_point_range_metrics(controller, track, [(10, 1)])
        self.assertEqual(first, second)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(process.call_count, 1)

    def test_edits_and_processing_settings_invalidate_cached_selection(self):
        controller, track = self.controller(), track_record()
        first = GPXEditorController.compute_point_range_metrics(controller, track, [(1, 10)])
        track.points()[4].element.set("lon", "6")
        GPXEditorController.invalidate_track_metrics(controller, track)
        changed = GPXEditorController.compute_point_range_metrics(controller, track, [(1, 10)])
        self.assertNotEqual(first["length_km"], changed["length_km"])
        controller.options = ProcessingOptions(running_speed_window_distance_m=750)
        with patch("GPXEditor.process_raw_points", wraps=process_raw_points) as process:
            GPXEditorController.compute_point_range_metrics(controller, track, [(1, 10)])
        self.assertEqual(process.call_count, 1)

    def test_cache_is_bounded_and_empty_selection_does_no_processing(self):
        controller, track = self.controller(), track_record()
        with patch("GPXEditor.extract_raw_track_points", wraps=extract_raw_track_points) as extract:
            self.assertIsNone(GPXEditorController.compute_point_range_metrics(controller, track, []))
        extract.assert_not_called()
        for end in range(2, 15):
            GPXEditorController.compute_point_range_metrics(controller, track, [(0, end)])
        self.assertEqual(len(track.point_range_metrics_cache), 8)


if __name__ == "__main__":
    unittest.main()
