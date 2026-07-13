"""Shared Contextily map-provider and request configuration helpers."""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock


_REQUEST_PATCH_LOCK = RLock()


def contextily_provider(
    contextily_module,
    provider: str = "osm",
    custom_url: str = "",
    custom_attribution: str = "",
    maximum_zoom: int = 19,
):
    """Return a Contextily-compatible provider from project settings."""
    provider = str(provider or "osm").strip().lower()
    if provider == "esri":
        return contextily_module.providers.Esri.WorldStreetMap
    if provider == "custom":
        try:
            from xyzservices import TileProvider
        except ImportError as exc:
            raise RuntimeError("Custom map providers require xyzservices.") from exc
        return TileProvider(
            name="myCamino Custom",
            url=str(custom_url),
            attribution=str(custom_attribution),
            max_zoom=int(maximum_zoom),
        )
    return contextily_module.providers.OpenStreetMap.Mapnik


def provider_display_name(provider: str) -> str:
    provider = str(provider or "osm").strip().lower()
    return {
        "osm": "OpenStreetMap.Mapnik",
        "esri": "Esri.WorldStreetMap",
        "custom": "Custom",
    }.get(provider, "OpenStreetMap.Mapnik")


@contextmanager
def contextily_request_timeout(contextily_module, timeout_seconds: float):
    """Temporarily add a timeout to Contextily tile HTTP requests."""
    tile_module = getattr(contextily_module, "tile", None)
    request_module = getattr(tile_module, "requests", None) if tile_module is not None else None
    original_get = getattr(request_module, "get", None) if request_module is not None else None
    if original_get is None:
        yield
        return

    def get_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", float(timeout_seconds))
        return original_get(*args, **kwargs)

    with _REQUEST_PATCH_LOCK:
        request_module.get = get_with_timeout
        try:
            yield
        finally:
            request_module.get = original_get
