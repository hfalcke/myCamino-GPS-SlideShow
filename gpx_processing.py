#!/usr/bin/env python3
"""Shared segment-aware GPX geometry and quality processing."""

from __future__ import annotations

import math
import hashlib
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Iterable


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
GPX_NS = {"gpx": GPX_NAMESPACE}
EARTH_RADIUS_KM = 6371.0088
DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M = 500.0
DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH = 1.5
TRACK_FINGERPRINT_VERSION = 2

HORIZONTAL_ACCURACY_NAMES = {
    "accuracy",
    "horizontalaccuracy",
    "horizontal_accuracy",
    "horizontal-accuracy",
    "hacc",
    "eph",
}
VERTICAL_ACCURACY_NAMES = {
    "verticalaccuracy",
    "vertical_accuracy",
    "vertical-accuracy",
    "vacc",
    "epv",
}
PROCESSING_CACHE_MAX_ENTRIES = 64
PROCESSING_CACHE_MAX_POINTS = 250_000
_PROCESSING_CACHE: OrderedDict[tuple, "ProcessedTrack"] = OrderedDict()
_PROCESSING_CACHE_POINTS = 0
_PROCESSING_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class ProcessingOptions:
    horizontal_smoothing_distance_m: float = 10.0
    minimum_point_spacing_m: float = 10.0
    elevation_smoothing_distance_m: float = 50.0
    maximum_horizontal_accuracy_m: float = 10.0
    maximum_vertical_accuracy_m: float = 20.0
    maximum_hdop: float = 20.0
    maximum_vdop: float = 20.0
    running_speed_window_distance_m: float = DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M
    stationary_speed_threshold_kmh: float = DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH

    def normalized(self) -> "ProcessingOptions":
        return ProcessingOptions(
            horizontal_smoothing_distance_m=max(0.0, float(self.horizontal_smoothing_distance_m)),
            minimum_point_spacing_m=max(0.0, float(self.minimum_point_spacing_m)),
            elevation_smoothing_distance_m=max(0.0, float(self.elevation_smoothing_distance_m)),
            maximum_horizontal_accuracy_m=max(0.0, float(self.maximum_horizontal_accuracy_m)),
            maximum_vertical_accuracy_m=max(0.0, float(self.maximum_vertical_accuracy_m)),
            maximum_hdop=max(0.0, float(self.maximum_hdop)),
            maximum_vdop=max(0.0, float(self.maximum_vdop)),
            running_speed_window_distance_m=max(0.0, float(self.running_speed_window_distance_m)),
            stationary_speed_threshold_kmh=max(0.0, float(self.stationary_speed_threshold_kmh)),
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "horizontal_smoothing_distance_m": self.horizontal_smoothing_distance_m,
            "minimum_point_spacing_m": self.minimum_point_spacing_m,
            "elevation_smoothing_distance_m": self.elevation_smoothing_distance_m,
            "maximum_horizontal_accuracy_m": self.maximum_horizontal_accuracy_m,
            "maximum_vertical_accuracy_m": self.maximum_vertical_accuracy_m,
            "maximum_hdop": self.maximum_hdop,
            "maximum_vdop": self.maximum_vdop,
            "running_speed_window_distance_m": self.running_speed_window_distance_m,
            "stationary_speed_threshold_kmh": self.stationary_speed_threshold_kmh,
        }


@dataclass
class RawTrackPoint:
    source_index: int
    segment_index: int
    segment_point_index: int
    lat: float
    lon: float
    elevation_m: float | None = None
    time: datetime | None = None
    horizontal_accuracy_m: float | None = None
    vertical_accuracy_m: float | None = None
    hdop: float | None = None
    vdop: float | None = None
    pdop: float | None = None
    satellites: int | None = None
    fix: str | None = None
    element: ET.Element | None = field(default=None, repr=False, compare=False)
    horizontal_status: str = "pending"
    elevation_status: str = "pending"
    retained: bool = False


@dataclass
class ProcessedPoint:
    source_index: int
    segment_index: int
    segment_point_index: int
    lat: float
    lon: float
    time: datetime | None
    elevation_m: float | None
    cumulative_distance_km: float
    segment_distance_km: float
    horizontal_status: str
    elevation_status: str
    horizontal_accuracy_m: float | None = None
    vertical_accuracy_m: float | None = None
    hdop: float | None = None
    vdop: float | None = None
    pdop: float | None = None
    satellites: int | None = None
    fix: str | None = None
    running_speed_kmh: float | None = None

    def as_record(self) -> dict:
        return {
            "source_index": self.source_index,
            "segment_index": self.segment_index,
            "segment_point_index": self.segment_point_index,
            "lat": self.lat,
            "lon": self.lon,
            "time": self.time,
            "elevation_m": self.elevation_m,
            "cumulative_distance_km": self.cumulative_distance_km,
            "segment_distance_km": self.segment_distance_km,
            "horizontal_status": self.horizontal_status,
            "elevation_status": self.elevation_status,
            "horizontal_accuracy_m": self.horizontal_accuracy_m,
            "vertical_accuracy_m": self.vertical_accuracy_m,
            "hdop": self.hdop,
            "vdop": self.vdop,
            "pdop": self.pdop,
            "satellites": self.satellites,
            "fix": self.fix,
            "running_speed_kmh": self.running_speed_kmh,
        }


