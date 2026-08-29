"""Tests for the project-scoped parameter registry."""

from __future__ import annotations

import unittest

from adventure_parameters import (
    PARAMETER_SCHEMA_VERSION,
    SECTION_ORDER,
    default_parameters,
    map_affecting_parameter_keys,
    normalize_parameter_value,
    normalize_parameters,
    parameter_payload,
    SPECS_BY_KEY,
    validate_parameters,
    visible_specs_for_section,
)


class AdventureParameterTests(unittest.TestCase):
    def test_defaults_are_complete_and_versioned(self):
        defaults = default_parameters()
        self.assertEqual(set(defaults), set(SPECS_BY_KEY))
        payload = parameter_payload(defaults)
        self.assertEqual(payload["version"], PARAMETER_SCHEMA_VERSION)
        self.assertEqual(payload["values"], defaults)
        self.assertEqual(defaults["slideshow.transition"], "time_lapse")
        self.assertEqual(defaults["slideshow.end_behavior"], "loop_forever")
        self.assertNotIn("slideshow.repeat", defaults)
        self.assertNotIn("slideshow.start_mode", defaults)
        self.assertEqual(defaults["slideshow.window_mode"], "auto")
        self.assertFalse(defaults["slideshow.track_map_before_media"])
        self.assertFalse(defaults["audio.enabled"])
        self.assertEqual(defaults["audio.music_volume_percent"], 65.0)
        self.assertEqual(defaults["audio.video_volume_percent"], 100.0)
        self.assertEqual(defaults["audio.narration_volume_percent"], 100.0)
        self.assertEqual(defaults["audio.narration_music_behavior"], "reduce")
        self.assertEqual(defaults["audio.narration_music_reduction_percent"], 25.0)
        self.assertEqual(defaults["audio.narration_video_reduction_percent"], 25.0)
        self.assertTrue(defaults["audio.use_normalized_videos"])
        self.assertTrue(defaults["slideshow.elevation_profile"])
        self.assertTrue(defaults["slideshow.clock"])
        self.assertTrue(defaults["slideshow.header_stage_name"])
        self.assertTrue(defaults["slideshow.header_track_details"])
        self.assertTrue(defaults["slideshow.header_place_name"])
        self.assertTrue(defaults["slideshow.header_track_stats"])
        self.assertEqual(defaults["slideshow.header_background"], "black")
        self.assertEqual(defaults["slideshow.header_shadow_color"], "#000000")
        self.assertNotIn("timelapse.header_background", defaults)
        self.assertNotIn("reserved", dict(SPECS_BY_KEY["slideshow.header_background"].choices))
        self.assertEqual(defaults["audio.video_normalization_target_lufs"], -16.0)
        self.assertTrue(defaults["locations.add_place_names"])
        self.assertTrue(defaults["timelapse.overview_as_media"])
        self.assertTrue(defaults["timelapse.overview_on_stage_map_dual"])
        self.assertEqual(defaults["gpx.horizontal_smoothing_distance_m"], 10.0)
        self.assertEqual(defaults["gpx.minimum_point_spacing_m"], 10.0)
        self.assertEqual(defaults["gpx.elevation_smoothing_distance_m"], 50.0)
        self.assertEqual(defaults["gpx.maximum_accuracy_m"], 10.0)
        self.assertEqual(defaults["gpx.maximum_vertical_accuracy_m"], 20.0)
        self.assertEqual(defaults["gpx.maximum_hdop"], 20.0)
        self.assertEqual(defaults["gpx.maximum_vdop"], 20.0)
        self.assertEqual(defaults["gpx.running_speed_window_distance_m"], 500.0)
        self.assertEqual(defaults["trackmaps.media_point_color"], "#0066FF")
        self.assertEqual(defaults["trackmaps.track_title"], "endpoint_places")
        self.assertEqual(defaults["trackmaps.zoom"], 16)
        self.assertNotIn("trackmaps.media_point_size", defaults)
        self.assertNotIn("trackmaps.variant", defaults)

    def test_legacy_header_choices_migrate_to_three_line_settings(self):
        values = normalize_parameters({
            "version": 13,
            "values": {
                "slideshow.header_title": False,
                "slideshow.place_names": False,
            },
        })
        self.assertFalse(values["slideshow.header_stage_name"])
        self.assertFalse(values["slideshow.header_track_details"])
        self.assertFalse(values["slideshow.header_place_name"])

    def test_removed_reserved_layout_becomes_black(self):
        values = normalize_parameters({
            "version": 14,
            "values": {
                "slideshow.header_background": "reserved",
            },
        })
        self.assertEqual(values["slideshow.header_background"], "black")

    def test_previous_time_lapse_layout_becomes_the_shared_layout_when_needed(self):
        values = normalize_parameters({
            "version": 15,
            "values": {"timelapse.header_background": "transparent"},
        })
        self.assertEqual(values["slideshow.header_background"], "transparent")
        self.assertNotIn("timelapse.header_background", values)

    def test_previous_default_map_zoom_migrates_to_sixteen(self):
        migrated = normalize_parameters(
            {"version": 7, "values": {"trackmaps.zoom": 15}}
        )
        current_custom = normalize_parameters(
            {"version": PARAMETER_SCHEMA_VERSION, "values": {"trackmaps.zoom": 15}}
        )
        self.assertEqual(migrated["trackmaps.zoom"], 16)
        self.assertEqual(current_custom["trackmaps.zoom"], 15)

    def test_previous_running_speed_default_migrates_to_five_hundred(self):
        migrated = normalize_parameters(
            {"version": 16, "values": {"gpx.running_speed_window_distance_m": 100.0}}
        )
        current_custom = normalize_parameters(
            {
                "version": PARAMETER_SCHEMA_VERSION,
                "values": {"gpx.running_speed_window_distance_m": 100.0},
            }
        )
        self.assertEqual(migrated["gpx.running_speed_window_distance_m"], 500.0)
        self.assertEqual(current_custom["gpx.running_speed_window_distance_m"], 100.0)

    def test_schema_one_default_orange_migrates_but_custom_color_is_preserved(self):
        migrated = normalize_parameters(
            {"version": 1, "values": {"trackmaps.media_point_color": "#FF8C00"}}
        )
        custom = normalize_parameters(
            {"version": 1, "values": {"trackmaps.media_point_color": "#00AA00"}}
        )
        self.assertEqual(migrated["trackmaps.media_point_color"], "#0066FF")
        self.assertEqual(custom["trackmaps.media_point_color"], "#00AA00")

    def test_legacy_start_mode_migrates_into_initial_style(self):
        time_lapse = normalize_parameters(
            {
                "version": 5,
                "values": {
                    "slideshow.start_mode": "time_lapse",
                    "slideshow.transition": "fade",
                },
            }
        )
        standard = normalize_parameters(
            {
                "version": 5,
                "values": {
                    "slideshow.start_mode": "standard",
                    "slideshow.transition": "fade",
                },
            }
        )
        self.assertEqual(time_lapse["slideshow.transition"], "time_lapse")
        self.assertEqual(standard["slideshow.transition"], "fade")

    def test_legacy_repeat_setting_migrates_to_new_loop_forever_default(self):
        for repeat in (False, True):
            migrated = normalize_parameters(
                {
                    "version": 11,
                    "values": {"slideshow.repeat": repeat},
                }
            )
            self.assertEqual(migrated["slideshow.end_behavior"], "loop_forever")
            self.assertNotIn("slideshow.repeat", migrated)

    def test_invalid_loaded_values_fall_back_individually(self):
        normalized = normalize_parameters(
            {"values": {"slideshow.font_size": "bad", "timelapse.stage_duration_seconds": 45}}
        )
        self.assertEqual(normalized["slideshow.font_size"], 30)
        self.assertEqual(normalized["timelapse.stage_duration_seconds"], 45.0)

    def test_fraction_accepts_percentage_input(self):
        spec = SPECS_BY_KEY["timelapse.media_min_fraction"]
        self.assertEqual(normalize_parameter_value(spec, "50"), 0.5)
        self.assertEqual(normalize_parameter_value(spec, "0.5"), 0.5)

    def test_integer_accepts_json_float_but_rejects_fraction(self):
        spec = SPECS_BY_KEY["slideshow.font_size"]
        self.assertEqual(normalize_parameter_value(spec, 30.0), 30)
        with self.assertRaises(ValueError):
            normalize_parameter_value(spec, 30.5)

    def test_custom_provider_requires_template_and_attribution(self):
        values = default_parameters()
        values["maps.output_provider"] = "custom"
        values["maps.custom_url"] = "https://tiles.example/{z}/{x}.png"
        errors = validate_parameters(values)
        self.assertIn("maps.custom_url", errors)
        self.assertIn("maps.custom_attribution", errors)
        values["maps.custom_url"] = "https://tiles.example/{z}/{x}/{y}.png"
        values["maps.custom_attribution"] = "Example Maps"
        self.assertEqual(validate_parameters(values), {})

    def test_legacy_shared_map_provider_migrates_to_both_roles(self):
        values = normalize_parameters(
            {"version": 19, "values": {"maps.provider": "esri"}}
        )
        self.assertEqual(values["maps.interactive_provider"], "esri")
        self.assertEqual(values["maps.output_provider"], "esri")
        self.assertNotIn("maps.provider", values)

    def test_cross_field_geocoder_pacing_is_validated(self):
        values = default_parameters()
        values["locations.pacing_min_seconds"] = 6
        values["locations.pacing_max_seconds"] = 2
        errors = validate_parameters(values)
        self.assertIn("locations.pacing_min_seconds", errors)
        self.assertIn("locations.pacing_max_seconds", errors)

    def test_common_gpx_and_pdf_sections_are_not_empty(self):
        values = default_parameters()
        self.assertGreater(len(visible_specs_for_section("GPX Processing", values)), 0)
        self.assertGreater(len(visible_specs_for_section("PDF Export", values)), 0)

    def test_time_lapse_settings_are_a_slideshow_subsection(self):
        values = default_parameters()
        slideshow_specs = visible_specs_for_section("Slide Show", values)
        time_lapse_specs = [spec for spec in slideshow_specs if spec.subsection == "Time-Lapse"]
        self.assertNotIn("Time-Lapse", SECTION_ORDER)
        self.assertTrue(time_lapse_specs)
        self.assertEqual(
            {spec.key for spec in time_lapse_specs},
            {
                "timelapse.stage_duration_seconds",
                "timelapse.media_min_fraction",
                "timelapse.overview_as_media",
                "timelapse.overview_on_stage_map_dual",
                "timelapse.marker_style",
                "slideshow.speedometer",
            },
        )

    def test_header_settings_are_grouped_together(self):
        values = default_parameters()
        header_keys = {
            spec.key
            for spec in visible_specs_for_section("Slide Show", values)
            if spec.subsection == "Header"
        }
        self.assertEqual(
            header_keys,
            {
                "slideshow.clock",
                "slideshow.font_color",
                "slideshow.font_size",
                "slideshow.font_family",
                "slideshow.font_style",
                "slideshow.header_stage_name",
                "slideshow.header_track_details",
                "slideshow.header_place_name",
                "slideshow.header_track_stats",
                "slideshow.header_background",
                "slideshow.header_shadow_color",
            },
        )

    def test_custom_provider_fields_follow_provider_selection(self):
        values = default_parameters()
        normal_keys = {spec.key for spec in visible_specs_for_section("Map Service", values)}
        self.assertNotIn("maps.custom_url", normal_keys)
        values["maps.output_provider"] = "custom"
        custom_keys = {spec.key for spec in visible_specs_for_section("Map Service", values)}
        self.assertIn("maps.custom_url", custom_keys)
        self.assertIn("maps.custom_attribution", custom_keys)

    def test_all_geometry_parameters_invalidate_track_maps(self):
        keys = map_affecting_parameter_keys()
        self.assertTrue(
            {
                "gpx.horizontal_smoothing_distance_m",
                "gpx.minimum_point_spacing_m",
                "gpx.elevation_smoothing_distance_m",
                "gpx.maximum_accuracy_m",
                "gpx.maximum_vertical_accuracy_m",
                "gpx.maximum_hdop",
                "gpx.maximum_vdop",
            }.issubset(keys)
        )
        self.assertNotIn("gpx.running_speed_window_distance_m", keys)
        self.assertNotIn("gpx.stationary_speed_threshold_kmh", keys)


if __name__ == "__main__":
    unittest.main()
