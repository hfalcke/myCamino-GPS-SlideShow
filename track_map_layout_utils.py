#!/usr/bin/env python3
"""Shared naming, obstruction geometry, and cache helpers for track maps."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional


RectTuple = tuple[float, float, float, float]
CORNER_ORDER = ("top_right", "top_left", "bottom_right", "bottom_left")
TIME_LAPSE_SUFFIX = "-timelapse"
MEDIA_CLEAR_BOX_VERSION = 1
MEDIA_CLEAR_BOX_COORDINATE_SPACE = "image_fraction_bottom_left"
DEFAULT_TRACK_EDGE_MARGIN_FRACTION = 0.05
DEFAULT_GRID_LONG_AXIS = 192


def canonical_track_map_name(filename: str) -> str:
    """Return the standard variant name for a per-track map filename."""
    path = Path(filename)
    stem = path.stem
    if stem.endswith(TIME_LAPSE_SUFFIX):
        stem = stem[: -len(TIME_LAPSE_SUFFIX)]
    return str(path.with_name(stem + path.suffix))


def time_lapse_track_map_name(filename: str) -> str:
    """Return the time-lapse variant name for a per-track map filename."""
    path = Path(canonical_track_map_name(filename))
    return str(path.with_name(path.stem + TIME_LAPSE_SUFFIX + path.suffix))


def track_map_variant_names(filename: str, prefer_time_lapse: bool) -> list[str]:
    """Return preferred and fallback per-track map names without duplicates."""
    standard = canonical_track_map_name(filename)
    time_lapse = time_lapse_track_map_name(filename)
    return [time_lapse, standard] if prefer_time_lapse else [standard, time_lapse]


def resolve_track_map_variant(path: str | Path, prefer_time_lapse: bool) -> Optional[Path]:
    """Resolve an existing preferred map variant next to ``path``."""
    source = Path(path)
    for name in track_map_variant_names(source.name, prefer_time_lapse):
        candidate = source.with_name(name)
        if candidate.is_file():
            return candidate
    return None


def aspect_fit_size(source_width: float, source_height: float, max_width: float, max_height: float) -> tuple[float, float]:
    """Fit one rectangle inside another while preserving aspect ratio."""
    if source_width <= 0 or source_height <= 0:
        return max(1.0, max_width), max(1.0, max_height)
    scale = min(max_width / source_width, max_height / source_height)
    return max(1.0, source_width * scale), max(1.0, source_height * scale)


def map_plot_rect(image_rect: RectTuple, metadata: Optional[dict]) -> RectTuple:
    """Return the map axes in view coordinates, or the full image as fallback."""
    image_x, image_y, image_width, image_height = image_rect
    axes = metadata.get("axes_box_fraction") if isinstance(metadata, dict) else None
    if not isinstance(axes, dict):
        return image_rect
    try:
        left = max(0.0, min(1.0, float(axes["left"])))
        bottom = max(0.0, min(1.0, float(axes["bottom"])))
        right = max(left, min(1.0, left + float(axes["width"])))
        top = max(bottom, min(1.0, bottom + float(axes["height"])))
    except (KeyError, TypeError, ValueError):
        return image_rect
    if right <= left or top <= bottom:
        return image_rect
    return (
        image_x + left * image_width,
        image_y + bottom * image_height,
        (right - left) * image_width,
        (top - bottom) * image_height,
    )


def inset_rect(rect: RectTuple, fraction: float = DEFAULT_TRACK_EDGE_MARGIN_FRACTION) -> RectTuple:
    """Inset a rectangle by a fraction of its own width and height."""
    x, y, width, height = rect
    inset_x = max(0.0, width * fraction)
    inset_y = max(0.0, height * fraction)
    return (
        x + inset_x,
        y + inset_y,
        max(1.0, width - 2.0 * inset_x),
        max(1.0, height - 2.0 * inset_y),
    )


def clear_corner_rect_options(
    placement_rect: RectTuple,
    route_points: list[tuple[float, float]],
    grid_long_axis: int = DEFAULT_GRID_LONG_AXIS,
) -> dict[str, list[RectTuple]]:
    """Return route-free width/height frontiers anchored at every corner."""
    x, y, width, height = placement_rect
    if len(route_points) < 2 or width <= 1.0 or height <= 1.0:
        return {corner: [placement_rect] for corner in CORNER_ORDER}
    grid_long_axis = max(32, int(grid_long_axis))
    if width >= height:
        columns = grid_long_axis
        rows = max(32, round(grid_long_axis * height / width))
    else:
        rows = grid_long_axis
        columns = max(32, round(grid_long_axis * width / height))
    cell_width, cell_height = width / columns, height / rows
    sample_step = max(0.5, min(cell_width, cell_height) * 0.45)
    occupied: set[tuple[int, int]] = set()
    for start, end in zip(route_points, route_points[1:]):
        segment_length = math.hypot(end[0] - start[0], end[1] - start[1])
        sample_count = max(1, math.ceil(segment_length / sample_step))
        for sample_index in range(sample_count + 1):
            fraction = sample_index / sample_count
            point_x = start[0] + (end[0] - start[0]) * fraction
            point_y = start[1] + (end[1] - start[1]) * fraction
            if not (x <= point_x <= x + width and y <= point_y <= y + height):
                continue
            column = min(columns - 1, max(0, int((point_x - x) / cell_width)))
            row = min(rows - 1, max(0, int((point_y - y) / cell_height)))
            for column_offset in (-1, 0, 1):
                for row_offset in (-1, 0, 1):
                    marked_column, marked_row = column + column_offset, row + row_offset
                    if 0 <= marked_column < columns and 0 <= marked_row < rows:
                        occupied.add((marked_column, marked_row))

    result: dict[str, list[RectTuple]] = {}
    for corner in CORNER_ORDER:
        from_right = corner.endswith("right")
        from_top = corner.startswith("top")
        nearest_by_column = [rows] * columns
        for occupied_column, occupied_row in occupied:
            distance = rows - 1 - occupied_row if from_top else occupied_row
            nearest_by_column[occupied_column] = min(nearest_by_column[occupied_column], distance)
        clear_height = rows
        cell_options: list[tuple[int, int]] = []
        for horizontal_distance in range(columns):
            column = columns - 1 - horizontal_distance if from_right else horizontal_distance
            clear_height = min(clear_height, nearest_by_column[column])
            if clear_height <= 0:
                continue
            option = (horizontal_distance + 1, clear_height)
            if cell_options and cell_options[-1][1] == clear_height:
                cell_options[-1] = option
            else:
                cell_options.append(option)
        if not cell_options:
            cell_options = [(1, 1)]
        rect_options = []
        for width_cells, height_cells in cell_options:
            clear_width = max(1.0, width_cells * cell_width)
            clear_height_value = max(1.0, height_cells * cell_height)
            clear_x = x + width - clear_width if from_right else x
            clear_y = y + height - clear_height_value if from_top else y
            rect_options.append((clear_x, clear_y, clear_width, clear_height_value))
        result[corner] = rect_options
    return result


def largest_clear_corner_rects(
    placement_rect: RectTuple,
    route_points: list[tuple[float, float]],
    grid_long_axis: int = DEFAULT_GRID_LONG_AXIS,
) -> dict[str, RectTuple]:
    """Return the largest-area member of every corner's clear frontier."""
    options = clear_corner_rect_options(placement_rect, route_points, grid_long_axis)
    return {corner: max(rects, key=lambda rect: rect[2] * rect[3]) for corner, rects in options.items()}


