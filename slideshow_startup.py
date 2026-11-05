"""Shared startup validation/preparation cache (worker-only, no AppKit calls)."""

import hashlib
import json
import math
import time
from pathlib import Path

from gpx_processing import TRACK_FINGERPRINT_VERSION, PROCESSING_INPUT_FINGERPRINT_VERSION
from json_storage import atomic_write_json
from map_overlay import MAP_CONTENT_VERSION
from slideshow_control_format import split_disabled_control_line
from track_map_layout_utils import time_lapse_track_map_name


STARTUP_CACHE_VERSION = 2
ALGORITHM_VERSIONS = [TRACK_FINGERPRINT_VERSION, PROCESSING_INPUT_FINGERPRINT_VERSION, MAP_CONTENT_VERSION]
PRESENTATION_REFRESH_VERSION = 1
PRESENTATION_KEYS = ("track_type", "hidden", "stage_number", "track_id", "internal_id", "display_track_number")
PROCESSING_OPTIONS = (
    "sort_original", "sort_date", "sort_distance", "remove_prefix", "gpx_threshold_distance",
    "gpx_threshold_accuracy", "gpx_horizontal_smoothing_distance", "gpx_elevation_smoothing_distance",
    "gpx_maximum_vertical_accuracy", "gpx_maximum_hdop", "gpx_maximum_vdop",
    "gpx_running_speed_window_distance", "gpx_stationary_speed_threshold", "fallback_walking_speed_kmh",
)


def file_signature(path):
    path = Path(path).resolve(strict=False)
    try:
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    except FileNotFoundError:
        return (str(path), None)


def options_signature(options):
    return hashlib.sha256(json.dumps(options, sort_keys=True, default=str).encode()).hexdigest()


def preparation_options(options):
    """Rendering preferences cannot invalidate geometry/timing preparation."""
    return {key: options.get(key) for key in ("output_base", "adventure_processing_parameters", *PROCESSING_OPTIONS)}


def prepared_track_hint(context, source_signature):
    """Keep validation identities from completed Adventure loading, not point arrays."""
    if source_signature != file_signature(context["gpx_path"]):
        return None
    keys = ("table_number", "track_plot_image_filename", "track_plot_time_lapse_image_filename",
            "track_data_sidecar", "track_data_fingerprint", "track_geometry_fingerprint", *PRESENTATION_KEYS,
            "segment_count")
    directory = Path(context["output_dir"])
    return {
        "source": source_signature,
        "directory": str(directory.resolve(strict=False)),
        "output_base": context["output_base"],
        "processing": context["args"].adventure_processing_parameters,
        "processing_inputs": {key: getattr(context["args"], key) for key in PROCESSING_OPTIONS
                              if hasattr(context["args"], key)},
        "summary": file_signature(context["table_json_path"]),
        "derived_signatures": [file_signature(directory / track["track_data_sidecar"])
                               for track in context["tracks"] if track.get("track_data_sidecar")],
        "context": {"tracks": [{key: track[key] for key in keys if key in track} for track in context["tracks"]],
                    "overview_metadata_path": (str(context["overview_metadata_path"])
                                               if context.get("overview_metadata_path") else None)},
    }


def prepared_hint_matches(hint, gpx, directory, options):
    try:
        return bool(
            hint and hint["source"] == file_signature(gpx)
            and hint["directory"] == str(Path(directory).resolve(strict=False))
            and hint["output_base"] == options.get("output_base")
            and hint["processing"] == options.get("adventure_processing_parameters")
            and all(key in hint.get("processing_inputs", {}) and hint["processing_inputs"][key] == options[key]
                    for key in PROCESSING_OPTIONS if key in options)
            and hint["summary"][1] is not None
            and hint["summary"] == file_signature(hint["summary"][0])
            and isinstance(hint["context"]["tracks"], list)
            and all(isinstance(track, dict)
                    and all(isinstance(track.get(key), str) and track[key]
                            for key in ("track_data_sidecar", "track_data_fingerprint", "track_geometry_fingerprint"))
                    for track in hint["context"]["tracks"])
            and (hint["context"].get("overview_metadata_path") is None
                 or isinstance(hint["context"]["overview_metadata_path"], (str, Path)))
        )
    except (KeyError, TypeError, IndexError):
        return False


