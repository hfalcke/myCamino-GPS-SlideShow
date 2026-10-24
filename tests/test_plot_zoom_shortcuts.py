"""Consistent selection, current-track, and Adventure zoom in both plot views."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import (
    NSEventModifierFlagCommand, NSEventModifierFlagControl,
    NSEventModifierFlagOption, NSEventModifierFlagShift,
)

from GPXEditor import ElevationProfileView, PlotView, plot_zoom_key_action


def invoke(method, *args):
    return getattr(method, "callable", method)(*args)


def key_event(key, flags=0):
    return SimpleNamespace(
        charactersIgnoringModifiers=lambda: key, characters=lambda: key,
        keyCode=lambda: 0, modifierFlags=lambda: flags, isARepeat=lambda: False,
    )


class PlotZoomShortcutTests(unittest.TestCase):
    def test_both_shift_encodings_select_the_same_zoom_action(self):
        for key, flags, expected in (
            ("z", 0, "selection"), ("Z", NSEventModifierFlagShift, "track"),
            ("z", NSEventModifierFlagShift, "track"), ("Z", 0, "track"),
            ("A", NSEventModifierFlagShift, "all"), ("a", NSEventModifierFlagShift, "all"),
            ("A", 0, "all"), ("a", 0, None),
        ):
            with self.subTest(key=key, flags=flags):
                self.assertEqual(plot_zoom_key_action(key, flags), expected)

    def test_menu_and_editing_modifiers_are_not_consumed(self):
        for modifier in (NSEventModifierFlagCommand, NSEventModifierFlagControl, NSEventModifierFlagOption):
            for key in ("z", "Z", "a", "A"):
                self.assertIsNone(plot_zoom_key_action(key, modifier))

    def test_map_key_dispatch_keeps_anchor_and_undo_separate(self):
        plot = PlotView.alloc().init()
        plot.adventure_workspace_delegate = None
        plot.mode = "overview"
        plot.track_index = 0
        plot.cursor = (2, object(), object())
        plot.inspector = None
        plot.marker = None
        plot.controller = SimpleNamespace(
            tracks=[], set_anchor_from_point=Mock(), undo_=Mock(), redo_=Mock(),
        )
        for key, flags, method in (
            ("z", 0, "zoom_to_selected"),
            ("z", NSEventModifierFlagShift, "reset_view"),
            ("A", NSEventModifierFlagShift, "fitEntireAdventure_"),
            ("a", NSEventModifierFlagShift, "fitEntireAdventure_"),
        ):
            with self.subTest(key=key, flags=flags), patch.object(PlotView, method) as action:
                PlotView.keyDown_(plot, key_event(key, flags))
                action.assert_called_once()
        plot.controller.set_anchor_from_point.assert_not_called()
        PlotView.keyDown_(plot, key_event("a"))
        plot.controller.set_anchor_from_point.assert_called_once_with(plot.cursor[1])
        PlotView.keyDown_(plot, key_event("z", NSEventModifierFlagCommand))
        PlotView.keyDown_(plot, key_event("z", NSEventModifierFlagCommand | NSEventModifierFlagShift))
        plot.controller.undo_.assert_called_once_with(None)
        plot.controller.redo_.assert_called_once_with(None)

    def test_map_shift_z_uses_current_track_not_neighbours_and_preserves_selection(self):
        current = SimpleNamespace(nr=26)
        plot = SimpleNamespace(
            mode="overview", current_track_for_title=lambda: current,
            controller=SimpleNamespace(extent_for_track_records=Mock(return_value={"current": True})),
            render_extent=Mock(), marker=3, cursor=(7, object(), current), zoom=8,
        )
        PlotView.reset_view(plot)
        plot.controller.extent_for_track_records.assert_called_once_with([current])
        self.assertEqual(plot.render_extent.call_args.args, ({"current": True},))
        self.assertEqual(plot.marker, 3)
        self.assertEqual(plot.cursor[0], 7)

    def test_no_current_track_does_not_silently_fit_whole_adventure(self):
        plot = SimpleNamespace(
            current_track_for_title=lambda: None, cursor=None,
            controller=SimpleNamespace(selected_tracks=lambda: [], set_status=Mock()),
            render_extent=Mock(),
        )
        PlotView.reset_view(plot)
        plot.render_extent.assert_not_called()
        self.assertIn("Select a track", plot.controller.set_status.call_args.args[0])

    def test_map_z_uses_marked_points_even_in_overview(self):
        points = [object() for _ in range(10)]
        current = SimpleNamespace(points=lambda: points)
        plot = SimpleNamespace(
            mode="overview", current_track_for_title=lambda: current,
            selected_ranges_for_track=lambda track: [(3, 5)],
            extent_for_points=Mock(return_value={"selection": True}), render_extent=Mock(),
        )
        PlotView.zoom_to_selected(plot)
        plot.extent_for_points.assert_called_once_with(points[3:6])
        self.assertEqual(plot.render_extent.call_args.args, ({"selection": True},))

    def test_map_z_without_point_range_fits_active_track_not_first_neighbour(self):
        current = SimpleNamespace(nr=26)
        plot = SimpleNamespace(
            mode="track", current_track_for_title=lambda: current,
            selected_ranges_for_track=lambda track: [],
            controller=SimpleNamespace(extent_for_track_records=Mock(return_value={"current": True})),
            render_extent=Mock(),
        )
        PlotView.zoom_to_selected(plot)
        plot.controller.extent_for_track_records.assert_called_once_with([current])

    def test_standalone_a_installs_overview_in_the_same_view(self):
        tracks = [object(), object()]
        info = {"metadata": {"extent_mercator": {"all": True}}}
        plot = SimpleNamespace(
            mode="track", adventure_workspace_delegate=None,
            current_track_for_title=lambda: SimpleNamespace(nr=26),
            controller=SimpleNamespace(
                visible_tracks=lambda: tracks, interactive_plot_info=Mock(return_value=info),
            ), replace_plot_info=Mock(), render_extent=Mock(),
        )
        invoke(PlotView.fitEntireAdventure_, plot, None)
        self.assertEqual(plot.mode, "overview")
        self.assertEqual(info["current_track_nr"], 26)
        plot.controller.interactive_plot_info.assert_called_once_with("overview", tracks_override=tracks)
        plot.replace_plot_info.assert_called_once_with(info)
        self.assertEqual(plot.render_extent.call_args.args, ({"all": True},))

    def test_standalone_overview_remembers_current_track_with_multiple_selected(self):
        first, current = SimpleNamespace(nr=1), SimpleNamespace(nr=26)
        plot = SimpleNamespace(
            mode="overview", plot_info={"current_track_nr": 26},
            controller=SimpleNamespace(tracks=[first, current], selected_tracks=lambda: [first, current]),
        )
        self.assertIs(PlotView.current_track_for_title(plot), current)


class ProfileZoomShortcutTests(unittest.TestCase):
    def profile(self, map_mode="overview"):
        first = SimpleNamespace(nr=1, name="First")
        current = SimpleNamespace(nr=26, name="Current")
        plot = SimpleNamespace(
            mode=map_mode, current_track_for_title=lambda: current,
            selected_ranges_for_track=Mock(return_value=[]),
        )
        controller = SimpleNamespace(
            visible_tracks=lambda: [first, current], selected_nrs=[26],
            set_status=Mock(), undo_=Mock(), redo_=Mock(),
        )
        view = SimpleNamespace(
            plot_view=plot, controller=controller, profile_scope=None,
            x_zoom=8, x_center=25, window=Mock(return_value=Mock()), setNeedsDisplay_=Mock(),
        )
        for name in ("current_track", "shows_all_tracks", "profile_tracks", "profile_title", "set_profile_scope"):
            method = getattr(ElevationProfileView, name)
            setattr(view, name, lambda *args, method=method: invoke(method, view, *args))
        return view, first, current

    def test_shift_z_then_a_switch_scope_without_moving_the_map(self):
        for mode in ("overview", "track"):
            with self.subTest(mode=mode):
                view, first, current = self.profile(mode)
                invoke(ElevationProfileView.keyDown_, view, key_event("z", NSEventModifierFlagShift))
                self.assertEqual(view.profile_tracks(), [current])
                self.assertEqual((view.x_zoom, view.x_center), (1, None))
                self.assertIn("#26", view.profile_title())
                invoke(ElevationProfileView.keyDown_, view, key_event("A", NSEventModifierFlagShift))
                self.assertEqual(view.profile_tracks(), [first, current])
                self.assertEqual((view.x_zoom, view.x_center), (1, None))
                self.assertEqual(view.profile_title(), "Overview Elevation Profile")
                self.assertEqual(view.plot_view.mode, mode)
                view.window().setTitle_.assert_called_with("Overview Elevation Profile")

    def test_z_fits_point_range_inside_all_tracks(self):
        view, first, current = self.profile()
        view.plot_view.selected_ranges_for_track.return_value = [(3, 5)]
        rows = [
            {"track": track, "index": index, "distance": offset + index}
            for track, offset in ((first, 0), (current, 100)) for index in range(11)
        ]
        view.profile_data = lambda: (rows, {1: (0, 100), 26: (100, 200)}, 200)
        invoke(ElevationProfileView.zoom_to_selected, view)
        self.assertEqual(view.x_center, 104)
        self.assertGreater(view.x_zoom, 1)
        left, right = invoke(ElevationProfileView.visible_distance_range, view, 200)
        self.assertLessEqual(left, 103)
        self.assertGreaterEqual(right, 105)
        self.assertLess(right - left, 10)

    def test_z_without_point_range_fits_selected_tracks(self):
        view, _first, _current = self.profile()
        view.profile_data = lambda: ([{}], {1: (0, 100), 26: (100, 200)}, 200)
        invoke(ElevationProfileView.zoom_to_selected, view)
        self.assertEqual(view.x_center, 150)
        self.assertAlmostEqual(view.x_zoom, 200 / 110)

    def test_no_current_track_does_not_change_profile_scope(self):
        view, _first, _current = self.profile()
        view.current_track = lambda: None
        invoke(ElevationProfileView.keyDown_, view, key_event("Z"))
        self.assertIsNone(view.profile_scope)
        self.assertEqual(view.x_zoom, 8)

    def test_undo_redo_do_not_change_profile_scope(self):
        view, _first, _current = self.profile()
        for flags in (NSEventModifierFlagCommand, NSEventModifierFlagCommand | NSEventModifierFlagShift):
            invoke(ElevationProfileView.keyDown_, view, key_event("z", flags))
        view.controller.undo_.assert_called_once_with(None)
        view.controller.redo_.assert_called_once_with(None)
        self.assertIsNone(view.profile_scope)


if __name__ == "__main__":
    unittest.main()
