import unittest
import xml.etree.ElementTree as ET
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from GPXEditor import (
    GPXEditorController,
    PlotView,
    TRACK_SORT_CRITERIA,
    TrackRecord,
    TrackInspectorController,
    compact_elevation_status,
    compact_xy_status,
    context_track_row_selection,
    duplicate_track_records,
    elevation_distance_range_for_map_extent,
    elevation_profile_visible_range,
    format_inspector_elevation,
    format_inspector_timestamp,
    inspector_table_document_size,
    lonlat_to_web_mercator,
    normalize_inspector_timestamp_edit,
    qname,
    reordered_selected_items,
    unique_track_copy_name,
    visible_simplified_polyline_runs,
)


class InspectorPointSelectionTests(unittest.TestCase):
    def make_controller(self, rows, *, suppressed=False, marker=4):
        track = object()
        plot = SimpleNamespace(
            marker=marker,
            move_cursor_to_track_point=Mock(),
            setNeedsDisplay_=Mock(),
        )
        parent = SimpleNamespace(refresh_elevation_profile_for_plot_view=Mock())
        controller = SimpleNamespace(
            suppress_selection_change=suppressed,
            update_info_label=Mock(),
            selected_row_indexes=Mock(return_value=list(rows)),
            plot_view=plot,
            track=track,
            parent=parent,
        )
        return controller, plot, parent, track

    def test_manual_table_selection_replaces_marker_without_stealing_focus(self):
        controller, plot, _parent, track = self.make_controller([7, 8, 9])

        TrackInspectorController.selection_changed(controller)

        self.assertIsNone(plot.marker)
        plot.move_cursor_to_track_point.assert_called_once_with(
            track,
            9,
            controller,
            sync_table=False,
            focus_plot=False,
        )

    def test_empty_manual_selection_clears_marker_and_redraws_both_plots(self):
        controller, plot, parent, _track = self.make_controller([])

        TrackInspectorController.selection_changed(controller)

        self.assertIsNone(plot.marker)
        plot.move_cursor_to_track_point.assert_not_called()
        plot.setNeedsDisplay_.assert_called_once_with(True)
        parent.refresh_elevation_profile_for_plot_view.assert_called_once_with(plot)

    def test_programmatic_map_selection_keeps_marker(self):
        controller, plot, parent, _track = self.make_controller(
            [2, 3, 4],
            suppressed=True,
            marker=2,
        )

        TrackInspectorController.selection_changed(controller)

        self.assertEqual(plot.marker, 2)
        controller.update_info_label.assert_not_called()
        plot.move_cursor_to_track_point.assert_not_called()
        parent.refresh_elevation_profile_for_plot_view.assert_not_called()

    def test_disjoint_table_selection_produces_separate_plot_ranges(self):
        track = object()
        inspector = SimpleNamespace(
            track=track,
            selected_row_indexes=lambda: [2, 3, 7, 9, 10],
        )
        plot = SimpleNamespace(
            marker=None,
            cursor=None,
            inspector=inspector,
            group_point_indexes=lambda indexes: PlotView.group_point_indexes(None, indexes),
        )

        self.assertEqual(
            PlotView.selected_ranges_for_track(plot, track),
            [(2, 3), (7, 7), (9, 10)],
        )


class TrackContextSelectionTests(unittest.TestCase):
    def test_right_click_preserves_existing_multi_selection(self):
        self.assertEqual(context_track_row_selection(3, [1, 3, 5], 7), [1, 3, 5])

    def test_right_click_on_other_track_replaces_selection(self):
        self.assertEqual(context_track_row_selection(2, [1, 3, 5], 7), [2])

    def test_empty_area_and_summary_row_have_no_track_context(self):
        self.assertEqual(context_track_row_selection(-1, [1], 4), [])
        self.assertEqual(context_track_row_selection(4, [1], 4), [])


