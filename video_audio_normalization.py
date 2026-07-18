#!/usr/bin/env python3
"""Persistent, source-safe video-audio normalization for myCamino projects."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from json_storage import atomic_write_json


VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg",
    ".mpg", ".mts", ".webm",
}
NORMALIZED_VIDEO_DIRECTORY = "normalized-videos"
NORMALIZATION_MANIFEST = "manifest.json"
MANIFEST_VERSION = 1
GENERATED_DIRECTORY_NAMES = frozenset(
    {"audio", "trackimages", NORMALIZED_VIDEO_DIRECTORY, "build", "dist", ".git"}
)


@dataclass(frozen=True)
class NormalizationSettings:
    """Settings that determine whether one generated video remains current."""

    target_lufs: float = -16.0
    maximum_boost_db: float = 12.0
    true_peak_db: float = -1.5

    def payload(self) -> dict[str, float]:
        return {
            "target_lufs": float(self.target_lufs),
            "maximum_boost_db": float(self.maximum_boost_db),
            "true_peak_db": float(self.true_peak_db),
        }


@dataclass
class NormalizationResult:
    """Aggregate result for one explicit project normalization run."""

    total: int = 0
    normalized: int = 0
    current: int = 0
    without_audio: int = 0
    failed: int = 0
    cancelled: bool = False
    messages: list[str] = field(default_factory=list)


def source_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def is_generated_media_path(path: Path, project_dir: Path) -> bool:
    """Return whether *path* is inside a generated project directory."""
    try:
        relative = path.resolve(strict=False).relative_to(project_dir.resolve(strict=False))
    except ValueError:
        return False
    return any(part.casefold() in GENERATED_DIRECTORY_NAMES for part in relative.parts[:-1])


def discover_project_videos(project_dir: Path | str) -> list[Path]:
    """Discover source videos recursively while excluding generated assets."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    videos: list[Path] = []
    for root, directory_names, file_names in os.walk(project):
        directory_names[:] = [
            name for name in directory_names
            if name.casefold() not in GENERATED_DIRECTORY_NAMES
        ]
        root_path = Path(root)
        for name in file_names:
            candidate = root_path / name
            if candidate.suffix.casefold() in VIDEO_EXTENSIONS:
                videos.append(candidate.resolve(strict=False))
    return sorted(videos, key=lambda item: item.relative_to(project).as_posix().casefold())


def manifest_path(project_dir: Path | str) -> Path:
    return Path(project_dir).expanduser().resolve(strict=False) / NORMALIZED_VIDEO_DIRECTORY / NORMALIZATION_MANIFEST