def _cache_path(gpx, directory, options):
    identity = (str(Path(gpx).resolve(strict=False)), str(Path(directory).resolve(strict=False)), options.get("output_base"))
    name = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
    return Path(directory).parent / ".mycamino" / "cache" / f"slideshow-startup-{name}.json"


def _tuple_tree(value):
    return tuple(_tuple_tree(item) for item in value) if isinstance(value, list) else value


def _load_cache(gpx, directory, options):
    try:
        record = json.loads(_cache_path(gpx, directory, options).read_text(encoding="utf-8"))
        if record["version"] != STARTUP_CACHE_VERSION or record["algorithms"] != ALGORITHM_VERSIONS:
            return {}
        hint = record.get("prepared_hint")
        if hint is not None:
            hint["source"] = tuple(hint["source"])
            hint["summary"] = tuple(hint["summary"])
            hint["derived_signatures"] = [tuple(item) for item in hint.get("derived_signatures", [])]
            if any(len(item) < 2 or not isinstance(item[0], str) for item in hint["derived_signatures"]):
                return {}
        record["cache_key"] = _tuple_tree(record.get("cache_key"))
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def save_prepared_track_hint(gpx, directory, options, hint, *, cache_key=None):
    """Persist the existing cache; failure only loses an optimization."""
    if hint is None:
        return
    try:
        atomic_write_json(_cache_path(gpx, directory, options), {
            "version": STARTUP_CACHE_VERSION, "algorithms": ALGORITHM_VERSIONS,
            "prepared_hint": hint, "cache_key": cache_key,
        })
    except (OSError, TypeError, ValueError):
        pass


def reuse_prepared_tracks(gpx, directory, options):
    """Reuse completed loading across launches, only with unchanged derived files."""
    hint = _load_cache(gpx, directory, options).get("prepared_hint")
    if not prepared_hint_matches(hint, gpx, directory, options):
        return None
    signatures = hint.get("derived_signatures", [])
    tracks = hint["context"]["tracks"]
    if (len(signatures) != len(tracks) or not signatures
            or any(signature[1] is None or signature != file_signature(signature[0]) for signature in signatures)):
        return None
    return hint


def referenced_control_map_assets(control_text, trackimages_dir, *, include_disabled=False):
    """One shared dependency list for GUI checks and persistent startup validation."""
    paths = []
    for line in str(control_text).splitlines():
        disabled, content = split_disabled_control_line(line.strip())
        if disabled and not include_disabled:
            continue
        keyword, separator, name = content.partition(":")
        keyword = keyword[1:].strip().lower() if keyword.startswith("#") else ""
        if not separator or keyword not in {"overviewmap", "map", "mapbefore", "mapafter", "mediamap"}:
            continue
        name = name.strip()
        if not name:
            continue
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = Path(trackimages_dir) / path
        paths.append(path)
        if keyword == "map":
            paths.append(path.with_name(time_lapse_track_map_name(path.name)))
    return list(dict.fromkeys(paths))


def validation_signature(gpx, control, directory, options, extra_assets=(), *, context=None):
    """Stat required dependencies only, including absent files and external maps."""
    directory = Path(directory).resolve(strict=False)
    paths = set(Path(path).resolve(strict=False) for path in extra_assets)
    for path in referenced_control_map_assets(Path(control).read_text(encoding="utf-8"), directory):
        paths.update((path.resolve(strict=False), path.with_suffix(".json").resolve(strict=False)))
    if context is not None:
        for track in context.get("tracks", []):
            if track.get("track_data_sidecar"):
                paths.add((directory / track["track_data_sidecar"]).resolve(strict=False))
        overview = context.get("overview_metadata_path")
        if overview:
            paths.add(Path(overview).resolve(strict=False))
    if options.get("output_base"):
        paths.add(directory / f"{options['output_base']}-summary.json")
    return (file_signature(gpx), file_signature(control), str(directory),
            options_signature(preparation_options(options)), tuple(file_signature(path) for path in sorted(paths)),
            # Recheck old successful validation once without discarding prepared tracks.
            (*ALGORITHM_VERSIONS, PRESENTATION_REFRESH_VERSION))


