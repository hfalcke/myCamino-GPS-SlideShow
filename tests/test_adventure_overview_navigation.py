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

    def test_hidden_track_menu_reflects_display_policy(self):
        self.assertEqual(self.menu_entries('overview', SimpleNamespace(show_hidden_tracks=False))['Show Hidden Tracks'],
                         'toggleWorkspaceHiddenTracks:')
        self.assertEqual(self.menu_entries('track', SimpleNamespace(show_hidden_tracks=True))['Hide Hidden Tracks'],
                         'toggleWorkspaceHiddenTracks:')
        self.assertNotIn('Show Hidden Tracks', self.menu_entries('overview', None))

    def test_hidden_track_toggle_only_redraws_existing_vector_views(self):
        tracks = [SimpleNamespace(nr=1, hidden=False), SimpleNamespace(nr=2, hidden=True)]
        workspace = SimpleNamespace(show_hidden_tracks=False,
            plot_view=SimpleNamespace(setNeedsDisplay_=Mock()),
            gpx_controller=SimpleNamespace(tracks=tracks, visible_tracks=lambda: tracks[:1],
                refresh_elevation_profile_for_plot_view=Mock(), set_status=Mock(), mark_dirty=Mock()))
        self.assertEqual(AdventureMapController.workspace_display_tracks(workspace), tracks[:1])
        toggle = AdventureMapController.toggleWorkspaceHiddenTracks_.callable
        toggle(workspace, None)
        self.assertEqual(AdventureMapController.workspace_display_tracks(workspace), tracks)
        self.assertEqual([track.hidden for track in tracks], [False, True])
        workspace.gpx_controller.mark_dirty.assert_not_called()
        workspace.plot_view.setNeedsDisplay_.assert_called_once_with(True)
        workspace.gpx_controller.refresh_elevation_profile_for_plot_view.assert_called_once_with(workspace.plot_view)
        toggle(workspace, None)
        self.assertEqual(AdventureMapController.workspace_display_tracks(workspace), tracks[:1])

    def test_plot_display_includes_hidden_only_when_requested(self):
        tracks = [SimpleNamespace(nr=1, hidden=False), SimpleNamespace(nr=2, hidden=True)]
        workspace = SimpleNamespace(show_hidden_tracks=False)
        view = SimpleNamespace(mode='overview', adventure_workspace_delegate=workspace,
            controller=SimpleNamespace(tracks=tracks, visible_tracks=lambda: tracks[:1], track_load_timer=None))
        self.assertEqual(PlotView.display_tracks(view), tracks[:1])
        workspace.show_hidden_tracks = True
        self.assertEqual(PlotView.display_tracks(view), tracks)
        view.mode = 'track'
        self.assertEqual(PlotView.display_tracks(view), tracks)

    def test_hidden_routes_are_lighter_gray_and_dashed(self):
        tracks = [SimpleNamespace(nr=1, hidden=False), SimpleNamespace(nr=2, hidden=False),
                  SimpleNamespace(nr=3, hidden=True)]
        geometry = SimpleNamespace(segments=[SimpleNamespace(points=[object(), object()])])
        view = SimpleNamespace(mode='overview', adventure_workspace_delegate=object(),
            controller=SimpleNamespace(selected_nrs=[1]), plot_info={'image': None},
            draw_track=Mock(), draw_segment_end_caps=Mock(), draw_selected_tracks_overlay=Mock())
        PlotView.draw_track_layers(view, [(track, geometry) for track in tracks], Mock())
        colors = [call.args[2] for call in view.draw_track.call_args_list]
        self.assertEqual(len(colors), 2)
        self.assertAlmostEqual(colors[0].redComponent(), 119 / 255)
        self.assertAlmostEqual(colors[1].redComponent(), 170 / 255)
        self.assertAlmostEqual(colors[1].greenComponent(), 170 / 255)
        self.assertAlmostEqual(colors[1].blueComponent(), 170 / 255)
        self.assertFalse(view.draw_track.call_args_list[0].args[4])
        self.assertTrue(view.draw_track.call_args_list[1].args[4])

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

    def test_fit_includes_hidden_extents_only_when_display_enabled(self):
        workspace, _ = self.make_workspace('overview')
        workspace.show_hidden_tracks = True
        workspace.gpx_controller.tracks = [SimpleNamespace(nr=1), SimpleNamespace(nr=2, hidden=True)]
        AdventureMapController.show_workspace_overview(workspace, reset_viewport=True)
        workspace.gpx_controller.interactive_plot_info.assert_called_once_with(
            'overview', tracks_override=workspace.gpx_controller.tracks, include_hidden=True)
        self.assertEqual(workspace._fit_workspace_plot_info.call_args.args[1], workspace.gpx_controller.tracks)

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
