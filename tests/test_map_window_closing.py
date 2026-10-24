import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import GPXEditorController, PlotView, PlotWindowDelegate
from cocoa_adventure_map import AdventureMapController


def invoke(method, *args):
    return getattr(method, 'callable', method)(*args)


class MapWindowClosingTests(unittest.TestCase):
    def workspace(self):
        window = Mock()
        view = SimpleNamespace(cancel_interactive_tile_requests=Mock())
        controller = SimpleNamespace(
            closing_auxiliary_windows=False,
            close_elevation_profile_for_plot_view=Mock(),
            existing_elevation_profile_for_plot_view=Mock(return_value=None),
        )
        return SimpleNamespace(window=window, plot_view=view, gpx_controller=controller)

    def test_shared_map_is_hidden_not_disposed_while_an_editor_remains(self):
        workspace = self.workspace()
        editor = Mock()
        editor.isVisible.return_value = True
        with patch('cocoa_adventure_map.NSApp', return_value=SimpleNamespace(windows=lambda: [workspace.window, editor])):
            self.assertTrue(invoke(AdventureMapController.hideWorkspaceMapIfOtherWindowsRemain_, workspace, workspace.window))
        workspace.window.orderOut_.assert_called_once()
        workspace.plot_view.cancel_interactive_tile_requests.assert_called_once_with(shutdown=False)
        workspace.gpx_controller.close_elevation_profile_for_plot_view.assert_called_once_with(workspace.plot_view)

    def test_last_map_window_is_allowed_to_close_normally(self):
        workspace = self.workspace()
        with patch('cocoa_adventure_map.NSApp', return_value=SimpleNamespace(windows=lambda: [workspace.window])):
            self.assertFalse(invoke(AdventureMapController.hideWorkspaceMapIfOtherWindowsRemain_, workspace, workspace.window))
        workspace.window.orderOut_.assert_not_called()
        workspace.gpx_controller.close_elevation_profile_for_plot_view.assert_not_called()

    def test_bulk_cleanup_never_hides_instead_of_disposing(self):
        workspace = self.workspace()
        workspace.gpx_controller.closing_auxiliary_windows = True
        self.assertFalse(invoke(AdventureMapController.hideWorkspaceMapIfOtherWindowsRemain_, workspace, workspace.window))
        workspace.gpx_controller.close_elevation_profile_for_plot_view.assert_not_called()

    def test_delegate_obeys_the_shared_map_close_policy(self):
        workspace = SimpleNamespace(hideWorkspaceMapIfOtherWindowsRemain_=Mock(return_value=True))
        delegate = SimpleNamespace(view=SimpleNamespace(adventure_workspace_delegate=workspace))
        window = object()
        self.assertFalse(invoke(PlotWindowDelegate.windowShouldClose_, delegate, window))
        workspace.hideWorkspaceMapIfOtherWindowsRemain_.assert_called_once_with(window)
        delegate.view.adventure_workspace_delegate = None
        self.assertTrue(invoke(PlotWindowDelegate.windowShouldClose_, delegate, window))

    def test_last_map_close_can_be_cancelled_by_save_confirmation(self):
        workspace = SimpleNamespace(
            hideWorkspaceMapIfOtherWindowsRemain_=Mock(return_value=False),
            confirm_quit=Mock(return_value=False),
        )
        delegate = SimpleNamespace(view=SimpleNamespace(adventure_workspace_delegate=workspace))
        self.assertFalse(invoke(PlotWindowDelegate.windowShouldClose_, delegate, object()))
        workspace.confirm_quit.assert_called_once_with()

    def test_q_uses_normal_close_validation_instead_of_forcing_close(self):
        window = Mock()
        view = SimpleNamespace(adventure_workspace_delegate=None, window=lambda: window)
        event = SimpleNamespace(
            charactersIgnoringModifiers=lambda: 'q', characters=lambda: 'q',
            keyCode=lambda: 12, modifierFlags=lambda: 0, isARepeat=lambda: False,
        )
        invoke(PlotView.keyDown_, view, event)
        window.performClose_.assert_called_once_with(None)
        window.close.assert_not_called()

    def test_disposed_view_rejects_late_drawing_and_worker_callbacks(self):
        view = SimpleNamespace(plot_closed=True)
        invoke(PlotView.drawRect_, view, None)
        self.assertIsNone(PlotView.request_photo_thumbnail(view, Path('irrelevant.jpeg')))
        PlotView.schedule_interactive_tiles(view)
        invoke(PlotView.requestInteractiveTiles_, view, None)
        invoke(PlotView.installInteractiveTile_, view, {})
        invoke(PlotView.installPhotoThumbnail_, view, None)

    def test_disposal_is_idempotent_and_detaches_the_inspector(self):
        view = SimpleNamespace(
            plot_closed=False, long_press_timer=Mock(),
            photo_thumbnail_generation=0, photo_thumbnail_futures={'pending': Mock()},
            photo_thumbnail_executor=Mock(), cancel_interactive_tile_requests=Mock(),
        )
        inspector = SimpleNamespace(plot_view=view)
        view.inspector = inspector
        controller = SimpleNamespace(close_elevation_profile_for_plot_view=Mock())
        with patch('GPXEditor.PlotView', SimpleNamespace):
            GPXEditorController.plot_window_closing(controller, view)
            GPXEditorController.plot_window_closing(controller, view)
        self.assertTrue(view.plot_closed)
        self.assertIsNone(inspector.plot_view)
        view.photo_thumbnail_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        view.cancel_interactive_tile_requests.assert_called_once_with(shutdown=True)

    def test_closing_track_table_restores_a_hidden_shared_map(self):
        map_window, table = Mock(), Mock()
        map_window.isVisible.return_value = False
        controller = SimpleNamespace(adventure_workspace_delegate=SimpleNamespace(window=map_window), window=table)
        self.assertTrue(GPXEditorController.hide_main_editor_for_workspace(controller))
        map_window.makeKeyAndOrderFront_.assert_called_once_with(None)
        table.orderOut_.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
