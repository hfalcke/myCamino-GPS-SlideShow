#!/usr/bin/env python3
"""Format-2 Adventure discovery and transactional file operations."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from json_storage import atomic_write_json


ADVENTURE_FORMAT_VERSION = 2
REQUIRED_KEYS = {
    "adventure_format_version",
    "project_name",
    "project_directory",
    "gpx_file",
    "control_file",
    "track_map_base",
    "parameters",
}


class AdventureFormatError(ValueError):
    """Raised when an Adventure does not use the current explicit format."""


@dataclass(frozen=True)
class AdventureRecord:
    path: Path
    payload: dict
    modified_time: float

    @property
    def project_name(self) -> str:
        return str(self.payload["project_name"])


def filename_base(text: str) -> str:
    cleaned = (text or "").strip().replace("/", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "project"


def safe_map_base(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(".")
    return cleaned or "Unnamed"


def local_reference(project_dir: Path, value: str, suffix: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise AdventureFormatError(f"Missing {suffix} filename")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve(strict=False)
    if path.parent != project_dir.resolve(strict=False):
        raise AdventureFormatError(f"{suffix} file must be inside the project directory: {path}")
    if path.suffix.lower() != suffix.lower():
        raise AdventureFormatError(f"Expected a {suffix} file: {path.name}")
    return path


def validate_adventure_payload(payload: object, adv_path: Path | None = None) -> dict:
    if not isinstance(payload, dict):
        raise AdventureFormatError("Adventure content is not an object")
    missing = sorted(REQUIRED_KEYS - payload.keys())
    if missing:
        raise AdventureFormatError(f"Adventure format 2 fields are missing: {', '.join(missing)}")
    if payload.get("adventure_format_version") != ADVENTURE_FORMAT_VERSION:
        raise AdventureFormatError(
            f"Unsupported Adventure format {payload.get('adventure_format_version')!r}; expected {ADVENTURE_FORMAT_VERSION}"
        )
    project_name = str(payload.get("project_name", "")).strip()
    project_directory = Path(str(payload.get("project_directory", ""))).expanduser().resolve(strict=False)
    track_map_base = str(payload.get("track_map_base", "")).strip()
    if not project_name or not track_map_base or not project_directory.is_absolute():
        raise AdventureFormatError("Adventure name, project directory, and Track Map base must be set")
    if Path(track_map_base).name != track_map_base:
        raise AdventureFormatError("track_map_base must be a filename base, not a path")
    if not isinstance(payload.get("parameters"), dict):
        raise AdventureFormatError("Adventure parameters must be an object")
    if adv_path is not None and project_directory != adv_path.parent.expanduser().resolve(strict=False):
        raise AdventureFormatError("Adventure project_directory does not match the .adv file directory")
    if adv_path is not None and adv_path.stem != filename_base(project_name):
        raise AdventureFormatError("Adventure filename stem must match project_name")
    for field, suffix in (("gpx_file", ".gpx"), ("control_file", ".lst")):
        reference = str(payload.get(field, ""))
        if Path(reference).is_absolute():
            raise AdventureFormatError(f"{field} must be project-relative")
        local_reference(project_directory, reference, suffix)
    return dict(payload)


def load_adventure(path: str | os.PathLike) -> AdventureRecord:
    adv_path = Path(path).expanduser().resolve(strict=False)
    try:
        payload = json.loads(adv_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdventureFormatError(f"Could not read {adv_path.name}: {exc}") from exc
    payload = validate_adventure_payload(payload, adv_path)
    return AdventureRecord(adv_path, payload, adv_path.stat().st_mtime)


def discover_adventures(project_dir: str | os.PathLike) -> tuple[list[AdventureRecord], list[str]]:
    directory = Path(project_dir).expanduser().resolve(strict=False)
    records: list[AdventureRecord] = []
    errors: list[str] = []
    for path in sorted(directory.glob("*.adv"), key=lambda item: item.name.casefold()):
        if path.name.startswith("."):
            continue
        try:
            records.append(load_adventure(path))
        except AdventureFormatError as exc:
            errors.append(str(exc))
    records.sort(key=lambda item: (-item.modified_time, item.path.name.casefold()))
    return records, errors


def project_file_names(project_dir: Path, suffix: str) -> list[str]:
    return [
        path.name
        for path in sorted(project_dir.iterdir(), key=lambda item: item.name.casefold())
        if path.is_file() and not path.name.startswith(".") and path.suffix.casefold() == suffix.casefold()
    ]


def _track_asset_target_name(name: str, old_base: str, new_base: str) -> str | None:
    old_safe = safe_map_base(old_base)
    new_safe = safe_map_base(new_base)
    direct = {
        f"{old_base}.png": f"{new_base}.png",
        f"{old_base}.json": f"{new_base}.json",
        f"{old_base}-summary.json": f"{new_base}-summary.json",
        f"{old_base}-summary.pdf": f"{new_base}-summary.pdf",
    }
    if name in direct:
        return direct[name]
    for extension in (".png", ".json"):
        suffix = f"_{old_safe}{extension}"
        timelapse_suffix = f"_{old_safe}-timelapse{extension}"
        if name.endswith(timelapse_suffix):
            return f"{name[:-len(timelapse_suffix)]}_{new_safe}-timelapse{extension}"
        if name.endswith(suffix):
            return f"{name[:-len(suffix)]}_{new_safe}{extension}"
    return None


def related_track_assets(project_dir: Path, old_base: str, new_base: str) -> dict[Path, Path]:
    track_dir = project_dir / "trackimages"
    result: dict[Path, Path] = {}
    if not track_dir.is_dir():
        return result
    for source in track_dir.iterdir():
        if not source.is_file():
            continue
        target_name = _track_asset_target_name(source.name, old_base, new_base)
        if target_name is not None:
            result[source.resolve(strict=False)] = (track_dir / target_name).resolve(strict=False)
    return result


def _rewrite_json_value(value, replacements: dict[str, str]):
    if isinstance(value, dict):
        return {key: _rewrite_json_value(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_json_value(item, replacements) for item in value]
    if isinstance(value, str):
        replacement = replacements.get(value)
        if replacement is None and Path(value).is_absolute():
            replacement = replacements.get(str(Path(value).expanduser().resolve(strict=False)))
        return replacement if replacement is not None else value
    return value


def _rewrite_control_text(text: str, replacements: dict[str, str]) -> str:
    output = []
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        ending = raw_line[len(content):]
        if content.startswith("#Overviewmap:") or content.startswith("#Map:"):
            key, separator, value = content.partition(":")
            stripped = value.strip()
            replacement = replacements.get(stripped)
            if replacement is None and Path(stripped).is_absolute():
                replacement = replacements.get(str(Path(stripped).expanduser().resolve(strict=False)))
            content = f"{key}{separator} {replacement if replacement is not None else stripped}"
        output.append(content + ending)
    return "".join(output)


def _write_staged_file(source: Path, destination: Path, replacements: dict[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        atomic_write_json(destination, _rewrite_json_value(payload, replacements))
    elif source.suffix.lower() == ".lst":
        destination.write_text(_rewrite_control_text(source.read_text(encoding="utf-8"), replacements), encoding="utf-8")
    else:
        shutil.copy2(source, destination)


def rename_or_copy_adventure(
    adv_path: str | os.PathLike,
    payload: dict,
    new_name: str,
    operation: str,
    include_related: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Path, dict]:
    """Rename/copy an Adventure and optionally its non-media project assets."""
    if operation not in {"rename", "copy"}:
        raise ValueError("operation must be 'rename' or 'copy'")
    source_adv = Path(adv_path).expanduser().resolve(strict=True)
    current = validate_adventure_payload(payload, source_adv)
    project_dir = source_adv.parent
    target_base = filename_base(new_name)
    target_adv = (project_dir / f"{target_base}.adv").resolve(strict=False)
    old_name = str(current["project_name"])
    old_map_base = str(current["track_map_base"])
    source_gpx = local_reference(project_dir, current["gpx_file"], ".gpx")
    source_control = local_reference(project_dir, current["control_file"], ".lst")

    mapping: dict[Path, Path] = {source_adv: target_adv}
    target_payload = dict(current)
    target_payload["project_name"] = target_base
    target_payload["project_directory"] = str(project_dir)
    target_payload["adventure_format_version"] = ADVENTURE_FORMAT_VERSION
    if operation == "copy":
        target_payload["slideshow_resume_position"] = None

    if include_related:
        if operation == "rename":
            shared_labels = []
            for field, value in (
                ("gpx_file", current["gpx_file"]),
                ("control_file", current["control_file"]),
                ("track_map_base", current["track_map_base"]),
            ):
                users = shared_references(project_dir, source_adv, field, str(value))
                if users:
                    shared_labels.append(f"{field}: {', '.join(path.name for path in users)}")
            if shared_labels:
                raise ValueError(
                    "Associated files are shared by other Adventures. Use Copy or disable related-file renaming.\n"
                    + "\n".join(shared_labels)
                )
        target_gpx = (project_dir / f"{target_base}.gpx").resolve(strict=False)
        target_control = (project_dir / f"{target_base}-sorted.lst").resolve(strict=False)
        if source_gpx.exists():
            mapping[source_gpx] = target_gpx
        if source_control.exists():
            mapping[source_control] = target_control
        mapping.update(related_track_assets(project_dir, old_map_base, target_base))
        target_payload["gpx_file"] = target_gpx.name
        target_payload["control_file"] = target_control.name
        target_payload["track_map_base"] = target_base

    unique_mapping = {source: target for source, target in mapping.items() if source != target}
    for source, target in unique_mapping.items():
        if not source.exists():
            raise FileNotFoundError(f"Associated source file does not exist: {source}")
        if target.exists():
            try:
                same_file = source.samefile(target)
            except OSError:
                same_file = False
            if operation != "rename" or not same_file:
                raise FileExistsError(f"Destination already exists: {target}")

    replacements: dict[str, str] = {old_name: target_base}
    for source, target in mapping.items():
        replacements[str(source)] = str(target)
        replacements[source.name] = target.name

    sources_to_remove = list(unique_mapping) if operation == "rename" else []
    total = len(unique_mapping) + 1
    completed = 0
    with tempfile.TemporaryDirectory(prefix=".adventure-transaction-", dir=project_dir) as temporary:
        transaction_dir = Path(temporary)
        staged_dir = transaction_dir / "staged"
        backup_dir = transaction_dir / "backup"
        staged_targets: dict[Path, Path] = {}
        for index, (source, target) in enumerate(unique_mapping.items()):
            staged = staged_dir / f"{index:05d}-{target.name}"
            if source == source_adv:
                atomic_write_json(staged, _rewrite_json_value(target_payload, replacements))
            else:
                _write_staged_file(source, staged, replacements)
            staged_targets[target] = staged
            completed += 1
            if progress:
                progress(completed, total)

        if source_adv == target_adv:
            atomic_write_json(source_adv, target_payload)
            if progress:
                progress(total, total)
            return source_adv, target_payload

        moved_sources: dict[Path, Path] = {}
        committed_targets: list[Path] = []
        try:
            if operation == "rename":
                backup_dir.mkdir(parents=True, exist_ok=True)
                for index, source in enumerate(sources_to_remove):
                    backup = backup_dir / f"{index:05d}-{source.name}"
                    os.replace(source, backup)
                    moved_sources[source] = backup
            for target, staged in staged_targets.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                committed_targets.append(target)
        except Exception:
            for target in reversed(committed_targets):
                target.unlink(missing_ok=True)
            for source, backup in reversed(list(moved_sources.items())):
                if backup.exists():
                    os.replace(backup, source)
            raise
    if progress:
        progress(total, total)
    return target_adv, target_payload


def shared_references(project_dir: Path, active_adv: Path, field: str, value: str) -> list[Path]:
    shared = []
    records, _errors = discover_adventures(project_dir)
    for record in records:
        if record.path == active_adv:
            continue
        if str(record.payload.get(field, "")) == str(value):
            shared.append(record.path)
    return shared
