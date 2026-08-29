"""Tests for non-GUI project parameter adapters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from GetGeoLocations import params_from_options
from map_provider_utils import (
    MYCAMINO_USER_AGENT,
    OSM_MINIMUM_CACHE_HOURS,
    TileProviderAccessError,
    contextily_provider,
    contextily_request_timeout,
    effective_cache_retention_hours,
    provider_tile_url,
)


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
        self.assertEqual(calls[0]["headers"]["user-agent"], MYCAMINO_USER_AGENT)
        self.assertIs(requests.get, original_get)

    def test_public_osm_cache_is_retained_for_at_least_seven_days(self):
        self.assertEqual(
            effective_cache_retention_hours("osm", 1),
            OSM_MINIMUM_CACHE_HOURS,
        )
        self.assertEqual(effective_cache_retention_hours("custom", 1), 1)

    def test_http_403_is_a_terminal_provider_error(self):
        response = SimpleNamespace(status_code=403, headers={})
        requests = SimpleNamespace(get=lambda *_args, **_kwargs: response)
        contextily = SimpleNamespace(tile=SimpleNamespace(requests=requests))
        with self.assertRaises(TileProviderAccessError):
            with contextily_request_timeout(contextily, 1, "osm", 0):
                requests.get("https://tile.openstreetmap.org/1/1/1.png")

    def test_http_429_waits_once_for_retry_after(self):
        responses = iter(
            [
                SimpleNamespace(status_code=429, headers={"Retry-After": "2"}),
                SimpleNamespace(status_code=200, headers={"Content-Type": "image/png"}),
            ]
        )
        requests = SimpleNamespace(get=lambda *_args, **_kwargs: next(responses))
        contextily = SimpleNamespace(tile=SimpleNamespace(requests=requests))
        with patch("map_provider_utils.time.sleep") as sleep:
            with contextily_request_timeout(contextily, 1, "osm", 0):
                response = requests.get("https://tile.openstreetmap.org/1/1/1.png")
        self.assertEqual(response.status_code, 200)
        sleep.assert_called_once_with(2.0)

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
