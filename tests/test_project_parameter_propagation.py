"""Tests for non-GUI project parameter adapters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from GetGeoLocations import params_from_options
from map_provider_utils import contextily_provider, contextily_request_timeout, provider_tile_url


class ProjectParameterPropagationTests(unittest.TestCase):
    def test_geolocation_options_are_retained(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            params = params_from_options(
                Path(temp_dir),
                distance=275,
                geocode_timeout_seconds=17,
                geocode_pacing_min_seconds=2,
                geocode_pacing_max_seconds=4,
                infer_gps_from_tracks=False,
            )
        self.assertEqual(params.distance, 275.0)
        self.assertEqual(params.geocode_timeout_seconds, 17.0)
        self.assertEqual(params.geocode_pacing_min_seconds, 2.0)
        self.assertEqual(params.geocode_pacing_max_seconds, 4.0)
        self.assertFalse(params.infer_gps_from_tracks)

    def test_geolocation_pacing_rejects_reversed_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                params_from_options(
                    Path(temp_dir),
                    geocode_pacing_min_seconds=5,
                    geocode_pacing_max_seconds=1,
                )

    def test_contextily_provider_presets(self):
        providers = SimpleNamespace(
            OpenStreetMap=SimpleNamespace(Mapnik=object()),
            Esri=SimpleNamespace(WorldStreetMap=object()),
        )
        contextily = SimpleNamespace(providers=providers)
        self.assertIs(contextily_provider(contextily, "osm"), providers.OpenStreetMap.Mapnik)
        self.assertIs(contextily_provider(contextily, "esri"), providers.Esri.WorldStreetMap)

    def test_contextily_timeout_is_temporary(self):
        calls = []

        def original_get(*args, **kwargs):
            calls.append(kwargs)

        requests = SimpleNamespace(get=original_get)
        contextily = SimpleNamespace(tile=SimpleNamespace(requests=requests))
        with contextily_request_timeout(contextily, 7.5):
            requests.get("https://example.invalid/tile")
        self.assertEqual(calls[0]["timeout"], 7.5)
        self.assertIs(requests.get, original_get)

    def test_provider_tile_url_uses_configured_provider_builder(self):
        calls = []

        class Provider:
            def build_url(self, **values):
                calls.append(values)
                return f"https://tiles.example/{values['z']}/{values['x']}/{values['y']}.png"

        self.assertEqual(
            provider_tile_url(Provider(), 4, 5, 6),
            "https://tiles.example/6/4/5.png",
        )
        self.assertEqual(calls, [{"x": 4, "y": 5, "z": 6}])

    def test_provider_tile_url_supports_plain_url_templates(self):
        self.assertEqual(
            provider_tile_url({"url": "https://tiles.example/{z}/{x}/{y}.png"}, 7, 8, 9),
            "https://tiles.example/9/7/8.png",
        )


if __name__ == "__main__":
    unittest.main()
