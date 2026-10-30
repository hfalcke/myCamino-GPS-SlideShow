"""Track-time repair must not rewrite recorded times, geometry or other files."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import threading
import unittest
import copy
import xml.etree.ElementTree as ET

from adventure_parameters import default_parameters
from cocoa_track_timing import TrackTimingTool, analyze_track_times
from GPXEditor import GPXEditorController, TrackInspectorController, TrackRecord, NSMakeRect, qname
from gpx_point_editing import point_time_origin, point_time_source_label, set_point_time_origin
from gpx_processing import ProcessingOptions, raw_track_geometry_fingerprint
from gpx_processing import parse_time, format_gpx_timestamp
from gpx_import import load_gpx_document
from media_route_timing import MediaObservation
from track_time_estimation import (TimingCancelled, TimingPoint, estimate_track_times, missing_time_ranges,
    missing_time_ranges_from_element, timing_observations, timing_points_from_element)


START = datetime(2026, 8, 1, 10, tzinfo=timezone.utc)


def point(distance, seconds=None, segment=0, **kwargs):
    return TimingPoint(50, 7 + distance / 70, segment,
        None if seconds is None else START + timedelta(seconds=seconds), distance_km=distance, **kwargs)


def element(times=(0, None, 60), distances=None):
    track = ET.Element(qname("trk"))
    ET.SubElement(track, qname("name")).text = "Timing test"
    segment = ET.SubElement(track, qname("trkseg"))
    for index, time in enumerate(times):
        p = ET.SubElement(segment, qname("trkpt"), lat="50", lon=str(7 + (distances[index] if distances else index * .001)))
        ET.SubElement(p, qname("ele")).text = "100"
        if time is not None:
            ET.SubElement(p, qname("time")).text = (START + timedelta(seconds=time)).isoformat()
    return track


def bind(controller, *names):
    for name in names:
        function = getattr(GPXEditorController, name)
        function = getattr(function, "callable", function)
        setattr(controller, name, lambda *args, _fn=function, **kwargs: _fn(controller, *args, **kwargs))
    return controller


class TimingPlannerTests(unittest.TestCase):
    def test_distance_weighted_interpolation_not_row_count(self):
        points = [point(0, 0), point(.1), point(.9), point(1, 600)]
        proposal = estimate_track_times(points)
        self.assertEqual([patch.time for patch in proposal.patches],
                         [START + timedelta(seconds=60), START + timedelta(seconds=540)])
        self.assertTrue(all(patch.method == "interpolation" for patch in proposal.patches))
        self.assertEqual(points[1].time, None)

    def test_open_ends_use_measured_speed_before_fallback(self):
        result = estimate_track_times([point(0), point(.5, 0), point(1, 600), point(1.5)])
        self.assertEqual([patch.time for patch in result.patches],
                         [START - timedelta(seconds=600), START + timedelta(seconds=1200)])
        self.assertEqual(result.count("extrapolation"), 2)
        result = estimate_track_times([point(0), point(.5, 0)], fallback_speed_kmh=6)
        self.assertEqual(result.patches[0].time, START - timedelta(seconds=300))

    def test_running_speed_uses_same_segment_only(self):
        points = [point(0, 0, running_speed_kmh=20), point(0, None, 1),
                  point(1, 0, 1, running_speed_kmh=6)]
        result = estimate_track_times(points)
        self.assertEqual(result.patches[0].time, START - timedelta(seconds=600))

    def test_no_absolute_anchor_and_no_cross_segment_interpolation(self):
        result = estimate_track_times([point(0, 0), point(1, None, 1), point(2, None, 1)])
        self.assertFalse(result.patches)
        self.assertEqual([index for index, _reason in result.unresolved], [1, 2])

    def test_recorded_and_manual_preserved_and_generated_refresh_is_explicit(self):
        points = [point(0, 0), point(.2, 500, origin="generated"), point(1, 600, origin="manual")]
        self.assertFalse(estimate_track_times(points).patches)
        result = estimate_track_times(points, mode="refresh")
        self.assertEqual([patch.index for patch in result.patches], [1])
        self.assertEqual(result.patches[0].time, START + timedelta(seconds=120))

    def test_conflicting_existing_anchors_stay_unchanged(self):
        result = estimate_track_times([point(0, 60), point(.5), point(1, 0)])
        self.assertFalse(result.patches)
        self.assertIn("Conflicting", result.unresolved[0][1])

    def test_zero_distance_does_not_invent_microseconds(self):
        result = estimate_track_times([point(0, 0), point(0), point(0, 60)])
        self.assertEqual(result.patches[0].time, START + timedelta(seconds=60))
        self.assertFalse(result.patches[0].time.microsecond)

    def test_dst_interpolation_uses_elapsed_utc_time(self):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("Europe/Berlin")
        before = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
        after = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=1)
        points = [replace(point(0), time=before), point(.5), replace(point(1), time=after)]
        self.assertEqual(estimate_track_times(points).patches[0].time, datetime(2026, 10, 25, 1, tzinfo=timezone.utc))

    def test_manual_start_anchors_only_first_segment(self):
        result = estimate_track_times([point(0), point(.5), point(0, None, 1)], manual_start_time=START)
        self.assertEqual(result.patches[0].time, START)
        self.assertEqual([index for index, _reason in result.unresolved], [2])

    def test_media_anchors_work_without_reversing_route(self):
        points = [point(0), point(.05), point(.1)]
        media = [MediaObservation(Path("start.jpg"), START, points[0].latitude, points[0].longitude),
                 MediaObservation(Path("end.jpg"), START + timedelta(minutes=10), points[2].latitude, points[2].longitude)]
        result = estimate_track_times(points, media=media)
        self.assertEqual(len(result.patches), 3)
        self.assertAlmostEqual((result.patches[1].time - START).total_seconds(), 300, places=3)
        self.assertFalse(result.unresolved)

    def test_duplicate_offroute_and_conflicting_media_cannot_bias_existing_times(self):
        points = [point(0, 0), point(.05), point(.1, 600)]
        bad = MediaObservation(Path("bad.jpg"), START - timedelta(days=1), 50, points[1].longitude, "same")
        far = MediaObservation(Path("far.jpg"), START, 51, 8)
        result = estimate_track_times(points, media=[bad, replace(bad, path=Path("copy.jpg")), far])
        self.assertEqual(len(result.patches), 1)
        self.assertAlmostEqual((result.patches[0].time - START).total_seconds(), 300, places=3)
        self.assertTrue(result.ignored_media)

    def test_track_inferred_gps_is_not_an_anchor(self):
        record = SimpleNamespace(photo_datetime=START, latitude=50, longitude=7,
            gps_source="track_time_interpolation", raw_metadata={})
        observed, ignored = timing_observations([SimpleNamespace(path=Path("photo.jpg"), record=record)])
        self.assertFalse(observed)
        self.assertIn("inferred", ignored[0])

    def test_diagnostic_ranges_keep_original_indexes_and_segments(self):
        points = [point(0, 0), point(.1), point(.2), point(.3, 600), point(0, None, 1)]
        gaps = missing_time_ranges(points)
        self.assertEqual([(gap.first, gap.last, gap.segment) for gap in gaps], [(1, 2, 0), (4, 4, 1)])
        self.assertEqual(gaps[0].preceding, START)
        self.assertEqual(gaps[0].following, START + timedelta(minutes=10))
        self.assertIsNone(gaps[1].preceding)

    def test_cancel_and_invalid_mode(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(TimingCancelled):
            estimate_track_times([point(0)], cancel_event=cancel)
        with self.assertRaises(ValueError):
            estimate_track_times([point(0)], mode="replace-everything")

    def test_isolated_waypoint_accepts_only_unambiguous_media_time(self):
        observation = MediaObservation(Path("only.jpg"), START, 50, 7)
        result = estimate_track_times([point(0)], media=[observation])
        self.assertEqual(result.patches[0].time, START)
        result = estimate_track_times([point(0)], media=[observation,
            replace(observation, path=Path("later.jpg"), timestamp=START + timedelta(hours=2))])
        self.assertFalse(result.patches)
        self.assertTrue(result.unresolved)

    def test_regression_thirty_gaps_keep_all_other_times(self):
        missing = {82, 83, *range(169, 197)}
        points = [point(index / 100, None if index in missing else index * 10) for index in range(210)]
        result = estimate_track_times(points)
        self.assertEqual({patch.index for patch in result.patches}, missing)
        self.assertEqual(len(result.patches), 30)
        self.assertEqual(result.count("interpolation"), 30)
        self.assertFalse(result.unresolved)

    def test_source_labels_and_manual_edit_clear_estimation_method(self):
        p = list(element().iter(qname("trkpt")))[0]
        self.assertEqual(point_time_source_label(p), "Recorded")
        set_point_time_origin(p, "generated", "interpolation")
        self.assertEqual(point_time_source_label(p), "Estimated: interpolation")
        set_point_time_origin(p, "manual")
        self.assertEqual(point_time_source_label(p), "Manual")
        self.assertNotIn('method=', ET.tostring(p, encoding="unicode"))


class TimingServiceTests(unittest.TestCase):
    def test_bounded_gaps_do_not_even_scan_media_or_prepare_metadata(self):
        track = element()
        snapshot = [(9, ET.tostring(track), "Track 9", False)]
        with patch("project_media_watcher.discover_project_media", side_effect=AssertionError("unnecessary scan")), \
             patch("media_metadata_service.prepare_media_records", side_effect=AssertionError("unnecessary extraction")):
            results = analyze_track_times(snapshot, ProcessingOptions(), default_parameters(), "fill",
                {"project_directory": "/not-needed"}, [], True, threading.Event(), Mock())
        self.assertEqual(len(results[9].patches), 1)
        self.assertEqual(track.find("gpx:trkseg/gpx:trkpt[2]/gpx:time", {"gpx": qname("")[1:-1]}), None)

    def test_project_and_control_media_used_automatically_without_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "photo.jpg"
            media.write_bytes(b"photo")
            record = SimpleNamespace(photo_datetime=START, latitude=50, longitude=7,
                gps_source="embedded", raw_metadata={})
            prepared = [SimpleNamespace(path=media, record=record)]
            snapshot = [(9, ET.tostring(element((None, None, None))), "Track 9", False)]
            with patch("media_metadata_service.prepare_media_records", return_value=prepared) as prepare:
                results = analyze_track_times(snapshot, ProcessingOptions(), default_parameters(), "fill",
                    {"project_directory": str(root)}, [], True, threading.Event(), Mock())
            self.assertEqual(prepare.call_args.args[0], [media.resolve()])
            self.assertNotIn("tracks_summary_path", prepare.call_args.kwargs)
            self.assertEqual(len(results[9].patches), 3)

    def test_existing_adventure_media_index_avoids_another_directory_scan(self):
        record = SimpleNamespace(photo_datetime=START, latitude=50, longitude=7,
            gps_source="embedded", raw_metadata={})
        prepared = [SimpleNamespace(path=Path("existing.jpg"), record=record)]
        snapshots = [(9, ET.tostring(element((None, None))), "Track 9", False)]
        with patch("project_media_watcher.discover_project_media", side_effect=AssertionError("index already available")), \
             patch("media_metadata_service.prepare_media_records", return_value=prepared) as prepare:
            result = analyze_track_times(snapshots, ProcessingOptions(), default_parameters(), "fill",
                {"project_directory": "/project", "_timing_project_media_paths": ["existing.jpg"]},
                [], True, threading.Event(), Mock())
        prepare.assert_called_once()
        self.assertEqual(len(result[9].patches), 2)


class TimingEditorIntegrationTests(unittest.TestCase):
    def editor(self):
        track = TrackRecord(7, element(), "source.gpx")
        controller = SimpleNamespace(tracks=[track], project_parameters=default_parameters(),
            undo_stack=[], redo_stack=[], mark_dirty=Mock(), set_status=Mock(),
            invalidate_track_metrics=Mock(side_effect=lambda track: GPXEditorController.invalidate_track_metrics(controller, track)),
            redraw_open_plot_views=Mock(), existing_inspector_for_track=lambda _track: None,
            selected_nrs=[7], media_context={}, last_save_path=None)
        return bind(controller, "timestamp_states", "finish_timestamp_change", "restore_timestamp_states",
                    "apply_timing_results", "_timing_suppression_context", "_standalone_timing_key",
                    "update_timing_suppressions", "timing_prompt_suppressed", "timing_ranges")

    def test_apply_undo_redo_only_timestamp_fields_and_keep_geometry_cache(self):
        editor = self.editor()
        track = editor.tracks[0]
        original = ET.tostring(track.element)
        geometry = raw_track_geometry_fingerprint(track.element)
        retained = track.display_geometry = {"kept": True}
        result = estimate_track_times(timing_points_from_element(track.element))
        snapshots = [(7, original, "Track 7", False)]
        self.assertTrue(editor.apply_timing_results(snapshots, {7: result}, dict(editor.project_parameters)))
        changed = ET.tostring(track.element)
        self.assertNotEqual(changed, original)
        self.assertEqual(raw_track_geometry_fingerprint(track.element), geometry)
        self.assertIs(track.display_geometry, retained)
        self.assertEqual(editor.selected_nrs, [7])
        self.assertEqual(editor.mark_dirty.call_count, 1)
        self.assertFalse(missing_time_ranges_from_element(track.element))
        self.assertEqual(point_time_origin(list(track.element.iter(qname("trkpt")))[1]), "generated")
        GPXEditorController.undo_.callable(editor, None)
        self.assertEqual(ET.tostring(track.element), original)
        GPXEditorController.redo_.callable(editor, None)
        self.assertEqual(ET.tostring(track.element), changed)

    def test_fractional_anchor_plateau_survives_apply_save_and_repeated_reload(self):
        # Like Santiago #117 row 170, a repair may fall within its anchor's
        # second. This also covers a plateau with identical processed distance.
        editor = self.editor()
        track = editor.tracks[0]
        track.element = element((38.664, None, 43), distances=[0, 0, .001])
        original = ET.tostring(track.element)
        recorded = [p.findtext(qname("time")) for p in track.element.iter(qname("trkpt"))]
        proposal = estimate_track_times(timing_points_from_element(track.element))
        self.assertEqual(proposal.patches[0].time.microsecond, 664000)
        self.assertTrue(editor.apply_timing_results([(7, original, "Track 117", False)], {7: proposal}, dict(editor.project_parameters)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repaired.gpx"
            editor.project_name = "Precision regression"
            editor.anchor = None
            editor.next_nr = 8
            editor.build_root = lambda destination: GPXEditorController.build_root(editor, destination)
            for _ in range(2):
                GPXEditorController.write_gpx_file(editor, path)
                document = load_gpx_document(path)
                self.assertEqual(document.report.backward_timestamps, 0)
                self.assertEqual(document.report.invalid_timestamps, 0)
                self.assertFalse(missing_time_ranges_from_element(document.tracks[0]))
                saved = [p.findtext(qname("time")) for p in document.tracks[0].iter(qname("trkpt"))]
                self.assertEqual(saved[0], recorded[0])
                self.assertEqual(saved[2], recorded[2])
                self.assertEqual(parse_time(saved[1]), parse_time(recorded[0]))
                self.assertFalse(estimate_track_times(timing_points_from_element(document.tracks[0])).patches)
                track.element = copy.deepcopy(document.tracks[0])

    def test_fractional_interpolation_is_not_rounded_to_whole_seconds(self):
        editor = self.editor()
        track = editor.tracks[0]
        track.element = element((.999, None, 1.001))
        original = ET.tostring(track.element)
        points = [replace(point(0, .999)), replace(point(.1)), replace(point(1, 1.001))]
        proposal = estimate_track_times(points)
        expected = START + timedelta(seconds=1, microseconds=-800)
        self.assertEqual(proposal.patches[0].time, expected)
        self.assertTrue(editor.apply_timing_results([(7, original, "Precision", False)], {7: proposal}, dict(editor.project_parameters)))
        timestamp = list(track.element.iter(qname("trkpt")))[1].findtext(qname("time"))
        self.assertEqual(parse_time(timestamp), expected)

    def test_gui_cli_and_shared_writers_preserve_precision_and_utc(self):
        from GPXEditor import format_gpx_time as gui_format
        from editGPXTrack import format_gpx_time as cli_format
        for microsecond in (0, 1, 664000, 999999):
            value = START.astimezone(timezone(timedelta(hours=2))).replace(microsecond=microsecond)
            for formatter in (format_gpx_timestamp, gui_format, cli_format):
                text = formatter(value)
                self.assertTrue(text.endswith("Z"))
                self.assertEqual(parse_time(text), value)
        self.assertEqual(gui_format(START), "2026-08-01T10:00:00Z")

    def test_stale_track_or_settings_results_cannot_apply(self):
        editor = self.editor()
        track = editor.tracks[0]
        snapshots = [(7, ET.tostring(track.element), "Track 7", False)]
        result = estimate_track_times(timing_points_from_element(track.element))
        track.element.find(qname("name")).text = "Changed while analyzing"
        self.assertFalse(editor.apply_timing_results(snapshots, {7: result}, editor.project_parameters))
        self.assertFalse(editor.undo_stack)
        self.assertFalse(editor.apply_timing_results([], {}, {}))

    def test_timestamp_undo_cannot_overwrite_a_newer_manual_edit(self):
        editor = self.editor()
        track = editor.tracks[0]
        snapshot = [(7, ET.tostring(track.element), "Track 7", False)]
        result = estimate_track_times(timing_points_from_element(track.element))
        editor.apply_timing_results(snapshot, {7: result}, dict(editor.project_parameters))
        p = list(track.element.iter(qname("trkpt")))[1]
        p.find(qname("time")).text = (START + timedelta(seconds=45)).isoformat()
        set_point_time_origin(p, "manual")
        before = ET.tostring(track.element)
        GPXEditorController.undo_.callable(editor, None)
        self.assertEqual(ET.tostring(track.element), before)
        self.assertEqual(len(editor.undo_stack), 1)
        self.assertIn("newer waypoint edits", editor.set_status.call_args.args[0])

    def test_diagnostics_reuse_cache_until_the_affected_track_is_invalidated(self):
        editor = self.editor()
        with patch("track_time_estimation.missing_time_ranges_from_element", wraps=missing_time_ranges_from_element) as ranges:
            self.assertEqual(len(editor.timing_ranges(editor.tracks[0])), 1)
            self.assertEqual(len(editor.timing_ranges(editor.tracks[0])), 1)
            self.assertEqual(ranges.call_count, 1)
            editor.invalidate_track_metrics(editor.tracks[0])
            editor.timing_ranges(editor.tracks[0])
            self.assertEqual(ranges.call_count, 2)

    def test_remembered_identity_survives_renaming_but_not_changed_geometry(self):
        editor = self.editor()
        values = set()
        editor.adventure_workspace_delegate = SimpleNamespace(untimed_prompt_suppressed_identities=lambda: values,
            remember_untimed_prompt_suppressions=lambda identities: values.update(identities),
            forget_untimed_prompt_suppressions=lambda identities: values.difference_update(identities))
        editor.update_timing_suppressions([7], suppress=True)
        editor.tracks[0].element.find(qname("name")).text = "Renamed"
        self.assertTrue(editor.timing_prompt_suppressed(editor.tracks[0]))
        list(editor.tracks[0].element.iter(qname("trkpt")))[1].set("lon", "7.123")
        self.assertFalse(editor.timing_prompt_suppressed(editor.tracks[0]))

    def test_suppression_is_document_local_and_does_not_dirty_gpx(self):
        editor = self.editor()
        stored = {}
        defaults = Mock()
        defaults.dictionaryForKey_.side_effect = lambda key: stored.get(key)
        defaults.setObject_forKey_.side_effect = lambda value, key: stored.__setitem__(key, value)
        with patch("GPXEditor.NSUserDefaults") as prefs:
            prefs.standardUserDefaults.return_value = defaults
            self.assertFalse(editor.timing_prompt_suppressed(editor.tracks[0]))
            editor.update_timing_suppressions([7], suppress=True)
            self.assertTrue(editor.timing_prompt_suppressed(editor.tracks[0]))
            editor.last_save_path = Path("/different/document.gpx")
            self.assertFalse(editor.timing_prompt_suppressed(editor.tracks[0]))
            editor.last_save_path = None
            editor.update_timing_suppressions([7], suppress=False)
            self.assertFalse(editor.timing_prompt_suppressed(editor.tracks[0]))
        editor.mark_dirty.assert_not_called()

    def test_adventure_suppression_uses_existing_callbacks_and_ignores_old_global_choices(self):
        editor = self.editor()
        values = set()
        workspace = SimpleNamespace(untimed_prompt_suppressed_identities=lambda: values,
            remember_untimed_prompt_suppressions=lambda identities: values.update(identities),
            forget_untimed_prompt_suppressions=lambda identities: values.difference_update(identities))
        editor.adventure_workspace_delegate = workspace
        with patch("GPXEditor.NSUserDefaults", side_effect=AssertionError("Adventure does not use global choices")):
            editor.update_timing_suppressions([7], suppress=True)
            self.assertTrue(editor.timing_prompt_suppressed(editor.tracks[0]))
            editor.update_timing_suppressions([7], suppress=False)
            self.assertFalse(editor.timing_prompt_suppressed(editor.tracks[0]))

    def test_import_offer_nonmodal_and_suppressed_tracks_remain_inspectable(self):
        editor = self.editor()
        tool = Mock()
        editor.timing_tool = Mock(return_value=tool)
        editor.timing_prompt_suppressed = Mock(return_value=False)
        with patch("GPXEditor.NSAlert", side_effect=AssertionError("No modal import prompt")):
            GPXEditorController.offer_track_time_entry(editor)
        tool.show.assert_called_once_with([7])
        tool.reset_mock()
        editor.timing_prompt_suppressed.return_value = True
        GPXEditorController.offer_track_time_entry(editor)
        tool.show.assert_not_called()
        GPXEditorController.findMissingTimestamps_.callable(editor, None)
        tool.show.assert_called_once_with()

    def test_ignore_flag_only_persists_on_explicit_ignore(self):
        for checked in (0, 1):
            tool = SimpleNamespace(flag=Mock(state=Mock(return_value=checked)), offered=[7], rows=[dict(nr=7)],
                                   editor=Mock(), dismiss_=Mock())
            TrackTimingTool.ignore_.callable(tool, None)
            self.assertEqual(tool.editor.update_timing_suppressions.call_count, checked)
            tool.dismiss_.assert_called_once()

    def test_escape_and_close_cancel_analysis_without_suppressing(self):
        tool = SimpleNamespace(cancel_event=threading.Event(), future=Mock(), timer=Mock(), window=Mock(), editor=Mock())
        TrackTimingTool.dismiss_.callable(tool, None)
        self.assertTrue(tool.cancel_event.is_set())
        self.assertIsNone(tool.future)
        self.assertIsNone(tool.timer)
        tool.editor.update_timing_suppressions.assert_not_called()

    def test_missing_navigation_keeps_original_row_indexes_and_does_not_open_map(self):
        points = [SimpleNamespace(time=value) for value in (START, None, START, None)]
        inspector = SimpleNamespace(track=SimpleNamespace(points=lambda: points),
            selected_row_indexes=lambda: [1], select_point_rows=Mock(), plot_view=None, parent=Mock())
        TrackInspectorController.navigate_missing_time(inspector, 1)
        inspector.select_point_rows.assert_called_once_with([3])
        inspector.parent.open_track_plot_window.assert_not_called()

    def test_tools_include_hidden_tracks_in_all_scope_and_selected_tracks_only_when_requested(self):
        tracks = [SimpleNamespace(nr=7, hidden=True), SimpleNamespace(nr=8, hidden=False)]
        tool = SimpleNamespace(editor=SimpleNamespace(tracks=tracks, selected_nrs=[7]),
                               scope=Mock(indexOfSelectedItem=Mock(return_value=0)))
        self.assertEqual(TrackTimingTool.tracks(tool), tracks)
        tool.scope.indexOfSelectedItem.return_value = 1
        self.assertEqual(TrackTimingTool.tracks(tool), [tracks[0]])


class TimingSaveOptionTests(unittest.TestCase):
    def tool(self):
        context = {"project_directory": "/project"}
        return SimpleNamespace(
            editor=SimpleNamespace(media_context=dict(context), apply_timing_results=Mock(return_value=True),
                save_current=Mock(return_value=True), current_output_path=Mock(return_value=Path("/project/route.gpx"))),
            analysis_context=dict(context), applied_context=None, snapshots=[], results={7: object()},
            analysis_settings={}, buttons={"Apply": Mock(), "Save Tracks": Mock()}, status=Mock(),
        )

    def test_successful_apply_offers_save_without_saving_automatically(self):
        tool = self.tool()
        TrackTimingTool.apply_.callable(tool, None)
        tool.buttons["Save Tracks"].setEnabled_.assert_called_once_with(True)
        tool.editor.save_current.assert_not_called()
        self.assertIn("Choose Save Tracks", tool.progress_text)
        self.assertEqual(tool.applied_context, tool.editor.media_context)
        self.assertIsNot(tool.applied_context, tool.editor.media_context)

    def test_unsuccessful_apply_does_not_enable_save(self):
        tool = self.tool()
        tool.editor.apply_timing_results.return_value = False
        TrackTimingTool.apply_.callable(tool, None)
        tool.buttons["Save Tracks"].setEnabled_.assert_not_called()
        self.assertIsNone(tool.applied_context)

    def test_save_uses_existing_document_save_and_preserves_undo(self):
        tool = self.tool()
        tool.applied_context = dict(tool.editor.media_context)
        undo = tool.editor.undo_stack = [object()]
        TrackTimingTool.saveTracks_.callable(tool, None)
        tool.editor.save_current.assert_called_once_with()
        tool.buttons["Save Tracks"].setEnabled_.assert_called_once_with(False)
        self.assertIn("saved", tool.progress_text)
        self.assertIs(tool.editor.undo_stack, undo)

    def test_cancelled_save_keeps_applied_changes_available_for_retry(self):
        tool = self.tool()
        tool.applied_context = dict(tool.editor.media_context)
        tool.editor.save_current.return_value = False
        TrackTimingTool.saveTracks_.callable(tool, None)
        tool.buttons["Save Tracks"].setEnabled_.assert_not_called()
        self.assertIn("remain in the editor", tool.progress_text)
        self.assertIn("retry", tool.progress_text)

    def test_failed_save_does_not_escape_cocoa_action_or_discard_changes(self):
        tool = self.tool()
        tool.applied_context = dict(tool.editor.media_context)
        tool.editor.save_current.side_effect = OSError("Read-only destination")
        TrackTimingTool.saveTracks_.callable(tool, None)
        tool.buttons["Save Tracks"].setEnabled_.assert_not_called()
        self.assertIn("Read-only destination", tool.progress_text)
        self.assertIn("retry", tool.progress_text)

    def test_save_cannot_write_before_apply_or_after_changing_adventures(self):
        tool = self.tool()
        TrackTimingTool.saveTracks_.callable(tool, None)
        tool.editor.save_current.assert_not_called()
        tool.applied_context = {"project_directory": "/previous-project"}
        TrackTimingTool.saveTracks_.callable(tool, None)
        tool.editor.save_current.assert_not_called()
        self.assertIn("Adventure changed", tool.progress_text)

    def test_new_button_fits_the_minimum_window_width(self):
        tool = SimpleNamespace(window=Mock(), info=Mock(), scope=Mock(), mode=Mock(), flag=Mock(),
            scroll=Mock(), status=Mock(), buttons={title: Mock() for title in
                ("Choose Additional Media…", "Set Start Time…", "Analyze", "Apply", "Save Tracks", "Cancel")})
        tool.window.contentView.return_value.bounds.return_value = NSMakeRect(0, 0, 840, 430)
        TrackTimingTool.layout(tool)
        previous_right = 0
        for button in tool.buttons.values():
            frame = button.setFrame_.call_args.args[0]
            self.assertGreaterEqual(frame.origin.x, previous_right)
            previous_right = frame.origin.x + frame.size.width
            self.assertLessEqual(previous_right, 840 - 16)
