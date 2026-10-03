import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSModalPanelWindowLevel

from cocoa_alerts import run_foreground_modal_alert
from cocoa_adventure_map import AdventureMapAppDelegate, AdventureMapController


class ForegroundAlertTests(unittest.TestCase):
    def test_activation_and_front_order_precede_blocking_modal_loop(self):
        events = Mock()
        alert = events.alert
        app = events.app
        window = alert.window.return_value
        window.level.return_value = 0
        alert.runModal.return_value = 1001
        with patch("cocoa_alerts.NSApp", return_value=app):
            self.assertEqual(run_foreground_modal_alert(alert), 1001)
        names = [call[0] for call in events.mock_calls]
        self.assertLess(names.index("app.activateIgnoringOtherApps_"), names.index("alert.runModal"))
        self.assertLess(names.index("alert.layout"), names.index("alert.window"))
        self.assertLess(names.index("alert.window().makeKeyAndOrderFront_"), names.index("alert.runModal"))
        self.assertLess(names.index("alert.window().orderFrontRegardless"), names.index("alert.runModal"))
        self.assertEqual(window.setLevel_.call_args_list[0].args, (NSModalPanelWindowLevel,))
        self.assertEqual(window.setLevel_.call_args_list[-1].args, (0,))
        window.orderOut_.assert_called_once_with(None)

    def test_level_and_visibility_restored_after_failure(self):
        alert = Mock()
        window = alert.window.return_value
        window.level.return_value = NSModalPanelWindowLevel + 1
        alert.runModal.side_effect = RuntimeError("presentation failed")
        with patch("cocoa_alerts.NSApp"), self.assertRaises(RuntimeError):
            run_foreground_modal_alert(alert)
        self.assertTrue(all(call.args == (NSModalPanelWindowLevel + 1,)
                            for call in window.setLevel_.call_args_list))
        window.orderOut_.assert_called_once_with(None)


class RecoveryDialogTests(unittest.TestCase):
    def make_controller(self, directory):
        return SimpleNamespace(
            adventure_file=None, project_directory=None,
            adventure_name="Test", recovery=SimpleNamespace(directory=directory),
            gpx_controller=SimpleNamespace(tracks=[], _load_gpx_paths_synchronously=Mock()),
            _prepare_gpx_document_context=Mock(), _alert=Mock(),
        )

    def test_recovery_choices_use_foreground_dialog_without_changing_actions(self):
        for response in (1000, 1001, 1002):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                controller = self.make_controller(directory)
                snapshot = {"adventure_name": "Recovered trip", "gpx_xml": "<gpx/>"}
                with (
                    patch("cocoa_adventure_map.discover_recovery_sessions", return_value=[snapshot]),
                    patch("cocoa_adventure_map.is_guided_demo_recovery", return_value=False),
                    patch("cocoa_adventure_map.NSAlert") as alerts,
                    patch("cocoa_adventure_map.run_foreground_modal_alert", return_value=response) as present,
                    patch("cocoa_adventure_map.delete_recovery_session", return_value=True) as delete,
                ):
                    self.assertEqual(AdventureMapController._offer_recovery(controller), response == 1000)
                    present.assert_called_once_with(alerts.alloc.return_value.init.return_value)
                    if response == 1000:
                        controller.gpx_controller._load_gpx_paths_synchronously.assert_called_once_with(
                            [directory / "recovered.gpx"], mark_dirty=False
                        )
                        self.assertEqual(controller.adventure_name, "Recovered trip")
                    else:
                        controller.gpx_controller._load_gpx_paths_synchronously.assert_not_called()
                    if response == 1002:
                        delete.assert_called_once_with(snapshot)
                    else:
                        delete.assert_not_called()

    def test_no_recovery_does_not_activate_or_show_dialog(self):
        controller = self.make_controller(None)
        with (
            patch("cocoa_adventure_map.discover_recovery_sessions", return_value=[]),
            patch("cocoa_adventure_map.run_foreground_modal_alert") as present,
        ):
            self.assertFalse(AdventureMapController._offer_recovery(controller))
            present.assert_not_called()

    def test_application_activates_before_show_can_open_recovery(self):
        events = Mock()
        delegate = SimpleNamespace(project_directory=None, project_file=None)
        with (
            patch("cocoa_adventure_map.configure_mycamino_branding"),
            patch("cocoa_adventure_map.AdventureMapController") as controllers,
            patch("cocoa_adventure_map.build_adventure_map_menu"),
            patch("cocoa_adventure_map.NSApp", return_value=events.app),
            patch("cocoa_adventure_map.GuidedDemoCoordinator"),
            patch("cocoa_adventure_map.NSTimer"),
        ):
            controllers.alloc.return_value.initWithProjectDirectory_projectFile_.return_value = events.controller
            AdventureMapAppDelegate.applicationDidFinishLaunching_.callable(delegate, None)
        names = [call[0] for call in events.mock_calls]
        self.assertLess(names.index("app.activateIgnoringOtherApps_"), names.index("controller.show"))


if __name__ == "__main__":
    unittest.main()
