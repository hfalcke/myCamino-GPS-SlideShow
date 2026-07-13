"""Regression coverage for unavailable Contextily map tiles."""

from __future__ import annotations

import unittest

import numpy as np
import requests

from basemap_tile_utils import tolerate_missing_tiles


class FakeMemory:
    def cache(self, function, *args, **kwargs):
        return function


class CachingFakeMemory:
    def __init__(self):
        self.values = {}

    def cache(self, function, *args, **kwargs):
        def cached_function(*call_args):
            key = (id(function), repr(call_args))
            if key not in self.values:
                self.values[key] = function(*call_args)
            return self.values[key]

        return cached_function


class FakeTileModule:
    def __init__(self, fetch, memory=None):
        self._fetch_tile = fetch
        self.memory = memory or FakeMemory()
        self.requests = requests


class FakeContextily:
    def __init__(self, fetch, memory=None):
        self.tile = FakeTileModule(fetch, memory)


class BasemapTileTests(unittest.TestCase):
    def test_404_returns_blank_tile_and_restores_contextily(self):
        def fetch(*_args):
            raise requests.HTTPError("Tile URL resulted in a 404 error")

        contextily = FakeContextily(fetch)
        original_fetch = contextily.tile._fetch_tile
        original_cache = contextily.tile.memory.cache
        with tolerate_missing_tiles(contextily) as report:
            downloader = contextily.tile.memory.cache(contextily.tile._fetch_tile)
            tile = downloader("https://tiles.example/1/2/3.png", 0, 0, {})

        self.assertEqual(report.count, 1)
        self.assertEqual(tile.shape, (256, 256, 4))
        self.assertEqual(tile.dtype, np.uint8)
        self.assertFalse(tile.any())
        self.assertIs(contextily.tile._fetch_tile, original_fetch)
        self.assertEqual(contextily.tile.memory.cache.__self__, original_cache.__self__)
        self.assertEqual(contextily.tile.memory.cache.__func__, original_cache.__func__)

    def test_non_404_http_error_still_fails_and_restores_contextily(self):
        def fetch(*_args):
            raise requests.HTTPError("Tile server returned 500")

        contextily = FakeContextily(fetch)
        original_fetch = contextily.tile._fetch_tile
        original_cache = contextily.tile.memory.cache
        with self.assertRaises(requests.HTTPError):
            with tolerate_missing_tiles(contextily):
                downloader = contextily.tile.memory.cache(contextily.tile._fetch_tile)
                downloader("https://tiles.example/1/2/3.png", 0, 0, {})

        self.assertIs(contextily.tile._fetch_tile, original_fetch)
        self.assertEqual(contextily.tile.memory.cache.__self__, original_cache.__self__)
        self.assertEqual(contextily.tile.memory.cache.__func__, original_cache.__func__)

    def test_404_fallback_is_not_cached(self):
        calls = 0

        def fetch(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise requests.HTTPError("Tile URL resulted in a 404 error")
            return np.ones((256, 256, 4), dtype=np.uint8)

        contextily = FakeContextily(fetch, CachingFakeMemory())
        url = "https://tiles.example/1/2/3.png"
        with tolerate_missing_tiles(contextily) as first_report:
            first = contextily.tile.memory.cache(contextily.tile._fetch_tile)(url, 0, 0, {})
        with tolerate_missing_tiles(contextily) as second_report:
            second = contextily.tile.memory.cache(contextily.tile._fetch_tile)(url, 0, 0, {})

        self.assertEqual(first_report.count, 1)
        self.assertEqual(second_report.count, 0)
        self.assertFalse(first.any())
        self.assertTrue(second.any())
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
