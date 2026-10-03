import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from GPXEditor import PlotView
from cocoa_adventure_map import AdventureMapController


class AdventureOverviewNavigationTests(unittest.TestCase):
    def menu_entries(self, mode, workspace):
        view = SimpleNamespace(
            mode=mode, adventure_workspace_delegate=workspace,
            waypoint_editing_enabled=lambda: False,
            _plot_context_item=Mock(), cursor=None,
            controller=SimpleNamespace(selected_tracks=lambda: []),
        )
        PlotView.add_view_context_items(view, Mock())
        return {call.args[1]: call.args[2] for call in view._plot_context_item.call_args_list}

    def test_overview_offers_fit_but_not_redundant_return(self):
        items = self.menu_entries("overview", SimpleNamespace())
        self.assertNotIn("Return to Adventure Overview", items)
        self.assertNotIn("Show Adventure Overview", items)
        self.assertEqual(items["Fit Entire Adventure"], "fitEntireAdventure:")

    def test_focused_track_offers_both_even_without_editing_enabled(self):
        items = self.menu_entries("track", SimpleNamespace())
        self.assertEqual(items["Return to Adventure Overview"], "showAdventureOverview:")
        self.assertEqual(items["Fit Entire Adventure"], "fitEntireAdventure:")

    def test_standalone_editor_does_not_offer_adventure_actions(self):
        for mode in ("track", "overview"):
            with self.subTest(mode=mode):
                items = self.menu_entries(mode, None)
                self.assertNotIn("Fit Entire Adventure", items)
                self.assertNotIn("Return to Adventure Overview", items)

    def make_workspace(self, state):
        view = SimpleNamespace(
            mode=state, track_index=1, replace_plot_info=Mock(), setNeedsDisplay_=Mock(),
            current_extent=lambda: {"min_x": 10, "max_x": 20, "min_y": 10, "max_y": 20},
            current_tile_zoom=lambda: 14,
        )
        tracks = [SimpleNamespace(nr=1), SimpleNamespace(nr=2)]
        fresh_info = {"metadata": {"extent_mercator": {
            "min_x": 0, "max_x": 100, "min_y": 0, "max_y": 100,
        }}, "tile_zoom_level": 8}
        workspace = SimpleNamespace(
            plot_view=view, map_state=state, focused_track_nr=2 if state == "track" else None,
            track_viewports={}, overview_viewport={"extent": {"saved": True}, "zoom": 9},
            gpx_controller=SimpleNamespace(
                visible_tracks=Mock(return_value=tracks),
                interactive_plot_info=Mock(return_value=fresh_info), selected_nrs=[2],
            ),
            _fit_workspace_plot_info=Mock(return_value=fresh_info), _set_title=Mock(),
        )
        workspace._remember_workspace_viewport = lambda: AdventureMapController._remember_workspace_viewport(workspace)
        return workspace, fresh_info

    def test_fit_ignores_saved_zoom_from_overview_and_focused_track(self):
        for state in ("overview", "track"):
            with self.subTest(state=state):
                workspace, fresh_info = self.make_workspace(state)
                result = AdventureMapController.show_workspace_overview(workspace, reset_viewport=True)
                self.assertIs(result, workspace.plot_view)
                workspace._fit_workspace_plot_info.assert_called_once_with(
                    fresh_info, workspace.gpx_controller.visible_tracks.return_value, None
                )
                self.assertIsNone(workspace.overview_viewport)
                self.assertEqual(workspace.map_state, "overview")
                self.assertEqual(workspace.plot_view.mode, "overview")
                self.assertIsNone(workspace.focused_track_nr)
                self.assertEqual(workspace.gpx_controller.selected_nrs, [2])
                workspace.plot_view.replace_plot_info.assert_called_once_with(fresh_info)
                workspace.plot_view.setNeedsDisplay_.assert_called_once_with(True)
                if state == "track":
                    self.assertEqual(workspace.track_viewports[2]["zoom"], 14)

    def test_return_preserves_previous_overview_and_current_track_position(self):
        workspace, fresh_info = self.make_workspace("track")
        saved = workspace.overview_viewport
        AdventureMapController.show_workspace_overview(workspace)
        workspace._fit_workspace_plot_info.assert_called_once_with(
            fresh_info, workspace.gpx_controller.visible_tracks.return_value, saved
        )
        self.assertIs(workspace.overview_viewport, saved)
        self.assertEqual(workspace.track_viewports[2]["zoom"], 14)

    def test_no_plot_model_does_not_clear_saved_overview(self):
        workspace, _ = self.make_workspace("track")
        saved = workspace.overview_viewport
        workspace.gpx_controller.interactive_plot_info.return_value = None
        self.assertIsNone(AdventureMapController.show_workspace_overview(workspace, reset_viewport=True))
        self.assertIs(workspace.overview_viewport, saved)
        workspace.plot_view.replace_plot_info.assert_not_called()

    def test_context_actions_use_different_navigation_modes(self):
        workspace = SimpleNamespace(show_workspace_overview=Mock(return_value=object()))
        view = SimpleNamespace(adventure_workspace_delegate=workspace, controller=SimpleNamespace(set_status=Mock()))
        PlotView.showAdventureOverview_.callable(view, None)
        workspace.show_workspace_overview.assert_called_once_with()
        workspace.show_workspace_overview.reset_mock()
        PlotView.fitEntireAdventure_.callable(view, None)
        workspace.show_workspace_overview.assert_called_once_with(reset_viewport=True)
        view.controller.set_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
