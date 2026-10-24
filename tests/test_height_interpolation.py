import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagOption, NSEventModifierFlagShift
from GPXEditor import (
    ElevationProfileView, GPXEditorController, PlotView, TrackRecord,
    height_interpolation_key, qname,
)
from gpx_point_editing import interpolated_height_updates, point_locations
from workspace_mutations import MutationKind


def make_track(*segments):
    track = ET.Element(qname("trk"))
    for values in segments:
        segment = ET.SubElement(track, qname("trkseg"))
        for longitude, elevation in values:
            point = ET.SubElement(segment, qname("trkpt"), lat="0", lon=str(longitude))
            if elevation is not None:
                ET.SubElement(point, qname("ele")).text = str(elevation)
            ET.SubElement(point, qname("time")).text = "2026-01-01T10:00:00Z"
    return track


def event(key, flags=0):
    return SimpleNamespace(
        charactersIgnoringModifiers=lambda: key, characters=lambda: key,
        keyCode=lambda: 0, modifierFlags=lambda: flags, isARepeat=lambda: False,
    )


def invoke(method, *args):
    return getattr(method, "callable", method)(*args)


class HeightInterpolationTests(unittest.TestCase):
    def test_distance_not_row_number_and_no_xml_mutation_during_planning(self):
        track = make_track([(0, 100), (.001, 900), (.004, 400)])
        original = ET.tostring(track)
        updates = interpolated_height_updates(track, [(0, 2)])
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1], "175.000")
        self.assertIs(updates[0][0], point_locations(track)[1][0])
        self.assertEqual(ET.tostring(track), original)

    def test_bent_route_uses_total_path_distance_not_endpoint_distance(self):
        track = make_track([(0, 100), (.001, 900), (0, 200)])
        self.assertEqual(interpolated_height_updates(track, [(0, 2)])[0][1], "150.000")

    def test_missing_interior_height_can_be_filled(self):
        track = make_track([(0, 100), (.001, None), (.002, 200)])
        self.assertEqual(interpolated_height_updates(track, [(0, 2)])[0][1], "150.000")

    def test_zero_distance_falls_back_to_even_spacing(self):
        track = make_track([(0, 0), (0, 999), (0, -999), (0, 300)])
        self.assertEqual([text for _, text in interpolated_height_updates(track, [(0, 3)])],
                         ["100.000", "200.000"])

    def test_reverse_range_and_overlapping_ranges_are_normalized(self):
        track = make_track([(0, 100), (.001, 999), (.002, 300), (.003, 400)])
        updates = interpolated_height_updates(track, [(2, 0), (0, 3)])
        self.assertEqual([text for _, text in updates], ["200.000"])

    def test_disjoint_selections_do_not_change_unselected_points(self):
        track = make_track([(i * .001, elevation) for i, elevation in enumerate(
            [0, 999, 200, 999, 400, -999, 600]
        )])
        updates = interpolated_height_updates(track, [(0, 2), (4, 6)])
        points = [point for point, _, _ in point_locations(track)]
        self.assertEqual([(points.index(point), text) for point, text in updates],
                         [(1, "100.000"), (5, "500.000")])

    def test_segment_boundaries_have_independent_anchors(self):
        track = make_track([(0, 0), (.001, 999), (.002, 200)],
                           [(1, 1000), (1.001, 999), (1.002, 1200)])
        self.assertEqual([text for _, text in interpolated_height_updates(track, [(0, 5)])],
                         ["100.000", "1100.000"])

    def test_invalid_endpoint_aborts_entire_plan_without_partial_edits(self):
        for invalid in (None, "NaN", "inf", "not a height"):
            track = make_track([(0, 0), (.001, 999), (.002, 200)],
                               [(1, invalid), (1.001, 999), (1.002, 1200)])
            before = ET.tostring(track)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                interpolated_height_updates(track, [(0, 5)])
            self.assertEqual(ET.tostring(track), before)

    def test_invalid_coordinate_rejected(self):
        track = make_track([(0, 0), ("NaN", 999), (.002, 200)])
        with self.assertRaises(ValueError):
            interpolated_height_updates(track, [(0, 2)])

    def test_noop_short_or_already_linear_selections(self):
        track = make_track([(0, 0), (.001, 100), (.002, 200)])
        for ranges in ([], [(0, 0)], [(0, 1)], [(0, 2)]):
            self.assertEqual(interpolated_height_updates(track, ranges), [])

    def controller(self, track):
        controller = SimpleNamespace(
            undo_stack=[], redo_stack=[], tracks=[track], plot_windows=[],
            existing_inspector_for_track=Mock(return_value=None),
            invalidate_track_metrics=Mock(), mark_dirty=Mock(),
            redraw_open_plot_views=Mock(), refresh_open_plot_views=Mock(), set_status=Mock(),
        )
        controller.push_track_elements_undo = lambda *args: GPXEditorController.push_track_elements_undo(controller, *args)
        return controller

    def test_controller_creates_one_elevation_undo_and_preserves_other_xml(self):
        track = TrackRecord(26, make_track([(0, 100), (.001, 900), (.004, 400)]))
        before = ET.tostring(track.element)
        controller = self.controller(track)
        changed = GPXEditorController.interpolate_track_heights(controller, track, [(0, 2)])
        self.assertEqual(changed, 1)
        self.assertEqual(track.points()[1].ele, 175)
        self.assertEqual(len(controller.undo_stack), 1)
        self.assertEqual(controller.undo_stack[0].kind, MutationKind.ELEVATION)
        controller.mark_dirty.assert_called_once_with(
            "Interpolated height for 1 point in track #26.", MutationKind.ELEVATION, [26],
        )
        after = ET.tostring(track.element)
        invoke(GPXEditorController.undo_, controller, None)
        self.assertEqual(ET.tostring(track.element), before)
        invoke(GPXEditorController.redo_, controller, None)
        self.assertEqual(ET.tostring(track.element), after)
        self.assertEqual(track.points()[0].time, track.points()[1].time)

    def test_controller_noop_or_invalid_endpoints_make_no_undo_or_dirty_change(self):
        for elevations in ([0, 100, 200], [None, 999, 200]):
            track = TrackRecord(1, make_track(list(zip([0, .001, .002], elevations))))
            controller = self.controller(track)
            with patch("GPXEditor.show_alert"):
                self.assertEqual(GPXEditorController.interpolate_track_heights(controller, track, [(0, 2)]), 0)
            controller.mark_dirty.assert_not_called()
            self.assertEqual(controller.undo_stack, [])

    def test_inspector_undo_history_and_selection_are_reused(self):
        track = TrackRecord(1, make_track([(0, 0), (.001, 999), (.002, 200)]))
        controller = self.controller(track)
        inspector = SimpleNamespace(
            selected_row_indexes=lambda: [0, 1, 2], push_undo=Mock(),
            reload_rows=Mock(), select_point_rows=Mock(),
        )
        controller.existing_inspector_for_track.return_value = inspector
        GPXEditorController.interpolate_track_heights(controller, track, [(0, 2)])
        inspector.push_undo.assert_called_once()
        inspector.select_point_rows.assert_called_once_with([0, 1, 2])
        self.assertEqual(controller.undo_stack, [])
        controller.refresh_open_plot_views.assert_not_called()

    def test_shift_i_distinguishes_info_and_command_i(self):
        self.assertTrue(height_interpolation_key("I", 0))
        self.assertTrue(height_interpolation_key("i", NSEventModifierFlagShift))
        self.assertFalse(height_interpolation_key("i", 0))
        self.assertFalse(height_interpolation_key("I", NSEventModifierFlagCommand | NSEventModifierFlagShift))
        self.assertFalse(height_interpolation_key("I", NSEventModifierFlagOption))

    def test_profile_uppercase_i_interpolates_but_lowercase_i_keeps_info_toggle(self):
        plot = SimpleNamespace(
            interpolate_selected_heights=Mock(), show_info=False,
            setNeedsDisplay_=Mock(),
        )
        profile = SimpleNamespace(plot_view=plot, controller=SimpleNamespace(set_status=Mock()), setNeedsDisplay_=Mock())
        invoke(ElevationProfileView.keyDown_, profile, event("i", NSEventModifierFlagShift))
        plot.interpolate_selected_heights.assert_called_once()
        self.assertFalse(plot.show_info)
        invoke(ElevationProfileView.keyDown_, profile, event("i"))
        self.assertTrue(plot.show_info)

    def test_profile_undo_uses_inspector_when_it_owns_the_edit(self):
        inspector = SimpleNamespace(undo_stack=["original"], redo_stack=[], undo=Mock())
        profile = SimpleNamespace(plot_view=SimpleNamespace(inspector=inspector), controller=Mock())
        invoke(ElevationProfileView.keyDown_, profile, event("z", NSEventModifierFlagCommand))
        inspector.undo.assert_called_once()
        profile.controller.undo_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
