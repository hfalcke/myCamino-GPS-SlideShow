#!/usr/bin/env python3
"""Edit timestamps of selected GPX 1.1 tracks based on a constant speed."""

from __future__ import annotations

import argparse
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
NS = {"gpx": GPX_NAMESPACE}
DEFAULT_VELOCITY_KMH = 3.5

ET.register_namespace("", GPX_NAMESPACE)


# AI prompt: Build the CLI parser for the GPX timestamp editing tool and define
# all required positional and optional arguments.
def build_parser() -> argparse.ArgumentParser:
    """Create and return the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="editGPXTrack",
        description=(
            "Edit timestamps of selected tracks in a GPX 1.1 file by assigning "
            "a new start time and propagating later timestamps from distance and "
            "average velocity."
        ),
    )
    parser.add_argument("input_file", help="Path to the input GPX 1.1 file.")
    parser.add_argument(
        "track_updates",
        nargs="+",
        help=(
            "Pairs of track number and datetime, for example: "
            "1 2026-04-15-08:30 3 2026-04-15T10:00:00Z"
        ),
    )
    parser.add_argument(
        "-velocity",
        "--velocity",
        type=float,
        default=DEFAULT_VELOCITY_KMH,
        help=f"Average travel speed in km/h. Default: {DEFAULT_VELOCITY_KMH}.",
    )
    parser.add_argument(
        "--output",
        help=(
            "Path to the output GPX file. If omitted, '-edited' is added to the "
            "input basename."
        ),
    )
    parser.add_argument(
        "-verbose",
        "--verbose",
        action="store_true",
        help=(
            "Print the changed GPS points as a comma-separated list of "
            "(lon,lat,timestamp) tuples."
        ),
    )
    return parser


# AI prompt: Parse a user-supplied datetime string into a timezone-aware UTC
# datetime, accepting compact local formats, ISO timestamps, and trailing Z.
def parse_datetime_to_utc(value: str) -> datetime:
    """Parse a datetime string and return a timezone-aware UTC datetime."""
    text = value.strip()

    compact_formats = (
        "%Y-%m-%d-%H:%M",
        "%Y-%m-%d-%H:%M:%S",
    )
    for fmt in compact_formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.astimezone(UTC)
        except ValueError:
            continue

    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime '{value}'. Use YYYY-MM-DD-HH:MM or ISO 8601."
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC)


# AI prompt: Convert a UTC datetime into the GPX 1.1 timestamp format with a
# trailing Z suffix.
def format_gpx_time(dt_utc: datetime) -> str:
    """Format a timezone-aware datetime as a GPX timestamp in UTC."""
    return dt_utc.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# AI prompt: Format a datetime for user-facing CLI output in the local system
# timezone while keeping it timezone-aware.
def format_local_display(dt_utc: datetime) -> str:
    """Format a UTC datetime in the local timezone for display."""
    return dt_utc.astimezone().isoformat(timespec="minutes")


# AI prompt: Parse the flat list of alternating track numbers and datetime
# strings into a validated mapping from track index to UTC start datetime.
def parse_track_updates(values: list[str]) -> dict[int, datetime]:
    """Parse alternating track-number and datetime arguments."""
    if len(values) % 2 != 0:
        raise argparse.ArgumentTypeError(
            "Track updates must be provided as pairs: tracknumber datetime."
        )

    updates: dict[int, datetime] = {}
    for index in range(0, len(values), 2):
        raw_track = values[index]
        raw_datetime = values[index + 1]

        try:
            track_number = int(raw_track)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid track number '{raw_track}'. Track numbers must be integers."
            ) from exc

        if track_number < 1:
            raise argparse.ArgumentTypeError(
                f"Invalid track number '{raw_track}'. Track numbers start at 1."
            )
        if track_number in updates:
            raise argparse.ArgumentTypeError(
                f"Track {track_number} was specified more than once."
            )

        updates[track_number] = parse_datetime_to_utc(raw_datetime)

    return updates


# AI prompt: Return the XML namespace-qualified tag name for a GPX element.
def qname(local_name: str) -> str:
    """Build a fully qualified GPX XML tag name."""
    return f"{{{GPX_NAMESPACE}}}{local_name}"


# AI prompt: Iterate over all track point elements of a GPX track in document
# order across every segment.
def iter_track_points(track_element: ET.Element) -> Iterable[ET.Element]:
    """Yield all track points from a track in segment order."""
    for segment in track_element.findall("gpx:trkseg", NS):
        yield from segment.findall("gpx:trkpt", NS)


# AI prompt: Compute the great-circle distance in meters between two latitude /
# longitude pairs using the haversine formula.
def haversine_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Return the haversine distance between two coordinates in meters."""
    earth_radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return earth_radius_m * c


# AI prompt: Ensure a track point contains a time child element and return that
# element so callers can update its timestamp text.
def get_or_create_time_element(track_point: ET.Element) -> ET.Element:
    """Get or create the <time> child element of a track point."""
    time_element = track_point.find("gpx:time", NS)
    if time_element is not None:
        return time_element

    insert_at = len(track_point)
    for child_index, child in enumerate(list(track_point)):
        if child.tag in {qname("ele"), qname("magvar"), qname("geoidheight")}:
            insert_at = child_index + 1
        else:
            break

    time_element = ET.Element(qname("time"))
    track_point.insert(insert_at, time_element)
    return time_element


