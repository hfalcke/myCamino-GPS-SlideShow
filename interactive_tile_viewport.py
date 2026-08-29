# SPDX-License-Identifier: GPL-3.0-or-later
"""Progressive XYZ tiles and viewport math for interactive GPX maps."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

from map_provider_utils import (
    configure_contextily_cache,
    contextily_provider,
    contextily_request_timeout,
    provider_tile_url,
)


WEB_MERCATOR_HALF_WORLD_M = 20037508.342789244
WEB_MERCATOR_WORLD_M = WEB_MERCATOR_HALF_WORLD_M * 2.0


@dataclass(frozen=True)
class TileCoordinate:
    zoom: int
    x: int
    y: int


def tile_bounds_mercator(tile: TileCoordinate) -> dict[str, float]:
    """Return an XYZ tile's Web Mercator bounds."""
    count = 1 << int(tile.zoom)
    size = WEB_MERCATOR_WORLD_M / count
    min_x = -WEB_MERCATOR_HALF_WORLD_M + tile.x * size
    max_y = WEB_MERCATOR_HALF_WORLD_M - tile.y * size
    return {
        "min_x": min_x,
        "max_x": min_x + size,
        "min_y": max_y - size,
        "max_y": max_y,
    }


def visible_tiles(extent: dict[str, float], zoom: int) -> list[TileCoordinate]:
    """Return visible tiles ordered from the viewport center outwards."""
    zoom = max(0, int(zoom))
    count = 1 << zoom
    size = WEB_MERCATOR_WORLD_M / count

    def x_index(value: float) -> int:
        return max(0, min(count - 1, int(math.floor((value + WEB_MERCATOR_HALF_WORLD_M) / size))))

    def y_index(value: float) -> int:
        return max(0, min(count - 1, int(math.floor((WEB_MERCATOR_HALF_WORLD_M - value) / size))))

    x0 = x_index(float(extent["min_x"]))
    x1 = x_index(math.nextafter(float(extent["max_x"]), -math.inf))
    y0 = y_index(float(extent["max_y"]))
    y1 = y_index(math.nextafter(float(extent["min_y"]), math.inf))
    center_x = (x0 + x1) / 2.0
    center_y = (y0 + y1) / 2.0
    tiles = [
        TileCoordinate(zoom, x, y)
        for y in range(min(y0, y1), max(y0, y1) + 1)
        for x in range(min(x0, x1), max(x0, x1) + 1)
    ]
    return sorted(
        tiles,
        key=lambda tile: ((tile.x - center_x) ** 2 + (tile.y - center_y) ** 2, tile.y, tile.x),
    )


def shifted_extent(
    extent: dict[str, float],
    delta_x_points: float,
    delta_y_points: float,
    viewport_width: float,
    viewport_height: float,
) -> dict[str, float]:
    """Shift an extent by a view-space drag or scroll delta."""
    width = max(float(viewport_width), 1.0)
    height = max(float(viewport_height), 1.0)
    span_x = float(extent["max_x"]) - float(extent["min_x"])
    span_y = float(extent["max_y"]) - float(extent["min_y"])
    shift_x = -float(delta_x_points) / width * span_x
    # Cocoa view coordinates grow upward. Moving the pointer upward should
    # move the map content upward, which shifts the viewed world extent down.
    shift_y = -float(delta_y_points) / height * span_y
    return {
        "min_x": float(extent["min_x"]) + shift_x,
        "max_x": float(extent["max_x"]) + shift_x,
        "min_y": float(extent["min_y"]) + shift_y,
        "max_y": float(extent["max_y"]) + shift_y,
    }


def zoomed_extent(
    extent: dict[str, float],
    factor: float,
    focus_x: float | None = None,
    focus_y: float | None = None,
) -> dict[str, float]:
    """Zoom around a Web Mercator focus while keeping that focus stationary."""
    factor = max(float(factor), 1.0e-6)
    min_x = float(extent["min_x"])
    max_x = float(extent["max_x"])
    min_y = float(extent["min_y"])
    max_y = float(extent["max_y"])
    focus_x = (min_x + max_x) / 2.0 if focus_x is None else float(focus_x)
    focus_y = (min_y + max_y) / 2.0 if focus_y is None else float(focus_y)
    left_fraction = (focus_x - min_x) / max(max_x - min_x, 1.0)
    bottom_fraction = (focus_y - min_y) / max(max_y - min_y, 1.0)
    span_x = max((max_x - min_x) / factor, 1.0)
    span_y = max((max_y - min_y) / factor, 1.0)
    return {
        "min_x": focus_x - left_fraction * span_x,
        "max_x": focus_x + (1.0 - left_fraction) * span_x,
        "min_y": focus_y - bottom_fraction * span_y,
        "max_y": focus_y + (1.0 - bottom_fraction) * span_y,
    }


def tile_zoom_after_scale(start_zoom: int, accumulated_scale: float) -> int:
    """Convert a completed continuous zoom gesture into an XYZ zoom level."""
    scale = max(float(accumulated_scale), 1.0e-6)
    return int(start_zoom) + int(round(math.log2(scale)))


def load_tile_png(
    tile: TileCoordinate,
    *,
    provider: str,
    cache_dir: Path,
    timeout_seconds: float,
    custom_url: str = "",
    custom_attribution: str = "",
    maximum_zoom: int = 19,
    credential_id: str = "default",
) -> bytes:
    """Load one tile through Contextily's persistent cache and provider policy."""
    import contextily as cx
    from PIL import Image

    configure_contextily_cache(cx, cache_dir)
    source = contextily_provider(
        cx,
        provider,
        custom_url,
        custom_attribution,
        maximum_zoom,
        credential_id,
    )
    url = provider_tile_url(source, tile.x, tile.y, tile.zoom)
    cached_fetch = cx.tile.memory.cache(cx.tile._fetch_tile)
    with contextily_request_timeout(cx, timeout_seconds, provider):
        array = cached_fetch(url, 0, 1, {})
    output = io.BytesIO()
    Image.fromarray(array).save(output, format="PNG")
    return output.getvalue()
