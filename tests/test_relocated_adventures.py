import json
import shutil
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adventure_files import (AdventureFormatError, discover_adventure_candidates, load_adventure,
                             open_adventure)
from cocoa_adventure_chooser import choose_adventure
from cocoa_adventure_map import AdventureMapController
from GPSTrackShowGUI import GPXTrackerController
from json_storage import atomic_write_json
from security_scoped_access import temporary_directory_access


def payload(root, name="Trip"):
    return dict(adventure_format_version=2, project_name=name, project_directory=str(root),
        gpx_file="", control_file="authored.lst", track_map_base="original-map-family",
        parameters={"version": 1, "values": {}}, description="Keep this description",
        title_image=str(root / "photo.jpg"), music_source="audio", music_playlist="audio/music.playlist",
        narration_playlist="narration/narration.playlist",
        slideshow_resume_position={"playlist_index": 12, "source": str(root / "authored.lst")},
        slideshow_resume_history=[{"version": 4, "playlist_index": 11}],
        gpx_identity={"version": 1, "source": {"path": str(root / "original.gpx"), "sha256": "keep-sha"}},
        extra={"outside": "/Volumes/Archive/music.mp3", "relative": "photo.jpg"})


def write_adventure(root, name="Trip", previous=None):
    root.mkdir(parents=True, exist_ok=True)
    data = payload(previous or root, name)
    path = root / f"{name}.adv"
    atomic_write_json(path, data)
    return path, data


class RelocatedAdventureFilesTests(unittest.TestCase):
    def test_moved_folder_opens_existing_file_preserving_every_other_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old = base / "old" / "Trip"
            source, original = write_adventure(old)
            assets = {"authored.lst": b"#Map: saved-map.png\n", "photo.jpg.json": b'{"weather":{"temperature":18}}',
                      "authored.lst.mycamino-state.json": b'{"excluded":["photo.jpg"]}',
                      "saved-map.png": b"cached-map", "original.gpx": b"<gpx/>"}
            for name, content in assets.items():
                (old / name).write_bytes(content)
            new = base / "new" / "Renamed folder"
            new.parent.mkdir()
            shutil.move(old, new)
            path = new / source.name
            before = path.read_bytes()
            names = {item.name for item in new.iterdir()}
            records, relocated, errors = discover_adventure_candidates(new)
            self.assertEqual([record.path for record in records], [path.resolve()])
            self.assertEqual(len(relocated), 1)
            self.assertFalse(errors)
            self.assertEqual(path.read_bytes(), before)
            with patch("adventure_files._atomic_create_json", side_effect=AssertionError("Must not create an Adventure")):
                record = open_adventure(path)
            self.assertEqual(record.path, path.resolve())
            self.assertEqual(record.project_name, "Trip")
            self.assertEqual(record.payload["project_directory"], str(new.resolve()))
            self.assertEqual(record.payload["title_image"], str(new.resolve() / "photo.jpg"))
            self.assertEqual(record.payload["slideshow_resume_position"]["playlist_index"], 12)
            self.assertEqual(record.payload["slideshow_resume_history"], original["slideshow_resume_history"])
            for key in ("parameters", "description", "control_file", "track_map_base", "extra"):
                self.assertEqual(record.payload[key], original[key])
            self.assertEqual(record.payload["gpx_identity"]["source"]["sha256"], "keep-sha")
            self.assertEqual({item.name for item in new.iterdir()}, names)
            for name, content in assets.items():
                self.assertEqual((new / name).read_bytes(), content)
            self.assertFalse(old.exists())
            self.assertEqual(load_adventure(path).payload, record.payload)

    def test_copy_opens_in_place_even_while_original_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old = base / "old" / "Trip"
            source, original = write_adventure(old)
            new = base / "new" / "Trip"
            shutil.copytree(old, new)
            record = open_adventure(new / source.name)
            self.assertEqual(record.payload["project_directory"], str(new.resolve()))
            self.assertEqual(json.loads(source.read_text()), original)
            self.assertEqual(record.payload["media_identity_source_project"], str(old.resolve()))

    def test_existing_identity_provenance_is_not_rebased(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, data = write_adventure(root / "new", previous=root / "old")
            data["media_identity_source_project"] = str(root / "old")
            atomic_write_json(path, data)
            self.assertEqual(open_adventure(path).payload["media_identity_source_project"], str(root / "old"))

    def test_discovery_is_read_only_and_merges_local_and_relocated_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, _ = write_adventure(root, "Local")
            second, _ = write_adventure(root, "Relocated", previous=root / "old")
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (first, second)}
            for _ in range(2):
                records, relocated, errors = discover_adventure_candidates(root)
                self.assertEqual({record.path for record in records}, {first.resolve(), second.resolve()})
                self.assertEqual([record.path for record in relocated], [second.resolve()])
                self.assertFalse(errors)
            self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (first, second)})

    def test_same_location_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path, data = write_adventure(Path(directory))
            with patch("adventure_files.atomic_write_json", side_effect=AssertionError("No repair needed")):
                self.assertEqual(open_adventure(path).payload, data)

    def test_failed_atomic_install_keeps_original_and_returns_rebased_memory_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root / "new", previous=root / "old")
            before = path.read_bytes()
            with patch("json_storage.os.replace", side_effect=OSError("disk unavailable")):
                record = open_adventure(path)
            self.assertIn("disk unavailable", record.location_save_error)
            self.assertEqual(record.payload["project_directory"], str(path.parent.resolve()))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            self.assertFalse(open_adventure(path).location_save_error)

    def test_read_only_file_opens_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root / "new", previous=root / "old")
            before = path.read_bytes()
            path.chmod(0o444)
            record = open_adventure(path)
            self.assertIn("read-only", record.location_save_error)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(record.payload["project_directory"], str(path.parent.resolve()))

    def test_other_format_errors_are_never_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, original = write_adventure(root / "new", previous=root / "old")
            invalid = [dict(original, project_directory="relative"), dict(original, adventure_format_version=1),
                       dict(original, parameters=[]), dict(original, project_name="Wrong filename"),
                       dict(original, gpx_file="../outside.gpx")]
            for data in invalid:
                with self.subTest(data=data):
                    atomic_write_json(path, data)
                    before = path.read_bytes()
                    with self.assertRaises(AdventureFormatError):
                        open_adventure(path)
                    self.assertEqual(path.read_bytes(), before)

    def test_missing_gpx_reference_is_retained_for_existing_recovery_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, data = write_adventure(root / "new", previous=root / "old")
            data["gpx_file"] = "missing.gpx"
            atomic_write_json(path, data)
            record = open_adventure(path)
            self.assertEqual(record.payload["gpx_file"], "missing.gpx")
            self.assertFalse((path.parent / "missing.gpx").exists())


