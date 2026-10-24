import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cocoa_adventure_map import AdventureMapAppDelegate, AdventureMapController, NSTerminateCancel, NSTerminateNow


def invoke(method, *args):
    return getattr(method, 'callable', method)(*args)


class AdventureQuitConfirmationTests(unittest.TestCase):
    def workspace(self, dirty=True):
        return SimpleNamespace(
            gpx_controller=SimpleNamespace(dirty=dirty),
            workspace_dirty_components=set(), full_gui_controller=None,
            recovery_dirty=False, _has_recoverable_workspace_content=Mock(return_value=False),
            saveWorkspace_=Mock(return_value=True),
        )

    def confirm(self, workspace, response):
        with patch('cocoa_adventure_map.NSAlert') as alerts:
            alerts.alloc.return_value.init.return_value.runModal.return_value = response
            result = AdventureMapController.confirm_quit(workspace)
        return result

    def test_save_must_succeed_before_quit_is_approved(self):
        workspace = self.workspace()
        self.assertTrue(self.confirm(workspace, 1000))
        workspace.saveWorkspace_.assert_called_once_with(None)
        self.assertTrue(workspace.quit_approved)

    def test_failed_or_cancelled_save_keeps_application_open(self):
        workspace = self.workspace()
        workspace.saveWorkspace_.return_value = False
        self.assertFalse(self.confirm(workspace, 1000))
        self.assertFalse(getattr(workspace, 'quit_approved', False))

    def test_cancel_leaves_dirty_state_unchanged(self):
        workspace = self.workspace()
        self.assertFalse(self.confirm(workspace, 1002))
        self.assertTrue(workspace.gpx_controller.dirty)
        workspace.saveWorkspace_.assert_not_called()
        self.assertFalse(getattr(workspace, 'discard_workspace_on_quit', False))

    def test_dont_save_disables_late_autosave(self):
        workspace = self.workspace()
        self.assertTrue(self.confirm(workspace, 1001))
        self.assertTrue(workspace.discard_workspace_on_quit)
        workspace.saveWorkspace_.assert_not_called()
        invoke(AdventureMapController.performWorkspaceAutosave_, workspace, None)

    def test_clean_workspace_needs_no_prompt(self):
        workspace = self.workspace(dirty=False)
        with patch('cocoa_adventure_map.NSAlert') as alerts:
            self.assertTrue(AdventureMapController.confirm_quit(workspace))
            alerts.alloc.assert_not_called()

    def test_already_confirmed_quit_is_not_asked_twice(self):
        workspace = self.workspace()
        workspace.quit_approved = True
        with patch('cocoa_adventure_map.NSAlert') as alerts:
            self.assertTrue(AdventureMapController.confirm_quit(workspace))
            alerts.alloc.assert_not_called()

    def test_quit_menu_honors_cancellation(self):
        controller = SimpleNamespace(confirm_quit=Mock(return_value=False))
        delegate = SimpleNamespace(controller=controller)
        self.assertEqual(invoke(AdventureMapAppDelegate.applicationShouldTerminate_, delegate, None), NSTerminateCancel)
        controller.confirm_quit.return_value = True
        self.assertEqual(invoke(AdventureMapAppDelegate.applicationShouldTerminate_, delegate, None), NSTerminateNow)

    def test_dont_save_skips_shutdown_autosave_and_recovery_creation(self):
        workspace = SimpleNamespace(
            discard_workspace_on_quit=True, _network_state_changed=Mock(),
            _reset_optional_enrichment=Mock(), _stop_media_watcher=Mock(), demo_mode=False,
            workspace_autosave_timer=Mock(), recovery_debounce_timer=Mock(),
            workspace_autosave_future=None, recovery_write_future=None,
            adventure_file=Path('/tmp/Adventure.adv'), workspace_dirty_components={'gpx'},
            gpx_controller=Mock(), full_gui_controller=None,
            _flush_workspace_autosave=Mock(), recovery_dirty=True,
            writeRecovery_=Mock(), demo_coordinator=None, recovery=Mock(),
            cancel_event=Mock(), executor=Mock(), project_access=Mock(),
            parameter_editor_controller=None, recovery_periodic_timer=None,
            journal_refresh_timer=None, document_windows={}, help_window=None,
        )
        with patch('cocoa_adventure_map.network_availability'):
            AdventureMapController.shutdown(workspace)
        workspace._flush_workspace_autosave.assert_not_called()
        workspace.writeRecovery_.assert_not_called()
        workspace.recovery.discard.assert_called_once()


if __name__ == '__main__':
    unittest.main()
