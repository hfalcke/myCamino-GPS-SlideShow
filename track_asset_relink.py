# SPDX-License-Identifier: GPL-3.0-or-later
"""Relink numbered track-map assets after a track-order-only change."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from adventure_parameters import normalize_parameter_signature, parameter_signatures_match
from gpx_processing import (
    TRACK_FINGERPRINT_VERSION,
    data_fingerprint_from_segments,
    geometry_fingerprint_from_segments,
)


@dataclass
class TrackAssetRelinkReport:
    relinked: list[tuple[str, str]] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    control_files_updated: list[str] = field(default_factory=list)
    overview_metadata_updated: bool = False


@dataclass
class TrackAssetReconciliationReport:
    reused: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    substantive_parameter_changes: list[str] = field(default_factory=list)
    derived_repaired: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return value


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _layout_for_metadata(path: Path, metadata: dict) -> str:
    layout = str(metadata.get("map_layout") or metadata.get("layout") or "").casefold()
    if "time" in layout or path.stem.casefold().endswith("-timelapse"):
        return "time-lapse"
    return "standard"


def _stored_geometry_segments(metadata: dict) -> list[list[object]]:
    processed = metadata.get("processed_track_segments")
    if isinstance(processed, list) and processed:
        return [segment for segment in processed if isinstance(segment, list)]
    overlay = metadata.get("overlay_geometry")
    if isinstance(overlay, dict):
        segments = overlay.get("segments")
        if isinstance(segments, list) and segments:
            return [segment for segment in segments if isinstance(segment, list)]
    segments = metadata.get("track_segments")
    if isinstance(segments, list):
        return [segment for segment in segments if isinstance(segment, list)]
    return []


def _timed_segments(metadata: dict) -> list[list[dict]]:
    points = metadata.get("timed_track_points")
    if not isinstance(points, list):
        return []
    grouped: dict[int, list[dict]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        try:
            segment_index = int(point.get("segment_index", 0) or 0)
        except (TypeError, ValueError):
            segment_index = 0
        grouped.setdefault(segment_index, []).append(point)
    return [grouped[index] for index in sorted(grouped)]


def _apply_track_identity(
    payload: dict,
    track: dict,
    source_gpx: str,
    *,
    data_fingerprint: str | None = None,
) -> dict:
    updated = dict(payload)
    stored_data_fingerprint = (
        data_fingerprint
        or track.get("track_data_fingerprint")
        or track.get("track_fingerprint")
    )
    updated.update(
        {
            "track_number": track.get("table_number"),
            "track_name": track.get("name", ""),
            "track_fingerprint": stored_data_fingerprint,
            "track_fingerprint_version": TRACK_FINGERPRINT_VERSION,
            "track_geometry_fingerprint": track.get("track_geometry_fingerprint"),
            "track_data_fingerprint": stored_data_fingerprint,
            "source_gpx": source_gpx,
        }
    )
    clear_boxes = updated.get("media_clear_boxes")
    if isinstance(clear_boxes, dict):
        clear_boxes = dict(clear_boxes)
        clear_boxes["track_fingerprint"] = stored_data_fingerprint
        updated["media_clear_boxes"] = clear_boxes
    return updated


def reconcile_legacy_track_sidecars(
    context: dict,
    *,
    render_parameters_by_layout: dict[str, dict] | None = None,
) -> TrackAssetReconciliationReport:
    """Upgrade uniquely geometry-matching sidecars without touching map images."""
    report = TrackAssetReconciliationReport()
    output_dir = Path(context["output_dir"]).resolve(strict=False)
    source_gpx = str(Path(context["args"].gpx_file).resolve(strict=False))
    tracks_by_geometry: dict[str, list[dict]] = {}
    for track in context.get("tracks", []):
        fingerprint = str(track.get("track_geometry_fingerprint") or "")
        if fingerprint:
            tracks_by_geometry.setdefault(fingerprint, []).append(track)

    updates: dict[Path, dict] = {}
    for metadata_path in sorted(output_dir.glob("[0-9]*.json")):
        image_path = metadata_path.with_suffix(".png")
        if not image_path.is_file():
            continue
        try:
            payload = _read_json(metadata_path)
        except (OSError, ValueError, json.JSONDecodeError):
            report.unmatched.append(metadata_path.name)
            continue
        stored_geometry = str(payload.get("track_geometry_fingerprint") or "")
        if not stored_geometry:
            segments = _stored_geometry_segments(payload)
            if segments:
                stored_geometry = geometry_fingerprint_from_segments(segments)
        matches = tracks_by_geometry.get(stored_geometry, [])
        if not matches:
            report.unmatched.append(metadata_path.name)
            continue
        if len(matches) != 1:
            report.ambiguous.append(metadata_path.name)
            continue
        track = matches[0]
        stored_segments = _stored_geometry_segments(payload)
        stored_data_fingerprint = (
            data_fingerprint_from_segments(stored_segments)
            if stored_segments
            else None
        )
        updated = _apply_track_identity(
            payload,
            track,
            source_gpx,
            data_fingerprint=stored_data_fingerprint,
        )
        updated["output_image"] = str(image_path.resolve(strict=False))
        updated["output_metadata"] = str(metadata_path.resolve(strict=False))
        layout = _layout_for_metadata(metadata_path, payload)
        expected = (render_parameters_by_layout or {}).get(layout)
        saved = payload.get("adventure_render_parameters")
        if isinstance(expected, dict):
            if parameter_signatures_match(saved, expected):
                updated["adventure_render_parameters"] = normalize_parameter_signature(expected)
                report.reused.append(metadata_path.name)
            else:
                report.substantive_parameter_changes.append(metadata_path.name)
        if updated == payload:
            report.current.append(metadata_path.name)
        else:
            updates[metadata_path] = updated
            report.repaired.append(metadata_path.name)

    derived_root = output_dir / f"{context.get('output_base', '')}-trackdata"
    if derived_root.is_dir():
        for metadata_path in sorted(derived_root.glob("*.json")):
            try:
                payload = _read_json(metadata_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            segments = _timed_segments(payload)
            if not segments:
                continue
            geometry = geometry_fingerprint_from_segments(segments)
            matches = tracks_by_geometry.get(geometry, [])
            if len(matches) != 1:
                continue
            track = matches[0]
            stored_data_fingerprint = data_fingerprint_from_segments(segments)
            if stored_data_fingerprint != str(track.get("track_data_fingerprint") or ""):
                continue
            updated = _apply_track_identity(
                payload,
                track,
                source_gpx,
                data_fingerprint=stored_data_fingerprint,
            )
            if updated != payload:
                updates[metadata_path] = updated
                report.derived_repaired.append(metadata_path.name)

    for path, payload in updates.items():
        _write_json_atomic(path, payload)
    return report


def _rewrite_control_text(text: str, filename_mapping: dict[str, str]) -> str:
    pattern = re.compile(
        r"^(?P<prefix>\s*(?:#\s+)?#(?:Overviewmap|Map|MapBefore|MapAfter)\s*:\s*)"
        r"(?P<value>.*?)(?P<suffix>\s*)$",
        re.IGNORECASE,
    )
    output = []
    for line in text.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if ending else line
        if content.endswith("\r"):
            content = content[:-1]
            ending = "\r" + ending
        match = pattern.match(content)
        if match is None:
            output.append(line)
            continue
        value = match.group("value").strip()
        replacement = filename_mapping.get(Path(value).name)
        if replacement is None:
            output.append(line)
            continue
        parent = str(Path(value).parent)
        new_value = replacement if parent in {"", "."} else str(Path(parent) / replacement)
        output.append(
            f"{match.group('prefix')}{new_value}{match.group('suffix')}{ending}"
        )
    return "".join(output)


def _expected_assets(context: dict) -> list[dict]:
    output_dir = Path(context["output_dir"]).resolve(strict=False)
    expected = []
    for track in context.get("tracks", []):
        fingerprint = str(
            track.get("track_geometry_fingerprint")
            or track.get("track_fingerprint")
            or ""
        ).strip()
        if not fingerprint:
            continue
        for layout, key in (
            ("standard", "track_plot_image_filename"),
            ("time-lapse", "track_plot_time_lapse_image_filename"),
        ):
            filename = str(track.get(key) or "").strip()
            if filename:
                expected.append(
                    {
                        "fingerprint": fingerprint,
                        "layout": layout,
                        "track": track,
                        "image": output_dir / filename,
                        "metadata": (output_dir / filename).with_suffix(".json"),
                    }
                )
    return expected


def relink_numbered_track_assets(
    context: dict,
    *,
    project_dir: Path | None = None,
    control_files: list[Path] | None = None,
) -> TrackAssetRelinkReport:
    """Relink fingerprint-matching map pairs without rendering map images."""
    report = TrackAssetRelinkReport()
    output_dir = Path(context["output_dir"]).resolve(strict=False)
    candidates: dict[tuple[str, str], list[tuple[Path, Path, dict]]] = {}
    for metadata_path in sorted(output_dir.glob("[0-9]*.json")):
        image_path = metadata_path.with_suffix(".png")
        if not image_path.is_file():
            continue
        try:
            metadata = _read_json(metadata_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        fingerprint = str(
            metadata.get("track_geometry_fingerprint")
            or metadata.get("track_fingerprint")
            or ""
        ).strip()
        if not fingerprint:
            continue
        key = (fingerprint, _layout_for_metadata(metadata_path, metadata))
        candidates.setdefault(key, []).append((image_path, metadata_path, metadata))

    operations = []
    for item in _expected_assets(context):
        key = (item["fingerprint"], item["layout"])
        matches = candidates.get(key, [])
        exact = [match for match in matches if match[0] == item["image"]]
        if exact:
            source = exact[0]
            report.current.append(item["image"].name)
        elif len(matches) == 1:
            source = matches[0]
        else:
            reason = "missing" if not matches else "ambiguous"
            report.skipped.append(
                f"{item['image'].name}: {reason} fingerprint match"
            )
            continue
        operations.append({**item, "source_image": source[0], "source_metadata": source[1], "payload": source[2]})

    changing = [
        item
        for item in operations
        if item["source_image"] != item["image"]
        or item["source_metadata"] != item["metadata"]
    ]
    source_paths = {
        path
        for item in changing
        for path in (item["source_image"], item["source_metadata"])
    }
    for item in changing:
        for destination in (item["image"], item["metadata"]):
            if destination.exists() and destination not in source_paths:
                report.skipped.append(
                    f"{destination.name}: destination belongs to another asset"
                )
                return report

    filename_mapping = {
        item["source_image"].name: item["image"].name for item in changing
    }
    if control_files is None:
        root = Path(project_dir).resolve(strict=False) if project_dir else output_dir.parent
        control_files = sorted(root.glob("*.lst"))
    control_originals = {}
    control_updates = {}
    for path in control_files:
        path = Path(path).resolve(strict=False)
        if not path.is_file():
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue
        updated = _rewrite_control_text(original, filename_mapping)
        if updated != original:
            control_originals[path] = original
            control_updates[path] = updated

    overview_metadata_path = Path(
        context.get("overview_metadata_path")
        or Path(context.get("overview_path", "")).with_suffix(".json")
    ).resolve(strict=False)
    overview_original = None
    overview_updated = None
    if overview_metadata_path.is_file():
        try:
            overview_original = _read_json(overview_metadata_path)
            old_overview_tracks = [
                item
                for item in overview_original.get("tracks", [])
                if isinstance(item, dict)
            ]
            old_geometry = [
                str(
                    item.get("track_geometry_fingerprint")
                    or item.get("track_fingerprint")
                    or ""
                )
                for item in old_overview_tracks
            ]
            current_geometry = [
                str(
                    track.get("track_geometry_fingerprint")
                    or track.get("track_fingerprint")
                    or ""
                )
                for track in context.get("tracks", [])
            ]
            # Relinking may modernize an overview only when its complete route
            # set is unchanged. Added or removed tracks require a redraw.
            if not old_geometry or sorted(old_geometry) != sorted(current_geometry):
                overview_updated = None
                raise StopIteration
            overview_updated = dict(overview_original)
            overview_updated["source_track_geometry_fingerprints"] = current_geometry
            overview_updated["source_track_fingerprints"] = [
                str(
                    track.get("track_data_fingerprint")
                    or track.get("track_fingerprint")
                    or ""
                )
                for track in context.get("tracks", [])
            ]
            old_tracks = {
                str(
                    item.get("track_geometry_fingerprint")
                    or item.get("track_fingerprint")
                    or ""
                ): item
                for item in old_overview_tracks
            }
            overview_tracks = []
            for track in context.get("tracks", []):
                geometry_fingerprint = str(
                    track.get("track_geometry_fingerprint")
                    or track.get("track_fingerprint")
                    or ""
                )
                entry = dict(old_tracks.get(geometry_fingerprint, {}))
                entry.update(
                    {
                        "track_number": track.get("table_number"),
                        "track_name": track.get("name", ""),
                        "track_fingerprint": track.get("track_data_fingerprint")
                        or track.get("track_fingerprint"),
                        "track_fingerprint_version": track.get(
                            "track_fingerprint_version", TRACK_FINGERPRINT_VERSION
                        ),
                        "track_geometry_fingerprint": geometry_fingerprint,
                        "track_data_fingerprint": track.get("track_data_fingerprint"),
                        "track_plot_image_filename": track.get("track_plot_image_filename"),
                        "track_plot_time_lapse_image_filename": track.get("track_plot_time_lapse_image_filename"),
                    }
                )
                overview_tracks.append(entry)
            overview_updated["tracks"] = overview_tracks
        except StopIteration:
            overview_original = None
            overview_updated = None
        except (OSError, ValueError, json.JSONDecodeError):
            overview_original = None
            overview_updated = None

    affected_paths = {
        path
        for item in changing
        for path in (
            item["source_image"],
            item["source_metadata"],
            item["image"],
            item["metadata"],
        )
    }
    original_existence = {path: path.exists() for path in affected_paths}
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with tempfile.TemporaryDirectory(prefix=".track-relink-", dir=output_dir) as temporary_name:
        temporary_dir = Path(temporary_name)
        backups = {}
        for index, path in enumerate(sorted(affected_paths, key=str)):
            if not path.exists():
                continue
            backup = temporary_dir / f"backup-{index}{path.suffix}"
            _link_or_copy(path, backup)
            backups[path] = backup
        try:
            for index, item in enumerate(changing):
                staged_image = temporary_dir / f"install-{index}.png"
                _link_or_copy(backups[item["source_image"]], staged_image)
                os.replace(staged_image, item["image"])
                payload = dict(item["payload"])
                payload.update(
                    {
                        "track_number": item["track"].get("table_number"),
                        "track_name": item["track"].get("name", ""),
                        "track_fingerprint": payload.get("track_data_fingerprint")
                        or payload.get("track_fingerprint")
                        or item["track"].get("track_data_fingerprint")
                        or item["track"].get("track_fingerprint"),
                        "track_fingerprint_version": item["track"].get(
                            "track_fingerprint_version", TRACK_FINGERPRINT_VERSION
                        ),
                        "track_geometry_fingerprint": item["track"].get(
                            "track_geometry_fingerprint"
                        ),
                        "track_data_fingerprint": payload.get(
                            "track_data_fingerprint"
                        )
                        or item["track"].get("track_data_fingerprint"),
                        "output_image": str(item["image"]),
                        "output_metadata": str(item["metadata"]),
                    }
                )
                _write_json_atomic(item["metadata"], payload)
                report.relinked.append(
                    (item["source_image"].name, item["image"].name)
                )
            for path, updated in control_updates.items():
                backup_dir = path.parent / ".mycamino-control-backups" / timestamp
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_dir / path.name)
                _write_text_atomic(path, updated)
                report.control_files_updated.append(path.name)
            if overview_updated is not None and overview_updated != overview_original:
                _write_json_atomic(overview_metadata_path, overview_updated)
                report.overview_metadata_updated = True
        except Exception:
            for path in affected_paths:
                backup = backups.get(path)
                if backup is not None:
                    restore = temporary_dir / f"restore-{len(backups)}-{path.name}"
                    _link_or_copy(backup, restore)
                    os.replace(restore, path)
                elif path.exists() and not original_existence[path]:
                    path.unlink()
            for path, original in control_originals.items():
                _write_text_atomic(path, original)
            if overview_original is not None:
                _write_json_atomic(overview_metadata_path, overview_original)
            raise

    referenced_names = set()
    for path in control_files:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        referenced_names.update(
            name for name in filename_mapping if name in text
        )
    destination_paths = {
        path for item in changing for path in (item["image"], item["metadata"])
    }
    for item in changing:
        if item["source_image"].name in referenced_names:
            continue
        for old_path in (item["source_image"], item["source_metadata"]):
            if old_path not in destination_paths:
                try:
                    old_path.unlink()
                except OSError:
                    pass
    return report
