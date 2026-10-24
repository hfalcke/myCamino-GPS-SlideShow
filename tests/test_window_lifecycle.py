"""Logical tool reuse and retained Cocoa window lifetime regressions.

SPDX-License-Identifier: GPL-3.0-or-later
"""

import gc
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from window_lifecycle import WindowRegistry


class FakeWindow:
    def __init__(self, title="Tool", visible=False, minimized=False):
        self.name, self.visible, self.minimized = title, visible, minimized

    def title(self):
        return self.name

    def isVisible(self):
        return self.visible

    def isMiniaturized(self):
        return self.minimized


class WindowRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = WindowRegistry()

    def test_internal_windows_are_not_open_tools(self):
        window = FakeWindow()
        self.registry.register(window, owner=self, role="track-table")
        self.assertEqual(self.registry.entries(), [])
        self.assertFalse(self.registry.is_active(window))

    def test_hidden_tools_and_minimized_windows_are_reusable(self):
        window = FakeWindow(visible=True)
        record = self.registry.register(window, owner=self, role="track-table")
        self.assertEqual(self.registry.entries(), [record])
        window.visible = False
        self.assertEqual(self.registry.entries(), [record])
        self.assertTrue(self.registry.is_active(window))
        other = FakeWindow(minimized=True)
        self.registry.register(other)
        self.assertEqual(len(self.registry.entries()), 2)

    def test_transient_panels_are_owned_but_not_menu_tools(self):
        window = FakeWindow(visible=True)
        self.registry.register(window, owner=self, listed=False)
        self.registry.presented(window)
        self.assertEqual(self.registry.entries(), [])
        self.assertFalse(self.registry.is_active(window))
        self.registry.retire_owner(self)
        self.assertTrue(self.registry.records[id(window)].retired)

    def test_closed_windows_can_be_reopened_but_retired_windows_cannot(self):
        window = FakeWindow()
        self.registry.presented(window)
        self.registry.dismiss(window)
        self.assertEqual(self.registry.entries(), [])
        self.assertTrue(self.registry.presented(window))
        self.registry.retire(window)
        window.visible = True
        self.assertFalse(self.registry.presented(window))
        self.assertFalse(self.registry.is_active(window))
        self.assertEqual(self.registry.entries(), [])

    def test_retirement_is_by_owner_not_title_or_document(self):
        owner = object()
        old, current = FakeWindow(), FakeWindow()
        self.registry.register(old, owner=owner, document="old.adv")
        current_record = self.registry.register(current, owner=self, document="new.adv")
        self.registry.presented(old)
        self.registry.presented(current)
        self.registry.retire_owner(owner)
        self.registry.register(old, owner=owner)
        self.assertEqual(self.registry.entries(), [current_record])

    def test_registry_does_not_retain_native_windows_or_duplicate_records(self):
        window = FakeWindow()
        record = self.registry.register(window)
        self.assertIs(self.registry.register(window, owner=self, role="help"), record)
        self.assertEqual(record.role, "help")
        reference = weakref.ref(window)
        del window
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(self.registry.entries(), [])
        self.assertEqual(self.registry.records, {})

    def test_native_windows_use_objc_zeroing_weak_references(self):
        # Cocoa windows do not support Python's weakref.ref.
        window = object()
        with patch("objc.WeakRef", return_value=lambda: window) as weak:
            record = self.registry.register(window)
        weak.assert_called_once_with(window)
        self.assertIs(record.window, window)


def invoke(method, *args, **kwargs):
    return getattr(method, "callable", method)(*args, **kwargs)


