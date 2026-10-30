"""Explicit GPX saves must acknowledge the Adventure's source transaction."""

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from adventure_parameters import default_parameters
from cocoa_adventure_map import AdventureMapController
from GPXEditor import GPXEditorController, TrackRecord, qname
from json_storage import atomic_write_json
from workspace_mutations import MutationJournal, MutationKind


def invoke(method, *args, **kwargs):
    return getattr(method, "callable", method)(*args, **kwargs)


class AdventureEditorSaveTests(unittest.TestCase):
    def workspace(self, directory):
        root = Path(directory).resolve()
        path = root / "journey.gpx"
        element = ET.Element(qname("trk"))
        ET.SubElement(element, qname("name")).text = "Saved timing"
        segment = ET.SubElement(element, qname("trkseg"))
        for index, timestamp in enumerate(("2026-08-01T05:48:38.664Z", "2026-08-01T05:48:38.945179Z")):
            point = ET.SubElement(segment, qname("trkpt"), lat="50", lon=str(7 + index * .001))
            ET.SubElement(point, qname("time")).text = timestamp
        editor = SimpleNamespace(tracks=[TrackRecord(117, element, str(path))], project_name="Trip",
            anchor=None, next_nr=118, dirty=True, undo_stack=[object()],
            output_field=Mock(stringValue=Mock(return_value=str(path))),
            output_path_from_field=lambda: path, schedule_saved_metrics_cache=Mock(), set_output_path=Mock(),
            set_status=Mock(), save_to_path=Mock(side_effect=AssertionError("Must use the Adventure coordinator")))
        editor.build_root = lambda output: GPXEditorController.build_root(editor, output)
        editor.write_gpx_file = Mock(side_effect=lambda output: GPXEditorController.write_gpx_file(editor, output))
        editor.backup_existing_file = Mock(side_effect=lambda output: GPXEditorController.backup_existing_file(editor, output))
        editor.write_gpx_file(path)
        editor.write_gpx_file.reset_mock()
        journal = MutationJournal()
        journal.record("gpx", MutationKind.TIMING, track_numbers=[117])
        workspace = SimpleNamespace(
            project_directory=root, adventure_file=root / "Trip.adv", adventure_name="Trip",
            adventure_payload=dict(adventure_format_version=2, project_name="Trip", project_directory=str(root),
                gpx_file="journey.gpx", control_file="Trip-sorted.lst", track_map_base="Trip"),
            gpx_controller=editor, full_gui_controller=None, workspace_dirty_components={"gpx"},
            workspace_autosave_generation=1, workspace_autosave_future=None, workspace_autosave_in_progress=False,
            mutation_journal=journal, recovery_dirty=True, parameters=default_parameters(),
            weather_consent="disabled", demo_mode=False, untimed_prompt_suppressions=set(),
            control_file=root / "Trip-sorted.lst", control_rows=[], temporary_control_model=True, media_items=[],
            _set_background_progress=Mock(), _clear_background_progress=Mock(), _queue_derived_track_data=Mock(),
            _discard_recovery_after_successful_save=Mock(), _remember_recent_adventure=Mock(),
            _journal=Mock(), _alert=Mock(), journal=Mock(), _mark_adventure_component_changed=Mock(),
            _has_recoverable_workspace_content=Mock(return_value=True),
        )
        atomic_write_json(workspace.adventure_file, workspace.adventure_payload)
        workspace._flush_workspace_autosave = lambda **kwargs: AdventureMapController._flush_workspace_autosave(workspace, **kwargs)
        workspace.save_gpx_editor_document = lambda controller, output: AdventureMapController.save_gpx_editor_document(workspace, controller, output)
        editor.adventure_workspace_delegate = workspace
        return workspace

    def test_save_tracks_then_quit_has_no_redundant_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(directory)
            editor = workspace.gpx_controller
            undo = editor.undo_stack
            self.assertTrue(GPXEditorController.save_current(editor))
            self.assertFalse(editor.dirty)
            self.assertFalse(workspace.workspace_dirty_components)
            self.assertFalse(workspace.recovery_dirty)
            self.assertFalse(workspace.mutation_journal.pending())
            editor.write_gpx_file.assert_called_once()
            editor.save_to_path.assert_not_called()
            editor.backup_existing_file.assert_called_once()
            self.assertTrue((Path(directory) / "journey.bak").is_file())
            self.assertIs(editor.undo_stack, undo)
            workspace._discard_recovery_after_successful_save.assert_called_once()
            workspace._queue_derived_track_data.assert_called_once()
            saved = json.loads(workspace.adventure_file.read_text())
            self.assertEqual(saved["gpx_identity"]["source"]["sha256"],
                hashlib.sha256((Path(directory) / "journey.gpx").read_bytes()).hexdigest())
            with patch("cocoa_adventure_map.NSAlert", side_effect=AssertionError("Already saved")):
                self.assertTrue(AdventureMapController.confirm_quit(workspace))

    def test_failed_adventure_commit_keeps_the_save_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(directory)
            with patch("cocoa_adventure_map.atomic_write_json", side_effect=OSError("read-only Adventure")):
                self.assertFalse(GPXEditorController.save_current(workspace.gpx_controller))
            self.assertTrue(workspace.gpx_controller.dirty)
            self.assertIn("gpx", workspace.workspace_dirty_components)
            self.assertTrue(workspace.recovery_dirty)
            workspace._discard_recovery_after_successful_save.assert_not_called()
            self.assertTrue(GPXEditorController.save_current(workspace.gpx_controller))
            self.assertFalse(workspace.gpx_controller.dirty)
            self.assertFalse(workspace.workspace_dirty_components)
            self.assertFalse(workspace.recovery_dirty)

    def test_backup_failure_never_reports_saved_or_clears_dirty_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(directory)
            editor = workspace.gpx_controller
            editor.backup_existing_file.side_effect = PermissionError("Cannot create backup")
            self.assertFalse(GPXEditorController.save_current(editor))
            editor.write_gpx_file.assert_not_called()
            self.assertTrue(editor.dirty)
            self.assertIn("gpx", workspace.workspace_dirty_components)

    def test_new_edit_during_save_is_not_acknowledged_as_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(directory)
            editor = workspace.gpx_controller
            def write_then_edit(path):
                root = GPXEditorController.write_gpx_file(editor, path)
                list(editor.tracks[0].element.iter(qname("time")))[-1].text = "2026-08-01T05:48:39Z"
                workspace.mutation_journal.record("gpx", MutationKind.TIMING, track_numbers=[117])
                workspace.workspace_autosave_generation += 1
                return root
            editor.write_gpx_file.side_effect = write_then_edit
            self.assertTrue(GPXEditorController.save_current(editor))
            self.assertTrue(editor.dirty)
            self.assertIn("gpx", workspace.workspace_dirty_components)
            self.assertTrue(workspace.recovery_dirty)
            workspace._discard_recovery_after_successful_save.assert_not_called()

    def test_unrelated_new_control_changes_still_require_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(directory)
            self.assertTrue(GPXEditorController.save_current(workspace.gpx_controller))
            workspace.workspace_dirty_components.add("control")
            with patch("cocoa_adventure_map.NSAlert") as alerts:
                alerts.alloc.return_value.init.return_value.runModal.return_value = 1002
                self.assertFalse(AdventureMapController.confirm_quit(workspace))
                alerts.alloc.assert_called_once()

    def test_save_as_elsewhere_is_not_an_adventure_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.workspace(directory)
            other = Path(directory) / "export.gpx"
            self.assertIsNone(workspace.save_gpx_editor_document(workspace.gpx_controller, other))
            self.assertIn("gpx", workspace.workspace_dirty_components)
            workspace.gpx_controller.write_gpx_file.assert_not_called()

    def test_standalone_save_and_save_as_keep_existing_workflow(self):
        editor = SimpleNamespace(tracks=[object()], output_field=Mock(stringValue=Mock(return_value="track.gpx")),
            output_path_from_field=lambda: Path("track.gpx"), save_to_path=Mock(), save_with_panel=Mock(return_value=False))
        self.assertTrue(GPXEditorController.save_current(editor))
        editor.save_to_path.assert_called_once_with(Path("track.gpx"))
        editor.output_field.stringValue.return_value = ""
        self.assertFalse(GPXEditorController.save_current(editor))
        editor.save_with_panel.assert_called_once()