@dataclass
class ProcessedSegment:
    segment_index: int
    raw_points: list[RawTrackPoint]
    points: list[ProcessedPoint]
    length_km: float
    ascent_m: float
    descent_m: float


@dataclass
class ProcessedTrack:
    raw_points: list[RawTrackPoint]
    segments: list[ProcessedSegment]
    points: list[ProcessedPoint]
    length_km: float
    ascent_m: float
    descent_m: float
    start_time: datetime | None
    end_time: datetime | None
    duration: timedelta | None
    rejection_counts: dict[str, dict[str, int]]
    options: ProcessingOptions
    moving_average_speed_kmh: float | None = None
    maximum_running_speed_kmh: float | None = None

    @property
    def first_point(self) -> ProcessedPoint | None:
        return self.points[0] if self.points else None

    @property
    def last_point(self) -> ProcessedPoint | None:
        return self.points[-1] if self.points else None

    @property
    def raw_point_count(self) -> int:
        return len(self.raw_points)

    @property
    def retained_point_count(self) -> int:
        return len(self.points)

    def source_distances_km(self) -> dict[int, float]:
        """Map every valid raw point to the best processed route distance."""
        distances: dict[int, float] = {}
        cumulative_start = 0.0
        for segment in self.segments:
            raw = segment.raw_points
            retained = segment.points
            if not raw:
                continue
            if not retained:
                for point in raw:
                    distances[point.source_index] = cumulative_start
                continue
            anchors = {point.source_index: point.segment_distance_km for point in retained}
            anchor_indexes = sorted(anchors)
            for point in raw:
                source_index = point.source_index
                if source_index in anchors:
                    segment_distance = anchors[source_index]
                elif source_index <= anchor_indexes[0]:
                    segment_distance = anchors[anchor_indexes[0]]
                elif source_index >= anchor_indexes[-1]:
                    segment_distance = anchors[anchor_indexes[-1]]
                else:
                    right_position = bisect_right(anchor_indexes, source_index)
                    left_index = anchor_indexes[right_position - 1]
                    right_index = anchor_indexes[right_position]
                    fraction = _raw_distance_fraction(raw, left_index, source_index, right_index)
                    segment_distance = anchors[left_index] + (
                        anchors[right_index] - anchors[left_index]
                    ) * fraction
                distances[source_index] = cumulative_start + segment_distance
            cumulative_start += segment.length_km
        return distances


