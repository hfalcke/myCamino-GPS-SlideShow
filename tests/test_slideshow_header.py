import tempfile
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

from GPSTrackShow import (
    CocoaImagePresenter,
    DEFAULT_LIST_NAME,
    GPSTrackShowApp,
    HELP_OVERLAY_PERSISTENCE_SECONDS,
    Transition,
    WindowTarget,
    config_from_options,
    external_restart_command,
    external_settings_command,
    fit_size_to_aspect,
    header_content_rect,
    map_image_rect_and_scale,
    photo_track_metrics,
    photo_track_speedometer,
    presentation_header_metadata,
    runtime_header_band,
    runtime_header_metrics_font_size,
    runtime_header_text_shadow_color,
    selected_stage_header_lines,
    time_lapse_header_title_font_size,
    weather_badge_layout,
)


class SlideshowHeaderTests(unittest.TestCase):
    def test_weather_badge_uses_one_compact_vertical_column(self):
        frame = weather_badge_layout(
            (0.0, 0.0, 1920.0, 1080.0),
            None,
            False,
            True,
        )
        self.assertAlmostEqual(frame[2], frame[3] * 1.12)

    def test_weather_badge_reserves_measured_statistics_width(self):
        without_statistics = weather_badge_layout(
            (0.0, 0.0, 1920.0, 1080.0), None, False, True, False
        )
        short_statistics = weather_badge_layout(
            (0.0, 0.0, 1920.0, 1080.0),
            None,
            False,
            True,
            True,
            ("Total: 999 km", "Stage: 99.9 km", "Height: 999 m"),
        )
        long_statistics = weather_badge_layout(
            (0.0, 0.0, 1920.0, 1080.0),
            None,
            False,
            True,
            True,
            ("Total: 1234567 km", "Stage: 12345.9 km", "Height: 8000 m"),
        )
        self.assertLess(short_statistics[0], without_statistics[0])
        self.assertLess(long_statistics[0], short_statistics[0])

    def test_right_header_statistics_use_a_readable_three_row_font(self):
        self.assertAlmostEqual(
            runtime_header_metrics_font_size(30.0, 1.0, 210.0),
            24.6,
        )
        self.assertLessEqual(
            runtime_header_metrics_font_size(80.0, 2.0, 90.0),
            90.0 / 3.15,
        )

    def test_header_defaults_are_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / DEFAULT_LIST_NAME).write_text("# test\n", encoding="utf-8")
            config = config_from_options(root)
        self.assertTrue(config.clock)
        self.assertEqual(config.header_mode, "full")
        self.assertTrue(config.map_header_enabled)
        self.assertTrue(config.header_stage_name)
        self.assertTrue(config.header_track_details)
        self.assertTrue(config.header_place_name)
        self.assertTrue(config.header_track_stats)
        self.assertEqual(config.header_background, "black")
        self.assertEqual(config.header_shadow_color, (0.0, 0.0, 0.0, 1.0))

    def test_header_modes_are_assigned_by_logical_role(self):
        app = object.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(map_header_enabled=True)
        app.header_mode = "simple"
        self.assertEqual(app._header_mode_for_role("photo"), "simple")
        self.assertEqual(app._header_mode_for_role("map"), "full")

        app.config.map_header_enabled = False
        self.assertEqual(app._header_mode_for_role("map"), "off")
        self.assertEqual(app._header_mode_for_role("photo"), "simple")

    def test_screen_swap_routes_presenters_without_swapping_header_policy(self):
        app = object.__new__(GPSTrackShowApp)
        photo = object()
        map_presenter = object()
        app.config = SimpleNamespace(
            mapwindow=True,
            join_windows=False,
            map_header_enabled=True,
        )
        app.header_mode = "off"
        app.photo_presenter = photo
        app.map_presenter = map_presenter
        app.screen_swap = True

        self.assertIs(app._presenter_for_role("photo"), map_presenter)
        self.assertIs(app._presenter_for_role("map"), photo)
        self.assertEqual(app._header_mode_for_role("photo"), "off")
        self.assertEqual(app._header_mode_for_role("map"), "full")

    def test_simple_renderer_keeps_clock_and_hides_weather_and_speed(self):
        presenter = CocoaImagePresenter.__new__(CocoaImagePresenter)
        presenter._shared_header_render_signature = None
        presenter._shared_clock_render_signature = None
        presenter._shared_weather_render_signature = None
        presenter.set_header = lambda *args: None
        clocks = []
        speeds = []
        weather = []
        presenter.set_clock_time = lambda *args: clocks.append(args)
        presenter.set_speedometer = lambda *args: speeds.append(args)
        presenter.set_weather = lambda *args, **kwargs: weather.append((args, kwargs))

        state = SimpleNamespace(
            generation=1,
            visible=True,
            lines=("Stage", "Place"),
            metrics=("Total: 42 km",),
            metadata={},
            clock_time=(12, 30),
            clock_date_text="02.09.2026",
            clock_visible=True,
            running_speed_kmh=4.8,
            speedometer_maximum_kmh=7.0,
            speedometer_visible=True,
            weather={"temperature_2m_c": 18.0},
            weather_visible=True,
            weather_primary="temperature",
            weather_secondary="humidity",
            weather_condition_icon=True,
            font_size=30.0,
            font_family="System",
            font_style="bold",
            font_color=(1.0, 1.0, 1.0, 1.0),
            font_factor=2.2,
            background_style="black",
            shadow_color=(0.0, 0.0, 0.0, 1.0),
        )

        presenter.apply_header_state(state, mode="simple")

        self.assertEqual(clocks[-1], ((12, 30), "02.09.2026"))
        self.assertFalse(speeds[-1][2])
        self.assertFalse(weather[-1][0][1])

    def test_standard_stage_target_builds_the_same_contextual_header(self):
        app = object.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(
            header_stage_name=True,
            header_track_details=True,
            header_place_name=True,
            header_track_stats=True,
            track_title_mode="track_name",
            clock=True,
            speedometer=True,
            weather=True,
            weather_primary="temperature",
            weather_secondary="none",
            weather_condition_icon=True,
            font_size=30,
            font_family="System",
            font_style="bold",
            font_color=(1.0, 1.0, 1.0, 1.0),
            map_header_font_factor=2.2,
            header_background="black",
            background_color=(0.0, 0.0, 0.0, 1.0),
            header_shadow_color=(0.0, 0.0, 0.0, 1.0),
        )
        app.header_state_generation = 0
        app.current_stage_index = None
        app.current_track_metadata = {
            "track_name": "Stage 7",
            "track_length_km": 18.4,
            "track_duration": "05:12",
            "timed_track_points": [
                {
                    "lat": 50.0,
                    "lon": 8.0,
                    "elapsed_seconds": 0.0,
                    "cumulative_distance_km": 0.0,
                    "elevation_m": 123.0,
                },
                {
                    "lat": 50.01,
                    "lon": 8.01,
                    "elapsed_seconds": 300.0,
                    "cumulative_distance_km": 1.3,
                    "elevation_m": 150.0,
                },
            ],
        }
        state = app._header_state_for_target(
            WindowTarget("photo", object(), Transition.SWITCH)
        )

        self.assertEqual(state.title, "Stage 7")
        self.assertEqual(
            state.metrics,
            ("Total: 0 km", "Stage: 0,0 km", "Height: 123 m"),
        )

    def test_live_settings_apply_track_title_source(self):
        app = object.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(track_title_mode="endpoint_places")
        app.photo_presenter = None
        app.map_presenter = None
        app._refresh_photo_overlays = lambda: None
        app._show_temporary_status_overlay = lambda *_args: None

        app._apply_runtime_settings({"trackmaps.track_title": "track_name"})

        self.assertEqual(app.config.track_title_mode, "track_name")

        app._apply_runtime_settings({"trackmaps.track_title": "unsupported"})
        self.assertEqual(app.config.track_title_mode, "track_name")

    def test_custom_header_shadow_color_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / DEFAULT_LIST_NAME).write_text("# test\n", encoding="utf-8")
            config = config_from_options(root, header_shadow_color="#336699")
        self.assertEqual(config.header_shadow_color, (0.2, 0.4, 0.6, 1.0))

    def test_config_accepts_independent_presentation_and_map_header_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / DEFAULT_LIST_NAME).write_text("# test\n", encoding="utf-8")
            config = config_from_options(
                root,
                header_mode="simple",
                map_header_enabled=False,
            )
        self.assertEqual(config.header_mode, "simple")
        self.assertFalse(config.map_header_enabled)

    def test_clock_and_title_share_the_header_shadow_rule(self):
        selected = (0.2, 0.4, 0.6, 1.0)
        self.assertEqual(
            runtime_header_text_shadow_color("off", selected),
            (0.2, 0.4, 0.6, 0.5),
        )
        for style in ("black", "transparent"):
            self.assertEqual(
                runtime_header_text_shadow_color(style, selected),
                (0.2, 0.4, 0.6, 0.0),
            )

    def test_photo_statistics_use_nearest_timed_track_point(self):
        metadata = {
            "timed_track_points": [
                {"lat": 50.0, "lon": 8.0, "cumulative_distance_km": 0.0, "elevation_m": 100.0},
                {"lat": 50.1, "lon": 8.1, "cumulative_distance_km": 12.3, "elevation_m": 456.0},
            ]
        }
        self.assertEqual(
            photo_track_metrics(metadata, 50.099, 8.099, 25.0),
            (
                "Total: 37 km",
                "Stage: 12,3 km",
                "Height: 456 m",
            ),
        )

    def test_standard_photo_uses_nearest_timed_running_speed(self):
        metadata = {
            "running_speed": {"maximum_running_speed_kmh": 7.8},
            "timed_track_points": [
                {
                    "lat": 50.0,
                    "lon": 8.0,
                    "cumulative_distance_km": 0.0,
                    "running_speed_kmh": 3.2,
                },
                {
                    "lat": 50.1,
                    "lon": 8.1,
                    "cumulative_distance_km": 12.3,
                    "running_speed_kmh": 5.4,
                },
            ],
        }
        self.assertEqual(
            photo_track_speedometer(metadata, 50.099, 8.099),
            (5.4, 10.0),
        )

    def test_runtime_header_uses_saved_fraction_when_map_fills_frame(self):
        band = runtime_header_band(
            (0.0, 0.0, 1000.0, 500.0),
            {
                "axes_box_fraction": {"bottom": 0.0, "height": 1.0},
                "runtime_header_fraction": 0.15,
            },
        )
        self.assertEqual(band, (0.0, 425.0, 1000.0, 75.0))

    def test_standard_media_uses_time_lapse_header_fraction_not_map_axes(self):
        map_metadata = {
            "axes_box_fraction": {"bottom": 0.0, "height": 0.70},
            "runtime_header_fraction": 0.20,
        }
        map_band = runtime_header_band(
            (0.0, 0.0, 1000.0, 500.0),
            map_metadata,
        )
        presentation_band = runtime_header_band(
            (0.0, 0.0, 1000.0, 500.0),
            presentation_header_metadata(map_metadata),
        )
        for actual, expected in zip(map_band, (0.0, 350.0, 1000.0, 150.0)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(
            presentation_band,
            (0.0, 400.0, 1000.0, 100.0),
        ):
            self.assertAlmostEqual(actual, expected)

    def test_generated_map_reserved_header_is_not_reserved_twice(self):
        metadata = {
            "axes_box_fraction": {"bottom": 0.0, "height": 0.85},
        }
        self.assertEqual(
            header_content_rect((0.0, 0.0, 1000.0, 500.0), metadata, "black"),
            (0.0, 0.0, 1000.0, 500.0),
        )
        self.assertEqual(
            header_content_rect((0.0, 0.0, 1000.0, 500.0), None, "black"),
            (0.0, 0.0, 1000.0, 440.0),
        )
        for style in ("off", "transparent"):
            self.assertEqual(
                header_content_rect((0.0, 0.0, 1000.0, 500.0), metadata, style),
                (0.0, 0.0, 1000.0, 500.0),
            )

    def test_external_settings_command_requires_a_new_sequence(self):
        payload = {
            "command": "settings",
            "sequence": 22,
            "values": {"slideshow.header_track_stats": True},
        }
        self.assertEqual(
            external_settings_command(payload, 21),
            (22, {"slideshow.header_track_stats": True}, False),
        )
        self.assertIsNone(external_settings_command(payload, 22))
        self.assertIsNone(
            external_settings_command({**payload, "command": "jump"}, 21)
        )

    def test_settings_restore_and_restart_commands_are_explicit(self):
        settings = {
            "command": "settings",
            "sequence": 30,
            "values": {},
            "restore_display": True,
        }
        self.assertEqual(external_settings_command(settings, 29), (30, {}, True))
        restart = {"command": "restart", "sequence": 31}
        self.assertEqual(external_restart_command(restart, 30), 31)
        self.assertIsNone(external_restart_command(restart, 31))
        self.assertIsNone(
            external_restart_command({**restart, "command": "settings"}, 30)
        )

    def test_black_time_lapse_map_preserves_image_aspect_ratio(self):
        rect, scale = map_image_rect_and_scale(
            (0.0, 0.0, 1920.0, 1080.0),
            (1920.0, 1080.0),
            {"runtime_header_fraction": 0.12},
            "black",
        )
        self.assertAlmostEqual(rect[0], 115.2)
        self.assertAlmostEqual(rect[1], 0.0)
        self.assertAlmostEqual(rect[2], 1689.6)
        self.assertAlmostEqual(rect[3], 950.4)
        self.assertEqual(scale, (0.88, 0.88))

        transparent_rect, transparent_scale = map_image_rect_and_scale(
            (0.0, 0.0, 1920.0, 1080.0),
            (1920.0, 1080.0),
            {"runtime_header_fraction": 0.12},
            "transparent",
        )
        self.assertEqual(transparent_rect, (0.0, 0.0, 1920.0, 1080.0))
        self.assertEqual(transparent_scale, (1.0, 1.0))

    def test_window_size_fits_map_aspect_without_distortion(self):
        self.assertEqual(
            fit_size_to_aspect(1600.0, 900.0, 16.0 / 9.0),
            (1600.0, 900.0),
        )
        width, height = fit_size_to_aspect(1000.0, 800.0, 16.0 / 9.0)
        self.assertAlmostEqual(width, 1000.0)
        self.assertAlmostEqual(height, 562.5)

    def test_startup_hint_uses_the_general_help_persistence_time(self):
        app = object.__new__(GPSTrackShowApp)
        shown = []
        app.photo_presenter = SimpleNamespace(
            set_startup_hint_visible=lambda *args, **kwargs: shown.append((args, kwargs))
        )
        app.startup_hint_hide_handle = None
        scheduled = []
        app.schedule_callback = lambda seconds, callback: scheduled.append((seconds, callback)) or object()
        app._show_startup_hint(bottom=True, wait_for_start=True)
        self.assertEqual(scheduled[0][0], HELP_OVERLAY_PERSISTENCE_SECONDS)
        self.assertEqual(HELP_OVERLAY_PERSISTENCE_SECONDS, 5.0)

    def test_s_key_request_is_published_for_the_parent_settings_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_file = root / DEFAULT_LIST_NAME
            state_file = root / "state.json"
            control_file.write_text("# test\n", encoding="utf-8")
            app = object.__new__(GPSTrackShowApp)
            app.config = SimpleNamespace(
                state_file=state_file,
                inputlist=control_file,
                debug=False,
            )
            app.settings_request_sequence = 0
            app.live_state_sequence = 2
            app._resume_state_payload = lambda: {"version": 4}
            app._show_temporary_status_overlay = lambda *_args: None
            app._publish_settings_request()
            payload = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["request"], "open_settings")
        self.assertEqual(payload["settings_section"], "Slide Show")
        self.assertEqual(payload["request_sequence"], 1)
        self.assertEqual(payload["sequence"], 3)

    def test_selected_title_fields_compact_from_the_top(self):
        metadata = {
            "map_kind": "track",
            "track_name": "Stage 7",
            "track_length_km": 18.4,
            "track_duration": "05:12",
        }
        config = SimpleNamespace(
            header_stage_name=False,
            header_track_details=True,
            header_place_name=True,
            track_title_mode="track_name",
        )
        self.assertEqual(
            selected_stage_header_lines(metadata, config, place_text="Ponferrada\nLeón"),
            ("18.4 km - 05:12 h", "Ponferrada · León"),
        )

    def test_title_font_scales_above_full_hd(self):
        hd = time_lapse_header_title_font_size((0.0, 0.0, 1920.0, 1080.0), None, 2.2, 3)
        uhd = time_lapse_header_title_font_size((0.0, 0.0, 3840.0, 2160.0), None, 2.2, 3)
        self.assertGreater(uhd, hd)

    def test_statistics_fall_back_to_track_length_without_timed_points(self):
        self.assertEqual(
            photo_track_metrics({"track_length_km": 9.6}, None, None, 20.0),
            (
                "Total: 30 km",
                "Stage: 9,6 km",
                "Height: -- m",
            ),
        )

    def test_c_key_cycles_presentation_header_without_changing_components(self):
        app = object.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(debug=False, clock=True, header_mode="full")
        app.header_mode = "full"
        app.header_visible = True
        messages = []
        app._update_window_titles = messages.append
        app._refresh_photo_overlays = lambda: messages.append("refreshed")
        GPSTrackShowApp._toggle_clock(app)
        self.assertFalse(app.header_visible)
        self.assertEqual(app.header_mode, "off")
        self.assertTrue(app.config.clock)
        GPSTrackShowApp._toggle_clock(app)
        self.assertEqual(app.header_mode, "simple")
        GPSTrackShowApp._toggle_clock(app)
        self.assertEqual(app.header_mode, "full")
        self.assertEqual(app.config.header_mode, "full")
        self.assertEqual(
            messages,
            [
                "Header Off", "refreshed",
                "Header Simple", "refreshed",
                "Header Full", "refreshed",
            ],
        )

    def test_quad_and_collage_receive_track_statistics(self):
        for transition in (Transition.QUAD, Transition.COLLAGE):
            with self.subTest(transition=transition):
                headers = []

                def apply_header_state(state, enabled=True, mode="full"):
                    headers.append((state.lines, state.metrics, enabled, mode))

                presenter = SimpleNamespace(
                    set_header_reference_image=lambda *_args: None,
                    apply_header_state=apply_header_state,
                    set_place_text=lambda *_args: None,
                    set_info_text=lambda *_args: None,
                    transition_to=lambda *_args, **_kwargs: None,
                    set_status_visible=lambda *_args: None,
                )
                app = object.__new__(GPSTrackShowApp)
                app.config = SimpleNamespace(
                    debug=False,
                    mapwindow=False,
                    join_windows=False,
                    font_size=30,
                    font_color=(1.0, 1.0, 1.0, 1.0),
                    map_header_font_factor=2.2,
                    header_background="off",
                    header_shadow_color=(0.0, 0.0, 0.0, 1.0),
                    header_track_stats=True,
                    clock=False,
                    speedometer=True,
                    header_mode="full",
                    map_header_enabled=True,
                )
                app.photo_presenter = presenter
                app.map_presenter = None
                app.screen_swap = False
                app.header_visible = True
                app.time_lapse_active = False
                app.time_lapse_stage = None
                app.time_lapse_overview_preview_active = False
                app.transition_key_down = False
                app._raise_global_overlay_layers = lambda: None
                app._show_targets(
                    [
                        WindowTarget(
                            "photo",
                            object(),
                            transition,
                            header_lines=("Stage",),
                            header_metrics=("Total: 42 km",),
                        )
                    ]
                )
                self.assertEqual(headers[0][1], ("Total: 42 km",))
                self.assertEqual(headers[0][3], "full")

    def test_shared_header_state_contains_complete_immutable_content(self):
        app = object.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(
            clock=True,
            speedometer=True,
            weather=True,
            weather_primary="temperature",
            weather_secondary="humidity",
            weather_condition_icon=True,
            header_track_stats=True,
            font_size=32,
            font_family="Avenir",
            font_style="bold",
            font_color=(1.0, 1.0, 1.0, 1.0),
            map_header_font_factor=2.2,
            header_background="black",
            background_color=(0.0, 0.0, 0.0, 1.0),
            header_shadow_color=(0.0, 0.0, 0.0, 1.0),
        )
        app.header_visible = True
        app.header_state_generation = 0
        state = app._build_header_state(
            ("Stage", "Place"),
            ("Distance: 12 km",),
            {"track_name": "Stage"},
            place="Place",
            clock_time=(13, 45),
            clock_date_text="01.09.2026",
            running_speed_kmh=4.8,
            speedometer_maximum_kmh=7.0,
            weather={"temperature_2m_c": 18.0},
        )

        self.assertEqual(state.lines, ("Stage", "Place"))
        self.assertEqual(state.weekday_text, "Tuesday")
        self.assertEqual(state.calendar_date_text, "01.09.2026")
        self.assertEqual(state.metrics, ("Distance: 12 km",))
        self.assertTrue(state.clock_visible)
        self.assertTrue(state.speedometer_visible)
        self.assertTrue(state.weather_visible)
        with self.assertRaises(FrozenInstanceError):
            state.title = "Changed"

    def test_global_presenter_overlays_are_raised_as_one_top_layer(self):
        class HostStub:
            def __init__(self):
                self.items = []

            def addSubview_(self, view):
                if view in self.items:
                    self.items.remove(view)
                self.items.append(view)

            def subviews(self):
                return tuple(self.items)

        class ViewStub:
            def __init__(self, host, name):
                self.host = host
                self.name = name

            def removeFromSuperview(self):
                if self in self.host.items:
                    self.host.items.remove(self)

        presenter = CocoaImagePresenter.__new__(CocoaImagePresenter)
        presenter.host_view = HostStub()
        content = ViewStub(presenter.host_view, "content")
        presenter.info_view = ViewStub(presenter.host_view, "info")
        presenter.status_view = ViewStub(presenter.host_view, "status")
        presenter.memory_view = ViewStub(presenter.host_view, "memory")
        presenter.startup_hint_view = ViewStub(presenter.host_view, "startup")
        presenter.help_view = ViewStub(presenter.host_view, "help")
        presenter.host_view.items = [presenter.help_view, content, presenter.info_view]

        presenter.raise_global_overlays()

        self.assertTrue(presenter.global_overlays_are_topmost())
        self.assertEqual(presenter.host_view.items[0], content)

    def test_hiding_standard_content_does_not_hide_global_info(self):
        class ViewStub:
            def __init__(self):
                self.hidden = []

            def setHidden_(self, hidden):
                self.hidden.append(hidden)

        presenter = CocoaImagePresenter.__new__(CocoaImagePresenter)
        for name in (
            "primary_view",
            "overlay_view",
            "header_view",
            "clock_view",
            "elevation_profile_view",
            "place_view",
            "fade_view",
            "speedometer_view",
            "weather_view",
            "info_view",
        ):
            setattr(presenter, name, ViewStub())
        presenter.speedometer_visible = False
        presenter.running_speed_kmh = None
        presenter.weather_visible = False
        presenter.weather_snapshot = None
        presenter.video_view = None

        presenter.set_content_visible(False)

        self.assertEqual(presenter.info_view.hidden, [])
        self.assertEqual(presenter.primary_view.hidden, [True])


if __name__ == "__main__":
    unittest.main()
