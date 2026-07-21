#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare license resources and exact source archives for a DMG build."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile


PROJECT_REPOSITORY = "https://github.com/hfalcke/myCamino-GPS-SlideShow.git"
FFMPEG_VERSION = "8.1.1"
FFMPEG_SHA256 = "b6863adde98898f42602017462871b5f6333e65aec803fdd7a6308639c52edf3"

RUNTIME_DISTRIBUTIONS = (
    "affine",
    "attrs",
    "certifi",
    "charset-normalizer",
    "click",
    "cligj",
    "contextily",
    "contourpy",
    "cycler",
    "fonttools",
    "geographiclib",
    "geopy",
    "idna",
    "joblib",
    "kiwisolver",
    "matplotlib",
    "mercantile",
    "numpy",
    "packaging",
    "pillow",
    "pyobjc-core",
    "pyobjc-framework-AVFoundation",
    "pyobjc-framework-AVKit",
    "pyobjc-framework-Cocoa",
    "pyobjc-framework-CoreAudio",
    "pyobjc-framework-CoreLocation",
    "pyobjc-framework-CoreMedia",
    "pyobjc-framework-Quartz",
    "pyparsing",
    "python-dateutil",
    "rasterio",
    "requests",
    "setuptools",
    "six",
    "urllib3",
    "xyzservices",
)

EXCLUDED_SOURCE_PARTS = {".git", ".venv", "build", "dist", "__pycache__", "node_modules"}


class LicenseBundleError(RuntimeError):
    """Raised when a required distribution document cannot be packaged."""


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _license_files(distribution: metadata.Distribution) -> list[Path]:
    matches: list[Path] = []
    for item in distribution.files or ():
        name = item.name.lower()
        parts = {part.lower() for part in item.parts}
        if (
            name.startswith(("license", "copying", "notice"))
            or "licenses" in parts
        ):
            located = Path(distribution.locate_file(item))
            if located.is_file():
                matches.append(located)
    return sorted(set(matches), key=lambda path: str(path).casefold())


def _metadata_license(distribution: metadata.Distribution) -> str:
    value = distribution.metadata.get("License-Expression")
    if value:
        return value.strip()
    value = distribution.metadata.get("License")
    if value and value.strip() and value.strip().upper() != "UNKNOWN":
        return value.strip()
    return ""


def _project_url(distribution: metadata.Distribution) -> str:
    homepage = distribution.metadata.get("Home-page")
    if homepage:
        return homepage.strip()
    for value in distribution.metadata.get_all("Project-URL") or ():
        if "," in value:
            _label, url = value.split(",", 1)
            return url.strip()
    return ""


