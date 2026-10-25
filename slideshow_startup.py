"""Read-only startup signatures and background metadata checks (no UI calls)."""

import hashlib
import json
import math
import time
from pathlib import Path


def file_signature(path):
    path = Path(path).resolve(strict=False)
    try:
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    except FileNotFoundError:
        return (str(path), None)


def options_signature(options):
    return hashlib.sha256(json.dumps(options, sort_keys=True, default=str).encode()).hexdigest()


def prepared_track_hint(context, source_signature):
    """Keep validation identities from completed Adventure loading, not point arrays."""
    if source_signature != file_signature(context["gpx_path"]):
        return None
    keys = ("table_number", "track_plot_image_filename", "track_plot_time_lapse_image_filename",
            "track_data_sidecar", "track_data_fingerprint", "track_geometry_fingerprint", "track_type", "stage_number", "track_id", "segment_count", "hidden", "internal_id", "display_track_number")
    return {
        "source": source_signature,
        "directory": str(Path(context["output_dir"]).resolve(strict=False)),
        "output_base": context["output_base"],
        "processing": context["args"].adventure_processing_parameters,
        "summary": file_signature(context["table_json_path"]),
        "context": {"tracks": [{key: track[key] for key in keys if key in track} for track in context["tracks"]],
                    "overview_metadata_path": context.get("overview_metadata_path")},
    }


def prepared_hint_matches(hint, gpx, directory, options):
    return bool(
        hint and hint["source"] == file_signature(gpx)
        and hint["directory"] == str(Path(directory).resolve(strict=False))
        and hint["output_base"] == options.get("output_base")
        and hint["processing"] == options.get("adventure_processing_parameters")
        and hint["summary"][1] is not None
        and hint["summary"] == file_signature(hint["summary"][0])
    )


def validation_signature(gpx, control, directory, options, extra_assets=()):
    """Cache only a complete, successful validation of unchanged local files.

    Include derived data, maps and metadata, plus externally referenced maps.
    Deletion, addition, replacement and timestamp-preserving edits invalidate it.
    This in-memory optimisation is not a security/integrity check.
    """
    directory = Path(directory).resolve(strict=False)
    paths = {path.resolve(strict=False) for path in directory.rglob("*") if path.is_file()}
    paths.update(Path(path).resolve(strict=False) for path in extra_assets)
    return (
        file_signature(gpx), file_signature(control), str(directory),
        options_signature(options), tuple(file_signature(path) for path in sorted(paths)),
    )


def refresh_speed_metadata(gpx, directory, options, *, read_metadata, upgrade, progress, context=None):
    """Inspect/upgrade sidecars on the worker, never on the AppKit thread."""
    expected_window = float(options["gpx_running_speed_window_distance"])
    expected_threshold = float(options["gpx_stationary_speed_threshold"])
    paths = sorted(Path(directory).glob("*.json"))
    for index, path in enumerate(paths, 1):
        progress("Checking speed metadata", index, len(paths))
        try:
            metadata = read_metadata(path)
        except Exception:
            continue
        if (not isinstance(metadata, dict) or not metadata.get("track_fingerprint")
                or "timed_track_points" not in metadata):
            continue
        speed = metadata.get("running_speed")
        try:
            current = (
                isinstance(speed, dict) and speed.get("version") == 1
                and math.isclose(float(speed["window_distance_m"]), expected_window)
                and math.isclose(float(speed["stationary_threshold_kmh"]), expected_threshold)
            )
        except (KeyError, TypeError, ValueError):
            current = False
        if not current:
            progress("Updating speed metadata", 0, 0)
            upgrade_options = dict(options)
            for key in ("output_dir", "plot_overview", "plot_tracks"):
                upgrade_options.pop(key, None)
            if context is not None:
                upgrade_options["prepared_context"] = context
            upgrade(gpx, directory, **upgrade_options)
            return


def validate_startup(gpx, control, directory, options, *, previous_key, extra_assets,
                     prepare, execute, check, read_metadata, upgrade, progress, prepared_hint=None):
    """Blocking worker operation; callbacks must not directly touch AppKit."""
    timings = {}
    started = time.perf_counter()
    progress("Checking saved preparation", 0, 0)
    before = validation_signature(gpx, control, directory, options, extra_assets)
    timings["Dependency signatures"] = time.perf_counter() - started
    if before == previous_key:
        progress("Reusing verified track and map data", 0, 0)
        return dict(issues={"maps": [], "derived": []}, retry=False,
                    cache_key=before, reused=True, timings=timings, source_signatures=before[:2])
    control_text = Path(control).read_text(encoding="utf-8")

    def measured(label, callback):
        phase_start = time.perf_counter()
        result = callback()
        timings[label] = time.perf_counter() - phase_start
        return result

    context = None
    reused_loaded_tracks = False
    if prepared_hint_matches(prepared_hint, gpx, directory, options):
        progress("Checking already prepared Adventure tracks", 0, 0)
        context = prepared_hint["context"]
        issues = measured("Prepared Adventure validation", lambda: check(control_text, directory, context))
        if not issues.get("derived"):
            reused_loaded_tracks = True
            progress("Reusing prepared Adventure tracks", len(context["tracks"]), len(context["tracks"]))
    if not reused_loaded_tracks:
        progress("Checking tracks", 0, 0)
        prepare_options = dict(options)
        prepare_options["track_processing_callback"] = lambda current, total: progress("Checking tracks", current, total)
        context = measured("Track preparation", lambda: prepare(str(gpx), **prepare_options))
        progress("Refreshing derived track data", 0, 0)
        measured("Derived track data", lambda: execute(context, print_table_output=False, write_summary=True))
    measured("Speed metadata", lambda: refresh_speed_metadata(
        gpx, directory, options, read_metadata=read_metadata, upgrade=upgrade, progress=progress,
        context=None if reused_loaded_tracks else context
    ))
    progress("Checking slideshow maps", 0, 0)
    from gpx_tracks_table import refresh_track_presentation_metadata
    refresh_track_presentation_metadata({**context, "output_dir": directory})
    validated_files = validation_signature(gpx, control, directory, options, extra_assets)
    issues = measured("Map validation", lambda: check(control_text, directory, context))
    after = validation_signature(gpx, control, directory, options, extra_assets)
    retry = before[:2] != after[:2] or validated_files != after
    successful = not retry and not issues.get("maps") and not issues.get("derived")
    return dict(issues=issues, retry=retry, cache_key=after if successful else None,
                reused=False, reused_loaded_tracks=reused_loaded_tracks,
                timings=timings, source_signatures=after[:2])
