"""Tests for track-aware media placement in stage time-lapse mode."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from GPSTrackShow import (
    GPSTrackShowApp,
    PilgrimWalkState,
    advance_time_lapse_progress,
    best_media_corner_layout,
    clear_corner_rect_options,
    config_from_options,
    derive_clock_date_text,
    endpoint_tangent,
    fixed_arrow_normal,
    inset_rect,
    largest_clear_corner_rects,
    map_plot_rect,
    normalize_transition,
    previous_displayable_playlist_index,
    pilgrim_orientation_for_tangent,
    pilgrim_motion_threshold,
    relation_title_band,
    resolve_map_window,
    set_runtime_map_window,
    should_show_single_window_stage_overview,
    slideshow_transition_completion_allowed,
    time_lapse_marker_style,
    time_lapse_media_minimum_pending,
)


class TimeLapseMediaPlacementTests(unittest.TestCase):
    def test_automatic_window_mode_uses_screen_count(self):
        self.assertFalse(resolve_map_window(None, 1))
        self.assertTrue(resolve_map_window(None, 2))
        self.assertTrue(resolve_map_window(True, 1))
        self.assertFalse(resolve_map_window(False, 2))

    def test_single_window_stage_overview_is_only_for_fresh_stages(self):
        self.assertTrue(should_show_single_window_stage_overview(False, 0.0, False))
        self.assertFalse(should_show_single_window_stage_overview(True, 0.0, False))
        self.assertFalse(should_show_single_window_stage_overview(False, 0.4, False))
        self.assertFalse(should_show_single_window_stage_overview(False, 0.0, True))

    def test_time_lapse_overview_transition_may_schedule_motion(self):
        self.assertTrue(slideshow_transition_completion_allowed(True, True))
        self.assertFalse(slideshow_transition_completion_allowed(True, False))
        self.assertTrue(slideshow_transition_completion_allowed(False, False))

    def test_closing_map_window_does_not_quit_player(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        map_window = object()
        app.map_window = map_window
        app.running = True
        actions = []
        app._deactivate_separate_map_window = lambda window_already_closing=False: actions.append(
            ("map", window_already_closing)
        )
        app.quit = lambda: actions.append(("quit", True))
        app.window_will_close(map_window, "map")
        self.assertEqual(actions, [("map", True)])
        app.window_will_close(object(), "photo")
        self.assertEqual(actions[-1], ("quit", True))

    def test_w_creates_and_removes_the_optional_map_window(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = type("ConfigStub", (), {"join_windows": False})()
        app.map_window = None
        actions = []
        app._create_separate_map_window = lambda: actions.append("create")
        app._deactivate_separate_map_window = lambda: actions.append("remove")
        app._show_temporary_status_overlay = lambda text, _seconds: actions.append(text)
        app._toggle_window_mode()
        self.assertEqual(actions[:2], ["create", "Separate overview window"])

        app.map_window = object()
        app._toggle_window_mode()
        self.assertEqual(actions[-2:], ["remove", "Single window"])

    def test_retired_map_cleanup_uses_bound_callbacks_and_retains_wrappers(self):
        class WindowStub:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class PresenterStub:
            def __init__(self):
                self.disposed = False

            def dispose(self):
                self.disposed = True

        class ViewStub:
            def __init__(self):
                self.retired = False

            def _retire_content(self):
                self.retired = True

        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        window = WindowStub()
        presenter = PresenterStub()
        view = ViewStub()
        delegate = object()
        resource = {
            "window": window,
            "presenter": presenter,
            "delegate": delegate,
            "view": view,
            "state": "pending_close",
            "disposed": False,
        }
        app.retired_map_resources = [resource]
        app.window_delegates = [delegate]
        scheduled = []
        app.schedule_callback = lambda delay, callback: scheduled.append((delay, callback))

        app._close_retired_map_windows()

        self.assertTrue(window.closed)
        self.assertEqual(resource["state"], "closed")
        self.assertEqual(scheduled[0][0], 0.05)
        self.assertIs(scheduled[0][1].__self__, app)
        self.assertEqual(scheduled[0][1].__func__, GPSTrackShowApp._dispose_retired_map_windows)

        scheduled[0][1]()
        self.assertTrue(presenter.disposed)
        self.assertTrue(view.retired)
        self.assertTrue(resource["disposed"])
        self.assertIs(app.retired_map_resources[0], resource)

    def test_w_parks_map_window_without_closing_native_object(self):
        class WindowStub:
            def __init__(self):
                self.ordered_out = False
                self.closed = False
                self.delegate_value = object()

            def delegate(self):
                return self.delegate_value

            def parentWindow(self):
                return None

            def orderOut_(self, _sender):
                self.ordered_out = True

            def close(self):
                self.closed = True

        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = type("ConfigStub", (), {"join_windows": False, "mapwindow": True})()
        app.map_window = WindowStub()
        app.map_presenter = object()
        app.time_map_view = object()
        app.parked_map_resource = None
        app.retired_map_resources = []
        app.screen_swap = False
        app.time_lapse_active = False
        app.time_lapse_stage = None
        app.role_targets = {}
        app._update_window_titles = lambda *_args: None
        app.schedule_callback = lambda *_args: self.fail("parking must not schedule window destruction")

        original_window = app.map_window
        original_delegate = original_window.delegate()
        app._deactivate_separate_map_window()

        self.assertTrue(original_window.ordered_out)
        self.assertFalse(original_window.closed)
        self.assertIs(original_window.delegate(), original_delegate)
        self.assertIs(app.parked_map_resource["window"], original_window)
        self.assertIsNone(app.map_window)
        self.assertEqual(app.retired_map_resources, [])

    def test_arrow_navigation_resumes_only_in_previous_automatic_mode(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.manual_mode = False
        app.paused = False
        app.time_lapse_stage = object()
        app.time_lapse_last_tick = None
        ticks = []
        app._time_lapse_tick = lambda: ticks.append("tick")
        app._continue_time_lapse_after_navigation()
        self.assertEqual(ticks, ["tick"])
        self.assertFalse(app.manual_mode)

        app.manual_mode = True
        app._continue_time_lapse_after_navigation()
        self.assertEqual(ticks, ["tick"])
        self.assertTrue(app.manual_mode)

    def test_axes_metadata_excludes_header_from_map_placement(self):
        image_rect = (100.0, 50.0, 800.0, 400.0)
        metadata = {
            "axes_box_fraction": {
                "left": 0.10,
                "bottom": 0.20,
                "width": 0.80,
                "height": 0.60,
            }
        }
        for actual, expected in zip(map_plot_rect(image_rect, metadata), (180.0, 130.0, 640.0, 240.0)):
            self.assertAlmostEqual(actual, expected)

    def test_relation_title_uses_top_five_percent_of_map_axes(self):
        image_rect = (100.0, 50.0, 800.0, 400.0)
        metadata = {
            "axes_box_fraction": {
                "left": 0.10,
                "bottom": 0.20,
                "width": 0.80,
                "height": 0.60,
            },
            "media_clear_boxes": {"margin_fraction": 0.05},
        }
        actual = relation_title_band(image_rect, metadata)
        expected = (180.0, 358.0, 640.0, 12.0)
        for value, wanted in zip(actual, expected):
            self.assertAlmostEqual(value, wanted)

    def test_missing_axes_metadata_uses_full_image_and_all_corners(self):
        image_rect = (100.0, 50.0, 800.0, 400.0)
        placement = inset_rect(map_plot_rect(image_rect, {}), 0.05)
        candidates = clear_corner_rect_options(placement, [])
        self.assertEqual(map_plot_rect(image_rect, {}), image_rect)
        self.assertEqual(set(candidates), {"top_right", "top_left", "bottom_right", "bottom_left"})

    def test_margin_uses_map_dimensions_not_window_dimensions(self):
        self.assertEqual(inset_rect((100.0, 200.0, 400.0, 200.0), 0.05), (120.0, 210.0, 360.0, 180.0))

    def test_clear_frontier_does_not_depend_on_point_density(self):
        placement = (0.0, 0.0, 100.0, 100.0)
        sparse = [(0.0, 50.0), (100.0, 50.0)]
        dense = [(0.0, 50.0), (20.0, 50.0), (40.0, 50.0), (60.0, 50.0), (80.0, 50.0), (100.0, 50.0)]
        self.assertEqual(clear_corner_rect_options(placement, sparse), clear_corner_rect_options(placement, dense))

    def test_stage_stores_largest_clear_box_for_every_corner(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        route = [(300.0, 0.0), (300.0, 600.0)]
        clear_rects = largest_clear_corner_rects(placement, route)
        self.assertEqual(set(clear_rects), {"top_right", "top_left", "bottom_right", "bottom_left"})
        self.assertGreater(clear_rects["top_right"][2], clear_rects["top_left"][2])
        self.assertGreater(clear_rects["bottom_right"][2], clear_rects["bottom_left"][2])

    def test_stage_keeps_width_height_frontier_for_different_aspect_ratios(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        route = [(200.0, 500.0), (800.0, 100.0)]
        options = clear_corner_rect_options(placement, route)
        self.assertGreater(len(options["top_left"]), 1)
        self.assertTrue(any(rect[2] < 250.0 and rect[3] > 500.0 for rect in options["top_left"]))

    def test_each_image_uses_corner_allowing_largest_display(self):
        clear_rects = {
            "top_right": (200.0, 200.0, 800.0, 400.0),
            "top_left": (0.0, 0.0, 400.0, 600.0),
            "bottom_right": (600.0, 0.0, 400.0, 150.0),
            "bottom_left": (0.0, 0.0, 200.0, 400.0),
        }
        landscape_corner, _outer, landscape = best_media_corner_layout(
            clear_rects, (1000.0, 600.0), 0.5, (1600.0, 900.0)
        )
        portrait_corner, _outer, portrait = best_media_corner_layout(
            clear_rects, (1000.0, 600.0), 0.5, (900.0, 1600.0)
        )
        self.assertEqual(landscape_corner, "top_right")
        self.assertEqual(portrait_corner, "top_left")
        self.assertGreater(landscape[2], 500.0)
        self.assertGreater(portrait[3], 300.0)

    def test_backward_navigation_uses_playlist_rows_without_image_history(self):
        lines = [
            "#Overviewmap: overview.png",
            "#Datum: 01.01.2024",
            "#Map: track-1.png",
            "photo-1.jpeg 10:00 - - -",
            "photo-2.jpeg 10:10 - - -",
        ]
        self.assertEqual(previous_displayable_playlist_index(lines, 4), 3)
        self.assertEqual(previous_displayable_playlist_index(lines, 3), 2)
        self.assertIsNone(previous_displayable_playlist_index(lines, 2))

    def test_arrow_moves_while_media_is_visible_without_overlap(self):
        progress, event_due, blocked = advance_time_lapse_progress(0.2, 3.0, 30.0, 0.8, True)
        self.assertAlmostEqual(progress, 0.3)
        self.assertFalse(event_due)
        self.assertFalse(blocked)

    def test_arrow_waits_at_next_media_until_previous_media_finishes(self):
        progress, event_due, blocked = advance_time_lapse_progress(0.2, 3.0, 30.0, 0.25, True)
        self.assertEqual(progress, 0.25)
        self.assertFalse(event_due)
        self.assertTrue(blocked)
        progress, event_due, blocked = advance_time_lapse_progress(progress, 0.02, 30.0, 0.25, False)
        self.assertEqual(progress, 0.25)
        self.assertTrue(event_due)
        self.assertFalse(blocked)

    def test_media_remains_replaceable_after_its_minimum_duration(self):
        self.assertTrue(time_lapse_media_minimum_pending(True, 103.0, 102.0))
        self.assertFalse(time_lapse_media_minimum_pending(True, 103.0, 104.0))
        progress, event_due, blocked = advance_time_lapse_progress(0.2, 3.0, 30.0, 0.25, False)
        self.assertEqual(progress, 0.25)
        self.assertTrue(event_due)
        self.assertFalse(blocked)

    def test_stage_arrow_uses_one_start_to_end_orientation(self):
        tangent = endpoint_tangent([(10.0, 20.0), (50.0, 60.0), (110.0, 20.0)])
        self.assertEqual(tangent, (1.0, 0.0))

    def test_pilgrim_uses_arrow_orientation_and_faces_track_direction(self):
        self.assertEqual(fixed_arrow_normal((1.0, 0.0)), (0.0, -1.0))
        self.assertEqual(pilgrim_orientation_for_tangent((1.0, 0.0)), (0.0, False))
        self.assertEqual(pilgrim_orientation_for_tangent((-1.0, 0.0)), (0.0, True))
        rotation, mirrored = pilgrim_orientation_for_tangent((0.0, 1.0))
        self.assertAlmostEqual(rotation, 90.0)
        self.assertTrue(mirrored)

    def test_overview_always_uses_arrow_marker(self):
        self.assertEqual(time_lapse_marker_style("pilgrim", overview=False), "pilgrim")
        self.assertEqual(time_lapse_marker_style("pilgrim", overview=True), "arrow")
        self.assertEqual(time_lapse_marker_style("arrow", overview=True), "arrow")

    def test_default_and_validation_for_media_fraction(self):
        with TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            control_file = project_dir / "slides.lst"
            control_file.write_text("#Datum: 01.01.2024\n", encoding="utf-8")
            config = config_from_options(project_dir, inputlist=control_file)
            set_runtime_map_window(config, True)
            self.assertTrue(config.mapwindow)
            set_runtime_map_window(config, False)
            self.assertFalse(config.mapwindow)
            self.assertEqual(config.time_lapse_media_min_fraction, 0.5)
            self.assertEqual(config.time_lapse_media_max_fraction, 0.5)
            self.assertEqual(config.time_lapse_marker, "pilgrim")
            self.assertTrue(config.time_lapse_overview_as_media)
            self.assertFalse(config.track_map_before_media)
            self.assertEqual(
                config_from_options(project_dir, inputlist=control_file, transition="blend").transition.value,
                "BLEND",
            )
            self.assertEqual(normalize_transition(" blend "), "BLEND")
            arrow_config = config_from_options(
                project_dir,
                inputlist=control_file,
                time_lapse_marker="arrow",
            )
            self.assertEqual(arrow_config.time_lapse_marker, "arrow")
            self.assertTrue(
                config_from_options(
                    project_dir,
                    inputlist=control_file,
                    track_map_before_media=True,
                ).track_map_before_media
            )
            self.assertFalse(
                config_from_options(
                    project_dir,
                    inputlist=control_file,
                    time_lapse_overview_as_media=False,
                ).time_lapse_overview_as_media
            )
            with self.assertRaises(ValueError):
                config_from_options(
                    project_dir,
                    inputlist=control_file,
                    time_lapse_media_min_fraction=1.1,
                )
            with self.assertRaises(ValueError):
                config_from_options(
                    project_dir,
                    inputlist=control_file,
                    time_lapse_marker="unknown",
                )

    def test_pilgrim_stands_and_resumes_with_frame_three(self):
        state = PilgrimWalkState()
        self.assertEqual(state.update((100.0, 100.0), 0.0, 2.0), 0)
        self.assertEqual(state.update((103.0, 100.0), 0.02, 2.0), 3)
        self.assertEqual(state.update((107.0, 100.0), 0.12, 2.0), 4)
        self.assertEqual(state.update((107.0, 100.0), 0.23, 2.0), 0)
        self.assertEqual(state.update((110.0, 100.0), 0.24, 2.0), 3)

    def test_pilgrim_walk_cycle_wraps_from_frame_eight_to_one(self):
        state = PilgrimWalkState()
        state.update((0.0, 0.0), 0.0, 1.0)
        self.assertEqual(state.update((2.0, 0.0), 0.01, 1.0), 3)
        self.assertEqual(state.update((4.0, 0.0), 0.51, 1.0), 8)
        self.assertEqual(state.update((6.0, 0.0), 0.61, 1.0), 1)

    def test_pilgrim_motion_tolerance_scales_with_resolution(self):
        small = pilgrim_motion_threshold(640.0, 480.0)
        large = pilgrim_motion_threshold(3840.0, 2160.0)
        self.assertGreaterEqual(small, 1.0)
        self.assertGreater(large, small)
        state = PilgrimWalkState()
        state.update((10.0, 10.0), 0.0, large)
        self.assertEqual(state.update((10.0 + large / 2.0, 10.0), 0.11, large), 0)

    def test_time_lapse_clock_accepts_plain_date_context(self):
        self.assertEqual(derive_clock_date_text({}, "23.05.2020"), "23.05.2020")
        self.assertEqual(
            derive_clock_date_text({}, "Samstag, 23.05.2020"),
            "23.05.2020",
        )


if __name__ == "__main__":
    unittest.main()
