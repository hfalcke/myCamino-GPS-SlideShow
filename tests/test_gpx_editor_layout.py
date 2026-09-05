import unittest
import xml.etree.ElementTree as ET
import tempfile
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from interactive_tile_viewport import visible_tiles, zoomed_extent

from GPXEditor import (
    GPXEditorController,
    PlotView,
    TRACK_SORT_CRITERIA,
    TrackRecord,
    TrackInspectorController,
    command_modifier_requests_media_selection,
    apply_untimed_prompt_response,
    compact_elevation_status,
    compact_xy_status,
    context_track_row_selection,
    duplicate_track_records,
    elevation_distance_range_for_map_extent,
    elevation_rows_for_selected_tracks,
    elevation_profile_visible_range,
    format_inspector_elevation,
    format_inspector_timestamp,
    inspector_table_document_size,
    lonlat_to_web_mercator,
    normalize_inspector_timestamp_edit,
    point_table_segment_break_rows,
    qname,
    remember_untimed_track_identities,
    renumber_track_records,
    reordered_selected_items,
    sampled_polyline_points,
    suppressed_untimed_track_identities,
    untimed_tracks_requiring_prompt,
    unique_track_copy_name,
    visible_track_count,
    visible_simplified_polyline_runs,
    write_selected_tracks_gpx,
    stored_track_order_number,
)


class FakeDefaults:
    def __init__(self):
        self.values = {}
        self.synchronized = False

    def arrayForKey_(self, key):
        return self.values.get(key)

    def setObject_forKey_(self, value, key):
        self.values[key] = list(value)

    def synchronize(self):
        self.synchronized = True


