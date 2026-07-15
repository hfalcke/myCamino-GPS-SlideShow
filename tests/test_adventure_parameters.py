"""Tests for the project-scoped parameter registry."""

from __future__ import annotations

import unittest

from adventure_parameters import (
    PARAMETER_SCHEMA_VERSION,
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
        self.assertEqual(defaults["slideshow.start_mode"], "time_lapse")
        self.assertEqual(defaults["slideshow.window_mode"], "auto")
        self.assertFalse(defaults["slideshow.track_map_before_media"])
        self.assertTrue(defaults["timelapse.overview_as_media"])
        self.assertEqual(defaults["gpx.horizontal_smoothing_distance_m"], 10.0)
        self.assertEqual(defaults["gpx.minimum_point_spacing_m"], 10.0)
        self.assertEqual(defaults["gpx.elevation_smoothing_distance_m"], 50.0)
        self.assertEqual(defaults["gpx.maximum_accuracy_m"], 10.0)
        self.assertEqual(defaults["gpx.maximum_vertical_accuracy_m"], 20.0)
        self.assertEqual(defaults["gpx.maximum_hdop"], 20.0)
        self.assertEqual(defaults["gpx.maximum_vdop"], 20.0)

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
        values["maps.provider"] = "custom"
        values["maps.custom_url"] = "https://tiles.example/{z}/{x}.png"
        errors = validate_parameters(values)
        self.assertIn("maps.custom_url", errors)
        self.assertIn("maps.custom_attribution", errors)
        values["maps.custom_url"] = "https://tiles.example/{z}/{x}/{y}.png"
        values["maps.custom_attribution"] = "Example Maps"
        self.assertEqual(validate_parameters(values), {})

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

    def test_custom_provider_fields_follow_provider_selection(self):
        values = default_parameters()
        normal_keys = {spec.key for spec in visible_specs_for_section("Map Service", values)}
        self.assertNotIn("maps.custom_url", normal_keys)
        values["maps.provider"] = "custom"
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


if __name__ == "__main__":
    unittest.main()
