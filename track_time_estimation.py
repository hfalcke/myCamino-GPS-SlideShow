"""Non-destructive, segment-local GPX timestamp repair proposals.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

from gpx_processing import haversine_km
from media_route_timing import RoutePoint, _route_geometry, deduplicate_media_observations, propose_route_timing


class TimingCancelled(Exception):
    pass


def check_cancelled(event):
    if event is not None and event.is_set():
        raise TimingCancelled("Track-time analysis was cancelled.")


@dataclass(frozen=True)
class TimingPoint:
    latitude: float
    longitude: float
    segment: int
    time: datetime | None = None
    origin: str = ""
    distance_km: float | None = None
    running_speed_kmh: float | None = None


@dataclass(frozen=True)
class MissingTimeRange:
    segment: int
    first: int
    last: int
    preceding: datetime | None
    following: datetime | None

    @property
    def count(self):
        return self.last - self.first + 1


@dataclass(frozen=True)
class TimestampPatch:
    index: int
    time: datetime
    method: str


@dataclass(frozen=True)
class TimingProposal:
    patches: tuple[TimestampPatch, ...]
    unresolved: tuple[tuple[int, str], ...]
    ignored_media: tuple[str, ...] = ()

    def count(self, method):
        return sum(patch.method == method for patch in self.patches)


def missing_time_ranges(points):
    """Keep original zero-based row indexes and never merge segment gaps."""
    result = []
    start = 0
    while start < len(points):
        end = start + 1
        while end < len(points) and points[end].segment == points[start].segment:
            end += 1
        index = start
        previous = None
        while index < end:
            if points[index].time is not None:
                previous = points[index].time
                index += 1
                continue
            first = index
            while index < end and points[index].time is None:
                index += 1
            result.append(MissingTimeRange(points[first].segment, first, index - 1, previous,
                                           points[index].time if index < end else None))
        start = end
    return tuple(result)


def missing_time_ranges_from_element(element):
    from gpx_import import parse_time
    from gpx_processing import GPX_NS

    points = []
    for segment_index, segment in enumerate(element.findall("gpx:trkseg", GPX_NS)):
        for point in segment.findall("gpx:trkpt", GPX_NS):
            points.append(TimingPoint(0, 0, segment_index,
                parse_time(point.findtext("gpx:time", namespaces=GPX_NS))))
    return missing_time_ranges(points)


def _utc(value):
    if value.tzinfo is None:
        raise ValueError("Timestamp anchors must include a timezone.")
    return value.astimezone(timezone.utc)


def _distances(points):
    stored = [point.distance_km for point in points]
    if (all(value is not None and math.isfinite(value) for value in stored)
            and all(right >= left for left, right in zip(stored, stored[1:]))):
        return [float(value - stored[0]) for value in stored]
    distances = [0.0]
    for previous, current in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_km(previous.latitude, previous.longitude,
                                                    current.latitude, current.longitude))
    return distances


def _speed_at_edge(points, distances, known, first, fallback):
    indexes = range(len(points)) if first else range(len(points) - 1, -1, -1)
    for index in indexes:
        speed = points[index].running_speed_kmh
        if speed is not None and math.isfinite(speed) and speed > 0:
            return speed
    pairs = list(zip(known, known[1:]))
    for left, right in pairs if first else reversed(pairs):
        elapsed = (right[1] - left[1]).total_seconds()
        distance = right[0] - left[0]
        if distance > 0 and elapsed > 0:
            return distance / elapsed * 3600
    return max(.01, float(fallback))


def _media_anchors(points, distances, fixed, observations, radius, fallback, cancel):
    check_cancelled(cancel)
    if len(points) == 1:
        nearby = [item for item in observations if haversine_km(points[0].latitude, points[0].longitude,
                   item.latitude, item.longitude) * 1000 <= radius]
        times = {_utc(item.timestamp) for item in nearby}
        if len(times) == 1 and (not fixed or next(iter(times)) == fixed.get(0)):
            return [(0.0, next(iter(times)), "media")], []
        return [], ["An isolated waypoint needs one unambiguous nearby media time or a manual time."]
    route = [RoutePoint(point.latitude, point.longitude, 0) for point in points]
    proposal = propose_route_timing(route, observations, maximum_distance_m=radius,
        fallback_speed_kmh=fallback, fixed_point_times=fixed, allow_reverse=False)
    check_cancelled(cancel)
    vertices, _edges = _route_geometry(route)
    raw_distances = [point.route_distance_m for point in vertices]
    anchors = []
    ignored = [f"{item.path.name}: {reason}" for item, reason in proposal.ignored]
    # Repeated visits on different days cannot identify an unanchored segment.
    if not fixed:
        for left, right in zip(proposal.anchors, proposal.anchors[1:]):
            if (abs(right.route_distance_m - left.route_distance_m) < 1
                    and abs((_utc(right.observation.timestamp) - _utc(left.observation.timestamp)).total_seconds()) > 86400):
                ignored.append("Ambiguous repeat visits without a waypoint time anchor; choose a start time or a specific media set.")
                return [], ignored
    for anchor in proposal.anchors:
        index = max(0, min(len(points) - 2, bisect_right(raw_distances, anchor.route_distance_m) - 1))
        span = raw_distances[index + 1] - raw_distances[index]
        fraction = 0 if span <= 0 else (anchor.route_distance_m - raw_distances[index]) / span
        distance = distances[index] + fraction * (distances[index + 1] - distances[index])
        anchors.append((distance, _utc(anchor.observation.timestamp), "media"))
    return anchors, ignored


def estimate_track_times(points, *, mode="fill", media=(), maximum_distance_m=100,
                         fallback_speed_kmh=3.5, cancel_event=None, manual_start_time=None):
    """Return patches only; existing recorded/manual times are never rewritten."""
    if mode not in {"fill", "refresh"}:
        raise ValueError("Unknown timestamp estimation mode")
    points = tuple(points)
    observations, _duplicates = deduplicate_media_observations(media)
    patches, unresolved, ignored = [], [], []
    start = 0
    while start < len(points):
        check_cancelled(cancel_event)
        end = start + 1
        while end < len(points) and points[end].segment == points[start].segment:
            end += 1
        segment = points[start:end]
        targets = [i for i, point in enumerate(segment)
                   if point.time is None or (mode == "refresh" and point.origin == "generated")]
        if not targets:
            start = end
            continue
        distances = _distances(segment)
        target_set = set(targets)
        fixed = {i: _utc(point.time) for i, point in enumerate(segment)
                 if point.time is not None and i not in target_set}
        if start == 0 and manual_start_time is not None:
            fixed.setdefault(0, _utc(manual_start_time))
        known = [(distances[i], timestamp, "waypoint") for i, timestamp in sorted(fixed.items())]
        if any(right[1] < left[1] or (right[0] > left[0] and right[1] == left[1])
               for left, right in zip(known, known[1:])):
            unresolved.extend((start + i, "Conflicting existing timestamp anchors") for i in targets)
            start = end
            continue
        if observations:
            anchors, rejected = _media_anchors(segment, distances, fixed, observations,
                maximum_distance_m, fallback_speed_kmh, cancel_event)
            ignored.extend(rejected)
            for candidate in anchors:
                position = bisect_right([item[0] for item in known], candidate[0])
                left = known[position - 1] if position else None
                right = known[position] if position < len(known) else None
                if ((left and (candidate[1] < left[1] or (candidate[0] == left[0] and candidate[1] != left[1])))
                        or (right and candidate[1] > right[1])):
                    ignored.append("Media timestamp conflicts with an existing anchor")
                    continue
                known.insert(position, candidate)
        if not known:
            unresolved.extend((start + i, "No absolute time anchor in this segment; enter a start time") for i in targets)
            start = end
            continue
        first_speed = _speed_at_edge(segment, distances, known, True, fallback_speed_kmh)
        last_speed = _speed_at_edge(segment, distances, known, False, fallback_speed_kmh)
        positions = [item[0] for item in known]
        for index in targets:
            check_cancelled(cancel_event)
            distance = distances[index]
            position = bisect_right(positions, distance)
            if distance < positions[0]:
                timestamp = known[0][1] - timedelta(hours=(positions[0] - distance) / first_speed)
                method = "extrapolation"
            elif position == len(known) and distance > positions[-1]:
                timestamp = known[-1][1] + timedelta(hours=(distance - positions[-1]) / last_speed)
                method = "extrapolation"
            else:
                left = known[max(0, position - 1)]
                right = known[min(position, len(known) - 1)]
                span = right[0] - left[0]
                fraction = 0 if span <= 0 else (distance - left[0]) / span
                timestamp = left[1] + (right[1] - left[1]) * fraction
                method = "media" if left[2] == "media" or right[2] == "media" else "interpolation"
            if points[start + index].time != timestamp:
                patches.append(TimestampPatch(start + index, timestamp, method))
        start = end
    return TimingProposal(tuple(patches), tuple(unresolved), tuple(ignored))


def timing_points_from_element(element, processed=None):
    from gpx_processing import extract_raw_track_points
    from gpx_point_editing import point_time_origin

    distances = processed.source_distances_km() if processed is not None else {}
    speeds = processed.running_speeds_by_source_index() if processed is not None else {}
    return tuple(TimingPoint(point.lat, point.lon, point.segment_index, point.time,
        point_time_origin(point.element), distances.get(point.source_index), speeds.get(point.source_index))
        for point in extract_raw_track_points(element))


def timing_observations(prepared):
    """Do not use GPS inferred from track timestamps to estimate those times."""
    from media_route_timing import MediaObservation

    observations, ignored = [], []
    for item in prepared:
        record = item.record
        if record is None or record.photo_datetime is None or record.latitude is None or record.longitude is None:
            ignored.append(f"{item.path.name}: missing capture time or independent GPS")
            continue
        if getattr(record, "gps_source", None) == "track_time_interpolation":
            ignored.append(f"{item.path.name}: GPS was inferred from track time")
            continue
        metadata = getattr(record, "raw_metadata", {}) or {}
        identity = metadata.get("source_file_identity", {})
        observations.append(MediaObservation(item.path, record.photo_datetime, float(record.latitude),
            float(record.longitude), str(identity.get("sha256", "")) if isinstance(identity, dict) else ""))
    return tuple(observations), tuple(ignored)