class StartupMetadataReader:
    """Per-pass compact projections, not retained full-resolution point arrays."""

    def __init__(self, read):
        self.read = read
        self.records = {}
        self.read_count = 0

    def __call__(self, path):
        signature = file_signature(path)
        key = signature[0]
        if key in self.records and self.records[key][0] == signature:
            return self.records[key][1]
        data = self.read(path)
        self.read_count += 1
        if signature != file_signature(path):
            raise RuntimeError(f"Metadata changed while being read: {path}")
        if isinstance(data, dict):
            keys = ("version", "track_fingerprint", "track_data_fingerprint", "track_geometry_fingerprint",
                    "track_number", "map_content_version", "background_only", "source_track_geometry_fingerprints",
                    "running_speed", "tracks", "overlay_track_types", "overlay_hidden_tracks", *PRESENTATION_KEYS)
            projected = {key: data[key] for key in keys if key in data}
            if "timed_track_points" in data:
                projected["timed_track_points"] = []
            data = projected
        self.records[key] = (signature, data)
        return data

    def changed(self):
        return any(signature != file_signature(path) for path, (signature, _data) in self.records.items())


def derived_track_issues(directory, context, read_metadata):
    issues = []
    for track in context.get("tracks", []):
        name = str(track.get("track_data_sidecar", "")).strip()
        expected = str(track.get("track_data_fingerprint", "")).strip()
        try:
            data = read_metadata(Path(directory) / name) if name else None
        except (OSError, ValueError, TypeError):
            data = None
        if (not expected or not isinstance(data, dict) or data.get("track_data_fingerprint") != expected
                or ("version" in data and data["version"] != 1)):
            issues.append(name or f"Track {track.get('table_number', '?')} derived data")
    return issues


def refresh_speed_metadata(gpx, directory, options, *, read_metadata, upgrade, progress,
                           context=None, paths=None, check_only=False):
    """Inspect canonical derived data during startup, map payloads for explicit upgrades."""
    expected_window = float(options["gpx_running_speed_window_distance"])
    expected_threshold = float(options["gpx_stationary_speed_threshold"])
    paths = sorted(Path(directory).glob("*.json")) if paths is None else list(paths)
    for index, path in enumerate(paths, 1):
        progress("Checking speed metadata", index, len(paths))
        try:
            metadata = read_metadata(path)
        except (OSError, ValueError, TypeError):
            if check_only:
                return False
            continue
        if (not isinstance(metadata, dict) or not metadata.get("track_fingerprint")
                or "timed_track_points" not in metadata):
            if check_only:
                return False
            continue
        speed = metadata.get("running_speed")
        try:
            current = (isinstance(speed, dict) and speed.get("version") == 1
                       and math.isclose(float(speed["window_distance_m"]), expected_window)
                       and math.isclose(float(speed["stationary_threshold_kmh"]), expected_threshold))
        except (KeyError, TypeError, ValueError):
            current = False
        if not current:
            if check_only:
                return False
            progress("Updating speed metadata", 0, 0)
            upgrade_options = dict(options)
            for key in ("output_dir", "plot_overview", "plot_tracks"):
                upgrade_options.pop(key, None)
            if context is not None:
                upgrade_options["prepared_context"] = context
            upgrade(gpx, directory, **upgrade_options)
            return False
    return True


