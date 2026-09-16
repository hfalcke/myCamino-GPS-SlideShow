import tempfile
import unittest
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import (
    FILE_DRAG_TYPE,
    EditorTableDataSource,
    GPXEditorController,
    PlotView,
)
from cocoa_adventure_map import (
    AdventureMapAppDelegate,
    AdventureMapController,
    adventure_autosave_delays,
)
from GPSTrackShowGUI import (
    GPXTrackerController,
    SLIDESHOW_CHECKPOINT_VERSION,
    append_slideshow_checkpoint,
    build_argument_parser,
    ensure_default_project_playlist,
    ensure_project_audio_directory,
    playlist_belongs_to_audio_directory,
    normalize_slideshow_resume_history,
    resolve_gui_startup_paths,
    slideshow_dependency_issues,
    slideshow_settings_command_payload,
    style_control_table_cell,
    missing_control_map_assets,
    validated_slideshow_resume_position,
)


class GUIStartupPathTests(unittest.TestCase):
    def test_deleted_workspace_tracks_clear_old_extent_and_refit(self):
        plot_view = object()
        controller = SimpleNamespace(
            track_viewports={2: {"extent": "old"}, 3: {"extent": "keep"}},
            track_media_identity_cache={"old": "value"},
            overview_viewport={"extent": "old overview"},
            focused_track_nr=2,
            selected_track_identity="2",
            plot_view=plot_view,
            show_workspace_overview=Mock(),
        )

        AdventureMapController.workspace_tracks_deleted(controller, {2})

        self.assertNotIn(2, controller.track_viewports)
        self.assertIn(3, controller.track_viewports)
        self.assertEqual(controller.track_media_identity_cache, {})
        self.assertIsNone(controller.overview_viewport)
        self.assertIsNone(controller.focused_track_nr)
        controller.show_workspace_overview.assert_called_once_with(
            remember_current=False
        )

    def test_route_timing_offer_uses_all_project_media_after_new_metadata(self):
        first = Path("/tmp/first.jpg")
        second = Path("/tmp/second.jpg")
        track = SimpleNamespace(nr=4)
        prepared = [
            SimpleNamespace(path=first),
            SimpleNamespace(path=second),
        ]
        gpx_controller = SimpleNamespace(
            tracks=[track],
            offer_route_timing_from_prepared=Mock(return_value=True),
        )
        controller = SimpleNamespace(
            pending_route_timing_media={first},
            pending_route_timing_track_nr=4,
            gpx_controller=gpx_controller,
            _journal=Mock(),
            _project_media_paths=Mock(return_value=[first, second]),
            _prepare_route_timing_offer=Mock(),
        )

        AdventureMapController.offerPendingRouteTiming_.callable(
            controller, ([first, second], prepared)
        )

        controller._prepare_route_timing_offer.assert_called_once_with(
            4, [first, second], confirmation_required=False
        )
        self.assertEqual(controller.pending_route_timing_media, set())
        self.assertEqual(controller.pending_route_timing_track_nr, 4)

    def test_untimed_prompt_suppression_is_owned_by_one_adventure(self):
        first = SimpleNamespace(
            untimed_prompt_suppressions=set(),
            adventureComponentDidChange_=Mock(),
        )
        second = SimpleNamespace(
            untimed_prompt_suppressions=set(),
            adventureComponentDidChange_=Mock(),
        )

        AdventureMapController.remember_untimed_prompt_suppressions(
            first, ["same-route"]
        )

        self.assertEqual(first.untimed_prompt_suppressions, {"same-route"})
        self.assertEqual(second.untimed_prompt_suppressions, set())
        first.adventureComponentDidChange_.assert_called_once_with("adventure")

    def test_workspace_drop_is_deferred_until_drag_session_finishes(self):
        workspace = SimpleNamespace(
            performWorkspaceDrop_=Mock(),
            performSelector_withObject_afterDelay_=Mock(),
        )
        pasteboard = SimpleNamespace(
            propertyListForType_=Mock(return_value=["/tmp/route.gpx"])
        )
        sender = SimpleNamespace(draggingPasteboard=lambda: pasteboard)
        view = SimpleNamespace(adventure_workspace_delegate=workspace, mode="overview")

        self.assertTrue(PlotView.performDragOperation_.callable(view, sender))

        pasteboard.propertyListForType_.assert_called_once_with(FILE_DRAG_TYPE)
        workspace.performWorkspaceDrop_.assert_not_called()
        workspace.performSelector_withObject_afterDelay_.assert_called_once_with(
            "performWorkspaceDrop:", ["/tmp/route.gpx"], 0.0
        )

    def test_standalone_map_defers_gpx_loading_until_drag_finishes(self):
        controller = SimpleNamespace(
            load_gpx_paths=Mock(),
            performSelector_withObject_afterDelay_=Mock(),
        )
        pasteboard = SimpleNamespace(
            propertyListForType_=Mock(return_value=["/tmp/route.gpx"])
        )
        sender = SimpleNamespace(draggingPasteboard=lambda: pasteboard)
        view = SimpleNamespace(
            adventure_workspace_delegate=None,
            mode="track",
            controller=controller,
        )

        self.assertTrue(PlotView.performDragOperation_.callable(view, sender))

        controller.load_gpx_paths.assert_not_called()
        controller.performSelector_withObject_afterDelay_.assert_called_once_with(
            "loadDroppedGPXPaths:",
            [str(Path("/tmp/route.gpx").resolve(strict=False))],
            0.0,
        )

    def test_track_table_defers_media_dialog_until_drop_finishes(self):
        track = SimpleNamespace(nr=7)
        controller = SimpleNamespace(
            tracks=[track],
            selected_nrs=[],
            media_context={},
            highlight_selected_rows=Mock(),
            update_selection_field=Mock(),
            addTracksFromPhotos_=Mock(),
            performSelector_withObject_afterDelay_=Mock(),
        )
        pasteboard = SimpleNamespace(
            propertyListForType_=Mock(return_value=["/tmp/photo.jpg"]),
            stringForType_=Mock(return_value=""),
        )
        info = SimpleNamespace(draggingPasteboard=lambda: pasteboard)
        data_source = SimpleNamespace(controller=controller)

        accepted = EditorTableDataSource.tableView_acceptDrop_row_dropOperation_.callable(
            data_source, None, info, 0, 0
        )

        self.assertTrue(accepted)
        self.assertEqual(controller.selected_nrs, [7])
        controller.addTracksFromPhotos_.assert_not_called()
        controller.performSelector_withObject_afterDelay_.assert_called_once_with(
            "addTracksFromPhotos:", None, 0.0
        )

    def test_adventure_autosave_uses_recovery_then_real_file(self):
        self.assertEqual(adventure_autosave_delays(True), (1.0, 2.0))
        self.assertEqual(adventure_autosave_delays(False), (1.0, None))

    def test_control_map_validation_reports_unavailable_maps(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "trackimages").mkdir()
            (root / "trackimages" / "available.png").write_bytes(b"png")
            (root / "trackimages" / "available-timelapse.png").write_bytes(b"png")
            missing = missing_control_map_assets(
                "#Overviewmap: missing.png\n#Map: available.png\nphoto.jpg | 12:00\n",
                root / "trackimages",
            )
            self.assertEqual(missing, ["missing.png"])

    def test_slideshow_dependencies_reject_existing_maps_for_old_geometry(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            maps = Path(temporary_directory)
            context = {
                "overview_path": str(maps / "Trip.png"),
                "tracks": [
                    {
                        "table_number": 1,
                        "track_geometry_fingerprint": "new-geometry",
                        "track_data_fingerprint": "new-data",
                        "track_plot_image_filename": "0001_Stage_Trip.png",
                        "track_plot_time_lapse_image_filename": "0001_Stage_Trip-timelapse.png",
                        "track_data_sidecar": "Trip-trackdata/0001.json",
                    }
                ],
            }
            for name in (
                "Trip.png",
                "0001_Stage_Trip.png",
                "0001_Stage_Trip-timelapse.png",
            ):
                (maps / name).write_bytes(b"png")
                (maps / name).with_suffix(".json").write_text(
                    json.dumps(
                        {
                            "track_geometry_fingerprint": "old-geometry",
                            "source_track_geometry_fingerprints": ["old-geometry"],
                        }
                    ),
                    encoding="utf-8",
                )
            derived = maps / "Trip-trackdata" / "0001.json"
            derived.parent.mkdir()
            derived.write_text(
                json.dumps({"track_data_fingerprint": "old-data"}),
                encoding="utf-8",
            )

            issues = slideshow_dependency_issues(
                "#Overviewmap: Trip.png\n#Map: 0001_Stage_Trip.png\n",
                maps,
                context,
            )

            self.assertEqual(
                issues["maps"],
                [
                    "Trip.png",
                    "0001_Stage_Trip.png",
                    "0001_Stage_Trip-timelapse.png",
                ],
            )
            self.assertEqual(issues["derived"], ["Trip-trackdata/0001.json"])

    def test_slideshow_dependencies_accept_current_geometry_despite_render_settings(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            maps = Path(temporary_directory)
            context = {
                "overview_path": str(maps / "Trip.png"),
                "tracks": [
                    {
                        "table_number": 1,
                        "track_geometry_fingerprint": "geometry",
                        "track_data_fingerprint": "data",
                        "track_plot_image_filename": "0001_Stage_Trip.png",
                        "track_plot_time_lapse_image_filename": "0001_Stage_Trip-timelapse.png",
                        "track_data_sidecar": "Trip-trackdata/0001.json",
                    }
                ],
            }
            metadata_by_name = {
                "Trip.png": {"source_track_geometry_fingerprints": ["geometry"]},
                "0001_Stage_Trip.png": {"track_geometry_fingerprint": "geometry"},
                "0001_Stage_Trip-timelapse.png": {"track_geometry_fingerprint": "geometry"},
            }
            for name, metadata in metadata_by_name.items():
                (maps / name).write_bytes(b"png")
                metadata["adventure_render_parameters"] = {"maps.output_provider": "old"}
                (maps / name).with_suffix(".json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
            derived = maps / "Trip-trackdata" / "0001.json"
            derived.parent.mkdir()
            derived.write_text(
                json.dumps({"track_data_fingerprint": "data"}), encoding="utf-8"
            )

            issues = slideshow_dependency_issues(
                "#Overviewmap: Trip.png\n#Map: 0001_Stage_Trip.png\n",
                maps,
                context,
            )

            self.assertEqual(issues, {"maps": [], "derived": []})

    def test_slideshow_missing_maps_start_automatic_hosted_repair(self):
        class RunningThread:
            @staticmethod
            def is_alive():
                return True

        controller = SimpleNamespace(
            parameters={"maps.output_provider": "geoapify"},
            pending_slideshow_after_map_generation=None,
            pending_map_provider_action=None,
            geolocations_thread=None,
            set_status=Mock(),
            startAutomaticMapGeneration_=Mock(),
        )
        controller.startAutomaticMapGeneration_.side_effect = lambda _paths: setattr(
            controller, "geolocations_thread", RunningThread()
        )

        GPXTrackerController._prepare_maps_then_start_slideshow(
            controller,
            ["overview.png", "stage.png"],
            continue_previous=True,
            transient_resume_position={"row": 8},
        )

        controller.startAutomaticMapGeneration_.assert_called_once_with([])
        self.assertTrue(
            controller.pending_slideshow_after_map_generation["continue_previous"]
        )
        self.assertEqual(
            controller.pending_slideshow_after_map_generation[
                "transient_resume_position"
            ],
            {"row": 8},
        )

    def test_slideshow_missing_maps_opens_osm_map_workflow(self):
        controller = SimpleNamespace(
            parameters={"maps.output_provider": "osm"},
            pending_slideshow_after_map_generation=None,
            set_status=Mock(),
            makePlots_=Mock(),
        )

        GPXTrackerController._prepare_maps_then_start_slideshow(
            controller,
            ["stage.png"],
        )

        controller.makePlots_.assert_called_once_with(None)
        self.assertIsNotNone(controller.pending_slideshow_after_map_generation)

    def test_large_slideshow_map_repair_can_be_declined(self):
        controller = SimpleNamespace(
            parameters={"maps.output_provider": "geoapify"},
            pending_slideshow_after_map_generation=None,
            set_status=Mock(),
            startAutomaticMapGeneration_=Mock(),
        )

        with patch("GPSTrackShowGUI.confirm_alert", return_value=False):
            GPXTrackerController._prepare_maps_then_start_slideshow(
                controller,
                [f"map-{index}.png" for index in range(7)],
            )

        controller.startAutomaticMapGeneration_.assert_not_called()
        self.assertIsNone(controller.pending_slideshow_after_map_generation)

    def test_slideshow_request_resumes_with_original_arguments_after_maps(self):
        request = {
            "time_lapse": False,
            "continue_previous": True,
            "transient_resume_position": {"row": 12},
        }
        controller = SimpleNamespace(
            pending_slideshow_after_map_generation=request,
            _start_slide_show=Mock(),
        )

        GPXTrackerController.resumeSlideShowAfterMaps_.callable(controller, None)

        self.assertIsNone(controller.pending_slideshow_after_map_generation)
        controller._start_slide_show.assert_called_once_with(
            time_lapse=False,
            continue_previous=True,
            transient_resume_position={"row": 12},
        )

    def test_adventure_map_waits_for_weather_decision_before_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            media = Path(temporary_directory) / "photo.jpg"
            media.write_bytes(b"photo")
            controller = SimpleNamespace(
                pending_media=set(),
                processing_active=False,
                _metadata_creation_requires_weather_choice=Mock(return_value=True),
                _request_initial_weather_choice=Mock(return_value=False),
                _start_next_media_batch=Mock(),
            )

            AdventureMapController.queue_media_processing(controller, [media])

            self.assertEqual(controller.pending_media, set())
            controller._start_next_media_batch.assert_not_called()

    def test_media_index_and_watcher_wait_for_staged_track_loading(self):
        controller = SimpleNamespace(
            startup_track_loading=True,
            startup_media_scan_deferred=False,
            startup_watcher_deferred=False,
            _stop_media_watcher=Mock(),
        )

        AdventureMapController._scan_project_media(controller)
        AdventureMapController._start_media_watcher(controller)

        self.assertTrue(controller.startup_media_scan_deferred)
        self.assertTrue(controller.startup_watcher_deferred)
        controller._stop_media_watcher.assert_not_called()

    def test_lower_priority_startup_work_begins_after_media_index(self):
        media = Path("new-photo.jpg")
        controller = SimpleNamespace(
            startup_pending_media_paths={media},
            startup_watcher_deferred=True,
            pending_control_media=set(),
            processing_active=False,
            _queue_derived_track_data=Mock(),
            queue_media_processing=Mock(),
            _start_media_watcher=Mock(),
        )

        AdventureMapController._finish_prioritized_startup_work(controller)

        controller._queue_derived_track_data.assert_called_once_with()
        controller.queue_media_processing.assert_called_once_with([media])
        controller._start_media_watcher.assert_called_once_with()
        self.assertFalse(controller.startup_watcher_deferred)

    def test_adventure_map_does_not_terminate_while_recovery_prompt_closes(self):
        delegate = AdventureMapAppDelegate.alloc().initWithProjectDirectory_projectFile_(
            None,
            None,
        )
        delegate.controller = SimpleNamespace(startup_in_progress=True)
        self.assertFalse(delegate.applicationShouldTerminateAfterLastWindowClosed_(None))

        delegate.controller.startup_in_progress = False
        self.assertTrue(delegate.applicationShouldTerminateAfterLastWindowClosed_(None))

    def test_empty_placeholder_track_does_not_create_recovery(self):
        controller = SimpleNamespace(
            gpx_controller=SimpleNamespace(
                tracks=[SimpleNamespace(element=ET.fromstring("<trk><trkseg /></trk>"))],
                dirty=True,
            ),
            control_rows=[],
            pending_media=set(),
            pending_control_media=set(),
            recovery_dirty=False,
            control_file=None,
            _saved_gpx_contains_points=Mock(return_value=False),
        )
        self.assertFalse(
            AdventureMapController._has_recoverable_workspace_content(controller)
        )

    def test_track_points_are_meaningful_recovery_content(self):
        controller = SimpleNamespace(
            gpx_controller=SimpleNamespace(
                tracks=[SimpleNamespace(element=ET.fromstring("<trk><trkseg><trkpt /></trkseg></trk>"))],
                dirty=True,
            ),
            control_rows=[],
            pending_media=set(),
            pending_control_media=set(),
            recovery_dirty=False,
            control_file=None,
            _saved_gpx_contains_points=Mock(return_value=False),
        )
        self.assertTrue(
            AdventureMapController._has_recoverable_workspace_content(controller)
        )

    def test_workspace_track_inspection_bypasses_legacy_map_window(self):
        expected_inspector = object()
        workspace = SimpleNamespace(
            inspect_workspace_track=Mock(return_value=expected_inspector)
        )
        source_view = SimpleNamespace(adventure_workspace_delegate=workspace)
        controller = SimpleNamespace()
        track = object()

        result = GPXEditorController.open_track_workflow_at_point(
            controller,
            track,
            17,
            source_view=source_view,
        )

        self.assertIs(result, expected_inspector)
        workspace.inspect_workspace_track.assert_called_once_with(track, 17)

    def test_full_gui_flag_is_explicit_opt_out_of_map_first_startup(self):
        args = build_argument_parser().parse_args(["--full-gui"])
        self.assertTrue(args.full_gui)

    def test_control_table_does_not_apply_text_style_to_preview_image_cell(self):
        class ImageCell:
            def respondsToSelector_(self, selector):
                return False

            def setFont_(self, _font):
                self.fail("Image preview cell must not receive text font styling")

            def setTextColor_(self, _color):
                self.fail("Image preview cell must not receive text color styling")

            def fail(self, message):
                raise AssertionError(message)

        self.assertFalse(style_control_table_cell(ImageCell(), "IMG", False))

    def test_view_map_index_uses_saved_references_without_gpx_processing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project = Path(temporary_directory)
            output = project / "trackimages"
            output.mkdir()
            overview = output / "Trip.png"
            standard = output / "0001_Stage_Trip.png"
            time_lapse = output / "0001_Stage_Trip-timelapse.png"
            for path in (overview, standard, time_lapse):
                path.write_bytes(b"png")
            control = project / "Trip-sorted.lst"
            control.write_text(
                "#Overviewmap: Trip.png\n#Map: 0001_Stage_Trip.png\n",
                encoding="utf-8",
            )
            controller = SimpleNamespace(
                track_map_base="Trip",
                _track_images_dir=lambda: output,
                _current_project_name=lambda: "Trip",
                _control_file_path=lambda: control,
                _tracks_summary_json_path=lambda: output / "Trip-summary.json",
            )

            paths = GPXTrackerController._existing_project_map_paths(controller)

        self.assertEqual(
            paths,
            [path.resolve(strict=False) for path in (overview, standard, time_lapse)],
        )

    def test_speed_sidecar_upgrade_does_not_pass_output_directory_twice(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            gpx_path = directory / "trip.gpx"
            gpx_path.write_text("<gpx/>", encoding="utf-8")
            (directory / "track.json").write_text(
                json.dumps({"track_fingerprint": "one", "timed_track_points": []}),
                encoding="utf-8",
            )
            statuses = []
            controller = SimpleNamespace(
                parameters={
                    "gpx.running_speed_window_distance_m": 100.0,
                    "gpx.stationary_speed_threshold_kmh": 1.5,
                },
                _current_single_gpx_path=lambda: gpx_path,
                _current_project_name=lambda: "Trip",
                _plot_common_options=lambda *_args: {
                    "output_dir": str(directory),
                    "gpx_running_speed_window_distance": 100.0,
                },
                set_status=statuses.append,
            )
            with patch(
                "GPSTrackShowGUI.upgrade_timed_track_sidecars",
                return_value={"updated": [1], "current": [], "skipped": []},
            ) as upgrade:
                result = GPXTrackerController._ensure_running_speed_sidecars_current(
                    controller,
                    directory,
                )
        self.assertTrue(result)
        self.assertNotIn("output_dir", upgrade.call_args.kwargs)
        self.assertEqual(upgrade.call_args.args[1], directory)

    def test_live_settings_payload_contains_only_changed_values(self):
        payload = slideshow_settings_command_payload(
            {
                "slideshow.header_track_stats": True,
                "slideshow.header_background": "transparent",
                "slideshow.font_size": 30,
            },
            {
                "slideshow.header_track_stats",
                "slideshow.header_background",
            },
            42,
        )
        self.assertEqual(payload["command"], "settings")
        self.assertEqual(payload["sequence"], 42)
        self.assertFalse(payload["restore_display"])
        self.assertEqual(
            payload["values"],
            {
                "slideshow.header_background": "transparent",
                "slideshow.header_track_stats": True,
            },
        )

    def test_live_settings_can_acknowledge_display_restore_without_changes(self):
        payload = slideshow_settings_command_payload(
            {"slideshow.font_size": 30},
            set(),
            43,
            restore_display=True,
        )
        self.assertEqual(payload["values"], {})
        self.assertTrue(payload["restore_display"])

    def test_resume_history_accepts_only_current_checkpoint_format(self):
        current = {"version": SLIDESHOW_CHECKPOINT_VERSION, "completed": False, "playlist_index": 4}
        self.assertEqual(
            normalize_slideshow_resume_history(
                [
                    current,
                    {"version": 2, "playlist_index": 3},
                    {"version": SLIDESHOW_CHECKPOINT_VERSION, "completed": True},
                ]
            ),
            [current],
        )
        self.assertEqual(
            normalize_slideshow_resume_history(
                {"version": 2, "playlist_index": 3}
            ),
            [],
        )

    def test_resume_history_keeps_latest_twenty(self):
        history = []
        for index in range(25):
            history = append_slideshow_checkpoint(
                history,
                {
                    "version": SLIDESHOW_CHECKPOINT_VERSION,
                    "completed": False,
                    "playlist_index": index,
                },
            )
        self.assertEqual(len(history), 20)
        self.assertEqual(history[0]["playlist_index"], 24)
        self.assertEqual(history[-1]["playlist_index"], 5)
        self.assertEqual(
            append_slideshow_checkpoint(
                history,
                {"version": SLIDESHOW_CHECKPOINT_VERSION, "completed": True},
            ),
            history,
        )

    def test_resume_position_requires_unchanged_control_file_identity(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            control_file = Path(temporary_directory) / "Trip-sorted.lst"
            control_file.write_text("photo.jpeg | 12:00 | - | -\n", encoding="utf-8")
            stat = control_file.stat()
            checkpoint = {
                "version": SLIDESHOW_CHECKPOINT_VERSION,
                "completed": False,
                "control_file": str(control_file),
                "control_file_identity": {
                    "path": str(control_file),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                },
                "playlist_index": 0,
                "line_text": "photo.jpeg | 12:00 | - | -",
            }
            self.assertEqual(
                validated_slideshow_resume_position(
                    checkpoint,
                    control_file,
                ),
                checkpoint,
            )
            control_file.write_text(
                "photo.jpeg | 12:00 | - | changed\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                validated_slideshow_resume_position(
                    checkpoint,
                    control_file,
                )
            )

    def test_project_audio_directory_is_created_and_recreated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project = Path(temporary_directory)
            audio_dir = ensure_project_audio_directory(project)
            self.assertEqual(audio_dir, (project / "audio").resolve())
            self.assertTrue(audio_dir.is_dir())
            audio_dir.rmdir()
            self.assertTrue(ensure_project_audio_directory(project).is_dir())

    def test_playlist_must_be_directly_inside_project_audio_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            audio_dir = ensure_project_audio_directory(temporary_directory)
            playlist = audio_dir / "Morning.playlist"
            nested = audio_dir / "Album" / "Nested.playlist"
            wrong_type = audio_dir / "Morning.txt"
            self.assertTrue(
                playlist_belongs_to_audio_directory(playlist, audio_dir)
            )
            self.assertFalse(
                playlist_belongs_to_audio_directory(nested, audio_dir)
            )
            self.assertFalse(
                playlist_belongs_to_audio_directory(wrong_type, audio_dir)
            )

    def test_default_empty_playlist_uses_adventure_name_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            playlist = ensure_default_project_playlist(
                temporary_directory,
                "Test Reise",
            )
            self.assertEqual(playlist.name, "Test Reise.playlist")
            self.assertEqual(playlist.read_text(encoding="utf-8"), "")

            playlist.write_text("$START\nsong.mp3\n", encoding="utf-8")
            self.assertEqual(
                ensure_default_project_playlist(
                    temporary_directory,
                    "Test Reise",
                ).read_text(encoding="utf-8"),
                "$START\nsong.mp3\n",
            )

    def test_status_update_does_not_reenter_the_appkit_event_loop(self):
        class StatusLabel:
            def __init__(self):
                self.value = None
                self.needs_display = False

            def setStringValue_(self, value):
                self.value = value

            def setNeedsDisplay_(self, value):
                self.needs_display = bool(value)

        controller = type("ControllerStub", (), {})()
        controller.status_label = StatusLabel()

        GPXTrackerController.set_status(controller, "Starting")

        self.assertEqual(controller.status_label.value, "Starting")
        self.assertTrue(controller.status_label.needs_display)

    def test_resume_chooser_is_hidden_but_retained_after_modal_use(self):
        class Window:
            def __init__(self):
                self.hidden = False

            def orderOut_(self, _sender):
                self.hidden = True

        controller = type("ControllerStub", (), {})()
        controller.resume_selection_window = Window()
        controller.resume_selection_table = object()
        controller.resume_selection_data_source = object()
        controller.resume_selection_delegate = object()
        controller.resume_selection_play_button = object()

        GPXTrackerController._cleanup_resume_selection_window(controller)

        self.assertTrue(controller.resume_selection_window.hidden)
        self.assertIsNotNone(controller.resume_selection_table)
        self.assertIsNotNone(controller.resume_selection_data_source)
        self.assertIsNotNone(controller.resume_selection_delegate)
        self.assertIsNotNone(controller.resume_selection_play_button)

    def test_positional_directory_becomes_project_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            args = build_argument_parser().parse_args([str(directory)])
            project_dir, adventure_file = resolve_gui_startup_paths(
                args.project_directory,
                args.startup_path,
            )

            self.assertEqual(project_dir, directory.resolve())
            self.assertIsNone(adventure_file)

    def test_positional_adventure_remains_an_adventure_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            adventure = Path(temporary_directory) / "Trip.adv"
            project_dir, adventure_file = resolve_gui_startup_paths(None, adventure)

            self.assertIsNone(project_dir)
            self.assertEqual(adventure_file, adventure.resolve())

    def test_conflicting_directory_arguments_are_rejected(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with self.assertRaises(ValueError):
                resolve_gui_startup_paths(first, second)


if __name__ == "__main__":
    unittest.main()
