#!/usr/bin/env python3
# Install note:
# python3 -m pip install pyobjc-core pyobjc-framework-CoreLocation pyobjc-framework-Cocoa
"""Extract photo dates and geolocations from a directory on macOS."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plot_metadata_utils import (
    build_photo_metadata_payload,
    legacy_media_sidecar_path,
    media_sidecar_matches_media,
    media_sidecar_path,
    parse_photo_datetime,
    read_json_data,
    read_photo_metadata,
    write_photo_metadata,
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
    migrate_media_sidecars: bool = False
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


@dataclass
class MediaSidecarMigrationReport:
    """Result of a media-sidecar migration for one project directory."""

    project_dir: Path
    migrated: list[tuple[Path, Path]]
    regenerated: list[Path]
    preserved: list[tuple[Path, Path]]
    conflicts: list[tuple[Path, Path]]


@dataclass(frozen=True)
class TrackInfo:
    """Track summary metadata used in the sorted list output."""

    start_time: datetime
    track_plot_image_filename: str
    original_sequence_number: int


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
    migrate_media_sidecars: bool = False,
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
        migrate_media_sidecars=bool(migrate_media_sidecars),
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
        "-CreateDate",
        "-MediaCreateDate",
        "-CreationDate",
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
    for metadata_key in ("kMDItemContentCreationDate", "kMDItemFSCreationDate"):
        raw_value = read_mdls_raw(file_path, metadata_key)
        if raw_value:
            parsed = parse_mdls_datetime(raw_value)
            if parsed is not None:
                return parsed

    exif_datetime = read_exiftool_datetime(file_path)
    if exif_datetime is not None:
        return exif_datetime

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

    exif_datetime, exif_debug = read_exiftool_datetime_with_debug(file_path)
    debug_info["exiftool"] = exif_debug
    if exif_datetime is not None:
        debug_info["selected_source"] = f"exiftool:{exif_debug.get('selected_source')}"
        return exif_datetime, debug_info

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


def read_exiftool_datetime(file_path: Path) -> Optional[datetime]:
    """Read the preferred datetime from exiftool metadata."""
    metadata = read_exiftool_json(file_path)
    if not metadata:
        return None

    for key in ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "CreationDate"):
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

    for key in ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "CreationDate"):
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


def get_photo_datetime_prefer_exif(file_path: Path) -> datetime:
    """Return the best timestamp, preferring exiftool metadata."""
    exif_datetime = read_exiftool_datetime(file_path)
    if exif_datetime is not None:
        return exif_datetime
    return get_photo_datetime(file_path)


def get_photo_datetime_prefer_exif_with_debug(file_path: Path) -> tuple[datetime, dict[str, Any]]:
    """Return the best timestamp, preferring exiftool metadata, with debug info."""
    exif_datetime, exif_debug = read_exiftool_datetime_with_debug(file_path)
    if exif_datetime is not None:
        return exif_datetime, {"selected_source": f"exiftool:{exif_debug.get('selected_source')}", "exiftool": exif_debug}
    return get_photo_datetime_with_debug(file_path)


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
        image_name = maybe_shorten_output_path(item.get("track_plot_image_filename"), photolist)
        try:
            original_sequence_number = int(item.get("original_sequence_number", item.get("nr", len(tracks) + 1)))
        except (TypeError, ValueError):
            original_sequence_number = len(tracks) + 1
        if isinstance(item.get("track_plot_image_filename"), str):
            ignored_photo_names.add(normalize_filename_for_match(str(item["track_plot_image_filename"])))
        if start_time is None or not image_name:
            continue
        tracks.append(
            TrackInfo(
                start_time=start_time,
                track_plot_image_filename=image_name,
                original_sequence_number=original_sequence_number,
            )
        )

    return TracksSummary(overview_image=overview_image, tracks=tracks, ignored_photo_names=ignored_photo_names)


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
    try:
        data = read_photo_metadata(json_path)
    except (OSError, ValueError, TypeError):
        return None
    if not media_sidecar_matches_media(data, photo_path):
        return None

    try:
        photo_datetime = normalize_datetime_timezone(parse_photo_datetime(data.get("datetime_iso")))
    except (TypeError, ValueError):
        return None

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
        used_exif_gps = False
        if latitude is None or longitude is None:
            exif_latitude, exif_longitude, exif_gps_debug = read_exiftool_gps_pair_with_debug(photo_path)
            gps_debug["exiftool"] = exif_gps_debug
            if exif_latitude is not None and exif_longitude is not None:
                latitude, longitude = exif_latitude, exif_longitude
                gps_debug["source"] = f"fallback:{exif_gps_debug.get('source')}"
                used_exif_gps = True
            elif not is_exiftool_available():
                warn_exiftool_missing_once()

        if used_exif_gps:
            photo_datetime, datetime_debug = get_photo_datetime_prefer_exif_with_debug(photo_path)
        else:
            photo_datetime, datetime_debug = get_photo_datetime_with_debug(photo_path)
        debug_info["datetime"] = datetime_debug
        debug_info["gps"] = gps_debug
    else:
        latitude, longitude = read_mdls_gps_pair(photo_path)
        used_exif_gps = False
        if latitude is None or longitude is None:
            exif_latitude, exif_longitude = read_exiftool_gps_pair(photo_path)
            if exif_latitude is not None and exif_longitude is not None:
                latitude, longitude = exif_latitude, exif_longitude
                used_exif_gps = True
            elif not is_exiftool_available():
                warn_exiftool_missing_once()

        if used_exif_gps:
            photo_datetime = get_photo_datetime_prefer_exif(photo_path)
        else:
            photo_datetime = get_photo_datetime(photo_path)

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

    cache_key = (round(record.latitude, GEOCODE_ROUND_DIGITS), round(record.longitude, GEOCODE_ROUND_DIGITS))
    debug_info = dict(record.debug_info or {})

    if cache_key not in geocode_cache:
        nearby_place, nearby_distance = find_nearby_known_place(
            record.latitude,
            record.longitude,
            known_places,
            place_distance_m,
        )
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
                    record.latitude, record.longitude, timeout_seconds=geocode_timeout_seconds
                )
                geocode_cache[cache_key] = (place, place_details)
                debug_info["geocode"] = {"cache_key": cache_key, "requested": True, **geocode_debug}
            else:
                geocode_cache[cache_key] = reverse_geocode_location_details(
                    record.latitude, record.longitude, timeout_seconds=geocode_timeout_seconds
                )
            sleep_between_geocode_requests(geocode_pacing_min_seconds, geocode_pacing_max_seconds)
    elif debug:
        debug_info["geocode"] = {"cache_key": cache_key, "cached": True, "place": geocode_cache[cache_key][0]}

    new_place, new_place_details = geocode_cache[cache_key]
    if is_resolved_place_name(new_place):
        record.place = str(new_place)
        record.place_details = new_place_details
        record.place_updated = True
    record.geocode_requested = True
    record.debug_info = debug_info or None
    return record


def write_record_json(record: PhotoRecord, protected_json_paths: set[Path]) -> None:
    """Write one sidecar JSON file."""
    try:
        resolved_json_path = record.json_path.resolve()
    except OSError:
        resolved_json_path = record.json_path

    if resolved_json_path in protected_json_paths:
        raise ValueError(f"refusing to overwrite protected JSON file: {record.json_path}")

    payload = build_photo_metadata_payload(
        source_filename=record.source_filename,
        photo_path=record.photo_path,
        photo_datetime=record.photo_datetime,
        latitude=record.latitude,
        longitude=record.longitude,
        place=record.place,
        place_details=record.place_details,
    )
    write_photo_metadata(payload, record.json_path)


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


def write_sorted_output(
    records: list[PhotoRecord],
    sorted_output_path: Path,
    tracks_summary: Optional[TracksSummary],
    sort_date_sections_by_tracks: bool = False,
) -> None:
    """Write the grouped sorted list after collecting all records."""
    sorted_records = sort_records_for_output(records, tracks_summary, sort_date_sections_by_tracks)
    sorted_output_path.parent.mkdir(parents=True, exist_ok=True)

    with sorted_output_path.open("w", encoding="utf-8") as output_file:
        if tracks_summary and tracks_summary.overview_image:
            output_file.write(f"#Overviewmap: {tracks_summary.overview_image}\n")

        current_date_label = None
        for record in sorted_records:
            date_label = format_german_date(record.photo_datetime)
            if date_label != current_date_label:
                output_file.write(f"#Datum: {date_label}\n")
                if tracks_summary:
                    for track in tracks_summary.tracks:
                        if track.start_time.date() == record.photo_datetime.date():
                            output_file.write(f"#Map: {track.track_plot_image_filename}\n")
                current_date_label = date_label

            time_text = record.photo_datetime.strftime("%H:%M")
            gps_text = format_gps_text(record.latitude, record.longitude)
            place_text = format_place_text(record.latitude, record.longitude, record.place, record.geocode_requested)
            output_file.write(f"{record.source_filename} | {time_text} | {gps_text} | {place_text}\n")


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
        entry: dict[str, Any] = {"line": stripped, "type": "other", "date": current_date, "name": control_line_name(stripped)}
        if stripped.startswith("#"):
            keyword, _separator, value = stripped[1:].partition(":")
            normalized = keyword.strip().lower()
            if normalized in {"datum", "date"}:
                current_date_label = value.strip()
                current_date = parse_german_date_label(current_date_label)
                entry.update({"type": "date", "date": current_date, "date_label": current_date_label})
            elif normalized == "overviewmap":
                entry.update({"type": "overview"})
            elif normalized == "map":
                entry.update({"type": "map"})
        else:
            parts = [part.strip() for part in stripped.split("|")]
            if parts:
                entry.update({"type": "media", "name": parts[0], "date": current_date})
                if len(parts) > 1 and current_date is not None:
                    try:
                        parsed_time = datetime.strptime(parts[1], "%H:%M").time()
                        entry["datetime"] = datetime.combine(current_date, parsed_time).astimezone()
                    except ValueError:
                        pass
        entries.append(entry)
    return entries


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


def insert_map_entry(
    entries: list[dict[str, Any]],
    track: TrackInfo,
    tracks_summary: TracksSummary,
    sort_date_sections_by_tracks: bool,
) -> None:
    """Insert one missing #Map line into its date section."""
    day = track.start_time.date()
    header_index, end_index = ensure_date_section(entries, day, tracks_summary, sort_date_sections_by_tracks)
    insert_at = header_index + 1
    for index in range(header_index + 1, end_index):
        if entries[index].get("type") == "map":
            insert_at = index + 1
            continue
        if entries[index].get("type") == "media":
            break
    entries.insert(
        insert_at,
        {
            "line": f"#Map: {track.track_plot_image_filename}",
            "type": "map",
            "date": day,
            "name": track.track_plot_image_filename,
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


def record_for_merge_media(params: Params, media_path: Path) -> PhotoRecord:
    """Build or load metadata for one media file being merged."""
    json_path = get_json_path_for_photo(media_path)
    record = None if params.ignorejson else load_record_from_json(json_path, media_path)
    if record is not None:
        return record
    record = build_record_from_photo(
        media_path,
        params.getclearnames,
        {},
        [],
        params.distance,
        params.debug,
    )
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
                or normalized_name in existing_names
                or normalized_map_name in existing_map_names
            ):
                continue
            insert_map_entry(entries, track, tracks_summary, params.sort_date_sections_by_tracks)
            existing_names.add(normalized_name)
            existing_map_names.add(normalized_map_name)
            inserted_maps += 1

    media_records = [record_for_merge_media(params, media_path) for media_path in params.merge_media]
    for record in sort_records_for_output(media_records, tracks_summary, params.sort_date_sections_by_tracks):
        normalized_name = normalize_filename_for_match(record.source_filename)
        if normalized_name in existing_names or normalized_name in existing_media_names:
            continue
        insert_media_entry(entries, record, tracks_summary, params.sort_date_sections_by_tracks)
        existing_names.add(normalized_name)
        existing_media_names.add(normalized_name)
        inserted_media += 1

    sorted_output_path.write_text("\n".join(entry["line"] for entry in entries) + "\n", encoding="utf-8")
    print(
        f"Merged {inserted_maps} track map/overview line(s) and {inserted_media} media line(s) into {sorted_output_path}",
        flush=True,
    )
    return sorted_output_path


def collect_photo_location_and_dates(params: Params) -> Optional[MediaSidecarMigrationReport]:
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
    tracks_summary = load_tracks_summary(params.tracks, params.photolist)
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
            if record is not None:
                record.geocode_requested = params.getclearnames
                record.place_updated = False

            if record is None:
                record = build_record_from_photo(
                    photo_path,
                    params.getclearnames or params.redo_reverse_geolocation or params.overwrite_reverse_geolocation,
                    geocode_cache,
                    known_places,
                    params.distance,
                    params.debug,
                    params.geocode_timeout_seconds,
                    params.geocode_pacing_min_seconds,
                    params.geocode_pacing_max_seconds,
                )
                if (params.redo_reverse_geolocation or params.overwrite_reverse_geolocation) and is_resolved_place_name(record.place):
                    record.place_updated = True
                if params.redo_reverse_geolocation or params.overwrite_reverse_geolocation:
                    if record.place_updated:
                        write_record_json(record, protected_json_paths)
                else:
                    write_record_json(record, protected_json_paths)
            elif params.overwrite_reverse_geolocation and record.latitude is not None and record.longitude is not None:
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
                if record.place_updated:
                    write_record_json(record, protected_json_paths)
            elif params.redo_reverse_geolocation and needs_place_repair(record):
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
                if record.place_updated:
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

    check_cancelled()
    write_sorted_output(
        collected_records,
        build_sorted_output_path(params.photolist),
        tracks_summary,
        params.sort_date_sections_by_tracks,
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
    result: Optional[MediaSidecarMigrationReport] = None
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
        "migration_report": result,
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
