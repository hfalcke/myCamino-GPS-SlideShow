"""Read-only checkpoint analysis and enrichment for printable track pages.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import bisect
import heapq
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gpx_processing import haversine_km
from gpx_pdf_summary import check_cancelled


CHECKPOINT_WEATHER_WINDOW_SECONDS = 30 * 60


@dataclass(frozen=True)
class Checkpoint:
    position: float
    latitude: float
    longitude: float
    elevation: float | None
    distance: float
    timestamp: datetime | None
    kind: str = "regular"
    break_end: datetime | None = None
    number: int = 0
    average_speed: float | None = None
    moving_speed: float | None = None
    place: str = ""
    weather: str = ""
    weather_code: int | None = None
    place_detail: str = ""

    @property
    def break_seconds(self):
        return (seconds_between(self.timestamp, self.break_end) or 0.0) if self.break_end else 0.0


def seconds_between(start, end):
    if start is None or end is None:
        return None
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()


def point_checkpoint(point, position, kind="regular", **changes):
    return Checkpoint(position, point.lat, point.lon, point.elevation_m,
                      point.cumulative_distance_km, point.time, kind, **changes)


def _source_positions_linear(processed):
    locations = {}
    offset = 0
    for segment in processed.segments:
        raw, retained = segment.raw_points, segment.points
        if not raw or not retained:
            offset += len(retained)
            continue
        raw_indexes = {point.source_index: index for index, point in enumerate(raw)}
        cumulative = [0.0]
        for a, b in zip(raw, raw[1:]):
            cumulative.append(cumulative[-1] + haversine_km(a.lat, a.lon, b.lat, b.lon))
        right = 0
        for index, point in enumerate(raw):
            while right < len(retained) - 1 and retained[right].source_index < point.source_index:
                right += 1
            if point.source_index <= retained[0].source_index:
                distance = retained[0].cumulative_distance_km
                position = offset
            elif point.source_index >= retained[-1].source_index:
                distance = retained[-1].cumulative_distance_km
                position = offset + len(retained) - 1
            else:
                a, b = retained[right - 1], retained[right]
                left_raw, right_raw = raw_indexes[a.source_index], raw_indexes[b.source_index]
                span = cumulative[right_raw] - cumulative[left_raw]
                fraction = ((cumulative[index] - cumulative[left_raw]) / span if span > 0
                            else (index - left_raw) / (right_raw - left_raw))
                distance = a.cumulative_distance_km + fraction * (b.cumulative_distance_km - a.cumulative_distance_km)
                position = offset + right - 1 + (fraction if b.cumulative_distance_km > a.cumulative_distance_km else 0)
            locations[point.source_index] = distance, position
        offset += len(retained)
    return locations


def break_candidates(processed, radius_m, minimum_seconds):
    """Greedy stationary runs anchored at arrival; every point is visited once."""
    result = []
    offset = 0
    source_locations = None
    for segment in processed.segments:
        if not segment.points:
            continue
        anchor = last = None
        anchor_index = 0
        raw = segment.raw_points
        observed = raw if raw else segment.points

        def finish():
            nonlocal source_locations
            if anchor is not None and last is not None:
                duration = seconds_between(anchor.time, last.time)
                if duration is not None and duration > minimum_seconds:
                    if not raw:
                        marker = point_checkpoint(anchor, anchor_index, "break", break_end=last.time)
                    else:
                        # Smoothing removes many stationary observations. Detect on
                        # accepted raw samples, but locate the marker on canonical geometry.
                        if source_locations is None:
                            source_locations = _source_positions_linear(processed)
                        distance, position = source_locations[anchor.source_index]
                        local_position = position - offset
                        left = min(len(segment.points) - 1, int(local_position))
                        right = min(len(segment.points) - 1, left + 1)
                        a, b = segment.points[left], segment.points[right]
                        part = local_position - left
                        marker = Checkpoint(position,
                            a.lat + part * (b.lat - a.lat), a.lon + part * (b.lon - a.lon),
                            a.elevation_m + part * (b.elevation_m - a.elevation_m)
                            if a.elevation_m is not None and b.elevation_m is not None else None,
                            distance, anchor.time, "break", break_end=last.time)
                    result.append(marker)

        for index, point in enumerate(observed):
            if point.time is None or (raw and point.horizontal_status not in {"retained", "smoothing only", "quality accepted"}):
                finish()
                anchor = last = None
                continue
            advancing = last is None or seconds_between(last.time, point.time) > 0
            nearby = anchor is not None and haversine_km(anchor.lat, anchor.lon, point.lat, point.lon) * 1000 <= radius_m
            if anchor is None or not advancing or not nearby:
                finish()
                anchor, anchor_index = point, offset + index
            last = point
        finish()
        offset += len(segment.points)
    return result


def regular_checkpoints(processed, maximum, interval_seconds):
    points = processed.points
    if not points:
        return [], interval_seconds
    result = [point_checkpoint(points[0], 0, "start")]
    if len(points) == 1:
        return result, interval_seconds
    result.append(point_checkpoint(points[-1], len(points) - 1, "end"))
    timed = [point.time for point in points if point.time is not None]
    if len(timed) < 2:
        return result, interval_seconds
    duration = seconds_between(timed[0], timed[-1])
    if duration is None or duration <= 0:
        return result, interval_seconds
    interval = max(interval_seconds, duration / (maximum - 1))
    start = timed[0].astimezone(timezone.utc)
    targets = [start + timedelta(seconds=step * interval)
               for step in range(1, math.ceil(duration / interval - 1e-10))]
    target_index = 0
    offset = 0
    for segment in processed.segments:
        anchors = [index for index, point in enumerate(segment.points) if point.time is not None]
        distances = [point.cumulative_distance_km for point in segment.points]
        for left, right in zip(anchors, anchors[1:]):
            a, b = segment.points[left], segment.points[right]
            elapsed = seconds_between(a.time, b.time)
            if elapsed is None or elapsed <= 0:
                continue
            while target_index < len(targets) and targets[target_index] < a.time:
                target_index += 1  # Never interpolate across a segment or timing gap.
            while target_index < len(targets) and targets[target_index] <= b.time:
                target = targets[target_index]
                fraction = seconds_between(a.time, target) / elapsed
                distance = a.cumulative_distance_km + fraction * (b.cumulative_distance_km - a.cumulative_distance_km)
                index = min(right, max(left + 1, bisect.bisect_left(distances, distance, left, right + 1)))
                p, q = segment.points[index - 1], segment.points[index]
                span = q.cumulative_distance_km - p.cumulative_distance_km
                part = (distance - p.cumulative_distance_km) / span if span > 0 else fraction
                elevation = (p.elevation_m + part * (q.elevation_m - p.elevation_m)
                             if p.elevation_m is not None and q.elevation_m is not None else None)
                result.append(Checkpoint(offset + index - 1 + part,
                    p.lat + part * (q.lat - p.lat), p.lon + part * (q.lon - p.lon),
                    elevation, distance, target.astimezone(a.time.tzinfo)))
                target_index += 1
        offset += len(segment.points)
    return result, interval


def moving_prefixes(processed):
    """Reuse the canonical distance-weighted timestamp/threshold convention."""
    distance_prefix, time_prefix = [0.0], [0.0]
    threshold = processed.options.stationary_speed_threshold_kmh
    for segment in processed.segments:
        points = segment.points
        moving = [(0.0, 0.0)] * len(points)
        anchors = [index for index, point in enumerate(points) if point.time is not None]
        for left, right in zip(anchors, anchors[1:]):
            a, b = points[left], points[right]
            seconds = seconds_between(a.time, b.time)
            distance = b.segment_distance_km - a.segment_distance_km
            if seconds is None or seconds <= 0 or distance <= 0 or distance * 3600 / seconds < threshold:
                continue
            for index in range(left + 1, right + 1):
                step = max(0, points[index].segment_distance_km - points[index - 1].segment_distance_km)
                moving[index] = step, seconds * step / distance
        for distance, seconds in moving:
            distance_prefix.append(distance_prefix[-1] + distance)
            time_prefix.append(time_prefix[-1] + seconds)
    # Entry k+1 contains the integral up to point k.
    return distance_prefix[1:], time_prefix[1:]


def _prefix_at(prefix, position):
    left = max(0, min(len(prefix) - 1, int(position)))
    right = min(left + 1, len(prefix) - 1)
    return prefix[left] + (position - left) * (prefix[right] - prefix[left])


def build_checkpoints(processed, parameters):
    maximum = max(2, int(parameters.get("pdf.maximum_markers", 10)))
    markers, interval = regular_checkpoints(processed, maximum,
        max(60.0, float(parameters.get("pdf.marker_interval_minutes", 60)) * 60))
    if not markers:
        return ()
    candidates = break_candidates(processed, float(parameters.get("pdf.break_radius_m", 100)),
                                 float(parameters.get("pdf.break_duration_minutes", 20)) * 60)
    # The marker limit is bounded: keep only its longest candidates rather than
    # sorting every pause in a very long track.
    for candidate in heapq.nlargest(maximum, candidates, key=lambda item: (item.break_seconds, -item.position)):
        trial = list(markers)
        same = next((index for index, item in enumerate(trial)
                     if abs(item.position - candidate.position) < 1e-8), None)
        regular = [index for index, item in enumerate(trial) if item.kind == "regular" and not item.break_end]
        nearest = min(regular, key=lambda index: abs(seconds_between(trial[index].timestamp, candidate.timestamp)), default=None)
        if same is not None:
            if trial[same].break_end or trial[same].timestamp is None:
                continue
            trial[same] = replace(trial[same], break_end=candidate.break_end)
        elif nearest is not None and abs(seconds_between(trial[nearest].timestamp, candidate.timestamp)) <= .3 * interval:
            trial[nearest] = candidate
        elif len(trial) < maximum:
            trial.append(candidate)
        elif nearest is not None:
            trial[nearest] = candidate
        else:
            continue
        trial = [item for item in trial if item.kind != "regular" or item.break_end
                 or item.timestamp is None or not (candidate.timestamp <= item.timestamp <= candidate.break_end)]
        if sum(bool(item.break_end) for item in trial) <= len(trial) // 2:
            markers = trial
    markers.sort(key=lambda item: item.position)
    distances, seconds = moving_prefixes(processed)
    result = []
    for number, marker in enumerate(markers, 1):
        average = moving = None
        if result:
            previous = result[-1]
            elapsed = seconds_between(previous.timestamp, marker.timestamp)
            if elapsed is not None and elapsed > 0:
                average = max(0, marker.distance - previous.distance) * 3600 / elapsed
                moving_time = _prefix_at(seconds, marker.position) - _prefix_at(seconds, previous.position)
                if moving_time > 0:
                    moving = (_prefix_at(distances, marker.position) - _prefix_at(distances, previous.position)) * 3600 / moving_time
        result.append(replace(marker, number=number, average_speed=average, moving_speed=moving))
    return tuple(result)


def checkpoint_time(marker, origin=None):
    if marker.timestamp is None:
        return ""
    local = marker.timestamp.astimezone()
    label = local.strftime("%H:%M")
    if origin is not None:
        days = (local.date() - origin.astimezone().date()).days
        if days:
            label += f" ({days:+d}d)"
    return label


def break_duration(seconds):
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes} min"


def weather_text(weather):
    values = weather.get("values", {})
    fields = (("temperature_2m", "{:.1f} °C"), ("wind_speed_10m", "Wind {:.0f} km/h"),
              ("precipitation", "Rain {:.1f} mm"), ("cloud_cover", "Cloud {:.0f}%"),
              ("relative_humidity_2m", "RH {:.0f}%"), ("pressure_msl", "{:.0f} hPa"))
    return " · ".join(fmt.format(float(values[key])) for key, fmt in fields
                      if isinstance(values.get(key), (int, float)) and math.isfinite(values[key]))


def compact_weather_text(text):
    text = re.sub(r"\b(?:RH|Cloud|C)\s*[+-]?\d+(?:\.\d+)?%|\b[+-]?\d+(?:\.\d+)?\s*hPa\b", "", text)
    for before, after in ((" °C", "°"), ("°C", "°"), ("Wind ", ""), ("Rain ", ""),
                          (" km/h", "km/h"), (" mm", "mm")):
        text = text.replace(before, after)
    return " ".join(text.replace("·", " ").split())


def compact_place_name(payload):
    from GetGeoLocations import primary_stage_place_name

    details = payload.get("place_details") or {}
    for value in (details.get("locality") if isinstance(details, dict) else None,
                  payload.get("locality")):
        place = primary_stage_place_name(value) if isinstance(value, str) else None
        if place:
            return place
    place = primary_stage_place_name(payload.get("place")) or ""
    # Legacy formatted labels append the administrative area in parentheses.
    # Do not strip parentheses from a structured, authoritative locality above.
    if place.endswith(")") and " (" in place:
        place = place.rsplit(" (", 1)[0].rstrip()
    if place:
        return place
    for key in ("subLocality", "street", "thoroughfare", "name", "areasOfInterest", "administrativeArea"):
        value = details.get(key) if isinstance(details, dict) else None
        value = value or payload.get(key)
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if isinstance(item, str) and item.strip()), "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def place_extra_detail(payload, place):
    details = payload.get("place_details") or {}
    details = details if isinstance(details, dict) else {}
    for key in ("street", "thoroughfare", "name", "subLocality", "areasOfInterest"):
        value = details.get(key) or payload.get(key)
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if isinstance(item, str) and item.strip()), "")
        if isinstance(value, str) and value.strip() and value.strip().casefold() != place.casefold():
            return value.strip()
    return ""


def coordinate_place(marker):
    return f"{marker.latitude:.4f}°, {marker.longitude:.4f}°"


class PDFMediaIndex:
    """One read-only, spatially indexed snapshot of existing media sidecars."""

    def __init__(self, directories=(), control_file=None, cancel=None, checkpoint_payloads=()):
        from GetGeoLocations import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
        from plot_metadata_utils import media_sidecar_path, validate_media_sidecar
        paths = set()
        if control_file and Path(control_file).is_file():
            control = Path(control_file)
            try:
                lines = control.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                lines = []  # Optional enrichment must not make a valid report fail.
            for line in lines:
                value = line.strip().split("|", 1)[0].strip().strip('"')
                if value and not value.startswith("#"):
                    media = Path(value)
                    paths.add(media_sidecar_path(media if media.is_absolute() else control.parent / media).resolve(strict=False))
        for directory in directories:
            for path in Path(directory).rglob("*.json"):
                check_cancelled(cancel)
                if path.parent.name not in {"trackimages", ".mycamino"} and Path(path.stem).suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                    paths.add(path.resolve(strict=False))
        self.cells = {}
        for path in sorted(paths):
            check_cancelled(cancel)
            try:
                status, payload, _ = validate_media_sidecar(path.with_suffix(""), path)
                if status != "available":
                    continue
                self.add_payload(payload)
            except (OSError, ValueError, TypeError, KeyError):
                continue
        for payload in checkpoint_payloads:
            check_cancelled(cancel)
            self.add_payload(payload)

    def add_payload(self, payload):
        try:
            latitude, longitude = float(payload["latitude"]), float(payload["longitude"])
            if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                return
        except (ValueError, TypeError, KeyError):
            return
        cell = (math.floor(latitude * 1000), math.floor(longitude * 1000))
        self.cells.setdefault(cell, []).append((latitude, longitude, payload))

    def enrich(self, markers, parameters):
        from historical_weather import weather_is_current
        from plot_metadata_utils import parse_photo_datetime
        place_radius = float(parameters.get("pdf.checkpoint_reuse_radius_m", 1000))
        radius = place_radius / 110_000
        result = []
        for marker in markers:
            longitude_radius = min(180, radius / max(.000001, math.cos(math.radians(marker.latitude))))
            candidates = []
            lat_cells = range(math.floor((marker.latitude - radius) * 1000), math.floor((marker.latitude + radius) * 1000) + 1)
            lon_cells = range(math.floor((marker.longitude - longitude_radius) * 1000), math.floor((marker.longitude + longitude_radius) * 1000) + 1)
            wrap = marker.longitude - longitude_radius < -180 or marker.longitude + longitude_radius > 180
            cells = (self.cells.values() if wrap or len(lat_cells) * len(lon_cells) > max(64, len(self.cells) * 4)
                     else (self.cells.get((lat_cell, lon_cell), ()) for lat_cell in lat_cells for lon_cell in lon_cells))
            for cell in cells:
                for latitude, longitude, payload in cell:
                    distance = haversine_km(marker.latitude, marker.longitude, latitude, longitude) * 1000
                    candidates.append((distance, payload))
            place = weather = detail = ""
            code = None
            weather_score = None
            for distance, payload in sorted(candidates, key=lambda item: item[0]):
                if not place and parameters.get("pdf.checkpoint_places", True) and distance <= place_radius:
                    source = payload.get("place_coordinate") or payload
                    try:
                        source_distance = haversine_km(marker.latitude, marker.longitude,
                            float(source["latitude"]), float(source["longitude"])) * 1000
                    except (KeyError, TypeError, ValueError):
                        source_distance = float("inf")
                    if source_distance <= place_radius:
                        place = compact_place_name(payload)
                        detail = place_extra_detail(payload, place) if place else ""
                if parameters.get("pdf.checkpoint_weather", True) and distance <= place_radius and marker.timestamp:
                    query = dict(payload, latitude=marker.latitude, longitude=marker.longitude,
                                 datetime_iso=marker.timestamp.isoformat())
                    stored_weather = payload.get("weather")
                    if not isinstance(stored_weather, dict):
                        continue
                    source_query = stored_weather.get("query")
                    source_query = source_query if isinstance(source_query, dict) else {}
                    try:
                        query_distance = haversine_km(marker.latitude, marker.longitude,
                            float(source_query.get("latitude", payload["latitude"])),
                            float(source_query.get("longitude", payload["longitude"]))) * 1000
                    except (TypeError, ValueError):
                        query_distance = float("inf")
                    if (query_distance <= place_radius and weather_is_current(payload)
                            and weather_is_current(query, distance_m=place_radius,
                                                   time_seconds=CHECKPOINT_WEATHER_WINDOW_SECONDS)):
                        source_time = parse_photo_datetime(stored_weather["media_source"]["datetime_iso"])
                        score = (abs(seconds_between(marker.timestamp, source_time)), distance)
                        if weather_score is not None and score >= weather_score:
                            continue
                        weather_score = score
                        weather = weather_text(payload["weather"])
                        code = None
                        try:
                            code = int(payload["weather"]["values"].get("weather_code"))
                        except (TypeError, ValueError, OverflowError):
                            pass
            if not place and parameters.get("pdf.checkpoint_places", True):
                place = coordinate_place(marker)
            result.append(replace(marker, place=place, place_detail=detail, weather=weather, weather_code=code))
        return tuple(result)
