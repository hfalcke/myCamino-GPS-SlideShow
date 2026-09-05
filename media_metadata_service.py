"""Reusable sidecar-backed media preparation for GUI workflows.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from plot_metadata_utils import (
    media_sidecar_freshness,
    media_sidecar_path,
    patch_media_sidecar_signature,
    validate_media_sidecar,
)


@dataclass
class PreparedMedia:
    path: Path
    record: object | None
    sidecar_status: str
    freshness: str
    action: str
    error: str | None = None


def media_paths_from_control_file(control_path, media_extensions) -> list[Path]:
    """Resolve enabled media rows from a myCamino control file."""
    path = Path(control_path).expanduser().resolve(strict=False)
    result = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value = line.split("|", 1)[0].strip().strip('"')
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        candidate = candidate.resolve(strict=False)
        if candidate.suffix.casefold() in media_extensions and candidate.is_file():
            result.append(candidate)
    return list(dict.fromkeys(result))


def prepare_media_records(
    media_paths: Iterable[Path],
    *,
    refresh_changed: bool = True,
    tracks_summary_path: Path | None = None,
    place_equivalence_m: float = 150.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
) -> list[PreparedMedia]:
    """Reuse valid sidecars and batch-extract only missing/invalid/changed files."""
    from GetGeoLocations import (
        ProcessingCancelled,
        LazyTrackGpsResolver,
        build_record_from_photo,
        load_tracks_summary,
        prefetch_media_metadata,
        record_from_sidecar_payload,
        write_record_json,
    )

    paths = list(dict.fromkeys(Path(path).expanduser().resolve(strict=False) for path in media_paths))
    prepared: list[PreparedMedia | None] = [None] * len(paths)
    extraction: list[tuple[int, Path, str, dict | None]] = []
    for index, path in enumerate(paths):
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Aborted.")
        if not path.is_file():
            prepared[index] = PreparedMedia(path, None, "missing", "unknown", "skipped", "file is missing")
            continue
        sidecar = media_sidecar_path(path)
        status, payload, reason = validate_media_sidecar(path, sidecar)
        freshness = media_sidecar_freshness(path, payload) if status == "available" else "unknown"
        if status == "available" and isinstance(payload, dict) and freshness != "changed":
            if freshness == "content-current":
                payload = patch_media_sidecar_signature(path, payload, sidecar)
                freshness = "current"
            record = record_from_sidecar_payload(payload, sidecar, path)
            prepared[index] = PreparedMedia(path, record, status, freshness, "reused")
            continue
        if status == "available" and freshness == "changed" and not refresh_changed:
            record = record_from_sidecar_payload(payload, sidecar, path)
            prepared[index] = PreparedMedia(path, record, status, freshness, "reused")
            continue
        extraction.append((index, path, status, payload if isinstance(payload, dict) else None))

    bundles = (
        prefetch_media_metadata(
            [path for _index, path, _status, _payload in extraction],
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        if extraction
        else {}
    )
    for index, path, status, old_payload in extraction:
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Aborted.")
        try:
            record = build_record_from_photo(
                path,
                getclearnames=False,
                geocode_cache={},
                known_places=[],
                place_distance_m=0.0,
                debug=False,
                metadata_bundle=bundles.get(path),
            )
            if old_payload:
                record.raw_metadata = dict(old_payload)
            write_record_json(record, set())
        except Exception as exc:
            prepared[index] = PreparedMedia(path, None, status, "unknown", "skipped", str(exc))
        else:
            prepared[index] = PreparedMedia(path, record, "available", "current", "extracted")
    completed = [item for item in prepared if item is not None]
    summary_path = (
        Path(tracks_summary_path).expanduser().resolve(strict=False)
        if tracks_summary_path is not None
        else None
    )
    if summary_path is not None and summary_path.is_file():
        tracks_summary = load_tracks_summary(summary_path, summary_path.with_suffix(".lst"))
        resolver = LazyTrackGpsResolver(tracks_summary, place_equivalence_m)
        for item in completed:
            if item.record is None:
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise ProcessingCancelled("Aborted.")
            if resolver.apply(item.record):
                write_record_json(item.record, set())
                item.action = "gps inferred" if item.record.gps_source else "gps refreshed"
    return completed