class TrackBatchOrderTests(unittest.TestCase):
    def test_move_disjoint_groups_up_and_down_one_position(self):
        items = list("ABCDE")
        self.assertEqual(
            reordered_selected_items(items, [1, 3], "up"),
            list("BADCE"),
        )
        self.assertEqual(
            reordered_selected_items(items, [1, 3], "down"),
            list("ACBED"),
        )

    def test_move_selection_to_top_and_bottom_preserves_relative_order(self):
        items = list("ABCDE")
        self.assertEqual(
            reordered_selected_items(items, [1, 3], "top"),
            list("BDACE"),
        )
        self.assertEqual(
            reordered_selected_items(items, [1, 3], "bottom"),
            list("ACEBD"),
        )

    def test_copy_names_are_unique_case_insensitively(self):
        self.assertEqual(unique_track_copy_name("Stage", []), "Stage (copy)")
        self.assertEqual(
            unique_track_copy_name("Stage", ["stage (COPY)", "Stage (copy 2)"]),
            "Stage (copy 3)",
        )

    @staticmethod
    def make_track(number, name, source):
        element = ET.Element(qname("trk"))
        ET.SubElement(element, qname("name")).text = name
        segment = ET.SubElement(element, qname("trkseg"))
        ET.SubElement(segment, qname("trkpt"), {"lat": "50", "lon": "7"})
        return TrackRecord(number, element, source)

    def test_duplicate_tracks_form_one_block_after_last_selected_track(self):
        tracks = [
            self.make_track(1, "A", "one.gpx"),
            self.make_track(2, "B", "two.gpx"),
            self.make_track(3, "C", "three.gpx"),
            self.make_track(4, "D", "four.gpx"),
        ]
        updated, duplicates, next_nr = duplicate_track_records(tracks, [0, 2], 5)

        self.assertEqual(
            [track.name for track in updated],
            ["A", "B", "C", "A (copy)", "C (copy)", "D"],
        )
        self.assertEqual([track.nr for track in duplicates], [5, 6])
        self.assertEqual(next_nr, 7)
        self.assertEqual(updated[3].source_file, "one.gpx")
        self.assertIsNot(updated[3].element, updated[0].element)
        updated[3].element.find("gpx:name", {"gpx": "http://www.topografix.com/GPX/1/1"}).text = "Changed"
        self.assertEqual(updated[0].name, "A")

    def test_batch_visibility_changes_only_tracks_that_need_it(self):
        visible = self.make_track(1, "Visible", "one.gpx")
        hidden = self.make_track(2, "Hidden", "two.gpx")
        hidden.set_hidden(True)
        controller = SimpleNamespace(
            selected_tracks=lambda: [visible, hidden],
            push_undo=Mock(),
            mark_dirty=Mock(),
            refresh_open_plot_views=Mock(),
        )

        GPXEditorController._set_selected_tracks_hidden(controller, True)

        self.assertTrue(visible.hidden)
        self.assertTrue(hidden.hidden)
        controller.push_undo.assert_called_once_with()
        controller.mark_dirty.assert_called_once_with("Hidden 1 selected track(s).")

    def test_selected_moving_speed_sort_preserves_unselected_positions(self):
        tracks = [
            self.make_track(1, "A", "one.gpx"),
            self.make_track(2, "B", "two.gpx"),
            self.make_track(3, "C", "three.gpx"),
            self.make_track(4, "D", "four.gpx"),
        ]
        speeds = {1: 5.0, 2: 9.0, 3: 3.0, 4: 7.0}

        def metrics(track):
            return {
                "time": None,
                "length_km": float(track.nr),
                "duration": timedelta(hours=1),
                "distance_km": float(track.nr),
                "speed_kmh": speeds[track.nr],
                "moving_speed_kmh": speeds[track.nr],
                "ascent_m": float(track.nr),
                "descent_m": float(track.nr),
                "npoints": track.nr,
            }

        controller = SimpleNamespace(
            tracks=tracks,
            columns=[(identifier, title, 80, False) for identifier, title in TRACK_SORT_CRITERIA],
            sort_column=None,
            sort_ascending=True,
            selected_nrs=[1, 3],
            push_undo=Mock(),
            set_status=Mock(),
            compute_metrics=metrics,
            selected_track_row_indexes=lambda: [0, 2],
            recalculate=Mock(),
            highlight_selected_rows=Mock(),
            update_sort_descriptor=Mock(),
            refresh_open_plot_views=Mock(),
            update_selection_field=Mock(),
        )

        GPXEditorController.sort_by_column(
            controller,
            "moving_speed",
            ascending=True,
            source="test",
        )

        self.assertEqual([track.nr for track in controller.tracks], [3, 2, 1, 4])
        self.assertEqual(controller.selected_nrs, [3, 1])
        controller.refresh_open_plot_views.assert_called_once_with()

    def test_sort_dialog_criteria_exclude_order_dependent_columns(self):
        identifiers = [identifier for identifier, _title in TRACK_SORT_CRITERIA]
        self.assertIn("moving_speed", identifiers)
        self.assertNotIn("row", identifiers)
        self.assertNotIn("sum", identifiers)

    def test_snapshot_restores_duplicate_source_file(self):
        track = self.make_track(8, "Copy", "source.gpx")
        controller = SimpleNamespace(tracks=[track], next_nr=9, dirty=False, recalculate=Mock())
        snapshot = GPXEditorController.snapshot(controller)
        controller.tracks = []

        GPXEditorController.restore_snapshot(controller, snapshot)

        self.assertEqual(controller.tracks[0].source_file, "source.gpx")


class InspectorTableDocumentSizeTests(unittest.TestCase):
    def test_document_fills_larger_viewport(self):
        self.assertEqual(
            inspector_table_document_size(800, 500, [100, 120], 3, 22, 3, 2),
            (800, 500),
        )

    def test_document_preserves_full_scrollable_content(self):
        width, height = inspector_table_document_size(300, 100, [100, 120, 180], 10, 22, 3, 2)
        self.assertEqual(width, 406)
        self.assertEqual(height, 238)