# AI prompt: Ensure a track element contains a direct time child element and
# return that element so callers can align the track creation time.
def get_or_create_track_time_element(track_element: ET.Element) -> ET.Element:
    """Get or create the direct <time> child element of a track."""
    time_element = track_element.find("gpx:time", NS)
    if time_element is not None:
        return time_element

    insert_at = len(track_element)
    ordered_tags = {
        qname("name"),
        qname("cmt"),
        qname("desc"),
        qname("src"),
        qname("link"),
        qname("number"),
        qname("type"),
    }
    for child_index, child in enumerate(list(track_element)):
        if child.tag in ordered_tags:
            insert_at = child_index + 1
            continue
        if child.tag == qname("extensions"):
            insert_at = child_index
            break

    time_element = ET.Element(qname("time"))
    track_element.insert(insert_at, time_element)
    return time_element


# AI prompt: Update all timestamps in one selected track, starting from the
# provided UTC datetime and propagating forward with distance / speed.
def update_track_timestamps(
    track_element: ET.Element, start_time_utc: datetime, velocity_kmh: float
) -> list[str]:
    """Rewrite timestamps for one track and return changed point descriptions."""
    points = list(iter_track_points(track_element))
    if not points:
        raise ValueError("Selected track contains no track points.")

    seconds_per_meter = 3.6 / velocity_kmh
    current_time = start_time_utc.astimezone(UTC)
    previous_point: ET.Element | None = None
    changed_points: list[str] = []

    for point in points:
        if previous_point is not None:
            distance_m = haversine_distance_meters(
                float(previous_point.attrib["lat"]),
                float(previous_point.attrib["lon"]),
                float(point.attrib["lat"]),
                float(point.attrib["lon"]),
            )
            current_time += timedelta(seconds=distance_m * seconds_per_meter)

        timestamp_text = format_gpx_time(current_time)
        get_or_create_time_element(point).text = timestamp_text
        changed_points.append(
            f"({point.attrib['lon']},{point.attrib['lat']},{timestamp_text})"
        )
        previous_point = point

    return changed_points


# AI prompt: Build the default output path by inserting '-edited' before the
# original file extension.
def build_default_output_path(input_path: Path) -> Path:
    """Return the default output path for an edited GPX file."""
    return input_path.with_name(f"{input_path.stem}-edited{input_path.suffix}")


# AI prompt: Read the display name of a GPX track and fall back to a placeholder
# when the track has no usable name.
def get_track_name(track_element: ET.Element) -> str:
    """Return the track name or a fallback label."""
    name = track_element.findtext("gpx:name", default="", namespaces=NS)
    stripped_name = name.strip()
    return stripped_name if stripped_name else "Unnamed track"


# AI prompt: Load the GPX tree, validate selected tracks, update only the
# requested ones, and write the modified tree to the output file.
def process_gpx_file(
    input_path: Path,
    output_path: Path,
    updates: dict[int, datetime],
    velocity_kmh: float,
    verbose: bool = False,
) -> tuple[list[str], list[str]]:
    """Apply timestamp edits to selected tracks and write the output GPX file."""
    tree = ET.parse(input_path)
    root = tree.getroot()
    tracks = root.findall("gpx:trk", NS)

    if not tracks:
        raise ValueError("The GPX file does not contain any tracks.")

    messages: list[str] = []
    verbose_lines: list[str] = []
    for track_number, start_time_utc in sorted(updates.items()):
        if track_number > len(tracks):
            raise ValueError(
                f"Track {track_number} does not exist. The file contains {len(tracks)} tracks."
            )

        track_element = tracks[track_number - 1]
        track_name = get_track_name(track_element)
        get_or_create_track_time_element(track_element).text = format_gpx_time(
            start_time_utc
        )
        changed_points = update_track_timestamps(
            track_element, start_time_utc, velocity_kmh
        )
        messages.append(
            f"Track {track_number} ({track_name}): updated {len(changed_points)} "
            f"points, start {format_local_display(start_time_utc)}"
        )
        if verbose:
            verbose_lines.append(
                f"Track {track_number} ({track_name}): {', '.join(changed_points)}"
            )

    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return messages, verbose_lines


# AI prompt: Orchestrate argument parsing, validation, GPX processing, user
# feedback, and program exit status for the CLI entrypoint.
def main(argv: list[str] | None = None) -> int:
    """Run the command-line program."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        updates = parse_track_updates(args.track_updates)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    if args.velocity <= 0:
        parser.error("Velocity must be greater than zero.")

    input_path = Path(args.input_file)
    output_path = Path(args.output) if args.output else build_default_output_path(input_path)

    try:
        messages, verbose_lines = process_gpx_file(
            input_path,
            output_path,
            updates,
            args.velocity,
            verbose=args.verbose,
        )
    except (ET.ParseError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote edited GPX to: {output_path}")
    for message in messages:
        print(message)
    for line in verbose_lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
