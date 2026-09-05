#!/usr/bin/env python3
"""Build editable, explicitly estimated GPX tracks from located media.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import copy
import mimetypes
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from gpx_processing import GPX_NAMESPACE, haversine_km


MYCAMINO_NAMESPACE = "https://mycamino.org/gpx/extensions/1"
ET.register_namespace("", GPX_NAMESPACE)
ET.register_namespace("mycamino", MYCAMINO_NAMESPACE)


def qname(name: str) -> str:
    return f"{{{GPX_NAMESPACE}}}{name}"


def mycamino_name(name: str) -> str:
    return f"{{{MYCAMINO_NAMESPACE}}}{name}"


@dataclass
class MediaTrackPoint:
    media_path: Path
    latitude: float
    longitude: float
    timestamp: datetime | None
    place: str | None = None


@dataclass
class MediaTrackStage:
    name: str
    points: list[MediaTrackPoint] = field(default_factory=list)


@dataclass
class MediaTrackBuildResult:
    tracks: list[ET.Element] = field(default_factory=list)
    refreshed_sidecars: int = 0
    reused_sidecars: int = 0
    skipped_media: list[str] = field(default_factory=list)
    merged_points: int = 0


def load_media_track_points(
    media_paths: Iterable[Path],
    *,
    refresh_metadata: bool = True,
) -> tuple[list[MediaTrackPoint], int, int, list[str]]:
    """Load selected media through canonical sidecars and return located points."""
    from media_metadata_service import prepare_media_records

    points: list[MediaTrackPoint] = []
    refreshed = reused = 0
    skipped: list[str] = []
    prepared = prepare_media_records(media_paths, refresh_changed=refresh_metadata)
    for item in prepared:
        path = item.path
        record = item.record
        if record is None:
            skipped.append(f"{path.name}: {item.error or item.sidecar_status}")
            continue
        was_refreshed = item.action == "extracted"
        refreshed += int(was_refreshed)
        reused += int(not was_refreshed)
        if record.latitude is None or record.longitude is None:
            skipped.append(f"{path.name}: no GPS coordinates")
            continue
        points.append(
            MediaTrackPoint(
                media_path=path,
                latitude=float(record.latitude),
                longitude=float(record.longitude),
                timestamp=record.photo_datetime,
                place=record.place,
            )
        )
    return points, refreshed, reused, skipped


def _control_stage_groups(
    control_path: Path,
    points: list[MediaTrackPoint],
) -> list[MediaTrackStage]:
    """Group selected media by display-stage directives and control-file order."""
    by_name: dict[str, list[MediaTrackPoint]] = {}
    for point in points:
        by_name.setdefault(point.media_path.name.casefold(), []).append(point)
    stages: list[MediaTrackStage] = []
    current: MediaTrackStage | None = None
    current_date = ""
    for raw_line in control_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.casefold().startswith("#datum:"):
            current_date = line.partition(":")[2].strip()
            continue
        match = re.match(
            r"^#(?:Map|MapBefore|MapAfter|MediaMap):\s*([^|]+)",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            label = Path(match.group(1).strip()).stem
            current = MediaTrackStage(label or current_date or "Media Track")
            stages.append(current)
            continue
        if not line or line.startswith("#"):
            continue
        filename = line.split("|", 1)[0].strip()
        candidates = by_name.get(Path(filename).name.casefold(), [])
        if not candidates:
            continue
        if current is None:
            current = MediaTrackStage(current_date or "Media Track")
            stages.append(current)
        current.points.append(candidates.pop(0))
    return [stage for stage in stages if stage.points]


def _date_stage_groups(points: list[MediaTrackPoint]) -> list[MediaTrackStage]:
    groups: dict[str, list[MediaTrackPoint]] = {}
    for point in points:
        day = (
            point.timestamp.astimezone().date().isoformat()
            if point.timestamp is not None
            else "Undated"
        )
        groups.setdefault(day, []).append(point)
    stages = []
    for day, members in sorted(groups.items()):
        members.sort(
            key=lambda item: (
                item.timestamp is None,
                item.timestamp.timestamp() if item.timestamp is not None else float("inf"),
                item.media_path.name.casefold(),
            )
        )
        stages.append(MediaTrackStage(day, members))
    return stages


def reduce_media_points(
    points: list[MediaTrackPoint],
    minimum_spacing_m: float,
) -> tuple[list[MediaTrackPoint], int]:
    """Apply endpoint-preserving spacing to one ordered media stage."""
    if len(points) <= 2 or minimum_spacing_m <= 0:
        return list(points), 0
    kept = [points[0]]
    for point in points[1:-1]:
        previous = kept[-1]
        distance_m = (
            haversine_km(
                previous.latitude,
                previous.longitude,
                point.latitude,
                point.longitude,
            )
            * 1000.0
        )
        if distance_m >= minimum_spacing_m:
            kept.append(point)
    kept.append(points[-1])
    return kept, len(points) - len(kept)


def _stage_track_name(stage: MediaTrackStage) -> str:
    places = [point.place.strip() for point in stage.points if point.place and point.place.strip()]
    if places:
        return places[0] if places[0] == places[-1] else f"{places[0]} - {places[-1]}"
    return stage.name


def media_stage_to_track(stage: MediaTrackStage) -> ET.Element:
    """Convert one stage into a canonical GPX track with media provenance."""
    track = ET.Element(qname("trk"))
    ET.SubElement(track, qname("name")).text = _stage_track_name(stage)
    extensions = ET.SubElement(track, qname("extensions"))
    origin = ET.SubElement(extensions, mycamino_name("trackOrigin"))
    origin.set("kind", "media-derived")
    origin.set("estimated", "true")
    segment = ET.SubElement(track, qname("trkseg"))
    for item in stage.points:
        point = ET.SubElement(
            segment,
            qname("trkpt"),
            lat=f"{item.latitude:.8f}",
            lon=f"{item.longitude:.8f}",
        )
        if item.timestamp is not None:
            ET.SubElement(point, qname("time")).text = item.timestamp.isoformat()
        ET.SubElement(point, qname("name")).text = item.media_path.name
        link = ET.SubElement(point, qname("link"), href=item.media_path.resolve(strict=False).as_uri())
        ET.SubElement(link, qname("text")).text = item.media_path.name
        media_type, _encoding = mimetypes.guess_type(item.media_path.name)
        if media_type:
            ET.SubElement(link, qname("type")).text = media_type
    return track


def build_media_tracks(
    media_paths: Iterable[Path],
    *,
    control_path: Path | None = None,
    minimum_spacing_m: float = 10.0,
    refresh_metadata: bool = True,
) -> MediaTrackBuildResult:
    """Build media-derived GPX tracks without writing a GPX file."""
    points, refreshed, reused, skipped = load_media_track_points(
        media_paths,
        refresh_metadata=refresh_metadata,
    )
    if control_path is not None and Path(control_path).is_file():
        stages = _control_stage_groups(Path(control_path), points)
        assigned = {id(point) for stage in stages for point in stage.points}
        stages.extend(_date_stage_groups([point for point in points if id(point) not in assigned]))
    else:
        stages = _date_stage_groups(points)

    result = MediaTrackBuildResult(
        refreshed_sidecars=refreshed,
        reused_sidecars=reused,
        skipped_media=skipped,
    )
    for stage in stages:
        reduced, merged = reduce_media_points(stage.points, minimum_spacing_m)
        if not reduced:
            continue
        result.merged_points += merged
        result.tracks.append(media_stage_to_track(MediaTrackStage(stage.name, reduced)))
    return result


def build_media_gpx_root(result: MediaTrackBuildResult, creator: str) -> ET.Element:
    root = ET.Element(qname("gpx"), {"version": "1.1", "creator": creator})
    for track in result.tracks:
        root.append(copy.deepcopy(track))
    return root


def write_media_gpx(path: Path, result: MediaTrackBuildResult, creator: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    ET.ElementTree(build_media_gpx_root(result, creator)).write(
        temporary,
        encoding="utf-8",
        xml_declaration=True,
    )
    temporary.replace(path)
