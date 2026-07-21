import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from GPSTrackShow import (
    DEFAULT_LIST_NAME,
    GPSTrackShowApp,
    HELP_OVERLAY_PERSISTENCE_SECONDS,
    Transition,
    WindowTarget,
    config_from_options,
    external_settings_command,
    header_content_rect,
    map_image_rect_and_scale,
    photo_track_metrics,
    runtime_header_band,
    runtime_header_text_shadow_color,
    selected_stage_header_lines,
    time_lapse_header_title_font_size,
)


class SlideshowHeaderTests(unittest.TestCase):
    def test_header_defaults_are_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / DEFAULT_LIST_NAME).write_text("# test\n", encoding="utf-8")
            config = config_from_options(root)
        self.assertTrue(config.clock)
        self.assertTrue(config.header_stage_name)
        self.assertTrue(config.header_track_details)
        self.assertTrue(config.header_place_name)
        self.assertTrue(config.header_track_stats)
        self.assertEqual(config.header_background, "black")
        self.assertEqual(config.header_shadow_color, (0.0, 0.0, 0.0, 1.0))

    def test_custom_header_shadow_color_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / DEFAULT_LIST_NAME).write_text("# test\n", encoding="utf-8")
            config = config_from_options(root, header_shadow_color="#336699")
        self.assertEqual(config.header_shadow_color, (0.2, 0.4, 0.6, 1.0))

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
                "Total traveled: 37 km",
                "Stage traveled: 12,3 km",
                "Height: 456 m",
            ),
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

    def test_only_black_header_shrinks_content(self):
        metadata = {
            "axes_box_fraction": {"bottom": 0.0, "height": 0.85},
        }
        self.assertEqual(
            header_content_rect((0.0, 0.0, 1000.0, 500.0), metadata, "black"),
            (0.0, 0.0, 1000.0, 425.0),
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
            (22, {"slideshow.header_track_stats": True}),
        )
        self.assertIsNone(external_settings_command(payload, 22))
        self.assertIsNone(
            external_settings_command({**payload, "command": "jump"}, 21)
        )

    def test_black_time_lapse_map_remains_full_width(self):
        rect, scale = map_image_rect_and_scale(
            (0.0, 0.0, 1920.0, 1080.0),
            (1920.0, 1080.0),
            {"runtime_header_fraction": 0.12},
            "black",
        )
        self.assertEqual(rect, (0.0, 0.0, 1920.0, 950.4))
        self.assertEqual(scale[0], 1.0)
        self.assertAlmostEqual(scale[1], 0.88)

        transparent_rect, transparent_scale = map_image_rect_and_scale(
            (0.0, 0.0, 1920.0, 1080.0),
            (1920.0, 1080.0),
            {"runtime_header_fraction": 0.12},
            "transparent",
        )
        self.assertEqual(transparent_rect, (0.0, 0.0, 1920.0, 1080.0))
        self.assertEqual(transparent_scale, (1.0, 1.0))

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
                "Total traveled: 30 km",
                "Stage traveled: 9,6 km",
                "Height: -- m",
            ),
        )

    def test_c_key_toggle_controls_complete_header_without_changing_choices(self):
        app = object.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(debug=False, clock=True)
        app.header_visible = True
        messages = []
        app._update_window_titles = messages.append
        app._refresh_photo_overlays = lambda: messages.append("refreshed")
        GPSTrackShowApp._toggle_clock(app)
        self.assertFalse(app.header_visible)
        self.assertTrue(app.config.clock)
        self.assertEqual(messages, ["Header off", "refreshed"])

    def test_quad_and_collage_receive_track_statistics(self):
        for transition in (Transition.QUAD, Transition.COLLAGE):
            with self.subTest(transition=transition):
                headers = []
                presenter = SimpleNamespace(
                    set_header_reference_image=lambda *_args: None,
                    set_header=lambda *args: headers.append(args),
                    set_clock_time=lambda *_args: None,
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
                )
                app.photo_presenter = presenter
                app.map_presenter = None
                app.screen_swap = False
                app.header_visible = True
                app.time_lapse_active = False
                app.time_lapse_stage = None
                app.time_lapse_overview_preview_active = False
                app.transition_key_down = False
                app._show_targets(
                    [
                        WindowTarget(
                            "photo",
                            object(),
                            transition,
                            header_lines=("Stage",),
                            header_metrics=("Total traveled: 42 km",),
                        )
                    ]
                )
                self.assertEqual(headers[0][1], ("Total traveled: 42 km",))


if __name__ == "__main__":
    unittest.main()
