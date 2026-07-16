#!/usr/bin/env python3
"""Helpers for writing, reading, and using shared metadata JSON files."""

import json
from datetime import datetime
from math import atan, degrees, exp, isfinite, log, pi, radians, tan
from pathlib import Path


# AI prompt: "Write a converter from WGS84 longitude/latitude to Web Mercator
# x/y meters for reuse in coordinate-to-pixel conversion."
def lonlat_to_web_mercator(lon, lat):
    """Convert WGS84 coordinates to EPSG:3857 meters."""
    limited_lat = max(min(lat, 85.05112878), -85.05112878)
    radius = 6378137.0
    x_coord = radius * radians(lon)
    y_coord = radius * log(tan(pi / 4 + radians(limited_lat) / 2))
    return x_coord, y_coord


# AI prompt: "Write an inverse converter from Web Mercator x/y meters back to
# WGS84 longitude/latitude for metadata reporting."
def web_mercator_to_lonlat(x_coord, y_coord):
    """Convert EPSG:3857 meters to WGS84 coordinates."""
    radius = 6378137.0
    lon = degrees(x_coord / radius)
    lat = degrees(2 * atan(exp(y_coord / radius)) - pi / 2)
    return lon, lat


# AI prompt: "Write a helper that stores plot metadata as UTF-8 JSON so it can
# be read again by this or other programs."
def write_plot_metadata(metadata, output_path):
    """Write plot metadata to a JSON file."""
    write_json_data(metadata, output_path)


# AI prompt: "Write a helper that reads previously stored plot metadata from a
# UTF-8 JSON file."
def read_plot_metadata(input_path):
    """Read plot metadata from a JSON file."""
    return read_json_data(input_path)


# AI prompt: "Write helpers that store and load table summary data as UTF-8 JSON
# for reuse by other programs."
def write_table_data(table_data, output_path):
    """Write structured table summary data to JSON."""
    write_json_data(table_data, output_path)


# AI prompt: "Write a helper that reads structured table summary data from a
# UTF-8 JSON file."
def read_table_data(input_path):
    """Read structured table summary data from JSON."""
    return read_json_data(input_path)


def read_json_data(input_path):
    """Read UTF-8 JSON data from a file."""
    with open(input_path, "r", encoding="utf-8") as input_file:
        return json.load(input_file)


def write_json_data(data, output_path, ensure_ascii=False):
    """Write UTF-8 JSON data with stable formatting."""
    with open(output_path, "w", encoding="utf-8") as output_file:
        json.dump(data, output_file, indent=2, ensure_ascii=ensure_ascii, sort_keys=True)


def build_photo_metadata_payload(
    source_filename,
    photo_path,
    photo_datetime,
    latitude,
    longitude,
    place,
    place_details=None,
    *,
    source_file_signature=None,
    datetime_source=None,
    metadata_updated_at=None,
    place_coordinate=None,
):
    """Build one normalized photo sidecar JSON payload."""
    payload = {
        "source_filename": source_filename,
        "photo_path": str(photo_path),
        "datetime_iso": photo_datetime.isoformat(),
        "date_german": format_german_date(photo_datetime),
        "time": photo_datetime.strftime("%H:%M"),
        "latitude": latitude,
        "longitude": longitude,
        "place": place,
        "has_gps": latitude is not None and longitude is not None,
    }
    if isinstance(place_details, dict):
        payload["place_details"] = place_details
        for key in ("name", "locality", "subLocality", "administrativeArea", "areasOfInterest"):
            if key in place_details:
                payload[key] = place_details.get(key)
    if isinstance(source_file_signature, dict):
        payload["source_file_signature"] = dict(source_file_signature)
    if isinstance(datetime_source, str) and datetime_source.strip():
        payload["datetime_source"] = datetime_source.strip()
    if isinstance(metadata_updated_at, str) and metadata_updated_at.strip():
        payload["metadata_updated_at"] = metadata_updated_at.strip()
    if isinstance(place_coordinate, dict):
        payload["place_coordinate"] = dict(place_coordinate)
    return payload