def find_python_license() -> Path:
    candidates = [
        Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
    ]
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        candidates.extend((parent / "LICENSE", parent / "LICENSE.txt"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise LicenseBundleError("Python's license file could not be located")


def collect_dependency_licenses(destination: Path) -> list[dict[str, str]]:
    """Copy license texts and return a deterministic build inventory."""
    destination.mkdir(parents=True, exist_ok=True)
    inventory: list[dict[str, str]] = []

    python_dir = destination / "Python"
    python_dir.mkdir()
    shutil.copy2(find_python_license(), python_dir / "LICENSE.txt")
    inventory.append(
        {
            "name": "Python",
            "version": sys.version.split()[0],
            "license": "Python Software Foundation License",
            "url": "https://www.python.org/",
        }
    )

    for distribution_name in RUNTIME_DISTRIBUTIONS:
        try:
            distribution = metadata.distribution(distribution_name)
        except metadata.PackageNotFoundError as exc:
            raise LicenseBundleError(
                f"Required runtime distribution is missing: {distribution_name}"
            ) from exc

        package_dir = destination / distribution_name
        package_dir.mkdir()
        files = _license_files(distribution)
        license_value = _metadata_license(distribution)
        if not files and not license_value:
            raise LicenseBundleError(
                f"No license information found for {distribution_name}"
            )
        used_names: set[str] = set()
        for index, source in enumerate(files, start=1):
            target_name = source.name
            if target_name.casefold() in used_names:
                target_name = f"{index:02d}-{target_name}"
            used_names.add(target_name.casefold())
            shutil.copy2(source, package_dir / target_name)
        if license_value:
            (package_dir / "METADATA-LICENSE.txt").write_text(
                license_value.rstrip() + "\n", encoding="utf-8"
            )
        inventory.append(
            {
                "name": distribution.metadata.get("Name") or distribution_name,
                "version": distribution.version,
                "license": license_value or "See packaged license files",
                "url": _project_url(distribution),
            }
        )
    return inventory


def source_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if any(part in EXCLUDED_SOURCE_PARTS for part in relative.parts):
            continue
        if relative.name == ".DS_Store" or relative == Path("vendor/ffmpeg/ffmpeg"):
            continue
        path = root / relative
        if path.is_file():
            paths.append(relative)
    return sorted(paths, key=lambda item: item.as_posix().casefold())


def create_source_archive(root: Path, destination: Path, revision: str) -> Path:
    short_revision = revision[:12] if revision and revision != "unversioned" else revision
    archive = destination / f"myCamino-source-{short_revision}.tar.gz"
    prefix = Path(f"myCamino-GPS-SlideShow-source-{short_revision}")
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as output:
        for relative in source_files(root):
            output.add(root / relative, arcname=str(prefix / relative), recursive=False)
    return archive


def render_inventory(base_notice: str, inventory: list[dict[str, str]]) -> str:
    lines = [base_notice.rstrip(), "", "## Build Inventory", ""]
    for item in inventory:
        suffix = f"; {item['url']}" if item["url"] else ""
        license_text = " ".join(item["license"].split())
        if len(license_text) > 160:
            license_text = "See packaged license files"
        lines.append(
            f"- {item['name']} {item['version']}: {license_text}{suffix}"
        )
    lines.append("")
    return "\n".join(lines)


def prepare_bundle(root: Path, output: Path, ffmpeg_source: Path) -> dict[str, Path]:
    required = [
        root / "LICENSE",
        root / "COPYRIGHT",
        root / "THIRD_PARTY_NOTICES.md",
        root / "SOURCE_CODE.md",
        root / "vendor" / "ffmpeg" / "COPYING.LGPLv2.1",
        root / "vendor" / "ffmpeg" / "README.txt",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise LicenseBundleError("Missing required project files: " + ", ".join(missing))
    if not ffmpeg_source.is_file():
        raise LicenseBundleError(f"FFmpeg source archive is missing: {ffmpeg_source}")
    if sha256(ffmpeg_source) != FFMPEG_SHA256:
        raise LicenseBundleError("FFmpeg source archive checksum does not match")

    if output.exists():
        shutil.rmtree(output)
    app_root = output / "app_resources" / "licenses"
    project_dir = app_root / "myCamino"
    third_party_dir = app_root / "third-party"
    source_dir = output / "source"
    project_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)

    inventory = collect_dependency_licenses(third_party_dir)
    ffmpeg_dir = third_party_dir / "FFmpeg"
    ffmpeg_dir.mkdir()
    shutil.copy2(root / "vendor" / "ffmpeg" / "COPYING.LGPLv2.1", ffmpeg_dir)
    shutil.copy2(root / "vendor" / "ffmpeg" / "README.txt", ffmpeg_dir)

    shutil.copy2(root / "LICENSE", project_dir / "GPL-3.0.txt")
    shutil.copy2(root / "COPYRIGHT", project_dir / "COPYRIGHT.txt")
    notice = render_inventory(
        (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"), inventory
    )
    (project_dir / "Third-Party Notices.txt").write_text(notice, encoding="utf-8")

    try:
        revision = run_git(root, "rev-parse", "HEAD")
        remote = run_git(root, "remote", "get-url", "origin") or PROJECT_REPOSITORY
        dirty = bool(run_git(root, "status", "--porcelain", "--untracked-files=normal"))
    except (subprocess.CalledProcessError, FileNotFoundError):
        revision = "unversioned"
        remote = PROJECT_REPOSITORY
        dirty = True

    source_archive = create_source_archive(root, source_dir, revision)
    ffmpeg_copy = source_dir / ffmpeg_source.name
    shutil.copy2(ffmpeg_source, ffmpeg_copy)
    build_date = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    source_info = (
        "myCamino GPS SlideShow - Source Code Information\n\n"
        f"Repository: {remote}\n"
        f"Git commit: {revision}\n"
        f"Build date: {build_date}\n"
        f"Uncommitted worktree changes: {'yes' if dirty else 'no'}\n\n"
        f"Application source archive: {source_archive.name}\n"
        f"SHA-256: {sha256(source_archive)}\n\n"
        f"FFmpeg source archive: {ffmpeg_copy.name}\n"
        f"SHA-256: {sha256(ffmpeg_copy)}\n"
        "FFmpeg configuration: see licenses/third-party/FFmpeg/README.txt\n\n"
        "The application source archive contains the exact source files and "
        "assets used by this build. Build products, virtual environments, "
        "caches, and compiled third-party binaries are excluded.\n"
    )
    (project_dir / "Source Code Information.txt").write_text(
        source_info, encoding="utf-8"
    )
    return {
        "app_resources": output / "app_resources",
        "source_archive": source_archive,
        "ffmpeg_source": ffmpeg_copy,
        "source_info": project_dir / "Source Code Information.txt",
        "notice": project_dir / "Third-Party Notices.txt",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("build/license_bundle"))
    parser.add_argument("--ffmpeg-source", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    ffmpeg_source = (
        args.ffmpeg_source
        if args.ffmpeg_source.is_absolute()
        else root / args.ffmpeg_source
    )
    try:
        result = prepare_bundle(root, output, ffmpeg_source)
    except LicenseBundleError as exc:
        parser.error(str(exc))
    print(f"Prepared license bundle: {output}")
    print(f"Application source: {result['source_archive']}")
    print(f"FFmpeg source: {result['ffmpeg_source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
