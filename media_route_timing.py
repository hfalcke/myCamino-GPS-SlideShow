"""Estimate timestamps for existing GPX route points from nearby media.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


EARTH_RADIUS_M = 6378137.0


@dataclass(frozen=True)
class RoutePoint:
    latitude: float
    longitude: float
    segment_index: int


@dataclass(frozen=True)
class MediaObservation:
    path: Path
    timestamp: datetime
    latitude: float
    longitude: float


@dataclass(frozen=True)
class MediaRouteAnchor:
    observation: MediaObservation
    segment_index: int
    segment_fraction: float
    route_distance_m: float
    distance_from_route_m: float


@dataclass(frozen=True)
class MediaTimingProposal:
    point_times: tuple[datetime, ...]
    anchors: tuple[MediaRouteAnchor, ...]
    ignored: tuple[tuple[MediaObservation, str], ...]
    fallback_speed_kmh: float
    extrapolation_speed_kmh: float


@dataclass(frozen=True)
class _Vertex:
    point_index: int
    segment_index: int
    x: float
    y: float
    route_distance_m: float
    latitude: float


@dataclass(frozen=True)
class _Edge:
    segment_index: int
    start: _Vertex
    end: _Vertex
    length_m: float


@dataclass(frozen=True)
class _MatchNode:
    count: int
    total_distance_m: float
    anchor: MediaRouteAnchor
    previous: int | None


def _mercator(latitude: float, longitude: float) -> tuple[float, float]:
    limited = max(-85.05112878, min(85.05112878, float(latitude)))
    return (
        EARTH_RADIUS_M * math.radians(float(longitude)),
        EARTH_RADIUS_M
        * math.log(math.tan(math.pi / 4.0 + math.radians(limited) / 2.0)),
    )


def _ground_distance(x1: float, y1: float, x2: float, y2: float, latitude: float) -> float:
    return math.hypot(x2 - x1, y2 - y1) * max(0.1, math.cos(math.radians(latitude)))


def _route_geometry(points: list[RoutePoint]) -> tuple[list[_Vertex], list[_Edge]]:
    vertices: list[_Vertex] = []
    edges: list[_Edge] = []
    route_distance = 0.0
    previous: _Vertex | None = None
    for point_index, point in enumerate(points):
        x_coord, y_coord = _mercator(point.latitude, point.longitude)
        if previous is not None and previous.segment_index == point.segment_index:
            increment = _ground_distance(
                previous.x,
                previous.y,
                x_coord,
                y_coord,
                (previous.latitude + point.latitude) / 2.0,
            )
            route_distance += increment
        vertex = _Vertex(
            point_index,
            int(point.segment_index),
            x_coord,
            y_coord,
            route_distance,
            float(point.latitude),
        )
        vertices.append(vertex)
        if previous is not None and previous.segment_index == vertex.segment_index:
            edges.append(
                _Edge(
                    vertex.segment_index,
                    previous,
                    vertex,
                    route_distance - previous.route_distance_m,
                )
            )
        previous = vertex
    return vertices, edges


def _edge_grid(edges: list[_Edge], maximum_distance_m: float):
    cell_size = max(50.0, maximum_distance_m * 2.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for index, edge in enumerate(edges):
        latitude = (edge.start.latitude + edge.end.latitude) / 2.0
        radius = maximum_distance_m / max(0.1, math.cos(math.radians(latitude)))
        minimum_x = min(edge.start.x, edge.end.x) - radius
        maximum_x = max(edge.start.x, edge.end.x) + radius
        minimum_y = min(edge.start.y, edge.end.y) - radius
        maximum_y = max(edge.start.y, edge.end.y) + radius
        for x_cell in range(math.floor(minimum_x / cell_size), math.floor(maximum_x / cell_size) + 1):
            for y_cell in range(math.floor(minimum_y / cell_size), math.floor(maximum_y / cell_size) + 1):
                grid.setdefault((x_cell, y_cell), []).append(index)
    return cell_size, grid


def _projection_candidates(
    observation: MediaObservation,
    edges: list[_Edge],
    cell_size: float,
    grid: dict[tuple[int, int], list[int]],
    maximum_distance_m: float,
) -> list[MediaRouteAnchor]:
    x_coord, y_coord = _mercator(observation.latitude, observation.longitude)
    edge_indexes = grid.get(
        (math.floor(x_coord / cell_size), math.floor(y_coord / cell_size)), ()
    )
    candidates: list[MediaRouteAnchor] = []
    for edge_index in edge_indexes:
        edge = edges[edge_index]
        delta_x = edge.end.x - edge.start.x
        delta_y = edge.end.y - edge.start.y
        denominator = delta_x * delta_x + delta_y * delta_y
        fraction = 0.0 if denominator <= 0 else (
            ((x_coord - edge.start.x) * delta_x + (y_coord - edge.start.y) * delta_y)
            / denominator
        )
        fraction = max(0.0, min(1.0, fraction))
        projected_x = edge.start.x + delta_x * fraction
        projected_y = edge.start.y + delta_y * fraction
        distance = _ground_distance(
            x_coord,
            y_coord,
            projected_x,
            projected_y,
            observation.latitude,
        )
        if distance <= maximum_distance_m + 1e-6:
            candidates.append(
                MediaRouteAnchor(
                    observation,
                    edge.segment_index,
                    fraction,
                    edge.start.route_distance_m + edge.length_m * fraction,
                    distance,
                )
            )
    candidates.sort(key=lambda item: (item.route_distance_m, item.distance_from_route_m))
    deduplicated: list[MediaRouteAnchor] = []
    for candidate in candidates:
        if deduplicated and abs(candidate.route_distance_m - deduplicated[-1].route_distance_m) < 0.01:
            if candidate.distance_from_route_m < deduplicated[-1].distance_from_route_m:
                deduplicated[-1] = candidate
        else:
            deduplicated.append(candidate)
    return deduplicated


def _better(nodes: list[_MatchNode], first: int | None, second: int | None) -> int | None:
    if first is None:
        return second
    if second is None:
        return first
    first_node = nodes[first]
    second_node = nodes[second]
    first_score = (first_node.count, -first_node.total_distance_m)
    second_score = (second_node.count, -second_node.total_distance_m)
    return first if first_score >= second_score else second


def _monotonic_anchors(
    observations: list[MediaObservation],
    candidates: list[list[MediaRouteAnchor]],
) -> list[MediaRouteAnchor]:
    positions = sorted(
        {candidate.route_distance_m for members in candidates for candidate in members}
    )
    if not positions:
        return []
    tree: list[int | None] = [None] * (len(positions) + 1)
    nodes: list[_MatchNode] = []

    def query(position: float) -> int | None:
        index = bisect.bisect_right(positions, position)
        result = None
        while index > 0:
            result = _better(nodes, result, tree[index])
            index -= index & -index
        return result

    def update(position: float, node_index: int) -> None:
        index = bisect.bisect_left(positions, position) + 1
        while index < len(tree):
            tree[index] = _better(nodes, tree[index], node_index)
            index += index & -index

    grouped: dict[float, list[int]] = {}
    for index, observation in enumerate(observations):
        grouped.setdefault(observation.timestamp.timestamp(), []).append(index)
    for timestamp in sorted(grouped):
        pending: list[tuple[float, int]] = []
        for observation_index in grouped[timestamp]:
            for candidate in candidates[observation_index]:
                previous = query(candidate.route_distance_m)
                nodes.append(
                    _MatchNode(
                        1 if previous is None else nodes[previous].count + 1,
                        candidate.distance_from_route_m
                        if previous is None
                        else nodes[previous].total_distance_m + candidate.distance_from_route_m,
                        candidate,
                        previous,
                    )
                )
                pending.append((candidate.route_distance_m, len(nodes) - 1))
        for position, node_index in pending:
            update(position, node_index)
    best = query(positions[-1])
    selected: list[MediaRouteAnchor] = []
    while best is not None:
        selected.append(nodes[best].anchor)
        best = nodes[best].previous
    selected.reverse()
    return selected


def _timeline(
    distances: list[float],
    anchors: list[MediaRouteAnchor],
    fallback_speed_kmh: float,
) -> tuple[list[datetime], float]:
    groups: list[tuple[float, datetime, datetime]] = []
    for anchor in anchors:
        timestamp = anchor.observation.timestamp
        if groups and abs(anchor.route_distance_m - groups[-1][0]) < 0.01:
            distance, arrival, departure = groups[-1]
            groups[-1] = (distance, min(arrival, timestamp), max(departure, timestamp))
        else:
            groups.append((anchor.route_distance_m, timestamp, timestamp))
    if len(groups) >= 2:
        span_km = (groups[-1][0] - groups[0][0]) / 1000.0
        elapsed_hours = (groups[-1][2] - groups[0][1]).total_seconds() / 3600.0
        speed = span_km / elapsed_hours if span_km > 0 and elapsed_hours > 0 else fallback_speed_kmh
    else:
        speed = fallback_speed_kmh
    speed = max(0.01, float(speed))
    metres_per_second = speed * 1000.0 / 3600.0

    def time_at(distance: float) -> datetime:
        if distance < groups[0][0]:
            return groups[0][1] - timedelta(seconds=(groups[0][0] - distance) / metres_per_second)
        if distance > groups[-1][0]:
            return groups[-1][2] + timedelta(seconds=(distance - groups[-1][0]) / metres_per_second)
        for index, group in enumerate(groups):
            if abs(distance - group[0]) < 0.01:
                return group[1]
            if index + 1 >= len(groups):
                break
            following = groups[index + 1]
            if group[0] < distance < following[0]:
                fraction = (distance - group[0]) / (following[0] - group[0])
                return group[2] + (following[1] - group[2]) * fraction
        return groups[-1][2]

    result: list[datetime] = []
    for distance in distances:
        proposed = time_at(distance)
        if result and proposed <= result[-1]:
            proposed = result[-1] + timedelta(microseconds=1)
        result.append(proposed)
    return result, speed


def propose_route_timing(
    route_points: Iterable[RoutePoint],
    media: Iterable[MediaObservation],
    *,
    maximum_distance_m: float = 100.0,
    fallback_speed_kmh: float = 3.5,
) -> MediaTimingProposal:
    """Return a geometry-preserving timestamp proposal for an untimed route."""
    points = list(route_points)
    observations = sorted(
        list(media), key=lambda item: (item.timestamp.timestamp(), str(item.path).casefold())
    )
    maximum_distance_m = max(1.0, float(maximum_distance_m))
    fallback_speed_kmh = max(0.01, float(fallback_speed_kmh))
    vertices, edges = _route_geometry(points)
    if not vertices or not observations or not edges:
        return MediaTimingProposal((), (), tuple((item, "no route match") for item in observations), fallback_speed_kmh, fallback_speed_kmh)
    cell_size, grid = _edge_grid(edges, maximum_distance_m)
    candidates = [
        _projection_candidates(item, edges, cell_size, grid, maximum_distance_m)
        for item in observations
    ]
    anchors = _monotonic_anchors(observations, candidates)
    accepted = {id(anchor.observation) for anchor in anchors}
    ignored = []
    for observation, members in zip(observations, candidates):
        if id(observation) in accepted:
            continue
        ignored.append(
            (observation, "farther than configured distance" if not members else "inconsistent route order")
        )
    if not anchors:
        return MediaTimingProposal((), (), tuple(ignored), fallback_speed_kmh, fallback_speed_kmh)
    point_times, speed = _timeline(
        [vertex.route_distance_m for vertex in vertices], anchors, fallback_speed_kmh
    )
    return MediaTimingProposal(
        tuple(point_times), tuple(anchors), tuple(ignored), fallback_speed_kmh, speed
    )
