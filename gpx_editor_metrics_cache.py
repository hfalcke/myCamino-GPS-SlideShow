#!/usr/bin/env python3
"""Compact persistent metrics cache for GPX Editor startup.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from json_storage import atomic_write_json


CACHE_VERSION = 1
IMPORTER_VERSION = 1
DEFAULT_CACHE_DIR = Path.home() / "Library" / "Caches" / "myCamino" / "gpx-editor-metrics"
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


def match_cached_tracks(
    track_elements: Iterable[Any],
    lookup: MetricsCacheLookup | None,
    fingerprint_callback,
) -> list[tuple[str | None, dict[str, Any] | None]]:
    """Match current tracks without hashing an exact unchanged source."""
    elements = list(track_elements)
    if lookup is None:
        return [(None, None) for _element in elements]
    if lookup.exact_source and len(lookup.tracks) == len(elements):
        by_index = {int(item["source_index"]): item for item in lookup.tracks}
        return [
            (
                by_index[index]["fingerprint"],
                dict(by_index[index]["metrics"]),
            )
            if index in by_index
            else (None, None)
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
            )
        )
    return result


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
    path = cache_path(source_path, cache_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
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
            }
        )
    try:
        current = source_identity(source_path)
    except OSError:
        return None
    return MetricsCacheLookup(exact_source=source == current, tracks=tuple(restored))


def write_metrics_cache(
    source_path: str | Path,
    options: Any,
    tracks: Iterable[dict[str, Any]],
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> Path:
    source = source_identity(source_path)
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
            }
        )
    destination = cache_path(source_path, cache_dir)
    atomic_write_json(
        destination,
        {
            "version": CACHE_VERSION,
            "importer_version": IMPORTER_VERSION,
            "source": source,
            "processing_options": _options_payload(options),
            "tracks": items,
        },
    )
    return destination