def best_media_corner_layout(
    clear_rects: dict[str, RectTuple | list[RectTuple]],
    window_size: tuple[float, float],
    min_fraction: float,
    media_size: tuple[float, float],
) -> tuple[str, RectTuple, RectTuple]:
    """Choose the largest track-free framed media layout."""
    media_width, media_height = media_size
    best_corner = "top_right"
    best_outer: RectTuple = (0.0, 0.0, 1.0, 1.0)
    best_content: RectTuple = best_outer
    best_area = -1.0
    best_minimum_ratio = -1.0
    for corner in CORNER_ORDER:
        corner_options = clear_rects.get(corner, best_outer)
        if isinstance(corner_options, tuple):
            corner_options = [corner_options]
        for clear_x, clear_y, clear_width, clear_height in corner_options:
            provisional_width, provisional_height = aspect_fit_size(
                media_width, media_height, max(1.0, clear_width), max(1.0, clear_height)
            )
            frame = max(5.0, min(provisional_width, provisional_height) * 0.025)
            frame = min(frame, max(0.0, (min(clear_width, clear_height) - 1.0) / 2.0))
            draw_width, draw_height = aspect_fit_size(
                media_width,
                media_height,
                max(1.0, clear_width - frame * 2.0),
                max(1.0, clear_height - frame * 2.0),
            )
            framed_width, framed_height = draw_width + frame * 2.0, draw_height + frame * 2.0
            outer_x = clear_x if corner.endswith("left") else clear_x + clear_width - framed_width
            outer_y = clear_y if corner.startswith("bottom") else clear_y + clear_height - framed_height
            outer = (outer_x, outer_y, framed_width, framed_height)
            content = (outer_x + frame, outer_y + frame, draw_width, draw_height)
            area = draw_width * draw_height
            long_dimension = framed_width if media_width >= media_height else framed_height
            window_long_dimension = window_size[0] if media_width >= media_height else window_size[1]
            minimum_ratio = min(1.0, long_dimension / max(1.0, window_long_dimension * min_fraction))
            if area > best_area or (math.isclose(area, best_area) and minimum_ratio > best_minimum_ratio):
                best_corner, best_outer, best_content, best_area = corner, outer, content, area
                best_minimum_ratio = minimum_ratio
    return best_corner, best_outer, best_content