class AdventureChooserTests(unittest.TestCase):
    def test_single_and_empty_discovery_require_no_dialog(self):
        record = SimpleNamespace(path=Path("/tmp/Trip.adv"))
        with patch("cocoa_adventure_chooser.NSAlert", side_effect=AssertionError("No chooser needed")):
            self.assertIsNone(choose_adventure([]))
            self.assertEqual(choose_adventure([record]), record.path)

    def test_multiple_always_offer_open_selected_and_show_names_and_files(self):
        records = [SimpleNamespace(project_name=name, path=Path(f"/tmp/{name}.adv")) for name in ("Trip", "Other")]
        with patch("cocoa_adventure_chooser.NSAlert") as alert, \
                patch("cocoa_adventure_chooser.NSPopUpButton") as popup, \
                patch("cocoa_adventure_chooser.run_foreground_modal_alert", return_value=1000):
            popup.alloc.return_value.initWithFrame_pullsDown_.return_value.indexOfSelectedItem.return_value = 1
            self.assertEqual(choose_adventure(records), records[1].path)
            labels = popup.alloc.return_value.initWithFrame_pullsDown_.return_value.addItemsWithTitles_.call_args.args[0]
            self.assertEqual(labels, ["Trip (Trip.adv)", "Other (Other.adv)"])
            buttons = [call.args[0] for call in alert.alloc.return_value.init.return_value.addButtonWithTitle_.call_args_list]
            self.assertEqual(buttons, ["Open Selected", "Cancel"])

    def test_cancel_leaves_selection_unopened(self):
        with patch("cocoa_adventure_chooser.NSAlert"), patch("cocoa_adventure_chooser.NSPopUpButton"), \
                patch("cocoa_adventure_chooser.run_foreground_modal_alert", return_value=1001):
            self.assertIsNone(choose_adventure([Mock(), Mock()]))


