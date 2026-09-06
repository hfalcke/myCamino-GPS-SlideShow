#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build or verify the redistributable St-Jean to Roncesvalles demo assets."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageCms, ImageOps, ImageStat


TRACK_NAME = "JW St-Jean-Pied-de-Port - Roncevalles"
MAP_PREFIX = "0090_"
DEMO_PROJECT_NAME = "St Jean to Roncesvalles Demo"
PREPARED_DIRECTORY = "3 - Prepared Adventure"
PHOTO_EXTENSIONS = {".jpg", ".jpeg"}
SRGB_PROFILE = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")


def media_names_for_stage(control_file: Path) -> list[str]:
    names = []
    active = False
    for raw in control_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#Map:"):
            if active:
                break
            active = Path(line.partition(":")[2].strip()).name.startswith(MAP_PREFIX)
            continue
        if not active or not line or line.startswith("#"):
            continue
        name = line.split("|", 1)[0].strip()
        if Path(name).suffix.casefold() in PHOTO_EXTENSIONS:
            names.append(name)
    if not names:
        raise ValueError(f"No photographs found below {MAP_PREFIX} in {control_file}")
    return names


def extract_track(source: Path, destination: Path):
    tree = ET.parse(source)
    root = tree.getroot()
    namespace = root.tag.partition("}")[0].lstrip("{")
    ns = {"gpx": namespace}
    selected = None
    for track in root.findall("gpx:trk", ns):
        if (track.findtext("gpx:name", default="", namespaces=ns) or "").strip() == TRACK_NAME:
            selected = track
            break
    if selected is None:
        raise ValueError(f"Track not found: {TRACK_NAME}")
    ET.register_namespace("", namespace)
    output = ET.Element(root.tag, {"version": "1.1", "creator": "myCamino guided demo"})
    metadata = ET.SubElement(output, f"{{{namespace}}}metadata")
    ET.SubElement(metadata, f"{{{namespace}}}name").text = "St-Jean-Pied-de-Port to Roncesvalles Demo"
    output.append(copy.deepcopy(selected))
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(output).write(destination, encoding="utf-8", xml_declaration=True)


