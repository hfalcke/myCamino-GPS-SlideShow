#!/usr/bin/env python3
"""Compact persistent metrics cache for GPX Editor startup.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from json_storage import atomic_write_json


CACHE_VERSION = 3
IMPORTER_VERSION = 1
DEFAULT_CACHE_DIR = Path.home() / "Library" / "Caches" / "myCamino" / "gpx-editor-metrics"
DISPLAY_GEOMETRY_LEVELS = (64, 160, 320)
_DATETIME_KEYS = {"time", "start_time", "end_time"}
_METRIC_KEYS = {
    "time",
    "start_time",
    "end_time",
    "duration",
    "length_km",
    "distance_km",
    "speed_kmh",
    "moving_speed_kmh",
    "ascent_m",
    "descent_m",
    "npoints",
    "raw_npoints",
    "retained_npoints",
    "rejection_counts",
    "processing_options",
    "first_lat",
    "first_lon",
    "last_lat",
    "last_lon",
}


@dataclass(frozen=True)
class MetricsCacheLookup:
    exact_source: bool
    tracks: tuple[dict[str, Any], ...]


def _sample_points(points: list[Any], limit: int) -> list[Any]:
    if len(points) <= limit:
        return list(points)
    if limit <= 2:
        return [points[0], points[-1]]
    indexes = {
        round(index * (len(points) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [points[index] for index in sorted(indexes)]


def display_geometry_for_processed_track(processed: Any) -> dict[str, Any]:
    """Create a compact, segment-preserving overview representation."""
    source_segments = [list(segment.points) for segment in processed.segments if segment.points]
    levels: dict[str, list[list[list[float]]]] = {}
    total_points = sum(len(segment) for segment in source_segments)
    for level in DISPLAY_GEOMETRY_LEVELS:
        sampled_segments = []
        for segment in source_segments:
            if total_points <= level:
                allowance = len(segment)
            else:
                allowance = max(2, round(level * len(segment) / total_points))
            sampled_segments.append(
                [
                    [
                        float(point.lat),
                        float(point.lon),
                        int(getattr(point, "source_index", -1)),
                    ]
                    for point in _sample_points(segment, allowance)
                ]
            )
        levels[str(level)] = sampled_segments

    projected = []
    radius = 6378137.0
    for segment in source_segments:
        for point in segment:
            limited_lat = max(min(float(point.lat), 85.05112878), -85.05112878)
            projected.append(
                (
                    radius * math.radians(float(point.lon)),
                    radius
                    * math.log(
                        math.tan(math.pi / 4.0 + math.radians(limited_lat) / 2.0)
                    ),
                )
            )
    extent = None
    if projected:
        xs = [point[0] for point in projected]
        ys = [point[1] for point in projected]
        extent = {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
        }
    return {"levels": levels, "extent_mercator": extent}


def match_cached_tracks(
    track_elements: Iterable[Any],
    lookup: MetricsCacheLookup | None,
    fingerprint_callback,
) -> list[tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]]:
    """Match current tracks without hashing an exact unchanged source."""
    elements = list(track_elements)
    if lookup is None:
        return [(None, None, None) for _element in elements]
    if lookup.exact_source and len(lookup.tracks) == len(elements):
        by_index = {int(item["source_index"]): item for item in lookup.tracks}
        return [
            (
                by_index[index]["fingerprint"],
                dict(by_index[index]["metrics"]),
                dict(by_index[index]["display_geometry"]),
            )
            if index in by_index
            else (None, None, None)
            for index in range(len(elements))
        ]
    cached_by_fingerprint = defaultdict(deque)
    for item in lookup.tracks:
        cached_by_fingerprint[item["fingerprint"]].append(item)
    result = []
    for element in elements:
        fingerprint = fingerprint_callback(element)
        matches = cached_by_fingerprint.get(fingerprint)
        cached = matches.popleft() if matches else None
        result.append(
            (
                fingerprint,
                dict(cached["metrics"]) if cached is not None else None,
                dict(cached["display_geometry"]) if cached is not None else None,
            )
        )
    return result


def _display_geometry_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"levels": {}, "extent_mercator": None}
    levels = {}
    raw_levels = value.get("levels")
    if isinstance(raw_levels, dict):
        for key, segments in raw_levels.items():
            if not isinstance(segments, list):
                continue
            normalized_segments = []
            for segment in segments:
                if not isinstance(segment, list):
                    continue
                normalized = []
                for coordinate in segment:
                    if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                        continue
                    try:
                        source_index = (
                            int(coordinate[2]) if len(coordinate) >= 3 else -1
                        )
                        normalized.append(
                            [
                                round(float(coordinate[0]), 9),
                                round(float(coordinate[1]), 9),
                                source_index,
                            ]
                        )
                    except (TypeError, ValueError):
                        continue
                if normalized:
                    normalized_segments.append(normalized)
            levels[str(key)] = normalized_segments
    extent = value.get("extent_mercator")
    normalized_extent = None
    if isinstance(extent, dict):
        try:
            normalized_extent = {
                key: float(extent[key])
                for key in ("min_x", "max_x", "min_y", "max_y")
            }
        except (KeyError, TypeError, ValueError):
            normalized_extent = None
    return {"levels": levels, "extent_mercator": normalized_extent}


def source_identity(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve(strict=False)
    stat_result = source.stat()
    return {
        "path": str(source),
        "size": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
    }


def cache_path(path: str | Path, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> Path:
    source = Path(path).expanduser().resolve(strict=False)
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    return Path(cache_dir).expanduser() / f"{digest}.json"


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def content_cache_path(
    source_digest: str, cache_dir: str | Path = DEFAULT_CACHE_DIR
) -> Path:
    return Path(cache_dir).expanduser() / "content" / f"{source_digest}.json"


def _options_payload(options: Any) -> dict[str, float]:
    values = options.as_dict() if hasattr(options, "as_dict") else dict(options)
    return {str(key): float(value) for key, value in values.items()}


def _metrics_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _METRIC_KEYS:
        value = metrics.get(key)
        if key in _DATETIME_KEYS:
            payload[key] = value.isoformat() if isinstance(value, datetime) else None
        elif key == "duration":
            payload[key] = value.total_seconds() if isinstance(value, timedelta) else None
        else:
            payload[key] = value
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _metrics_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    try:
        metrics = {key: payload.get(key) for key in _METRIC_KEYS}
        for key in _DATETIME_KEYS:
            metrics[key] = _parse_datetime(payload.get(key))
        duration = payload.get("duration")
        metrics["duration"] = None if duration is None else timedelta(seconds=float(duration))
        for key in ("length_km", "ascent_m", "descent_m"):
            metrics[key] = float(payload[key])
        for key in ("npoints", "raw_npoints", "retained_npoints"):
            metrics[key] = int(payload[key])
    except (KeyError, TypeError, ValueError):
        return None
    metrics["_anchor_key"] = None
    return metrics


def load_metrics_cache(
    source_path: str | Path,
    options: Any,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> MetricsCacheLookup | None:
    def restore_payload(payload):
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return None
        if payload.get("importer_version") != IMPORTER_VERSION:
            return None
        if payload.get("processing_options") != _options_payload(options):
            return None
        source = payload.get("source")
        tracks = payload.get("tracks")
        if not isinstance(source, dict) or not isinstance(tracks, list):
            return None
        restored = []
        for item in tracks:
            if not isinstance(item, dict) or not isinstance(item.get("fingerprint"), str):
                return None
            metrics = _metrics_from_payload(item.get("metrics"))
            if metrics is None:
                return None
            restored.append(
                {
                    "source_index": int(item.get("source_index", len(restored))),
                    "fingerprint": item["fingerprint"],
                    "metrics": metrics,
                    "display_geometry": _display_geometry_payload(
                        item.get("display_geometry")
                    ),
                }
            )
        return source, tuple(restored)

    payload = None
    path = cache_path(source_path, cache_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    restored_payload = restore_payload(payload)
    try:
        current = source_identity(source_path)
    except OSError:
        return None
    if restored_payload is not None:
        source, restored = restored_payload
        if all(source.get(key) == current.get(key) for key in ("path", "size", "mtime_ns")):
            return MetricsCacheLookup(exact_source=True, tracks=restored)
    try:
        source_digest = _file_sha256(source_path)
    except OSError:
        source_digest = None
    if restored_payload is not None:
        source, restored = restored_payload
        if source_digest and source.get("sha256") == source_digest:
            return MetricsCacheLookup(exact_source=True, tracks=restored)
    if source_digest:
        try:
            content_payload = json.loads(
                content_cache_path(source_digest, cache_dir).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            content_payload = None
        content_restored = restore_payload(content_payload)
        if content_restored is not None:
            source, restored = content_restored
            if source.get("sha256") == source_digest:
                return MetricsCacheLookup(exact_source=True, tracks=restored)
    if restored_payload is None:
        return None
    _source, restored = restored_payload
    return MetricsCacheLookup(exact_source=False, tracks=restored)


def write_metrics_cache(
    source_path: str | Path,
    options: Any,
    tracks: Iterable[dict[str, Any]],
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> Path:
    source = source_identity(source_path)
    source["sha256"] = _file_sha256(source_path)
    items = []
    for fallback_index, track in enumerate(tracks):
        fingerprint = track.get("fingerprint")
        metrics = track.get("metrics")
        if not isinstance(fingerprint, str) or not isinstance(metrics, dict):
            raise ValueError("Every cached track requires a fingerprint and metrics.")
        items.append(
            {
                "source_index": int(track.get("source_index", fallback_index)),
                "fingerprint": fingerprint,
                "metrics": _metrics_payload(metrics),
                "display_geometry": _display_geometry_payload(
                    track.get("display_geometry")
                ),
            }
        )
    destination = cache_path(source_path, cache_dir)
    payload = {
            "version": CACHE_VERSION,
            "importer_version": IMPORTER_VERSION,
            "source": source,
            "processing_options": _options_payload(options),
            "tracks": items,
        }
    atomic_write_json(destination, payload)
    atomic_write_json(content_cache_path(source["sha256"], cache_dir), payload)
    return destination