def write_photo_metadata(payload, output_path):
    """Write one photo sidecar JSON file."""
    write_json_data(payload, output_path, ensure_ascii=True)


def read_photo_metadata(input_path):
    """Read one photo sidecar JSON file."""
    return read_json_data(input_path)


def media_sidecar_path(media_path):
    """Return the collision-safe JSON sidecar path for one media file.

    The extension remains part of the filename: ``IMG_4104.mov.json`` is
    distinct from ``IMG_4104.jpeg.json``.
    """
    path = Path(media_path)
    return path.with_name(f"{path.name}.json")


def media_file_signature(media_path):
    """Return the inexpensive signature used to detect changed media files."""
    stat_result = Path(media_path).stat()
    return {
        "size": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
    }


def media_sidecar_freshness(media_path, payload):
    """Return ``current``, ``changed``, or ``unknown`` for a valid sidecar."""
    stored = payload.get("source_file_signature") if isinstance(payload, dict) else None
    if not isinstance(stored, dict):
        return "unknown"
    try:
        expected = {
            "size": int(stored["size"]),
            "mtime_ns": int(stored["mtime_ns"]),
        }
        current = media_file_signature(media_path)
    except (KeyError, OSError, TypeError, ValueError):
        return "unknown"
    return "current" if expected == current else "changed"


def legacy_media_sidecar_path(media_path):
    """Return the pre-migration, stem-only media sidecar path."""
    return Path(media_path).with_suffix(".json")


def media_sidecar_matches_media(metadata, media_path) -> bool:
    """Return whether a sidecar explicitly identifies the supplied media file."""
    if not isinstance(metadata, dict):
        return False
    path = Path(media_path)
    expected_name = path.name.casefold()
    declared_names = []
    source_filename = metadata.get("source_filename")
    if isinstance(source_filename, str) and source_filename.strip():
        declared_names.append(Path(source_filename).name.casefold())
    photo_path = metadata.get("photo_path")
    if isinstance(photo_path, str) and photo_path.strip():
        declared_names.append(Path(photo_path).name.casefold())
    return bool(declared_names) and all(name == expected_name for name in declared_names)


def validate_media_sidecar(media_path, sidecar_path=None):
    """Return ``(status, payload, reason)`` for one extension-aware sidecar.

    Consumers use this without falling back to metadata extraction. ``status``
    is one of ``available``, ``missing``, or ``invalid``.
    """
    media = Path(media_path)
    sidecar = Path(sidecar_path) if sidecar_path is not None else media_sidecar_path(media)
    if not sidecar.is_file():
        return "missing", None, "sidecar file does not exist"
    try:
        payload = read_photo_metadata(sidecar)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return "invalid", None, f"could not read sidecar: {exc}"
    if not isinstance(payload, dict):
        return "invalid", None, "sidecar does not contain an object"
    if not media_sidecar_matches_media(payload, media):
        return "invalid", None, "sidecar belongs to another media file"
    try:
        parse_photo_datetime(payload.get("datetime_iso"))
    except (TypeError, ValueError):
        return "invalid", None, "sidecar has no valid exposure date/time"

    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if (latitude is None) != (longitude is None):
        return "invalid", None, "sidecar contains an incomplete GPS coordinate"
    if latitude is not None:
        try:
            latitude_value = float(latitude)
            longitude_value = float(longitude)
        except (TypeError, ValueError):
            return "invalid", None, "sidecar contains an invalid GPS coordinate"
        if (
            not isfinite(latitude_value)
            or not isfinite(longitude_value)
            or not -90.0 <= latitude_value <= 90.0
            or not -180.0 <= longitude_value <= 180.0
        ):
            return "invalid", None, "sidecar GPS coordinate is outside the valid range"
    return "available", payload, None


def format_german_date(value):
    """Return a German weekday/date string for a datetime."""
    weekdays = [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ]
    return f"{weekdays[value.weekday()]}, {value.strftime('%d.%m.%Y')}"


