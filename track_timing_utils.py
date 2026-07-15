"""Shared distance-weighted timestamp repair for GPX tracks and playback."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from gpx_processing import haversine_km

DEFAULT_WALKING_SPEED_KMH = 3.5


def repair_timed_points(points: Iterable[dict], fallback_speed_kmh: float = DEFAULT_WALKING_SPEED_KMH) -> list[dict]:
    """Return ordered points with monotonic timestamps estimated by distance.

    Existing usable timestamps are retained. Missing runs between anchors are
    distance-interpolated; leading/trailing runs use overall track speed where
    possible and otherwise the configured walking-speed fallback.
    """
    result = []
    for point in points:
        try:
            lat, lon = float(point["lat"]), float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            elevation_m = float(point.get("elevation_m", point.get("elevation")))
        except (TypeError, ValueError):
            elevation_m = None
        point_time = point.get("time")
        try:
            cumulative_distance_km = float(point.get("cumulative_distance_km"))
        except (TypeError, ValueError):
            cumulative_distance_km = None
        result.append(
            {
                "lat": lat,
                "lon": lon,
                "time": point_time if isinstance(point_time, datetime) else None,
                "estimated": False,
                "elevation_m": elevation_m,
                "segment_index": int(point.get("segment_index", 0) or 0),
                "cumulative_distance_km": cumulative_distance_km,
            }
        )
    if not result:
        return result

    # A timestamp that runs backwards is not usable for a time-ordered route.
    # Preserve all valid source timestamps and estimate only invalid entries.
    last_valid_time = None
    for point in result:
        point_time = point["time"]
        if point_time is not None and last_valid_time is not None and point_time <= last_valid_time:
            point["time"] = None
            point["estimated"] = True
            continue
        if point_time is not None:
            last_valid_time = point_time

    stored_distances = [point.get("cumulative_distance_km") for point in result]
    stored_valid = all(value is not None and value >= 0 for value in stored_distances)
    stored_monotonic = stored_valid and all(
        current >= previous for previous, current in zip(stored_distances, stored_distances[1:])
    )
    if stored_monotonic:
        origin = stored_distances[0]
        distances = [value - origin for value in stored_distances]
    else:
        distances = [0.0]
        for previous, current in zip(result, result[1:]):
            increment = 0.0
            if current["segment_index"] == previous["segment_index"]:
                increment = haversine_km(previous["lat"], previous["lon"], current["lat"], current["lon"])
            distances.append(distances[-1] + increment)
    for point, cumulative_distance_km in zip(result, distances):
        point["cumulative_distance_km"] = cumulative_distance_km
    anchors = [index for index, point in enumerate(result) if point["time"] is not None]
    if len(anchors) >= 2:
        first, last = anchors[0], anchors[-1]
        elapsed = (result[last]["time"] - result[first]["time"]).total_seconds()
        span = distances[last] - distances[first]
        speed = span / (elapsed / 3600.0) if elapsed > 0 and span > 0 else fallback_speed_kmh
    else:
        speed = fallback_speed_kmh
    speed = max(float(speed or fallback_speed_kmh), 0.01)

    def fill_range(start: int, end: int, start_time: datetime, end_time: datetime | None):
        span = distances[end] - distances[start]
        for index in range(start + 1, end):
            fraction = 0.0 if span <= 0 else (distances[index] - distances[start]) / span
            seconds = (end_time - start_time).total_seconds() * fraction if end_time is not None else (distances[index] - distances[start]) / speed * 3600.0
            result[index]["time"] = start_time + timedelta(seconds=seconds)
            result[index]["estimated"] = True

    if anchors:
        for start, end in zip(anchors, anchors[1:]):
            fill_range(start, end, result[start]["time"], result[end]["time"])
        first = anchors[0]
        for index in range(first - 1, -1, -1):
            km = distances[index + 1] - distances[index]
            result[index]["time"] = result[index + 1]["time"] - timedelta(hours=km / speed)
            result[index]["estimated"] = True
        last = anchors[-1]
        for index in range(last + 1, len(result)):
            km = distances[index] - distances[index - 1]
            result[index]["time"] = result[index - 1]["time"] + timedelta(hours=km / speed)
            result[index]["estimated"] = True
    else:
        base = datetime.now().astimezone()
        for index, point in enumerate(result):
            point["time"] = base + timedelta(hours=distances[index] / speed)
            point["estimated"] = True
    return result


def timed_points_payload(points: Iterable[dict], fallback_speed_kmh: float = DEFAULT_WALKING_SPEED_KMH) -> list[dict]:
    """Serialize repaired points for plot sidecars without changing GPX data."""
    payload = []
    for point in repair_timed_points(points, fallback_speed_kmh):
        if point.get("time") is None:
            continue
        payload.append(
            {
                "lat": point["lat"],
                "lon": point["lon"],
                "time_iso": point["time"].isoformat(),
                "estimated": point["estimated"],
                "elevation_m": point.get("elevation_m"),
                "cumulative_distance_km": round(float(point.get("cumulative_distance_km", 0.0)), 6),
                "segment_index": int(point.get("segment_index", 0) or 0),
            }
        )
    return payload


def timestamps_from_start(points: Iterable[dict], start_time: datetime, end_time: datetime | None = None, fallback_speed_kmh: float = DEFAULT_WALKING_SPEED_KMH) -> list[datetime]:
    """Assign distance-weighted times from a supplied first-point timestamp."""
    normalized = [
        {
            "lat": point["lat"],
            "lon": point["lon"],
            "time": None,
            "segment_index": point.get("segment_index", 0),
            "cumulative_distance_km": point.get("cumulative_distance_km"),
        }
        for point in points
    ]
    if not normalized:
        return []
    normalized[0]["time"] = start_time
    if end_time is not None and end_time > start_time:
        normalized[-1]["time"] = end_time
    repaired = repair_timed_points(normalized, fallback_speed_kmh)
    return [point["time"] for point in repaired]
