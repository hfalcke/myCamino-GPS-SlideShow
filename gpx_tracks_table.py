#!/usr/bin/env python3
"""List tracks from a GPX 1.1 file and optionally plot them on an OSM basemap."""

import argparse
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from math import asin, atan2, cos, degrees, floor, log, pi, radians, sin, sqrt, tan
from pathlib import Path

from gpx_processing import (
    DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
    DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
    ProcessingOptions,
    RawTrackPoint,
    extract_raw_track_points,
    haversine_km as shared_haversine_km,
    parse_time,
    process_raw_points,
    process_track_element,
    semantic_track_fingerprint as shared_semantic_track_fingerprint,
)
from gpx_import import load_gpx_document, timing_status_for_track

from plot_metadata_utils import (
    build_coordinate_point,
    image_origin_metadata,
    write_plot_metadata,
    write_table_data,
    read_plot_metadata,
)
from basemap_tile_utils import tolerate_missing_tiles
from map_provider_utils import (
    DEFAULT_TILE_CACHE_DIR,
    TileProviderAccessError,
    configure_contextily_cache,
    contextily_provider,
    contextily_request_timeout,
    provider_attribution,
    provider_display_name,
)
from map_overlay import MAP_CONTENT_VERSION, json_overlay_geometry
from track_timing_utils import timed_points_payload
from track_map_layout_utils import (
    DEFAULT_GRID_LONG_AXIS,
    DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
    MEDIA_CLEAR_BOX_COMPATIBLE_VERSIONS,
    MEDIA_CLEAR_BOX_VERSION,
    build_media_clear_boxes_metadata,
    clear_box_options_for_extent,
    optimized_track_extent,
    time_lapse_track_map_name,
    track_map_variant_names,
)


GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
EARLIEST_UNKNOWN = datetime.max.replace(tzinfo=timezone.utc)
DEFAULT_IMAGE_SIZE = (1600, 1200)
STAGE_HEADER_HEIGHT_SCALE = 1.25
RUNNING_SPEED_METADATA_VERSION = 1


def running_speed_metadata(track):
    """Return sidecar-only speed metadata, independent of rendered map pixels."""
    options = track.get("processing_options", {})
    return {
        "version": RUNNING_SPEED_METADATA_VERSION,
        "window_distance_m": options.get(
            "running_speed_window_distance_m",
            DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
        ),
        "stationary_threshold_kmh": options.get(
            "stationary_speed_threshold_kmh",
            DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
        ),
        "moving_average_speed_kmh": track.get("moving_average_speed_kmh"),
        "maximum_running_speed_kmh": track.get("maximum_running_speed_kmh"),
    }


# AI prompt: "Write a function that parses GPX/ISO timestamps, handles trailing Z,
# treats naive times as UTC, and returns a timezone-aware datetime or None."
# AI prompt: "Write a haversine helper that returns distance in kilometers
# between two latitude/longitude pairs."
haversine_km = shared_haversine_km


# AI prompt: "Write a converter from WGS84 longitude/latitude to Web Mercator
# x/y meters for use with slippy-map basemaps."
def lonlat_to_web_mercator(lon, lat):
    """Convert WGS84 coordinates to EPSG:3857 meters."""
    limited_lat = max(min(lat, 85.05112878), -85.05112878)
    radius = 6378137.0
    x_coord = radius * radians(lon)
    y_coord = radius * log(tan(pi / 4 + radians(limited_lat) / 2))
    return x_coord, y_coord


# AI prompt: "Write a formatter that converts a datetime to local time and prints
# it as DD.MM.YYYY HH:MM, returning N/A when the value is missing."
def format_datetime_local(dt_value):
    """Format a datetime in local time for table output."""
    if dt_value is None:
        return "N/A"
    local_dt = dt_value.astimezone()
    return local_dt.strftime("%d.%m.%Y %H:%M")


# AI prompt: "Write a formatter that converts a datetime to local time and prints
# it as DD.MM.YYYY HH:MM:SS, returning N/A when the value is missing."
def format_datetime_local_seconds(dt_value):
    """Format a datetime in local time with seconds."""
    if dt_value is None:
        return "N/A"
    local_dt = dt_value.astimezone()
    return local_dt.strftime("%d.%m.%Y %H:%M:%S")


# AI prompt: "Write a formatter that converts a datetime to local date-only form
# DD.MM.YYYY, returning N/A when the value is missing."
def format_date_local(dt_value):
    """Format a datetime as a local date string."""
    if dt_value is None:
        return "N/A"
    local_dt = dt_value.astimezone()
    return local_dt.strftime("%d.%m.%Y")


# AI prompt: "Write a formatter that converts a datetime to local date-only form
# DD.MM.YY, returning N/A when the value is missing."
def format_date_local_short(dt_value):
    """Format a datetime as a short local date string."""
    if dt_value is None:
        return "N/A"
    local_dt = dt_value.astimezone()
    return local_dt.strftime("%d.%m.%y")


# AI prompt: "Write a duration formatter that returns h:mm for timedeltas whose
# hours may exceed 24, and N/A for missing values."
def format_duration(duration):
    """Format a timedelta as h:mm."""
    if duration is None:
        return "N/A"
    total_minutes = int(duration.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}:{minutes:02d}"