def local_tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def semantic_track_fingerprint(track_element: ET.Element) -> str:
    """Return a stable content hash independent of XML formatting whitespace."""
    parts = []
    order_only_nodes = {
        id(element)
        for element in track_element.iter()
        if local_tag_name(element.tag) == "mycamino_gpx_editor"
        and set(element.attrib) <= {"order_number"}
        and not (element.text or "").strip()
        and len(element) == 0
    }
    for element in track_element.iter():
        tag = local_tag_name(element.tag)
        if id(element) in order_only_nodes:
            continue
        if (
            tag == "extensions"
            and not element.attrib
            and not (element.text or "").strip()
            and len(element) > 0
            and all(id(child) in order_only_nodes for child in element)
        ):
            continue
        attributes = dict(element.attrib)
        if tag == "mycamino_gpx_editor":
            attributes.pop("order_number", None)
        text = (element.text or "").strip()
        if (
            tag == "mycamino_gpx_editor"
            and not attributes
            and not text
            and len(element) == 0
        ):
            continue
        attrs = "|".join(
            f"{key}={value}" for key, value in sorted(attributes.items())
        )
        parts.append(f"{tag}\t{attrs}\t{text}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _fingerprint_value(value: float | None, precision: int = 9) -> str:
    if value is None:
        return ""
    return f"{float(value):.{precision}f}"


def geometry_fingerprint_from_segments(segments: Iterable[Iterable[object]]) -> str:
    """Hash segment-preserving processed latitude/longitude geometry."""
    digest = hashlib.sha256()
    digest.update(f"track-geometry-v{TRACK_FINGERPRINT_VERSION}\n".encode("ascii"))
    for segment in segments:
        digest.update(b"segment\n")
        for point in segment:
            if isinstance(point, dict):
                latitude = point.get("lat")
                longitude = point.get("lon")
            elif isinstance(point, (tuple, list)) and len(point) >= 2:
                latitude, longitude = point[0], point[1]
            else:
                latitude = getattr(point, "lat", None)
                longitude = getattr(point, "lon", None)
            digest.update(
                f"{_fingerprint_value(latitude)}\t{_fingerprint_value(longitude)}\n".encode(
                    "ascii"
                )
            )
    return digest.hexdigest()


def processed_track_geometry_fingerprint(processed: ProcessedTrack) -> str:
    return geometry_fingerprint_from_segments(
        segment.points for segment in processed.segments
    )


def processed_track_data_fingerprint(processed: ProcessedTrack) -> str:
    """Hash processed geometry, elevation, timing, and segment boundaries."""
    return data_fingerprint_from_segments(segment.points for segment in processed.segments)


def data_fingerprint_from_segments(segments: Iterable[Iterable[object]]) -> str:
    """Hash processed point records from objects, dictionaries, or sequences."""
    digest = hashlib.sha256()
    digest.update(f"track-data-v{TRACK_FINGERPRINT_VERSION}\n".encode("ascii"))
    for segment in segments:
        digest.update(b"segment\n")
        for point in segment:
            if isinstance(point, dict):
                latitude = point.get("lat")
                longitude = point.get("lon")
                elevation = point.get("elevation_m")
                time_value = point.get("time_iso", point.get("time"))
            else:
                latitude = getattr(point, "lat", None)
                longitude = getattr(point, "lon", None)
                elevation = getattr(point, "elevation_m", None)
                time_value = getattr(point, "time", None)
            timestamp = time_value.isoformat() if isinstance(time_value, datetime) else str(time_value or "")
            digest.update(
                (
                    f"{_fingerprint_value(latitude)}\t"
                    f"{_fingerprint_value(longitude)}\t"
                    f"{_fingerprint_value(elevation, 6)}\t{timestamp}\n"
                ).encode("utf-8")
            )
    return digest.hexdigest()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    first_lat = math.radians(lat1)
    second_lat = math.radians(lat2)
    delta_lat = second_lat - first_lat
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lon / 2.0) ** 2
    )
    return EARTH_RADIUS_KM * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def _raw_distance_fraction(
    raw_points: list[RawTrackPoint],
    left_source_index: int,
    source_index: int,
    right_source_index: int,
) -> float:
    positions = {point.source_index: index for index, point in enumerate(raw_points)}
    left = positions[left_source_index]
    current = positions[source_index]
    right = positions[right_source_index]
    distances = [0.0]
    for previous, point in zip(raw_points[left:right], raw_points[left + 1 : right + 1]):
        distances.append(distances[-1] + haversine_km(previous.lat, previous.lon, point.lat, point.lon))
    total = distances[-1]
    if total <= 0.0:
        return (current - left) / max(right - left, 1)
    return distances[current - left] / total


