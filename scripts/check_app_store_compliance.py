#!/usr/bin/env python3
"""Fail closed when a proposed myCamino Store bundle is incomplete.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import plistlib
import subprocess
from pathlib import Path


class ComplianceError(RuntimeError):
    pass


def validate_info_plist(bundle: Path) -> None:
    path = bundle / "Contents" / "Info.plist"
    if not path.is_file():
        raise ComplianceError("Contents/Info.plist is missing")
    with path.open("rb") as handle:
        info = plistlib.load(handle)
    expected = {
        "CFBundleIdentifier": "org.mycamino",
        "CFBundleDisplayName": "myCamino",
        "LSMinimumSystemVersion": "14.0",
    }
    for key, value in expected.items():
        if str(info.get(key, "")) != value:
            raise ComplianceError(f"{key} is {info.get(key)!r}, expected {value!r}")


def validate_resources(bundle: Path) -> None:
    required = (
        bundle / "Contents" / "Resources" / "PrivacyInfo.xcprivacy",
        bundle / "Contents" / "embedded.provisionprofile",
    )
    missing = [str(path.relative_to(bundle)) for path in required if not path.is_file()]
    if missing:
        raise ComplianceError("missing Store resource(s): " + ", ".join(missing))


def validate_entitlements(bundle: Path) -> None:
    completed = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(bundle)],
        capture_output=True,
        check=False,
    )
    payload = completed.stdout or completed.stderr
    if completed.returncode != 0 or b"com.apple.security.app-sandbox" not in payload:
        raise ComplianceError("the application is not signed with App Sandbox entitlement")


def _mach_o_files(bundle: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        identified = subprocess.run(
            ["file", "-b", str(path)], capture_output=True, check=False, text=True
        )
        if "Mach-O" in identified.stdout:
            candidates.append(path)
    return candidates


def validate_nested_code(bundle: Path) -> None:
    unsigned: list[str] = []
    for path in _mach_o_files(bundle):
        checked = subprocess.run(
            ["codesign", "--verify", "--strict", str(path)],
            capture_output=True,
            check=False,
            text=True,
        )
        if checked.returncode:
            unsigned.append(str(path.relative_to(bundle)))
    if unsigned:
        raise ComplianceError("unsigned nested Mach-O code: " + ", ".join(unsigned[:10]))


def validate_slideshow_helper(bundle: Path) -> None:
    candidates = (
        bundle / "Contents" / "Frameworks" / "GPSTrackShow" / "GPSTrackShow",
        bundle / "Contents" / "Helpers" / "GPSTrackShow" / "GPSTrackShow",
        bundle / "Contents" / "MacOS" / "GPSTrackShow",
    )
    helper = next((path for path in candidates if path.is_file()), None)
    if helper is None:
        raise ComplianceError("the bundled Slide Show helper is missing")
    completed = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(helper)],
        capture_output=True,
        check=False,
    )
    payload = completed.stdout or completed.stderr
    required = (b"com.apple.security.app-sandbox", b"com.apple.security.inherit")
    if completed.returncode != 0 or any(key not in payload for key in required):
        raise ComplianceError("the Slide Show helper lacks sandbox-inherit entitlements")


def validate_bundle(bundle: Path) -> None:
    if not bundle.is_dir() or bundle.suffix != ".app":
        raise ComplianceError(f"not an application bundle: {bundle}")
    validate_info_plist(bundle)
    validate_resources(bundle)
    validate_entitlements(bundle)
    validate_nested_code(bundle)
    validate_slideshow_helper(bundle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        validate_bundle(args.bundle.resolve())
    except ComplianceError as exc:
        parser.error(str(exc))
    print(f"App Store compliance preflight passed: {args.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