def parse_photo_datetime(value):
    """Parse an ISO timestamp from sidecar JSON."""
    if not isinstance(value, str):
        raise TypeError("datetime_iso must be a string")
    return datetime.fromisoformat(value)


def build_coordinate_point(lat, lon):
    """Return one normalized coordinate point payload for JSON metadata."""
    if lat is None or lon is None:
        return None
    return {"lat": float(lat), "lon": float(lon)}


def extract_coordinate_point(point):
    """Extract latitude/longitude from supported metadata point shapes."""
    if isinstance(point, dict):
        try:
            lat = float(point.get("lat")) if point.get("lat") is not None else None
            lon = float(point.get("lon")) if point.get("lon") is not None else None
        except (TypeError, ValueError):
            return None, None
        return lat, lon
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        try:
            return float(point[0]), float(point[1])
        except (TypeError, ValueError):
            return None, None
    return None, None


# AI prompt: "Write a helper that maps a geographic coordinate to pixel
# coordinates using the stored plot extent, image size, and axes box."
def coordinate_to_pixel(lat, lon, metadata):
    """Return the pixel position for a geographic coordinate.

    The result uses image pixel coordinates with origin in the top-left corner.
    """
    x_coord, y_coord = lonlat_to_web_mercator(lon, lat)
    extent = metadata["extent_mercator"]
    axes_box = metadata["axes_box_fraction"]
    image_width = metadata["image_size_px"]["width"]
    image_height = metadata["image_size_px"]["height"]

    normalized_x = (x_coord - extent["min_x"]) / (extent["max_x"] - extent["min_x"])
    normalized_y = (y_coord - extent["min_y"]) / (extent["max_y"] - extent["min_y"])

    axes_left_px = axes_box["left"] * image_width
    axes_width_px = axes_box["width"] * image_width
    axes_bottom_px = axes_box["bottom"] * image_height
    axes_height_px = axes_box["height"] * image_height

    pixel_x = axes_left_px + normalized_x * axes_width_px
    pixel_y_from_bottom = axes_bottom_px + normalized_y * axes_height_px
    pixel_y = image_height - pixel_y_from_bottom
    return pixel_x, pixel_y


# AI prompt: "Write a helper that maps an image pixel position back to the
# corresponding geographic coordinate using stored plot metadata."
def pixel_to_coordinate(pixel_x, pixel_y, metadata):
    """Return the geographic coordinate for an image pixel."""
    extent = metadata["extent_mercator"]
    axes_box = metadata["axes_box_fraction"]
    image_width = metadata["image_size_px"]["width"]
    image_height = metadata["image_size_px"]["height"]

    axes_left_px = axes_box["left"] * image_width
    axes_width_px = axes_box["width"] * image_width
    axes_bottom_px = axes_box["bottom"] * image_height
    axes_height_px = axes_box["height"] * image_height

    normalized_x = (pixel_x - axes_left_px) / axes_width_px
    pixel_y_from_bottom = image_height - pixel_y
    normalized_y = (pixel_y_from_bottom - axes_bottom_px) / axes_height_px

    x_coord = extent["min_x"] + normalized_x * (extent["max_x"] - extent["min_x"])
    y_coord = extent["min_y"] + normalized_y * (extent["max_y"] - extent["min_y"])
    lon, lat = web_mercator_to_lonlat(x_coord, y_coord)
    return {"lat": lat, "lon": lon, "x_mercator": x_coord, "y_mercator": y_coord}


# AI prompt: "Write a helper that returns metadata describing the meaning of
# pixel origin (0,0) in the image coordinate system."
def image_origin_metadata(metadata):
    """Return metadata for pixel origin (0,0)."""
    origin_coordinate = pixel_to_coordinate(0, 0, metadata)
    return {
        "pixel_origin": [0, 0],
        "pixel_origin_corner": "top-left",
        "pixel_origin_coordinate_wgs84": {
            "lat": origin_coordinate["lat"],
            "lon": origin_coordinate["lon"],
        },
        "pixel_origin_coordinate_mercator": {
            "x": origin_coordinate["x_mercator"],
            "y": origin_coordinate["y_mercator"],
        },
    }