# AI prompt: "Write a duration formatter that returns HH:MM with zero-padded
# hours for use in fixed-width labels."
def format_duration_hhmm(duration):
    """Format a timedelta as HH:MM."""
    if duration is None:
        return "N/A"
    total_minutes = int(duration.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


# AI prompt: "Write a helper that removes a configured leading prefix from a
# track name when present and falls back to Unnamed for empty results."
def normalize_track_name(track_name, remove_prefix):
    """Return the display name after removing an optional leading prefix."""
    normalized = track_name or ""
    if remove_prefix and normalized.startswith(remove_prefix):
        normalized = normalized[len(remove_prefix):]
    normalized = normalized.lstrip()
    return normalized.strip() or "Unnamed"


# AI prompt: "Write a helper that extracts the local tag name without any XML
# namespace prefix so mixed GPX extension tags can be matched reliably."
def local_tag_name(tag):
    """Return the local XML tag name without namespace."""
    return tag.rsplit("}", 1)[-1].casefold()


# AI prompt: "Write a helper that tries to extract a point accuracy in meters
# from common GPX extension field names and also reports which field was used."
def extract_point_accuracy(point_element):
    """Return point accuracy in meters and the field name when available."""
    candidate_names = {
        "accuracy",
        "horizontalaccuracy",
        "horizontal_accuracy",
        "horizontal-accuracy",
        "hacc",
        "eph",
    }
    for candidate in point_element.iter():
        if local_tag_name(candidate.tag) not in candidate_names:
            continue
        if candidate.text is None:
            continue
        try:
            value = float(candidate.text.strip())
        except ValueError:
            continue
        if value >= 0:
            return value, local_tag_name(candidate.tag)
    return None, None


# AI prompt: "Write a parser that extracts valid trackpoint coordinates and
# timestamps from a GPX <trk> element while preserving point order."
def extract_track_points(track_element):
    """Return ordered trackpoints with coordinates, time, elevation, and accuracy."""
    points = []
    for point in extract_raw_track_points(track_element):
        accuracy_field = "accuracy" if point.horizontal_accuracy_m is not None else None
        points.append(
            {
                "source_index": point.source_index,
                "segment_index": point.segment_index,
                "segment_point_index": point.segment_point_index,
                "lat": point.lat,
                "lon": point.lon,
                "time": point.time,
                "elevation_m": point.elevation_m,
                "accuracy": point.horizontal_accuracy_m,
                "accuracy_field": accuracy_field,
                "vertical_accuracy_m": point.vertical_accuracy_m,
                "hdop": point.hdop,
                "vdop": point.vdop,
                "pdop": point.pdop,
                "satellites": point.satellites,
                "fix": point.fix,
            }
        )
    return points


# AI prompt: "Write a helper that filters trackpoints by optional accuracy and
# minimum spacing in meters, preserving order and ignoring missing accuracy."
def filter_track_points(points, threshold_distance_m, threshold_accuracy_m):
    """Return filtered points using accuracy and spacing thresholds."""
    raw = [
        RawTrackPoint(
            source_index=int(point.get("source_index", index)),
            segment_index=int(point.get("segment_index", 0)),
            segment_point_index=int(point.get("segment_point_index", index)),
            lat=float(point["lat"]),
            lon=float(point["lon"]),
            elevation_m=point.get("elevation_m"),
            time=point.get("time"),
            horizontal_accuracy_m=point.get("accuracy"),
            vertical_accuracy_m=point.get("vertical_accuracy_m"),
            hdop=point.get("hdop"),
            vdop=point.get("vdop"),
            pdop=point.get("pdop"),
            satellites=point.get("satellites"),
            fix=point.get("fix"),
        )
        for index, point in enumerate(points)
    ]
    processed = process_raw_points(
        raw,
        ProcessingOptions(
            horizontal_smoothing_distance_m=0.0,
            minimum_point_spacing_m=threshold_distance_m,
            elevation_smoothing_distance_m=0.0,
            maximum_horizontal_accuracy_m=threshold_accuracy_m,
            maximum_vertical_accuracy_m=0.0,
            maximum_hdop=0.0,
            maximum_vdop=0.0,
        ),
    )
    original = {int(point.get("source_index", index)): point for index, point in enumerate(points)}
    result = []
    for point in processed.points:
        item = dict(original.get(point.source_index, {}))
        item.update(point.as_record())
        result.append(item)
    return result


# AI prompt: "Write a helper that returns the first non-missing timestamp from
# a point sequence, or None when no timestamps exist."
def first_point_time(points):
    """Return the first available point timestamp in original order."""
    for point in points:
        if point["time"] is not None:
            return point["time"]
    return None


def semantic_track_fingerprint(track_element):
    """Return a stable hash for one track, ignoring XML formatting whitespace."""
    return shared_semantic_track_fingerprint(track_element)


# AI prompt: "Write a summarizer for one GPX <trk> element that extracts display
# fields, timing, geometry, length, endpoints, and the point list for plotting."
def summarize_track(
    track_element,
    remove_prefix,
    threshold_distance_m,
    threshold_accuracy_m,
    verbose,
    horizontal_smoothing_distance_m=10.0,
    elevation_smoothing_distance_m=50.0,
    maximum_vertical_accuracy_m=20.0,
    maximum_hdop=20.0,
    maximum_vdop=20.0,
    running_speed_window_distance_m=DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
    stationary_speed_threshold_kmh=DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
):
    """Build a normalized track dictionary from a GPX <trk> element."""
    track_name = normalize_track_name(
        track_element.findtext("gpx:name", default="", namespaces=GPX_NS),
        remove_prefix,
    )
    options = ProcessingOptions(
        horizontal_smoothing_distance_m=horizontal_smoothing_distance_m,
        minimum_point_spacing_m=threshold_distance_m,
        elevation_smoothing_distance_m=elevation_smoothing_distance_m,
        maximum_horizontal_accuracy_m=threshold_accuracy_m,
        maximum_vertical_accuracy_m=maximum_vertical_accuracy_m,
        maximum_hdop=maximum_hdop,
        maximum_vdop=maximum_vdop,
        running_speed_window_distance_m=running_speed_window_distance_m,
        stationary_speed_threshold_kmh=stationary_speed_threshold_kmh,
    )
    processed = process_track_element(track_element, options)
    raw_points = processed.raw_points
    points = [point.as_record() for point in processed.points]
    detected_accuracy_fields = sorted(
        name
        for name, present in {
            "horizontal accuracy": any(point.horizontal_accuracy_m is not None for point in raw_points),
            "vertical accuracy": any(point.vertical_accuracy_m is not None for point in raw_points),
            "hdop": any(point.hdop is not None for point in raw_points),
            "vdop": any(point.vdop is not None for point in raw_points),
        }.items()
        if present
    )
    accuracy_field_text = ", ".join(detected_accuracy_fields) if detected_accuracy_fields else "keine"
    if verbose:
        print(
            f"Track eingelesen: {track_name} | Punkte vor Filter: {len(raw_points)} | "
            f"Punkte nach Filter: {len(points)} | Genauigkeitsfelder vorhanden: "
            f"{'ja' if detected_accuracy_fields else 'nein'} | Felder: {accuracy_field_text}"
        )

    start_time = processed.start_time
    end_time = processed.end_time

    track_time = parse_time(track_element.findtext("gpx:time", default="", namespaces=GPX_NS))
    if track_time is None:
        track_time = first_point_time(points)

    first_point = None if processed.first_point is None else (processed.first_point.lat, processed.first_point.lon)
    last_point = None if processed.last_point is None else (processed.last_point.lat, processed.last_point.lon)

    return {
        "name": track_name,
        "time": track_time,
        "start_time": start_time,
        "end_time": end_time,
        "duration": processed.duration,
        "length_km": processed.length_km,
        "ascent_m": processed.ascent_m,
        "descent_m": processed.descent_m,
        "moving_average_speed_kmh": processed.moving_average_speed_kmh,
        "maximum_running_speed_kmh": processed.maximum_running_speed_kmh,
        "first_point": first_point,
        "last_point": last_point,
        "points": [(point["lat"], point["lon"]) for point in points],
        "point_records": points,
        "distance_km": None,
        "segments": [
            [(point.lat, point.lon) for point in segment.points]
            for segment in processed.segments
        ],
        "segment_records": [
            [point.as_record() for point in segment.points]
            for segment in processed.segments
        ],
        "raw_points": [(point.lat, point.lon) for point in raw_points],
        "filtered_point_count": len(points),
        "raw_point_count": len(raw_points),
        "rejection_counts": processed.rejection_counts,
        "processing_options": processed.options.as_dict(),
        "track_fingerprint": semantic_track_fingerprint(track_element),
    }


# AI prompt: "Write a GPX 1.1 parser using xml.etree.ElementTree that loads all
# <trk> elements from a file and returns summarized track dictionaries."
def parse_gpx_file(
    file_path,
    remove_prefix,
    threshold_distance_m,
    threshold_accuracy_m,
    verbose,
    horizontal_smoothing_distance_m=10.0,
    elevation_smoothing_distance_m=50.0,
    maximum_vertical_accuracy_m=20.0,
    maximum_hdop=20.0,
    maximum_vdop=20.0,
    track_processing_callback=None,
    running_speed_window_distance_m=DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
    stationary_speed_threshold_kmh=DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
):
    """Parse the GPX file and return summarized tracks."""
    document = load_gpx_document(file_path)

    tracks = []
    for original_sequence_number, track_element in enumerate(document.tracks, start=1):
        if track_processing_callback is not None:
            track_processing_callback()
        track = summarize_track(
            track_element,
            remove_prefix,
            threshold_distance_m,
            threshold_accuracy_m,
            verbose,
            horizontal_smoothing_distance_m,
            elevation_smoothing_distance_m,
            maximum_vertical_accuracy_m,
            maximum_hdop,
            maximum_vdop,
            running_speed_window_distance_m,
            stationary_speed_threshold_kmh,
        )
        track["original_sequence_number"] = original_sequence_number
        track["timing_status"] = timing_status_for_track(track_element)
        track["has_absolute_time"] = track["start_time"] is not None
        media_derived = any(
            element.tag.rsplit("}", 1)[-1] == "trackOrigin"
            and str(element.attrib.get("kind", "")).casefold() == "media-derived"
            for element in track_element.iter()
        )
        track["source_structure"] = (
            "media"
            if media_derived
            else "route"
            if original_sequence_number > document.report.track_count
            and original_sequence_number
            <= document.report.track_count + document.report.converted_routes
            else "waypoints"
            if document.report.converted_waypoint_tracks
            else "track"
        )
        tracks.append(track)
    return tracks


# AI prompt: "Write a helper that returns the sort key datetime for a track,
# putting missing timestamps last."
def track_time_key(track):
    """Return the timestamp used for chronological ordering."""
    return track["time"] if track["time"] is not None else EARLIEST_UNKNOWN


# AI prompt: "Write logic that finds the anchor point from the earliest track by
# time and stores the last-point distance to that anchor in each track dict."
def assign_anchor_distances(tracks):
    """Populate distance_km for each track and return anchor metadata."""
    anchor_track = None
    for candidate in sorted(tracks, key=track_time_key):
        if candidate["first_point"] is not None and candidate["time"] is not None:
            anchor_track = candidate
            break
    if anchor_track is None:
        for candidate in sorted(tracks, key=track_time_key):
            if candidate["first_point"] is not None:
                anchor_track = candidate
                break
    if anchor_track is None:
        for track in tracks:
            track["distance_km"] = None
        return None, None

    anchor_point = anchor_track["first_point"]
    for track in tracks:
        if track["last_point"] is None:
            track["distance_km"] = None
            continue
        track["distance_km"] = haversine_km(
            track["last_point"][0],
            track["last_point"][1],
            anchor_point[0],
            anchor_point[1],
        )
    return anchor_point, anchor_track["name"]


# AI prompt: "Write a helper that reports whether a track duration is missing or
# effectively zero seconds so default sorting can treat it as distance-based."
def has_missing_or_zero_duration(track):
    """Return True when duration is missing or zero seconds."""
    duration = track["duration"]
    return duration is None or duration.total_seconds() <= 0


# AI prompt: "Write a helper that returns a track's distance sort key, putting
# missing distances last."
def distance_key(track):
    """Return the distance key used for anchor-distance ordering."""
    return float("inf") if track["distance_km"] is None else track["distance_km"]


# AI prompt: "Write sorting logic for the three CLI modes: strict date sort,
# distance sort, and the mixed default mode described in the specification."
def sort_tracks(tracks, sort_date, sort_distance, sort_original=False):
    """Return sorted tracks plus anchor metadata."""
    for track in tracks:
        track["is_sort_exception"] = has_missing_or_zero_duration(track)
    anchor_point, anchor_name = assign_anchor_distances(tracks)

    if sort_original:
        pass
    elif sort_distance:
        tracks.sort(
            key=lambda track: (
                distance_key(track),
                track_time_key(track),
                track["name"].casefold(),
            )
        )
    elif sort_date:
        tracks[:] = date_order_with_untimed_tracks(tracks)
    else:
        regular_tracks = sorted(
            (track for track in tracks if not has_missing_or_zero_duration(track)),
            key=lambda track: (track_time_key(track), track["name"].casefold()),
        )
        exceptional_tracks = sorted(
            (track for track in tracks if has_missing_or_zero_duration(track)),
            key=lambda track: (distance_key(track), track_time_key(track), track["name"].casefold()),
        )
        merged_tracks = list(regular_tracks)
        for exceptional_track in exceptional_tracks:
            exceptional_distance = distance_key(exceptional_track)
            insert_index = len(merged_tracks)
            for index, current_track in enumerate(merged_tracks):
                if exceptional_distance <= distance_key(current_track):
                    insert_index = index
                    break
            merged_tracks.insert(insert_index, exceptional_track)
        tracks[:] = merged_tracks
    return tracks, anchor_point, anchor_name


def date_order_with_untimed_tracks(tracks):
    """Sort dated tracks while retaining each untimed track's original context."""
    original = sorted(
        tracks,
        key=lambda track: int(track.get("original_sequence_number", 0)),
    )
    dated = [track for track in original if track.get("time") is not None]
    if not dated:
        return original

    leading = []
    attached: dict[int, list[dict]] = {}
    preceding_dated = None
    for track in original:
        if track.get("time") is not None:
            preceding_dated = track
        elif preceding_dated is None:
            leading.append(track)
        else:
            attached.setdefault(id(preceding_dated), []).append(track)

    ordered = list(leading)
    for track in sorted(
        dated,
        key=lambda item: (
            track_time_key(item),
            int(item.get("original_sequence_number", 0)),
            item["name"].casefold(),
        ),
    ):
        ordered.append(track)
        ordered.extend(attached.get(id(track), ()))
    return ordered


# AI prompt: "Write a parser for a WIDTHxHEIGHT image size option, validating
# that both dimensions are positive integers."
def parse_image_size(value):
    """Parse an image size string formatted as WIDTHxHEIGHT."""
    match = re.fullmatch(r"(\d+)x(\d+)", value.strip(), re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError(
            "Size must be in the form WIDTHxHEIGHT, for example 1600x1200."
        )
    width_px = int(match.group(1))
    height_px = int(match.group(2))
    if width_px <= 0 or height_px <= 0:
        raise argparse.ArgumentTypeError("Width and height must be positive integers.")
    return width_px, height_px


# AI prompt: "Write a parser for a positive floating-point font scale factor
# used to multiply the automatically chosen plot font size."
def parse_font_factor(value):
    """Parse a positive label font scale factor."""
    try:
        factor = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fontsize must be a positive number.") from exc
    if factor <= 0:
        raise argparse.ArgumentTypeError("fontsize must be greater than zero.")
    return factor


# AI prompt: "Write a parser for a positive floating-point line width option
# used when drawing tracks."
def parse_positive_float(value):
    """Parse a generic positive floating-point value."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a positive number.") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero.")
    return parsed


# AI prompt: "Write a parser for a generic non-negative floating-point value
# where zero is allowed."
def parse_non_negative_float(value):
    """Parse a generic non-negative floating-point value."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a non-negative number.") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("Value must be zero or greater.")
    return parsed


# AI prompt: "Write a parser that accepts a flexible matplotlib color string
# and simply returns the trimmed value."
def parse_color(value):
    """Return a trimmed matplotlib color string."""
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("Color must not be empty.")
    return text


# AI prompt: "Write a parser for a track selection option that accepts 'all',
# a single number, comma-separated numbers, and inclusive ranges like N-M."
def parse_track_selection(value):
    """Parse the individual-track selection expression."""
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("TRACKS must not be empty.")
    if text.lower() == "all":
        return "all"
    parts = [part.strip() for part in text.split(",")]
    selections = []
    for part in parts:
        if not part:
            raise argparse.ArgumentTypeError("TRACKS contains an empty item.")
        if re.fullmatch(r"\d+", part):
            selections.append((int(part), int(part)))
            continue
        range_match = re.fullmatch(r"(\d+)-(\d+)", part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                raise argparse.ArgumentTypeError("Track ranges must use ascending order.")
            selections.append((start, end))
            continue
        raise argparse.ArgumentTypeError(
            "TRACKS must be 'all', a number, a comma-separated list, or ranges like 2-5."
        )
    return selections


# AI prompt: "Write a parser for the overview label configuration that accepts
# 'none' or a comma-separated list of label lines, where colon joins multiple
# keywords onto the same line."
def parse_label_text(value):
    """Parse the overview label text configuration."""
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("LABELTEXT must not be empty.")
    if text.casefold() == "none":
        return []
    allowed = {"TRACKNAME", "TRACKNUMBER", "DATE", "LENGTH", "DURATION"}
    lines = []
    for line_text in text.split(","):
        line_text = line_text.strip()
        if not line_text:
            raise argparse.ArgumentTypeError("LABELTEXT contains an empty item.")
        line_items = []
        for part in line_text.split(":"):
            keyword = part.strip().upper()
            if not keyword:
                raise argparse.ArgumentTypeError("LABELTEXT contains an empty item.")
            if keyword not in allowed:
                raise argparse.ArgumentTypeError(
                    "LABELTEXT supports only TRACKNAME, TRACKNUMBER, DATE, LENGTH, DURATION, or none."
                )
            line_items.append(keyword)
        lines.append(line_items)
    return lines


# AI prompt: "Write a helper that converts the parsed track selection into the
# sorted list of 1-based track numbers that should be plotted."
def selected_track_numbers(selection, track_count):
    """Return the selected 1-based track numbers."""
    if selection == "all":
        return list(range(1, track_count + 1))
    numbers = set()
    for start, end in selection:
        for number in range(start, end + 1):
            if 1 <= number <= track_count:
                numbers.add(number)
    return sorted(numbers)


# AI prompt: "Write a helper that sanitizes a track name for safe use in a
# filesystem path while preserving readability."
def sanitize_filename_component(text):
    """Return a filesystem-safe filename component."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip()
    sanitized = re.sub(r"\s+", " ", sanitized)
    sanitized = sanitized.rstrip(".")
    return sanitized or "Unnamed"


# AI prompt: "Write a helper that resolves output directory and base name,
# defaulting to the input file's base name and the current working directory."
def resolve_output_settings(input_path, output_dir, output_base):
    """Return normalized output directory and base name."""
    resolved_dir = os.path.abspath(output_dir) if output_dir else os.getcwd()
    if output_base:
        # Treat --output-base as a filename stem. If the user accidentally
        # passes a path fragment here, keep only the basename so it does not
        # duplicate --output-dir in later joins.
        resolved_base = os.path.basename(os.path.normpath(os.path.expanduser(output_base)))
    else:
        resolved_base = os.path.splitext(os.path.basename(input_path))[0]
    resolved_base = sanitize_filename_component(resolved_base)
    return resolved_dir, resolved_base


# AI prompt: "Write a startup logger that prints the effective CLI parameters so
# the user can see file, sorting, zoom, size, and plot settings."
def print_startup_parameters(args, output_dir, output_base, overview_path, pdf_output_path):
    """Print the effective CLI parameters."""
    sort_mode = "default"
    if args.sort_date:
        sort_mode = "date"
    elif args.sort_distance:
        sort_mode = "distance"
    basemap = "ESRI World Street" if args.esri else "OpenStreetMap Mapnik"
    print("Parameter:")
    print(f"  GPX-Datei: {args.gpx_file}")
    print(f"  Sortierung: {sort_mode}")
    print(f"  Plot Overview: {'ja' if args.plot_overview else 'nein'}")
    print(f"  Plot Tracks: {args.plot_tracks if args.plot_tracks else '(nein)'}")
    label_text = (
        ",".join(":".join(group) for group in args.print_labels)
        if args.print_labels
        else "none"
    )
    print(f"  Overview Labels: {label_text}")
    print(f"  Overview Header: {args.header}")
    print(f"  Basiskarte: {basemap}")
    print(f"  Zoom: {args.zoom}")
    print(f"  Bildgroesse: {args.size[0]}x{args.size[1]}")
    print(f"  Fontfaktor: {args.fontsize}")
    print(f"  Linienfarbe: {args.line_color}")
    print(f"  Liniendicke: {args.line_width}")
    print(f"  Punktfarbe: {args.dot_color}")
    print(f"  Punktgroesse: {args.dot_size}")
    print(f"  Hintergrundfarbe: {args.background_color}")
    print(f"  Titelfarbe: {args.title_color}")
    print(f"  GPX-THRESHOLD-DISTANCE: {args.gpx_threshold_distance}")
    print(f"  GPX-THRESHOLD-ACCURACY: {args.gpx_threshold_accuracy}")
    print(f"  GPX-HORIZONTAL-SMOOTHING: {args.gpx_horizontal_smoothing_distance}")
    print(f"  GPX-ELEVATION-SMOOTHING: {args.gpx_elevation_smoothing_distance}")
    print(f"  GPX-MAXIMUM-VERTICAL-ACCURACY: {args.gpx_maximum_vertical_accuracy}")
    print(f"  GPX-MAXIMUM-HDOP: {args.gpx_maximum_hdop}")
    print(f"  GPX-MAXIMUM-VDOP: {args.gpx_maximum_vdop}")
    print(f"  Prefix entfernen: {args.remove_prefix if args.remove_prefix else '(keiner)'}")
    print(f"  Ausgabeordner: {output_dir}")
    print(f"  Basisname: {output_base}")
    print(f"  PDF-Datei: {pdf_output_path}")
    print(f"  Overview-Datei: {overview_path}")


# AI prompt: "Write a function that turns sorted track dicts into aligned table
# rows including cumulative length and formatted distance values, with an
# optional screen-only column for original sequence number."
def build_table_rows(tracks, include_original_sequence=False):
    """Build printable rows for the track table."""
    rows = []
    cumulative_km = 0.0
    for index, track in enumerate(tracks, start=1):
        cumulative_km += track["length_km"]
        distance_text = "N/A" if track["distance_km"] is None else f"{track['distance_km']:.1f}"
        marker = "*" if track.get("is_sort_exception") else ""
        row = [f"{index}.", marker]
        if include_original_sequence:
            row.append(f"#{track['original_sequence_number']}")
        row.extend(
            [
                track["name"],
                format_datetime_local(track["time"]),
                format_duration(track["duration"]),
                f"{track['length_km']:.1f}",
                f"{cumulative_km:.1f}",
                distance_text,
            ]
        )
        rows.append(row)
    return rows


# AI prompt: "Write a helper that builds structured JSON-ready summary data for
# the sorted tracks, including table fields and extra timing/coordinate details."
def build_table_summary_data(gpx_path, tracks, fallback_walking_speed_kmh=3.5):
    """Return compact summary data; point geometry remains in plot sidecars."""
    cumulative_km = 0.0
    track_items = []
    for track in tracks:
        cumulative_km += track["length_km"]
        track_items.append(
            {
                "nr": track["table_number"],
                "original_sequence_number": track["original_sequence_number"],
                "track_name": track["name"],
                "track_fingerprint": track.get("track_fingerprint"),
                "track_plot_image_filename": track.get("track_plot_image_filename"),
                "track_plot_time_lapse_image_filename": track.get("track_plot_time_lapse_image_filename"),
                "track_data_sidecar": track.get("track_data_sidecar"),
                "erstellungsdatum": format_datetime_local(track["time"]),
                "dauer": format_duration(track["duration"]),
                "laenge_km": round(track["length_km"], 1),
                "ascent_m": round(track.get("ascent_m", 0.0), 1),
                "descent_m": round(track.get("descent_m", 0.0), 1),
                "moving_average_speed_kmh": (
                    None
                    if track.get("moving_average_speed_kmh") is None
                    else round(track["moving_average_speed_kmh"], 2)
                ),
                "maximum_running_speed_kmh": (
                    None
                    if track.get("maximum_running_speed_kmh") is None
                    else round(track["maximum_running_speed_kmh"], 2)
                ),
                "kumulativ_km": round(cumulative_km, 1),
                "abstand_km": None if track["distance_km"] is None else round(track["distance_km"], 1),
                "start_time": format_datetime_local_seconds(track["start_time"]),
                "end_time": format_datetime_local_seconds(track["end_time"]),
                "timing_status": track.get("timing_status", "recorded"),
                "has_absolute_time": bool(track.get("has_absolute_time")),
                "source_structure": track.get("source_structure", "track"),
                "start_point": build_coordinate_point(
                    track["first_point"][0] if track["first_point"] is not None else None,
                    track["first_point"][1] if track["first_point"] is not None else None,
                ),
                "end_point": build_coordinate_point(
                    track["last_point"][0] if track["last_point"] is not None else None,
                    track["last_point"][1] if track["last_point"] is not None else None,
                ),
                "raw_point_count": track["raw_point_count"],
                "filtered_point_count": track["filtered_point_count"],
                "rejection_counts": track.get("rejection_counts", {}),
                "processing_options": track.get("processing_options", {}),
            }
        )
    return {
        "source_gpx": os.path.abspath(gpx_path),
        "tracks": track_items,
    }


def derived_track_data_payload(track, fallback_walking_speed_kmh=3.5):
    """Return map-independent geometry, timing, elevation, and speed data."""
    return {
        "version": 1,
        "track_number": track.get("table_number"),
        "original_sequence_number": track.get("original_sequence_number"),
        "track_name": track.get("name", ""),
        "track_fingerprint": track.get("track_fingerprint"),
        "timing_status": track.get("timing_status", "recorded"),
        "has_absolute_time": bool(track.get("has_absolute_time")),
        "gpx_processing": track.get("processing_options", {}),
        "raw_point_count": track.get("raw_point_count", 0),
        "retained_point_count": track.get("filtered_point_count", 0),
        "rejection_counts": track.get("rejection_counts", {}),
        "processed_geometry_source": "timed_track_points",
        "timed_track_points": timed_points_payload(
            track.get("point_records", []), fallback_walking_speed_kmh
        ),
        "running_speed": running_speed_metadata(track),
    }


def write_derived_track_data(context):
    """Write one current sidecar per track without touching any map image."""
    args = context["args"]
    root = Path(context["output_dir"]) / f"{context['output_base']}-trackdata"
    root.mkdir(parents=True, exist_ok=True)
    expected = set()
    for track in context["tracks"]:
        relative_path = Path(str(track["track_data_sidecar"]))
        output_path = Path(context["output_dir"]) / relative_path
        expected.add(output_path.resolve(strict=False))
        write_plot_metadata(
            derived_track_data_payload(track, args.fallback_walking_speed_kmh),
            output_path,
        )
    for stale_path in root.glob("*.json"):
        if stale_path.resolve(strict=False) not in expected:
            try:
                stale_path.unlink()
            except OSError:
                pass
    return root


def processed_point_json_record(point):
    """Return one processed point record with a JSON-safe timestamp."""
    record = dict(point)
    point_time = record.pop("time", None)
    record["time_iso"] = point_time.isoformat() if point_time is not None else None
    return record


# AI prompt: "Write a helper that formats a table with optional multi-line cells
# into aligned plain-text lines for both console and PDF output."
def table_lines(headers, rows):
    """Return aligned plain-text table lines."""
    all_rows = [headers] + rows
    widths = [0] * len(headers)
    split_rows = []
    for row in all_rows:
        split_row = [cell.splitlines() or [""] for cell in row]
        split_rows.append(split_row)
        for index, lines in enumerate(split_row):
            widths[index] = max(widths[index], max(len(line) for line in lines))

    template = "  ".join(f"{{:<{width}}}" for width in widths)
    rendered = []
    for row_index, split_row in enumerate(split_rows):
        row_height = max(len(lines) for lines in split_row)
        for line_index in range(row_height):
            rendered.append(
                template.format(
                    *[
                        lines[line_index] if line_index < len(lines) else ""
                        for lines in split_row
                    ]
                )
            )
        if row_index == 0:
            rendered.append(template.format(*["-" * width for width in widths]))
    return rendered


# AI prompt: "Write a simple table printer that prints preformatted aligned lines."
def print_table(headers, rows):
    """Print an aligned plain-text table."""
    for line in table_lines(headers, rows):
        print(line)


# AI prompt: "Write a helper that escapes text for a basic PDF literal string
# using WinAnsi-compatible encoding so umlauts and common special characters
# render correctly with a standard PDF font."
def pdf_escape_text(text):
    """Escape text for a PDF literal string."""
    sanitized = text.encode("cp1252", "replace").decode("cp1252")
    sanitized = sanitized.replace("\\", "\\\\")
    sanitized = sanitized.replace("(", "\\(")
    sanitized = sanitized.replace(")", "\\)")
    return sanitized


# AI prompt: "Write a small stdlib-only PDF writer that renders monospaced table
# text across one or more A4 pages and saves it to disk, using the same
# WinAnsi/cp1252-compatible text handling for both the title and table body."
def write_table_pdf(lines, output_path, title):
    """Write the table lines to a simple PDF file."""
    page_width = 595
    page_height = 842
    left_margin = 36
    top_margin = 40
    bottom_margin = 40
    title_font_size = 12
    body_font_size = 9
    line_height = 11
    usable_height = page_height - top_margin - bottom_margin - 24
    lines_per_page = max(1, usable_height // line_height)

    pages = []
    for start in range(0, len(lines), lines_per_page):
        page_lines = lines[start:start + lines_per_page]
        commands = [
            "BT",
            f"/F1 {title_font_size} Tf",
            f"1 0 0 1 {left_margin} {page_height - top_margin} Tm",
            f"({pdf_escape_text(title)}) Tj",
            "0 -18 Td",
            f"/F1 {body_font_size} Tf",
        ]
        for index, line in enumerate(page_lines):
            if index > 0:
                commands.append(f"0 -{line_height} Td")
            commands.append(f"({pdf_escape_text(line)}) Tj")
        commands.append("ET")
        pages.append("\n".join(commands).encode("cp1252", "replace"))

    objects = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"),
    ]

    page_object_numbers = []
    next_object_number = 4
    for content in pages:
        content_object_number = next_object_number
        page_object_number = next_object_number + 1
        stream_object = (
            f"<< /Length {len(content)} >>\nstream\n".encode("latin-1")
            + content
            + b"\nendstream"
        )
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_object_number} 0 R >>"
        ).encode("latin-1")
        objects.append((content_object_number, stream_object))
        objects.append((page_object_number, page_object))
        page_object_numbers.append(page_object_number)
        next_object_number += 2

    kids = " ".join(f"{number} 0 R" for number in page_object_numbers)
    objects.insert(1, (2, f"<< /Type /Pages /Count {len(page_object_numbers)} /Kids [{kids}] >>".encode("latin-1")))

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, content in objects:
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("latin-1"))
        pdf.extend(content)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )

    with open(output_path, "wb") as pdf_file:
        pdf_file.write(pdf)


# AI prompt: "Write a helper that computes a plot extent for a rectangular plot
# area, keeps x/y data units equal, and guarantees all points remain at least
# 5 percent away from the plot edges."
def extent_for_image(all_x, all_y, image_size):
    """Return an extent matched to the plot-area aspect ratio with 5 percent margins."""
    min_x = min(all_x)
    max_x = max(all_x)
    min_y = min(all_y)
    max_y = max(all_y)
    width_px, height_px = image_size
    image_ratio = width_px / height_px

    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    padded_span_x = span_x / 0.9
    padded_span_y = span_y / 0.9

    extent_width = max(padded_span_x, padded_span_y * image_ratio)
    extent_height = max(padded_span_y, padded_span_x / image_ratio)

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    half_width = extent_width / 2
    half_height = extent_height / 2
    return (
        center_x - half_width,
        center_x + half_width,
        center_y - half_height,
        center_y + half_height,
    )


# This fills a 1920x1080 plotting area's short axis at tile zoom 16 closely
# enough to avoid tile enlargement while retaining substantially more detail.
MINIMUM_MAP_SHORT_DIMENSION_M = 2_250.0


def extent_with_minimum_short_dimension(
    extent,
    minimum_short_dimension_m=MINIMUM_MAP_SHORT_DIMENSION_M,
):
    """Expand a centered extent until its smaller dimension reaches the minimum."""
    min_x, max_x, min_y, max_y = extent
    width = max_x - min_x
    height = max_y - min_y
    short_dimension = min(width, height)
    minimum = max(0.0, float(minimum_short_dimension_m))
    if short_dimension <= 0.0 or short_dimension >= minimum:
        return extent
    scale = minimum / short_dimension
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    half_width = width * scale / 2.0
    half_height = height * scale / 2.0
    return (
        center_x - half_width,
        center_x + half_width,
        center_y - half_height,
        center_y + half_height,
    )


# AI prompt: "Write a helper that limits the OSM zoom level so the requested map
# extent fits into the requested plot-area pixel size while preserving coverage."
def fitted_zoom_level(requested_zoom, extent, image_size):
    """Return a zoom level that fits the extent into the target plot-area pixel size."""
    world_width_m = 40075016.68557849
    tile_size_px = 256
    width_px, height_px = image_size
    min_x, max_x, min_y, max_y = extent
    meters_per_pixel_needed = max(
        (max_x - min_x) / max(width_px, 1),
        (max_y - min_y) / max(height_px, 1),
    )
    if meters_per_pixel_needed <= 0:
        return max(0, requested_zoom)
    max_zoom_that_fits = floor(log(world_width_m / (tile_size_px * meters_per_pixel_needed), 2))
    return max(0, min(requested_zoom, max_zoom_that_fits))


# AI prompt: "Write a plotting helper that chooses a conservative font size based
# on how many tracks are being labeled."
def label_font_size(track_count):
    """Choose a label font size that shrinks as track count grows."""
    return max(6.0, min(12.0, 14.0 - 0.35 * track_count))


# AI prompt: "Write a helper that returns projected track geometries and the
# flattened coordinate lists needed for extent calculation."
def projected_track_data(tracks):
    """Return flattened and segment-preserving projected track coordinates."""
    projected_tracks = []
    projected_track_segments = []
    all_x = []
    all_y = []
    for track in tracks:
        projected_segments = [
            [lonlat_to_web_mercator(lon, lat) for lat, lon in segment]
            for segment in track.get("segments", [])
            if segment
        ]
        if not projected_segments and track.get("points"):
            projected_segments = [[lonlat_to_web_mercator(lon, lat) for lat, lon in track["points"]]]
        projected_points = [point for segment in projected_segments for point in segment]
        projected_tracks.append(projected_points)
        projected_track_segments.append(projected_segments)
        for x_coord, y_coord in projected_points:
            all_x.append(x_coord)
            all_y.append(y_coord)
    return projected_tracks, projected_track_segments, all_x, all_y


# AI prompt: "Write a helper that selects the configured basemap provider from
# contextily based on whether ESRI mode is enabled."
def basemap_provider(
    contextily_module,
    use_esri=False,
    provider="osm",
    custom_url="",
    custom_attribution="",
    maximum_zoom=19,
    credential_id="default",
):
    """Return the selected basemap provider."""
    selected = "esri" if use_esri else provider
    return contextily_provider(
        contextily_module,
        selected,
        custom_url,
        custom_attribution,
        maximum_zoom,
        credential_id,
    )


# AI prompt: "Write a helper that draws one projected track with configurable
# line and dot styling."
def draw_track(ax, projected_points, line_color, line_width, dot_color, dot_size):
    """Draw one projected track on the axes."""
    xs = [point[0] for point in projected_points]
    ys = [point[1] for point in projected_points]
    ax.plot(
        xs,
        ys,
        color=line_color,
        linewidth=line_width,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=3,
    )
    if dot_size > 0:
        ax.scatter(
            [xs[0], xs[-1]],
            [ys[0], ys[-1]],
            s=dot_size,
            c=dot_color,
            marker="o",
            linewidths=0,
            zorder=4,
        )


# AI prompt: "Write a helper that builds the overview label lines from a list of
# requested keywords such as track name, date, length, and duration."
def overview_label_lines(track, label_items):
    """Return the configured overview label lines for one track."""
    mapping = {
        "TRACKNAME": track["name"],
        "TRACKNUMBER": str(track["table_number"]),
        "DATE": format_date_local_short(track["time"]),
        "LENGTH": f"{track['length_km']:.1f} km",
        "DURATION": format_duration_hhmm(track["duration"]),
    }
    return [" ".join(mapping[item] for item in line_items) for line_items in label_items]


# AI prompt: "Write a helper that draws one boxed point label above a projected
# point using the same formatting for overview and single-track markers."
def add_boxed_point_label(ax, x_coord, y_coord, text, image_height_px, dpi, marker_color):
    """Draw one boxed point label above a projected point."""
    marker_font_size = max(8.0, 0.02 * image_height_px * 72.0 / dpi)
    line_offset_points = marker_font_size * 1.2
    marker_box = {
        "boxstyle": "round,pad=0.2",
        "facecolor": "white",
        "edgecolor": marker_color,
        "linewidth": 0.8,
    }
    ax.annotate(
        text,
        xy=(x_coord, y_coord),
        xytext=(0, line_offset_points),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=marker_font_size,
        color=marker_color,
        bbox=marker_box,
    )


# AI prompt: "Write a helper that adds rotated on-track labels for the overview
# image using the configured font scaling and user-selected label content."
def add_overview_labels(ax, tracks, projected_tracks, actual_font_size, span, label_color, label_items):
    """Add rotated labels to the overview plot."""
    if not label_items:
        return
    font_height = span * (0.012 * (actual_font_size / 10.0))
    label_offset = 1.0 * font_height

    for index, (track, projected_points) in enumerate(zip(tracks, projected_tracks)):
        if not projected_points:
            continue
        start_x, start_y = projected_points[0]
        end_x, end_y = projected_points[-1]
        center_x = (start_x + end_x) / 2
        center_y = (start_y + end_y) / 2
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        line_length = sqrt(delta_x ** 2 + delta_y ** 2)
        if line_length == 0:
            normal_x, normal_y = 0.0, 1.0
            rotation = 0.0
        else:
            normal_x = -delta_y / line_length
            normal_y = delta_x / line_length
            rotation = degrees(atan2(delta_y, delta_x))
            if rotation > 90:
                rotation -= 180
            if rotation < -90:
                rotation += 180

        direction = -1.0 if index % 2 == 0 else 1.0
        text_x = center_x + normal_x * label_offset * direction
        text_y = center_y + normal_y * label_offset * direction
        label_text = "\n".join(overview_label_lines(track, label_items))
        ax.text(
            text_x,
            text_y,
            label_text,
            fontsize=actual_font_size,
            rotation=rotation,
            rotation_mode="anchor",
            ha="center",
            va="center",
            multialignment="center",
            linespacing=1.15,
            color=label_color,
        )


# AI prompt: "Write a helper that marks the start of the first sorted track and
# the end of the last sorted track on the overview plot using boxed labels."
def add_overview_markers(ax, projected_tracks, image_height_px, dpi, marker_color):
    """Add boxed Start and End labels to the overview plot."""
    non_empty_tracks = [points for points in projected_tracks if points]
    if not non_empty_tracks:
        return
    first_x, first_y = non_empty_tracks[0][0]
    last_x, last_y = non_empty_tracks[-1][-1]
    add_boxed_point_label(ax, first_x, first_y, "Start", image_height_px, dpi, marker_color)
    add_boxed_point_label(ax, last_x, last_y, "End", image_height_px, dpi, marker_color)


# AI prompt: "Write a helper that formats the title lines for an individual
# track image with bold track name and date, length, and duration below it."
def single_track_heading(track):
    """Return heading lines for one track image."""
    duration_text = "N/A" if track["duration"] is None else format_duration(track["duration"]).rjust(5, "0")
    subtitle = f"{format_date_local(track['time'])} ({track['length_km']:.1f} km, {duration_text})"
    return track["name"], subtitle


# AI prompt: "Write a helper that adds boxed start and end labels above the
# first and last point of a single-track plot."
def add_single_track_markers(ax, projected_points, image_height_px, dpi, marker_color):
    """Add boxed start and end labels to a single-track plot."""
    start_x, start_y = projected_points[0]
    end_x, end_y = projected_points[-1]
    add_boxed_point_label(ax, start_x, start_y, "Start", image_height_px, dpi, marker_color)
    add_boxed_point_label(ax, end_x, end_y, "Ende", image_height_px, dpi, marker_color)


# AI prompt: "Write a plotting helper that renders tracks to a PNG using shared
# configuration for overview and single-track outputs."
def render_track_plot(
    tracks,
    zoom_level,
    image_size,
    font_factor,
    use_esri,
    output_path,
    line_color,
    line_width,
    dot_color,
    dot_size,
    background_color,
    title_color,
    overview_label_items,
    overview_header,
    overview_mode,
    map_layout="standard",
    track_edge_margin_fraction=DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
    map_provider="osm",
    custom_map_url="",
    custom_map_attribution="",
    maximum_map_zoom=19,
    map_request_timeout_seconds=12.0,
    map_credential_id="default",
    minimum_short_dimension_m=MINIMUM_MAP_SHORT_DIMENSION_M,
    media_map_date=None,
    background_only=True,
    media_map_title="",
):
    """Render one overview or one single-track plot."""
    try:
        import matplotlib
        if str(matplotlib.get_backend()).lower() != "agg":
            matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        import contextily as cx
    except ImportError as exc:
        raise RuntimeError(
            "Plotting requires matplotlib and contextily. Install them first."
        ) from exc

    projected_tracks, projected_track_segments, all_x, all_y = projected_track_data(tracks)

    if not all_x or not all_y:
        raise RuntimeError("No valid track points available for plotting.")

    dpi = 100
    width_px, height_px = image_size
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    fig.patch.set_facecolor(background_color)
    if overview_mode:
        header_font_size = max(label_font_size(len(tracks)) * font_factor + 2.0, 10.0)
        # The overview is a complete basemap. Titles are drawn at playback time
        # so the Intro can use the same image without a duplicated heading.
        side_margin_px = 2.0
        bottom_margin_px = 2.0
        top_margin_px = 2.0
        axes_box = [
            side_margin_px / width_px,
            bottom_margin_px / height_px,
            1.0 - (2.0 * side_margin_px / width_px),
            1.0 - ((top_margin_px + bottom_margin_px) / height_px),
        ]
    elif media_map_date is not None:
        title_font_size = max(label_font_size(1) * font_factor + 2.0, 10.0)
        subtitle_font_size = max(label_font_size(1) * font_factor, 9.0)
        title_height_px = title_font_size * dpi / 72.0
        subtitle_height_px = (
            subtitle_font_size * dpi / 72.0
            if str(media_map_title).strip()
            else 0.0
        )
        heading_total_px = title_height_px + subtitle_height_px
        line_gap_px = 0.05 * heading_total_px if subtitle_height_px else 0.0
        top_margin_px = 0.05 * heading_total_px
        reserved_header_px = (
            top_margin_px
            + title_height_px
            + line_gap_px
            + subtitle_height_px
        ) * STAGE_HEADER_HEIGHT_SCALE
        side_margin_px = 2.0
        bottom_margin_px = 2.0
        heading_bottom_px = (
            height_px - reserved_header_px
        )
        axes_box = [
            side_margin_px / width_px,
            bottom_margin_px / height_px,
            1.0 - (2.0 * side_margin_px / width_px),
            max(0.60, (heading_bottom_px - bottom_margin_px) / height_px),
        ]

    else:
        title_font_size = max(label_font_size(len(tracks)) * font_factor + 2.0, 10.0)
        subtitle_font_size = max(label_font_size(len(tracks)) * font_factor, 9.0)
        title_height_px = title_font_size * dpi / 72.0
        subtitle_height_px = subtitle_font_size * dpi / 72.0
        heading_total_px = title_height_px + subtitle_height_px
        line_gap_px = 0.05 * heading_total_px
        top_margin_px = 0.05 * heading_total_px
        reserved_header_px = (
            top_margin_px
            + title_height_px
            + line_gap_px
            + subtitle_height_px
        ) * STAGE_HEADER_HEIGHT_SCALE
        side_margin_px = 2.0
        bottom_margin_px = 2.0
        heading_bottom_px = (
            height_px - reserved_header_px
        )
        axes_box = [
            side_margin_px / width_px,
            bottom_margin_px / height_px,
            1.0 - (2.0 * side_margin_px / width_px),
            max(0.60, (heading_bottom_px - bottom_margin_px) / height_px),
        ]

    runtime_header_fraction = max(
        0.08,
        min(0.20, 1.0 - (axes_box[1] + axes_box[3])),
    )
    if not overview_mode and map_layout == "time-lapse":
        # Time-Lapse headers are drawn by the player. Let the map continue
        # behind them so Black, Semi-transparent, and Off are real choices.
        axes_box = [
            side_margin_px / width_px,
            bottom_margin_px / height_px,
            1.0 - (2.0 * side_margin_px / width_px),
            1.0 - ((2.0 * bottom_margin_px) / height_px),
        ]

    plot_area_size = (
        max(1.0, width_px * axes_box[2]),
        max(1.0, height_px * axes_box[3]),
    )
    standard_extent = extent_for_image(all_x, all_y, plot_area_size)
    if not overview_mode:
        standard_extent = extent_with_minimum_short_dimension(
            standard_extent,
            minimum_short_dimension_m,
        )
    selected_optimized_corner = None
    extent_shift = (0.0, 0.0)
    media_clear_options = None
    if not overview_mode and map_layout == "time-lapse":
        selected_extent, selected_optimized_corner, extent_shift, media_clear_options = optimized_track_extent(
            standard_extent,
            projected_tracks[0],
            (width_px, height_px),
            axes_box,
            track_edge_margin_fraction,
            DEFAULT_GRID_LONG_AXIS,
            connect_points=media_map_date is None,
        )
    else:
        selected_extent = standard_extent
    min_x, max_x, min_y, max_y = selected_extent
    effective_zoom = fitted_zoom_level(
        min(int(zoom_level), int(maximum_map_zoom)),
        (min_x, max_x, min_y, max_y),
        plot_area_size,
    )
    span = max(max_x - min_x, max_y - min_y)

    ax = fig.add_axes(axes_box)
    ax.set_facecolor(background_color)

    auto_font_size = label_font_size(len(tracks))
    actual_font_size = max(1.0, auto_font_size * font_factor)

    header_lines = []
    if overview_mode:
        header_lines = [str(overview_header)] if str(overview_header).strip() else []
        header_font_size = max(actual_font_size + 2.0, 10.0)
        header_height_frac = (header_font_size * dpi / 72.0) / height_px
        header_y = 1.0 - (0.05 * header_height_frac)
        if not background_only:
            fig.text(
                0.5,
                header_y,
                overview_header,
                ha="center",
                va="top",
                fontsize=header_font_size,
                fontweight="bold",
                color=title_color,
            )
    elif media_map_date is not None:
        title_font_size = max(actual_font_size + 2.0, 10.0)
        subtitle_font_size = max(actual_font_size, 9.0)
        title_height_frac = (title_font_size * dpi / 72.0) / height_px
        date_label = media_map_date.strftime("%d.%m.%Y") if hasattr(media_map_date, "strftime") else str(media_map_date)
        stage_title = str(media_map_title or "").strip()
        header_lines = [line for line in (stage_title, date_label) if line]
        if stage_title:
            subtitle_height_frac = (subtitle_font_size * dpi / 72.0) / height_px
            line_gap_frac = 0.05 * (title_height_frac + subtitle_height_frac)
            title_y = 1.0 - (0.05 * (title_height_frac + subtitle_height_frac))
            subtitle_y = title_y - title_height_frac - line_gap_frac
        else:
            title_y = 1.0 - (0.05 * title_height_frac)
            subtitle_y = None
        if not background_only:
            fig.text(
                0.5,
                title_y,
                stage_title or date_label,
                ha="center",
                va="top",
                fontsize=title_font_size,
                fontweight="bold",
                color=title_color,
            )
            if subtitle_y is not None:
                fig.text(
                    0.5,
                    subtitle_y,
                    date_label,
                    ha="center",
                    va="top",
                    fontsize=subtitle_font_size,
                    color=title_color,
                )
    else:
        title_line, subtitle_line = single_track_heading(tracks[0])
        header_lines = [line for line in (title_line, subtitle_line) if str(line).strip()]
        title_font_size = max(actual_font_size + 2.0, 10.0)
        subtitle_font_size = max(actual_font_size, 9.0)
        title_height_frac = (title_font_size * dpi / 72.0) / height_px
        subtitle_height_frac = (subtitle_font_size * dpi / 72.0) / height_px
        line_gap_frac = 0.05 * (title_height_frac + subtitle_height_frac)
        title_y = 1.0 - ((0.05 * (title_font_size * dpi / 72.0 + subtitle_font_size * dpi / 72.0)) / height_px)
        subtitle_y = title_y - title_height_frac - line_gap_frac
        if not background_only:
            fig.text(
                0.5,
                title_y,
                title_line,
                ha="center",
                va="top",
                fontsize=title_font_size,
                fontweight="bold",
                color=title_color,
            )
            fig.text(
                0.5,
                subtitle_y,
                subtitle_line,
                ha="center",
                va="top",
                fontsize=subtitle_font_size,
                color=title_color,
            )

    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect("equal", adjustable="box")
    try:
        configure_contextily_cache(cx, DEFAULT_TILE_CACHE_DIR)
        with contextily_request_timeout(cx, map_request_timeout_seconds, map_provider):
            with tolerate_missing_tiles(cx) as missing_tile_report:
                cx.add_basemap(
                    ax,
                    source=basemap_provider(
                        cx,
                        use_esri,
                        map_provider,
                        custom_map_url,
                        custom_map_attribution,
                        maximum_map_zoom,
                        map_credential_id,
                    ),
                    zoom=effective_zoom,
                )
    except TileProviderAccessError:
        raise
    except Exception as exc:
        provider_name = provider_display_name("esri" if use_esri else map_provider)
        raise RuntimeError(
            f"Could not download the {provider_name} basemap. "
            "Please check the internet connection or try again later; the map server may have timed out."
        ) from exc
    if media_map_date is None and not background_only:
        for track_segments in projected_track_segments:
            for projected_points in track_segments:
                if projected_points:
                    draw_track(ax, projected_points, line_color, line_width, dot_color, dot_size)
    if overview_mode and not background_only:
        add_overview_markers(ax, projected_tracks, height_px, dpi, line_color)
        add_overview_labels(
            ax,
            tracks,
            projected_tracks,
            actual_font_size,
            span,
            line_color,
            overview_label_items,
        )
    if not background_only and not overview_mode and media_map_date is None and projected_tracks and projected_tracks[0]:
        add_single_track_markers(ax, projected_tracks[0], height_px, dpi, line_color)
    ax.axis("off")
    fig.savefig(output_path, dpi=dpi, facecolor=background_color, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    metadata = {
        "map_content_version": MAP_CONTENT_VERSION,
        "background_only": bool(background_only),
        "stage_kind": "overview" if overview_mode else ("media_stage" if media_map_date is not None else "gpx_track"),
        "header_lines": header_lines,
        "overlay_defaults": {
            "gpx_mode": "line",
            "media_mode": "dots",
            "line_color": line_color,
            "line_width": float(line_width),
            "dot_color": dot_color,
            "dot_size": float(dot_size),
            "title_color": title_color,
        },
        "crs": "EPSG:3857",
        "image_size_px": {"width": width_px, "height": height_px},
        "axes_box_fraction": {
            "left": axes_box[0],
            "bottom": axes_box[1],
            "width": axes_box[2],
            "height": axes_box[3],
        },
        "runtime_header_fraction": runtime_header_fraction,
        "extent_mercator": {
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
        },
        "basemap": provider_display_name("esri" if use_esri else map_provider),
        "map_attribution": provider_attribution(
            "esri" if use_esri else map_provider,
            custom_map_attribution,
        ),
        "effective_zoom": effective_zoom,
        "missing_basemap_tiles": missing_tile_report.count,
        "minimum_short_dimension_m": (
            float(minimum_short_dimension_m) if not overview_mode else None
        ),
    }
    if overview_mode:
        runtime_line_height = max(
            0.035,
            min(0.08, (header_font_size * 1.35 * dpi / 72.0) / height_px),
        )
        title_height = runtime_line_height
        stage_height = min(0.18, 2.0 * runtime_line_height)
        metadata["overview_title_box_fraction"] = {
            "left": 0.0,
            "bottom": 1.0 - title_height,
            "width": 1.0,
            "height": title_height,
        }
        metadata["stage_header_box_fraction"] = {
            "left": 0.0,
            "bottom": 1.0 - stage_height,
            "width": 1.0,
            "height": stage_height,
        }
    geometry_segments = []
    for track_index, _track_segments in enumerate(projected_track_segments):
        # Store geographic rather than projected points so every renderer uses
        # the sidecar projection metadata consistently.
        raw_segments = tracks[track_index].get("segments", []) if track_index < len(tracks) else []
        if raw_segments:
            geometry_segments.append(raw_segments)
        elif track_index < len(tracks):
            geometry_segments.append(tracks[track_index].get("points", []))
    flattened_segments = []
    for item in geometry_segments:
        if item and isinstance(item[0], (list, tuple)) and len(item[0]) >= 2 and isinstance(item[0][0], (int, float)):
            flattened_segments.append(item)
        else:
            flattened_segments.extend(item)
    metadata["overlay_geometry"] = json_overlay_geometry(
        flattened_segments,
        geometry_kind="media_points" if media_map_date is not None else ("overview_tracks" if overview_mode else "gpx_track"),
        estimated=media_map_date is not None,
    )
    if overview_mode and overview_label_items:
        metadata["overview_dynamic_labels"] = [
            {
                "lat": float(track["points"][len(track["points"]) // 2][0]),
                "lon": float(track["points"][len(track["points"]) // 2][1]),
                "text": "\n".join(overview_label_lines(track, overview_label_items)),
            }
            for track in tracks
            if track.get("points")
        ]
    if not overview_mode:
        if media_clear_options is None:
            media_clear_options = clear_box_options_for_extent(
                projected_tracks[0],
                selected_extent,
                (width_px, height_px),
                axes_box,
                track_edge_margin_fraction,
                DEFAULT_GRID_LONG_AXIS,
                connect_points=media_map_date is None,
            )
        metadata.update(
            {
                "map_layout": map_layout,
                "track_edge_margin_fraction": track_edge_margin_fraction,
                "selected_optimized_corner": selected_optimized_corner,
                "extent_shift_mercator": {"x": extent_shift[0], "y": extent_shift[1]},
                "media_clear_box_options": media_clear_options,
            }
        )
    if media_map_date is not None:
        metadata.update(
            {
                "map_kind": "media",
                "track_name": str(media_map_title or "").strip(),
                "media_stage_name": str(media_map_title or "").strip(),
                "media_map_date": media_map_date.isoformat() if hasattr(media_map_date, "isoformat") else str(media_map_date),
                "media_points": [
                    build_coordinate_point(point[0], point[1])
                    for track in tracks
                    for point in track.get("points", [])
                    if len(point) >= 2
                ],
            }
        )
    return effective_zoom, actual_font_size, metadata


def render_media_location_map(
    coordinates,
    media_date: date,
    output_path,
    *,
    media_points=None,
    stage_name="",
    zoom_level=15,
    image_size=DEFAULT_IMAGE_SIZE,
    font_factor=1.0,
    use_esri=False,
    background_color="black",
    title_color="white",
    map_provider="osm",
    custom_map_url="",
    custom_map_attribution="",
    maximum_map_zoom=19,
    map_request_timeout_seconds=12.0,
    map_credential_id="default",
    minimum_short_dimension_m=MINIMUM_MAP_SHORT_DIMENSION_M,
    map_layout="standard",
    track_edge_margin_fraction=DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
    adventure_render_parameters=None,
):
    """Render a date-only map containing all supplied media coordinates."""
    points = [
        (float(latitude), float(longitude))
        for latitude, longitude in coordinates
        if latitude is not None and longitude is not None
    ]
    if not points:
        raise ValueError("A media location map requires at least one GPS coordinate.")
    output = Path(output_path).expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    pseudo_track = {
        "name": str(stage_name or "").strip(),
        "time": datetime.combine(media_date, datetime.min.time()).replace(tzinfo=timezone.utc),
        "points": points,
    }
    # Rendering may normalize the temporary geometry in place. Map identity
    # must describe the original media coordinates shared with the control file.
    media_fingerprint = media_coordinates_fingerprint(media_date, points)
    effective_zoom, actual_font_size, metadata = render_track_plot(
        [pseudo_track],
        zoom_level,
        image_size,
        font_factor,
        use_esri,
        str(output),
        "blue",
        0.0,
        "white",
        0.0,
        background_color,
        title_color,
        [],
        "",
        False,
        map_layout=map_layout,
        track_edge_margin_fraction=track_edge_margin_fraction,
        map_provider=map_provider,
        custom_map_url=custom_map_url,
        custom_map_attribution=custom_map_attribution,
        maximum_map_zoom=maximum_map_zoom,
        map_request_timeout_seconds=map_request_timeout_seconds,
        map_credential_id=map_credential_id,
        minimum_short_dimension_m=minimum_short_dimension_m,
        media_map_date=media_date,
        media_map_title=stage_name,
    )
    media_clear_options = metadata.pop("media_clear_box_options", None)
    rich_media_points = list(media_points) if isinstance(media_points, (list, tuple)) else [
        build_coordinate_point(latitude, longitude) for latitude, longitude in points
    ]
    metadata.update(
        {
            "output_image": str(output),
            "output_metadata": str(output.with_suffix(".json")),
            "media_fingerprint": media_fingerprint,
            "track_fingerprint": media_fingerprint,
            "track_name": str(stage_name or "").strip(),
            "media_stage_name": str(stage_name or "").strip(),
            "media_points": rich_media_points,
            "overlay_geometry": json_overlay_geometry(
                [rich_media_points], geometry_kind="media_points", estimated=True
            ),
        }
    )
    if str(stage_name or "").strip():
        metadata.setdefault(
            "header_lines",
            [str(stage_name).strip(), media_date.strftime("%d.%m.%Y")],
        )
    if isinstance(adventure_render_parameters, dict):
        metadata["adventure_render_parameters"] = dict(adventure_render_parameters)
    if media_clear_options is not None:
        metadata["media_clear_boxes"] = build_media_clear_boxes_metadata(
            media_clear_options,
            image_size,
            track_edge_margin_fraction,
            media_fingerprint,
            DEFAULT_GRID_LONG_AXIS,
        )
    metadata.update(image_origin_metadata(metadata))
    write_plot_metadata(metadata, output.with_suffix(".json"))
    return {
        "output_image": output,
        "output_metadata": output.with_suffix(".json"),
        "effective_zoom": effective_zoom,
        "font_size": actual_font_size,
        "metadata": metadata,
    }


def render_media_overview_map(
    coordinates,
    output_path,
    *,
    media_points=None,
    header="",
    zoom_level=8,
    image_size=DEFAULT_IMAGE_SIZE,
    font_factor=1.0,
    use_esri=False,
    background_color="black",
    title_color="white",
    map_provider="osm",
    custom_map_url="",
    custom_map_attribution="",
    maximum_map_zoom=19,
    map_request_timeout_seconds=12.0,
    map_credential_id="default",
    adventure_render_parameters=None,
):
    """Render one shared overview basemap for a media-only Adventure."""
    points = [
        (float(latitude), float(longitude))
        for latitude, longitude in coordinates
        if latitude is not None and longitude is not None
    ]
    if not points:
        raise ValueError("A media overview map requires at least one GPS coordinate.")
    output = Path(output_path).expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    pseudo_track = {"name": "", "time": None, "points": points}
    effective_zoom, actual_font_size, metadata = render_track_plot(
        [pseudo_track],
        zoom_level,
        image_size,
        font_factor,
        use_esri,
        str(output),
        "blue",
        0.0,
        "white",
        0.0,
        background_color,
        title_color,
        [],
        header,
        True,
        map_provider=map_provider,
        custom_map_url=custom_map_url,
        custom_map_attribution=custom_map_attribution,
        maximum_map_zoom=maximum_map_zoom,
        map_request_timeout_seconds=map_request_timeout_seconds,
        map_credential_id=map_credential_id,
        background_only=True,
    )
    fingerprint = media_overview_fingerprint(points)
    metadata.update(
        {
            "stage_kind": "media_stage",
            "map_kind": "media_overview",
            "header_lines": [str(header)] if str(header).strip() else [],
            "media_points": list(media_points) if isinstance(media_points, (list, tuple)) else [
                build_coordinate_point(latitude, longitude) for latitude, longitude in points
            ],
            "media_fingerprint": fingerprint,
            "track_fingerprint": fingerprint,
            "output_image": str(output),
            "output_metadata": str(output.with_suffix(".json")),
        }
    )
    metadata["overlay_geometry"] = json_overlay_geometry(
        [metadata["media_points"]], geometry_kind="media_points", estimated=True
    )
    if isinstance(adventure_render_parameters, dict):
        metadata["adventure_render_parameters"] = dict(adventure_render_parameters)
    metadata.update(image_origin_metadata(metadata))
    write_plot_metadata(metadata, output.with_suffix(".json"))
    return {
        "output_image": output,
        "output_metadata": output.with_suffix(".json"),
        "effective_zoom": effective_zoom,
        "font_size": actual_font_size,
        "metadata": metadata,
    }


def media_overview_fingerprint(coordinates) -> str:
    """Return the stable ordered-coordinate fingerprint for a media overview."""
    return hashlib.sha256(
        "|".join(
            f"{float(latitude):.8f},{float(longitude):.8f}"
            for latitude, longitude in coordinates
            if latitude is not None and longitude is not None
        ).encode("utf-8")
    ).hexdigest()


def media_coordinates_fingerprint(media_date: date, coordinates) -> str:
    """Return the stable fingerprint shared by media-map creation and status checks."""
    payload = media_date.isoformat() + "|" + "|".join(
        f"{float(latitude):.8f},{float(longitude):.8f}"
        for latitude, longitude in coordinates
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def media_coordinates_fingerprint_matches(saved_fingerprint, media_date, coordinates) -> bool:
    """Accept exact sidecar coordinates and their six-decimal control-file form."""
    saved = str(saved_fingerprint or "")
    if saved == media_coordinates_fingerprint(media_date, coordinates):
        return True
    rounded_coordinates = [
        (round(float(latitude), 6), round(float(longitude), 6))
        for latitude, longitude in coordinates
    ]
    return saved == media_coordinates_fingerprint(media_date, rounded_coordinates)


def media_map_metadata_matches_coordinates(metadata, media_date, coordinates) -> bool:
    """Accept equivalent media-map geometry even when equal-time rows reordered."""
    if not isinstance(metadata, dict):
        return False
    clear_boxes = metadata.get("media_clear_boxes")
    if (
        not isinstance(clear_boxes, dict)
        or clear_boxes.get("version") not in MEDIA_CLEAR_BOX_COMPATIBLE_VERSIONS
    ):
        return False
    if media_coordinates_fingerprint_matches(
        metadata.get("media_fingerprint"),
        media_date,
        coordinates,
    ):
        return True
    saved_points = metadata.get("media_points")
    if not isinstance(saved_points, list):
        return False
    try:
        saved_coordinates = sorted(
            (round(float(point["lat"]), 6), round(float(point["lon"]), 6))
            for point in saved_points
            if isinstance(point, dict)
        )
        current_coordinates = sorted(
            (round(float(latitude), 6), round(float(longitude), 6))
            for latitude, longitude in coordinates
        )
    except (KeyError, TypeError, ValueError):
        return False
    return saved_coordinates == current_coordinates


# AI prompt: "Write argparse setup for a standalone GPX track CLI with positional
# file input, sorting flags, plotting, zoom selection, and a usage example."
def build_argument_parser():
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Read a GPX 1.1 file, list all tracks in a table, and optionally plot them.",
        epilog=(
            "Example: python3 gpx_tracks_table.py --plot-Overview --plot-Tracks=1,3-5 "
            "--output-dir ./out --output-base trip --Zoom=8 --Size=1600x1200 "
            "--fontsize=1.2 --line-color '#0066cc' --dot-color black "
            "--print-Labels TRACKNAME:DATE,LENGTH --header 'My Overview' "
            "--background-color black --title-color white "
            "/path/to/file.gpx"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("gpx_file", help="Path to the input .gpx file")
    parser.add_argument(
        "--sort-date",
        action="store_true",
        help="Strictly sort by date/time, oldest first, with unknown times last.",
    )
    parser.add_argument(
        "--sort-distance",
        action="store_true",
        help="Sort by distance from the anchor point.",
    )
    parser.add_argument(
        "--sort-original",
        "--track-order",
        dest="sort_original",
        action="store_true",
        help="Preserve the original GPX track order.",
    )
    parser.add_argument(
        "--plot-Overview",
        dest="plot_overview",
        action="store_true",
        help="Create one overview PNG with all tracks on the default OpenStreetMap basemap.",
    )
    parser.add_argument(
        "--plot-Tracks",
        dest="plot_tracks",
        type=parse_track_selection,
        help="Create one PNG per selected track: all, one number, comma lists, or ranges like 2-5.",
    )
    parser.add_argument(
        "--print-Labels",
        dest="print_labels",
        type=parse_label_text,
        default=[["LENGTH"], ["TRACKNAME"], ["DATE"]],
        help="Overview label lines: use commas for new lines and colons for one shared line. Keywords: TRACKNUMBER, TRACKNAME, DATE, LENGTH, DURATION, or none.",
    )
    parser.add_argument(
        "--header",
        default="",
        help="Header text for the overview plot. Defaults to the input GPX base name.",
    )
    parser.add_argument(
        "--esri",
        action="store_true",
        help="Use ESRI World Street basemap for plotting instead of the default OpenStreetMap style.",
    )
    parser.add_argument(
        "--map-provider",
        choices=("osm", "geoapify", "thunderforest", "stadia", "esri", "custom"),
        default="osm",
        help="Basemap provider for plots (default: osm). --esri remains a compatibility alias.",
    )
    parser.add_argument("--custom-map-url", default="", help="Custom tile URL containing {z}, {x}, and {y}.")
    parser.add_argument("--custom-map-attribution", default="", help="Attribution for a custom tile provider.")
    parser.add_argument("--map-credential-id", default="default", help="macOS Keychain account name for the provider API key.")
    parser.add_argument("--maximum-map-zoom", type=int, default=19, help="Maximum zoom supported by the provider.")
    parser.add_argument("--map-request-timeout-seconds", type=float, default=12.0, help="Timeout for one tile request.")
    parser.add_argument(
        "--Zoom",
        "--zoom",
        dest="zoom",
        type=int,
        default=8,
        help="OSM basemap zoom level for --plot (default: 8).",
    )
    parser.add_argument(
        "--Size",
        "--size",
        dest="size",
        type=parse_image_size,
        default=DEFAULT_IMAGE_SIZE,
        help="PNG size for plots as WIDTHxHEIGHT in pixels (default: 1600x1200).",
    )
    parser.add_argument(
        "--fontsize",
        dest="fontsize",
        type=parse_font_factor,
        default=1.0,
        help="Multiply the automatically chosen plot label font size by this factor (default: 1.0).",
    )
    parser.add_argument(
        "--line-width",
        type=parse_positive_float,
        default=1.5,
        help="Track line width for plots (default: 1.5).",
    )
    parser.add_argument(
        "--line-color",
        type=parse_color,
        default="blue",
        help="Track line color for plots (default: blue).",
    )
    parser.add_argument(
        "--dot-size",
        type=parse_non_negative_float,
        default=4.0,
        help="Track endpoint dot size for plots; use 0 to hide dots (default: 4.0).",
    )
    parser.add_argument(
        "--dot-color",
        type=parse_color,
        default="black",
        help="Track endpoint dot color for plots (default: black).",
    )
    parser.add_argument(
        "--background-color",
        type=parse_color,
        default="black",
        help="Background color for plot images (default: black).",
    )
    parser.add_argument(
        "--title-color",
        type=parse_color,
        default="white",
        help="Font color for single-track plot titles (default: white).",
    )
    parser.add_argument(
        "--remove_prefix",
        default="",
        help="Remove this leading prefix from each track name before printing and plotting.",
    )
    parser.add_argument(
        "--output-base",
        default="",
        help="Base name for overview image and PDF output files.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory for all generated files. Defaults to the current directory.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Write the table to a PDF file.",
    )
    parser.add_argument(
        "--nojson",
        action="store_true",
        help="Suppress writing all JSON output files.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print startup parameters, track import details, and created files.",
    )
    parser.add_argument(
        "--gpx-threshold-distance",
        type=parse_non_negative_float,
        default=10.0,
        help="Minimum spacing in meters between kept GPX points after filtering (default: 10).",
    )
    parser.add_argument(
        "--gpx-threshold-accuracy",
        type=parse_non_negative_float,
        default=10.0,
        help="Maximum horizontal point error in meters; zero disables it (default: 10).",
    )
    parser.add_argument(
        "--gpx-horizontal-smoothing-distance",
        type=parse_non_negative_float,
        default=10.0,
        help="Horizontal coordinate smoothing width in meters; zero disables it (default: 10).",
    )
    parser.add_argument(
        "--gpx-elevation-smoothing-distance",
        type=parse_non_negative_float,
        default=50.0,
        help="Elevation smoothing width in meters; zero disables it (default: 50).",
    )
    parser.add_argument(
        "--gpx-maximum-vertical-accuracy",
        type=parse_non_negative_float,
        default=20.0,
        help="Maximum vertical point error in meters; zero disables it (default: 20).",
    )
    parser.add_argument(
        "--gpx-maximum-hdop",
        type=parse_non_negative_float,
        default=20.0,
        help="Maximum horizontal dilution of precision; zero disables it (default: 20).",
    )
    parser.add_argument(
        "--gpx-maximum-vdop",
        type=parse_non_negative_float,
        default=20.0,
        help="Maximum vertical dilution of precision; zero disables it (default: 20).",
    )
    parser.add_argument(
        "--fallback-walking-speed-kmh",
        type=parse_positive_float,
        default=3.5,
        help="Fallback speed for repairing missing point timestamps (default: 3.5 km/h).",
    )
    parser.add_argument(
        "--gpx-running-speed-window-distance",
        type=parse_non_negative_float,
        default=DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
        help=(
            "Centered route-distance window for running speed in meters "
            f"(default: {DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M:g})."
        ),
    )
    parser.add_argument(
        "--gpx-stationary-speed-threshold",
        type=parse_non_negative_float,
        default=DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
        help=(
            "Speeds below this km/h value are stationary for moving averages "
            f"(default: {DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH:g})."
        ),
    )
    parser.add_argument(
        "--map-layout",
        choices=("standard", "time-lapse"),
        default="standard",
        help="Per-track map layout variant (default: standard).",
    )
    parser.add_argument(
        "--track-edge-margin-fraction",
        type=float,
        default=DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
        help="Minimum track margin inside the map axes for time-lapse layouts (default: 0.05).",
    )
    parser.add_argument(
        "--bake-overlays",
        action="store_false",
        dest="background_only",
        default=True,
        help="Bake routes and headers into PNG files instead of drawing them dynamically.",
    )
    return parser


def normalize_runtime_args(args):
    """Normalize in-process options and validate common argument forms."""
    if args.sort_date and args.sort_distance:
        raise ValueError("Use either --sort-date or --sort-distance, not both.")
    if getattr(args, "sort_original", False) and (args.sort_date or args.sort_distance):
        raise ValueError("Use either original track order, --sort-date, or --sort-distance, not multiple modes.")

    if args.zoom < 0:
        raise ValueError("Zoom level must be a non-negative integer.")
    if float(args.gpx_threshold_distance) < 0:
        raise ValueError("gpx_threshold_distance must be non-negative.")
    for name in (
        "gpx_threshold_accuracy",
        "gpx_horizontal_smoothing_distance",
        "gpx_elevation_smoothing_distance",
        "gpx_maximum_vertical_accuracy",
        "gpx_maximum_hdop",
        "gpx_maximum_vdop",
        "gpx_running_speed_window_distance",
        "gpx_stationary_speed_threshold",
    ):
        if float(getattr(args, name, 0.0)) < 0:
            raise ValueError(f"{name} must be non-negative.")
    if float(getattr(args, "fallback_walking_speed_kmh", 3.5)) <= 0:
        raise ValueError("fallback_walking_speed_kmh must be positive.")
    if getattr(args, "maximum_map_zoom", 19) < 0:
        raise ValueError("maximum_map_zoom must be non-negative.")
    if getattr(args, "map_request_timeout_seconds", 12.0) <= 0:
        raise ValueError("map_request_timeout_seconds must be positive.")
    if getattr(args, "map_provider", "osm") == "custom":
        custom_url = str(getattr(args, "custom_map_url", ""))
        if not custom_url.startswith(("http://", "https://")) or not all(
            token in custom_url for token in ("{z}", "{x}", "{y}")
        ):
            raise ValueError("custom_map_url must use HTTP(S) and contain {z}, {x}, and {y}.")
        if not str(getattr(args, "custom_map_attribution", "")).strip():
            raise ValueError("custom_map_attribution is required for a custom provider.")
    if getattr(args, "map_layout", "standard") not in {"standard", "time-lapse"}:
        raise ValueError("map_layout must be 'standard' or 'time-lapse'.")
    margin = float(getattr(args, "track_edge_margin_fraction", DEFAULT_TRACK_EDGE_MARGIN_FRACTION))
    if not 0.0 <= margin < 0.5:
        raise ValueError("track_edge_margin_fraction must be at least 0 and below 0.5.")
    args.track_edge_margin_fraction = margin

    if args.output_base:
        normalized_output_base = os.path.normpath(os.path.expanduser(args.output_base))
        if os.path.basename(normalized_output_base) != normalized_output_base:
            raise ValueError(
                "--output-base must be a base name only; use --output-dir for directories."
            )

    if isinstance(args.print_labels, str):
        args.print_labels = parse_label_text(args.print_labels)
    elif args.print_labels is None:
        args.print_labels = [["LENGTH"], ["TRACKNAME"], ["DATE"]]

    if isinstance(args.plot_tracks, str):
        args.plot_tracks = parse_track_selection(args.plot_tracks)

    if isinstance(args.size, str):
        args.size = parse_image_size(args.size)

    gpx_path = os.path.abspath(os.path.expanduser(str(args.gpx_file)))
    if not os.path.isfile(gpx_path):
        raise FileNotFoundError(f"file not found: {gpx_path}")
    if not gpx_path.lower().endswith(".gpx"):
        raise ValueError("input file must have a .gpx extension.")
    args.gpx_file = gpx_path
    return args


def prepare_run_context(args):
    """Parse the GPX file and compute all output paths without rendering yet."""
    args = normalize_runtime_args(args)

    gpx_path = args.gpx_file
    output_dir, output_base = resolve_output_settings(gpx_path, args.output_dir, args.output_base)
    if getattr(args, "create_output_dir", True):
        os.makedirs(output_dir, exist_ok=True)
    overview_path = os.path.join(output_dir, f"{output_base}.png")
    overview_metadata_path = os.path.join(output_dir, f"{output_base}.json")
    summary_base = f"{output_base}-summary"
    pdf_output_path = os.path.join(output_dir, f"{summary_base}.pdf")
    table_json_path = os.path.join(output_dir, f"{summary_base}.json")
    header = args.header or os.path.splitext(os.path.basename(gpx_path))[0]
    args.header = header

    if args.verbose:
        print_startup_parameters(args, output_dir, output_base, overview_path, pdf_output_path)

    tracks = parse_gpx_file(
        gpx_path,
        args.remove_prefix,
        args.gpx_threshold_distance,
        args.gpx_threshold_accuracy,
        args.verbose,
        args.gpx_horizontal_smoothing_distance,
        args.gpx_elevation_smoothing_distance,
        args.gpx_maximum_vertical_accuracy,
        args.gpx_maximum_hdop,
        args.gpx_maximum_vdop,
        getattr(args, "track_processing_callback", None),
        args.gpx_running_speed_window_distance,
        args.gpx_stationary_speed_threshold,
    )

    tracks, anchor_point, anchor_name = sort_tracks(
        tracks,
        args.sort_date,
        args.sort_distance,
        getattr(args, "sort_original", False),
    )

    for index, track in enumerate(tracks, start=1):
        track["table_number"] = index

    screen_headers = [
        "Nr",
        "",
        "",
        "Track-Name",
        "Erstellungsdatum",
        "Dauer\n(h:mm)",
        "Laenge\n(km)",
        "Kumulativ\n(km)",
        "Abstand\n(km)",
    ]
    pdf_headers = [
        "Nr",
        "",
        "Track-Name",
        "Erstellungsdatum",
        "Dauer\n(h:mm)",
        "Laenge\n(km)",
        "Kumulativ\n(km)",
        "Abstand\n(km)",
    ]
    screen_rows = build_table_rows(tracks, include_original_sequence=True)
    pdf_rows = build_table_rows(tracks, include_original_sequence=False)

    number_width = max(4, len(str(len(tracks))))
    safe_base = sanitize_filename_component(output_base)
    for track in tracks:
        track["track_plot_image_filename"] = (
            f"{track['table_number']:0{number_width}d}_"
            f"{sanitize_filename_component(track['name'])}_"
            f"{safe_base}.png"
        )
        track["track_plot_time_lapse_image_filename"] = time_lapse_track_map_name(
            track["track_plot_image_filename"]
        )
        track["track_data_sidecar"] = (
            f"{safe_base}-trackdata/{track['table_number']:0{number_width}d}.json"
        )

    selected_numbers = []
    if args.plot_tracks:
        selected_numbers = selected_track_numbers(args.plot_tracks, len(tracks))
    track_plot_paths = []
    for number in selected_numbers:
        track = next(track for track in tracks if track["table_number"] == number)
        filename = (
            track["track_plot_time_lapse_image_filename"]
            if args.map_layout == "time-lapse"
            else track["track_plot_image_filename"]
        )
        track_output_path = os.path.join(output_dir, filename)
        metadata_output_path = os.path.splitext(track_output_path)[0] + ".json"
        track_plot_paths.append(
            {
                "track_number": number,
                "track_name": track["name"],
                "output_image": track_output_path,
                "output_metadata": metadata_output_path,
            }
        )

    return {
        "args": args,
        "gpx_path": gpx_path,
        "output_dir": output_dir,
        "output_base": output_base,
        "overview_path": overview_path,
        "overview_metadata_path": overview_metadata_path,
        "summary_base": summary_base,
        "pdf_output_path": pdf_output_path,
        "table_json_path": table_json_path,
        "tracks": tracks,
        "anchor_point": anchor_point,
        "anchor_name": anchor_name,
        "screen_headers": screen_headers,
        "screen_rows": screen_rows,
        "pdf_headers": pdf_headers,
        "pdf_rows": pdf_rows,
        "selected_numbers": selected_numbers,
        "track_plot_paths": track_plot_paths,
    }


def execute_run_context(context, print_table_output=True, write_summary=True):
    """Write tables and plots for a prepared run context."""
    args = context["args"]
    gpx_path = context["gpx_path"]
    tracks = context["tracks"]
    cancel_event = getattr(args, "cancel_event", None)
    render_progress_callback = getattr(args, "render_progress_callback", None)

    def check_render_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("GPX map rendering cancelled")
    if not tracks:
        if print_table_output:
            print("No tracks found.")
        result = dict(context)
        result.update(
            {
                "overview_created": False,
                "overview_metadata_written": False,
                "created_track_plot_paths": [],
            }
        )
        return result

    if args.verbose and args.sort_distance and context["anchor_point"] is not None:
        anchor_point = context["anchor_point"]
        print(f"Ankerpunkt ({anchor_point[0]:.6f},{anchor_point[1]:.6f}) aus Track: {context['anchor_name']}")

    if args.plot_tracks and not context["selected_numbers"]:
        if print_table_output:
            print("Keine gueltigen Track-Nummern fuer Einzelplots ausgewaehlt.")
        result = dict(context)
        result.update(
            {
                "overview_created": False,
                "overview_metadata_written": False,
                "created_track_plot_paths": [],
            }
        )
        return result

    if print_table_output:
        print_table(context["screen_headers"], context["screen_rows"])
    if args.pdf:
        write_table_pdf(
            table_lines(context["pdf_headers"], context["pdf_rows"]),
            context["pdf_output_path"],
            f"GPX Tracks: {os.path.basename(gpx_path)}",
        )
        if args.verbose:
            print(f"PDF gespeichert: {context['pdf_output_path']}")
    if not args.nojson and write_summary:
        write_derived_track_data(context)
        table_summary = build_table_summary_data(
            gpx_path,
            tracks,
            args.fallback_walking_speed_kmh,
        )
        table_summary["gpx_processing"] = tracks[0].get("processing_options", {}) if tracks else {}
        processing_parameters = getattr(args, "adventure_processing_parameters", None)
        if isinstance(processing_parameters, dict):
            table_summary["adventure_processing_parameters"] = processing_parameters
        write_table_data(table_summary, context["table_json_path"])
        if args.verbose:
            print(f"Tabellen-JSON gespeichert: {context['table_json_path']}")

    overview_created = False
    overview_metadata_written = False
    created_track_plot_paths = []

    if args.plot_overview:
        check_render_cancelled()
        if render_progress_callback is not None:
            render_progress_callback(0, max(len(context["track_plot_paths"]), 1), "Overview")
        effective_zoom, actual_font_size, overview_metadata = render_track_plot(
            tracks,
            args.zoom,
            args.size,
            args.fontsize,
            args.esri,
            context["overview_path"],
            args.line_color,
            args.line_width,
            args.dot_color,
            args.dot_size,
            args.background_color,
            args.title_color,
            args.print_labels,
            args.header,
            True,
            map_provider=args.map_provider,
            custom_map_url=args.custom_map_url,
            custom_map_attribution=args.custom_map_attribution,
            maximum_map_zoom=args.maximum_map_zoom,
            map_request_timeout_seconds=args.map_request_timeout_seconds,
            map_credential_id=getattr(args, "map_credential_id", "default"),
            background_only=getattr(args, "background_only", True),
        )
        render_parameters = getattr(args, "adventure_overview_render_parameters", None)
        if not isinstance(render_parameters, dict):
            render_parameters = getattr(args, "adventure_render_parameters", None)
        if isinstance(render_parameters, dict):
            overview_metadata["adventure_render_parameters"] = render_parameters
        overview_metadata.update(
            {
                "source_gpx": os.path.abspath(gpx_path),
                "source_track_fingerprints": [
                    track.get("track_fingerprint")
                    for track in tracks
                ],
                "output_image": context["overview_path"],
                "output_metadata": context["overview_metadata_path"],
                "header": args.header,
                "line_color": args.line_color,
                "line_width": args.line_width,
                "dot_color": args.dot_color,
                "dot_size": args.dot_size,
                "background_color": args.background_color,
                "title_color": args.title_color,
                "gpx_threshold_distance_m": args.gpx_threshold_distance,
                "gpx_threshold_accuracy_m": args.gpx_threshold_accuracy,
                "gpx_processing": tracks[0].get("processing_options", {}) if tracks else {},
                "overview_labels": args.print_labels,
                "tracks": [
                    {
                        "track_number": track["table_number"],
                        "track_name": track["name"],
                        "track_fingerprint": track.get("track_fingerprint"),
                        "track_plot_image_filename": track.get("track_plot_image_filename"),
                        "track_plot_time_lapse_image_filename": track.get("track_plot_time_lapse_image_filename"),
                        "start_time": format_datetime_local_seconds(track["start_time"]),
                        "end_time": format_datetime_local_seconds(track["end_time"]),
                        "start_point": build_coordinate_point(
                            track["first_point"][0] if track["first_point"] is not None else None,
                            track["first_point"][1] if track["first_point"] is not None else None,
                        ),
                        "end_point": build_coordinate_point(
                            track["last_point"][0] if track["last_point"] is not None else None,
                            track["last_point"][1] if track["last_point"] is not None else None,
                        ),
                    }
                    for track in tracks
                ],
            }
        )
        overview_created = True
        if not args.nojson:
            overview_metadata.update(image_origin_metadata(overview_metadata))
            write_plot_metadata(overview_metadata, context["overview_metadata_path"])
            overview_metadata_written = True
        if args.verbose:
            print(f"Verwendeter Plot-Zoom: {effective_zoom}")
            print(f"Verwendete Plot-Schriftgroesse: {actual_font_size:.2f}")
            print(f"Overview gespeichert: {context['overview_path']}")
            if not args.nojson:
                print(f"Overview-Metadaten gespeichert: {context['overview_metadata_path']}")
                print(
                    "Overview Pixelursprung: Ecke "
                    f"{overview_metadata['pixel_origin_corner']}, Koordinate "
                    f"({overview_metadata['pixel_origin_coordinate_wgs84']['lat']:.6f}, "
                    f"{overview_metadata['pixel_origin_coordinate_wgs84']['lon']:.6f})"
                )

    if args.plot_tracks:
        total_plots = len(context["track_plot_paths"])
        for plot_index, plot_info in enumerate(context["track_plot_paths"], start=1):
            check_render_cancelled()
            number = plot_info["track_number"]
            track = next(track for track in tracks if track["table_number"] == number)
            track_output_path = plot_info["output_image"]
            metadata_output_path = plot_info["output_metadata"]
            preserved_endpoint_places = None
            for variant_name in track_map_variant_names(
                Path(track_output_path).name,
                prefer_time_lapse=False,
            ):
                candidate_path = Path(metadata_output_path).with_name(
                    Path(variant_name).with_suffix(".json").name
                )
                if not candidate_path.is_file():
                    continue
                try:
                    existing_metadata = read_plot_metadata(candidate_path)
                except Exception:
                    continue
                if (
                    isinstance(existing_metadata, dict)
                    and existing_metadata.get("track_fingerprint")
                    == track.get("track_fingerprint")
                    and isinstance(
                        existing_metadata.get("track_endpoint_places"),
                        dict,
                    )
                ):
                    preserved_endpoint_places = dict(
                        existing_metadata["track_endpoint_places"]
                    )
                    break
            effective_zoom, actual_font_size, plot_metadata = render_track_plot(
                [track],
                args.zoom,
                args.size,
                args.fontsize,
                args.esri,
                track_output_path,
                args.line_color,
                args.line_width,
                args.dot_color,
                args.dot_size,
                args.background_color,
                args.title_color,
                args.print_labels,
                args.header,
                False,
                args.map_layout,
                args.track_edge_margin_fraction,
                args.map_provider,
                args.custom_map_url,
                args.custom_map_attribution,
                args.maximum_map_zoom,
                args.map_request_timeout_seconds,
                getattr(args, "map_credential_id", "default"),
                background_only=getattr(args, "background_only", True),
            )
            render_parameters = getattr(args, "adventure_render_parameters", None)
            if isinstance(render_parameters, dict):
                plot_metadata["adventure_render_parameters"] = render_parameters
            media_clear_options = plot_metadata.pop("media_clear_box_options", None)
            plot_metadata.update(
                {
                    "source_gpx": os.path.abspath(gpx_path),
                    "output_image": track_output_path,
                    "track_number": number,
                    "track_name": track["name"],
                    "track_fingerprint": track.get("track_fingerprint"),
                    "track_date": format_date_local(track["time"]),
                    "track_start_time": format_datetime_local_seconds(track["start_time"]),
                    "track_length_km": round(track["length_km"], 1),
                    "track_duration": format_duration(track["duration"]),
                    "timing_status": track.get("timing_status", "recorded"),
                    "has_absolute_time": bool(track.get("has_absolute_time")),
                    "line_color": args.line_color,
                    "line_width": args.line_width,
                    "dot_color": args.dot_color,
                    "dot_size": args.dot_size,
                    "background_color": args.background_color,
                    "title_color": args.title_color,
                    "gpx_threshold_distance_m": args.gpx_threshold_distance,
                    "gpx_threshold_accuracy_m": args.gpx_threshold_accuracy,
                    "gpx_processing": track.get("processing_options", {}),
                    "raw_point_count": track.get("raw_point_count", 0),
                    "retained_point_count": track.get("filtered_point_count", 0),
                    "rejection_counts": track.get("rejection_counts", {}),
                    "start_point": build_coordinate_point(
                        track["first_point"][0] if track["first_point"] is not None else None,
                        track["first_point"][1] if track["first_point"] is not None else None,
                    ),
                    "end_point": build_coordinate_point(
                        track["last_point"][0] if track["last_point"] is not None else None,
                        track["last_point"][1] if track["last_point"] is not None else None,
                    ),
                    "track_points": [(point[0], point[1]) for point in track["points"]],
                    "track_segments": [
                        [(point[0], point[1]) for point in segment]
                        for segment in track.get("segments", [])
                    ],
                    "processed_track_segments": [
                        [processed_point_json_record(point) for point in segment]
                        for segment in track.get("segment_records", [])
                    ],
                    "timed_track_points": timed_points_payload(
                        track.get("point_records", []),
                        args.fallback_walking_speed_kmh,
                    ),
                    "running_speed": running_speed_metadata(track),
                }
            )
            if media_clear_options is not None:
                plot_metadata["media_clear_boxes"] = build_media_clear_boxes_metadata(
                    media_clear_options,
                    args.size,
                    args.track_edge_margin_fraction,
                    track.get("track_fingerprint"),
                    DEFAULT_GRID_LONG_AXIS,
                )
            if preserved_endpoint_places is not None:
                plot_metadata["track_endpoint_places"] = (
                    preserved_endpoint_places
                )
            if not args.nojson:
                plot_metadata.update(image_origin_metadata(plot_metadata))
                write_plot_metadata(plot_metadata, metadata_output_path)
            created_track_plot_paths.append(track_output_path)
            if render_progress_callback is not None:
                render_progress_callback(plot_index, max(total_plots, 1), track["name"])
            if args.verbose:
                if args.nojson:
                    print(
                        f"Track-Plot gespeichert: Nr {number}, Zoom {effective_zoom}, "
                        f"Schriftgroesse {actual_font_size:.2f}, Datei {track_output_path}"
                    )
                else:
                    print(
                        f"Track-Plot gespeichert: Nr {number}, Zoom {effective_zoom}, "
                        f"Schriftgroesse {actual_font_size:.2f}, Datei {track_output_path}, "
                        f"Metadaten {metadata_output_path}"
                    )
                    print(
                        "Track Pixelursprung: Ecke "
                        f"{plot_metadata['pixel_origin_corner']}, Koordinate "
                        f"({plot_metadata['pixel_origin_coordinate_wgs84']['lat']:.6f}, "
                        f"{plot_metadata['pixel_origin_coordinate_wgs84']['lon']:.6f})"
                    )
    result = dict(context)
    result.update(
        {
            "overview_created": overview_created,
            "overview_metadata_written": overview_metadata_written,
            "created_track_plot_paths": created_track_plot_paths,
        }
    )
    return result


def execute_map_variants_from_context(
    context,
    *,
    selected_track_numbers,
    plot_overview=False,
    map_layouts=("standard", "time-lapse"),
    render_parameters_by_layout=None,
    progress_callback=None,
):
    """Render paired map variants from one already prepared GPX context."""
    layouts = tuple(
        layout
        for layout in (str(value) for value in map_layouts)
        if layout in {"standard", "time-lapse"}
    )
    layouts = tuple(dict.fromkeys(layouts)) or ("standard", "time-lapse")
    selected = [
        int(number)
        for number in selected_track_numbers
        if any(int(track["table_number"]) == int(number) for track in context["tracks"])
    ]
    base_args = context["args"]
    summary_args = argparse.Namespace(**vars(base_args))
    summary_args.plot_overview = False
    summary_args.plot_tracks = None
    summary_context = dict(context)
    summary_context["args"] = summary_args
    summary_context["selected_numbers"] = []
    summary_context["track_plot_paths"] = []
    execute_run_context(
        summary_context,
        print_table_output=False,
        write_summary=True,
    )

    created_paths = []
    completed = 0
    total = int(bool(plot_overview)) + len(selected) * len(layouts)
    total = max(total, 1)
    if plot_overview:
        overview_args = argparse.Namespace(**vars(base_args))
        overview_args.plot_overview = True
        overview_args.plot_tracks = None
        overview_context = dict(context)
        overview_context["args"] = overview_args
        overview_context["selected_numbers"] = []
        overview_context["track_plot_paths"] = []
        result = execute_run_context(
            overview_context,
            print_table_output=False,
            write_summary=False,
        )
        if result.get("overview_created"):
            created_paths.append(str(context["overview_path"]))
        completed += 1
        if progress_callback is not None:
            progress_callback(completed, total, "Overview", "overview")

    safe_base = sanitize_filename_component(context["output_base"])
    number_width = max(4, len(str(len(context["tracks"]))))
    for track_number in selected:
        track = next(
            track for track in context["tracks"]
            if int(track["table_number"]) == track_number
        )
        canonical_name = (
            f"{track_number:0{number_width}d}_"
            f"{sanitize_filename_component(track['name'])}_"
            f"{safe_base}.png"
        )
        for layout in layouts:
            args = argparse.Namespace(**vars(base_args))
            args.plot_overview = False
            args.plot_tracks = str(track_number)
            args.map_layout = layout
            if isinstance(render_parameters_by_layout, dict):
                parameters = render_parameters_by_layout.get(layout)
                if isinstance(parameters, dict):
                    args.adventure_render_parameters = parameters
            filename = (
                time_lapse_track_map_name(canonical_name)
                if layout == "time-lapse"
                else canonical_name
            )
            output_path = os.path.join(context["output_dir"], filename)
            variant_context = dict(context)
            variant_context["args"] = args
            variant_context["selected_numbers"] = [track_number]
            variant_context["track_plot_paths"] = [
                {
                    "track_number": track_number,
                    "track_name": track["name"],
                    "output_image": output_path,
                    "output_metadata": os.path.splitext(output_path)[0] + ".json",
                }
            ]
            result = execute_run_context(
                variant_context,
                print_table_output=False,
                write_summary=False,
            )
            created_paths.extend(result.get("created_track_plot_paths", []))
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total, track["name"], layout)
    return {
        "created_paths": created_paths,
        "overview_created": bool(plot_overview),
        "completed": completed,
        "total": total,
    }


def namespace_from_options(gpx_file, **overrides):
    """Build a Namespace for direct Python calls with CLI-equivalent defaults."""
    args = argparse.Namespace(
        gpx_file=str(gpx_file),
        sort_date=False,
        sort_distance=False,
        sort_original=False,
        plot_overview=False,
        plot_tracks=None,
        print_labels=[["LENGTH"], ["TRACKNAME"], ["DATE"]],
        header="",
        esri=False,
        map_provider="osm",
        custom_map_url="",
        custom_map_attribution="",
        maximum_map_zoom=19,
        map_request_timeout_seconds=12.0,
        map_credential_id="default",
        zoom=8,
        size=DEFAULT_IMAGE_SIZE,
        fontsize=1.0,
        line_width=1.5,
        line_color="blue",
        dot_size=4.0,
        dot_color="black",
        background_color="black",
        title_color="white",
        remove_prefix="",
        output_base="",
        output_dir="",
        pdf=False,
        nojson=False,
        verbose=False,
        gpx_threshold_distance=10.0,
        gpx_threshold_accuracy=10.0,
        gpx_horizontal_smoothing_distance=10.0,
        gpx_elevation_smoothing_distance=50.0,
        gpx_maximum_vertical_accuracy=20.0,
        gpx_maximum_hdop=20.0,
        gpx_maximum_vdop=20.0,
        gpx_running_speed_window_distance=DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
        gpx_stationary_speed_threshold=DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
        fallback_walking_speed_kmh=3.5,
        map_layout="standard",
        track_edge_margin_fraction=DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
        background_only=True,
        create_output_dir=True,
        adventure_render_parameters=None,
        adventure_overview_render_parameters=None,
        adventure_processing_parameters=None,
        track_processing_callback=None,
        cancel_event=None,
        render_progress_callback=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def prepare_with_options(gpx_file, **overrides):
    """Prepare an in-process run context without rendering files."""
    args = namespace_from_options(gpx_file, **overrides)
    return prepare_run_context(args)


def run_with_options(gpx_file, print_table_output=True, **overrides):
    """Execute the GPX processing pipeline directly from Python."""
    context = prepare_with_options(gpx_file, **overrides)
    return execute_run_context(context, print_table_output=print_table_output)


def _legacy_sidecar_matches_track(metadata, track):
    """Return True only when legacy identity fields unambiguously match a track."""
    try:
        if int(metadata.get("track_number")) != int(track["table_number"]):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    if metadata.get("track_name") != track.get("name"):
        return False
    if metadata.get("track_start_time") != format_datetime_local_seconds(track.get("start_time")):
        return False

    for metadata_key, track_key in (("start_point", "first_point"), ("end_point", "last_point")):
        stored_point = metadata.get(metadata_key)
        current_point = track.get(track_key)
        if not isinstance(stored_point, dict) or current_point is None:
            return False
        try:
            stored_lat = float(stored_point["lat"])
            stored_lon = float(stored_point["lon"])
        except (KeyError, TypeError, ValueError):
            return False
        if abs(stored_lat - float(current_point[0])) > 1e-7:
            return False
        if abs(stored_lon - float(current_point[1])) > 1e-7:
            return False
    return True


def upgrade_timed_track_sidecars(gpx_file, output_dir, **overrides):
    """Upgrade derived timing and speed data without regenerating map images."""
    fallback_walking_speed_kmh = float(overrides.get("fallback_walking_speed_kmh", 3.5))
    context = prepare_with_options(gpx_file, output_dir=str(output_dir), **overrides)
    tracks_by_fingerprint = {
        track.get("track_fingerprint"): track
        for track in context["tracks"]
        if track.get("track_fingerprint")
    }
    report = {"updated": [], "current": [], "skipped": []}
    matched_fingerprints = set()
    output_path = Path(output_dir)
    for metadata_path in sorted(output_path.glob("*.json")):
        try:
            metadata = read_plot_metadata(metadata_path)
        except Exception as exc:
            report["skipped"].append((metadata_path.name, f"unreadable metadata: {exc}"))
            continue
        fingerprint = metadata.get("track_fingerprint")
        if fingerprint:
            track = tracks_by_fingerprint.get(fingerprint)
            if track is None:
                report["skipped"].append((metadata_path.name, "GPX track no longer matches plot metadata"))
                continue
        else:
            legacy_matches = [
                candidate
                for candidate in context["tracks"]
                if _legacy_sidecar_matches_track(metadata, candidate)
            ]
            if not legacy_matches:
                if metadata.get("track_number") is not None:
                    report["skipped"].append((metadata_path.name, "legacy track identity does not match current GPX"))
                continue
            if len(legacy_matches) != 1:
                report["skipped"].append((metadata_path.name, "legacy track identity is ambiguous"))
                continue
            track = legacy_matches[0]
            fingerprint = track.get("track_fingerprint")
        matched_fingerprints.add(fingerprint)
        payload = timed_points_payload(
            track.get("point_records", []),
            fallback_walking_speed_kmh,
        )
        speed_metadata = running_speed_metadata(track)
        if (
            metadata.get("timed_track_points") == payload
            and metadata.get("running_speed") == speed_metadata
            and metadata.get("track_fingerprint") == fingerprint
        ):
            report["current"].append(track["table_number"])
            continue
        metadata["track_fingerprint"] = fingerprint
        metadata["timed_track_points"] = payload
        metadata["running_speed"] = speed_metadata
        write_plot_metadata(metadata, metadata_path)
        report["updated"].append(track["table_number"])
    for fingerprint, track in tracks_by_fingerprint.items():
        if fingerprint not in matched_fingerprints:
            report["skipped"].append((track["table_number"], "matching track-map metadata missing"))
    return report


# AI prompt: "Write the main entrypoint that validates CLI inputs, parses the GPX
# file, computes anchor distances, sorts tracks, prints the table, and optionally
# renders the PNG plot."
def main():
    """Run the CLI."""
    parser = build_argument_parser()
    args = parser.parse_args()
    try:
        context = prepare_run_context(args)
        execute_run_context(context, print_table_output=True)
        return 0
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
