"""Tests for track-aware media placement in stage time-lapse mode."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from GPSTrackShow import (
    GPSTrackShowApp,
    PlaybackPhase,
    PilgrimWalkState,
    advance_time_lapse_progress,
    best_media_corner_layout,
    clear_corner_rect_options,
    config_from_options,
    derive_clock_date_text,
    dynamic_stage_header_lines,
    endpoint_tangent,
    fixed_arrow_normal,
    inset_rect,
    largest_clear_corner_rects,
    map_plot_rect,
    normalize_transition,
    parse_stage_descriptors,
    stage_name_endpoints,
    stage_index_for_playlist_row,
    intro_metadata_from_playlist,
    previous_displayable_playlist_index,
    first_media_coordinate,
    should_restart_time_lapse_stage_at_overview,
    pilgrim_orientation_for_tangent,
    pilgrim_motion_threshold,
    relation_title_band,
    resolve_intro_title_image,
    resolve_map_window,
    set_runtime_map_window,
    simplify_display_path,
    should_show_single_window_stage_overview,
    slideshow_transition_completion_allowed,
    time_lapse_marker_style,
    time_lapse_clock_layout,
    time_lapse_header_title_font_size,
    time_lapse_media_minimum_pending,
    track_display_title,
    track_header_lines,
    track_metadata_supports_clock,
)


class TimeLapseMediaPlacementTests(unittest.TestCase):
    def test_track_header_prefers_endpoint_places_and_formats_metrics(self):
        metadata = {
            "track_name": "JW Internal Name",
            "track_date": "16.07.2026",
            "track_length_km": 21.45,
            "track_duration": "7:03",
            "track_endpoint_places": {
                "start": {"place": "Zubiri"},
                "end": {"place": "Pamplona"},
            },
        }
        self.assertEqual(
            track_display_title(metadata),
            "Zubiri - Pamplona",
        )
        self.assertEqual(
            track_header_lines(metadata),
            (
                "Zubiri - Pamplona",
                "16.07.2026 · 21.4 km - 07:03 h",
            ),
        )
        self.assertEqual(
            track_header_lines(metadata, omit_date=True),
            ("Zubiri - Pamplona", "21.4 km - 07:03 h"),
        )
        self.assertEqual(
            track_display_title(metadata, "track_name"),
            "JW Internal Name",
        )

    def test_track_header_falls_back_to_track_name_without_endpoint_places(self):
        metadata = {
            "track_name": "JW Zubiri - Pamplona",
            "track_length_km": 21.5,
            "track_duration": "06:30",
        }
        self.assertEqual(
            track_header_lines(metadata, omit_date=True),
            ("JW Zubiri - Pamplona", "21.5 km - 06:30 h"),
        )

    def test_adjacent_day_replaces_length_and_duration_in_second_header_line(self):
        metadata = {
            "track_name": "JW Zubiri - Pamplona",
            "track_date": "16.07.2026",
            "track_length_km": 21.5,
            "track_duration": "06:30",
        }
        self.assertEqual(
            track_header_lines(metadata, details_override="Day before"),
            ("JW Zubiri - Pamplona", "16.07.2026 · Day before"),
        )
        self.assertEqual(
            track_header_lines(
                metadata,
                omit_date=True,
                details_override="Day after",
            ),
            ("JW Zubiri - Pamplona", "Day after"),
        )

    def test_media_stage_omits_date_when_clock_is_visible(self):
        metadata = {
            "stage_kind": "media_stage",
            "media_stage_name": "Vichy",
            "header_lines": ["Vichy", "01.04.2023"],
            "media_points": [
                {
                    "lat": 46.1,
                    "lon": 3.4,
                    "time_iso": "2023-04-01T11:51:15+02:00",
                }
            ],
        }
        config = SimpleNamespace(track_title_mode="endpoint_places")
        self.assertEqual(
            dynamic_stage_header_lines(
                metadata,
                config,
                clock_visible=True,
            ),
            ("Vichy",),
        )
        self.assertEqual(
            dynamic_stage_header_lines(
                metadata,
                config,
                clock_visible=False,
            ),
            ("Vichy", "01.04.2023"),
        )

    def test_track_header_uses_locality_instead_of_long_reverse_geocoded_label(self):
        metadata = {
            "track_name": "JW Internal Name",
            "track_endpoint_places": {
                "start": {
                    "place": "Zubiri (Navarra), Calle Mayor 1",
                    "place_details": {
                        "locality": "Zubiri",
                        "administrativeArea": "Navarra",
                        "name": "Calle Mayor 1",
                    },
                },
                "end": {
                    "place": "Pamplona-Iruna (Navarra), Plaza del Castillo",
                    "place_details": {
                        "locality": "Pamplona",
                        "subLocality": "Iruna",
                        "administrativeArea": "Navarra",
                    },
                },
            },
        }
        self.assertEqual(track_display_title(metadata), "Zubiri - Pamplona")

    def test_track_header_uses_another_place_when_locality_is_missing(self):
        metadata = {
            "track_endpoint_places": {
                "start": {
                    "place": "Navarra, Monastery",
                    "place_details": {"areasOfInterest": ["Monastery"]},
                },
                "end": {"place": "Pimbo (Landes), Rue Principale"},
            },
        }
        self.assertEqual(track_display_title(metadata), "Monastery - Pimbo")

    def test_intro_title_image_prefers_configuration_then_first_still(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.jpeg"
            selected = root / "selected.png"
            movie = root / "before.mov"
            for path in (first, selected, movie):
                path.write_bytes(b"media")
            lines = [
                "before.mov | 09:00 | - | -",
                "first.jpeg | 09:01 | - | -",
            ]
            self.assertEqual(
                resolve_intro_title_image(selected, root, lines),
                selected.resolve(),
            )
            self.assertEqual(
                resolve_intro_title_image(None, root, lines),
                first.resolve(),
            )

    def test_automatic_window_mode_uses_screen_count(self):
        self.assertFalse(resolve_map_window(None, 1))
        self.assertTrue(resolve_map_window(None, 2))
        self.assertTrue(resolve_map_window(True, 1))
        self.assertFalse(resolve_map_window(False, 2))

    def test_single_window_stage_overview_is_only_for_fresh_stages(self):
        self.assertTrue(should_show_single_window_stage_overview(False, 0.0, False))
        self.assertTrue(
            should_show_single_window_stage_overview(False, 0.0, False, "")
        )
        self.assertFalse(
            should_show_single_window_stage_overview(
                False,
                0.0,
                False,
                "Day before",
            )
        )
        self.assertFalse(should_show_single_window_stage_overview(True, 0.0, False))
        self.assertFalse(should_show_single_window_stage_overview(False, 0.4, False))
        self.assertFalse(should_show_single_window_stage_overview(False, 0.0, True))

    def test_media_only_overview_continues_into_static_stage(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.time_lapse_stage = SimpleNamespace(relation="")
        app.time_lapse_active = True
        app.active_callback = None
        app.time_lapse_media_image = object()
        app.time_lapse_media_marker_latlon = (50.0, 7.0)
        app.time_lapse_overview_preview_active = True
        app.time_lapse_overview_inset_active = True
        app.current_state = object()
        app.photo_presenter = None
        app.map_presenter = None
        calls = []
        app._begin_special_time_lapse_stage = lambda: calls.append("static")

        app._continue_after_time_lapse_overview()

        self.assertEqual(calls, ["static"])
        self.assertIsNone(app.time_lapse_media_image)
        self.assertFalse(app.time_lapse_overview_preview_active)

    def test_media_only_stage_uses_first_known_photo_coordinate_on_overview(self):
        metadata = {
            "stage_kind": "media_stage",
            "media_points": [
                {"lat": 50.1, "lon": 7.2, "source_name": "first.jpeg"},
                {"lat": 50.2, "lon": 7.3, "source_name": "second.jpeg"},
            ],
        }
        self.assertEqual(first_media_coordinate(metadata), (50.1, 7.2))
        self.assertIsNone(
            first_media_coordinate(
                {"stage_kind": "gpx_track", "track_points": [[50.1, 7.2]]}
            )
        )

    def test_back_restarts_current_stage_overview_before_previous_stage(self):
        self.assertTrue(
            should_restart_time_lapse_stage_at_overview(
                0.4,
                False,
                0,
                False,
                None,
            )
        )
        self.assertTrue(
            should_restart_time_lapse_stage_at_overview(
                0.0,
                True,
                1,
                False,
                "",
            )
        )
        self.assertFalse(
            should_restart_time_lapse_stage_at_overview(
                0.0,
                False,
                0,
                True,
                None,
            )
        )

    def test_backward_from_first_media_restores_the_stage_overview(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(debug=False)
        app.time_lapse_active = True
        app.time_lapse_stage = SimpleNamespace(
            map_index=2,
            relation=None,
        )
        app.time_lapse_handle = None
        app.time_lapse_media_cursor = 1
        app.time_lapse_current_media = (3, object())
        app.time_lapse_media_queue = [(0.2, 3, object())]
        app.time_lapse_progress = 0.2
        app.time_lapse_overview_preview_active = False
        calls = []
        app._end_time_lapse_media = lambda redraw=False: calls.append(("end", redraw))
        app._show_time_lapse_overview_or_begin = lambda progress, media, relation: calls.append(
            ("overview", progress, media, relation)
        )

        app._step_backward()

        self.assertEqual(
            calls,
            [
                ("end", False),
                ("overview", 0.0, None, None),
            ],
        )
        self.assertEqual(app.time_lapse_progress, 0.0)
        self.assertEqual(app.time_lapse_media_cursor, 0)

    def test_backward_from_first_standard_stage_returns_to_title_intro(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(debug=False)
        app.time_lapse_active = False
        app.time_lapse_stage = None
        app.time_lapse_handle = None
        app.manual_mode = True
        app.active_callback = None
        app.current_phase = PlaybackPhase.STAGE_MAP
        app.current_stage_index = 0
        app.stages = [SimpleNamespace(map_index=2, media_indexes=())]
        app.intro_was_shown = True
        calls = []
        app._show_intro_phase = lambda phase: calls.append(phase)

        app._step_backward()

        self.assertEqual(calls, [PlaybackPhase.INTRO_INFO])

    def test_backward_from_first_dual_window_time_lapse_returns_to_title_intro(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(debug=False)
        app.time_lapse_active = True
        app.time_lapse_stage = SimpleNamespace(map_index=2, relation=None)
        app.time_lapse_handle = None
        app.time_lapse_stage_map_preview_active = False
        app.time_lapse_overview_preview_active = False
        app.time_lapse_current_media = None
        app.time_lapse_media_cursor = 0
        app.time_lapse_media_queue = []
        app.time_lapse_progress = 0.0
        app.current_stage_index = 0
        app.intro_was_shown = True
        calls = []
        app._cancel_time_lapse_stage = lambda: calls.append("cancel")
        app._show_intro_phase = lambda phase: calls.append(phase)

        app._step_backward()

        self.assertEqual(calls, ["cancel", PlaybackPhase.INTRO_INFO])

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
        self.assertEqual(
            set(candidates),
            {
                "top_right",
                "top_left",
                "bottom_right",
                "bottom_left",
                "center_left",
                "center",
                "center_right",
                "top_center",
                "bottom_center",
            },
        )

    def test_margin_uses_map_dimensions_not_window_dimensions(self):
        self.assertEqual(inset_rect((100.0, 200.0, 400.0, 200.0), 0.05), (120.0, 210.0, 360.0, 180.0))

    def test_clear_frontier_does_not_depend_on_point_density(self):
        placement = (0.0, 0.0, 100.0, 100.0)
        sparse = [(0.0, 50.0), (100.0, 50.0)]
        dense = [(0.0, 50.0), (20.0, 50.0), (40.0, 50.0), (60.0, 50.0), (80.0, 50.0), (100.0, 50.0)]
        self.assertEqual(clear_corner_rect_options(placement, sparse), clear_corner_rect_options(placement, dense))

    def test_stage_stores_largest_clear_box_for_every_position(self):
        placement = (0.0, 0.0, 1000.0, 600.0)
        route = [(300.0, 0.0), (300.0, 600.0)]
        clear_rects = largest_clear_corner_rects(placement, route)
        self.assertEqual(
            set(clear_rects),
            {
                "top_right",
                "top_left",
                "bottom_right",
                "bottom_left",
                "center_left",
                "center",
                "center_right",
                "top_center",
                "bottom_center",
            },
        )
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

    def test_center_position_is_used_when_it_allows_the_largest_image(self):
        clear_rects = {
            "top_right": (800.0, 500.0, 200.0, 100.0),
            "top_left": (0.0, 500.0, 200.0, 100.0),
            "bottom_right": (800.0, 0.0, 200.0, 100.0),
            "bottom_left": (0.0, 0.0, 200.0, 100.0),
            "center": (250.0, 100.0, 500.0, 400.0),
        }
        position, outer, content = best_media_corner_layout(
            clear_rects, (1000.0, 600.0), 0.5, (1200.0, 800.0)
        )
        self.assertEqual(position, "center")
        self.assertAlmostEqual(outer[0] + outer[2] / 2.0, 500.0)
        self.assertAlmostEqual(outer[1] + outer[3] / 2.0, 300.0)
        self.assertGreater(content[2] * content[3], 100000.0)

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
            self.assertTrue(config.time_lapse_stages)
            self.assertEqual(config.initial_style, "TIME_LAPSE")
            self.assertEqual(config.transition.value, "BLEND")
            self.assertEqual(config.audio_crossfade_seconds, 2.0)
            self.assertEqual(
                config_from_options(project_dir, inputlist=control_file, transition="blend").transition.value,
                "BLEND",
            )
            self.assertEqual(normalize_transition(" blend "), "BLEND")
            fade_config = config_from_options(
                project_dir,
                inputlist=control_file,
                transition="fade",
            )
            self.assertFalse(fade_config.time_lapse_stages)
            self.assertEqual(fade_config.initial_style, "FADE")
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
            with self.assertRaises(ValueError):
                config_from_options(
                    project_dir,
                    inputlist=control_file,
                    audio_crossfade_seconds=31.0,
                )

    def test_stage_descriptors_and_intro_metadata(self):
        lines = [
            "#Overviewmap: overview.png",
            "#Datum: Mon, 01.01.2024",
            "#Map: stage1.png",
            "one.jpg | 09:00 | 50, 7 | Start",
            "#Datum: Tue, 02.01.2024",
            "#MediaMap: stage2.png",
            "two.jpg | 10:00 | 51, 8 | Finish",
        ]
        stages = parse_stage_descriptors(lines)
        self.assertEqual(len(stages), 2)
        self.assertEqual(stages[0].media_indexes, (3,))
        self.assertEqual(stages[1].media_indexes, (6,))
        self.assertEqual(stage_index_for_playlist_row(stages, 6), 1)
        summary = intro_metadata_from_playlist(lines)
        self.assertEqual(summary["date_range"], "01.01.2024 - 02.01.2024")
        self.assertEqual(summary["first_place"], "Start")
        self.assertEqual(summary["last_place"], "Finish")

    def test_intro_summary_uses_first_and_last_stage_endpoints(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename, track_name in (
                ("stage1.png", "JW Cologne - Bonn"),
                ("stage2.png", "JW Sarria - Santiago"),
            ):
                (root / filename).write_bytes(b"map")
                (root / filename).with_suffix(".json").write_text(
                    f'{{"track_name": "{track_name}", "track_length_km": 10.0}}',
                    encoding="utf-8",
                )
            app = GPSTrackShowApp.__new__(GPSTrackShowApp)
            app.config = SimpleNamespace(trackdir=root, photodir=root)
            app.playlist_lines = [
                "#Datum: Mon, 01.01.2024",
                "#Map: stage1.png",
                "one.jpg | 09:00 | 50, 7 | Cologne",
                "#Datum: Tue, 02.01.2024",
                "#Map: stage2.png",
                "two.jpg | 10:00 | 51, 8 | Bonn",
            ]
            app.stages = parse_stage_descriptors(app.playlist_lines)
            self.assertIn("Cologne - Santiago", app._intro_summary_lines())

    def test_intro_summary_uses_one_compact_summary_without_stage_sidecars(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            overview = root / "Trip.png"
            overview.write_bytes(b"map")
            (root / "Trip-summary.json").write_text(
                """
                {
                  "tracks": [
                    {
                      "track_name": "JW Cologne - Bonn",
                      "track_plot_image_filename": "stage1.png",
                      "laenge_km": 12.5
                    },
                    {
                      "track_name": "JW Sarria - Santiago",
                      "track_plot_image_filename": "stage2.png",
                      "laenge_km": 18.0
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            app = GPSTrackShowApp.__new__(GPSTrackShowApp)
            app.config = SimpleNamespace(trackdir=root, photodir=root)
            app.current_overview_path = overview
            app.current_overview_metadata = None
            app.compact_track_summary_loaded = False
            app.compact_track_summary = None
            app.playlist_lines = [
                "#Datum: Mon, 01.01.2024",
                "#Map: stage1.png",
                "one.jpg | 09:00 | 50, 7 | Cologne",
                "#Datum: Tue, 02.01.2024",
                "#Map: stage2.png",
                "two.jpg | 10:00 | 51, 8 | Bonn",
            ]
            app.stages = parse_stage_descriptors(app.playlist_lines)
            with patch(
                "GPSTrackShow.try_read_plot_metadata",
                side_effect=AssertionError("stage sidecar should not be opened"),
            ):
                summary_lines = app._intro_summary_lines()
            self.assertIn("Cologne - Santiago", summary_lines)
            self.assertIn("Total traveled: 30.5 km", summary_lines)

    def test_time_lapse_distance_uses_compact_summary_and_caches_prefix(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(trackdir=Path("/unused"), photodir=Path("/unused"))
        app.playlist_lines = [
            "#Map: stage1.png",
            "#Map: stage2.png",
            "#Map: stage3.png",
        ]
        app.stages = parse_stage_descriptors(app.playlist_lines)
        app.stage_start_distance_cache = {}
        app.stage_length_cache = {}
        app._summary_tracks_by_map_filename = lambda: {
            "stage1.png": {"laenge_km": 10.0},
            "stage2.png": {"laenge_km": 20.0},
            "stage3.png": {"laenge_km": 30.0},
        }
        self.assertEqual(app._time_lapse_distance_before_stage(2), 30.0)
        app._summary_tracks_by_map_filename = lambda: (_ for _ in ()).throw(
            AssertionError("cached prefix should be reused")
        )
        self.assertEqual(app._time_lapse_distance_before_stage(2), 30.0)

    def test_stage_name_endpoints_remove_generated_jw_prefix(self):
        self.assertEqual(
            stage_name_endpoints("JW Cologne - Santiago"),
            ("Cologne", "Santiago"),
        )

    def test_time_lapse_clock_fits_inside_map_header(self):
        frame, clock_size = time_lapse_clock_layout(
            (0.0, 0.0, 1600.0, 900.0),
            {
                "axes_box_fraction": {
                    "left": 0.05,
                    "bottom": 0.05,
                    "width": 0.90,
                    "height": 0.80,
                }
            },
            True,
        )
        header_bottom = 900.0 * 0.85
        self.assertGreaterEqual(frame[1], header_bottom)
        self.assertLessEqual(frame[1] + frame[3], 900.0)
        self.assertGreater(clock_size, 100.0)
        self.assertGreater(frame[2], clock_size)

    def test_time_lapse_clock_fits_small_notebook_header(self):
        frame, clock_size = time_lapse_clock_layout(
            (0.0, 40.0, 1280.0, 720.0),
            {
                "axes_box_fraction": {
                    "left": 0.001,
                    "bottom": 0.002,
                    "width": 0.998,
                    "height": 0.90,
                }
            },
            True,
        )
        header_bottom = 40.0 + 720.0 * 0.902
        self.assertGreaterEqual(frame[1], header_bottom)
        self.assertLessEqual(frame[1] + frame[3], 760.0)
        self.assertLessEqual(frame[0] + frame[2], 1280.0)
        self.assertGreater(clock_size, 50.0)

    def test_clock_date_uses_the_stage_header_title_font_size(self):
        metadata = {
            "axes_box_fraction": {
                "left": 0.001,
                "bottom": 0.002,
                "width": 0.998,
                "height": 0.90,
            }
        }
        font_size = time_lapse_header_title_font_size(
            (0.0, 40.0, 1280.0, 720.0),
            metadata,
            2.2,
            3,
        )
        self.assertGreaterEqual(font_size, 8.0)
        self.assertLessEqual(font_size, 14.0 * 2.2)

    def test_track_clock_support_accepts_timed_point_iso_field(self):
        self.assertTrue(
            track_metadata_supports_clock(
                {"timed_track_points": [{"time_iso": "2024-07-15T10:00:00+02:00"}]}
            )
        )

    def test_track_clock_support_accepts_media_point_iso_field(self):
        self.assertTrue(
            track_metadata_supports_clock(
                {
                    "media_points": [
                        {"time_iso": "2024-07-15T10:00:00+02:00"}
                    ]
                }
            )
        )

    def test_display_path_simplification_preserves_endpoints(self):
        points = [
            (0.0, 0.0),
            (0.1, 0.1),
            (0.2, 0.2),
            (2.0, 0.0),
            (2.1, 0.1),
            (4.0, 1.0),
        ]
        simplified = simplify_display_path(points, 0.75)
        self.assertEqual(simplified[0], points[0])
        self.assertEqual(simplified[-1], points[-1])
        self.assertLess(len(simplified), len(points))

    def test_space_begins_waiting_intro_and_starts_music(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.awaiting_intro_start = True
        app.playlist_index = 7
        calls = []
        app._hide_startup_hint = lambda: calls.append("hide")
        app.music_controller = SimpleNamespace(
            start=lambda index: calls.append(("music", index))
        )
        app._show_intro_phase = lambda phase: calls.append(phase)
        app._begin_intro_playback()
        self.assertFalse(app.awaiting_intro_start)
        self.assertEqual(
            calls,
            ["hide", ("music", 7), PlaybackPhase.INTRO_INFO],
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
