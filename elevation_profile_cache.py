#!/usr/bin/env python3
"""Shared elevation-profile data and cache helpers."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable

from track_map_layout_utils import canonical_track_map_name


ELEVATION_PROFILE_RENDER_VERSION = 1
ELEVATION_PROFILE_WIDTH = 1200
ELEVATION_PROFILE_HEIGHT = 360
ELEVATION_PROFILE_PLOT_RECT = (
    88.0,
    54.0,
    ELEVATION_PROFILE_WIDTH - 118.0,
    ELEVATION_PROFILE_HEIGHT - 112.0,
)


def elevation_profile_visible_range(
    rows: Iterable[dict],
    x_min: float,
    x_max: float,
) -> tuple[float, float] | None:
    """Return visible elevation bounds with five percent headroom."""
    visible = [
        float(row["elevation"])
        for row in rows
        if row.get("elevation") is not None
        and x_min <= float(row.get("distance", 0.0)) <= x_max
    ]
    if not visible:
        return None
    minimum = min(visible)
    maximum = max(visible)
    span = maximum - minimum
    margin = span * 0.05 if span > 0.0 else max(5.0, abs(maximum) * 0.05)
    return minimum - margin, maximum + margin


def elevation_profile_segments(metadata: object) -> list[list[tuple[float, float]]]:
    """Extract distance/elevation samples from processed Track Map metadata."""
    if not isinstance(metadata, dict):
        return []
    raw_segments = metadata.get("processed_track_segments")
    if not isinstance(raw_segments, list):
        return []
    result: list[list[tuple[float, float]]] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, list):
            continue
        segment: list[tuple[float, float]] = []
        for item in raw_segment:
            if not isinstance(item, dict):
                continue
            try:
                distance = float(item["cumulative_distance_km"])
                elevation = float(item["elevation_m"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(distance) or not math.isfinite(elevation):
                continue
            if segment and distance < segment[-1][0]:
                continue
            if segment and math.isclose(distance, segment[-1][0]):
                segment[-1] = (distance, elevation)
            else:
                segment.append((distance, elevation))
        if segment:
            result.append(segment)
    return result


def elevation_profile_ranges(
    segments: list[list[tuple[float, float]]],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return full distance and min/max elevation ranges for a profile."""
    rows = [
        {"distance": distance, "elevation": elevation}
        for segment in segments
        for distance, elevation in segment
    ]
    if len(rows) < 2:
        return None
    x_min = min(float(row["distance"]) for row in rows)
    x_max = max(float(row["distance"]) for row in rows)
    if x_max <= x_min:
        x_max = x_min + 0.001
    y_range = elevation_profile_visible_range(rows, x_min, x_max)
    return ((x_min, x_max), y_range) if y_range is not None else None


def elevation_profile_state_at_distance(
    metadata: object,
    distance_km: float,
) -> tuple[float, float] | None:
    """Return a segment-safe distance/elevation point for one route position."""
    segments = elevation_profile_segments(metadata)
    candidates: list[tuple[float, float]] = []
    target = float(distance_km)
    if not math.isfinite(target):
        return None
    for segment in segments:
        if not segment:
            continue
        candidates.extend((segment[0], segment[-1]))
        if target < segment[0][0] or target > segment[-1][0]:
            continue
        for start, end in zip(segment, segment[1:]):
            if not start[0] <= target <= end[0]:
                continue
            span = end[0] - start[0]
            fraction = 0.0 if span <= 0.0 else (target - start[0]) / span
            return (
                target,
                start[1] + (end[1] - start[1]) * fraction,
            )
        return min(segment, key=lambda point: abs(point[0] - target))
    if not candidates:
        return None
    return min(candidates, key=lambda point: abs(point[0] - target))


def elevation_profile_marker_point(
    metadata: object,
    distance_km: float,
    elevation_m: float | None = None,
) -> tuple[float, float] | None:
    """Map a route position into the cached elevation-profile image."""
    segments = elevation_profile_segments(metadata)
    ranges = elevation_profile_ranges(segments)
    state = elevation_profile_state_at_distance(metadata, distance_km)
    if ranges is None or state is None:
        return None
    distance = state[0]
    elevation = state[1] if elevation_m is None else float(elevation_m)
    if not math.isfinite(elevation):
        elevation = state[1]
    (x_min, x_max), (y_min, y_max) = ranges
    plot_x, plot_y, plot_width, plot_height = ELEVATION_PROFILE_PLOT_RECT
    x = plot_x + ((distance - x_min) / max(0.001, x_max - x_min)) * plot_width
    y = plot_y + ((elevation - y_min) / max(1.0, y_max - y_min)) * plot_height
    return (
        max(plot_x, min(plot_x + plot_width, x)),
        max(plot_y, min(plot_y + plot_height, y)),
    )


def elevation_profile_cache_paths(track_map_path: str | Path) -> tuple[Path, Path]:
    """Return the shared PNG and manifest paths for either map variant."""
    source = Path(track_map_path)
    canonical = Path(canonical_track_map_name(source.name))
    cache_dir = source.parent / "elevation-profiles"
    stem = canonical.stem
    return cache_dir / f"{stem}-elevation.png", cache_dir / f"{stem}-elevation.json"


def elevation_profile_source_signature(metadata: object) -> dict:
    """Return fields that make a cached profile stale when processing changes."""
    if not isinstance(metadata, dict):
        return {}
    processing = metadata.get("gpx_processing")
    digest = hashlib.sha256()
    for segment in elevation_profile_segments(metadata):
        digest.update(b"segment:")
        for distance, elevation in segment:
            digest.update(f"{distance:.9f},{elevation:.6f};".encode("ascii"))
    return {
        "track_fingerprint": metadata.get("track_fingerprint"),
        "track_name": metadata.get("track_name"),
        "gpx_processing": processing if isinstance(processing, dict) else {},
        "retained_point_count": metadata.get("retained_point_count"),
        "profile_fingerprint": digest.hexdigest(),
    }


def elevation_profile_manifest(metadata: object) -> dict:
    """Build the manifest stored beside a generated elevation PNG."""
    return {
        "version": ELEVATION_PROFILE_RENDER_VERSION,
        "width": ELEVATION_PROFILE_WIDTH,
        "height": ELEVATION_PROFILE_HEIGHT,
        "y_axis": "track_min_max_5_percent",
        "source": elevation_profile_source_signature(metadata),
    }


def elevation_profile_cache_is_current(manifest: object, metadata: object) -> bool:
    """Return whether a cached PNG was generated from the current track data."""
    return isinstance(manifest, dict) and manifest == elevation_profile_manifest(metadata)
