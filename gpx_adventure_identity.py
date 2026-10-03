#!/usr/bin/env python3
"""Stable GPX identities and copied-project recovery helpers.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from gpx_import import _core_child, _core_children, load_gpx_document
from json_storage import atomic_write_json


GPX_IDENTITY_VERSION = 1
GPX_FINGERPRINT_VERSION = 1


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFC", str(value or "")).casefold()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def gpx_source_identity(path: str | Path) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    return {
        "algorithm": "sha256",
        "size": source.stat().st_size,
        "sha256": _file_sha256(source),
    }


def _text(parent, name: str) -> str:
    element = _core_child(parent, name)
    return (element.text or "").strip() if element is not None else ""


def _point_value(point, name: str, precision: int) -> str:
    value = _text(point, name)
    if not value:
        return ""
    try:
        return f"{float(value):.{precision}f}"
    except ValueError:
        return value


def _track_identity(track) -> tuple[dict, tuple[float, float, float, float] | None]:
    geometry = hashlib.sha256()
    data = hashlib.sha256()
    geometry.update(f"gpx-geometry-v{GPX_FINGERPRINT_VERSION}\n".encode("ascii"))
    data.update(f"gpx-data-v{GPX_FINGERPRINT_VERSION}\n".encode("ascii"))
    minimum_latitude = minimum_longitude = math.inf
    maximum_latitude = maximum_longitude = -math.inf
    point_count = 0
    timestamps = []
    for segment in _core_children(track, "trkseg"):
        geometry.update(b"segment\n")
        data.update(b"segment\n")
        for point in _core_children(segment, "trkpt"):
            latitude = float(point.attrib["lat"])
            longitude = float(point.attrib["lon"])
            coordinate = f"{latitude:.9f}\t{longitude:.9f}"
            geometry.update(f"{coordinate}\n".encode("ascii"))
            elevation = _point_value(point, "ele", 6)
            timestamp = _text(point, "time")
            if timestamp:
                timestamps.append(timestamp)
            data.update(f"{coordinate}\t{elevation}\t{timestamp}\n".encode("utf-8"))
            minimum_latitude = min(minimum_latitude, latitude)
            maximum_latitude = max(maximum_latitude, latitude)
            minimum_longitude = min(minimum_longitude, longitude)
            maximum_longitude = max(maximum_longitude, longitude)
            point_count += 1
    extent = None
    if point_count:
        extent = (
            minimum_latitude,
            minimum_longitude,
            maximum_latitude,
            maximum_longitude,
        )
    return (
        {
            "name": _text(track, "name"),
            "geometry": geometry.hexdigest(),
            "data": data.hexdigest(),
            "point_count": point_count,
            "start_time": timestamps[0] if timestamps else "",
            "end_time": timestamps[-1] if timestamps else "",
        },
        extent,
    )


def build_gpx_identity(path: str | Path) -> dict:
    """Return a versioned identity based on normalized GPX track content."""
    source = Path(path).expanduser().resolve(strict=True)
    document = load_gpx_document(source)
    tracks = []
    extents = []
    for track in document.tracks:
        identity, extent = _track_identity(track)
        tracks.append(identity)
        if extent is not None:
            extents.append(extent)
    extent_payload = None
    if extents:
        extent_payload = {
            "min_lat": min(value[0] for value in extents),
            "min_lon": min(value[1] for value in extents),
            "max_lat": max(value[2] for value in extents),
            "max_lon": max(value[3] for value in extents),
        }
    return {
        "version": GPX_IDENTITY_VERSION,
        "source": gpx_source_identity(source),
        "fingerprint_version": GPX_FINGERPRINT_VERSION,
        "track_count": len(tracks),
        "tracks": tracks,
        "extent": extent_payload,
    }


def normalize_gpx_identity(value) -> dict | None:
    if not isinstance(value, dict) or value.get("version") != GPX_IDENTITY_VERSION:
        return None
    source = value.get("source")
    tracks = value.get("tracks")
    if not isinstance(source, dict) or not isinstance(tracks, list):
        return None
    if source.get("algorithm") != "sha256" or not str(source.get("sha256", "")).strip():
        return None
    if value.get("fingerprint_version") != GPX_FINGERPRINT_VERSION:
        return None
    normalized_tracks = []
    for track in tracks:
        if not isinstance(track, dict):
            return None
        geometry = str(track.get("geometry", "")).strip()
        data = str(track.get("data", "")).strip()
        if not geometry or not data:
            return None
        normalized_tracks.append(
            {
                "name": str(track.get("name", "")),
                "geometry": geometry,
                "data": data,
                "point_count": int(track.get("point_count", 0)),
                "start_time": str(track.get("start_time", "")),
                "end_time": str(track.get("end_time", "")),
            }
        )
    result = dict(value)
    result["tracks"] = normalized_tracks
    result["track_count"] = len(normalized_tracks)
    return result


def classify_gpx_identity(stored, candidate) -> str:
    """Classify a candidate as exact, equivalent, data, geometry, or unknown."""
    stored = normalize_gpx_identity(stored)
    candidate = normalize_gpx_identity(candidate)
    if stored is None or candidate is None:
        return "unknown"
    if stored["source"]["sha256"].casefold() == candidate["source"]["sha256"].casefold():
        return "exact"
    stored_tracks = stored["tracks"]
    candidate_tracks = candidate["tracks"]
    stored_geometry = [track["geometry"] for track in stored_tracks]
    candidate_geometry = [track["geometry"] for track in candidate_tracks]
    if stored_geometry != candidate_geometry:
        return "geometry"
    stored_data = [track["data"] for track in stored_tracks]
    candidate_data = [track["data"] for track in candidate_tracks]
    stored_names = [track["name"] for track in stored_tracks]
    candidate_names = [track["name"] for track in candidate_tracks]
    if stored_data == candidate_data and stored_names == candidate_names:
        return "equivalent"
    return "data"


@dataclass(frozen=True)
class GpxRecoveryCandidate:
    path: Path
    score: int
    summary_base: str | None = None
    summary_source_match: bool = False
    evidence: str = ""


def _summary_candidates(project_directory: Path) -> list[tuple[Path, dict]]:
    track_directory = project_directory / "trackimages"
    result = []
    if not track_directory.is_dir():
        return result
    for path in track_directory.glob("*-summary.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            result.append((path, payload))
    return result


def discover_gpx_recovery_candidates(
    project_directory: str | Path,
    missing_reference: str,
    stored_identity=None,
) -> list[GpxRecoveryCandidate]:
    """Rank project GPX files, favoring candidates named by existing summaries."""
    project = Path(project_directory).expanduser().resolve(strict=False)
    missing_stem = _normalized_name(Path(missing_reference).stem)
    summaries = _summary_candidates(project)
    stored = normalize_gpx_identity(stored_identity)
    stored_source = stored.get("source", {}) if stored is not None else {}
    candidates = []
    for path in project.glob("*.gpx"):
        score = 0
        summary_base = None
        summary_source_match = False
        evidence = ""
        candidate_name = _normalized_name(path.name)
        candidate_stem = _normalized_name(path.stem)
        if candidate_stem == missing_stem:
            score += 30
        elif missing_stem and (candidate_stem in missing_stem or missing_stem in candidate_stem):
            score += 10
        if stored_source:
            try:
                same_size = path.stat().st_size == int(stored_source.get("size", -1))
            except OSError:
                same_size = False
            if same_size:
                try:
                    if _file_sha256(path).casefold() == str(stored_source.get("sha256", "")).casefold():
                        score += 1000
                except OSError:
                    pass
        for summary_path, payload in summaries:
            source_name = _normalized_name(Path(str(payload.get("source_gpx", ""))).name)
            if source_name and source_name == candidate_name:
                score += 100
                summary_source_match = True
                summary_base = summary_path.name[: -len("-summary.json")]
                tracks = payload.get("tracks", [])
                if isinstance(tracks, list) and tracks:
                    names = [
                        str(item.get("track_name", "")).strip()
                        for item in tracks
                        if isinstance(item, dict)
                    ]
                    names = [name for name in names if name]
                    name_text = ""
                    if names:
                        name_text = f"; {names[0]}"
                        if len(names) > 1:
                            name_text += f" … {names[-1]}"
                    evidence = f"Existing map summary: {len(tracks)} tracks{name_text}"
                    first_time = str(tracks[0].get("start_time", "")).strip() if isinstance(tracks[0], dict) else ""
                    last_time = str(tracks[-1].get("end_time", "")).strip() if isinstance(tracks[-1], dict) else ""
                    if first_time or last_time:
                        evidence += f"; {first_time or '?'} – {last_time or '?'}"
                break
        candidates.append(
            GpxRecoveryCandidate(
                path.resolve(strict=False),
                score,
                summary_base,
                summary_source_match,
                evidence,
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.path.name.casefold()))


def matching_track_map_base(project_directory: str | Path, candidate: GpxRecoveryCandidate) -> str | None:
    """Return a uniquely supported map base for a legacy recovery candidate."""
    if not candidate.summary_base or not candidate.summary_source_match:
        return None
    project = Path(project_directory).expanduser().resolve(strict=False)
    track_directory = project / "trackimages"
    base = candidate.summary_base
    if (track_directory / f"{base}-summary.json").is_file() and (
        (track_directory / f"{base}.png").is_file()
        or (track_directory / f"{base}.json").is_file()
    ):
        return base
    return None


def patch_gpx_source_references(
    project_directory: str | Path,
    old_reference: str,
    new_path: str | Path,
) -> int:
    """Patch metadata-only GPX source fields without touching rendered images."""
    project = Path(project_directory).expanduser().resolve(strict=False)
    track_directory = project / "trackimages"
    if not track_directory.is_dir():
        return 0
    old_name = _normalized_name(Path(old_reference).name)
    replacement = str(Path(new_path).expanduser().resolve(strict=False))
    changed_count = 0

    def patched(value):
        nonlocal changed_count
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "source_gpx" and isinstance(item, str):
                    item_name = _normalized_name(Path(item).name)
                    if not old_name or item_name == old_name:
                        if item != replacement:
                            changed_count += 1
                        result[key] = replacement
                        continue
                result[key] = patched(item)
            return result
        if isinstance(value, list):
            return [patched(item) for item in value]
        return value

    changed_files = 0
    for path in track_directory.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        before = changed_count
        updated = patched(payload)
        if changed_count != before:
            atomic_write_json(path, updated)
            changed_files += 1
    return changed_files