def prepare_photo(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    resized = destination.with_name(f".{destination.name}.tmp.jpeg")
    try:
        with Image.open(source) as opened:
            opened.seek(0)
            image = ImageOps.exif_transpose(opened).convert("RGB")
            target_profile = ImageCms.ImageCmsProfile(str(SRGB_PROFILE))
            source_profile_data = opened.info.get("icc_profile")
            if source_profile_data:
                try:
                    from io import BytesIO

                    source_profile = ImageCms.ImageCmsProfile(BytesIO(source_profile_data))
                    image = ImageCms.profileToProfile(
                        image,
                        source_profile,
                        target_profile,
                        outputMode="RGB",
                    )
                except (OSError, ValueError):
                    # A malformed camera profile must not prevent a usable demo copy.
                    pass
            image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            image.save(
                resized,
                format="JPEG",
                quality=82,
                optimize=True,
                progressive=True,
                icc_profile=target_profile.tobytes(),
            )
        subprocess.run(
            [
                "exiftool", "-overwrite_original", "-all=", "-tagsFromFile", str(source),
                "-DateTimeOriginal", "-CreateDate", "-ModifyDate", "-OffsetTime*",
                "-GPS:all", "-Copyright", "-Artist", "-Creator", "-Orientation#=1",
                f"-ICC_Profile<={SRGB_PROFILE}", str(resized),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "exiftool", "-overwrite_original", "-if", "not $Copyright",
                "-Copyright=Copyright (C) 2024-2026 Heino Falcke, CC BY 4.0",
                str(resized),
            ],
            check=True,
            capture_output=True,
        )
        if not image_has_visible_content(resized):
            raise ValueError(f"Downsampled demo photograph is blank: {source}")
        if not image_orientation_is_normal(resized):
            raise ValueError(f"Downsampled demo photograph has invalid orientation: {source}")
        resized.replace(destination)
    finally:
        resized.unlink(missing_ok=True)


def image_has_visible_content(path: Path) -> bool:
    """Reject decoder failures that produce a uniformly black demo image."""
    with Image.open(path) as image:
        sample = image.convert("RGB")
        sample.thumbnail((128, 128), Image.Resampling.BILINEAR)
        statistics = ImageStat.Stat(sample)
    return max(maximum for _minimum, maximum in statistics.extrema) > 2


def image_orientation_is_normal(path: Path) -> bool:
    """Ensure resized pixels are not rotated again by stale EXIF orientation."""
    with Image.open(path) as image:
        return int(image.getexif().get(274, 1)) == 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _contains_private_path(value) -> bool:
    if isinstance(value, dict):
        return any(_contains_private_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_path(item) for item in value)
    return isinstance(value, str) and (value.startswith("/Users/") or "file:///Users/" in value)


def _demo_map_names() -> tuple[str, str]:
    base = f"0001_{TRACK_NAME}_{DEMO_PROJECT_NAME}"
    return f"{base}.png", f"{base}-timelapse.png"


def prepare_offline_assets(source_project: Path, output: Path):
    """Package current, sanitized metadata and maps for network-free demo use."""
    prepared = output / PREPARED_DIRECTORY
    metadata_directory = prepared / "metadata"
    map_directory = prepared / "trackimages"
    metadata_directory.mkdir(parents=True, exist_ok=True)
    map_directory.mkdir(parents=True, exist_ok=True)

    for photo in sorted((output / "2 - Photographs").glob("*")):
        if not photo.is_file() or photo.suffix.casefold() not in PHOTO_EXTENSIONS:
            continue
        source_sidecar = source_project / f"{photo.name}.json"
        payload = json.loads(source_sidecar.read_text(encoding="utf-8"))
        if not payload.get("place") or not isinstance(payload.get("weather"), dict):
            raise ValueError(f"Source sidecar lacks place or weather: {source_sidecar}")
        retained = {
            key: copy.deepcopy(value)
            for key, value in payload.items()
            if key not in {"photo_path", "source_file_signature", "source_file_identity"}
        }
        stat = photo.stat()
        retained["photo_path"] = photo.name
        retained["source_filename"] = photo.name
        retained["source_file_signature"] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        retained["source_file_identity"] = {
            "version": 1,
            "algorithm": "sha256",
            "size": stat.st_size,
            "sha256": sha256(photo),
        }
        if _contains_private_path(retained):
            raise ValueError(f"Private absolute path remains in {source_sidecar}")
        _write_json(metadata_directory / f"{photo.name}.json", retained)

    source_maps = source_project / "trackimages"
    source_summary_path = source_maps / "Jakobsweg Köln - Santiago-summary.json"
    source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    track = next((item for item in source_summary["tracks"] if item.get("track_name") == TRACK_NAME), None)
    if track is None:
        raise ValueError(f"Track metadata not found: {TRACK_NAME}")
    standard_name, timelapse_name = _demo_map_names()
    for source_name, destination_name in (
        (track["track_plot_image_filename"], standard_name),
        (track["track_plot_time_lapse_image_filename"], timelapse_name),
    ):
        source_png = source_maps / source_name
        source_json = source_png.with_suffix(".json")
        destination_png = map_directory / destination_name
        shutil.copy2(source_png, destination_png)
        map_payload = json.loads(source_json.read_text(encoding="utf-8"))
        map_payload.update(
            output_image=destination_name,
            output_metadata=Path(destination_name).with_suffix(".json").name,
            source_gpx=f"{DEMO_PROJECT_NAME}.gpx",
            track_number=1,
        )
        if _contains_private_path(map_payload):
            raise ValueError(f"Private absolute path remains in {source_json}")
        _write_json(destination_png.with_suffix(".json"), map_payload)

    overview_name = f"{DEMO_PROJECT_NAME}.png"
    shutil.copy2(map_directory / standard_name, map_directory / overview_name)
    overview_payload = json.loads((map_directory / standard_name).with_suffix(".json").read_text(encoding="utf-8"))
    overview_payload.update(
        output_image=overview_name,
        output_metadata=f"{DEMO_PROJECT_NAME}.json",
        source_gpx=f"{DEMO_PROJECT_NAME}.gpx",
        map_layout="overview",
        stage_kind="overview",
    )
    _write_json(map_directory / f"{DEMO_PROJECT_NAME}.json", overview_payload)

    track_data_source = source_maps / track["track_data_sidecar"]
    track_data = json.loads(track_data_source.read_text(encoding="utf-8"))
    track_data["track_number"] = 1
    track_data["original_sequence_number"] = 1
    track_data_relative = f"{DEMO_PROJECT_NAME}-trackdata/0001.json"
    _write_json(map_directory / track_data_relative, track_data)

    demo_track = copy.deepcopy(track)
    demo_track.update(
        nr=1,
        original_sequence_number=1,
        abstand_km=0.0,
        kumulativ_km=demo_track.get("laenge_km"),
        track_plot_image_filename=standard_name,
        track_plot_time_lapse_image_filename=timelapse_name,
        track_data_sidecar=track_data_relative,
    )
    summary = {
        key: copy.deepcopy(value)
        for key, value in source_summary.items()
        if key not in {"tracks", "output_image", "output_metadata", "source_gpx"}
    }
    summary.update(
        source_gpx=f"{DEMO_PROJECT_NAME}.gpx",
        output_image=overview_name,
        output_metadata=f"{DEMO_PROJECT_NAME}.json",
        tracks=[demo_track],
    )
    if _contains_private_path(summary) or _contains_private_path(track_data):
        raise ValueError("Private absolute path remains in prepared demo track data")
    _write_json(map_directory / f"{DEMO_PROJECT_NAME}-summary.json", summary)


def validate_offline_assets(output: Path) -> list[str]:
    prepared = output / PREPARED_DIRECTORY
    metadata = prepared / "metadata"
    maps = prepared / "trackimages"
    failures: list[str] = []
    photos = sorted((output / "2 - Photographs").glob("*.jpeg"))
    for photo in photos:
        sidecar = metadata / f"{photo.name}.json"
        if not sidecar.is_file():
            failures.append(f"Missing prepared sidecar for {photo.name}")
            continue
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        identity = payload.get("source_file_identity", {})
        if identity.get("sha256") != sha256(photo) or identity.get("size") != photo.stat().st_size:
            failures.append(f"Stale prepared identity for {photo.name}")
        if not payload.get("place") or not payload.get("weather"):
            failures.append(f"Missing place or weather for {photo.name}")
        weather = payload.get("weather", {})
        if not weather.get("attribution") or not weather.get("provider"):
            failures.append(f"Missing weather attribution for {photo.name}")
        if _contains_private_path(payload):
            failures.append(f"Private path in prepared sidecar for {photo.name}")
    standard_name, timelapse_name = _demo_map_names()
    required = [
        standard_name,
        timelapse_name,
        Path(standard_name).with_suffix(".json").name,
        Path(timelapse_name).with_suffix(".json").name,
        f"{DEMO_PROJECT_NAME}.png",
        f"{DEMO_PROJECT_NAME}.json",
        f"{DEMO_PROJECT_NAME}-summary.json",
        f"{DEMO_PROJECT_NAME}-trackdata/0001.json",
    ]
    for name in required:
        path = maps / name
        if not path.is_file():
            failures.append(f"Missing prepared map asset {name}")
        elif path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if _contains_private_path(payload):
                failures.append(f"Private path in prepared map asset {name}")
    return failures


def write_manifest(output: Path):
    files = []
    for path in sorted(output.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if (
            not path.is_file()
            or path.name == "manifest.json"
            or any(part.startswith(".") for part in path.relative_to(output).parts)
        ):
            continue
        entry = {
            "path": path.relative_to(output).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        if path.suffix.casefold() in PHOTO_EXTENSIONS:
            with Image.open(path) as image:
                entry["width"], entry["height"] = image.size
            if not image_has_visible_content(path):
                raise ValueError(f"Blank demo image: {entry['path']}")
            if not image_orientation_is_normal(path):
                raise ValueError(f"Invalid demo image orientation: {entry['path']}")
            entry["visible_content"] = True
            entry["orientation"] = 1
        files.append(entry)
    payload = {
        "format_version": 2,
        "title": "St-Jean-Pied-de-Port to Roncesvalles",
        "track_name": TRACK_NAME,
        "image_processing": {"maximum_long_edge": 1920, "jpeg_quality": 82, "color_space": "sRGB"},
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build(source_project: Path, control_file: Path, output: Path):
    track_directory = output / "1 - GPX Track"
    photo_directory = output / "2 - Photographs"
    extract_track(source_project / "Jakobsweg Köln - Santiago.gpx", track_directory / "St-Jean-Pied-de-Port-to-Roncesvalles.gpx")
    for name in media_names_for_stage(control_file):
        prepare_photo(source_project / name, photo_directory / name)
    write_manifest(output)


def verify(output: Path) -> int:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest.get("format_version", 0)) != 2:
        print("Unsupported demo manifest version")
        return 1
    failures = []
    for item in manifest.get("files", []):
        path = output / item["path"]
        if not path.is_file():
            failures.append(f"Missing {item['path']}")
        elif sha256(path) != item.get("sha256"):
            failures.append(f"Checksum mismatch for {item['path']}")
        elif path.suffix.casefold() in PHOTO_EXTENSIONS and not image_has_visible_content(path):
            failures.append(f"Blank demo image: {item['path']}")
        elif path.suffix.casefold() in PHOTO_EXTENSIONS and not image_orientation_is_normal(path):
            failures.append(f"Invalid demo image orientation: {item['path']}")
    failures.extend(validate_offline_assets(output))
    if failures:
        print("\n".join(failures))
        return 1
    oversized = [item["path"] for item in manifest["files"] if max(item.get("width", 0), item.get("height", 0)) > 1920]
    if oversized:
        print("Oversized demo images: " + ", ".join(oversized))
        return 1
    print(f"Verified {len(manifest['files'])} demo files.")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-project", type=Path)
    parser.add_argument("--control-file", type=Path)
    parser.add_argument("--offline-assets-source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("demo/St-Jean-Pied-de-Port-to-Roncesvalles"))
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="rewrite the manifest for the demo files currently present",
    )
    args = parser.parse_args()
    if args.refresh_manifest:
        if args.offline_assets_source:
            prepare_offline_assets(args.offline_assets_source, args.output)
        write_manifest(args.output)
        return verify(args.output)
    if args.verify:
        return verify(args.output)
    if args.source_project is None or args.control_file is None:
        parser.error("--source-project and --control-file are required when building")
    build(args.source_project, args.control_file, args.output)
    return verify(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
