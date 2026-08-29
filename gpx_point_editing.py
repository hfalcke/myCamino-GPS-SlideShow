"""Shared XML helpers for inserting, copying, and moving GPX track points.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from gpx_processing import parse_time


EARTH_RADIUS_M = 6378137.0


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace_of(element: ET.Element) -> str:
    if element.tag.startswith("{"):
        return element.tag[1:].split("}", 1)[0]
    return ""


def qualified_like(element: ET.Element, name: str) -> str:
    namespace = namespace_of(element)
    return f"{{{namespace}}}{name}" if namespace else name


def track_segments(track: ET.Element) -> list[ET.Element]:
    return [element for element in list(track) if local_name(element.tag) == "trkseg"]


def segment_points(segment: ET.Element) -> list[ET.Element]:
    return [element for element in list(segment) if local_name(element.tag) == "trkpt"]


def point_locations(track: ET.Element) -> list[tuple[ET.Element, ET.Element, int]]:
    result = []
    for segment in track_segments(track):
        for index, point in enumerate(segment_points(segment)):
            result.append((point, segment, index))
    return result


def lonlat_to_web_mercator(longitude: float, latitude: float) -> tuple[float, float]:
    limited_lat = max(min(float(latitude), 85.05112878), -85.05112878)
    return (
        EARTH_RADIUS_M * math.radians(float(longitude)),
        EARTH_RADIUS_M
        * math.log(math.tan(math.pi / 4.0 + math.radians(limited_lat) / 2.0)),
    )


def web_mercator_to_lonlat(x_coord: float, y_coord: float) -> tuple[float, float]:
    longitude = math.degrees(float(x_coord) / EARTH_RADIUS_M)
    latitude = math.degrees(
        2.0 * math.atan(math.exp(float(y_coord) / EARTH_RADIUS_M)) - math.pi / 2.0
    )
    return longitude, latitude


def translate_points_web_mercator(
    points: list[ET.Element],
    original_coordinates: list[tuple[float, float]],
    delta_x_m: float,
    delta_y_m: float,
) -> None:
    """Translate points together in Web Mercator while preserving their metadata."""
    if len(points) != len(original_coordinates):
        raise ValueError("Point and coordinate counts do not match.")
    for point, (longitude, latitude) in zip(points, original_coordinates):
        x_coord, y_coord = lonlat_to_web_mercator(longitude, latitude)
        moved_lon, moved_lat = web_mercator_to_lonlat(
            x_coord + float(delta_x_m),
            y_coord + float(delta_y_m),
        )
        point.set("lat", f"{moved_lat:.8f}")
        point.set("lon", f"{moved_lon:.8f}")


def _direct_child(point: ET.Element, name: str) -> ET.Element | None:
    return next(
        (child for child in list(point) if local_name(child.tag) == name),
        None,
    )


def _number_child(point: ET.Element, name: str) -> float | None:
    child = _direct_child(point, name)
    try:
        return float(child.text) if child is not None and child.text is not None else None
    except (TypeError, ValueError):
        return None


def _time_child(point: ET.Element) -> datetime | None:
    child = _direct_child(point, "time")
    return parse_time(child.text) if child is not None and child.text else None


def _format_time(value: datetime) -> str:
    text = value.isoformat()
    return text.replace("+00:00", "Z")


def synthesized_point(
    first: ET.Element,
    second: ET.Element,
    fraction: float,
) -> ET.Element:
    """Create a point by interpolation or extrapolation in Web Mercator space."""
    first_lat = float(first.attrib["lat"])
    first_lon = float(first.attrib["lon"])
    second_lat = float(second.attrib["lat"])
    second_lon = float(second.attrib["lon"])
    first_x, first_y = lonlat_to_web_mercator(first_lon, first_lat)
    second_x, second_y = lonlat_to_web_mercator(second_lon, second_lat)
    fraction = float(fraction)
    longitude, latitude = web_mercator_to_lonlat(
        first_x + (second_x - first_x) * fraction,
        first_y + (second_y - first_y) * fraction,
    )
    point = ET.Element(
        qualified_like(first, "trkpt"),
        {"lat": f"{latitude:.8f}", "lon": f"{longitude:.8f}"},
    )

    first_ele = _number_child(first, "ele")
    second_ele = _number_child(second, "ele")
    if first_ele is not None and second_ele is not None:
        ET.SubElement(point, qualified_like(first, "ele")).text = (
            f"{first_ele + (second_ele - first_ele) * fraction:.3f}"
        )

    first_time = _time_child(first)
    second_time = _time_child(second)
    if first_time is not None and second_time is not None:
        delta = second_time - first_time
        ET.SubElement(point, qualified_like(first, "time")).text = _format_time(
            first_time + timedelta(seconds=delta.total_seconds() * fraction)
        )
    return point


def duplicate_point(point: ET.Element) -> ET.Element:
    return copy.deepcopy(point)


def insertion_for_row(
    track: ET.Element,
    row: int,
    *,
    before: bool = False,
) -> tuple[ET.Element, int, ET.Element]:
    """Return segment, insertion index, and smart synthesized point.

    Interior insertion uses the midpoint of the surrounding segment. At the
    beginning or end it extrapolates the first or last pair. A one-point
    segment duplicates its only coordinate without inventing a timestamp.
    """
    locations = point_locations(track)
    if not locations:
        raise ValueError("The track has no points.")
    row = max(0, min(int(row), len(locations) - 1))
    selected, segment, local_index = locations[row]
    points = segment_points(segment)
    insert_index = local_index if before else local_index + 1
    if len(points) == 1:
        return segment, insert_index, duplicate_point(selected)
    if before:
        if local_index > 0:
            return segment, insert_index, synthesized_point(
                points[local_index - 1], points[local_index], 0.5
            )
        return segment, insert_index, synthesized_point(points[0], points[1], -1.0)
    if local_index < len(points) - 1:
        return segment, insert_index, synthesized_point(
            points[local_index], points[local_index + 1], 0.5
        )
    return segment, insert_index, synthesized_point(points[-2], points[-1], 2.0)


def insert_point_for_rows(
    track: ET.Element,
    selected_rows: list[int],
    *,
    before: bool = False,
) -> tuple[ET.Element, int]:
    if not selected_rows:
        locations = point_locations(track)
        if not locations:
            raise ValueError("The track has no points.")
        selected_rows = [len(locations) - 1]
    anchor = min(selected_rows) if before else max(selected_rows)
    segment, index, point = insertion_for_row(track, anchor, before=before)
    segment.insert(index, point)
    new_row = next(
        row for row, (candidate, _segment, _index) in enumerate(point_locations(track))
        if candidate is point
    )
    return point, new_row


def cut_segment_after_row(track: ET.Element, row: int) -> tuple[int, int]:
    """Break the route after a point without creating a second track.

    Returns the zero-based indexes of the old and newly created GPX segments.
    """
    locations = point_locations(track)
    if row < 0 or row >= len(locations) - 1:
        raise ValueError("The cut must leave a waypoint on both sides.")
    _point, segment, local_index = locations[row]
    next_point, next_segment, _next_local_index = locations[row + 1]
    if next_segment is not segment:
        raise ValueError("The track is already cut at this location.")
    points = segment_points(segment)
    if local_index >= len(points) - 1:
        raise ValueError("The track is already cut at this location.")

    segments = track_segments(track)
    segment_index = segments.index(segment)
    new_segment = ET.Element(qualified_like(segment, "trkseg"))
    move_points = points[local_index + 1 :]
    for point in move_points:
        segment.remove(point)
        new_segment.append(point)

    children = list(track)
    insertion_index = children.index(segment) + 1
    track.insert(insertion_index, new_segment)
    if next_point not in segment_points(new_segment):
        raise ValueError("Could not preserve the first point after the cut.")
    return segment_index, segment_index + 1


def joinable_segment_boundary(track: ET.Element, row: int) -> tuple[int, int] | None:
    """Return adjacent segment indexes when row touches an internal boundary."""
    locations = point_locations(track)
    if row < 0 or row >= len(locations):
        return None
    _point, segment, local_index = locations[row]
    segments = track_segments(track)
    segment_index = segments.index(segment)
    points = segment_points(segment)
    if local_index == len(points) - 1 and segment_index + 1 < len(segments):
        return segment_index, segment_index + 1
    if local_index == 0 and segment_index > 0:
        return segment_index - 1, segment_index
    return None


def join_segments_at_row(track: ET.Element, row: int) -> tuple[int, int]:
    """Remove the segment break adjacent to a selected endpoint."""
    boundary = joinable_segment_boundary(track, row)
    if boundary is None:
        raise ValueError("Select an endpoint next to a cut track connection.")
    first_index, second_index = boundary
    segments = track_segments(track)
    first = segments[first_index]
    second = segments[second_index]
    for child in list(second):
        second.remove(child)
        first.append(child)
    track.remove(second)
    return boundary


def serialized_points(points: list[ET.Element]) -> str:
    wrapper = ET.Element("mycamino-points")
    for point in points:
        wrapper.append(copy.deepcopy(point))
    return ET.tostring(wrapper, encoding="unicode")


def deserialize_points(payload: str) -> list[ET.Element]:
    wrapper = ET.fromstring(payload)
    if local_name(wrapper.tag) != "mycamino-points":
        raise ValueError("Clipboard does not contain myCamino GPX points.")
    points = [copy.deepcopy(point) for point in list(wrapper) if local_name(point.tag) == "trkpt"]
    if not points:
        raise ValueError("Clipboard does not contain GPX track points.")
    return points


def insert_points_after_row(
    track: ET.Element,
    row: int,
    points: list[ET.Element],
) -> list[int]:
    locations = point_locations(track)
    if not locations:
        raise ValueError("The track has no insertion segment.")
    row = max(0, min(int(row), len(locations) - 1))
    _selected, segment, local_index = locations[row]
    inserted = [copy.deepcopy(point) for point in points]
    for offset, point in enumerate(inserted, start=1):
        segment.insert(local_index + offset, point)
    identities = {id(point) for point in inserted}
    return [
        index
        for index, (point, _segment, _local_index) in enumerate(point_locations(track))
        if id(point) in identities
    ]


def remove_rows(track: ET.Element, rows: list[int]) -> int:
    locations = point_locations(track)
    targets = {id(locations[row][0]) for row in rows if 0 <= row < len(locations)}
    removed = 0
    for segment in track_segments(track):
        for point in segment_points(segment):
            if id(point) in targets:
                segment.remove(point)
                removed += 1
    return removed


def move_rows(track: ET.Element, rows: list[int], destination_row: int) -> list[int]:
    """Move rows inside one segment while preserving their selected order."""
    locations = point_locations(track)
    selected = [row for row in sorted(set(rows)) if 0 <= row < len(locations)]
    if not selected:
        return []
    segments = {id(locations[row][1]) for row in selected}
    if len(segments) != 1:
        raise ValueError("Points from different GPX segments cannot be reordered together.")
    source_segment = locations[selected[0]][1]
    destination_row = max(0, min(int(destination_row), len(locations)))
    if destination_row == len(locations):
        destination_segment = locations[-1][1]
        destination_index = len(segment_points(destination_segment))
    else:
        destination_segment = locations[destination_row][1]
        destination_index = locations[destination_row][2]
    if destination_segment is not source_segment:
        raise ValueError("Points cannot be dragged across GPX segment boundaries.")
    points = segment_points(source_segment)
    selected_points = [locations[row][0] for row in selected]
    removed_before = sum(points.index(point) < destination_index for point in selected_points)
    for point in selected_points:
        source_segment.remove(point)
    destination_index = max(0, destination_index - removed_before)
    for offset, point in enumerate(selected_points):
        source_segment.insert(destination_index + offset, point)
    identities = {id(point) for point in selected_points}
    return [
        index
        for index, (point, _segment, _local_index) in enumerate(point_locations(track))
        if id(point) in identities
    ]
