#!/usr/bin/env python3
# Install note:
# python3 -m pip install pyobjc-core pyobjc-framework-CoreLocation pyobjc-framework-Cocoa
"""Extract photo dates and geolocations from a directory on macOS."""

from __future__ import annotations

import argparse
import bisect
import copy
from contextlib import redirect_stderr, redirect_stdout
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from typing import Any, Callable, Optional, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plot_metadata_utils import (
    build_photo_metadata_payload,
    legacy_media_sidecar_path,
    media_sidecar_matches_media,
    media_file_signature,
    media_sidecar_freshness,
    media_sidecar_path,
    parse_photo_datetime,
    read_json_data,
    read_photo_metadata,
    validate_media_sidecar,
    write_photo_metadata,
)
from gpx_tracks_table import (
    media_coordinates_fingerprint,
    media_map_metadata_matches_coordinates,
    media_overview_fingerprint,
    render_media_location_map,
    render_media_overview_map,
)
from track_map_layout_utils import (
    canonical_track_map_name,
    time_lapse_track_map_name,
    track_map_variant_names,
)


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".tif",
    ".tiff",
    ".gif",
    ".bmp",
    ".webp",
}
VIDEO_EXTENSIONS = {
    ".mov",
    ".mp4",
    ".m4v",
    ".avi",
    ".mkv",
    ".3gp",
    ".3g2",
    ".mpg",
    ".mpeg",
    ".mts",
    ".m2ts",
    ".wmv",
}
DEFAULT_PLACE_GPS_EQUIVALENCE_M = 150.0
FILE_FILTERS = {"IMAGE", "VIDEO", "ALL"}

GERMAN_WEEKDAYS = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]

GPS_NOT_AVAILABLE = "kein GPS"
PLACE_NOT_AVAILABLE = "kein Ort"
PLACE_NOT_REQUESTED = "-"
PLACE_FAILED = "Place failed"
GEOCODE_PACING_MIN_SECONDS = 1.0
GEOCODE_PACING_MAX_SECONDS = 5.0
GEOCODE_ROUND_DIGITS = 6
# Optional fallback dependency:
# exiftool can recover GPS/date metadata that mdls does not expose.
EXIFTOOL_WARNING_SHOWN = False
RUNTIME_CANCEL_EVENT: Optional[threading.Event] = None


class ProcessingCancelled(Exception):
    """Raised when an in-process run is cancelled."""