class InspectorPointSelectionTests(unittest.TestCase):
    def test_selected_track_export_is_atomic_renumbered_and_preserves_segments(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            media = directory / "source" / "photo one.jpg"
            media.parent.mkdir()
            media.write_bytes(b"photo")
            tracks = []
            for number, name in ((7, "First"), (12, "Second")):
                element = ET.Element(qname("trk"))
                ET.SubElement(element, qname("name")).text = name
                for offset in (0, 1):
                    segment = ET.SubElement(element, qname("trkseg"))
                    point = ET.SubElement(
                        segment,
                        qname("trkpt"),
                        {"lat": str(50 + offset), "lon": str(7 + offset)},
                    )
                    link = ET.SubElement(point, qname("link"), {"href": str(media)})
                    ET.SubElement(link, qname("text")).text = media.name
                track = TrackRecord(number, element, str(directory / "source.gpx"))
                track.set_order_number(number)
                tracks.append(track)
            destination = directory / "selected.gpx"

            count = write_selected_tracks_gpx(
                tracks,
                destination,
                project_name="Selection",
            )

            self.assertEqual(count, 2)
            root = ET.parse(destination).getroot()
            exported = root.findall("gpx:trk", {"gpx": "http://www.topografix.com/GPX/1/1"})
            self.assertEqual([stored_track_order_number(track) for track in exported], [1, 2])
            self.assertEqual([len(track.findall("gpx:trkseg", {"gpx": "http://www.topografix.com/GPX/1/1"})) for track in exported], [2, 2])
            href = exported[0].find(".//gpx:link", {"gpx": "http://www.topografix.com/GPX/1/1"}).get("href")
            self.assertFalse(Path(href).is_absolute())
            self.assertEqual([track.nr for track in tracks], [7, 12])
            self.assertEqual([track.stored_order_number for track in tracks], [7, 12])
            self.assertEqual(
                tracks[0].element.find(".//gpx:link", {"gpx": "http://www.topografix.com/GPX/1/1"}).get("href"),
                str(media),
            )

    def test_media_rubber_band_requires_command_modifier(self):
        self.assertFalse(command_modifier_requests_media_selection(0))
        self.assertTrue(command_modifier_requests_media_selection(1 << 20))

    def test_workspace_requires_track_selection_before_point_editing(self):
        track = SimpleNamespace(nr=7)
        selected = []
        controller = SimpleNamespace(
            select_track_in_table=Mock(side_effect=lambda nr: selected.append(nr)),
            selected_tracks=Mock(side_effect=lambda: [track] if selected else []),
        )
        workspace = SimpleNamespace(workspaceTrackSelectedForEditing_=Mock())
        target = ("point", track, 3, None)
        plot = SimpleNamespace(
            adventure_workspace_delegate=workspace,
            mode="overview",
            controller=controller,
            cursor=(1, object(), object()),
            workspace_track_selection_consumed=False,
            waypoint_editing_enabled=lambda: bool(selected),
            nearest_edit_target=Mock(side_effect=lambda _location: target if selected else None),
            nearest_track_for_location=Mock(return_value=(4.0, track, 3)),
        )

        first_result = PlotView.edit_target_for_mouse_down(plot, object())

        self.assertIsNone(first_result)
        self.assertIsNone(plot.cursor)
        self.assertTrue(plot.workspace_track_selection_consumed)
        controller.select_track_in_table.assert_called_once_with(7)
        workspace.workspaceTrackSelectedForEditing_.assert_called_once_with(track)

        plot.workspace_track_selection_consumed = False
        second_result = PlotView.edit_target_for_mouse_down(plot, object())
        self.assertEqual(second_result, target)
        controller.select_track_in_table.assert_called_once_with(7)

    def test_long_press_timer_runs_in_common_tracking_modes(self):
        timer = Mock()
        run_loop = Mock()
        plot = SimpleNamespace(long_press_timer=None)
        with (
            patch("GPXEditor.NSTimer") as timer_class,
            patch("GPXEditor.NSRunLoop") as run_loop_class,
        ):
            timer_class.timerWithTimeInterval_target_selector_userInfo_repeats_.return_value = timer
            run_loop_class.currentRunLoop.return_value = run_loop

            PlotView.schedule_point_editing_long_press(plot)

        run_loop.addTimer_forMode_.assert_called_once()
        self.assertIs(plot.long_press_timer, timer)

    def test_drag_ready_feedback_matches_active_cursor_point(self):
        track = object()
        plot = SimpleNamespace(
            cursor=(4, object(), track),
            dragged_point_track=track,
            dragged_point_index=4,
        )
        self.assertTrue(PlotView.cursor_is_ready_for_drag(plot))

        plot.dragged_point_index = 5
        self.assertFalse(PlotView.cursor_is_ready_for_drag(plot))

    def test_map_context_can_open_track_table_without_changing_viewport(self):
        track = SimpleNamespace(nr=7)
        controller = SimpleNamespace(select_track_in_table=Mock(), show=Mock())
        workspace = SimpleNamespace(openTrackEditor_=Mock())
        plot = PlotView.alloc().init()
        plot.context_track_target = (track, 12)
        plot.controller = controller
        plot.adventure_workspace_delegate = workspace

        PlotView.showContextTrackTable_(plot, None)

        controller.select_track_in_table.assert_called_once_with(7)
        workspace.openTrackEditor_.assert_called_once_with(None)
        controller.show.assert_not_called()

    def test_map_context_opens_profile_for_the_same_plot_view(self):
        controller = SimpleNamespace(open_elevation_profile_for_plot_view=Mock())
        plot = PlotView.alloc().init()
        plot.controller = controller

        PlotView.showElevationProfile_(plot, None)

        controller.open_elevation_profile_for_plot_view.assert_called_once_with(plot)

    def test_track_table_hides_instead_of_closing_map_first_workspace(self):
        table_window = Mock()
        map_window = Mock()
        map_window.isVisible.return_value = True
        controller = SimpleNamespace(
            adventure_workspace_delegate=SimpleNamespace(window=map_window),
            window=table_window,
        )

        hidden = GPXEditorController.hide_main_editor_for_workspace(controller)

        self.assertTrue(hidden)
        table_window.orderOut_.assert_called_once_with(None)

    def test_selected_track_is_editable_in_adventure_overview(self):
        controller = SimpleNamespace(selected_tracks=Mock(return_value=[object()]))
        plot = SimpleNamespace(
            mode="overview",
            adventure_workspace_delegate=object(),
            controller=controller,
        )
        self.assertTrue(PlotView.waypoint_editing_enabled(plot))

    def test_unselected_adventure_overview_is_not_waypoint_editable(self):
        controller = SimpleNamespace(selected_tracks=Mock(return_value=[]))
        plot = SimpleNamespace(
            mode="overview",
            adventure_workspace_delegate=object(),
            controller=controller,
        )
        self.assertFalse(PlotView.waypoint_editing_enabled(plot))

    def test_adventure_overview_targets_the_single_selected_track(self):
        selected_track = object()
        controller = SimpleNamespace(selected_tracks=Mock(return_value=[selected_track]))
        plot = SimpleNamespace(
            mode="overview",
            adventure_workspace_delegate=object(),
            controller=controller,
        )
        self.assertIs(PlotView.current_track_for_title(plot), selected_track)

    def test_segment_break_rows_follow_segment_indexes(self):
        rows = [
            {"segment_index": 0},
            {"segment_index": 0},
            {"segment_index": 1},
            {"segment_index": 1},
            {"segment_index": 2},
        ]
        self.assertEqual(point_table_segment_break_rows(rows), {2, 4})

    def test_closing_inspector_only_closes_map_it_created(self):
        for owns_map in (False, True):
            parent = SimpleNamespace(
                unregister_auxiliary_window=Mock(),
                close_plot_windows_for_inspector=Mock(),
            )
            plot = SimpleNamespace(inspector=None)
            inspector = TrackInspectorController.alloc().init()
            inspector.parent = parent
            inspector.owns_plot_window = owns_map
            inspector.plot_view = plot
            notification = SimpleNamespace(object=Mock(return_value=object()))

            inspector.windowWillClose_(notification)

            if owns_map:
                parent.close_plot_windows_for_inspector.assert_called_once_with(inspector)
            else:
                parent.close_plot_windows_for_inspector.assert_not_called()

    def test_track_table_double_click_opens_waypoint_table(self):
        track = SimpleNamespace(nr=7)
        controller = GPXEditorController.alloc().init()
        controller.track_table = SimpleNamespace(clickedRow=Mock(return_value=0), selectedRow=Mock(return_value=0))
        controller.tracks = [track]
        controller.selected_nrs = []
        controller.update_selection_field = Mock()
        controller.highlight_selected_rows = Mock()
        controller.open_inspector_for_track = Mock(return_value=Mock())
        controller.set_status = Mock()

        controller.trackDoubleClicked_(None)

        self.assertEqual(controller.selected_nrs, [7])
        controller.open_inspector_for_track.assert_called_once_with(track)

    def test_plot_selected_reuses_adventure_workspace_map(self):
        track = SimpleNamespace(nr=7)
        workspace = SimpleNamespace(plot_view=object(), focus_workspace_tracks=Mock())
        controller = GPXEditorController.alloc().init()
        controller.tracks = [track]
        controller.selected_nrs = [7]
        controller.adventure_workspace_delegate = workspace
        controller.open_plot_window = Mock()

        controller.plotSelected_(None)

        workspace.focus_workspace_tracks.assert_called_once_with(
            [track],
            mode="track",
            reset_viewport=True,
        )
        controller.open_plot_window.assert_not_called()

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

    def test_marked_range_hit_accepts_points_and_internal_connections(self):
        track = object()
        plot = SimpleNamespace(
            selected_ranges_for_track=lambda candidate: [(3, 7)] if candidate is track else [],
        )
        self.assertEqual(
            PlotView.selected_range_at_target(plot, ("point", track, 5, None)),
            (track, 3, 7),
        )
        self.assertEqual(
            PlotView.selected_range_at_target(plot, ("segment", track, 6, 0.5)),
            (track, 3, 7),
        )
        self.assertIsNone(
            PlotView.selected_range_at_target(plot, ("segment", track, 7, 0.5))
        )

    def test_inspector_undo_and_redo_restore_track_local_edits(self):
        original = ET.Element(qname("trk"))
        segment = ET.SubElement(original, qname("trkseg"))
        ET.SubElement(segment, qname("trkpt"), {"lat": "50", "lon": "7"})
        track = TrackRecord(1, ET.fromstring(ET.tostring(original)), "track.gpx")
        track.points()[0].element.set("lat", "51")
        parent = SimpleNamespace(
            tracks=[track],
            invalidate_track_metrics=Mock(),
            mark_dirty=Mock(),
        )
        inspector = TrackInspectorController.alloc().init()
        inspector.undo_stack = [ET.tostring(original, encoding="unicode")]
        inspector.redo_stack = []
        inspector.track = track
        inspector.parent = parent
        inspector.reload_rows = Mock()
        inspector.refresh_after_point_table_change = Mock()

        TrackInspectorController.undo(inspector)
        self.assertEqual(track.points()[0].lat, 50.0)
        self.assertEqual(len(inspector.redo_stack), 1)

        TrackInspectorController.redo(inspector)
        self.assertEqual(track.points()[0].lat, 51.0)
        self.assertEqual(len(inspector.undo_stack), 1)

    def test_point_table_edit_refreshes_existing_map_without_rebuilding_viewport(self):
        track_element = ET.Element(qname("trk"))
        segment = ET.SubElement(track_element, qname("trkseg"))
        ET.SubElement(segment, qname("trkpt"), {"lat": "50", "lon": "7"})
        ET.SubElement(segment, qname("trkpt"), {"lat": "50.1", "lon": "7.1"})
        track = TrackRecord(1, track_element, "track.gpx")
        plot = SimpleNamespace(refresh_after_map_edit=Mock(), setNeedsDisplay_=Mock(), cursor=None)
        parent = SimpleNamespace(
            selected_nrs=[],
            update_selection_field=Mock(),
            highlight_selected_rows=Mock(),
            refresh_track_plot_for_track=Mock(),
            refresh_elevation_profile_for_plot_view=Mock(),
            redraw_open_plot_views=Mock(),
        )
        inspector = SimpleNamespace(
            parent=parent,
            track=track,
            plot_view=plot,
            selected_row_indexes=Mock(return_value=[1]),
        )

        TrackInspectorController.refresh_after_point_table_change(inspector, [1])

        plot.refresh_after_map_edit.assert_called_once_with(track, 1)
        parent.refresh_track_plot_for_track.assert_not_called()

    def test_cursor_title_uses_coordinates_and_optional_height(self):
        point = SimpleNamespace(lat=50.1234567, lon=7.7654321, ele=123.45)
        self.assertEqual(
            PlotView.cursor_title_text(point),
            "50.123457, 7.765432   123.5 m",
        )
        point.ele = None
        self.assertEqual(PlotView.cursor_title_text(point), "50.123457, 7.765432")

    def test_map_header_uses_pointer_position_and_interpolated_height(self):
        plot = SimpleNamespace(
            pointer_coordinate=(7.7654321, 50.1234567),
            pointer_elevation=123.45,
        )
        self.assertEqual(
            PlotView.map_pointer_title_text(plot),
            "50.123457, 7.765432   123.5 m",
        )
        plot.pointer_elevation = None
        self.assertEqual(
            PlotView.map_pointer_title_text(plot),
            "50.123457, 7.765432",
        )

    def test_dismissing_point_information_latches_overlay_closed(self):
        plot = SimpleNamespace(
            show_info=True,
            info_overlay_dismissed=False,
            context_overlay_coordinate=(7.0, 50.0),
            info_overlay_close_rect=object(),
            setNeedsDisplay_=Mock(),
        )

        PlotView.dismiss_info_overlay(plot)

        self.assertFalse(plot.show_info)
        self.assertTrue(plot.info_overlay_dismissed)
        self.assertIsNone(plot.context_overlay_coordinate)
        self.assertIsNone(plot.info_overlay_close_rect)


class UntimedTrackPromptTests(unittest.TestCase):
    @staticmethod
    def make_track(name="Untimed", *, timed=False, latitude="50"):
        element = ET.Element(qname("trk"))
        ET.SubElement(element, qname("name")).text = name
        segment = ET.SubElement(element, qname("trkseg"))
        first = ET.SubElement(segment, qname("trkpt"), {"lat": latitude, "lon": "7"})
        second = ET.SubElement(segment, qname("trkpt"), {"lat": "50.1", "lon": "7.1"})
        if timed:
            ET.SubElement(first, qname("time")).text = "2026-01-01T10:00:00Z"
            ET.SubElement(second, qname("time")).text = "2026-01-01T10:10:00Z"
        return TrackRecord(1, element, "track.gpx")

    def test_no_decision_is_remembered_without_changing_gpx(self):
        defaults = FakeDefaults()
        track = self.make_track()
        before = ET.tostring(track.element)
        pending = untimed_tracks_requiring_prompt([track], set())

        remember_untimed_track_identities(defaults, [pending[0][2]])

        self.assertEqual(ET.tostring(track.element), before)
        suppressed = suppressed_untimed_track_identities(defaults)
        self.assertEqual(untimed_tracks_requiring_prompt([track], suppressed), [])
        self.assertTrue(defaults.synchronized)

    def test_name_changes_do_not_reactivate_suppressed_prompt(self):
        track = self.make_track("Original")
        identity = untimed_tracks_requiring_prompt([track], set())[0][2]
        track.element.find("gpx:name", {"gpx": "http://www.topografix.com/GPX/1/1"}).text = "Renamed"

        self.assertEqual(untimed_tracks_requiring_prompt([track], {identity}), [])

    def test_geometry_changes_reactivate_prompt(self):
        track = self.make_track()
        identity = untimed_tracks_requiring_prompt([track], set())[0][2]
        track.points()[0].element.set("lat", "51")

        self.assertEqual(len(untimed_tracks_requiring_prompt([track], {identity})), 1)

    def test_timed_tracks_are_never_prompted(self):
        self.assertEqual(
            untimed_tracks_requiring_prompt([self.make_track(timed=True)], set()),
            [],
        )

    def test_yes_edits_first_track_without_storing_suppression(self):
        defaults = FakeDefaults()
        pending = untimed_tracks_requiring_prompt(
            [self.make_track("First"), self.make_track("Second", latitude="49")],
            set(),
        )

        self.assertEqual(apply_untimed_prompt_response(1000, pending, defaults), ("edit", 0))
        self.assertEqual(suppressed_untimed_track_identities(defaults), set())

    def test_no_suppresses_every_listed_track(self):
        defaults = FakeDefaults()
        pending = untimed_tracks_requiring_prompt(
            [self.make_track("First"), self.make_track("Second", latitude="49")],
            set(),
        )

        self.assertEqual(apply_untimed_prompt_response(1001, pending, defaults), ("suppress", None))
        self.assertEqual(
            suppressed_untimed_track_identities(defaults),
            {pending[0][2], pending[1][2]},
        )

    def test_later_records_nothing(self):
        defaults = FakeDefaults()
        pending = untimed_tracks_requiring_prompt([self.make_track()], set())

        self.assertEqual(apply_untimed_prompt_response(1002, pending, defaults), ("later", None))
        self.assertEqual(suppressed_untimed_track_identities(defaults), set())


class InteractiveTileLimitTests(unittest.TestCase):
    @staticmethod
    def controller():
        controller = SimpleNamespace(maximum_map_zoom=19)
        controller.tile_diagnostics = lambda extent, zoom: GPXEditorController.tile_diagnostics(
            controller, extent, zoom
        )
        return controller

    def test_hanisch_sized_viewport_is_reduced_below_tile_limit(self):
        extent = {"min_x": 0.0, "max_x": 48600.0, "min_y": 0.0, "max_y": 35000.0}
        controller = self.controller()

        zoom, diagnostics = GPXEditorController.effective_tile_zoom_for_limit(
            controller, extent, 14, 48
        )

        self.assertEqual(zoom, 12)
        self.assertLessEqual(diagnostics["count"], 48)
        self.assertLessEqual(len(visible_tiles(extent, zoom)), 48)

    def test_repeated_factor_two_zoom_keeps_each_request_bounded(self):
        extent = {"min_x": 0.0, "max_x": 48600.0, "min_y": 0.0, "max_y": 35000.0}
        controller = self.controller()
        zoom, _diagnostics = GPXEditorController.effective_tile_zoom_for_limit(
            controller, extent, 14, 48
        )

        for _step in range(5):
            extent = zoomed_extent(extent, 2.0)
            zoom, diagnostics = GPXEditorController.effective_tile_zoom_for_limit(
                controller, extent, zoom + 1, 48
            )
            self.assertLessEqual(diagnostics["count"], 48)


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
        self.assertEqual(duplicates[0].stored_order_number, 5)
        self.assertEqual(duplicates[1].stored_order_number, 6)
        updated[3].element.find("gpx:name", {"gpx": "http://www.topografix.com/GPX/1/1"}).text = "Changed"
        self.assertEqual(updated[0].name, "A")

    def test_renumber_tracks_uses_current_rows(self):
        tracks = [
            self.make_track(8, "C", "one.gpx"),
            self.make_track(3, "A", "one.gpx"),
            self.make_track(12, "B", "one.gpx"),
        ]
        next_nr = renumber_track_records(tracks)

        self.assertEqual([track.nr for track in tracks], [1, 2, 3])
        self.assertEqual([track.stored_order_number for track in tracks], [1, 2, 3])
        self.assertEqual(next_nr, 4)

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

    def test_visible_track_count_excludes_hidden_rows(self):
        tracks = [
            self.make_track(1, "Visible", "one.gpx"),
            self.make_track(2, "Hidden", "one.gpx"),
            self.make_track(3, "Also visible", "one.gpx"),
        ]
        tracks[1].set_hidden(True)
        self.assertEqual(visible_track_count(tracks), 2)
        tracks[0].set_hidden(True)
        tracks[2].set_hidden(True)
        self.assertEqual(visible_track_count(tracks), 0)

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

    def test_dropped_distance_sort_sets_anchor_from_first_dropped_track(self):
        first = self.make_track(4, "First", "first.gpx")
        second = self.make_track(5, "Second", "second.gpx")
        controller = SimpleNamespace(
            tracks=[first, second],
            anchor=(1.0, 2.0),  # The automatic table calculation may have set this.
            selected_nrs=[],
            update_selection_field=Mock(),
            highlight_selected_rows=Mock(),
            sort_by_column=Mock(),
        )

        GPXEditorController.apply_dropped_track_sort(
            controller,
            [first, second],
            "distance",
            anchor_was_set=False,
        )

        self.assertEqual(controller.anchor, (50.0, 7.0))
        self.assertEqual(controller.selected_nrs, [4, 5])
        controller.sort_by_column.assert_called_once_with(
            "distance",
            ascending=True,
            source="dropped tracks",
            push_undo_step=False,
        )

    def test_dropped_distance_sort_preserves_existing_anchor(self):
        first = self.make_track(4, "First", "first.gpx")
        second = self.make_track(5, "Second", "second.gpx")
        controller = SimpleNamespace(
            tracks=[first, second],
            anchor=(48.0, 11.0),
            selected_nrs=[],
            update_selection_field=Mock(),
            highlight_selected_rows=Mock(),
            sort_by_column=Mock(),
        )

        GPXEditorController.apply_dropped_track_sort(
            controller,
            [first, second],
            "distance",
            anchor_was_set=True,
        )

        self.assertEqual(controller.anchor, (48.0, 11.0))


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
    def test_axis_rows_prefer_the_selected_track(self):
        first = SimpleNamespace(nr=1)
        second = SimpleNamespace(nr=2)
        rows = [
            {"track": first, "elevation": 120.0},
            {"track": second, "elevation": 1800.0},
        ]
        self.assertEqual(
            elevation_rows_for_selected_tracks(rows, [1]),
            [rows[0]],
        )
        self.assertEqual(elevation_rows_for_selected_tracks(rows, []), rows)

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
    def test_coarse_overview_sampling_preserves_endpoints_and_budget(self):
        points = [(float(index), float(index % 7)) for index in range(1000)]
        sampled = sampled_polyline_points(points, 80)
        self.assertLessEqual(len(sampled), 80)
        self.assertEqual(sampled[0], points[0])
        self.assertEqual(sampled[-1], points[-1])

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
