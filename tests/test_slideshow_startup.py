import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from slideshow_startup import (
    validate_startup, validation_signature, refresh_speed_metadata, options_signature,
    prepared_track_hint, file_signature,
)
from GPSTrackShowGUI import GPXTrackerController, slideshow_dependency_issues
from cocoa_adventure_map import AdventureMapController
from gpx_tracks_table import upgrade_timed_track_sidecars, prepare_with_options, execute_run_context


class StartupValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.gpx = self.directory / "test.gpx"
        self.control = self.directory / "test.lst"
        self.maps = self.directory / "maps"
        self.maps.mkdir()
        self.gpx.write_text("<gpx/>")
        self.control.write_text("#Overviewmap: overview.png\n")
        (self.maps / "overview.png").write_bytes(b"test image")
        (self.maps / "overview.json").write_text("{}")
        self.options = dict(gpx_running_speed_window_distance=100, gpx_stationary_speed_threshold=1,
                            plot_overview=False, plot_tracks=None, output_dir=str(self.maps))
        self.prepare = Mock(return_value={"tracks": []})
        self.execute = Mock()
        self.check = Mock(return_value={"maps": [], "derived": []})
        self.upgrade = Mock()
        self.progress = Mock()

    def run_validation(self, key=None, extra_assets=(), hint=None):
        return validate_startup(
            self.gpx, self.control, self.maps, self.options, previous_key=key, extra_assets=extra_assets,
            prepare=self.prepare, execute=self.execute, check=self.check,
            read_metadata=lambda path: json.loads(path.read_text()), upgrade=self.upgrade, progress=self.progress,
            prepared_hint=hint,
        )

    def make_hint(self):
        summary = self.maps / "summary.json"
        summary.write_text("{}")
        self.options.update(output_base="trip", adventure_processing_parameters={"smoothing": 10})
        context = dict(gpx_path=self.gpx, output_dir=self.maps, output_base="trip", table_json_path=summary,
                       args=SimpleNamespace(adventure_processing_parameters={"smoothing": 10}),
                       tracks=[dict(table_number=1, track_data_sidecar="1.json", track_data_fingerprint="data",
                                    track_geometry_fingerprint="geometry", point_records=[object()])])
        return prepared_track_hint(context, file_signature(self.gpx))

    def test_first_start_reuses_completed_adventure_data_without_point_arrays(self):
        hint = self.make_hint()
        self.assertNotIn("point_records", hint["context"]["tracks"][0])
        result = self.run_validation(hint=hint)
        self.assertTrue(result["reused_loaded_tracks"])
        self.assertIsNotNone(result["cache_key"])
        self.prepare.assert_not_called()
        self.execute.assert_not_called()

    def test_changed_processing_settings_reject_loaded_adventure_hint(self):
        hint = self.make_hint()
        self.options["adventure_processing_parameters"] = {"smoothing": 20}
        self.assertFalse(self.run_validation(hint=hint)["reused_loaded_tracks"])
        self.prepare.assert_called_once()

    def test_missing_derived_data_falls_back_to_full_preparation(self):
        hint = self.make_hint()
        self.check.side_effect = [dict(maps=[], derived=["missing"]), dict(maps=[], derived=[])]
        result = self.run_validation(hint=hint)
        self.assertFalse(result["reused_loaded_tracks"])
        self.prepare.assert_called_once()
        self.assertIsNotNone(result["cache_key"])

    def test_summary_changes_reject_loaded_adventure_hint(self):
        hint = self.make_hint()
        (self.maps / "summary.json").write_text('{"changed":true}')
        self.assertFalse(self.run_validation(hint=hint)["reused_loaded_tracks"])
        self.prepare.assert_called_once()

    def test_real_demo_derived_data_is_reused_without_reprocessing(self):
        source = (Path(__file__).resolve().parents[1]
                  / "demo/St-Jean-Pied-de-Port-to-Roncesvalles/1 - GPX Track"
                  / "St-Jean-Pied-de-Port-to-Roncesvalles.gpx")
        self.gpx.write_bytes(source.read_bytes())
        self.control.write_text("")
        self.options.update(output_base="trip", adventure_processing_parameters={"test": True},
                            nojson=False, pdf=False)
        signature = file_signature(self.gpx)
        context = prepare_with_options(str(self.gpx), **self.options)
        result = execute_run_context(context, print_table_output=False, write_summary=True)
        hint = prepared_track_hint(result, signature)
        self.check.side_effect = slideshow_dependency_issues
        validation = self.run_validation(hint=hint)
        self.assertTrue(validation["reused_loaded_tracks"])
        self.assertEqual(validation["issues"], {"maps": [], "derived": []})
        self.prepare.assert_not_called()
        self.execute.assert_not_called()
        self.assertTrue(self.run_validation(validation["cache_key"])["reused"])

    def test_second_unchanged_start_reuses_successful_validation(self):
        first = self.run_validation()
        self.assertFalse(first["reused"])
        self.assertIsNotNone(first["cache_key"])
        second = self.run_validation(first["cache_key"])
        self.assertTrue(second["reused"])
        self.prepare.assert_called_once()
        self.execute.assert_called_once()
        self.check.assert_called_once()
        self.assertIn("Track preparation", first["timings"])
        self.assertIn("Map validation", first["timings"])
        self.assertEqual(first["source_signatures"], second["source_signatures"])

    def test_gpx_control_maps_sidecars_and_settings_changes_invalidate(self):
        key = self.run_validation()["cache_key"]
        for path in (self.gpx, self.control, self.maps / "overview.png", self.maps / "overview.json"):
            with self.subTest(path=path.name):
                path.write_bytes(path.read_bytes() + b" ")
                result = self.run_validation(key)
                self.assertFalse(result["reused"])
                key = result["cache_key"]
        self.options["gpx_running_speed_window_distance"] = 200
        self.assertFalse(self.run_validation(key)["reused"])

    def test_added_deleted_and_external_map_files_invalidate(self):
        external = self.directory / "external.png"
        external.write_bytes(b"map")
        key = self.run_validation(extra_assets=[external])["cache_key"]
        external.write_bytes(b"changed map")
        result = self.run_validation(key, [external])
        self.assertFalse(result["reused"])
        (self.maps / "extra.json").write_text("{}")
        result = self.run_validation(result["cache_key"], [external])
        self.assertFalse(result["reused"])
        (self.maps / "overview.png").unlink()
        self.check.return_value = {"maps": ["overview.png"], "derived": []}
        result = self.run_validation(result["cache_key"], [external])
        self.assertFalse(result["reused"])
        self.assertIsNone(result["cache_key"])

    def test_preserving_mtime_does_not_hide_edit(self):
        before = validation_signature(self.gpx, self.control, self.maps, self.options)
        stat = self.gpx.stat()
        self.gpx.write_text("<gpx >")
        os.utime(self.gpx, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(before, validation_signature(self.gpx, self.control, self.maps, self.options))

    def test_source_or_map_changes_during_validation_require_retry(self):
        for path in (self.gpx, self.maps / "overview.png"):
            with self.subTest(path=path.name):
                def check(*_args):
                    path.write_bytes(path.read_bytes() + b" ")
                    return {"maps": [], "derived": []}
                self.check.side_effect = check
                result = self.run_validation()
                self.assertTrue(result["retry"])
                self.assertIsNone(result["cache_key"])

    def test_derived_data_failure_never_creates_cache_entry(self):
        self.check.return_value = {"maps": [], "derived": ["outdated track"]}
        self.assertIsNone(self.run_validation()["cache_key"])

    def test_errors_propagate_and_track_progress_is_reported(self):
        def prepare(_path, **options):
            options["track_processing_callback"](3, 126)
            raise RuntimeError("Cannot prepare track")
        self.prepare.side_effect = prepare
        with self.assertRaisesRegex(RuntimeError, "Cannot prepare"):
            self.run_validation()
        self.progress.assert_any_call("Checking tracks", 3, 126)
        self.execute.assert_not_called()

    def test_own_derived_data_writes_do_not_trigger_endless_retry(self):
        self.execute.side_effect = lambda *_args, **_kwargs: (self.maps / "derived.json").write_text("{}")
        result = self.run_validation()
        self.assertFalse(result["retry"])
        self.assertTrue(self.run_validation(result["cache_key"])["reused"])

    def test_speed_upgrade_uses_worker_options_and_no_output_dir_keyword(self):
        read = Mock(return_value={"track_fingerprint": "x", "timed_track_points": [], "running_speed": {}})
        refresh_speed_metadata(self.gpx, self.maps, self.options, read_metadata=read,
                               upgrade=self.upgrade, progress=self.progress)
        self.upgrade.assert_called_once()
        self.assertNotIn("output_dir", self.upgrade.call_args.kwargs)
        self.assertEqual(self.upgrade.call_args.args, (self.gpx, self.maps))

    def test_speed_upgrade_reuses_prepared_tracks_without_reparsing_gpx(self):
        context = {"tracks": []}
        with patch("gpx_tracks_table.prepare_with_options") as prepare:
            upgrade_timed_track_sidecars(self.gpx, self.maps, prepared_context=context)
        prepare.assert_not_called()
        read = Mock(return_value={"track_fingerprint": "x", "timed_track_points": []})
        refresh_speed_metadata(self.gpx, self.maps, self.options, context=context,
                               read_metadata=read, upgrade=self.upgrade, progress=self.progress)
        self.assertIs(self.upgrade.call_args.kwargs["prepared_context"], context)


class StartupFeedbackTests(unittest.TestCase):
    def controller(self):
        controller = SimpleNamespace(
            slideshow_startup_started_at=None, slideshow_dependency_preflight_generation=2,
            slideshow_dependency_preflight_running=True, adventure_map_host=Mock(),
            set_status=Mock(), progress_bar=Mock(), _log_slideshow_startup=Mock(),
        )
        controller._slideshow_startup_progress = lambda *args: GPXTrackerController._slideshow_startup_progress(controller, *args)
        return controller

    def test_progress_is_visible_in_map_and_finishing_clears_it(self):
        controller = self.controller()
        GPXTrackerController._slideshow_startup_progress(controller, "Checking tracks", 42, 126)
        controller.adventure_map_host._set_background_progress.assert_called_with("slideshow", "Checking tracks", 42, 126)
        controller.progress_bar.setIndeterminate_.assert_called_with(False)
        self.assertIsNotNone(controller.slideshow_startup_started_at)
        GPXTrackerController._finish_slideshow_startup(controller, "Slide show ready.")
        controller.adventure_map_host._clear_background_progress.assert_called_with("slideshow")
        self.assertIsNone(controller.slideshow_startup_started_at)

    def test_old_progress_does_not_overwrite_newer_request(self):
        controller = self.controller()
        GPXTrackerController.slideshowStartupProgress_.callable(controller, dict(generation=1, phase="Old", current=1, total=2))
        controller.set_status.assert_not_called()
        controller.slideshow_dependency_preflight_running = False
        GPXTrackerController.slideshowStartupProgress_.callable(controller, dict(generation=2, phase="Late", current=1, total=2))
        controller.set_status.assert_not_called()

    def test_map_displays_feedback_before_deferred_setup_and_blocks_double_start(self):
        host = SimpleNamespace(full_gui_controller=None, slideshow_launch_queued=False,
                               pending_start_after_processing=False, _set_background_progress=Mock(),
                               performSelector_withObject_afterDelay_=Mock(), _journal=Mock())
        AdventureMapController.startSlideShow_.callable(host, None)
        self.assertTrue(host.slideshow_launch_queued)
        host._set_background_progress.assert_called_once_with("slideshow", "Preparing slideshow…", 0, 0)
        host.performSelector_withObject_afterDelay_.assert_called_once_with("beginSlideShowPreparation:", None, 0.05)
        AdventureMapController.startSlideShow_.callable(host, None)
        self.assertEqual(host.performSelector_withObject_afterDelay_.call_count, 1)

    def test_failed_save_does_not_start(self):
        host = SimpleNamespace(slideshow_launch_queued=True, adventure_file=None, saveWorkspace_=Mock(return_value=False),
                               _clear_background_progress=Mock(), finishPendingStart_=Mock())
        AdventureMapController.beginSlideShowPreparation_.callable(host, None)
        self.assertFalse(host.slideshow_launch_queued)
        host._clear_background_progress.assert_called_once_with("slideshow")
        host.finishPendingStart_.assert_not_called()

    def test_live_player_state_finishes_startup_feedback(self):
        controller = SimpleNamespace(
            slideshow_process=SimpleNamespace(pid=12), slideshow_live_state_sequence=-1,
            slideshow_startup_started_at=1.0, slideshow_process_started_at=2.0,
            _log_slideshow_startup=Mock(), _finish_slideshow_startup=Mock(),
            adventure_map_host=Mock(), _apply_slideshow_live_state=Mock(),
        )
        state = {"sequence": 1, "phase": "intro_info"}
        GPXTrackerController.slideShowLiveStateUpdated_.callable(controller, {"pid": 12, "state": state})
        controller._finish_slideshow_startup.assert_called_once_with("Slide show ready.")
        self.assertIn("Player to first display", controller._log_slideshow_startup.call_args.args[0])

    def test_process_launch_failure_clears_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            control = root / "show.lst"
            control.write_text("")
            controller = SimpleNamespace(
                slideshow_dependency_preflight_running=False, pending_slideshow_after_map_generation=None,
                _resolve_project_directory=Mock(return_value=root), _control_file_path=lambda: control,
                slideshow_process=None, _track_images_dir=lambda: root,
                _slideshow_startup_progress=Mock(), _finish_slideshow_startup=Mock(),
                _slideshow_command=Mock(return_value=["unused-player"]), base_dir=root, set_status=Mock(),
            )
            with (patch("GPSTrackShowGUI.subprocess.Popen", side_effect=OSError("Launch failed")),
                  patch("GPSTrackShowGUI.is_app_store_build", return_value=False),
                  patch("GPSTrackShowGUI.show_alert")):
                GPXTrackerController._start_slide_show(controller, assets_preflighted=True)
            controller._finish_slideshow_startup.assert_called_once_with("Slide show failed to start.")

    def test_duplicate_preflight_start_is_not_dispatched(self):
        controller = SimpleNamespace(slideshow_dependency_preflight_running=True, set_status=Mock(),
                                     _resolve_project_directory=Mock())
        GPXTrackerController._start_slide_show(controller)
        controller._resolve_project_directory.assert_not_called()

    def test_background_status_waits_until_slideshow_startup_finishes(self):
        controller = SimpleNamespace(status_refresh_generation=1, slideshow_startup_started_at=1.0,
                                     project_status_processing_gate=Mock())
        controller.project_status_processing_gate.wait.return_value = True
        with patch("GPSTrackShowGUI.time.sleep", side_effect=lambda _delay: setattr(controller, "slideshow_startup_started_at", None)) as sleep:
            GPXTrackerController._wait_for_project_status_processing(controller, 1)
        sleep.assert_called_once_with(0.05)
        self.assertEqual(controller.project_status_processing_gate.wait.call_count, 2)

    def test_validation_error_and_changed_settings_do_not_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            gpx, control = root / "a.gpx", root / "a.lst"
            gpx.write_text("<gpx/>")
            control.write_text("")
            options = dict(plot_overview=False, plot_tracks=None)
            controller = SimpleNamespace(
                slideshow_dependency_preflight_generation=1, slideshow_dependency_preflight_running=True,
                _resolve_project_directory=Mock(return_value=root), _control_file_path=lambda: control,
                _current_single_gpx_path=lambda: gpx, _current_project_name=lambda: "a",
                _plot_common_options=Mock(return_value=dict(options)), _track_images_dir=lambda: root,
                _finish_slideshow_startup=Mock(), _log_slideshow_startup=Mock(), set_status=Mock(),
                _start_slideshow_dependency_preflight=Mock(), _start_slide_show=Mock(),
            )
            request = dict(project_dir=str(root.resolve()), control_file_path=str(control.resolve()),
                           gpx_path=str(gpx.resolve()), options_signature=options_signature(options))
            payload = dict(generation=1, request=request, error="Cannot read GPX", retry=False)
            with patch("GPSTrackShowGUI.show_alert"):
                GPXTrackerController.slideshowDependencyPreflightFinished_.callable(controller, payload)
            controller._finish_slideshow_startup.assert_called_once()
            controller._start_slide_show.assert_not_called()
            request["options_signature"] = "changed"
            GPXTrackerController.slideshowDependencyPreflightFinished_.callable(controller, payload)
            controller._start_slideshow_dependency_preflight.assert_called_once()


if __name__ == "__main__":
    unittest.main()
