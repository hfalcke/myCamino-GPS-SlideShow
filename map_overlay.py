#!/usr/bin/env python3
"""Renderer-independent map overlay descriptions shared by maps and players."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


MAP_CONTENT_VERSION = 6
DYNAMIC_OVERLAY_MIN_VERSION = 3
GPX_OVERLAY_MODES = frozenset({"line", "hidden"})
MEDIA_OVERLAY_MODES = frozenset({"dots", "interpolated", "hidden"})


@dataclass(frozen=True)
class OverlayPoint:
    latitude: float
    longitude: float
    time_iso: Optional[str] = None
    source_name: Optional[str] = None


@dataclass(frozen=True)
class MapOverlayScene:
    """Geographic overlay content independent of AppKit and Matplotlib."""

    stage_kind: str
    mode: str
    segments: tuple[tuple[OverlayPoint, ...], ...]
    header_lines: tuple[str, ...] = ()

    @property
    def points(self) -> tuple[OverlayPoint, ...]:
        return tuple(point for segment in self.segments for point in segment)


def map_uses_dynamic_overlays(metadata: object) -> bool:
    """Return whether a map image intentionally omits route and header layers."""
    if not isinstance(metadata, dict):
        return False
    try:
        version = int(metadata.get("map_content_version", 0))
    except (TypeError, ValueError):
        return False
    return version >= DYNAMIC_OVERLAY_MIN_VERSION and bool(metadata.get("background_only"))


def overlay_stage_kind(metadata: object) -> str:
    """Return the normalized stage kind encoded by map metadata."""
    if not isinstance(metadata, dict):
        return "gpx_track"
    value = str(metadata.get("stage_kind") or "").strip().lower()
    if value in {"gpx_track", "media_stage", "overview"}:
        return value
    if str(metadata.get("map_kind") or "").strip().lower() == "media":
        return "media_stage"
    if isinstance(metadata.get("tracks"), list) and not metadata.get("track_number"):
        return "overview"
    return "gpx_track"


def normalize_overlay_mode(stage_kind: str, mode: object) -> str:
    """Validate an overlay mode and return a safe default for the stage kind."""
    normalized = str(mode or "").strip().lower().replace("-", "_")
    if stage_kind == "media_stage":
        if normalized == "line":
            normalized = "interpolated"
        return normalized if normalized in MEDIA_OVERLAY_MODES else "dots"
    return normalized if normalized in GPX_OVERLAY_MODES else "line"


def _point_from_value(value: object) -> Optional[OverlayPoint]:
    if isinstance(value, dict):
        latitude = value.get("lat", value.get("latitude"))
        longitude = value.get("lon", value.get("longitude"))
        time_iso = value.get("time_iso", value.get("time"))
        source_name = value.get("source_name", value.get("filename"))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        latitude, longitude = value[0], value[1]
        time_iso = value[2] if len(value) >= 3 else None
        source_name = value[3] if len(value) >= 4 else None
    else:
        return None
    try:
        return OverlayPoint(
            float(latitude),
            float(longitude),
            str(time_iso) if time_iso not in {None, ""} else None,
            str(source_name) if source_name not in {None, ""} else None,
        )
    except (TypeError, ValueError):
        return None


def normalize_overlay_segments(values: object) -> tuple[tuple[OverlayPoint, ...], ...]:
    """Normalize a JSON-friendly point or segment collection."""
    if not isinstance(values, (list, tuple)):
        return ()
    if values and _point_from_value(values[0]) is not None:
        segment = tuple(point for item in values if (point := _point_from_value(item)) is not None)
        return (segment,) if segment else ()
    segments = []
    for raw_segment in values:
        if not isinstance(raw_segment, (list, tuple)):
            continue
        segment = tuple(point for item in raw_segment if (point := _point_from_value(item)) is not None)
        if segment:
            segments.append(segment)
    return tuple(segments)


def _metadata_segments(metadata: dict[str, Any], stage_kind: str) -> tuple[tuple[OverlayPoint, ...], ...]:
    geometry = metadata.get("overlay_geometry")
    if isinstance(geometry, dict):
        segments = normalize_overlay_segments(geometry.get("segments"))
        if segments:
            return segments
        points = normalize_overlay_segments(geometry.get("points"))
        if points:
            return points
    if stage_kind == "media_stage":
        return normalize_overlay_segments(metadata.get("media_points"))
    if stage_kind == "overview":
        result = []
        for track in metadata.get("tracks", []):
            if not isinstance(track, dict):
                continue
            segments = normalize_overlay_segments(track.get("track_segments"))
            if not segments:
                segments = normalize_overlay_segments(track.get("track_points"))
            result.extend(segments)
        return tuple(result)
    segments = normalize_overlay_segments(metadata.get("track_segments"))
    if segments:
        return segments
    return normalize_overlay_segments(metadata.get("track_points"))


def map_header_lines(metadata: object) -> tuple[str, ...]:
    """Return stored dynamic header lines without reconstructing domain data."""
    if not isinstance(metadata, dict):
        return ()
    value = metadata.get("header_lines")
    if isinstance(value, (list, tuple)):
        lines = tuple(str(line).strip() for line in value if str(line).strip())
        if lines:
            return lines
    stage_kind = overlay_stage_kind(metadata)
    if stage_kind == "overview":
        header = str(metadata.get("header") or "").strip()
        return (header,) if header else ()
    if stage_kind == "media_stage":
        media_date = str(metadata.get("media_map_date") or "").strip()
        return (media_date,) if media_date else ()
    title = str(metadata.get("track_name") or "").strip()
    subtitle = str(metadata.get("track_date") or "").strip()
    return tuple(line for line in (title, subtitle) if line)


def scene_from_metadata(
    metadata: object,
    *,
    gpx_mode: object = "line",
    media_mode: object = "dots",
    show_header: bool = True,
) -> MapOverlayScene:
    """Build the common overlay scene encoded by one map sidecar."""
    payload = metadata if isinstance(metadata, dict) else {}
    stage_kind = overlay_stage_kind(payload)
    mode = normalize_overlay_mode(stage_kind, media_mode if stage_kind == "media_stage" else gpx_mode)
    return MapOverlayScene(
        stage_kind=stage_kind,
        mode=mode,
        segments=_metadata_segments(payload, stage_kind),
        header_lines=map_header_lines(payload) if show_header else (),
    )


def placement_obstacle_points(metadata: object, route_points: object = None) -> tuple[OverlayPoint, ...]:
    """Return every route or media point that framed time-lapse media must avoid."""
    route_segments = normalize_overlay_segments(route_points)
    route = tuple(point for segment in route_segments for point in segment)
    if route:
        return route
    scene = scene_from_metadata(metadata, show_header=False)
    return scene.points


def json_overlay_geometry(
    segments: Iterable[Iterable[object]],
    *,
    geometry_kind: str,
    estimated: bool = False,
) -> dict[str, Any]:
    """Return a compact JSON overlay geometry payload for a map sidecar."""
    normalized = normalize_overlay_segments(list(segments))
    return {
        "kind": str(geometry_kind),
        "estimated": bool(estimated),
        "segments": [
            [
                {
                    "lat": point.latitude,
                    "lon": point.longitude,
                    **({"time_iso": point.time_iso} if point.time_iso else {}),
                    **({"source_name": point.source_name} if point.source_name else {}),
                }
                for point in segment
            ]
            for segment in normalized
        ],
    }
