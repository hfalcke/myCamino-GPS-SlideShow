"""Elevation profiles follow the active track; help has an explicit lifetime."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import ElevationProfileView, GPXEditorController, PlotView


def invoke(method, *args):
    return getattr(method, "callable", method)(*args)


class ElevationProfileWindowTests(unittest.TestCase):
    def profile(self):
        first = SimpleNamespace(nr=1, name="First stage", display_label="Track #1")
        selected = SimpleNamespace(nr=26, name="Selected stage", display_label="Track #26")
        controller = SimpleNamespace(
            tracks=[first, selected], visible_tracks=Mock(return_value=[first, selected]),
            selected_tracks=Mock(return_value=[]), set_status=Mock(),
        )
        plot = SimpleNamespace(
            mode="track", adventure_workspace_delegate=object(), controller=controller,
            plot_info={"current_track_nr": 26}, track_index=0,
            track_sequence=lambda: [selected], display_tracks=Mock(return_value=[first, selected]),
        )
        plot.current_track_for_title = lambda: invoke(PlotView.current_track_for_title, plot)
        profile = SimpleNamespace(controller=controller, plot_view=plot)
        profile.shows_all_tracks = lambda: invoke(ElevationProfileView.shows_all_tracks, profile)
        profile.current_track = lambda: invoke(ElevationProfileView.current_track, profile)
        profile.profile_tracks = lambda: invoke(ElevationProfileView.profile_tracks, profile)
        return profile, first, selected

    def test_track_26_title_ignores_neighbour_track_1(self):
        profile, _first, selected = self.profile()
        self.assertIs(profile.current_track(), selected)
        self.assertEqual(invoke(ElevationProfileView.profile_title, profile),
                         "Track #26: Selected stage - Elevation Profile")
        self.assertEqual(profile.profile_tracks(), [selected])
        profile.plot_view.display_tracks.assert_not_called()

    def test_profile_data_processes_only_the_active_track(self):
        profile, _first, selected = self.profile()
        point = SimpleNamespace(source_index=170, lat=47.3, lon=5.0, cumulative_distance_km=12.5, elevation_m=200)
        profile.controller.display_processed_track = Mock(return_value=SimpleNamespace(
            raw_points=[SimpleNamespace(source_index=170)], points=[point], length_km=25,
        ))
        rows, ranges, length = invoke(ElevationProfileView.profile_data, profile)
        self.assertEqual(length, 25)
        self.assertEqual(ranges, {26: (0, 25)})
        self.assertEqual(rows[0]["distance"], 12.5)
        self.assertIs(rows[0]["track"], selected)
        profile.controller.display_processed_track.assert_called_once_with(selected)

    def test_overview_still_contains_all_visible_tracks(self):
        profile, first, selected = self.profile()
        profile.plot_view.mode = "overview"
        self.assertEqual(profile.profile_tracks(), [first, selected])
        self.assertEqual(invoke(ElevationProfileView.profile_title, profile), "Overview Elevation Profile")

    def test_switching_tracks_updates_subject_without_recreating_profile(self):
        profile, first, selected = self.profile()
        self.assertEqual(profile.profile_tracks(), [selected])
        profile.plot_view.plot_info["current_track_nr"] = 1
        self.assertEqual(profile.profile_tracks(), [first])
        self.assertIn("Track #1: First stage", invoke(ElevationProfileView.profile_title, profile))

    def test_no_active_track_does_not_silently_use_first_visible_track(self):
        profile, _first, _selected = self.profile()
        profile.plot_view.plot_info = {}
        profile.plot_view.track_sequence = lambda: []
        self.assertEqual(profile.profile_tracks(), [])
        self.assertEqual(invoke(ElevationProfileView.profile_title, profile), "Track Elevation Profile")

    def test_map_replacement_resynchronizes_open_profile_without_new_tiles(self):
        synchronize = Mock()
        plot = SimpleNamespace(
            controller=SimpleNamespace(sync_elevation_profile_to_plot_view=synchronize),
            reset_plot_interaction=Mock(), refresh_interactive_tile_zoom=Mock(),
            ensure_interactive_tile_store_provider=Mock(), clone_plot_info=copy.deepcopy,
            interactive_tile_generation=0, interactive_tile_target_keys=set(),
            cancel_interactive_tile_requests=Mock(), schedule_interactive_tiles=Mock(),
            setNeedsDisplay_=Mock(),
        )
        PlotView.replace_plot_info(plot, {"image": object(), "current_track_nr": 26})
        synchronize.assert_called_once_with(plot)
        plot.schedule_interactive_tiles.assert_not_called()

    def test_resynchronization_updates_window_title_and_visible_range(self):
        window, view, plot = Mock(), Mock(), object()
        view.profile_title.return_value = "Track #26: Selected stage - Elevation Profile"
        controller = SimpleNamespace(existing_elevation_profile_for_plot_view=Mock(return_value=(window, view)))
        GPXEditorController.sync_elevation_profile_to_plot_view(controller, plot)
        window.setTitle_.assert_called_once_with("Track #26: Selected stage - Elevation Profile")
        view.sync_to_map_extent.assert_called_once()

    def test_help_and_h_release_create_no_auto_hide_timer(self):
        help_window = Mock()
        profile = SimpleNamespace(profile_help_window=help_window, window=lambda: None)
        event = SimpleNamespace(charactersIgnoringModifiers=lambda: "h")
        with patch("GPXEditor.NSTimer") as timer:
            invoke(ElevationProfileView.show_profile_help, profile)
            invoke(ElevationProfileView.keyUp_, profile, event)
            timer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.assert_not_called()
        help_window.makeKeyAndOrderFront_.assert_called_once()
        help_window.orderOut_.assert_not_called()
        help_window.close.assert_not_called()

    def test_closing_parent_profile_closes_its_help_and_unregisters_it(self):
        profile = ElevationProfileView.alloc().init()
        controller = SimpleNamespace(unregister_auxiliary_window=Mock())
        help_window = Mock()
        profile.controller = controller
        profile.profile_help_window = help_window
        profile.profile_help_text_view = Mock()
        profile.profile_help_delegate = Mock()
        GPXEditorController.plot_window_closing(controller, profile)
        controller.unregister_auxiliary_window.assert_called_once_with(help_window)
        help_window.close.assert_called_once()
        self.assertIsNone(profile.profile_help_window)
        self.assertIsNone(profile.profile_help_text_view)
        self.assertIsNone(profile.profile_help_delegate)


if __name__ == "__main__":
    unittest.main()