def validate_startup(gpx, control, directory, options, *, previous_key, extra_assets,
                     prepare, execute, check, read_metadata, upgrade, progress, prepared_hint=None,
                     prepared_future=None):
    """Reuse successful validation, otherwise validate dependencies on the worker."""
    timings = {}

    def measured(label, callback):
        started = time.perf_counter()
        result = callback()
        timings[label] = time.perf_counter() - started
        return result

    if prepared_future is not None:
        progress("Waiting for current track preparation", 0, 0)
        try:
            result = measured("Shared track preparation", prepared_future.result)
            prepared_hint = result.get("slideshow_prepared_hint") or prepared_hint
        except Exception:
            pass  # Failed background work is checked/repaired by this same pipeline.
    record = _load_cache(gpx, directory, options)
    if not prepared_hint_matches(prepared_hint, gpx, directory, options):
        prepared_hint = record.get("prepared_hint")
    context = prepared_hint["context"] if prepared_hint_matches(prepared_hint, gpx, directory, options) else None
    progress("Checking saved preparation", 0, 0)
    before = measured("Dependency signatures", lambda: validation_signature(
        gpx, control, directory, options, extra_assets, context=context))
    if before == previous_key or before == record.get("cache_key"):
        progress("Reusing verified track and map data", 0, 0)
        return dict(issues={"maps": [], "derived": []}, retry=False, cache_key=before,
                    prepared_hint=prepared_hint, reused=True, timings=timings, source_signatures=before[:2])

    reader = StartupMetadataReader(read_metadata)
    control_text = Path(control).read_text(encoding="utf-8")
    reused_loaded_tracks = context is not None
    if context is not None:
        issues = measured("Prepared Adventure validation", lambda: check(
            control_text, directory, context, read_metadata=reader))
        speed_paths = [Path(directory) / track["track_data_sidecar"] for track in context["tracks"]
                       if track.get("track_data_sidecar")]
        speeds_current = measured("Speed metadata", lambda: refresh_speed_metadata(
            gpx, directory, options, read_metadata=reader, upgrade=upgrade, progress=progress,
            paths=speed_paths, check_only=True))
        reused_loaded_tracks = not issues.get("derived") and speeds_current
    if not reused_loaded_tracks:
        progress("Checking tracks", 0, 0)
        prepare_options = dict(options)
        prepare_options["track_processing_callback"] = lambda current, total: progress("Checking tracks", current, total)
        context = measured("Track preparation", lambda: prepare(str(gpx), **prepare_options))
        progress("Refreshing derived track data", 0, 0)
        measured("Derived track data", lambda: execute(context, print_table_output=False, write_summary=True))
        if context.get("gpx_path") and context.get("table_json_path"):
            prepared_hint = prepared_track_hint(context, before[0])

    progress("Checking slideshow maps", 0, 0)
    from gpx_tracks_table import refresh_track_presentation_metadata
    measured("Presentation metadata", lambda: refresh_track_presentation_metadata(
        {**context, "output_dir": directory}, read_metadata=reader))
    validated_files = validation_signature(gpx, control, directory, options, extra_assets, context=context)
    if not reused_loaded_tracks or validated_files != before or reader.changed():
        issues = measured("Map validation", lambda: check(control_text, directory, context, read_metadata=reader))
    after = validation_signature(gpx, control, directory, options, extra_assets, context=context)
    retry = before[:2] != after[:2] or validated_files != after or reader.changed()
    successful = not retry and not issues.get("maps") and not issues.get("derived")
    cache_key = after if successful else None
    if not retry:
        if prepared_hint is not None and not issues.get("derived"):
            prepared_hint = dict(prepared_hint, derived_signatures=[
                file_signature(Path(directory) / track["track_data_sidecar"])
                for track in context["tracks"] if track.get("track_data_sidecar")])
        save_prepared_track_hint(gpx, directory, options, prepared_hint, cache_key=cache_key)
    return dict(issues=issues, retry=retry, cache_key=cache_key, prepared_hint=prepared_hint,
                reused=False, reused_loaded_tracks=reused_loaded_tracks, metadata_reads=reader.read_count,
                timings=timings, source_signatures=after[:2])
