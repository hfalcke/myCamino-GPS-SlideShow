#!/usr/bin/env python3
"""List summary information for tracks stored in GPX 1.1 files."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

from gpx_processing import (
    ProcessingOptions,
    haversine_km,
    parse_time,
    process_track_element,
)
from gpx_import import load_gpx_document


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
    parser.add_argument("--horizontal-smoothing", type=float, default=10.0, metavar="M")
    parser.add_argument("--point-spacing", type=float, default=10.0, metavar="M")
    parser.add_argument("--elevation-smoothing", type=float, default=50.0, metavar="M")
    parser.add_argument("--maximum-horizontal-error", type=float, default=10.0, metavar="M")
    parser.add_argument("--maximum-vertical-error", type=float, default=20.0, metavar="M")
    parser.add_argument("--maximum-hdop", type=float, default=20.0)
    parser.add_argument("--maximum-vdop", type=float, default=20.0)
    return parser


# AI prompt: Return the fully qualified GPX 1.1 tag name for a given local element name.
def ns_tag(local_name: str) -> str:
    """Create a namespaced GPX 1.1 tag."""
    return f"{{{GPX_NAMESPACE}}}{local_name}"


# AI prompt: Parse one GPX file, validate that it is GPX 1.1, and return the XML root element.
def parse_gpx_file(filename: str) -> ET.Element:
    """Parse supported GPX variants and return a canonical GPX 1.1 root."""
    document = load_gpx_document(filename)
    root = ET.Element(
        ns_tag("gpx"),
        {"version": "1.1", "creator": "myCamino gpxlist"},
    )
    for track in document.tracks:
        root.append(track)
    return root


# AI prompt: Parse an ISO-like GPX timestamp, handle trailing Z, and normalize naive values to UTC.
def parse_gpx_datetime(value: str) -> datetime:
    """Parse a GPX timestamp into a timezone-aware ``datetime``."""
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError(f"invalid datetime {value!r}")
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
def summarize_track(
    track: ET.Element,
    number: int,
    processing_options: ProcessingOptions | None = None,
) -> TrackSummary:
    """Compute the summary table row for one GPX track."""
    name = child_text(track, "name") or "Unnamed"
    processed = process_track_element(track, processing_options or ProcessingOptions())
    point_count = processed.retained_point_count

    track_time_text = child_text(track, "time")
    track_time: datetime | None = None
    if track_time_text is not None:
        try:
            track_time = parse_gpx_datetime(track_time_text)
        except ValueError:
            track_time = None

    first_point_time = processed.start_time
    display_time_source = track_time or first_point_time

    duration = processed.duration
    length_km = processed.length_km

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
def process_file(
    filename: str,
    verbose: bool = False,
    processing_options: ProcessingOptions | None = None,
) -> None:
    """Parse one GPX file and print its track summary table."""
    root = parse_gpx_file(filename)
    tracks = root.findall(ns_tag("trk"))

    if verbose:
        print(f"Processing {filename} ({len(tracks)} track(s))", file=sys.stderr)

    print(f"{filename}:")

    summaries = [
        summarize_track(track, index, processing_options)
        for index, track in enumerate(tracks, 1)
    ]

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

    values = [
        args.horizontal_smoothing,
        args.point_spacing,
        args.elevation_smoothing,
        args.maximum_horizontal_error,
        args.maximum_vertical_error,
        args.maximum_hdop,
        args.maximum_vdop,
    ]
    if any(value < 0 for value in values):
        parser.error("GPX processing values must be zero or positive")
    processing_options = ProcessingOptions(
        horizontal_smoothing_distance_m=args.horizontal_smoothing,
        minimum_point_spacing_m=args.point_spacing,
        elevation_smoothing_distance_m=args.elevation_smoothing,
        maximum_horizontal_accuracy_m=args.maximum_horizontal_error,
        maximum_vertical_accuracy_m=args.maximum_vertical_error,
        maximum_hdop=args.maximum_hdop,
        maximum_vdop=args.maximum_vdop,
    )

    for index, filename in enumerate(args.files):
        if index > 0:
            print()
        process_file(filename, verbose=args.verbose, processing_options=processing_options)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