class SharedToolTests(unittest.TestCase):
    def test_advanced_uses_map_document_without_reparsing_or_losing_undo(self):
        import GPSTrackShowGUI as gui

        editor = SimpleNamespace(
            tracks=[SimpleNamespace(source_file="/tmp/journey.gpx")],
            current_output_path=Mock(return_value=Path("/tmp/journey.gpx")),
            apply_project_parameters=Mock(), load_gpx_paths=Mock(),
            set_output_path=Mock(), open_pdf_summary_window=Mock(),
            undo_stack=["unsaved edit"],
        )
        host = SimpleNamespace(gpx_controller=editor, openTrackEditor_=Mock())
        controller = SimpleNamespace(current_project_file=None, adventure_map_host=host, parameters={})
        result = gui.GPXTrackerController._open_gpx_editor(controller, input_paths=[Path("/tmp/journey.gpx")])
        self.assertIs(result, editor)
        self.assertIs(controller.gpx_editor_controller, editor)
        editor.load_gpx_paths.assert_not_called()
        self.assertEqual(editor.undo_stack, ["unsaved edit"])
        host.openTrackEditor_.assert_called_once()
        gui.GPXTrackerController._open_gpx_editor(controller, open_pdf_summary=True)
        editor.open_pdf_summary_window.assert_called_once()
        self.assertEqual(host.openTrackEditor_.call_count, 1)

    def test_new_inputs_are_imported_into_shared_document_only_once(self):
        import GPSTrackShowGUI as gui

        editor = SimpleNamespace(
            tracks=[], current_output_path=Mock(return_value=None),
            apply_project_parameters=Mock(), load_gpx_paths=Mock(),
            set_output_path=Mock(),
        )
        host = SimpleNamespace(gpx_controller=editor, openTrackEditor_=Mock())
        controller = SimpleNamespace(current_project_file=None, adventure_map_host=host, parameters={})
        gui.GPXTrackerController._open_gpx_editor(controller, input_paths=[Path("/tmp/new.gpx")])
        editor.load_gpx_paths.assert_called_once_with([Path("/tmp/new.gpx").resolve()], mark_dirty=True)

    def test_all_adventure_settings_entry_points_share_one_editor(self):
        from GPSTrackShowGUI import GPXTrackerController
        from GPXEditor import GPXEditorController

        host = SimpleNamespace(open_shared_settings=Mock(return_value="shared draft"))
        controller = SimpleNamespace(adventure_map_host=host)
        self.assertEqual(GPXTrackerController._show_parameter_editor_section(
            controller, "Audio", draft_overrides={"audio.enabled": True}), "shared draft")
        host.open_shared_settings.assert_called_once_with("Audio", draft_overrides={"audio.enabled": True}, show_advanced=None)
        invoke(GPXEditorController.showSettings_, SimpleNamespace(adventure_workspace_delegate=host), None)
        self.assertEqual(host.open_shared_settings.call_args.args, ("GPX Processing",))

    def test_reopening_visible_settings_does_not_discard_draft(self):
        from cocoa_parameter_editor import CocoaParameterEditor

        window = Mock()
        window.isVisible.return_value = True
        draft = {"test": "not yet applied"}
        editor = SimpleNamespace(
            window=window, draft=draft, values={}, show_advanced=False,
            advanced_checkbox=Mock(), _render_section=Mock(),
        )
        with patch("cocoa_parameter_editor.NSApp", return_value=Mock()), patch("cocoa_parameter_editor.WINDOW_REGISTRY"):
            invoke(CocoaParameterEditor.show, editor)
        self.assertIs(editor.draft, draft)

    def test_hidden_help_reopens_same_window(self):
        from GPXEditor import GPXEditorController

        window = Mock()
        window.isVisible.return_value = False
        controller = SimpleNamespace(help_window=window)
        GPXEditorController.show_scrollable_help(controller, "Help", "Help text")
        window.makeKeyAndOrderFront_.assert_called_once()
        self.assertIs(controller.help_window, window)

    def test_pdf_close_button_preserves_options_and_stops_polling(self):
        from GPXEditor import GPXEditorController

        window, timer = Mock(), Mock()
        controller = SimpleNamespace(pdf_summary_window=window, pdf_summary_poll_timer=timer, cancelPdfSummary_=Mock())
        invoke(GPXEditorController.closePdfSummary_, controller, None)
        self.assertIs(controller.pdf_summary_window, window)
        window.orderOut_.assert_called_once()
        window.close.assert_not_called()
        timer.invalidate.assert_called_once()
        self.assertIsNone(controller.pdf_summary_poll_timer)

    def test_closing_media_viewer_hides_and_stops_video_without_destroying_it(self):
        from GPSTrackShowGUI import GPXTrackerController, SlideShowMediaViewerDelegate

        window = Mock()
        controller = SimpleNamespace(media_viewer_window=window, media_viewer_hint_timer=Mock(), stop_media_viewer_video=Mock())
        GPXTrackerController.close_media_viewer(controller)
        controller.stop_media_viewer_video.assert_called_once()
        window.orderOut_.assert_called_once()
        window.close.assert_not_called()
        self.assertIs(controller.media_viewer_window, window)
        controller.close_media_viewer = Mock()
        self.assertFalse(invoke(SlideShowMediaViewerDelegate.windowShouldClose_, SimpleNamespace(controller=controller), window))
        controller.close_media_viewer.assert_called_once()

    def test_retired_callbacks_are_inert_even_with_malformed_payloads(self):
        from GPSTrackShowGUI import GPXTrackerController

        controller = SimpleNamespace(lifecycle_retired=True)
        for name in (
            "applyAsyncProjectStatus_", "plotSelectionAnalysisProgress_", "plotSelectionAnalysisFinished_",
            "plotSelectionAcceptanceFinished_", "plotImageCreated_", "plotCreationFinished_",
            "geoLocationsRunFinished_", "derivedTrackUpdateFinished_", "controlPreviewReady_",
            "slideShowLiveStateUpdated_", "slideShowProcessFinished_", "slideshowDependencyPreflightFinished_",
            "slideshowStartupProgress_", "videoNormalizationFinished_", "videoNormalizationProgress_",
            "appendGeoLocationsOutputLine_", "resumePendingMapProviderAction_", "mediaImportPanelFinished_",
            "setStatusFromWorker_", "setProgressFromWorker_", "clearOptionalEnrichmentProgress_",
            "setOptionalEnrichmentProgress_",
            "applyNetworkState_",
        ):
            with self.subTest(callback=name):
                invoke(getattr(GPXTrackerController, name), controller, None)

    def test_model_cleanup_waits_for_worker_and_does_not_block_main_thread(self):
        from GPSTrackShowGUI import GPXTrackerController

        thread = Mock()
        thread.is_alive.return_value = True
        controller = SimpleNamespace(
            lifecycle_retired=True, lifecycle_status_threads=[thread],
            lifecycle_retired_windows=[], control_table_rows=["large model"],
            geolocations_text_view=None,
            performSelector_withObject_afterDelay_=Mock(),
        )
        invoke(GPXTrackerController.releaseRetiredModels_, controller, None)
        self.assertEqual(controller.control_table_rows, ["large model"])
        controller.performSelector_withObject_afterDelay_.assert_called_once()
        thread.is_alive.return_value = False
        invoke(GPXTrackerController.releaseRetiredModels_, controller, None)
        self.assertEqual(controller.control_table_rows, [])
        self.assertEqual(controller.lifecycle_status_threads, [])


if __name__ == "__main__":
    unittest.main()