def detect_local_timezone() -> Optional[object]:
    """Return the local timezone with historical DST rules when possible."""
    tz_name = os.environ.get("TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            pass

    localtime_path = Path("/etc/localtime")
    try:
        resolved = localtime_path.resolve()
    except OSError:
        resolved = None
    if resolved is not None:
        path_parts = resolved.parts
        for anchor in ("zoneinfo", "zoneinfo.default"):
            if anchor in path_parts:
                anchor_index = path_parts.index(anchor)
                candidate_name = "/".join(path_parts[anchor_index + 1 :])
                if candidate_name:
                    try:
                        return ZoneInfo(candidate_name)
                    except ZoneInfoNotFoundError:
                        pass

    current_tz = datetime.now().astimezone().tzinfo
    if current_tz is not None:
        tz_key = getattr(current_tz, "key", None)
        if isinstance(tz_key, str):
            try:
                return ZoneInfo(tz_key)
            except ZoneInfoNotFoundError:
                pass
    return current_tz


def check_cancelled() -> None:
    """Raise when the current in-process run has been cancelled."""
    if RUNTIME_CANCEL_EVENT is not None and RUNTIME_CANCEL_EVENT.is_set():
        raise ProcessingCancelled("Aborted.")


def sleep_with_cancel(seconds: float) -> None:
    """Sleep in short intervals so cancellation remains responsive."""
    remaining = max(0.0, float(seconds))
    while remaining > 0.0:
        check_cancelled()
        chunk = min(0.1, remaining)
        time.sleep(chunk)
        remaining -= chunk


LOCAL_TIMEZONE = detect_local_timezone()
ADJACENT_TRACK_RADIUS_FRACTION = 0.5


try:
    from CoreLocation import CLGeocoder, CLLocation  # type: ignore
    from Foundation import NSDate, NSRunLoop  # type: ignore

    CORELOCATION_AVAILABLE = True
except Exception:
    CLGeocoder = None  # type: ignore[assignment]
    CLLocation = None  # type: ignore[assignment]
    NSDate = None  # type: ignore[assignment]
    NSRunLoop = None  # type: ignore[assignment]
    CORELOCATION_AVAILABLE = False


@dataclass(frozen=True)
class Params:
    """Command-line parameters."""

    photodir: Path
    gps_dir: Path
    photolist: Path
    tracks: Optional[Path]
    distance: float
    geocode_timeout_seconds: float
    geocode_pacing_min_seconds: float
    geocode_pacing_max_seconds: float
    file_filter: str
    getclearnames: bool
    redo_reverse_geolocation: bool
    overwrite_reverse_geolocation: bool
    photos: Optional[str]
    photonames: Optional[str]
    ignorejson: bool
    debug: bool
    sort_date_sections_by_tracks: bool
    merge_tracks: Optional[Path]
    merge_media: tuple[Path, ...]
    infer_gps_from_tracks: bool = True
    migrate_media_sidecars: bool = False
    media_map_options: Optional[dict[str, Any]] = None
    progress_callback: Optional[Callable[[int, int, str], None]] = None


@dataclass
class PhotoRecord:
    """Normalized photo metadata used for output and JSON caching."""

    source_filename: str
    display_filename: str
    photo_path: Path
    json_path: Path
    photo_datetime: datetime
    latitude: Optional[float]
    longitude: Optional[float]
    place: Optional[str]
    place_details: Optional[dict[str, Any]]
    source: str
    geocode_requested: bool
    place_updated: bool
    debug_info: Optional[dict[str, Any]] = None
    gps_source: Optional[str] = None
    gps_inference: Optional[dict[str, Any]] = None
    gps_updated: bool = False
    raw_metadata: Optional[dict[str, Any]] = None
    datetime_source: Optional[str] = None


@dataclass
class MediaSidecarMigrationReport:
    """Result of a media-sidecar migration for one project directory."""

    project_dir: Path
    migrated: list[tuple[Path, Path]]
    regenerated: list[Path]
    preserved: list[tuple[Path, Path]]
    conflicts: list[tuple[Path, Path]]


@dataclass
class SidecarPlaceUpdateReport:
    """Counts from a sidecar-only reverse-geolocation pass."""

    total: int = 0
    updated: int = 0
    already_complete: int = 0
    missing: int = 0
    invalid: int = 0
    gps_less: int = 0
    failed: int = 0
    track_endpoints_total: int = 0
    track_endpoints_updated: int = 0
    track_endpoints_complete: int = 0
    track_endpoints_failed: int = 0
    track_sidecars_updated: int = 0


@dataclass
class MediaUpdateItem:
    """One selected media file and its proposed incremental update."""

    media_path: Path
    action: str
    sidecar_status: str
    freshness: str
    included_count: int
    old_record: Optional[PhotoRecord]
    new_record: PhotoRecord
    staged_payload: dict[str, Any]
    current_section: str
    proposed_section: str
    reposition: bool
    gps_changed: bool
    place_update_recommended: bool
    analyzed_media_signature: dict[str, int] = field(default_factory=dict)
    analyzed_sidecar_signature: Optional[tuple[int, int]] = None
    sidecar_write_required: bool = False
    control_conflict: Optional[str] = None
    control_update_pending: bool = False
    apply_update: bool = True


@dataclass(frozen=True)
class MediaUpdateCandidate:
    """One media file selected by the inexpensive update inventory."""

    media_path: Path
    action: str
    reason: str


@dataclass
class MediaUpdatePlan:
    """A side-effect-free preview of selected media updates."""

    project_dir: Path
    control_file: Optional[Path]
    tracks_summary_path: Optional[Path]
    items: list[MediaUpdateItem]
    tracks_summary: Optional[TracksSummary]
    sort_date_sections_by_tracks: bool
    control_signature: Optional[tuple[int, int]] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TrackMapReferenceUpdatePlan:
    """Side-effect-free changes needed to align control rows with Track Maps."""

    missing_overview: list[str] = field(default_factory=list)
    missing_tracks: list[str] = field(default_factory=list)
    obsolete_overview: list[str] = field(default_factory=list)
    obsolete_tracks: list[str] = field(default_factory=list)
    reordered_tracks: list[str] = field(default_factory=list)
    special_updates: list[tuple[str, str, str]] = field(default_factory=list)
    summary_available: bool = False
    summary_current: bool = True
    warning: Optional[str] = None

    @property
    def change_count(self) -> int:
        return (
            len(self.missing_overview)
            + len(self.missing_tracks)
            + len(self.obsolete_overview)
            + len(self.obsolete_tracks)
            + len(self.reordered_tracks)
            + len(self.special_updates)
        )


@dataclass
class ControlFileUpdatePlan:
    """Combined Track Map reference and selective media update preview."""

    media: MediaUpdatePlan
    track_maps: TrackMapReferenceUpdatePlan


@dataclass
class MediaUpdateResult:
    """Counts produced by one committed selective media update."""

    refreshed_sidecars: int = 0
    rows_added: int = 0
    rows_updated: int = 0
    rows_moved: int = 0
    control_rows_pending: int = 0
    media_maps_regenerated: int = 0
    places_preserved: int = 0
    places_updated: int = 0
    gps_inferred: int = 0
    gps_refreshed: int = 0


@dataclass
class ControlFileUpdateResult:
    """Result of one combined atomic control-file update."""

    media: MediaUpdateResult = field(default_factory=MediaUpdateResult)
    map_entries_added: int = 0
    map_entries_replaced: int = 0
    map_entries_removed: int = 0
    map_entries_reordered: int = 0


@dataclass
class ProjectMapPlan:
    """Shared stage and media-map plan used before and during control creation."""

    records: list[PhotoRecord]
    sections: list[dict[str, Any]]
    media_map_specs: list[dict[str, Any]]
    overview_name: Optional[str]
    overview_points: list[dict[str, Any]]
    tracks_summary: Optional[TracksSummary]


def discover_media_update_candidates(
    project_dir: Path | str,
    *,
    imported_paths: Optional[list[Path | str] | tuple[Path | str, ...] | set[Path | str]] = None,
    only_imported: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    detail_callback: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> list[MediaUpdateCandidate]:
    """Find clear selective-update candidates without extracting media metadata."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    imported = {
        Path(path).expanduser().resolve(strict=False)
        for path in (imported_paths or ())
    }
    if only_imported:
        media_paths = sorted(
            (
                path for path in imported
                if path.parent == project
                and path.is_file()
                and path.suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
    else:
        media_paths = sorted(
            (
                path.resolve(strict=False)
                for path in project.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
    candidates: list[MediaUpdateCandidate] = []
    total = len(media_paths)
    if progress_callback is not None:
        progress_callback(0, total, "")
    for index, media_path in enumerate(media_paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Aborted.")
        check_cancelled()
        status, payload, _reason = validate_media_sidecar(media_path)
        freshness = media_sidecar_freshness(media_path, payload) if status == "available" else status
        if media_path in imported:
            action = "repair" if status != "available" else (
                "refresh" if freshness in {"changed", "unknown"} else "use_sidecar"
            )
            candidates.append(MediaUpdateCandidate(media_path, action, "Imported"))
        elif only_imported:
            pass
        elif status in {"missing", "invalid"}:
            candidates.append(MediaUpdateCandidate(media_path, "repair", status.title()))
        elif freshness == "changed":
            candidates.append(MediaUpdateCandidate(media_path, "refresh", "File changed"))
        elif freshness == "unknown":
            candidates.append(
                MediaUpdateCandidate(
                    media_path,
                    "refresh",
                    "Legacy sidecar: extracting metadata once to establish file signature",
                )
            )
            if detail_callback is not None:
                detail_callback(
                    f"Legacy sidecar: extracting metadata once to establish file signature: {media_path.name}."
                )
        elif detail_callback is not None:
            detail_callback(
                f"Skipping {media_path.name}: metadata sidecar is current."
            )
        if progress_callback is not None:
            progress_callback(index, total, media_path.name)
    return candidates


@dataclass(frozen=True)
class TrackInfo:
    """Track summary metadata used in the sorted list output."""

    start_time: datetime
    track_plot_image_filename: str
    original_sequence_number: int
    length_km: Optional[float] = None
    start_latitude: Optional[float] = None
    start_longitude: Optional[float] = None
    end_latitude: Optional[float] = None
    end_longitude: Optional[float] = None
    end_time: Optional[datetime] = None
    track_name: Optional[str] = None
    track_fingerprint: Optional[str] = None
    map_sidecar_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class TrackTimeline:
    """Validated timed points lazily loaded from one Track Map sidecar."""

    times: tuple[datetime, ...]
    points: tuple[dict[str, Any], ...]
    source_path: Path


@dataclass(frozen=True)
class AdjacentDayAssignment:
    """One media-to-track relation for a date without an exact track."""

    relation: str
    track: TrackInfo
    distance_m: Optional[float]


@dataclass(frozen=True)
class TracksSummary:
    """Overview map and per-track summary information."""

    overview_image: Optional[str]
    tracks: list[TrackInfo]
    ignored_photo_names: set[str]


@dataclass(frozen=True)
class KnownPlace:
    """One successful reverse-geocoded location that can be reused nearby."""

    latitude: float
    longitude: float
    place: str
    place_details: Optional[dict[str, Any]]


def collect_input_parameters(argv: list[str]) -> Params:
    """Parse and validate CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="GetGeoLocations.py",
        description=(
            "Scan a media directory, extract date/time and GPS metadata, optionally "
            "reverse-geocode locations on macOS, and write text and JSON output."
        ),
    )
    parser.add_argument("photodir", type=Path, help="Directory containing media files to process.")
    parser.add_argument(
        "--photolist",
        "-l",
        dest="photolist",
        type=Path,
        default=None,
        help="Output text file path (default: photodir/photos.lst).",
    )
    parser.add_argument(
        "--tracks",
        "-t",
        dest="tracks",
        type=Path,
        default=None,
        help="Track summary JSON file created by gpx_tracks_table.py.",
    )
    parser.add_argument(
        "--distance",
        dest="distance",
        type=float,
        default=150.0,
        help="Reuse a known successful place within this distance in meters (default: 150).",
    )
    parser.add_argument("--geocode-timeout-seconds", type=float, default=10.0, help="Timeout for one reverse-geocoding request.")
    parser.add_argument("--geocode-pacing-min-seconds", type=float, default=1.0, help="Minimum delay between reverse-geocoding requests.")
    parser.add_argument("--geocode-pacing-max-seconds", type=float, default=5.0, help="Maximum delay between reverse-geocoding requests.")
    parser.add_argument(
        "--file-filter",
        dest="file_filter",
        type=str.upper,
        choices=sorted(FILE_FILTERS),
        default="ALL",
        help="File types to process: IMAGE, VIDEO, or ALL (default: ALL).",
    )
    parser.add_argument(
        "--getclearnames",
        "-g",
        action="store_true",
        help="Reverse-geocode GPS coordinates into human-readable place names.",
    )
    parser.add_argument(
        "--redo_reverse_geolocation",
        "-r",
        action="store_true",
        help="Redo reverse geolocation for records with GPS but missing place names.",
    )
    parser.add_argument(
        "--overwrite_reverse_geolocation",
        action="store_true",
        help="Redo reverse geolocation and overwrite existing place names in media sidecar files.",
    )
    parser.add_argument(
        "--photos",
        dest="photos",
        default=None,
        help=(
            "Photo numbers to process from the sorted input list, for example "
            "'1,3,5-8,12-'."
        ),
    )
    parser.add_argument(
        "--photonames",
        "-p",
        dest="photonames",
        default=None,
        help="Comma-separated list of photo filenames to process.",
    )
    parser.add_argument(
        "--ignorejson",
        "-i",
        action="store_true",
        help="Ignore existing sidecar JSON files and overwrite them with fresh metadata.",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Print debug information about metadata lookup and fallback decisions.",
    )
    parser.add_argument(
        "--sort-date-sections-by-tracks",
        action="store_true",
        help=(
            "In the sorted output list, order #Datum sections by the original "
            "track order from the tracks JSON instead of chronological date order."
        ),
    )
    parser.add_argument(
        "--merge_tracks",
        dest="merge_tracks",
        type=Path,
        default=None,
        help="Merge missing #Overviewmap/#Map entries from an updated gpx_tracks_table.py summary JSON into the sorted photolist.",
    )
    parser.add_argument(
        "--merge_media",
        dest="merge_media",
        default="",
        help="Comma-separated image/video files to merge into the existing sorted photolist.",
    )
    parser.add_argument(
        "--no-track-gps-inference",
        dest="infer_gps_from_tracks",
        action="store_false",
        default=True,
        help="Do not infer missing media GPS coordinates from timed Track Map sidecars.",
    )
    parser.add_argument(
        "--migrate-media-sidecars",
        action="store_true",
        help="Move legacy <stem>.json media sidecars to collision-safe <mediafile>.json names and create missing sidecars.",
    )

    args = parser.parse_args(argv)
    photodir = args.photodir.expanduser().resolve()
    if not photodir.is_dir():
        parser.error(f"photodir is not a directory: {photodir}")

    photolist = args.photolist.expanduser().resolve() if args.photolist else (photodir / "photos.lst")
    tracks = args.tracks.expanduser().resolve() if args.tracks else None
    if tracks is not None and not tracks.is_file():
        parser.error(f"tracks file is not a file: {tracks}")
    merge_tracks = args.merge_tracks.expanduser().resolve() if args.merge_tracks else None
    if merge_tracks is not None and not merge_tracks.is_file():
        parser.error(f"merge tracks file is not a file: {merge_tracks}")
    merge_media = tuple(
        Path(item.strip()).expanduser().resolve()
        for item in str(args.merge_media or "").split(",")
        if item.strip()
    )
    for media_path in merge_media:
        if not media_path.is_file():
            parser.error(f"merge media file is not a file: {media_path}")
    if args.distance < 0:
        parser.error(f"distance must be non-negative: {args.distance}")
    if args.geocode_timeout_seconds <= 0:
        parser.error("geocode timeout must be positive")
    if args.geocode_pacing_min_seconds < 0 or args.geocode_pacing_max_seconds < args.geocode_pacing_min_seconds:
        parser.error("geocode pacing must satisfy 0 <= minimum <= maximum")
    return Params(
        photodir=photodir,
        gps_dir=photodir,
        photolist=photolist,
        tracks=tracks,
        distance=float(args.distance),
        geocode_timeout_seconds=float(args.geocode_timeout_seconds),
        geocode_pacing_min_seconds=float(args.geocode_pacing_min_seconds),
        geocode_pacing_max_seconds=float(args.geocode_pacing_max_seconds),
        file_filter=args.file_filter,
        getclearnames=bool(args.getclearnames),
        redo_reverse_geolocation=bool(args.redo_reverse_geolocation),
        overwrite_reverse_geolocation=bool(args.overwrite_reverse_geolocation),
        photos=args.photos,
        photonames=args.photonames,
        ignorejson=bool(args.ignorejson),
        debug=bool(args.debug),
        sort_date_sections_by_tracks=bool(args.sort_date_sections_by_tracks),
        merge_tracks=merge_tracks,
        merge_media=merge_media,
        infer_gps_from_tracks=bool(args.infer_gps_from_tracks),
        migrate_media_sidecars=bool(args.migrate_media_sidecars),
    )


def params_from_options(
    photodir: Path | str,
    *,
    photolist: Path | str | None = None,
    tracks: Path | str | None = None,
    distance: float = 150.0,
    geocode_timeout_seconds: float = 10.0,
    geocode_pacing_min_seconds: float = 1.0,
    geocode_pacing_max_seconds: float = 5.0,
    file_filter: str = "ALL",
    getclearnames: bool = False,
    redo_reverse_geolocation: bool = False,
    overwrite_reverse_geolocation: bool = False,
    photos: Optional[str] = None,
    photonames: Optional[str] = None,
    ignorejson: bool = False,
    debug: bool = False,
    sort_date_sections_by_tracks: bool = False,
    merge_tracks: Path | str | None = None,
    merge_media: list[Path | str] | tuple[Path | str, ...] | str | None = None,
    infer_gps_from_tracks: bool = True,
    migrate_media_sidecars: bool = False,
    media_map_options: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Params:
    """Build validated parameters for direct in-process execution."""
    photo_dir_path = Path(photodir).expanduser().resolve()
    if not photo_dir_path.is_dir():
        raise ValueError(f"photodir is not a directory: {photo_dir_path}")

    photo_list_path = Path(photolist).expanduser() if photolist is not None else (photo_dir_path / "photos.lst")
    if not photo_list_path.is_absolute():
        photo_list_path = (photo_dir_path / photo_list_path).resolve(strict=False)
    else:
        photo_list_path = photo_list_path.resolve(strict=False)

    tracks_path: Optional[Path]
    if tracks is None:
        tracks_path = None
    else:
        tracks_path = Path(tracks).expanduser()
        if not tracks_path.is_absolute():
            tracks_path = (photo_dir_path / tracks_path).resolve(strict=False)
        else:
            tracks_path = tracks_path.resolve(strict=False)
        if not tracks_path.is_file():
            raise ValueError(f"tracks file is not a file: {tracks_path}")
    if merge_tracks is None:
        merge_tracks_path = None
    else:
        merge_tracks_path = Path(merge_tracks).expanduser()
        if not merge_tracks_path.is_absolute():
            merge_tracks_path = (photo_dir_path / merge_tracks_path).resolve(strict=False)
        else:
            merge_tracks_path = merge_tracks_path.resolve(strict=False)
        if not merge_tracks_path.is_file():
            raise ValueError(f"merge tracks file is not a file: {merge_tracks_path}")
    merge_media_paths: tuple[Path, ...]
    if merge_media is None:
        merge_media_paths = ()
    elif isinstance(merge_media, str):
        merge_media_paths = tuple(
            (photo_dir_path / item.strip()).resolve(strict=False)
            if not Path(item.strip()).expanduser().is_absolute()
            else Path(item.strip()).expanduser().resolve(strict=False)
            for item in merge_media.split(",")
            if item.strip()
        )
    else:
        resolved_media_paths = []
        for media_item in merge_media:
            media_path = Path(media_item).expanduser()
            if not media_path.is_absolute():
                media_path = (photo_dir_path / media_path).resolve(strict=False)
            else:
                media_path = media_path.resolve(strict=False)
            resolved_media_paths.append(media_path)
        merge_media_paths = tuple(resolved_media_paths)
    for media_path in merge_media_paths:
        if not media_path.is_file():
            raise ValueError(f"merge media file is not a file: {media_path}")

    normalized_filter = str(file_filter).upper()
    if normalized_filter not in FILE_FILTERS:
        raise ValueError(f"file_filter must be one of {sorted(FILE_FILTERS)}: {normalized_filter}")
    if float(distance) < 0:
        raise ValueError(f"distance must be non-negative: {distance}")
    if float(geocode_timeout_seconds) <= 0:
        raise ValueError("geocode_timeout_seconds must be positive")
    if float(geocode_pacing_min_seconds) < 0 or float(geocode_pacing_max_seconds) < float(geocode_pacing_min_seconds):
        raise ValueError("geocode pacing must satisfy 0 <= minimum <= maximum")

    return Params(
        photodir=photo_dir_path,
        gps_dir=photo_dir_path,
        photolist=photo_list_path,
        tracks=tracks_path,
        distance=float(distance),
        geocode_timeout_seconds=float(geocode_timeout_seconds),
        geocode_pacing_min_seconds=float(geocode_pacing_min_seconds),
        geocode_pacing_max_seconds=float(geocode_pacing_max_seconds),
        file_filter=normalized_filter,
        getclearnames=bool(getclearnames),
        redo_reverse_geolocation=bool(redo_reverse_geolocation),
        overwrite_reverse_geolocation=bool(overwrite_reverse_geolocation),
        photos=photos,
        photonames=photonames,
        ignorejson=bool(ignorejson),
        debug=bool(debug),
        sort_date_sections_by_tracks=bool(sort_date_sections_by_tracks),
        merge_tracks=merge_tracks_path,
        merge_media=merge_media_paths,
        infer_gps_from_tracks=bool(infer_gps_from_tracks),
        migrate_media_sidecars=bool(migrate_media_sidecars),
        media_map_options=dict(media_map_options) if media_map_options is not None else None,
        progress_callback=progress_callback,
    )


def read_mdls_raw(file_path: Path, metadata_key: str) -> Optional[str]:
    """Read one raw metadata value using mdls."""
    command = ["mdls", "-raw", "-name", metadata_key, str(file_path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    value = result.stdout.strip()
    if value in {"", "(null)"}:
        return None
    return value


def normalize_datetime_timezone(value: datetime) -> datetime:
    """Return a timezone-aware datetime normalized to the local timezone."""
    if LOCAL_TIMEZONE is None:
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=LOCAL_TIMEZONE)
    return value.astimezone(LOCAL_TIMEZONE)


def read_mdls_float(file_path: Path, metadata_key: str) -> Optional[float]:
    """Read one mdls metadata value as float."""
    raw_value = read_mdls_raw(file_path, metadata_key)
    if raw_value is None:
        return None

    normalized = raw_value.strip().strip("\"'")
    if "," in normalized and "." not in normalized:
        normalized = normalized.replace(",", ".")

    try:
        return float(normalized)
    except ValueError:
        return None


def read_mdls_float_details(file_path: Path, metadata_key: str) -> tuple[Optional[float], Optional[str]]:
    """Read one mdls metadata value as float and also return the raw value."""
    raw_value = read_mdls_raw(file_path, metadata_key)
    if raw_value is None:
        return None, None

    normalized = raw_value.strip().strip("\"'")
    if "," in normalized and "." not in normalized:
        normalized = normalized.replace(",", ".")

    try:
        return float(normalized), raw_value
    except ValueError:
        return None, raw_value


def read_mdls_gps_pair(file_path: Path) -> tuple[Optional[float], Optional[float]]:
    """Read latitude and longitude from mdls metadata."""
    latitude = read_mdls_float(file_path, "kMDItemLatitude")
    longitude = read_mdls_float(file_path, "kMDItemLongitude")
    if latitude is not None and longitude is not None:
        return latitude, longitude

    raw_coordinates = read_mdls_raw(file_path, "kMDItemGPSCoordinates")
    if raw_coordinates is None:
        return latitude, longitude

    tokens = re.findall(r"[-+]?\d+(?:[.,]\d+)?", raw_coordinates)
    if len(tokens) < 2:
        return latitude, longitude

    try:
        return float(tokens[0].replace(",", ".")), float(tokens[1].replace(",", "."))
    except ValueError:
        return latitude, longitude


def get_photo_gps(file_path: Path) -> tuple[Optional[float], Optional[float]]:
    """Return GPS coordinates using mdls first and exiftool as fallback."""
    latitude, longitude = read_mdls_gps_pair(file_path)
    if latitude is not None and longitude is not None:
        return latitude, longitude

    exif_latitude, exif_longitude = read_exiftool_gps_pair(file_path)
    if exif_latitude is not None and exif_longitude is not None:
        return exif_latitude, exif_longitude

    if not is_exiftool_available():
        warn_exiftool_missing_once()
    return latitude, longitude


def read_mdls_gps_pair_with_debug(file_path: Path) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    """Read GPS values and return debug details about the lookup path."""
    latitude, latitude_raw = read_mdls_float_details(file_path, "kMDItemLatitude")
    longitude, longitude_raw = read_mdls_float_details(file_path, "kMDItemLongitude")
    debug_info: dict[str, Any] = {
        "latitude_raw": latitude_raw,
        "longitude_raw": longitude_raw,
        "gps_coordinates_raw": None,
        "source": None,
    }

    if latitude is not None and longitude is not None:
        debug_info["source"] = "kMDItemLatitude/kMDItemLongitude"
        return latitude, longitude, debug_info

    raw_coordinates = read_mdls_raw(file_path, "kMDItemGPSCoordinates")
    debug_info["gps_coordinates_raw"] = raw_coordinates
    if raw_coordinates is None:
        debug_info["source"] = "missing"
        return latitude, longitude, debug_info

    tokens = re.findall(r"[-+]?\d+(?:[.,]\d+)?", raw_coordinates)
    debug_info["gps_coordinate_tokens"] = tokens
    if len(tokens) < 2:
        debug_info["source"] = "unparseable kMDItemGPSCoordinates"
        return latitude, longitude, debug_info

    try:
        parsed_latitude = float(tokens[0].replace(",", "."))
        parsed_longitude = float(tokens[1].replace(",", "."))
        debug_info["source"] = "kMDItemGPSCoordinates"
        return parsed_latitude, parsed_longitude, debug_info
    except ValueError:
        debug_info["source"] = "invalid numeric GPSCoordinates"
        return latitude, longitude, debug_info


def is_exiftool_available() -> bool:
    """Return True when exiftool is available on PATH."""
    return shutil.which("exiftool") is not None


def warn_exiftool_missing_once() -> None:
    """Print a one-time warning when exiftool fallback is unavailable."""
    global EXIFTOOL_WARNING_SHOWN
    if EXIFTOOL_WARNING_SHOWN:
        return
    EXIFTOOL_WARNING_SHOWN = True
    print(
        "Warning: mdls metadata was incomplete for at least one photo and optional "
        "'exiftool' is not installed, so GPS/date fallback data may be missing.",
        file=sys.stderr,
        flush=True,
    )


def parse_exiftool_float(value: object) -> Optional[float]:
    """Parse a numeric exiftool value into float."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def read_exiftool_json(file_path: Path) -> Optional[dict[str, Any]]:
    """Read metadata from exiftool JSON output when available."""
    if not is_exiftool_available():
        return None

    command = [
        "exiftool",
        "-j",
        "-n",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSPosition",
        "-DateTimeOriginal",
        "-CreationDate",
        "-GPSDateTime",
        "-GPSDateStamp",
        "-GPSTimeStamp",
        "-MediaCreateDate",
        "-CreateDate",
        str(file_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None

    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return payload[0]


def read_exiftool_gps_pair(file_path: Path) -> tuple[Optional[float], Optional[float]]:
    """Read latitude and longitude from exiftool metadata."""
    metadata = read_exiftool_json(file_path)
    if not metadata:
        return None, None

    latitude = parse_exiftool_float(metadata.get("GPSLatitude"))
    longitude = parse_exiftool_float(metadata.get("GPSLongitude"))
    if latitude is not None and longitude is not None:
        return latitude, longitude

    gps_position = metadata.get("GPSPosition")
    if gps_position is None:
        return latitude, longitude

    tokens = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(gps_position))
    if len(tokens) < 2:
        return latitude, longitude

    try:
        return float(tokens[0].replace(",", ".")), float(tokens[1].replace(",", "."))
    except ValueError:
        return latitude, longitude


def read_exiftool_gps_pair_with_debug(file_path: Path) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    """Read GPS values via exiftool and return debug details."""
    debug_info: dict[str, Any] = {"available": is_exiftool_available()}
    if not debug_info["available"]:
        return None, None, debug_info

    metadata = read_exiftool_json(file_path)
    debug_info["metadata_found"] = metadata is not None
    if not metadata:
        return None, None, debug_info

    debug_info["GPSLatitude"] = metadata.get("GPSLatitude")
    debug_info["GPSLongitude"] = metadata.get("GPSLongitude")
    debug_info["GPSPosition"] = metadata.get("GPSPosition")

    latitude = parse_exiftool_float(metadata.get("GPSLatitude"))
    longitude = parse_exiftool_float(metadata.get("GPSLongitude"))
    if latitude is not None and longitude is not None:
        debug_info["source"] = "exiftool GPSLatitude/GPSLongitude"
        return latitude, longitude, debug_info

    gps_position = metadata.get("GPSPosition")
    if gps_position is None:
        debug_info["source"] = "missing"
        return latitude, longitude, debug_info

    tokens = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(gps_position))
    debug_info["gps_position_tokens"] = tokens
    if len(tokens) < 2:
        debug_info["source"] = "unparseable GPSPosition"
        return latitude, longitude, debug_info

    try:
        parsed_latitude = float(tokens[0].replace(",", "."))
        parsed_longitude = float(tokens[1].replace(",", "."))
        debug_info["source"] = "exiftool GPSPosition"
        return parsed_latitude, parsed_longitude, debug_info
    except ValueError:
        debug_info["source"] = "invalid numeric GPSPosition"
        return latitude, longitude, debug_info


def parse_mdls_datetime(raw_value: str) -> Optional[datetime]:
    """Parse common mdls date string variants."""
    cleaned = raw_value.strip().strip("\"'")
    known_formats = [
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f %z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]

    for date_format in known_formats:
        try:
            return normalize_datetime_timezone(datetime.strptime(cleaned, date_format))
        except ValueError:
            continue

    try:
        return normalize_datetime_timezone(datetime.fromisoformat(cleaned.replace("Z", "+00:00")))
    except ValueError:
        return None


def get_photo_datetime(file_path: Path) -> datetime:
    """Return the best available photo timestamp."""
    exif_datetime = read_exiftool_datetime(file_path)
    if exif_datetime is not None:
        return exif_datetime

    for metadata_key in ("kMDItemContentCreationDate", "kMDItemFSCreationDate"):
        raw_value = read_mdls_raw(file_path, metadata_key)
        if raw_value:
            parsed = parse_mdls_datetime(raw_value)
            if parsed is not None:
                return parsed

    if not is_exiftool_available():
        warn_exiftool_missing_once()

    file_stat = file_path.stat()
    birthtime = getattr(file_stat, "st_birthtime", None)
    if birthtime is not None:
        return datetime.fromtimestamp(birthtime, tz=LOCAL_TIMEZONE)
    return datetime.fromtimestamp(file_stat.st_mtime, tz=LOCAL_TIMEZONE)


def get_photo_datetime_with_debug(file_path: Path) -> tuple[datetime, dict[str, Any]]:
    """Return the best timestamp and describe how it was selected."""
    debug_info: dict[str, Any] = {"candidates": []}

    exif_datetime, exif_debug = read_exiftool_datetime_with_debug(file_path)
    debug_info["exiftool"] = exif_debug
    if exif_datetime is not None:
        debug_info["selected_source"] = f"exiftool:{exif_debug.get('selected_source')}"
        return exif_datetime, debug_info

    for metadata_key in ("kMDItemContentCreationDate", "kMDItemFSCreationDate"):
        raw_value = read_mdls_raw(file_path, metadata_key)
        parsed = parse_mdls_datetime(raw_value) if raw_value else None
        debug_info["candidates"].append(
            {
                "source": metadata_key,
                "raw": raw_value,
                "parsed": parsed.isoformat() if parsed is not None else None,
            }
        )
        if parsed is not None:
            debug_info["selected_source"] = metadata_key
            return parsed, debug_info

    if not is_exiftool_available():
        warn_exiftool_missing_once()

    file_stat = file_path.stat()
    birthtime = getattr(file_stat, "st_birthtime", None)
    if birthtime is not None:
        selected = datetime.fromtimestamp(birthtime, tz=LOCAL_TIMEZONE)
        debug_info["selected_source"] = "st_birthtime"
        debug_info["st_birthtime"] = birthtime
        return selected, debug_info

    selected = datetime.fromtimestamp(file_stat.st_mtime, tz=LOCAL_TIMEZONE)
    debug_info["selected_source"] = "st_mtime"
    debug_info["st_mtime"] = file_stat.st_mtime
    return selected, debug_info


def parse_exiftool_datetime(raw_value: object) -> Optional[datetime]:
    """Parse a datetime string emitted by exiftool."""
    if raw_value is None:
        return None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None

    normalized = re.sub(r"^(\d{4}):(\d{2}):(\d{2})(?=\s)", r"\1-\2-\3", cleaned)
    known_formats = [
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for date_format in known_formats:
        try:
            return normalize_datetime_timezone(datetime.strptime(normalized, date_format))
        except ValueError:
            continue

    try:
        return normalize_datetime_timezone(datetime.fromisoformat(normalized.replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_exiftool_gps_datetime(raw_value: object) -> Optional[datetime]:
    """Parse an EXIF GPS timestamp as UTC and normalize it to local time."""
    if raw_value is None:
        return None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None
    if not re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", cleaned, flags=re.IGNORECASE):
        cleaned = f"{cleaned}Z"
    return parse_exiftool_datetime(cleaned)


def read_gps_datetime_from_exiftool_metadata(
    metadata: dict[str, Any],
) -> tuple[Optional[datetime], Optional[str], object]:
    """Return a UTC GPS datetime plus its ExifTool source and raw value."""
    raw_composite = metadata.get("GPSDateTime")
    parsed = parse_exiftool_gps_datetime(raw_composite)
    if parsed is not None:
        return parsed, "GPSDateTime", raw_composite

    raw_date = metadata.get("GPSDateStamp")
    raw_time = metadata.get("GPSTimeStamp")
    date_match = re.search(r"(\d{4})[:/-](\d{2})[:/-](\d{2})", str(raw_date or ""))
    time_parts = re.findall(r"\d+(?:\.\d+)?", str(raw_time or ""))
    if date_match is None or len(time_parts) < 3:
        return None, None, {"GPSDateStamp": raw_date, "GPSTimeStamp": raw_time}
    combined = (
        f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)} "
        f"{int(float(time_parts[0])):02d}:{int(float(time_parts[1])):02d}:{float(time_parts[2]):06.3f}Z"
    )
    parsed = parse_exiftool_gps_datetime(combined)
    return parsed, "GPSDateStamp+GPSTimeStamp" if parsed is not None else None, {
        "GPSDateStamp": raw_date,
        "GPSTimeStamp": raw_time,
    }


def read_exiftool_datetime(file_path: Path) -> Optional[datetime]:
    """Read the preferred datetime from exiftool metadata."""
    metadata = read_exiftool_json(file_path)
    if not metadata:
        return None

    for key in ("DateTimeOriginal", "CreationDate"):
        parsed = parse_exiftool_datetime(metadata.get(key))
        if parsed is not None:
            return parsed
    parsed, _source, _raw = read_gps_datetime_from_exiftool_metadata(metadata)
    if parsed is not None:
        return parsed
    for key in ("MediaCreateDate", "CreateDate"):
        parsed = parse_exiftool_datetime(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def read_exiftool_datetime_with_debug(file_path: Path) -> tuple[Optional[datetime], dict[str, Any]]:
    """Read datetime via exiftool and return debug details."""
    debug_info: dict[str, Any] = {"available": is_exiftool_available(), "candidates": []}
    if not debug_info["available"]:
        return None, debug_info

    metadata = read_exiftool_json(file_path)
    debug_info["metadata_found"] = metadata is not None
    if not metadata:
        return None, debug_info

    for key in ("DateTimeOriginal", "CreationDate"):
        raw_value = metadata.get(key)
        parsed = parse_exiftool_datetime(raw_value)
        debug_info["candidates"].append(
            {
                "source": key,
                "raw": raw_value,
                "parsed": parsed.isoformat() if parsed is not None else None,
            }
        )
        if parsed is not None:
            debug_info["selected_source"] = key
            return parsed, debug_info

    gps_datetime, gps_source, gps_raw = read_gps_datetime_from_exiftool_metadata(metadata)
    debug_info["candidates"].append(
        {
            "source": gps_source or "GPSDateTime",
            "raw": gps_raw,
            "parsed": gps_datetime.isoformat() if gps_datetime is not None else None,
        }
    )
    if gps_datetime is not None:
        debug_info["selected_source"] = gps_source
        return gps_datetime, debug_info

    for key in ("MediaCreateDate", "CreateDate"):
        raw_value = metadata.get(key)
        parsed = parse_exiftool_datetime(raw_value)
        debug_info["candidates"].append(
            {
                "source": key,
                "raw": raw_value,
                "parsed": parsed.isoformat() if parsed is not None else None,
            }
        )
        if parsed is not None:
            debug_info["selected_source"] = key
            return parsed, debug_info

    debug_info["selected_source"] = "missing"
    return None, debug_info


def placemark_place_details(placemark: object) -> dict[str, Any]:
    """Extract normalized place fields from a CoreLocation placemark."""
    def get_value(attribute_name: str) -> Any:
        attribute = getattr(placemark, attribute_name, None)
        if attribute is None:
            return None
        return attribute() if callable(attribute) else attribute

    def clean_text(value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    areas_of_interest = get_value("areasOfInterest")
    area_names: list[str] = []
    if areas_of_interest:
        try:
            for item in areas_of_interest:
                cleaned = clean_text(item)
                if cleaned:
                    area_names.append(cleaned)
        except Exception:
            area_names = []

    return {
        "name": clean_text(get_value("name")),
        "locality": clean_text(get_value("locality")),
        "subLocality": clean_text(get_value("subLocality")),
        "administrativeArea": clean_text(get_value("administrativeArea")),
        "areasOfInterest": area_names,
    }


def build_place_name_from_details(details: Optional[dict[str, Any]]) -> Optional[str]:
    """Build the table/slideshow place label from normalized place fields."""
    if not isinstance(details, dict):
        return None

    city = str(details.get("locality") or "").strip()
    sublocality = str(details.get("subLocality") or "").strip()
    administrative_area = str(details.get("administrativeArea") or "").strip()
    name = str(details.get("name") or "").strip()
    areas = details.get("areasOfInterest")
    area_text = ""
    if isinstance(areas, list):
        area_text = ", ".join(str(item).strip() for item in areas if str(item).strip())
    elif areas:
        area_text = str(areas).strip()

    location_parts = []
    if city:
        location_parts.append(city)
    if sublocality and sublocality != city:
        if location_parts:
            location_parts[-1] = f"{location_parts[-1]}-{sublocality}"
        else:
            location_parts.append(sublocality)
    if administrative_area and administrative_area not in {city, sublocality}:
        if location_parts:
            location_parts[-1] = f"{location_parts[-1]} ({administrative_area})"
        else:
            location_parts.append(administrative_area)

    primary = location_parts[0] if location_parts else ""
    secondary = name or area_text
    if primary and secondary and secondary != primary:
        return f"{primary}, {secondary}"
    if primary:
        return primary
    if secondary:
        return secondary
    return None


def build_place_name_from_placemark(placemark: object) -> Optional[str]:
    """Build a concise location label from a placemark."""
    return build_place_name_from_details(placemark_place_details(placemark))


def reverse_geocode_location_details_with_debug(
    latitude: float,
    longitude: float,
    timeout_seconds: float = 10.0,
) -> tuple[Optional[str], Optional[dict[str, Any]], dict[str, Any]]:
    """Reverse-geocode one coordinate pair into place text/details with debug details."""
    check_cancelled()
    debug_info: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "attempts": [],
    }
    if not CORELOCATION_AVAILABLE:
        debug_info["status"] = "corelocation_unavailable"
        return None, None, debug_info

    location = CLLocation.alloc().initWithLatitude_longitude_(latitude, longitude)
    retry_delays = [0.4, 1.2, 2.5]

    for attempt_index in range(len(retry_delays) + 1):
        geocoder = CLGeocoder.alloc().init()
        attempt_info: dict[str, Any] = {"attempt": attempt_index + 1}
        state: dict[str, Any] = {"done": False, "place": None, "details": None, "error": None, "placemark_count": 0}

        def completion_handler(placemarks, error) -> None:
            try:
                if error is not None:
                    state["error"] = str(error)
                if placemarks:
                    try:
                        state["placemark_count"] = len(placemarks)
                    except Exception:
                        state["placemark_count"] = 0
                if error is None and placemarks and len(placemarks) > 0:
                    details = placemark_place_details(placemarks[0])
                    state["details"] = details
                    state["place"] = build_place_name_from_details(details)
            finally:
                state["done"] = True

        geocoder.reverseGeocodeLocation_completionHandler_(location, completion_handler)

        deadline = time.monotonic() + timeout_seconds
        while not state["done"] and time.monotonic() < deadline:
            check_cancelled()
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))

        if not state["done"]:
            geocoder.cancelGeocode()
            attempt_info["timeout"] = True
        else:
            attempt_info["timeout"] = False

        attempt_info["error"] = state["error"]
        attempt_info["placemark_count"] = state["placemark_count"]
        attempt_info["place"] = state["place"]
        attempt_info["place_details"] = state["details"]
        debug_info["attempts"].append(attempt_info)

        if state["place"]:
            debug_info["status"] = "success"
            debug_info["place"] = str(state["place"])
            debug_info["place_details"] = state["details"]
            return str(state["place"]), state["details"], debug_info

        if attempt_index < len(retry_delays):
            sleep_with_cancel(retry_delays[attempt_index])

    debug_info["status"] = "failed"
    return None, None, debug_info


def reverse_geocode_location_details(
    latitude: float,
    longitude: float,
    timeout_seconds: float = 10.0,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Reverse-geocode one coordinate pair into place text and structured fields."""
    place, details, _ = reverse_geocode_location_details_with_debug(latitude, longitude, timeout_seconds=timeout_seconds)
    return place, details


def reverse_geocode_location_name_with_debug(
    latitude: float,
    longitude: float,
    timeout_seconds: float = 10.0,
) -> tuple[Optional[str], dict[str, Any]]:
    """Reverse-geocode one coordinate pair into a human-readable place name with debug details."""
    place, _details, debug = reverse_geocode_location_details_with_debug(latitude, longitude, timeout_seconds=timeout_seconds)
    return place, debug


def reverse_geocode_location_name(latitude: float, longitude: float, timeout_seconds: float = 10.0) -> Optional[str]:
    """Reverse-geocode one coordinate pair into a human-readable place name."""
    place, _details = reverse_geocode_location_details(latitude, longitude, timeout_seconds=timeout_seconds)
    return place


def sleep_between_geocode_requests(minimum_seconds=GEOCODE_PACING_MIN_SECONDS, maximum_seconds=GEOCODE_PACING_MAX_SECONDS) -> None:
    """Wait a randomized interval between geocode requests."""
    sleep_with_cancel(random.uniform(float(minimum_seconds), float(maximum_seconds)))


def format_german_date(value: datetime) -> str:
    """Return a date in German weekday form."""
    return f"{GERMAN_WEEKDAYS[value.weekday()]}, {value.strftime('%d.%m.%Y')}"


def emit_output_line(screen_line: str, file_line: str, output_file: TextIO) -> None:
    """Write one line immediately to stdout and the output file."""
    print(screen_line, flush=True)
    output_file.write(file_line + "\n")
    output_file.flush()


def parse_track_start_time(raw_value: object) -> Optional[datetime]:
    """Parse the start_time field from a track summary JSON file."""
    if not isinstance(raw_value, str):
        return None

    cleaned = raw_value.strip()
    if not cleaned:
        return None

    for date_format in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return normalize_datetime_timezone(datetime.strptime(cleaned, date_format))
        except ValueError:
            continue
    return None


def track_map_sidecar_candidates(
    item: dict[str, Any],
    tracks_path: Path,
    photolist: Path,
) -> tuple[Path, ...]:
    """Return Standard then Time-Lapse sidecar candidates for one track."""
    result: list[Path] = []
    seen: set[Path] = set()
    for key in ("track_plot_image_filename", "track_plot_time_lapse_image_filename"):
        raw_value = item.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        raw_path = Path(raw_value.strip()).expanduser()
        if raw_path.is_absolute():
            image_candidates = [raw_path]
        else:
            image_candidates = [
                photolist.parent / raw_path,
                tracks_path.parent / raw_path,
                tracks_path.parent / raw_path.name,
            ]
        for image_path in image_candidates:
            sidecar_path = image_path.with_suffix(".json").resolve(strict=False)
            if sidecar_path not in seen:
                seen.add(sidecar_path)
                result.append(sidecar_path)
    return tuple(result)


def maybe_shorten_output_path(path_text: object, photolist: Path) -> Optional[str]:
    """Shorten a path to basename when it points into the photolist directory."""
    if not isinstance(path_text, str):
        return None

    cleaned = path_text.strip()
    if not cleaned:
        return None

    candidate = Path(cleaned).expanduser()
    if candidate.is_absolute():
        try:
            if candidate.resolve().parent == photolist.parent.resolve():
                return candidate.name
        except OSError:
            pass
    return cleaned


def infer_overview_image_from_tracks_path(tracks_path: Path, photolist: Path) -> Optional[str]:
    """Infer the overview image name from a gpx_tracks_table summary JSON path."""
    stem = tracks_path.stem
    if stem.endswith("-summary"):
        candidate = tracks_path.with_name(f"{stem[:-8]}{tracks_path.suffix}").with_suffix(".png")
        if candidate.exists():
            return maybe_shorten_output_path(str(candidate), photolist)
    metadata_candidate = tracks_path.with_name(f"{stem[:-8] if stem.endswith('-summary') else stem}.json")
    if metadata_candidate.exists() and metadata_candidate != tracks_path:
        try:
            payload = read_json_data(metadata_candidate)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            image_name = maybe_shorten_output_path(payload.get("output_image"), photolist)
            if image_name:
                return image_name
    return None


def normalize_filename_for_match(path_text: str) -> str:
    """Normalize a filename for robust matching across path and Unicode variants."""
    return unicodedata.normalize("NFC", Path(path_text.strip()).name)


def normalize_track_plot_filename_for_match(path_text: str) -> str:
    """Normalize track plot names across old/new numeric zero-padding widths."""
    name = normalize_filename_for_match(path_text)
    match = re.match(r"^0*(\d+)_(.+)$", name)
    if match:
        return f"{int(match.group(1))}_{match.group(2)}"
    return name


def _optional_float(value: object) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coordinate_pair(value: object) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(value, dict):
        return None, None
    latitude = _optional_float(value.get("lat", value.get("latitude")))
    longitude = _optional_float(value.get("lon", value.get("longitude")))
    return latitude, longitude


def is_resolved_place_name(place: Optional[str]) -> bool:
    """Return True when a place string is a resolved human-readable place name."""
    return bool(place and place not in {PLACE_NOT_AVAILABLE, PLACE_NOT_REQUESTED, PLACE_FAILED})


def needs_place_repair(record: PhotoRecord) -> bool:
    """Return True when a record has GPS coordinates but no resolved place."""
    return record.latitude is not None and record.longitude is not None and not is_resolved_place_name(record.place)


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two coordinates in meters."""
    earth_radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * earth_radius_m * math.atan2(math.sqrt(haversine), math.sqrt(1.0 - haversine))


def assign_adjacent_day_track(
    record: PhotoRecord,
    tracks_in_order: list[TrackInfo],
) -> Optional[AdjacentDayAssignment]:
    """Assign media on a trackless date to a nearby previous/next stage."""
    record_day = record.photo_datetime.date()
    if any(track.start_time.date() == record_day for track in tracks_in_order):
        return None
    has_gps = record.latitude is not None and record.longitude is not None
    candidates: list[tuple[float, int, int, AdjacentDayAssignment]] = []
    for order_index, track in enumerate(tracks_in_order):
        day_delta = (track.start_time.date() - record_day).days
        if day_delta == 1:
            relation = "before"
            relation_priority = 0
            endpoint = (track.start_latitude, track.start_longitude)
        elif day_delta == -1:
            relation = "after"
            relation_priority = 1
            endpoint = (track.end_latitude, track.end_longitude)
        else:
            continue
        distance = None
        if has_gps:
            if (
                track.length_km is None
                or track.length_km <= 0.0
                or endpoint[0] is None
                or endpoint[1] is None
            ):
                continue
            distance = distance_meters(
                float(record.latitude),
                float(record.longitude),
                float(endpoint[0]),
                float(endpoint[1]),
            )
            if distance > track.length_km * 1000.0 * ADJACENT_TRACK_RADIUS_FRACTION:
                continue
        assignment = AdjacentDayAssignment(relation, track, distance)
        candidates.append(
            (
                float("inf") if distance is None else distance,
                relation_priority,
                order_index,
                assignment,
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[:3])[3]


def find_nearby_known_place(
    latitude: float,
    longitude: float,
    known_places: list[KnownPlace],
    max_distance_m: float,
) -> tuple[Optional[KnownPlace], Optional[float]]:
    """Return the nearest reusable known place within the configured distance."""
    best_place: Optional[KnownPlace] = None
    best_distance: Optional[float] = None

    for known_place in known_places:
        current_distance = distance_meters(latitude, longitude, known_place.latitude, known_place.longitude)
        if current_distance > max_distance_m:
            continue
        if best_distance is None or current_distance < best_distance:
            best_place = known_place
            best_distance = current_distance

    return best_place, best_distance


def load_tracks_summary(tracks_path: Optional[Path], photolist: Path) -> Optional[TracksSummary]:
    """Load overview and track-per-day metadata from the tracks JSON file."""
    if tracks_path is None:
        return None

    payload = read_json_data(tracks_path)

    if not isinstance(payload, dict):
        raise ValueError(f"tracks file does not contain a JSON object: {tracks_path}")

    has_output_image = "output_image" in payload
    has_tracks = "tracks" in payload
    if not has_output_image and not has_tracks:
        available_keys = ", ".join(sorted(payload.keys()))
        raise ValueError(
            "tracks file does not look like a gpx_tracks_table.py summary JSON "
            f"(missing 'output_image' and 'tracks'): {tracks_path} | keys: {available_keys}"
        )

    overview_image = maybe_shorten_output_path(payload.get("output_image"), photolist)
    if overview_image is None:
        overview_image = infer_overview_image_from_tracks_path(tracks_path, photolist)
    ignored_photo_names: set[str] = set()
    if isinstance(payload.get("output_image"), str):
        ignored_photo_names.add(normalize_filename_for_match(str(payload["output_image"])))
    elif overview_image:
        ignored_photo_names.add(normalize_filename_for_match(overview_image))
    tracks_payload = payload.get("tracks")
    if tracks_payload is None:
        return TracksSummary(overview_image=overview_image, tracks=[], ignored_photo_names=ignored_photo_names)
    if not isinstance(tracks_payload, list):
        raise ValueError(f"tracks entry is not a list: {tracks_path}")

    tracks: list[TrackInfo] = []
    for item in tracks_payload:
        if not isinstance(item, dict):
            continue
        start_time = parse_track_start_time(item.get("start_time"))
        end_time = parse_track_start_time(item.get("end_time"))
        image_name = maybe_shorten_output_path(item.get("track_plot_image_filename"), photolist)
        try:
            original_sequence_number = int(item.get("original_sequence_number", item.get("nr", len(tracks) + 1)))
        except (TypeError, ValueError):
            original_sequence_number = len(tracks) + 1
        length_km = _optional_float(item.get("laenge_km", item.get("track_length_km")))
        start_latitude, start_longitude = _coordinate_pair(item.get("start_point"))
        end_latitude, end_longitude = _coordinate_pair(item.get("end_point"))
        track_name_value = item.get("track_name")
        track_name = track_name_value.strip() if isinstance(track_name_value, str) and track_name_value.strip() else None
        fingerprint_value = item.get("track_fingerprint")
        track_fingerprint = (
            fingerprint_value.strip()
            if isinstance(fingerprint_value, str) and fingerprint_value.strip()
            else None
        )
        if isinstance(item.get("track_plot_image_filename"), str):
            ignored_photo_names.add(normalize_filename_for_match(str(item["track_plot_image_filename"])))
        if start_time is None or not image_name:
            continue
        tracks.append(
            TrackInfo(
                start_time=start_time,
                track_plot_image_filename=image_name,
                original_sequence_number=original_sequence_number,
                length_km=length_km,
                start_latitude=start_latitude,
                start_longitude=start_longitude,
                end_latitude=end_latitude,
                end_longitude=end_longitude,
                end_time=end_time,
                track_name=track_name,
                track_fingerprint=track_fingerprint,
                map_sidecar_paths=track_map_sidecar_candidates(item, tracks_path, photolist),
            )
        )

    return TracksSummary(overview_image=overview_image, tracks=tracks, ignored_photo_names=ignored_photo_names)


class LazyTrackGpsResolver:
    """Infer media GPS from lazily loaded, fingerprint-matched track timelines."""

    def __init__(
        self,
        tracks_summary: Optional[TracksSummary],
        place_equivalence_m: float = DEFAULT_PLACE_GPS_EQUIVALENCE_M,
    ):
        self.tracks = list(tracks_summary.tracks) if tracks_summary is not None else []
        self.place_equivalence_m = max(0.0, float(place_equivalence_m))
        self._timeline_cache: dict[int, Optional[TrackTimeline]] = {}
        self._reference_records: list[tuple[datetime, float, float, str]] = []
        self._warned: set[str] = set()
        self.inferred_count = 0
        self.refreshed_count = 0
        self.cleared_count = 0

    @staticmethod
    def _is_track_inferred(record: PhotoRecord) -> bool:
        return record.gps_source == "track_time_interpolation"

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        print(f"Warning: {message}", flush=True)

    def _candidate_tracks(self, photo_datetime: datetime) -> list[TrackInfo]:
        candidates = []
        for track in self.tracks:
            if track.end_time is None or track.end_time < track.start_time:
                continue
            try:
                if track.start_time <= photo_datetime <= track.end_time:
                    candidates.append(track)
            except TypeError:
                continue
        return candidates

    def _remember_reference_record(self, record: PhotoRecord) -> None:
        """Retain authoritative media GPS as a spatial tie-breaker."""
        if (
            record.latitude is None
            or record.longitude is None
            or self._is_track_inferred(record)
        ):
            return
        reference = (
            record.photo_datetime,
            float(record.latitude),
            float(record.longitude),
            record.source_filename,
        )
        if reference not in self._reference_records:
            self._reference_records.append(reference)

    def _track_from_existing_inference(
        self,
        record: PhotoRecord,
        candidates: list[TrackInfo],
    ) -> Optional[TrackInfo]:
        if not self._is_track_inferred(record) or not isinstance(record.gps_inference, dict):
            return None
        fingerprint = str(record.gps_inference.get("track_fingerprint") or "")
        matches = [
            track for track in candidates
            if fingerprint and track.track_fingerprint == fingerprint
        ]
        return matches[0] if len(matches) == 1 else None

    def _track_from_nearby_media(
        self,
        photo_datetime: datetime,
        candidates: list[TrackInfo],
    ) -> tuple[Optional[TrackInfo], Optional[dict[str, Any]]]:
        """Resolve overlapping time intervals using nearby authoritative media GPS."""
        nearby = []
        for reference in self._reference_records:
            try:
                delta_seconds = abs((reference[0] - photo_datetime).total_seconds())
            except TypeError:
                continue
            if delta_seconds <= 2.0 * 60.0 * 60.0:
                nearby.append((delta_seconds, reference))
        if not nearby:
            return None, None
        nearby.sort(key=lambda item: item[0])
        nearby = nearby[:4]

        maximum_distance_m = 500.0
        scored = []
        for track in candidates:
            timeline = self._load_timeline(track)
            if timeline is None:
                continue
            distances = []
            best_reference = None
            for _delta_seconds, reference in nearby:
                reference_time, reference_latitude, reference_longitude, reference_name = reference
                try:
                    if not (track.start_time <= reference_time <= track.end_time):
                        continue
                except (TypeError, AttributeError):
                    continue
                position = self._interpolate(timeline, reference_time)
                if position is None:
                    continue
                distance = distance_meters(
                    position[0],
                    position[1],
                    reference_latitude,
                    reference_longitude,
                )
                distances.append(distance)
                if best_reference is None or distance < best_reference[0]:
                    best_reference = (distance, reference_name)
            if distances and best_reference is not None:
                scored.append((min(distances), track, best_reference[1]))

        eligible = [item for item in scored if item[0] <= maximum_distance_m]
        if len(eligible) != 1:
            return None, None
        distance, track, reference_name = eligible[0]
        return track, {
            "track_interval_disambiguated_by": reference_name,
            "track_interval_disambiguation_distance_m": round(distance, 3),
        }

    @staticmethod
    def _parse_timeline(metadata: object, source_path: Path) -> Optional[TrackTimeline]:
        if not isinstance(metadata, dict):
            return None
        raw_points = metadata.get("timed_track_points")
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            return None
        times: list[datetime] = []
        points: list[dict[str, Any]] = []
        for raw_point in raw_points:
            if not isinstance(raw_point, dict):
                return None
            try:
                latitude = float(raw_point["lat"])
                longitude = float(raw_point["lon"])
                point_time = normalize_datetime_timezone(datetime.fromisoformat(str(raw_point["time_iso"])))
                segment_index = int(raw_point.get("segment_index", 0) or 0)
            except (KeyError, TypeError, ValueError):
                return None
            if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
                return None
            if times and point_time < times[-1]:
                return None
            times.append(point_time)
            points.append(
                {
                    "lat": latitude,
                    "lon": longitude,
                    "time": point_time,
                    "segment_index": segment_index,
                    "estimated": bool(raw_point.get("estimated", False)),
                }
            )
        return TrackTimeline(tuple(times), tuple(points), source_path)

    def _load_timeline(self, track: TrackInfo) -> Optional[TrackTimeline]:
        cache_key = track.original_sequence_number
        if cache_key in self._timeline_cache:
            return self._timeline_cache[cache_key]
        timeline = None
        for sidecar_path in track.map_sidecar_paths:
            if not sidecar_path.is_file():
                continue
            try:
                metadata = read_json_data(sidecar_path)
            except (OSError, ValueError, TypeError):
                continue
            if not track.track_fingerprint or not isinstance(metadata, dict):
                continue
            if metadata.get("track_fingerprint") != track.track_fingerprint:
                continue
            timeline = self._parse_timeline(metadata, sidecar_path)
            if timeline is not None:
                break
        self._timeline_cache[cache_key] = timeline
        if timeline is None:
            self._warn_once(
                f"timeline:{cache_key}",
                f"Track #{cache_key} has no current timed map sidecar; run Generate and Update Maps to infer media GPS.",
            )
        return timeline

    @staticmethod
    def _interpolate(
        timeline: TrackTimeline,
        photo_datetime: datetime,
    ) -> Optional[tuple[float, float, dict[str, Any]]]:
        index = bisect.bisect_left(timeline.times, photo_datetime)
        if index < len(timeline.times) and timeline.times[index] == photo_datetime:
            point = timeline.points[index]
            return point["lat"], point["lon"], {
                "before_time_iso": point["time"].isoformat(),
                "after_time_iso": point["time"].isoformat(),
                "fraction": 0.0,
                "timing_estimated": bool(point["estimated"]),
            }
        if index <= 0 or index >= len(timeline.times):
            return None
        before = timeline.points[index - 1]
        after = timeline.points[index]
        elapsed = (after["time"] - before["time"]).total_seconds()
        if elapsed <= 0:
            return None
        fraction = (photo_datetime - before["time"]).total_seconds() / elapsed
        if before["segment_index"] != after["segment_index"]:
            selected = before if fraction <= 0.5 else after
            latitude, longitude = selected["lat"], selected["lon"]
        else:
            latitude = before["lat"] + (after["lat"] - before["lat"]) * fraction
            longitude_delta = ((after["lon"] - before["lon"] + 180.0) % 360.0) - 180.0
            longitude = ((before["lon"] + longitude_delta * fraction + 180.0) % 360.0) - 180.0
        return latitude, longitude, {
            "before_time_iso": before["time"].isoformat(),
            "after_time_iso": after["time"].isoformat(),
            "fraction": round(fraction, 9),
            "timing_estimated": bool(before["estimated"] or after["estimated"]),
        }

    @staticmethod
    def _clear_inferred_gps(record: PhotoRecord) -> None:
        record.latitude = None
        record.longitude = None
        record.place = None
        record.place_details = None
        record.gps_source = None
        record.gps_inference = None
        record.gps_updated = True

    def apply(self, record: PhotoRecord) -> bool:
        """Apply or refresh track-derived GPS, returning whether metadata changed."""
        was_inferred = self._is_track_inferred(record)
        if record.latitude is not None and record.longitude is not None and not was_inferred:
            self._remember_reference_record(record)
            return False

        candidates = self._candidate_tracks(record.photo_datetime)
        disambiguation_details = None
        if len(candidates) == 1:
            track = candidates[0]
        elif len(candidates) > 1:
            track = self._track_from_existing_inference(record, candidates)
            if track is None:
                track, disambiguation_details = self._track_from_nearby_media(
                    record.photo_datetime,
                    candidates,
                )
            if track is None:
                self._warn_once(
                    f"ambiguous:{record.source_filename}:{record.photo_datetime.isoformat()}",
                    f"{record.source_filename} falls inside multiple track intervals; GPS was not inferred.",
                )
        else:
            track = None
        if track is None:
            if was_inferred:
                self._clear_inferred_gps(record)
                self.cleared_count += 1
                return True
            return False

        previous_fingerprint = ""
        if was_inferred and isinstance(record.gps_inference, dict):
            previous_fingerprint = str(record.gps_inference.get("track_fingerprint", ""))
        if (
            was_inferred
            and record.latitude is not None
            and record.longitude is not None
            and previous_fingerprint
            and previous_fingerprint == track.track_fingerprint
        ):
            return False

        timeline = self._load_timeline(track)
        result = self._interpolate(timeline, record.photo_datetime) if timeline is not None else None
        if result is None:
            if was_inferred:
                self._clear_inferred_gps(record)
                self.cleared_count += 1
                return True
            return False

        latitude, longitude, details = result
        previous_latitude = record.latitude
        previous_longitude = record.longitude
        coordinates_changed = (
            previous_latitude is None
            or previous_longitude is None
            or not math.isclose(previous_latitude, latitude, abs_tol=1e-7)
            or not math.isclose(previous_longitude, longitude, abs_tol=1e-7)
        )
        displacement = (
            math.inf
            if previous_latitude is None or previous_longitude is None
            else distance_meters(
                float(previous_latitude),
                float(previous_longitude),
                latitude,
                longitude,
            )
        )
        if coordinates_changed and displacement > self.place_equivalence_m:
            record.place = None
            record.place_details = None
        record.latitude = latitude
        record.longitude = longitude
        record.gps_source = "track_time_interpolation"
        record.gps_inference = {
            "track_number": track.original_sequence_number,
            "track_name": track.track_name,
            "track_fingerprint": track.track_fingerprint,
            "track_map_sidecar": timeline.source_path.name,
            **details,
        }
        if disambiguation_details:
            record.gps_inference.update(disambiguation_details)
        record.gps_updated = True
        if was_inferred:
            self.refreshed_count += 1
        else:
            self.inferred_count += 1
        return True

    def emit_summary(self) -> None:
        if self.inferred_count or self.refreshed_count or self.cleared_count:
            print(
                "Track-time GPS inference: "
                f"{self.inferred_count} added, {self.refreshed_count} refreshed, "
                f"{self.cleared_count} cleared.",
                flush=True,
            )


def allowed_extensions_for_filter(file_filter: str) -> set[str]:
    """Return allowed media extensions for the selected file filter."""
    if file_filter == "IMAGE":
        return IMAGE_EXTENSIONS
    if file_filter == "VIDEO":
        return VIDEO_EXTENSIONS
    return IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def list_photo_files(photodir: Path, file_filter: str = "ALL") -> list[Path]:
    """Return supported media files in the input directory."""
    allowed_extensions = allowed_extensions_for_filter(file_filter)
    return sorted(
        [path for path in photodir.iterdir() if path.is_file() and path.suffix.lower() in allowed_extensions],
        key=lambda item: item.name.lower(),
    )


def _migration_backup_path(path: Path) -> Path:
    """Return an unused, clearly named preservation path for a legacy sidecar."""
    candidate = path.with_name(f"{path.stem}.legacy-sidecar.json")
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.legacy-sidecar-{index}.json")
        index += 1
    return candidate


def _read_sidecar_or_none(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = read_photo_metadata(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def migrate_media_sidecars(
    photodir: Path | str,
    *,
    getclearnames: bool = False,
    distance: float = 150.0,
    debug: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> MediaSidecarMigrationReport:
    """Migrate media JSON sidecars without losing valid cached place data.

    Legacy sidecars were named from the stem only, which lets ``photo.jpeg``
    and ``photo.mov`` collide.  This routine moves an unambiguous sidecar to
    the extension-aware name, preserves ambiguous files under a backup name,
    and creates sidecars for media that still have none.
    """
    project_dir = Path(photodir).expanduser().resolve()
    if not project_dir.is_dir():
        raise ValueError(f"photodir is not a directory: {project_dir}")

    media_files = list_photo_files(project_dir)
    report = MediaSidecarMigrationReport(project_dir, [], [], [], [])
    legacy_groups: dict[Path, list[Path]] = {}
    for media_path in media_files:
        legacy_groups.setdefault(legacy_media_sidecar_path(media_path), []).append(media_path)

    # A malformed canonical sidecar must never block migration or be reused.
    for media_path in media_files:
        canonical_path = media_sidecar_path(media_path)
        if canonical_path.exists() and not media_sidecar_matches_media(_read_sidecar_or_none(canonical_path), media_path):
            backup_path = _migration_backup_path(canonical_path)
            canonical_path.rename(backup_path)
            report.conflicts.append((canonical_path, backup_path))

    for legacy_path, candidates in legacy_groups.items():
        if not legacy_path.exists():
            continue
        payload = _read_sidecar_or_none(legacy_path)
        owners = [media_path for media_path in candidates if media_sidecar_matches_media(payload, media_path)]
        if len(owners) == 1:
            canonical_path = media_sidecar_path(owners[0])
            if canonical_path.exists():
                backup_path = _migration_backup_path(legacy_path)
                legacy_path.rename(backup_path)
                report.preserved.append((legacy_path, backup_path))
            else:
                legacy_path.rename(canonical_path)
                report.migrated.append((legacy_path, canonical_path))
            continue

        # Do not guess ownership when the sidecar is missing, malformed, or
        # identifies a media file not present in this project directory.
        backup_path = _migration_backup_path(legacy_path)
        legacy_path.rename(backup_path)
        report.conflicts.append((legacy_path, backup_path))

    geocode_cache: dict[tuple[float, float], tuple[Optional[str], Optional[dict[str, Any]]]] = {}
    known_places: list[KnownPlace] = []
    total_media = len(media_files)
    for index, media_path in enumerate(media_files, start=1):
        canonical_path = media_sidecar_path(media_path)
        if not canonical_path.exists():
            record = build_record_from_photo(
                media_path,
                getclearnames,
                geocode_cache,
                known_places,
                distance,
                debug,
            )
            write_record_json(record, set())
            report.regenerated.append(canonical_path)
        if progress_callback is not None:
            progress_callback(index, total_media, media_path.name)
    return report


def exclude_tracks_images(photo_files: list[Path], tracks_summary: Optional[TracksSummary]) -> list[Path]:
    """Exclude overview and track plot images referenced by the tracks summary."""
    if tracks_summary is None or not tracks_summary.ignored_photo_names:
        return photo_files
    return [
        photo_path
        for photo_path in photo_files
        if normalize_filename_for_match(photo_path.name) not in tracks_summary.ignored_photo_names
    ]


def parse_photo_number_list(photo_number_list: str, max_index: int) -> list[int]:
    """Parse a list of photo numbers and ranges into zero-based indices."""
    selected_indices: set[int] = set()

    for part in photo_number_list.split(","):
        token = part.strip()
        if not token:
            raise ValueError("empty item in --photos list")

        if "-" not in token:
            number = int(token)
            if number < 1 or number > max_index:
                raise ValueError(f"photo number out of range: {number}")
            selected_indices.add(number - 1)
            continue

        if token.count("-") != 1:
            raise ValueError(f"invalid range: {token}")

        start_text, end_text = token.split("-", 1)
        if not start_text.strip():
            raise ValueError(f"range start missing: {token}")

        start_number = int(start_text)
        if start_number < 1 or start_number > max_index:
            raise ValueError(f"photo number out of range: {start_number}")

        if end_text.strip():
            end_number = int(end_text)
            if end_number < 1 or end_number > max_index:
                raise ValueError(f"photo number out of range: {end_number}")
        else:
            end_number = max_index

        if end_number < start_number:
            raise ValueError(f"invalid descending range: {token}")

        for index in range(start_number - 1, end_number):
            selected_indices.add(index)

    return sorted(selected_indices)


def select_photo_files(photo_files: list[Path], photo_number_list: Optional[str]) -> list[Path]:
    """Filter the sorted input files to the requested photo numbers."""
    if not photo_number_list:
        return photo_files
    if not photo_files:
        return []

    selected_indices = parse_photo_number_list(photo_number_list, len(photo_files))
    return [photo_files[index] for index in selected_indices]


def parse_photo_name_list(photo_name_list: str) -> list[str]:
    """Parse a comma-separated list of photo names."""
    photo_names = [item.strip() for item in photo_name_list.split(",")]
    cleaned_names = [item for item in photo_names if item]
    if not cleaned_names:
        raise ValueError("no filenames provided in --photonames")
    return cleaned_names


def filter_photo_files_by_name(photo_files: list[Path], photo_name_list: Optional[str]) -> list[Path]:
    """Filter the sorted input files by explicit file name."""
    if not photo_name_list:
        return photo_files

    wanted_names = parse_photo_name_list(photo_name_list)
    photo_map = {photo_path.name: photo_path for photo_path in photo_files}
    missing_names = [name for name in wanted_names if name not in photo_map]
    if missing_names:
        raise ValueError(f"photo filename not found: {missing_names[0]}")
    return [photo_map[name] for name in wanted_names]


def get_json_path_for_photo(photo_path: Path) -> Path:
    """Return the sidecar JSON path for one photo."""
    return media_sidecar_path(photo_path)


def load_record_from_json(json_path: Path, photo_path: Path) -> Optional[PhotoRecord]:
    """Load cached metadata from a sidecar JSON file."""
    status, data, _reason = validate_media_sidecar(photo_path, json_path)
    if status != "available" or not isinstance(data, dict):
        return None
    return record_from_sidecar_payload(data, json_path, photo_path)


def record_from_sidecar_payload(data: dict[str, Any], json_path: Path, photo_path: Path) -> PhotoRecord:
    """Build a normalized record from an already validated media sidecar."""
    photo_datetime = normalize_datetime_timezone(parse_photo_datetime(data.get("datetime_iso")))

    latitude = data.get("latitude")
    longitude = data.get("longitude")
    place = data.get("place")
    place_details = data.get("place_details")
    if not isinstance(place_details, dict):
        top_level_details = {
            key: data.get(key)
            for key in ("name", "locality", "subLocality", "administrativeArea", "areasOfInterest")
            if key in data
        }
        place_details = top_level_details if top_level_details else None

    try:
        latitude_value = float(latitude) if latitude is not None else None
        longitude_value = float(longitude) if longitude is not None else None
    except (TypeError, ValueError):
        latitude_value = None
        longitude_value = None

    place_value = str(place).strip() if isinstance(place, str) and place.strip() else None
    if place_value is None and isinstance(place_details, dict):
        place_value = build_place_name_from_details(place_details)
    gps_source = data.get("gps_source")
    gps_inference = data.get("gps_inference")
    if not isinstance(gps_source, str) or not gps_source.strip():
        gps_source = None
    if not isinstance(gps_inference, dict):
        gps_inference = None
    return PhotoRecord(
        source_filename=photo_path.name,
        display_filename=json_path.name,
        photo_path=photo_path,
        json_path=json_path,
        photo_datetime=photo_datetime,
        latitude=latitude_value,
        longitude=longitude_value,
        place=place_value,
        place_details=place_details if isinstance(place_details, dict) else None,
        source="json",
        geocode_requested=False,
        place_updated=False,
        debug_info={"selected_source": "json"},
        gps_source=gps_source,
        gps_inference=gps_inference,
        raw_metadata=dict(data),
        datetime_source=str(data.get("datetime_source") or "sidecar"),
    )


def build_record_from_photo(
    photo_path: Path,
    getclearnames: bool,
    geocode_cache: dict[tuple[float, float], tuple[Optional[str], Optional[dict[str, Any]]]],
    known_places: list[KnownPlace],
    place_distance_m: float,
    debug: bool,
    geocode_timeout_seconds: float = 10.0,
    geocode_pacing_min_seconds: float = GEOCODE_PACING_MIN_SECONDS,
    geocode_pacing_max_seconds: float = GEOCODE_PACING_MAX_SECONDS,
) -> PhotoRecord:
    """Extract metadata from a photo and create a normalized record."""
    check_cancelled()
    debug_info: dict[str, Any] = {}

    if debug:
        latitude, longitude, gps_debug = read_mdls_gps_pair_with_debug(photo_path)
        if latitude is None or longitude is None:
            exif_latitude, exif_longitude, exif_gps_debug = read_exiftool_gps_pair_with_debug(photo_path)
            gps_debug["exiftool"] = exif_gps_debug
            if exif_latitude is not None and exif_longitude is not None:
                latitude, longitude = exif_latitude, exif_longitude
                gps_debug["source"] = f"fallback:{exif_gps_debug.get('source')}"
            elif not is_exiftool_available():
                warn_exiftool_missing_once()

        photo_datetime, datetime_debug = get_photo_datetime_with_debug(photo_path)
        debug_info["datetime"] = datetime_debug
        debug_info["gps"] = gps_debug
    else:
        latitude, longitude = read_mdls_gps_pair(photo_path)
        if latitude is None or longitude is None:
            exif_latitude, exif_longitude = read_exiftool_gps_pair(photo_path)
            if exif_latitude is not None and exif_longitude is not None:
                latitude, longitude = exif_latitude, exif_longitude
            elif not is_exiftool_available():
                warn_exiftool_missing_once()

        # Keep timestamp provenance in normal sidecars too. This uses the same
        # extraction calls as get_photo_datetime(), not a second metadata pass.
        photo_datetime, datetime_debug = get_photo_datetime_with_debug(photo_path)
        debug_info["datetime"] = datetime_debug

    place = None
    place_details = None

    if latitude is not None and longitude is not None and getclearnames:
        cache_key = (round(latitude, GEOCODE_ROUND_DIGITS), round(longitude, GEOCODE_ROUND_DIGITS))
        if cache_key not in geocode_cache:
            nearby_place, nearby_distance = find_nearby_known_place(latitude, longitude, known_places, place_distance_m)
            if nearby_place is not None:
                geocode_cache[cache_key] = (nearby_place.place, nearby_place.place_details)
                if debug:
                    debug_info["geocode"] = {
                        "cache_key": cache_key,
                        "reused_known_place": nearby_place.place,
                        "reused_distance_m": nearby_distance,
                        "requested": False,
                    }
            else:
                if debug:
                    place, place_details, geocode_debug = reverse_geocode_location_details_with_debug(
                        latitude, longitude, timeout_seconds=geocode_timeout_seconds
                    )
                    geocode_cache[cache_key] = (place, place_details)
                    debug_info["geocode"] = {"cache_key": cache_key, "requested": True, **geocode_debug}
                else:
                    geocode_cache[cache_key] = reverse_geocode_location_details(
                        latitude, longitude, timeout_seconds=geocode_timeout_seconds
                    )
                sleep_between_geocode_requests(geocode_pacing_min_seconds, geocode_pacing_max_seconds)
        elif debug:
            debug_info["geocode"] = {"cache_key": cache_key, "cached": True, "place": geocode_cache[cache_key][0]}
        place, place_details = geocode_cache[cache_key]
    elif debug:
        debug_info["geocode"] = {"requested": False, "place": None}

    datetime_source = None
    if isinstance(debug_info.get("datetime"), dict):
        datetime_source = debug_info["datetime"].get("selected_source")
    return PhotoRecord(
        source_filename=photo_path.name,
        display_filename=photo_path.name,
        photo_path=photo_path,
        json_path=get_json_path_for_photo(photo_path),
        photo_datetime=photo_datetime,
        latitude=latitude,
        longitude=longitude,
        place=place,
        place_details=place_details,
        source="photo",
        geocode_requested=getclearnames,
        place_updated=False,
        debug_info=debug_info or None,
        gps_source="embedded" if latitude is not None and longitude is not None else None,
        datetime_source=str(datetime_source) if datetime_source else None,
    )


def resolve_place_for_record(
    record: PhotoRecord,
    geocode_cache: dict[tuple[float, float], tuple[Optional[str], Optional[dict[str, Any]]]],
    known_places: list[KnownPlace],
    place_distance_m: float,
    debug: bool,
    geocode_timeout_seconds: float = 10.0,
    geocode_pacing_min_seconds: float = GEOCODE_PACING_MIN_SECONDS,
    geocode_pacing_max_seconds: float = GEOCODE_PACING_MAX_SECONDS,
) -> PhotoRecord:
    """Resolve a missing place for an existing record without changing other metadata."""
    check_cancelled()
    if record.latitude is None or record.longitude is None:
        record.geocode_requested = True
        if debug:
            debug_info = dict(record.debug_info or {})
            debug_info["geocode"] = {"requested": True, "skipped": "no_gps"}
            record.debug_info = debug_info
        return record

    debug_info = dict(record.debug_info or {})
    new_place, new_place_details, geocode_debug = resolve_place_for_coordinate(
        float(record.latitude),
        float(record.longitude),
        geocode_cache,
        known_places,
        place_distance_m,
        debug,
        geocode_timeout_seconds,
        geocode_pacing_min_seconds,
        geocode_pacing_max_seconds,
    )
    if debug and geocode_debug:
        debug_info["geocode"] = geocode_debug
    if is_resolved_place_name(new_place):
        record.place = str(new_place)
        record.place_details = new_place_details
        record.place_updated = True
    record.geocode_requested = True
    record.debug_info = debug_info or None
    return record


def resolve_place_for_coordinate(
    latitude: float,
    longitude: float,
    geocode_cache: dict[
        tuple[float, float],
        tuple[Optional[str], Optional[dict[str, Any]]],
    ],
    known_places: list[KnownPlace],
    place_distance_m: float,
    debug: bool,
    geocode_timeout_seconds: float = 10.0,
    geocode_pacing_min_seconds: float = GEOCODE_PACING_MIN_SECONDS,
    geocode_pacing_max_seconds: float = GEOCODE_PACING_MAX_SECONDS,
) -> tuple[Optional[str], Optional[dict[str, Any]], dict[str, Any]]:
    """Resolve one coordinate using the same cache and radius policy as media."""
    check_cancelled()
    cache_key = (
        round(float(latitude), GEOCODE_ROUND_DIGITS),
        round(float(longitude), GEOCODE_ROUND_DIGITS),
    )
    debug_info: dict[str, Any] = {"cache_key": cache_key}
    if cache_key not in geocode_cache:
        nearby_place, nearby_distance = find_nearby_known_place(
            float(latitude),
            float(longitude),
            known_places,
            place_distance_m,
        )
        if nearby_place is not None:
            geocode_cache[cache_key] = (
                nearby_place.place,
                nearby_place.place_details,
            )
            debug_info.update(
                {
                    "reused_known_place": nearby_place.place,
                    "reused_distance_m": nearby_distance,
                    "requested": False,
                }
            )
        else:
            if debug:
                place, place_details, request_debug = (
                    reverse_geocode_location_details_with_debug(
                        float(latitude),
                        float(longitude),
                        timeout_seconds=geocode_timeout_seconds,
                    )
                )
                debug_info.update({"requested": True, **request_debug})
            else:
                place, place_details = reverse_geocode_location_details(
                    float(latitude),
                    float(longitude),
                    timeout_seconds=geocode_timeout_seconds,
                )
            geocode_cache[cache_key] = (place, place_details)
            sleep_between_geocode_requests(
                geocode_pacing_min_seconds,
                geocode_pacing_max_seconds,
            )
    else:
        debug_info.update(
            {
                "cached": True,
                "place": geocode_cache[cache_key][0],
            }
        )
    place, place_details = geocode_cache[cache_key]
    return place, place_details, debug_info if debug else {}


PLACE_DETAIL_KEYS = ("name", "locality", "subLocality", "administrativeArea", "areasOfInterest")


def place_coordinate_for_record(record: PhotoRecord) -> Optional[dict[str, float]]:
    """Return the coordinate provenance for a resolved place name."""
    if (
        not is_resolved_place_name(record.place)
        or record.latitude is None
        or record.longitude is None
    ):
        return None
    return {"latitude": float(record.latitude), "longitude": float(record.longitude)}


def build_record_sidecar_payload(record: PhotoRecord) -> dict[str, Any]:
    """Build a refreshed sidecar while retaining unknown future fields."""
    payload = dict(record.raw_metadata or {})
    for key in {
        "source_filename", "photo_path", "datetime_iso", "date_german", "time",
        "latitude", "longitude", "place", "has_gps", "place_details",
        "gps_source", "gps_inference", "source_file_signature", "datetime_source",
        "metadata_updated_at", "place_coordinate", *PLACE_DETAIL_KEYS,
    }:
        payload.pop(key, None)
    try:
        signature = media_file_signature(record.photo_path)
    except OSError:
        signature = None
    canonical = build_photo_metadata_payload(
        source_filename=record.source_filename,
        photo_path=record.photo_path,
        photo_datetime=record.photo_datetime,
        latitude=record.latitude,
        longitude=record.longitude,
        place=record.place,
        place_details=record.place_details,
        source_file_signature=signature,
        datetime_source=record.datetime_source,
        metadata_updated_at=datetime.now().astimezone().isoformat(),
        place_coordinate=place_coordinate_for_record(record),
    )
    payload.update(canonical)
    if record.gps_source:
        payload["gps_source"] = record.gps_source
    if isinstance(record.gps_inference, dict):
        payload["gps_inference"] = record.gps_inference
    return payload


def write_record_json(record: PhotoRecord, protected_json_paths: set[Path]) -> None:
    """Write one sidecar JSON file."""
    try:
        resolved_json_path = record.json_path.resolve()
    except OSError:
        resolved_json_path = record.json_path

    if resolved_json_path in protected_json_paths:
        raise ValueError(f"refusing to overwrite protected JSON file: {record.json_path}")

    payload = build_record_sidecar_payload(record)
    write_photo_metadata(payload, record.json_path)
    record.raw_metadata = payload


def write_record_place_fields(record: PhotoRecord) -> None:
    """Patch only reverse-geocoded place fields in an existing sidecar."""
    if not isinstance(record.raw_metadata, dict):
        raise ValueError(f"no validated sidecar payload is available for {record.photo_path.name}")
    payload = dict(record.raw_metadata)
    payload["place"] = record.place
    payload.pop("place_details", None)
    for key in PLACE_DETAIL_KEYS:
        payload.pop(key, None)
    if isinstance(record.place_details, dict):
        payload["place_details"] = dict(record.place_details)
        for key in PLACE_DETAIL_KEYS:
            if key in record.place_details:
                payload[key] = record.place_details.get(key)
    place_coordinate = place_coordinate_for_record(record)
    if place_coordinate is not None:
        payload["place_coordinate"] = place_coordinate
    else:
        payload.pop("place_coordinate", None)
    write_photo_metadata(payload, record.json_path)
    record.raw_metadata = payload


def record_place_matches_gps(
    record: PhotoRecord,
    place_equivalence_m: float = DEFAULT_PLACE_GPS_EQUIVALENCE_M,
) -> bool:
    """Return whether an existing place is known to match the current GPS.

    Sidecars created before coordinate provenance was introduced remain valid;
    selected media refreshes establish the field without forcing a global
    reverse-geocoding pass.
    """
    if not is_resolved_place_name(record.place):
        return False
    payload = record.raw_metadata if isinstance(record.raw_metadata, dict) else {}
    coordinate = payload.get("place_coordinate")
    if not isinstance(coordinate, dict):
        return True
    old_latitude = _optional_float(coordinate.get("latitude"))
    old_longitude = _optional_float(coordinate.get("longitude"))
    if None in {old_latitude, old_longitude, record.latitude, record.longitude}:
        return False
    return distance_meters(
        float(old_latitude),
        float(old_longitude),
        float(record.latitude),
        float(record.longitude),
    ) <= max(0.0, float(place_equivalence_m))


def emit_debug_info(record: PhotoRecord) -> None:
    """Print debug information for one processed record."""
    if not record.debug_info:
        return

    print(f"#DEBUG {record.display_filename}", flush=True)
    print(f"#DEBUG source: {record.source}", flush=True)

    if record.source == "json":
        print(f"#DEBUG json_path: {record.json_path}", flush=True)
        print(f"#DEBUG geocode requested: {record.geocode_requested}", flush=True)

    datetime_info = record.debug_info.get("datetime")
    if datetime_info:
        print(f"#DEBUG datetime selected: {datetime_info.get('selected_source')}", flush=True)
        for candidate in datetime_info.get("candidates", []):
            print(
                f"#DEBUG datetime candidate: {candidate.get('source')} | raw={candidate.get('raw')} | parsed={candidate.get('parsed')}",
                flush=True,
            )
        if "st_birthtime" in datetime_info:
            print(f"#DEBUG st_birthtime: {datetime_info['st_birthtime']}", flush=True)
        if "st_mtime" in datetime_info:
            print(f"#DEBUG st_mtime: {datetime_info['st_mtime']}", flush=True)

    gps_info = record.debug_info.get("gps")
    if gps_info:
        print(f"#DEBUG gps source: {gps_info.get('source')}", flush=True)
        print(f"#DEBUG kMDItemLatitude raw: {gps_info.get('latitude_raw')}", flush=True)
        print(f"#DEBUG kMDItemLongitude raw: {gps_info.get('longitude_raw')}", flush=True)
        print(f"#DEBUG kMDItemGPSCoordinates raw: {gps_info.get('gps_coordinates_raw')}", flush=True)
        if "gps_coordinate_tokens" in gps_info:
            print(f"#DEBUG GPS tokens: {gps_info.get('gps_coordinate_tokens')}", flush=True)
        if "exiftool" in gps_info:
            print(
                f"#DEBUG exiftool gps: {json.dumps(gps_info['exiftool'], ensure_ascii=True, sort_keys=True)}",
                flush=True,
            )

    geocode_info = record.debug_info.get("geocode")
    if geocode_info:
        print(f"#DEBUG geocode: {json.dumps(geocode_info, ensure_ascii=True, sort_keys=True)}", flush=True)


def emit_tracks_file_debug(photo_files: list[Path], tracks_summary: Optional[TracksSummary]) -> None:
    """Print debug information about track-based file exclusion."""
    if tracks_summary is None:
        print("#DEBUG tracks excluded filenames: []", flush=True)
    else:
        excluded_names = sorted(tracks_summary.ignored_photo_names)
        print(f"#DEBUG tracks excluded filenames: {json.dumps(excluded_names, ensure_ascii=True)}", flush=True)

    processed_names = [photo_path.name for photo_path in photo_files]
    print(f"#DEBUG files to process: {json.dumps(processed_names, ensure_ascii=True)}", flush=True)


def format_gps_text(latitude: Optional[float], longitude: Optional[float]) -> str:
    """Format GPS coordinates for output."""
    if latitude is None or longitude is None:
        return GPS_NOT_AVAILABLE
    return f"{latitude:.6f}, {longitude:.6f}"


def format_place_text(
    latitude: Optional[float],
    longitude: Optional[float],
    place: Optional[str],
    geocode_requested: bool,
) -> str:
    """Format place text for output."""
    if latitude is None or longitude is None:
        return PLACE_NOT_AVAILABLE
    if place:
        return place
    if not geocode_requested:
        return PLACE_NOT_REQUESTED
    return PLACE_FAILED


def primary_stage_place_name(place: Optional[str]) -> Optional[str]:
    """Return the concise locality portion suitable for a media-stage title."""
    text = str(place or "").strip()
    if not text or text in {
        "-",
        PLACE_NOT_AVAILABLE,
        PLACE_NOT_REQUESTED,
        PLACE_FAILED,
    }:
        return None
    first_line = text.splitlines()[0].strip()
    locality = first_line.split(",", 1)[0].strip()
    return locality or None


def media_stage_name(records: list[PhotoRecord]) -> str:
    """Build a stage name from the first and last available media place."""
    ordered = sorted(
        records,
        key=lambda record: (record.photo_datetime, record.source_filename.casefold()),
    )
    places = [
        place_name
        for record in ordered
        if (place_name := primary_stage_place_name(record.place)) is not None
    ]
    if not places:
        return ""
    if places[0].casefold() == places[-1].casefold():
        return places[0]
    return f"{places[0]} - {places[-1]}"


def control_media_stage_name(entries: list[dict[str, Any]]) -> str:
    """Build a media-stage name from parsed control rows in saved order."""
    places = [
        place_name
        for entry in entries
        if entry.get("type") == "media"
        and (place_name := primary_stage_place_name(entry.get("place"))) is not None
    ]
    if not places:
        return ""
    if places[0].casefold() == places[-1].casefold():
        return places[0]
    return f"{places[0]} - {places[-1]}"


def build_unsorted_output_line(record: PhotoRecord, include_update_marker: bool = False) -> str:
    """Build the immediate output line for one record."""
    date_text = format_german_date(record.photo_datetime)
    time_text = record.photo_datetime.strftime("%H:%M")
    gps_text = format_gps_text(record.latitude, record.longitude)
    place_text = format_place_text(record.latitude, record.longitude, record.place, record.geocode_requested)
    if include_update_marker and record.place_updated:
        place_text = f"{place_text} [*updated]"
    return f"{record.source_filename} | {date_text} | {time_text} | {gps_text} | {place_text}"


def build_sorted_output_path(photolist: Path) -> Path:
    """Return the output path for the sorted list file."""
    if photolist.stem.endswith("-sorted"):
        return photolist
    return photolist.with_name(f"{photolist.stem}-sorted{photolist.suffix or '.lst'}")


def sort_records_for_output(
    records: list[PhotoRecord],
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
) -> list[PhotoRecord]:
    """Sort output records by date/time or by the original track date order."""
    if not sort_date_sections_by_tracks or tracks_summary is None or not tracks_summary.tracks:
        return sorted(records, key=lambda item: (item.photo_datetime, item.source_filename.lower()))

    track_date_order: dict[object, int] = {}
    tracks_in_original_order = sorted(
        tracks_summary.tracks,
        key=lambda track: (track.original_sequence_number, track.start_time),
    )
    for track in tracks_in_original_order:
        track_date = track.start_time.date()
        if track_date not in track_date_order:
            track_date_order[track_date] = len(track_date_order)

    fallback_offset = len(track_date_order)

    def sort_key(record: PhotoRecord) -> tuple[int, datetime, str]:
        record_date = record.photo_datetime.date()
        if record_date in track_date_order:
            return (track_date_order[record_date], record.photo_datetime, record.source_filename.lower())
        return (fallback_offset, record.photo_datetime, record.source_filename.lower())

    return sorted(records, key=sort_key)


def build_control_sections(
    records: list[PhotoRecord],
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
    media_map_filenames: Optional[dict[date, str]] = None,
    *,
    include_empty_track_sections: bool = False,
) -> list[dict[str, Any]]:
    """Build ordered normal, adjacent-day, and leftover date sections."""
    tracks = list(tracks_summary.tracks) if tracks_summary is not None else []
    if sort_date_sections_by_tracks:
        ordered_tracks = sorted(tracks, key=lambda track: (track.original_sequence_number, track.start_time))
    else:
        ordered_tracks = sorted(tracks, key=lambda track: (track.start_time, track.original_sequence_number))
    tracks_by_date: dict[date, list[TrackInfo]] = {}
    for track in ordered_tracks:
        tracks_by_date.setdefault(track.start_time.date(), []).append(track)

    exact_records: dict[date, list[PhotoRecord]] = {}
    adjacent_records: dict[tuple[str, int, str, date], list[PhotoRecord]] = {}
    adjacent_assignments: dict[tuple[str, int, str, date], AdjacentDayAssignment] = {}
    leftover_records: dict[date, list[PhotoRecord]] = {}
    for record in records:
        record_day = record.photo_datetime.date()
        if record_day in tracks_by_date:
            exact_records.setdefault(record_day, []).append(record)
            continue
        assignment = assign_adjacent_day_track(record, ordered_tracks)
        if assignment is None:
            leftover_records.setdefault(record_day, []).append(record)
            continue
        key = (
            assignment.relation,
            assignment.track.original_sequence_number,
            assignment.track.track_plot_image_filename,
            record_day,
        )
        adjacent_records.setdefault(key, []).append(record)
        adjacent_assignments[key] = assignment

    def sorted_media(items: list[PhotoRecord]) -> list[PhotoRecord]:
        return sorted(items, key=lambda item: (item.photo_datetime, item.source_filename.lower()))

    track_groups: list[dict[str, Any]] = []
    ordered_dates: list[date] = []
    for track in ordered_tracks:
        track_day = track.start_time.date()
        if track_day not in ordered_dates:
            ordered_dates.append(track_day)
    for track_day in ordered_dates:
        day_tracks = tracks_by_date[track_day]
        group_sections: list[dict[str, Any]] = []
        day_track_keys = {
            (track.original_sequence_number, track.track_plot_image_filename)
            for track in day_tracks
        }
        for relation in ("before", "normal", "after"):
            if relation == "normal":
                if include_empty_track_sections or track_day in exact_records:
                    group_sections.append(
                        {
                            "date": track_day,
                            "maps": [("Map", track.track_plot_image_filename) for track in day_tracks],
                            "records": sorted_media(exact_records.get(track_day, [])),
                            "relation": None,
                        }
                    )
                continue
            matching_keys = [
                key
                for key, assignment in adjacent_assignments.items()
                if assignment.relation == relation
                and (assignment.track.original_sequence_number, assignment.track.track_plot_image_filename) in day_track_keys
            ]
            matching_keys.sort(key=lambda key: (key[3], key[1], key[2].casefold()))
            for key in matching_keys:
                assignment = adjacent_assignments[key]
                keyword = "MapBefore" if relation == "before" else "MapAfter"
                group_sections.append(
                    {
                        "date": key[3],
                        "maps": [(keyword, assignment.track.track_plot_image_filename)],
                        "records": sorted_media(adjacent_records[key]),
                        "relation": relation,
                    }
                )

        if group_sections:
            track_groups.append(
                {
                    "date": track_day,
                    "sections": group_sections,
                    "sequence": min(track.original_sequence_number for track in day_tracks),
                }
            )

    map_names = media_map_filenames or {}
    media_groups = [
        {
            "date": record_day,
            "sections": [
                {
                    "date": record_day,
                    "maps": [
                        ("MediaMap", map_names[record_day])
                    ] if record_day in map_names else [],
                    "records": sorted_media(items),
                    "relation": "media" if record_day in map_names else None,
                }
            ],
            "sequence": None,
        }
        for record_day, items in leftover_records.items()
    ]

    if sort_date_sections_by_tracks:
        groups = list(track_groups)
        for media_group in sorted(media_groups, key=lambda item: item["date"]):
            insert_at = len(groups)
            for index, group in enumerate(groups):
                if media_group["date"] < group["date"]:
                    insert_at = index
                    break
                if media_group["date"] == group["date"]:
                    media_records = media_group["sections"][0]["records"]
                    track_records = [
                        record
                        for section in group["sections"]
                        if section.get("relation") is None
                        for record in section["records"]
                    ]
                    media_average = sum(record.photo_datetime.timestamp() for record in media_records) / len(media_records)
                    track_times = [record.photo_datetime.timestamp() for record in track_records]
                    if track_times and media_average <= (min(track_times) + max(track_times)) / 2.0:
                        insert_at = index
                    else:
                        insert_at = index + 1
                    break
            groups.insert(insert_at, media_group)
    else:
        groups = sorted(
            track_groups + media_groups,
            key=lambda item: (
                item["date"],
                0 if item["sequence"] is not None else 1,
                item["sequence"] if item["sequence"] is not None else 0,
            ),
        )
        # Keep media blocks outside the complete before/track/after group. On a
        # shared date, use average capture time to choose the side of the group.
        for media_group in [group for group in list(groups) if group["sequence"] is None]:
            matching = next(
                (group for group in groups if group["sequence"] is not None and group["date"] == media_group["date"]),
                None,
            )
            if matching is None:
                continue
            groups.remove(media_group)
            track_records = [
                record
                for section in matching["sections"]
                if section.get("relation") is None
                for record in section["records"]
            ]
            media_records = media_group["sections"][0]["records"]
            media_average = sum(record.photo_datetime.timestamp() for record in media_records) / len(media_records)
            track_times = [record.photo_datetime.timestamp() for record in track_records]
            matching_index = groups.index(matching)
            insert_at = matching_index
            if not track_times or media_average > (min(track_times) + max(track_times)) / 2.0:
                insert_at += 1
            groups.insert(insert_at, media_group)

    return [section for group in groups for section in group["sections"]]


def media_map_filename(sorted_output_path: Path, media_day: date, filename_base: Optional[str] = None) -> str:
    """Return a deterministic project-local filename for one media-only map."""
    base = str(filename_base or sorted_output_path.stem)
    if base.endswith("-sorted"):
        base = base[:-7]
    safe_base = re.sub(r"[^\w.-]+", "_", base, flags=re.UNICODE).strip("_") or "adventure"
    return f"{safe_base}-media-{media_day.isoformat()}.png"


def media_map_output_filename(canonical_filename: str, map_layout: str) -> str:
    """Return the selected variant filename while keeping control rows canonical."""
    if str(map_layout) == "time-lapse":
        return time_lapse_track_map_name(canonical_filename)
    return canonical_filename


def render_media_map_specs(
    specs: list[dict[str, Any]],
    sorted_output_path: Path,
    options: Optional[dict[str, Any]],
    output_writer=None,
) -> list[Path]:
    """Render media-map specifications through one shared variant-aware path."""
    if options is None:
        return []
    if options.get("skip_rendering"):
        return []
    output_dir = Path(options.get("output_dir") or (sorted_output_path.parent / "trackimages"))
    output_dir.mkdir(parents=True, exist_ok=True)
    requested_layouts = options.get("map_layouts")
    if isinstance(requested_layouts, (list, tuple)):
        map_layouts = tuple(
            layout for layout in (str(value) for value in requested_layouts) if layout in {"standard", "time-lapse"}
        )
    else:
        map_layouts = (str(options.get("map_layout", "standard")),)
    map_layouts = tuple(dict.fromkeys(map_layouts)) or ("standard",)
    render_options = {
        key: value
        for key, value in options.items()
        if key not in {
            "output_dir",
            "filename_base",
            "map_layouts",
            "map_failures",
            "skip_rendering",
            "continue_on_map_error",
            "adventure_render_parameters_by_layout",
            "adventure_overview_render_parameters",
            "only_missing_or_stale",
        }
    }
    rendered = []
    failures = options.setdefault("map_failures", []) if options.get("continue_on_map_error") else None
    stop_after_failure = False
    for spec in specs:
        if stop_after_failure:
            break
        for map_layout in map_layouts:
            check_cancelled()
            output_name = media_map_output_filename(spec["filename"], map_layout)
            output_path = output_dir / output_name
            layout_options = dict(render_options)
            layout_options["map_layout"] = map_layout
            signatures = options.get("adventure_render_parameters_by_layout")
            if isinstance(signatures, dict) and isinstance(signatures.get(map_layout), dict):
                layout_options["adventure_render_parameters"] = signatures[map_layout]
            if options.get("only_missing_or_stale") and media_map_variant_is_current(
                output_path,
                spec,
                map_layout,
                layout_options.get("adventure_render_parameters"),
            ):
                print(
                    f"Keeping current {map_layout} media location map: {output_name}",
                    file=output_writer,
                )
                continue
            print(
                f"Creating {map_layout} media location map: {output_name}",
                file=output_writer,
            )
            try:
                render_media_location_map(
                    spec["coordinates"],
                    spec["date"],
                    output_path,
                    media_points=spec.get("media_points"),
                    stage_name=spec.get("stage_name", ""),
                    **layout_options,
                )
            except Exception as exc:
                if failures is None:
                    raise
                failures.append({"filename": output_name, "error": str(exc)})
                options["skip_rendering"] = True
                print(
                    f"Could not create {output_name}: {exc}",
                    file=output_writer,
                    flush=True,
                )
                stop_after_failure = True
                break
            rendered.append((output_dir / output_name).resolve(strict=False))
    return rendered


def media_map_variant_is_current(
    output_path: Path,
    spec: dict[str, Any],
    map_layout: str,
    expected_parameters: Optional[dict[str, Any]],
) -> bool:
    """Return whether one media-map variant matches geometry and render settings."""
    if not output_path.is_file():
        return False
    try:
        metadata = read_json_data(output_path.with_suffix(".json"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False
    if not media_map_metadata_matches_coordinates(
        metadata,
        spec["date"],
        spec["coordinates"],
    ):
        return False
    if str(metadata.get("map_layout", "standard")) != str(map_layout):
        return False
    if isinstance(expected_parameters, dict):
        return metadata.get("adventure_render_parameters") == expected_parameters
    return True


def media_map_specs_for_sections(
    sections: list[dict[str, Any]],
    sorted_output_path: Path,
    options: Optional[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[date, str]]:
    """Build deterministic media-map specifications without rendering them."""
    if options is None:
        return [], {}
    filename_base = options.get("filename_base")
    filenames: dict[date, str] = {}
    specs = []
    for section in sections:
        check_cancelled()
        if section.get("maps") or not section.get("records"):
            continue
        coordinates = [
            (record.latitude, record.longitude)
            for record in section["records"]
            if record.latitude is not None and record.longitude is not None
        ]
        if not coordinates:
            continue
        filename = media_map_filename(sorted_output_path, section["date"], filename_base)
        specs.append(
            {
                "date": section["date"],
                "filename": filename,
                "coordinates": coordinates,
                "stage_name": media_stage_name(section["records"]),
                "media_points": [
                    {
                        "lat": record.latitude,
                        "lon": record.longitude,
                        "time_iso": record.photo_datetime.isoformat(),
                        "source_name": record.photo_path.name,
                    }
                    for record in section["records"]
                    if record.latitude is not None and record.longitude is not None
                ],
            }
        )
        filenames[section["date"]] = filename
    return specs, filenames


def create_media_maps_for_sections(
    sections: list[dict[str, Any]],
    sorted_output_path: Path,
    options: Optional[dict[str, Any]],
) -> dict[date, str]:
    """Render location maps for leftover date sections that contain GPS media."""
    if options is None:
        return {}
    specs, filenames = media_map_specs_for_sections(
        sections,
        sorted_output_path,
        options,
    )
    render_media_map_specs(specs, sorted_output_path, options)
    return filenames


def create_media_overview_for_records(
    records: list[PhotoRecord],
    sorted_output_path: Path,
    options: Optional[dict[str, Any]],
    output_writer=None,
) -> Optional[str]:
    """Create the shared overview used by a media-only Adventure."""
    if options is None:
        return None
    media_points = [
        {
            "lat": record.latitude,
            "lon": record.longitude,
            "time_iso": record.photo_datetime.isoformat(),
            "source_name": record.photo_path.name,
        }
        for record in records
        if record.latitude is not None and record.longitude is not None
    ]
    if not media_points:
        return None
    output_dir = Path(options.get("output_dir") or (sorted_output_path.parent / "trackimages"))
    output_dir.mkdir(parents=True, exist_ok=True)
    filename_base = str(options.get("filename_base") or sorted_output_path.stem.removesuffix("-sorted"))
    output_name = f"{filename_base}.png"
    if options.get("skip_rendering") or options.get("map_failures"):
        return output_name
    output_path = output_dir / output_name
    expected_parameters = options.get(
        "adventure_overview_render_parameters",
        options.get("adventure_render_parameters"),
    )
    if options.get("only_missing_or_stale") and media_overview_is_current(
        output_path,
        media_points,
        expected_parameters,
    ):
        print(f"Keeping current media overview: {output_name}", file=output_writer)
        return output_name
    print(f"Creating media Tour Overview: {output_name}", file=output_writer)
    render_media_overview_map(
        [(point["lat"], point["lon"]) for point in media_points],
        output_path,
        media_points=media_points,
        header=filename_base,
        zoom_level=int(options.get("zoom_level", 8)),
        image_size=tuple(options.get("image_size", (1600, 1200))),
        font_factor=float(options.get("font_factor", 1.0)),
        use_esri=bool(options.get("use_esri", False)),
        background_color=str(options.get("background_color", "black")),
        title_color=str(options.get("title_color", "white")),
        map_provider=str(options.get("map_provider", "osm")),
        custom_map_url=str(options.get("custom_map_url", "")),
        custom_map_attribution=str(options.get("custom_map_attribution", "")),
        maximum_map_zoom=int(options.get("maximum_map_zoom", 19)),
        map_request_timeout_seconds=float(options.get("map_request_timeout_seconds", 12.0)),
        adventure_render_parameters=options.get("adventure_overview_render_parameters", options.get("adventure_render_parameters")),
    )
    return output_name


def media_overview_is_current(
    output_path: Path,
    media_points: list[dict[str, Any]],
    expected_parameters: Optional[dict[str, Any]],
) -> bool:
    """Return whether the shared media overview matches its points and settings."""
    if not output_path.is_file():
        return False
    try:
        metadata = read_json_data(output_path.with_suffix(".json"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False
    expected_fingerprint = media_overview_fingerprint(
        [(point["lat"], point["lon"]) for point in media_points]
    )
    if str(metadata.get("media_fingerprint", "")) != expected_fingerprint:
        return False
    if isinstance(expected_parameters, dict):
        return metadata.get("adventure_render_parameters") == expected_parameters
    return True


def build_project_map_plan(
    records: list[PhotoRecord],
    sorted_output_path: Path,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
    media_map_options: Optional[dict[str, Any]],
) -> ProjectMapPlan:
    """Build one deterministic stage plan shared by maps and control output."""
    sections = build_control_sections(
        records,
        tracks_summary,
        sort_date_sections_by_tracks,
        include_empty_track_sections=True,
    )
    specs, filenames = media_map_specs_for_sections(
        sections,
        sorted_output_path,
        media_map_options,
    )
    if filenames:
        sections = build_control_sections(
            records,
            tracks_summary,
            sort_date_sections_by_tracks,
            filenames,
            include_empty_track_sections=True,
        )
    overview_points = [
        {
            "lat": record.latitude,
            "lon": record.longitude,
            "time_iso": record.photo_datetime.isoformat(),
            "source_name": record.photo_path.name,
        }
        for record in records
        if record.latitude is not None and record.longitude is not None
    ]
    overview_name = None
    if tracks_summary and tracks_summary.overview_image:
        overview_name = tracks_summary.overview_image
    elif overview_points and media_map_options is not None:
        filename_base = str(
            media_map_options.get("filename_base")
            or sorted_output_path.stem.removesuffix("-sorted")
        )
        overview_name = f"{filename_base}.png"
    return ProjectMapPlan(
        records=list(records),
        sections=sections,
        media_map_specs=specs,
        overview_name=overview_name,
        overview_points=overview_points,
        tracks_summary=tracks_summary,
    )


def render_project_map_plan(
    plan: ProjectMapPlan,
    sorted_output_path: Path,
    media_map_options: Optional[dict[str, Any]],
    output_writer=None,
) -> list[Path]:
    """Render the media portion of a shared project map plan."""
    rendered = render_media_map_specs(
        plan.media_map_specs,
        sorted_output_path,
        media_map_options,
        output_writer=output_writer,
    )
    if plan.tracks_summary is None or not plan.tracks_summary.tracks:
        create_media_overview_for_records(
            plan.records,
            sorted_output_path,
            media_map_options,
            output_writer=output_writer,
        )
    return rendered


def project_map_plan_from_sidecars(
    project_dir: Path | str,
    sorted_output_path: Path | str,
    *,
    tracks_summary_path: Path | str | None = None,
    sort_date_sections_by_tracks: bool = False,
    media_map_options: Optional[dict[str, Any]] = None,
    infer_gps_from_tracks: bool = True,
    place_equivalence_m: float = DEFAULT_PLACE_GPS_EQUIVALENCE_M,
) -> ProjectMapPlan:
    """Load valid media sidecars and build a map plan without extracting metadata."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    output = Path(sorted_output_path).expanduser().resolve(strict=False)
    tracks_summary = load_tracks_summary(
        Path(tracks_summary_path).expanduser().resolve(strict=False)
        if tracks_summary_path is not None
        else None,
        output,
    )
    media_files = exclude_tracks_images(
        list_photo_files(project, "ALL"),
        tracks_summary,
    )
    resolver = (
        LazyTrackGpsResolver(tracks_summary, place_equivalence_m)
        if infer_gps_from_tracks and tracks_summary is not None
        else None
    )
    records = []
    for media_path in media_files:
        record = load_record_from_json(get_json_path_for_photo(media_path), media_path)
        if record is None:
            continue
        if resolver is not None and resolver.apply(record):
            write_record_json(record, set())
        records.append(record)
    if resolver is not None:
        resolver.emit_summary()
    return build_project_map_plan(
        records,
        output,
        tracks_summary,
        sort_date_sections_by_tracks,
        media_map_options,
    )


def add_media_maps_to_control_entries(
    entries: list[dict[str, Any]],
    sorted_output_path: Path,
    options: Optional[dict[str, Any]],
    affected_dates: Optional[set[date]] = None,
) -> int:
    """Reconcile media maps for mapless date sections in a merged list."""
    if options is None:
        return 0
    inserted = 0
    for header_index, end_index in reversed(_control_section_ranges(entries)):
        media_day = entries[header_index].get("date")
        if affected_dates is not None and media_day not in affected_dates:
            continue
        section_entries = entries[header_index + 1 : end_index]
        if any(entry.get("type") in {"map", "map_before", "map_after"} for entry in section_entries):
            continue
        existing_media_map = next(
            (entry for entry in section_entries if entry.get("type") == "media_map"),
            None,
        )
        coordinates = control_media_coordinates(section_entries)
        if not coordinates or not isinstance(media_day, date):
            if existing_media_map is not None:
                entries.remove(existing_media_map)
            continue
        filename = (
            str(existing_media_map.get("name"))
            if existing_media_map is not None and existing_media_map.get("name")
            else media_map_filename(sorted_output_path, media_day, options.get("filename_base"))
        )
        if existing_media_map is not None:
            continue
        entries.insert(
            header_index + 1,
            {
                "line": f"#MediaMap: {filename}",
                "type": "media_map",
                "date": media_day,
                "name": filename,
            },
        )
        inserted += 1
    render_media_map_specs(
        media_map_specs_from_control_entries(entries, affected_dates=affected_dates),
        sorted_output_path,
        options,
    )
    return inserted


def control_media_coordinates(entries: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Extract ordered GPS coordinates from media rows in one control section."""
    coordinates = []
    for entry in entries:
        if entry.get("type") != "media":
            continue
        parts = [part.strip() for part in str(entry.get("line", "")).split("|")]
        if len(parts) < 3 or "," not in parts[2]:
            continue
        try:
            latitude_text, longitude_text = parts[2].split(",", 1)
            coordinates.append((float(latitude_text), float(longitude_text)))
        except ValueError:
            continue
    return coordinates


def control_media_points(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ordered, named and timed media points from control rows."""
    result = []
    for entry in entries:
        if entry.get("type") != "media":
            continue
        parts = [part.strip() for part in str(entry.get("line", "")).split("|")]
        if len(parts) < 3 or "," not in parts[2]:
            continue
        try:
            latitude_text, longitude_text = parts[2].split(",", 1)
            point = {
                "lat": float(latitude_text),
                "lon": float(longitude_text),
                "source_name": str(entry.get("name") or parts[0]),
            }
        except ValueError:
            continue
        timestamp = entry.get("datetime")
        if isinstance(timestamp, datetime):
            point["time_iso"] = timestamp.isoformat()
        result.append(point)
    return result


def media_map_specs_from_control_entries(
    entries: list[dict[str, Any]],
    affected_dates: Optional[set[date]] = None,
) -> list[dict[str, Any]]:
    """Return canonical media-map render specifications from parsed control rows."""
    specs = []
    for header_index, end_index in _control_section_ranges(entries):
        media_day = entries[header_index].get("date")
        if affected_dates is not None and media_day not in affected_dates:
            continue
        section_entries = entries[header_index + 1 : end_index]
        media_map_entry = next(
            (entry for entry in section_entries if entry.get("type") == "media_map"),
            None,
        )
        if media_map_entry is None or not isinstance(media_day, date):
            continue
        coordinates = control_media_coordinates(section_entries)
        if not coordinates:
            continue
        specs.append(
            {
                "date": media_day,
                "filename": str(media_map_entry.get("name", "")).strip(),
                "coordinates": coordinates,
                "stage_name": control_media_stage_name(section_entries),
                "media_points": control_media_points(section_entries),
            }
        )
    return [spec for spec in specs if spec["filename"]]


def write_sorted_output(
    records: list[PhotoRecord],
    sorted_output_path: Path,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool = False,
    media_map_options: Optional[dict[str, Any]] = None,
) -> None:
    """Write the grouped sorted list after collecting all records."""
    plan = build_project_map_plan(
        records,
        sorted_output_path,
        tracks_summary,
        sort_date_sections_by_tracks,
        media_map_options,
    )
    media_overview = plan.overview_name
    try:
        render_project_map_plan(plan, sorted_output_path, media_map_options)
    except Exception as exc:
        if not (media_map_options and media_map_options.get("continue_on_map_error")):
            raise
        media_map_options.setdefault("map_failures", []).append(
            {"filename": "project maps", "error": str(exc)}
        )
        print(f"Could not create project maps: {exc}", flush=True)
    sorted_output_path.parent.mkdir(parents=True, exist_ok=True)

    with sorted_output_path.open("w", encoding="utf-8") as output_file:
        if tracks_summary and tracks_summary.overview_image:
            output_file.write(f"#Overviewmap: {tracks_summary.overview_image}\n")
        elif media_overview:
            output_file.write(f"#Overviewmap: {media_overview}\n")

        for section in plan.sections:
            date_datetime = datetime.combine(section["date"], datetime.min.time()).replace(tzinfo=LOCAL_TIMEZONE)
            output_file.write(f"#Datum: {format_german_date(date_datetime)}\n")
            for keyword, filename in section["maps"]:
                output_file.write(f"#{keyword}: {filename}\n")
            for record in section["records"]:
                output_file.write(sorted_media_output_line(record) + "\n")


def parse_german_date_label(value: str) -> Optional[date]:
    """Parse '#Datum:' labels written by this module."""
    text = str(value or "").strip()
    if "," in text:
        text = text.split(",", 1)[1].strip()
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        return None


def sorted_media_output_line(record: PhotoRecord) -> str:
    """Return one media line in sorted-list format."""
    time_text = record.photo_datetime.strftime("%H:%M")
    gps_text = format_gps_text(record.latitude, record.longitude)
    place_text = format_place_text(record.latitude, record.longitude, record.place, record.geocode_requested)
    return f"{record.source_filename} | {time_text} | {gps_text} | {place_text}"


def control_line_name(line: str) -> str:
    """Return the filename-like payload from a control-file line."""
    text = line.strip()
    if text.startswith("#"):
        _keyword, _separator, value = text[1:].partition(":")
        return value.strip()
    return text.split("|", 1)[0].strip()


def parse_control_file_entries(lines: list[str]) -> list[dict[str, Any]]:
    """Parse sorted-list lines just enough for non-destructive merging."""
    entries: list[dict[str, Any]] = []
    current_date: Optional[date] = None
    current_date_label = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        content = stripped
        entry: dict[str, Any] = {
            "line": stripped,
            "type": "other",
            "date": current_date,
            "name": control_line_name(stripped),
        }
        if content.startswith("#"):
            keyword, _separator, value = content[1:].partition(":")
            normalized = keyword.strip().lower()
            if normalized in {"datum", "date"}:
                current_date_label = value.strip()
                current_date = parse_german_date_label(current_date_label)
                entry.update({"type": "date", "date": current_date, "date_label": current_date_label})
            elif normalized == "overviewmap":
                entry.update({"type": "overview"})
            elif normalized == "map":
                entry.update({"type": "map"})
            elif normalized == "mapbefore":
                entry.update({"type": "map_before", "relation": "before"})
            elif normalized == "mapafter":
                entry.update({"type": "map_after", "relation": "after"})
            elif normalized == "mediamap":
                entry.update({"type": "media_map", "relation": "media"})
            elif normalized == "music":
                entry.update({"type": "music", "name": ""})
            elif normalized == "control":
                entry.update({"type": "control", "name": ""})
        else:
            parts = [part.strip() for part in content.split("|")]
            if parts:
                entry.update(
                    {
                        "type": "media",
                        "name": parts[0],
                        "date": current_date,
                        "place": parts[3] if len(parts) > 3 else "",
                    }
                )
                if len(parts) > 1 and current_date is not None:
                    try:
                        parsed_time = datetime.strptime(parts[1], "%H:%M").time()
                        entry["datetime"] = datetime.combine(current_date, parsed_time).astimezone()
                    except ValueError:
                        pass
        entries.append(entry)
    return entries


def remove_control_track_map_entries(control_file_path: Path | str, names_to_remove: list[str]) -> int:
    """Remove selected overview/map lines without touching dates or media rows."""
    names = {
        normalize_filename_for_match(name)
        for name in names_to_remove
        if str(name).strip()
    }
    map_names = {
        normalize_track_plot_filename_for_match(name)
        for name in names_to_remove
        if str(name).strip()
    }
    if not names and not map_names:
        return 0
    path = Path(control_file_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    kept_lines = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        content = stripped
        if content.startswith("#"):
            keyword, _separator, value = content[1:].partition(":")
            normalized_keyword = keyword.strip().lower()
            name = value.strip()
            if normalized_keyword == "overviewmap" and normalize_filename_for_match(name) in names:
                removed += 1
                continue
            if normalized_keyword == "map" and normalize_track_plot_filename_for_match(name) in map_names:
                removed += 1
                continue
            if normalized_keyword in {"mapbefore", "mapafter"} and normalize_track_plot_filename_for_match(name) in map_names:
                removed += 1
                continue
        kept_lines.append(line)
    if removed:
        path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
    return removed


def update_control_special_map_entries(
    control_file_path: Path | str,
    replacements: dict[str, str],
) -> int:
    """Update MapBefore/MapAfter filenames while preserving their directives."""
    normalized_replacements = {
        normalize_track_plot_filename_for_match(old): new
        for old, new in replacements.items()
        if str(old).strip() and str(new).strip()
    }
    if not normalized_replacements:
        return 0
    path = Path(control_file_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = 0
    output = []
    for line in lines:
        stripped = line.strip()
        content = stripped
        if content.startswith("#"):
            keyword, separator, value = content[1:].partition(":")
            if separator and keyword.strip().lower() in {"mapbefore", "mapafter"}:
                replacement = normalized_replacements.get(
                    normalize_track_plot_filename_for_match(value.strip())
                )
                if replacement is not None and replacement != value.strip():
                    line = f"#{keyword.strip()}: {replacement}"
                    changed += 1
        output.append(line)
    if changed:
        path.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
    return changed


def date_order_key(day: date, tracks_summary: Optional[TracksSummary], sort_date_sections_by_tracks: bool) -> tuple[int, date]:
    """Return a stable date-section order key matching the normal sorted writer."""
    if not sort_date_sections_by_tracks or tracks_summary is None or not tracks_summary.tracks:
        return (0, day)
    ordered_dates: dict[date, int] = {}
    for track in sorted(tracks_summary.tracks, key=lambda item: (item.original_sequence_number, item.start_time)):
        track_day = track.start_time.date()
        if track_day not in ordered_dates:
            ordered_dates[track_day] = len(ordered_dates)
    return (ordered_dates.get(day, len(ordered_dates)), day)


def find_date_section(entries: list[dict[str, Any]], day: date) -> tuple[Optional[int], int]:
    """Return (date-header-index, exclusive section-end-index)."""
    header_index = None
    for index, entry in enumerate(entries):
        if entry.get("type") == "date" and entry.get("date") == day:
            header_index = index
            break
    if header_index is None:
        return None, len(entries)
    end_index = len(entries)
    for index in range(header_index + 1, len(entries)):
        if entries[index].get("type") == "date":
            end_index = index
            break
    return header_index, end_index


def ensure_date_section(
    entries: list[dict[str, Any]],
    day: date,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
) -> tuple[int, int]:
    """Ensure a #Datum section exists and return its bounds."""
    header_index, end_index = find_date_section(entries, day)
    if header_index is not None:
        return header_index, end_index

    new_key = date_order_key(day, tracks_summary, sort_date_sections_by_tracks)
    insert_at = len(entries)
    for index, entry in enumerate(entries):
        if entry.get("type") != "date" or entry.get("date") is None:
            continue
        if date_order_key(entry["date"], tracks_summary, sort_date_sections_by_tracks) > new_key:
            insert_at = index
            break
    date_datetime = datetime.combine(day, datetime.min.time()).astimezone()
    label = format_german_date(date_datetime)
    entries.insert(
        insert_at,
        {
            "line": f"#Datum: {label}",
            "type": "date",
            "date": day,
            "date_label": label,
            "name": label,
        },
    )
    return insert_at, insert_at + 1


def _date_section_bounds_containing(
    entries: list[dict[str, Any]],
    entry_index: int,
) -> tuple[int, int]:
    """Return the date-section bounds containing one parsed entry."""
    start = entry_index
    while start > 0 and entries[start].get("type") != "date":
        start -= 1
    if entries[start].get("type") != "date":
        start = entry_index
    end = len(entries)
    for index in range(entry_index + 1, len(entries)):
        if entries[index].get("type") == "date":
            end = index
            break
    return start, end


def _track_relative_section_insert_index(
    entries: list[dict[str, Any]],
    track: TrackInfo,
    tracks_summary: TracksSummary,
    sort_date_sections_by_tracks: bool,
) -> Optional[int]:
    """Place a missing stage beside its nearest existing canonical stage."""
    ordered_tracks = sorted(
        tracks_summary.tracks,
        key=(
            (lambda item: (item.original_sequence_number, item.start_time))
            if sort_date_sections_by_tracks
            else (lambda item: (item.start_time, item.original_sequence_number))
        ),
    )
    order = {
        _canonical_control_track_map_name(item.track_plot_image_filename): index
        for index, item in enumerate(ordered_tracks)
        if item.track_plot_image_filename
    }
    target_key = _canonical_control_track_map_name(track.track_plot_image_filename)
    target_order = order.get(target_key)
    if target_order is None:
        return None

    existing = []
    for index, entry in enumerate(entries):
        if entry.get("type") != "map" or not entry.get("name"):
            continue
        entry_order = order.get(_canonical_control_track_map_name(str(entry["name"])))
        if entry_order is not None:
            existing.append((entry_order, index))
    predecessors = [item for item in existing if item[0] < target_order]
    if predecessors:
        _order, entry_index = max(predecessors, key=lambda item: item[0])
        _start, end = _date_section_bounds_containing(entries, entry_index)
        return end
    successors = [item for item in existing if item[0] > target_order]
    if successors:
        _order, entry_index = min(successors, key=lambda item: item[0])
        start, _end = _date_section_bounds_containing(entries, entry_index)
        return start
    return None


def insert_map_entry(
    entries: list[dict[str, Any]],
    track: TrackInfo,
    tracks_summary: TracksSummary,
    sort_date_sections_by_tracks: bool,
) -> None:
    """Insert one missing #Map line into its date section."""
    day = track.start_time.date()
    header_index, end_index = find_date_section(entries, day)
    if header_index is None:
        insert_at = _track_relative_section_insert_index(
            entries,
            track,
            tracks_summary,
            sort_date_sections_by_tracks,
        )
        if insert_at is None:
            header_index, end_index = ensure_date_section(
                entries,
                day,
                tracks_summary,
                sort_date_sections_by_tracks,
            )
        else:
            date_datetime = datetime.combine(day, datetime.min.time()).astimezone()
            label = format_german_date(date_datetime)
            entries.insert(
                insert_at,
                {
                    "line": f"#Datum: {label}",
                    "type": "date",
                    "date": day,
                    "date_label": label,
                    "name": label,
                },
            )
            header_index, end_index = insert_at, insert_at + 1
    insert_at = header_index + 1
    for index in range(header_index + 1, end_index):
        if entries[index].get("type") == "map":
            insert_at = index + 1
            continue
        if entries[index].get("type") == "media":
            break
    image_name = Path(track.track_plot_image_filename).name
    entries.insert(
        insert_at,
        {
            "line": f"#Map: {image_name}",
            "type": "map",
            "date": day,
            "name": image_name,
        },
    )


def insert_media_entry(
    entries: list[dict[str, Any]],
    record: PhotoRecord,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
) -> None:
    """Insert one missing media line into its date section."""
    day = record.photo_datetime.date()
    header_index, end_index = ensure_date_section(entries, day, tracks_summary, sort_date_sections_by_tracks)
    insert_at = header_index + 1
    for index in range(header_index + 1, end_index):
        entry = entries[index]
        if entry.get("type") == "map":
            insert_at = index + 1
            continue
        if entry.get("type") == "media":
            existing_datetime = entry.get("datetime")
            if existing_datetime is not None and existing_datetime > record.photo_datetime:
                break
            insert_at = index + 1
    entries.insert(
        insert_at,
        {
            "line": sorted_media_output_line(record),
            "type": "media",
            "date": day,
            "datetime": record.photo_datetime,
            "name": record.source_filename,
        },
    )


def _control_section_ranges(entries: list[dict[str, Any]]) -> list[tuple[int, int]]:
    headers = [index for index, entry in enumerate(entries) if entry.get("type") == "date"]
    return [
        (header, headers[index + 1] if index + 1 < len(headers) else len(entries))
        for index, header in enumerate(headers)
    ]


def _track_number_from_map_name(filename: str) -> Optional[int]:
    match = re.match(r"^0*(\d+)_", normalize_filename_for_match(filename))
    return int(match.group(1)) if match else None


def _map_name_matches_track(candidate: str, expected: str) -> bool:
    if normalize_track_plot_filename_for_match(candidate) == normalize_track_plot_filename_for_match(expected):
        return True
    candidate_number = _track_number_from_map_name(candidate)
    expected_number = _track_number_from_map_name(expected)
    return candidate_number is not None and candidate_number == expected_number


def _insert_media_in_section(
    entries: list[dict[str, Any]],
    header_index: int,
    end_index: int,
    record: PhotoRecord,
) -> None:
    insert_at = header_index + 1
    for index in range(header_index + 1, end_index):
        entry = entries[index]
        if entry.get("type") in {"map", "map_before", "map_after"}:
            insert_at = index + 1
            continue
        if entry.get("type") != "media":
            continue
        existing_datetime = entry.get("datetime")
        if existing_datetime is not None and existing_datetime > record.photo_datetime:
            break
        insert_at = index + 1
    entries.insert(
        insert_at,
        {
            "line": sorted_media_output_line(record),
            "type": "media",
            "date": record.photo_datetime.date(),
            "datetime": record.photo_datetime,
            "name": record.source_filename,
        },
    )


def _new_date_entry(day: date) -> dict[str, Any]:
    label = format_german_date(datetime.combine(day, datetime.min.time()).replace(tzinfo=LOCAL_TIMEZONE))
    return {"line": f"#Datum: {label}", "type": "date", "date": day, "date_label": label, "name": label}


def insert_adjacent_media_entry(
    entries: list[dict[str, Any]],
    record: PhotoRecord,
    assignment: AdjacentDayAssignment,
) -> None:
    """Insert merged media into a matching or newly created special section."""
    day = record.photo_datetime.date()
    special_type = "map_before" if assignment.relation == "before" else "map_after"
    keyword = "MapBefore" if assignment.relation == "before" else "MapAfter"
    for header_index, end_index in _control_section_ranges(entries):
        if entries[header_index].get("date") != day:
            continue
        if any(
            entry.get("type") == special_type
            and _map_name_matches_track(str(entry.get("name", "")), assignment.track.track_plot_image_filename)
            for entry in entries[header_index + 1 : end_index]
        ):
            _insert_media_in_section(entries, header_index, end_index, record)
            return

    target_section = None
    for header_index, end_index in _control_section_ranges(entries):
        if any(
            entry.get("type") == "map"
            and _map_name_matches_track(str(entry.get("name", "")), assignment.track.track_plot_image_filename)
            for entry in entries[header_index + 1 : end_index]
        ):
            target_section = (header_index, end_index)
            break
    if target_section is None:
        insert_at = len(entries)
    elif assignment.relation == "before":
        insert_at = target_section[0]
    else:
        insert_at = target_section[1]
    entries[insert_at:insert_at] = [
        _new_date_entry(day),
        {
            "line": f"#{keyword}: {assignment.track.track_plot_image_filename}",
            "type": special_type,
            "relation": assignment.relation,
            "date": day,
            "name": assignment.track.track_plot_image_filename,
        },
    ]
    _insert_media_in_section(entries, insert_at, insert_at + 2, record)


def insert_classified_media_entry(
    entries: list[dict[str, Any]],
    record: PhotoRecord,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
) -> None:
    """Insert merged media into an exact, adjacent, or trailing leftover section."""
    tracks = list(tracks_summary.tracks) if tracks_summary is not None else []
    tracks_in_order = sorted(
        tracks,
        key=(
            (lambda track: (track.original_sequence_number, track.start_time))
            if sort_date_sections_by_tracks
            else (lambda track: (track.start_time, track.original_sequence_number))
        ),
    )
    day = record.photo_datetime.date()
    day_tracks = [track for track in tracks_in_order if track.start_time.date() == day]
    if day_tracks:
        for header_index, end_index in _control_section_ranges(entries):
            if entries[header_index].get("date") != day:
                continue
            if any(entry.get("type") == "map" for entry in entries[header_index + 1 : end_index]):
                _insert_media_in_section(entries, header_index, end_index, record)
                return
        insert_at = len(entries)
        entries[insert_at:insert_at] = [
            _new_date_entry(day),
            *[
                {
                    "line": f"#Map: {track.track_plot_image_filename}",
                    "type": "map",
                    "date": day,
                    "name": track.track_plot_image_filename,
                }
                for track in day_tracks
            ],
        ]
        _insert_media_in_section(entries, insert_at, insert_at + 1 + len(day_tracks), record)
        return

    # Reuse the complete creation classifier so Merge cannot diverge from the
    # exact-date and adjacent-day passes used for a newly created list.
    classified_sections = build_control_sections(
        [record],
        tracks_summary,
        sort_date_sections_by_tracks,
    )
    classified_maps = classified_sections[0].get("maps", []) if classified_sections else []
    if classified_maps and classified_maps[0][0] in {"MapBefore", "MapAfter"}:
        keyword, track_filename = classified_maps[0]
        matched_track = next(
            (
                track
                for track in tracks_in_order
                if _map_name_matches_track(track.track_plot_image_filename, track_filename)
            ),
            None,
        )
        if matched_track is not None:
            insert_adjacent_media_entry(
                entries,
                record,
                AdjacentDayAssignment(
                    "before" if keyword == "MapBefore" else "after",
                    matched_track,
                    None,
                ),
            )
            return

    for header_index, end_index in _control_section_ranges(entries):
        if entries[header_index].get("date") != day:
            continue
        if not any(
            entry.get("type") in {"map", "map_before", "map_after"}
            for entry in entries[header_index + 1 : end_index]
        ):
            _insert_media_in_section(entries, header_index, end_index, record)
            return
    insert_at = len(entries)
    entries.insert(insert_at, _new_date_entry(day))
    _insert_media_in_section(entries, insert_at, insert_at + 1, record)


def _media_record_gps_distance(
    old_record: Optional[PhotoRecord],
    new_record: PhotoRecord,
) -> Optional[float]:
    """Return GPS displacement, or None when both records have no GPS."""
    old_pair = None if old_record is None else (old_record.latitude, old_record.longitude)
    new_pair = (new_record.latitude, new_record.longitude)
    if old_pair == (None, None) and new_pair == (None, None):
        return None
    if old_pair is None or None in old_pair or None in new_pair:
        return math.inf
    return distance_meters(float(old_pair[0]), float(old_pair[1]), float(new_pair[0]), float(new_pair[1]))


def _control_media_record(entry: dict[str, Any], media_path: Path) -> Optional[PhotoRecord]:
    """Build a comparison-only record from one saved control-file media row."""
    timestamp = entry.get("datetime")
    if not isinstance(timestamp, datetime):
        return None
    parts = [part.strip() for part in str(entry.get("line", "")).split("|")]
    latitude = None
    longitude = None
    if len(parts) > 2 and "," in parts[2] and parts[2] != GPS_NOT_AVAILABLE:
        try:
            latitude_text, longitude_text = parts[2].split(",", 1)
            latitude = float(latitude_text)
            longitude = float(longitude_text)
        except ValueError:
            latitude = None
            longitude = None
    place = str(entry.get("place", "") or "").strip()
    if not is_resolved_place_name(place):
        place = None
    return PhotoRecord(
        source_filename=media_path.name,
        display_filename=media_path.name,
        photo_path=media_path,
        json_path=media_sidecar_path(media_path),
        photo_datetime=timestamp,
        latitude=latitude,
        longitude=longitude,
        place=place,
        place_details=None,
        source="control_file",
        geocode_requested=False,
        place_updated=False,
        debug_info={"selected_source": "control_file"},
        gps_source="control_file" if latitude is not None and longitude is not None else None,
        datetime_source="control_file",
    )


def _control_section_description(entries: list[dict[str, Any]], entry_index: int) -> str:
    """Return a compact human-readable description of one control section."""
    entry = entries[entry_index]
    day = entry.get("date")
    prefix = day.isoformat() if isinstance(day, date) else "No date"
    header_index = None
    end_index = len(entries)
    for start, end in _control_section_ranges(entries):
        if start < entry_index < end:
            header_index, end_index = start, end
            break
    if header_index is None:
        return prefix
    map_entry = next(
        (
            candidate
            for candidate in entries[header_index + 1 : end_index]
            if candidate.get("type") in {"map", "map_before", "map_after", "media_map"}
        ),
        None,
    )
    if map_entry is None:
        return f"{prefix} - Media section"
    relation = {
        "map": "Track",
        "map_before": "Day before",
        "map_after": "Day after",
        "media_map": "Media map",
    }.get(str(map_entry.get("type")), "Map")
    return f"{prefix} - {relation}"


def _proposed_media_section(
    record: PhotoRecord,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
) -> str:
    sections = build_control_sections([record], tracks_summary, sort_date_sections_by_tracks)
    if not sections:
        return record.photo_datetime.date().isoformat()
    section = sections[0]
    maps = section.get("maps", [])
    relation = "Media section"
    if maps:
        relation = {
            "Map": "Track",
            "MapBefore": "Day before",
            "MapAfter": "Day after",
            "MediaMap": "Media map",
        }.get(str(maps[0][0]), str(maps[0][0]))
    return f"{section['date'].isoformat()} - {relation}"


def _control_track_map_file_exists(project_dir: Path, filename: str) -> bool:
    """Return whether a canonical or alternate Track Map variant exists."""
    name = Path(str(filename)).name
    names = track_map_variant_names(name, prefer_time_lapse=False) if re.match(r"^\d+_", name) else [name]
    return any(
        candidate.exists() and candidate.is_file()
        for candidate_name in names
        for candidate in (project_dir / candidate_name, project_dir / "trackimages" / candidate_name)
    )


def _canonical_control_track_map_name(filename: str) -> str:
    canonical = canonical_track_map_name(Path(str(filename)).name)
    return normalize_track_plot_filename_for_match(canonical)


def analyze_track_map_reference_updates(
    project_dir: Path | str,
    control_file: Path | str,
    tracks_summary_path: Optional[Path | str],
    *,
    control_entries: Optional[list[dict[str, Any]]] = None,
    tracks_summary: Optional[TracksSummary] = None,
    summary_current: bool = True,
    sort_date_sections_by_tracks: bool = False,
) -> TrackMapReferenceUpdatePlan:
    """Compare control-file Track Map directives with one current summary."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    control_path = Path(control_file).expanduser().resolve(strict=False)
    summary_path = (
        Path(tracks_summary_path).expanduser().resolve(strict=False)
        if tracks_summary_path is not None else None
    )
    plan = TrackMapReferenceUpdatePlan(summary_current=bool(summary_current))
    if not control_path.is_file():
        plan.warning = "No slide show control file is available."
        return plan
    if summary_path is None or not summary_path.is_file():
        plan.warning = "Track summary missing; run Generate and Update Maps first."
        return plan
    if tracks_summary is None:
        tracks_summary = load_tracks_summary(summary_path, control_path)
    if tracks_summary is None:
        plan.warning = "Track summary is invalid; run Generate and Update Maps first."
        return plan
    plan.summary_available = True
    if not summary_current:
        plan.warning = "Maps or their summary are not current; run Generate and Update Maps first."
        return plan
    if control_entries is None:
        control_entries = parse_control_file_entries(
            control_path.read_text(encoding="utf-8").splitlines()
        )

    existing_names = {
        normalize_filename_for_match(str(entry.get("name", "")))
        for entry in control_entries if entry.get("name")
    }
    existing_map_keys = {
        (
            _canonical_control_track_map_name(str(entry.get("name", ""))),
            entry.get("date"),
        )
        for entry in control_entries
        if entry.get("type") == "map" and entry.get("name")
    }
    expected_map_names = {
        _canonical_control_track_map_name(Path(track.track_plot_image_filename).name)
        for track in tracks_summary.tracks if track.track_plot_image_filename
    }
    expected_map_dates: dict[str, set[date]] = {}
    for track in tracks_summary.tracks:
        if not track.track_plot_image_filename:
            continue
        key = _canonical_control_track_map_name(track.track_plot_image_filename)
        expected_map_dates.setdefault(key, set()).add(track.start_time.date())
    expected_by_number = {
        int(track.original_sequence_number): Path(track.track_plot_image_filename).name
        for track in tracks_summary.tracks if track.track_plot_image_filename
    }
    if tracks_summary.overview_image:
        overview_filename = Path(tracks_summary.overview_image).name
        overview_name = normalize_filename_for_match(overview_filename)
        if (
            overview_name not in existing_names
            and _control_track_map_file_exists(project, overview_filename)
        ):
            plan.missing_overview.append(overview_filename)
    for track in tracks_summary.tracks:
        image_name = Path(track.track_plot_image_filename).name
        if not image_name or not _control_track_map_file_exists(project, image_name):
            continue
        expected_key = (_canonical_control_track_map_name(image_name), track.start_time.date())
        if expected_key not in existing_map_keys:
            plan.missing_tracks.append(image_name)

    expected_overview = (
        normalize_filename_for_match(Path(tracks_summary.overview_image).name)
        if tracks_summary.overview_image else None
    )
    for entry in control_entries:
        entry_type = entry.get("type")
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        if entry_type == "overview":
            if (
                normalize_filename_for_match(name) != expected_overview
                or not _control_track_map_file_exists(project, name)
            ):
                plan.obsolete_overview.append(name)
        elif entry_type == "map":
            normalized_map = _canonical_control_track_map_name(name)
            if (
                normalized_map not in expected_map_names
                or entry.get("date") not in expected_map_dates.get(normalized_map, set())
                or not _control_track_map_file_exists(project, name)
            ):
                plan.obsolete_tracks.append(name)
        elif entry_type in {"map_before", "map_after"}:
            normalized = _canonical_control_track_map_name(name)
            number_match = re.match(r"^0*(\d+)_", Path(name).name)
            entry_date = entry.get("date")
            date_candidates = []
            if entry_date is not None:
                target_date = entry_date + timedelta(
                    days=1 if entry_type == "map_before" else -1
                )
                date_candidates = [
                    track for track in tracks_summary.tracks
                    if track.start_time.date() == target_date
                    and track.track_plot_image_filename
                ]
            replacement = None
            if len(date_candidates) == 1:
                replacement = Path(date_candidates[0].track_plot_image_filename).name
            elif date_candidates and number_match:
                old_number = int(number_match.group(1))
                replacement = next(
                    (
                        Path(track.track_plot_image_filename).name for track in date_candidates
                        if track.original_sequence_number == old_number
                    ),
                    Path(date_candidates[0].track_plot_image_filename).name,
                )
            elif date_candidates:
                replacement = Path(date_candidates[0].track_plot_image_filename).name
            elif entry_date is None and number_match:
                replacement = expected_by_number.get(int(number_match.group(1)))
            if (
                replacement
                and normalized == _canonical_control_track_map_name(replacement)
                and _control_track_map_file_exists(project, name)
            ):
                continue
            if replacement and _control_track_map_file_exists(project, replacement):
                plan.special_updates.append((name, replacement, str(entry_type)))
            else:
                plan.obsolete_tracks.append(name)

    ordered_tracks = sorted(
        tracks_summary.tracks,
        key=(
            (lambda item: (item.original_sequence_number, item.start_time))
            if sort_date_sections_by_tracks
            else (lambda item: (item.start_time, item.original_sequence_number))
        ),
    )
    expected_order = {
        _canonical_control_track_map_name(track.track_plot_image_filename): index
        for index, track in enumerate(ordered_tracks)
        if track.track_plot_image_filename
    }
    map_rows = []
    for index, entry in enumerate(control_entries):
        if entry.get("type") != "map" or not entry.get("name"):
            continue
        key = _canonical_control_track_map_name(str(entry["name"]))
        if key in expected_order:
            map_rows.append((index, expected_order[key], str(entry["name"])))
    if map_rows:
        tails: list[int] = []
        predecessors = [-1] * len(map_rows)
        tail_indexes: list[int] = []
        for row_index, (_entry_index, rank, _name) in enumerate(map_rows):
            position = bisect.bisect_left(tails, rank)
            if position == len(tails):
                tails.append(rank)
                tail_indexes.append(row_index)
            else:
                tails[position] = rank
                tail_indexes[position] = row_index
            if position > 0:
                predecessors[row_index] = tail_indexes[position - 1]
        longest_indexes = set()
        cursor = tail_indexes[-1]
        while cursor >= 0:
            longest_indexes.add(cursor)
            cursor = predecessors[cursor]
        for row_index, (entry_index, _rank, name) in enumerate(map_rows):
            if row_index in longest_indexes:
                continue
            section_start, section_end = _date_section_bounds_containing(
                control_entries,
                entry_index,
            )
            section = control_entries[section_start:section_end]
            normal_maps = [item for item in section if item.get("type") == "map"]
            media_rows = [item for item in section if item.get("type") == "media"]
            if len(normal_maps) == 1 and not media_rows:
                plan.reordered_tracks.append(name)
    return plan


def analyze_media_updates(
    project_dir: Path | str,
    media_paths: list[Path | str] | tuple[Path | str, ...],
    *,
    control_file: Optional[Path | str] = None,
    tracks_summary_path: Optional[Path | str] = None,
    actions: Optional[dict[str, str]] = None,
    sort_date_sections_by_tracks: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    detail_callback: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    control_entries: Optional[list[dict[str, Any]]] = None,
    tracks_summary: Optional[TracksSummary] = None,
    control_signature: Optional[tuple[int, int]] = None,
    place_equivalence_m: float = DEFAULT_PLACE_GPS_EQUIVALENCE_M,
) -> MediaUpdatePlan:
    """Analyze selected media without changing sidecars or the control file."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    control_path = Path(control_file).expanduser().resolve(strict=False) if control_file else None
    summary_path = Path(tracks_summary_path).expanduser().resolve(strict=False) if tracks_summary_path else None
    entries = list(control_entries or [])
    if control_path is not None and control_path.is_file():
        if control_signature is None:
            control_stat = control_path.stat()
            control_signature = (int(control_stat.st_size), int(control_stat.st_mtime_ns))
        if control_entries is None:
            entries = parse_control_file_entries(control_path.read_text(encoding="utf-8").splitlines())
    if tracks_summary is None and summary_path and summary_path.is_file():
        tracks_summary = load_tracks_summary(summary_path, control_path or project)
    place_equivalence_m = max(0.0, float(place_equivalence_m))
    gps_resolver = (
        LazyTrackGpsResolver(tracks_summary, place_equivalence_m)
        if tracks_summary is not None
        else None
    )
    media_entry_indexes: dict[str, list[int]] = {}
    for entry_index, entry in enumerate(entries):
        if entry.get("type") != "media":
            continue
        normalized_entry_name = normalize_filename_for_match(str(entry.get("name", "")))
        media_entry_indexes.setdefault(normalized_entry_name, []).append(entry_index)
        if gps_resolver is not None:
            reference_path = project / Path(str(entry.get("name", ""))).name
            reference_record = _control_media_record(entry, reference_path)
            if reference_record is not None:
                gps_resolver._remember_reference_record(reference_record)
    selected_actions = {normalize_filename_for_match(key): str(value) for key, value in (actions or {}).items()}
    results: list[MediaUpdateItem] = []
    warnings: list[str] = []
    paths = [Path(path).expanduser().resolve(strict=False) for path in media_paths]
    for index, media_path in enumerate(paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Aborted.")
        check_cancelled()
        if progress_callback is not None:
            progress_callback(index - 1, len(paths), media_path.name)
        if media_path.parent != project or not media_path.is_file():
            warning = f"Skipped {media_path}: not an existing project media file."
            warnings.append(warning)
            if detail_callback is not None:
                detail_callback(warning)
            continue
        status, old_payload, reason = validate_media_sidecar(media_path)
        analyzed_media_signature = media_file_signature(media_path)
        sidecar_path = media_sidecar_path(media_path)
        if sidecar_path.is_file():
            sidecar_stat = sidecar_path.stat()
            analyzed_sidecar_signature = (int(sidecar_stat.st_size), int(sidecar_stat.st_mtime_ns))
        else:
            analyzed_sidecar_signature = None
        freshness = media_sidecar_freshness(media_path, old_payload) if status == "available" else status
        old_record = (
            record_from_sidecar_payload(old_payload, media_sidecar_path(media_path), media_path)
            if status == "available" and isinstance(old_payload, dict)
            else None
        )
        normalized_name = normalize_filename_for_match(media_path.name)
        occurrences = list(media_entry_indexes.get(normalized_name, ()))
        control_record = (
            _control_media_record(entries[occurrences[0]], media_path)
            if len(occurrences) == 1
            else None
        )
        comparison_record = copy.deepcopy(old_record or control_record)
        action = selected_actions.get(normalized_name)
        if action not in {"use_sidecar", "refresh", "repair", "skip"}:
            if status != "available":
                action = "repair"
            elif freshness == "changed" or occurrences:
                action = "refresh"
            else:
                action = "use_sidecar"
        if action == "skip":
            continue
        if action == "use_sidecar" and old_record is None:
            action = "repair"

        sidecar_write_required = action in {"refresh", "repair"}
        if action in {"refresh", "repair"}:
            new_record = build_record_from_photo(media_path, False, {}, [], 0.0, True)
            new_record.raw_metadata = dict(old_payload or {})
        else:
            new_record = old_record
            assert new_record is not None

        if gps_resolver is not None and gps_resolver.apply(new_record):
            sidecar_write_required = True
        if detail_callback is not None:
            local_datetime = (
                new_record.photo_datetime.astimezone()
                if new_record.photo_datetime.tzinfo is not None
                else new_record.photo_datetime
            )
            date_text = local_datetime.isoformat(sep=" ", timespec="seconds")
            gps_text = (
                f"{new_record.latitude:.6f}, {new_record.longitude:.6f}"
                if new_record.latitude is not None
                and new_record.longitude is not None
                else "not available"
            )
            verb = "Using" if action == "use_sidecar" else "Extracted"
            detail_callback(
                f"{verb} {media_path.name}: date {date_text}; GPS {gps_text}."
            )
            if (
                new_record.latitude is None
                and new_record.longitude is None
                and tracks_summary is None
            ):
                detail_callback(
                    f"{media_path.name}: No embedded GPS; track inference deferred until maps are ready."
                )
        displacement = _media_record_gps_distance(comparison_record, new_record)
        old_has_gps = bool(
            comparison_record is not None
            and comparison_record.latitude is not None
            and comparison_record.longitude is not None
        )
        new_has_gps = bool(
            new_record.latitude is not None and new_record.longitude is not None
        )
        gps_changed = (
            old_has_gps != new_has_gps
            or (displacement is not None and displacement > place_equivalence_m)
        )
        if (
            comparison_record is not None
            and not gps_changed
            and is_resolved_place_name(comparison_record.place)
        ):
            new_record.place = comparison_record.place
            new_record.place_details = comparison_record.place_details
        elif gps_changed:
            new_record.place = None
            new_record.place_details = None
        place_update_recommended = (
            new_record.latitude is not None
            and new_record.longitude is not None
            and (
                gps_changed
                or comparison_record is None
                or (
                    old_record is None
                    and not is_resolved_place_name(comparison_record.place)
                )
                or (
                    old_record is not None
                    and is_resolved_place_name(old_record.place)
                    and not record_place_matches_gps(
                        old_record,
                        place_equivalence_m,
                    )
                )
            )
        )
        staged_payload = build_record_sidecar_payload(new_record) if sidecar_write_required else dict(old_payload or {})
        current_section = _control_section_description(entries, occurrences[0]) if occurrences else "Not included"
        proposed_section = _proposed_media_section(new_record, tracks_summary, sort_date_sections_by_tracks)
        conflict = None
        if len(occurrences) > 1:
            conflict = f"{media_path.name} occurs {len(occurrences)} times in the control file"
            warnings.append(conflict)
        baseline_day = (
            comparison_record.photo_datetime.date()
            if comparison_record is not None
            else entries[occurrences[0]].get("date")
            if len(occurrences) == 1
            else None
        )
        placement_changed = bool(
            baseline_day != new_record.photo_datetime.date()
            or gps_changed
        )
        reposition = bool(
            occurrences
            and placement_changed
            and current_section != proposed_section
            and conflict is None
        )
        results.append(
            MediaUpdateItem(
                media_path=media_path,
                action=action,
                sidecar_status=status,
                freshness=freshness,
                included_count=len(occurrences),
                old_record=comparison_record,
                new_record=new_record,
                staged_payload=staged_payload,
                current_section=current_section,
                proposed_section=proposed_section,
                reposition=reposition,
                gps_changed=gps_changed,
                place_update_recommended=place_update_recommended,
                analyzed_media_signature=analyzed_media_signature,
                analyzed_sidecar_signature=analyzed_sidecar_signature,
                sidecar_write_required=sidecar_write_required,
                control_conflict=conflict or (reason if status == "invalid" else None),
            )
        )
    if progress_callback is not None:
        progress_callback(len(paths), len(paths), "")
    if gps_resolver is not None:
        gps_resolver.emit_summary()
    return MediaUpdatePlan(
        project_dir=project,
        control_file=control_path,
        tracks_summary_path=summary_path,
        items=results,
        tracks_summary=tracks_summary,
        sort_date_sections_by_tracks=sort_date_sections_by_tracks,
        control_signature=control_signature,
        warnings=warnings,
    )


def analyze_control_file_updates(
    project_dir: Path | str,
    media_paths: list[Path | str] | tuple[Path | str, ...],
    *,
    control_file: Optional[Path | str],
    tracks_summary_path: Optional[Path | str],
    actions: Optional[dict[str, str]] = None,
    sort_date_sections_by_tracks: bool = False,
    summary_current: bool = True,
    media_only: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    detail_callback: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    place_equivalence_m: float = DEFAULT_PLACE_GPS_EQUIVALENCE_M,
) -> ControlFileUpdatePlan:
    """Analyze Track Map references and selected media from one control snapshot."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    control_path = Path(control_file).expanduser().resolve(strict=False) if control_file else None
    summary_path = Path(tracks_summary_path).expanduser().resolve(strict=False) if tracks_summary_path else None
    entries: list[dict[str, Any]] = []
    control_signature = None
    if control_path is not None and control_path.is_file():
        stat = control_path.stat()
        control_signature = (int(stat.st_size), int(stat.st_mtime_ns))
        entries = parse_control_file_entries(control_path.read_text(encoding="utf-8").splitlines())
    tracks_summary = (
        load_tracks_summary(summary_path, control_path or project)
        if summary_path is not None and summary_path.is_file() else None
    )
    if media_only:
        track_plan = TrackMapReferenceUpdatePlan(
            summary_available=False,
            summary_current=True,
        )
        usable_tracks_summary = None
    else:
        track_plan = (
            analyze_track_map_reference_updates(
                project,
                control_path,
                summary_path,
                control_entries=entries,
                tracks_summary=tracks_summary,
                summary_current=summary_current,
                sort_date_sections_by_tracks=sort_date_sections_by_tracks,
            )
            if control_path is not None
            else TrackMapReferenceUpdatePlan(warning="No slide show control file is available.")
        )
        usable_tracks_summary = tracks_summary if summary_current else None
    media_plan = analyze_media_updates(
        project,
        media_paths,
        control_file=control_path,
        tracks_summary_path=summary_path,
        actions=actions,
        sort_date_sections_by_tracks=sort_date_sections_by_tracks,
        progress_callback=progress_callback,
        detail_callback=detail_callback,
        cancel_event=cancel_event,
        control_entries=entries,
        tracks_summary=usable_tracks_summary,
        control_signature=control_signature,
        place_equivalence_m=place_equivalence_m,
    )
    if not media_only and not summary_current:
        warning = "Maps must be generated or updated before media can be added or repositioned."
        media_plan.warnings.append(warning)
        for item in media_plan.items:
            if item.included_count == 0 or item.reposition:
                item.reposition = False
                item.control_update_pending = True
                item.control_conflict = warning
    return ControlFileUpdatePlan(media=media_plan, track_maps=track_plan)


def _write_json_atomic(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=f".{destination.name}.", suffix=".tmp",
        dir=destination.parent, delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_path, destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _apply_track_map_reference_updates(
    entries: list[dict[str, Any]],
    plan: TrackMapReferenceUpdatePlan,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool,
) -> tuple[int, int, int, int]:
    """Apply one validated Track Map reference plan to parsed control entries."""
    obsolete_overview = {
        normalize_filename_for_match(name) for name in plan.obsolete_overview
    }
    obsolete_tracks = {
        normalize_track_plot_filename_for_match(name) for name in plan.obsolete_tracks
    }
    kept = []
    removed = 0
    for entry in entries:
        entry_type = entry.get("type")
        name = str(entry.get("name", "")).strip()
        remove = (
            entry_type == "overview"
            and normalize_filename_for_match(name) in obsolete_overview
        ) or (
            entry_type in {"map", "map_before", "map_after"}
            and normalize_track_plot_filename_for_match(name) in obsolete_tracks
        )
        if remove:
            removed += 1
        else:
            kept.append(entry)
    entries[:] = kept

    reordered_names = {
        _canonical_control_track_map_name(name) for name in plan.reordered_tracks
    }
    reordered_sections = []
    seen_sections = set()
    for index, entry in enumerate(entries):
        if (
            entry.get("type") != "map"
            or _canonical_control_track_map_name(str(entry.get("name", "")))
            not in reordered_names
        ):
            continue
        start, end = _date_section_bounds_containing(entries, index)
        if (start, end) not in seen_sections:
            reordered_sections.append((start, end))
            seen_sections.add((start, end))
    for start, end in reversed(reordered_sections):
        del entries[start:end]

    replacements = {
        normalize_track_plot_filename_for_match(old): new
        for old, new, _entry_type in plan.special_updates
    }
    replaced = 0
    for entry in entries:
        if entry.get("type") not in {"map_before", "map_after"}:
            continue
        old_name = str(entry.get("name", "")).strip()
        replacement = replacements.get(normalize_track_plot_filename_for_match(old_name))
        if replacement is None or replacement == old_name:
            continue
        keyword = "MapBefore" if entry.get("type") == "map_before" else "MapAfter"
        entry["name"] = replacement
        entry["line"] = f"#{keyword}: {replacement}"
        replaced += 1

    added = 0
    if plan.missing_overview:
        overview_name = plan.missing_overview[0]
        entries.insert(
            0,
            {
                "line": f"#Overviewmap: {overview_name}",
                "type": "overview",
                "date": None,
                "name": overview_name,
            },
        )
        added += 1
    if tracks_summary is not None:
        missing = {
            _canonical_control_track_map_name(name) for name in plan.missing_tracks
        }
        reordered = {
            _canonical_control_track_map_name(name) for name in plan.reordered_tracks
        }
        tracks = sorted(
            tracks_summary.tracks,
            key=lambda track: (
                track.original_sequence_number if sort_date_sections_by_tracks else track.start_time,
                track.start_time,
            ),
        )
        for track in tracks:
            track_key = _canonical_control_track_map_name(track.track_plot_image_filename)
            if track_key not in missing and track_key not in reordered:
                continue
            insert_map_entry(entries, track, tracks_summary, sort_date_sections_by_tracks)
            if track_key in missing:
                added += 1
    return added, replaced, removed, len(reordered_sections)


def commit_media_update_plan(
    plan: MediaUpdatePlan,
    *,
    update_place_names: bool = True,
    place_distance_m: float = 0.0,
    geocode_timeout_seconds: float = 10.0,
    geocode_pacing_min_seconds: float = GEOCODE_PACING_MIN_SECONDS,
    geocode_pacing_max_seconds: float = GEOCODE_PACING_MAX_SECONDS,
    media_map_options: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    _track_map_plan: Optional[TrackMapReferenceUpdatePlan] = None,
    _control_update_result: Optional[ControlFileUpdateResult] = None,
) -> MediaUpdateResult:
    """Commit an approved selective update, with the control file replaced last."""
    result = MediaUpdateResult()
    selected_items = [item for item in plan.items if item.apply_update]
    control_entries = []
    if plan.control_file is not None and plan.control_file.is_file():
        current_stat = plan.control_file.stat()
        current_signature = (int(current_stat.st_size), int(current_stat.st_mtime_ns))
        if plan.control_signature is not None and current_signature != plan.control_signature:
            raise RuntimeError("The control file changed after analysis; analyze the selected media again.")
        control_entries = parse_control_file_entries(plan.control_file.read_text(encoding="utf-8").splitlines())
    if _track_map_plan is not None and plan.control_file is not None:
        added, replaced_count, removed, reordered = _apply_track_map_reference_updates(
            control_entries,
            _track_map_plan,
            plan.tracks_summary,
            plan.sort_date_sections_by_tracks,
        )
        if _control_update_result is not None:
            _control_update_result.map_entries_added = added
            _control_update_result.map_entries_replaced = replaced_count
            _control_update_result.map_entries_removed = removed
            _control_update_result.map_entries_reordered = reordered
    affected_dates: set[date] = set()
    geocode_cache: dict[tuple[float, float], tuple[Optional[str], Optional[dict[str, Any]]]] = {}
    known_places: list[KnownPlace] = []
    total = len(selected_items)
    for index, item in enumerate(selected_items, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Aborted.")
        check_cancelled()
        if media_file_signature(item.media_path) != item.analyzed_media_signature:
            raise RuntimeError(f"{item.media_path.name} changed after analysis; analyze it again.")
        current_sidecar = media_sidecar_path(item.media_path)
        current_sidecar_signature = None
        if current_sidecar.is_file():
            current_sidecar_stat = current_sidecar.stat()
            current_sidecar_signature = (
                int(current_sidecar_stat.st_size), int(current_sidecar_stat.st_mtime_ns)
            )
        if current_sidecar_signature != item.analyzed_sidecar_signature:
            raise RuntimeError(f"{current_sidecar.name} changed after analysis; analyze it again.")
        if progress_callback is not None:
            progress_callback(index - 1, total, item.media_path.name)
        record = item.new_record
        old_gps_source = (
            item.old_record.gps_source
            if item.old_record is not None
            else None
        )
        if record.gps_source == "track_time_interpolation":
            if old_gps_source == "track_time_interpolation":
                if item.sidecar_write_required:
                    result.gps_refreshed += 1
            else:
                result.gps_inferred += 1
        if item.place_update_recommended and update_place_names:
            resolve_place_for_record(
                record, geocode_cache, known_places, place_distance_m, False,
                geocode_timeout_seconds, geocode_pacing_min_seconds, geocode_pacing_max_seconds,
            )
            if record.place_updated and is_resolved_place_name(record.place):
                item.sidecar_write_required = True
                result.places_updated += 1
        elif item.old_record is not None and is_resolved_place_name(record.place):
            result.places_preserved += 1
        if item.sidecar_write_required:
            item.staged_payload = build_record_sidecar_payload(record)

        if item.control_update_pending:
            result.control_rows_pending += 1
            continue
        if not control_entries or item.control_conflict and item.included_count > 1:
            if item.control_conflict and item.included_count > 1:
                item.control_update_pending = True
                result.control_rows_pending += 1
            continue
        normalized_name = normalize_filename_for_match(item.media_path.name)
        occurrences = [
            entry_index
            for entry_index, entry in enumerate(control_entries)
            if entry.get("type") == "media"
            and normalize_filename_for_match(str(entry.get("name", ""))) == normalized_name
        ]
        old_day = control_entries[occurrences[0]].get("date") if len(occurrences) == 1 else None
        if not occurrences:
            insert_classified_media_entry(
                control_entries, record, plan.tracks_summary, plan.sort_date_sections_by_tracks,
            )
            affected_dates.add(record.photo_datetime.date())
            result.rows_added += 1
        elif len(occurrences) == 1 and item.reposition:
            control_entries.pop(occurrences[0])
            insert_classified_media_entry(
                control_entries, record, plan.tracks_summary, plan.sort_date_sections_by_tracks,
            )
            if isinstance(old_day, date):
                affected_dates.add(old_day)
            affected_dates.add(record.photo_datetime.date())
            result.rows_moved += 1
        elif len(occurrences) == 1 and old_day == record.photo_datetime.date():
            control_entries[occurrences[0]].update(
                line=sorted_media_output_line(record),
                datetime=record.photo_datetime,
                name=record.source_filename,
            )
            if item.gps_changed:
                affected_dates.add(record.photo_datetime.date())
            result.rows_updated += 1
        else:
            item.control_update_pending = True
            result.control_rows_pending += 1

    staged_control_text = None
    staged_map_options = None
    map_temp_dir = None
    if control_entries and plan.control_file is not None:
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Aborted.")
        if media_map_options is not None and affected_dates:
            map_temp_dir = tempfile.TemporaryDirectory(prefix="mycamino-media-maps-")
            staged_map_options = dict(media_map_options)
            staged_map_options["output_dir"] = map_temp_dir.name
            add_media_maps_to_control_entries(
                control_entries, plan.control_file, staged_map_options, affected_dates=affected_dates,
            )
            result.media_maps_regenerated = len(
                media_map_specs_from_control_entries(control_entries, affected_dates=affected_dates)
            )
        staged_control_text = "\n".join(entry["line"] for entry in control_entries) + "\n"

    if cancel_event is not None and cancel_event.is_set():
        if map_temp_dir is not None:
            map_temp_dir.cleanup()
        raise ProcessingCancelled("Aborted.")

    backup_dir = plan.project_dir / ".mycamino-control-backups" / (
        f"control-update-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    replaced: list[tuple[Path, Optional[Path]]] = []
    try:
        for item in selected_items:
            if not item.sidecar_write_required:
                continue
            destination = media_sidecar_path(item.media_path)
            backup = None
            if destination.exists():
                backup = backup_dir / destination.name
                shutil.copy2(destination, backup)
            replaced.append((destination, backup))
            _write_json_atomic(item.staged_payload, destination)
            result.refreshed_sidecars += 1
        if map_temp_dir is not None and media_map_options is not None:
            destination_dir = Path(str(media_map_options.get("output_dir", "")))
            destination_dir.mkdir(parents=True, exist_ok=True)
            for source in Path(map_temp_dir.name).iterdir():
                destination = destination_dir / source.name
                backup = None
                if destination.exists():
                    backup = backup_dir / source.name
                    shutil.copy2(destination, backup)
                replaced.append((destination, backup))
                os.replace(source, destination)
        if staged_control_text is not None and plan.control_file is not None:
            backup = backup_dir / plan.control_file.name
            shutil.copy2(plan.control_file, backup)
            replaced.append((plan.control_file, backup))
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=f".{plan.control_file.name}.", suffix=".tmp",
                dir=plan.control_file.parent, delete=False,
            ) as handle:
                temp_control = Path(handle.name)
                handle.write(staged_control_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_control, plan.control_file)
    except Exception:
        for destination, backup in reversed(replaced):
            if backup is None:
                destination.unlink(missing_ok=True)
            elif backup.exists():
                shutil.copy2(backup, destination)
        raise
    finally:
        if map_temp_dir is not None:
            map_temp_dir.cleanup()
    if progress_callback is not None:
        progress_callback(total, total, "")
    return result


def commit_control_file_update_plan(
    plan: ControlFileUpdatePlan,
    **options,
) -> ControlFileUpdateResult:
    """Commit Track Map references and selected media as one transaction."""
    result = ControlFileUpdateResult()
    result.media = commit_media_update_plan(
        plan.media,
        _track_map_plan=plan.track_maps,
        _control_update_result=result,
        **options,
    )
    return result


def record_for_merge_media(
    params: Params,
    media_path: Path,
    gps_resolver: Optional[LazyTrackGpsResolver] = None,
) -> PhotoRecord:
    """Build or load metadata for one media file being merged."""
    json_path = get_json_path_for_photo(media_path)
    record = None if params.ignorejson else load_record_from_json(json_path, media_path)
    if record is not None:
        if gps_resolver is not None and gps_resolver.apply(record):
            write_record_json(record, set())
        return record
    record = build_record_from_photo(
        media_path,
        params.getclearnames,
        {},
        [],
        params.distance,
        params.debug,
    )
    if gps_resolver is not None:
        gps_resolver.apply(record)
        if params.getclearnames and needs_place_repair(record):
            resolve_place_for_record(record, {}, [], params.distance, params.debug)
    write_record_json(record, set())
    return record


def merge_sorted_control_file(params: Params) -> Path:
    """Merge new track maps/media into an existing user-edited sorted list."""
    sorted_output_path = build_sorted_output_path(params.photolist)
    if not sorted_output_path.exists():
        raise FileNotFoundError(f"sorted photolist does not exist: {sorted_output_path}")

    existing_lines = sorted_output_path.read_text(encoding="utf-8").splitlines()
    entries = parse_control_file_entries(existing_lines)
    existing_names = {
        normalize_filename_for_match(str(entry.get("name", "")))
        for entry in entries
        if entry.get("name")
    }
    existing_map_names = {
        normalize_track_plot_filename_for_match(str(entry.get("name", "")))
        for entry in entries
        if entry.get("type") == "map" and entry.get("name")
    }
    existing_media_names = {
        normalize_filename_for_match(str(entry.get("name", "")))
        for entry in entries
        if entry.get("type") == "media" and entry.get("name")
    }
    tracks_summary = load_tracks_summary(params.merge_tracks or params.tracks, params.photolist)
    gps_resolver = (
        LazyTrackGpsResolver(tracks_summary, params.distance)
        if params.infer_gps_from_tracks and params.merge_media and tracks_summary is not None
        else None
    )
    if gps_resolver is not None:
        for entry in entries:
            if entry.get("type") != "media":
                continue
            reference_path = params.photodir / Path(str(entry.get("name", ""))).name
            reference_record = _control_media_record(entry, reference_path)
            if reference_record is not None:
                gps_resolver._remember_reference_record(reference_record)
    inserted_maps = 0
    inserted_media = 0

    if tracks_summary is not None:
        if tracks_summary.overview_image and normalize_filename_for_match(tracks_summary.overview_image) not in existing_names:
            entries.insert(
                0,
                {
                    "line": f"#Overviewmap: {tracks_summary.overview_image}",
                    "type": "overview",
                    "name": tracks_summary.overview_image,
                    "date": None,
                },
            )
            existing_names.add(normalize_filename_for_match(tracks_summary.overview_image))
            inserted_maps += 1
        track_order = sorted(
            tracks_summary.tracks,
            key=lambda track: (
                date_order_key(track.start_time.date(), tracks_summary, params.sort_date_sections_by_tracks),
                track.original_sequence_number if params.sort_date_sections_by_tracks else track.start_time,
                track.track_plot_image_filename.lower(),
            ),
        )
        for track in track_order:
            normalized_name = normalize_filename_for_match(track.track_plot_image_filename)
            normalized_map_name = normalize_track_plot_filename_for_match(track.track_plot_image_filename)
            if (
                not track.track_plot_image_filename
                or normalized_map_name in existing_map_names
            ):
                continue
            insert_map_entry(entries, track, tracks_summary, params.sort_date_sections_by_tracks)
            existing_names.add(normalized_name)
            existing_map_names.add(normalized_map_name)
            inserted_maps += 1

    media_records = [
        record_for_merge_media(params, media_path, gps_resolver)
        for media_path in params.merge_media
    ]
    for record in sort_records_for_output(media_records, tracks_summary, params.sort_date_sections_by_tracks):
        normalized_name = normalize_filename_for_match(record.source_filename)
        if normalized_name in existing_names or normalized_name in existing_media_names:
            continue
        insert_classified_media_entry(entries, record, tracks_summary, params.sort_date_sections_by_tracks)
        existing_names.add(normalized_name)
        existing_media_names.add(normalized_name)
        inserted_media += 1

    inserted_media_maps = add_media_maps_to_control_entries(
        entries,
        sorted_output_path,
        params.media_map_options,
    )

    sorted_output_path.write_text("\n".join(entry["line"] for entry in entries) + "\n", encoding="utf-8")
    print(
        f"Merged {inserted_maps} track map/overview line(s), {inserted_media_maps} media map(s), "
        f"and {inserted_media} media line(s) into {sorted_output_path}",
        flush=True,
    )
    if gps_resolver is not None:
        gps_resolver.emit_summary()
    return sorted_output_path


def track_endpoint_entry_matches(
    entry: object,
    latitude: float,
    longitude: float,
    radius_m: float,
) -> bool:
    """Return whether a stored endpoint place still belongs to the endpoint."""
    if not isinstance(entry, dict) or not is_resolved_place_name(entry.get("place")):
        return False
    stored_latitude = _optional_float(entry.get("latitude"))
    stored_longitude = _optional_float(entry.get("longitude"))
    if stored_latitude is None or stored_longitude is None:
        coordinate = entry.get("place_coordinate")
        if isinstance(coordinate, dict):
            stored_latitude = _optional_float(coordinate.get("latitude"))
            stored_longitude = _optional_float(coordinate.get("longitude"))
    if stored_latitude is None or stored_longitude is None:
        return False
    return distance_meters(
        float(stored_latitude),
        float(stored_longitude),
        float(latitude),
        float(longitude),
    ) <= max(0.0, float(radius_m))


_track_endpoint_entry_matches = track_endpoint_entry_matches


def _track_endpoint_entry(
    latitude: float,
    longitude: float,
    place: str,
    place_details: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Build the portable endpoint-place payload stored in Track Map sidecars."""
    entry: dict[str, Any] = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "place": str(place),
        "place_coordinate": {
            "latitude": float(latitude),
            "longitude": float(longitude),
        },
    }
    if isinstance(place_details, dict):
        entry["place_details"] = dict(place_details)
    return entry


def update_track_endpoint_places(
    tracks_summary: Optional[TracksSummary],
    params: Params,
    report: SidecarPlaceUpdateReport,
    geocode_cache: dict[
        tuple[float, float],
        tuple[Optional[str], Optional[dict[str, Any]]],
    ],
    known_places: list[KnownPlace],
    *,
    progress_offset: int = 0,
    progress_total: int = 0,
) -> int:
    """Resolve GPX start/end places before media and patch matching map sidecars."""
    if tracks_summary is None:
        return progress_offset
    track_payloads: list[tuple[TrackInfo, list[tuple[Path, dict[str, Any]]]]] = []
    for track in tracks_summary.tracks:
        payloads = []
        for sidecar_path in track.map_sidecar_paths:
            if not sidecar_path.is_file():
                continue
            try:
                payload = read_json_data(sidecar_path)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            stored_fingerprint = str(payload.get("track_fingerprint") or "").strip()
            if (
                track.track_fingerprint
                and stored_fingerprint != track.track_fingerprint
            ):
                continue
            payloads.append((sidecar_path, payload))
        track_payloads.append((track, payloads))

    # Existing endpoint places participate in the same radius-based reuse pool
    # as existing media places before any network request is made.
    for _track, payloads in track_payloads:
        for _path, payload in payloads:
            places = payload.get("track_endpoint_places")
            if not isinstance(places, dict):
                continue
            for entry in places.values():
                if not isinstance(entry, dict):
                    continue
                latitude = _optional_float(entry.get("latitude"))
                longitude = _optional_float(entry.get("longitude"))
                place = entry.get("place")
                if (
                    latitude is not None
                    and longitude is not None
                    and is_resolved_place_name(place)
                ):
                    known_places.append(
                        KnownPlace(
                            latitude=float(latitude),
                            longitude=float(longitude),
                            place=str(place),
                            place_details=(
                                entry.get("place_details")
                                if isinstance(entry.get("place_details"), dict)
                                else None
                            ),
                        )
                    )

    progress_index = progress_offset
    for track, payloads in track_payloads:
        endpoints = (
            ("start", track.start_latitude, track.start_longitude),
            ("end", track.end_latitude, track.end_longitude),
        )
        for endpoint_name, latitude, longitude in endpoints:
            if latitude is None or longitude is None:
                continue
            report.track_endpoints_total += 1
            progress_index += 1
            label = (
                f"Track {track.original_sequence_number} "
                f"{endpoint_name}: {track.track_name or 'unnamed track'}"
            )
            if params.progress_callback is not None:
                params.progress_callback(
                    progress_index,
                    max(progress_total, progress_index),
                    label,
                )
            if not payloads:
                report.track_endpoints_failed += 1
                print(
                    f"Skipping {label}: matching Track Map sidecar is missing; "
                    "run Generate and Update Maps first.",
                    flush=True,
                )
                continue
            existing_entry = None
            for _path, payload in payloads:
                places = payload.get("track_endpoint_places")
                candidate = places.get(endpoint_name) if isinstance(places, dict) else None
                if _track_endpoint_entry_matches(
                    candidate,
                    float(latitude),
                    float(longitude),
                    params.distance,
                ):
                    existing_entry = candidate
                    break
            if existing_entry is not None and not params.overwrite_reverse_geolocation:
                report.track_endpoints_complete += 1
                print(f"Keeping {label}: {existing_entry.get('place')}", flush=True)
                resolved_entry = dict(existing_entry)
                resolved_entry["latitude"] = float(latitude)
                resolved_entry["longitude"] = float(longitude)
                resolved_entry["place_coordinate"] = {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                }
            else:
                place, place_details, _debug = resolve_place_for_coordinate(
                    float(latitude),
                    float(longitude),
                    geocode_cache,
                    known_places,
                    params.distance,
                    params.debug,
                    params.geocode_timeout_seconds,
                    params.geocode_pacing_min_seconds,
                    params.geocode_pacing_max_seconds,
                )
                if not is_resolved_place_name(place):
                    report.track_endpoints_failed += 1
                    print(f"No place name found for {label}.", flush=True)
                    continue
                resolved_entry = _track_endpoint_entry(
                    float(latitude),
                    float(longitude),
                    str(place),
                    place_details,
                )
                report.track_endpoints_updated += 1
                known_places.append(
                    KnownPlace(
                        latitude=float(latitude),
                        longitude=float(longitude),
                        place=str(place),
                        place_details=place_details,
                    )
                )
                print(f"Updated {label}: {place}", flush=True)
            for sidecar_path, payload in payloads:
                endpoint_places = payload.get("track_endpoint_places")
                endpoint_places = (
                    dict(endpoint_places)
                    if isinstance(endpoint_places, dict)
                    else {}
                )
                endpoint_places[endpoint_name] = dict(resolved_entry)
                payload["track_endpoint_places"] = endpoint_places
        # Checkpoint completed tracks so an interrupted network/UI run resumes
        # from the next unfinished endpoint instead of repeating the whole tour.
        for sidecar_path, payload in payloads:
            original = read_json_data(sidecar_path)
            if original == payload:
                continue
            _write_json_atomic(payload, sidecar_path)
            report.track_sidecars_updated += 1
    return progress_index


def update_place_names_from_sidecars(params: Params) -> SidecarPlaceUpdateReport:
    """Reverse-geocode only GPS coordinates already stored in valid sidecars."""
    photo_files = filter_photo_files_by_name(
        select_photo_files(list_photo_files(params.photodir, params.file_filter), params.photos),
        params.photonames,
    )
    report = SidecarPlaceUpdateReport(total=len(photo_files))
    tracks_summary = (
        load_tracks_summary(params.tracks, params.photolist)
        if params.tracks is not None
        else None
    )
    endpoint_total = sum(
        int(track.start_latitude is not None and track.start_longitude is not None)
        + int(track.end_latitude is not None and track.end_longitude is not None)
        for track in (tracks_summary.tracks if tracks_summary is not None else ())
    )
    progress_total = report.total + endpoint_total
    geocode_cache: dict[tuple[float, float], tuple[Optional[str], Optional[dict[str, Any]]]] = {}
    known_places: list[KnownPlace] = []
    validated_records: dict[Path, PhotoRecord] = {}
    sidecar_states: dict[Path, tuple[str, Optional[dict[str, Any]], Optional[str]]] = {}

    for photo_path in photo_files:
        status, payload, reason = validate_media_sidecar(photo_path)
        sidecar_states[photo_path] = (status, payload, reason)
        if status != "available" or not isinstance(payload, dict):
            continue
        record = record_from_sidecar_payload(payload, media_sidecar_path(photo_path), photo_path)
        validated_records[photo_path] = record
        if (
            not params.overwrite_reverse_geolocation
            and record.latitude is not None
            and record.longitude is not None
            and record_place_matches_gps(record, params.distance)
        ):
            known_places.append(
                KnownPlace(
                    latitude=record.latitude,
                    longitude=record.longitude,
                    place=str(record.place),
                    place_details=record.place_details,
                )
            )

    if params.progress_callback is not None:
        params.progress_callback(0, progress_total, "")
    progress_offset = update_track_endpoint_places(
        tracks_summary,
        params,
        report,
        geocode_cache,
        known_places,
        progress_offset=0,
        progress_total=progress_total,
    )
    for photo_index, photo_path in enumerate(photo_files, start=1):
        check_cancelled()
        status, payload, reason = sidecar_states[photo_path]
        if status == "missing":
            report.missing += 1
            print(f"Skipping {photo_path.name}: missing metadata sidecar.", flush=True)
        elif status != "available" or not isinstance(payload, dict):
            report.invalid += 1
            print(f"Skipping {photo_path.name}: invalid metadata sidecar ({reason}).", flush=True)
        else:
            record = validated_records[photo_path]
            if record.latitude is None or record.longitude is None:
                report.gps_less += 1
                print(f"Skipping {photo_path.name}: metadata sidecar has no GPS coordinates.", flush=True)
            elif not params.overwrite_reverse_geolocation and record_place_matches_gps(
                record,
                params.distance,
            ):
                report.already_complete += 1
                print(f"Keeping {photo_path.name}: place name already available.", flush=True)
            else:
                record = resolve_place_for_record(
                    record,
                    geocode_cache,
                    known_places,
                    params.distance,
                    params.debug,
                    params.geocode_timeout_seconds,
                    params.geocode_pacing_min_seconds,
                    params.geocode_pacing_max_seconds,
                )
                if record.place_updated and is_resolved_place_name(record.place):
                    write_record_place_fields(record)
                    report.updated += 1
                    known_places.append(
                        KnownPlace(
                            latitude=float(record.latitude),
                            longitude=float(record.longitude),
                            place=str(record.place),
                            place_details=record.place_details,
                        )
                    )
                    print(f"Updated {photo_path.name}: {record.place}", flush=True)
                else:
                    report.failed += 1
                    print(f"No place name found for {photo_path.name}.", flush=True)
        if params.progress_callback is not None:
            params.progress_callback(
                progress_offset + photo_index,
                progress_total,
                photo_path.name,
            )

    print(
        "Place-name sidecar pass: "
        f"updated {report.updated}, already complete {report.already_complete}, "
        f"missing {report.missing}, invalid {report.invalid}, "
        f"without GPS {report.gps_less}, unresolved {report.failed}; "
        f"track endpoints updated {report.track_endpoints_updated}, "
        f"already complete {report.track_endpoints_complete}, "
        f"unresolved {report.track_endpoints_failed}, "
        f"Track Map sidecars written {report.track_sidecars_updated}.",
        flush=True,
    )
    return report


def collect_photo_location_and_dates(
    params: Params,
) -> Optional[MediaSidecarMigrationReport | SidecarPlaceUpdateReport]:
    """Process photos, emit live output, cache JSON, and write a sorted list."""
    check_cancelled()
    if params.migrate_media_sidecars:
        report = migrate_media_sidecars(
            params.photodir,
            getclearnames=params.getclearnames,
            distance=params.distance,
            debug=params.debug,
            progress_callback=params.progress_callback,
        )
        print(
            f"Migrated {len(report.migrated)} sidecars, regenerated {len(report.regenerated)}, "
            f"preserved {len(report.preserved)}, conflicts {len(report.conflicts)}.",
            flush=True,
        )
        for source_path, backup_path in [*report.preserved, *report.conflicts]:
            print(f"Preserved legacy sidecar: {source_path.name} -> {backup_path.name}", flush=True)
        return report
    if params.merge_tracks is not None or params.merge_media:
        merge_sorted_control_file(params)
        return None
    if params.redo_reverse_geolocation or params.overwrite_reverse_geolocation:
        return update_place_names_from_sidecars(params)
    tracks_summary = load_tracks_summary(params.tracks, params.photolist)
    gps_resolver = (
        LazyTrackGpsResolver(tracks_summary, params.distance)
        if params.infer_gps_from_tracks and tracks_summary is not None
        else None
    )
    photo_files = list_photo_files(params.photodir, params.file_filter)
    photo_files = exclude_tracks_images(photo_files, tracks_summary)
    if params.debug:
        emit_tracks_file_debug(photo_files, tracks_summary)
    photo_files = select_photo_files(photo_files, params.photos)
    photo_files = filter_photo_files_by_name(photo_files, params.photonames)
    geocode_cache: dict[tuple[float, float], tuple[Optional[str], Optional[dict[str, Any]]]] = {}
    known_places: list[KnownPlace] = []
    collected_records: list[PhotoRecord] = []
    protected_json_paths: set[Path] = set()
    if params.tracks is not None:
        try:
            protected_json_paths.add(params.tracks.resolve())
        except OSError:
            protected_json_paths.add(params.tracks)

    params.photolist.parent.mkdir(parents=True, exist_ok=True)
    total_photos = len(photo_files)
    if params.progress_callback is not None:
        params.progress_callback(0, total_photos, "")
    with params.photolist.open("w", encoding="utf-8") as output_file:
        for photo_index, photo_path in enumerate(photo_files, start=1):
            check_cancelled()
            json_path = get_json_path_for_photo(photo_path)
            record = None if params.ignorejson else load_record_from_json(json_path, photo_path)
            record_was_loaded = record is not None
            if record_was_loaded:
                record.geocode_requested = params.getclearnames
                record.place_updated = False
                record.gps_updated = False

            if record is None:
                record = build_record_from_photo(
                    photo_path,
                    params.getclearnames,
                    geocode_cache,
                    known_places,
                    params.distance,
                    params.debug,
                    params.geocode_timeout_seconds,
                    params.geocode_pacing_min_seconds,
                    params.geocode_pacing_max_seconds,
                )
            if gps_resolver is not None:
                gps_resolver.apply(record)

            if record.gps_updated and params.getclearnames and needs_place_repair(record):
                record = resolve_place_for_record(
                    record,
                    geocode_cache,
                    known_places,
                    params.distance,
                    params.debug,
                    params.geocode_timeout_seconds,
                    params.geocode_pacing_min_seconds,
                    params.geocode_pacing_max_seconds,
                )
            should_write = record.gps_updated or record.place_updated
            if not record_was_loaded:
                should_write = True
            if should_write:
                write_record_json(record, protected_json_paths)

            if (
                record.latitude is not None
                and record.longitude is not None
                and is_resolved_place_name(record.place)
            ):
                known_places.append(
                    KnownPlace(
                        latitude=record.latitude,
                        longitude=record.longitude,
                        place=str(record.place),
                        place_details=record.place_details,
                    )
                )

            screen_line = build_unsorted_output_line(record, include_update_marker=True)
            file_line = build_unsorted_output_line(record, include_update_marker=False)
            emit_output_line(screen_line, file_line, output_file)
            if params.debug:
                emit_debug_info(record)
            collected_records.append(record)
            if params.progress_callback is not None:
                params.progress_callback(photo_index, total_photos, photo_path.name)

    if gps_resolver is not None:
        gps_resolver.emit_summary()

    check_cancelled()
    write_sorted_output(
        collected_records,
        build_sorted_output_path(params.photolist),
        tracks_summary,
        params.sort_date_sections_by_tracks,
        params.media_map_options,
    )
    return None


def run_with_options(
    photodir: Path | str,
    *,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    cancel_event: Optional[threading.Event] = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Run the photo-location pipeline directly from Python."""
    global RUNTIME_CANCEL_EVENT
    params = params_from_options(photodir, **overrides)
    previous_cancel_event = RUNTIME_CANCEL_EVENT
    RUNTIME_CANCEL_EVENT = cancel_event
    result: Optional[MediaSidecarMigrationReport | SidecarPlaceUpdateReport] = None
    try:
        if stdout is None and stderr is None:
            result = collect_photo_location_and_dates(params)
        else:
            output_stream = stdout or sys.stdout
            error_stream = stderr or output_stream
            with redirect_stdout(output_stream), redirect_stderr(error_stream):
                result = collect_photo_location_and_dates(params)
    finally:
        RUNTIME_CANCEL_EVENT = previous_cancel_event
    return {
        "params": params,
        "sorted_output_path": build_sorted_output_path(params.photolist),
        "migration_report": result if isinstance(result, MediaSidecarMigrationReport) else None,
        "place_update_report": result if isinstance(result, SidecarPlaceUpdateReport) else None,
    }


def main(argv: list[str]) -> int:
    """Run the program."""
    try:
        params = collect_input_parameters(argv)
        collect_photo_location_and_dates(params)
        return 0
    except ProcessingCancelled:
        print("Aborted.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
