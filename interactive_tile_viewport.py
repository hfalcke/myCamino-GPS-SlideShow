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
DEFAULT_WORLD_OCEAN_RGB = (170 / 255.0, 211 / 255.0, 223 / 255.0)


@dataclass(frozen=True)
class TileCoordinate:
    zoom: int
    x: int
    y: int


def is_complete_world_extent(
    extent: dict[str, float],
    *,
    tolerance_fraction: float = 1e-7,
) -> bool:
    """Return whether an extent represents the finite Web Mercator world."""
    try:
        values = (
            float(extent["min_x"]),
            float(extent["max_x"]),
            float(extent["min_y"]),
            float(extent["max_y"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    tolerance = WEB_MERCATOR_WORLD_M * max(float(tolerance_fraction), 0.0)
    expected = (
        -WEB_MERCATOR_HALF_WORLD_M,
        WEB_MERCATOR_HALF_WORLD_M,
        -WEB_MERCATOR_HALF_WORLD_M,
        WEB_MERCATOR_HALF_WORLD_M,
    )
    return all(abs(value - target) <= tolerance for value, target in zip(values, expected))


def world_ocean_sample_pixels(
    tile: TileCoordinate,
    pixel_width: int,
    pixel_height: int,
) -> list[tuple[int, int]]:
    """Choose antimeridian samples that are normally open ocean."""
    width = max(int(pixel_width), 1)
    height = max(int(pixel_height), 1)
    count = 1 << max(0, int(tile.zoom))
    if count == 1:
        return [
            (min(2, width - 1), height // 2),
            (max(width - 3, 0), height // 2),
        ]
    equator_rows = {count // 2 - 1, count // 2}
    if tile.x not in {0, count - 1} or tile.y not in equator_rows:
        return []
    sample_x = min(2, width - 1) if tile.x == 0 else max(width - 3, 0)
    return [
        (sample_x, max(0, min(height - 1, int(height * fraction))))
        for fraction in (0.35, 0.5, 0.65)
    ]


def sample_world_ocean_rgb_from_png(
    png_data: bytes,
    tile: TileCoordinate,
) -> list[tuple[float, float, float]]:
    """Sample open-ocean colors without decoding non-candidate tiles."""
    if not world_ocean_sample_pixels(tile, 256, 256):
        return []
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png_data)) as source:
            image = source.convert("RGBA")
            positions = world_ocean_sample_pixels(tile, image.width, image.height)
            samples = []
            for position in positions:
                red, green, blue, alpha = image.getpixel(position)
                if alpha < 192:
                    continue
                samples.append(
                    tuple(float(component) / 255.0 for component in (red, green, blue))
                )
            return samples
    except (OSError, TypeError, ValueError):
        return []


def aspect_preserving_rect(
    viewport_width: float,
    viewport_height: float,
    extent: dict[str, float],
) -> tuple[float, float, float, float]:
    """Return a centered viewport rectangle that does not stretch map units."""
    view_width = max(float(viewport_width), 1.0)
    view_height = max(float(viewport_height), 1.0)
    span_x = max(float(extent["max_x"]) - float(extent["min_x"]), 1.0)
    span_y = max(float(extent["max_y"]) - float(extent["min_y"]), 1.0)
    map_ratio = span_x / span_y
    view_ratio = view_width / view_height
    if view_ratio > map_ratio:
        height = view_height
        width = height * map_ratio
    else:
        width = view_width
        height = width / map_ratio
    return (view_width - width) / 2.0, (view_height - height) / 2.0, width, height


def clamp_extent_to_web_mercator(extent: dict[str, float]) -> dict[str, float]:
    """Keep a viewport inside the finite XYZ Web Mercator world."""
    min_x = float(extent["min_x"])
    max_x = float(extent["max_x"])
    min_y = float(extent["min_y"])
    max_y = float(extent["max_y"])

    def clamp_axis(low: float, high: float) -> tuple[float, float]:
        span = min(max(high - low, 1.0), WEB_MERCATOR_WORLD_M)
        center = (low + high) / 2.0
        minimum_center = -WEB_MERCATOR_HALF_WORLD_M + span / 2.0
        maximum_center = WEB_MERCATOR_HALF_WORLD_M - span / 2.0
        center = max(minimum_center, min(maximum_center, center))
        return center - span / 2.0, center + span / 2.0

    min_x, max_x = clamp_axis(min_x, max_x)
    min_y, max_y = clamp_axis(min_y, max_y)
    return {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y}


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


def tile_intersects_extent(
    tile: TileCoordinate,
    extent: dict[str, float],
) -> bool:
    """Return whether a tile overlaps a Web Mercator viewport."""
    bounds = tile_bounds_mercator(tile)
    return not (
        bounds["max_x"] <= float(extent["min_x"])
        or bounds["min_x"] >= float(extent["max_x"])
        or bounds["max_y"] <= float(extent["min_y"])
        or bounds["min_y"] >= float(extent["max_y"])
    )


def panned_extent_to_include_point(
    extent: dict[str, float],
    point_x: float,
    point_y: float,
    *,
    margin_fraction: float = 0.10,
) -> dict[str, float]:
    """Pan without changing scale until a point lies inside a safe margin."""
    current = {
        key: float(extent[key])
        for key in ("min_x", "max_x", "min_y", "max_y")
    }
    span_x = max(current["max_x"] - current["min_x"], 1.0)
    span_y = max(current["max_y"] - current["min_y"], 1.0)
    margin = max(0.0, min(float(margin_fraction), 0.45))
    inner_min_x = current["min_x"] + span_x * margin
    inner_max_x = current["max_x"] - span_x * margin
    inner_min_y = current["min_y"] + span_y * margin
    inner_max_y = current["max_y"] - span_y * margin
    shift_x = min(0.0, float(point_x) - inner_min_x) + max(
        0.0, float(point_x) - inner_max_x
    )
    shift_y = min(0.0, float(point_y) - inner_min_y) + max(
        0.0, float(point_y) - inner_max_y
    )
    if shift_x == 0.0 and shift_y == 0.0:
        return current
    return clamp_extent_to_web_mercator(
        {
            "min_x": current["min_x"] + shift_x,
            "max_x": current["max_x"] + shift_x,
            "min_y": current["min_y"] + shift_y,
            "max_y": current["max_y"] + shift_y,
        }
    )


def retained_tile_draw_order(
    keys,
    extent: dict[str, float],
    target_zoom: int,
) -> list[tuple[int, int, int]]:
    """Order reusable tiles so exact target tiles replace coarse substitutes."""
    target_zoom = max(0, int(target_zoom))
    candidates = []
    for raw_key in keys:
        try:
            key = tuple(int(value) for value in raw_key)
            tile = TileCoordinate(*key)
        except (TypeError, ValueError):
            continue
        if len(key) != 3 or not tile_intersects_extent(tile, extent):
            continue
        if tile.zoom < target_zoom:
            priority = (0, tile.zoom)
        elif tile.zoom > target_zoom:
            priority = (1, tile.zoom)
        else:
            priority = (2, tile.zoom)
        candidates.append((priority, key))
    return [
        key
        for _priority, key in sorted(
            candidates,
            key=lambda item: (item[0], item[1][2], item[1][1]),
        )
    ]


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


def scroll_pan_deltas(
    delta_x_points: float,
    delta_y_points: float,
    *,
    reverse_vertical: bool = False,
) -> tuple[float, float]:
    """Return view-space pan deltas without changing drag-pan semantics."""
    delta_y = -float(delta_y_points) if reverse_vertical else float(delta_y_points)
    return float(delta_x_points), delta_y


def mouse_wheel_zoom_factor(delta_y: float, *, units_per_level: float = 4.0) -> float:
    """Convert wheel units to a bounded continuous scale, not a level per event."""
    units = max(-4.0, min(4.0, float(delta_y)))
    return 2.0 ** (units / max(float(units_per_level), 1.0))


def tile_zoom_for_viewport(
    extent: dict[str, float],
    viewport_width: float,
    viewport_height: float,
    *,
    maximum_zoom: int = 19,
    tile_pixels: int = 256,
) -> int:
    """Choose an XYZ level whose native pixels cover the fitted viewport."""
    span_x = max(float(extent["max_x"]) - float(extent["min_x"]), 1.0)
    span_y = max(float(extent["max_y"]) - float(extent["min_y"]), 1.0)
    width = max(float(viewport_width), 1.0)
    height = max(float(viewport_height), 1.0)
    pixel_size = max(int(tile_pixels), 1)
    zoom_x = math.log2(WEB_MERCATOR_WORLD_M * width / (span_x * pixel_size))
    zoom_y = math.log2(WEB_MERCATOR_WORLD_M * height / (span_y * pixel_size))
    return max(0, min(int(maximum_zoom), int(math.ceil(max(zoom_x, zoom_y)))))


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
    # Interactive viewports request only visible tiles. Two workers may fetch
    # them concurrently; generated maps retain the conservative provider pace.
    with contextily_request_timeout(
        cx,
        timeout_seconds,
        provider,
        minimum_interval_seconds=0.0,
    ):
        array = cached_fetch(url, 0, 1, {})
    output = io.BytesIO()
    Image.fromarray(array).save(output, format="PNG")
    return output.getvalue()
