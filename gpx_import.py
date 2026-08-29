#!/usr/bin/env python3
"""Normalize common GPX document variants into canonical GPX 1.1 tracks.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from gpx_processing import GPX_NAMESPACE, parse_time


GPX_10_NAMESPACE = "http://www.topografix.com/GPX/1/0"
SUPPORTED_GPX_NAMESPACES = frozenset({GPX_10_NAMESPACE, GPX_NAMESPACE, ""})
GPX_CORE_NAMES = frozenset(
    {
        "gpx",
        "metadata",
        "name",
        "cmt",
        "desc",
        "src",
        "link",
        "url",
        "urlname",
        "number",
        "type",
        "extensions",
        "wpt",
        "rte",
        "rtept",
        "trk",
        "trkseg",
        "trkpt",
        "ele",
        "time",
        "magvar",
        "geoidheight",
        "sym",
        "fix",
        "sat",
        "hdop",
        "vdop",
        "pdop",
        "ageofdgpsdata",
        "dgpsid",
    }
)


def namespace_uri(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def qname(name: str) -> str:
    return f"{{{GPX_NAMESPACE}}}{name}"


def _core_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if (
            local_name(child.tag) == name
            and namespace_uri(child.tag) in SUPPORTED_GPX_NAMESPACES
        ):
            return child
    return None


def _core_children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [
        child
        for child in parent
        if local_name(child.tag) == name
        and namespace_uri(child.tag) in SUPPORTED_GPX_NAMESPACES
    ]


def _normalize_element(element: ET.Element) -> ET.Element:
    cloned = ET.Element(element.tag, dict(element.attrib))
    uri = namespace_uri(element.tag)
    name = local_name(element.tag)
    if uri in SUPPORTED_GPX_NAMESPACES and name in GPX_CORE_NAMES:
        cloned.tag = qname(name)
    cloned.text = element.text
    cloned.tail = element.tail
    for child in element:
        cloned.append(_normalize_element(child))
    return cloned


def _point_as_trackpoint(point: ET.Element) -> ET.Element:
    normalized = _normalize_element(point)
    normalized.tag = qname("trkpt")
    return normalized


def _copy_track_metadata(source: ET.Element, target: ET.Element) -> None:
    for child in source:
        name = local_name(child.tag)
        if name in {"rtept", "trkseg"}:
            continue
        target.append(_normalize_element(child))


def _route_as_track(route: ET.Element, fallback_name: str) -> ET.Element:
    track = ET.Element(qname("trk"))
    _copy_track_metadata(route, track)
    if _core_child(track, "name") is None:
        ET.SubElement(track, qname("name")).text = fallback_name
    segment = ET.SubElement(track, qname("trkseg"))
    for point in _core_children(route, "rtept"):
        segment.append(_point_as_trackpoint(point))
    return track


def _waypoints_as_track(
    waypoints: list[ET.Element],
    fallback_name: str,
) -> ET.Element:
    track = ET.Element(qname("trk"))
    ET.SubElement(track, qname("name")).text = fallback_name
    segment = ET.SubElement(track, qname("trkseg"))
    for point in waypoints:
        segment.append(_point_as_trackpoint(point))
    return track


@dataclass
class GpxImportReport:
    source_version: str
    source_namespace: str
    track_count: int = 0
    route_count: int = 0
    waypoint_count: int = 0
    converted_routes: int = 0
    converted_waypoint_tracks: int = 0
    invalid_coordinates: int = 0
    invalid_timestamps: int = 0
    backward_timestamps: int = 0
    missing_timestamps: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.track_count} track(s)",
            f"{self.route_count} route(s)",
            f"{self.waypoint_count} waypoint(s)",
        ]
        if self.missing_timestamps:
            parts.append(f"{self.missing_timestamps} point(s) without time")
        if self.invalid_coordinates:
            parts.append(f"{self.invalid_coordinates} invalid coordinate(s)")
        if self.invalid_timestamps or self.backward_timestamps:
            parts.append(
                f"{self.invalid_timestamps + self.backward_timestamps} unusable timestamp(s)"
            )
        return ", ".join(parts)


@dataclass
class NormalizedGpxDocument:
    tracks: list[ET.Element]
    report: GpxImportReport
    source_path: Path
    metadata: ET.Element | None = None


def sanitize_track_element(
    track: ET.Element,
    report: GpxImportReport | None = None,
) -> dict[str, int]:
    """Retain valid geometry while discarding only unusable values."""
    counts = {
        "coordinates": 0,
        "timestamps": 0,
        "out_of_order": 0,
        "missing_timestamps": 0,
    }
    for segment in _core_children(track, "trkseg"):
        last_time = None
        for point in list(_core_children(segment, "trkpt")):
            try:
                lat = float(point.attrib["lat"])
                lon = float(point.attrib["lon"])
            except (KeyError, TypeError, ValueError):
                segment.remove(point)
                counts["coordinates"] += 1
                continue
            if not (
                math.isfinite(lat)
                and math.isfinite(lon)
                and -90.0 <= lat <= 90.0
                and -180.0 <= lon <= 180.0
            ):
                segment.remove(point)
                counts["coordinates"] += 1
                continue
            time_element = _core_child(point, "time")
            if time_element is None or not (time_element.text or "").strip():
                counts["missing_timestamps"] += 1
                continue
            point_time = parse_time(time_element.text)
            if point_time is None:
                point.remove(time_element)
                counts["timestamps"] += 1
                continue
            if last_time is not None and point_time < last_time:
                point.remove(time_element)
                counts["out_of_order"] += 1
                continue
            last_time = point_time
    if report is not None:
        report.invalid_coordinates += counts["coordinates"]
        report.invalid_timestamps += counts["timestamps"]
        report.backward_timestamps += counts["out_of_order"]
        report.missing_timestamps += counts["missing_timestamps"]
    return counts


def _document_name(root: ET.Element, source_path: Path) -> str:
    metadata = _core_child(root, "metadata")
    if metadata is not None:
        name = _core_child(metadata, "name")
        if name is not None and (name.text or "").strip():
            return name.text.strip()
    legacy_name = _core_child(root, "name")
    if legacy_name is not None and (legacy_name.text or "").strip():
        return legacy_name.text.strip()
    return source_path.stem


def load_gpx_document(path: str | Path) -> NormalizedGpxDocument:
    """Load supported GPX structures and return canonical GPX 1.1 tracks."""
    source_path = Path(path).expanduser().resolve(strict=False)
    try:
        root = ET.parse(source_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid GPX XML: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read GPX file: {exc}") from exc

    root_uri = namespace_uri(root.tag)
    root_name = local_name(root.tag)
    version = str(root.attrib.get("version", "")).strip()
    if root_name != "gpx":
        raise ValueError("The XML root element is not <gpx>.")
    if root_uri not in SUPPORTED_GPX_NAMESPACES:
        raise ValueError(f"Unsupported GPX namespace: {root_uri or '(none)'}")
    if version not in {"1.0", "1.1"}:
        raise ValueError(f"Unsupported GPX version: {version or '(missing)'}")
    if root_uri == GPX_10_NAMESPACE and version != "1.0":
        raise ValueError("The GPX namespace and version do not agree.")
    if root_uri == GPX_NAMESPACE and version != "1.1":
        raise ValueError("The GPX namespace and version do not agree.")

    tracks = _core_children(root, "trk")
    routes = _core_children(root, "rte")
    waypoints = _core_children(root, "wpt")
    report = GpxImportReport(
        source_version=version,
        source_namespace=root_uri,
        track_count=len(tracks),
        route_count=len(routes),
        waypoint_count=len(waypoints),
    )
    fallback_name = _document_name(root, source_path)
    canonical_document = root_uri == GPX_NAMESPACE and version == "1.1"
    # Canonical GPX 1.1 elements can be edited directly. Cloning every node is
    # expensive for large files and is only required when namespaces or point
    # types need conversion.
    normalized_tracks = list(tracks) if canonical_document else [_normalize_element(track) for track in tracks]
    for index, route in enumerate(routes, start=1):
        route_name = _core_child(route, "name")
        name = (
            route_name.text.strip()
            if route_name is not None and (route_name.text or "").strip()
            else f"{fallback_name} Route {index}" if len(routes) > 1 else fallback_name
        )
        normalized_tracks.append(_route_as_track(route, name))
        report.converted_routes += 1
    if not normalized_tracks and waypoints:
        normalized_tracks.append(_waypoints_as_track(waypoints, fallback_name))
        report.converted_waypoint_tracks = 1

    usable_tracks = []
    for track in normalized_tracks:
        sanitize_track_element(track, report)
        if any(_core_children(segment, "trkpt") for segment in _core_children(track, "trkseg")):
            usable_tracks.append(track)
    if not usable_tracks:
        raise ValueError(
            "The GPX file contains no usable track, route, or waypoint coordinates."
        )

    metadata = _core_child(root, "metadata")
    return NormalizedGpxDocument(
        tracks=usable_tracks,
        report=report,
        source_path=source_path,
        metadata=(
            metadata
            if canonical_document
            else _normalize_element(metadata) if metadata is not None else None
        ),
    )


def timing_status_for_track(track: ET.Element) -> str:
    point_count = 0
    timed_count = 0
    for segment in _core_children(track, "trkseg"):
        for point in _core_children(segment, "trkpt"):
            point_count += 1
            time_element = _core_child(point, "time")
            if time_element is not None and parse_time(time_element.text) is not None:
                timed_count += 1
    if timed_count == 0:
        return "untimed"
    if timed_count < point_count:
        return "partially_timed"
    return "recorded"