def extract_raw_track_points(track_element: ET.Element) -> list[RawTrackPoint]:
    points: list[RawTrackPoint] = []
    source_index = 0
    for segment_index, segment in enumerate(track_element.findall("gpx:trkseg", GPX_NS)):
        for segment_point_index, element in enumerate(segment.findall("gpx:trkpt", GPX_NS)):
            try:
                lat = float(element.attrib["lat"])
                lon = float(element.attrib["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            elevation = _child_float(element, "ele")
            values = _quality_values(element)
            satellites_value = _child_float(element, "sat")
            fix = element.findtext("gpx:fix", default="", namespaces=GPX_NS).strip() or None
            points.append(
                RawTrackPoint(
                    source_index=source_index,
                    segment_index=segment_index,
                    segment_point_index=segment_point_index,
                    lat=lat,
                    lon=lon,
                    elevation_m=elevation,
                    time=parse_time(element.findtext("gpx:time", default="", namespaces=GPX_NS)),
                    horizontal_accuracy_m=_maximum_named_value(values, HORIZONTAL_ACCURACY_NAMES),
                    vertical_accuracy_m=_maximum_named_value(values, VERTICAL_ACCURACY_NAMES),
                    hdop=_maximum_named_value(values, {"hdop"}),
                    vdop=_maximum_named_value(values, {"vdop"}),
                    pdop=_maximum_named_value(values, {"pdop"}),
                    satellites=int(satellites_value) if satellites_value is not None else None,
                    fix=fix,
                    element=element,
                )
            )
            source_index += 1
    return points


def process_track_element(
    track_element: ET.Element,
    options: ProcessingOptions | None = None,
    fingerprint: str | None = None,
) -> ProcessedTrack:
    normalized = (options or ProcessingOptions()).normalized()
    signature = tuple(normalized.as_dict().items())
    cache_key = (fingerprint or semantic_track_fingerprint(track_element), signature)
    with _PROCESSING_CACHE_LOCK:
        cached = _PROCESSING_CACHE.get(cache_key)
        if cached is not None:
            _PROCESSING_CACHE.move_to_end(cache_key)
            return cached
    processed = process_raw_points(extract_raw_track_points(track_element), normalized)
    for point in processed.raw_points:
        point.element = None
    _cache_processed_track(cache_key, processed)
    return processed


def clear_processing_cache() -> None:
    """Clear the bounded shared cache, primarily for tests and diagnostics."""
    global _PROCESSING_CACHE_POINTS
    with _PROCESSING_CACHE_LOCK:
        _PROCESSING_CACHE.clear()
        _PROCESSING_CACHE_POINTS = 0


def processing_cache_info() -> dict[str, int]:
    with _PROCESSING_CACHE_LOCK:
        return {
            "entries": len(_PROCESSING_CACHE),
            "points": _PROCESSING_CACHE_POINTS,
            "maximum_entries": PROCESSING_CACHE_MAX_ENTRIES,
            "maximum_points": PROCESSING_CACHE_MAX_POINTS,
        }


def _cache_processed_track(cache_key: tuple, processed: ProcessedTrack) -> None:
    global _PROCESSING_CACHE_POINTS
    point_count = processed.raw_point_count
    if point_count > PROCESSING_CACHE_MAX_POINTS:
        return
    with _PROCESSING_CACHE_LOCK:
        replaced = _PROCESSING_CACHE.pop(cache_key, None)
        if replaced is not None:
            _PROCESSING_CACHE_POINTS -= replaced.raw_point_count
        _PROCESSING_CACHE[cache_key] = processed
        _PROCESSING_CACHE_POINTS += point_count
        while (
            len(_PROCESSING_CACHE) > PROCESSING_CACHE_MAX_ENTRIES
            or _PROCESSING_CACHE_POINTS > PROCESSING_CACHE_MAX_POINTS
        ):
            _key, evicted = _PROCESSING_CACHE.popitem(last=False)
            _PROCESSING_CACHE_POINTS -= evicted.raw_point_count


def process_raw_points(
    raw_points: Iterable[RawTrackPoint],
    options: ProcessingOptions | None = None,
) -> ProcessedTrack:
    normalized = (options or ProcessingOptions()).normalized()
    copied = [_copy_raw_point(point) for point in raw_points]
    by_segment: dict[int, list[RawTrackPoint]] = {}
    for point in copied:
        by_segment.setdefault(point.segment_index, []).append(point)

    segments: list[ProcessedSegment] = []
    flat_points: list[ProcessedPoint] = []
    cumulative_km = 0.0
    total_ascent = 0.0
    total_descent = 0.0
    for segment_index in sorted(by_segment):
        segment = _process_segment(by_segment[segment_index], normalized, cumulative_km)
        segments.append(segment)
        flat_points.extend(segment.points)
        cumulative_km += segment.length_km
        total_ascent += segment.ascent_m
        total_descent += segment.descent_m

    moving_distance_km = 0.0
    moving_seconds = 0.0
    maximum_running_speed_kmh = None
    for segment in segments:
        segment_distance, segment_seconds = _assign_running_speeds(segment.points, normalized)
        moving_distance_km += segment_distance
        moving_seconds += segment_seconds
        for point in segment.points:
            if point.running_speed_kmh is not None:
                maximum_running_speed_kmh = max(
                    maximum_running_speed_kmh or 0.0,
                    point.running_speed_kmh,
                )

    times = [point.time for point in copied if point.time is not None]
    start_time = min(times) if times else None
    end_time = max(times) if times else None
    duration = end_time - start_time if start_time is not None and end_time is not None else None
    rejection_counts: dict[str, dict[str, int]] = {"horizontal": {}, "elevation": {}}
    for point in copied:
        horizontal = rejection_counts["horizontal"]
        elevation = rejection_counts["elevation"]
        horizontal[point.horizontal_status] = horizontal.get(point.horizontal_status, 0) + 1
        elevation[point.elevation_status] = elevation.get(point.elevation_status, 0) + 1
    return ProcessedTrack(
        raw_points=copied,
        segments=segments,
        points=flat_points,
        length_km=cumulative_km,
        ascent_m=total_ascent,
        descent_m=total_descent,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
        rejection_counts=rejection_counts,
        options=normalized,
        moving_average_speed_kmh=(
            moving_distance_km / (moving_seconds / 3600.0)
            if moving_seconds > 0.0
            else None
        ),
        maximum_running_speed_kmh=maximum_running_speed_kmh,
    )


def _assign_running_speeds(
    points: list[ProcessedPoint],
    options: ProcessingOptions,
) -> tuple[float, float]:
    """Assign distance-window speeds using only recorded timestamp anchors."""
    if len(points) < 2:
        return 0.0, 0.0
    times: list[datetime | None] = [None] * len(points)
    anchors = [index for index, point in enumerate(points) if point.time is not None]
    for left, right in zip(anchors, anchors[1:]):
        left_time, right_time = points[left].time, points[right].time
        if left_time is None or right_time is None or right_time <= left_time:
            continue
        distance = points[right].segment_distance_km - points[left].segment_distance_km
        if distance <= 0.0:
            continue
        for index in range(left, right + 1):
            fraction = (
                points[index].segment_distance_km - points[left].segment_distance_km
            ) / distance
            times[index] = left_time + (right_time - left_time) * fraction

    intervals = []
    moving_distance_km = 0.0
    moving_seconds = 0.0
    threshold = options.stationary_speed_threshold_kmh
    for index in range(len(points) - 1):
        start_time, end_time = times[index], times[index + 1]
        distance = points[index + 1].segment_distance_km - points[index].segment_distance_km
        if start_time is None or end_time is None or distance <= 0.0:
            intervals.append(None)
            continue
        seconds = (end_time - start_time).total_seconds()
        if seconds <= 0.0:
            intervals.append(None)
            continue
        speed = distance / (seconds / 3600.0)
        intervals.append((distance, seconds, speed))
        if speed >= threshold:
            moving_distance_km += distance
            moving_seconds += seconds

    half_window_km = options.running_speed_window_distance_m / 2000.0
    point_distances = [point.segment_distance_km for point in points]
    all_distance_prefix = [0.0]
    all_seconds_prefix = [0.0]
    moving_distance_prefix = [0.0]
    moving_seconds_prefix = [0.0]
    for interval in intervals:
        valid = interval is not None
        moving = valid and interval[2] >= threshold
        all_distance_prefix.append(all_distance_prefix[-1] + (interval[0] if valid else 0.0))
        all_seconds_prefix.append(all_seconds_prefix[-1] + (interval[1] if valid else 0.0))
        moving_distance_prefix.append(
            moving_distance_prefix[-1] + (interval[0] if moving else 0.0)
        )
        moving_seconds_prefix.append(
            moving_seconds_prefix[-1] + (interval[1] if moving else 0.0)
        )

    def cumulative_interval_values(position: float, moving_only: bool) -> tuple[float, float]:
        """Integrate valid interval distance and time through a route position."""
        if position <= point_distances[0]:
            return 0.0, 0.0
        if position >= point_distances[-1]:
            if moving_only:
                return moving_distance_prefix[-1], moving_seconds_prefix[-1]
            return all_distance_prefix[-1], all_seconds_prefix[-1]
        index = max(0, min(len(intervals) - 1, bisect_right(point_distances, position) - 1))
        distance_prefix = moving_distance_prefix if moving_only else all_distance_prefix
        seconds_prefix = moving_seconds_prefix if moving_only else all_seconds_prefix
        distance = distance_prefix[index]
        seconds = seconds_prefix[index]
        interval = intervals[index]
        if interval is None or (moving_only and interval[2] < threshold):
            return distance, seconds
        overlap = min(position, point_distances[index + 1]) - point_distances[index]
        if overlap <= 0.0 or interval[0] <= 0.0:
            return distance, seconds
        fraction = overlap / interval[0]
        return distance + overlap, seconds + interval[1] * fraction

    def window_values(start: float, end: float, moving_only: bool) -> tuple[float, float]:
        end_distance, end_seconds = cumulative_interval_values(end, moving_only)
        start_distance, start_seconds = cumulative_interval_values(start, moving_only)
        return end_distance - start_distance, end_seconds - start_seconds

    for point_index, point in enumerate(points):
        if times[point_index] is None:
            continue
        if half_window_km <= 0.0:
            candidates = [
                interval
                for interval in (
                    intervals[point_index - 1] if point_index > 0 else None,
                    intervals[point_index] if point_index < len(intervals) else None,
                )
                if interval is not None and interval[2] >= threshold
            ]
            if candidates:
                distance = sum(item[0] for item in candidates)
                seconds = sum(item[1] for item in candidates)
                point.running_speed_kmh = distance / (seconds / 3600.0)
            continue

        center = point.segment_distance_km
        window_start = max(points[0].segment_distance_km, center - half_window_km)
        window_end = min(points[-1].segment_distance_km, center + half_window_km)
        selected_distance, selected_seconds = window_values(window_start, window_end, True)
        if selected_seconds > 0.0:
            point.running_speed_kmh = selected_distance / (selected_seconds / 3600.0)
        else:
            all_distance, all_seconds = window_values(window_start, window_end, False)
            if all_seconds <= 0.0:
                continue
            # A completely stationary local window still receives its endpoint speed.
            point.running_speed_kmh = all_distance / (all_seconds / 3600.0)
    return moving_distance_km, moving_seconds


def elevation_gain_loss(
    samples: Iterable[tuple[float, float]],
    smoothing_distance_m: float = 50.0,
    resample_interval_m: float = 5.0,
) -> tuple[float, float]:
    profile = smoothed_elevation_profile(samples, smoothing_distance_m, resample_interval_m)
    return _sum_elevation_changes([elevation for _distance, elevation in profile])


def smoothed_elevation_profile(
    samples: Iterable[tuple[float, float]],
    smoothing_distance_m: float = 50.0,
    resample_interval_m: float = 5.0,
) -> list[tuple[float, float]]:
    cleaned = _clean_samples(samples)
    if len(cleaned) < 2:
        return cleaned
    smoothing_distance_m = max(0.0, float(smoothing_distance_m))
    if smoothing_distance_m == 0.0:
        return cleaned
    start = cleaned[0][0]
    end = cleaned[-1][0]
    if end <= start:
        return cleaned[:1]
    step = min(max(float(resample_interval_m), 0.5), max(smoothing_distance_m / 10.0, 0.5))
    positions = [start + index * step for index in range(int((end - start) / step) + 1)]
    if positions[-1] < end:
        positions.append(end)
    elevations = _interpolate_samples(cleaned, positions)
    smoothed = _centered_scalar_average(positions, elevations, smoothing_distance_m)
    return list(zip(positions, smoothed))


def _process_segment(
    raw_points: list[RawTrackPoint],
    options: ProcessingOptions,
    cumulative_start_km: float,
) -> ProcessedSegment:
    accepted: list[RawTrackPoint] = []
    for point in raw_points:
        reason = _horizontal_rejection_reason(point, options)
        if reason is None:
            point.horizontal_status = "quality accepted"
            accepted.append(point)
        else:
            point.horizontal_status = reason
            point.elevation_status = "XY rejected"
    if not accepted:
        return ProcessedSegment(raw_points[0].segment_index if raw_points else 0, raw_points, [], 0.0, 0.0, 0.0)

    accepted_distances = _cumulative_distances(accepted)
    smoothed_coordinates = _smooth_coordinates(accepted, accepted_distances, options.horizontal_smoothing_distance_m)
    retained_indexes = _retained_indexes(smoothed_coordinates, options.minimum_point_spacing_m)
    retained_raw = [accepted[index] for index in retained_indexes]
    retained_coordinates = [smoothed_coordinates[index] for index in retained_indexes]
    retained_distances = _coordinate_distances(retained_coordinates)

    retained_index_set = set(retained_indexes)
    for index, point in enumerate(accepted):
        if index in retained_index_set:
            point.retained = True
            point.horizontal_status = "retained"
        else:
            point.horizontal_status = "smoothing only"
            point.elevation_status = "not retained"

    elevation_samples: list[tuple[float, float]] = []
    elevation_statuses: list[str] = []
    for distance_km, point in zip(retained_distances, retained_raw):
        reason = _vertical_rejection_reason(point, options)
        if point.elevation_m is None:
            elevation_statuses.append("missing")
        elif reason is not None:
            elevation_statuses.append(reason)
        else:
            elevation_statuses.append("retained")
            elevation_samples.append((distance_km * 1000.0, point.elevation_m))

    interpolated = _interpolated_elevations(retained_distances, elevation_samples)
    valid_profile_samples = [
        (distance_km * 1000.0, elevation)
        for distance_km, elevation in zip(retained_distances, interpolated)
        if elevation is not None
    ]
    profile = smoothed_elevation_profile(valid_profile_samples, options.elevation_smoothing_distance_m)
    ascent, descent = _sum_elevation_changes([value for _distance, value in profile])
    smoothed_point_elevations = _profile_values_at(profile, [distance * 1000.0 for distance in retained_distances])

    points: list[ProcessedPoint] = []
    segment_index = raw_points[0].segment_index if raw_points else 0
    for raw, coordinate, segment_distance, elevation, elevation_status in zip(
        retained_raw,
        retained_coordinates,
        retained_distances,
        smoothed_point_elevations,
        elevation_statuses,
    ):
        measurement_status = elevation_status
        output_status = elevation_status
        if elevation is not None and elevation_status != "retained":
            output_status = f"interpolated ({elevation_status})"
        raw.elevation_status = measurement_status
        points.append(
            ProcessedPoint(
                source_index=raw.source_index,
                segment_index=raw.segment_index,
                segment_point_index=raw.segment_point_index,
                lat=coordinate[0],
                lon=coordinate[1],
                time=raw.time,
                elevation_m=elevation,
                cumulative_distance_km=cumulative_start_km + segment_distance,
                segment_distance_km=segment_distance,
                horizontal_status=raw.horizontal_status,
                elevation_status=output_status,
                horizontal_accuracy_m=raw.horizontal_accuracy_m,
                vertical_accuracy_m=raw.vertical_accuracy_m,
                hdop=raw.hdop,
                vdop=raw.vdop,
                pdop=raw.pdop,
                satellites=raw.satellites,
                fix=raw.fix,
            )
        )
    return ProcessedSegment(segment_index, raw_points, points, retained_distances[-1] if retained_distances else 0.0, ascent, descent)


def _quality_values(point: ET.Element) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {}
    for candidate in point.iter():
        if candidate.text is None:
            continue
        try:
            value = float(candidate.text.strip())
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        values.setdefault(local_tag_name(candidate.tag), []).append(value)
    return values


def _maximum_named_value(values: dict[str, list[float]], names: set[str]) -> float | None:
    matches = [value for name in names for value in values.get(name, [])]
    return max(matches) if matches else None


def _child_float(element: ET.Element, local_name: str) -> float | None:
    text = element.findtext(f"gpx:{local_name}", default="", namespaces=GPX_NS).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _horizontal_rejection_reason(point: RawTrackPoint, options: ProcessingOptions) -> str | None:
    if _exceeds(point.horizontal_accuracy_m, options.maximum_horizontal_accuracy_m):
        return "H error"
    if _exceeds(point.hdop, options.maximum_hdop):
        return "HDOP"
    return None


def _vertical_rejection_reason(point: RawTrackPoint, options: ProcessingOptions) -> str | None:
    if _exceeds(point.vertical_accuracy_m, options.maximum_vertical_accuracy_m):
        return "V error"
    if _exceeds(point.vdop, options.maximum_vdop):
        return "VDOP"
    return None


def _exceeds(value: float | None, threshold: float) -> bool:
    return value is not None and threshold > 0.0 and value > threshold


def _copy_raw_point(point: RawTrackPoint) -> RawTrackPoint:
    return RawTrackPoint(
        source_index=point.source_index,
        segment_index=point.segment_index,
        segment_point_index=point.segment_point_index,
        lat=point.lat,
        lon=point.lon,
        elevation_m=point.elevation_m,
        time=point.time,
        horizontal_accuracy_m=point.horizontal_accuracy_m,
        vertical_accuracy_m=point.vertical_accuracy_m,
        hdop=point.hdop,
        vdop=point.vdop,
        pdop=point.pdop,
        satellites=point.satellites,
        fix=point.fix,
        element=point.element,
    )


def _cumulative_distances(points: list[RawTrackPoint]) -> list[float]:
    distances = [0.0]
    for previous, current in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_km(previous.lat, previous.lon, current.lat, current.lon))
    return distances


def _coordinate_distances(coordinates: list[tuple[float, float]]) -> list[float]:
    distances = [0.0]
    for previous, current in zip(coordinates, coordinates[1:]):
        distances.append(distances[-1] + haversine_km(previous[0], previous[1], current[0], current[1]))
    return distances


def _smooth_coordinates(
    points: list[RawTrackPoint],
    cumulative_km: list[float],
    smoothing_distance_m: float,
) -> list[tuple[float, float]]:
    if len(points) < 3 or smoothing_distance_m <= 0.0:
        return [(point.lat, point.lon) for point in points]
    positions = [distance * 1000.0 for distance in cumulative_km]
    vectors = [_coordinate_vector(point.lat, point.lon) for point in points]
    prefixes = [[0.0] for _axis in range(3)]
    for vector in vectors:
        for axis in range(3):
            prefixes[axis].append(prefixes[axis][-1] + vector[axis])
    half = smoothing_distance_m / 2.0
    result: list[tuple[float, float]] = []
    for index, position in enumerate(positions):
        if index in {0, len(points) - 1}:
            result.append((points[index].lat, points[index].lon))
            continue
        left = bisect_left(positions, position - half)
        right = bisect_right(positions, position + half)
        count = max(1, right - left)
        vector = tuple((prefixes[axis][right] - prefixes[axis][left]) / count for axis in range(3))
        result.append(_vector_coordinate(vector))
    return result


def _coordinate_vector(lat: float, lon: float) -> tuple[float, float, float]:
    lat_radians = math.radians(lat)
    lon_radians = math.radians(lon)
    return (
        math.cos(lat_radians) * math.cos(lon_radians),
        math.cos(lat_radians) * math.sin(lon_radians),
        math.sin(lat_radians),
    )


def _vector_coordinate(vector: tuple[float, float, float]) -> tuple[float, float]:
    x, y, z = vector
    magnitude = math.sqrt(x * x + y * y + z * z)
    if magnitude == 0.0:
        return 0.0, 0.0
    x /= magnitude
    y /= magnitude
    z /= magnitude
    return math.degrees(math.asin(z)), math.degrees(math.atan2(y, x))


def _retained_indexes(coordinates: list[tuple[float, float]], spacing_m: float) -> list[int]:
    if len(coordinates) <= 2 or spacing_m <= 0.0:
        return list(range(len(coordinates)))
    cumulative_m = [distance * 1000.0 for distance in _coordinate_distances(coordinates)]
    retained = [0]
    for index in range(1, len(coordinates) - 1):
        if cumulative_m[index] - cumulative_m[retained[-1]] >= spacing_m:
            retained.append(index)
    if retained[-1] != len(coordinates) - 1:
        retained.append(len(coordinates) - 1)
    return retained


def _interpolated_elevations(
    retained_distances_km: list[float],
    valid_samples_m: list[tuple[float, float]],
) -> list[float | None]:
    if not valid_samples_m:
        return [None] * len(retained_distances_km)
    positions = [distance for distance, _elevation in valid_samples_m]
    values = [elevation for _distance, elevation in valid_samples_m]
    result: list[float | None] = []
    for distance_km in retained_distances_km:
        distance_m = distance_km * 1000.0
        if distance_m < positions[0] or distance_m > positions[-1]:
            result.append(None)
            continue
        right = bisect_right(positions, distance_m)
        if right == 0:
            result.append(values[0])
        elif right >= len(positions):
            result.append(values[-1])
        else:
            left = right - 1
            span = positions[right] - positions[left]
            fraction = 0.0 if span <= 0.0 else (distance_m - positions[left]) / span
            result.append(values[left] + fraction * (values[right] - values[left]))
    return result


def _clean_samples(samples: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for distance, value in samples:
        distance = float(distance)
        value = float(value)
        if not math.isfinite(distance) or not math.isfinite(value):
            continue
        if result and distance < result[-1][0]:
            continue
        if result and distance == result[-1][0]:
            result[-1] = (distance, value)
        else:
            result.append((distance, value))
    return result


def _interpolate_samples(samples: list[tuple[float, float]], positions: list[float]) -> list[float]:
    result: list[float] = []
    index = 0
    for position in positions:
        while index + 1 < len(samples) and samples[index + 1][0] < position:
            index += 1
        if index + 1 >= len(samples):
            result.append(samples[-1][1])
            continue
        left_distance, left_value = samples[index]
        right_distance, right_value = samples[index + 1]
        span = right_distance - left_distance
        fraction = 0.0 if span <= 0.0 else (position - left_distance) / span
        result.append(left_value + fraction * (right_value - left_value))
    return result


def _centered_scalar_average(positions: list[float], values: list[float], width: float) -> list[float]:
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    half = width / 2.0
    result: list[float] = []
    for position in positions:
        left = bisect_left(positions, position - half)
        right = bisect_right(positions, position + half)
        result.append((prefix[right] - prefix[left]) / max(1, right - left))
    return result


def _sum_elevation_changes(elevations: list[float]) -> tuple[float, float]:
    ascent = 0.0
    descent = 0.0
    for previous, current in zip(elevations, elevations[1:]):
        delta = current - previous
        if delta > 0.0:
            ascent += delta
        else:
            descent -= delta
    return ascent, descent


def _profile_values_at(profile: list[tuple[float, float]], positions: list[float]) -> list[float | None]:
    if not profile:
        return [None] * len(positions)
    profile_positions = [position for position, _value in profile]
    profile_values = [value for _position, value in profile]
    result: list[float | None] = []
    for position in positions:
        if position < profile_positions[0] or position > profile_positions[-1]:
            result.append(None)
            continue
        right = bisect_right(profile_positions, position)
        if right == 0:
            result.append(profile_values[0])
        elif right >= len(profile_positions):
            result.append(profile_values[-1])
        else:
            left = right - 1
            span = profile_positions[right] - profile_positions[left]
            fraction = 0.0 if span <= 0.0 else (position - profile_positions[left]) / span
            result.append(profile_values[left] + fraction * (profile_values[right] - profile_values[left]))
    return result