class AdventureOpenRoutingTests(unittest.TestCase):
    def map_controller(self):
        controller = SimpleNamespace(project_directory=Path("/tmp/previous-project"), adventure_file=None,
            project_access=SimpleNamespace(defaults=None, activate_directory=Mock(return_value=True)),
            workspace_dirty_components=set(), startup_pending_media_paths=set(),
            gpx_controller=SimpleNamespace(dirty=False, apply_project_parameters=Mock()),
            workspace_autosave_timer=None, workspace_autosave_poll_timer=None, workspace_autosave_future=None,
            track_viewports={}, auto_processed_paths=set(), mutation_journal=Mock(), journal=Mock())
        for name in ("_alert", "_flush_workspace_autosave", "_reset_optional_enrichment", "_stop_media_watcher",
                     "_retire_full_gui", "_prepare_gpx_document_context", "_remember_recent_adventure",
                     "_configure_workspace_autosave_timer", "_show_empty_world", "_load_control_rows",
                     "_scan_project_media", "_start_media_watcher", "_mark_adventure_component_changed"):
            setattr(controller, name, Mock())
        return controller

    def advanced_controller(self):
        controller = SimpleNamespace(current_project_file=None, parameters={}, audio_section_expanded=False,
            music_source="audio", music_playlist="audio/music.playlist", narration_playlist="narration/narration.playlist")
        for name in ("flush_adventure_autosave", "_stop_project_media_watcher", "_update_loaded_project_fields",
                     "_resize_main_window_for_audio_visibility", "_set_adventure_name_editing", "_remember_recent_adventure",
                     "_refresh_project_file_menus", "set_status", "reset_progress", "_refresh_loaded_gpx_summary",
                     "layout_window", "mark_clean", "mark_dirty", "_start_project_media_watcher"):
            setattr(controller, name, Mock())
        controller._stored_optional_project_path = lambda value: value
        return controller

    def test_map_direct_file_repairs_it_without_starting_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, data = write_adventure(root / "new", previous=root / "old")
            controller = self.map_controller()
            with patch("cocoa_adventure_map.temporary_directory_access", return_value=nullcontext(True)), \
                    patch("cocoa_adventure_map.choose_adventure", side_effect=AssertionError("Explicit file")):
                self.assertTrue(AdventureMapController.load_adventure_path(controller, path))
            self.assertEqual(controller.adventure_file, path.resolve())
            self.assertEqual(controller.adventure_name, "Trip")
            self.assertEqual(controller.adventure_payload["slideshow_resume_history"], data["slideshow_resume_history"])
            controller._remember_recent_adventure.assert_called_once_with(path.resolve())
            controller._mark_adventure_component_changed.assert_not_called()
            self.assertEqual(load_adventure(path).payload["project_directory"], str(path.parent.resolve()))

    def test_failed_location_save_stays_pending_in_both_interfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root / "new", previous=root / "old")
            before = path.read_bytes()
            controller = self.map_controller()
            with patch("cocoa_adventure_map.temporary_directory_access", return_value=nullcontext(True)), \
                    patch("adventure_files.atomic_write_json", side_effect=PermissionError("read-only")):
                self.assertTrue(AdventureMapController.load_adventure_path(controller, path))
            self.assertEqual(controller.project_directory, path.parent.resolve())
            controller._mark_adventure_component_changed.assert_called_once_with("adventure")
            self.assertIn("new location", controller._alert.call_args.args[0])
            self.assertEqual(path.read_bytes(), before)
            advanced = self.advanced_controller()
            with patch("adventure_files.atomic_write_json", side_effect=PermissionError("read-only")), \
                    patch("GPSTrackShowGUI.show_alert") as alert:
                self.assertTrue(GPXTrackerController.load_project_configuration(advanced, path))
            self.assertIsNone(advanced.saved_project_payload)
            advanced.mark_dirty.assert_called_once_with()
            advanced._remember_recent_adventure.assert_called_once_with(path.resolve())
            self.assertIn("new location", alert.call_args.args[0])
            self.assertEqual(path.read_bytes(), before)

    def test_old_save_failure_aborts_before_repair_or_job_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root / "new", previous=root / "old")
            before = path.read_bytes()
            controller = self.map_controller()
            controller.adventure_file = root / "active.adv"
            controller.workspace_dirty_components = {"gpx"}
            controller._flush_workspace_autosave.return_value = False
            with patch("cocoa_adventure_map.temporary_directory_access", return_value=nullcontext(True)):
                self.assertFalse(AdventureMapController.load_adventure_path(controller, path))
            self.assertEqual(path.read_bytes(), before)
            controller.project_access.activate_directory.assert_not_called()
            controller._stop_media_watcher.assert_not_called()
            controller._reset_optional_enrichment.assert_not_called()

    def test_pending_drop_is_not_imported_into_old_adventure_after_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root)
            controller = SimpleNamespace(_choose_adventure_or_project_path=Mock(return_value=root),
                load_project_directory=Mock(return_value=False), _dispatch_pending_workspace_drop=Mock())
            self.assertFalse(AdventureMapController._open_existing_adventure_for_pending_drop(
                controller, [root / "extra.gpx"], path))
            self.assertEqual(controller.pending_workspace_drop_after_adventure_load, [])
            controller._dispatch_pending_workspace_drop.assert_not_called()

    def test_map_folder_cancel_preserves_current_jobs_and_dirty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_adventure(root, "Trip", previous=root / "previous")
            write_adventure(root, "Other", previous=root / "previous")
            controller = SimpleNamespace(project_access=SimpleNamespace(defaults=None),
                project_directory=Path("/tmp/current"), workspace_dirty_components={"gpx"},
                _reset_optional_enrichment=Mock(), _stop_media_watcher=Mock(),
                _flush_workspace_autosave=Mock(), load_adventure_path=Mock())
            with patch("cocoa_adventure_map.temporary_directory_access", return_value=nullcontext(True)), \
                    patch("cocoa_adventure_map.choose_adventure", return_value=None) as choose:
                self.assertFalse(AdventureMapController.load_project_directory(controller, root))
            self.assertEqual(len(choose.call_args.args[0]), 2)
            controller._reset_optional_enrichment.assert_not_called()
            controller._stop_media_watcher.assert_not_called()
            controller._flush_workspace_autosave.assert_not_called()
            controller.load_adventure_path.assert_not_called()
            self.assertEqual(controller.workspace_dirty_components, {"gpx"})

    def test_map_folder_opens_selected_existing_adventure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root, previous=root / "previous")
            controller = SimpleNamespace(project_access=SimpleNamespace(defaults=None), load_adventure_path=Mock(return_value=True))
            with patch("cocoa_adventure_map.temporary_directory_access", return_value=nullcontext(True)):
                self.assertTrue(AdventureMapController.load_project_directory(controller, root))
            controller.load_adventure_path.assert_called_once_with(path.resolve())

    def test_advanced_folder_cancel_happens_before_autosave_or_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_adventure(root, "Trip", previous=root / "previous")
            write_adventure(root, "Other", previous=root / "previous")
            previous = Path("/tmp/current")
            controller = SimpleNamespace(current_project_dir=previous, _restore_project_directory_field=Mock(),
                flush_adventure_autosave=Mock(), load_project_configuration=Mock())
            with patch("GPSTrackShowGUI.choose_adventure", return_value=None):
                self.assertFalse(GPXTrackerController._activate_project_directory(controller, root))
            controller._restore_project_directory_field.assert_called_once_with(previous)
            controller.flush_adventure_autosave.assert_not_called()
            controller.load_project_configuration.assert_not_called()

    def test_advanced_directory_and_explicit_file_use_existing_open_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root, previous=root / "previous")
            controller = SimpleNamespace(current_project_dir=None, load_project_configuration=Mock(return_value=True))
            self.assertTrue(GPXTrackerController._activate_project_directory(controller, root))
            controller.load_project_configuration.assert_called_once_with(path.resolve())
            controller.load_project_configuration.reset_mock()
            with patch("GPSTrackShowGUI.choose_adventure", side_effect=AssertionError("Explicit file chosen")):
                self.assertTrue(GPXTrackerController._activate_project_directory(controller, path))
            controller.load_project_configuration.assert_called_once_with(path.resolve())

    def test_advanced_cli_file_startup_does_not_switch_to_folder_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = write_adventure(root, previous=root / "previous")
            controller = SimpleNamespace(startup_project_file=path, startup_project_directory=None,
                window=Mock(), title_field=object(), load_project_configuration=Mock(return_value=True))
            with patch("GPSTrackShowGUI.discover_adventure_candidates", side_effect=AssertionError("Explicit file must open directly")):
                GPXTrackerController.show(controller, present_window=False)
            controller.load_project_configuration.assert_called_once_with(path.resolve())

    def test_invalid_file_does_not_flush_or_stop_the_current_project(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Broken.adv"
            path.write_text("{}")
            controller = SimpleNamespace(project_access=SimpleNamespace(defaults=None), project_directory=None,
                _alert=Mock(), _flush_workspace_autosave=Mock(), _stop_media_watcher=Mock())
            with patch("cocoa_adventure_map.temporary_directory_access", return_value=nullcontext(True)):
                self.assertFalse(AdventureMapController.load_adventure_path(controller, path))
            controller._flush_workspace_autosave.assert_not_called()
            controller._stop_media_watcher.assert_not_called()


class RelocatedAdventureRecentTests(unittest.TestCase):
    def test_map_recents_store_selected_file_without_removing_other_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, _ = write_adventure(root / "old")
            selected, _ = write_adventure(root / "new")
            store = root / "recents.json"
            controller = SimpleNamespace(adventure_file=selected, demo_mode=False,
                recent_adventure_paths=Mock(return_value=[selected.parent, old]),
                _recent_adventures_store_path=lambda: store, _refresh_recent_adventure_menu=Mock())
            AdventureMapController._remember_recent_adventure(controller, selected.parent)
            self.assertEqual(json.loads(store.read_text()), [str(selected.resolve()), str(old.resolve())])

    def test_advanced_recents_load_both_legacy_directories_and_selected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected, _ = write_adventure(root / "new")
            previous = root / "previous"
            previous.mkdir()
            store = root / "recents.json"
            store.write_text(json.dumps([str(selected), str(previous)]))
            controller = SimpleNamespace(_recent_adventures_store_path=lambda: store, _save_recent_adventures=Mock())
            self.assertEqual(GPXTrackerController._load_recent_adventures(controller), [selected.resolve(), previous.resolve()])

    def test_advanced_recents_preserve_other_copy_and_open_recent_file_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, _ = write_adventure(root / "old")
            selected, _ = write_adventure(root / "new")
            controller = SimpleNamespace(current_project_file=selected, recent_adventures=[selected.parent, old],
                _save_recent_adventures=Mock(), _refresh_recent_adventure_menu=Mock())
            GPXTrackerController._remember_recent_adventure(controller, selected.parent)
            self.assertEqual(controller.recent_adventures, [selected.resolve(), old.resolve()])
            workspace = SimpleNamespace(load_adventure_path=Mock(), load_project_directory=Mock())
            sender = SimpleNamespace(representedObject=lambda: str(selected))
            AdventureMapController.openRecentAdventure_.callable(workspace, sender)
            workspace.load_adventure_path.assert_called_once_with(selected.resolve())
            workspace.load_project_directory.assert_not_called()

    def test_temporary_access_balances_its_own_scope_on_cancel_or_error(self):
        defaults = object()
        with patch("security_scoped_access.SecurityScopedAccessManager") as cls:
            cls.return_value.activate_directory.return_value = True
            with self.assertRaises(RuntimeError):
                with temporary_directory_access("/tmp/new", defaults) as permitted:
                    self.assertTrue(permitted)
                    raise RuntimeError("cancelled")
            cls.assert_called_once_with(defaults)
            cls.return_value.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