def load_manifest(project_dir: Path | str) -> dict:
    path = manifest_path(project_dir)
    if not path.is_file():
        return {"version": MANIFEST_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": MANIFEST_VERSION, "entries": {}}
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        return {"version": MANIFEST_VERSION, "entries": {}}
    if not isinstance(payload.get("entries"), dict):
        payload["entries"] = {}
    return payload


def _settings_match(stored: object, settings: NormalizationSettings) -> bool:
    if not isinstance(stored, dict):
        return False
    return all(
        isinstance(stored.get(key), (int, float))
        and math.isclose(float(stored[key]), value, rel_tol=0.0, abs_tol=1e-6)
        for key, value in settings.payload().items()
    )


def valid_normalized_video(
    project_dir: Path | str,
    source: Path | str,
    settings: NormalizationSettings,
    *,
    manifest: Optional[dict] = None,
) -> Optional[Path]:
    """Resolve a current generated copy for *source*, otherwise return ``None``."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    source_path = Path(source).expanduser().resolve(strict=False)
    try:
        key = source_path.relative_to(project).as_posix()
        signature = source_signature(source_path)
    except (ValueError, OSError):
        return None
    payload = manifest if isinstance(manifest, dict) else load_manifest(project)
    entry = payload.get("entries", {}).get(key)
    if not isinstance(entry, dict):
        return None
    if entry.get("source_signature") != signature or not _settings_match(entry.get("parameters"), settings):
        return None
    output_text = entry.get("output")
    if not isinstance(output_text, str) or not output_text:
        return None
    output = (project / output_text).resolve(strict=False)
    normalized_root = (project / NORMALIZED_VIDEO_DIRECTORY).resolve(strict=False)
    try:
        output.relative_to(normalized_root)
    except ValueError:
        return None
    return output if output.is_file() else None


def normalization_status(
    project_dir: Path | str,
    settings: NormalizationSettings,
) -> dict[str, int]:
    """Return current/stale/missing/no-audio counts without invoking FFmpeg."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    videos = discover_project_videos(project)
    payload = load_manifest(project)
    counts = {"total": len(videos), "current": 0, "stale": 0, "missing": 0, "without_audio": 0}
    entries = payload.get("entries", {})
    for source in videos:
        key = source.relative_to(project).as_posix()
        entry = entries.get(key)
        if (
            isinstance(entry, dict)
            and entry.get("status") == "without_audio"
            and entry.get("source_signature") == source_signature(source)
            and _settings_match(entry.get("parameters"), settings)
        ):
            counts["without_audio"] += 1
        elif valid_normalized_video(project, source, settings, manifest=payload) is not None:
            counts["current"] += 1
        elif isinstance(entry, dict):
            counts["stale"] += 1
        else:
            counts["missing"] += 1
    return counts


def find_ffmpeg_executable() -> Optional[Path]:
    """Find the explicitly configured, bundled, or development FFmpeg binary."""
    candidates: list[Path] = []
    configured = os.environ.get("MYCAMINO_FFMPEG")
    if configured:
        candidates.append(Path(configured).expanduser())
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.extend(
            (
                Path(meipass) / "ffmpeg",
                Path(meipass) / "GPSTrackShow" / "ffmpeg",
                Path(meipass) / "vendor" / "ffmpeg" / "ffmpeg",
            )
        )
    executable = Path(sys.executable).resolve(strict=False)
    candidates.extend(
        (
            executable.parent / "ffmpeg",
            executable.parent.parent / "Resources" / "ffmpeg",
            executable.parent.parent / "Resources" / "GPSTrackShow" / "ffmpeg",
            Path(__file__).resolve().parent / "vendor" / "ffmpeg" / "ffmpeg",
        )
    )
    from_path = shutil.which("ffmpeg")
    if from_path:
        candidates.append(Path(from_path))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve(strict=False)
    return None


def _ffmpeg_version(ffmpeg: Path) -> str:
    completed = subprocess.run(
        [str(ffmpeg), "-version"], capture_output=True, text=True, check=False
    )
    return (completed.stdout.splitlines() or [""])[0].strip()


def _parse_loudnorm_json(stderr: str) -> Optional[dict[str, float]]:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.DOTALL)
    for block in reversed(matches):
        try:
            payload = json.loads(block)
            parsed = {
                key: float(payload[key])
                for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if all(math.isfinite(value) for value in parsed.values()):
            return parsed
    return None


def _run_process(command: list[str], cancel_event: Optional[threading.Event]) -> subprocess.CompletedProcess:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while process.poll() is None:
        if cancel_event is not None and cancel_event.wait(0.1):
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(command, -15, stdout, stderr)
    stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _output_relative_path(project: Path, source: Path) -> Path:
    relative = source.relative_to(project)
    suffix = source.suffix.casefold()
    output_suffix = suffix if suffix in {".mov", ".mp4", ".m4v"} else ".mov"
    return Path(NORMALIZED_VIDEO_DIRECTORY) / relative.parent / f"{source.stem}{output_suffix}"


def normalize_project_videos(
    project_dir: Path | str,
    settings: NormalizationSettings,
    *,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    detail_callback: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> NormalizationResult:
    """Normalize stale project videos, preserving originals and completed outputs."""
    project = Path(project_dir).expanduser().resolve(strict=False)
    ffmpeg = find_ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is not available. Build or install the bundled LGPL FFmpeg binary first.")
    videos = discover_project_videos(project)
    result = NormalizationResult(total=len(videos))
    payload = load_manifest(project)
    entries = payload.setdefault("entries", {})
    ffmpeg_version = _ffmpeg_version(ffmpeg)
    for position, source in enumerate(videos, start=1):
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
            break
        relative = source.relative_to(project)
        if progress_callback is not None:
            progress_callback(position - 1, len(videos), relative.as_posix())
        existing_entry = entries.get(relative.as_posix())
        current_without_audio = bool(
            isinstance(existing_entry, dict)
            and existing_entry.get("status") == "without_audio"
            and existing_entry.get("source_signature") == source_signature(source)
            and _settings_match(existing_entry.get("parameters"), settings)
        )
        if current_without_audio:
            result.without_audio += 1
            message = f"Skipping video without audio: {relative.as_posix()}"
            result.messages.append(message)
            if detail_callback is not None:
                detail_callback(message)
            continue
        if valid_normalized_video(project, source, settings, manifest=payload) is not None:
            result.current += 1
            message = f"Skipping current normalized video: {relative.as_posix()}"
            result.messages.append(message)
            if detail_callback is not None:
                detail_callback(message)
            continue
        if detail_callback is not None:
            detail_callback(f"Measuring audio: {relative.as_posix()}")
        analysis_filter = (
            f"loudnorm=I={settings.target_lufs}:TP={settings.true_peak_db}:LRA=11:print_format=json"
        )
        measured = _run_process(
            [str(ffmpeg), "-hide_banner", "-nostats", "-i", str(source), "-map", "0:a:0", "-af", analysis_filter, "-f", "null", "-"],
            cancel_event,
        )
        if measured.returncode == -15:
            result.cancelled = True
            break
        loudness = _parse_loudnorm_json(measured.stderr or "")
        key = relative.as_posix()
        signature = source_signature(source)
        if loudness is None:
            no_audio = "matches no streams" in (measured.stderr or "").casefold() or "stream map" in (measured.stderr or "").casefold()
            if no_audio:
                entries[key] = {
                    "source_signature": signature,
                    "parameters": settings.payload(),
                    "status": "without_audio",
                    "ffmpeg_version": ffmpeg_version,
                }
                atomic_write_json(manifest_path(project), payload)
                result.without_audio += 1
                message = f"No audio stream: {relative.as_posix()}"
            else:
                result.failed += 1
                message = f"Could not measure audio: {relative.as_posix()}"
            result.messages.append(message)
            if detail_callback is not None:
                detail_callback(message)
            continue
        requested_gain = float(settings.target_lufs) - loudness["input_i"]
        boost_limited = requested_gain > float(settings.maximum_boost_db)
        applied_gain = min(requested_gain, float(settings.maximum_boost_db))
        effective_target = loudness["input_i"] + applied_gain
        output_relative = _output_relative_path(project, source)
        output = project / output_relative
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=output.suffix, dir=output.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        if boost_limited:
            peak_limit = 10.0 ** (float(settings.true_peak_db) / 20.0)
            render_filter = (
                f"volume={applied_gain}dB,"
                f"alimiter=limit={peak_limit:.8f}:level=false"
            )
        else:
            render_filter = (
                f"loudnorm=I={effective_target}:TP={settings.true_peak_db}:LRA=11:"
                f"measured_I={loudness['input_i']}:measured_TP={loudness['input_tp']}:"
                f"measured_LRA={loudness['input_lra']}:measured_thresh={loudness['input_thresh']}:"
                f"offset={loudness['target_offset']}:linear=true:print_format=summary"
            )
        try:
            rendered = _run_process(
                [
                    str(ffmpeg), "-hide_banner", "-nostats", "-y", "-i", str(source),
                    "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", render_filter,
                    str(temporary),
                ],
                cancel_event,
            )
            if rendered.returncode == -15:
                result.cancelled = True
                break
            if rendered.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                result.failed += 1
                message = f"Normalization failed; original will be used: {relative.as_posix()}"
                result.messages.append(message)
                if detail_callback is not None:
                    detail_callback(message)
                continue
            os.replace(temporary, output)
            entries[key] = {
                "source_signature": signature,
                "output": output_relative.as_posix(),
                "status": "normalized",
                "parameters": settings.payload(),
                "stream": "0:a:0",
                "measured": loudness,
                "applied_gain_db": applied_gain,
                "boost_limited": boost_limited,
                "ffmpeg_version": ffmpeg_version,
            }
            atomic_write_json(manifest_path(project), payload)
            result.normalized += 1
            message = (
                f"Normalized {relative.as_posix()}: {loudness['input_i']:.1f} LUFS, "
                f"gain {applied_gain:+.1f} dB"
            )
            result.messages.append(message)
            if detail_callback is not None:
                detail_callback(message)
        finally:
            temporary.unlink(missing_ok=True)
    if progress_callback is not None and not result.cancelled:
        progress_callback(len(videos), len(videos), "Complete")
    return result