class ElevationProfileVisibleRangeTests(unittest.TestCase):
    def test_uses_only_points_in_visible_distance_range(self):
        rows = [
            {"distance": 0.0, "elevation": 900.0},
            {"distance": 5.0, "elevation": 100.0},
            {"distance": 6.0, "elevation": 200.0},
            {"distance": 10.0, "elevation": 1200.0},
        ]
        self.assertEqual(elevation_profile_visible_range(rows, 4.0, 7.0), (95.0, 205.0))

    def test_flat_visible_profile_receives_headroom(self):
        rows = [{"distance": 2.0, "elevation": 500.0}]
        self.assertEqual(elevation_profile_visible_range(rows, 0.0, 3.0), (475.0, 525.0))

    def test_returns_none_without_visible_elevation(self):
        rows = [{"distance": 2.0, "elevation": None}, {"distance": 5.0, "elevation": 100.0}]
        self.assertIsNone(elevation_profile_visible_range(rows, 0.0, 3.0))

    def test_map_extent_selects_matching_profile_distance_range(self):
        rows = [
            {"distance": 0.0, "latitude": 50.0, "longitude": 7.0},
            {"distance": 5.0, "latitude": 50.1, "longitude": 7.1},
            {"distance": 10.0, "latitude": 51.0, "longitude": 8.0},
        ]
        center_x, center_y = lonlat_to_web_mercator(7.05, 50.05)
        extent = {
            "min_x": center_x - 10_000.0,
            "max_x": center_x + 10_000.0,
            "min_y": center_y - 10_000.0,
            "max_y": center_y + 10_000.0,
        }
        self.assertEqual(
            elevation_distance_range_for_map_extent(rows, extent),
            (0.0, 5.0),
        )


class InspectorValueFormattingTests(unittest.TestCase):
    def test_elevation_uses_one_decimal_without_changing_invalid_text(self):
        self.assertEqual(format_inspector_elevation("451.276"), "451.3")
        self.assertEqual(format_inspector_elevation("N/A"), "N/A")

    def test_timestamp_is_human_readable_and_preserves_zone_marker(self):
        self.assertEqual(
            format_inspector_timestamp("2024-07-15T12:34:56.789Z"),
            "15.07.2024 12:34:56.8Z",
        )
        self.assertEqual(
            format_inspector_timestamp("2024-07-15T12:34:56.789+02:00"),
            "15.07.2024 12:34:56.8+02:00",
        )

    def test_timestamp_rounding_carries_into_the_next_minute(self):
        self.assertEqual(
            format_inspector_timestamp("2024-07-15T12:34:59.960Z"),
            "15.07.2024 12:35:00.0Z",
        )

    def test_edited_human_timestamp_returns_to_iso_gpx_form(self):
        self.assertEqual(
            normalize_inspector_timestamp_edit("15.07.2024 12:34:56.8+02:00"),
            "2024-07-15T12:34:56.8+02:00",
        )

    def test_compact_statuses_preserve_processing_distinctions(self):
        self.assertEqual(compact_xy_status("retained"), "Used")
        self.assertEqual(compact_xy_status("smoothing only"), "Smooth")
        self.assertEqual(compact_xy_status("HDOP"), "HDOP")
        self.assertEqual(compact_elevation_status("interpolated (VDOP)"), "Interp VDOP")
        self.assertEqual(compact_elevation_status("not retained"), "Not used")


class InitialLoadCallbackTests(unittest.TestCase):
    def test_initial_load_callback_fires_only_once(self):
        calls = []
        controller = SimpleNamespace(
            initial_load_completion_notified=False,
            on_initial_load_complete_callback=lambda: calls.append("complete"),
        )

        GPXEditorController.notify_initial_load_complete(controller)
        GPXEditorController.notify_initial_load_complete(controller)

        self.assertEqual(calls, ["complete"])
        self.assertTrue(controller.initial_load_completion_notified)
        self.assertIsNone(controller.on_initial_load_complete_callback)


class PlotPolylinePreparationTests(unittest.TestCase):
    def test_clips_to_visible_extent_and_removes_subpixel_points(self):
        points = [
            (-20.0, 50.0),
            (0.0, 50.0),
            (0.02, 50.01),
            (0.04, 50.02),
            (50.0, 50.0),
            (120.0, 50.0),
        ]
        runs = visible_simplified_polyline_runs(
            points,
            {"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0},
            (100.0, 100.0),
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][0], (0.0, 50.0))
        self.assertEqual(runs[0][-1], (100.0, 50.0))
        self.assertLess(len(runs[0]), len(points))

    def test_omits_polyline_that_is_entirely_outside_visible_extent(self):
        self.assertEqual(
            visible_simplified_polyline_runs(
                [(-20.0, -20.0), (-10.0, -10.0)],
                {"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0},
                (100.0, 100.0),
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
