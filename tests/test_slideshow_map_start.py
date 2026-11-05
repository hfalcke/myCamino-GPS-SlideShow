"""Map selections reuse slideshow preparation and start at a member's real time."""

from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GPSTrackShow as show
from GPSTrackShowGUI import slideshow_track_start_position, validated_slideshow_resume_position
from GPXEditor import PlotView
from cocoa_adventure_map import AdventureMapController
from slideshow_stages import normalize_track_start, track_control_start


class SlideshowMapStartTests(unittest.TestCase):
    def setUp(self):
        self.lines = ["#Overviewmap: Trip.png", "#Map: shared.png", "#Track: internal_id=7",
                      "early.jpg | 09:00", "#Track: internal_id=12", "before.jpg | 08:30", "next.jpg | 09:30"]
        members = {}
        for identity, day in ((7, "2024-07-16"), (12, "2024-07-15")):
            members[identity] = {"internal_id": identity, "track_name": str(identity), "track_length_km": 10,
                "track_type": "pilgrimage", "timed_track_points": [
                    {"lat": 50, "lon": 6, "time_iso": day + "T08:00:00Z", "cumulative_distance_km": 0},
                    {"lat": 50.1, "lon": 6.1, "time_iso": day + "T10:00:00Z", "cumulative_distance_km": 10}]}
        metadata = {"stage_plan_version": 1, "stage_statistics": {"track_length_km": 20},
                    "stage_member_data": members, "overlay_track_internal_ids": [7, 12]}
        self.metadata = show.shared_stage_block_metadata(metadata, self.lines, 1, len(self.lines))
        self.selection = {"internal_id": 12, "timestamp": "2024-07-15T09:00:00Z"}

    def test_control_lookup_uses_internal_member_not_stage_or_row_number(self):
        self.assertEqual(track_control_start(self.lines, 12), (1, 5))
        self.assertEqual(track_control_start(self.lines + ["#Map: shared.png", "#Track: internal_id=12"], 12), (1, 5))
        with self.assertRaisesRegex(ValueError, "no enabled route section"):
            track_control_start(self.lines, 99)

    def test_legacy_lookup_preserves_real_unicode_asset_paths(self):
        lines = ["#Map: Etappe Ko\u0308ln.png", "photo.jpg"]
        tracks = [{"internal_id": 12, "display_track_number": 100,
                   "track_plot_image_filename": "Etappe K\u00f6ln.png"}]
        self.assertEqual(track_control_start(lines, 12, tracks), (0, 1))

    def test_selected_time_maps_into_member_span_even_when_dates_go_backward(self):
        anchor = show.track_start_in_stage(self.metadata, self.lines, 1, len(self.lines), self.selection)
        self.assertEqual(anchor["fraction"], .75)
        self.assertEqual(anchor["row_index"], 5)
        state = show.interpolate_timeline_state(show.timed_points_from_metadata(self.metadata), anchor["fraction"])
        self.assertEqual(state["member_id"], 12)
        self.assertEqual(state["time"], datetime.fromisoformat("2024-07-15T09:00:00Z"))

    def test_track_only_starts_at_member_beginning_not_whole_stage(self):
        anchor = show.track_start_in_stage(self.metadata, self.lines, 1, len(self.lines), {"internal_id": 12})
        self.assertEqual(anchor["fraction"], .5)
        self.assertIsNone(anchor["timestamp"])
        with self.assertRaisesRegex(ValueError, "not part"):
            show.track_start_in_stage(self.metadata, self.lines, 1, len(self.lines), {"internal_id": 99})

    def test_request_validation_and_python_cli_options(self):
        for invalid in ({"internal_id": 0}, {"internal_id": True}, {"internal_id": "bad"},
                        {"internal_id": 1, "timestamp": "not a time"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_track_start(invalid)
        with TemporaryDirectory() as directory:
            control = Path(directory) / "Trip.lst"
            control.write_text("\n".join(self.lines))
            config = show.config_from_options(directory, inputlist=control, resume_index=1, resume_track_start=self.selection)
            self.assertEqual(config.resume_track_start["internal_id"], 12)
            with patch.object(show, "available_screen_count", return_value=1):
                config = show.parse_args([directory, "--inputlist", str(control), "--resume-index", "1",
                                          "--resume-track-start", json.dumps(self.selection)])
            self.assertEqual(config.resume_track_start["timestamp"], "2024-07-15T09:00:00+00:00")

    def test_gui_resolves_checkpoint_after_control_updates_without_changing_file(self):
        with TemporaryDirectory() as directory:
            control = Path(directory) / "Trip.lst"
            control.write_text("\n".join(["# A newly inserted directive", *self.lines]))
            before = control.read_bytes()
            position = slideshow_track_start_position(self.selection, control, directory)
            self.assertEqual(position["playlist_index"], 2)
            self.assertEqual(position["track_start"]["internal_id"], 12)
            self.assertIsNotNone(validated_slideshow_resume_position(position, control))
            self.assertEqual(control.read_bytes(), before)
            summary = Path(directory) / "Trip-summary.json"
            summary.write_text(json.dumps({"tracks": [{"internal_id": 12, "hidden": True}]}))
            with self.assertRaisesRegex(ValueError, "excluded"):
                slideshow_track_start_position(self.selection, control, directory)
            self.assertEqual(slideshow_track_start_position(self.selection, control, directory,
                             include_hidden_tracks=True)["playlist_index"], 2)

    def make_player(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.playlist_lines, app.stages = self.lines, show.parse_stage_descriptors(self.lines)
        app.current_track_metadata, app.current_date = self.metadata, None
        app.resume_track_start_pending = self.selection.copy()
        app._reconstruct_control_state_before = Mock()
        app.music_controller, app.narration_controller = Mock(), Mock()
        dates = {"early.jpg": "2024-07-16T09:00:00Z", "before.jpg": "2024-07-15T08:30:00Z",
                 "next.jpg": "2024-07-15T09:30:00Z"}
        app._time_lapse_media_datetime = lambda entry, *_args: datetime.fromisoformat(dates[entry.source_name])
        return app

    def test_standard_starts_at_next_authored_media_and_reconstructs_skipped_state(self):
        app = self.make_player()
        anchor = app._apply_selected_track_start(1, standard=True)
        self.assertEqual(anchor["row_index"], 6)
        self.assertEqual(anchor["fraction"], .75)
        self.assertEqual(app.current_track_metadata["active_member_id"], 12)
        self.assertIsNone(app.resume_track_start_pending)
        app._reconstruct_control_state_before.assert_called_once_with(6)
        app.music_controller.synchronize_row.assert_called_once_with(5)

    def test_time_lapse_starts_at_exact_time_without_replaying_previous_member(self):
        app = self.make_player()
        app.config = SimpleNamespace(clock=True)
        app.current_stage_index, app.current_overview_image, app.current_overview_metadata = None, object(), None
        app.resume_phase_pending, app.walker_steering_by_row = None, {}
        entry = show.StagePresentationCacheEntry((), Path("shared.png"), object(), self.metadata,
                                                rendered_image=object(), rendered_overview=object())
        app._load_stage_entry = Mock(return_value=entry)
        for name in ("_suspend_standard_playback_for_time_lapse", "_stop_time_lapse_video", "_set_time_lapse_info_text",
                     "_show_time_lapse_overview_or_begin", "_begin_time_lapse_motion"):
            setattr(app, name, Mock())
        app._uses_persistent_dual_profile = lambda: False
        app._time_lapse_distance_before_stage = lambda _index: 0
        app._start_time_lapse_stage(1, "shared.png")
        self.assertEqual(app.time_lapse_progress, .75)
        self.assertEqual(app.time_lapse_entry_row, 5)
        self.assertEqual(app.time_lapse_media_queue[app.time_lapse_media_cursor][2].source_name, "next.jpg")
        app._begin_time_lapse_motion.assert_called_once()
        app._show_time_lapse_overview_or_begin.assert_not_called()

    def test_route_entry_repeats_use_the_selected_control_range(self):
        app = SimpleNamespace(running=True, time_lapse_active=True, time_lapse_stage=SimpleNamespace(map_index=1),
                              time_lapse_entry_row=5, _run_repeated_directives=Mock(return_value=True))
        show.GPSTrackShowApp._begin_time_lapse_motion(app)
        self.assertEqual(app._run_repeated_directives.call_args.args[:2], (5, "all"))

    def test_map_selection_distinguishes_tracks_points_and_stale_cursors(self):
        point = SimpleNamespace(time=None)
        first, second = SimpleNamespace(points=lambda: [point]), SimpleNamespace(points=lambda: [point])
        view = SimpleNamespace(controller=SimpleNamespace(selected_tracks=lambda: [second]),
                               context_target=None, context_track_target=None, cursor=(0, point, first))
        self.assertEqual(PlotView.slideshow_context_selection(view), (second, None))
        view.cursor = (0, point, second)
        self.assertEqual(PlotView.slideshow_context_selection(view), (second, point))
        view.context_target = ("point", first, 0)
        self.assertEqual(PlotView.slideshow_context_selection(view), (first, point))

    def test_context_menu_offers_start_only_for_adventure_track_selection(self):
        import GPXEditor as editor
        track = SimpleNamespace(nr=12)
        workspace = SimpleNamespace(start_slideshow_at_track=Mock())
        view = SimpleNamespace(adventure_workspace_delegate=workspace, mode="overview",
            convertPoint_fromView_=Mock(return_value=object()), waypoint_editing_enabled=lambda: False,
            nearest_track_for_location=lambda _point: None, selected_range_at_target=lambda _target: None,
            add_location_context_items=Mock(), add_view_context_items=Mock(), _plot_context_item=Mock(return_value=Mock()),
            slideshow_context_selection=lambda **_kwargs: (track, None), controller=SimpleNamespace(selected_tracks=lambda: [track]))
        for delegate, selected in ((workspace, track), (workspace, None), (None, track)):
            view.adventure_workspace_delegate = delegate
            view.slideshow_context_selection = lambda **_kwargs: (selected, None)
            view._plot_context_item.reset_mock()
            with patch.object(editor, "NSMenu") as menus, patch.object(editor, "NSMenuItem"):
                menus.alloc.return_value.initWithTitle_.return_value.numberOfItems.return_value = 1
                PlotView.menuForEvent_.callable(view, Mock())
            labels = [call.args[1] for call in view._plot_context_item.call_args_list]
            self.assertEqual("Start Slide Show Here" in labels, delegate is not None and selected is not None)

    def test_menu_selection_reuses_cursor_without_reparsing_track(self):
        point = SimpleNamespace(time=None)
        track = SimpleNamespace(points=Mock(side_effect=AssertionError("No track parsing while opening menu")))
        view = SimpleNamespace(controller=SimpleNamespace(selected_tracks=lambda: [track]),
                               context_target=("point", track, 123), cursor=(123, point, track))
        self.assertEqual(PlotView.slideshow_context_selection(view, resolve_point=False), (track, None))
        self.assertEqual(PlotView.slideshow_context_selection(view), (track, point))

    def test_workspace_selection_forwards_timestamp_or_untimed_fallback(self):
        track = SimpleNamespace(nr=12, hidden=False)
        workspace = SimpleNamespace(gpx_controller=SimpleNamespace(tracks=[track]), parameters={},
                                    startSlideShow_=Mock(), _journal=Mock())
        AdventureMapController.start_slideshow_at_track(workspace, track, SimpleNamespace(time=datetime.fromisoformat("2024-07-15T09:00:00Z")))
        self.assertEqual(workspace.startSlideShow_.call_args.args[0]["timestamp"], "2024-07-15T09:00:00+00:00")
        AdventureMapController.start_slideshow_at_track(workspace, track, SimpleNamespace(time=None))
        self.assertIsNone(workspace.startSlideShow_.call_args.args[0]["timestamp"])
        workspace._journal.assert_called_once()

    def test_selected_start_is_retained_when_initial_control_file_needs_creation(self):
        controller = SimpleNamespace(_control_file_path=lambda: None, _finish_slideshow_startup=Mock(),
                                     _start_assistant_control_assets_creation=Mock(), makePlots_=Mock())
        workspace = SimpleNamespace(pending_slideshow_track_start=self.selection.copy(),
            _set_background_progress=Mock(), _ensure_full_gui=Mock(return_value=controller), _journal=Mock(),
            _clear_background_progress=Mock(), _synchronize_control_file_from_full_gui=Mock())
        AdventureMapController.finishPendingStart_.callable(workspace, None)
        self.assertEqual(workspace.pending_slideshow_track_start, self.selection)
        controller._start_assistant_control_assets_creation.assert_called_once()
        controller.makePlots_.assert_not_called()

    def test_successful_initial_creation_resumes_the_pending_map_start(self):
        with TemporaryDirectory() as directory:
            control = Path(directory) / "Trip.lst"
            control.write_text("\n".join(self.lines))
            workspace = SimpleNamespace(full_gui_controller=SimpleNamespace(_control_file_path=lambda: control),
                pending_slideshow_track_start=self.selection.copy(), _load_control_rows=Mock(),
                saveWorkspace_=Mock(), _start_optional_enrichment=Mock(), demo_coordinator=None,
                performSelector_withObject_afterDelay_=Mock())
            AdventureMapController.controlAssetsCreationDidFinish_success_(workspace, True)
            workspace.performSelector_withObject_afterDelay_.assert_called_once_with("finishPendingStart:", None, 0.)
            self.assertEqual(workspace.pending_slideshow_track_start, self.selection)
