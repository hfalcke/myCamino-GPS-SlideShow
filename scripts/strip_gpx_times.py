#!/usr/bin/env python3
"""Create an untimed GPX copy for testing, without changing the input.

Usage: python3 scripts/strip_gpx_times.py INPUT.gpx [OUTPUT.gpx]

The default output is INPUT-untimed.gpx beside the input. Existing files are
never overwritten. Removes every <time> element, including metadata, waypoint,
route/track point times and vendor-extension times. Coordinates, elevations,
point order, names and other extensions (including recorded speeds) stay intact.
This is a timing-test utility, not a personal-data anonymizer.
"""

import argparse
from pathlib import Path
from xml.dom import minidom
from xml.parsers.expat import ExpatError


def strip_gpx_times(source: Path, destination: Path) -> int:
    """Write a new untimed file and return the number of removed time elements."""
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output must be different files.")
    with minidom.parse(str(source)) as document:
        root = document.documentElement
        if root.localName != "gpx" or root.namespaceURI not in (
            None,
            "http://www.topografix.com/GPX/1/0",
            "http://www.topografix.com/GPX/1/1",
        ):
            raise ValueError("Input is not a GPX document.")
        # Snapshot the nodes before modifying the tree; match local names so
        # namespaced extension times (e.g. Guru Maps) are removed as well.
        times = [
            node for node in document.getElementsByTagName("*")
            if node.localName == "time"
        ]
        for node in times:
            # Pretty-printed GPX commonly puts a newline and indentation before
            # each element. Remove the indentation belonging to <time> as well,
            # otherwise its deletion leaves a whitespace-only line that fails
            # repository checks. Inline elements and meaningful text/CDATA are
            # deliberately left untouched.
            indentation = node.previousSibling
            if (
                indentation is not None
                and indentation.nodeType == indentation.TEXT_NODE
                and not indentation.data.strip()
            ):
                node.parentNode.removeChild(indentation)
                indentation.unlink()
            node.parentNode.removeChild(node)
            node.unlink()
        data = document.toxml(encoding="UTF-8")
    # Exclusive creation also protects against overwriting existing symlinks.
    with destination.open("xb") as output:
        output.write(data)
        output.write(b"\n")
    return len(times)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Original GPX file (left unchanged)")
    parser.add_argument("output", type=Path, nargs="?", help="New GPX file to create")
    args = parser.parse_args(argv)
    destination = args.output or args.input.with_name(f"{args.input.stem}-untimed.gpx")
    try:
        count = strip_gpx_times(args.input, destination)
    except (OSError, ValueError, ExpatError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Removed {count} time elements. Original unchanged.")
    print(f"Untimed GPX: {destination.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
