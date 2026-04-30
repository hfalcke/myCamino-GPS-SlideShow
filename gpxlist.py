#!/usr/bin/env python3
"""List summary information for tracks stored in GPX 1.1 files."""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo


@dataclass
class TrackPoint:
    """Represent one filtered GPX track point with coordinates and time."""

    latitude: float
    longitude: float
    timestamp: datetime


@dataclass
class TrackSummary:
    """Store the computed summary values for one GPX track."""

    number: int
    name: str
    display_time: str
    duration_text: str
    length_km: float
    point_count: int


# AI prompt: Build and return the command-line argument parser for the GPX listing tool.
def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for the GPX track listing program."""
    parser = argparse.ArgumentParser(
        description="Print a track summary table for GPX 1.1 files."
    )
    parser.add_argument(
        "files",
        metavar="FILE",
        nargs="+",
        help="Input GPX 1.1 filenames to inspect.",
    )
    parser.add_argument(
        "-verbose",
        "--verbose",
        action="store_true",
        help="Print additional progress information while processing files.",
    )
    return parser


# AI prompt: Return the fully qualified GPX 1.1 tag name for a given local element name.
def ns_tag(local_name: str) -> str:
    """Create a namespaced GPX 1.1 tag."""
    return f"{{{GPX_NAMESPACE}}}{local_name}"


# AI prompt: Parse one GPX file, validate that it is GPX 1.1, and return the XML root element.
def parse_gpx_file(filename: str) -> ET.Element:
    """Parse a GPX 1.1 file and return its root XML element."""
    try:
        tree = ET.parse(filename)
    except ET.ParseError as exc:
        raise ValueError(f"{filename}: invalid XML/GPX content: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{filename}: unable to read file: {exc}") from exc

    root = tree.getroot()
    if root.tag != ns_tag("gpx"):
        raise ValueError(f"{filename}: root element is not GPX 1.1")

    version = root.attrib.get("version")
    if version != "1.1":
        raise ValueError(f"{filename}: expected GPX version 1.1, found {version!r}")

    return root


# AI prompt: Parse an ISO-like GPX timestamp, handle trailing Z, and normalize naive values to UTC.
def parse_gpx_datetime(value: str) -> datetime:
    """Parse a GPX timestamp into a timezone-aware ``datetime``."""
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid datetime {value!r}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


# AI prompt: Convert a timezone-aware datetime to the local system timezone for display purposes.
def to_local_time(value: datetime) -> datetime:
    """Convert a datetime to the local timezone used for console output."""
    if LOCAL_TIMEZONE is None:
        return value.astimezone()
    return value.astimezone(LOCAL_TIMEZONE)


# AI prompt: Extract the direct text value of a child element and return None when it is missing or empty.
def child_text(parent: ET.Element, child_name: str) -> str | None:
    """Return stripped child text for the given GPX element name."""
    child = parent.find(ns_tag(child_name))
    if child is None or child.text is None:
        return None

    text = child.text.strip()
    return text or None


# AI prompt: Build a list of filtered track points that have valid coordinates and parseable timestamps.
def extract_filtered_points(track: ET.Element) -> list[TrackPoint]:
    """Collect track points that contain valid latitude, longitude, and time."""
    filtered_points: list[TrackPoint] = []

    for point in track.findall(f".//{ns_tag('trkpt')}"):
        time_text = child_text(point, "time")
        if time_text is None:
            continue

        try:
            latitude = float(point.attrib["lat"])
            longitude = float(point.attrib["lon"])
            timestamp = parse_gpx_datetime(time_text)
        except (KeyError, ValueError):
            continue

        filtered_points.append(
            TrackPoint(latitude=latitude, longitude=longitude, timestamp=timestamp)
        )

    return filtered_points


# AI prompt: Count every track point element in the track, even if some points are later filtered out.
def count_track_points(track: ET.Element) -> int:
    """Count all ``trkpt`` elements contained in a GPX track."""
    return len(track.findall(f".//{ns_tag('trkpt')}"))


# AI prompt: Compute the haversine distance in kilometers between two latitude/longitude coordinates.
def haversine_km(
    latitude1: float, longitude1: float, latitude2: float, longitude2: float
) -> float:
    """Return the great-circle distance between two coordinates in kilometers."""
    earth_radius_km = 6371.0088

    lat1 = math.radians(latitude1)
    lon1 = math.radians(longitude1)
    lat2 = math.radians(latitude2)
    lon2 = math.radians(longitude2)

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_km * c


# AI prompt: Sum the distances between consecutive filtered track points and return the track length in kilometers.
def compute_track_length_km(points: list[TrackPoint]) -> float:
    """Calculate total track length from consecutive filtered track points."""
    total_km = 0.0
    for previous, current in zip(points, points[1:]):
        total_km += haversine_km(
            previous.latitude,
            previous.longitude,
            current.latitude,
            current.longitude,
        )
    return total_km


# AI prompt: Format a datetime value as local time using day-month-year and hour-minute output.
def format_display_time(value: datetime | None) -> str:
    """Format a datetime for display in the local timezone."""
    if value is None:
        return "-"
    return to_local_time(value).strftime("%d.%m.%Y %H:%M")


# AI prompt: Format a time duration as hours and minutes, falling back to a dash when it is unavailable.
def format_duration(value: timedelta | None) -> str:
    """Format a duration as ``hh:mm``."""
    if value is None:
        return "-"

    total_minutes = max(0, int(value.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


# AI prompt: Build a summary object for one track using track metadata and filtered track point timestamps.
def summarize_track(track: ET.Element, number: int) -> TrackSummary:
    """Compute the summary table row for one GPX track."""
    name = child_text(track, "name") or "Unnamed"
    filtered_points = extract_filtered_points(track)
    point_count = count_track_points(track)

    track_time_text = child_text(track, "time")
    track_time: datetime | None = None
    if track_time_text is not None:
        try:
            track_time = parse_gpx_datetime(track_time_text)
        except ValueError:
            track_time = None

    first_point_time = filtered_points[0].timestamp if filtered_points else None
    display_time_source = track_time or first_point_time

    start_time = filtered_points[0].timestamp if filtered_points else None
    end_time = filtered_points[-1].timestamp if filtered_points else None
    duration = end_time - start_time if start_time and end_time else None
    length_km = compute_track_length_km(filtered_points)

    return TrackSummary(
        number=number,
        name=name,
        display_time=format_display_time(display_time_source),
        duration_text=format_duration(duration),
        length_km=length_km,
        point_count=point_count,
    )


# AI prompt: Turn the computed track summaries into aligned plain-text table rows ready for console output.
def format_table(summaries: list[TrackSummary]) -> list[str]:
    """Create a text table for a list of track summaries."""
    headers = ("No", "Name", "Date and time", "Duration", "Length km", "Points")

    rows = [
        (
            str(summary.number),
            summary.name,
            summary.display_time,
            summary.duration_text,
            f"{summary.length_km:.2f}",
            str(summary.point_count),
        )
        for summary in summaries
    ]

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    header_line = "  ".join(
        header.ljust(widths[index]) for index, header in enumerate(headers)
    )
    separator_line = "  ".join("-" * width for width in widths)

    lines = [header_line, separator_line]
    for row in rows:
        lines.append(
            "  ".join(
                value.ljust(widths[index]) if index == 1 else value.rjust(widths[index])
                for index, value in enumerate(row)
            )
        )

    return lines


# AI prompt: Read one GPX file, summarize all of its tracks, and print the required table to the console.
def process_file(filename: str, verbose: bool = False) -> None:
    """Parse one GPX file and print its track summary table."""
    root = parse_gpx_file(filename)
    tracks = root.findall(ns_tag("trk"))

    if verbose:
        print(f"Processing {filename} ({len(tracks)} track(s))", file=sys.stderr)

    print(f"{filename}:")

    summaries = [summarize_track(track, index) for index, track in enumerate(tracks, 1)]

    if not summaries:
        print("No tracks found.")
        return

    for line in format_table(summaries):
        print(line)


# AI prompt: Run the CLI by parsing arguments and processing each input GPX file in sequence.
def main(argv: list[str] | None = None) -> int:
    """Run the GPX track listing command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)

    for index, filename in enumerate(args.files):
        if index > 0:
            print()
        process_file(filename, verbose=args.verbose)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
