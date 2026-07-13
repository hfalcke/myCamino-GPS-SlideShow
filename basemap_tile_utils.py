"""Small compatibility helpers for resilient Contextily basemap downloads."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock

import numpy as np


_TILE_FETCH_LOCK = RLock()


@dataclass
class MissingTileReport:
    """Tiles that were unavailable but did not prevent map rendering."""

    urls: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.urls)

    def status_text(self) -> str:
        if self.count == 1:
            return "1 unavailable map tile skipped."
        return f"{self.count} unavailable map tiles skipped."


def _is_http_404(exc: Exception, requests_module) -> bool:
    """Recognize Contextily's 404 exception across its supported versions."""
    http_error = getattr(requests_module, "HTTPError", ())
    if not isinstance(exc, http_error):
        return False
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 404:
        return True
    # Contextily 1.7 raises a replacement HTTPError without attaching response.
    return "404" in str(exc)


@contextmanager
def tolerate_missing_tiles(contextily_module):
    """Let a Contextily render continue when individual tiles return HTTP 404.

    Successful tiles keep Contextily's normal cache. The blank fallback is not
    cached, so a temporarily unavailable tile can be fetched during a later run.
    """
    tile_module = getattr(contextily_module, "tile", None)
    original_fetch = getattr(tile_module, "_fetch_tile", None)
    memory = getattr(tile_module, "memory", None)
    original_cache = getattr(memory, "cache", None)
    requests_module = getattr(tile_module, "requests", None)
    report = MissingTileReport()

    if not callable(original_fetch) or not callable(original_cache) or requests_module is None:
        yield report
        return

    with _TILE_FETCH_LOCK:
        cached_original_fetch = original_cache(original_fetch)

        def fetch_with_missing_tile_fallback(tile_url, wait, max_retries, headers):
            try:
                return cached_original_fetch(tile_url, wait, max_retries, headers)
            except Exception as exc:
                if not _is_http_404(exc, requests_module):
                    raise
                report.urls.append(str(tile_url))
                return np.zeros((256, 256, 4), dtype=np.uint8)

        def cache_with_uncached_fallback(function, *args, **kwargs):
            if function is fetch_with_missing_tile_fallback:
                return fetch_with_missing_tile_fallback
            return original_cache(function, *args, **kwargs)

        tile_module._fetch_tile = fetch_with_missing_tile_fallback
        memory.cache = cache_with_uncached_fallback
        try:
            yield report
        finally:
            memory.cache = original_cache
            tile_module._fetch_tile = original_fetch
