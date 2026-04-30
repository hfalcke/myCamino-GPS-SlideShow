#!/usr/bin/env python3
"""Join tracks from multiple GPX 1.1 files into a single GPX file."""

from __future__ import annotations

import argparse
import copy
import os
import sys
import xml.etree.ElementTree as ET


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
ET.register_namespace("", GPX_NAMESPACE)


# AI prompt: Build and return the command-line argument parser for the GPX join tool.
def build_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Merge tracks from multiple GPX 1.1 files into one GPX file."
    )
    parser.add_argument(
        "files",
        metavar="FILE",
        nargs="+",
        help="Input GPX 1.1 files to read and merge.",
    )
    parser.add_argument(
        "--output",
        dest="output",
        help="Output GPX filename. Defaults to the first input file with '-joined' added.",
    )
    parser.add_argument(
        "-verbose",
        "--verbose",
        action="store_true",
        help="Print progress information while reading input files.",
    )
    return parser


# AI prompt: Return the XML tag name for a GPX element inside the GPX 1.1 namespace.
def ns_tag(local_name: str) -> str:
    """Create a fully qualified GPX 1.1 tag name."""
    return f"{{{GPX_NAMESPACE}}}{local_name}"


# AI prompt: Parse a GPX file, validate that it looks like GPX 1.1, and return its XML tree root.
def parse_gpx_file(filename: str) -> ET.Element:
    """Parse a GPX file and return the root XML element."""
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


# AI prompt: Extract all track elements from a parsed GPX root and return them as a list.
def extract_tracks(root: ET.Element) -> list[ET.Element]:
    """Collect all GPX track elements from the document root."""
    return list(root.findall(ns_tag("trk")))


# AI prompt: Create a safe output filename by inserting '-joined' before the first input file extension.
def default_output_filename(first_input: str) -> str:
    """Build the default output filename from the first input filename."""
    base, ext = os.path.splitext(first_input)
    if not ext:
        ext = ".gpx"
    return f"{base}-joined{ext}"


# AI prompt: Build a new GPX root element and copy metadata from the first input root when available.
def create_output_root(first_root: ET.Element) -> ET.Element:
    """Create the output GPX root element using the first file as a template."""
    output_root = ET.Element(
        ns_tag("gpx"),
        {
            "version": "1.1",
            "creator": first_root.attrib.get("creator", "gpxjoin.py"),
        },
    )

    for key in ("xsi:schemaLocation",):
        if key in first_root.attrib:
            output_root.set(key, first_root.attrib[key])

    schema_location_key = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"
    if schema_location_key in first_root.attrib:
        output_root.set(schema_location_key, first_root.attrib[schema_location_key])

    metadata = first_root.find(ns_tag("metadata"))
    if metadata is not None:
        output_root.append(copy.deepcopy(metadata))

    return output_root


# AI prompt: Merge tracks from all input GPX roots into one new GPX root and return it.
def merge_tracks(roots: list[ET.Element]) -> ET.Element:
    """Create a new GPX document containing all tracks from the input roots."""
    output_root = create_output_root(roots[0])
    for root in roots:
        for track in extract_tracks(root):
            output_root.append(copy.deepcopy(track))
    return output_root


# AI prompt: Write the merged GPX XML tree to disk with an XML declaration and UTF-8 encoding.
def write_gpx_file(filename: str, root: ET.Element) -> None:
    """Write the GPX XML document to the specified file."""
    tree = ET.ElementTree(root)
    tree.write(filename, encoding="utf-8", xml_declaration=True)


# AI prompt: Run the command-line program by reading inputs, merging tracks, and writing the output file.
def main(argv: list[str] | None = None) -> int:
    """Run the GPX join command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)

    output_filename = args.output or default_output_filename(args.files[0])
    roots: list[ET.Element] = []

    for filename in args.files:
        root = parse_gpx_file(filename)
        track_count = len(extract_tracks(root))
        if args.verbose:
            print(f"Reading {filename}: {track_count} track(s)")
        roots.append(root)

    merged_root = merge_tracks(roots)
    write_gpx_file(output_filename, merged_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
