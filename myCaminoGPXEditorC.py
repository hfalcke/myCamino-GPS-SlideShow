#!/usr/bin/env python3
"""Compatibility launcher for the native myCamino GPX Editor.

The editor itself is implemented as an Objective-C++/C++ executable in
``cpp/myCaminoGPXEditor.mm`` and built by ``make``.  Python GUI applications can
continue importing this module and call ``show_gpx_editor`` to launch the
standalone native program.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Iterable


PROGRAM_TITLE = "myCamino GPX Editor"
HERE = Path(__file__).resolve().parent
EXECUTABLE = HERE / "build" / "myCaminoGPXEditor"


def _ensure_executable() -> Path:
    if EXECUTABLE.exists() and os.access(EXECUTABLE, os.X_OK):
        return EXECUTABLE
    raise FileNotFoundError(
        f"{PROGRAM_TITLE} native executable was not found at {EXECUTABLE}.\n"
        "Run `make` in this directory first."
    )


def show_gpx_editor(
    gpx_paths: Iterable[str | os.PathLike[str]] | None = None,
    standalone: bool = False,
    output_file: str | os.PathLike[str] | None = None,
    debug: bool = False,
):
    """Launch the native editor and return the ``subprocess.Popen`` handle.

    ``standalone`` is kept for API compatibility with the former PyObjC module.
    The native C++ editor always runs as a separate Cocoa application process.
    """

    executable = _ensure_executable()
    args = [str(executable)]
    paths = [Path(path).expanduser() for path in (gpx_paths or [])]
    if len(paths) > 1:
        raise ValueError("The command-line native editor accepts one startup GPX file.")
    if paths:
        args.append(str(paths[0]))
    if output_file is not None:
        args.extend(["--output-file", str(Path(output_file).expanduser())])
    if debug:
        args.append("--debug")
    return subprocess.Popen(args)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="GPXEditor.py", description=f"Open the {PROGRAM_TITLE}.")
    parser.add_argument("gpx_file", nargs="?", help="Optional .gpx file to load when the editor starts.")
    parser.add_argument("--output-file", metavar="ofile.gpx", help="Default GPX filename used by Save.")
    parser.add_argument("--debug", action="store_true", help="Print benchmark diagnostics from the native editor.")
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    process = show_gpx_editor(
        [args.gpx_file] if args.gpx_file else None,
        standalone=True,
        output_file=args.output_file,
        debug=args.debug,
    )
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