def projected_route_pixels(
    projected_points: list[tuple[float, float]],
    extent: tuple[float, float, float, float],
    image_size: tuple[float, float],
    axes_box: tuple[float, float, float, float] | list[float],
) -> list[tuple[float, float]]:
    """Project Mercator points into full-image bottom-left pixel coordinates."""
    min_x, max_x, min_y, max_y = extent
    width, height = image_size
    left, bottom, axes_width, axes_height = axes_box
    span_x, span_y = max(max_x - min_x, 1e-12), max(max_y - min_y, 1e-12)
    return [
        (
            (left + ((x - min_x) / span_x) * axes_width) * width,
            (bottom + ((y - min_y) / span_y) * axes_height) * height,
        )
        for x, y in projected_points
    ]


def extent_with_route_at_corner_extreme(
    standard_extent: tuple[float, float, float, float],
    projected_points: list[tuple[float, float]],
    free_corner: str,
    margin_fraction: float,
) -> tuple[float, float, float, float]:
    """Shift a fixed-size extent so the route is farthest from ``free_corner``."""
    min_x, max_x, min_y, max_y = standard_extent
    width, height = max_x - min_x, max_y - min_y
    data_min_x = min(point[0] for point in projected_points)
    data_max_x = max(point[0] for point in projected_points)
    data_min_y = min(point[1] for point in projected_points)
    data_max_y = max(point[1] for point in projected_points)
    # A free right/top corner requires the route against the opposite edge.
    shifted_min_x = data_min_x - margin_fraction * width if free_corner.endswith("right") else data_max_x - (1.0 - margin_fraction) * width
    shifted_min_y = data_min_y - margin_fraction * height if free_corner.startswith("top") else data_max_y - (1.0 - margin_fraction) * height
    return shifted_min_x, shifted_min_x + width, shifted_min_y, shifted_min_y + height


def optimized_track_extent(
    standard_extent: tuple[float, float, float, float],
    projected_points: list[tuple[float, float]],
    image_size: tuple[float, float],
    axes_box: tuple[float, float, float, float] | list[float],
    margin_fraction: float = DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
    grid_long_axis: int = DEFAULT_GRID_LONG_AXIS,
) -> tuple[tuple[float, float, float, float], str, tuple[float, float], dict[str, list[RectTuple]]]:
    """Choose the legal extreme shift with the largest free corner rectangle."""
    if len(projected_points) < 2:
        options = clear_box_options_for_extent(
            projected_points, standard_extent, image_size, axes_box, margin_fraction, grid_long_axis
        )
        return standard_extent, CORNER_ORDER[0], (0.0, 0.0), options
    standard_center = (
        (standard_extent[0] + standard_extent[1]) / 2.0,
        (standard_extent[2] + standard_extent[3]) / 2.0,
    )
    centered_options = clear_box_options_for_extent(
        projected_points, standard_extent, image_size, axes_box, margin_fraction, grid_long_axis
    )
    centered_corner = max(
        CORNER_ORDER,
        key=lambda corner: max(rect[2] * rect[3] for rect in centered_options[corner]),
    )
    centered_maximum_area = max(rect[2] * rect[3] for rect in centered_options[centered_corner])
    best = (
        (centered_maximum_area, 0.0, -CORNER_ORDER.index(centered_corner)),
        standard_extent,
        centered_corner,
        (0.0, 0.0),
        centered_options,
    )
    for order, corner in enumerate(CORNER_ORDER):
        extent = extent_with_route_at_corner_extreme(standard_extent, projected_points, corner, margin_fraction)
        options = clear_box_options_for_extent(
            projected_points, extent, image_size, axes_box, margin_fraction, grid_long_axis
        )
        maximum = max(options[corner], key=lambda rect: rect[2] * rect[3])
        center = ((extent[0] + extent[1]) / 2.0, (extent[2] + extent[3]) / 2.0)
        displacement = math.hypot(center[0] - standard_center[0], center[1] - standard_center[1])
        score = (maximum[2] * maximum[3], -displacement, -order)
        if score > best[0]:
            best = (score, extent, corner, (center[0] - standard_center[0], center[1] - standard_center[1]), options)
    return best[1], best[2], best[3], best[4]


