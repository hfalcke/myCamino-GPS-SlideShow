"""Shared state and persistence helpers for the map-first Adventure workspace.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from json_storage import atomic_write_json


RECOVERY_FORMAT_VERSION = 1
RECOVERY_HISTORY_LIMIT = 20
LOG_SIZE_LIMIT = 5 * 1024 * 1024
LOG_HISTORY_LIMIT = 5


_EXTENT_KEYS = ("min_x", "max_x", "min_y", "max_y")


def validated_mercator_extent(value) -> dict | None:
    """Return a finite, non-empty Web Mercator extent or None."""
    if not isinstance(value, dict):
        return None
    try:
        extent = {key: float(value[key]) for key in _EXTENT_KEYS}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in extent.values()):
        return None
    if extent["max_x"] <= extent["min_x"] or extent["max_y"] <= extent["min_y"]:
        return None
    return extent


def read_map_extent_prefix(path: Path, *, maximum_bytes: int = 256 * 1024) -> dict | None:
    """Read an early extent field without decoding a potentially huge map sidecar."""
    try:
        with Path(path).open("rb") as handle:
            prefix = handle.read(max(1024, int(maximum_bytes))).decode("utf-8", "replace")
    except OSError:
        return None
    match = re.search(r'"extent_mercator"\s*:\s*(\{[^{}]+\})', prefix)
    if match is None:
        return None
    try:
        return validated_mercator_extent(json.loads(match.group(1)))
    except (json.JSONDecodeError, TypeError):
        return None


def extent_from_track_summary(summary) -> dict | None:
    """Build a padded fallback extent from compact track-summary endpoints."""
    if not isinstance(summary, dict):
        return None
    tracks = summary.get("tracks") or summary.get("track_items") or []
    coordinates = []
    for track in tracks if isinstance(tracks, list) else []:
        if not isinstance(track, dict):
            continue
        for key in ("start_point", "end_point"):
            point = track.get(key)
            if not isinstance(point, dict):
                continue
            try:
                latitude = float(point.get("latitude", point.get("lat")))
                longitude = float(point.get("longitude", point.get("lon")))
            except (TypeError, ValueError):
                continue
            if math.isfinite(latitude) and math.isfinite(longitude):
                coordinates.append((longitude, latitude))
    if not coordinates:
        return None
    radius = 6378137.0
    projected = []
    for longitude, latitude in coordinates:
        latitude = max(-85.05112878, min(85.05112878, latitude))
        x_coord = radius * math.radians(longitude)
        y_coord = radius * math.log(math.tan(math.pi / 4.0 + math.radians(latitude) / 2.0))
        projected.append((x_coord, y_coord))
    xs = [item[0] for item in projected]
    ys = [item[1] for item in projected]
    span_x = max(max(xs) - min(xs), 1000.0)
    span_y = max(max(ys) - min(ys), 1000.0)
    return validated_mercator_extent(
        {
            "min_x": min(xs) - span_x * 0.08,
            "max_x": max(xs) + span_x * 0.08,
            "min_y": min(ys) - span_y * 0.08,
            "max_y": max(ys) + span_y * 0.08,
        }
    )


@dataclass(frozen=True)
class MediaMapItem:
    path: Path
    latitude: float | None = None
    longitude: float | None = None
    exposure_time: str = ""
    place: str = ""
    track_identity: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class MediaCluster:
    items: tuple[MediaMapItem, ...]
    x: float
    y: float
    track_identities: frozenset[str]

    @property
    def count(self) -> int:
        return len(self.items)


def resolved_media_paths(items: Iterable[MediaMapItem]) -> set[Path]:
    return {item.path.resolve(strict=False) for item in items}


def update_media_selection(
    current: Iterable[Path],
    clicked: Iterable[Path],
    *,
    additive: bool,
) -> set[Path]:
    """Apply standard click or Command-click semantics to media paths."""
    existing = {Path(path).resolve(strict=False) for path in current}
    targets = {Path(path).resolve(strict=False) for path in clicked}
    if not additive:
        return targets
    return existing.symmetric_difference(targets)


def normalized_screen_rectangle(start, end) -> tuple[float, float, float, float]:
    left, right = sorted((float(start[0]), float(end[0])))
    bottom, top = sorted((float(start[1]), float(end[1])))
    return left, bottom, right, top


def screen_rectangles_intersect(first, second) -> bool:
    first_left, first_bottom, first_right, first_top = first
    second_left, second_bottom, second_right, second_top = second
    return not (
        first_right < second_left
        or second_right < first_left
        or first_top < second_bottom
        or second_top < first_bottom
    )


def cluster_projected_media(
    projected: Iterable[tuple[MediaMapItem, float, float]],
    *,
    cell_size: float = 48.0,
) -> list[MediaCluster]:
    """Group nearby media in display space, including across grid boundaries."""
    size = max(1.0, float(cell_size))
    values = [(item, float(x), float(y)) for item, x, y in projected]
    parents = list(range(len(values)))
    bounds = [[x, x, y, y] for _item, x, y in values]

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first, second):
        first_root = find(first)
        second_root = find(second)
        if first_root == second_root:
            return
        combined = [
            min(bounds[first_root][0], bounds[second_root][0]),
            max(bounds[first_root][1], bounds[second_root][1]),
            min(bounds[first_root][2], bounds[second_root][2]),
            max(bounds[first_root][3], bounds[second_root][3]),
        ]
        # Proximity is not transitive for display clustering: otherwise a chain
        # of Camino photos can collapse an entire continent into one icon.
        if math.hypot(combined[1] - combined[0], combined[3] - combined[2]) > size * 1.5:
            return
        parents[second_root] = first_root
        bounds[first_root] = combined

    cells: dict[tuple[int, int], list[int]] = {}
    for index, (_item, x, y) in enumerate(values):
        cell = (math.floor(x / size), math.floor(y / size))
        for neighbor_x in range(cell[0] - 1, cell[0] + 2):
            for neighbor_y in range(cell[1] - 1, cell[1] + 2):
                for other in cells.get((neighbor_x, neighbor_y), []):
                    _other_item, other_x, other_y = values[other]
                    if math.hypot(x - other_x, y - other_y) <= size:
                        union(index, other)
        cells.setdefault(cell, []).append(index)

    groups: dict[int, list[tuple[MediaMapItem, float, float]]] = {}
    first_indexes: dict[int, int] = {}
    for index, value in enumerate(values):
        root = find(index)
        groups.setdefault(root, []).append(value)
        first_indexes[root] = min(index, first_indexes.get(root, index))
    clusters = []
    for key in sorted(groups, key=first_indexes.__getitem__):
        members = groups[key]
        items = tuple(member[0] for member in members)
        clusters.append(
            MediaCluster(
                items,
                sum(member[1] for member in members) / len(members),
                sum(member[2] for member in members) / len(members),
                frozenset(item.track_identity for item in items if item.track_identity),
            )
        )
    return clusters


def should_expand_media_thumbnails(
    clusters: Sequence[MediaCluster],
    *,
    projected_track_length: float,
    thumbnail_size: float = 72.0,
) -> bool:
    """Quickly decide whether at least half-size thumbnails can avoid crowding."""
    count = len(clusters)
    if count == 0:
        return False
    size = max(float(thumbnail_size), 1.0)
    # Nearby photos are already represented by one cluster. Remaining groups
    # need at least half a thumbnail width of route each to remain recognizable.
    if float(projected_track_length) < count * size * 0.5:
        return False
    overlap_distance = size * 0.5
    cells: dict[tuple[int, int], list[int]] = {}
    crowded_indexes = set()
    for index, cluster in enumerate(clusters):
        cell = (
            math.floor(cluster.x / overlap_distance),
            math.floor(cluster.y / overlap_distance),
        )
        for neighbor_x in range(cell[0] - 1, cell[0] + 2):
            for neighbor_y in range(cell[1] - 1, cell[1] + 2):
                for other_index in cells.get((neighbor_x, neighbor_y), []):
                    other = clusters[other_index]
                    if (
                        abs(cluster.x - other.x) < overlap_distance
                        and abs(cluster.y - other.y) < overlap_distance
                    ):
                        crowded_indexes.update((index, other_index))
        cells.setdefault(cell, []).append(index)
    return len(crowded_indexes) <= count / 2.0


def media_cluster_belongs_to_track(
    cluster: MediaCluster,
    selected_identities: set[str] | frozenset[str],
    *,
    focused_track_view: bool,
) -> bool:
    """Associate inferred media exactly and embedded-GPS media by focused extent."""
    if cluster.track_identities:
        return bool(cluster.track_identities & set(selected_identities))
    return bool(focused_track_view)


def track_extent_is_prominent(track_extent: dict, viewport_extent: dict, *, fraction=2.0 / 3.0) -> bool:
    """Return whether a track spans enough of the current viewport for thumbnails."""
    required = ("min_x", "max_x", "min_y", "max_y")
    if not all(key in track_extent and key in viewport_extent for key in required):
        return False
    viewport_width = max(float(viewport_extent["max_x"]) - float(viewport_extent["min_x"]), 1.0)
    viewport_height = max(float(viewport_extent["max_y"]) - float(viewport_extent["min_y"]), 1.0)
    track_width = max(float(track_extent["max_x"]) - float(track_extent["min_x"]), 0.0)
    track_height = max(float(track_extent["max_y"]) - float(track_extent["min_y"]), 0.0)
    return max(track_width / viewport_width, track_height / viewport_height) >= float(fraction)


def ordered_media_viewer_paths(
    media: Sequence[MediaMapItem],
    control_names: Iterable[str] = (),
    *,
    project_directory: Path | None = None,
) -> list[Path]:
    """Order all Adventure media by control rows, then exposure time and name."""
    items_by_path = {
        item.path.resolve(strict=False): item
        for item in media
    }
    items_by_name: dict[str, list[MediaMapItem]] = {}
    for item in media:
        items_by_name.setdefault(item.path.name.casefold(), []).append(item)
    ordered: list[Path] = []
    seen: set[Path] = set()
    for raw_name in control_names:
        value = str(raw_name or "").strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() and project_directory is not None:
            candidate = Path(project_directory) / candidate
        resolved = candidate.resolve(strict=False)
        item = items_by_path.get(resolved)
        if item is None:
            matches = items_by_name.get(Path(value).name.casefold(), [])
            item = matches[0] if len(matches) == 1 else None
        if item is not None:
            path = item.path.resolve(strict=False)
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    remaining = sorted(
        (item for item in media if item.path.resolve(strict=False) not in seen),
        key=lambda item: (
            not bool(item.exposure_time),
            item.exposure_time,
            item.path.name.casefold(),
            str(item.path).casefold(),
        ),
    )
    ordered.extend(item.path.resolve(strict=False) for item in remaining)
    return ordered


def pixel_simplify_segments(
    segments: Sequence[Sequence[tuple[float, float]]],
    project,
    *,
    minimum_pixel_distance: float = 1.5,
) -> list[list[tuple[float, float]]]:
    """Simplify segments in display space while retaining ends and boundaries."""
    threshold_sq = max(0.0, float(minimum_pixel_distance)) ** 2
    result: list[list[tuple[float, float]]] = []
    for segment in segments:
        if len(segment) <= 2:
            result.append(list(segment))
            continue
        kept = [segment[0]]
        last_x, last_y = project(*segment[0])
        for point in segment[1:-1]:
            x, y = project(*point)
            if (x - last_x) ** 2 + (y - last_y) ** 2 >= threshold_sq:
                kept.append(point)
                last_x, last_y = x, y
        kept.append(segment[-1])
        result.append(kept)
    return result


def temporary_control_rows(media: Iterable[MediaMapItem]) -> list[dict]:
    """Build a simple dated control model used by folder drops."""
    groups: dict[str, list[MediaMapItem]] = {}
    for item in media:
        date = str(item.exposure_time or "")[:10]
        groups.setdefault(date if len(date) == 10 else "Date unknown", []).append(item)
    rows: list[dict] = []
    dated = sorted(key for key in groups if key != "Date unknown")
    if "Date unknown" in groups:
        dated.append("Date unknown")
    for date in dated:
        rows.append({"type": "DAT", "name": date, "enabled": True})
        for item in sorted(groups[date], key=lambda value: (value.exposure_time, value.path.name.casefold())):
            rows.append(
                {
                    "type": "VID" if item.path.suffix.casefold() in {".mov", ".mp4", ".m4v", ".avi"} else "IMG",
                    "name": str(item.path),
                    "time": item.exposure_time,
                    "gps": (
                        f"{item.latitude:.7f}, {item.longitude:.7f}"
                        if item.latitude is not None and item.longitude is not None
                        else ""
                    ),
                    "place": item.place,
                    "enabled": bool(item.enabled),
                    "disabled": not bool(item.enabled),
                }
            )
    return rows


class ProcessingJournal:
    """Thread-safe rotating journal which can move into an Adventure."""

    def __init__(self, directory: Path, *, max_bytes=LOG_SIZE_LIMIT, retained=LOG_HISTORY_LIMIT):
        self.directory = Path(directory)
        self.max_bytes = int(max_bytes)
        self.retained = int(retained)
        self._lock = threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "processing.log"

    def _rotate(self):
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.retained}")
        oldest.unlink(missing_ok=True)
        for index in range(self.retained - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    @staticmethod
    def redact(message: str) -> str:
        text = str(message)
        for marker in ("apikey=", "api_key=", "key="):
            start = 0
            while True:
                index = text.casefold().find(marker, start)
                if index < 0:
                    break
                value_start = index + len(marker)
                value_end = value_start
                while value_end < len(text) and text[value_end] not in "& ,\n\r\t":
                    value_end += 1
                text = text[:value_start] + "[redacted]" + text[value_end:]
                start = value_start + 10
        return text

    def append(self, message: str, *, phase: str | None = None):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        prefix = f"[{timestamp}] "
        if phase:
            prefix += f"[{phase}] "
        line = prefix + self.redact(message).rstrip() + "\n"
        with self._lock:
            self._rotate()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def read(self) -> str:
        with self._lock:
            try:
                return self.path.read_text(encoding="utf-8")
            except OSError:
                return ""

    def move_to(self, directory: Path):
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self.directory.resolve(strict=False) == destination.resolve(strict=False):
                return
            for source in self.directory.glob("processing.log*"):
                target = destination / source.name
                if target.exists():
                    with target.open("a", encoding="utf-8") as output:
                        output.write(source.read_text(encoding="utf-8"))
                    source.unlink(missing_ok=True)
                else:
                    shutil.move(str(source), str(target))
            self.directory = destination
            self.path = destination / "processing.log"


@dataclass
class RecoverySnapshot:
    created_at: str
    adventure_name: str
    gpx_xml: str
    control_rows: list[dict] = field(default_factory=list)
    source_directory: str = ""
    adventure_draft: dict = field(default_factory=dict)
    playlists: dict = field(default_factory=dict)
    asset_references: list[dict] = field(default_factory=list)
    selected_track: str = ""


class WorkspaceRecoverySession:
    """Versioned recovery history for an unsaved map workspace."""

    def __init__(self, session_id: str | None = None, *, root: Path | None = None):
        base = Path(root) if root else Path(tempfile.gettempdir()) / "myCamino-map-recovery"
        if session_id is None:
            session_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
        self.session_id = session_id
        self.directory = base / session_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self._sequence = self._discover_sequence()

    def _discover_sequence(self) -> int:
        values = []
        for path in self.directory.glob("snapshot-*.json"):
            try:
                values.append(int(path.stem.rsplit("-", 1)[-1]))
            except ValueError:
                pass
        return max(values, default=0)

    def write(self, snapshot: RecoverySnapshot) -> Path:
        self._sequence += 1
        path = self.directory / f"snapshot-{self._sequence:06d}.json"
        atomic_write_json(
            path,
            {"format_version": RECOVERY_FORMAT_VERSION, "session_id": self.session_id, **asdict(snapshot)},
        )
        snapshots = sorted(self.directory.glob("snapshot-*.json"), reverse=True)
        for old in snapshots[RECOVERY_HISTORY_LIMIT:]:
            old.unlink(missing_ok=True)
        return path

    def latest(self) -> dict | None:
        paths = sorted(self.directory.glob("snapshot-*.json"), reverse=True)
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("format_version") == RECOVERY_FORMAT_VERSION:
                return payload
        return None

    def discard(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def discover_recovery_sessions(
    *,
    root: Path | None = None,
    project_directory: Path | None = None,
) -> list[dict]:
    """Return recoveries relevant to a project, or every session for an empty launch."""
    base = Path(root) if root else Path(tempfile.gettempdir()) / "myCamino-map-recovery"
    expected_directory = (
        Path(project_directory).expanduser().resolve(strict=False)
        if project_directory is not None
        else None
    )
    result = []
    if not base.is_dir():
        return result
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        session = WorkspaceRecoverySession(directory.name, root=base)
        payload = session.latest()
        if payload:
            if expected_directory is not None:
                source = str(payload.get("source_directory") or "").strip()
                if not source or Path(source).expanduser().resolve(strict=False) != expected_directory:
                    continue
            payload["recovery_directory"] = str(directory)
            result.append(payload)
    return sorted(result, key=lambda item: str(item.get("created_at", "")), reverse=True)


def delete_recovery_session(payload: dict, *, root: Path | None = None) -> bool:
    """Delete one discovered recovery session without accepting arbitrary paths."""
    base = Path(root) if root else Path(tempfile.gettempdir()) / "myCamino-map-recovery"
    try:
        directory = Path(str(payload.get("recovery_directory") or "")).resolve(strict=False)
        if directory.parent != base.resolve(strict=False) or not directory.is_dir():
            return False
        shutil.rmtree(directory)
        return not directory.exists()
    except OSError:
        return False


def media_item_from_sidecar(path: Path, payload: dict | None) -> MediaMapItem:
    payload = payload if isinstance(payload, dict) else {}
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    gps = payload.get("gps")
    if isinstance(gps, dict):
        latitude = gps.get("latitude", latitude)
        longitude = gps.get("longitude", longitude)
    place = payload.get("place")
    if isinstance(place, dict):
        place = place.get("name") or place.get("display_name") or ""
    track = payload.get("gps_inference")
    track_identity = ""
    if isinstance(track, dict):
        track_identity = str(track.get("track_data_fingerprint") or track.get("track_fingerprint") or track.get("track_number") or "")
    try:
        latitude = float(latitude) if latitude is not None else None
        longitude = float(longitude) if longitude is not None else None
    except (TypeError, ValueError):
        latitude = longitude = None
    return MediaMapItem(
        Path(path),
        latitude,
        longitude,
        str(payload.get("datetime_iso") or ""),
        str(place or ""),
        track_identity,
        True,
    )