def clear_box_options_for_extent(
    projected_points: list[tuple[float, float]],
    extent: tuple[float, float, float, float],
    image_size: tuple[float, float],
    axes_box: tuple[float, float, float, float] | list[float],
    margin_fraction: float = DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
    grid_long_axis: int = DEFAULT_GRID_LONG_AXIS,
) -> dict[str, list[RectTuple]]:
    """Calculate all route-free corner frontiers in full-image pixels."""
    width, height = image_size
    axes_rect = (axes_box[0] * width, axes_box[1] * height, axes_box[2] * width, axes_box[3] * height)
    placement_rect = inset_rect(axes_rect, margin_fraction)
    points = projected_route_pixels(projected_points, extent, image_size, axes_box)
    return clear_corner_rect_options(placement_rect, points, grid_long_axis)


def _normalized_rect(rect: RectTuple, image_size: tuple[float, float]) -> dict[str, float]:
    width, height = image_size
    return {
        "x": rect[0] / width,
        "y": rect[1] / height,
        "width": rect[2] / width,
        "height": rect[3] / height,
    }


def build_media_clear_boxes_metadata(
    options: dict[str, list[RectTuple]],
    image_size: tuple[float, float],
    margin_fraction: float,
    track_fingerprint: Optional[str],
    grid_long_axis: int = DEFAULT_GRID_LONG_AXIS,
) -> dict:
    """Serialize clear-box frontiers in full-image normalized coordinates."""
    corners = {}
    for corner in CORNER_ORDER:
        frontier = options.get(corner) or [(0.0, 0.0, 1.0, 1.0)]
        maximum = max(frontier, key=lambda rect: rect[2] * rect[3])
        corners[corner] = {
            "maximum": _normalized_rect(maximum, image_size),
            "frontier": [_normalized_rect(rect, image_size) for rect in frontier],
        }
    return {
        "version": MEDIA_CLEAR_BOX_VERSION,
        "coordinate_space": MEDIA_CLEAR_BOX_COORDINATE_SPACE,
        "margin_fraction": float(margin_fraction),
        "grid_long_axis": int(grid_long_axis),
        "image_size_px": {"width": int(image_size[0]), "height": int(image_size[1])},
        "track_fingerprint": track_fingerprint,
        "corners": corners,
    }


def cached_clear_box_options(
    metadata: Optional[dict],
    image_rect: RectTuple,
    actual_image_size: Optional[tuple[float, float]] = None,
) -> Optional[dict[str, list[RectTuple]]]:
    """Validate and convert cached normalized frontiers into view coordinates."""
    if not isinstance(metadata, dict):
        return None
    cache = metadata.get("media_clear_boxes")
    if not isinstance(cache, dict):
        return None
    if cache.get("version") != MEDIA_CLEAR_BOX_VERSION or cache.get("coordinate_space") != MEDIA_CLEAR_BOX_COORDINATE_SPACE:
        return None
    if cache.get("track_fingerprint") != metadata.get("track_fingerprint"):
        return None
    try:
        cached_margin = float(cache["margin_fraction"])
        metadata_margin = float(metadata["track_edge_margin_fraction"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 <= cached_margin < 0.5 or not math.isclose(cached_margin, metadata_margin, abs_tol=1e-9):
        return None
    source_size = cache.get("image_size_px")
    metadata_size = metadata.get("image_size_px")
    try:
        source_pair = (float(source_size["width"]), float(source_size["height"]))
        metadata_pair = (float(metadata_size["width"]), float(metadata_size["height"]))
    except (KeyError, TypeError, ValueError):
        return None
    if source_pair != metadata_pair:
        return None
    if actual_image_size and (abs(source_pair[0] - actual_image_size[0]) > 1.0 or abs(source_pair[1] - actual_image_size[1]) > 1.0):
        return None
    corners = cache.get("corners")
    if not isinstance(corners, dict):
        return None
    image_x, image_y, image_width, image_height = image_rect
    result: dict[str, list[RectTuple]] = {}
    for corner in CORNER_ORDER:
        entry = corners.get(corner)
        frontier = entry.get("frontier") if isinstance(entry, dict) else None
        if not isinstance(frontier, list) or not frontier:
            return None
        converted = []
        for item in frontier:
            try:
                x = float(item["x"])
                y = float(item["y"])
                width = float(item["width"])
                height = float(item["height"])
            except (KeyError, TypeError, ValueError):
                return None
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
                return None
            converted.append((image_x + x * image_width, image_y + y * image_height, width * image_width, height * image_height))
        result[corner] = converted
    return result
