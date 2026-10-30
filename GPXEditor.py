#!/Users/falcke/Dropbox/Documents/Python/trackit/.venv/bin/python
"""Native macOS GPX track editor using Cocoa/AppKit.

The module can be launched standalone or imported by another PyObjC GUI.  The
public entry point for embedding is ``show_gpx_editor``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
from collections import OrderedDict, defaultdict
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable

from plot_rendering import add_headless_basemap, new_figure, serialized_plotting

from elevation_profile_cache import elevation_profile_visible_range
from gpx_pdf_summary import (
    DEFAULT_PDF_COLUMNS, PDF_EXPORT_EXECUTOR, PDFExportCancelled,
    PDF_CHECKPOINT_OPTIONS, SummaryOptions, SummarySnapshot, check_cancelled, export_summary_pdf,
    pdf_report_parameters,
    report_name, reusable_map_background, select_summary_tracks, summary_track,
)
from gpx_pdf_details import PDFMediaIndex, build_checkpoints
from gpx_pdf_enrichment import enrich_checkpoints, prepare_checkpoint_cache
from gpx_pdf_layout import (
    continuation_figures, draw_checkpoint_markers, draw_panel, measure_panel, report_title, track_statistics,
)

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSButton,
    NSButtonCell,
    NSButtonTypeSwitch,
    NSColor,
    NSCompositingOperationSourceOver,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFont,
    NSFontAttributeName,
    NSFontManager,
    NSBoldFontMask,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageAlignCenter,
    NSImageNameFolder,
    NSImageOnly,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSZeroRect,
    NSOpenPanel,
    NSPasteboard,
    NSPopUpButton,
    NSProgressIndicator,
    NSProgressIndicatorStyleBar,
    NSRoundLineCapStyle,
    NSRoundLineJoinStyle,
    NSSavePanel,
    NSScrollView,
    NSSortDescriptor,
    NSTableColumn,
    NSTableRowView,
    NSTableView,
    NSTableViewNoColumnAutoresizing,
    NSTextField,
    NSTextView,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWorkspace,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import (
    NSDate,
    NSData,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSString,
    NSTimer,
    NSURL,
    NSUserDefaults,
)

from beta_notice import (
    BETA_NOTICE_PREFERENCE_KEY,
    BETA_NOTICE_VERSION,
    BUG_REPORT_URL,
    FEATURE_REQUEST_URL,
    beta_notice_should_be_shown,
)
from cocoa_button_style import apply_liquid_glass_button_style, make_liquid_glass_button
from cocoa_table_editing import make_table_text_cell
from basemap_tile_utils import tolerate_missing_tiles
from adventure_parameters import (
    EDITOR_PARAMETER_KEYS,
    EDITOR_PARAMETER_SECTIONS,
    default_parameters,
    normalize_parameters,
    parameter_subset,
)
from cocoa_parameter_editor import CocoaParameterEditor
from cocoa_map_provider_setup import configure_hosted_provider, run_map_provider_setup
from cocoa_media_viewer import CocoaMediaViewer
from cocoa_native_menus import (
    ReusableToolWindowDelegate,
    WindowMenuCoordinator,
    add_menu,
    configure_mycamino_branding,
    menu_item,
)
from window_lifecycle import WINDOW_REGISTRY
from cocoa_application_news import install_application_news
from workspace_mutations import MutationKind, WorkspaceMutation
from track_types import (TRACK_TYPES, TYPE_LABELS, element_track_type, normalize_track_type,
                         stage_number, track_number, track_ids, numbering_proposal, track_color, report_stage_title,
                         allocate_track_ids, store_document_counter, apply_numbering_proposal)
from track_symbols import draw_pdf_symbol


def cocoa_track_color(track, parameters=None):
    from PIL import ImageColor
    value = track_color(track, parameters)
    red, green, blue = (component / 255 for component in ImageColor.getrgb(value)[:3])
    return NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1)
from gpx_processing import (
    ProcessedTrack,
    ProcessingOptions,
    extract_raw_track_points,
    format_gpx_timestamp,
    haversine_km,
    parse_time,
    process_raw_points,
    process_track_element,
    processed_track_geometry_fingerprint,
    processing_input_fingerprint,
    raw_track_geometry_fingerprint,
    semantic_track_fingerprint,
)
from gpx_import import (
    load_gpx_document,
    sanitize_track_element as sanitize_imported_track,
    timing_status_for_track,
)
from gpx_editor_metrics_cache import (
    display_geometry_for_processed_track,
    load_metrics_cache,
    match_cached_tracks,
    write_metrics_cache,
)
from gpx_point_editing import (
    cut_segment_after_row,
    deserialize_points,
    duplicate_point,
    insert_point_for_rows,
    insert_points_at_route_matches,
    insert_points_chronologically,
    insert_points_after_row,
    interpolated_height_updates,
    join_segments_at_row,
    joinable_segment_boundary,
    media_paths_for_point,
    mark_track_media_derived,
    mark_track_media_timed,
    manual_track_start_time,
    monotonic_route_matches,
    move_rows as move_point_rows,
    point_from_values,
    point_time_origin,
    point_time_source_label,
    rewrite_local_media_links,
    remove_rows,
    serialized_points,
    set_manual_track_start_time,
    set_point_time_origin,
    translate_points_web_mercator,
)
from media_route_timing import MediaObservation, RoutePoint, propose_route_timing
from gpx_coordinate_input import CoordinateParseError, ParsedCoordinate, parse_coordinate_text
from json_storage import atomic_write_json, load_parameter_subset, parameter_subset_payload
from map_provider_utils import (
    DEFAULT_TILE_CACHE_DIR,
    TileCacheMissError,
    TileRequestCancelled,
    configure_contextily_cache,
    contextily_provider,
    contextily_request_timeout,
    provider_display_name,
    provider_attribution,
    provider_requires_credential,
    provider_tile_url,
    prune_contextily_cache,
    read_provider_credential,
)
from map_provider_setup import load_map_provider_preference, save_map_provider_preference
from map_overlay import MapOverlayScene, normalize_overlay_segments
from control_preview_cache import ThumbnailMemoryCache, create_or_reuse_thumbnail
from interactive_tile_viewport import (
    DEFAULT_WORLD_OCEAN_RGB,
    TileCoordinate,
    aspect_preserving_rect,
    clamp_extent_to_web_mercator,
    is_complete_world_extent,
    load_tile_png,
    mouse_wheel_zoom_factor,
    retained_tile_draw_order,
    shifted_extent as shifted_interactive_extent,
    scroll_pan_deltas,
    sample_world_ocean_rgb_from_png,
    tile_bounds_mercator,
    tile_intersects_extent,
    tile_zoom_for_viewport,
    visible_tiles,
    zoomed_extent as zoomed_interactive_extent,
)
from track_timing_utils import timestamps_from_start
from license_resources import read_license_document


_INTERACTIVE_WORLD_OCEAN_RGB_BY_PROVIDER: dict[str, tuple[float, float, float]] = {}
INTERACTIVE_TILE_MEMORY_LIMIT = 256


def process_track_snapshots(xml_snapshots: list[str], options: ProcessingOptions) -> list[ProcessedTrack]:
    """Process immutable XML snapshots without touching AppKit objects."""
    return [process_track_element(ET.fromstring(xml_text), options) for xml_text in xml_snapshots]


def process_track_for_staged_load(
    track_element: ET.Element,
    options: ProcessingOptions,
    fingerprint: str | None,
) -> tuple[str, ProcessedTrack, dict]:
    """Calculate one cache miss without touching AppKit state."""
    resolved_fingerprint = fingerprint or processing_input_fingerprint(track_element)
    processed = process_track_element(
        track_element, options, fingerprint=resolved_fingerprint
    )
    return (
        resolved_fingerprint,
        processed,
        display_geometry_for_processed_track(processed),
    )


def prepare_gpx_sources_for_load(
    paths: list[Path], options: ProcessingOptions
) -> tuple[list[dict], list[tuple[Path, str]]]:
    """Parse source files and resolve cache matches away from AppKit."""
    prepared = []
    errors = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            document = load_gpx_document(path)
            lookup = load_metrics_cache(path, options)
            cached_tracks = match_cached_tracks(
                document.tracks,
                lookup,
                processing_input_fingerprint,
            )
            from track_time_estimation import missing_time_ranges_from_element
            timing_gaps = [missing_time_ranges_from_element(track) for track in document.tracks]
        except (OSError, ValueError) as exc:
            errors.append((path, str(exc)))
            continue
        prepared.append(
            {
                "path": path,
                "document": document,
                "cached_tracks": cached_tracks,
                "timing_gaps": timing_gaps,
            }
        )
    return prepared, errors


def resolved_gpx_document_name(document, path: Path, adventure_name: str = ""):
    """Choose a document name without letting a temporary placeholder escape."""
    source_name = str(document.document_name or Path(path).stem).strip()
    fallback_name = str(adventure_name or "").strip()
    placeholder_name = source_name.casefold() in {
        "untitled",
        "untitled adventure",
    }
    if fallback_name and (
        not document.document_name_is_explicit or placeholder_name
    ):
        return fallback_name, source_name != fallback_name
    return source_name or Path(path).stem, False


def write_saved_metrics_cache(
    path: Path,
    options: ProcessingOptions,
    track_elements: list[ET.Element],
    metrics: list[dict],
    fingerprints: list[str | None] | None = None,
    display_geometries: list[dict | None] | None = None,
) -> None:
    """Cache the exact elements written by Save without blocking AppKit."""
    entries = []
    for index, (element, track_metrics) in enumerate(zip(track_elements, metrics)):
        fingerprint = (
            fingerprints[index]
            if fingerprints is not None and index < len(fingerprints)
            else None
        ) or processing_input_fingerprint(element)
        display_geometry = (
            display_geometries[index]
            if display_geometries is not None and index < len(display_geometries)
            else None
        )
        if display_geometry is None:
            processed = process_track_element(element, options, fingerprint=fingerprint)
            display_geometry = display_geometry_for_processed_track(processed)
        entries.append(
            {
                "source_index": index,
                "fingerprint": fingerprint,
                "metrics": track_metrics,
                "display_geometry": display_geometry,
            }
        )
    write_metrics_cache(path, options, entries)

try:
    from gpx_tracks_table import (
        format_datetime_local,
        lonlat_to_web_mercator,
        render_track_plot,
    )
except ImportError:  # pragma: no cover - fallback for unusual embedding paths
    def format_datetime_local(dt_value):
        return "N/A" if dt_value is None else dt_value.astimezone().strftime("%d.%m.%Y %H:%M")

    def lonlat_to_web_mercator(lon, lat):
        limited_lat = max(min(lat, 85.05112878), -85.05112878)
        radius = 6378137.0
        x_coord = radius * math.radians(lon)
        y_coord = radius * math.log(math.tan(math.pi / 4 + math.radians(limited_lat) / 2))
        return x_coord, y_coord

    render_track_plot = None


PROGRAM_TITLE = "myCamino GPX Editor"
GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
NS = {"gpx": GPX_NAMESPACE}
ET.register_namespace("", GPX_NAMESPACE)
MYCAMINO_EXT_TAG = "mycamino_gpx_editor"
MYCAMINO_ORDER_NUMBER_ATTR = "order_number"
STANDALONE_SETTINGS_PATH = (
    Path.home() / "Library" / "Application Support" / "myCamino GPX Editor" / "settings.json"
)


def bundled_resource_path(filename: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / filename
    return Path(__file__).resolve().parent / filename

MIN_WINDOW_WIDTH = 1180.0
MIN_WINDOW_HEIGHT = 700.0
PADDING = 14.0
FIELD_HEIGHT = 26.0
BUTTON_HEIGHT = 28.0
STATUS_HEIGHT = 24.0
ROW_HEIGHT = 24.0
DRAG_TYPE = "myCaminoGPXEditorRows"
POINT_DRAG_TYPE = "myCaminoGPXEditorPointRows"
FILE_DRAG_TYPE = "NSFilenamesPboardType"
POINT_PASTEBOARD_TYPE = "org.mycamino.gpx-track-points"
UNTIMED_PROMPT_SUPPRESSION_KEY = "org.mycamino.gpx-editor.untimed-prompt-suppressed.v1"
UNTIMED_PROMPT_SUPPRESSION_LIMIT = 2000
ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD = (
    "untimed_prompt_suppressed_track_identities"
)
APP_CACHE_DIR = Path.home() / "Library" / "Caches" / "myCamino-GPXEditor"
TILE_CACHE_DIR = DEFAULT_TILE_CACHE_DIR
MPL_CACHE_DIR = APP_CACHE_DIR / "matplotlib"


def command_modifier_requests_media_selection(modifier_flags: int) -> bool:
    """Keep ordinary map drags available for panning."""
    return bool(int(modifier_flags) & NSEventModifierFlagCommand)


def plot_zoom_key_action(key: str, modifier_flags: int) -> str | None:
    """Share zoom shortcuts without consuming editing/menu key equivalents."""
    if modifier_flags & (NSEventModifierFlagCommand | NSEventModifierFlagOption | NSEventModifierFlagControl):
        return None
    shifted = bool(modifier_flags & NSEventModifierFlagShift)
    if key.casefold() == "z":
        return "track" if shifted or key == "Z" else "selection"
    if key == "A" or (key == "a" and shifted):
        return "all"
    return None


def height_interpolation_key(key: str, modifier_flags: int) -> bool:
    return not modifier_flags & (
        NSEventModifierFlagCommand | NSEventModifierFlagOption | NSEventModifierFlagControl
    ) and (key == "I" or (key == "i" and bool(modifier_flags & NSEventModifierFlagShift)))


def suppressed_untimed_track_identities(defaults) -> set[str]:
    """Read locally suppressed untimed-track identities defensively."""
    try:
        values = defaults.arrayForKey_(UNTIMED_PROMPT_SUPPRESSION_KEY) or []
    except Exception:
        return set()
    return {str(value) for value in values if str(value).strip()}


def remember_untimed_track_identities(defaults, identities: Iterable[str]) -> None:
    """Remember a bounded insertion-ordered set without modifying GPX data."""
    existing = list(defaults.arrayForKey_(UNTIMED_PROMPT_SUPPRESSION_KEY) or [])
    ordered = [str(value) for value in existing if str(value).strip()]
    seen = set(ordered)
    for identity in identities:
        text = str(identity).strip()
        if text and text not in seen:
            ordered.append(text)
            seen.add(text)
    ordered = ordered[-UNTIMED_PROMPT_SUPPRESSION_LIMIT:]
    defaults.setObject_forKey_(ordered, UNTIMED_PROMPT_SUPPRESSION_KEY)
    defaults.synchronize()


def untimed_tracks_requiring_prompt(tracks, suppressed: set[str]):
    """Return row, track, and stable identity for unsuppressed untimed tracks."""
    pending = []
    for row, track in enumerate(tracks):
        if not track.points():
            continue
        if timing_status_for_track(track.element) != "untimed":
            continue
        identity = raw_track_geometry_fingerprint(track.element)
        if identity not in suppressed:
            pending.append((row, track, identity))
    return pending


def apply_untimed_prompt_response(
    response: int,
    pending,
    defaults=None,
    *,
    remember_callback=None,
):
    """Apply a three-button prompt response and return the resulting action."""
    if int(response) == 1001:
        identities = [identity for _row, _track, identity in pending]
        if remember_callback is not None:
            remember_callback(identities)
        elif defaults is not None:
            remember_untimed_track_identities(defaults, identities)
        return "suppress", None
    if int(response) == 1000 and pending:
        return "edit", pending[0][0]
    return "later", None
RECOVERY_PATH = Path(tempfile.gettempdir()) / "myCamino-GPXEditor-recovery.gpx"
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE_DIR))


def _line_outcode(
    point: tuple[float, float],
    extent: dict[str, float],
) -> int:
    """Return a Cohen-Sutherland outcode for one point."""
    x_coord, y_coord = point
    code = 0
    if x_coord < extent["min_x"]:
        code |= 1
    elif x_coord > extent["max_x"]:
        code |= 2
    if y_coord < extent["min_y"]:
        code |= 4
    elif y_coord > extent["max_y"]:
        code |= 8
    return code


def _clip_line_to_extent(
    start: tuple[float, float],
    end: tuple[float, float],
    extent: dict[str, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Clip one line segment to an axis-aligned extent."""
    x0, y0 = start
    x1, y1 = end
    while True:
        start_code = _line_outcode((x0, y0), extent)
        end_code = _line_outcode((x1, y1), extent)
        if not (start_code | end_code):
            return (x0, y0), (x1, y1)
        if start_code & end_code:
            return None
        code = start_code or end_code
        if code & 8:
            if y1 == y0:
                return None
            x_coord = x0 + (x1 - x0) * (extent["max_y"] - y0) / (y1 - y0)
            y_coord = extent["max_y"]
        elif code & 4:
            if y1 == y0:
                return None
            x_coord = x0 + (x1 - x0) * (extent["min_y"] - y0) / (y1 - y0)
            y_coord = extent["min_y"]
        elif code & 2:
            if x1 == x0:
                return None
            y_coord = y0 + (y1 - y0) * (extent["max_x"] - x0) / (x1 - x0)
            x_coord = extent["max_x"]
        else:
            if x1 == x0:
                return None
            y_coord = y0 + (y1 - y0) * (extent["min_x"] - x0) / (x1 - x0)
            x_coord = extent["min_x"]
        if code == start_code:
            x0, y0 = x_coord, y_coord
        else:
            x1, y1 = x_coord, y_coord


def visible_simplified_polyline_runs(
    points: Iterable[tuple[float, float]],
    extent: dict[str, float],
    viewport_size: tuple[float, float],
    tolerance_pixels: float = 0.75,
) -> list[list[tuple[float, float]]]:
    """Clip a polyline to the viewport and remove sub-pixel vertices."""
    sequence = list(points)
    if len(sequence) < 2:
        return []
    raw_runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for start, end in zip(sequence, sequence[1:]):
        clipped = _clip_line_to_extent(start, end, extent)
        if clipped is None:
            if current:
                raw_runs.append(current)
                current = []
            continue
        clipped_start, clipped_end = clipped
        if not current:
            current = [clipped_start, clipped_end]
        elif math.isclose(current[-1][0], clipped_start[0], abs_tol=1e-9) and math.isclose(
            current[-1][1],
            clipped_start[1],
            abs_tol=1e-9,
        ):
            current.append(clipped_end)
        else:
            raw_runs.append(current)
            current = [clipped_start, clipped_end]
    if current:
        raw_runs.append(current)

    span_x = max(float(extent["max_x"]) - float(extent["min_x"]), 1e-12)
    span_y = max(float(extent["max_y"]) - float(extent["min_y"]), 1e-12)
    scale_x = max(float(viewport_size[0]), 1.0) / span_x
    scale_y = max(float(viewport_size[1]), 1.0) / span_y
    tolerance_squared = max(0.0, float(tolerance_pixels)) ** 2
    simplified_runs: list[list[tuple[float, float]]] = []
    for run in raw_runs:
        if len(run) <= 2 or tolerance_squared <= 0.0:
            simplified_runs.append(run)
            continue
        simplified = [run[0]]
        last_x, last_y = run[0]
        for point in run[1:-1]:
            delta_x = (point[0] - last_x) * scale_x
            delta_y = (point[1] - last_y) * scale_y
            if delta_x * delta_x + delta_y * delta_y >= tolerance_squared:
                simplified.append(point)
                last_x, last_y = point
        if run[-1] != simplified[-1]:
            simplified.append(run[-1])
        simplified_runs.append(simplified)
    return [run for run in simplified_runs if len(run) >= 2]


def sampled_polyline_points(points, maximum_points: int):
    """Retain endpoints while bounding coarse overview drawing work."""
    sequence = list(points)
    limit = max(2, int(maximum_points))
    if len(sequence) <= limit:
        return sequence
    indexes = {
        round(index * (len(sequence) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [sequence[index] for index in sorted(indexes)]


def nsstring(value: str) -> NSString:
    return NSString.stringWithString_(value)


def inspector_table_document_size(
    viewport_width: float,
    viewport_height: float,
    column_widths: Iterable[float],
    row_count: int,
    row_height: float,
    intercell_width: float = 0.0,
    intercell_height: float = 0.0,
) -> tuple[float, float]:
    """Return a table document size that preserves scrolling and fills its viewport."""
    widths = [max(0.0, float(width)) for width in column_widths]
    column_gaps = max(0, len(widths) - 1)
    row_gaps = max(0, int(row_count) - 1)
    content_width = sum(widths) + column_gaps * max(0.0, float(intercell_width))
    content_height = max(0, int(row_count)) * max(0.0, float(row_height))
    content_height += row_gaps * max(0.0, float(intercell_height))
    return max(float(viewport_width), content_width), max(float(viewport_height), content_height)


def elevation_distance_range_for_map_extent(
    rows: Iterable[dict],
    extent: dict[str, float],
) -> tuple[float, float] | None:
    """Return the cumulative-distance range occupied by map-visible points."""
    try:
        min_x = float(extent["min_x"])
        max_x = float(extent["max_x"])
        min_y = float(extent["min_y"])
        max_y = float(extent["max_y"])
    except (KeyError, TypeError, ValueError):
        return None
    distances: list[float] = []
    for row in rows:
        try:
            merc_x, merc_y = lonlat_to_web_mercator(
                float(row["longitude"]),
                float(row["latitude"]),
            )
            distance = float(row["distance"])
        except (KeyError, TypeError, ValueError):
            continue
        if min_x <= merc_x <= max_x and min_y <= merc_y <= max_y:
            distances.append(distance)
    if not distances:
        return None
    return min(distances), max(distances)


def elevation_rows_for_selected_tracks(rows: Iterable[dict], selected_track_numbers) -> list[dict]:
    """Use selected-track heights when available, otherwise retain all rows."""
    values = list(rows)
    selected = {int(number) for number in selected_track_numbers}
    matching = [
        row
        for row in values
        if getattr(row.get("track"), "nr", None) in selected
    ]
    return matching or values


def qname(local_name: str) -> str:
    return f"{{{GPX_NAMESPACE}}}{local_name}"


def show_alert(message: str, informative: str = "") -> None:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(message)
    if informative:
        alert.setInformativeText_(informative)
    alert.runModal()


def confirm(message: str, informative: str = "") -> bool:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(message)
    if informative:
        alert.setInformativeText_(informative)
    alert.addButtonWithTitle_("OK")
    alert.addButtonWithTitle_("Cancel")
    return alert.runModal() == 1000


def request_provider_api_key(provider: str, credential_id: str, *, remember_output_provider=True) -> bool:
    preference = load_map_provider_preference()
    if (
        read_provider_credential(provider, credential_id)
        and preference.get("preferred_output_provider") == provider
        and preference.get("credential_id", "default") == credential_id
        and preference.get("credential_verified", False)
    ):
        return True
    result = configure_hosted_provider(
        provider,
        credential_id,
        initial_key=read_provider_credential(provider, credential_id),
        remember_output_provider=remember_output_provider,
    )
    return bool(result and result.get("action") == "configured")


def file_panel_ok(result) -> bool:
    try:
        return int(result) in {1, 1000}
    except (TypeError, ValueError):
        return bool(result)


def nsimage_from_png_bytes(png_bytes: bytes) -> NSImage:
    data = NSData.alloc().initWithBytes_length_(png_bytes, len(png_bytes))
    return NSImage.alloc().initWithData_(data)


def iter_track_points(track_element: ET.Element) -> Iterable[ET.Element]:
    for segment in track_element.findall("gpx:trkseg", NS):
        yield from segment.findall("gpx:trkpt", NS)


def sanitize_track_points(track_element: ET.Element) -> dict[str, int]:
    """Retain valid geometry when optional or unusable timestamps are present."""
    removed = sanitize_imported_track(track_element)
    return {
        "coordinates": removed["coordinates"],
        "timestamps": removed["timestamps"],
        "out_of_order": removed["out_of_order"],
        "missing_timestamps": removed["missing_timestamps"],
    }


def first_segment(track_element: ET.Element) -> ET.Element:
    segment = track_element.find("gpx:trkseg", NS)
    if segment is None:
        segment = ET.SubElement(track_element, qname("trkseg"))
    return segment


def get_or_create_child(parent: ET.Element, local_name: str, after: set[str] | None = None) -> ET.Element:
    existing = parent.find(f"gpx:{local_name}", NS)
    if existing is not None:
        return existing
    child = ET.Element(qname(local_name))
    if after:
        insert_at = 0
        for index, current in enumerate(list(parent)):
            if current.tag in after:
                insert_at = index + 1
        parent.insert(insert_at, child)
    else:
        parent.insert(0, child)
    return child


def get_or_create_track_name(track_element: ET.Element) -> ET.Element:
    return get_or_create_child(track_element, "name")


def get_or_create_track_time(track_element: ET.Element) -> ET.Element:
    return get_or_create_child(
        track_element,
        "time",
        {qname("name"), qname("cmt"), qname("desc"), qname("src"), qname("link"), qname("number"), qname("type")},
    )


def get_or_create_point_time(point: ET.Element) -> ET.Element:
    time_element = point.find("gpx:time", NS)
    if time_element is not None:
        return time_element
    insert_at = 0
    for index, child in enumerate(list(point)):
        if child.tag in {qname("ele"), qname("magvar"), qname("geoidheight")}:
            insert_at = index + 1
    time_element = ET.Element(qname("time"))
    point.insert(insert_at, time_element)
    return time_element


def get_or_create_track_extensions(track_element: ET.Element) -> ET.Element:
    extensions = track_element.find("gpx:extensions", NS)
    if extensions is not None:
        return extensions
    extensions = ET.Element(qname("extensions"))
    insert_at = 0
    for index, child in enumerate(list(track_element)):
        if child.tag != qname("trkseg"):
            insert_at = index + 1
    track_element.insert(insert_at, extensions)
    return extensions


def find_mycamino_track_extension(track_element: ET.Element) -> ET.Element | None:
    extensions = track_element.find("gpx:extensions", NS)
    if extensions is None:
        return None
    for candidate in list(extensions):
        if candidate.tag == MYCAMINO_EXT_TAG or candidate.tag.endswith(
            "}" + MYCAMINO_EXT_TAG
        ):
            return candidate
    return None


def get_or_create_mycamino_track_extension(
    track_element: ET.Element,
) -> ET.Element:
    node = find_mycamino_track_extension(track_element)
    if node is not None:
        return node
    return ET.SubElement(get_or_create_track_extensions(track_element), MYCAMINO_EXT_TAG)


def stored_track_order_number(track_element: ET.Element) -> int | None:
    node = find_mycamino_track_extension(track_element)
    if node is None:
        return None
    try:
        value = int(str(node.get(MYCAMINO_ORDER_NUMBER_ATTR) or "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def has_stored_track_order_number(track_element: ET.Element) -> bool:
    node = find_mycamino_track_extension(track_element)
    return bool(node is not None and MYCAMINO_ORDER_NUMBER_ATTR in node.attrib)


def format_gpx_time(dt_value: datetime) -> str:
    return format_gpx_timestamp(dt_value)


def format_inspector_elevation(value: str) -> str:
    """Format an elevation for display without changing the GPX value."""
    text = str(value or "").strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return f"{number:.1f}" if math.isfinite(number) else text


def format_inspector_timestamp(value: str) -> str:
    """Render an ISO GPX timestamp as local-order text while retaining its zone marker."""
    text = str(value or "").strip()
    if not text:
        return ""
    zone_match = re.search(r"(Z|[+-]\d{2}:?\d{2})$", text, flags=re.IGNORECASE)
    zone_marker = zone_match.group(1) if zone_match else ""
    iso_text = text[:-1] + "+00:00" if text.upper().endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return text
    rounded = parsed + timedelta(microseconds=50_000)
    rounded = rounded.replace(microsecond=(rounded.microsecond // 100_000) * 100_000)
    seconds = rounded.second + rounded.microsecond / 1_000_000.0
    return f"{rounded.strftime('%d.%m.%Y %H:%M')}:{seconds:04.1f}{zone_marker}"


def normalize_inspector_timestamp_edit(value: str) -> str:
    """Convert the inspector's human-readable timestamp back to GPX ISO form."""
    text = str(value or "").strip()
    match = re.fullmatch(
        r"(\d{2})\.(\d{2})\.(\d{4})\s+"
        r"(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)"
        r"(Z|[+-]\d{2}:?\d{2})?",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return text
    day, month, year, hour, minute, seconds, zone = match.groups()
    normalized = f"{year}-{month}-{day}T{hour}:{minute}:{seconds}{zone or ''}"
    iso_text = normalized[:-1] + "+00:00" if normalized.upper().endswith("Z") else normalized
    try:
        datetime.fromisoformat(iso_text)
    except ValueError:
        return text
    return normalized


def compact_xy_status(value: str) -> str:
    """Keep processing distinctions visible in a narrow XY status column."""
    text = str(value or "").strip()
    return {
        "retained": "Used",
        "smoothing only": "Smooth",
        "quality accepted": "Valid",
        "invalid coordinate": "Invalid",
        "H error": "H err",
    }.get(text, text)


def compact_elevation_status(value: str) -> str:
    """Keep elevation quality and interpolation reasons in compact form."""
    text = str(value or "").strip()
    direct = {
        "retained": "Used",
        "not retained": "Not used",
        "XY rejected": "XY reject",
        "missing": "Missing",
        "V error": "V err",
    }
    if text in direct:
        return direct[text]
    match = re.fullmatch(r"interpolated \((.+)\)", text)
    if match is not None:
        reason = {
            "missing": "miss",
            "V error": "V err",
        }.get(match.group(1), match.group(1))
        return f"Interp {reason}"
    return text


def parse_user_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text or text.upper() == "N/A":
        return None
    formats = ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d-%H:%M")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).astimezone()
        except ValueError:
            pass
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def format_duration(duration: timedelta | None, allow_days: bool = False) -> str:
    if duration is None:
        return "N/A"
    total_minutes = max(0, int(duration.total_seconds() // 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if allow_days and days:
        return f"{days:02d}d{hours:02d}h{minutes:02d}m"
    return f"{hours + days * 24:02d}:{minutes:02d}"


def format_total_duration(duration: timedelta | None) -> str:
    if duration is None:
        return "N/A"
    total_minutes = max(0, int(duration.total_seconds() // 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days:02d}d{hours:02d}h"
    return f"{hours:02d}:{minutes:02d}"


def format_speed(speed: float | None) -> str:
    return "N/A" if speed is None else f"{speed:.1f} km/h"


def parse_track_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for part in [part.strip() for part in text.split(",") if part.strip()]:
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                start, end = end, start
            candidates = range(start, end + 1)
        elif re.fullmatch(r"\d+", part):
            candidates = [int(part)]
        else:
            continue
        for number in candidates:
            if number not in seen:
                seen.add(number)
                numbers.append(number)
    return numbers


def compress_track_numbers(numbers: list[int]) -> str:
    if not numbers:
        return ""
    result: list[str] = []
    index = 0
    while index < len(numbers):
        start = numbers[index]
        end = start
        while index + 1 < len(numbers) and numbers[index + 1] == end + 1:
            index += 1
            end = numbers[index]
        result.append(str(start) if start == end else f"{start}-{end}")
        index += 1
    return ", ".join(result)


@dataclass
class PointInfo:
    element: ET.Element
    lat: float
    lon: float
    ele: float | None
    time: datetime | None
    segment_index: int = 0


@dataclass(frozen=True)
class CachedDisplayPoint:
    lat: float
    lon: float
    source_index: int


@dataclass(frozen=True)
class CachedDisplaySegment:
    points: tuple[CachedDisplayPoint, ...]


@dataclass(frozen=True)
class CachedDisplayTrack:
    segments: tuple[CachedDisplaySegment, ...]
    points: tuple[CachedDisplayPoint, ...]


@dataclass
class TrackRecord:
    nr: int
    element: ET.Element
    source_file: str = ""
    source_track_index: int = 0
    metrics: dict = field(default_factory=dict)
    metrics_dirty: bool = True
    processed: ProcessedTrack | None = field(default=None, repr=False)
    processed_signature: tuple | None = field(default=None, repr=False)
    processed_fingerprint: str | None = field(default=None, repr=False)
    display_geometry: dict | None = field(default=None, repr=False)
    display_geometry_cache: dict[int, CachedDisplayTrack] = field(
        default_factory=dict, repr=False
    )
    point_range_metrics_cache: OrderedDict = field(default_factory=OrderedDict, repr=False)

    def __setattr__(self, name, value):
        if name == "nr" and "nr" in self.__dict__ and value != self.nr:
            raise AttributeError("A track's internal ID cannot be changed.")
        super().__setattr__(name, value)

    def __post_init__(self):
        if not isinstance(self.nr, int) or self.nr < 1:
            raise ValueError("Internal track IDs must be positive whole numbers.")
        number = track_number(self.element, self.nr)
        stage = stage_number(self.element, number if number is not None else self.nr)
        get_or_create_mycamino_track_extension(self.element).set("internal_id", str(self.nr))
        self.set_order_number(number)
        self.set_stage_number(stage)

    @property
    def internal_id(self):
        return self.nr

    @property
    def display_label(self):
        return f"Track #{self.track_number}" if self.track_number is not None else "Unnumbered track"

    @property
    def name(self) -> str:
        return self.element.findtext("gpx:name", default="", namespaces=NS).strip() or "Unnamed track"

    @property
    def hidden(self) -> bool:
        for candidate in self.element.findall("gpx:extensions/*", NS):
            if candidate.tag == MYCAMINO_EXT_TAG or candidate.tag.endswith("}" + MYCAMINO_EXT_TAG):
                value = (candidate.get("hidden") or candidate.findtext("hidden", default="") or candidate.text or "").strip().casefold()
                return value in {"1", "true", "yes", "hidden"}
        return False

    def set_hidden(self, hidden: bool):
        extensions = get_or_create_track_extensions(self.element)
        node = None
        for candidate in list(extensions):
            if candidate.tag == MYCAMINO_EXT_TAG or candidate.tag.endswith("}" + MYCAMINO_EXT_TAG):
                node = candidate
                break
        if node is None:
            node = ET.SubElement(extensions, MYCAMINO_EXT_TAG)
        node.set("hidden", "yes" if hidden else "no")

    @property
    def stored_order_number(self) -> int | None:
        return stored_track_order_number(self.element)

    def set_order_number(self, number: int | None):
        node = get_or_create_mycamino_track_extension(self.element)
        if number is not None and int(number) < 1:
            raise ValueError("Track Number must be positive or unassigned.")
        node.set(MYCAMINO_ORDER_NUMBER_ATTR, str(int(number)) if number is not None else "")
        node.attrib.pop("track_number_unassigned", None)

    @property
    def track_number(self):
        return track_number(self.element, self.nr)

    def set_track_number_assigned(self, assigned):
        self.set_order_number((self.track_number or self.nr) if assigned else None)

    @property
    def stage_number(self):
        return stage_number(self.element, self.nr)

    def set_stage_number(self, number):
        if number is None or str(number).strip() == "":
            get_or_create_mycamino_track_extension(self.element).set("stage_number", "")
            return
        if int(number) < 1:
            raise ValueError("Stage must be a positive number.")
        get_or_create_mycamino_track_extension(self.element).set("stage_number", str(int(number)))

    @property
    def track_type(self):
        return element_track_type(self.element)

    def set_track_type(self, value):
        node = self.element.find(qname("type"))
        if node is None:
            node = ET.Element(qname("type"))
            index = next((i for i, child in enumerate(self.element)
                          if child.tag in {qname("extensions"), qname("trkseg")}), len(self.element))
            self.element.insert(index, node)
        node.text = normalize_track_type(value)

    @property
    def time(self) -> datetime | None:
        track_time = parse_time(self.element.findtext("gpx:time", default="", namespaces=NS))
        if track_time is not None:
            return track_time
        for point in self.points():
            if point.time is not None:
                return point.time
        return None

    def points(self) -> list[PointInfo]:
        points: list[PointInfo] = []
        for segment_index, segment in enumerate(self.element.findall("gpx:trkseg", NS)):
            for point in segment.findall("gpx:trkpt", NS):
                try:
                    lat = float(point.attrib["lat"])
                    lon = float(point.attrib["lon"])
                except (KeyError, ValueError):
                    continue
                ele = None
                ele_text = point.findtext("gpx:ele", default="", namespaces=NS)
                if ele_text:
                    try:
                        ele = float(ele_text)
                    except ValueError:
                        ele = None
                points.append(
                    PointInfo(
                        point,
                        lat,
                        lon,
                        ele,
                        parse_time(point.findtext("gpx:time", default="", namespaces=NS)),
                        segment_index,
                    )
                )
        return points


@dataclass(frozen=True)
class VisibilityUndoEntry:
    before: dict[int, bool]
    after: dict[int, bool]


@dataclass(frozen=True)
class TrackMetadataUndoEntry:
    # Compact deltas are keyed by immutable IDs, including across structural Undo.
    values: tuple
    kind: MutationKind = MutationKind.METADATA


@dataclass(frozen=True)
class TrackElementsUndoEntry:
    elements: dict[int, str]
    kind: MutationKind


@dataclass(frozen=True)
class TrackOrderUndoEntry:
    order: tuple[int, ...]


@dataclass(frozen=True)
class TimestampUndoEntry:
    values: dict
    expected: dict | None = None
    geometry: dict | None = None


@dataclass(frozen=True)
class AnchorUndoEntry:
    anchor: tuple[float, float] | None


class SummaryTrackRowView(NSTableRowView):
    """Table row that separates the aggregate values from track rows."""

    def drawRect_(self, dirty_rect):
        objc.super(SummaryTrackRowView, self).drawRect_(dirty_rect)
        bounds = self.bounds()
        NSColor.separatorColor().setStroke()
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(1.0)
        path.moveToPoint_(NSMakePoint(bounds.origin.x, bounds.size.height - 0.5))
        path.lineToPoint_(NSMakePoint(bounds.size.width, bounds.size.height - 0.5))
        path.stroke()


class SegmentBreakPointRowView(NSTableRowView):
    """Waypoint row with a subtle separator at a GPX segment boundary."""

    def initWithSegmentBreak_(self, segment_break):
        self = objc.super(SegmentBreakPointRowView, self).initWithFrame_(NSZeroRect)
        if self is not None:
            self.segment_break = bool(segment_break)
        return self

    def drawRect_(self, dirty_rect):
        objc.super(SegmentBreakPointRowView, self).drawRect_(dirty_rect)
        if not getattr(self, "segment_break", False):
            return
        bounds = self.bounds()
        NSColor.separatorColor().setStroke()
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(1.0)
        path.moveToPoint_(NSMakePoint(bounds.origin.x, bounds.size.height - 0.5))
        path.lineToPoint_(NSMakePoint(bounds.size.width, bounds.size.height - 0.5))
        path.stroke()


class EditorTableDataSource(NSObject):
    def initWithController_(self, controller):
        self = objc.super(EditorTableDataSource, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def numberOfRowsInTableView_(self, _table_view):
        return len(self.controller.table_rows)

    def tableView_objectValueForTableColumn_row_(self, _table_view, table_column, row):
        if row < 0 or row >= len(self.controller.table_rows):
            return ""
        identifier = str(table_column.identifier())
        value = self.controller.table_rows[row].get(identifier, "")
        if identifier == "show" and row < len(self.controller.tracks):
            return NSControlStateValueOn if value == "yes" else NSControlStateValueOff
        return value

    def tableView_setObjectValue_forTableColumn_row_(self, _table_view, value, table_column, row):
        self.controller.commit_table_value(
            row, str(table_column.identifier()), str(value)
        )

    def tableView_shouldSelectRow_(self, _table_view, row):
        if row < 0 or row >= len(self.controller.tracks):
            return False
        try:
            clicked_column = int(_table_view.clickedColumn())
            if clicked_column >= 0:
                identifier = str(
                    _table_view.tableColumns()[clicked_column].identifier()
                )
                if identifier == "show":
                    return False
        except (AttributeError, IndexError, TypeError, ValueError):
            pass
        return True

    def tableView_willDisplayCell_forTableColumn_row_(
        self, table_view, cell, table_column, row
    ):
        if str(table_column.identifier()) == "show":
            # NSButtonCell does not implement the text-color API used by the
            # text columns. Sending it setTextColor_ aborts AppKit rather than
            # producing a recoverable Python exception.
            cell.setEnabled_(
                row < len(self.controller.tracks)
                and self.controller.track_mutations_enabled
            )
            return
        base_font = self.controller.table_font(12)
        if row == len(self.controller.tracks):
            font = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
                base_font, NSBoldFontMask
            )
            cell.setFont_(font or NSFont.boldSystemFontOfSize_(12))
            cell.setTextColor_(NSColor.controlTextColor())
            return
        cell.setFont_(base_font)
        if 0 <= row < len(self.controller.tracks) and self.controller.tracks[row].hidden:
            if table_view.isRowSelected_(row):
                selected_color = getattr(NSColor, "alternateSelectedControlTextColor", None)
                cell.setTextColor_(
                    selected_color() if selected_color is not None else NSColor.controlTextColor()
                )
            else:
                cell.setTextColor_(NSColor.disabledControlTextColor())
        else:
            cell.setTextColor_(NSColor.controlTextColor())

    def tableView_rowViewForRow_(self, _table_view, row):
        if row == len(self.controller.tracks):
            return SummaryTrackRowView.alloc().initWithFrame_(NSZeroRect)
        return None

    def tableView_shouldEditTableColumn_row_(self, _table_view, table_column, row):
        identifier = str(table_column.identifier())
        if not self.controller.is_editable_cell(row, identifier):
            if not self.controller.track_mutations_enabled:
                self.controller.ensure_track_mutations_available()
            return False
        self.controller.begin_table_edit(row, identifier)
        return True

    def control_textView_doCommandBySelector_(self, _control, _text_view, selector):
        if str(selector) == "cancelOperation:":
            self.controller.cancel_active_table_edit()
            return True
        return False

    def tableViewSelectionDidChange_(self, _notification):
        self.controller.table_selection_changed()

    def tableView_sortDescriptorsDidChange_(self, _table_view, _old_descriptors):
        if getattr(self.controller, "suppress_sort_descriptor_change", False):
            return
        descriptors = list(_table_view.sortDescriptors())
        if not descriptors:
            return
        descriptor = descriptors[0]
        self.controller.sort_by_column(str(descriptor.key()), bool(descriptor.ascending()), update_header=False, source="sortDescriptorsDidChange")
        self.controller.pending_header_column = None

    def tableView_didClickTableColumn_(self, _table_view, table_column):
        column = str(table_column.identifier())
        if table_column.sortDescriptorPrototype() is None or self.controller.pending_header_column == column:
            self.controller.pending_header_column = None
            self.controller.sort_by_column(column, source="didClickTableColumn")

    def tableView_mouseDownInHeaderOfTableColumn_(self, _table_view, table_column):
        self.controller.pending_header_column = str(table_column.identifier())

    def tableView_writeRowsWithIndexes_toPasteboard_(self, _table_view, row_indexes, pasteboard):
        if not self.controller.track_mutations_enabled:
            return False
        rows = [
            index
            for index in range(row_indexes.firstIndex(), row_indexes.lastIndex() + 1)
            if row_indexes.containsIndex_(index)
            and index < len(self.controller.tracks)
        ]
        if not rows:
            return False
        pasteboard.declareTypes_owner_([DRAG_TYPE], None)
        pasteboard.setString_forType_(",".join(str(row) for row in rows), DRAG_TYPE)
        return True

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, _table_view, _info, row, _operation):
        if not self.controller.track_mutations_enabled:
            return 0
        if _info.draggingPasteboard().propertyListForType_(FILE_DRAG_TYPE):
            return 1 if 0 <= row < len(self.controller.tracks) else 0
        return 1 if 0 <= row <= len(self.controller.tracks) else 0

    def tableView_acceptDrop_row_dropOperation_(self, _table_view, info, row, _operation):
        if hasattr(self.controller, "ensure_track_mutations_available") and not self.controller.ensure_track_mutations_available():
            return False
        file_paths = info.draggingPasteboard().propertyListForType_(FILE_DRAG_TYPE)
        if file_paths:
            resolved_paths = [
                Path(str(path)).expanduser().resolve(strict=False)
                for path in file_paths
            ]
            gpx_paths = [
                path for path in resolved_paths if path.suffix.casefold() == ".gpx"
            ]
            if gpx_paths:
                self.controller.performSelector_withObject_afterDelay_(
                    "loadDroppedGPXPaths:",
                    [str(path) for path in gpx_paths],
                    0.0,
                )
                return True
            if not 0 <= row < len(self.controller.tracks):
                return False
            self.controller.selected_nrs = [self.controller.tracks[row].nr]
            self.controller.highlight_selected_rows()
            self.controller.update_selection_field()
            self.controller.media_context["_pending_media_paths"] = [
                str(path) for path in resolved_paths
            ]
            self.controller.performSelector_withObject_afterDelay_(
                "addTracksFromPhotos:", None, 0.0
            )
            return True
        text = str(info.draggingPasteboard().stringForType_(DRAG_TYPE) or "")
        indexes = [int(part) for part in text.split(",") if part.isdigit()]
        if not indexes:
            return False
        self.controller.move_rows(indexes, row)
        return True


def context_track_row_selection(
    clicked_row: int,
    selected_rows: Iterable[int],
    track_count: int,
) -> list[int]:
    """Return the standard macOS selection for a track-table context click."""
    if clicked_row < 0 or clicked_row >= track_count:
        return []
    selected = sorted(
        {int(row) for row in selected_rows if 0 <= int(row) < track_count}
    )
    return selected if clicked_row in selected else [clicked_row]


def reordered_selected_items(items: list, selected_indexes: Iterable[int], action: str) -> list:
    """Reorder selected groups while preserving their internal relative order."""
    result = list(items)
    selected = [False] * len(result)
    for index in selected_indexes:
        if 0 <= int(index) < len(result):
            selected[int(index)] = True
    if action == "up":
        for index in range(1, len(result)):
            if selected[index] and not selected[index - 1]:
                result[index - 1], result[index] = result[index], result[index - 1]
                selected[index - 1], selected[index] = True, False
    elif action == "down":
        for index in range(len(result) - 2, -1, -1):
            if selected[index] and not selected[index + 1]:
                result[index], result[index + 1] = result[index + 1], result[index]
                selected[index], selected[index + 1] = False, True
    elif action == "top":
        moving = [item for index, item in enumerate(result) if selected[index]]
        result = moving + [item for index, item in enumerate(result) if not selected[index]]
    elif action == "bottom":
        moving = [item for index, item in enumerate(result) if selected[index]]
        result = [item for index, item in enumerate(result) if not selected[index]] + moving
    else:
        raise ValueError(f"Unknown selected-track move action: {action}")
    return result


def unique_track_copy_name(name: str, existing_names: Iterable[str]) -> str:
    """Return a stable case-insensitive copy name for one duplicated track."""
    existing = {str(item).casefold() for item in existing_names}
    candidate = f"{name} (copy)"
    if candidate.casefold() not in existing:
        return candidate
    suffix = 2
    while True:
        candidate = f"{name} (copy {suffix})"
        if candidate.casefold() not in existing:
            return candidate
        suffix += 1


def duplicate_track_records(
    tracks: list[TrackRecord], selected_indexes: Iterable[int], next_nr: int
) -> tuple[list[TrackRecord], list[TrackRecord], int]:
    """Return tracks with one deep-copy block inserted after the last selection."""
    indexes = sorted(
        {int(index) for index in selected_indexes if 0 <= int(index) < len(tracks)}
    )
    if not indexes:
        return list(tracks), [], next_nr
    next_nr = max(next_nr, max((track.internal_id for track in tracks), default=0) + 1)
    existing_names = [track.name for track in tracks]
    visible_number = max((track.track_number or 0 for track in tracks), default=0) + 1
    duplicates = []
    for index in indexes:
        source = tracks[index]
        element = copy.deepcopy(source.element)
        duplicate_name = unique_track_copy_name(source.name, existing_names)
        get_or_create_track_name(element).text = duplicate_name
        existing_names.append(duplicate_name)
        duplicate = TrackRecord(next_nr, element, source.source_file)
        duplicate.set_order_number(visible_number if source.track_number is not None else None)
        visible_number += source.track_number is not None
        duplicates.append(duplicate)
        next_nr += 1
    insert_at = max(indexes) + 1
    result = list(tracks)
    result[insert_at:insert_at] = duplicates
    return result, duplicates, next_nr


def renumber_track_records(tracks: list[TrackRecord]) -> int:
    """Assign visible 1-based numbers without changing internal identities."""
    for number, track in enumerate(tracks, start=1):
        track.set_order_number(number)
    return max((track.internal_id for track in tracks), default=0) + 1


def visible_track_count(tracks: Iterable[TrackRecord]) -> int:
    return sum(1 for track in tracks if not track.hidden)


def selected_tracks_export_filename(tracks: Iterable[TrackRecord]) -> str:
    """Return a readable default filename for a selected-track export."""
    selected = list(tracks)
    if not selected:
        return "Selected tracks.gpx"
    suffix = f" - {len(selected)} tracks" if len(selected) > 1 else ""
    name = re.sub(r'[\\/:*?"<>|]+', "_", selected[0].name).strip(" .")
    return f"{name or 'Selected track'}{suffix}.gpx"


def _canonical_metadata_copy(element: ET.Element) -> ET.Element:
    """Copy GPX 1.0/unnamespaced metadata into the GPX 1.1 namespace."""
    local_name = element.tag.rsplit("}", 1)[-1]
    copied = ET.Element(qname(local_name), dict(element.attrib))
    copied.text = element.text
    copied.tail = element.tail
    for child in element:
        if local_name == "extensions":
            copied.append(copy.deepcopy(child))
        else:
            copied.append(_canonical_metadata_copy(child))
    return copied


def _selected_tracks_document_template(
    selected: list[TrackRecord], project_name: str
) -> tuple[ET.Element, ET.Element | None]:
    """Preserve document metadata from the first selected track's source GPX."""
    source = Path(selected[0].source_file).expanduser() if selected and selected[0].source_file else None
    source_root = None
    if source is not None and source.is_file():
        try:
            candidate = ET.parse(source).getroot()
            if candidate.tag.rsplit("}", 1)[-1] == "gpx":
                source_root = candidate
        except (ET.ParseError, OSError):
            source_root = None

    attributes = {"version": "1.1", "creator": "myCamino GPX Editor"}
    metadata = None
    extensions = None
    if source_root is not None:
        attributes.update(source_root.attrib)
        attributes["version"] = "1.1"
        for child in source_root:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name == "metadata" and metadata is None:
                metadata = _canonical_metadata_copy(child)
            elif local_name == "extensions" and extensions is None:
                extensions = _canonical_metadata_copy(child)

    root = ET.Element(qname("gpx"), attributes)
    if metadata is not None:
        root.append(metadata)
    elif project_name:
        metadata = ET.SubElement(root, qname("metadata"))
        ET.SubElement(metadata, qname("name")).text = project_name
    return root, extensions


def build_selected_tracks_root(
    tracks: Iterable[TrackRecord],
    output_path: Path,
    *,
    project_name: str = "",
    anchor: tuple[float, float] | None = None,
) -> ET.Element:
    """Build an independent GPX document from selected tracks in display order."""
    selected = list(tracks)
    root, extensions = _selected_tracks_document_template(selected, project_name)
    for number, track in enumerate(selected, start=1):
        output_track = copy.deepcopy(track.element)
        get_or_create_mycamino_track_extension(output_track).set(
            MYCAMINO_ORDER_NUMBER_ATTR, str(number)
        )
        if track.track_number is None:
            get_or_create_mycamino_track_extension(output_track).set(MYCAMINO_ORDER_NUMBER_ATTR, "")
        get_or_create_mycamino_track_extension(output_track).set("internal_id", str(number))
        get_or_create_mycamino_track_extension(output_track).set("stage_number", str(track.stage_number or ""))
        rewrite_local_media_links(
            output_track,
            source_directory=Path(track.source_file).parent if track.source_file else None,
            output_directory=Path(output_path).parent,
        )
        root.append(output_track)
    if extensions is not None or anchor is not None:
        if extensions is None:
            extensions = ET.Element(qname("extensions"))
        if anchor is not None:
            anchor_node = next(
                (
                    child
                    for child in extensions
                    if child.tag.rsplit("}", 1)[-1] == "mycamino_anchor"
                ),
                None,
            )
            if anchor_node is None:
                anchor_node = ET.SubElement(extensions, "mycamino_anchor")
            anchor_node.set("lat", f"{anchor[0]:.8f}")
            anchor_node.set("lon", f"{anchor[1]:.8f}")
        root.append(extensions)
    store_document_counter(root, len(selected) + 1)
    return root


def write_selected_tracks_gpx(
    tracks: Iterable[TrackRecord],
    output_path: Path,
    *,
    project_name: str = "",
    anchor: tuple[float, float] | None = None,
) -> int:
    """Atomically export selected tracks without mutating their source records."""
    selected = list(tracks)
    if not selected:
        raise ValueError("No tracks selected")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root = build_selected_tracks_root(
        selected,
        output_path,
        project_name=project_name,
        anchor=anchor,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            ET.ElementTree(root).write(handle, encoding="utf-8", xml_declaration=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(selected)


TRACK_SORT_CRITERIA = (
    ("date", "Date & Time"),
    ("nr", "Track Number"),
    ("stage", "Stage"),
    ("track_id", "Track ID"),
    ("type", "Type"),
    ("name", "Name"),
    ("length", "Length"),
    ("duration", "Duration"),
    ("distance", "Distance from Anchor"),
    ("speed", "Average Speed"),
    ("moving_speed", "Moving Average Speed"),
    ("ascent", "Ascent"),
    ("descent", "Descent"),
    ("npoints", "Number of Points"),
)


class EditorTableView(NSTableView):
    def menuForEvent_(self, event):
        controller = getattr(self, "controller", None)
        if controller is None:
            return None
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        clicked_row = int(self.rowAtPoint_(point))
        selected_rows = controller.selected_track_row_indexes()
        context_rows = context_track_row_selection(
            clicked_row, selected_rows, len(controller.tracks)
        )
        if not context_rows:
            return None
        if context_rows != selected_rows:
            indexes = objc.lookUpClass("NSMutableIndexSet").alloc().init()
            for row in context_rows:
                indexes.addIndex_(row)
            self.selectRowIndexes_byExtendingSelection_(indexes, False)
            controller.table_selection_changed()
        return controller.build_track_context_menu()

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        if (
            key.casefold() == "i"
            and event.modifierFlags() & NSEventModifierFlagCommand
            and getattr(self, "controller", None) is not None
        ):
            selected = self.controller.selected_tracks()
            if len(selected) == 1 and not selected[0].points():
                self.controller.addPointsFromClipboard_(None)
                return
        if key in {"\x7f", "\uf728"} and getattr(self, "controller", None) is not None:
            if not self.controller.ensure_track_mutations_available():
                return
            self.controller.delete_selected_tracks()
            return
        objc.super(EditorTableView, self).keyDown_(event)


class PlotView(NSView):
    def initWithController_mode_plotInfo_(self, controller, mode, plot_info):
        self = objc.super(PlotView, self).initWithFrame_(NSMakeRect(0, 0, 800, 600))
        if self is None:
            return None
        self.controller = controller
        self.mode = mode
        self.plot_info = plot_info or {}
        self.track_index = 0
        self.zoom = 1.0
        self.cursor = None
        self.marker = None
        self.show_info = True
        self.info_overlay_dismissed = False
        self.info_overlay_close_rect = None
        self.info_overlay_rect = None
        self.info_overlay_origin = None
        self.info_overlay_dragging = False
        self.info_overlay_drag_offset = None
        self.info_overlay_mouse_active = False
        self.context_overlay_coordinate = None
        self.pointer_coordinate = None
        self.pointer_elevation = None
        self.show_help = False
        self.show_track_numbers = False
        self.show_endpoint_markers = True
        self.show_media_markers = True
        self.show_all_photo_thumbnails = False
        self.hovered_media_point = None
        self.active_media_path = None
        self.photo_thumbnail_hit_targets = []
        self.clicked_media_target = None
        self.clicked_workspace_media_target = None
        self.workspace_media_selection_active = False
        self.workspace_track_selection_consumed = False
        self.photo_thumbnail_cache = ThumbnailMemoryCache()
        self.photo_thumbnail_futures = {}
        self.photo_thumbnail_generation = 0
        self.photo_thumbnail_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mycamino-gpx-photo-preview",
        )
        self.transient_help_until = time.monotonic() + 5.0
        self.transient_help_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            5.1,
            self,
            "clearTransientHelp:",
            None,
            False,
        )
        self.inspector = None
        self.rendering_map = False
        self.last_viewport_signature = None
        self.long_press_timer = None
        self.long_press_origin = None
        self.long_press_target = None
        self.dragged_point_index = None
        self.dragged_point_track = None
        self.dragged_segment_rows = []
        self.dragged_segment_elements = []
        self.dragged_segment_original_coordinates = []
        self.dragged_segment_origin_mercator = None
        self.drag_original_xml = None
        self.drag_inserted_point = False
        self.drag_undo_owner = None
        self.mouse_down_started_at = None
        self.mouse_down_click_count = 0
        self.pan_active = False
        self.pan_origin = None
        self.pan_start_extent = None
        self.pan_restore_extent = None
        self.last_gesture_location = None
        self.interactive_tiles_active = False
        self.plot_closed = False
        self.interactive_tiles = OrderedDict()
        self.interactive_tile_store_provider_key = ""
        self.interactive_tile_target_keys = set()
        self.interactive_tile_generation = 0
        self.interactive_tile_futures = {}
        self.interactive_tile_timer = None
        self.pinch_finish_timer = None
        self.wheel_finish_timer = None
        self.pinch_start_tile_zoom = None
        self.pinch_accumulated_scale = 1.0
        self.interactive_tile_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mycamino-map-tile",
        )
        self.interactive_provider_failed_generation = None
        self.interactive_world_ocean_samples = []
        self.interactive_world_ocean_provider_key = ""
        self.context_target = None
        self.context_track_target = None
        self.context_location = None
        self.adventure_workspace_delegate = None
        self.reparenting_for_adventure_workspace = False
        self.initial_plot_info = self.clone_plot_info(self.plot_info)
        self.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        if self.mode == "track":
            self.registerForDraggedTypes_([FILE_DRAG_TYPE])
        if self.plot_info.get("image") is None and (self.plot_info.get("metadata") or {}).get("extent_mercator"):
            self.interactive_tiles_active = True
            self.schedule_interactive_tiles()
        return self

    def acceptsFirstResponder(self):
        return True

    def drawRect_(self, _rect):
        if self.plot_closed:
            return
        bounds = self.bounds()
        workspace = self.adventure_workspace_delegate
        NSColor.windowBackgroundColor().setFill()
        NSBezierPath.fillRect_(bounds)
        self.draw_plot_image(bounds)
        tracks = self.display_tracks()
        processed_tracks = self.display_geometry_for_tracks(tracks)
        if not any(processed.points for _track, processed in processed_tracks):
            workspace = self.adventure_workspace_delegate
            if workspace is None:
                self.draw_text("No track points available.", 20, bounds.size.height - 40, 16)
            if self.interactive_tiles_active:
                self.draw_interactive_attribution(bounds)
            if workspace is not None and hasattr(workspace, "drawWorkspaceOverlay_inView_bounds_"):
                workspace.drawWorkspaceOverlay_inView_bounds_(self, self, bounds)
            if self.show_help:
                self.draw_help(bounds)
            return
        transformer = self._metadata_transformer([], bounds)
        if transformer is None:
            points = [
                point
                for _track, processed in processed_tracks
                for point in processed.points
            ]
            transformer = self._point_transformer(points, bounds)
        self.draw_track_layers(processed_tracks, transformer)
        if self.mode == "overview" and self.show_endpoint_markers:
            self.draw_overview_track_endpoint_dots(transformer)
        self.draw_selection_overlay(transformer)
        if self.mode == "overview":
            self.draw_overview_endpoint_markers(transformer)
        if self.mode == "track" and tracks:
            current_track = self.current_track_for_title() or tracks[0]
            self.draw_track_start_end_markers(current_track, transformer)
            self.draw_media_markers(current_track, transformer, bounds)
        if self.mode == "overview" and self.show_track_numbers:
            self.draw_track_number_labels(transformer)
        if self.cursor is not None:
            point = self.cursor[1]
            x, y = transformer(point)
            self.draw_cursor_arrow(transformer, bounds)
            drag_ready = self.cursor_is_ready_for_drag()
            if drag_ready:
                NSColor.systemYellowColor().setFill()
                halo = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 12, y - 12, 24, 24))
                halo.fill()
            selected = any(
                start <= self.cursor[0] <= end
                for start, end in self.selected_ranges_for_track(self.cursor[2])
            )
            (NSColor.systemRedColor() if selected or drag_ready else NSColor.whiteColor()).setFill()
            (NSColor.whiteColor() if drag_ready else NSColor.systemRedColor() if selected else NSColor.systemBlueColor()).setStroke()
            dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 7, y - 7, 14, 14))
            dot.setLineWidth_(2.0)
            dot.fill()
            dot.stroke()
        self.draw_selection_marker(transformer)
        if self.show_info and self.context_overlay_coordinate is not None:
            self.draw_coordinate_overlay(self.context_overlay_coordinate, bounds)
        elif self.show_info and self.cursor is not None:
            self.draw_overlay(self.cursor[1], self.cursor[0], bounds)
        if self.interactive_tiles_active:
            self.draw_interactive_attribution(bounds)
        if self.show_help:
            self.draw_help(bounds)
        elif workspace is None and self.transient_help_until and time.monotonic() < self.transient_help_until:
            self.draw_overlay_panel(["Press h for help on keys"], bounds, width=260.0, centered=True)
        elif self.transient_help_until:
            self.transient_help_until = None
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "drawWorkspaceOverlay_inView_bounds_"):
            workspace.drawWorkspaceOverlay_inView_bounds_(self, self, bounds)

    def viewWillMoveToWindow_(self, window):
        # Detaching/reparenting a retained view is not closing its document.
        # PlotWindowDelegate owns worker shutdown on an actual window close.
        if hasattr(window, "setAcceptsMouseMovedEvents_"):
            window.setAcceptsMouseMovedEvents_(True)
        objc.super(PlotView, self).viewWillMoveToWindow_(window)

    def mouseMoved_(self, event):
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self.show_info and self.point_inside_rect(location, self.info_overlay_rect):
            return
        self.update_pointer_location(location)
        target = self.nearest_edit_target(location) if self.waypoint_editing_enabled() else None
        hovered = None
        if target is not None and target[0] == "point":
            track, index = target[1], target[2]
            if self.controller.media_paths_for_track_point(track, index):
                hovered = (track.nr, index)
        if hovered != self.hovered_media_point:
            self.hovered_media_point = hovered
            self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        self.pointer_coordinate = None
        self.pointer_elevation = None
        self.hovered_media_point = None
        self.update_window_title()

    def draggingEntered_(self, sender):
        if self.adventure_workspace_delegate is None and self.mode != "track":
            return 0
        paths = sender.draggingPasteboard().propertyListForType_(FILE_DRAG_TYPE)
        return 1 if paths else 0

    def prepareForDragOperation_(self, sender):
        return bool(sender.draggingPasteboard().propertyListForType_(FILE_DRAG_TYPE))

    def performDragOperation_(self, sender):
        paths = sender.draggingPasteboard().propertyListForType_(FILE_DRAG_TYPE)
        if self.adventure_workspace_delegate is not None:
            deferred_paths = [str(path) for path in paths or []]
            if not deferred_paths:
                return False
            # A modal Open panel cannot reliably receive mouse events while
            # AppKit is still completing the drag session.
            self.adventure_workspace_delegate.performSelector_withObject_afterDelay_(
                "performWorkspaceDrop:", deferred_paths, 0.0
            )
            return True
        if self.mode != "track":
            return False
        resolved_paths = [
            Path(str(path)).expanduser().resolve(strict=False)
            for path in paths or []
        ]
        gpx_paths = [path for path in resolved_paths if path.suffix.casefold() == ".gpx"]
        if gpx_paths:
            self.controller.performSelector_withObject_afterDelay_(
                "loadDroppedGPXPaths:",
                [str(path) for path in gpx_paths],
                0.0,
            )
            remaining = [path for path in resolved_paths if path not in gpx_paths]
            if remaining:
                self.controller.performSelector_withObject_afterDelay_(
                    "addDroppedMediaPaths:", [str(path) for path in remaining], 0.0
                )
            return True
        track = self.current_track_for_title()
        if not paths or track is None:
            return False
        self.controller.selected_nrs = [track.nr]
        self.controller.update_selection_field()
        self.controller.highlight_selected_rows()
        self.controller.performSelector_withObject_afterDelay_(
            "addDroppedMediaPaths:", [str(path) for path in paths], 0.0
        )
        return True

    def update_pointer_location(self, location):
        coordinate = self.coordinate_for_view_point(location)
        if coordinate is None:
            return
        self.pointer_coordinate = coordinate
        self.pointer_elevation = (
            self.elevation_for_view_location(location) if self.mode == "track" else None
        )
        self.update_window_title()

    def clearTransientHelp_(self, _timer):
        if self.transient_help_until is not None and time.monotonic() >= self.transient_help_until:
            self.transient_help_until = None
            self.setNeedsDisplay_(True)

    def display_tracks(self) -> list[TrackRecord]:
        show_hidden = bool(getattr(self.adventure_workspace_delegate, "show_hidden_tracks", False))
        if self.mode == "overview":
            tracks = list(self.controller.tracks) if show_hidden else self.controller.visible_tracks()
            if (
                self.adventure_workspace_delegate is not None
                and self.controller.track_load_timer is not None
            ):
                return [
                    track
                    for track in tracks
                    if track.processed is not None or track.display_geometry is not None
                ]
            return tracks
        if self.adventure_workspace_delegate is not None:
            return list(self.controller.tracks) if show_hidden else self.controller.visible_tracks()
        sequence = self.track_sequence()
        if not sequence:
            return []
        self.track_index = max(0, min(self.track_index, len(sequence) - 1))
        return [sequence[self.track_index]]

    def waypoint_editing_enabled(self) -> bool:
        if not getattr(self.controller, "track_mutations_enabled", True):
            return False
        if self.mode == "track":
            return True
        return bool(
            self.adventure_workspace_delegate is not None
            and len(self.controller.selected_tracks()) == 1
        )

    def track_sequence(self) -> list[TrackRecord]:
        if self.mode != "track":
            return self.controller.tracks
        per_track = self.plot_info.get("tracks")
        if isinstance(per_track, dict) and per_track:
            by_nr = {track.nr: track for track in self.controller.tracks}
            ordered = [by_nr[nr] for nr in per_track.keys() if nr in by_nr]
            if ordered:
                return ordered
        return self.controller.selected_tracks() or self.controller.tracks

    def update_track_plot_info(self, track: TrackRecord):
        per_track = self.plot_info.get("tracks", {})
        next_info = per_track.get(track.nr)
        if next_info is not None and self.plot_info.get("current_track_nr") != track.nr:
            self.reset_plot_interaction()
            tracks_info = self.plot_info.get("tracks")
            self.plot_info.update(next_info)
            if tracks_info is not None:
                self.plot_info["tracks"] = tracks_info
            self.plot_info["current_track_nr"] = track.nr
            self.plot_info.setdefault("base_extent_mercator", self.plot_info.get("metadata", {}).get("extent_mercator"))
            if self.interactive_tiles_active:
                self.refresh_interactive_tile_zoom()
                self.interactive_tile_generation += 1
                self.cancel_interactive_tile_requests(shutdown=False)
                self.schedule_interactive_tiles()
        self.update_window_title()

    def current_track_for_title(self):
        if self.mode != "track":
            selected = self.controller.selected_tracks()
            if len(selected) == 1:
                return selected[0]
            current_nr = self.plot_info.get("current_track_nr")
            return next((track for track in self.controller.tracks if track.nr == current_nr), None)
        current_nr = self.plot_info.get("current_track_nr")
        if current_nr is not None:
            for track in self.controller.tracks:
                if track.nr == current_nr:
                    return track
        sequence = self.track_sequence()
        if not sequence:
            return None
        self.track_index = max(0, min(self.track_index, len(sequence) - 1))
        return sequence[self.track_index]

    def update_window_title(self):
        window = self.window()
        if window is None:
            return
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "updateWorkspaceWindowTitle_"):
            workspace.updateWorkspaceWindowTitle_(window)
            return
        cursor_text = self.map_pointer_title_text()
        if self.mode == "overview":
            self.set_window_title_and_cursor(window, f"{PROGRAM_TITLE} - Overview", cursor_text)
            return
        track = self.current_track_for_title()
        if track is None:
            self.set_window_title_and_cursor(window, f"{PROGRAM_TITLE} - Track", cursor_text)
        else:
            self.set_window_title_and_cursor(
                window,
                f"{PROGRAM_TITLE} - {track.display_label}: {track.name}",
                cursor_text,
            )

    @staticmethod
    def set_window_title_and_cursor(window, title: str, cursor_text: str):
        window.setTitle_(title)
        if hasattr(window, "setSubtitle_"):
            window.setSubtitle_(cursor_text)
        elif cursor_text:
            window.setTitle_(f"{title}   {cursor_text}")

    @staticmethod
    def cursor_title_text(point: PointInfo) -> str:
        text = f"{point.lat:.6f}, {point.lon:.6f}"
        if point.ele is not None:
            text += f"   {point.ele:.1f} m"
        return text

    def map_pointer_title_text(self) -> str:
        if self.pointer_coordinate is None:
            return ""
        longitude, latitude = self.pointer_coordinate
        text = f"{latitude:.6f}, {longitude:.6f}"
        if self.pointer_elevation is not None:
            text += f"   {self.pointer_elevation:.1f} m"
        return text

    def switch_track(self, delta: int):
        if self.mode != "track":
            return
        sequence = self.track_sequence()
        if not sequence:
            return
        self.track_index = (self.track_index + delta) % len(sequence)
        self.update_track_plot_info(sequence[self.track_index])
        self.cursor = None
        self.marker = None
        self.last_viewport_signature = None
        self.update_window_title()
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.setNeedsDisplay_(True)

    def _point_transformer(self, points: list[PointInfo], bounds):
        min_lat = min(point.lat for point in points)
        max_lat = max(point.lat for point in points)
        min_lon = min(point.lon for point in points)
        max_lon = max(point.lon for point in points)
        center_lat = (min_lat + max_lat) / 2.0
        center_lon = (min_lon + max_lon) / 2.0
        span_lat = max((max_lat - min_lat) / self.zoom, 0.0001)
        span_lon = max((max_lon - min_lon) / self.zoom, 0.0001)
        aspect = max(bounds.size.width, 1.0) / max(bounds.size.height, 1.0)
        if span_lon / span_lat < aspect:
            span_lon = span_lat * aspect
        else:
            span_lat = span_lon / aspect
        left = center_lon - span_lon / 2.0
        bottom = center_lat - span_lat / 2.0
        margin = 24.0
        width = max(bounds.size.width - 2 * margin, 1.0)
        height = max(bounds.size.height - 2 * margin, 1.0)

        def transform(point: PointInfo):
            x = margin + ((point.lon - left) / span_lon) * width
            y = margin + ((point.lat - bottom) / span_lat) * height
            return x, y

        return transform

    def base_image_rect(self, bounds):
        image = self.plot_info.get("image")
        if image is None:
            return bounds
        image_size = image.size()
        image_ratio = image_size.width / max(image_size.height, 1.0)
        view_ratio = bounds.size.width / max(bounds.size.height, 1.0)
        if view_ratio > image_ratio:
            height = bounds.size.height
            width = height * image_ratio
        else:
            width = bounds.size.width
            height = width / image_ratio
        return NSMakeRect((bounds.size.width - width) / 2.0, (bounds.size.height - height) / 2.0, width, height)

    def image_rect(self, bounds):
        base_rect = self.base_image_rect(bounds)
        zoom = max(self.zoom, 1.0)
        if zoom <= 1.0:
            return base_rect
        focus_base = self.base_focus_point(bounds, base_rect)
        focus_view = (bounds.size.width / 2.0, bounds.size.height / 2.0)
        return NSMakeRect(
            focus_view[0] - (focus_base[0] - base_rect.origin.x) * zoom,
            focus_view[1] - (focus_base[1] - base_rect.origin.y) * zoom,
            base_rect.size.width * zoom,
            base_rect.size.height * zoom,
        )

    def base_focus_point(self, bounds, base_rect):
        if self.cursor is not None:
            base_point = self.point_to_rect(self.cursor[1], base_rect)
            if base_point is not None:
                return base_point
        return (bounds.size.width / 2.0, bounds.size.height / 2.0)

    def draw_plot_image(self, bounds):
        if self.interactive_tiles_active:
            self.draw_interactive_tiles(bounds)
            return
        image = self.plot_info.get("image")
        if image is None:
            return
        image.drawInRect_fromRect_operation_fraction_(
            self.image_rect(bounds),
            NSZeroRect,
            NSCompositingOperationSourceOver,
            1.0,
        )

    def draw_interactive_tiles(self, bounds):
        extent = (self.plot_info.get("metadata") or {}).get("extent_mercator") or {}
        if not extent:
            return
        self.ensure_interactive_tile_store_provider()
        if is_complete_world_extent(extent):
            self.interactive_world_ocean_color().setFill()
        else:
            NSColor.colorWithSRGBRed_green_blue_alpha_(
                0.28,
                0.32,
                0.34,
                1.0,
            ).setFill()
        NSBezierPath.fillRect_(bounds)
        map_rect = self.interactive_map_rect(bounds)
        tiles = visible_tiles(extent, self.current_tile_zoom())
        draw_keys = retained_tile_draw_order(
            self.interactive_tiles.keys(),
            extent,
            self.current_tile_zoom(),
        )
        for key in draw_keys:
            image = self.interactive_tiles.get(key)
            if image is None:
                continue
            rect = self.tile_view_rect(TileCoordinate(*key), extent, map_rect)
            image.drawInRect_fromRect_operation_fraction_(
                rect,
                NSZeroRect,
                NSCompositingOperationSourceOver,
                1.0,
            )
        for key in draw_keys:
            self.interactive_tiles.move_to_end(key)

        # Missing cells stay on the retained map or neutral provider backdrop.
        # A subtle border communicates loading without flashing black tiles.
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.12, 0.16, 0.18, 0.22).setStroke()
        for tile in tiles:
            if (tile.zoom, tile.x, tile.y) in self.interactive_tiles:
                continue
            border = NSBezierPath.bezierPathWithRect_(
                self.tile_view_rect(tile, extent, map_rect)
            )
            border.setLineWidth_(0.35)
            border.stroke()

    def interactive_tile_provider_cache_key(self):
        provider = str(self.controller.map_provider or "osm").strip().casefold()
        if provider == "custom":
            return f"custom:{str(self.controller.custom_map_url or '').strip()}"
        return provider

    def interactive_world_ocean_cache_key(self):
        return self.interactive_tile_provider_cache_key()

    def ensure_interactive_tile_store_provider(self):
        provider_key = self.interactive_tile_provider_cache_key()
        if provider_key == self.interactive_tile_store_provider_key:
            return provider_key
        self.interactive_tiles.clear()
        self.interactive_tile_target_keys.clear()
        self.interactive_tile_store_provider_key = provider_key
        self.interactive_world_ocean_provider_key = ""
        self.interactive_world_ocean_samples = []
        return provider_key

    def remember_interactive_tile(self, key, image):
        key = tuple(key)
        self.interactive_tiles[key] = image
        self.interactive_tiles.move_to_end(key)
        while len(self.interactive_tiles) > INTERACTIVE_TILE_MEMORY_LIMIT:
            removable = next(
                (
                    candidate
                    for candidate in self.interactive_tiles
                    if candidate not in self.interactive_tile_target_keys
                ),
                None,
            )
            if removable is None:
                break
            self.interactive_tiles.pop(removable, None)

    def interactive_world_ocean_color(self):
        provider_key = self.interactive_world_ocean_cache_key()
        rgb = _INTERACTIVE_WORLD_OCEAN_RGB_BY_PROVIDER.get(
            provider_key,
            DEFAULT_WORLD_OCEAN_RGB,
        )
        return NSColor.colorWithSRGBRed_green_blue_alpha_(
            float(rgb[0]),
            float(rgb[1]),
            float(rgb[2]),
            1.0,
        )

    def update_interactive_world_ocean_color(self, samples):
        extent = self.current_extent()
        if not extent or not is_complete_world_extent(extent):
            return False
        provider_key = self.interactive_world_ocean_cache_key()
        if provider_key != self.interactive_world_ocean_provider_key:
            self.interactive_world_ocean_provider_key = provider_key
            self.interactive_world_ocean_samples = []
        if not samples:
            return False
        self.interactive_world_ocean_samples.extend(samples)
        self.interactive_world_ocean_samples = self.interactive_world_ocean_samples[-24:]
        rgb = tuple(
            median(sample[channel] for sample in self.interactive_world_ocean_samples)
            for channel in range(3)
        )
        _INTERACTIVE_WORLD_OCEAN_RGB_BY_PROVIDER[provider_key] = rgb
        return True

    def draw_interactive_attribution(self, bounds):
        attribution = provider_attribution(
            self.controller.map_provider,
            self.controller.custom_map_attribution,
        )
        if attribution:
            width = min(bounds.size.width - 12.0, max(130.0, len(attribution) * 6.2 + 12.0))
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.68).setFill()
            panel = NSMakeRect(bounds.size.width - width - 6.0, 6.0, width, 20.0)
            NSBezierPath.fillRect_(panel)
            self.draw_text(attribution, panel.origin.x + 6.0, panel.origin.y + 4.0, 10, NSColor.whiteColor())

    def tile_view_rect(self, tile, extent, bounds):
        tile_extent = tile_bounds_mercator(tile)
        span_x = max(float(extent["max_x"]) - float(extent["min_x"]), 1.0)
        span_y = max(float(extent["max_y"]) - float(extent["min_y"]), 1.0)
        left = bounds.origin.x + (tile_extent["min_x"] - float(extent["min_x"])) / span_x * bounds.size.width
        right = bounds.origin.x + (tile_extent["max_x"] - float(extent["min_x"])) / span_x * bounds.size.width
        bottom = bounds.origin.y + (tile_extent["min_y"] - float(extent["min_y"])) / span_y * bounds.size.height
        top = bounds.origin.y + (tile_extent["max_y"] - float(extent["min_y"])) / span_y * bounds.size.height
        return NSMakeRect(left, bottom, right - left, top - bottom)

    def interactive_map_rect(self, bounds=None, *, extent=None):
        bounds = self.bounds() if bounds is None else bounds
        extent = extent or (
            (self.plot_info.get("metadata") or {}).get("extent_mercator") or {}
        )
        required = ("min_x", "max_x", "min_y", "max_y")
        if any(key not in extent for key in required):
            return bounds
        x, y, width, height = aspect_preserving_rect(
            bounds.size.width,
            bounds.size.height,
            extent,
        )
        return NSMakeRect(bounds.origin.x + x, bounds.origin.y + y, width, height)

    def _metadata_transformer(self, points: list[PointInfo], bounds):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator") or {}
        axes = metadata.get("axes_box_fraction") or {}
        image = self.plot_info.get("image")
        required = ("min_x", "max_x", "min_y", "max_y")
        if (image is None and not self.interactive_tiles_active) or any(key not in extent for key in required):
            return None
        image_rect = self.interactive_map_rect(bounds) if self.interactive_tiles_active else self.image_rect(bounds)
        return self._metadata_transformer_for_rect(image_rect)

    def _metadata_transformer_for_rect(self, image_rect):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator") or {}
        axes = metadata.get("axes_box_fraction") or {}
        required = ("min_x", "max_x", "min_y", "max_y")
        if any(key not in extent for key in required):
            return None
        left = image_rect.origin.x + axes.get("left", 0.0) * image_rect.size.width
        bottom = image_rect.origin.y + axes.get("bottom", 0.0) * image_rect.size.height
        width = axes.get("width", 1.0) * image_rect.size.width
        height = axes.get("height", 1.0) * image_rect.size.height
        min_x = extent["min_x"]
        max_x = extent["max_x"]
        min_y = extent["min_y"]
        max_y = extent["max_y"]
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)

        def transform(point: PointInfo):
            merc_x, merc_y = lonlat_to_web_mercator(point.lon, point.lat)
            x = left + ((merc_x - min_x) / span_x) * width
            y = bottom + ((merc_y - min_y) / span_y) * height
            return x, y

        return transform

    def point_to_rect(self, point: PointInfo, image_rect):
        transformer = self._metadata_transformer_for_rect(image_rect)
        if transformer is None:
            return None
        return transformer(point)

    def draw_track(self, points: list[PointInfo], transformer, color, width, dashed=False):
        if len(points) < 2:
            return
        bounds = self.bounds()
        margin = max(2.0, float(width))
        extent = {
            "min_x": -margin,
            "max_x": float(bounds.size.width) + margin,
            "min_y": -margin,
            "max_y": float(bounds.size.height) + margin,
        }
        view_points = [transformer(point) for point in points]
        runs = visible_simplified_polyline_runs(
            view_points,
            extent,
            (float(bounds.size.width), float(bounds.size.height)),
        )
        color.setStroke()
        for run in runs:
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(width)
            if dashed:
                path.setLineDash_count_phase_([5., 4.], 2, 0.)
            path.setLineJoinStyle_(NSRoundLineJoinStyle)
            path.setLineCapStyle_(NSRoundLineCapStyle)
            path.moveToPoint_(run[0])
            for point in run[1:]:
                path.lineToPoint_(point)
            path.stroke()

    def draw_segment_end_caps(self, points: list[PointInfo], transformer):
        """Mark both ends of a GPX segment with a short perpendicular line."""
        if len(points) < 2:
            return
        for point, neighbor in ((points[0], points[1]), (points[-1], points[-2])):
            x_coord, y_coord = transformer(point)
            neighbor_x, neighbor_y = transformer(neighbor)
            dx = neighbor_x - x_coord
            dy = neighbor_y - y_coord
            length = math.hypot(dx, dy)
            if length <= 0:
                continue
            normal_x = -dy / length
            normal_y = dx / length
            half_length = 5.0
            path = NSBezierPath.bezierPath()
            path.moveToPoint_((x_coord - normal_x * half_length, y_coord - normal_y * half_length))
            path.lineToPoint_((x_coord + normal_x * half_length, y_coord + normal_y * half_length))
            NSColor.blackColor().setStroke()
            path.setLineWidth_(3.0)
            path.stroke()
            NSColor.whiteColor().setStroke()
            path.setLineWidth_(1.25)
            path.stroke()

    def display_geometry_for_tracks(self, tracks):
        """Keep editable/selected geometry detailed; only overview context is coarse.

        draw_track clips and simplifies in screen space on every repaint, so
        zooming or resizing never reuses a fixed-budget selected-track outline.
        """
        selected = set(self.controller.selected_nrs)
        overview_point_limit = max(48, min(320, 16000 // max(len(tracks), 1)))
        return [
            (
                track,
                self.controller.display_track_geometry(track, overview_point_limit)
                if self.mode == "overview" and track.nr not in selected
                else self.controller.display_processed_track(track),
            )
            for track in tracks
        ]

    def draw_track_layers(self, processed_tracks, transformer):
        selected = set(self.controller.selected_nrs)
        highlight_selection = self.mode == "overview" or self.adventure_workspace_delegate is not None
        draw_base = self.plot_info.get("image") is None or self.interactive_tiles_active
        for track, processed in processed_tracks:
            # A selected track is drawn once, on top, using this same geometry.
            # Do not leave a differently simplified blue line underneath it.
            selected_overlay = highlight_selection and track.nr in selected
            color = cocoa_track_color(track, getattr(self.controller, "project_parameters", None))
            for segment in processed.segments:
                if draw_base and not selected_overlay:
                    self.draw_track(segment.points, transformer, color, 3.5, track.hidden)
                self.draw_segment_end_caps(segment.points, transformer)
        self.draw_selected_tracks_overlay(transformer, processed_tracks)

    def draw_selected_tracks_overlay(self, transformer, processed_tracks=None):
        if self.mode != "overview" and self.adventure_workspace_delegate is None:
            return
        selected = set(self.controller.selected_nrs)
        if not selected:
            return
        show_hidden = bool(getattr(self.adventure_workspace_delegate, "show_hidden_tracks", False))
        tracks = [track for track in self.controller.tracks if track.nr in selected and (show_hidden or not track.hidden)]
        geometry_by_number = (
            {track.nr: processed for track, processed in processed_tracks}
            if processed_tracks is not None else None
        )
        selected_color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.02, 0.25, 0.62, 1.0)
        for track in tracks:
            if (
                self.controller.track_load_timer is not None
                and track.processed is None
                and track.display_geometry is None
            ):
                continue
            if geometry_by_number is not None:
                processed = geometry_by_number.get(track.nr)
                if processed is None:
                    continue
            else:
                processed = self.controller.display_processed_track(track)
            for segment in processed.segments:
                color = cocoa_track_color(track, getattr(self.controller, "project_parameters", None))
                self.draw_track(segment.points, transformer, NSColor.whiteColor(), 8.5, track.hidden)
                self.draw_track(segment.points, transformer, color, 6.0, track.hidden)

    def draw_overview_endpoint_markers(self, transformer):
        if self.mode != "overview":
            return
        tracks_with_points = [
            (track, points)
            for track in self.display_tracks()
            if (points := self.controller.display_track_geometry(track, 64).points)
        ]
        if not tracks_with_points:
            return
        endpoints = (("Start", tracks_with_points[0][1][0]), ("End", tracks_with_points[-1][1][-1]))
        for label, point in endpoints:
            x, y = transformer(point)
            NSColor.whiteColor().setFill()
            NSColor.systemBlueColor().setStroke()
            dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 5, y - 5, 10, 10))
            dot.setLineWidth_(2.0)
            dot.fill()
            dot.stroke()
            self.draw_label_box(label, x + 8, y + 8)

    def draw_overview_track_endpoint_dots(self, transformer):
        if self.mode != "overview":
            return
        selected = set(self.controller.selected_nrs)
        for track in self.display_tracks():
            points = self.controller.display_track_geometry(track, 64).points
            if not points:
                continue
            stroke_color = cocoa_track_color(track, getattr(self.controller, "project_parameters", None))
            NSColor.whiteColor().setFill()
            stroke_color.setStroke()
            for point in (points[0], points[-1]):
                x, y = transformer(point)
                dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 4, y - 4, 8, 8))
                dot.setLineWidth_(1.5)
                dot.fill()
                dot.stroke()

    def draw_track_number_labels(self, transformer):
        if self.mode != "overview":
            return
        for track in self.display_tracks():
            points = self.controller.display_track_geometry(track, 64).points
            if not points:
                continue
            point = points[len(points) // 2]
            x, y = transformer(point)
            if track.track_number is not None:
                self.draw_label_box(str(track.track_number), x + 8, y + 8)

    def draw_track_start_end_markers(self, track: TrackRecord, transformer):
        points = self.controller.display_processed_track(track).points
        if not points:
            return
        endpoint_rows = [("Start", points[0], points[1] if len(points) > 1 else None)]
        endpoint_rows.append(("End", points[-1], points[-2] if len(points) > 1 else None))
        for label, point, neighbor in endpoint_rows:
            x, y = transformer(point)
            if self.show_endpoint_markers:
                NSColor.whiteColor().setFill()
                NSColor.blackColor().setStroke()
                dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 6, y - 6, 12, 12))
                dot.setLineWidth_(2.0)
                dot.fill()
                dot.stroke()
                if neighbor is not None:
                    neighbor_x, neighbor_y = transformer(neighbor)
                    if label == "Start":
                        dx, dy = neighbor_x - x, neighbor_y - y
                    else:
                        dx, dy = x - neighbor_x, y - neighbor_y
                    length = math.hypot(dx, dy)
                    if length > 0:
                        dx, dy = dx / length, dy / length
                        side_x, side_y = -dy, dx
                        tip_x, tip_y = x + dx * 16.0, y + dy * 16.0
                        arrow = NSBezierPath.bezierPath()
                        arrow.moveToPoint_((tip_x, tip_y))
                        arrow.lineToPoint_((x + dx * 7.0 + side_x * 4.0, y + dy * 7.0 + side_y * 4.0))
                        arrow.lineToPoint_((x + dx * 7.0 - side_x * 4.0, y + dy * 7.0 - side_y * 4.0))
                        arrow.closePath()
                        NSColor.blackColor().setFill()
                        arrow.fill()
            self.draw_label_box(label, x + 9, y + 7)

    def draw_label_box(self, text, x, y):
        width = max(44.0, len(text) * 7.0 + 14.0)
        height = 20.0
        rect = NSMakeRect(x, y, width, height)
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.72).setFill()
        panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 5.0, 5.0)
        panel.fill()
        self.draw_text(text, x + 7, y + 4, 12, NSColor.whiteColor())

    def linked_media_for_track(self, track: TrackRecord):
        source_directory = Path(track.source_file).parent if track.source_file else None
        result = []
        for index, point in enumerate(track.points()):
            for path in media_paths_for_point(point.element, source_directory):
                if path.is_file():
                    result.append((index, point, path))
        return result

    def request_photo_thumbnail(self, path: Path):
        if getattr(self, "plot_closed", False):
            return None
        try:
            stat = path.stat()
            cache_key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            return None
        image = self.photo_thumbnail_cache.get(cache_key)
        if image is not None or cache_key in self.photo_thumbnail_futures:
            return image
        generation = self.photo_thumbnail_generation

        def load():
            _identity, thumbnail_path, _created = create_or_reuse_thumbnail(path)
            return thumbnail_path

        future = self.photo_thumbnail_executor.submit(load)
        self.photo_thumbnail_futures[cache_key] = future

        def completed(done):
            try:
                thumbnail_path = done.result()
            except Exception:
                thumbnail_path = None
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "installPhotoThumbnail:", (cache_key, generation, thumbnail_path), False
            )

        future.add_done_callback(completed)
        return None

    @objc.IBAction
    def installPhotoThumbnail_(self, payload):
        if getattr(self, "plot_closed", False):
            return
        cache_key, generation, thumbnail_path = payload
        self.photo_thumbnail_futures.pop(cache_key, None)
        if generation != self.photo_thumbnail_generation or thumbnail_path is None:
            return
        image = NSImage.alloc().initWithContentsOfFile_(str(thumbnail_path))
        if image is not None:
            self.photo_thumbnail_cache.put(cache_key, image)
            self.setNeedsDisplay_(True)

    def draw_media_markers(self, track: TrackRecord, transformer, bounds):
        selected_element = self.cursor[1].element if self.cursor is not None else None
        self.photo_thumbnail_hit_targets = []
        if not self.show_media_markers:
            return
        linked = self.linked_media_for_track(track)
        linked.sort(
            key=lambda value: (
                value[2].resolve(strict=False) == self.active_media_path,
                value[1].element is selected_element,
                self.hovered_media_point == (track.nr, value[0]),
            )
        )
        for _index, point, path in linked:
            x, y = transformer(point)
            NSColor.whiteColor().setStroke()
            NSColor.systemBlueColor().setFill()
            marker = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 5, y + 5, 10, 10))
            marker.setLineWidth_(1.5)
            marker.fill()
            marker.stroke()
            hovered = self.hovered_media_point == (track.nr, _index)
            if not (self.show_all_photo_thumbnails or point.element is selected_element or hovered):
                continue
            image = self.request_photo_thumbnail(path)
            if image is None:
                continue
            maximum = 72.0 if self.show_all_photo_thumbnails else 96.0
            image_size = image.size()
            scale = min(maximum / max(image_size.width, 1.0), maximum / max(image_size.height, 1.0))
            width, height = image_size.width * scale, image_size.height * scale
            left = max(2.0, min(bounds.size.width - width - 2.0, x - width / 2.0))
            bottom = max(2.0, min(bounds.size.height - height - 2.0, y + 18.0))
            active = path.resolve(strict=False) == self.active_media_path
            (NSColor.systemBlueColor() if active else NSColor.whiteColor()).setFill()
            NSBezierPath.fillRect_(NSMakeRect(left - 3, bottom - 3, width + 6, height + 6))
            image.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(left, bottom, width, height), NSZeroRect,
                NSCompositingOperationSourceOver, 1.0
            )
            self.photo_thumbnail_hit_targets.append(
                (NSMakeRect(left - 3, bottom - 3, width + 6, height + 6), track, _index, path)
            )

    def draw_selection_overlay(self, transformer):
        if self.mode != "track" and self.adventure_workspace_delegate is None:
            return
        track = self.cursor[2] if self.cursor is not None else None
        if track is None:
            selected = self.controller.selected_tracks()
            track = selected[0] if len(selected) == 1 else None
        if track is None:
            return
        for start, end in self.selected_ranges_for_track(track):
            self.draw_selected_point_range(track, transformer, start, end)

    def selected_ranges_for_track(self, track: TrackRecord):
        ranges = []
        if self.marker is not None and self.cursor is not None and self.cursor[2] is track:
            ranges.append((min(self.marker, self.cursor[0]), max(self.marker, self.cursor[0])))
        elif self.inspector is not None and track is self.inspector.track:
            ranges.extend(self.group_point_indexes(self.inspector.selected_row_indexes()))
        return ranges

    def set_selection_marker(self, swap=False):
        if self.cursor is None:
            return
        index, _point, track = self.cursor
        if swap and self.marker is not None:
            points = track.points()
            if not 0 <= self.marker < len(points):
                return
            self.cursor = (self.marker, points[self.marker], track)
            self.marker = index
            message = f"Swapped marker and cursor in {track.display_label}."
        else:
            self.marker = index
            message = f"Marker set at point {index + 1} of {track.display_label}."
        self.sync_inspector_selection()
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.update_window_title()
        self.setNeedsDisplay_(True)
        self.controller.set_status(message)

    def draw_selection_marker(self, transformer):
        if self.marker is None or self.cursor is None:
            return
        points = self.cursor[2].points()
        if not 0 <= self.marker < len(points):
            return
        x, y = transformer(points[self.marker])
        NSColor.systemRedColor().setFill()
        NSColor.whiteColor().setStroke()
        square = NSBezierPath.bezierPathWithRect_(NSMakeRect(x - 7, y - 7, 14, 14))
        square.setLineWidth_(2.0)
        square.fill()
        square.stroke()

    def selected_range_at_target(self, target):
        """Return the selected range hit by a point or connecting segment."""
        if target is None or target[0] not in {"point", "segment"}:
            return None
        kind, track, index, _fraction = target
        for start, end in self.selected_ranges_for_track(track):
            if end <= start:
                continue
            if kind == "point" and start <= index <= end:
                return track, start, end
            if kind == "segment" and start <= index and index + 1 <= end:
                return track, start, end
        return None

    def group_point_indexes(self, indexes):
        ordered = sorted(set(index for index in indexes if index >= 0))
        if not ordered:
            return []
        ranges = []
        start = previous = ordered[0]
        for index in ordered[1:]:
            if index == previous + 1:
                previous = index
                continue
            ranges.append((start, previous))
            start = previous = index
        ranges.append((start, previous))
        return ranges

    def draw_selected_point_range(self, track: TrackRecord, transformer, start, end):
        points = track.points()
        start = max(0, min(start, len(points) - 1))
        end = max(0, min(end, len(points) - 1))
        if start > end or not points:
            return
        segment = points[start:end + 1]
        runs = []
        for point in segment:
            if not runs or runs[-1][-1].segment_index != point.segment_index:
                runs.append([point])
            else:
                runs[-1].append(point)
        for run in runs:
            if len(run) >= 2:
                self.draw_track(run, transformer, NSColor.systemRedColor(), 8.0)
        NSColor.systemRedColor().setFill()
        for point in segment:
            x, y = transformer(point)
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 5, y - 5, 10, 10)).fill()

    def draw_cursor_arrow(self, transformer, bounds):
        if self.cursor is None:
            return
        point_index, point, track = self.cursor
        points = track.points()
        if len(points) < 2:
            return
        if point_index < len(points) - 1 and points[point_index + 1].segment_index == point.segment_index:
            first, second = point, points[point_index + 1]
        elif point_index > 0 and points[point_index - 1].segment_index == point.segment_index:
            first, second = points[point_index - 1], point
        else:
            return
        first_x, first_y = transformer(first)
        second_x, second_y = transformer(second)
        tangent_x = second_x - first_x
        tangent_y = second_y - first_y
        tangent_length = math.hypot(tangent_x, tangent_y)
        if tangent_length <= 0:
            return
        tangent_x /= tangent_length
        tangent_y /= tangent_length
        normal_x, normal_y = -tangent_y, tangent_x
        if normal_y < 0:
            normal_x, normal_y = -normal_x, -normal_y
        normal_length = math.hypot(normal_x, normal_y)
        if normal_length <= 0:
            return
        normal_x /= normal_length
        normal_y /= normal_length

        pixel_x, pixel_y = transformer(point)
        radius = 7.0
        arrow_length = max(bounds.size.height * 0.07, 42.0)
        head_width = arrow_length / 3.0
        head_length = head_width
        shaft_width = head_width / 2.0
        shaft_length = arrow_length - head_length
        if head_width <= 0 or shaft_length <= 0:
            return
        offset = radius * 2.2
        base_x = pixel_x + normal_x * offset
        base_y = pixel_y + normal_y * offset
        tip_x = base_x + tangent_x * arrow_length
        tip_y = base_y + tangent_y * arrow_length
        head_base_x = tip_x - tangent_x * head_length
        head_base_y = tip_y - tangent_y * head_length
        shaft_base_x = base_x
        shaft_base_y = base_y
        side_x, side_y = normal_x, normal_y
        half_head_width = head_width / 2.0
        half_shaft_width = shaft_width / 2.0

        path = NSBezierPath.bezierPath()
        path.moveToPoint_((tip_x, tip_y))
        path.lineToPoint_((head_base_x + side_x * half_head_width, head_base_y + side_y * half_head_width))
        path.lineToPoint_((head_base_x + side_x * half_shaft_width, head_base_y + side_y * half_shaft_width))
        path.lineToPoint_((shaft_base_x + side_x * half_shaft_width, shaft_base_y + side_y * half_shaft_width))
        path.lineToPoint_((shaft_base_x - side_x * half_shaft_width, shaft_base_y - side_y * half_shaft_width))
        path.lineToPoint_((head_base_x - side_x * half_shaft_width, head_base_y - side_y * half_shaft_width))
        path.lineToPoint_((head_base_x - side_x * half_head_width, head_base_y - side_y * half_head_width))
        path.closePath()

        NSColor.blackColor().setStroke()
        path.setLineWidth_(max(3.0, head_width * 0.20))
        path.stroke()
        NSColor.whiteColor().setStroke()
        path.setLineWidth_(max(2.0, head_width * 0.12))
        path.stroke()

    def draw_text(self, text, x, y, size=13, color=None):
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(size),
            NSForegroundColorAttributeName: color or NSColor.labelColor(),
        }
        NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y), attrs)

    def draw_overlay_panel(self, lines, bounds, title=None, width=440.0, centered=False, closable=False):
        line_height = 19.0
        title_height = 24.0 if title else 0.0
        padding = 16.0
        panel_height = padding * 2 + title_height + line_height * len(lines)
        panel_width = min(width, max(260.0, bounds.size.width - 32.0))
        if closable and self.info_overlay_origin is not None:
            maximum_x = max(0.0, bounds.size.width - panel_width)
            maximum_y = max(0.0, bounds.size.height - panel_height)
            rect = NSMakeRect(
                max(0.0, min(maximum_x, self.info_overlay_origin.x)),
                max(0.0, min(maximum_y, self.info_overlay_origin.y)),
                panel_width,
                panel_height,
            )
        elif centered:
            rect = NSMakeRect(
                (bounds.size.width - panel_width) / 2.0,
                (bounds.size.height - panel_height) / 2.0,
                panel_width,
                panel_height,
            )
        else:
            rect = NSMakeRect(16.0, bounds.size.height - panel_height - 16.0, panel_width, panel_height)
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.72).setFill()
        panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 8.0, 8.0)
        panel.fill()
        if closable:
            self.info_overlay_rect = rect
            self.info_overlay_origin = rect.origin
        if closable:
            close_size = 20.0
            close_rect = NSMakeRect(
                rect.origin.x + rect.size.width - close_size - 7.0,
                rect.origin.y + rect.size.height - close_size - 7.0,
                close_size,
                close_size,
            )
            self.info_overlay_close_rect = close_rect
            self.draw_text("×", close_rect.origin.x + 4.0, close_rect.origin.y + 1.0, 17, NSColor.whiteColor())
        y = rect.origin.y + rect.size.height - padding - 15.0
        if title:
            self.draw_text(title, rect.origin.x + padding, y, 16, NSColor.whiteColor())
            y -= title_height
        for line in lines:
            self.draw_text(line, rect.origin.x + padding, y, 13, NSColor.whiteColor())
            y -= line_height

    def draw_overlay(self, point: PointInfo, point_index: int, bounds):
        track = self.cursor[2] if self.cursor is not None and len(self.cursor) > 2 else None
        if track is None:
            return
        distance_anchor = self.controller.distance_to_anchor(point)
        distance_start = self.controller.distance_from_track_start(track, point_index)
        elapsed, remaining = self.controller.elapsed_and_remaining(track, point_index)
        metrics = self.controller.compute_metrics(track)
        selected_metrics = self.controller.compute_point_range_metrics(track, self.selected_ranges_for_track(track))
        point_time = format_datetime_local(point.time) if point.time else "N/A"
        point_height = "N/A" if point.ele is None else f"{point.ele:.1f} m"
        point_parts = [
            f"Track Point: {point_index + 1}/{len(track.points())}",
            f"Lat/Lon: {point.lat:.6f}, {point.lon:.6f}",
        ]
        if point.time is not None:
            point_parts.insert(1, f"Time: {point_time}")
        if point.ele is not None:
            point_parts.append(f"Height: {point_height}")
        metric_parts = [f"Length: {metrics['length_km']:.1f} km"]
        if metrics.get("duration") is not None:
            metric_parts.append(f"Duration: {format_duration(metrics['duration'], allow_days=True)}")
        if metrics.get("speed_kmh") is not None:
            metric_parts.append(f"Avg: {format_speed(metrics.get('speed_kmh'))}")
        rows = ["   ".join(point_parts), f"{track.display_label}: {track.name}", "   ".join(metric_parts)]
        if metrics.get("start_time") is not None and metrics.get("end_time") is not None:
            rows.append(
                "Track time: "
                f"{format_datetime_local(metrics['start_time'])} - "
                f"{format_datetime_local(metrics['end_time'])}"
            )
        rows.append(f"Anchor: {distance_anchor:.1f} km   Start: {distance_start:.1f} km")
        elapsed_parts = []
        if elapsed is not None:
            elapsed_parts.append(f"Elapsed: {format_duration(elapsed, allow_days=True)}")
        if remaining is not None:
            elapsed_parts.append(f"Left: {format_duration(remaining, allow_days=True)}")
        if elapsed_parts:
            rows.append("   ".join(elapsed_parts))
        if selected_metrics is not None:
            selected_parts = [f"{selected_metrics['length_km']:.1f} km"]
            if selected_metrics.get("duration") is not None:
                selected_parts.append(format_duration(selected_metrics["duration"], allow_days=True))
            if selected_metrics.get("speed_kmh") is not None:
                selected_parts.append(format_speed(selected_metrics["speed_kmh"]))
            selected_parts.append(
                f"+{selected_metrics['ascent_m']:.1f}/-{selected_metrics['descent_m']:.1f} m"
            )
            rows.insert(
                3,
                "Selected: " + ", ".join(selected_parts),
            )
            if selected_metrics.get("start_time") is not None and selected_metrics.get("end_time") is not None:
                rows.insert(
                    4,
                    "Selected time: "
                    f"{format_datetime_local(selected_metrics['start_time'])} - "
                    f"{format_datetime_local(selected_metrics['end_time'])}",
                )
        self.draw_overlay_panel(rows, bounds, title=None, width=720.0, closable=True)

    def draw_coordinate_overlay(self, coordinate, bounds):
        longitude, latitude = coordinate[:2]
        lines = [f"Lat/Lon: {latitude:.6f}, {longitude:.6f}"]
        if len(coordinate) > 2 and coordinate[2] is not None:
            lines.append(f"Height: {float(coordinate[2]):.1f} m")
        self.draw_overlay_panel(
            lines,
            bounds,
            width=360.0,
            closable=True,
        )

    @staticmethod
    def point_inside_rect(point, rect) -> bool:
        if rect is None:
            return False
        return (
            rect.origin.x <= point.x <= rect.origin.x + rect.size.width
            and rect.origin.y <= point.y <= rect.origin.y + rect.size.height
        )

    def dismiss_info_overlay(self):
        timer = getattr(self, "long_press_timer", None)
        if timer is not None:
            timer.invalidate()
            self.long_press_timer = None
        self.long_press_origin = None
        self.long_press_target = None
        self.show_info = False
        self.info_overlay_dismissed = True
        self.context_overlay_coordinate = None
        self.info_overlay_close_rect = None
        self.info_overlay_rect = None
        self.info_overlay_dragging = False
        self.info_overlay_drag_offset = None
        self.setNeedsDisplay_(True)

    def draw_help(self, bounds):
        help_lines = [
            "h: toggle this help",
            "i: toggle point information",
            "a: set current point as anchor for table distances",
            "I (Shift-I): interpolate heights across the selected point range",
            "+ / -: zoom around the selected waypoint (or view center)",
            "c: center map on current cursor point",
            "z / Shift-Z: fit selection / current track; A: all Adventure tracks",
            "q: close this map window (quits when it is the last window)",
            "p: toggle photo thumbnails on a track map",
            "s: save current plot as PNG",
            "u: clear current plot selection",
            "e: open elevation profile",
            "d: toggle start/end dots",
            "click: select nearest waypoint; right-click: edit, inspect coordinates, cut, or join",
            "drag or two-finger trackpad scroll: pan; Option-drag pans immediately",
            "mouse wheel, +/-, or trackpad pinch: zoom",
            "hold 1 second: insert or drag a waypoint or marked segment; Escape cancels",
            "m / Shift-click: set square marker; M: swap marker and cursor",
            "overview double-click: open the Track Table",
            "double-click track point: open the waypoint inspector",
            "overview: n toggles track numbers",
            "Cmd-Z / Shift-Cmd-Z: undo / redo the last track edit",
            "Cmd-X: cut the track after the current cursor point",
            "point table: Cmd-I / Shift-Cmd-I inserts after / before",
        ]
        if self.adventure_workspace_delegate is not None:
            help_lines.extend(
                [
                    "arrows: previous/next waypoint or media; pan into view if needed",
                    "Cmd-arrows: previous/next visible track and fit it to the map",
                    "media: click selects; Cmd-click adds or removes a group",
                    "media: Cmd-drag empty map draws a selection rectangle",
                ]
            )
        else:
            help_lines.append(
                "track: arrows/space next track, m marker, delete range, x cut after cursor"
            )
        self.draw_overlay_panel(help_lines, bounds, title="myCamino GPX Editor Keys", width=560.0, centered=True)

    def clone_plot_info(self, info):
        if isinstance(info, dict):
            return {key: self.clone_plot_info(value) for key, value in info.items()}
        if isinstance(info, list):
            return [self.clone_plot_info(value) for value in info]
        return info

    def replace_plot_info(self, plot_info):
        self.reset_plot_interaction()
        self.ensure_interactive_tile_store_provider()
        self.plot_info = plot_info or {}
        self.cursor = None
        self.marker = None
        self.last_viewport_signature = None
        self.interactive_tiles_active = self.plot_info.get("image") is None
        if self.interactive_tiles_active:
            self.refresh_interactive_tile_zoom()
        self.initial_plot_info = self.clone_plot_info(self.plot_info)
        self.interactive_tile_generation += 1
        self.cancel_interactive_tile_requests(shutdown=False)
        self.interactive_tile_target_keys.clear()
        if self.interactive_tiles_active:
            self.schedule_interactive_tiles(immediate=True)
        self.setNeedsDisplay_(True)
        self.controller.sync_elevation_profile_to_plot_view(self)

    def reset_plot_interaction(self):
        """Do not carry an unfinished gesture or edit into another track."""
        for name in ("pinch_finish_timer", "wheel_finish_timer", "long_press_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.invalidate()
                setattr(self, name, None)
        track = getattr(self, "dragged_point_track", None)
        original = getattr(self, "drag_original_xml", None)
        if track is not None and original is not None:
            track.element = ET.fromstring(original)
            self.controller.invalidate_track_metrics(track)
            inspector = self.inspector
            if self.drag_undo_owner == "inspector" and inspector is not None:
                if inspector.undo_stack and inspector.undo_stack[-1] is original:
                    inspector.undo_stack.pop()
            elif self.drag_undo_owner == "controller" and self.controller.undo_stack:
                entry = self.controller.undo_stack[-1]
                if isinstance(entry, TrackElementsUndoEntry) and entry.elements.get(track.nr) is original:
                    self.controller.undo_stack.pop()
            if inspector is not None and inspector.track is track:
                inspector.reload_rows()
        self.pan_active = False
        self.pan_origin = self.pan_start_extent = self.pan_restore_extent = None
        self.pinch_start_tile_zoom = None
        self.pinch_accumulated_scale = 1.0
        self.long_press_origin = self.long_press_target = None
        self.last_gesture_location = None
        self.info_overlay_mouse_active = self.info_overlay_dragging = False
        self.info_overlay_drag_offset = None
        if getattr(self, "workspace_media_selection_active", False):
            workspace = self.adventure_workspace_delegate
            if workspace is not None and hasattr(workspace, "cancelWorkspaceMediaSelection_"):
                workspace.cancelWorkspaceMediaSelection_(None)
        self.workspace_media_selection_active = False
        self.mouse_down_started_at = None
        self.mouse_down_click_count = 0
        self.dragged_point_index = self.dragged_point_track = None
        self.dragged_segment_rows = []
        self.dragged_segment_elements = []
        self.dragged_segment_original_coordinates = []
        self.dragged_segment_origin_mercator = None
        self.drag_original_xml = self.drag_undo_owner = None
        self.drag_inserted_point = False

    def refresh_interactive_tile_zoom(self):
        extent = self.current_extent()
        if not extent:
            return
        zoom = self.recommended_tile_zoom(extent)
        self.plot_info["zoom_level"] = self.plot_info["tile_zoom_level"] = zoom
        current = (self.plot_info.get("tracks") or {}).get(self.plot_info.get("current_track_nr"))
        if current is not None:
            current["zoom_level"] = current["tile_zoom_level"] = zoom

    def mouseDown_(self, event):
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.info_overlay_mouse_active = False
        self.clicked_media_target = None
        self.clicked_workspace_media_target = None
        self.workspace_track_selection_consumed = False
        closes_info = self.show_info and self.point_inside_rect(location, self.info_overlay_close_rect)
        if closes_info or (self.show_info and self.point_inside_rect(location, self.info_overlay_rect)):
            self.info_overlay_mouse_active = True
            if self.long_press_timer is not None:
                self.long_press_timer.invalidate()
                self.long_press_timer = None
            self.long_press_origin = None
            self.long_press_target = None
            self.mouse_down_started_at = None
            self.mouse_down_click_count = 0
            if closes_info:
                self.dismiss_info_overlay()
            else:
                self.info_overlay_dragging = True
                self.info_overlay_drag_offset = NSMakePoint(
                    location.x - self.info_overlay_rect.origin.x,
                    location.y - self.info_overlay_rect.origin.y,
                )
            return
        for rect, track, point_index, path in reversed(self.photo_thumbnail_hit_targets):
            if (
                rect.origin.x <= location.x <= rect.origin.x + rect.size.width
                and rect.origin.y <= location.y <= rect.origin.y + rect.size.height
            ):
                self.clicked_media_target = (track, point_index, path)
                self.active_media_path = path.resolve(strict=False)
                self.move_cursor_to_track_point(
                    track,
                    point_index,
                    self.inspector,
                    sync_table=True,
                    focus_plot=False,
                )
                break
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "workspace_media_target_at_location"):
            self.clicked_workspace_media_target = workspace.workspace_media_target_at_location(location)
        self.update_pointer_location(location)
        self.mouse_down_started_at = time.monotonic()
        self.mouse_down_click_count = int(event.clickCount())
        self.pan_origin = location
        self.pan_start_extent = self.current_extent()
        self.pan_restore_extent = dict(self.pan_start_extent) if self.pan_start_extent else None
        self.pan_active = bool(event.modifierFlags() & NSEventModifierFlagOption)
        self.last_gesture_location = location
        self.context_overlay_coordinate = None
        target = self.edit_target_for_mouse_down(location)
        selected_target = self.selected_range_at_target(target)
        if (
            workspace is not None
            and target is None
            and not self.workspace_track_selection_consumed
            and self.clicked_media_target is None
            and self.clicked_workspace_media_target is None
            and not self.pan_active
            and command_modifier_requests_media_selection(event.modifierFlags())
            and hasattr(workspace, "beginWorkspaceMediaSelectionAt_modifierFlags_")
        ):
            workspace.beginWorkspaceMediaSelectionAt_modifierFlags_(
                location,
                int(event.modifierFlags()),
            )
            self.workspace_media_selection_active = True
            self.pan_origin = None
            self.pan_start_extent = None
            return
        if not self.pan_active:
            if (
                selected_target is None
                and not self.workspace_track_selection_consumed
                and self.clicked_media_target is None
                and self.clicked_workspace_media_target is None
                and target is not None
            ):
                self.move_cursor_to_event(event)
                target = self.edit_target_for_mouse_down(location)
            self.long_press_target = target
        if (
            self.waypoint_editing_enabled()
            and target is not None
        ):
            self.long_press_origin = location
            if self.long_press_timer is not None:
                self.long_press_timer.invalidate()
            if not self.pan_active:
                self.schedule_point_editing_long_press()
        if self.waypoint_editing_enabled() and self.cursor is not None and event.modifierFlags() & NSEventModifierFlagShift:
            self.set_selection_marker()

    def edit_target_for_mouse_down(self, location):
        """Resolve an edit target after an Adventure track has been selected."""
        workspace = self.adventure_workspace_delegate
        if workspace is not None and self.mode == "overview":
            nearest = self.nearest_track_for_location(location)
            if nearest is not None and nearest[0] <= 12.0:
                _distance, track, _point_index = nearest
                selected = self.controller.selected_tracks()
                if len(selected) != 1 or selected[0] is not track:
                    self.controller.select_track_in_table(track.nr)
                    self.cursor = None
                    self.marker = None
                    self.workspace_track_selection_consumed = True
                    if hasattr(workspace, "workspaceTrackSelectedForEditing_"):
                        workspace.workspaceTrackSelectedForEditing_(track)
                    return None
        return self.nearest_edit_target(location) if self.waypoint_editing_enabled() else None

    def schedule_point_editing_long_press(self):
        """Run the edit timer while AppKit is tracking the held mouse button."""
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0,
            self,
            "beginPointEditingLongPress:",
            None,
            False,
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self.long_press_timer = timer

    def cursor_is_ready_for_drag(self) -> bool:
        return bool(
            self.cursor is not None
            and self.dragged_point_track is self.cursor[2]
            and self.dragged_point_index == self.cursor[0]
        )

    def mouseDragged_(self, event):
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self.info_overlay_dragging and self.info_overlay_drag_offset is not None:
            self.info_overlay_origin = NSMakePoint(
                location.x - self.info_overlay_drag_offset.x,
                location.y - self.info_overlay_drag_offset.y,
            )
            self.setNeedsDisplay_(True)
            return
        if getattr(self, "info_overlay_mouse_active", False):
            return
        if self.workspace_media_selection_active:
            workspace = self.adventure_workspace_delegate
            if workspace is not None and hasattr(workspace, "updateWorkspaceMediaSelectionTo_"):
                workspace.updateWorkspaceMediaSelectionTo_(location)
            self.last_gesture_location = location
            return
        if self.dragged_segment_rows and self.dragged_point_track is not None:
            coordinate = self.coordinate_for_view_point(location)
            if coordinate is not None and self.dragged_segment_origin_mercator is not None:
                current_x, current_y = lonlat_to_web_mercator(*coordinate)
                origin_x, origin_y = self.dragged_segment_origin_mercator
                if self.drag_original_xml is None and math.hypot(current_x - origin_x, current_y - origin_y) < 1e-6:
                    return
                self.prepare_drag_undo(self.dragged_point_track)
                translate_points_web_mercator(
                    self.dragged_segment_elements,
                    self.dragged_segment_original_coordinates,
                    current_x - origin_x,
                    current_y - origin_y,
                )
                self.controller.invalidate_track_metrics(self.dragged_point_track)
                points = self.dragged_point_track.points()
                cursor_index = self.dragged_segment_rows[-1]
                if 0 <= cursor_index < len(points):
                    self.cursor = (cursor_index, points[cursor_index], self.dragged_point_track)
                self.controller.refresh_elevation_profile_for_plot_view(self)
                self.setNeedsDisplay_(True)
            return
        if self.dragged_point_index is not None and self.dragged_point_track is not None:
            coordinate = self.coordinate_for_view_point(location)
            points = self.dragged_point_track.points()
            if coordinate is not None and self.dragged_point_index < len(points):
                longitude, latitude = coordinate
                element = points[self.dragged_point_index].element
                if element.get("lat") == f"{latitude:.8f}" and element.get("lon") == f"{longitude:.8f}":
                    return
                self.prepare_drag_undo(self.dragged_point_track)
                element.set("lat", f"{latitude:.8f}")
                element.set("lon", f"{longitude:.8f}")
                self.controller.invalidate_track_metrics(self.dragged_point_track)
                updated = self.dragged_point_track.points()[self.dragged_point_index]
                self.cursor = (
                    self.dragged_point_index,
                    updated,
                    self.dragged_point_track,
                )
                self.update_window_title()
                self.controller.refresh_elevation_profile_for_plot_view(self)
                self.setNeedsDisplay_(True)
            return
        if self.pan_origin is not None:
            distance = math.hypot(
                location.x - self.pan_origin.x,
                location.y - self.pan_origin.y,
            )
            if distance > 5.0:
                self.pan_active = True
                if self.long_press_timer is not None:
                    self.long_press_timer.invalidate()
                    self.long_press_timer = None
            if self.pan_active and self.pan_start_extent is not None:
                map_rect = self.interactive_map_rect(self.bounds())
                next_extent = shifted_interactive_extent(
                    self.pan_start_extent,
                    location.x - self.pan_origin.x,
                    location.y - self.pan_origin.y,
                    map_rect.size.width,
                    map_rect.size.height,
                )
                self.set_interactive_viewport(next_extent, request_tiles=False)
                self.last_gesture_location = location
                return

    def mouseUp_(self, event):
        if getattr(self, "info_overlay_mouse_active", False) or self.info_overlay_dragging:
            self.info_overlay_mouse_active = False
            self.info_overlay_dragging = False
            self.info_overlay_drag_offset = None
            return
        if self.workspace_media_selection_active:
            self.workspace_media_selection_active = False
            workspace = self.adventure_workspace_delegate
            location = self.convertPoint_fromView_(event.locationInWindow(), None)
            if workspace is not None and hasattr(workspace, "finishWorkspaceMediaSelectionAt_"):
                workspace.finishWorkspaceMediaSelectionAt_(location)
            self.mouse_down_started_at = None
            self.mouse_down_click_count = 0
            return
        if self.long_press_timer is not None:
            self.long_press_timer.invalidate()
            self.long_press_timer = None
        click_target = self.long_press_target
        self.long_press_origin = None
        self.long_press_target = None
        if self.pan_active:
            self.pan_active = False
            self.pan_origin = None
            self.pan_start_extent = None
            self.schedule_interactive_tiles(immediate=True)
            self.controller.sync_elevation_profile_to_plot_view(self)
            self.controller.set_status("Map moved; loading visible tiles.")
            return
        self.pan_origin = None
        self.pan_start_extent = None
        if (
            self.dragged_point_index is None
            and self.dragged_point_track is None
            and not self.dragged_segment_rows
            and click_target is not None
            and self.selected_range_at_target(click_target) is not None
            and not (event.modifierFlags() & NSEventModifierFlagShift)
        ):
            # A short click moves the range end. Holding the same red range
            # still starts the existing whole-range drag operation.
            self.move_cursor_to_event(event)
        workspace = self.adventure_workspace_delegate
        if (
            workspace is not None
            and self.dragged_point_index is None
            and self.dragged_point_track is None
            and not self.dragged_segment_rows
        ):
            if hasattr(workspace, "workspaceMapClicked_clickCount_modifierFlags_"):
                workspace.workspaceMapClicked_clickCount_modifierFlags_(
                    self,
                    int(event.clickCount()),
                    int(event.modifierFlags()),
                )
            else:
                workspace.workspaceMapClicked_clickCount_(self, int(event.clickCount()))
            self.mouse_down_started_at = None
            self.mouse_down_click_count = 0
            return
        if self.dragged_segment_rows and self.dragged_point_track is not None:
            track = self.dragged_point_track
            rows = list(self.dragged_segment_rows)
            changed = self.drag_original_xml is not None
            self.dragged_segment_rows = []
            self.dragged_segment_elements = []
            self.dragged_segment_original_coordinates = []
            self.dragged_segment_origin_mercator = None
            self.dragged_point_track = None
            self.drag_original_xml = None
            self.drag_undo_owner = None
            self.mouse_down_started_at = None
            self.mouse_down_click_count = 0
            if not changed:
                self.setNeedsDisplay_(True)
                return
            self.controller.mark_dirty(
                f"Moved {len(rows)} selected points in {track.display_label}."
            )
            self.refresh_after_map_edit(track, rows[-1])
            self.marker = rows[0]
            points = track.points()
            if points and rows[-1] < len(points):
                self.cursor = (rows[-1], points[rows[-1]], track)
            if self.inspector is not None and self.inspector.track is track:
                self.inspector.select_point_rows(rows)
            self.controller.refresh_elevation_profile_for_plot_view(self)
            self.setNeedsDisplay_(True)
            return
        if self.dragged_point_index is None or self.dragged_point_track is None:
            elapsed = time.monotonic() - self.mouse_down_started_at if self.mouse_down_started_at else 0.0
            if self.mouse_down_click_count == 2 and elapsed < 1.0 and self.cursor is not None:
                if self.mode == "overview":
                    self.controller.open_track_workflow_at_point(
                        self.cursor[2], self.cursor[0], source_view=self
                    )
                elif self.mode == "track":
                    if not self.controller.show_media_for_track_point(self.cursor[2], self.cursor[0]):
                        inspector = self.controller.open_inspector_for_track(self.cursor[2])
                        if inspector is not None:
                            self.inspector = inspector
                            inspector.plot_view = self
                            inspector.select_point_index(self.cursor[0])
                            inspector.window.makeKeyAndOrderFront_(None)
                            inspector.window.orderFrontRegardless()
            self.mouse_down_started_at = None
            self.mouse_down_click_count = 0
            return
        track = self.dragged_point_track
        point_index = self.dragged_point_index
        inserted = self.drag_inserted_point
        changed = self.drag_original_xml is not None
        self.dragged_point_index = None
        self.dragged_point_track = None
        self.drag_original_xml = None
        self.drag_inserted_point = False
        self.drag_undo_owner = None
        self.mouse_down_started_at = None
        self.mouse_down_click_count = 0
        if not changed:
            self.setNeedsDisplay_(True)
            return
        if self.inspector is not None and self.inspector.track is track:
            self.inspector.reload_rows()
            self.inspector.select_point_index(point_index)
        self.controller.mark_dirty(
            f"{'Inserted' if inserted else 'Moved'} point {point_index + 1} "
            f"in {track.display_label}."
        )
        self.refresh_after_map_edit(track, point_index)

    def coordinate_for_view_point(self, location):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator") or {}
        axes = metadata.get("axes_box_fraction") or {}
        required = ("min_x", "max_x", "min_y", "max_y")
        if any(key not in extent for key in required):
            return None
        image_rect = self.interactive_map_rect(self.bounds()) if self.interactive_tiles_active else self.image_rect(self.bounds())
        if not self.point_inside_rect(location, image_rect):
            return None
        left = image_rect.origin.x + float(axes.get("left", 0.0)) * image_rect.size.width
        bottom = image_rect.origin.y + float(axes.get("bottom", 0.0)) * image_rect.size.height
        width = max(float(axes.get("width", 1.0)) * image_rect.size.width, 1.0)
        height = max(float(axes.get("height", 1.0)) * image_rect.size.height, 1.0)
        fraction_x = (float(location.x) - left) / width
        fraction_y = (float(location.y) - bottom) / height
        merc_x = float(extent["min_x"]) + fraction_x * (
            float(extent["max_x"]) - float(extent["min_x"])
        )
        merc_y = float(extent["min_y"]) + fraction_y * (
            float(extent["max_y"]) - float(extent["min_y"])
        )
        from gpx_point_editing import web_mercator_to_lonlat

        return web_mercator_to_lonlat(merc_x, merc_y)

    def elevation_for_view_location(self, location):
        target = self.nearest_edit_target(location)
        if target is None:
            return None
        kind, track, index, fraction = target
        points = track.points()
        if kind == "point":
            return points[index].ele if 0 <= index < len(points) else None
        if kind != "segment" or index < 0 or index + 1 >= len(points):
            return None
        first = points[index]
        second = points[index + 1]
        if first.segment_index != second.segment_index or first.ele is None or second.ele is None:
            return None
        return first.ele + (second.ele - first.ele) * float(fraction)

    def nearest_edit_target(self, location):
        displayed = self.display_tracks()
        if not displayed:
            return None
        track = self.current_track_for_title() or displayed[0]
        points = track.points()
        if not points:
            return None
        transformer = self._metadata_transformer(points, self.bounds())
        if transformer is None:
            transformer = self._point_transformer(points, self.bounds())
        projected = [transformer(point) for point in points]
        segment_indexes = [point.segment_index for point in points]
        point_distances = [
            math.hypot(location.x - x_coord, location.y - y_coord)
            for x_coord, y_coord in projected
        ]
        nearest_point = min(range(len(points)), key=point_distances.__getitem__)
        if point_distances[nearest_point] <= 11.0:
            return ("point", track, nearest_point, None)
        best = None
        for index, ((x1, y1), (x2, y2)) in enumerate(
            zip(projected, projected[1:])
        ):
            if segment_indexes[index] != segment_indexes[index + 1]:
                continue
            dx = x2 - x1
            dy = y2 - y1
            denominator = dx * dx + dy * dy
            if denominator <= 0:
                continue
            fraction = max(
                0.0,
                min(
                    1.0,
                    ((location.x - x1) * dx + (location.y - y1) * dy)
                    / denominator,
                ),
            )
            nearest_x = x1 + fraction * dx
            nearest_y = y1 + fraction * dy
            distance = math.hypot(location.x - nearest_x, location.y - nearest_y)
            if best is None or distance < best[0]:
                best = (distance, index, fraction)
        if best is not None and best[0] <= 12.0:
            return ("segment", track, best[1], best[2])
        return None

    def nearest_track_for_location(self, location):
        """Return the nearest visible route and source row for map-level actions."""
        transformer = self._metadata_transformer([], self.bounds())
        if transformer is None:
            return None
        best = None
        tracks = self.display_tracks()
        per_track_limit = max(48, min(320, 16000 // max(len(tracks), 1)))
        for track in tracks:
            processed = self.controller.display_track_geometry(track, per_track_limit)
            for segment in processed.segments:
                points = segment.points
                projected = [transformer(point) for point in points]
                for point, (point_x, point_y) in zip(points, projected):
                    distance = math.hypot(location.x - point_x, location.y - point_y)
                    if best is None or distance < best[0]:
                        best = (distance, track, point.source_index)
                for index, ((x1, y1), (x2, y2)) in enumerate(zip(projected, projected[1:])):
                    dx = x2 - x1
                    dy = y2 - y1
                    denominator = dx * dx + dy * dy
                    if denominator <= 0.0:
                        continue
                    fraction = max(
                        0.0,
                        min(1.0, ((location.x - x1) * dx + (location.y - y1) * dy) / denominator),
                    )
                    nearest_x = x1 + fraction * dx
                    nearest_y = y1 + fraction * dy
                    distance = math.hypot(location.x - nearest_x, location.y - nearest_y)
                    source = points[index if fraction < 0.5 else index + 1].source_index
                    if best is None or distance < best[0]:
                        best = (distance, track, source)
        return best

    def _plot_context_item(self, menu, title, action, enabled=True):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self)
        item.setEnabled_(bool(enabled))
        menu.addItem_(item)
        return item

    def menuForEvent_(self, event):
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "workspace_media_target_at_location"):
            media_target = workspace.workspace_media_target_at_location(location)
            if media_target is not None and hasattr(workspace, "workspaceMediaContextMenuForTarget_"):
                return workspace.workspaceMediaContextMenuForTarget_(media_target)
        target = self.nearest_edit_target(location) if self.waypoint_editing_enabled() else None
        self.context_target = target
        self.context_track_target = None
        self.context_location = location
        if self.adventure_workspace_delegate is not None and self.mode == "overview":
            nearest = self.nearest_track_for_location(location)
            if nearest is not None and nearest[0] <= 18.0:
                _distance, track, point_index = nearest
                points = track.points()
                point_index = max(0, min(int(point_index), max(0, len(points) - 1)))
                selected = self.controller.selected_tracks()
                newly_selected = len(selected) != 1 or selected[0] is not track
                if newly_selected:
                    self.controller.select_track_in_table(track.nr)
                    self.cursor = None
                    target = None
                    self.context_target = None
                if target is None:
                    self.context_track_target = (track, point_index)
        selected_range = self.selected_range_at_target(target)
        self.context_selected_range = selected_range
        menu = NSMenu.alloc().initWithTitle_("GPX Map")
        menu.setAutoenablesItems_(False)
        if self.context_track_target is not None:
            self._plot_context_item(menu, "Show Track Table", "showContextTrackTable:")
            self._plot_context_item(
                menu,
                "Show Waypoint Table",
                "showContextWaypointTable:",
                self.controller.track_mutations_enabled,
            )
            self._plot_context_item(menu, "Fit to Track", "focusContextTrack:")
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_location_context_items(menu)
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_view_context_items(menu)
        elif selected_range is not None:
            self.context_track_target = (target[1], target[2])
            self._plot_context_item(menu, "Show Track Table", "showContextTrackTable:")
            self._plot_context_item(menu, "Show Waypoint Table", "showContextWaypointTable:")
            menu.addItem_(NSMenuItem.separatorItem())
            self._plot_context_item(menu, "Copy Marked Segment", "copyContextSegment:")
            self._plot_context_item(menu, "Cut Marked Segment", "cutContextSelectedSegment:")
            self._plot_context_item(menu, "Delete Marked Segment", "deleteContextSelectedSegment:")
            self._plot_context_item(menu, "Interpolate Height", "interpolateHeight:")
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_location_context_items(menu)
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_view_context_items(menu)
        elif target is not None and target[0] == "point":
            self.context_track_target = (target[1], target[2])
            self.prepare_context_point(target[1], target[2], create_inspector=False)
            linked_media = self.controller.media_paths_for_track_point(target[1], target[2])
            for title, action in [
                ("Show Track Table", "showContextTrackTable:"),
                ("Show Waypoint Table", "showContextWaypointTable:"),
                ("Show Point Information", "inspectContextPoint:"),
                ("View Media", "viewContextMedia:"),
                ("Set Selection Marker", "markContextPoint:"),
                ("Center", "centerOnlyContextPoint:"),
                ("Center and Zoom", "centerContextPoint:"),
                ("Fit to Track", "fitContextTrack:"),
                (None, None),
                ("Insert Waypoint Before", "insertContextBefore:"),
                ("Insert Waypoint After", "insertContextAfter:"),
                ("Paste Waypoint(s) After", "pasteContextPointsAfter:"),
                ("Copy Waypoint(s)", "copyContextPoints:"),
                ("Copy Coordinates", "copyContextCoordinates:"),
                ("Delete Waypoint(s)", "deleteContextPoints:"),
                ("Cut Track Connection After", "cutContextPoint:"),
                ("Join Track Segments", "joinContextSegments:"),
                ("Split Track Above", "splitContextAbove:"),
                (None, None),
                ("Set Anchorpoint", "anchorContextPoint:"),
                ("Undo", "undoContextEdit:"),
                ("Redo", "redoContextEdit:"),
            ]:
                if title is None:
                    menu.addItem_(NSMenuItem.separatorItem())
                else:
                    enabled = True
                    if action == "joinContextSegments:":
                        enabled = joinable_segment_boundary(target[1].element, target[2]) is not None
                    elif action == "pasteContextPointsAfter:":
                        enabled = self.point_clipboard_payload() is not None
                    elif action == "viewContextMedia:":
                        enabled = bool(linked_media)
                    self._plot_context_item(menu, title, action, enabled)
        elif target is not None and target[0] == "segment":
            self.context_track_target = (target[1], target[2])
            self._plot_context_item(menu, "Show Track Table", "showContextTrackTable:")
            self._plot_context_item(menu, "Show Waypoint Table", "showContextWaypointTable:")
            self._plot_context_item(menu, "Insert Waypoint Here", "insertContextSegment:")
            self._plot_context_item(menu, "Cut Track Connection Here", "cutContextSegment:")
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_location_context_items(menu)
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_view_context_items(menu)
        else:
            self.add_location_context_items(menu)
            if menu.numberOfItems() > 0:
                menu.addItem_(NSMenuItem.separatorItem())
            self.add_view_context_items(menu)
        return menu

    def add_location_context_items(self, menu):
        coordinate_available = self.coordinate_for_view_point(self.context_location) is not None
        self._plot_context_item(menu, "Show Coordinates Here", "showContextCoordinates:", coordinate_available)
        self._plot_context_item(menu, "Copy Coordinates Here", "copyMapContextCoordinates:", coordinate_available)
        self._plot_context_item(
            menu,
            "Create Waypoint Here",
            "createContextWaypointHere:",
            coordinate_available and self.waypoint_editing_enabled() and bool(self.controller.selected_tracks()),
        )
        can_insert = coordinate_available and self.cursor is not None and self.waypoint_editing_enabled()
        self._plot_context_item(menu, "Insert Waypoint Before Selected Here", "insertContextLocationBefore:", can_insert)
        self._plot_context_item(menu, "Insert Waypoint After Selected Here", "insertContextLocationAfter:", can_insert)
        self._plot_context_item(
            menu,
            "Paste Waypoint(s) at Nearest Point",
            "pasteContextPointsNearest:",
            self.waypoint_editing_enabled() and self.point_clipboard_payload() is not None,
        )

    def add_view_context_items(self, menu):
        if self.adventure_workspace_delegate is not None:
            if self.mode == "track":
                self._plot_context_item(menu, "Return to Adventure Overview", "showAdventureOverview:")
            self._plot_context_item(menu, "Fit Entire Adventure", "fitEntireAdventure:")
        self._plot_context_item(menu, "Show Elevation Profile", "showElevationProfile:")
        self._plot_context_item(menu, "Center on Selected Waypoint", "centerContextPoint:", self.cursor is not None)
        self._plot_context_item(menu, "Zoom In", "zoomContextIn:")
        self._plot_context_item(menu, "Zoom Out", "zoomContextOut:")
        self._plot_context_item(menu, "Fit to Track", "fitContextTrack:")
        if self.controller.selected_tracks():
            menu.addItem_(NSMenuItem.separatorItem())
            self._plot_context_item(
                menu,
                "Save Selected Tracks As…",
                "saveSelectedTracksAs:",
            )
        workspace = self.adventure_workspace_delegate
        if workspace is not None:
            title = (
                "Hide All Media Icons"
                if getattr(workspace, "show_media_icons", True)
                else "Show All Media Icons"
            )
            self._plot_context_item(menu, title, "toggleWorkspaceMediaIcons:")
            self._plot_context_item(menu,
                "Hide Hidden Tracks" if getattr(workspace, "show_hidden_tracks", False) else "Show Hidden Tracks",
                "toggleWorkspaceHiddenTracks:")
        elif self.waypoint_editing_enabled():
            title = (
                "Hide All Media Icons"
                if self.show_media_markers
                else "Show All Media Icons"
            )
            self._plot_context_item(menu, title, "toggleMediaIcons:")
        if self.waypoint_editing_enabled():
            if self.show_media_markers:
                title = "Show Selected Photo Thumbnail" if self.show_all_photo_thumbnails else "Show All Photo Thumbnails"
                self._plot_context_item(menu, title, "togglePhotoDisplay:")

    @objc.IBAction
    def saveSelectedTracksAs_(self, _sender):
        self.controller.saveSelectedTracksAs_(None)

    @objc.IBAction
    def showElevationProfile_(self, _sender):
        self.controller.open_elevation_profile_for_plot_view(self)

    @objc.IBAction
    def togglePhotoDisplay_(self, _sender):
        if not self.show_media_markers:
            self.show_media_markers = True
            self.show_all_photo_thumbnails = False
            self.controller.set_status("Showing media icons and the selected photo thumbnail.")
            self.setNeedsDisplay_(True)
            return
        self.show_all_photo_thumbnails = not self.show_all_photo_thumbnails
        self.controller.set_status(
            "Showing all visible photo thumbnails."
            if self.show_all_photo_thumbnails
            else "Showing a thumbnail only for the selected photo point."
        )
        self.setNeedsDisplay_(True)

    @objc.IBAction
    def toggleMediaIcons_(self, _sender):
        self.show_media_markers = not self.show_media_markers
        if not self.show_media_markers:
            self.photo_thumbnail_hit_targets = []
        self.controller.set_status(
            "Showing media icons."
            if self.show_media_markers
            else "All media icons are hidden."
        )
        self.setNeedsDisplay_(True)

    @objc.IBAction
    def toggleWorkspaceMediaIcons_(self, _sender):
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "toggleWorkspaceMediaIcons_"):
            workspace.toggleWorkspaceMediaIcons_(None)

    @objc.IBAction
    def toggleWorkspaceHiddenTracks_(self, _sender):
        workspace = self.adventure_workspace_delegate
        if workspace is not None:
            workspace.toggleWorkspaceHiddenTracks_(None)

    def prepare_context_point(self, track, index, create_inspector=True):
        inspector = self.controller.existing_inspector_for_track(track)
        if inspector is None and create_inspector:
            inspector = self.controller.open_inspector_for_track(track)
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
            selected = inspector.selected_row_indexes()
            if index not in selected:
                inspector.select_point_index(index)
            self.move_cursor_to_track_point(track, index, inspector, sync_table=False, focus_plot=False)
        else:
            points = track.points()
            if 0 <= index < len(points):
                self.cursor = (index, points[index], track)
                self.marker = None
                self.controller.select_track_in_table(track.nr)
                self.controller.refresh_elevation_profile_for_plot_view(self)
                self.update_window_title()
                self.setNeedsDisplay_(True)
        return inspector

    def context_point(self):
        target = self.context_target
        if target is None or target[0] != "point":
            return None
        return target[1], target[2]

    def point_clipboard_payload(self):
        return NSPasteboard.generalPasteboard().stringForType_(POINT_PASTEBOARD_TYPE)

    def copy_point_rows_to_clipboard(self, track, rows):
        points = track.points()
        selected = [points[row].element for row in rows if 0 <= row < len(points)]
        if not selected:
            return False
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.declareTypes_owner_([POINT_PASTEBOARD_TYPE, "public.utf8-plain-text"], None)
        payload = serialized_points(selected)
        pasteboard.setString_forType_(payload, POINT_PASTEBOARD_TYPE)
        pasteboard.setString_forType_(payload, "public.utf8-plain-text")
        self.controller.set_status(f"Copied {len(selected)} selected track points.")
        return True

    def context_selected_rows(self):
        selected = getattr(self, "context_selected_range", None)
        if selected is None:
            return None, []
        track, start, end = selected
        return track, list(range(start, end + 1))

    def nearest_waypoint_for_location(self, location):
        displayed = self.display_tracks()
        if not displayed or location is None:
            return None
        track = self.current_track_for_title() or displayed[0]
        points = track.points()
        if not points:
            return None
        transformer = self._metadata_transformer(points, self.bounds()) or self._point_transformer(points, self.bounds())
        index = min(
            range(len(points)),
            key=lambda row: (transformer(points[row])[0] - location.x) ** 2
            + (transformer(points[row])[1] - location.y) ** 2,
        )
        return track, index

    def paste_context_points(self, track, anchor):
        payload = self.point_clipboard_payload()
        if payload is None:
            show_alert("No GPX waypoints on the clipboard.")
            return
        inspector = self.controller.existing_inspector_for_track(track)
        try:
            points = deserialize_points(str(payload))
            self.push_map_edit_undo(track, inspector)
            inserted_rows = insert_points_after_row(track.element, anchor, points)
        except (ET.ParseError, ValueError) as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot paste waypoints.", str(exc))
            return
        self.controller.invalidate_track_metrics(track)
        self.controller.mark_dirty(
            f"Pasted {len(inserted_rows)} point(s) after point {anchor + 1} in {track.display_label}."
        )
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
            inspector.reload_rows()
            inspector.select_point_rows(inserted_rows)
        self.marker = inserted_rows[0] if inserted_rows else None
        self.refresh_after_map_edit(track, inserted_rows[-1] if inserted_rows else anchor)

    @objc.IBAction
    def copyContextSegment_(self, _sender):
        track, rows = self.context_selected_rows()
        if track is not None:
            self.copy_point_rows_to_clipboard(track, rows)

    def remove_context_selected_segment(self, *, copy_first=False):
        track, rows = self.context_selected_rows()
        if track is None or not rows:
            return
        if copy_first and not self.copy_point_rows_to_clipboard(track, rows):
            return
        action = "Cut" if copy_first else "Delete"
        if not confirm(f"{action} marked segment?", f"{action} {len(rows)} selected points from {track.display_label}?"):
            return
        inspector = self.controller.existing_inspector_for_track(track)
        self.push_map_edit_undo(track, inspector)
        removed = remove_rows(track.element, rows)
        self.controller.invalidate_track_metrics(track)
        self.controller.mark_dirty(f"{action} {removed} selected points from {track.display_label}.")
        self.marker = None
        next_index = min(rows[0], len(track.points()) - 1) if track.points() else None
        if inspector is not None:
            inspector.reload_rows()
        self.refresh_after_map_edit(track, next_index)

    @objc.IBAction
    def cutContextSelectedSegment_(self, _sender):
        self.remove_context_selected_segment(copy_first=True)

    @objc.IBAction
    def deleteContextSelectedSegment_(self, _sender):
        self.remove_context_selected_segment(copy_first=False)

    @objc.IBAction
    def pasteContextPointsAfter_(self, _sender):
        context = self.context_point()
        if context is not None:
            self.paste_context_points(*context)

    @objc.IBAction
    def pasteContextPointsNearest_(self, _sender):
        context = self.nearest_waypoint_for_location(self.context_location)
        if context is not None:
            self.paste_context_points(*context)

    @objc.IBAction
    def showContextTrackTable_(self, _sender):
        context = self.context_track_target
        if context is None:
            return
        track, _point_index = context
        self.controller.select_track_in_table(track.nr)
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "openTrackEditor_"):
            workspace.openTrackEditor_(None)
        else:
            self.controller.show()

    @objc.IBAction
    def showContextWaypointTable_(self, _sender):
        context = self.context_track_target
        if context is None:
            return
        track, point_index = context
        workspace = self.adventure_workspace_delegate
        if workspace is not None and hasattr(workspace, "inspect_workspace_track"):
            workspace.inspect_workspace_track(track, point_index)
            return
        inspector = self.controller.open_inspector_for_track(track)
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
            inspector.select_point_index(point_index)
            inspector.window.makeKeyAndOrderFront_(None)

    @objc.IBAction
    def inspectContextTrack_(self, sender):
        self.showContextWaypointTable_(sender)

    @objc.IBAction
    def focusContextTrack_(self, _sender):
        context = self.context_track_target
        workspace = self.adventure_workspace_delegate
        if context is not None and workspace is not None:
            workspace.focus_workspace_track(context[0], context[1], reset_viewport=True)

    @objc.IBAction
    def inspectContextPoint_(self, _sender):
        context = self.context_point()
        if context is None:
            return
        self.prepare_context_point(*context, create_inspector=False)
        self.info_overlay_dismissed = False
        self.show_info = True
        self.context_overlay_coordinate = None
        self.setNeedsDisplay_(True)

    @objc.IBAction
    def viewContextMedia_(self, _sender):
        context = self.context_point()
        if context is not None:
            self.controller.show_media_for_track_point(*context)

    @objc.IBAction
    def markContextPoint_(self, _sender):
        context = self.context_point()
        if context is None:
            return
        track, index = context
        self.prepare_context_point(track, index, create_inspector=False)
        self.marker = index
        self.sync_inspector_selection()
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.setNeedsDisplay_(True)

    @objc.IBAction
    def centerContextPoint_(self, _sender):
        if self.context_point() is not None:
            self.prepare_context_point(*self.context_point(), create_inspector=False)
        if self.cursor is not None:
            extent = self.centered_extent_on_point(self.cursor[1])
            if extent is not None:
                center_x, center_y = lonlat_to_web_mercator(
                    self.cursor[1].lon,
                    self.cursor[1].lat,
                )
                extent = zoomed_interactive_extent(extent, 2.0, center_x, center_y)
                self.render_extent(extent, status="Centered and zoomed on the selected waypoint.")

    @objc.IBAction
    def centerOnlyContextPoint_(self, _sender):
        if self.context_point() is not None:
            self.prepare_context_point(*self.context_point(), create_inspector=False)
        if self.cursor is None:
            return
        extent = self.centered_extent_on_point(self.cursor[1])
        if extent is not None:
            self.set_interactive_viewport(extent, self.current_tile_zoom())
            self.controller.sync_elevation_profile_to_plot_view(self)
            self.controller.set_status("Centered map on selected waypoint.")

    def context_inspector(self):
        context = self.context_point()
        if context is None:
            return self.inspector
        track, index = context
        inspector = self.controller.existing_inspector_for_track(track)
        self.prepare_context_point(track, index, create_inspector=False)
        return inspector

    def push_map_edit_undo(self, track, inspector=None):
        if inspector is not None:
            inspector.push_undo()
            return "inspector"
        self.controller.push_track_elements_undo([track])
        return "controller"

    def prepare_drag_undo(self, track):
        if self.drag_original_xml is not None:
            return
        inspector = self.inspector if self.inspector is not None and self.inspector.track is track else None
        self.drag_undo_owner = self.push_map_edit_undo(track, inspector)
        # Reuse the affected-track snapshot for Escape instead of serializing
        # it a second time. Merely holding a point does not create an Undo.
        self.drag_original_xml = (
            inspector.undo_stack[-1] if inspector is not None
            else self.controller.undo_stack[-1].elements[track.nr]
        )

    def discard_map_edit_undo(self, inspector=None):
        if inspector is not None and inspector.undo_stack:
            inspector.undo_stack.pop()
        elif self.controller.undo_stack:
            self.controller.undo_stack.pop()

    def refresh_after_map_edit(self, track, point_index=None):
        """Redraw edited geometry while retaining this map's live viewport."""
        if point_index is not None:
            points = track.points()
            if points:
                point_index = max(0, min(int(point_index), len(points) - 1))
                self.cursor = (point_index, points[point_index], track)
        self.update_window_title()
        if self.inspector is not None and self.inspector.track is track:
            self.inspector.reload_rows()
            if point_index is not None:
                self.inspector.select_point_index(point_index)
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.controller.redraw_open_plot_views()
        self.setNeedsDisplay_(True)

    def finish_map_structure_edit(self, track, point_index, inspector, message):
        self.controller.invalidate_track_metrics(track)
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
            inspector.reload_rows()
            inspector.select_point_index(point_index)
        self.controller.mark_dirty(message)
        self.refresh_after_map_edit(track, point_index)

    def insert_context_point(self, *, before):
        context = self.context_point()
        if context is None:
            return
        track, row = context
        inspector = self.controller.existing_inspector_for_track(track)
        from gpx_point_editing import insertion_for_row

        try:
            self.push_map_edit_undo(track, inspector)
            segment, insert_index, point = insertion_for_row(track.element, row, before=before)
            segment.insert(insert_index, point)
            inserted_row = next(
                index for index, candidate in enumerate(track.points()) if candidate.element is point
            )
        except (ValueError, StopIteration) as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot insert a waypoint.", str(exc))
            return
        position = "before" if before else "after"
        self.finish_map_structure_edit(
            track,
            inserted_row,
            inspector,
            f"Inserted waypoint {position} point {row + 1} in {track.display_label}.",
        )

    def insert_context_location(self, *, before):
        if self.cursor is None or not self.waypoint_editing_enabled():
            return
        coordinate = self.coordinate_for_view_point(self.context_location)
        if coordinate is None:
            return
        track = self.cursor[2]
        row = self.cursor[0]
        inspector = self.controller.existing_inspector_for_track(track)
        from gpx_point_editing import insertion_for_row

        try:
            self.push_map_edit_undo(track, inspector)
            segment, insert_index, point = insertion_for_row(track.element, row, before=before)
            longitude, latitude = coordinate
            point.set("lat", f"{latitude:.8f}")
            point.set("lon", f"{longitude:.8f}")
            segment.insert(insert_index, point)
            inserted_row = next(
                index for index, candidate in enumerate(track.points()) if candidate.element is point
            )
        except (ValueError, StopIteration) as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot insert a waypoint.", str(exc))
            return
        position = "before" if before else "after"
        self.finish_map_structure_edit(
            track,
            inserted_row,
            inspector,
            f"Inserted waypoint at the map location {position} point {row + 1} in {track.display_label}.",
        )

    @objc.IBAction
    def createContextWaypointHere_(self, _sender):
        coordinate = self.coordinate_for_view_point(self.context_location)
        tracks = self.track_sequence()
        if coordinate is None or not self.waypoint_editing_enabled() or not tracks:
            return
        selected_tracks = self.controller.selected_tracks()
        track = self.cursor[2] if self.cursor is not None else selected_tracks[0]
        longitude, latitude = coordinate
        old_count = len(track.points())
        inspector = self.controller.existing_inspector_for_track(track)
        try:
            self.push_map_edit_undo(track, inspector)
            point = point_from_values(track.element, latitude, longitude)
            target = self.context_target
            if target is not None and target[0] == "segment" and target[1] is track:
                anchor = int(target[2])
            elif self.cursor is not None and self.cursor[2] is track:
                anchor = int(self.cursor[0])
            else:
                anchor = max(-1, old_count - 1)
            inserted_rows = insert_points_after_row(track.element, anchor, [point])
            inserted_row = inserted_rows[0]
        except (ValueError, IndexError) as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot create a waypoint.", str(exc))
            return
        self.finish_map_structure_edit(
            track,
            inserted_row,
            inspector,
            f"Created waypoint {inserted_row + 1} at the selected map location.",
        )
        if old_count == 0:
            point_info = track.points()[0]
            self.render_extent(
                self.controller.extent_for_track_records([track]),
                status="Centered the map on the first waypoint.",
            )
            self.move_cursor_to_track_point(track, 0, inspector, sync_table=False, focus_plot=False)

    @objc.IBAction
    def insertContextLocationBefore_(self, _sender):
        self.insert_context_location(before=True)

    @objc.IBAction
    def insertContextLocationAfter_(self, _sender):
        self.insert_context_location(before=False)

    @objc.IBAction
    def insertContextBefore_(self, _sender):
        self.insert_context_point(before=True)

    @objc.IBAction
    def insertContextAfter_(self, _sender):
        self.insert_context_point(before=False)

    @objc.IBAction
    def copyContextPoints_(self, _sender):
        inspector = self.context_inspector()
        if inspector is not None:
            inspector.copy_selected_points()
            return
        context = self.context_point()
        if context is None:
            return
        point = context[0].points()[context[1]].element
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.declareTypes_owner_(
            [POINT_PASTEBOARD_TYPE, "public.utf8-plain-text"],
            None,
        )
        pasteboard.setString_forType_(serialized_points([point]), POINT_PASTEBOARD_TYPE)
        pasteboard.setString_forType_(ET.tostring(point, encoding="unicode"), "public.utf8-plain-text")

    @objc.IBAction
    def copyContextCoordinates_(self, _sender):
        context = self.context_point()
        if context is None:
            return
        point = context[0].points()[context[1]]
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.declareTypes_owner_(["public.utf8-plain-text"], None)
        pasteboard.setString_forType_(f"{point.lat:.8f}, {point.lon:.8f}", "public.utf8-plain-text")

    @objc.IBAction
    def showContextCoordinates_(self, _sender):
        coordinate = self.coordinate_for_view_point(self.context_location)
        if coordinate is None:
            return
        self.context_overlay_coordinate = (
            coordinate[0],
            coordinate[1],
            self.elevation_for_view_location(self.context_location),
        )
        self.info_overlay_dismissed = False
        self.show_info = True
        self.setNeedsDisplay_(True)

    @objc.IBAction
    def copyMapContextCoordinates_(self, _sender):
        coordinate = self.coordinate_for_view_point(self.context_location)
        if coordinate is None:
            return
        longitude, latitude = coordinate
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.declareTypes_owner_(["public.utf8-plain-text"], None)
        pasteboard.setString_forType_(f"{latitude:.8f}, {longitude:.8f}", "public.utf8-plain-text")
        self.controller.set_status("Copied map coordinates.")

    @objc.IBAction
    def deleteContextPoints_(self, _sender):
        inspector = self.context_inspector()
        if inspector is not None:
            inspector.delete_selected_points()
            return
        context = self.context_point()
        if context is None:
            return
        track, row = context
        if not confirm("Delete waypoint?", f"Delete point {row + 1} from {track.display_label}?"):
            return
        points = track.points()
        if not (0 <= row < len(points)):
            return
        self.controller.push_undo()
        target = points[row].element
        for segment in track.element.findall("gpx:trkseg", NS):
            if target in list(segment):
                segment.remove(target)
                break
        self.controller.invalidate_track_metrics(track)
        self.controller.mark_dirty(f"Deleted point {row + 1} from {track.display_label}.")
        self.refresh_after_map_edit(track, min(row, max(0, len(track.points()) - 1)))

    @objc.IBAction
    def splitContextAbove_(self, _sender):
        inspector = self.context_inspector()
        if inspector is not None:
            inspector.splitTrack_(None)
            return
        context = self.context_point()
        if context is None:
            return
        track, row = context
        if not confirm(
            "Split track above selected point?",
            f"Move waypoint {row + 1} and all following waypoints to a new track?",
        ):
            return
        new_record = self.controller.split_track_from_index(track, row)
        if new_record is not None:
            self.refresh_after_map_edit(track, max(0, row - 1))

    @objc.IBAction
    def anchorContextPoint_(self, _sender):
        context = self.context_point()
        if context is not None:
            self.controller.set_anchor_from_point(context[0].points()[context[1]])

    @objc.IBAction
    def undoContextEdit_(self, _sender):
        inspector = self.context_inspector()
        if inspector is not None and inspector.undo_stack:
            inspector.undo()
        else:
            self.controller.undo_(None)

    @objc.IBAction
    def redoContextEdit_(self, _sender):
        inspector = self.context_inspector()
        if inspector is not None and inspector.redo_stack:
            inspector.redo()
        else:
            self.controller.redo_(None)

    @objc.IBAction
    def insertContextSegment_(self, _sender):
        target = self.context_target
        if target is None or target[0] != "segment":
            return
        _kind, track, row, _fraction = target
        inspector = self.controller.existing_inspector_for_track(track)
        from gpx_point_editing import insertion_for_row

        try:
            self.push_map_edit_undo(track, inspector)
            segment, insert_index, point = insertion_for_row(track.element, row, before=False)
            coordinate = self.coordinate_for_view_point(self.context_location)
            if coordinate is not None:
                longitude, latitude = coordinate
                point.set("lat", f"{latitude:.8f}")
                point.set("lon", f"{longitude:.8f}")
            segment.insert(insert_index, point)
            inserted_row = next(
                index for index, candidate in enumerate(track.points()) if candidate.element is point
            )
        except (ValueError, StopIteration) as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot insert a waypoint.", str(exc))
            return
        self.finish_map_structure_edit(
            track,
            inserted_row,
            inspector,
            f"Inserted waypoint on a segment in {track.display_label}.",
        )

    @objc.IBAction
    def cutContextSegment_(self, _sender):
        target = self.context_target
        if target is None or target[0] != "segment":
            return
        _kind, track, row, _fraction = target
        inspector = self.controller.existing_inspector_for_track(track)
        try:
            self.push_map_edit_undo(track, inspector)
            cut_segment_after_row(track.element, row)
        except ValueError as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot cut the track connection.", str(exc))
            return
        self.finish_map_structure_edit(
            track,
            row,
            inspector,
            f"Cut the track connection after point {row + 1} in {track.display_label}.",
        )

    @objc.IBAction
    def cutContextPoint_(self, _sender):
        context = self.context_point()
        if context is None:
            return
        track, row = context
        inspector = self.controller.existing_inspector_for_track(track)
        try:
            self.push_map_edit_undo(track, inspector)
            cut_segment_after_row(track.element, row)
        except ValueError as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot cut the track connection.", str(exc))
            return
        self.finish_map_structure_edit(
            track,
            row,
            inspector,
            f"Cut the track connection after point {row + 1} in {track.display_label}.",
        )

    @objc.IBAction
    def joinContextSegments_(self, _sender):
        context = self.context_point()
        if context is None:
            return
        track, row = context
        inspector = self.controller.existing_inspector_for_track(track)
        try:
            self.push_map_edit_undo(track, inspector)
            join_segments_at_row(track.element, row)
        except ValueError as exc:
            self.discard_map_edit_undo(inspector)
            show_alert("Cannot join track segments.", str(exc))
            return
        self.finish_map_structure_edit(
            track,
            row,
            inspector,
            f"Joined track segments at point {row + 1} in {track.display_label}.",
        )

    @objc.IBAction
    def zoomContextIn_(self, _sender):
        self.change_view_zoom(2.0, focus_location=self.context_location)

    @objc.IBAction
    def zoomContextOut_(self, _sender):
        self.change_view_zoom(0.5, focus_location=self.context_location)

    @objc.IBAction
    def fitContextTrack_(self, _sender):
        context = self.context_track_target
        track = context[0] if context is not None else None
        if track is None and self.cursor is not None:
            track = self.cursor[2]
        if track is None:
            selected = self.controller.selected_tracks()
            track = selected[0] if len(selected) == 1 else None
        if track is None:
            self.controller.set_status("Select a track to fit first.")
            return
        workspace = self.adventure_workspace_delegate
        if workspace is not None:
            workspace.focus_workspace_track(track, reset_viewport=True)
            return
        extent = self.controller.extent_for_track_records([track])
        self.render_extent(extent, status=f"Fitted {track.display_label} to the map.")

    @objc.IBAction
    def showAdventureOverview_(self, _sender):
        workspace = self.adventure_workspace_delegate
        if workspace is not None:
            workspace.show_workspace_overview()

    @objc.IBAction
    def fitEntireAdventure_(self, _sender):
        workspace = self.adventure_workspace_delegate
        if workspace is not None:
            if workspace.show_workspace_overview(reset_viewport=True) is not None:
                self.controller.set_status("Fitted the Adventure overview to all visible tracks.")
            return
        tracks = self.controller.visible_tracks()
        info = self.controller.interactive_plot_info("overview", tracks_override=tracks)
        if info is None:
            return
        current = self.current_track_for_title()
        if current is not None:
            info["current_track_nr"] = current.nr
        self.mode = "overview"
        self.replace_plot_info(info)
        self.render_extent(
            info["metadata"]["extent_mercator"],
            status="Fitted the Adventure overview to all visible tracks.",
        )

    @objc.IBAction
    def beginPointEditingLongPress_(self, _timer):
        self.long_press_timer = None
        if self.long_press_origin is None:
            return
        target = self.long_press_target or self.nearest_edit_target(self.long_press_origin)
        if target is None:
            self.controller.set_status(
                "Hold closer to a waypoint or track segment to edit it."
            )
            return
        # A held media pin is an editing gesture, not a delayed media click.
        self.clicked_media_target = None
        self.clicked_workspace_media_target = None
        kind, track, index, fraction = target
        inspector = self.controller.existing_inspector_for_track(track)
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
        self.drag_undo_owner = None
        self.drag_original_xml = None
        selected_range = self.selected_range_at_target(target)
        if selected_range is not None:
            _selected_track, start, end = selected_range
            points = track.points()
            self.dragged_segment_rows = list(range(start, end + 1))
            self.dragged_segment_elements = [points[row].element for row in self.dragged_segment_rows]
            self.dragged_segment_original_coordinates = [
                (points[row].lon, points[row].lat) for row in self.dragged_segment_rows
            ]
            coordinate = self.coordinate_for_view_point(self.long_press_origin)
            if coordinate is None:
                self.dragged_segment_rows = []
                return
            self.dragged_segment_origin_mercator = lonlat_to_web_mercator(*coordinate)
            self.dragged_point_track = track
            self.controller.set_status(
                f"Drag the marked segment ({len(self.dragged_segment_rows)} points); release to keep it or press Escape to cancel."
            )
            self.setNeedsDisplay_(True)
            return
        if kind == "segment":
            from gpx_point_editing import insertion_for_row

            try:
                self.prepare_drag_undo(track)
                segment, insert_index, point = insertion_for_row(
                    track.element,
                    index,
                    before=False,
                )
                coordinate = self.coordinate_for_view_point(self.long_press_origin)
                if coordinate is not None:
                    longitude, latitude = coordinate
                    point.set("lat", f"{latitude:.8f}")
                    point.set("lon", f"{longitude:.8f}")
                segment.insert(insert_index, point)
                self.controller.invalidate_track_metrics(track)
                index = next(
                    row
                    for row, candidate in enumerate(track.points())
                    if candidate.element is point
                )
            except ValueError as exc:
                self.discard_map_edit_undo(inspector)
                show_alert("Cannot insert a waypoint.", str(exc))
                return
        self.drag_inserted_point = kind == "segment"
        self.dragged_point_index = index
        self.dragged_point_track = track
        points = track.points()
        if 0 <= index < len(points):
            self.cursor = (index, points[index], track)
        self.controller.set_status(
            f"Drag point {index + 1}; release to keep it or press Escape to cancel."
        )
        self.update_window_title()
        self.setNeedsDisplay_(True)

    def scrollWheel_(self, event):
        if not bool(event.hasPreciseScrollingDeltas()):
            delta_y = float(event.scrollingDeltaY())
            factor = mouse_wheel_zoom_factor(delta_y)
            if factor != 1.0:
                location = self.convertPoint_fromView_(event.locationInWindow(), None)
                self.last_gesture_location = location
                extent = self.zoomed_extent(factor, focus_location=location)
                if extent is not None:
                    self.set_interactive_viewport(
                        extent,
                        self.current_tile_zoom(),
                        request_tiles=False,
                    )
                    if self.wheel_finish_timer is not None:
                        self.wheel_finish_timer.invalidate()
                    self.wheel_finish_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        0.15,
                        self,
                        "finishWheelZoom:",
                        None,
                        False,
                    )
                return
        delta_x, delta_y = scroll_pan_deltas(
            event.scrollingDeltaX(),
            event.scrollingDeltaY(),
            reverse_vertical=self.adventure_workspace_delegate is not None,
        )
        self.pan_map(delta_x, delta_y)

    def pan_map(self, delta_x: float, delta_y: float):
        extent = self.shifted_extent(delta_x, delta_y)
        if extent is None:
            return
        self.set_interactive_viewport(extent)
        self.controller.sync_elevation_profile_to_plot_view(self)

    def current_extent(self):
        extent = (self.plot_info.get("metadata") or {}).get("extent_mercator")
        return dict(extent) if extent else None

    def current_tile_zoom(self):
        return int(
            self.plot_info.get(
                "tile_zoom_level",
                self.plot_info.get("zoom_level", 14 if self.mode == "track" else 8),
            )
        )

    def set_interactive_viewport(
        self,
        extent,
        tile_zoom=None,
        request_tiles=True,
    ):
        if not extent:
            return
        self.ensure_interactive_tile_store_provider()
        extent = clamp_extent_to_web_mercator(extent)
        requested_zoom = self.current_tile_zoom() if tile_zoom is None else int(tile_zoom)
        if request_tiles:
            requested_zoom, _diagnostics = self.controller.effective_tile_zoom(
                extent,
                requested_zoom,
            )
        metadata = dict(self.plot_info.get("metadata") or {})
        metadata.update(
            {
                "crs": "EPSG:3857",
                "extent_mercator": dict(extent),
                "axes_box_fraction": {"left": 0.0, "bottom": 0.0, "width": 1.0, "height": 1.0},
            }
        )
        self.plot_info["metadata"] = metadata
        self.plot_info["tile_zoom_level"] = requested_zoom
        self.plot_info["zoom_level"] = requested_zoom
        self.interactive_tiles_active = True
        self.interactive_tile_generation += 1
        self.interactive_provider_failed_generation = None
        self.cancel_interactive_tile_requests(shutdown=False)
        self.zoom = 1.0
        self.setNeedsDisplay_(True)
        if request_tiles:
            self.schedule_interactive_tiles()

    def schedule_interactive_tiles(self, immediate=False):
        if getattr(self, "plot_closed", False):
            return
        if self.interactive_tile_timer is not None:
            self.interactive_tile_timer.invalidate()
        self.interactive_tile_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0 if immediate else 0.15,
            self,
            "requestInteractiveTiles:",
            None,
            False,
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(
            self.interactive_tile_timer, NSRunLoopCommonModes
        )

    @objc.IBAction
    def requestInteractiveTiles_(self, _timer):
        if getattr(self, "plot_closed", False):
            return
        if _timer is not None and _timer != self.interactive_tile_timer:
            return
        self.interactive_tile_timer = None
        # Gesture previews cancel this timer and never schedule requests. A
        # settled viewport must not be vetoed by unrelated mouse-event state.
        if not self.interactive_tiles_active:
            return
        extent = self.current_extent()
        if not extent:
            return
        # Saved viewports and gesture previews can carry a coarse zoom. The
        # final request must derive its resolution from the live map scale.
        effective_zoom = self.recommended_tile_zoom(extent)
        if effective_zoom != self.current_tile_zoom():
            self.plot_info["tile_zoom_level"] = effective_zoom
            self.plot_info["zoom_level"] = effective_zoom
            self.interactive_tile_generation += 1
            self.cancel_interactive_tile_requests(shutdown=False)
        generation = self.interactive_tile_generation
        provider_key = self.ensure_interactive_tile_store_provider()
        target_tiles = visible_tiles(extent, self.current_tile_zoom())
        self.interactive_tile_target_keys = {
            (tile.zoom, tile.x, tile.y) for tile in target_tiles
        }
        for tile in target_tiles:
            key = (tile.zoom, tile.x, tile.y)
            pending_key = (generation, key)
            if key in self.interactive_tiles or pending_key in self.interactive_tile_futures:
                continue
            future = self.interactive_tile_executor.submit(
                load_tile_png,
                tile,
                provider=self.controller.map_provider,
                cache_dir=self.controller.tile_cache_dir,
                timeout_seconds=self.controller.map_request_timeout_seconds,
                custom_url=self.controller.custom_map_url,
                custom_attribution=self.controller.custom_map_attribution,
                maximum_zoom=self.controller.maximum_map_zoom,
                credential_id=self.controller.map_credential_id,
            )
            self.interactive_tile_futures[pending_key] = future
            future.add_done_callback(
                lambda completed, tile_key=key, expected_generation=generation, expected_provider=provider_key: self.deliver_interactive_tile(
                    tile_key,
                    expected_generation,
                    expected_provider,
                    completed,
                )
            )

    def deliver_interactive_tile(self, key, generation, provider_key, future):
        try:
            png_data = future.result()
            ocean_samples = sample_world_ocean_rgb_from_png(
                png_data,
                TileCoordinate(*key),
            )
            payload = {
                "key": key,
                "generation": generation,
                "provider_key": provider_key,
                "png": png_data,
                "ocean_samples": ocean_samples,
                "error": "",
            }
        except Exception as exc:
            payload = {
                "key": key,
                "generation": generation,
                "provider_key": provider_key,
                "png": b"",
                "ocean_samples": [],
                "error": str(exc),
            }
        try:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "installInteractiveTile:", payload, False
            )
        except Exception:
            pass

    @objc.IBAction
    def installInteractiveTile_(self, payload):
        if getattr(self, "plot_closed", False):
            return
        key = tuple(payload.get("key") or ())
        generation = int(payload.get("generation", -1))
        self.interactive_tile_futures.pop((generation, key), None)
        provider_key = str(payload.get("provider_key") or "")
        if provider_key != self.interactive_tile_provider_cache_key():
            return
        error = str(payload.get("error") or "")
        if error:
            if (
                generation == self.interactive_tile_generation
                and self.interactive_provider_failed_generation != generation
            ):
                self.interactive_provider_failed_generation = generation
                self.controller.set_status(f"Map tile unavailable: {error}")
                if "HTTP 403" in error or "HTTP 429" in error or "blocked tile access" in error:
                    self.cancel_interactive_tile_requests(shutdown=False)
            return
        image = nsimage_from_png_bytes(bytes(payload.get("png") or b""))
        if image is None:
            return
        self.remember_interactive_tile(key, image)
        if not self.interactive_tiles_active:
            return
        tile = TileCoordinate(*key)
        extent = self.current_extent()
        backdrop_changed = self.update_interactive_world_ocean_color(
            list(payload.get("ocean_samples") or [])
        )
        if backdrop_changed:
            self.setNeedsDisplay_(True)
        if extent and tile_intersects_extent(tile, extent):
            self.setNeedsDisplayInRect_(
                self.tile_view_rect(tile, extent, self.interactive_map_rect(self.bounds()))
            )

    def cancel_interactive_tile_requests(self, shutdown=False):
        if self.interactive_tile_timer is not None:
            self.interactive_tile_timer.invalidate()
            self.interactive_tile_timer = None
        for future in list(self.interactive_tile_futures.values()):
            future.cancel()
        self.interactive_tile_futures.clear()
        if shutdown and self.interactive_tile_executor is not None:
            if self.wheel_finish_timer is not None:
                self.wheel_finish_timer.invalidate()
                self.wheel_finish_timer = None
            if self.pinch_finish_timer is not None:
                self.pinch_finish_timer.invalidate()
                self.pinch_finish_timer = None
            self.interactive_tile_generation += 1
            self.interactive_tile_target_keys.clear()
            self.interactive_tile_executor.shutdown(wait=False, cancel_futures=True)
            self.interactive_tile_executor = None

    def recommended_tile_zoom(self, extent: dict) -> int:
        bounds = self.bounds()
        _x, _y, width, height = aspect_preserving_rect(
            bounds.size.width,
            bounds.size.height,
            extent,
        )
        requested = tile_zoom_for_viewport(
            extent,
            width,
            height,
            maximum_zoom=self.controller.maximum_map_zoom,
        )
        effective, _diagnostics = self.controller.effective_tile_zoom(extent, requested)
        return effective

    def render_extent(self, extent: dict, requested_tile_zoom: int | None = None, status: str = "Rendered OSM viewport."):
        bounds = self.bounds()
        extent = self.controller.fit_extent_to_aspect(
            extent,
            (max(bounds.size.width, 1.0), max(bounds.size.height, 1.0)),
        )
        if requested_tile_zoom is None:
            requested_tile_zoom = self.recommended_tile_zoom(extent)
        self.set_interactive_viewport(extent, requested_tile_zoom)
        self.update_window_title()
        self.controller.set_status(status)
        self.controller.sync_elevation_profile_to_plot_view(self)

    def rerender_current_map(self):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not extent:
            self.setNeedsDisplay_(True)
            return
        self.interactive_tiles.clear()
        self.interactive_tile_target_keys.clear()
        self.set_interactive_viewport(dict(extent), self.recommended_tile_zoom(extent))
        self.cursor = None
        self.marker = None

    def shifted_extent(self, delta_x: float, delta_y: float):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not extent:
            return None
        bounds = self.bounds()
        map_rect = self.interactive_map_rect(bounds)
        width = max(map_rect.size.width, 1.0)
        height = max(map_rect.size.height, 1.0)
        return shifted_interactive_extent(extent, delta_x, delta_y, width, height)

    def magnifyWithEvent_(self, event):
        magnification = float(event.magnification())
        if abs(magnification) < 0.001:
            return
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.last_gesture_location = location
        factor = max(0.20, 1.0 + magnification)
        if self.pinch_start_tile_zoom is None:
            self.pinch_start_tile_zoom = self.current_tile_zoom()
            self.pinch_accumulated_scale = 1.0
        self.pinch_accumulated_scale *= factor
        extent = self.zoomed_extent(factor, focus_location=location)
        if extent is None:
            return
        self.set_interactive_viewport(
            extent,
            self.current_tile_zoom(),
            request_tiles=False,
        )
        if self.pinch_finish_timer is not None:
            self.pinch_finish_timer.invalidate()
        try:
            phase = int(event.phase())
        except Exception:
            phase = 0
        delay = 0.0 if phase & (8 | 16) else 0.15
        self.pinch_finish_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay,
            self,
            "finishPinchZoom:",
            None,
            False,
        )

    @objc.IBAction
    def finishPinchZoom_(self, _timer):
        self.pinch_finish_timer = None
        if self.pinch_start_tile_zoom is None:
            return
        self.pinch_start_tile_zoom = None
        self.pinch_accumulated_scale = 1.0
        extent = self.current_extent()
        if extent is not None:
            requested_zoom = self.recommended_tile_zoom(extent)
            self.set_interactive_viewport(extent, requested_zoom, request_tiles=True)
            self.controller.sync_elevation_profile_to_plot_view(self)

    @objc.IBAction
    def finishWheelZoom_(self, _timer):
        self.wheel_finish_timer = None
        extent = self.current_extent()
        if extent is None:
            return
        self.set_interactive_viewport(
            extent,
            self.recommended_tile_zoom(extent),
            request_tiles=True,
        )
        self.controller.sync_elevation_profile_to_plot_view(self)
        self.controller.set_status(
            f"Map zoom {self.current_tile_zoom()}; loading visible tiles."
        )

    def centered_extent_on_point(self, point: PointInfo):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not extent:
            return None
        center_x, center_y = lonlat_to_web_mercator(point.lon, point.lat)
        span_x = float(extent["max_x"]) - float(extent["min_x"])
        span_y = float(extent["max_y"]) - float(extent["min_y"])
        return {
            "min_x": center_x - span_x / 2.0,
            "max_x": center_x + span_x / 2.0,
            "min_y": center_y - span_y / 2.0,
            "max_y": center_y + span_y / 2.0,
        }

    def center_on_cursor(self):
        if self.cursor is None:
            self.controller.set_status("No cursor point to center on.")
            return
        extent = self.centered_extent_on_point(self.cursor[1])
        if extent is None:
            return
        self.render_extent(extent, status="Centered map on cursor point.")

    def extent_for_points(self, points: list[PointInfo]):
        projected = [lonlat_to_web_mercator(point.lon, point.lat) for point in points]
        if not projected:
            return None
        xs = [point[0] for point in projected]
        ys = [point[1] for point in projected]
        span_x = max(max(xs) - min(xs), 1.0)
        span_y = max(max(ys) - min(ys), 1.0)
        padding_x = span_x * 0.10
        padding_y = span_y * 0.10
        return {
            "min_x": min(xs) - padding_x,
            "max_x": max(xs) + padding_x,
            "min_y": min(ys) - padding_y,
            "max_y": max(ys) + padding_y,
        }

    def zoom_to_selected(self):
        track = self.current_track_for_title()
        if track is None and self.cursor is not None:
            track = self.cursor[2]
        ranges = self.selected_ranges_for_track(track) if track is not None else []
        if ranges:
            points = track.points()
            selected_points = [
                point for index, point in enumerate(points)
                if any(start <= index <= end for start, end in ranges)
            ]
            extent = self.extent_for_points(selected_points)
            if extent is not None:
                self.render_extent(extent, status="Zoomed track map to selected points.")
                return
        if self.mode == "overview":
            tracks = self.controller.selected_tracks()
            if not tracks:
                tracks = self.controller.visible_tracks()
            tracks = [track for track in tracks if not track.hidden or
                      getattr(self.adventure_workspace_delegate, "show_hidden_tracks", False)]
            extent = self.controller.extent_for_track_records(tracks)
            self.render_extent(extent, status="Zoomed overview to selected tracks.")
            return
        if track is None:
            return
        extent = self.controller.extent_for_track_records([track])
        if extent is None:
            return
        self.render_extent(extent, status="Zoomed track map to selected points.")

    def move_cursor_to_event(self, event):
        tracks = self.display_tracks()
        if self.adventure_workspace_delegate is not None and self.mode == "overview":
            selected = self.controller.selected_tracks()
            if len(selected) != 1:
                return
            tracks = selected
        points = [(track, index, point) for track in tracks for index, point in enumerate(track.points())]
        if not points:
            return
        all_points = [item[2] for item in points]
        transformer = self._metadata_transformer(all_points, self.bounds()) or self._point_transformer(all_points, self.bounds())
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        best = min(points, key=lambda item: (transformer(item[2])[0] - location.x) ** 2 + (transformer(item[2])[1] - location.y) ** 2)
        if self.cursor is None or self.cursor[2] is not best[0]:
            self.marker = None
        self.cursor = (best[1], best[2], best[0])
        self.context_overlay_coordinate = None
        self.show_info = not self.info_overlay_dismissed
        self.controller.select_track_in_table(best[0].nr)
        self.sync_inspector_selection()
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.update_window_title()
        self.setNeedsDisplay_(True)

    def move_cursor_to_track_point(
        self,
        track: TrackRecord,
        point_index: int,
        inspector=None,
        sync_table: bool = True,
        focus_plot: bool = True,
        preserve_marker: bool = False,
    ):
        points = track.points()
        if point_index < 0 or point_index >= len(points):
            return
        sequence = self.track_sequence()
        for index, selected_track in enumerate(sequence):
            if selected_track is track:
                self.track_index = index
                break
        self.update_track_plot_info(track)
        previous_track = self.cursor[2] if self.cursor is not None else None
        self.cursor = (point_index, points[point_index], track)
        self.context_overlay_coordinate = None
        self.show_info = not self.info_overlay_dismissed
        if not preserve_marker or previous_track is not track:
            self.marker = None
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
            if sync_table:
                self.sync_inspector_selection()
        window = self.window()
        if focus_plot and window is not None:
            window.makeFirstResponder_(self)
        self.update_window_title()
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.setNeedsDisplay_(True)

    def sync_inspector_selection(self):
        if self.inspector is None or self.cursor is None or self.cursor[2] is not self.inspector.track:
            return
        if self.marker is not None:
            self.inspector.select_point_range(min(self.marker, self.cursor[0]), max(self.marker, self.cursor[0]))
        else:
            self.inspector.select_point_index(self.cursor[0])

    def unselect_plot_items(self):
        self.marker = None
        if self.mode == "overview":
            self.controller.selected_nrs = []
            self.controller.update_selection_field()
            self.controller.highlight_selected_rows()
            self.cursor = None
        elif self.inspector is not None:
            self.inspector.clear_point_selection()
        self.update_window_title()
        self.setNeedsDisplay_(True)

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        key_code = event.keyCode()
        command_down = bool(event.modifierFlags() & NSEventModifierFlagCommand)
        option_down = bool(event.modifierFlags() & NSEventModifierFlagOption)
        shift_down = bool(event.modifierFlags() & NSEventModifierFlagShift)
        zoom_action = plot_zoom_key_action(key, event.modifierFlags())
        self.transient_help_until = None
        workspace = self.adventure_workspace_delegate
        if workspace is not None and workspace.handleWorkspaceKeyEvent_(event):
            return
        if key == "\x1b" and self.workspace_media_selection_active:
            self.workspace_media_selection_active = False
            if hasattr(workspace, "cancelWorkspaceMediaSelection_"):
                workspace.cancelWorkspaceMediaSelection_(None)
            return
        if key == "\x1b":
            self.dismiss_info_overlay()
            self.show_help = False
            self.transient_help_until = None
        if key == "\x1b" and self.pan_active:
            if self.pan_restore_extent is not None:
                self.set_interactive_viewport(self.pan_restore_extent)
            self.pan_active = False
            self.pan_origin = None
            self.pan_start_extent = None
            self.controller.set_status("Cancelled map pan.")
            return
        if key == "\x1b" and (
            self.dragged_point_index is not None or self.dragged_segment_rows
        ):
            track = self.dragged_point_track
            if track is not None and self.drag_original_xml is not None:
                track.element = ET.fromstring(self.drag_original_xml)
                self.controller.invalidate_track_metrics(track)
                if self.inspector is not None:
                    if self.drag_undo_owner == "inspector" and self.inspector.undo_stack:
                        self.inspector.undo_stack.pop()
                    self.inspector.reload_rows()
                if self.drag_undo_owner == "controller" and self.controller.undo_stack:
                    self.controller.undo_stack.pop()
                self.refresh_after_map_edit(track, self.dragged_point_index)
            self.dragged_point_index = None
            self.dragged_point_track = None
            self.dragged_segment_rows = []
            self.dragged_segment_elements = []
            self.dragged_segment_original_coordinates = []
            self.dragged_segment_origin_mercator = None
            self.drag_original_xml = None
            self.drag_inserted_point = False
            self.drag_undo_owner = None
            self.controller.set_status("Cancelled waypoint drag.")
            self.setNeedsDisplay_(True)
            return
        if key == "\x1b":
            self.setNeedsDisplay_(True)
            return
        if key not in {"h", "H"}:
            self.show_help = False
        if command_down and key.casefold() == "z":
            if shift_down:
                if self.inspector is not None and self.inspector.redo_stack:
                    self.inspector.redo()
                else:
                    self.controller.redo_(None)
            elif self.inspector is not None and self.inspector.undo_stack:
                self.inspector.undo()
            else:
                self.controller.undo_(None)
            return
        if command_down and self.mode == "track" and key.casefold() == "x":
            self.cut_track()
            self.setNeedsDisplay_(True)
            return
        if (key in {"+", "=", "-", "_", "c", "C"} or zoom_action is not None) and event.isARepeat():
            return
        if height_interpolation_key(key, event.modifierFlags()):
            if not event.isARepeat():
                self.interpolate_selected_heights()
            return
        if key in {"i", "I"}:
            self.show_info = not self.show_info
            self.info_overlay_dismissed = not self.show_info
            if self.show_info:
                self.context_overlay_coordinate = None
        elif key in {"h", "H"}:
            self.show_help = not self.show_help
        elif key in {"q", "Q"}:
            window = self.window()
            if window is not None:
                window.performClose_(None)
            return
        elif key in {"u", "U"}:
            self.unselect_plot_items()
        elif key in {"s", "S"}:
            self.controller.save_plot_view_png(self)
        elif self.mode == "track" and key in {"p", "P"}:
            self.togglePhotoDisplay_(None)
        elif key in {"e", "E"}:
            self.controller.open_elevation_profile_for_plot_view(self)
        elif self.mode == "overview" and key in {"n", "N"}:
            self.show_track_numbers = not self.show_track_numbers
        elif key in {"d", "D"}:
            self.show_endpoint_markers = not self.show_endpoint_markers
            self.controller.set_status(
                "Start/end dots shown." if self.show_endpoint_markers else "Start/end dots hidden."
            )
        elif zoom_action == "all":
            self.fitEntireAdventure_(None)
        elif key == "a" and not command_down and not option_down and self.cursor is not None:
            self.controller.set_anchor_from_point(self.cursor[1])
        elif option_down and (key in {"\uf702", "\uf700", "\uf703", "\uf701"} or key_code in {123, 124, 125, 126}):
            width = self.bounds().size.width * 0.20
            height = self.bounds().size.height * 0.20
            if key_code == 123:
                self.pan_map(width, 0.0)
            elif key_code == 124:
                self.pan_map(-width, 0.0)
            elif key_code == 125:
                self.pan_map(0.0, height)
            else:
                self.pan_map(0.0, -height)
            return
        elif key in {"+", "="}:
            if command_down:
                self.change_view_zoom(4.0)
            else:
                self.change_view_zoom(2.0)
        elif key in {"-", "_"}:
            if command_down:
                self.change_view_zoom(0.25)
            else:
                self.change_view_zoom(0.5)
        elif key in {"c", "C"}:
            self.center_on_cursor()
        elif zoom_action in {"selection", "track"}:
            if zoom_action == "track":
                self.reset_view()
            else:
                self.zoom_to_selected()
        elif self.mode == "track" and (key in {" ", "\uf703", "\uf701"} or key_code in {124, 125}):
            self.switch_track(1)
            return
        elif self.mode == "track" and (key in {"\uf702", "\uf700"} or key_code in {123, 126}):
            self.switch_track(-1)
            return
        elif (
            self.mode == "track" or self.adventure_workspace_delegate is not None
        ) and key in {"m", "M"} and self.cursor is not None and not (
            command_down or option_down or event.modifierFlags() & NSEventModifierFlagControl
        ):
            if not event.isARepeat():
                self.set_selection_marker(swap=shift_down or key == "M")
        elif key in {"\x08", "\x7f", "\uf728"} and (
            self.mode == "track" or self.adventure_workspace_delegate is not None
        ):
            if self.marker is not None and self.cursor is not None:
                self.delete_marked_points()
            elif self.mode == "track":
                self.delete_cursor_waypoint()
        elif self.mode == "track" and key in {"x", "X"}:
            self.cut_track()
        if self.inspector is not None and self.marker is not None and self.cursor is not None and self.cursor[2] is self.inspector.track:
            self.inspector.select_point_range(min(self.marker, self.cursor[0]), max(self.marker, self.cursor[0]))
        sequence_length = len(self.track_sequence()) if self.mode == "track" else len(self.controller.tracks)
        self.track_index = max(0, min(self.track_index, max(0, sequence_length - 1)))
        self.setNeedsDisplay_(True)

    def reset_view(self):
        track = self.current_track_for_title()
        if track is None and self.cursor is not None:
            track = self.cursor[2]
        if track is None:
            selected = self.controller.selected_tracks()
            track = selected[0] if len(selected) == 1 else None
        if track is None:
            self.controller.set_status("Select a track to fit first; press A to show all tracks.")
            return
        extent = self.controller.extent_for_track_records([track])
        if extent is None:
            return
        self.zoom = 1.0
        self.last_viewport_signature = None
        self.render_extent(extent, status=f"Fitted the map to the complete {track.display_label}.")

    def change_view_zoom(self, factor: float, focus_location=None):
        if self.wheel_finish_timer is not None:
            self.wheel_finish_timer.invalidate()
            self.wheel_finish_timer = None
        if self.pinch_finish_timer is not None:
            self.pinch_finish_timer.invalidate()
            self.pinch_finish_timer = None
        self.pinch_start_tile_zoom = None
        self.pinch_accumulated_scale = 1.0
        if focus_location is None and self.cursor is None:
            focus_location = self.last_gesture_location
        extent = self.zoomed_extent(factor, focus_location=focus_location)
        if extent is None:
            return
        self.render_extent(extent, status="Loading tiles for the new map scale.")

    def zoomed_extent(self, factor: float, focus_location=None):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not extent:
            return None
        min_x = float(extent["min_x"])
        max_x = float(extent["max_x"])
        min_y = float(extent["min_y"])
        max_y = float(extent["max_y"])
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        if focus_location is not None:
            coordinate = self.coordinate_for_view_point(focus_location)
            if coordinate is not None:
                center_x, center_y = lonlat_to_web_mercator(*coordinate)
        elif self.cursor is not None:
            center_x, center_y = lonlat_to_web_mercator(self.cursor[1].lon, self.cursor[1].lat)
        return zoomed_interactive_extent(extent, factor, center_x, center_y)

    def clamp_extent_to_base(self, extent, base_extent):
        span_x = extent["max_x"] - extent["min_x"]
        span_y = extent["max_y"] - extent["min_y"]
        base_min_x = float(base_extent["min_x"])
        base_max_x = float(base_extent["max_x"])
        base_min_y = float(base_extent["min_y"])
        base_max_y = float(base_extent["max_y"])
        if span_x >= base_max_x - base_min_x:
            extent["min_x"], extent["max_x"] = base_min_x, base_max_x
        else:
            if extent["min_x"] < base_min_x:
                extent["min_x"] = base_min_x
                extent["max_x"] = base_min_x + span_x
            if extent["max_x"] > base_max_x:
                extent["max_x"] = base_max_x
                extent["min_x"] = base_max_x - span_x
        if span_y >= base_max_y - base_min_y:
            extent["min_y"], extent["max_y"] = base_min_y, base_max_y
        else:
            if extent["min_y"] < base_min_y:
                extent["min_y"] = base_min_y
                extent["max_y"] = base_min_y + span_y
            if extent["max_y"] > base_max_y:
                extent["max_y"] = base_max_y
                extent["min_y"] = base_max_y - span_y
        return extent

    def delete_marked_points(self):
        if self.marker is None or self.cursor is None:
            return
        track = self.cursor[2]
        start = min(self.marker, self.cursor[0])
        end = max(self.marker, self.cursor[0])
        if confirm("Delete selected points?", f"Delete points {start + 1} through {end + 1} from {track.display_label}?"):
            self.controller.delete_points(track, start, end, refresh=False)
            self.marker = None
            points = track.points()
            next_index = min(start, len(points) - 1) if points else None
            self.cursor = None
            self.refresh_after_map_edit(track, next_index)

    def interpolate_selected_heights(self):
        track = self.cursor[2] if self.cursor is not None else self.current_track_for_title()
        ranges = self.selected_ranges_for_track(track) if track is not None else []
        if not ranges:
            self.controller.set_status("Select a point range first to interpolate its heights.")
            return
        inspector = self.controller.existing_inspector_for_track(track)
        if inspector is not None:
            self.inspector = inspector
        self.controller.interpolate_track_heights(track, ranges)

    @objc.IBAction
    def interpolateHeight_(self, _sender):
        self.interpolate_selected_heights()

    def delete_cursor_waypoint(self):
        if self.cursor is None:
            return
        track = self.cursor[2]
        index = self.cursor[0]
        if not confirm("Delete waypoint?", f"Delete point {index + 1} from {track.display_label}?"):
            return
        self.controller.delete_points(track, index, index, refresh=False)
        points = track.points()
        next_index = min(index, len(points) - 1) if points else None
        self.cursor = None
        self.refresh_after_map_edit(track, next_index)

    def cut_track(self):
        if self.cursor is None:
            return
        track = self.cursor[2]
        index = self.cursor[0]
        if confirm("Cut track after cursor?", f"Cut {track.display_label} after point {index + 1}?"):
            self.controller.cut_track(track, index, refresh=False)
            self.marker = None
            self.refresh_after_map_edit(track, min(index, len(track.points()) - 1))


class PlotWindowDelegate(NSObject):
    def initWithController_view_(self, controller, view):
        self = objc.super(PlotWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        self.view = view
        return self

    def windowDidBecomeKey_(self, _notification):
        window = self.view.window() if self.view is not None else None
        if window is not None:
            window.makeFirstResponder_(self.view)

    def windowShouldClose_(self, window):
        workspace = getattr(self.view, "adventure_workspace_delegate", None)
        if workspace is not None and hasattr(workspace, "hideWorkspaceMapIfOtherWindowsRemain_"):
            if workspace.hideWorkspaceMapIfOtherWindowsRemain_(window):
                return False
            return workspace.confirm_quit()
        return True

    def windowWillClose_(self, _notification):
        window = _notification.object()
        workspace = getattr(self.view, "adventure_workspace_delegate", None)
        if self.controller is not None:
            self.controller.unregister_auxiliary_window(window)
        if self.controller is not None and not self.controller.closing_auxiliary_windows and self.view is not None:
            self.controller.plot_window_closing(self.view)
        if workspace is not None and hasattr(workspace, "workspaceMapWindowWillClose_"):
            workspace.workspaceMapWindowWillClose_(window)


class ElevationProfileView(NSView):
    def initWithController_plotView_(self, controller, plot_view):
        self = objc.super(ElevationProfileView, self).initWithFrame_(NSMakeRect(0, 0, 1000, 180))
        if self is None:
            return None
        self.controller = controller
        self.plot_view = plot_view
        self.x_zoom = 1.0
        self.x_center = None
        self.profile_scope = None
        self.y_axis_mode = "default"
        self.profile_help_window = None
        self.profile_help_text_view = None
        self.profile_help_delegate = None
        self.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        return self

    def acceptsFirstResponder(self):
        return True

    def profile_title(self):
        if self.shows_all_tracks():
            return "Overview Elevation Profile"
        track = self.current_track()
        if track is None:
            return "Track Elevation Profile"
        return f"{track.display_label}: {track.name} - Elevation Profile"

    def current_track(self):
        track = self.plot_view.current_track_for_title()
        if track is None:
            cursor = getattr(self.plot_view, "cursor", None)
            if cursor is not None:
                track = cursor[2]
        if track is None:
            selected = self.controller.selected_tracks()
            track = selected[0] if len(selected) == 1 else None
        return track

    def shows_all_tracks(self):
        scope = getattr(self, "profile_scope", None)
        return scope == "all" or (scope is None and self.plot_view.mode == "overview")

    def profile_tracks(self):
        if self.shows_all_tracks():
            if getattr(getattr(self.plot_view, "adventure_workspace_delegate", None), "show_hidden_tracks", False):
                return self.plot_view.display_tracks()
            return self.controller.visible_tracks()
        # Map-first views also draw neighbouring tracks. The single-track
        # profile must follow the active subject, not that background list.
        track = self.current_track()
        return [track] if track is not None else []

    def profile_data(self):
        rows = []
        track_ranges = {}
        total_distance = 0.0
        for track in self.profile_tracks():
            processed = self.controller.display_processed_track(track)
            if not processed.points:
                continue
            start_distance = total_distance
            raw_by_index = {point.source_index: point for point in processed.raw_points}
            for point in processed.points:
                raw = raw_by_index.get(point.source_index)
                rows.append(
                    {
                        "track": track,
                        "index": point.source_index,
                        "point": raw,
                        "latitude": point.lat,
                        "longitude": point.lon,
                        "distance": start_distance + point.cumulative_distance_km,
                        "elevation": point.elevation_m,
                    }
                )
            total_distance += processed.length_km
            track_ranges[track.nr] = (start_distance, total_distance)
        return rows, track_ranges, total_distance

    def drawRect_(self, _rect):
        bounds = self.bounds()
        NSColor.windowBackgroundColor().setFill()
        NSBezierPath.fillRect_(bounds)
        rows, track_ranges, total_distance = self.profile_data()
        elevations = [row["elevation"] for row in rows if row["elevation"] is not None]
        if not rows or not elevations:
            self.draw_text("No elevation data available.", 20, bounds.size.height - 34, 14)
            return
        plot = NSMakeRect(58, 32, max(bounds.size.width - 82, 10), max(bounds.size.height - 82, 10))
        x_min, x_max = self.visible_distance_range(total_distance)
        y_min, y_max = self.elevation_range(rows, x_min, x_max)

        def transform(distance, elevation):
            x_span = max(x_max - x_min, 0.001)
            y_span = max(y_max - y_min, 1.0)
            x = plot.origin.x + ((distance - x_min) / x_span) * plot.size.width
            y = plot.origin.y + ((elevation - y_min) / y_span) * plot.size.height
            return x, y

        self.draw_axes(plot, x_min, x_max, y_min, y_max)
        self.draw_time_axis(plot, rows, x_min, x_max)
        self.draw_profiles(rows, transform, x_min, x_max)
        self.draw_selected_ranges(rows, track_ranges, transform, x_min, x_max)
        self.draw_scale_bar(plot, y_min, y_max)
        self.draw_cursor(rows, transform, x_min, x_max, plot)
        self.draw_selection_marker(rows, transform, x_min, x_max)
        self.draw_text(self.profile_title(), plot.origin.x, bounds.size.height - 22, 13, NSColor.labelColor())

    def fixed_elevation_range(self, elevations):
        max_elevation = max(elevations)
        y_min = 0.0
        needed = max_elevation + max(1.0, max_elevation * self.controller.elevation_headroom_fraction)
        for candidate in (500.0, 1000.0, 1500.0, 2000.0, 3000.0, 4000.0):
            if candidate >= needed:
                return y_min, candidate
        y_max = math.ceil(needed / 1000.0) * 1000.0
        return y_min, max(y_max, y_min + 500.0)

    def elevation_range(self, rows, x_min, x_max):
        axis_rows = rows
        if self.shows_all_tracks():
            axis_rows = elevation_rows_for_selected_tracks(
                rows,
                self.controller.selected_nrs,
            )
        elevations = [
            row["elevation"]
            for row in axis_rows
            if row["elevation"] is not None
        ]
        if not elevations:
            return 0.0, 500.0
        if self.y_axis_mode != "visible":
            return self.fixed_elevation_range(elevations)
        visible_range = elevation_profile_visible_range(axis_rows, x_min, x_max)
        return visible_range if visible_range is not None else self.fixed_elevation_range(elevations)

    def visible_distance_range(self, total_distance):
        if total_distance <= 0:
            return 0.0, 1.0
        span = total_distance / max(self.x_zoom, 1.0)
        if self.x_center is None:
            center = total_distance / 2.0
        else:
            center = max(0.0, min(float(self.x_center), total_distance))
        left = center - span / 2.0
        right = center + span / 2.0
        if left < 0:
            right -= left
            left = 0.0
        if right > total_distance:
            left -= right - total_distance
            right = total_distance
        return max(0.0, left), max(right, min(total_distance, 0.001))

    def sync_to_map_extent(self):
        """Match the profile distance window to points visible on the map."""
        metadata = self.plot_view.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not isinstance(extent, dict):
            return False
        visible_distances: list[float] = []
        total_distance = 0.0
        try:
            min_x = float(extent["min_x"])
            max_x = float(extent["max_x"])
            min_y = float(extent["min_y"])
            max_y = float(extent["max_y"])
        except (KeyError, TypeError, ValueError):
            return False
        for track in self.profile_tracks():
            processed = self.controller.display_processed_track(track)
            for point in processed.points:
                merc_x, merc_y = lonlat_to_web_mercator(point.lon, point.lat)
                if min_x <= merc_x <= max_x and min_y <= merc_y <= max_y:
                    visible_distances.append(
                        total_distance + point.cumulative_distance_km
                    )
            total_distance += processed.length_km
        visible_range = (
            (min(visible_distances), max(visible_distances))
            if visible_distances
            else None
        )
        if visible_range is None or total_distance <= 0.0:
            return False
        left, right = visible_range
        span = max(right - left, total_distance / 64.0, 0.001)
        self.x_center = max(0.0, min((left + right) / 2.0, total_distance))
        self.x_zoom = max(1.0, min(total_distance / span, 64.0))
        self.setNeedsDisplay_(True)
        return True

    def draw_axes(self, plot, x_min, x_max, y_min, y_max):
        NSColor.separatorColor().setStroke()
        box = NSBezierPath.bezierPathWithRect_(plot)
        box.setLineWidth_(1.0)
        box.stroke()
        y_step = self.nice_step((y_max - y_min) / 4.0)
        tick = math.ceil(y_min / y_step) * y_step
        while tick <= y_max + 0.001:
            y = plot.origin.y + ((tick - y_min) / max(y_max - y_min, 1.0)) * plot.size.height
            if y > plot.origin.y + 0.5 and y < plot.origin.y + plot.size.height - 0.5:
                NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.45, 0.45, 0.35).setStroke()
                path = NSBezierPath.bezierPath()
                path.setLineWidth_(0.7)
                path.setLineDash_count_phase_([12.0, 7.0], 2, 0.0)
                path.moveToPoint_((plot.origin.x, y))
                path.lineToPoint_((plot.origin.x + plot.size.width, y))
                path.stroke()
            self.draw_text(f"{tick:.0f}", 8, y - 7, 10, NSColor.secondaryLabelColor())
            tick += y_step
        x_step = self.nice_step((x_max - x_min) / 6.0)
        tick = math.ceil(x_min / x_step) * x_step
        while tick <= x_max + 0.001:
            x = plot.origin.x + ((tick - x_min) / max(x_max - x_min, 0.001)) * plot.size.width
            NSColor.separatorColor().setStroke()
            path = NSBezierPath.bezierPath()
            path.moveToPoint_((x, plot.origin.y))
            path.lineToPoint_((x, plot.origin.y - 5))
            path.stroke()
            self.draw_text(f"{tick:.1f}", x - 12, 12, 10, NSColor.secondaryLabelColor())
            tick += x_step
        self.draw_text("Distance [km]", plot.origin.x + plot.size.width / 2.0 - 42, 2, 10, NSColor.secondaryLabelColor())
        unit = "km" if y_max >= 3000 else "m"
        label = f"Height [{unit}]"
        self.draw_text(label, 6, plot.origin.y + plot.size.height + 5, 10, NSColor.secondaryLabelColor())

    def draw_time_axis(self, plot, rows, x_min, x_max):
        timed_rows = [row for row in rows if row["point"].time is not None]
        if len(timed_rows) < 2:
            return
        origin_time = timed_rows[0]["point"].time
        end_time = timed_rows[-1]["point"].time
        total_seconds = (end_time - origin_time).total_seconds()
        if total_seconds <= 0:
            return
        step_seconds = self.time_tick_step(total_seconds)
        if step_seconds <= 0:
            return
        previous_label_x = None
        tick_seconds = step_seconds
        while tick_seconds < total_seconds + 0.5:
            distance = self.distance_at_elapsed(timed_rows, origin_time, tick_seconds)
            if distance is None or distance < x_min or distance > x_max:
                tick_seconds += step_seconds
                continue
            x = plot.origin.x + ((distance - x_min) / max(x_max - x_min, 0.001)) * plot.size.width
            label = self.short_elapsed_label(tick_seconds)
            label_width = max(24.0, len(label) * 6.0)
            if previous_label_x is not None and x - previous_label_x < label_width + 18.0:
                tick_seconds += step_seconds
                continue
            NSColor.separatorColor().setStroke()
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(1.0)
            path.moveToPoint_((x, plot.origin.y + plot.size.height))
            path.lineToPoint_((x, plot.origin.y + plot.size.height - 6.0))
            path.stroke()
            self.draw_text(label, x - label_width / 2.0, plot.origin.y + plot.size.height + 2.0, 9, NSColor.secondaryLabelColor())
            previous_label_x = x
            tick_seconds += step_seconds

    def time_tick_step(self, total_seconds):
        desired = total_seconds / 9.0
        candidates = [
            5 * 60,
            10 * 60,
            15 * 60,
            20 * 60,
            30 * 60,
            40 * 60,
            60 * 60,
            2 * 60 * 60,
            3 * 60 * 60,
            4 * 60 * 60,
            6 * 60 * 60,
            8 * 60 * 60,
            12 * 60 * 60,
            24 * 60 * 60,
            2 * 24 * 60 * 60,
            3 * 24 * 60 * 60,
            5 * 24 * 60 * 60,
            7 * 24 * 60 * 60,
            14 * 24 * 60 * 60,
            30 * 24 * 60 * 60,
        ]
        for candidate in candidates:
            if candidate >= desired:
                return candidate
        days = max(1, math.ceil(desired / (30 * 24 * 60 * 60)) * 30)
        return days * 24 * 60 * 60

    def distance_at_elapsed(self, timed_rows, origin_time, elapsed_seconds):
        target_time = origin_time + timedelta(seconds=elapsed_seconds)
        previous = None
        for row in timed_rows:
            current_time = row["point"].time
            if current_time is None:
                continue
            if current_time >= target_time:
                if previous is None:
                    return row["distance"]
                previous_time = previous["point"].time
                span = (current_time - previous_time).total_seconds()
                if span <= 0:
                    return row["distance"]
                fraction = (target_time - previous_time).total_seconds() / span
                return previous["distance"] + fraction * (row["distance"] - previous["distance"])
            previous = row
        return timed_rows[-1]["distance"] if timed_rows else None

    def short_elapsed_label(self, seconds):
        seconds = max(0, int(round(seconds)))
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 24 * 3600:
            hours = seconds / 3600.0
            return f"{hours:.0f}h" if abs(hours - round(hours)) < 0.01 else f"{hours:.1f}h"
        days = seconds / (24 * 3600.0)
        return f"{days:.0f}d" if abs(days - round(days)) < 0.01 else f"{days:.1f}d"

    def cursor_elapsed_text(self, seconds):
        seconds = max(0, int(seconds))
        total_minutes = seconds // 60
        days, remainder = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(remainder, 60)
        if days:
            return f"{days:02d}d{hours:02d}h"
        return f"{hours:02d}h{minutes:02d}m"

    def draw_scale_bar(self, plot, y_min, y_max):
        y_span = max(y_max - y_min, 1.0)
        scale_m = 250.0
        if y_span < scale_m:
            return
        bar_height = scale_m / y_span * plot.size.height
        if bar_height < 8.0:
            return
        x = plot.origin.x + plot.size.width - 26.0
        y = plot.origin.y + (plot.size.height - bar_height) / 2.0
        width = 10.0
        segment_height = bar_height / 5.0
        for index in range(5):
            if index % 2 == 0:
                NSColor.colorWithSRGBRed_green_blue_alpha_(0.55, 0.55, 0.55, 0.95).setFill()
            else:
                NSColor.whiteColor().setFill()
            NSBezierPath.fillRect_(NSMakeRect(x, y + index * segment_height, width, segment_height))
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.35, 0.35, 0.35, 1.0).setStroke()
        outline = NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, width, bar_height))
        outline.setLineWidth_(0.8)
        outline.stroke()
        self.draw_text("250 m", x - 12, y + bar_height + 3, 9, NSColor.secondaryLabelColor())

    def draw_profiles(self, rows, transform, x_min, x_max):
        selected_tracks = set(self.controller.selected_nrs)
        workspace_selection = self.plot_view.adventure_workspace_delegate is not None

        def track_color(track):
            return cocoa_track_color(track, self.controller.project_parameters)

        def stroke(path, track):
            if path is None or track is None:
                return
            track_color(track).setStroke()
            path.setLineWidth_(3.0 if track.nr in selected_tracks else 2.0)
            if track.hidden:
                path.setLineDash_count_phase_([5., 4.], 2, 0.)
            path.stroke()

        current_track = None
        path = None
        for row in rows:
            elevation = row["elevation"]
            if elevation is None or row["distance"] < x_min or row["distance"] > x_max:
                stroke(path, current_track)
                path = None
                current_track = None
                continue
            if row["track"] is not current_track or path is None:
                stroke(path, current_track)
                path = NSBezierPath.bezierPath()
                x, y = transform(row["distance"], elevation)
                path.moveToPoint_((x, y))
                current_track = row["track"]
            else:
                x, y = transform(row["distance"], elevation)
                path.lineToPoint_((x, y))
        stroke(path, current_track)

    def draw_selected_ranges(self, rows, track_ranges, transform, x_min, x_max):
        del track_ranges
        tracks = self.profile_tracks()
        for track in (item for item in tracks if item is not None):
            ranges = self.plot_view.selected_ranges_for_track(track)
            if (
                self.plot_view.marker is not None
                and self.plot_view.cursor is not None
                and self.plot_view.cursor[2] is track
            ):
                self.plot_view.sync_inspector_selection()
            points = track.points()
            for start, end in ranges:
                if start >= len(points) or end >= len(points):
                    continue
                selected_rows = [
                    row
                    for row in rows
                    if row["track"] is track
                    and start <= row["index"] <= end
                    and row["elevation"] is not None
                ]
                self.draw_profile_segment(
                    selected_rows,
                    transform,
                    x_min,
                    x_max,
                    NSColor.systemRedColor(),
                    4.0,
                )

    def draw_profile_segment(self, rows, transform, x_min, x_max, color, width):
        path = None
        for row in rows:
            if row["distance"] < x_min or row["distance"] > x_max:
                continue
            x, y = transform(row["distance"], row["elevation"])
            if path is None:
                path = NSBezierPath.bezierPath()
                path.moveToPoint_((x, y))
            else:
                path.lineToPoint_((x, y))
        if path is not None:
            color.setStroke()
            path.setLineWidth_(width)
            path.stroke()

    def draw_cursor(self, rows, transform, x_min, x_max, plot):
        cursor = getattr(self.plot_view, "cursor", None)
        if cursor is None:
            return
        point_index, point, track = cursor
        cursor_rows = [row for row in rows if row["track"] is track and row["index"] == point_index and row["elevation"] is not None]
        if not cursor_rows:
            return
        row = cursor_rows[0]
        if row["distance"] < x_min or row["distance"] > x_max:
            return
        x, y = transform(row["distance"], row["elevation"])
        NSColor.blackColor().setStroke()
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(1.4)
        path.setLineDash_count_phase_([5.0, 4.0], 2, 0.0)
        path.moveToPoint_((x, plot.origin.y))
        path.lineToPoint_((x, y))
        path.stroke()
        NSColor.whiteColor().setFill()
        NSColor.blackColor().setStroke()
        dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 4, y - 4, 8, 8))
        dot.setLineWidth_(1.2)
        dot.fill()
        dot.stroke()
        if not self.plot_view.show_info:
            return
        height_text = f"{row['elevation']:.0f} m"
        height_width = max(28.0, len(height_text) * 6.0)
        height_x = min(max(plot.origin.x + 2, x - height_width / 2.0), plot.origin.x + plot.size.width - height_width - 2)
        self.draw_text(height_text, height_x, min(y + 9, plot.origin.y + plot.size.height - 14), 10, NSColor.labelColor())
        elapsed_text = self.cursor_elapsed_label(rows, row)
        if elapsed_text:
            elapsed_width = max(48.0, len(elapsed_text) * 6.0)
            if x > plot.origin.x + plot.size.width / 2.0:
                text_x = max(plot.origin.x + 4, x - elapsed_width - 7)
            else:
                text_x = min(plot.origin.x + plot.size.width - elapsed_width - 4, x + 7)
            midpoint_y = plot.origin.y + max(0.0, (y - plot.origin.y) / 2.0) - 5.0
            self.draw_text(elapsed_text, text_x, max(plot.origin.y + 4, midpoint_y), 10, NSColor.labelColor())

    def cursor_elapsed_label(self, rows, cursor_row):
        track = cursor_row["track"]
        timed_rows = [row for row in rows if row["track"] is track and row["point"].time is not None]
        if not timed_rows or cursor_row["point"].time is None:
            return ""
        origin_time = timed_rows[0]["point"].time
        elapsed = cursor_row["point"].time - origin_time
        if elapsed.total_seconds() < 0:
            return ""
        return self.cursor_elapsed_text(elapsed.total_seconds())

    def elevation_baseline(self, rows):
        elevations = [row["elevation"] for row in rows if row["elevation"] is not None]
        return min(elevations) if elevations else 0.0

    def nice_step(self, raw):
        if raw <= 0:
            return 1.0
        exponent = math.floor(math.log10(raw))
        fraction = raw / (10 ** exponent)
        if fraction <= 1:
            nice = 1
        elif fraction <= 2:
            nice = 2
        elif fraction <= 5:
            nice = 5
        else:
            nice = 10
        return nice * (10 ** exponent)

    def draw_text(self, text, x, y, size=11, color=None):
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(size),
            NSForegroundColorAttributeName: color or NSColor.labelColor(),
        }
        NSString.stringWithString_(str(text)).drawAtPoint_withAttributes_((x, y), attrs)

    def draw_selection_marker(self, rows, transform, x_min, x_max):
        cursor = self.plot_view.cursor
        marker = self.plot_view.marker
        if cursor is None or marker is None:
            return
        track_rows = [row for row in rows if row["track"] is cursor[2] and row["elevation"] is not None]
        if not track_rows:
            return
        row = min(track_rows, key=lambda item: abs(item["index"] - marker))
        if not x_min <= row["distance"] <= x_max:
            return
        x, y = transform(row["distance"], row["elevation"])
        NSColor.systemRedColor().setFill()
        NSColor.blackColor().setStroke()
        square = NSBezierPath.bezierPathWithRect_(NSMakeRect(x - 4, y - 4, 8, 8))
        square.setLineWidth_(1.2)
        square.fill()
        square.stroke()

    def mouseDown_(self, event):
        self.move_cursor_to_event(event)
        if event.modifierFlags() & NSEventModifierFlagShift and self.plot_view.cursor is not None:
            self.plot_view.set_selection_marker()
        if event.clickCount() >= 2 and self.plot_view.cursor is not None:
            if self.plot_view.mode == "overview":
                self.controller.open_track_workflow_at_point(
                    self.plot_view.cursor[2],
                    self.plot_view.cursor[0],
                    source_view=self.plot_view,
                )
            elif self.plot_view.mode == "track":
                inspector = self.controller.open_inspector_for_track(self.plot_view.cursor[2])
                if inspector is not None:
                    self.plot_view.inspector = inspector
                    inspector.plot_view = self.plot_view
                    inspector.select_point_index(self.plot_view.cursor[0])
                    inspector.window.makeKeyAndOrderFront_(None)
                    inspector.window.orderFrontRegardless()
        self.window().makeFirstResponder_(self)

    def mouseDragged_(self, event):
        self.move_cursor_to_event(event)

    def move_cursor_to_event(self, event):
        row = self.nearest_row_for_event(event)
        if row is None:
            return
        track = row["track"]
        index = row["index"]
        self.controller.select_track_in_table(track.nr)
        self.plot_view.move_cursor_to_track_point(track, index, preserve_marker=True, focus_plot=False)
        self.plot_view.sync_inspector_selection()
        self.plot_view.setNeedsDisplay_(True)
        self.setNeedsDisplay_(True)

    def nearest_row_for_event(self, event):
        rows, _track_ranges, total_distance = self.profile_data()
        rows = [row for row in rows if row["elevation"] is not None]
        if not rows:
            return None
        bounds = self.bounds()
        plot = NSMakeRect(58, 32, max(bounds.size.width - 82, 10), max(bounds.size.height - 82, 10))
        x_min, x_max = self.visible_distance_range(total_distance)
        y_min, y_max = self.elevation_range(rows, x_min, x_max)
        location = self.convertPoint_fromView_(event.locationInWindow(), None)

        def pixel(row):
            x_span = max(x_max - x_min, 0.001)
            y_span = max(y_max - y_min, 1.0)
            x = plot.origin.x + ((row["distance"] - x_min) / x_span) * plot.size.width
            y = plot.origin.y + ((row["elevation"] - y_min) / y_span) * plot.size.height
            return x, y

        visible_rows = [row for row in rows if x_min <= row["distance"] <= x_max]
        candidates = visible_rows or rows
        return min(candidates, key=lambda row: (pixel(row)[0] - location.x) ** 2 + 0.35 * (pixel(row)[1] - location.y) ** 2)

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        key_code = event.keyCode()
        modifiers = event.modifierFlags()
        command_down = bool(modifiers & NSEventModifierFlagCommand)
        shift_down = bool(modifiers & NSEventModifierFlagShift)
        zoom_action = plot_zoom_key_action(key, modifiers)
        if command_down and key.casefold() == "z":
            inspector = getattr(self.plot_view, "inspector", None)
            if shift_down:
                if inspector is not None and inspector.redo_stack:
                    inspector.redo()
                else:
                    self.controller.redo_(None)
            else:
                if inspector is not None and inspector.undo_stack:
                    inspector.undo()
                else:
                    self.controller.undo_(None)
            return
        if command_down and self.plot_view.mode == "track" and key.casefold() == "x":
            self.plot_view.cut_track()
            self.setNeedsDisplay_(True)
            return
        if key in {"h", "H"}:
            self.show_profile_help()
            return
        if key in {"q", "Q"}:
            window = self.window()
            if window is not None:
                window.close()
            return
        if zoom_action == "track":
            self.set_profile_scope("track")
            return
        if zoom_action == "all":
            self.set_profile_scope("all")
            return
        if key in {"+", "="}:
            self.change_zoom(4.0 if command_down else 2.0)
            return
        if key in {"-", "_"}:
            self.change_zoom(0.25 if command_down else 0.5)
            return
        if height_interpolation_key(key, modifiers):
            if not event.isARepeat():
                self.plot_view.interpolate_selected_heights()
            return
        if key in {"i", "I"}:
            self.plot_view.show_info = not self.plot_view.show_info
            self.plot_view.info_overlay_dismissed = not self.plot_view.show_info
            if self.plot_view.show_info:
                self.plot_view.context_overlay_coordinate = None
            self.plot_view.setNeedsDisplay_(True)
            self.setNeedsDisplay_(True)
            self.controller.set_status(
                "Point information shown." if self.plot_view.show_info else "Point information hidden."
            )
            return
        if key in {"s", "S"}:
            self.controller.save_elevation_profile_png(self)
            return
        if key in {"c", "C"}:
            self.center_on_cursor()
            return
        if zoom_action == "selection":
            self.zoom_to_selected()
            return
        if key in {"y", "Y"}:
            self.y_axis_mode = "visible"
            self.setNeedsDisplay_(True)
            self.controller.set_status("Elevation axis fitted to the elevations in the visible distance range.")
            return
        if key == "0":
            self.y_axis_mode = "default"
            self.setNeedsDisplay_(True)
            self.controller.set_status("Elevation axis reset to the zero-based default range.")
            return
        if key in {"u", "U"}:
            self.plot_view.unselect_plot_items()
            self.plot_view.setNeedsDisplay_(True)
            self.setNeedsDisplay_(True)
            self.controller.refresh_elevation_profile_for_plot_view(self.plot_view)
            return
        if self.plot_view.mode == "track" and key in {"x", "X"}:
            self.plot_view.cut_track()
            self.setNeedsDisplay_(True)
            return
        if self.plot_view.mode == "track" and key in {"\x08", "\x7f", "\uf728"}:
            if self.plot_view.marker is not None:
                self.plot_view.delete_marked_points()
            else:
                self.plot_view.delete_cursor_waypoint()
            self.setNeedsDisplay_(True)
            return
        if self.plot_view.mode == "track" and (key in {" ", "\uf703", "\uf701"} or key_code in {124, 125}):
            self.plot_view.switch_track(1)
            self.setNeedsDisplay_(True)
            return
        if self.plot_view.mode == "track" and (key in {"\uf702", "\uf700"} or key_code in {123, 126}):
            self.plot_view.switch_track(-1)
            self.setNeedsDisplay_(True)
            return
        if key in {"m", "M"} and self.plot_view.cursor is not None and not (
            modifiers & (NSEventModifierFlagCommand | NSEventModifierFlagOption | NSEventModifierFlagControl)
        ):
            if not event.isARepeat():
                self.plot_view.set_selection_marker(swap=shift_down or key == "M")
            self.setNeedsDisplay_(True)
            return
        objc.super(ElevationProfileView, self).keyDown_(event)

    def keyUp_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        if key in {"h", "H"}:
            return
        objc.super(ElevationProfileView, self).keyUp_(event)

    def menuForEvent_(self, _event):
        menu = NSMenu.alloc().initWithTitle_("Elevation Profile")
        menu.setAutoenablesItems_(False)
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Interpolate Height", "interpolateHeight:", ""
        )
        item.setTarget_(self)
        track = self.current_track()
        ranges = self.plot_view.selected_ranges_for_track(track) if track is not None else []
        item.setEnabled_(any(end - start >= 2 for start, end in ranges))
        menu.addItem_(item)
        return menu

    @objc.IBAction
    def interpolateHeight_(self, _sender):
        self.plot_view.interpolate_selected_heights()

    def profile_help_text(self):
        return "\n".join(
            [
                "myCamino GPX Editor - Elevation Profile Keys",
                "",
                "h                 Show this help window",
                "Close button      Close help (it stays open until you close it)",
                "i                 Toggle cursor point information",
                "I (Shift-I)       Interpolate height across the selected range",
                "+ / -             Zoom distance in / out around the cursor",
                "Cmd + / Cmd -     Zoom two additional levels in / out",
                "c                 Center the visible profile on the cursor",
                "z                 Zoom to selected tracks or selected points",
                "Shift-Z           Fit the complete current track",
                "A (Shift-A)       Show all Adventure tracks",
                "y                 Fit height to the currently visible profile (+5% margins)",
                "0                 Restore the zero-based default height axis",
                "u                 Clear the current selection",
                "s                 Save the elevation profile as a PNG",
                "q                 Close the elevation profile",
                "",
                "Click / drag      Move the cursor to the nearest track point",
                "Shift-click       Set the selection marker",
                "Double-click      Open the track point inspector",
                "m                 Set the square marker at the current cursor",
                "M (Shift-M)       Swap marker and cursor (selection ends)",
                "Delete            Delete points between marker and cursor",
                "x or Cmd-X        Cut the track after the current cursor point",
                "Cmd-Z             Undo the last track edit",
                "Shift-Cmd-Z       Redo the last track edit",
                "Arrow keys        Previous / next track",
                "Space             Next track",
            ]
        )

    def show_profile_help(self):
        if self.profile_help_window is None:
            width = 650.0
            height = 445.0
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, width, height),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setReleasedWhenClosed_(False)
            self.profile_help_delegate = ReusableToolWindowDelegate.alloc().init()
            window.setDelegate_(self.profile_help_delegate)
            window.setTitle_("Elevation Profile Help")
            window.setMinSize_(NSMakeSize(500, 300))
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
            text_view.setEditable_(False)
            text_view.setSelectable_(True)
            text_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(13.0, 0.0))
            text_view.setTextContainerInset_((18.0, 16.0))
            text_view.setString_(self.profile_help_text())
            scroll.setDocumentView_(text_view)
            window.setContentView_(scroll)
            self.profile_help_window = window
            self.profile_help_text_view = text_view
            self.controller.register_auxiliary_window(window)
        profile_window = self.window()
        help_window = self.profile_help_window
        if profile_window is not None:
            profile_frame = profile_window.frame()
            help_frame = help_window.frame()
            screen = profile_window.screen()
            visible = screen.visibleFrame() if screen is not None else profile_frame
            x = profile_frame.origin.x + (profile_frame.size.width - help_frame.size.width) / 2.0
            x = max(visible.origin.x, min(x, visible.origin.x + visible.size.width - help_frame.size.width))
            y = profile_frame.origin.y + profile_frame.size.height + 8.0
            if y + help_frame.size.height > visible.origin.y + visible.size.height:
                y = profile_frame.origin.y - help_frame.size.height - 8.0
            y = max(visible.origin.y, min(y, visible.origin.y + visible.size.height - help_frame.size.height))
            help_window.setFrameOrigin_((x, y))
        help_window.makeKeyAndOrderFront_(None)
        help_window.orderFrontRegardless()

    def close_profile_help(self):
        window = self.profile_help_window
        self.profile_help_window = None
        self.profile_help_text_view = None
        if window is None:
            return
        self.controller.unregister_auxiliary_window(window)
        try:
            window.setDelegate_(None)
            window.orderOut_(None)
            window.close()
        except Exception:
            pass
        self.profile_help_delegate = None

    def change_zoom(self, factor):
        rows, _ranges, total_distance = self.profile_data()
        if total_distance <= 0:
            return
        cursor = getattr(self.plot_view, "cursor", None)
        if cursor is not None:
            for row in rows:
                if row["track"] is cursor[2] and row["index"] == cursor[0]:
                    self.x_center = row["distance"]
                    break
        elif self.x_center is None:
            self.x_center = total_distance / 2.0
        self.x_zoom = max(1.0, min(self.x_zoom * factor, 64.0))
        self.setNeedsDisplay_(True)

    def cursor_distance(self, rows):
        cursor = getattr(self.plot_view, "cursor", None)
        if cursor is None:
            return None
        for row in rows:
            if row["track"] is cursor[2] and row["index"] == cursor[0]:
                return row["distance"]
        same_track = [row for row in rows if row["track"] is cursor[2]]
        if not same_track:
            return None
        nearest = min(same_track, key=lambda row: abs(row["index"] - cursor[0]))
        return nearest["distance"]

    def center_on_cursor(self):
        rows, _ranges, total_distance = self.profile_data()
        distance = self.cursor_distance(rows)
        if distance is None:
            self.controller.set_status("No cursor point to center the elevation profile on.")
            return
        self.x_center = max(0.0, min(distance, total_distance))
        self.setNeedsDisplay_(True)
        self.controller.set_status("Centered elevation profile on the current cursor point.")

    def set_profile_scope(self, scope):
        if scope == "track" and self.current_track() is None:
            self.controller.set_status("Select a track to fit first; press A to show all tracks.")
            return
        # Profile scope is independent of the associated map's viewport.
        self.profile_scope = scope
        self.x_zoom = 1.0
        self.x_center = None
        window = self.window()
        if window is not None:
            window.setTitle_(self.profile_title())
        self.setNeedsDisplay_(True)
        self.controller.set_status(
            "Showing elevation profiles for all visible Adventure tracks."
            if scope == "all" else "Fitted the elevation profile to the complete current track."
        )

    def zoom_to_selected(self):
        rows, track_ranges, total_distance = self.profile_data()
        if not rows or total_distance <= 0:
            return
        distances = []
        track = self.current_track()
        ranges = self.plot_view.selected_ranges_for_track(track) if track is not None else []
        if ranges:
            track_rows = [row for row in rows if row["track"] is track]
            for start, end in ranges:
                selected_rows = [row for row in track_rows if start <= row["index"] <= end]
                if selected_rows:
                    distances.extend([selected_rows[0]["distance"], selected_rows[-1]["distance"]])
                elif track_rows:
                    first = min(track_rows, key=lambda row: abs(row["index"] - start))
                    last = min(track_rows, key=lambda row: abs(row["index"] - end))
                    distances.extend([first["distance"], last["distance"]])
        elif self.shows_all_tracks():
            selected = self.controller.selected_nrs or [track.nr for track in self.profile_tracks()]
            for track_nr in selected:
                track_range = track_ranges.get(track_nr)
                if track_range is not None:
                    distances.extend(track_range)
        else:
            if track is not None and track.nr in track_ranges:
                distances.extend(track_ranges[track.nr])
        if not distances:
            self.controller.set_status("No tracks or points are selected for profile zoom.")
            return
        left = min(distances)
        right = max(distances)
        span = max(right - left, total_distance / 64.0, 0.001)
        span = min(total_distance, span * 1.10)
        self.x_center = (left + right) / 2.0
        self.x_zoom = max(1.0, min(total_distance / span, 64.0))
        self.setNeedsDisplay_(True)
        self.controller.set_status("Zoomed elevation profile to the selected tracks or points.")


def point_table_segment_break_rows(rows) -> set[int]:
    """Return rows that begin a new GPX segment."""
    return {
        row
        for row in range(1, len(rows))
        if rows[row].get("segment_index") != rows[row - 1].get("segment_index")
    }


class InspectorPointTableView(NSTableView):
    def drawRect_(self, dirty_rect):
        objc.super(InspectorPointTableView, self).drawRect_(dirty_rect)
        controller = getattr(self, "controller", None)
        rows = getattr(controller, "rows", []) if controller is not None else []
        if len(rows) < 2:
            return
        top = max(0.0, float(dirty_rect.origin.y))
        bottom = top + max(0.0, float(dirty_rect.size.height))
        first = int(self.rowAtPoint_(NSMakePoint(0.0, top)))
        last = int(self.rowAtPoint_(NSMakePoint(0.0, bottom)))
        if first < 0:
            first = 0
        if last < 0:
            last = len(rows) - 1
        NSColor.separatorColor().setStroke()
        break_rows = getattr(controller, "segment_break_rows", set())
        for row in range(max(1, first), min(len(rows) - 1, last) + 1):
            if row not in break_rows:
                continue
            row_rect = self.rectOfRow_(row)
            y_coord = row_rect.origin.y - 0.5
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(1.0)
            path.moveToPoint_(NSMakePoint(dirty_rect.origin.x, y_coord))
            path.lineToPoint_(NSMakePoint(dirty_rect.origin.x + dirty_rect.size.width, y_coord))
            path.stroke()

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        modifiers = int(event.modifierFlags())
        command_down = bool(modifiers & NSEventModifierFlagCommand)
        shift_down = bool(modifiers & NSEventModifierFlagShift)
        if command_down and key.casefold() == "i" and getattr(self, "controller", None) is not None:
            self.controller.insert_point(before=shift_down)
            return
        if command_down and key.casefold() == "c" and getattr(self, "controller", None) is not None:
            self.controller.copy_selected_points()
            return
        if command_down and key.casefold() == "x" and getattr(self, "controller", None) is not None:
            self.controller.cut_selected_points()
            return
        if command_down and key.casefold() == "v" and getattr(self, "controller", None) is not None:
            self.controller.paste_points()
            return
        if command_down and key.casefold() == "z" and getattr(self, "controller", None) is not None:
            self.controller.undo()
            return
        if height_interpolation_key(key, modifiers) and getattr(self, "controller", None) is not None:
            if not event.isARepeat():
                self.controller.interpolateHeight_(None)
            return
        if key in {"\x08", "\x7f", "\uf728"} and getattr(self, "controller", None) is not None:
            self.controller.delete_selected_points()
            return
        objc.super(InspectorPointTableView, self).keyDown_(event)


class TrackPointDataSource(NSObject):
    def initWithController_(self, controller):
        self = objc.super(TrackPointDataSource, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def numberOfRowsInTableView_(self, _table_view):
        return len(self.controller.rows)

    def tableView_objectValueForTableColumn_row_(self, _table_view, table_column, row):
        if row < 0 or row >= len(self.controller.rows):
            return ""
        return self.controller.rows[row].get(str(table_column.identifier()), "")

    def tableView_setObjectValue_forTableColumn_row_(self, _table_view, value, table_column, row):
        self.controller.commit_point_value(
            row, str(table_column.identifier()), str(value)
        )

    def tableView_shouldEditTableColumn_row_(self, _table_view, table_column, row):
        identifier = str(table_column.identifier())
        if identifier in {"index", "xy_status", "z_status", "calc_velocity", "running_speed", "time_source"}:
            return False
        self.controller.begin_point_edit(row, identifier)
        return True

    def control_textView_doCommandBySelector_(self, _control, _text_view, selector):
        if str(selector) == "cancelOperation:":
            self.controller.cancel_active_point_edit()
            return True
        return False

    def tableViewSelectionDidChange_(self, _notification):
        self.controller.selection_changed()

    def tableViewColumnDidResize_(self, _notification):
        self.controller.resize_table_document()

    def tableView_rowViewForRow_(self, _table_view, row):
        segment_break = (
            0 < row < len(self.controller.rows)
            and self.controller.rows[row].get("segment_index")
            != self.controller.rows[row - 1].get("segment_index")
        )
        return SegmentBreakPointRowView.alloc().initWithSegmentBreak_(segment_break)

    def tableView_writeRowsWithIndexes_toPasteboard_(self, _table_view, row_indexes, pasteboard):
        rows = [
            index
            for index in range(row_indexes.firstIndex(), row_indexes.lastIndex() + 1)
            if row_indexes.containsIndex_(index)
        ]
        if not rows:
            return False
        pasteboard.declareTypes_owner_([POINT_DRAG_TYPE], None)
        pasteboard.setString_forType_(",".join(str(row) for row in rows), POINT_DRAG_TYPE)
        return True

    def tableView_validateDrop_proposedRow_proposedDropOperation_(
        self, _table_view, info, row, _operation
    ):
        text = str(info.draggingPasteboard().stringForType_(POINT_DRAG_TYPE) or "")
        rows = [int(part) for part in text.split(",") if part.isdigit()]
        return 1 if rows and 0 <= row <= len(self.controller.rows) else 0

    def tableView_acceptDrop_row_dropOperation_(self, _table_view, info, row, _operation):
        text = str(info.draggingPasteboard().stringForType_(POINT_DRAG_TYPE) or "")
        rows = [int(part) for part in text.split(",") if part.isdigit()]
        return self.controller.move_selected_rows(rows, row)


class TrackMetadataChoiceDataSource(NSObject):
    def initWithRows_(self, rows):
        self = objc.super(TrackMetadataChoiceDataSource, self).init()
        if self is None:
            return None
        self.rows = rows
        return self

    def numberOfRowsInTableView_(self, _table_view):
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(self, _table_view, table_column, row):
        if row < 0 or row >= len(self.rows):
            return ""
        return self.rows[row].get(str(table_column.identifier()), "")


class TrackInspectorController(NSObject):
    def initWithController_track_(self, parent, track):
        self = objc.super(TrackInspectorController, self).init()
        if self is None:
            return None
        self.parent = parent
        self.track = track
        self.undo_stack = []
        self.redo_stack = []
        self.rows = []
        self.segment_break_rows = set()
        self.extra_columns = []
        self.field_refs = {}
        self.suppress_selection_change = False
        self.plot_view = None
        self.owns_plot_window = False
        self.velocity_field_touched = False
        self.active_point_edit = None
        self.canceling_point_edit = False
        self._build_window()
        self.reload_rows()
        return self

    def _build_window(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(220, 140, 1040, 680),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_(f"Inspect {self.track.display_label}")
        self.window.setMinSize_(NSMakeSize(620, 360))
        self.window.setDelegate_(self)
        root = NSView.alloc().initWithFrame_(self.window.contentView().bounds())
        root.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.window.contentView().addSubview_(root)
        self.root = root

        self.info_label = NSTextField.labelWithString_("")
        self.info_label.setFont_(NSFont.systemFontOfSize_(13))
        root.addSubview_(self.info_label)

        self.velocity_label = NSTextField.labelWithString_("Velocity km/h")
        self.velocity_label.setFont_(NSFont.systemFontOfSize_(12))
        root.addSubview_(self.velocity_label)

        self.velocity_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 90, FIELD_HEIGHT))
        self.velocity_field.setToolTip_("Speed in km/h used by Readjust Time. Defaults to the master-table average speed.")
        self.velocity_field.setTarget_(self)
        self.velocity_field.setAction_("velocityCommitted:")
        root.addSubview_(self.velocity_field)

        self.table_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.table_scroll.setHasVerticalScroller_(True)
        self.table_scroll.setHasHorizontalScroller_(True)
        self.table_scroll.setBorderType_(1)
        self.table_scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.table = InspectorPointTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.table.controller = self
        self.table.setColumnAutoresizingStyle_(NSTableViewNoColumnAutoresizing)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setAllowsMultipleSelection_(True)
        self.table.setAllowsEmptySelection_(True)
        self.table.setRowHeight_(22)
        self.table.setTarget_(self)
        self.table.setDoubleAction_("pointDoubleClicked:")
        self.table.registerForDraggedTypes_([POINT_DRAG_TYPE])
        self.data_source = TrackPointDataSource.alloc().initWithController_(self)
        self.table.setDataSource_(self.data_source)
        self.table.setDelegate_(self.data_source)
        self.table_scroll.setDocumentView_(self.table)
        root.addSubview_(self.table_scroll)
        self.build_point_context_menu()

        self.buttons = {}
        for title, action in [
            ("Undo", "undo:"),
            ("Plot", "plot:"),
            ("PNG", "savePng:"),
            ("Split Track Above", "splitTrack:"),
            ("Readjust Time", "readjustTime:"),
            ("Previous Missing", "previousMissingTime:"),
            ("Next Missing", "nextMissingTime:"),
            ("Save", "save:"),
            ("Save & Exit", "saveAndExit:"),
            ("Help", "help:"),
            ("Quit", "quit:"),
        ]:
            button = make_liquid_glass_button(NSMakeRect(0, 0, 110, BUTTON_HEIGHT))
            button.setTitle_(title)
            button.setTarget_(self)
            button.setAction_(action)
            apply_liquid_glass_button_style(button)
            self.buttons[title] = button
            root.addSubview_(button)

        self.layout_window()

    def build_point_context_menu(self):
        menu = NSMenu.alloc().initWithTitle_("Waypoint")
        menu.setAutoenablesItems_(False)
        for title, action, key in [
            ("Insert Point After", "insertPointAfter:", ""),
            ("Insert Point Before", "insertPointBefore:", ""),
            ("Duplicate Points", "duplicatePoints:", ""),
            (None, None, None),
            ("Cut", "cutPoints:", "x"),
            ("Copy", "copyPoints:", "c"),
            ("Paste Below", "pastePoints:", "v"),
            ("Delete", "deletePoints:", ""),
            (None, None, None),
            ("Cut Track Connection After", "cutTrackConnection:", ""),
            ("Join Track Segments", "joinTrackSegments:", ""),
            ("Split Track Above", "splitTrack:", ""),
            ("Readjust Time", "readjustTime:", ""),
            ("Previous Missing Timestamp", "previousMissingTime:", ""),
            ("Next Missing Timestamp", "nextMissingTime:", ""),
            ("Interpolate Height", "interpolateHeight:", ""),
            ("Fit Map to Selection", "fitMapToSelection:", ""),
            ("Fit to Track", "fitMapToTrack:", ""),
        ]:
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(self)
            item.setEnabled_(True)
            menu.addItem_(item)
        self.point_context_menu = menu
        self.table.setMenu_(menu)

    def layout_window(self):
        bounds = self.root.bounds()
        width = bounds.size.width
        height = bounds.size.height
        self.info_label.setFrame_(NSMakeRect(PADDING, height - 84, width - 2 * PADDING, 70))
        self.velocity_label.setFrame_(NSMakeRect(PADDING, height - 114, 95, FIELD_HEIGHT))
        self.velocity_field.setFrame_(NSMakeRect(PADDING + 100, height - 114, 82, FIELD_HEIGHT))
        self.buttons["Previous Missing"].setFrame_(NSMakeRect(210, height - 114, 150, FIELD_HEIGHT))
        self.buttons["Next Missing"].setFrame_(NSMakeRect(368, height - 114, 140, FIELD_HEIGHT))
        self.table_scroll.setFrame_(
            NSMakeRect(PADDING, 60, max(180.0, width - 2 * PADDING), max(100.0, height - 152))
        )
        self.resize_table_document()
        x = PADDING
        for title in ["Undo", "Plot", "PNG", "Split Track Above", "Readjust Time", "Save", "Save & Exit", "Help", "Quit"]:
            w = 120 if title == "Save & Exit" else 100
            if title in {"Split Track Above", "Readjust Time"}:
                w = 120
            self.buttons[title].setFrame_(NSMakeRect(x, PADDING, w, BUTTON_HEIGHT))
            x += w + 8

    def resize_table_document(self):
        if not hasattr(self, "table") or not hasattr(self, "table_scroll"):
            return
        self.table_scroll.tile()
        viewport = self.table_scroll.contentSize()
        spacing = self.table.intercellSpacing()
        document_width, document_height = inspector_table_document_size(
            viewport.width,
            viewport.height,
            [column.width() for column in self.table.tableColumns()],
            len(self.rows),
            self.table.rowHeight(),
            spacing.width,
            spacing.height,
        )
        self.table.setFrameSize_(NSMakeSize(document_width, document_height))

    def windowDidResize_(self, _notification):
        self.layout_window()

    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        self.window.makeFirstResponder_(self.table)

    def windowWillClose_(self, notification):
        if self.parent is not None:
            self.parent.unregister_auxiliary_window(notification.object())
            if self.owns_plot_window:
                self.parent.close_plot_windows_for_inspector(self)
        if self.plot_view is not None and getattr(self.plot_view, "inspector", None) is self:
            self.plot_view.inspector = None
        self.plot_view = None

    def point_elements(self):
        return list(iter_track_points(self.track.element))

    def local_name(self, tag):
        return tag.rsplit("}", 1)[-1]

    def tag_label(self, tag):
        if not tag.startswith("{"):
            return tag
        uri, local = tag[1:].split("}", 1)
        if uri == GPX_NAMESPACE:
            return local
        prefix_map = {
            "https://gurumaps.app/gpx/v3": "gom",
            "http://www.topografix.com/GPX/gpx_style/0/2": "gpx_style",
        }
        return f"{prefix_map.get(uri, uri.rsplit('/', 1)[-1])}:{local}"

    def collect_point_fields(self, point):
        fields = {}
        refs = {}
        for attr_name, attr_value in point.attrib.items():
            key = f"@{attr_name}"
            fields[key] = attr_value
            refs[key] = ("attr", point, attr_name)
        for child in list(point):
            self.collect_child_fields(child, "", fields, refs)
        return fields, refs

    def collect_child_fields(self, element, prefix, fields, refs):
        label = self.tag_label(element.tag)
        if prefix == "" and label == "extensions":
            for child in list(element):
                self.collect_child_fields(child, "", fields, refs)
            return
        key = f"{prefix}/{label}" if prefix else label
        for attr_name, attr_value in element.attrib.items():
            attr_key = f"{key}/@{attr_name}"
            fields[attr_key] = attr_value
            refs[attr_key] = ("attr", element, attr_name)
        if len(list(element)) == 0:
            fields[key] = element.text or ""
            refs[key] = ("element", element)
            return
        if element.text and element.text.strip():
            fields[key] = element.text.strip()
            refs[key] = ("element", element)
        for child in list(element):
            self.collect_child_fields(child, key, fields, refs)

    def reload_rows(self):
        points = self.point_elements()
        point_infos = self.track.points()
        segment_index_by_element = {
            point: segment_index
            for segment_index, segment in enumerate(self.track.element.findall("gpx:trkseg", NS))
            for point in segment.findall("gpx:trkpt", NS)
        }
        velocity_by_element = self.point_velocity_text_by_element(point_infos)
        processed = self.parent.processed_track(self.track)
        original_raw = {point.source_index: point for point in extract_raw_track_points(self.track.element)}
        quality_by_element = {
            original_raw[point.source_index].element: (point.horizontal_status, point.elevation_status)
            for point in processed.raw_points
            if point.source_index in original_raw and original_raw[point.source_index].element is not None
        }
        running_speed_by_element = {}
        for point in processed.points:
            raw = original_raw.get(point.source_index)
            if raw is not None and raw.element is not None:
                quality_by_element[raw.element] = (point.horizontal_status, point.elevation_status)
        for source_index, speed in processed.running_speeds_by_source_index().items():
            raw = original_raw.get(source_index)
            if raw is not None and raw.element is not None:
                running_speed_by_element[raw.element] = f"{speed:.1f}"
        extra = []
        row_fields = []
        row_refs = []
        for point in points:
            fields, refs = self.collect_point_fields(point)
            row_fields.append(fields)
            row_refs.append(refs)
            for name in fields:
                if name not in {"@lat", "@lon", "ele", "time"} and name not in extra:
                    extra.append(name)
        self.extra_columns = extra
        self.rebuild_columns()
        self.rows = []
        self.field_refs = {}
        for index, point in enumerate(points, start=1):
            fields = row_fields[index - 1]
            refs = row_refs[index - 1]
            row = {
                "index": str(index),
                "xy_status": compact_xy_status(
                    quality_by_element.get(point, ("invalid coordinate", "N/A"))[0]
                ),
                "z_status": compact_elevation_status(
                    quality_by_element.get(point, ("N/A", "N/A"))[1]
                ),
                "lat": fields.get("@lat", ""),
                "lon": fields.get("@lon", ""),
                "ele": format_inspector_elevation(fields.get("ele", "")),
                "time": format_inspector_timestamp(fields.get("time", "")),
                "time_source": point_time_source_label(point),
                "calc_velocity": velocity_by_element.get(point, "N/A"),
                "running_speed": running_speed_by_element.get(point, "N/A"),
                "segment_index": segment_index_by_element.get(point, 0),
            }
            for col, key in {"lat": "@lat", "lon": "@lon", "ele": "ele", "time": "time"}.items():
                if key in refs:
                    self.field_refs[(index - 1, col)] = refs[key]
            for name in self.extra_columns:
                row[name] = fields.get(name, "")
                if name in refs:
                    self.field_refs[(index - 1, name)] = refs[name]
            self.rows.append(row)
        self.segment_break_rows = point_table_segment_break_rows(self.rows)
        self.update_info_label()
        self.refresh_velocity_field()
        self.table.reloadData()
        self.resize_table_document()

    def point_velocity_text_by_element(self, points: list[PointInfo]) -> dict[ET.Element, str]:
        velocities: dict[ET.Element, str] = {}
        if len(points) < 2:
            for point in points:
                velocities[point.element] = "N/A"
            return velocities
        for index, point in enumerate(points):
            if index == 0:
                first = points[0]
                second = points[1]
            elif index == len(points) - 1:
                first = points[-2]
                second = points[-1]
            else:
                first = points[index - 1]
                second = points[index + 1]
            speed = self.point_pair_speed(first, second)
            velocities[point.element] = "N/A" if speed is None else f"{speed:.1f}"
        return velocities

    def refresh_calculated_velocity_rows(self, changed_indexes):
        if not self.rows:
            return
        point_elements = self.point_elements()
        velocity_by_element = self.point_velocity_text_by_element(self.track.points())
        affected = set()
        for index in changed_indexes:
            for row in (index - 1, index, index + 1):
                if 0 <= row < len(self.rows):
                    affected.add(row)
        if not affected:
            return
        for row in affected:
            if row < len(point_elements):
                self.rows[row]["calc_velocity"] = velocity_by_element.get(point_elements[row], "N/A")
        column_index = self.table.columnWithIdentifier_(nsstring("calc_velocity"))
        if column_index < 0:
            self.table.reloadData()
            return
        row_indexes = objc.lookUpClass("NSMutableIndexSet").alloc().init()
        for row in sorted(affected):
            row_indexes.addIndex_(row)
        column_indexes = objc.lookUpClass("NSIndexSet").indexSetWithIndex_(column_index)
        self.table.reloadDataForRowIndexes_columnIndexes_(row_indexes, column_indexes)

    def point_pair_speed(self, first: PointInfo, second: PointInfo) -> float | None:
        if first.time is None or second.time is None:
            return None
        seconds = (second.time - first.time).total_seconds()
        if seconds <= 0:
            return None
        distance = haversine_km(first.lat, first.lon, second.lat, second.lon)
        return distance / (seconds / 3600.0)

    def processed_distance_between_rows(self, first_index: int, second_index: int) -> float:
        raw_points = extract_raw_track_points(self.track.element)
        if not raw_points:
            return 0.0
        first_index = max(0, min(int(first_index), len(raw_points) - 1))
        second_index = max(0, min(int(second_index), len(raw_points) - 1))
        first = raw_points[first_index]
        second = raw_points[second_index]
        if first.segment_index != second.segment_index:
            return 0.0
        distances = self.parent.processed_track(self.track).source_distances_km()
        return abs(
            distances.get(second.source_index, 0.0)
            - distances.get(first.source_index, 0.0)
        )

    def existing_point_speed(self, point: PointInfo) -> float | None:
        for element in point.element.iter():
            if self.local_name(element.tag) != "speed" or element.text is None:
                continue
            try:
                value = float(element.text.strip())
            except ValueError:
                continue
            if value <= 0:
                continue
            return value * 3.6 if value < 15.0 else value
        return None

    def speed_for_segment(self, previous: PointInfo, current: PointInfo, mode: str, fallback: float) -> float:
        if mode == "existing":
            values = [self.existing_point_speed(previous), self.existing_point_speed(current)]
            valid = [value for value in values if value is not None and value > 0]
            if valid:
                return sum(valid) / len(valid)
        elif mode == "calculated":
            speed = self.point_pair_speed(previous, current)
            if speed is not None and speed > 0:
                return speed
        return fallback

    def update_info_label(self):
        points = self.track.points()
        metrics = self.parent.compute_metrics(self.track)
        text = (
            f"{self.track.display_label}: {self.track.name} | Points: "
            f"{metrics['retained_npoints']}/{metrics['raw_npoints']} retained/raw | "
            f"Length: {metrics['length_km']:.1f} km | Duration: {format_duration(metrics['duration'])} | "
            f"Avg: {format_speed(metrics.get('speed_kmh'))} | "
            f"Ascent/Descent: {metrics['ascent_m']:.1f}/{metrics['descent_m']:.1f} m"
        )
        selected = self.selection_metrics()
        if selected is not None:
            text += (
                "\nSelected: "
                f"Points {selected['start_index'] + 1}-{selected['end_index'] + 1} | "
                f"Length: {selected['length_km']:.1f} km | "
                f"Duration: {format_duration(selected['duration'], allow_days=True)} | "
                f"Avg: {format_speed(selected['speed_kmh'])} | "
                f"Ascent/Descent: {selected['ascent_m']:.1f}/{selected['descent_m']:.1f} m | "
                f"Time: {format_datetime_local(selected['start_time']) if selected['start_time'] else 'N/A'} - "
                f"{format_datetime_local(selected['end_time']) if selected['end_time'] else 'N/A'}"
            )
        self.info_label.setStringValue_(text)

    def selection_metrics(self):
        rows = self.selected_row_indexes()
        if not rows:
            return None
        ranges = []
        start = previous = rows[0]
        for row in rows[1:]:
            if row == previous + 1:
                previous = row
                continue
            ranges.append((start, previous))
            start = previous = row
        ranges.append((start, previous))
        return self.parent.compute_point_range_metrics(self.track, ranges)

    def refresh_velocity_field(self):
        if self.velocity_field_touched and str(self.velocity_field.stringValue()).strip():
            return
        speed = self.parent.average_speed()
        if speed is None:
            speed = self.parent.compute_metrics(self.track).get("speed_kmh")
        if speed is None or speed <= 0:
            speed = 3.5
        self.velocity_field.setStringValue_(f"{speed:.1f}")

    @objc.IBAction
    def velocityCommitted_(self, _sender):
        self.velocity_field_touched = True

    def rebuild_columns(self):
        for column in list(self.table.tableColumns()):
            self.table.removeTableColumn_(column)
        widths = {
            "index": 60,
            "xy_status": 70,
            "z_status": 92,
            "lat": 120,
            "lon": 120,
            "ele": 72,
            "time": 205,
            "time_source": 175,
            "calc_velocity": 105,
            "running_speed": 105,
        }
        titles = {
            "time_source": "Time Source",
            "xy_status": "XY use",
            "z_status": "Elevation use",
            "calc_velocity": "Velocity\nkm/h",
            "running_speed": "Running mean\nkm/h",
        }
        tooltips = {
            "xy_status": (
                "Used: retained in processed geometry. Smooth: valid but used only for smoothing. "
                "Other values give the rejection reason."
            ),
            "z_status": (
                "Used: measured elevation retained. Interp: elevation was interpolated; "
                "the suffix gives the reason."
            ),
        }
        readonly = {"index", "xy_status", "z_status", "calc_velocity", "running_speed", "time_source"}
        identifiers = ["index", "lat", "lon", "ele", "time", "time_source", "calc_velocity", "running_speed"]
        identifiers += self.extra_columns
        identifiers += ["xy_status", "z_status"]
        for identifier in identifiers:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
            column.setDataCell_(make_table_text_cell())
            column.headerCell().setStringValue_(titles.get(identifier, identifier))
            if identifier in tooltips and hasattr(column, "setHeaderToolTip_"):
                column.setHeaderToolTip_(tooltips[identifier])
            column.setWidth_(widths.get(identifier, 140))
            column.setEditable_(identifier not in readonly)
            self.table.addTableColumn_(column)

    def push_undo(self):
        self.undo_stack.append(ET.tostring(self.track.element, encoding="unicode"))
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def get_or_create_point_child(self, point, local_name):
        if local_name == "ele":
            child = point.find("gpx:ele", NS)
            if child is None:
                child = ET.Element(qname("ele"))
                point.insert(0, child)
            return child
        if local_name == "time":
            return get_or_create_point_time(point)
        for child in list(point):
            if self.local_name(child.tag) == local_name:
                return child
        child = ET.SubElement(point, qname(local_name))
        return child

    def sync_track_start_time_from_first_point(self) -> bool:
        points = self.track.points()
        if not points or points[0].time is None:
            return False
        get_or_create_track_time(self.track.element).text = format_gpx_time(points[0].time)
        if (
            points[0].element is not None
            and point_time_origin(points[0].element) == "manual"
        ):
            set_manual_track_start_time(self.track.element, points[0].time)
        self.parent.invalidate_track_metrics(self.track)
        return True

    def refresh_after_point_table_change(self, selected_rows=None):
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        if self.plot_view is not None:
            rows = selected_rows if selected_rows is not None else self.selected_row_indexes()
            points = self.track.points()
            if rows and points:
                point_index = max(0, min(int(rows[-1]), len(points) - 1))
                self.plot_view.refresh_after_map_edit(self.track, point_index)
            else:
                self.plot_view.cursor = None
                self.plot_view.setNeedsDisplay_(True)
                self.parent.refresh_elevation_profile_for_plot_view(self.plot_view)
        else:
            self.parent.refresh_track_plot_for_track(self.track, self)
        self.parent.redraw_open_plot_views()

    def begin_point_edit(self, row: int, column: str):
        points = self.point_elements()
        if not (0 <= row < len(points)):
            self.active_point_edit = None
            return
        self.active_point_edit = {
            "point_id": id(points[row]),
            "row": row,
            "column": column,
        }

    def commit_point_value(self, row: int, column: str, value: str):
        if self.canceling_point_edit:
            return
        edit = self.active_point_edit
        self.active_point_edit = None
        points = self.point_elements()
        if edit is not None and (
            not (0 <= row < len(points))
            or id(points[row]) != edit.get("point_id")
            or column != edit.get("column")
        ):
            self.parent.set_status(
                "Canceled a waypoint edit because the point table changed."
            )
            return
        self.edit_point_value(row, column, value)

    def cancel_active_point_edit(self):
        edit = self.active_point_edit
        self.active_point_edit = None
        self.canceling_point_edit = True
        try:
            self.table.abortEditing()
        finally:
            self.canceling_point_edit = False
        if edit is not None:
            row = int(edit.get("row", -1))
            column = self.table.columnWithIdentifier_(
                nsstring(str(edit.get("column", "")))
            )
            if 0 <= row < len(self.rows) and column >= 0:
                rows = objc.lookUpClass("NSIndexSet").indexSetWithIndex_(row)
                columns = objc.lookUpClass("NSIndexSet").indexSetWithIndex_(column)
                self.table.reloadDataForRowIndexes_columnIndexes_(rows, columns)
        self.parent.set_status("Waypoint edit canceled.")

    def edit_point_value(self, row, column, value):
        points = self.point_elements()
        if row < 0 or row >= len(points) or column in {"index", "calc_velocity", "running_speed", "time_source"}:
            return
        point = points[row]
        self.push_undo()
        text = value.strip()
        if column == "time":
            text = normalize_inspector_timestamp_edit(text)
        if column in {"lat", "lon"}:
            try:
                float(text)
            except ValueError:
                show_alert("Invalid coordinate.", f"{column} must be a number.")
                self.undo()
                return
            point.set(column, text)
        else:
            ref = self.field_refs.get((row, column))
            if ref is not None:
                kind, *target = ref
                if kind == "attr":
                    target[0].set(target[1], text)
                else:
                    target[0].text = text
            elif column in {"ele", "time"}:
                self.get_or_create_point_child(point, column).text = text
            else:
                show_alert(
                    "Cannot create missing extension field.",
                    "Existing extension fields can be edited, but new nested extension fields are not created here.",
                )
                self.undo()
                return
        if column == "time":
            set_point_time_origin(point, "manual")
        self.parent.invalidate_track_metrics(self.track)
        if row == 0 and column == "time":
            self.sync_track_start_time_from_first_point()
        self.reload_rows()
        self.refresh_calculated_velocity_rows([row])
        self.parent.mark_dirty(f"Edited point {row + 1} in {self.track.display_label}.")
        self.refresh_after_point_table_change([row])

    def selected_row_indexes(self):
        indexes = self.table.selectedRowIndexes()
        if indexes.count() == 0:
            return []
        return [
            index
            for index in range(indexes.firstIndex(), indexes.lastIndex() + 1)
            if indexes.containsIndex_(index)
        ]

    def select_point_rows(self, rows):
        mutable = objc.lookUpClass("NSMutableIndexSet").alloc().init()
        valid = sorted({int(row) for row in rows if 0 <= int(row) < len(self.rows)})
        for row in valid:
            mutable.addIndex_(row)
        self.suppress_selection_change = True
        try:
            self.table.selectRowIndexes_byExtendingSelection_(mutable, False)
            if valid:
                self.table.scrollRowToVisible_(valid[0])
                self.table.scrollRowToVisible_(valid[-1])
        finally:
            self.suppress_selection_change = False
        self.update_info_label()

    def finish_point_structure_change(self, rows, message):
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.select_point_rows(rows)
        self.parent.mark_dirty(message)
        self.refresh_after_point_table_change(rows)

    def insert_point(self, *, before=False):
        rows = self.selected_row_indexes()
        if not self.track.points():
            coordinates = self.parent.prompt_for_coordinate_text()
            if coordinates:
                self.insert_parsed_coordinates(coordinates)
            return
        try:
            self.push_undo()
            _point, new_row = insert_point_for_rows(
                self.track.element,
                rows,
                before=before,
            )
        except ValueError as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot insert a waypoint.", str(exc))
            return
        position = "before" if before else "after"
        self.finish_point_structure_change(
            [new_row],
            f"Inserted point {position} the selection in {self.track.display_label}.",
        )

    @objc.IBAction
    def insertPointAfter_(self, _sender):
        self.insert_point(before=False)

    @objc.IBAction
    def insertPointBefore_(self, _sender):
        self.insert_point(before=True)

    def copy_selected_points(self):
        rows = self.selected_row_indexes()
        points = self.point_elements()
        selected = [points[row] for row in rows if 0 <= row < len(points)]
        if not selected:
            self.parent.set_status("Select one or more waypoints to copy.")
            return False
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.declareTypes_owner_(
            [POINT_PASTEBOARD_TYPE, "public.utf8-plain-text"],
            None,
        )
        pasteboard.setString_forType_(
            serialized_points(selected),
            POINT_PASTEBOARD_TYPE,
        )
        text_rows = []
        for point in selected:
            elevation = next(
                (
                    child.text or ""
                    for child in list(point)
                    if self.local_name(child.tag) == "ele"
                ),
                "",
            )
            timestamp = next(
                (
                    child.text or ""
                    for child in list(point)
                    if self.local_name(child.tag) == "time"
                ),
                "",
            )
            text_rows.append(
                "\t".join(
                    [
                        point.attrib.get("lat", ""),
                        point.attrib.get("lon", ""),
                        elevation,
                        timestamp,
                    ]
                )
            )
        pasteboard.setString_forType_(
            "\n".join(text_rows),
            "public.utf8-plain-text",
        )
        self.parent.set_status(f"Copied {len(selected)} waypoint(s).")
        return True

    def cut_selected_points(self):
        rows = self.selected_row_indexes()
        if not rows or not self.copy_selected_points():
            return
        self.push_undo()
        removed = remove_rows(self.track.element, rows)
        next_row = min(rows[0], max(0, len(self.point_elements()) - 1))
        self.finish_point_structure_change(
            [next_row] if self.point_elements() else [],
            f"Cut {removed} point(s) from {self.track.display_label}.",
        )

    def paste_points(self):
        pasteboard = NSPasteboard.generalPasteboard()
        payload = pasteboard.stringForType_(POINT_PASTEBOARD_TYPE)
        if payload is None:
            plain_text = pasteboard.stringForType_("public.utf8-plain-text")
            try:
                parsed = parse_coordinate_text(str(plain_text or ""))
            except CoordinateParseError as exc:
                show_alert("Cannot paste coordinates.", str(exc))
                return
            self.insert_parsed_coordinates(parsed)
            return
        try:
            points = deserialize_points(str(payload))
            rows = self.selected_row_indexes()
            anchor = rows[-1] if rows else max(0, len(self.rows) - 1)
            self.push_undo()
            inserted_rows = insert_points_after_row(self.track.element, anchor, points)
        except (ET.ParseError, ValueError) as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot paste waypoints.", str(exc))
            return
        self.finish_point_structure_change(
            inserted_rows,
            f"Pasted {len(inserted_rows)} point(s) into {self.track.display_label}.",
        )

    def insert_parsed_coordinates(self, coordinates: list[ParsedCoordinate]):
        if not coordinates:
            return
        rows = self.selected_row_indexes()
        anchor = rows[-1] if rows else len(self.track.points()) - 1
        points = [
            point_from_values(
                self.track.element,
                item.latitude,
                item.longitude,
                elevation_m=item.elevation_m,
                timestamp=item.timestamp,
                name=item.name,
            )
            for item in coordinates
        ]
        try:
            self.push_undo()
            inserted_rows = insert_points_after_row(self.track.element, anchor, points)
        except ValueError as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot insert coordinates.", str(exc))
            return
        self.finish_point_structure_change(
            inserted_rows,
            f"Inserted {len(inserted_rows)} coordinate point(s) in {self.track.display_label}.",
        )
        view = self.ensure_plot_view()
        if view is not None:
            if len(self.track.points()) == len(inserted_rows) and len(inserted_rows) == 1:
                view.render_extent(
                    self.parent.fit_extent_to_aspect(
                        self.parent.extent_for_track_records([self.track]), (1000, 720)
                    ),
                    status="Centered the map on the first waypoint.",
                )
            elif len(inserted_rows) > 1:
                view.render_extent(
                    self.parent.fit_extent_to_aspect(
                        self.parent.extent_for_point_infos(
                            [self.track.points()[row] for row in inserted_rows]
                        ),
                        (1000, 720),
                    ),
                    status="Fitted the map to the inserted waypoints.",
                )

    def duplicate_selected_points(self):
        rows = self.selected_row_indexes()
        points = self.point_elements()
        selected = [points[row] for row in rows if 0 <= row < len(points)]
        if not selected:
            return
        try:
            self.push_undo()
            inserted_rows = insert_points_after_row(
                self.track.element,
                rows[-1],
                [duplicate_point(point) for point in selected],
            )
        except ValueError as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot duplicate waypoints.", str(exc))
            return
        self.finish_point_structure_change(
            inserted_rows,
            f"Duplicated {len(inserted_rows)} point(s) in {self.track.display_label}.",
        )

    def move_selected_rows(self, rows, destination_row):
        if not rows:
            return False
        try:
            self.push_undo()
            moved_rows = move_point_rows(
                self.track.element,
                rows,
                max(0, min(int(destination_row), len(self.rows))),
            )
        except ValueError as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot move waypoints.", str(exc))
            return False
        self.finish_point_structure_change(
            moved_rows,
            f"Moved {len(moved_rows)} point(s) in {self.track.display_label}.",
        )
        return True

    @objc.IBAction
    def duplicatePoints_(self, _sender):
        self.duplicate_selected_points()

    @objc.IBAction
    def cutPoints_(self, _sender):
        self.cut_selected_points()

    @objc.IBAction
    def copyPoints_(self, _sender):
        self.copy_selected_points()

    @objc.IBAction
    def pastePoints_(self, _sender):
        self.paste_points()

    @objc.IBAction
    def deletePoints_(self, _sender):
        self.delete_selected_points()

    def ensure_plot_view(self):
        if self.plot_view is None:
            self.plot_(None)
        return self.plot_view

    @objc.IBAction
    def fitMapToSelection_(self, _sender):
        view = self.ensure_plot_view()
        if view is not None:
            view.zoom_to_selected()

    @objc.IBAction
    def fitMapToTrack_(self, _sender):
        view = self.ensure_plot_view()
        if view is not None:
            view.reset_view()

    @objc.IBAction
    def interpolateHeight_(self, _sender):
        ranges = PlotView.group_point_indexes(self, self.selected_row_indexes())
        self.parent.interpolate_track_heights(self.track, ranges)

    def delete_selected_points(self):
        rows = self.selected_row_indexes()
        if not rows:
            return
        if not confirm("Delete selected waypoints?", f"Delete {len(rows)} waypoint(s) from {self.track.display_label}?"):
            return
        self.push_undo()
        delete_set = {self.point_elements()[index] for index in rows if index < len(self.point_elements())}
        for segment in self.track.element.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                if point in delete_set:
                    segment.remove(point)
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.parent.mark_dirty(f"Deleted {len(delete_set)} point(s) from {self.track.display_label}.")
        remaining_rows = [row for row in rows if row < len(self.rows)]
        if not remaining_rows and self.rows:
            remaining_rows = [min(rows[0], len(self.rows) - 1)]
        self.refresh_after_point_table_change(remaining_rows)

    def select_point_index(self, index):
        self.select_point_range(index, index)

    def select_point_range(self, start, end):
        mutable = objc.lookUpClass("NSMutableIndexSet").alloc().init()
        for index in range(max(0, start), min(len(self.rows), end + 1)):
            mutable.addIndex_(index)
        self.suppress_selection_change = True
        try:
            self.table.selectRowIndexes_byExtendingSelection_(mutable, False)
            if len(self.rows):
                self.table.scrollRowToVisible_(max(0, min(len(self.rows) - 1, start)))
                self.table.scrollRowToVisible_(max(0, min(len(self.rows) - 1, end)))
        finally:
            self.suppress_selection_change = False
        self.update_info_label()

    def clear_point_selection(self):
        mutable = objc.lookUpClass("NSMutableIndexSet").alloc().init()
        self.suppress_selection_change = True
        try:
            self.table.selectRowIndexes_byExtendingSelection_(mutable, False)
        finally:
            self.suppress_selection_change = False
        self.update_info_label()

    def selection_changed(self):
        if self.suppress_selection_change:
            return
        self.update_info_label()
        if self.plot_view is not None:
            rows = self.selected_row_indexes()
            # A direct table selection replaces a range previously established
            # with the map marker. Programmatic map-to-table synchronization is
            # suppressed above and therefore keeps its marker intact.
            self.plot_view.marker = None
            if rows:
                self.plot_view.move_cursor_to_track_point(
                    self.track,
                    rows[-1],
                    self,
                    sync_table=False,
                    focus_plot=False,
                )
            else:
                self.plot_view.setNeedsDisplay_(True)
                self.parent.refresh_elevation_profile_for_plot_view(self.plot_view)

    def undo(self):
        if not self.undo_stack:
            return
        if isinstance(self.undo_stack[-1], TimestampUndoEntry):
            entry = self.undo_stack[-1]
            if self.parent.undo_stack and self.parent.undo_stack[-1] is entry:
                self.parent.undo_(None)
            else:
                self.parent.set_status("Use the Track Editor Undo to undo newer changes first.")
            return
        self.redo_stack.append(ET.tostring(self.track.element, encoding="unicode"))
        self.redo_stack = self.redo_stack[-10:]
        restored = ET.fromstring(self.undo_stack.pop())
        index = self.parent.tracks.index(self.track)
        self.track.element = restored
        self.parent.tracks[index] = self.track
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.parent.mark_dirty(f"Undid point edit in {self.track.display_label}.")
        self.refresh_after_point_table_change()

    def redo(self):
        if not self.redo_stack:
            return
        if isinstance(self.redo_stack[-1], TimestampUndoEntry):
            entry = self.redo_stack[-1]
            if self.parent.redo_stack and self.parent.redo_stack[-1] is entry:
                self.parent.redo_(None)
            else:
                self.parent.set_status("Use the Track Editor Redo to restore earlier changes first.")
            return
        self.undo_stack.append(ET.tostring(self.track.element, encoding="unicode"))
        self.undo_stack = self.undo_stack[-10:]
        restored = ET.fromstring(self.redo_stack.pop())
        index = self.parent.tracks.index(self.track)
        self.track.element = restored
        self.parent.tracks[index] = self.track
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.parent.mark_dirty(f"Redid point edit in {self.track.display_label}.")
        self.refresh_after_point_table_change()

    @objc.IBAction
    def undo_(self, _sender):
        self.undo()

    def navigate_missing_time(self, direction):
        points = self.track.points()
        missing = [index for index, point in enumerate(points) if point.time is None]
        if not missing:
            self.parent.set_status("This track has no missing timestamps.")
            return
        selected = self.selected_row_indexes()
        current = (max(selected) if direction > 0 else min(selected)) if selected else (-1 if direction > 0 else len(points))
        candidates = [index for index in missing if (index - current) * direction > 0]
        row = (min(candidates) if direction > 0 else max(candidates)) if candidates else (missing[0] if direction > 0 else missing[-1])
        self.select_point_rows([row])
        if self.plot_view is not None:
            self.plot_view.move_cursor_to_track_point(self.track, row, self, sync_table=False, focus_plot=False)
            self.parent.refresh_elevation_profile_for_plot_view(self.plot_view)

    @objc.IBAction
    def previousMissingTime_(self, _sender):
        self.navigate_missing_time(-1)

    @objc.IBAction
    def nextMissingTime_(self, _sender):
        self.navigate_missing_time(1)

    @objc.IBAction
    def plot_(self, _sender):
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        existing = self.parent.existing_plot_view("track")
        workspace = getattr(self.parent, "adventure_workspace_delegate", None)
        existing_workspace_map = getattr(workspace, "plot_view", None)
        view = self.parent.open_plot_window("track", tracks_override=[self.track])
        if view is not None:
            previous = view.inspector
            if previous is not None and previous is not self and previous.plot_view is view:
                previous.plot_view = None
                previous.owns_plot_window = False
            self.owns_plot_window = (
                False if existing_workspace_map is not None
                else self.owns_plot_window or existing is None
            )
            view.inspector = self
            self.plot_view = view
            view.setNeedsDisplay_(True)

    @objc.IBAction
    def savePng_(self, _sender):
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        existing = self.parent.existing_plot_view("track")
        view = self.parent.open_or_reload_track_plot_for_track(self.track)
        if view is not None:
            self.owns_plot_window = self.owns_plot_window or existing is None
            view.inspector = self
            self.plot_view = view
            self.parent.save_plot_view_png(view)

    @objc.IBAction
    def pointDoubleClicked_(self, _sender):
        row = self.table.clickedRow()
        if row < 0:
            row = self.table.selectedRow()
        if row < 0 or row >= len(self.rows):
            return
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        existing = self.parent.existing_plot_view("track")
        view = self.parent.open_plot_window("track")
        if view is None:
            return
        self.owns_plot_window = self.owns_plot_window or existing is None
        self.select_point_index(row)
        view.move_cursor_to_track_point(self.track, row, self)
        self.parent.set_status(f"Moved plot cursor to point {row + 1} of {self.track.display_label}.")

    @objc.IBAction
    def splitTrack_(self, _sender):
        rows = self.selected_row_indexes()
        if not rows:
            show_alert("Select the waypoint row above which the track should be cut.")
            return
        split_at = min(rows)
        if confirm("Split track above selected point?", f"Move waypoint {split_at + 1} and all following waypoints to a new track?"):
            new_record = self.parent.split_track_from_index(self.track, split_at)
            if new_record is not None:
                self.reload_rows()
                self.parent.selected_nrs = [self.track.nr]
                self.parent.update_selection_field()
                self.parent.highlight_selected_rows()
                if self.plot_view is not None:
                    self.plot_view.refresh_after_map_edit(self.track, max(0, split_at - 1))
                else:
                    self.parent.refresh_track_plot_for_track(self.track, self)
                    self.parent.redraw_open_plot_views()

    @objc.IBAction
    def cutTrackConnection_(self, _sender):
        rows = self.selected_row_indexes()
        if not rows:
            show_alert("Select the waypoint after which the connection should be cut.")
            return
        row = max(rows)
        try:
            self.push_undo()
            cut_segment_after_row(self.track.element, row)
        except ValueError as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot cut the track connection.", str(exc))
            return
        self.finish_point_structure_change(
            [row],
            f"Cut the track connection after point {row + 1} in {self.track.display_label}.",
        )

    @objc.IBAction
    def joinTrackSegments_(self, _sender):
        rows = self.selected_row_indexes()
        if not rows:
            show_alert("Select an endpoint next to a cut track connection.")
            return
        row = rows[-1]
        try:
            self.push_undo()
            join_segments_at_row(self.track.element, row)
        except ValueError as exc:
            if self.undo_stack:
                self.undo_stack.pop()
            show_alert("Cannot join track segments.", str(exc))
            return
        self.finish_point_structure_change(
            [row],
            f"Joined track segments at point {row + 1} in {self.track.display_label}.",
        )

    @objc.IBAction
    def readjustTime_(self, _sender):
        rows = self.selected_row_indexes()
        if not rows:
            show_alert("Select waypoint rows to readjust their timestamps.")
            return
        options = self.ask_readjust_time_options()
        if options is None:
            return
        direction, speed_mode, velocity = options
        points = self.track.points()
        selected = [index for index in rows if 0 <= index < len(points)]
        if not selected:
            show_alert("No valid waypoint rows selected.")
            return
        if direction == "interpolate":
            if len(selected) < 2:
                show_alert("Select at least two points.", "Interpolation needs a first and last selected waypoint.")
                return
            first_point = points[selected[0]]
            last_point = points[selected[-1]]
            if first_point.time is None or last_point.time is None:
                show_alert("Missing timestamps.", "Interpolation needs timestamps on the first and last selected waypoints.")
                return
            total_hours = (last_point.time - first_point.time).total_seconds() / 3600.0
            if total_hours <= 0:
                show_alert("Invalid timestamps.", "The last selected timestamp must be after the first selected timestamp.")
                return
            total_distance = 0.0
            previous_index = selected[0]
            for index in selected[1:]:
                total_distance += self.processed_distance_between_rows(previous_index, index)
                previous_index = index
            if total_distance <= 0:
                show_alert("No distance.", "The selected points do not contain a measurable distance.")
                return
            interpolated_speed = total_distance / total_hours
            self.velocity_field.setStringValue_(f"{interpolated_speed:.1f}")
            self.push_undo()
            elapsed_hours = 0.0
            previous_index = selected[0]
            for index in selected:
                point = points[index]
                if index != selected[0]:
                    elapsed_hours += self.processed_distance_between_rows(previous_index, index) / interpolated_speed
                get_or_create_point_time(point.element).text = format_gpx_time(first_point.time + timedelta(hours=elapsed_hours))
                previous_index = index
            if 0 in selected:
                self.sync_track_start_time_from_first_point()
            self.velocity_field_touched = True
            self.parent.invalidate_track_metrics(self.track)
            self.reload_rows()
            self.refresh_calculated_velocity_rows(selected)
            self.parent.mark_dirty(f"Interpolated timestamps for {len(selected)} point(s) in {self.track.display_label}.")
            self.refresh_after_point_table_change(selected)
            return
        anchor_index = selected[0] if direction == "forward" else selected[-1]
        anchor_time = points[anchor_index].time
        if anchor_time is None:
            anchor_name = "first" if direction == "forward" else "last"
            show_alert("Missing timestamp.", f"The {anchor_name} selected waypoint needs a timestamp to start the recalculation.")
            return
        self.push_undo()
        if direction == "forward":
            elapsed_hours = 0.0
            previous_index = selected[0]
            previous = points[previous_index]
            for index in selected:
                point = points[index]
                if index != selected[0]:
                    segment_speed = self.speed_for_segment(previous, point, speed_mode, velocity)
                    elapsed_hours += self.processed_distance_between_rows(previous_index, index) / segment_speed
                get_or_create_point_time(point.element).text = format_gpx_time(anchor_time + timedelta(hours=elapsed_hours))
                previous = point
                previous_index = index
        else:
            elapsed_hours = 0.0
            previous_index = selected[-1]
            previous = points[previous_index]
            for index in reversed(selected):
                point = points[index]
                if index != selected[-1]:
                    segment_speed = self.speed_for_segment(point, previous, speed_mode, velocity)
                    elapsed_hours += self.processed_distance_between_rows(index, previous_index) / segment_speed
                get_or_create_point_time(point.element).text = format_gpx_time(anchor_time - timedelta(hours=elapsed_hours))
                previous = point
                previous_index = index
        if 0 in selected:
            self.sync_track_start_time_from_first_point()
        self.velocity_field_touched = True
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.refresh_calculated_velocity_rows(selected)
        self.parent.mark_dirty(f"Readjusted timestamps for {len(selected)} point(s) in {self.track.display_label}.")
        self.refresh_after_point_table_change(selected)

    def interpolation_speed_for_selected_rows(self) -> float | None:
        points = self.track.points()
        selected = [index for index in self.selected_row_indexes() if 0 <= index < len(points)]
        if len(selected) < 2:
            return None
        first_point = points[selected[0]]
        last_point = points[selected[-1]]
        if first_point.time is None or last_point.time is None:
            return None
        total_hours = (last_point.time - first_point.time).total_seconds() / 3600.0
        if total_hours <= 0:
            return None
        total_distance = 0.0
        previous_index = selected[0]
        for index in selected[1:]:
            total_distance += self.processed_distance_between_rows(previous_index, index)
            previous_index = index
        if total_distance <= 0:
            return None
        return total_distance / total_hours

    @objc.IBAction
    def readjustDirectionChanged_(self, sender):
        if sender.indexOfSelectedItem() != 2:
            return
        speed = self.interpolation_speed_for_selected_rows()
        if speed is not None and hasattr(self, "_readjust_velocity_field"):
            self._readjust_velocity_field.setStringValue_(f"{speed:.1f}")

    def ask_readjust_time_options(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Readjust Time")
        alert.setInformativeText_("Choose the direction and velocity source for timestamp recalculation.")
        alert.addButtonWithTitle_("Readjust")
        alert.addButtonWithTitle_("Cancel")
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 430, 106))
        for text, x, y, width in [
            ("Direction", 0, 76, 120),
            ("Velocity source", 0, 46, 120),
            ("Average km/h", 0, 16, 120),
        ]:
            label = NSTextField.labelWithString_(text)
            label.setFrame_(NSMakeRect(x, y, width, FIELD_HEIGHT))
            accessory.addSubview_(label)
        direction_menu = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(130, 76, 210, FIELD_HEIGHT), False)
        direction_menu.addItemsWithTitles_(
            ["Forward from first timestamp", "Backward from last timestamp", "Interpolate between first and last timestamp"]
        )
        direction_menu.setToolTip_("Choose whether timestamps are calculated from one endpoint or interpolated between both selected endpoint timestamps.")
        direction_menu.setTarget_(self)
        direction_menu.setAction_("readjustDirectionChanged:")
        accessory.addSubview_(direction_menu)
        mode_menu = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(130, 46, 210, FIELD_HEIGHT), False)
        mode_menu.addItemsWithTitles_(["Average velocity", "Existing track speed", "Calculated point velocity"])
        mode_menu.setToolTip_("Choose the speed source used between points. Average velocity uses the editable km/h value below.")
        accessory.addSubview_(mode_menu)
        velocity_field = NSTextField.alloc().initWithFrame_(NSMakeRect(130, 16, 90, FIELD_HEIGHT))
        velocity_field.setStringValue_(str(self.velocity_field.stringValue()))
        accessory.addSubview_(velocity_field)
        self._readjust_velocity_field = velocity_field
        alert.setAccessoryView_(accessory)
        if alert.runModal() != 1000:
            self._readjust_velocity_field = None
            return None
        directions = ["forward", "backward", "interpolate"]
        direction = directions[max(0, min(direction_menu.indexOfSelectedItem(), len(directions) - 1))]
        speed_modes = ["average", "existing", "calculated"]
        speed_mode = speed_modes[max(0, min(mode_menu.indexOfSelectedItem(), len(speed_modes) - 1))]
        if direction == "interpolate":
            speed = self.interpolation_speed_for_selected_rows()
            if speed is not None:
                velocity_field.setStringValue_(f"{speed:.1f}")
                self.velocity_field.setStringValue_(f"{speed:.1f}")
                self._readjust_velocity_field = None
                return direction, speed_mode, speed
            self._readjust_velocity_field = None
            return direction, speed_mode, 0.0
        try:
            velocity = float(str(velocity_field.stringValue()).strip().replace(",", "."))
        except ValueError:
            self._readjust_velocity_field = None
            show_alert("Invalid velocity.", "Enter a positive average velocity in km/h.")
            return None
        if velocity <= 0:
            self._readjust_velocity_field = None
            show_alert("Invalid velocity.", "Velocity must be greater than zero.")
            return None
        self.velocity_field.setStringValue_(f"{velocity:.1f}")
        self._readjust_velocity_field = None
        return direction, speed_mode, velocity

    @objc.IBAction
    def save_(self, _sender):
        self.parent.mark_dirty(f"{self.track.display_label} waypoint edits saved in memory.")
        self.parent.redraw_open_plot_views()

    @objc.IBAction
    def saveAndExit_(self, _sender):
        self.save_(None)
        self.window.close()

    @objc.IBAction
    def help_(self, _sender):
        show_alert(
            f"Inspect {self.track.display_label} Help",
            "This window shows every raw waypoint of the selected track. Scroll the table to inspect coordinates, height, time, accuracy fields, and any extra data stored with each point. The compact XY use and Elevation use columns are at the right end. Used means retained in processed geometry; Smooth means valid but used only for smoothing. Interp retains the reason for elevation interpolation, while the other short values state rejection reasons. The header reports retained/raw point counts.\n\n"
            "Click a row to select a waypoint. Shift-click or drag in the table to select a range. Double-click a row to open the track map if needed and move the white cursor dot and arrow to that waypoint.\n\n"
            "Edit a table cell and press Enter to change a waypoint value. Undo restores recent inspector edits. Backspace/Delete removes selected waypoints after confirmation.\n\n"
            "Command-I inserts a waypoint after the selected rows; Shift-Command-I inserts before them. Interior points are placed halfway between their neighbors. At a track end, the last two points are extrapolated. Command-C, Command-X, and Command-V copy, cut, and paste complete GPX waypoint data. Drag selected table rows to reorder them inside the same GPX segment. A thin separator marks every cut connection. The right-click menu can cut a connection or join the adjoining segment endpoints again, and also provides Duplicate, Fit Map, Split Track Above, and Readjust Time.\n\n"
            "Plot Track opens the track map. When this inspector and the map are both open, selecting points in the table highlights them on the map; clicking the map selects the nearest waypoint here. If a map marker is active, the selected range is shown in red. A new manual table selection replaces the active map-marker range.\n\n"
            "On the track map, right-click a waypoint or segment for editing, centering, coordinate display, insertion, pasting, and connection-cut commands. Cutting a connection creates a GPX segment boundary but keeps one track; short perpendicular marks show segment ends, and selecting either adjoining endpoint allows the segments to be joined again. Press m or Shift-click to start a marked range. Right-click its red line to copy, cut, or delete it, or hold it for one second and drag all selected points together. Hold an unselected waypoint or connecting segment to move or insert one point. A normal drag or two-finger trackpad scroll pans the map, Option-drag pans immediately, Option-arrow pans by part of the window, and the mouse wheel or a trackpad pinch zooms. Press Escape while dragging to cancel, or otherwise to hide point information. Shift-Z fits the map to the complete current track after points have moved beyond the old map boundary.\n\n"
            "Split Track Above moves the selected waypoint and all following waypoints into a new track. Readjust Time recalculates selected waypoint timestamps from the first selected timestamp and the velocity field. Save keeps inspector edits in memory; Save & Exit keeps them and closes this window. The main editor Save writes everything to disk.",
        )

    @objc.IBAction
    def quit_(self, _sender):
        self.window.close()


class GPXEditorWindowDelegate(NSObject):
    def initWithController_(self, controller):
        self = objc.super(GPXEditorWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowDidResize_(self, _notification):
        self.controller.layout_window()

    def windowShouldClose_(self, _sender):
        if self.controller.hide_main_editor_for_workspace():
            return False
        if not self.controller.quit_confirmed and not self.controller.confirm_quit():
            return False
        try:
            _sender.orderOut_(None)
        except Exception:
            pass
        self.controller.finalize_editor_close(delete_recovery=True)
        if self.controller.standalone:
            NSApp().terminate_(None)
        return True

    def windowWillClose_(self, _notification):
        self.controller.finalize_editor_close(delete_recovery=True)


class GPXEditorPdfSummaryWindowDelegate(NSObject):
    def initWithController_(self, controller):
        self = objc.super(GPXEditorPdfSummaryWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowDidResize_(self, _notification):
        self.controller.layout_pdf_summary_window()

    def windowShouldClose_(self, window):
        self.controller.closePdfSummary_(None)
        return False

    def windowWillClose_(self, notification):
        self.controller.cancelPdfSummary_(None)
        if self.controller.pdf_summary_poll_timer is not None:
            self.controller.pdf_summary_poll_timer.invalidate()
            self.controller.pdf_summary_poll_timer = None
        window = notification.object()
        self.controller.unregister_auxiliary_window(window)
        self.controller.pdf_summary_window = None
        self.controller.pdf_summary_browse_button = None
        self.controller.pdf_summary_export_button = None
        self.controller.pdf_summary_close_button = None
        self.controller.pdf_summary_delegate = None


def export_prepared_summary_pdf(path, snapshot, renderer, cancel, progress):
    try:
        if renderer is not None:
            renderer.prepare_checkpoint_data()
        return export_summary_pdf(path, snapshot, map_pages=renderer.write_pages if renderer else None,
                                  cancel=cancel, progress=progress)
    except TileRequestCancelled as exc:
        raise PDFExportCancelled("PDF map downloading was cancelled.") from exc


class PDFMapRenderer:
    """Reuse PDF map rendering with detached tracks and no Cocoa objects."""

    _render_methods = frozenset({
        "processing_parameter_signature", "processed_track", "display_processed_track",
        "extent_for_track_records", "fit_extent_to_aspect",
        "effective_tile_zoom_for_limit", "tile_diagnostics", "map_tile_urls", "map_tile_urls_for_provider",
        "cached_tile_urls", "missing_map_tile_count", "add_basemap_with_timeout",
        "make_pdf_map_figure", "draw_pdf_elevation_profile", "draw_pdf_profile_time_ticks",
        "pdf_distance_at_elapsed", "profile_time_tick_step", "profile_short_elapsed_label",
        "draw_pdf_profile_scale_bar", "draw_pdf_start_end_labels", "draw_pdf_overview_labels",
    })

    def __init__(self, source, tracks, options, cancel, progress, checkpoint_sidecar_path=None):
        self.options, self.cancel, self.progress = options, cancel, progress
        self.processing_settings = source.processing_options()
        self.project_parameters = pdf_report_parameters(source.project_parameters, options)
        self.selected_nrs = tuple(source.selected_nrs)
        self.track_ids = track_ids(getattr(source, "tracks", tracks))
        for name in (
            "tile_cache_dir", "map_padding_fraction", "track_zoom", "overview_zoom",
            "pdf_document_dpi", "pdf_map_dpi", "pdf_overview_zoom", "pdf_track_zoom",
            "pdf_maximum_map_tiles", "maximum_map_zoom", "map_provider",
            "output_map_provider", "custom_map_url", "custom_map_attribution",
            "map_credential_id", "map_request_timeout_seconds", "elevation_headroom_fraction", "debug",
        ):
            setattr(self, name, getattr(source, name))
        pdf_provider = self.project_parameters.get("pdf.map_provider", "output")
        if pdf_provider != "output":
            self.output_map_provider = pdf_provider
        self.map_tile_attempts = 3
        self.map_minimum_read_timeout = 0.0 if self.output_map_provider == "osm" else 30.0
        self.map_wait_for_network = False
        self.geoapify_style = self.project_parameters.get("pdf.geoapify_style", "osm-bright")
        self.cached_map_tile_urls = None
        map_count = int(options.include_overview) + (len(tracks) if options.include_track_maps else 0)
        self.map_cache_only = self.output_map_provider == "osm" and map_count > 1
        self.overview_numbers = False  # Labels come from saved formatting, not an open map window.
        self.overview_dots = source.pdf_overview_show_endpoint_dots()
        self.track_dots = source.pdf_track_show_endpoint_dots()
        directories = {Path(track.source_file).parent / "trackimages" for track in tracks if track.source_file}
        media_context = getattr(source, "media_context", {}) or {}
        self.media_directories = tuple({Path(track.source_file).parent for track in tracks if track.source_file})
        self.media_control_file = media_context.get("control_file")
        if media_context.get("project_directory"):
            self.media_directories = (Path(media_context["project_directory"]),)
        self.media_index = None
        self.checkpoint_cache = {}
        self.checkpoint_sidecar_path = checkpoint_sidecar_path
        self.checkpoint_project_directory = media_context.get("project_directory")
        self.page_continuation = None
        self.track_context = {}
        cumulative, previous = 0.0, None
        for item in getattr(source, "tracks", tracks):
            if not item.hidden:
                length = item.metrics.get("length_km")
                cumulative = cumulative + float(length) if cumulative is not None and length is not None else None
            gap = None
            if previous is not None:
                coords = (previous.metrics.get("last_lat"), previous.metrics.get("last_lon"),
                          item.metrics.get("first_lat"), item.metrics.get("first_lon"))
                if all(value is not None for value in coords):
                    gap = haversine_km(*coords)
            self.track_context[item.nr] = (cumulative, gap)
            previous = item
        if media_context.get("project_directory"):
            directories.add(Path(media_context["project_directory"]) / "trackimages")
        self.background_directories = tuple(sorted(directories))
        self.tracks = []
        for track in tracks:
            record = TrackRecord(
                nr=track.nr, element=copy.deepcopy(track.element),
                source_file=track.source_file, source_track_index=track.source_track_index,
                metrics=dict(track.metrics), metrics_dirty=False,
                processed=track.processed, processed_signature=track.processed_signature,
                processed_fingerprint=track.processed_fingerprint,
                display_geometry=track.display_geometry,
            )
            name = record.element.find("gpx:name", NS)
            if name is not None:
                name.text = report_name(report_name(track.name, options.name_prefix),
                                        self.project_parameters.get("trackmaps.remove_name_prefix", ""))
            self.tracks.append(record)

    def __getattr__(self, name):
        if name in self._render_methods:
            method = getattr(GPXEditorController, name)
            return lambda *args, **kwargs: method(self, *args, **kwargs)
        raise AttributeError(name)

    def processing_options(self):
        return self.processing_settings

    def selected_tracks(self):
        return [track for track in self.tracks if track.nr in self.selected_nrs]

    def pdf_overview_show_track_numbers(self):
        return self.overview_numbers

    def pdf_overview_show_endpoint_dots(self):
        return self.overview_dots

    def pdf_track_show_endpoint_dots(self):
        return self.track_dots

    def set_status(self, message):
        check_cancelled(self.cancel)
        self.progress(message)

    def pdf_track_summary_text(self, mode, tracks, title):
        visible = [track for track in tracks if not track.hidden]
        length = sum(track.metrics.get("length_km", 0) for track in visible)
        duration = sum((track.metrics.get("duration") or timedelta() for track in visible), timedelta())
        return f"{len(tracks)} track(s) | Length {length:.1f} km | Duration {format_total_duration(duration)}"

    def checkpoints_for_track(self, track):
        if not self.project_parameters.get("pdf.checkpoints", True):
            return ()
        if track.nr not in self.checkpoint_cache:
            self.set_status(f"Preparing checkpoints and breaks for {track.display_label}.")
            markers = build_checkpoints(self.processed_track(track), self.project_parameters)
            if self.project_parameters.get("pdf.track_info", True) and (
                self.project_parameters.get("pdf.checkpoint_weather", True)
                or self.project_parameters.get("pdf.checkpoint_places", True)
            ):
                if self.media_index is None:
                    self.set_status("Reading existing place and weather sidecars for the PDF (no lookups).")
                    self.media_index = PDFMediaIndex(self.media_directories, self.media_control_file, self.cancel)
                markers = self.media_index.enrich(markers, self.project_parameters)
            self.checkpoint_cache[track.nr] = markers
        return self.checkpoint_cache[track.nr]

    def track_panel(self, track, figure, width_inches):
        if not self.project_parameters.get("pdf.track_info", True):
            return None
        statistics = track_statistics(track, *self.track_context.get(track.nr, (None, None))) \
            if self.project_parameters.get("pdf.track_statistics", True) else ()
        return measure_panel(figure, width_inches, statistics, self.checkpoints_for_track(track), self.project_parameters)

    def prepare_checkpoint_data(self):
        if (self.checkpoint_sidecar_path is None or not self.options.include_track_maps
                or not self.project_parameters.get("pdf.track_info", True)
                or not self.project_parameters.get("pdf.checkpoints", True)
                or not (self.project_parameters.get("pdf.checkpoint_places", True)
                        or self.project_parameters.get("pdf.checkpoint_weather", True))):
            return
        markers, counts = [], []
        for track in self.tracks:
            values = self.checkpoints_for_track(track)
            counts.append((track.nr, len(values)))
            markers.extend(values)
        if not markers:
            return
        def key():
            return read_provider_credential("open-meteo", str(self.project_parameters.get("weather.credential_id", "default")))
        sidecar_path = self.checkpoint_sidecar_path
        try:
            sidecar_path = prepare_checkpoint_cache(self.checkpoint_project_directory, sidecar_path,
                                                    cancel=self.cancel, progress=self.set_status)
        except (OSError, ValueError) as exc:
            self.set_status(f"Adventure checkpoint cache unavailable: {exc}; using this report's sidecar.")
        result = enrich_checkpoints(markers, self.media_index, self.project_parameters, sidecar_path,
                                    cancel=self.cancel, progress=self.set_status, weather_key=key)
        offset = 0
        for number, count in counts:
            self.checkpoint_cache[number] = tuple(result[offset:offset + count])
            offset += count

    def reuse_pdf_background(self, ax, mode, tracks, extent, pixel_size, tile_zoom=None):
        if self.output_map_provider == "custom":
            return False  # A display name cannot identify a custom URL/style.
        candidates = []
        for directory in self.background_directories:
            if mode == "track":
                candidates.extend(sorted(directory.glob(f"{tracks[0].nr:04d}_*.png")))
            else:
                candidates.extend(path for path in sorted(directory.glob("*.png"))
                                  if not path.name[:1].isdigit())
        if not candidates:
            return False
        image = reusable_map_background(
            candidates, mode=mode, extent=extent, pixel_size=pixel_size,
            fingerprints=[processed_track_geometry_fingerprint(self.processed_track(track)) for track in tracks],
            provider=provider_display_name(self.output_map_provider, self.geoapify_style),
            minimum_zoom=tile_zoom,
        )
        if image is None:
            return False
        ax.imshow(image, extent=(extent["min_x"], extent["max_x"], extent["min_y"], extent["max_y"]),
                  origin="upper", interpolation="bilinear", zorder=0)
        self.set_status("PDF export: reusing an existing attributed map background.")
        return True

    @serialized_plotting
    def write_pages(self, pdf):
        class Figures:
            figure = staticmethod(new_figure)
            close = staticmethod(lambda figure: figure.clear())

        size = (11.69, 8.27) if self.options.orientation == "landscape" else (8.27, 11.69)
        options = {
            "elevation_profile": self.options.elevation_profile,
            "rotate_maps": self.options.rotate_maps,
        }
        jobs = []
        if self.options.include_overview:
            jobs.append(("overview", self.tracks, "Overview"))
        if self.options.include_track_maps:
            jobs.extend(("track", [track], report_stage_title(track, self.tracks, self.track_ids)) for track in self.tracks)
        for index, (mode, tracks, title) in enumerate(jobs, 1):
            self.page_continuation = None
            self.set_status(f"Rendering map page {index} of {len(jobs)}: {title}")
            figure = self.make_pdf_map_figure(size, mode, tracks, title, self.options.orientation, options, Figures)
            try:
                check_cancelled(self.cancel)
                if figure is not None:
                    pdf.savefig(figure, dpi=self.pdf_document_dpi)
            finally:
                if figure is not None:
                    figure.clear()
            if self.page_continuation is not None:
                layout, first_row = self.page_continuation
                for extra in continuation_figures(size, title, layout, first_row):
                    try:
                        check_cancelled(self.cancel)
                        pdf.savefig(extra, dpi=self.pdf_document_dpi)
                    finally:
                        extra.clear()


class GPXEditorController(NSObject):
    def initStandalone_(self, standalone=True):
        self = objc.super(GPXEditorController, self).init()
        if self is None:
            return None
        self.standalone = standalone
        self.base_dir = Path(__file__).resolve().parent
        self.tracks: list[TrackRecord] = []
        self.table_rows: list[dict[str, str]] = []
        self.selected_nrs: list[int] = []
        self.suppress_selection_change = False
        self.next_nr = 1
        self.document_ids_initialized = False
        self.anchor: tuple[float, float] | None = None
        self.project_name = ""
        self.project_name_fallback = ""
        self.project_name_needs_metadata_repair = False
        self.last_save_path: Path | None = None
        self.last_load_dir: Path | None = None
        self.last_pdf_path: Path | None = None
        self.last_png_dir: Path | None = None
        self.last_track_export_dir: Path | None = None
        self.pdf_summary_window = None
        self.pdf_summary_path_field = None
        self.pdf_summary_checkboxes = {}
        self.pdf_summary_orientation_menu = None
        self.pdf_summary_options = {}
        self.pdf_summary_status_label = None
        self.pdf_summary_browse_button = None
        self.pdf_summary_export_button = None
        self.pdf_summary_close_button = None
        self.pdf_summary_delegate = None
        self.pdf_summary_poll_timer = None
        self.pdf_summary_future = None
        self.pdf_summary_export_request = None
        self.pdf_summary_cancel = None
        self.pdf_summary_progress = ""
        self.pdf_summary_result_message = ""
        self.pdf_only_loading = False
        self.pdf_summary_name_prefix = None
        self.pdf_summary_scope_menu = None
        self.pdf_summary_open_button = None
        self.pdf_summary_reveal_button = None
        self.pdf_summary_cancel_button = None
        self.pdf_summary_positions = []
        self.on_close_callback = None
        self.on_save_callback = None
        self.on_settings_change_callback = None
        self.media_context = {}
        self.settings_controller = None
        self.parameter_load_warnings = []
        self.help_window = None
        self.media_viewer = None
        self.license_document_windows = {}
        self.did_auto_size_track_columns = False
        self.dirty = False
        self.debug = False
        self.duration_diagnostic_cache: dict[int, tuple] = {}
        self.quit_confirmed = False
        self.editor_close_finalized = False
        self.sort_column = None
        self.sort_ascending = True
        self.suppress_sort_descriptor_change = False
        self.pending_header_column = None
        self.active_table_edit = None
        self.canceling_table_edit = False
        self.undo_stack: list[list[tuple[int, str, str]]] = []
        self.redo_stack: list[list[tuple[int, str, str]]] = []
        self.track_context_menu = None
        self.plot_windows = []
        self.plot_window_refs = []
        self.auxiliary_windows = []
        self.closing_auxiliary_windows = False
        self.processing_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mycamino-gpx-processing",
        )
        self.metrics_cache_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mycamino-gpx-metrics-cache",
        )
        self.track_load_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mycamino-gpx-load",
        )
        self.processing_future = None
        self.processing_poll_timer = None
        self.processing_generation = 0
        self.processing_track_ids = []
        self.processing_track_fingerprints = []
        self.track_load_timer = None
        self.pending_track_load_future = None
        self.pending_track_parse_future = None
        self.pending_track_parse_request = None
        self.track_mutations_enabled = True
        self.pending_track_loads: list[TrackRecord] = []
        self.pending_track_load_index = 0
        self.pending_track_load_total = 0
        self.pending_track_load_mark_dirty = False
        self.pending_track_load_path_count = 0
        self.pending_track_load_status = ""
        self.pending_track_load_start_index = 0
        self.pending_track_load_offer_sort = False
        self.pending_track_load_anchor_was_set = False
        self.pending_route_timing_media_after_load: list[Path] = []
        self.pending_pdf_export_after_load = False
        self.on_initial_load_complete_callback = None
        self.initial_load_completion_notified = False
        if self.standalone:
            self.project_parameters, self.parameter_load_warnings = load_parameter_subset(
                STANDALONE_SETTINGS_PATH,
                EDITOR_PARAMETER_KEYS,
            )
            if not STANDALONE_SETTINGS_PATH.is_file():
                provider_preference = load_map_provider_preference()
                preferred_provider = str(
                    provider_preference.get("preferred_output_provider", "")
                )
                credential_id = str(provider_preference.get("credential_id", "default"))
                if preferred_provider in {"osm", "esri"} or (
                    provider_requires_credential(preferred_provider)
                    and provider_preference.get("credential_verified", False)
                    and read_provider_credential(preferred_provider, credential_id)
                ):
                    self.project_parameters["maps.output_provider"] = preferred_provider
                    self.project_parameters["maps.credential_id"] = credential_id
        else:
            self.project_parameters = default_parameters()
        self._apply_parameter_attributes()
        self.tile_cache_dir = TILE_CACHE_DIR
        self.tile_cache_dir.mkdir(parents=True, exist_ok=True)
        self.cached_map_tile_urls: set[str] | None = None
        MPL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.prune_tile_cache(max_age_seconds=self.map_cache_retention_hours * 3600.0)
        self.columns = [
            ("row", "Row\n", 56, False),
            ("nr", "Track\nNumber", 76, False),
            ("stage", "Stage\n", 62, True),
            ("track_id", "Track ID\n", 76, False),
            ("type", "Type\n", 100, False),
            ("show", "Show\n", 58, True),
            ("name", "Name\n", 220, True),
            ("date", "Date & Time\n", 155, True),
            ("length", "Length\n[km]", 92, False),
            ("duration", "Duration\n[hh:mm]", 95, False),
            ("sum", "Sum\n[km]", 82, False),
            ("distance", "Distance\n[km]", 100, False),
            ("speed", "Avg Speed\n[km/h]", 105, False),
            ("moving_speed", "Moving Avg\n[km/h]", 105, False),
            ("ascent", "Ascent\n[m]", 82, False),
            ("descent", "Descent\n[m]", 88, False),
            ("npoints", "NPoints\n", 82, False),
        ]
        self._build_window()
        self.autosave_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self.editor_autosave_seconds, self, "autosave:", None, True
        )
        return self

    def _apply_parameter_attributes(self):
        values = self.project_parameters
        self.editor_autosave_seconds = float(values["gpx.editor_autosave_seconds"])
        self.map_padding_fraction = float(values["gpx.map_padding_fraction"])
        self.overview_zoom = int(values["gpx.overview_zoom"])
        self.track_zoom = int(values["gpx.track_zoom"])
        self.horizontal_smoothing_distance_m = float(values["gpx.horizontal_smoothing_distance_m"])
        self.minimum_point_spacing_m = float(values["gpx.minimum_point_spacing_m"])
        self.elevation_smoothing_distance_m = float(values["gpx.elevation_smoothing_distance_m"])
        self.maximum_horizontal_accuracy_m = float(values["gpx.maximum_accuracy_m"])
        self.maximum_vertical_accuracy_m = float(values["gpx.maximum_vertical_accuracy_m"])
        self.maximum_hdop = float(values["gpx.maximum_hdop"])
        self.maximum_vdop = float(values["gpx.maximum_vdop"])
        self.running_speed_window_distance_m = float(values["gpx.running_speed_window_distance_m"])
        self.media_timing_max_distance_m = float(values["gpx.media_timing_max_distance_m"])
        self.stationary_speed_threshold_kmh = float(values["gpx.stationary_speed_threshold_kmh"])
        self.elevation_headroom_fraction = float(values["gpx.elevation_headroom_fraction"])
        self.maximum_map_tiles = int(values["gpx.maximum_map_tiles"])
        self.pdf_document_dpi = int(values["pdf.document_dpi"])
        self.pdf_map_dpi = float(values["pdf.map_dpi"])
        self.pdf_overview_zoom = int(values["pdf.overview_zoom"])
        self.pdf_track_zoom = int(values["pdf.track_zoom"])
        self.pdf_maximum_map_tiles = int(values["pdf.maximum_map_tiles"])
        self.map_provider = str(values["maps.interactive_provider"])
        self.output_map_provider = str(values["maps.output_provider"])
        self.map_credential_id = str(values["maps.credential_id"])
        self.custom_map_url = str(values["maps.custom_url"])
        self.custom_map_attribution = str(values["maps.custom_attribution"])
        self.maximum_map_zoom = int(values["maps.maximum_zoom"])
        self.map_request_timeout_seconds = float(values["maps.request_timeout_seconds"])
        self.map_cache_retention_hours = float(values["maps.cache_retention_hours"])

    def apply_project_parameters(self, settings=None):
        """Apply project-scoped settings to an embedded editor instance."""
        previous_processing_signature = self.processing_parameter_signature() if hasattr(
            self, "horizontal_smoothing_distance_m"
        ) else None
        self.project_parameters = normalize_parameters(settings)
        self._apply_parameter_attributes()
        timer = getattr(self, "autosave_timer", None)
        if timer is not None:
            timer.invalidate()
            self.autosave_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.editor_autosave_seconds, self, "autosave:", None, True
            )
        self.cached_map_tile_urls = None
        self.prune_tile_cache(max_age_seconds=self.map_cache_retention_hours * 3600.0)
        if self.settings_controller is not None:
            self.settings_controller.update_values(self.project_parameters)
        if previous_processing_signature is not None and previous_processing_signature != self.processing_parameter_signature() and hasattr(self, "track_table"):
            self.invalidate_all_metrics()
            self.refresh_processing_summary()
            self.schedule_processing_recalculation()

    def processing_options(self) -> ProcessingOptions:
        return ProcessingOptions(
            horizontal_smoothing_distance_m=self.horizontal_smoothing_distance_m,
            minimum_point_spacing_m=self.minimum_point_spacing_m,
            elevation_smoothing_distance_m=self.elevation_smoothing_distance_m,
            maximum_horizontal_accuracy_m=self.maximum_horizontal_accuracy_m,
            maximum_vertical_accuracy_m=self.maximum_vertical_accuracy_m,
            maximum_hdop=self.maximum_hdop,
            maximum_vdop=self.maximum_vdop,
            running_speed_window_distance_m=self.running_speed_window_distance_m,
            stationary_speed_threshold_kmh=self.stationary_speed_threshold_kmh,
        ).normalized()

    def processing_parameter_signature(self) -> tuple:
        options = self.processing_options()
        return tuple(options.as_dict().items())

    def processed_track(self, track: TrackRecord, force: bool = False) -> ProcessedTrack:
        signature = self.processing_parameter_signature()
        fingerprint = track.processed_fingerprint
        if fingerprint is None:
            fingerprint = processing_input_fingerprint(track.element)
        if (
            not force
            and track.processed is not None
            and track.processed_signature == signature
            and track.processed_fingerprint == fingerprint
        ):
            return track.processed
        track.processed = process_track_element(
            track.element,
            self.processing_options(),
            fingerprint=fingerprint,
        )
        track.processed_signature = signature
        track.processed_fingerprint = fingerprint
        track.display_geometry = display_geometry_for_processed_track(track.processed)
        track.display_geometry_cache.clear()
        return track.processed

    def display_processed_track(self, track: TrackRecord) -> ProcessedTrack:
        """Reuse validated processed geometry during repaint-heavy display paths."""
        if (
            track.processed is not None
            and track.processed_signature == self.processing_parameter_signature()
        ):
            return track.processed
        return self.processed_track(track)

    def display_track_geometry(
        self, track: TrackRecord, point_limit: int = 320
    ) -> ProcessedTrack | CachedDisplayTrack:
        """Return compact cached geometry for overview-only drawing."""
        payload = track.display_geometry or {}
        levels = payload.get("levels") if isinstance(payload, dict) else None
        if not isinstance(levels, dict) or not levels:
            processed = self.display_processed_track(track)
            track.display_geometry = display_geometry_for_processed_track(processed)
            track.display_geometry_cache.clear()
            payload = track.display_geometry
            levels = payload.get("levels", {})
        available = sorted(
            int(key) for key in levels if str(key).isdigit() and int(key) > 0
        )
        if not available:
            return self.display_processed_track(track)
        chosen = next((level for level in available if level >= point_limit), available[-1])
        cached = track.display_geometry_cache.get(chosen)
        if cached is not None:
            return cached
        segments = []
        flat_points = []
        for segment_index, coordinates in enumerate(levels.get(str(chosen), [])):
            points = tuple(
                CachedDisplayPoint(
                    float(coordinate[0]),
                    float(coordinate[1]),
                    int(coordinate[2]) if len(coordinate) >= 3 else -1,
                )
                for coordinate in coordinates
                if isinstance(coordinate, (list, tuple)) and len(coordinate) >= 2
            )
            if not points:
                continue
            segments.append(CachedDisplaySegment(points))
            flat_points.extend(points)
        cached = CachedDisplayTrack(tuple(segments), tuple(flat_points))
        track.display_geometry_cache[chosen] = cached
        return cached

    def processed_point_infos(self, track: TrackRecord) -> list[PointInfo]:
        raw_by_index = {point.source_index: point for point in extract_raw_track_points(track.element)}
        infos = []
        for point in self.processed_track(track).points:
            raw = raw_by_index.get(point.source_index)
            if raw is None or raw.element is None:
                continue
            infos.append(
                PointInfo(
                    raw.element,
                    point.lat,
                    point.lon,
                    point.elevation_m,
                    point.time,
                    point.segment_index,
                )
            )
        return infos

    def processed_segment_point_infos(self, track: TrackRecord) -> list[list[PointInfo]]:
        raw_by_index = {point.source_index: point for point in extract_raw_track_points(track.element)}
        segments = []
        for segment in self.processed_track(track).segments:
            infos = []
            for point in segment.points:
                raw = raw_by_index.get(point.source_index)
                if raw is None or raw.element is None:
                    continue
                infos.append(
                    PointInfo(
                        raw.element,
                        point.lat,
                        point.lon,
                        point.elevation_m,
                        point.time,
                        point.segment_index,
                    )
                )
            if infos:
                segments.append(infos)
        return segments

    def processing_summary_text(self) -> str:
        options = self.processing_options()
        return (
            f"XY smooth {options.horizontal_smoothing_distance_m:g} m | "
            f"spacing {options.minimum_point_spacing_m:g} m | "
            f"elevation {options.elevation_smoothing_distance_m:g} m | "
            f"H/V error {options.maximum_horizontal_accuracy_m:g}/{options.maximum_vertical_accuracy_m:g} m | "
            f"HDOP/VDOP {options.maximum_hdop:g}/{options.maximum_vdop:g}"
            f" | speed {options.running_speed_window_distance_m:g} m / {options.stationary_speed_threshold_kmh:g} km/h"
        )

    def refresh_processing_summary(self):
        if hasattr(self, "processing_summary_label"):
            self.processing_summary_label.setStringValue_(self.processing_summary_text())

    def schedule_processing_recalculation(self):
        self.processing_generation += 1
        generation = self.processing_generation
        snapshots = [ET.tostring(track.element, encoding="unicode") for track in self.tracks]
        self.processing_track_ids = [id(track) for track in self.tracks]
        self.processing_track_fingerprints = [
            processing_input_fingerprint(track.element) for track in self.tracks
        ]
        options = self.processing_options()
        previous = self.processing_future
        if previous is not None and not previous.done():
            previous.cancel()
        self.processing_future = self.processing_executor.submit(
            process_track_snapshots,
            snapshots,
            options,
        )
        if self.processing_poll_timer is not None:
            self.processing_poll_timer.invalidate()
        self.processing_poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.05,
            self,
            "processingRecalculationPoll:",
            generation,
            True,
        )
        self.set_status(f"Recalculating {len(snapshots)} track(s) with updated GPX processing settings...")

    @objc.IBAction
    def processingRecalculationPoll_(self, timer):
        future = self.processing_future
        if future is None or not future.done():
            return
        timer.invalidate()
        if timer is self.processing_poll_timer:
            self.processing_poll_timer = None
        try:
            processed_tracks = future.result()
        except Exception as exc:
            self.set_status(f"GPX processing failed: {exc}")
            show_alert("Could not recalculate GPX tracks.", str(exc))
            return
        generation = int(timer.userInfo())
        if generation != self.processing_generation:
            return
        signature = self.processing_parameter_signature()
        by_id = {id(track): track for track in self.tracks}
        for track_id, snapshot_fingerprint, processed in zip(
            self.processing_track_ids,
            self.processing_track_fingerprints,
            processed_tracks,
        ):
            track = by_id.get(track_id)
            if (
                track is None
                or processing_input_fingerprint(track.element) != snapshot_fingerprint
            ):
                continue
            track.processed = processed
            track.processed_signature = signature
            track.processed_fingerprint = snapshot_fingerprint
            track.metrics_dirty = True
        self.recalculate()
        if not self.dirty:
            self.write_metrics_caches_for_records(self.tracks)
        for window in list(self.auxiliary_windows):
            delegate = window.delegate() if window is not None else None
            if isinstance(delegate, TrackInspectorController):
                delegate.reload_rows()
        self.refresh_open_plot_views()
        self.set_status(f"Recalculated {len(processed_tracks)} track(s) with updated GPX processing settings.")

    def _apply_editor_settings(self, values, changed):
        changed_editor_keys = set(changed) & set(EDITOR_PARAMETER_KEYS)
        if not changed_editor_keys:
            self.set_status("GPX Editor settings unchanged.")
            return True
        previous_parameters = dict(self.project_parameters)
        updated = dict(self.project_parameters)
        for key in EDITOR_PARAMETER_KEYS:
            if key in values:
                updated[key] = values[key]
        credential_id = str(updated.get("maps.credential_id", "default"))
        for key in ("maps.interactive_provider", "maps.output_provider", "pdf.map_provider"):
            provider = str(updated.get(key, "osm"))
            if provider_requires_credential(provider):
                if not ({key, "maps.credential_id"} & changed_editor_keys) and read_provider_credential(provider, credential_id):
                    continue
                if not request_provider_api_key(provider, credential_id, remember_output_provider=key == "maps.output_provider"):
                    return False
        output_provider = str(updated.get("maps.output_provider", "osm"))
        provider_preference = load_map_provider_preference()
        try:
            save_map_provider_preference(
                output_provider,
                credential_id,
                credential_verified=(
                    output_provider == "osm"
                    or (
                        provider_preference.get("preferred_output_provider") == output_provider
                        and provider_preference.get("credential_id", "default") == credential_id
                        and provider_preference.get("credential_verified", False)
                    )
                ),
            )
        except OSError as exc:
            show_alert("Could not remember the map-provider preference.", str(exc))
        self.apply_project_parameters(updated)
        if self.standalone:
            try:
                atomic_write_json(
                    STANDALONE_SETTINGS_PATH,
                    parameter_subset_payload(self.project_parameters, EDITOR_PARAMETER_KEYS),
                )
            except OSError as exc:
                self.apply_project_parameters(previous_parameters)
                show_alert("Could not save GPX Editor settings.", str(exc))
                self.set_status("GPX Editor settings could not be saved.")
                return False
            self.set_status(f"Saved {len(changed_editor_keys)} GPX Editor setting(s).")
            return True
        if self.on_settings_change_callback is not None:
            try:
                self.on_settings_change_callback(
                    parameter_subset(self.project_parameters, EDITOR_PARAMETER_KEYS)
                )
            except Exception as exc:
                self.apply_project_parameters(previous_parameters)
                show_alert("Could not update Adventure settings.", str(exc))
                self.set_status("Adventure settings could not be updated.")
                return False
        self.set_status(f"Applied {len(changed_editor_keys)} Adventure setting(s).")
        return True

    @objc.IBAction
    def showSettings_(self, _sender):
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None:
            workspace.open_shared_settings("GPX Processing")
            return
        if self.settings_controller is None:
            self.settings_controller = CocoaParameterEditor.alloc().init()
            self.settings_controller.configure(
                title="GPX Editor Settings",
                sections=EDITOR_PARAMETER_SECTIONS,
                values=self.project_parameters,
                apply_callback=self._apply_editor_settings,
                manage_map_provider_callback=self._manage_map_provider_from_settings,
            )
        elif not self.settings_controller.window.isVisible():
            self.settings_controller.update_values(self.project_parameters)
        else:
            self.settings_controller._capture_controls()
        self.settings_controller.show()

    def _manage_map_provider_from_settings(self, draft):
        credential_id = str(draft.get("maps.credential_id", "default"))
        key = draft.get("_provider_setting", "maps.output_provider")
        preferred = draft.get(key, "osm")
        if preferred == "output":
            preferred = draft.get("maps.output_provider", "osm")
        result = run_map_provider_setup(
            preferred_provider=str(preferred),
            credential_id=credential_id,
            timeout_seconds=float(draft.get("maps.request_timeout_seconds", 12.0)),
            remember_output_provider=key == "maps.output_provider",
        )
        if str(result.get("action", "cancel")) == "cancel":
            return None
        provider = str(result.get("provider", preferred))
        if result.get("action") == "settings" and provider == "custom":
            self.settings_controller.show_advanced = True
        return {key: provider}

    def _build_window(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(90, 90, 1280, 820), style, NSBackingStoreBuffered, False
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_(PROGRAM_TITLE)
        self.window.setMinSize_((MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT))
        self.root = NSView.alloc().initWithFrame_(self.window.contentView().bounds())
        self.root.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.window.contentView().addSubview_(self.root)

        self.title_label = self.make_label(PROGRAM_TITLE, 25, True, 2)
        self.root.addSubview_(self.title_label)
        self.header_logo_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 44, 44))
        self.header_logo_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self.header_logo_view.setImageAlignment_(NSImageAlignCenter)
        logo_path = bundled_resource_path("MyCaminoLogo-ohneText.png")
        if logo_path.exists():
            self.header_logo_view.setImage_(NSImage.alloc().initWithContentsOfFile_(str(logo_path)))
        self.root.addSubview_(self.header_logo_view)
        self.settings_button = make_liquid_glass_button(NSMakeRect(0, 0, 34, 34))
        self.configure_symbol_button(
            self.settings_button,
            "gearshape",
            "GPX Editor Settings",
            "NSActionTemplate",
            "Settings",
        )
        self.settings_button.setTarget_(self)
        self.settings_button.setAction_("showSettings:")
        self.settings_button.setToolTip_("Edit GPX processing, PDF export, and map-service settings.")
        apply_liquid_glass_button_style(self.settings_button, compact=True)
        self.root.addSubview_(self.settings_button)
        self.output_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, FIELD_HEIGHT))
        self.output_field.setFont_(NSFont.systemFontOfSize_(12))
        self.output_field.setPlaceholderString_("Choose output .gpx file")
        self.output_field.setToolTip_("Output GPX filename. Press Enter to save all tracks to this file.")
        self.output_field.setTarget_(self)
        self.output_field.setAction_("outputFileCommitted:")
        self.root.addSubview_(self.output_field)
        self.output_browse_button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, BUTTON_HEIGHT, BUTTON_HEIGHT))
        self.output_browse_button.setTitle_("...")
        browse_image = NSImage.imageNamed_(NSImageNameFolder)
        if browse_image is not None:
            self.output_browse_button.setImage_(browse_image)
            self.output_browse_button.setImagePosition_(NSImageOnly)
            self.output_browse_button.setTitle_("")
        self.output_browse_button.setBordered_(False)
        self.output_browse_button.setTarget_(self)
        self.output_browse_button.setAction_("chooseOutputFile:")
        self.output_browse_button.setToolTip_("Choose an output GPX filename and save to it.")
        self.root.addSubview_(self.output_browse_button)
        self.project_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, FIELD_HEIGHT))
        self.project_field.setFont_(NSFont.systemFontOfSize_(14))
        self.project_field.setPlaceholderString_("Project name")
        self.project_field.setToolTip_("Project name stored in GPX metadata. About 30 characters are visible; longer names scroll horizontally.")
        self.project_field.setTarget_(self)
        self.project_field.setAction_("projectNameCommitted:")
        self.root.addSubview_(self.project_field)
        self.project_label = self.make_label("Project Name", 12, False, 0)
        self.root.addSubview_(self.project_label)
        self.processing_summary_label = self.make_label(self.processing_summary_text(), 11, False, 0)
        self.processing_summary_label.setTextColor_(NSColor.secondaryLabelColor())
        self.processing_summary_label.setToolTip_(
            "Active shared GPX processing: horizontal smoothing and spacing, elevation smoothing, "
            "horizontal/vertical error limits, and HDOP/VDOP limits. Set a value to zero to disable it."
        )
        self.root.addSubview_(self.processing_summary_label)

        self.table_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.table_scroll.setHasVerticalScroller_(True)
        self.table_scroll.setHasHorizontalScroller_(True)
        self.table_scroll.setBorderType_(1)
        self.track_table = EditorTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.track_table.controller = self
        self.track_table.setUsesAlternatingRowBackgroundColors_(True)
        self.track_table.setAllowsMultipleSelection_(True)
        self.track_table.setAllowsEmptySelection_(True)
        self.track_table.setRowHeight_(ROW_HEIGHT)
        self.track_table.setToolTip_(
            "Right-click selected tracks for plotting, sorting, duplicating, moving, "
            "visibility, joining, and deletion actions."
        )
        self.track_table.registerForDraggedTypes_([DRAG_TYPE, FILE_DRAG_TYPE])
        table_font = self.table_font(12)
        header_font = self.table_font(11)
        numeric_columns = {"row", "nr", "stage", "length", "duration", "sum", "distance", "speed", "ascent", "descent", "npoints"}
        for identifier, title, width, editable in self.columns:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
            if identifier != "show":
                column.setDataCell_(make_table_text_cell())
            column.headerCell().setStringValue_(title)
            if hasattr(column.headerCell(), "setWraps_"):
                column.headerCell().setWraps_(True)
            column.headerCell().setFont_(header_font)
            column.headerCell().setAlignment_(0)
            column.dataCell().setFont_(table_font)
            column.dataCell().setAlignment_(1 if identifier in numeric_columns else 0)
            if identifier == "show":
                checkbox_cell = NSButtonCell.alloc().init()
                checkbox_cell.setButtonType_(NSButtonTypeSwitch)
                checkbox_cell.setTitle_("")
                column.setDataCell_(checkbox_cell)
            column.setWidth_(width)
            column.setMinWidth_(min(width, 55))
            column.setEditable_(editable)
            column.setSortDescriptorPrototype_(NSSortDescriptor.sortDescriptorWithKey_ascending_(identifier, True))
            self.track_table.addTableColumn_(column)
        self.data_source = EditorTableDataSource.alloc().initWithController_(self)
        self.track_table.setDataSource_(self.data_source)
        self.track_table.setDelegate_(self.data_source)
        self.track_table.setTarget_(self)
        self.track_table.setDoubleAction_("trackDoubleClicked:")
        self.configure_track_table_header()
        self.table_scroll.setDocumentView_(self.track_table)
        self.root.addSubview_(self.table_scroll)

        self.selection_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 240, FIELD_HEIGHT))
        self.selection_field.setPlaceholderString_("Track numbers, e.g. 1,3-5")
        self.selection_field.setToolTip_("Comma-separated track numbers and ranges. Selection order is preserved where possible.")
        self.selection_field.setTarget_(self)
        self.selection_field.setAction_("selectionFieldCommitted:")
        self.root.addSubview_(self.selection_field)

        button_specs = [
            ("New Track", "newTrack:", "Create an empty editable track."),
            ("Add Tracks", "addTracks:", "Load one or more .gpx files and append their tracks."),
            (
                "Add Media Points",
                "addTracksFromPhotos:",
                "Review photos/videos and add their GPS positions as linked waypoints to the selected track.",
            ),
            (
                "Renumber…",
                "renumberTracks:",
                "Preview and assign track or stage numbers to selected or all tracks.",
            ),
            ("Save", "save:", "Save all tracks to the output GPX filename."),
            ("Save & Exit", "saveAndExit:", "Save the GPX file and exit the editor."),
            ("PNG", "savePng:", "Save the current selected-track plot as a PNG image."),
            ("PDF Summary", "exportPdf:", "Create a printable track summary, with optional maps and elevation profiles."),
            ("Sort", "sortByDate:", "Sort by Date & Time using the special distance placement for untimed or zero-duration tracks."),
            ("Select All", "selectAllTracks:", "Select all tracks."),
            ("Invert Selection", "invertTrackSelection:", "Select every unselected track, including hidden tracks."),
            ("Set Track Type…", "setTrackType:", "Assign a track type to selected or all tracks."),
            ("Select Tracks by Type…", "selectTracksByType:", "Select tracks matching a type."),
            ("Unselect All", "unselectAllTracks:", "Clear the track selection."),
            ("Set Anchorpoint", "setAnchorpoint:", "Set the anchor to the first point of the first track in the current table."),
            ("Join Tracks", "joinTracks:", "Join selected tracks into the first selected track."),
            ("Inspect Track", "inspectTrack:", "Inspect and edit all waypoints of the selected track."),
            ("Plot Overview", "plotAll:", "Open an overview window for all tracks."),
            ("Plot Track(s)", "plotSelected:", "Open a track window for the selected track(s)."),
            ("View File", "viewFile:", "Open the source GPX file of the selected track in TextEdit."),
            ("Undo", "undo:", "Undo up to 10 editing actions."),
            ("Redo", "redo:", "Redo an undone action."),
            ("Help", "help:", "Show a summary of GPX Editor controls."),
            ("Quit", "quit:", "Quit the editor. You will be asked whether to save unsaved changes."),
        ]
        self.buttons = {}
        for title, action, tip in button_specs:
            button = make_liquid_glass_button(NSMakeRect(0, 0, 98, BUTTON_HEIGHT))
            button.setTitle_(title)
            button.setTarget_(self)
            button.setAction_(action)
            button.setToolTip_(tip)
            if title == "Undo":
                self.configure_symbol_button(button, "arrow.uturn.backward", "Undo", "NSGoBackTemplate", "↶")
            elif title == "Redo":
                self.configure_symbol_button(button, "arrow.uturn.forward", "Redo", "NSGoForwardTemplate", "↷")
            apply_liquid_glass_button_style(button, compact=title in {"Undo", "Redo"})
            self.buttons[title] = button
            self.root.addSubview_(button)

        self.status_label = self.make_label("Ready. Add Tracks to begin.", 12, False, 0)
        self.status_label.setBezeled_(True)
        self.status_label.setDrawsBackground_(True)
        self.status_label.setBackgroundColor_(NSColor.windowBackgroundColor())
        self.root.addSubview_(self.status_label)
        self.load_progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 220, STATUS_HEIGHT))
        self.load_progress.setStyle_(NSProgressIndicatorStyleBar)
        self.load_progress.setIndeterminate_(False)
        self.load_progress.setMinValue_(0.0)
        self.load_progress.setMaxValue_(1.0)
        self.load_progress.setDoubleValue_(0.0)
        self.load_progress.setHidden_(True)
        self.root.addSubview_(self.load_progress)

        self.window_delegate = GPXEditorWindowDelegate.alloc().initWithController_(self)
        self.window.setDelegate_(self.window_delegate)
        self.layout_window()
        self.configure_key_loop()

    def configure_track_table_header(self):
        header = self.track_table.headerView()
        if header is None:
            return
        header.setFrameSize_(NSMakeSize(max(self.track_table.bounds().size.width, 1.0), 42.0))
        header.setNeedsDisplay_(True)

    def _track_context_item(self, menu, title, action, enabled=True):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, ""
        )
        item.setTarget_(self)
        item.setEnabled_(bool(enabled))
        menu.addItem_(item)
        return item

    def build_track_context_menu(self):
        """Build a menu whose validation reflects the current track selection."""
        selected = self.selected_tracks()
        selected_indexes = self.selected_track_row_indexes()
        count = len(selected)
        editing = self.track_mutations_enabled
        selected_set = set(selected_indexes)
        can_move_up = any(
            index > 0 and index - 1 not in selected_set for index in selected_indexes
        )
        can_move_down = any(
            index + 1 < len(self.tracks) and index + 1 not in selected_set
            for index in selected_indexes
        )

        menu = NSMenu.alloc().initWithTitle_("Track")
        menu.setAutoenablesItems_(False)
        self._track_context_item(menu, "New Track", "newTrack:", editing)
        self._track_context_item(menu, "Add Points From Clipboard", "addPointsFromClipboard:", editing and count == 1)
        self._track_context_item(menu, "Add Media Points…", "addTracksFromPhotos:", editing and count == 1)
        self._track_context_item(
            menu,
            "Estimate Track Times…", "estimateTrackTimes:", editing and bool(self.tracks),
        )
        self._track_context_item(menu, "Find Missing Timestamps…", "findMissingTimestamps:", bool(self.tracks))
        self._track_context_item(menu, "Rebuild Route Times from Media…", "rebuildRouteTimesFromMedia:", editing and count == 1)
        menu.addItem_(NSMenuItem.separatorItem())
        self._track_context_item(menu, "Set Track Type…", "setTrackType:", editing and bool(self.tracks))
        self._track_context_item(menu, "Select Tracks by Type…", "selectTracksByType:", bool(self.tracks))
        self._track_context_item(menu, "Invert Selection", "invertTrackSelection:", bool(self.tracks))
        self._track_context_item(menu, "Inspect Track", "contextInspectTrack:", editing and count == 1)
        self._track_context_item(menu, "Plot Track(s)", "contextPlotTracks:", count > 0)
        self._track_context_item(menu, "Elevation Profile", "contextElevationProfile:", editing and count > 0)
        self._track_context_item(
            menu,
            "Plot Selected in Overview",
            "contextPlotOverview:",
            count > 0,
        )
        self._track_context_item(
            menu, "View Source File", "contextViewSource:", count == 1
        )
        menu.addItem_(NSMenuItem.separatorItem())
        self._track_context_item(
            menu,
            "Sort Selected Tracks…",
            "sortSelectedTracks:",
            editing and count >= 2,
        )
        self._track_context_item(
            menu,
            "Renumber…",
            "renumberTracks:",
            editing and bool(self.tracks),
        )
        self._track_context_item(
            menu,
            "Save Selected Tracks As…",
            "saveSelectedTracksAs:",
            editing and count > 0,
        )
        self._track_context_item(
            menu, "Duplicate Selected Tracks", "duplicateSelectedTracks:", editing and count > 0
        )
        menu.addItem_(NSMenuItem.separatorItem())
        self._track_context_item(menu, "Move Up", "moveSelectedTracksUp:", editing and can_move_up)
        self._track_context_item(
            menu, "Move Down", "moveSelectedTracksDown:", editing and can_move_down
        )
        self._track_context_item(
            menu, "Move to Top", "moveSelectedTracksToTop:", editing and can_move_up
        )
        self._track_context_item(
            menu, "Move to Bottom", "moveSelectedTracksToBottom:", editing and can_move_down
        )
        menu.addItem_(NSMenuItem.separatorItem())
        self._track_context_item(
            menu,
            "Show Selected",
            "showSelectedTracks:",
            editing and any(track.hidden for track in selected),
        )
        self._track_context_item(
            menu,
            "Hide Selected",
            "hideSelectedTracks:",
            editing and any(not track.hidden for track in selected),
        )
        menu.addItem_(NSMenuItem.separatorItem())
        self._track_context_item(
            menu, "Set Anchorpoint", "contextSetAnchorpoint:", editing and count > 0
        )
        self._track_context_item(
            menu,
            "Join Selected Tracks",
            "contextJoinTracks:",
            editing and count >= 2,
        )
        menu.addItem_(NSMenuItem.separatorItem())
        self._track_context_item(
            menu,
            "Delete Selected Tracks",
            "contextDeleteTracks:",
            editing and count > 0,
        )
        self.track_context_menu = menu
        return menu

    def table_font(self, size: float):
        preferred = NSFont.fontWithName_size_("DejaVu Sans Mono", size)
        if preferred is not None:
            return preferred
        preferred = NSFont.fontWithName_size_("Menlo", size)
        if preferred is not None:
            return preferred
        return self.table_monospace_font(size)

    def table_monospace_font(self, size: float):
        if hasattr(NSFont, "monospacedSystemFontOfSize_weight_"):
            return NSFont.monospacedSystemFontOfSize_weight_(size, 0.0)
        return NSFont.userFixedPitchFontOfSize_(size) or NSFont.systemFontOfSize_(size)

    def make_label(self, text, size=13, bold=False, alignment=0):
        field = NSTextField.labelWithString_(text)
        field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        field.setAlignment_(alignment)
        return field

    def configure_symbol_button(self, button, symbol_name: str, accessibility: str, template_name: str, fallback_title: str):
        image = None
        symbol_factory = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        if symbol_factory is not None:
            image = symbol_factory(symbol_name, accessibility)
        if image is None:
            image = NSImage.imageNamed_(template_name)
        if image is not None:
            button.setImage_(image)
            button.setImagePosition_(NSImageOnly)
            button.setTitle_("")
        else:
            button.setTitle_(fallback_title)

    def layout_window(self):
        bounds = self.root.bounds()
        width = bounds.size.width
        height = bounds.size.height
        gap = 8
        top = height - PADDING
        self.buttons["Help"].setFrame_(NSMakeRect(PADDING, top - 30, 64, BUTTON_HEIGHT))
        logo_size = 44.0
        logo_x = width - PADDING - logo_size
        settings_size = 34.0
        settings_x = logo_x - gap - settings_size
        title_x = PADDING + 64 + gap
        self.title_label.setFrame_(NSMakeRect(title_x, top - 34, max(220.0, settings_x - title_x - gap), 32))
        self.settings_button.setFrame_(NSMakeRect(settings_x, top - settings_size + 1.0, settings_size, settings_size))
        self.header_logo_view.setFrame_(NSMakeRect(logo_x, top - logo_size + 4, logo_size, logo_size))
        top -= 52
        project_y = top - FIELD_HEIGHT
        self.buttons["New Track"].setFrame_(NSMakeRect(PADDING, project_y, 96, BUTTON_HEIGHT))
        self.buttons["Add Tracks"].setFrame_(NSMakeRect(PADDING + 96 + gap, project_y, 104, BUTTON_HEIGHT))
        self.buttons["Add Media Points"].setFrame_(
            NSMakeRect(PADDING + 96 + gap + 104 + gap, project_y, 140, BUTTON_HEIGHT)
        )
        project_x = PADDING + 96 + gap + 104 + gap + 140 + gap
        self.project_label.setFrame_(NSMakeRect(project_x, project_y + FIELD_HEIGHT + 2, 220, 18))
        self.project_field.setFrame_(NSMakeRect(project_x, project_y, 190, FIELD_HEIGHT))
        self.buttons["Renumber…"].setFrame_(
            NSMakeRect(project_x + 198, project_y, 100, BUTTON_HEIGHT)
        )
        self.buttons["Set Track Type…"].setFrame_(NSMakeRect(project_x + 306, project_y, 142, BUTTON_HEIGHT))
        self.buttons["Select Tracks by Type…"].setFrame_(NSMakeRect(project_x + 456, project_y, 172, BUTTON_HEIGHT))
        undo_size = BUTTON_HEIGHT
        redo_x = width - PADDING - undo_size
        undo_x = redo_x - gap - undo_size
        self.buttons["Undo"].setFrame_(NSMakeRect(undo_x, project_y, undo_size, BUTTON_HEIGHT))
        self.buttons["Redo"].setFrame_(NSMakeRect(redo_x, project_y, undo_size, BUTTON_HEIGHT))
        top -= FIELD_HEIGHT + 10
        self.processing_summary_label.setFrame_(NSMakeRect(PADDING, top - 17, width - 2 * PADDING, 17))
        top -= 21
        bottom_controls = PADDING + STATUS_HEIGHT + 10 + BUTTON_HEIGHT * 2 + 8
        table_height = max(ROW_HEIGHT * 11 + 42, top - bottom_controls)
        self.table_scroll.setFrame_(NSMakeRect(PADDING, bottom_controls, width - 2 * PADDING, table_height))
        self.configure_track_table_header()
        y = PADDING + STATUS_HEIGHT + 10 + BUTTON_HEIGHT + 8
        self.selection_field.setFrame_(NSMakeRect(PADDING, y, 265, FIELD_HEIGHT))
        x = PADDING + 265 + gap
        selection_widths = {
            "Select All": 88,
            "Unselect All": 108,
            "Invert Selection": 126,
            "Join Tracks": 104,
            "Inspect Track": 116,
        }
        for title in ["Select All", "Unselect All", "Invert Selection", "Join Tracks", "Inspect Track"]:
            button = self.buttons[title]
            width_for_button = selection_widths[title]
            button.setFrame_(NSMakeRect(x, y, width_for_button, BUTTON_HEIGHT))
            x += width_for_button + gap
        anchor_width = 128
        self.buttons["Set Anchorpoint"].setFrame_(NSMakeRect(width - PADDING - anchor_width, y, anchor_width, BUTTON_HEIGHT))
        self.buttons["Sort"].setFrame_(NSMakeRect(-1000, -1000, 1, 1))
        y = PADDING + STATUS_HEIGHT + 10
        x = PADDING
        self.buttons["Save"].setFrame_(NSMakeRect(x, y, 72, BUTTON_HEIGHT))
        x += 72 + gap
        exit_width = 112
        quit_width = 72
        right_start = width - PADDING - quit_width - gap - exit_width
        file_button_widths = [("PDF Summary", 112), ("PNG", 62), ("Plot Overview", 112), ("Plot Track(s)", 112), ("View File", 88)]
        fixed_after_output = gap + BUTTON_HEIGHT + sum(gap + button_width for _title, button_width in file_button_widths)
        output_width = min(420, max(180, right_start - gap - x - fixed_after_output))
        self.output_field.setFrame_(NSMakeRect(x, y, output_width, FIELD_HEIGHT))
        x += output_width + gap
        self.output_browse_button.setFrame_(NSMakeRect(x, y, BUTTON_HEIGHT, BUTTON_HEIGHT))
        x += BUTTON_HEIGHT + gap
        for title, width_for_button in file_button_widths:
            button = self.buttons[title]
            button.setFrame_(NSMakeRect(x, y, width_for_button, BUTTON_HEIGHT))
            x += width_for_button + gap
        quit_x = width - PADDING - quit_width
        save_exit_x = quit_x - gap - exit_width
        self.buttons["Save & Exit"].setFrame_(NSMakeRect(save_exit_x, y, exit_width, BUTTON_HEIGHT))
        self.buttons["Quit"].setFrame_(NSMakeRect(quit_x, y, quit_width, BUTTON_HEIGHT))
        if self.load_progress.isHidden():
            self.status_label.setFrame_(NSMakeRect(PADDING, PADDING, width - 2 * PADDING, STATUS_HEIGHT))
        else:
            progress_width = min(240.0, max(160.0, width * 0.22))
            self.load_progress.setFrame_(
                NSMakeRect(width - PADDING - progress_width, PADDING + 2, progress_width, STATUS_HEIGHT - 4)
            )
            self.status_label.setFrame_(
                NSMakeRect(PADDING, PADDING, width - 2 * PADDING - progress_width - gap, STATUS_HEIGHT)
            )

    def configure_key_loop(self):
        order = [
            self.buttons["Help"],
            self.settings_button,
            self.buttons["New Track"],
            self.buttons["Add Tracks"],
            self.buttons["Add Media Points"],
            self.project_field,
            self.buttons["Undo"],
            self.buttons["Redo"],
            self.track_table,
            self.selection_field,
            self.buttons["Select All"],
            self.buttons["Unselect All"],
            self.buttons["Invert Selection"],
            self.buttons["Join Tracks"],
            self.buttons["Inspect Track"],
            self.buttons["Set Anchorpoint"],
            self.buttons["Save"],
            self.output_field,
            self.output_browse_button,
            self.buttons["PDF Summary"],
            self.buttons["PNG"],
            self.buttons["Plot Overview"],
            self.buttons["Plot Track(s)"],
            self.buttons["View File"],
            self.buttons["Save & Exit"],
            self.buttons["Quit"],
        ]
        for current, next_view in zip(order, order[1:] + order[:1]):
            current.setNextKeyView_(next_view)
        self.window.setInitialFirstResponder_(self.project_field)

    def show(self):
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.project_field)
        if self.parameter_load_warnings:
            show_alert(
                "Some GPX Editor settings could not be loaded.",
                "\n".join(self.parameter_load_warnings),
            )
            self.parameter_load_warnings = []

    def set_status(self, message: str):
        self.status_label.setStringValue_(message)
        self.status_label.displayIfNeeded()
        self.window.displayIfNeeded()
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))

    def set_track_loading_controls_enabled(self, enabled: bool):
        """Keep browsing available while staged GPX processing runs."""
        self.track_mutations_enabled = bool(enabled)
        for title, button in self.buttons.items():
            button.setEnabled_(enabled or title in {"Help", "Quit"})
        self.settings_button.setEnabled_(enabled)
        self.project_field.setEnabled_(enabled)
        self.output_field.setEnabled_(enabled)
        self.output_browse_button.setEnabled_(enabled)
        self.selection_field.setEnabled_(enabled)
        self.track_table.setEnabled_(True)
        if not enabled:
            self.status_label.setStringValue_(
                "Loading track data — editing will be available when processing finishes."
            )

    def show_track_load_progress(self, current: int, total: int):
        total = max(1, int(total))
        self.load_progress.stopAnimation_(None)
        self.load_progress.setIndeterminate_(False)
        self.load_progress.setMinValue_(0.0)
        self.load_progress.setMaxValue_(float(total))
        self.load_progress.setDoubleValue_(float(max(0, min(current, total))))
        self.load_progress.setHidden_(False)
        self.layout_window()
        self.load_progress.displayIfNeeded()

    def show_indeterminate_track_load_progress(self):
        self.load_progress.setIndeterminate_(True)
        self.load_progress.setHidden_(False)
        self.load_progress.startAnimation_(None)
        self.layout_window()
        self.load_progress.displayIfNeeded()

    def hide_track_load_progress(self):
        self.load_progress.stopAnimation_(None)
        self.load_progress.setHidden_(True)
        self.layout_window()

    def cancel_staged_track_loading(self):
        if self.track_load_timer is not None:
            self.track_load_timer.invalidate()
        self.track_load_timer = None
        if self.pending_track_load_future is not None:
            self.pending_track_load_future.cancel()
            self.pending_track_load_future = None
        if self.pending_track_parse_future is not None:
            self.pending_track_parse_future.cancel()
            self.pending_track_parse_future = None
        self.pending_track_parse_request = None
        self.pending_track_loads = []
        self.pending_track_load_index = 0
        self.pending_track_load_total = 0
        self.pending_pdf_export_after_load = False
        if hasattr(self, "load_progress"):
            self.hide_track_load_progress()
        self.notify_initial_load_complete()

    def notify_initial_load_complete(self):
        """Invoke the embedding startup callback once, including failure/cancel paths."""
        if self.initial_load_completion_notified:
            return
        self.initial_load_completion_notified = True
        callback = self.on_initial_load_complete_callback
        self.on_initial_load_complete_callback = None
        if callback is not None:
            callback()

    def set_output_path(self, path: Path | None):
        self.last_save_path = path
        if path is None:
            self.output_field.setStringValue_("")
        else:
            self.output_field.setStringValue_(str(path))

    def current_output_path(self) -> Path | None:
        """Return the most recently saved GPX file, with a field fallback."""
        if self.last_save_path is not None:
            return self.last_save_path
        value = str(self.output_field.stringValue()).strip()
        if value:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = self.default_save_directory() / path
            if path.suffix == "":
                path = path.with_suffix(".gpx")
            if path.suffix.lower() == ".gpx":
                return path.resolve()
        return None

    def notify_close_callback(self):
        """Report the last active output file to an embedding caller."""
        if self.on_close_callback is None:
            return
        callback = self.on_close_callback
        self.on_close_callback = None
        callback(self.current_output_path())

    def finalize_editor_close(self, delete_recovery=True):
        """Close all editor-owned secondary windows exactly once."""
        if self.editor_close_finalized:
            return
        self.editor_close_finalized = True
        for attribute in ("timing_issues_tool", "timing_estimate_tool"):
            tool = getattr(self, attribute, None)
            if tool is not None:
                tool.dismiss_(None)
        WINDOW_REGISTRY.retire_owner(self)
        self.cancelPdfSummary_(None)
        self.cancel_staged_track_loading()
        if self.processing_poll_timer is not None:
            self.processing_poll_timer.invalidate()
            self.processing_poll_timer = None
        if self.processing_future is not None and not self.processing_future.done():
            self.processing_future.cancel()
        self.processing_executor.shutdown(wait=False, cancel_futures=True)
        self.track_load_executor.shutdown(wait=False, cancel_futures=True)
        self.metrics_cache_executor.shutdown(wait=False, cancel_futures=False)
        if self.settings_controller is not None:
            self.settings_controller.close()
            self.settings_controller = None
        self.close_auxiliary_windows()
        self.notify_close_callback()
        if delete_recovery:
            self.delete_recovery_file()

    def close_main_editor_window(self, delete_recovery=True, force=False):
        if not force and self.hide_main_editor_for_workspace():
            return
        try:
            self.window.orderOut_(None)
        except Exception:
            pass
        self.finalize_editor_close(delete_recovery=delete_recovery)
        try:
            self.window.setDelegate_(None)
        except Exception:
            pass
        try:
            self.window.close()
        except Exception:
            pass
        if self.standalone:
            NSApp().terminate_(None)

    def hide_main_editor_for_workspace(self):
        """Hide the shared Track Table without closing its map-first document."""
        workspace = getattr(self, "adventure_workspace_delegate", None)
        map_window = getattr(workspace, "window", None) if workspace is not None else None
        if map_window is None:
            return False
        try:
            if not map_window.isVisible():
                map_window.makeKeyAndOrderFront_(None)
            self.window.orderOut_(None)
        except Exception:
            return False
        return True

    def notify_save_callback(self, saved_path: Path | None):
        """Report a successful save to an embedding caller."""
        if self.on_save_callback is None or saved_path is None:
            return
        self.on_save_callback(saved_path)

    def default_save_directory(self) -> Path:
        if self.last_save_path is not None:
            return self.last_save_path.parent
        if self.last_load_dir is not None:
            return self.last_load_dir
        return Path.cwd()

    def output_path_from_field(self) -> Path | None:
        value = str(self.output_field.stringValue()).strip()
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.default_save_directory() / path
        if path.suffix == "":
            path = path.with_suffix(".gpx")
        if path.suffix.lower() != ".gpx":
            show_alert("Output file must have a .gpx extension.")
            self.output_field.setStringValue_(str(self.last_save_path) if self.last_save_path else value)
            return None
        resolved = path.resolve()
        self.set_output_path(resolved)
        return resolved

    @objc.IBAction
    def outputFileCommitted_(self, _sender):
        value = str(self.output_field.stringValue()).strip()
        path = self.output_path_from_field()
        if path is None:
            if not value:
                self.save_current()
            return
        self.save_current()

    @objc.IBAction
    def chooseOutputFile_(self, _sender):
        self.save_with_panel()

    def prune_tile_cache(self, max_age_seconds: float):
        try:
            prune_contextily_cache(
                self.tile_cache_dir,
                self.map_provider,
                max(0.0, float(max_age_seconds)) / 3600.0,
            )
        except OSError as exc:
            print(f"GPXEditor cache cleanup warning: {exc}", flush=True)

    def count_tile_cache_files(self) -> int:
        try:
            return sum(1 for path in self.tile_cache_dir.rglob("*") if path.is_file())
        except OSError:
            return -1

    def cached_tile_urls(self) -> set[str]:
        if self.cached_map_tile_urls is not None:
            return self.cached_map_tile_urls
        urls: set[str] = set()
        cache_root = self.tile_cache_dir / "contextily" / "tile" / "_fetch_tile"
        try:
            for metadata_path in cache_root.glob("*/metadata.json"):
                try:
                    metadata = json.loads(metadata_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                tile_url = (metadata.get("input_args") or {}).get("tile_url")
                if tile_url:
                    urls.add(str(tile_url).strip("'\""))
        except OSError:
            pass
        self.cached_map_tile_urls = urls
        return urls

    def map_tile_urls(self, cx, diagnostics: dict, zoom: int) -> list[str]:
        return self.map_tile_urls_for_provider(cx, diagnostics, zoom, self.map_provider)

    @objc.python_method
    def map_tile_urls_for_provider(self, cx, diagnostics, zoom, provider):
        provider = contextily_provider(
            cx,
            provider,
            self.custom_map_url,
            self.custom_map_attribution,
            self.maximum_map_zoom,
            self.map_credential_id,
            geoapify_style=getattr(self, "geoapify_style", "osm-bright"),
        )
        return [
            provider_tile_url(provider, x, y, zoom)
            for x in range(diagnostics["x0"], diagnostics["x1"] + 1)
            for y in range(diagnostics["y0"], diagnostics["y1"] + 1)
        ]

    @objc.python_method
    def missing_map_tile_count(self, cx, diagnostics: dict, zoom: int, provider=None) -> int:
        cached = self.cached_tile_urls()
        urls = (self.map_tile_urls(cx, diagnostics, zoom) if provider is None else
                self.map_tile_urls_for_provider(cx, diagnostics, zoom, provider))
        return sum(1 for url in urls if url not in cached)

    def tile_diagnostics(self, extent: dict, zoom: int) -> dict:
        radius = 6378137.0
        limit = math.pi * radius

        def xtile(merc_x):
            value = int(math.floor(((max(-limit, min(limit, merc_x)) + limit) / (2 * limit)) * (2 ** zoom)))
            return max(0, min((2 ** zoom) - 1, value))

        def ytile(merc_y):
            value = int(math.floor(((limit - max(-limit, min(limit, merc_y))) / (2 * limit)) * (2 ** zoom)))
            return max(0, min((2 ** zoom) - 1, value))

        min_x = float(extent["min_x"])
        max_x = float(extent["max_x"])
        min_y = float(extent["min_y"])
        max_y = float(extent["max_y"])
        x0, x1 = sorted((xtile(min_x), xtile(max_x)))
        y0, y1 = sorted((ytile(max_y), ytile(min_y)))
        return {
            "width_m": max_x - min_x,
            "height_m": max_y - min_y,
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y1,
            "nx": x1 - x0 + 1,
            "ny": y1 - y0 + 1,
            "count": (x1 - x0 + 1) * (y1 - y0 + 1),
        }

    def effective_tile_zoom(self, extent: dict, requested_zoom: int) -> tuple[int, dict]:
        return self.effective_tile_zoom_for_limit(extent, requested_zoom, self.maximum_map_tiles)

    def effective_tile_zoom_for_limit(self, extent: dict, requested_zoom: int, max_tiles: int) -> tuple[int, dict]:
        effective_zoom = max(0, min(self.maximum_map_zoom, int(requested_zoom)))
        diagnostics = self.tile_diagnostics(extent, effective_zoom)
        while effective_zoom > 0 and diagnostics["count"] > max_tiles:
            effective_zoom -= 1
            diagnostics = self.tile_diagnostics(extent, effective_zoom)
        return effective_zoom, diagnostics

    def viewport_signature(self, mode: str, tracks: list[TrackRecord], extent: dict, requested_zoom: int):
        fitted = self.fit_extent_to_aspect(extent, (1920, 1080))
        effective_zoom, _diagnostics = self.effective_tile_zoom(fitted, requested_zoom)
        rounded_extent = tuple(round(float(fitted[key]), 1) for key in ("min_x", "max_x", "min_y", "max_y"))
        track_ids = tuple(track.nr for track in tracks)
        return mode, track_ids, effective_zoom, rounded_extent

    @objc.python_method
    def snapshot(self) -> list[tuple[int, str, str]]:
        return [
            (
                track.nr,
                ET.tostring(track.element, encoding="unicode"),
                str(track.source_file or ""),
            )
            for track in self.tracks
        ]

    def restore_snapshot(self, snapshot: list[tuple[int, str, str]]):
        restored = []
        for nr, xml_text, source in snapshot:
            restored.append(TrackRecord(nr, ET.fromstring(xml_text), source))
        self.tracks = restored
        self.next_nr = max(getattr(self, "next_nr", 1), max([track.nr for track in self.tracks], default=0) + 1)
        self.dirty = True
        self.recalculate()

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def push_visibility_undo(self, before: dict[int, bool], after: dict[int, bool]):
        self.undo_stack.append(VisibilityUndoEntry(dict(before), dict(after)))
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def metadata_snapshot(self, tracks, kind=MutationKind.METADATA):
        return TrackMetadataUndoEntry(tuple((track.nr, track.track_number,
            next((node.get("stage_number") for node in track.element.findall("gpx:extensions/*", NS)
                  if node.tag.rsplit("}", 1)[-1] == MYCAMINO_EXT_TAG), None),
            track.element.findtext(qname("type"))) for track in tracks), kind)

    def push_metadata_undo(self, tracks, kind=MutationKind.METADATA):
        self.undo_stack.append(self.metadata_snapshot(tracks, kind))
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def restore_metadata(self, entry):
        selected = list(self.selected_tracks())
        current = {track.nr: track for track in self.tracks}
        restored = [(current.get(value[0]), *value[1:]) for value in entry.values]
        restored = [value for value in restored if value[0] is not None]
        inverse = self.metadata_snapshot([value[0] for value in restored], entry.kind)
        for track, number, stage, kind in restored:
            track.set_order_number(number)
            node = get_or_create_mycamino_track_extension(track.element)
            if stage is None:
                node.attrib.pop("stage_number", None)
            else:
                node.set("stage_number", stage)
            type_node = track.element.find(qname("type"))
            if kind is None:
                if type_node is not None:
                    track.element.remove(type_node)
            else:
                track.set_track_type(kind)
                track.element.find(qname("type")).text = kind
        self.selected_nrs = [track.nr for track in selected]
        self.metadata_changed("Restored track metadata.", entry.kind)
        return inverse

    def metadata_changed(self, message, kind=MutationKind.METADATA):
        self.dirty = True
        self.next_nr = max(getattr(self, "next_nr", 1), max((track.nr for track in self.tracks), default=0) + 1)
        self._rebuild_table_rows_from_cached_metrics()
        self.update_selection_field()
        self.redraw_open_plot_views()
        self.set_status(message)
        self._notify_workspace_mutation(kind, message, [track.nr for track in self.tracks])

    def push_track_elements_undo(self, tracks, kind=MutationKind.GEOMETRY):
        self.undo_stack.append(
            TrackElementsUndoEntry(
                {
                    track.nr: ET.tostring(track.element, encoding="unicode")
                    for track in tracks
                },
                kind,
            )
        )
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def push_track_order_undo(self):
        self.undo_stack.append(
            TrackOrderUndoEntry(tuple(track.nr for track in self.tracks))
        )
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def push_anchor_undo(self):
        self.undo_stack.append(AnchorUndoEntry(self.anchor))
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def _reload_visibility_rows(self, changed_numbers: set[int]):
        indexes = [
            index
            for index, track in enumerate(self.tracks)
            if track.nr in changed_numbers
        ]
        if not indexes:
            return
        index_set_class = objc.lookUpClass("NSMutableIndexSet")
        changed_rows = index_set_class.alloc().init()
        for row in indexes:
            changed_rows.addIndex_(row)
        all_columns = index_set_class.alloc().init()
        for column in range(len(self.columns)):
            all_columns.addIndex_(column)
        self.track_table.reloadDataForRowIndexes_columnIndexes_(
            changed_rows, all_columns
        )

        sum_column = self.track_table.columnWithIdentifier_(nsstring("sum"))
        if sum_column >= 0:
            cumulative_rows = index_set_class.alloc().init()
            for row in range(min(indexes), len(self.tracks)):
                cumulative_rows.addIndex_(row)
            sum_columns = index_set_class.alloc().init()
            sum_columns.addIndex_(sum_column)
            self.track_table.reloadDataForRowIndexes_columnIndexes_(
                cumulative_rows, sum_columns
            )
        if len(self.table_rows) > len(self.tracks):
            summary_rows = index_set_class.alloc().init()
            summary_rows.addIndex_(len(self.tracks))
            self.track_table.reloadDataForRowIndexes_columnIndexes_(
                summary_rows, all_columns
            )

    def _notify_workspace_mutation(self, kind, message: str, track_numbers=()):
        refresh = getattr(self, "refresh_timing_tools", None)
        if callable(refresh):
            refresh()
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is None:
            return
        mutation = WorkspaceMutation.create(
            "gpx", kind, track_numbers=track_numbers, message=message
        )
        if hasattr(workspace, "workspaceMutationDidOccur_"):
            workspace.workspaceMutationDidOccur_(mutation)
        elif hasattr(workspace, "adventureComponentDidChange_"):
            workspace.adventureComponentDidChange_("gpx")
        elif hasattr(workspace, "workspaceDocumentDidChange_"):
            workspace.workspaceDocumentDidChange_(message)

    def mark_visibility_changed(self, changed_numbers: set[int], message: str):
        self.dirty = True
        self._rebuild_table_rows_from_cached_metrics(reload_table=False)
        self._reload_visibility_rows(set(changed_numbers))
        self.set_status(message)
        if hasattr(self.track_table, "displayIfNeeded"):
            self.track_table.displayIfNeeded()
        self._notify_workspace_mutation(
            MutationKind.VISIBILITY, message, changed_numbers
        )
        self.redraw_open_plot_views()

    def apply_visibility_states(self, states: dict[int, bool], message: str):
        changed = set()
        by_number = {track.nr: track for track in self.tracks}
        for number, hidden in states.items():
            track = by_number.get(number)
            if track is None or track.hidden == bool(hidden):
                continue
            track.set_hidden(bool(hidden))
            changed.add(number)
        if changed:
            self.mark_visibility_changed(changed, message)

    def mark_dirty(self, message: str, kind=MutationKind.GEOMETRY, track_numbers=()):
        self.dirty = True
        self.recalculate()
        self.set_status(message)
        self._notify_workspace_mutation(kind, message, track_numbers)

    def invalidate_track_metrics(self, track: TrackRecord):
        track.timing_diagnostic_generation = getattr(track, "timing_diagnostic_generation", 0) + 1
        track.metrics_dirty = True
        track.processed = None
        track.processed_signature = None
        track.processed_fingerprint = None
        track.display_geometry = None
        track.display_geometry_cache.clear()
        cache = getattr(track, "point_range_metrics_cache", None)
        if cache is not None:
            cache.clear()

    def interpolate_track_heights(self, track, ranges):
        try:
            updates = interpolated_height_updates(track.element, ranges)
        except ValueError as exc:
            show_alert("Cannot interpolate height.", str(exc))
            return 0
        if not updates:
            self.set_status("No height changes needed. Select at least three points with valid endpoint heights.")
            return 0
        inspector = self.existing_inspector_for_track(track)
        selected_rows = inspector.selected_row_indexes() if inspector is not None else []
        if inspector is not None:
            inspector.push_undo()
        else:
            self.push_track_elements_undo([track], MutationKind.ELEVATION)
        for point, text in updates:
            elevation = point.find("gpx:ele", NS)
            if elevation is None:
                elevation = ET.Element(qname("ele"))
                point.insert(0, elevation)
            elevation.text = text
        self.invalidate_track_metrics(track)
        self.mark_dirty(
            f"Interpolated height for {len(updates)} {'point' if len(updates) == 1 else 'points'} in {track.display_label}.",
            MutationKind.ELEVATION, [track.nr],
        )
        if inspector is not None:
            inspector.reload_rows()
            inspector.select_point_rows(selected_rows)
        points = track.points()
        for _window, _delegate, view in self.plot_windows:
            if isinstance(view, PlotView) and view.cursor is not None and view.cursor[2] is track:
                index = view.cursor[0]
                if 0 <= index < len(points):
                    view.cursor = (index, points[index], track)
        self.redraw_open_plot_views()
        return len(updates)

    def invalidate_all_metrics(self):
        for track in self.tracks:
            self.invalidate_track_metrics(track)

    def refresh_open_plot_views(self):
        live_windows = []
        for window, delegate, view in self.plot_windows:
            if window is None:
                continue
            live_windows.append((window, delegate, view))
            if not window.isVisible():
                continue
            if hasattr(view, "rerender_current_map"):
                if isinstance(view, PlotView):
                    view.cursor = None
                    view.marker = None
                    view.last_viewport_signature = None
                view.rerender_current_map()
            elif isinstance(view, ElevationProfileView):
                view.setNeedsDisplay_(True)
        self.plot_windows = live_windows

    def redraw_open_plot_views(self):
        live_windows = []
        for window, delegate, view in self.plot_windows:
            if window is None:
                continue
            live_windows.append((window, delegate, view))
            if not window.isVisible():
                continue
            if isinstance(view, (PlotView, ElevationProfileView)):
                if isinstance(view, PlotView):
                    view.update_window_title()
                else:
                    window.setTitle_(view.profile_title())
                view.setNeedsDisplay_(True)
            elif isinstance(view, TrackInspectorController):
                window.setTitle_(f"Inspect {view.track.display_label}")
                text = str(view.info_label.stringValue())
                _old, separator, metrics = text.partition(" | ")
                view.info_label.setStringValue_(f"{view.track.display_label}: {view.track.name}" + (separator + metrics if separator else ""))
        self.plot_windows = live_windows

    def register_auxiliary_window(self, window):
        if window is None or window is self.window:
            return
        if not any(existing == window for existing in self.auxiliary_windows):
            self.auxiliary_windows.append(window)
        if not any(existing == window for existing in self.plot_window_refs):
            self.plot_window_refs.append(window)

    def unregister_auxiliary_window(self, window):
        if window is None:
            return
        WINDOW_REGISTRY.retire(window)
        self.auxiliary_windows = [existing for existing in self.auxiliary_windows if existing != window]
        self.plot_windows = [
            (plot_window, delegate, view)
            for plot_window, delegate, view in self.plot_windows
            if plot_window != window
        ]
        self.plot_window_refs = [existing for existing in self.plot_window_refs if existing != window]
        if self.help_window is not None and self.help_window == window:
            self.help_window = None

    def existing_plot_view(self, mode: str):
        live_windows = []
        found = None
        for window, delegate, view in self.plot_windows:
            if window is None:
                continue
            live_windows.append((window, delegate, view))
            if found is None and isinstance(view, PlotView) and view.mode == mode:
                found = (window, view)
        self.plot_windows = live_windows
        return found

    def existing_elevation_profile_for_plot_view(self, plot_view: PlotView):
        live_windows = []
        found = None
        for window, delegate, view in self.plot_windows:
            if window is None:
                continue
            live_windows.append((window, delegate, view))
            if found is None and isinstance(view, ElevationProfileView) and view.plot_view is plot_view:
                found = (window, view)
        self.plot_windows = live_windows
        return found

    def close_auxiliary_windows(self):
        self.closing_auxiliary_windows = True
        windows = []
        for window in list(getattr(self, "plot_window_refs", [])):
            if window is not None and not any(existing == window for existing in windows):
                windows.append(window)
        for window in list(self.auxiliary_windows):
            if window is not None and not any(existing == window for existing in windows):
                windows.append(window)
        for window, _delegate, view in list(self.plot_windows):
            self.plot_window_closing(view)
            if window is not None and not any(existing == window for existing in windows):
                windows.append(window)
        if self.help_window is not None and not any(existing == self.help_window for existing in windows):
            windows.append(self.help_window)
        if self.media_viewer is not None:
            self.media_viewer.close_(None)
            self.media_viewer = None

        for window in windows:
            try:
                window.setDelegate_(None)
            except Exception:
                pass
            try:
                window.orderOut_(None)
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass

        self.plot_windows = []
        self.plot_window_refs = []
        self.auxiliary_windows = []
        self.help_window = None
        self.closing_auxiliary_windows = False

    def open_elevation_profile_for_plot_view(self, plot_view: PlotView):
        existing = self.existing_elevation_profile_for_plot_view(plot_view)
        if existing is not None:
            window, view = existing
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            window.setTitle_(view.profile_title())
            view.sync_to_map_extent()
            view.setNeedsDisplay_(True)
            return view
        title = "Overview Elevation Profile" if plot_view.mode == "overview" else "Track Elevation Profile"
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180, 80, 1000, 180),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setReleasedWhenClosed_(False)
        window.setMinSize_(NSMakeSize(520, 120))
        view = ElevationProfileView.alloc().initWithController_plotView_(self, plot_view)
        window.setContentView_(view)
        delegate = PlotWindowDelegate.alloc().initWithController_view_(self, view)
        window.setDelegate_(delegate)
        window.setTitle_(view.profile_title() or title)
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        window.makeFirstResponder_(view)
        self.plot_windows.append((window, delegate, view))
        self.register_auxiliary_window(window)
        view.sync_to_map_extent()
        if plot_view.window() is not None:
            plot_view.window().makeKeyAndOrderFront_(None)
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        window.makeFirstResponder_(view)
        self.set_status("Elevation profile opened.")
        return view

    def raise_elevation_profile_for_plot_view(self, plot_view: PlotView):
        existing = self.existing_elevation_profile_for_plot_view(plot_view)
        if existing is None:
            return
        window, view = existing
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        window.makeFirstResponder_(view)

    def refresh_elevation_profile_for_plot_view(self, plot_view: PlotView):
        existing = self.existing_elevation_profile_for_plot_view(plot_view)
        if existing is None:
            return
        window, view = existing
        window.setTitle_(view.profile_title())
        view.setNeedsDisplay_(True)

    def sync_elevation_profile_to_plot_view(self, plot_view: PlotView):
        """Synchronize an open profile after the associated map viewport changes."""
        existing = self.existing_elevation_profile_for_plot_view(plot_view)
        if existing is None:
            return
        window, view = existing
        window.setTitle_(view.profile_title())
        view.sync_to_map_extent()

    def close_elevation_profile_for_plot_view(self, plot_view: PlotView):
        existing = self.existing_elevation_profile_for_plot_view(plot_view)
        if existing is None:
            return
        window, _view = existing
        try:
            window.performClose_(None)
        except Exception:
            try:
                window.close()
            except Exception:
                pass

    def close_plot_windows_for_inspector(self, inspector):
        for window, _delegate, view in list(self.plot_windows):
            if isinstance(view, PlotView) and getattr(view, "inspector", None) is inspector:
                self.close_elevation_profile_for_plot_view(view)
                try:
                    window.performClose_(None)
                except Exception:
                    try:
                        window.close()
                    except Exception:
                        pass

    def refresh_track_plot_for_track(self, track: TrackRecord, inspector=None):
        existing = self.existing_plot_view("track")
        if existing is None:
            return
        window, view = existing
        self.selected_nrs = [track.nr]
        self.update_selection_field()
        self.highlight_selected_rows()
        plot_info = self.interactive_plot_info("track")
        if plot_info is None:
            return
        view.track_index = 0
        view.replace_plot_info(plot_info)
        if inspector is not None:
            view.inspector = inspector
            inspector.plot_view = view
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        window.makeFirstResponder_(view)
        view.update_window_title()
        view.setNeedsDisplay_(True)

    def open_track_workflow_at_point(
        self,
        track: TrackRecord,
        point_index: int,
        source_view=None,
    ):
        workspace = getattr(source_view, "adventure_workspace_delegate", None)
        if workspace is not None and hasattr(workspace, "inspect_workspace_track"):
            return workspace.inspect_workspace_track(track, point_index)
        if not any(loaded_track is track for loaded_track in self.tracks):
            return None
        self.selected_nrs = [track.nr]
        self.update_selection_field()
        self.highlight_selected_rows()
        inspector = self.open_inspector_for_track(track)
        view = self.open_or_reload_track_plot_for_track(track)
        if view is not None:
            view.inspector = inspector
            inspector.plot_view = view
            view.move_cursor_to_track_point(track, point_index, inspector)
        inspector.select_point_index(point_index)
        self.set_status(f"Opened {track.display_label} at point {point_index + 1}.")
        return view

    def open_or_reload_track_plot_for_track(self, track: TrackRecord):
        existing = self.existing_plot_view("track")
        if existing is not None:
            window, view = existing
            loaded = any(loaded_track is track for loaded_track in view.track_sequence())
            if loaded:
                view.track_index = next((index for index, loaded_track in enumerate(view.track_sequence()) if loaded_track is track), 0)
                view.update_track_plot_info(track)
                window.makeKeyAndOrderFront_(None)
                window.orderFrontRegardless()
                window.makeFirstResponder_(view)
                view.update_window_title()
                view.setNeedsDisplay_(True)
                return view
        if existing is None:
            return self.open_plot_window("track", recreate_existing=True)
        plot_info = self.interactive_plot_info("track")
        if plot_info is None:
            return None
        if existing is not None:
            window, view = existing
            view.track_index = 0
            view.replace_plot_info(plot_info)
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            view.update_window_title()
            view.setNeedsDisplay_(True)
            return view
        return None

    def plot_window_closing(self, closing_view):
        if isinstance(closing_view, PlotView):
            if closing_view.plot_closed:
                return
            closing_view.plot_closed = True
            if closing_view.long_press_timer is not None:
                closing_view.long_press_timer.invalidate()
                closing_view.long_press_timer = None
            closing_view.photo_thumbnail_generation += 1
            for future in closing_view.photo_thumbnail_futures.values():
                future.cancel()
            closing_view.photo_thumbnail_futures.clear()
            closing_view.photo_thumbnail_executor.shutdown(wait=False, cancel_futures=True)
            closing_view.cancel_interactive_tile_requests(shutdown=True)
            self.close_elevation_profile_for_plot_view(closing_view)
        elif isinstance(closing_view, ElevationProfileView):
            closing_view.close_profile_help()
            return
        inspector = getattr(closing_view, "inspector", None)
        if inspector is not None and getattr(inspector, "plot_view", None) is closing_view:
            inspector.plot_view = None
        if hasattr(closing_view, "inspector"):
            closing_view.inspector = None

    def selected_tracks(self) -> list[TrackRecord]:
        by_nr = {track.nr: track for track in self.tracks}
        return [by_nr[nr] for nr in self.selected_nrs if nr in by_nr]

    @objc.IBAction
    def showTrackTable_(self, _sender):
        self.show()

    def validateMenuItem_(self, item):
        action = str(item.action() or "")
        if action in {"inspectTrack:", "plotSelected:", "contextElevationProfile:", "saveSelectedTracksAs:"}:
            return bool(self.selected_tracks())
        if action in {"renumberTracks:", "invertTrackSelection:"}:
            return bool(self.tracks)
        if action == "estimateRouteTimesFromMedia:":
            selected = self.selected_tracks()
            return len(selected) == 1 and self._media_timing_eligible_route(selected[0])
        if action in {"estimateTrackTimes:", "findMissingTimestamps:"}:
            return bool(self.tracks) and (action == "findMissingTimestamps:" or self.track_mutations_enabled)
        if action == "undo:":
            return bool(self.undo_stack)
        if action == "redo:":
            return bool(self.redo_stack)
        return True

    def media_paths_for_track_point(self, track: TrackRecord, point_index: int) -> list[Path]:
        points = track.points()
        if not 0 <= int(point_index) < len(points):
            return []
        source_directory = Path(track.source_file).parent if track.source_file else None
        return [
            path
            for path in media_paths_for_point(points[int(point_index)].element, source_directory)
            if path.is_file()
        ]

    def show_media_for_track_point(self, track: TrackRecord, point_index: int) -> bool:
        all_paths = []
        selected_path = None
        source_directory = Path(track.source_file).parent if track.source_file else None
        for index, point in enumerate(track.points()):
            for path in media_paths_for_point(point.element, source_directory):
                if not path.is_file() or path in all_paths:
                    continue
                if index == point_index and selected_path is None:
                    selected_path = path
                all_paths.append(path)
        if not all_paths or selected_path is None:
            return False
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None:
            self.media_viewer = workspace.media_viewer
        if self.media_viewer is None:
            self.media_viewer = CocoaMediaViewer.alloc().init()
            if workspace is not None:
                workspace.media_viewer = self.media_viewer
        self.media_viewer.show_paths(
            all_paths,
            all_paths.index(selected_path),
            selection_callback=lambda path: self.highlight_media_path(track, path),
        )
        return True

    def highlight_media_path(self, track: TrackRecord, path: Path):
        selected_path = Path(path).resolve(strict=False)
        source_directory = Path(track.source_file).parent if track.source_file else None
        selected_index = None
        for index, point in enumerate(track.points()):
            if any(
                candidate.resolve(strict=False) == selected_path
                for candidate in media_paths_for_point(point.element, source_directory)
            ):
                selected_index = index
                break
        for _window, _delegate, view in self.plot_windows:
            if not isinstance(view, PlotView):
                continue
            view.active_media_path = selected_path
            if selected_index is not None and track in view.display_tracks():
                view.move_cursor_to_track_point(
                    track,
                    selected_index,
                    sync_table=True,
                    focus_plot=False,
                )
            view.setNeedsDisplay_(True)

    def visible_tracks(self) -> list[TrackRecord]:
        return [track for track in self.tracks if not track.hidden]

    def install_loading_table_rows(self):
        """Install stable rows without calculating a cache miss on AppKit."""
        rows = []
        cumulative = 0.0
        for index, track in enumerate(self.tracks, 1):
            metrics = track.metrics if track.metrics and not track.metrics_dirty else None
            included = not track.hidden
            if metrics is not None and included:
                cumulative += float(metrics.get("length_km") or 0.0)
            rows.append(
                {
                    "row": str(index),
                    "nr": str(track.track_number or ""),
                    "_internal_id": track.nr,
                    "show": "no" if track.hidden else "yes",
                    "name": track.name,
                    "date": "Loading…" if metrics is None else format_datetime_local(metrics.get("time")),
                    "length": "…" if metrics is None else f"{metrics['length_km']:.1f}",
                    "duration": "…" if metrics is None else format_duration(metrics.get("duration")),
                    "sum": f"{cumulative:.1f}" if metrics is not None else "…",
                    "distance": "…" if metrics is None else (
                        "N/A" if metrics.get("distance_km") is None else f"{metrics['distance_km']:.1f}"
                    ),
                    "speed": "…" if metrics is None else (
                        "N/A" if metrics.get("speed_kmh") is None else f"{metrics['speed_kmh']:.1f}"
                    ),
                    "moving_speed": "…" if metrics is None else (
                        "N/A" if metrics.get("moving_speed_kmh") is None else f"{metrics['moving_speed_kmh']:.1f}"
                    ),
                    "ascent": "…" if metrics is None else f"{metrics['ascent_m']:.1f}",
                    "descent": "…" if metrics is None else f"{metrics['descent_m']:.1f}",
                    "npoints": "…" if metrics is None else str(metrics["npoints"]),
                    "_summary": False,
                }
            )
        if self.tracks:
            rows.append(
                {
                    "row": str(visible_track_count(self.tracks)),
                    "nr": "",
                    "show": "",
                    "name": "Loading track data…",
                    "date": "",
                    "length": "",
                    "duration": "",
                    "sum": "",
                    "distance": "",
                    "speed": "",
                    "moving_speed": "",
                    "ascent": "",
                    "descent": "",
                    "npoints": "",
                    "_summary": True,
                }
            )
        self.table_rows = rows
        self.track_table.reloadData()
        self.highlight_selected_rows()

    def select_track_in_table(self, nr: int):
        if nr not in {track.nr for track in self.tracks}:
            return
        self.selected_nrs = [nr]
        self.update_selection_field()
        self.highlight_selected_rows()
        self.redraw_open_plot_views()

    def recalculate(self, reload_table: bool = True):
        if self.anchor is None:
            for track in self.tracks:
                first_lat = track.metrics.get("first_lat") if track.metrics else None
                first_lon = track.metrics.get("first_lon") if track.metrics else None
                if first_lat is not None and first_lon is not None:
                    self.anchor = (float(first_lat), float(first_lon))
                    break
                point = self.processed_track(track).first_point
                if point is not None:
                    self.anchor = (point.lat, point.lon)
                    break
        for track in self.tracks:
            metrics = self.compute_metrics(track)
            track.metrics = metrics
            self.maybe_log_duration_diagnostic(track, metrics)
        self._rebuild_table_rows_from_cached_metrics(reload_table=reload_table)

    def _rebuild_table_rows_from_cached_metrics(self, reload_table: bool = True):
        """Refresh table rows and totals without reprocessing track geometry."""
        rows: list[dict[str, str]] = []
        identities = track_ids(self.tracks)
        cumulative = 0.0
        values = {"length": [], "duration": [], "speed": [], "moving_speed": [], "ascent": [], "descent": []}
        for index, track in enumerate(self.tracks, 1):
            metrics = track.metrics
            if not metrics or track.metrics_dirty:
                # Editing is disabled while cache misses are being processed;
                # keep programmatic callers safe rather than showing stale data.
                return self.recalculate(reload_table=reload_table)
            included = not track.hidden
            if included:
                cumulative += metrics["length_km"]
            if included and metrics["length_km"] > 0:
                values["length"].append(metrics["length_km"])
            if included and metrics["duration"] is not None and metrics["duration"].total_seconds() > 0:
                values["duration"].append(metrics["duration"])
            if included and metrics["speed_kmh"] is not None:
                values["speed"].append(metrics["speed_kmh"])
            if included and metrics["moving_speed_kmh"] is not None:
                values["moving_speed"].append(metrics["moving_speed_kmh"])
            if included:
                values["ascent"].append(metrics["ascent_m"])
                values["descent"].append(metrics["descent_m"])
            rows.append(
                {
                    "row": str(index),
                    "nr": str(track.track_number or ""),
                    "_internal_id": track.nr,
                    "stage": str(track.stage_number or ""),
                    "track_id": identities[track.nr],
                    "type": TYPE_LABELS[track.track_type],
                    "show": "no" if track.hidden else "yes",
                    "name": track.name,
                    "date": format_datetime_local(metrics["time"]),
                    "length": f"{metrics['length_km']:.1f}",
                    "duration": format_duration(metrics["duration"]),
                    "sum": f"{cumulative:.1f}",
                    "distance": "N/A" if metrics["distance_km"] is None else f"{metrics['distance_km']:.1f}",
                    "speed": "N/A" if metrics["speed_kmh"] is None else f"{metrics['speed_kmh']:.1f}",
                    "moving_speed": "N/A" if metrics["moving_speed_kmh"] is None else f"{metrics['moving_speed_kmh']:.1f}",
                    "ascent": f"{metrics['ascent_m']:.1f}",
                    "descent": f"{metrics['descent_m']:.1f}",
                    "npoints": str(metrics["npoints"]),
                    "_summary": False,
                }
            )
        if self.tracks:
            visible_count = visible_track_count(self.tracks)
            rows.append(
                {
                    "row": str(visible_count),
                    "nr": "",
                    "show": "",
                    "name": "Average / Total",
                    "date": "",
                    "length": self.average_text(values["length"]),
                    "duration": format_total_duration(sum(values["duration"], timedelta())) if values["duration"] else "N/A",
                    "sum": f"{cumulative:.1f}",
                    "distance": "",
                    "speed": self.average_text(values["speed"]),
                    "moving_speed": self.average_text(values["moving_speed"]),
                    "ascent": self.average_text(values["ascent"]),
                    "descent": self.average_text(values["descent"]),
                    "npoints": str(sum(track.metrics.get("npoints", 0) for track in self.tracks if not track.hidden)),
                    "_summary": True,
                }
            )
        self.table_rows = rows
        if reload_table:
            self.track_table.reloadData()
        if self.tracks and not self.did_auto_size_track_columns:
            self.auto_size_track_columns()
            self.did_auto_size_track_columns = True
        if reload_table:
            self.highlight_selected_rows()

    def auto_size_track_columns(self):
        data_font = self.table_font(12)
        header_font = self.table_font(11)
        data_char_width = max(float(nsstring("0").sizeWithAttributes_({NSFontAttributeName: data_font}).width), 6.0)
        header_char_width = max(float(nsstring("0").sizeWithAttributes_({NSFontAttributeName: header_font}).width), 5.5)
        for identifier, title, default_width, _editable in self.columns:
            header_chars = max((len(line) for line in title.splitlines() if line), default=0)
            data_values = [str(row.get(identifier, "")) for row in self.table_rows[: min(len(self.table_rows), 80)]]
            data_chars = max((len(value) for value in data_values), default=0)
            width = max(34.0, header_chars * header_char_width + 8.0, data_chars * data_char_width + 10.0)
            if identifier == "name":
                width = min(width, 35 * data_char_width + 10.0)
            elif identifier == "date":
                width = max(width, 16 * data_char_width + 10.0)
            elif identifier == "show":
                width = 50.0
            elif identifier in {"length", "duration", "sum", "distance", "speed", "moving_speed", "ascent", "descent", "npoints"}:
                width = min(max(width, 42.0), float(default_width))
            column = self.track_table.tableColumnWithIdentifier_(nsstring(identifier))
            if column is not None:
                column.setWidth_(width)
                column.setMinWidth_(min(width, 55))

    def average_text(self, values: list[float]) -> str:
        return "N/A" if not values else f"{sum(values) / len(values):.1f}"

    def filtered_track_times(self, points: list[PointInfo]) -> list[tuple[int, datetime]]:
        timed_points = [(index, point.time) for index, point in enumerate(points) if point.time is not None]
        if len(timed_points) < 3:
            return timed_points
        filtered: list[tuple[int, datetime]] = []
        total = len(timed_points)
        for position, (point_index, current_time) in enumerate(timed_points):
            previous_time = filtered[-1][1] if filtered else None
            next_time = timed_points[position + 1][1] if position + 1 < total else None
            if (
                previous_time is not None
                and next_time is not None
                and current_time < previous_time
                and next_time >= previous_time
                and (previous_time - current_time) > timedelta(days=1)
                and (next_time - current_time) > timedelta(days=1)
            ):
                continue
            filtered.append((point_index, current_time))
        return filtered

    def maybe_log_duration_diagnostic(self, track: TrackRecord, metrics: dict):
        duration = metrics.get("duration")
        duration_seconds = None if duration is None else duration.total_seconds()
        suspicious = duration is not None and duration_seconds is not None and duration_seconds > 7 * 24 * 3600
        if not (self.debug or suspicious):
            return
        points = track.points()
        timed_points = [(index + 1, point.time) for index, point in enumerate(points) if point.time is not None]
        filtered_times = [(index + 1, time) for index, time in self.filtered_track_times(points)]
        first_seq_index = timed_points[0][0] if timed_points else None
        first_seq_time = timed_points[0][1] if timed_points else None
        last_seq_index = timed_points[-1][0] if timed_points else None
        last_seq_time = timed_points[-1][1] if timed_points else None
        min_time = min((time for _index, time in timed_points), default=None)
        max_time = max((time for _index, time in timed_points), default=None)
        filtered_start = filtered_times[0][1] if filtered_times else None
        filtered_end = filtered_times[-1][1] if filtered_times else None
        out_of_order = 0
        for (_previous_index, previous_time), (_current_index, current_time) in zip(timed_points, timed_points[1:]):
            if current_time < previous_time:
                out_of_order += 1
        signature = (
            track.nr,
            track.source_file,
            duration_seconds,
            metrics.get("time"),
            metrics.get("start_time"),
            metrics.get("end_time"),
            first_seq_index,
            first_seq_time,
            last_seq_index,
            last_seq_time,
            min_time,
            max_time,
            filtered_start,
            filtered_end,
            len(filtered_times),
            out_of_order,
            len(timed_points),
        )
        if self.duration_diagnostic_cache.get(track.nr) == signature:
            return
        self.duration_diagnostic_cache[track.nr] = signature
        print(
            "GPXEditor duration diagnostic:",
            f"track=#{track.nr}",
            f"name={track.name!r}",
            f"source={track.source_file or 'N/A'}",
            f"timed_points={len(timed_points)}",
            f"track_time={metrics.get('time')}",
            f"start_time={metrics.get('start_time')}",
            f"end_time={metrics.get('end_time')}",
            f"duration={duration!r}",
            f"formatted={format_duration(duration)}",
            f"first_seq=({first_seq_index},{first_seq_time})",
            f"last_seq=({last_seq_index},{last_seq_time})",
            f"min_time={min_time}",
            f"max_time={max_time}",
            f"filtered_start={filtered_start}",
            f"filtered_end={filtered_end}",
            f"filtered_timed_points={len(filtered_times)}",
            f"out_of_order={out_of_order}",
            f"suspicious={'yes' if suspicious else 'no'}",
            flush=True,
        )

    def anchor_key(self):
        return None if self.anchor is None else (round(self.anchor[0], 8), round(self.anchor[1], 8))

    def compute_metrics(self, track: TrackRecord, force: bool = False) -> dict:
        current_anchor_key = self.anchor_key()
        if not force and track.metrics and not track.metrics_dirty:
            if track.metrics.get("_anchor_key") == current_anchor_key:
                return track.metrics
            if track.metrics.get("last_lat") is not None and track.metrics.get("last_lon") is not None:
                metrics = dict(track.metrics)
                if self.anchor is not None:
                    metrics["distance_km"] = haversine_km(metrics["last_lat"], metrics["last_lon"], self.anchor[0], self.anchor[1])
                else:
                    metrics["distance_km"] = None
                metrics["_anchor_key"] = current_anchor_key
                track.metrics = metrics
                return metrics
        processed = self.processed_track(track, force=force)
        length = processed.length_km
        ascent = processed.ascent_m
        descent = processed.descent_m
        start_time = processed.start_time
        end_time = processed.end_time
        duration = processed.duration
        first_point_time = start_time
        track_time = parse_time(track.element.findtext("gpx:time", default="", namespaces=NS)) or first_point_time
        speed = None
        if duration is not None and duration.total_seconds() > 0:
            speed = length / (duration.total_seconds() / 3600.0)
        distance = None
        if self.anchor is not None and processed.last_point is not None:
            distance = haversine_km(
                processed.last_point.lat,
                processed.last_point.lon,
                self.anchor[0],
                self.anchor[1],
            )
        metrics = {
            "time": track_time,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "length_km": length,
            "distance_km": distance,
            "speed_kmh": speed,
            "moving_speed_kmh": processed.moving_average_speed_kmh,
            "ascent_m": ascent,
            "descent_m": descent,
            "npoints": processed.retained_point_count,
            "raw_npoints": processed.raw_point_count,
            "retained_npoints": processed.retained_point_count,
            "rejection_counts": processed.rejection_counts,
            "processing_options": processed.options.as_dict(),
            "first_lat": processed.first_point.lat if processed.first_point is not None else None,
            "first_lon": processed.first_point.lon if processed.first_point is not None else None,
            "last_lat": processed.last_point.lat if processed.last_point is not None else None,
            "last_lon": processed.last_point.lon if processed.last_point is not None else None,
            "_anchor_key": current_anchor_key,
        }
        track.metrics = metrics
        track.metrics_dirty = False
        return metrics

    def compute_point_range_metrics(self, track: TrackRecord, ranges: list[tuple[int, int]]) -> dict | None:
        if not ranges:
            return None
        key = (
            self.processing_parameter_signature(),
            tuple(sorted((min(int(start), int(end)), max(int(start), int(end))) for start, end in ranges)),
        )
        cache = track.point_range_metrics_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        raw_points = extract_raw_track_points(track.element)
        if not raw_points:
            return None
        normalized = []
        for start, end in ranges:
            start = max(0, min(int(start), len(raw_points) - 1))
            end = max(0, min(int(end), len(raw_points) - 1))
            if start > end:
                start, end = end, start
            normalized.append((start, end))
        if not normalized:
            return None
        normalized.sort()
        first_index = normalized[0][0]
        last_index = normalized[-1][1]
        length = 0.0
        ascent = 0.0
        descent = 0.0
        retained_count = 0
        duration = timedelta()
        has_duration = False
        for start, end in normalized:
            selected_raw = [copy.copy(point) for point in raw_points[start : end + 1]]
            if not selected_raw:
                continue
            first_segment = selected_raw[0].segment_index
            for point in selected_raw:
                point.segment_index -= first_segment
            processed = process_raw_points(selected_raw, self.processing_options())
            length += processed.length_km
            ascent += processed.ascent_m
            descent += processed.descent_m
            retained_count += processed.retained_point_count
            if processed.start_time is not None and processed.end_time is not None:
                segment_duration = processed.end_time - processed.start_time
                if segment_duration.total_seconds() >= 0:
                    duration += segment_duration
                    has_duration = True
        duration_value = duration if has_duration else None
        speed = None
        if duration_value is not None and duration_value.total_seconds() > 0:
            speed = length / (duration_value.total_seconds() / 3600.0)
        result = {
            "start_index": first_index,
            "end_index": last_index,
            "npoints": sum(end - start + 1 for start, end in normalized),
            "retained_npoints": retained_count,
            "start_time": raw_points[first_index].time,
            "end_time": raw_points[last_index].time,
            "duration": duration_value,
            "length_km": length,
            "speed_kmh": speed,
            "ascent_m": ascent,
            "descent_m": descent,
        }
        cache[key] = result
        while len(cache) > 8:
            cache.popitem(last=False)
        return result

    def is_editable_cell(self, row: int, column: str) -> bool:
        return (
            self.track_mutations_enabled
            and row < len(self.tracks)
            and column in {"show", "name", "date", "stage"}
        )

    def ensure_track_mutations_available(self) -> bool:
        if self.track_mutations_enabled:
            return True
        self.set_status(
            "Loading track data — editing will be available when processing finishes."
        )
        return False

    def begin_table_edit(self, row: int, column: str):
        if not (0 <= row < len(self.tracks)):
            self.active_table_edit = None
            return
        self.active_table_edit = {
            "track_nr": self.tracks[row].nr,
            "column": column,
            "value": self.table_rows[row].get(column, "")
            if row < len(self.table_rows)
            else "",
        }

    def commit_table_value(self, row: int, column: str, value: str):
        if self.canceling_table_edit:
            return
        edit = self.active_table_edit
        self.active_table_edit = None
        if edit is not None:
            if not (0 <= row < len(self.tracks)):
                return
            if (
                edit.get("track_nr") != self.tracks[row].nr
                or edit.get("column") != column
            ):
                self.set_status("Canceled an edit because the track table changed.")
                return
        self.edit_table_value(row, column, value)

    def cancel_active_table_edit(self):
        edit = self.active_table_edit
        self.active_table_edit = None
        self.canceling_table_edit = True
        try:
            self.track_table.abortEditing()
        finally:
            self.canceling_table_edit = False
        if edit is None:
            return
        row = next(
            (
                index
                for index, track in enumerate(self.tracks)
                if track.nr == edit.get("track_nr")
            ),
            None,
        )
        column = self.track_table.columnWithIdentifier_(
            nsstring(str(edit.get("column", "")))
        )
        if row is not None and column >= 0:
            rows = objc.lookUpClass("NSIndexSet").indexSetWithIndex_(row)
            columns = objc.lookUpClass("NSIndexSet").indexSetWithIndex_(column)
            self.track_table.reloadDataForRowIndexes_columnIndexes_(rows, columns)
        self.set_status("Edit canceled.")

    def edit_table_value(self, row: int, column: str, value: str):
        if hasattr(self, "ensure_track_mutations_available") and not self.ensure_track_mutations_available():
            return
        if row < 0 or row >= len(self.tracks):
            return
        track = self.tracks[row]
        if column == "show":
            text = value.strip().casefold()
            if text in {"", "toggle"}:
                hidden = not track.hidden
            elif text in {"yes", "y", "true", "1", "show", "visible"}:
                hidden = False
            elif text in {"no", "n", "false", "0", "hide", "hidden"}:
                hidden = True
            else:
                self.recalculate()
                self.set_status("Use yes or no for the Show column.")
                return
            if hidden == track.hidden:
                return
            before = {track.nr: track.hidden}
            after = {track.nr: hidden}
            self.push_visibility_undo(before, after)
            track.set_hidden(hidden)
            self.mark_visibility_changed(
                {track.nr},
                f"{track.display_label} is now {'hidden' if hidden else 'shown'} for statistics, overview, and PDF export.",
            )
        elif column == "stage":
            try:
                number = int(value.strip()) if value.strip() else None
                if number is not None and number < 1:
                    raise ValueError()
            except ValueError:
                self.set_status("Stage must be a positive whole number, or blank for no assigned stage.")
                self._rebuild_table_rows_from_cached_metrics()
                return
            if number != track.stage_number:
                self.push_metadata_undo([track])
                track.set_stage_number(number)
                self.metadata_changed("Stage number changed.")
        elif column == "name":
            old = track.name
            new = value.strip()
            if not new or new == old:
                self.recalculate()
                return
            self.push_track_elements_undo([track], MutationKind.METADATA)
            get_or_create_track_name(track.element).text = new
            self.mark_dirty(
                f"{track.display_label} name changed from '{old}' to '{new}'.",
                MutationKind.METADATA,
                {track.nr},
            )
        elif column == "date":
            new_time = parse_user_datetime(value)
            if new_time is None:
                self.recalculate()
                self.set_status(f"Ignored invalid date for {track.display_label}. Use dd.mm.yyyy hh:mm.")
                return
            self.push_track_elements_undo([track], MutationKind.TIMING)
            self.apply_track_start_time(track, new_time)
            self.mark_dirty(
                f"{track.display_label} start date changed to {format_datetime_local(new_time)}.",
                MutationKind.TIMING,
                {track.nr},
            )

    def apply_track_start_time(self, track: TrackRecord, new_time: datetime):
        points = track.points()
        old_first_time = next((point.time for point in points if point.time is not None), None)
        for child in list(track.element):
            if child.tag == qname("time"):
                track.element.remove(child)
        if old_first_time is not None:
            offset = new_time - old_first_time
            for point in points:
                if point.time is not None:
                    get_or_create_point_time(point.element).text = format_gpx_time(point.time + offset)
                    set_point_time_origin(point.element, "generated")
            set_manual_track_start_time(track.element, new_time)
            self.invalidate_track_metrics(track)
            return
        self.assign_missing_timestamps(track, new_time)
        set_manual_track_start_time(track.element, new_time)
        desc = get_or_create_child(track.element, "desc", {qname("name"), qname("cmt")})
        note = "Timestamp edited manually; original track had no usable point timestamps."
        desc.text = f"{desc.text or ''}\n{note}".strip()
        self.invalidate_track_metrics(track)

    def assign_missing_timestamps(self, track: TrackRecord, start_time: datetime, end_time: datetime | None = None):
        points = track.points()
        if not points:
            return
        processed = self.processed_track(track)
        distance_by_source = processed.source_distances_km()
        raw_points = extract_raw_track_points(track.element)
        repaired_times = timestamps_from_start(
            [
                {
                    "lat": point.lat,
                    "lon": point.lon,
                    "segment_index": point.segment_index,
                    "cumulative_distance_km": distance_by_source.get(point.source_index),
                }
                for point in raw_points
            ],
            start_time,
            end_time,
            self.average_speed() or self.project_parameters["gpx.fallback_walking_speed_kmh"],
        )
        for point, point_time in zip(raw_points, repaired_times):
            if point.element is not None:
                get_or_create_point_time(point.element).text = format_gpx_time(point_time)
                set_point_time_origin(point.element, "generated")
        get_or_create_track_time(track.element).text = format_gpx_time(start_time)
        self.invalidate_track_metrics(track)

    def average_speed(self) -> float | None:
        speeds = [track.metrics.get("speed_kmh") for track in self.tracks if track.metrics.get("speed_kmh")]
        return None if not speeds else sum(speeds) / len(speeds)

    def populate_track_time_from_first_point(self, track: TrackRecord) -> bool:
        existing = parse_time(track.element.findtext("gpx:time", default="", namespaces=NS))
        if existing is not None:
            return False
        points = track.points()
        if not points or points[0].time is None:
            return False
        get_or_create_track_time(track.element).text = format_gpx_time(points[0].time)
        track.metrics_dirty = True
        return True

    @objc.IBAction
    def projectNameCommitted_(self, _sender):
        new_name = str(self.project_field.stringValue()).strip()
        if new_name == self.project_name:
            return
        self.push_undo()
        self.project_name = new_name
        self.dirty = True
        self.set_status(f"Project name changed to '{new_name}'. It will be written to GPX metadata on save.")

    def create_empty_track(
        self,
        *,
        name: str | None = None,
        mark_dirty: bool = True,
        assign_default_project_name: bool = True,
    ) -> TrackRecord:
        """Append one canonical empty track and make it the active selection."""
        if mark_dirty:
            self.push_undo()
        element = ET.Element(qname("trk"))
        visible_number = max((track.track_number or 0 for track in self.tracks), default=0) + 1
        get_or_create_track_name(element).text = name or f"Track {visible_number}"
        ET.SubElement(element, qname("trkseg"))
        record = TrackRecord(self.next_nr, element, "")
        record.set_order_number(visible_number)
        record.set_stage_number(visible_number)
        record.set_track_type(self.project_parameters.get("track_types.new_track_default", "pilgrimage"))
        self.next_nr += 1
        if mark_dirty:
            self.document_ids_initialized = True
        self.tracks.append(record)
        self.selected_nrs = [record.nr]
        if assign_default_project_name and not self.project_name:
            self.project_name = "Untitled"
            self.project_field.setStringValue_(self.project_name)
        self.dirty = bool(mark_dirty) or self.dirty
        self.recalculate()
        self.update_selection_field()
        self.highlight_selected_rows()
        self.set_status(f"Created empty {record.display_label}. Add its first waypoint from the table, clipboard, media, or map.")
        if mark_dirty:
            self._notify_workspace_mutation(
                MutationKind.STRUCTURE,
                f"Created empty {record.display_label}.",
                {record.nr},
            )
        return record

    def prompt_for_coordinate_text(self) -> list[ParsedCoordinate] | None:
        """Collect and preview one or more common coordinate representations."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Add Waypoint Coordinates")
        alert.setInformativeText_(
            "Paste decimal, DMS, geo:, full Google Maps/Earth, KML, or GPX coordinates. "
            "Use one coordinate per line. Optional CSV fields are elevation, ISO time, and name."
        )
        alert.addButtonWithTitle_("Preview and Add")
        alert.addButtonWithTitle_("Cancel")
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 150))
        scroll.setHasVerticalScroller_(True)
        editor = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 510, 150))
        pasteboard_text = NSPasteboard.generalPasteboard().stringForType_("public.utf8-plain-text")
        if pasteboard_text:
            editor.setString_(str(pasteboard_text))
        scroll.setDocumentView_(editor)
        alert.setAccessoryView_(scroll)
        if int(alert.runModal()) != 1000:
            return None
        try:
            coordinates = parse_coordinate_text(str(editor.string()))
        except CoordinateParseError as exc:
            show_alert("Could not read the coordinates.", str(exc))
            return None
        lines = [
            f"{item.latitude:.8f}, {item.longitude:.8f}"
            + (f", {item.elevation_m:.1f} m" if item.elevation_m is not None else "")
            + (f", {item.timestamp.isoformat()}" if item.timestamp is not None else "")
            for item in coordinates[:10]
        ]
        if len(coordinates) > 10:
            lines.append(f"...and {len(coordinates) - 10} more")
        preview = NSAlert.alloc().init()
        preview.setMessageText_(f"Add {len(coordinates)} waypoint(s)?")
        preview.setInformativeText_("\n".join(lines))
        preview.addButtonWithTitle_("Add")
        preview.addButtonWithTitle_("Cancel")
        return coordinates if int(preview.runModal()) == 1000 else None

    @objc.IBAction
    def newTrack_(self, _sender):
        self.create_empty_track()

    @objc.IBAction
    def addPointsFromClipboard_(self, _sender):
        selected = self.selected_tracks()
        if len(selected) != 1:
            show_alert("Select one destination track.")
            return
        coordinates = self.prompt_for_coordinate_text()
        if not coordinates:
            return
        inspector = self.open_inspector_for_track(selected[0])
        inspector.insert_parsed_coordinates(coordinates)

    @objc.IBAction
    def addTracks_(self, _sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setAllowedFileTypes_(["gpx"])
        if panel.runModal():
            paths = [Path(str(url.path())).resolve() for url in panel.URLs()]
            self.load_gpx_paths(paths)

    @objc.python_method
    def _expanded_media_inputs(self, selected):
        from GetGeoLocations import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
        from media_metadata_service import media_paths_from_control_file
        from video_audio_normalization import GENERATED_DIRECTORY_NAMES

        media_extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
        excluded_names = set(GENERATED_DIRECTORY_NAMES) | {"__pycache__"}
        media_paths = []
        for selected_path in (
            Path(path).expanduser().resolve(strict=False) for path in selected
        ):
            if selected_path.is_file() and selected_path.suffix.casefold() == ".lst":
                try:
                    media_paths.extend(
                        media_paths_from_control_file(selected_path, media_extensions)
                    )
                except OSError as exc:
                    show_alert("Could not read the control file.", str(exc))
                continue
            candidates = selected_path.rglob("*") if selected_path.is_dir() else [selected_path]
            for candidate in candidates:
                if (
                    candidate.is_file()
                    and candidate.suffix.casefold() in media_extensions
                    and not any(part.casefold() in excluded_names for part in candidate.parts)
                ):
                    media_paths.append(candidate.resolve(strict=False))
        return sorted(set(media_paths), key=lambda path: str(path).casefold())

    @objc.python_method
    def _fully_untimed_route(self, track):
        """Offer automatic timing only when no point has a recorded time."""
        points = track.points() if track is not None else []
        return bool(points) and all(point.time is None for point in points)

    def refresh_timing_tools(self):
        tool = getattr(self, "timing_issues_tool", None)
        if tool is not None and tool.window.isVisible():
            tool.refresh()

    def timing_ranges(self, track):
        from track_time_estimation import missing_time_ranges_from_element
        key = (id(track.element), getattr(track, "timing_diagnostic_generation", 0))
        if getattr(track, "timing_ranges_key", None) != key:
            track.timing_ranges_cache = missing_time_ranges_from_element(track.element)
            track.timing_ranges_key = key
        return track.timing_ranges_cache

    def timing_tool(self, purpose):
        from cocoa_track_timing import TrackTimingTool
        attribute = "timing_issues_tool" if purpose == "issues" else "timing_estimate_tool"
        tool = getattr(self, attribute, None)
        if tool is None:
            tool = TrackTimingTool.alloc().initWithEditor_purpose_(self, purpose)
            setattr(self, attribute, tool)
        return tool

    @objc.IBAction
    def findMissingTimestamps_(self, _sender):
        self.timing_tool("issues").show()

    @objc.IBAction
    def estimateTrackTimes_(self, _sender):
        if not self.track_mutations_enabled:
            self.set_status("Loading track data — editing will be available when processing finishes.")
            return
        self.timing_tool("estimate").show()

    @objc.IBAction
    def estimateRouteTimesFromMedia_(self, _sender):
        self.estimateTrackTimes_(None)

    def inspect_missing_time_range(self, number, first, last):
        track = next((track for track in self.tracks if track.nr == number), None)
        if track is None:
            self.refresh_timing_tools()
            return
        self.select_track_in_table(number)
        inspector = self.open_inspector_for_track(track)
        inspector.select_point_rows(range(first, last + 1))
        if inspector.plot_view is not None:
            inspector.plot_view.move_cursor_to_track_point(track, first, inspector, sync_table=False, focus_plot=False)
            self.refresh_elevation_profile_for_plot_view(inspector.plot_view)

    def edit_timing_start(self, number, row=0):
        self.inspect_missing_time_range(number, row, row)
        track = next((track for track in self.tracks if track.nr == number), None)
        inspector = self.existing_inspector_for_track(track) if track is not None else None
        if inspector is not None:
            column = inspector.table.columnWithIdentifier_(nsstring("time"))
            if column >= 0:
                inspector.table.editColumn_row_withEvent_select_(column, row, None, True)

    def _timing_suppression_context(self):
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None and hasattr(workspace, "untimed_prompt_suppressed_identities"):
            return (set(workspace.untimed_prompt_suppressed_identities()),
                    workspace.remember_untimed_prompt_suppressions,
                    getattr(workspace, "forget_untimed_prompt_suppressions", None))
        context = getattr(self, "media_context", {})
        if ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD in context:
            return (set(context[ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD]),
                    context.get("remember_untimed_prompt_suppressions"),
                    context.get("forget_untimed_prompt_suppressions"))
        return None

    def _standalone_timing_key(self, track):
        source = self.last_save_path or track.source_file
        return str(Path(source).expanduser().resolve(strict=False)) if source else f"untitled:{id(self)}"

    def timing_prompt_suppressed(self, track):
        identity = raw_track_geometry_fingerprint(track.element)
        context = self._timing_suppression_context()
        if context is not None:
            return identity in context[0]
        values = NSUserDefaults.standardUserDefaults().dictionaryForKey_(UNTIMED_PROMPT_SUPPRESSION_KEY + ".documents.v2") or {}
        return identity in (values.get(self._standalone_timing_key(track), []) or [])

    def update_timing_suppressions(self, numbers, *, suppress):
        tracks = [track for track in self.tracks if track.nr in set(numbers)]
        identities = [raw_track_geometry_fingerprint(track.element) for track in tracks]
        context = self._timing_suppression_context()
        if context is not None:
            callback = context[1] if suppress else context[2]
            if callable(callback):
                callback(identities)
            return
        defaults = NSUserDefaults.standardUserDefaults()
        key = UNTIMED_PROMPT_SUPPRESSION_KEY + ".documents.v2"
        values = dict(defaults.dictionaryForKey_(key) or {})
        for track, identity in zip(tracks, identities):
            document = self._standalone_timing_key(track)
            stored = set(values.get(document, []))
            stored.add(identity) if suppress else stored.discard(identity)
            values[document] = sorted(stored)[-UNTIMED_PROMPT_SUPPRESSION_LIMIT:]
        defaults.setObject_forKey_(values, key)

    def timestamp_states(self, indexes_by_track):
        from gpx_point_editing import MYCAMINO_NAMESPACE
        values = {}
        for track in self.tracks:
            if track.nr not in indexes_by_track:
                continue
            points = list(iter_track_points(track.element))
            entries = []
            for index in indexes_by_track[track.nr]:
                point = points[index]
                timestamp = point.find("gpx:time", NS)
                origin = next(point.iter(f"{{{MYCAMINO_NAMESPACE}}}timeOrigin"), None)
                extensions = point.find("gpx:extensions", NS)
                entries.append((index, ET.tostring(timestamp) if timestamp is not None else None,
                    ET.tostring(origin) if origin is not None else None,
                    list(point).index(timestamp) if timestamp is not None else len(point),
                    extensions is not None,
                    list(extensions).index(origin) if extensions is not None and origin is not None else 0))
            values[track.nr] = tuple(entries)
        return TimestampUndoEntry(values)

    def finish_timestamp_change(self, numbers, message):
        for track in self.tracks:
            if track.nr in numbers:
                geometry, cached = track.display_geometry, dict(track.display_geometry_cache)
                self.invalidate_track_metrics(track)
                track.display_geometry, track.display_geometry_cache = geometry, cached
        self.mark_dirty(message, MutationKind.TIMING, numbers)
        for track in self.tracks:
            if track.nr in numbers:
                inspector = self.existing_inspector_for_track(track)
                if inspector is not None:
                    inspector.reload_rows()
        self.redraw_open_plot_views()

    def restore_timestamp_states(self, entry):
        from gpx_point_editing import MYCAMINO_NAMESPACE
        inverse = self.timestamp_states({number: [row[0] for row in rows] for number, rows in entry.values.items()})
        geometry = {track.nr: raw_track_geometry_fingerprint(track.element) for track in self.tracks if track.nr in entry.values}
        if (entry.expected is not None and inverse.values != entry.expected) or (entry.geometry is not None and geometry != entry.geometry):
            raise ValueError("Undo the newer waypoint edits first; the timing transaction was left unchanged.")
        for track in self.tracks:
            points = list(iter_track_points(track.element))
            for index, timestamp, origin, time_position, had_extensions, origin_position in entry.values.get(track.nr, ()):
                point = points[index]
                current_time = point.find("gpx:time", NS)
                if current_time is not None:
                    point.remove(current_time)
                for parent in point.iter():
                    for child in list(parent):
                        if child.tag == f"{{{MYCAMINO_NAMESPACE}}}timeOrigin":
                            parent.remove(child)
                if timestamp is not None:
                    point.insert(time_position, ET.fromstring(timestamp))
                if origin is not None:
                    extensions = point.find("gpx:extensions", NS)
                    if extensions is None:
                        extensions = ET.SubElement(point, qname("extensions"))
                    extensions.insert(origin_position, ET.fromstring(origin))
                else:
                    extensions = point.find("gpx:extensions", NS)
                    if not had_extensions and extensions is not None and not len(extensions) and not extensions.attrib and not (extensions.text or "").strip():
                        point.remove(extensions)
        self.finish_timestamp_change(set(entry.values), "Restored waypoint timestamps.")
        return TimestampUndoEntry(inverse.values, entry.values, geometry)

    def apply_timing_results(self, snapshots, results, settings):
        if not getattr(self, "track_mutations_enabled", True):
            self.set_status("Track loading is still active. Analyze again after it finishes.")
            return False
        tracks = {track.nr: track for track in self.tracks}
        if settings != self.project_parameters or any(
                number not in tracks or ET.tostring(tracks[number].element) != xml for number, xml, _label, _hidden in snapshots):
            self.set_status("Track data or settings changed during analysis. Analyze again before applying timestamps.")
            return False
        indexes = {number: [patch.index for patch in proposal.patches]
                   for number, proposal in results.items() if proposal.patches}
        if not indexes:
            return False
        before = self.timestamp_states(indexes)
        for number in indexes:
            points = list(iter_track_points(tracks[number].element))
            for patch in results[number].patches:
                get_or_create_point_time(points[patch.index]).text = format_gpx_time(patch.time)
                set_point_time_origin(points[patch.index], "generated", patch.method)
        after = self.timestamp_states(indexes)
        entry = TimestampUndoEntry(before.values, after.values,
            {number: raw_track_geometry_fingerprint(tracks[number].element) for number in indexes})
        self.undo_stack.append(entry)
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()
        for number in indexes:
            inspector = self.existing_inspector_for_track(tracks[number])
            if inspector is not None:
                inspector.undo_stack.append(entry)
                inspector.redo_stack.clear()
        self.finish_timestamp_change(set(indexes), f"Estimated {sum(len(rows) for rows in indexes.values())} missing/generated waypoint timestamp(s).")
        return True

    @objc.python_method
    def _media_timing_eligible_route(self, track):
        """Any non-empty selected route may be explicitly retimed by its user."""
        return bool(track is not None and track.points())

    @objc.python_method
    def _route_timing_proposal(self, track, prepared, *, allow_reverse=True):
        raw_points = extract_raw_track_points(track.element)
        route = [
            RoutePoint(point.lat, point.lon, point.segment_index)
            for point in raw_points
        ]
        observations = []
        for item in prepared:
            record = item.record
            if (
                record is None
                or record.photo_datetime is None
                or record.latitude is None
                or record.longitude is None
            ):
                continue
            raw_metadata = getattr(record, "raw_metadata", {})
            source_identity = (
                raw_metadata.get("source_file_identity", {})
                if isinstance(raw_metadata, dict)
                else {}
            )
            observations.append(
                MediaObservation(
                    item.path,
                    record.photo_datetime,
                    float(record.latitude),
                    float(record.longitude),
                    str(source_identity.get("sha256", ""))
                    if isinstance(source_identity, dict)
                    else "",
                )
            )
        proposal = propose_route_timing(
            route,
            observations,
            maximum_distance_m=self.media_timing_max_distance_m,
            fallback_speed_kmh=float(
                self.project_parameters["gpx.fallback_walking_speed_kmh"]
            ),
            fixed_point_times={
                index: point.time
                for index, point in enumerate(raw_points)
                if point.time is not None
                and point.element is not None
                and point_time_origin(point.element) == "manual"
            },
            manual_start_time=manual_track_start_time(track.element),
            allow_reverse=allow_reverse,
        )
        return raw_points, observations, proposal

    @objc.python_method
    def offer_route_timing_from_prepared(
        self,
        prepared,
        track=None,
        *,
        confirmation_required=True,
    ):
        if track is None:
            selected = self.selected_tracks()
            track = selected[0] if len(selected) == 1 else None
        if not self._media_timing_eligible_route(track):
            return False
        raw_points, _observations, proposal = self._route_timing_proposal(track, prepared)
        if not proposal.anchors:
            self.set_status(
                f"No dated media were within {self.media_timing_max_distance_m:g} m "
                f"of untimed {track.display_label}; route timing was unchanged."
            )
            return False
        ignored_count = max(
            0,
            len(prepared) - proposal.duplicate_count - len(proposal.anchors),
        )
        start_text = format_datetime_local(proposal.point_times[0])
        end_text = format_datetime_local(proposal.point_times[-1])
        speed_note = (
            f"The {float(self.project_parameters['gpx.fallback_walking_speed_kmh']):g} km/h "
            "fallback is used outside the single anchor."
            if len(proposal.anchors) == 1
            else f"Extrapolation uses {proposal.extrapolation_speed_kmh:.1f} km/h."
        )
        if confirmation_required:
            alert = NSAlert.alloc().init()
            alert.setMessageText_(f"Estimate times for {track.name}?")
            alert.setInformativeText_(
                f"{len(proposal.anchors)} of {len(prepared) - proposal.duplicate_count} unique "
                "media file(s) provide monotonic "
                f"timing anchors within {self.media_timing_max_distance_m:g} m; "
                f"{ignored_count} will be ignored for timing but remain media. "
                f"{proposal.duplicate_count} identical timing observation(s) were counted once.\n\n"
                f"Estimated route: {start_text} to {end_text}. {speed_note}\n\n"
                "Only timestamps on existing route points will change. Route coordinates "
                "and segments remain untouched."
            )
            alert.addButtonWithTitle_("Estimate Route Times")
            alert.addButtonWithTitle_("Not Now")
            if int(alert.runModal()) != 1000:
                self.set_status("Route timing was left unchanged.")
                return False
        if len(raw_points) != len(proposal.point_times):
            show_alert("Could not estimate route times.", "The route changed while media were analyzed.")
            return False
        manual_start = manual_track_start_time(track.element)
        if proposal.route_reversed and manual_start is not None:
            direction_alert = NSAlert.alloc().init()
            direction_alert.setMessageText_("Media indicate the opposite route direction")
            direction_alert.setInformativeText_(
                "The manually entered Track start time can move to the newly inferred "
                "start, or myCamino can keep the current route direction and ignore "
                "media that conflict with it."
            )
            direction_alert.addButtonWithTitle_("Use Media Direction")
            direction_alert.addButtonWithTitle_("Keep Current Direction")
            direction_alert.addButtonWithTitle_("Cancel")
            direction_response = int(direction_alert.runModal())
            if direction_response == 1002:
                self.set_status("Route timing was left unchanged.")
                return False
            if direction_response == 1001:
                raw_points, _observations, proposal = self._route_timing_proposal(
                    track,
                    prepared,
                    allow_reverse=False,
                )
                if not proposal.anchors:
                    self.set_status(
                        "No media are compatible with the current direction and manual timestamps."
                    )
                    return False
        self.push_undo()
        manual_values = {
            point.element: point.time
            for point in raw_points
            if point.element is not None
            and point.time is not None
            and point_time_origin(point.element) == "manual"
        }
        if proposal.route_reversed:
            segments = list(track.element.findall("gpx:trkseg", NS))
            for segment in segments:
                segment_points = list(segment.findall("gpx:trkpt", NS))
                for point in segment_points:
                    segment.remove(point)
                for point in reversed(segment_points):
                    segment.append(point)
            for segment in segments:
                track.element.remove(segment)
            for segment in reversed(segments):
                track.element.append(segment)
            raw_points = track.points()
        for point, point_time in zip(raw_points, proposal.point_times):
            if point.element is not None:
                get_or_create_point_time(point.element).text = format_gpx_time(point_time)
                set_point_time_origin(point.element, "generated")
        for point_element, point_time in manual_values.items():
            get_or_create_point_time(point_element).text = format_gpx_time(point_time)
            set_point_time_origin(point_element, "manual")
        if raw_points and raw_points[0].element is not None:
            first_time = parse_time(
                get_or_create_point_time(raw_points[0].element).text
            )
            if first_time is not None:
                get_or_create_track_time(track.element).text = format_gpx_time(first_time)
        mark_track_media_timed(
            track.element,
            maximum_distance_m=self.media_timing_max_distance_m,
            anchor_count=len(proposal.anchors),
        )
        self.invalidate_track_metrics(track)
        self.mark_dirty(
            f"Estimated {track.display_label} timestamps from {len(proposal.anchors)} nearby media file(s)"
            + (" and aligned its direction with the media." if proposal.route_reversed else "."),
            MutationKind.GEOMETRY if proposal.route_reversed else MutationKind.TIMING,
            {track.nr},
        )
        inspector = self.existing_inspector_for_track(track)
        if inspector is not None:
            inspector.reload_rows()
        for _window, _delegate, view in self.plot_windows:
            if hasattr(view, "setNeedsDisplay_"):
                view.setNeedsDisplay_(True)
        return True

    @objc.python_method
    def offerRouteTimingForPaths_(self, payload):
        from media_metadata_service import prepare_media_records

        track_nr, raw_paths = payload
        track = next((item for item in self.tracks if item.nr == int(track_nr)), None)
        if not self._media_timing_eligible_route(track):
            return
        paths = self._expanded_media_inputs(raw_paths)
        if not paths:
            self.set_status("No supported media were available for route timing.")
            return
        self.set_status(f"Preparing {len(paths)} media file(s) for route timing...")
        prepared = prepare_media_records(paths, refresh_changed=True)
        self.offer_route_timing_from_prepared(prepared, track)

    @objc.IBAction
    def rebuildRouteTimesFromMedia_(self, _sender):
        selected_tracks = self.selected_tracks()
        if len(selected_tracks) != 1 or not self._media_timing_eligible_route(selected_tracks[0]):
            show_alert("Select one untimed or previously media-timed route.")
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(True)
        panel.setCanCreateDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setPrompt_("Use Media for Timing")
        project_directory = self.media_context.get("project_directory")
        if project_directory:
            candidate = Path(project_directory).expanduser().resolve(strict=False)
            if candidate.is_dir():
                panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(candidate)))
        if not panel.runModal():
            return
        paths = [Path(str(url.path())).resolve(strict=False) for url in panel.URLs()]
        self.offerRouteTimingForPaths_((selected_tracks[0].nr, paths))

    def addDroppedMediaPaths_(self, paths):
        """Review media or a control file dropped onto the active track map."""
        if self.track_load_timer is not None:
            self.pending_route_timing_media_after_load.extend(
                Path(path).expanduser().resolve(strict=False) for path in paths or []
            )
            self.set_status("Media will be checked against the route after GPX loading finishes.")
            return
        selected = self.selected_tracks()
        if len(selected) == 1 and self._media_timing_eligible_route(selected[0]):
            self.offerRouteTimingForPaths_((selected[0].nr, list(paths or [])))
            return
        self.media_context["_pending_media_paths"] = [str(path) for path in paths or []]
        self.addTracksFromPhotos_(None)

    @objc.IBAction
    def addTracksFromPhotos_(self, _sender):
        from media_metadata_service import prepare_media_records
        from track_timing_utils import repair_timed_points

        pending_paths = self.media_context.pop("_pending_media_paths", None)
        if pending_paths:
            selected = [Path(path).expanduser().resolve(strict=False) for path in pending_paths]
        else:
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(True)
            panel.setAllowsMultipleSelection_(True)
            panel.setPrompt_("Add Media Points")
            media_directory = self.media_context.get("project_directory")
            if media_directory:
                candidate = Path(media_directory).expanduser().resolve(strict=False)
                if candidate.is_dir():
                    panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(candidate)))
            if not panel.runModal():
                return
            selected = [Path(str(url.path())).expanduser().resolve() for url in panel.URLs()]
        media_paths = self._expanded_media_inputs(selected)
        if not media_paths:
            show_alert("No supported photos or videos were selected.")
            return
        skip_selection = bool(self.media_context.pop("_skip_media_selection_once", False))
        if not skip_selection:
            from cocoa_media_point_selector import choose_media_points

            media_paths = choose_media_points(media_paths)
            if media_paths is None:
                return
            if not media_paths:
                show_alert("No media selected.", "Check at least one file before continuing.")
                return
        force_new_track = bool(self.media_context.pop("_force_new_media_track_once", False))
        destination = [self.create_empty_track(name="Track from Media")] if force_new_track else self.selected_tracks()
        if len(destination) != 1:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Choose a destination track.")
            alert.setInformativeText_(
                "Media points require exactly one selected track. You can create a new empty track now, "
                "or cancel and select an existing track."
            )
            alert.addButtonWithTitle_("Create New Track")
            alert.addButtonWithTitle_("Cancel")
            if int(alert.runModal()) != 1000:
                return
            destination = [self.create_empty_track()]
        track = destination[0]
        self.set_status(f"Preparing {len(media_paths)} media file(s) for {track.display_label}...")
        prepared = prepare_media_records(media_paths, refresh_changed=True)
        usable = [item for item in prepared if item.record is not None and item.record.latitude is not None and item.record.longitude is not None]
        skipped = [f"{item.path.name}: {item.error or 'no GPS coordinates'}" for item in prepared if item not in usable]
        if not usable:
            show_alert(
                "No media points could be added.",
                "\n".join(skipped[:12]) or "The selected media contain no usable GPS coordinates.",
            )
            return
        usable.sort(key=lambda item: (
            item.record.photo_datetime is None,
            item.record.photo_datetime.timestamp() if item.record.photo_datetime is not None else float("inf"),
            item.path.name.casefold(),
        ))
        existing_points = track.points()
        existing_untimed = [point for point in existing_points if point.time is None]
        estimate_times = False
        fallback_speed = 3.5
        route_matches = None
        if existing_untimed and any(item.record.photo_datetime is not None for item in usable):
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Review Media Point Timing")
            points_preview = [
                point_from_values(
                    track.element,
                    item.record.latitude,
                    item.record.longitude,
                    timestamp=item.record.photo_datetime,
                )
                for item in usable
            ]
            if existing_points and len(existing_untimed) == len(existing_points):
                route_matches = monotonic_route_matches(track.element, points_preview)
            match_text = ""
            if route_matches:
                distances = [distance for _row, distance in route_matches]
                match_text = (
                    f"\nClosest-route matching: mean {sum(distances) / len(distances):.0f} m, "
                    f"maximum {max(distances):.0f} m."
                )
            alert.setInformativeText_(
                f"{len(usable)} located media point(s) will be merged by exposure time. "
                f"The track also has {len(existing_untimed)} untimed waypoint(s).\n\n"
                "Choose Add and Estimate to fill those timestamps by route distance, preserving every existing timestamp. "
                "Edit the fallback speed below when the photo anchors do not determine one."
                f"{match_text}"
            )
            speed_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 180, FIELD_HEIGHT))
            speed_field.setStringValue_("3.5")
            speed_field.setPlaceholderString_("Fallback speed in km/h")
            alert.setAccessoryView_(speed_field)
            alert.addButtonWithTitle_("Add and Estimate")
            alert.addButtonWithTitle_("Add Without Estimating")
            alert.addButtonWithTitle_("Cancel")
            response = int(alert.runModal())
            if response == 1002:
                return
            estimate_times = response == 1000
            if estimate_times:
                try:
                    fallback_speed = float(str(speed_field.stringValue()).replace(",", "."))
                    if fallback_speed <= 0:
                        raise ValueError
                except ValueError:
                    show_alert("Invalid fallback speed.", "Enter a positive speed in km/h.")
                    return
        points = [
            point_from_values(
                track.element,
                item.record.latitude,
                item.record.longitude,
                timestamp=item.record.photo_datetime,
                name=item.path.name,
                media_path=item.path,
            )
            for item in usable
        ]
        self.push_undo()
        if estimate_times and route_matches and len(existing_untimed) == len(existing_points):
            inserted_rows = insert_points_at_route_matches(track.element, points, route_matches)
        else:
            inserted_rows = insert_points_chronologically(track.element, points)
        mark_track_media_derived(track.element)
        if estimate_times:
            timing_input = [
                {"lat": point.lat, "lon": point.lon, "time": point.time, "segment_index": point.segment_index}
                for point in track.points()
            ]
            repaired = repair_timed_points(timing_input, fallback_speed_kmh=fallback_speed)
            for point, proposal in zip(track.points(), repaired):
                if point.time is None and proposal.get("time") is not None:
                    get_or_create_point_time(point.element).text = format_gpx_time(proposal["time"])
        self.invalidate_track_metrics(track)
        self.mark_dirty(
            f"Added {len(inserted_rows)} media point(s) to {track.display_label}; "
            f"reused {sum(item.action == 'reused' for item in usable)}, "
            f"extracted {sum(item.action == 'extracted' for item in usable)}, skipped {len(skipped)}.",
            MutationKind.STRUCTURE,
            {track.nr},
        )
        inspector = self.existing_inspector_for_track(track)
        if inspector is not None:
            inspector.reload_rows()
            inspector.select_point_rows(inserted_rows)
        view = self.open_or_reload_track_plot_for_track(track)
        if view is not None:
            view.render_extent(
                self.fit_extent_to_aspect(
                    self.extent_for_point_infos([track.points()[row] for row in inserted_rows]),
                    (1000, 720),
                ),
                status="Fitted the map to the inserted media points.",
            )

    def _append_gpx_track_records(
        self, paths: list[Path], prepared_sources: list[dict] | None = None
    ):
        """Parse GPX files and append unprocessed records, returning load details."""
        added_records = []
        metrics_cache_hits = 0
        removed_invalid_coordinates = 0
        removed_invalid_timestamps = 0
        removed_out_of_order = 0
        missing_timestamps = 0
        converted_routes = 0
        converted_waypoint_tracks = 0
        reassigned_order_numbers = 0
        used_numbers = {track.track_number for track in self.tracks if track.track_number is not None}
        next_available_number = max(used_numbers, default=0) + 1
        used_ids = {track.nr for track in self.tracks}
        adopt_document_ids = not self.tracks and not getattr(self, "document_ids_initialized", False)
        self.last_load_dir = paths[0].parent
        if prepared_sources is None:
            prepared_sources, errors = prepare_gpx_sources_for_load(
                paths, self.processing_options()
            )
            for path, message in errors:
                show_alert("Could not read GPX file.", f"{path}\n\n{message}")
        for prepared in prepared_sources:
            path = Path(prepared["path"])
            document = prepared["document"]
            ids, next_id = allocate_track_ids(document.tracks,
                max(self.next_nr, getattr(document, "next_internal_id", 1)) if adopt_document_ids else self.next_nr,
                preserve=adopt_document_ids, used=used_ids,
                legacy=getattr(document, "next_internal_id", 1) == 1)
            self.next_nr = max(self.next_nr, next_id)
            self.document_ids_initialized = True
            adopt_document_ids = False
            if not self.tracks and not self.project_name:
                (
                    self.project_name,
                    self.project_name_needs_metadata_repair,
                ) = resolved_gpx_document_name(
                    document,
                    path,
                    self.project_name_fallback,
                )
                self.project_field.setStringValue_(self.project_name)
                if self.last_save_path is None and not str(self.output_field.stringValue()).strip():
                    self.set_output_path(path.with_suffix(".gpx").resolve())
            converted_routes += document.report.converted_routes
            converted_waypoint_tracks += document.report.converted_waypoint_tracks
            removed_invalid_coordinates += document.report.invalid_coordinates
            removed_invalid_timestamps += document.report.invalid_timestamps
            removed_out_of_order += document.report.backward_timestamps
            missing_timestamps += document.report.missing_timestamps
            cached_tracks = prepared["cached_tracks"]
            for source_index, trk in enumerate(document.tracks):
                # load_gpx_document returns detached normalized elements for
                # converted inputs and owned editable elements for GPX 1.1.
                stored_number = track_number(trk, source_index + 1)
                if stored_number is None:
                    assigned_number = None
                elif stored_number not in used_numbers:
                    assigned_number = stored_number
                else:
                    while next_available_number in used_numbers:
                        next_available_number += 1
                    assigned_number = next_available_number
                    next_available_number += 1
                    if stored_number is not None or has_stored_track_order_number(trk):
                        reassigned_order_numbers += 1
                if assigned_number is not None:
                    used_numbers.add(assigned_number)
                record = TrackRecord(
                    ids[source_index],
                    trk,
                    str(path),
                    source_track_index=source_index,
                )
                if "timing_gaps" in prepared:
                    record.timing_ranges_cache = prepared["timing_gaps"][source_index]
                    record.timing_ranges_key = (id(trk), 0)
                record.set_order_number(assigned_number)
                used_ids.add(record.nr)
                fingerprint, cached_metrics, cached_display_geometry = cached_tracks[
                    source_index
                ]
                record.processed_fingerprint = fingerprint
                record.display_geometry = cached_display_geometry
                if cached_metrics is not None:
                    record.metrics = cached_metrics
                    record.metrics_dirty = False
                    metrics_cache_hits += 1
                self.tracks.append(record)
                added_records.append(record)
        ignored_parts = []
        if removed_invalid_coordinates:
            ignored_parts.append(f"{removed_invalid_coordinates} point(s) with invalid coordinates")
        if removed_invalid_timestamps:
            ignored_parts.append(f"{removed_invalid_timestamps} point(s) with invalid timestamps")
        if removed_out_of_order:
            ignored_parts.append(f"{removed_out_of_order} out-of-order timestamp(s)")
        if converted_routes:
            ignored_parts.append(f"converted {converted_routes} route(s)")
        if converted_waypoint_tracks:
            ignored_parts.append(
                f"connected {converted_waypoint_tracks} waypoint collection(s)"
            )
        if missing_timestamps:
            ignored_parts.append(
                f"kept {missing_timestamps} point(s) without timestamps"
            )
        if metrics_cache_hits:
            ignored_parts.append(f"reused cached metrics for {metrics_cache_hits} track(s)")
        if reassigned_order_numbers:
            ignored_parts.append(
                f"reassigned {reassigned_order_numbers} duplicate or invalid track number(s)"
            )
        ignored_suffix = f" Import details: {', '.join(ignored_parts)}." if ignored_parts else ""
        return added_records, ignored_suffix

    def write_metrics_caches_for_records(self, records: Iterable[TrackRecord]):
        """Persist complete source-file groups with compact display geometry."""
        by_source: dict[str, list[TrackRecord]] = defaultdict(list)
        for track in records:
            if track.source_file:
                by_source[track.source_file].append(track)
        for source_file, source_tracks in by_source.items():
            source = Path(source_file)
            if not source.is_file():
                continue
            source_tracks.sort(key=lambda item: item.source_track_index)
            entries = []
            for track in source_tracks:
                if not track.metrics or track.metrics_dirty:
                    break
                fingerprint = track.processed_fingerprint
                if fingerprint is None:
                    fingerprint = processing_input_fingerprint(track.element)
                    track.processed_fingerprint = fingerprint
                if track.display_geometry is None:
                    processed = self.display_processed_track(track)
                    track.display_geometry = display_geometry_for_processed_track(
                        processed
                    )
                entries.append(
                    {
                        "source_index": track.source_track_index,
                        "fingerprint": fingerprint,
                        "metrics": track.metrics,
                        "display_geometry": track.display_geometry,
                    }
                )
            else:
                self.metrics_cache_executor.submit(
                    write_metrics_cache,
                    source,
                    self.processing_options(),
                    copy.deepcopy(entries),
                )

    def load_gpx_paths(
        self,
        paths: list[Path],
        mark_dirty: bool = True,
        offer_sort_after_drop: bool = False,
    ):
        """Parse and process GPX sources without blocking AppKit."""
        if not paths:
            return
        if self.track_load_timer is not None:
            show_alert("GPX files are already loading.", "Wait until the current files have finished loading.")
            return
        if mark_dirty:
            self.push_undo()
        self.set_track_loading_controls_enabled(False)
        self.show_indeterminate_track_load_progress()
        self.set_status(f"Reading {len(paths)} GPX file(s)...")
        self.pending_track_parse_request = {
            "paths": list(paths),
            "mark_dirty": bool(mark_dirty),
            "offer_sort": bool(offer_sort_after_drop),
            "start_index": len(self.tracks),
        }
        self.pending_track_parse_future = self.track_load_executor.submit(
            prepare_gpx_sources_for_load,
            list(paths),
            self.processing_options(),
        )
        self.track_load_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.02,
            self,
            "pollTrackSources:",
            None,
            True,
        )

    @objc.IBAction
    def pollTrackSources_(self, timer):
        future = self.pending_track_parse_future
        if future is None or not future.done():
            return
        timer.invalidate()
        self.track_load_timer = None
        self.pending_track_parse_future = None
        request = self.pending_track_parse_request or {}
        self.pending_track_parse_request = None
        try:
            prepared_sources, errors = future.result()
        except Exception as exc:
            self.set_track_loading_controls_enabled(True)
            self.hide_track_load_progress()
            show_alert("Could not read GPX files.", str(exc))
            self.set_status("GPX loading failed.")
            self.notify_initial_load_complete()
            return
        for path, message in errors:
            show_alert("Could not read GPX file.", f"{path}\n\n{message}")
        paths = list(request.get("paths") or [])
        start_index = int(request.get("start_index", len(self.tracks)))
        added_records, ignored_suffix = self._append_gpx_track_records(
            paths, prepared_sources
        )
        if not added_records:
            self.set_track_loading_controls_enabled(True)
            self.hide_track_load_progress()
            self.set_status("No tracks were loaded.")
            self.notify_initial_load_complete()
            return
        self.pending_track_loads = added_records
        self.pending_track_load_index = 0
        self.pending_track_load_total = len(added_records)
        self.pending_track_load_mark_dirty = bool(request.get("mark_dirty"))
        self.pending_track_load_path_count = len(paths)
        self.pending_track_load_status = ignored_suffix
        self.pending_track_load_start_index = start_index
        self.pending_track_load_offer_sort = bool(request.get("offer_sort"))
        self.pending_track_load_anchor_was_set = self.anchor is not None
        self.install_loading_table_rows()
        self.show_track_load_progress(0, self.pending_track_load_total)
        self.status_label.setStringValue_(f"Loading track 0 of {self.pending_track_load_total}...")
        if all(
            track.metrics
            and not track.metrics_dirty
            and track.display_geometry is not None
            for track in self.pending_track_loads
        ):
            self.pending_track_load_index = self.pending_track_load_total
            self._finish_staged_track_loading()
            return
        self.track_load_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.02,
            self,
            "loadNextTrack:",
            None,
            True,
        )

    def loadDroppedGPXPaths_(self, raw_paths):
        """Begin a file drop after AppKit has finished its drag session."""
        paths = [
            Path(str(path)).expanduser().resolve(strict=False)
            for path in raw_paths or []
        ]
        self.load_gpx_paths(
            paths,
            mark_dirty=True,
            offer_sort_after_drop=True,
        )

    @objc.IBAction
    def loadNextTrack_(self, timer):
        if self.editor_close_finalized:
            timer.invalidate()
            return
        index = self.pending_track_load_index
        total = self.pending_track_load_total
        if index >= total:
            self._finish_staged_track_loading()
            return
        track = self.pending_track_loads[index]
        try:
            needs_processing = (
                not track.metrics
                or track.metrics_dirty
                or track.display_geometry is None
            )
            if needs_processing and self.pending_track_load_future is None:
                self.pending_track_load_future = self.track_load_executor.submit(
                    process_track_for_staged_load,
                    track.element,
                    self.processing_options(),
                    track.processed_fingerprint,
                )
                return
            if self.pending_track_load_future is not None:
                if not self.pending_track_load_future.done():
                    return
                fingerprint, processed, display_geometry = (
                    self.pending_track_load_future.result()
                )
                self.pending_track_load_future = None
                track.processed_fingerprint = fingerprint
                track.processed = processed
                track.processed_signature = self.processing_parameter_signature()
                track.display_geometry = display_geometry
                track.display_geometry_cache.clear()
                track.metrics_dirty = True
            self.compute_metrics(track)
        except Exception as exc:
            timer.invalidate()
            self.track_load_timer = None
            self.pending_track_load_future = None
            del self.tracks[self.pending_track_load_start_index :]
            self.next_nr = max(self.next_nr, max([record.nr for record in self.tracks], default=0) + 1)
            self.pending_track_loads = []
            self.pending_track_load_offer_sort = False
            self.pending_pdf_export_after_load = False
            self.set_track_loading_controls_enabled(True)
            self.hide_track_load_progress()
            show_alert("Could not process GPX tracks.", str(exc))
            self.set_status("GPX loading failed.")
            self.notify_initial_load_complete()
            return
        self.pending_track_load_index += 1
        current = self.pending_track_load_index
        self.load_progress.setDoubleValue_(float(current))
        self.status_label.setStringValue_(f"Loading track {current} of {total}: {track.name}")
        self.status_label.displayIfNeeded()
        self.load_progress.displayIfNeeded()
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None and hasattr(workspace, "workspace_track_loading_progress"):
            workspace.workspace_track_loading_progress(current, total)
        if current >= total:
            self._finish_staged_track_loading()

    def _finish_staged_track_loading(self):
        if self.track_load_timer is not None:
            self.track_load_timer.invalidate()
            self.track_load_timer = None
        added = self.pending_track_load_total
        path_count = self.pending_track_load_path_count
        ignored_suffix = self.pending_track_load_status
        mark_dirty = self.pending_track_load_mark_dirty
        loaded_records = list(self.pending_track_loads)
        offer_sort = self.pending_track_load_offer_sort
        anchor_was_set = self.pending_track_load_anchor_was_set
        self.pending_track_loads = []
        self.pending_track_load_index = 0
        self.pending_track_load_total = 0
        self.pending_track_load_offer_sort = False
        try:
            self.recalculate()
        except Exception as exc:
            self.set_track_loading_controls_enabled(True)
            self.hide_track_load_progress()
            show_alert("Could not finish loading GPX tracks.", str(exc))
            self.set_status("GPX loading failed while preparing the track table.")
            self.notify_initial_load_complete()
            return
        self.dirty = bool(mark_dirty)
        self.write_metrics_caches_for_records(loaded_records)
        self.set_track_loading_controls_enabled(True)
        self.hide_track_load_progress()
        action = "Added" if mark_dirty else "Loaded"
        status = f"{action} {added} track(s) from {path_count} GPX file(s).{ignored_suffix}"
        self.set_status(status)
        if mark_dirty:
            self._notify_workspace_mutation(
                MutationKind.STRUCTURE,
                status,
                [track.nr for track in loaded_records],
            )
        if offer_sort and len(loaded_records) > 1:
            self.offer_sort_dropped_tracks(loaded_records, anchor_was_set)
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if mark_dirty and workspace is not None and hasattr(workspace, "workspace_tracks_did_load"):
            workspace.workspace_tracks_did_load(loaded_records)
        if self.pending_route_timing_media_after_load:
            pending_media = list(dict.fromkeys(self.pending_route_timing_media_after_load))
            self.pending_route_timing_media_after_load = []
            untimed = [track for track in loaded_records if self._fully_untimed_route(track)]
            if len(untimed) == 1:
                self.offerRouteTimingForPaths_((untimed[0].nr, pending_media))
        self.notify_initial_load_complete()
        if not self.pdf_only_loading:
            self.offer_track_time_entry()
        if self.pending_pdf_export_after_load:
            self.pending_pdf_export_after_load = False
            self.exportPdf_(None)

    def offer_sort_dropped_tracks(
        self,
        loaded_records: list[TrackRecord],
        anchor_was_set: bool,
    ):
        """Offer a chronological or anchor-distance order for a dropped track block."""
        loaded_records = [track for track in loaded_records if track in self.tracks]
        if len(loaded_records) < 2:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Sort {len(loaded_records)} Dropped Tracks?")
        alert.setInformativeText_(
            "Sort the newly added tracks by their recorded date and time, or by "
            "their distance from the current anchorpoint. If no anchorpoint was "
            "set, distance sorting uses the first point of the first dropped track."
        )
        alert.addButtonWithTitle_("Sort by Time")
        alert.addButtonWithTitle_("Sort by Distance")
        alert.addButtonWithTitle_("Keep Current Order")
        response = int(alert.runModal())
        if response not in {1000, 1001}:
            return
        self.apply_dropped_track_sort(
            loaded_records,
            "date" if response == 1000 else "distance",
            anchor_was_set,
        )

    def apply_dropped_track_sort(
        self,
        loaded_records: list[TrackRecord],
        criterion: str,
        anchor_was_set: bool,
    ):
        """Sort newly dropped tracks in their occupied rows without another undo step."""
        loaded_records = [track for track in loaded_records if track in self.tracks]
        if len(loaded_records) < 2 or criterion not in {"date", "distance"}:
            return
        if criterion == "distance" and not anchor_was_set:
            points = loaded_records[0].points()
            if not points:
                show_alert(
                    "The dropped tracks cannot be sorted by distance.",
                    "The first dropped track has no track points from which to set an anchorpoint.",
                )
                return
            self.anchor = (points[0].lat, points[0].lon)
        self.selected_nrs = [track.nr for track in loaded_records]
        self.update_selection_field()
        self.highlight_selected_rows()
        self.sort_by_column(
            criterion,
            ascending=True,
            source="dropped tracks",
            push_undo_step=False,
        )

    def offer_track_time_entry(self):
        """One nonmodal offer covers both partial and completely untimed tracks."""
        pending = [track.nr for track in self.tracks
                   if self.timing_ranges(track) and not self.timing_prompt_suppressed(track)]
        if pending:
            self.timing_tool("issues").show(pending)

    def _load_gpx_paths_synchronously(self, paths: list[Path], mark_dirty: bool = True):
        """Retain synchronous loading only for the exceptional recovery-file path."""
        if not paths:
            return
        if mark_dirty:
            self.push_undo()
        records, ignored_suffix = self._append_gpx_track_records(paths)
        self.recalculate()
        self.write_metrics_caches_for_records(records)
        self.dirty = bool(mark_dirty)
        action = "Added" if mark_dirty else "Loaded"
        self.set_status(f"{action} {len(records)} track(s) from {len(paths)} GPX file(s).{ignored_suffix}")

    def confirm_save_before_view_file(self) -> bool:
        if not self.dirty:
            return True
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Save changes before opening the source GPX file?")
        alert.setInformativeText_("The selected track has unsaved edits in the editor.")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Don't Save")
        alert.addButtonWithTitle_("Cancel")
        result = alert.runModal()
        if result == 1000:
            return self.save_current()
        if result == 1001:
            return True
        return False

    @objc.IBAction
    def save_(self, _sender):
        self.save_current()

    @objc.IBAction
    def saveAs_(self, _sender):
        self.save_with_panel()

    @objc.IBAction
    def saveAndExit_(self, _sender):
        if not self.save_current():
            return
        self.quit_confirmed = True
        self.close_main_editor_window(delete_recovery=True)

    @objc.IBAction
    def viewFile_(self, _sender):
        selected = self.selected_tracks()
        if not selected:
            show_alert("Select a track first.")
            return
        track = selected[0]
        source_path = Path(track.source_file).expanduser() if track.source_file else None
        if source_path is None or not str(source_path).strip():
            show_alert("No source GPX file is recorded for this track.")
            return
        if not source_path.exists():
            show_alert("Source GPX file not found.", str(source_path))
            return
        if not self.confirm_save_before_view_file():
            return
        workspace = NSWorkspace.sharedWorkspace()
        opened = workspace.openFile_withApplication_(str(source_path), "TextEdit")
        if not opened:
            opened = workspace.openFile_(str(source_path))
        if not opened:
            show_alert("Could not open source GPX file.", str(source_path))
            return
        self.set_status(f"Opened source GPX file: {source_path}")

    @objc.IBAction
    def savePng_(self, _sender):
        if not self.tracks:
            show_alert("No tracks to plot.")
            return
        existing = self.existing_plot_view("overview")
        if existing is None:
            view = self.open_plot_window("overview", recreate_existing=False)
        else:
            _window, view = existing
        if view is not None:
            self.save_plot_view_png(view)

    def default_png_directory(self) -> Path:
        if self.last_png_dir is not None:
            return self.last_png_dir
        if self.last_save_path is not None:
            return self.last_save_path.parent
        if self.last_load_dir is not None:
            return self.last_load_dir
        return Path.cwd()

    def save_plot_view_png(self, view: PlotView):
        if view.mode == "track" and len(view.track_sequence()) > 1:
            directory_panel = NSOpenPanel.openPanel()
            directory_panel.setCanChooseFiles_(False)
            directory_panel.setCanChooseDirectories_(True)
            directory_panel.setAllowsMultipleSelection_(False)
            directory_panel.setCanCreateDirectories_(True)
            directory_panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.default_png_directory())))
            directory_panel.setTitle_("Choose folder for track PNG files")
            if not file_panel_ok(directory_panel.runModal()) or directory_panel.URL() is None:
                return
            directory = Path(str(directory_panel.URL().path())).resolve()
            self.last_png_dir = directory
            saved_paths = []
            missing = 0
            for track in view.track_sequence():
                rendered = self.render_summary_plot("track", tracks_override=[track])
                info = (
                    (rendered.get("tracks") or {}).get(track.nr, rendered)
                    if rendered is not None
                    else {}
                )
                saved = self.write_png_data_to_unique_file(self.png_data_for_plot_info(info), directory, track.name)
                if saved is None:
                    missing += 1
                else:
                    saved_paths.append(saved)
            if missing:
                show_alert("PNG export incomplete.", f"{missing} track image(s) had no PNG data to write.")
            if saved_paths:
                self.set_status(f"Saved {len(saved_paths)} track PNG file(s) to {directory}: {', '.join(path.name for path in saved_paths[:3])}.")
            else:
                self.set_status("No PNG files were saved.")
            return
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["png"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.default_png_directory())))
        base = self.project_name or "overview"
        if view.mode == "track":
            track = view.current_track_for_title()
            base = track.name if track is not None else "track"
        panel.setNameFieldStringValue_(f"{self.safe_filename(base)}.png")
        if not file_panel_ok(panel.runModal()) or panel.URL() is None:
            return
        path = Path(str(panel.URL().path())).resolve()
        self.last_png_dir = path.parent
        tracks = view.display_tracks()
        rendered = self.render_summary_plot(view.mode, tracks_override=tracks)
        png_data = None
        if rendered is not None:
            if view.mode == "track" and tracks:
                png_data = self.png_data_for_plot_info(
                    (rendered.get("tracks") or {}).get(tracks[0].nr, rendered)
                )
            else:
                png_data = self.png_data_for_plot_info(rendered)
        if self.write_png_data(path, png_data):
            self.set_status(f"Saved plot PNG to {path}.")
        else:
            self.set_status("No PNG file was saved.")

    def save_elevation_profile_png(self, view: ElevationProfileView):
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["png"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.default_png_directory())))
        track = view.current_track()
        if track is not None:
            base = f"{track.name} - elevation profile"
        else:
            base = f"{self.project_name or 'overview'} - elevation profile"
        panel.setNameFieldStringValue_(f"{self.safe_filename(base)}.png")
        if not file_panel_ok(panel.runModal()) or panel.URL() is None:
            return
        path = Path(str(panel.URL().path())).resolve()
        self.last_png_dir = path.parent
        if self.write_png_data(path, self.png_data_for_plot_view(view)):
            self.set_status(f"Saved elevation profile PNG to {path}.")
        else:
            self.set_status("No elevation profile PNG file was saved.")

    def safe_filename(self, value: str) -> str:
        return re.sub(r'[/:\\]+', "_", value).strip() or "plot"

    def write_png_data_to_unique_file(self, png_data, directory: Path, basename: str):
        stem = self.safe_filename(basename)
        path = directory / f"{stem}.png"
        counter = 2
        while path.exists():
            path = directory / f"{stem} ({counter}).png"
            counter += 1
        return path if self.write_png_data(path, png_data) else None

    def write_png_data(self, path: Path, png_data):
        if png_data is None:
            show_alert("PNG export unavailable.", "The current renderer did not return PNG data.")
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png_data)
            if not path.exists() or path.stat().st_size <= 0:
                show_alert("PNG export failed.", f"No PNG data was written to:\n{path}")
                return False
        except OSError as exc:
            show_alert("PNG export failed.", f"Could not write PNG file:\n{path}\n\n{exc}")
            return False
        return True

    def png_data_for_plot_view(self, view: NSView):
        bounds = view.bounds()
        width = max(1, int(math.ceil(bounds.size.width)))
        height = max(1, int(math.ceil(bounds.size.height)))
        bitmap = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bitmapFormat_bytesPerRow_bitsPerPixel_(
            None,
            width,
            height,
            8,
            4,
            True,
            False,
            "NSCalibratedRGBColorSpace",
            0,
            0,
            0,
        )
        fallback_info = getattr(view, "plot_info", {})
        if bitmap is None:
            return self.png_data_for_plot_info(fallback_info)
        context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap)
        if context is None:
            return self.png_data_for_plot_info(fallback_info)
        previous_context = NSGraphicsContext.currentContext()
        try:
            NSGraphicsContext.setCurrentContext_(context)
            view.displayRectIgnoringOpacity_inContext_(bounds, context)
        finally:
            NSGraphicsContext.setCurrentContext_(previous_context)
        data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        return bytes(data) if data is not None else self.png_data_for_plot_info(fallback_info)

    def png_data_for_plot_info(self, info: dict):
        png_data = info.get("png_data")
        if png_data:
            return png_data
        image = info.get("image")
        if image is None:
            return None
        tiff_data = image.TIFFRepresentation()
        if tiff_data is None:
            return None
        rep = NSBitmapImageRep.imageRepWithData_(tiff_data)
        if rep is None:
            return None
        data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        if data is None:
            return None
        return bytes(data)

    @objc.IBAction
    def exportPdf_(self, _sender):
        self.open_pdf_summary_window()

    def default_pdf_path(self) -> Path:
        return self.default_pdf_directory() / f"{self.default_pdf_basename()}.pdf"

    def default_pdf_directory(self) -> Path:
        if self.last_pdf_path is not None:
            return self.last_pdf_path.parent
        if self.last_save_path is not None:
            return self.last_save_path.parent
        if self.last_load_dir is not None:
            return self.last_load_dir
        return Path.cwd()

    def default_pdf_basename(self) -> str:
        if self.last_pdf_path is not None:
            return self.last_pdf_path.stem
        if self.last_save_path is not None:
            return self.last_save_path.stem
        name = self.project_name.strip() if self.project_name else "tracks"
        return name or "tracks"

    def sync_pdf_project_settings(self):
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None:
            values = normalize_parameters(workspace.parameters)
            if values != self.project_parameters:
                self.apply_project_parameters(values)

    def open_pdf_summary_window(self):
        self.sync_pdf_project_settings()
        if self.pdf_summary_window is None:
            self.build_pdf_summary_window()
        if self.pdf_summary_poll_timer is None:
            self.pdf_summary_poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.1, self, "pollPdfSummary:", None, True
            )
        self.pollPdfSummary_(None)
        self.pdf_summary_window.makeKeyAndOrderFront_(None)
        self.pdf_summary_window.orderFrontRegardless()
        NSApp().activateIgnoringOtherApps_(True)

    def build_pdf_summary_window(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180, 180, 820, 650), style, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("PDF Summary")
        window.setContentMinSize_(NSMakeSize(800, 620))
        root = window.contentView()
        self.pdf_summary_positions = []

        def place(view, x, top, width, height=24, flexible=False):
            self.pdf_summary_positions.append((view, x, top, width, height, flexible))
            root.addSubview_(view)
            return view

        def label(text, x, top, width, size=12, bold=False, height=24, flexible=False):
            return place(self.make_label(text, size, bold, 0), x, top, width, height, flexible)

        def button(title, action, x, top, width):
            view = make_liquid_glass_button(NSMakeRect(0, 0, width, BUTTON_HEIGHT))
            view.setTitle_(title)
            view.setTarget_(self)
            view.setAction_(action)
            apply_liquid_glass_button_style(view)
            return place(view, x, top, width, BUTTON_HEIGHT)

        label("PDF Summary", 20, 16, 250, 17, True)
        gear = button("Settings", "pdfTypeSettings:", 756, 16, 40)
        self.pdf_summary_settings_button = gear
        self.configure_symbol_button(gear, "gearshape", "PDF Export Settings", "NSActionTemplate", "Settings")
        hint = label(
            "Create a printable summary of your tracks, with distances, durations, elevation statistics, "
            "and optional overview maps, individual track maps, and elevation profiles.",
            20, 48, 780, height=42, flexible=True,
        )
        hint.cell().setWraps_(True)
        hint.cell().setScrollable_(False)
        label("Tracks", 20, 102, 65)
        self.pdf_summary_scope_menu = place(NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 150, 24), False), 90, 102, 150)
        self.pdf_summary_scope_menu.addItemsWithTitles_(["Visible", "All", "Selected"])
        self.pdf_summary_scope_menu.setTarget_(self)
        self.pdf_summary_scope_menu.setAction_("pdfSummaryOptionsChanged:")
        self.pdf_summary_scope_menu.setToolTip_(
            "Choose tracks for the entire PDF. Hidden tracks are gray and excluded from totals and averages."
        )
        button("Select Tracks…", "selectPdfSummaryTracks:", 255, 102, 142)
        label("PDF file", 20, 140, 65)
        self.pdf_summary_path_field = place(NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 500, FIELD_HEIGHT)), 90, 140, 608, FIELD_HEIGHT, True)
        self.pdf_summary_path_field.setStringValue_(str(self.default_pdf_path()))
        self.pdf_summary_path_field.setToolTip_("Destination for the printable track summary.")
        self.pdf_summary_browse_button = button("Browse…", "choosePdfSummaryOutput:", 710, 140, 90)
        label("Columns", 20, 185, 140, 13, True)
        self.pdf_summary_checkboxes = {}
        report_columns = [column for column in self.columns if column[0] not in {"nr", "stage", "type"}]
        for index, (identifier, title, _width, _editable) in enumerate(report_columns):
            checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 330, 24))
            checkbox.setButtonType_(NSButtonTypeSwitch)
            checkbox.setTitle_("Track" if identifier == "track_id" else title.replace("\n", " ").strip())
            checkbox.setState_(NSControlStateValueOn if identifier in DEFAULT_PDF_COLUMNS else NSControlStateValueOff)
            place(checkbox, 20 + (index % 2) * 365, 214 + (index // 2) * 26, 330)
            self.pdf_summary_checkboxes[identifier] = checkbox
        label("Remove name prefix", 20, 409, 155)
        self.pdf_summary_name_prefix = place(NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 480, FIELD_HEIGHT)), 180, 409, 620, FIELD_HEIGHT, True)
        self.pdf_summary_name_prefix.setToolTip_("Remove this exact text from the start of names in this PDF only. Leading blanks are always removed.")
        label("Optional map pages", 20, 449, 200, 13, True)
        self.pdf_summary_options = {}
        for key, title, x, width, state in (
            ("include_overview", "Overview map", 20, 170, False),
            ("include_track_maps", "Individual track maps", 205, 200, False),
            ("elevation_profile", "Elevation profiles", 425, 180, True),
            ("rotate_maps", "Rotate maps", 620, 160, False),
        ):
            checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, width, 24))
            checkbox.setButtonType_(NSButtonTypeSwitch)
            checkbox.setTitle_(title)
            checkbox.setState_(NSControlStateValueOn if state else NSControlStateValueOff)
            place(checkbox, x, 478, width)
            self.pdf_summary_options[key] = checkbox
        label("Checkpoint details (this PDF only)", 20, 517, 400, 13, True)
        defaults = pdf_report_parameters(self.project_parameters, SummaryOptions())
        for key, title, x, top, width, tooltip in (
            ("include_checkpoints", "Include checkpoints", 20, 545, 300,
             "Include timed checkpoints, breaks and matching map/profile markers on individual track pages."),
            ("include_checkpoint_places", "Include checkpoint names", 20, 575, 330,
             "Show available place names from media and the PDF checkpoint sidecar."),
            ("retrieve_checkpoint_places", "Retrieve missing names", 390, 575, 390,
             "Look up missing checkpoint names when place-name retrieval is enabled in Settings. Results are stored only in the PDF checkpoint sidecar."),
            ("include_checkpoint_weather", "Include checkpoint weather", 20, 605, 330,
             "Show compatible weather already available in media or the PDF checkpoint sidecar."),
            ("retrieve_checkpoint_weather", "Retrieve missing weather", 390, 605, 390,
             "Download missing checkpoint weather using the existing Open-Meteo consent and account settings. Results are stored only in the PDF checkpoint sidecar."),
        ):
            checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, width, 24))
            checkbox.setButtonType_(NSButtonTypeSwitch)
            checkbox.setTitle_(title)
            parameter = dict(PDF_CHECKPOINT_OPTIONS)[key]
            checkbox.setState_(NSControlStateValueOn if defaults[parameter] else NSControlStateValueOff)
            checkbox.setToolTip_(tooltip)
            checkbox.setTarget_(self)
            checkbox.setAction_("pdfSummaryOptionsChanged:")
            place(checkbox, x, top, width)
            self.pdf_summary_options[key] = checkbox
        label("Page", 20, 648, 55)
        self.pdf_summary_orientation_menu = place(NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 140, 24), False), 80, 648, 140)
        self.pdf_summary_orientation_menu.addItemsWithTitles_(["Portrait", "Landscape"])
        self.pdf_summary_status_label = label("Preparing track data...", 20, 0, 780, 11)
        self.pdf_summary_export_button = button("Export", "exportPdfSummaryNow:", 0, 0, 82)
        self.pdf_summary_close_button = button("Close", "closePdfSummary:", 0, 0, 74)
        self.pdf_summary_cancel_button = button("Cancel Export", "cancelPdfSummary:", 0, 0, 120)
        self.pdf_summary_open_button = button("Open PDF", "openPdfSummary:", 20, 0, 100)
        self.pdf_summary_reveal_button = button("Show in Finder", "revealPdfSummary:", 130, 0, 135)
        self.pdf_summary_open_button.setHidden_(True)
        self.pdf_summary_reveal_button.setHidden_(True)
        self.pdf_summary_cancel_button.setHidden_(True)
        self.pdf_summary_window = window
        self.pdf_summary_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 90, 820, 560))
        self.pdf_summary_scroll.setHasVerticalScroller_(True)
        self.pdf_summary_scroll.setHasHorizontalScroller_(False)
        self.pdf_summary_scroll.setAutohidesScrollers_(True)
        self.pdf_summary_scroll.setDrawsBackground_(False)
        self.pdf_summary_body = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 800, 700))
        self.pdf_summary_scroll.setDocumentView_(self.pdf_summary_body)
        root.addSubview_(self.pdf_summary_scroll)
        footer = {
            self.pdf_summary_status_label, self.pdf_summary_export_button, self.pdf_summary_close_button,
            self.pdf_summary_cancel_button, self.pdf_summary_open_button, self.pdf_summary_reveal_button,
        }
        for view, *_position in self.pdf_summary_positions:
            if view not in footer:
                view.removeFromSuperview()
                self.pdf_summary_body.addSubview_(view)
        self.pdf_summary_delegate = GPXEditorPdfSummaryWindowDelegate.alloc().initWithController_(self)
        window.setDelegate_(self.pdf_summary_delegate)
        self.register_auxiliary_window(window)
        self.update_pdf_checkpoint_controls()
        self.layout_pdf_summary_window()
        clip = self.pdf_summary_scroll.contentView()
        clip.scrollToPoint_(NSMakePoint(0, max(0, 700 - float(clip.bounds().size.height))))
        self.pdf_summary_scroll.reflectScrolledClipView_(clip)

    def update_pdf_checkpoint_controls(self):
        controls = self.pdf_summary_options
        enabled = controls["include_checkpoints"].state() == NSControlStateValueOn
        for kind in ("places", "weather"):
            include = controls[f"include_checkpoint_{kind}"]
            include.setEnabled_(enabled)
            controls[f"retrieve_checkpoint_{kind}"].setEnabled_(
                enabled and include.state() == NSControlStateValueOn)

    def layout_pdf_summary_window(self):
        if self.pdf_summary_window is None:
            return
        bounds = self.pdf_summary_window.contentView().bounds()
        width, height = bounds.size.width, bounds.size.height
        self.pdf_summary_scroll.setFrame_(NSMakeRect(0, 90, width, max(1, height - 90)))
        body_width = width - 20
        self.pdf_summary_body.setFrame_(NSMakeRect(0, 0, body_width, 700))
        footer = {
            self.pdf_summary_status_label, self.pdf_summary_export_button, self.pdf_summary_close_button,
            self.pdf_summary_cancel_button, self.pdf_summary_open_button, self.pdf_summary_reveal_button,
        }
        for view, x, top, original_width, view_height, flexible in self.pdf_summary_positions:
            if view in footer:
                continue
            view_width = original_width + (body_width - 820 if flexible else 0)
            if view is self.pdf_summary_browse_button:
                x = body_width - 110
            if view is self.pdf_summary_settings_button:
                x = body_width - 64
            view.setFrame_(NSMakeRect(x, 700 - top - view_height, view_width, view_height))
        self.pdf_summary_status_label.setFrame_(NSMakeRect(20, 57, width - 40, 20))
        self.pdf_summary_export_button.setFrame_(NSMakeRect(width - 184, 18, 82, BUTTON_HEIGHT))
        self.pdf_summary_close_button.setFrame_(NSMakeRect(width - 94, 18, 74, BUTTON_HEIGHT))
        self.pdf_summary_cancel_button.setFrame_(NSMakeRect(width - 320, 18, 125, BUTTON_HEIGHT))
        self.pdf_summary_open_button.setFrame_(NSMakeRect(20, 18, 100, BUTTON_HEIGHT))
        self.pdf_summary_reveal_button.setFrame_(NSMakeRect(130, 18, 135, BUTTON_HEIGHT))

    @objc.IBAction
    def selectPdfSummaryTracks_(self, _sender):
        self.pdf_summary_scope_menu.selectItemAtIndex_(2)
        self.pdf_only_loading = False
        self.pdf_summary_result_message = ""
        self.show()

    @objc.IBAction
    def pdfSummaryOptionsChanged_(self, _sender):
        self.pdf_summary_result_message = ""
        self.update_pdf_checkpoint_controls()
        self.pollPdfSummary_(None)

    @objc.IBAction
    def choosePdfSummaryOutput_(self, _sender):
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["pdf"])
        panel.setCanCreateDirectories_(True)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.default_pdf_directory())))
        panel.setNameFieldStringValue_(Path(str(self.pdf_summary_path_field.stringValue())).name or f"{self.default_pdf_basename()}.pdf")
        if panel.runModal() and panel.URL() is not None:
            self.pdf_summary_path_field.setStringValue_(str(Path(str(panel.URL().path())).resolve()))

    def pdf_report_options(self):
        return SummaryOptions(
            columns=tuple(key for key, checkbox in self.pdf_summary_checkboxes.items() if checkbox.state() == NSControlStateValueOn),
            scope=("visible", "all", "selected")[self.pdf_summary_scope_menu.indexOfSelectedItem()],
            orientation="landscape" if self.pdf_summary_orientation_menu.indexOfSelectedItem() == 1 else "portrait",
            include_overview=self.pdf_summary_options["include_overview"].state() == NSControlStateValueOn,
            include_track_maps=self.pdf_summary_options["include_track_maps"].state() == NSControlStateValueOn,
            elevation_profile=self.pdf_summary_options["elevation_profile"].state() == NSControlStateValueOn,
            rotate_maps=self.pdf_summary_options["rotate_maps"].state() == NSControlStateValueOn,
            name_prefix=str(self.pdf_summary_name_prefix.stringValue()),
            **{key: self.pdf_summary_options[key].state() == NSControlStateValueOn
               for key, _parameter in PDF_CHECKPOINT_OPTIONS},
        )

    def pdf_report_tracks(self, options):
        return select_summary_tracks(self.tracks, options.scope, self.selected_nrs)

    @objc.IBAction
    def exportPdfSummaryNow_(self, _sender):
        if self.pdf_summary_future is not None:
            return
        self.sync_pdf_project_settings()
        options = self.pdf_report_options()
        if not options.columns:
            show_alert("No columns selected.", "Select at least one column for the PDF summary.")
            return
        tracks = self.pdf_report_tracks(options)
        if not tracks:
            show_alert("No tracks selected.", "Choose Visible or All, or use Select Tracks to choose tracks in the table.")
            return
        value = str(self.pdf_summary_path_field.stringValue()).strip()
        if not value:
            show_alert("No PDF file selected.", "Choose a PDF filename before exporting.")
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.default_pdf_directory() / path
        path = path.with_suffix(".pdf").resolve()
        if path.exists() and not confirm("Replace existing PDF?", str(path)):
            return
        workspace = getattr(self, "adventure_workspace_delegate", None)
        report_parameters = pdf_report_parameters(self.project_parameters, options)
        if (workspace is not None and getattr(workspace, "weather_consent", "disabled") == "unasked"
                and options.include_track_maps and report_parameters.get("pdf.track_info", True)
                and report_parameters["pdf.checkpoints"]
                and report_parameters["pdf.checkpoint_weather"]
                and report_parameters["pdf.retrieve_checkpoint_weather"]):
            workspace._request_initial_weather_choice(purpose="PDF checkpoints")
            self.sync_pdf_project_settings()
        map_count = int(options.include_overview) + (len(tracks) if options.include_track_maps else 0)
        try:
            identities = track_ids(self.tracks)
            snapshot = SummarySnapshot(
                self.project_name or PROGRAM_TITLE,
                tuple(summary_track(track, index, identities) for index, track in enumerate(self.tracks, 1) if track in tracks),
                options,
            )
            cancel = threading.Event()
            renderer = PDFMapRenderer(self, tracks, options, cancel, self._pdf_worker_progress,
                checkpoint_sidecar_path=path.with_name(path.stem + "-checkpoints.json")) if map_count else None
        except Exception as exc:
            show_alert("Could not prepare PDF summary.", str(exc))
            return
        self.start_pdf_summary_export(path, snapshot, renderer, cancel)

    def start_pdf_summary_export(self, path, snapshot, renderer, cancel):
        self.pdf_summary_export_request = (path, snapshot, renderer, cancel)
        self.pdf_summary_cancel = cancel
        self.pdf_summary_progress = "Preparing PDF summary..."
        self.pdf_summary_result_message = ""
        self.pdf_summary_export_button.setEnabled_(False)
        self.pdf_summary_cancel_button.setHidden_(False)
        self.pdf_summary_open_button.setHidden_(True)
        self.pdf_summary_reveal_button.setHidden_(True)
        self.pdf_summary_future = PDF_EXPORT_EXECUTOR.submit(
            export_prepared_summary_pdf, path, snapshot, renderer, cancel, self._pdf_worker_progress,
        )

    def confirm_pdf_osm_downloads(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Some PDF map tiles are not cached.")
        alert.setInformativeText_(
            "Existing maps and cached tiles were tried first. Completing this multi-map PDF "
            "would download additional tiles from the public OpenStreetMap server.\n\n"
            "This could violate its tile usage policy, which prohibits bulk downloading, "
            "and could cause access to be blocked. Choosing to proceed is at your own "
            "responsibility and does not grant permission from OpenStreetMap. "
            "A production map provider is recommended.\n\n"
            "https://operations.osmfoundation.org/policies/tiles/"
        )
        alert.addButtonWithTitle_("Choose Another Provider")
        alert.addButtonWithTitle_("Proceed at My Responsibility")
        alert.addButtonWithTitle_("Cancel")
        return int(alert.runModal())

    def show_pdf_map_settings(self):
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None:
            workspace.open_shared_settings("Map Service")
        else:
            self.showSettings_(None)
            self.settings_controller.current_section = "Map Service"
            self.settings_controller.show()

    @objc.IBAction
    def pdfTypeSettings_(self, _sender):
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None:
            workspace.open_shared_settings("PDF Export")
        else:
            self.showSettings_(None)
            self.settings_controller._capture_controls()
            self.settings_controller.current_section = "PDF Export"
            self.settings_controller.show()

    @objc.python_method
    def _pdf_worker_progress(self, message):
        self.pdf_summary_progress = message

    @objc.IBAction
    def pollPdfSummary_(self, _sender):
        if self.pdf_summary_window is None:
            return
        future = self.pdf_summary_future
        if future is not None:
            self.pdf_summary_export_button.setEnabled_(False)
            self.pdf_summary_cancel_button.setHidden_(False)
            self.pdf_summary_status_label.setStringValue_(self.pdf_summary_progress)
            if not future.done():
                return
            self.pdf_summary_future = None
            self.pdf_summary_cancel = None
            request = self.pdf_summary_export_request
            self.pdf_summary_export_request = None
            self.pdf_summary_cancel_button.setHidden_(True)
            try:
                path = future.result()
            except PDFExportCancelled:
                self.pdf_summary_result_message = "Export cancelled. The previous PDF was kept."
            except TileCacheMissError:
                if request is None or request[3].is_set():
                    self.pdf_summary_result_message = "Export cancelled. The previous PDF was kept."
                else:
                    response = self.confirm_pdf_osm_downloads()
                    if response == 1001:
                        path, snapshot, renderer, _old_cancel = request
                        cancel = threading.Event()
                        renderer.map_cache_only = False
                        renderer.cancel = cancel
                        self.start_pdf_summary_export(path, snapshot, renderer, cancel)
                        return
                    if response == 1000:
                        self.pdf_summary_result_message = "Choose a map provider, apply Settings, then Export again."
                        self.show_pdf_map_settings()
                    else:
                        self.pdf_summary_result_message = "Export cancelled. The previous PDF was kept."
            except Exception as exc:
                self.pdf_summary_result_message = "PDF export failed."
                show_alert("PDF summary export failed.", str(exc))
            else:
                self.last_pdf_path = path
                self.pdf_summary_path_field.setStringValue_(str(path))
                self.pdf_summary_result_message = f"Saved {path.name}."
                self.pdf_summary_open_button.setHidden_(False)
                self.pdf_summary_reveal_button.setHidden_(False)
                NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))
            self.pdf_summary_export_button.setEnabled_(True)
            self.pdf_summary_status_label.setStringValue_(self.pdf_summary_result_message)
            return
        loading = self.track_load_timer is not None or self.processing_future is not None
        ready = bool(self.tracks) and all(track.metrics and not track.metrics_dirty for track in self.tracks) and not loading
        self.pdf_summary_export_button.setEnabled_(ready)
        if not ready:
            message = str(self.status_label.stringValue()) if loading else "No GPX tracks are available."
            self.pdf_summary_status_label.setStringValue_(f"Preparing PDF summary: {message}")
        else:
            options = self.pdf_report_options()
            count = len(self.pdf_report_tracks(options))
            self.pdf_summary_status_label.setStringValue_(self.pdf_summary_result_message or f"{count} track(s) for this report. Hidden tracks do not contribute to totals or averages.")

    @objc.IBAction
    def cancelPdfSummary_(self, _sender):
        if self.pdf_summary_cancel is not None:
            self.pdf_summary_cancel.set()
            self.pdf_summary_progress = "Cancelling after the current rendering step..."

    @objc.IBAction
    def openPdfSummary_(self, _sender):
        if self.last_pdf_path is not None and self.last_pdf_path.is_file():
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(self.last_pdf_path)))

    @objc.IBAction
    def revealPdfSummary_(self, _sender):
        if self.last_pdf_path is not None and self.last_pdf_path.is_file():
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(self.last_pdf_path))])

    @objc.IBAction
    def closePdfSummary_(self, _sender):
        if self.pdf_summary_window is not None:
            self.cancelPdfSummary_(None)
            self.pdf_summary_window.orderOut_(None)
            if self.pdf_summary_poll_timer is not None:
                self.pdf_summary_poll_timer.invalidate()
                self.pdf_summary_poll_timer = None

    def write_track_table_pdf(self, path: Path, selected_columns: list[str], orientation: str, pdf_options: dict | None = None):
        """Compatibility entry point using the same snapshot/exporter as the panel."""
        values = pdf_options or {}
        scope = values.get("scope", "visible")
        options = SummaryOptions(
            columns=tuple(selected_columns), orientation=orientation, scope=scope,
            include_overview=bool(values.get("include_overview")),
            include_track_maps=bool(values.get("include_track_maps", values.get("track_mode", "none") != "none")),
            elevation_profile=bool(values.get("elevation_profile", True)),
            rotate_maps=bool(values.get("rotate_maps")), name_prefix=str(values.get("name_prefix", "")),
            **{key: values.get(key) for key, _parameter in PDF_CHECKPOINT_OPTIONS},
        )
        tracks = self.pdf_report_tracks(options)
        identities = track_ids(self.tracks)
        snapshot = SummarySnapshot(self.project_name or PROGRAM_TITLE, tuple(
            summary_track(track, index, identities) for index, track in enumerate(self.tracks, 1) if track in tracks
        ), options)
        cancel = threading.Event()
        renderer = PDFMapRenderer(self, tracks, options, cancel, lambda _message: None,
            checkpoint_sidecar_path=Path(path).with_name(Path(path).stem + "-checkpoints.json")) if options.include_overview or options.include_track_maps else None
        return export_prepared_summary_pdf(path, snapshot, renderer, cancel, lambda _message: None)

    @objc.python_method
    @serialized_plotting
    def make_pdf_map_figure(self, page_size, mode: str, tracks: list[TrackRecord], title: str, orientation: str, options: dict, plt):
        if not tracks:
            return None
        timing_start = time.perf_counter()
        import contextily as cx
        configure_contextily_cache(cx, self.tile_cache_dir)
        import_done = time.perf_counter()
        fig = plt.figure(figsize=page_size, dpi=self.pdf_document_dpi)
        fig.patch.set_facecolor("white")
        page_w, page_h = page_size
        checkpoints = ()
        if mode == "track" and callable(getattr(self, "checkpoints_for_track", None)):
            checkpoints = self.checkpoints_for_track(tracks[0])
        left_inches = 0.72 if options.get("elevation_profile") else 0.42
        left = left_inches / page_w
        right = 0.25 / page_w
        displayed_title = report_title(fig, title, (1 - left - right) * page_w - (.4 if mode == "track" else 0), 15 if mode == "track" else 13)
        top_summary_h = 0.78 / page_h
        recording_time = (tracks[0].metrics.get("time") or tracks[0].metrics.get("start_time")) if mode == "track" else None
        if mode == "track":
            top_summary_h = (6 + 18 * (displayed_title.count('\n') + 1)
                             + (15 if recording_time else 0) + 12) / (72 * page_h)
        bottom = 0.28 / page_h
        profile_h = 0.0
        # Leave room for profile tick labels and its distance-axis title.
        gap = (0.35 if options.get("elevation_profile") else 0.08) / page_h
        if options.get("elevation_profile"):
            profile_h = 0.125
        available_h = 1.0 - bottom - top_summary_h - (profile_h + gap if profile_h else 0.0) - 0.02
        map_rect = [left, bottom, 1.0 - left - right, max(0.25, available_h)]
        if options.get("rotate_maps"):
            map_rect = [left, bottom, 1.0 - left - right, max(0.25, available_h)]
        panel = None
        if mode == "track" and callable(getattr(self, "track_panel", None)):
            panel = self.track_panel(tracks[0], fig, (1 - left - right) * page_w)
        if panel is not None and panel.height:
            # Reserve readable information space; keep at least 42% of the page for the map.
            title_h = top_summary_h
            profile_h = .10 if options.get("elevation_profile") else 0
            gap = .32 / page_h if profile_h else 0
            panel_h = min(panel.height / (72 * page_h),
                          1 - title_h - bottom - profile_h - gap - .015 - .42)
            if panel.rows:
                minimum = panel.summary_height + panel.header_height + panel.row_heights[0] + (13 if "Weather" in panel.columns else 2)
                if panel_h * page_h * 72 < minimum:
                    panel_h = panel.summary_height / (page_h * 72)
            panel_top = 1 - title_h
            first_row = draw_panel(fig, [left, panel_top - panel_h, 1 - left - right, panel_h], panel)
            if first_row < len(panel.rows):
                self.page_continuation = (panel, first_row)
            map_rect[3] = max(.42, panel_top - panel_h - bottom - profile_h - gap - .015)
            top_summary_h = title_h
        summary = self.pdf_track_summary_text(mode, tracks, title)
        ax_text = fig.add_axes([left, 1.0 - top_summary_h, 1.0 - left - right, top_summary_h])
        ax_text.axis("off")
        title_y = 1 - 6 / (top_summary_h * page_h * 72) if mode == "track" else .92
        ax_text.text(0.0, title_y, displayed_title, ha="left", va="top", fontsize=15 if mode == "track" else 13,
                     fontweight="bold", color="#173b5e", wrap=True)
        if mode == "track":
            from matplotlib.backends.backend_agg import RendererAgg
            from matplotlib.font_manager import FontProperties
            title_lines = displayed_title.splitlines()
            renderer = RendererAgg(1, 1, 72)
            text_width = renderer.get_text_width_height_descent(title_lines[-1], FontProperties(size=15, weight="bold"), False)[0]
            symbol_ax = fig.add_axes([left + (text_width + 6) / (72 * page_w),
                1 - (24 + 18 * (len(title_lines) - 1)) / (72 * page_h),
                18 / (72 * page_w), 18 / (72 * page_h)])
            draw_pdf_symbol(symbol_ax, tracks[0].track_type, color=track_color(tracks[0], self.project_parameters))
        if recording_time is not None:
            height_points = top_summary_h * page_h * 72
            y = title_y - (18 * (displayed_title.count('\n') + 1) + 3) / height_points
            ax_text.text(0, y, recording_time.astimezone().strftime("%d %B %Y"),
                         fontsize=9.5, color="#52667a", va="top")
        if mode == "overview":
            ax_text.text(0.0, 0.48, summary, ha="left", va="top", fontsize=9.5, color="#38424d", linespacing=1.25)
        summary_done = time.perf_counter()
        ax_profile = None
        if profile_h:
            profile_rect = [left, bottom + map_rect[3] + gap, 1.0 - left - right, profile_h]
            ax_profile = fig.add_axes(profile_rect)
            self.draw_pdf_elevation_profile(ax_profile, tracks, mode)
        profile_done = time.perf_counter()
        ax = fig.add_axes(map_rect)
        ax.set_facecolor("black")
        extent = self.fit_extent_to_aspect(self.extent_for_track_records(tracks), (max(map_rect[2] * page_w, 0.1), max(map_rect[3] * page_h, 0.1)))
        ax.set_xlim(extent["min_x"], extent["max_x"])
        ax.set_ylim(extent["min_y"], extent["max_y"])
        ax.set_aspect("equal", adjustable="box")
        requested_zoom = self.pdf_track_zoom
        if mode == "overview":
            # A detached report contains only the exported tracks. Choose detail
            # from its fitted extent, not its size relative to the live document.
            requested_zoom = max(self.pdf_overview_zoom, self.pdf_track_zoom)
        tile_zoom, diagnostics = self.effective_tile_zoom_for_limit(
            extent, requested_zoom, self.pdf_maximum_map_tiles
        )
        pixel_width = int(max(map_rect[2] * page_w * self.pdf_map_dpi, 1.0))
        pixel_height = int(max(map_rect[3] * page_h * self.pdf_map_dpi, 1.0))
        reuse = getattr(self, "reuse_pdf_background", None)
        reused = bool(reuse and reuse(ax, mode, tracks, extent, (pixel_width, pixel_height), tile_zoom))
        provider_name = provider_display_name(self.output_map_provider, getattr(self, "geoapify_style", "osm-bright"))
        missing_tiles = 0
        if not reused:
            try:
                missing_tiles = self.missing_map_tile_count(cx, diagnostics, tile_zoom, provider=self.output_map_provider)
            except (RuntimeError, TypeError, ValueError):
                # Cache diagnostics must never prevent the provider request.
                missing_tiles = diagnostics["count"]
        map_label = "overview" if mode == "overview" else tracks[0].display_label
        self.set_status(
            f"PDF export: plotting {map_label} at {pixel_width}x{pixel_height}px, "
            f"tile zoom {tile_zoom}, tiles {diagnostics['nx']}x{diagnostics['ny']}={diagnostics['count']}."
        )
        missing_basemap_tiles = 0
        try:
            if reused:
                self.set_status(f"PDF export: using existing {provider_name} map image for {map_label}.")
            elif missing_tiles and getattr(self, "map_cache_only", False):
                self.set_status(f"PDF export: checking cached {provider_name} tiles for {map_label}; downloads require confirmation.")
            elif missing_tiles:
                self.set_status(
                    f"PDF export: connecting to map server for {missing_tiles} missing tile(s) "
                    f"for {map_label} at zoom {tile_zoom}."
                )
            else:
                self.set_status(f"PDF export: using cached {provider_name} tiles for {map_label} at zoom {tile_zoom}.")
            if not reused:
                missing_basemap_tiles = self.add_basemap_with_timeout(
                    cx, ax, tile_zoom, provider=self.output_map_provider
                )
            if missing_tiles:
                self.cached_map_tile_urls = None
        except (TileCacheMissError, TileRequestCancelled, PDFExportCancelled):
            fig.clear()
            raise
        except Exception as exc:
            fig.clear()
            raise RuntimeError(f"PDF map unavailable for {map_label}: {exc}") from exc
        map_done = time.perf_counter()
        if missing_basemap_tiles:
            self.set_status(
                f"PDF export: {map_label} created with {missing_basemap_tiles} unavailable "
                f"map tile{'s' if missing_basemap_tiles != 1 else ''} skipped."
            )
        route_mode = str(self.project_parameters.get("trackmaps.gpx_overlay", "line"))
        route_color = str(self.project_parameters.get("trackmaps.route_color", "#0000FF"))
        route_width = float(self.project_parameters.get("trackmaps.route_width", 4.0))
        endpoint_color = str(self.project_parameters.get("trackmaps.endpoint_color", "#FFFFFF"))
        endpoint_size = float(self.project_parameters.get("trackmaps.endpoint_size", 0.0))
        for track in tracks:
            color = track_color(track, self.project_parameters)
            width = route_width
            processed = self.processed_track(track)
            scene = MapOverlayScene(
                stage_kind="gpx_track",
                mode=route_mode,
                segments=normalize_overlay_segments(
                    [
                        [(point.lat, point.lon) for point in segment.points]
                        for segment in processed.segments
                    ]
                ),
            )
            for segment in (scene.segments if scene.mode == "line" else ()):
                projected = [lonlat_to_web_mercator(point.longitude, point.latitude) for point in segment]
                if len(projected) < 2:
                    continue
                xs = [point[0] for point in projected]
                ys = [point[1] for point in projected]
                ax.plot(xs, ys, color=color, linewidth=width, linestyle="--" if track.hidden else "-", solid_capstyle="round", zorder=3)
                show_endpoints = endpoint_size > 0 or (
                    self.pdf_overview_show_endpoint_dots() if mode == "overview" else self.pdf_track_show_endpoint_dots()
                )
                if show_endpoints:
                    size = endpoint_size if endpoint_size > 0 else (26 if mode == "overview" else 38)
                    ax.scatter([xs[0], xs[-1]], [ys[0], ys[-1]], s=size, c=endpoint_color, edgecolors=color, linewidths=1.2, zorder=4)
        if route_mode == "line":
            if not checkpoints:
                self.draw_pdf_start_end_labels(ax, tracks, mode)
        if checkpoints:
            draw_checkpoint_markers(ax, ax_profile, checkpoints)
        if mode == "overview" and route_mode == "line":
            self.draw_pdf_overview_labels(ax, tracks)
        ax.axis("off")
        if options.get("rotate_maps"):
            # Keep the page layout simple: rotate the axes contents visually by using the print aspect,
            # not by rotating text/labels. The map is aligned at the lower-left page area.
            pass
        tracks_done = time.perf_counter()
        if self.debug:
            print(
                "GPXEditor PDF benchmark: "
                f"mode={mode} tracks={len(tracks)} title={title!r} "
                f"tile_zoom={tile_zoom} tiles={diagnostics['nx']}x{diagnostics['ny']}={diagnostics['count']} "
                f"missing_tiles={missing_tiles} "
                f"import={import_done - timing_start:.3f}s "
                f"setup={summary_done - import_done:.3f}s "
                f"profile={profile_done - summary_done:.3f}s "
                f"map={map_done - profile_done:.3f}s "
                f"tracks={tracks_done - map_done:.3f}s "
                f"figure_total={tracks_done - timing_start:.3f}s",
                flush=True,
            )
        return fig

    def pdf_track_summary_text(self, mode: str, tracks: list[TrackRecord], title: str) -> str:
        if mode == "overview":
            total_length = sum(self.compute_metrics(track)["length_km"] for track in tracks)
            total_duration = sum((self.compute_metrics(track)["duration"] for track in tracks if self.compute_metrics(track)["duration"] is not None), timedelta())
            return f"{len(tracks)} visible track(s)\nLength {total_length:.1f} km | Duration {format_duration(total_duration, allow_days=True)}"
        track = tracks[0]
        row = next((row for row in self.table_rows if row.get("_internal_id") == track.nr), None)
        if row is None:
            metrics = self.compute_metrics(track)
            return f"Length {metrics['length_km']:.1f} km | Duration {format_duration(metrics['duration'], allow_days=True)}\nAvg {format_speed(metrics.get('speed_kmh'))}"
        line1 = f"Row {row.get('row', '')} | {row.get('name', '')}"
        line2 = f"{row.get('date', '')} | Length {row.get('length', '')} km | Duration {row.get('duration', '')} | Sum {row.get('sum', '')} km"
        distance = row.get("distance", "")
        line3_parts = [
            f"Distance {distance} km" if distance and distance != "N/A" else "",
            f"Avg {row.get('speed', '')} km/h" if row.get("speed") and row.get("speed") != "N/A" else "",
            f"Ascent {row.get('ascent', '')} m",
            f"Descent {row.get('descent', '')} m",
        ]
        return "\n".join([line1, line2, " | ".join(part for part in line3_parts if part)])

    def draw_pdf_elevation_profile(self, ax, tracks: list[TrackRecord], mode: str):
        timing_start = time.perf_counter()
        rows = []
        distance = 0.0
        for track in tracks:
            processed = self.processed_track(track)
            track_start = distance
            for index, point in enumerate(processed.points):
                if point.elevation_m is not None:
                    rows.append((track_start + point.cumulative_distance_km, point.elevation_m, track, index, point.time))
            distance += processed.length_km
        if len(rows) < 2:
            ax.axis("off")
            return
        rows_done = time.perf_counter()
        xs = [row[0] for row in rows]
        ys = [row[1] for row in rows]
        y_min = 0
        y_max = max(
            500,
            math.ceil(
                (max(ys) + max(1.0, max(ys) * self.elevation_headroom_fraction)) / 500.0
            ) * 500.0,
        )
        for track in tracks:
            track_rows = [row for row in rows if row[2] is track]
            color = track_color(track, self.project_parameters)
            if track_rows:
                ax.plot([row[0] for row in track_rows], [row[1] for row in track_rows], color=color, linewidth=1.4, linestyle="--" if track.hidden else "-")
        ax.set_xlim(min(xs), max(xs))
        ax.set_ylim(y_min, y_max)
        ax.set_xlabel("Distance [km]", fontsize=7)
        ax.set_ylabel("Height [m]" if y_max < 3000 else "Height [km]", fontsize=7, labelpad=2)
        ax.tick_params(labelsize=6, length=2)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
        base_done = time.perf_counter()
        checkpoint_reader = getattr(self, "checkpoints_for_track", None)
        has_clock_markers = mode == "track" and callable(checkpoint_reader) and any(
            marker.timestamp is not None for marker in checkpoint_reader(tracks[0]))
        if not has_clock_markers:
            self.draw_pdf_profile_time_ticks(ax, rows)
        ticks_done = time.perf_counter()
        self.draw_pdf_profile_scale_bar(ax, y_min, y_max)
        scale_done = time.perf_counter()
        if self.debug:
            timed_count = sum(1 for row in rows if row[4] is not None)
            print(
                "GPXEditor PDF benchmark: elevation_profile "
                f"mode={mode} tracks={len(tracks)} rows={len(rows)} timed={timed_count} "
                f"collect={rows_done - timing_start:.3f}s "
                f"plot={base_done - rows_done:.3f}s "
                f"time_ticks={ticks_done - base_done:.3f}s "
                f"scale={scale_done - ticks_done:.3f}s "
                f"total={scale_done - timing_start:.3f}s",
                flush=True,
            )

    def draw_pdf_profile_time_ticks(self, ax, rows):
        timed = [row for row in rows if row[4] is not None]
        if len(timed) < 2:
            return
        origin_time = timed[0][4]
        end_time = timed[-1][4]
        total_seconds = (end_time - origin_time).total_seconds()
        if total_seconds <= 0:
            return
        top = ax.secondary_xaxis("top")
        step = self.profile_time_tick_step(total_seconds)
        tick_seconds = []
        tick_labels = []
        previous_x = None
        current = step
        x_min, x_max = ax.get_xlim()
        while current < total_seconds + 0.5:
            distance = self.pdf_distance_at_elapsed(timed, origin_time, current)
            if distance is not None and x_min <= distance <= x_max:
                if previous_x is None or (distance - previous_x) / max(x_max - x_min, 0.001) > 0.08:
                    tick_seconds.append(distance)
                    tick_labels.append(self.profile_short_elapsed_label(current))
                    previous_x = distance
            current += step
        top.set_xticks(tick_seconds)
        top.set_xticklabels(tick_labels, fontsize=6)
        top.tick_params(length=2, pad=1)

    def pdf_distance_at_elapsed(self, timed_rows, origin_time, elapsed_seconds):
        target_time = origin_time + timedelta(seconds=elapsed_seconds)
        previous = None
        for row in timed_rows:
            current_time = row[4]
            if current_time is None:
                continue
            if current_time >= target_time:
                if previous is None:
                    return row[0]
                previous_time = previous[4]
                span = (current_time - previous_time).total_seconds()
                if span <= 0:
                    return row[0]
                fraction = (target_time - previous_time).total_seconds() / span
                return previous[0] + fraction * (row[0] - previous[0])
            previous = row
        return timed_rows[-1][0] if timed_rows else None

    def profile_time_tick_step(self, total_seconds):
        desired = total_seconds / 9.0
        candidates = [
            5 * 60,
            10 * 60,
            15 * 60,
            20 * 60,
            30 * 60,
            40 * 60,
            60 * 60,
            2 * 60 * 60,
            3 * 60 * 60,
            4 * 60 * 60,
            6 * 60 * 60,
            8 * 60 * 60,
            12 * 60 * 60,
            24 * 60 * 60,
            2 * 24 * 60 * 60,
            3 * 24 * 60 * 60,
            5 * 24 * 60 * 60,
            7 * 24 * 60 * 60,
            14 * 24 * 60 * 60,
            30 * 24 * 60 * 60,
        ]
        for candidate in candidates:
            if candidate >= desired:
                return candidate
        days = max(1, math.ceil(desired / (30 * 24 * 60 * 60)) * 30)
        return days * 24 * 60 * 60

    def profile_short_elapsed_label(self, seconds):
        seconds = max(0, int(round(seconds)))
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 24 * 3600:
            hours = seconds / 3600.0
            return f"{hours:.0f}h" if abs(hours - round(hours)) < 0.01 else f"{hours:.1f}h"
        days = seconds / (24 * 3600.0)
        return f"{days:.0f}d" if abs(days - round(days)) < 0.01 else f"{days:.1f}d"

    def draw_pdf_profile_scale_bar(self, ax, y_min, y_max):
        from matplotlib.patches import Rectangle

        if y_max - y_min < 250:
            return
        x_min, x_max = ax.get_xlim()
        x = x_min + (x_max - x_min) * 0.965
        width = (x_max - x_min) * 0.012
        y = y_min + (y_max - y_min - 250.0) / 2.0
        for index in range(5):
            color = "#8c8c8c" if index % 2 == 0 else "white"
            ax.add_patch(
                Rectangle(
                    (x - width / 2.0, y + index * 50.0),
                    width,
                    50.0,
                    facecolor=color,
                    edgecolor="none",
                    zorder=5,
                )
            )
        ax.add_patch(
            Rectangle(
                (x - width / 2.0, y),
                width,
                250.0,
                facecolor="none",
                edgecolor="#555555",
                linewidth=0.6,
                zorder=6,
            )
        )
        ax.text(x, y + 255.0, "250 m", ha="center", va="bottom", fontsize=5.5, color="#555555")

    def draw_pdf_start_end_labels(self, ax, tracks: list[TrackRecord], mode: str):
        if not tracks:
            return
        first_points = self.processed_track(tracks[0]).points
        last_points = self.processed_track(tracks[-1]).points
        if not first_points or not last_points:
            return
        endpoints = [("Start", first_points[0]), ("End", last_points[-1])]
        for label, point in endpoints:
            x, y = lonlat_to_web_mercator(point.lon, point.lat)
            edge = "black" if mode == "track" else "blue"
            ax.scatter([x], [y], s=42, c="white", edgecolors=edge, linewidths=1.3, zorder=6)
            ax.text(x, y, f" {label} ", ha="left", va="bottom", fontsize=7, color="white", bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.75, edgecolor="none"), zorder=7)

    def draw_pdf_overview_labels(self, ax, tracks):
        from map_label_layout import automatic_labels_visible

        mode = self.project_parameters.get("trackmaps.overview_labels", "automatic")
        if mode == "none":
            return
        labels, segments, anchors = [], [], []
        size = max(6, min(16, 7.5 * float(self.project_parameters.get("trackmaps.font_factor", 2.2)) / 2.2))
        for track in tracks:
            processed = self.processed_track(track)
            if not processed.points:
                continue
            name = report_name(track.name, self.project_parameters.get("trackmaps.remove_name_prefix", ""))
            date = track.metrics.get("time") or processed.start_time
            details = [date.astimezone().strftime("%d.%m.%Y") if date else "", f"{processed.length_km:.1f} km"]
            labels.append(name + "\n" + " · ".join(item for item in details if item))
            anchor = processed.points[len(processed.points) // 2]
            anchors.append(lonlat_to_web_mercator(anchor.lon, anchor.lat))
            segments.extend([lonlat_to_web_mercator(point.lon, point.lat) for point in segment.points]
                            for segment in processed.segments)
        if mode == "automatic" and not automatic_labels_visible(ax, labels, segments, size):
            return
        for text, (x, y) in zip(labels, anchors):
            ax.annotate(text, (x, y), xytext=(5, 7), textcoords="offset points", fontsize=size,
                        color=self.project_parameters.get("trackmaps.route_color", "#0000FF"),
                        ha="center", zorder=7,
                        bbox=dict(boxstyle="round,pad=.2", facecolor="white", edgecolor="none", alpha=.85))

    def pdf_overview_show_track_numbers(self) -> bool:
        existing = self.existing_plot_view("overview")
        return bool(existing and existing[1].show_track_numbers)

    def pdf_overview_show_endpoint_dots(self) -> bool:
        existing = self.existing_plot_view("overview")
        return True if existing is None else bool(existing[1].show_endpoint_markers)

    def pdf_track_show_endpoint_dots(self) -> bool:
        existing = self.existing_plot_view("track")
        return True if existing is None else bool(existing[1].show_endpoint_markers)

    def save_current(self) -> bool:
        if not self.tracks and not getattr(self, "document_ids_initialized", False):
            show_alert("No tracks to save.")
            return False
        if not str(self.output_field.stringValue()).strip():
            return self.save_with_panel()
        field_path = self.output_path_from_field()
        if field_path is not None:
            workspace = getattr(self, "adventure_workspace_delegate", None)
            save_document = getattr(workspace, "save_gpx_editor_document", None)
            if callable(save_document):
                saved = save_document(self, field_path)
                if saved is not None:
                    return bool(saved)
            self.save_to_path(field_path)
            return True
        return False

    def save_with_panel(self) -> bool:
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["gpx"])
        field_path = self.output_path_from_field()
        default_path = field_path or self.last_save_path
        default_name = (default_path.name if default_path else f"{self.project_name or 'tracks'}.gpx")
        panel.setNameFieldStringValue_(default_name)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(default_path.parent if default_path else self.default_save_directory())))
        if file_panel_ok(panel.runModal()):
            url = panel.URL()
            if url is not None:
                self.set_output_path(Path(str(url.path())).resolve())
                if not self.tracks and not getattr(self, "document_ids_initialized", False):
                    self.set_status(f"Output file set to {self.last_save_path}. Add tracks before saving.")
                    return False
                self.save_current()
                return True
        return False

    def build_root(self, output_path: Path | None = None) -> ET.Element:
        root = ET.Element(qname("gpx"), {"version": "1.1", "creator": "myCamino GPX Editor"})
        if self.project_name:
            metadata = ET.SubElement(root, qname("metadata"))
            ET.SubElement(metadata, qname("name")).text = self.project_name
        if self.anchor is not None:
            extensions = ET.SubElement(root, qname("extensions"))
            anchor = ET.SubElement(extensions, "mycamino_anchor")
            anchor.set("lat", f"{self.anchor[0]:.8f}")
            anchor.set("lon", f"{self.anchor[1]:.8f}")
        for track in self.tracks:
            output_track = copy.deepcopy(track.element)
            if output_path is not None:
                rewrite_local_media_links(
                    output_track,
                    source_directory=Path(track.source_file).parent if track.source_file else None,
                    output_directory=Path(output_path).parent,
                )
            for child in list(output_track):
                if child.tag == qname("time"):
                    output_track.remove(child)
            root.append(output_track)
        store_document_counter(root, max(getattr(self, "next_nr", 1), max((track.nr for track in self.tracks), default=0) + 1))
        return root

    def write_gpx_file(self, path: Path) -> ET.Element:
        path.parent.mkdir(parents=True, exist_ok=True)
        root = self.build_root(path)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return root

    def schedule_saved_metrics_cache(self, path: Path, saved_root: ET.Element):
        """Refresh the exact-source cache after a normal or Adventure autosave."""
        saved_tracks = list(saved_root.findall("gpx:trk", NS))
        saved_metrics = []
        fingerprints = []
        display_geometries = []
        for track in self.tracks:
            metrics = dict(track.metrics)
            metrics["time"] = metrics.get("start_time")
            saved_metrics.append(metrics)
            fingerprints.append(track.processed_fingerprint)
            display_geometries.append(copy.deepcopy(track.display_geometry))
        self.metrics_cache_executor.submit(
            write_saved_metrics_cache,
            Path(path),
            self.processing_options(),
            saved_tracks,
            saved_metrics,
            fingerprints,
            display_geometries,
        )

    def backup_existing_file(self, path: Path):
        if not path.exists():
            return
        backup_path = path.with_suffix(".bak")
        shutil.copy2(path, backup_path)

    def delete_recovery_file(self):
        try:
            if RECOVERY_PATH.exists():
                RECOVERY_PATH.unlink()
        except OSError as exc:
            self.set_status(f"Could not delete recovery file: {exc}")

    def offer_recovery_load(self) -> bool:
        if not RECOVERY_PATH.exists():
            return False
        if not confirm(
            "Recovery file found.",
            f"A temporary autosave exists from a previous session:\n{RECOVERY_PATH}\n\nLoad it now?",
        ):
            return False
        self._load_gpx_paths_synchronously([RECOVERY_PATH], mark_dirty=False)
        self.dirty = True
        self.set_status(f"Loaded recovery file {RECOVERY_PATH}. Save it to keep the recovered edits.")
        return True

    def save_to_path(self, path: Path, autosave: bool = False):
        if not autosave:
            self.backup_existing_file(path)
        saved_root = self.write_gpx_file(path)
        if autosave:
            self.set_status(f"Recovery autosave written to {path}.")
            return
        self.set_output_path(path)
        self.dirty = False
        # build_root intentionally omits track-level <time>; the cache helper
        # therefore records the first processed point as the displayed time.
        self.schedule_saved_metrics_cache(path, saved_root)
        for source_index, track in enumerate(self.tracks):
            track.source_file = str(path)
            track.source_track_index = source_index
        self.delete_recovery_file()
        self.set_status(("Autosaved" if autosave else "Saved") + f" {len(self.tracks)} track(s) to {path}.")
        self.notify_save_callback(path)

    def write_track_json(self, directory: Path, base: str):
        identities = track_ids(self.tracks)
        for row, track in zip(self.table_rows, self.tracks):
            data = {
                "source_gpx": str(self.last_save_path) if self.last_save_path else None,
                "nr": track.track_number,
                "internal_id": track.internal_id,
                "stage": track.stage_number,
                "track_id": identities[track.nr],
                "type": TYPE_LABELS[track.track_type],
                "track_name": track.name,
                "erstellungsdatum": row["date"],
                "dauer": row["duration"],
                "laenge_km": float(row["length"]),
                "kumulativ_km": float(row["sum"]),
                "abstand_km": None if row["distance"] == "N/A" else float(row["distance"]),
                "avg_speed_kmh": None if row["speed"] == "N/A" else float(row["speed"]),
                "ascent_m": float(row["ascent"]),
                "descent_m": float(row["descent"]),
                "npoints": int(row["npoints"]),
            }
            json_path = directory / f"{base}-track-{track.nr}.json"
            with json_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")

    @objc.IBAction
    def sortByDate_(self, _sender):
        self.sort_by_column("date", source="Sort button")

    @objc.IBAction
    def setAnchorpoint_(self, _sender):
        if not self.tracks:
            show_alert("No tracks loaded.")
            return
        track = self.selected_tracks()[0] if self.selected_tracks() else self.tracks[0]
        points = track.points()
        if not points:
            show_alert("The selected track has no track points.")
            return
        self.push_anchor_undo()
        self.anchor = (points[0].lat, points[0].lon)
        self.mark_dirty(
            f"Anchorpoint set to the first point of {track.display_label}.",
            MutationKind.METADATA,
        )

    @objc.IBAction
    def inspectTrack_(self, _sender):
        selected = self.selected_tracks()
        if not selected:
            show_alert("Select one track to inspect.")
            return
        self.open_inspector_for_track(selected[0])

    def existing_inspector_for_track(self, track: TrackRecord):
        live_windows = []
        found = None
        for window, delegate, view in self.plot_windows:
            if window is None:
                continue
            live_windows.append((window, delegate, view))
            if found is None and isinstance(view, TrackInspectorController) and view.track is track:
                found = view
        self.plot_windows = live_windows
        return found

    def open_inspector_for_track(self, track: TrackRecord):
        existing = self.existing_inspector_for_track(track)
        if existing is not None:
            existing.show()
            return existing
        inspector = TrackInspectorController.alloc().initWithController_track_(self, track)
        inspector.show()
        self.plot_windows.append((inspector.window, inspector, inspector))
        self.register_auxiliary_window(inspector.window)
        return inspector

    @objc.IBAction
    def trackDoubleClicked_(self, _sender):
        row = self.track_table.clickedRow()
        if row < 0:
            row = self.track_table.selectedRow()
        if row < 0 or row >= len(self.tracks):
            return
        track = self.tracks[row]
        self.selected_nrs = [track.nr]
        self.update_selection_field()
        self.highlight_selected_rows()
        inspector = self.open_inspector_for_track(track)
        workspace = getattr(self, "adventure_workspace_delegate", None)
        view = getattr(workspace, "plot_view", None) if workspace is not None else None
        if inspector is not None:
            cursor = getattr(view, "cursor", None)
            point_index = cursor[0] if cursor is not None and cursor[2] is track else 0
            inspector.plot_(None)
            inspector.select_point_index(point_index)
            inspector.show()
        self.set_status(f"Opened waypoint table for {track.display_label}.")

    @objc.IBAction
    def contextInspectTrack_(self, sender):
        self.inspectTrack_(sender)

    @objc.IBAction
    def contextPlotTracks_(self, sender):
        self.plotSelected_(sender)

    @objc.IBAction
    def contextElevationProfile_(self, _sender):
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None and getattr(workspace, "plot_view", None) is not None:
            selected = self.selected_tracks()
            if selected and hasattr(workspace, "focus_workspace_tracks"):
                workspace.focus_workspace_tracks(selected, mode="track", reset_viewport=False)
            self.open_elevation_profile_for_plot_view(workspace.plot_view)
            return
        view = self.open_plot_window("track", recreate_existing=False, open_profile=False)
        if view is not None:
            self.open_elevation_profile_for_plot_view(view)

    @objc.IBAction
    def contextPlotOverview_(self, sender):
        self.plotAll_(sender)

    @objc.IBAction
    def contextViewSource_(self, sender):
        self.viewFile_(sender)

    @objc.IBAction
    def contextSetAnchorpoint_(self, sender):
        self.setAnchorpoint_(sender)

    @objc.IBAction
    def contextJoinTracks_(self, sender):
        self.joinTracks_(sender)

    @objc.IBAction
    def contextDeleteTracks_(self, _sender):
        self.delete_selected_tracks()

    @objc.IBAction
    def sortSelectedTracks_(self, _sender):
        if len(self.selected_track_row_indexes()) < 2:
            return
        criteria = [
            (identifier, title)
            for identifier, title in TRACK_SORT_CRITERIA
            if identifier != "distance" or self.anchor is not None
        ]
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 440, 72))
        criterion_label = self.make_label("Sort by", 12, False, 0)
        criterion_label.setFrame_(NSMakeRect(0, 46, 90, 20))
        criterion_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(96, 42, 344, FIELD_HEIGHT), False
        )
        criterion_popup.addItemsWithTitles_([title for _identifier, title in criteria])
        selected_identifier = self.sort_column
        selected_criterion = next(
            (
                index
                for index, (identifier, _title) in enumerate(criteria)
                if identifier == selected_identifier
            ),
            0,
        )
        criterion_popup.selectItemAtIndex_(selected_criterion)
        direction_label = self.make_label("Direction", 12, False, 0)
        direction_label.setFrame_(NSMakeRect(0, 8, 90, 20))
        direction_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(96, 4, 180, FIELD_HEIGHT), False
        )
        direction_popup.addItemsWithTitles_(["Ascending", "Descending"])
        direction_popup.selectItemAtIndex_(0 if self.sort_ascending else 1)
        accessory.addSubview_(criterion_label)
        accessory.addSubview_(criterion_popup)
        accessory.addSubview_(direction_label)
        accessory.addSubview_(direction_popup)

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Sort Selected Tracks")
        alert.setInformativeText_(
            "Only the selected table positions are reordered. All unselected tracks remain in place."
        )
        alert.setAccessoryView_(accessory)
        alert.addButtonWithTitle_("Sort")
        alert.addButtonWithTitle_("Cancel")
        if int(alert.runModal()) != 1000:
            return
        criterion_index = int(criterion_popup.indexOfSelectedItem())
        if criterion_index < 0 or criterion_index >= len(criteria):
            return
        self.sort_by_column(
            criteria[criterion_index][0],
            ascending=int(direction_popup.indexOfSelectedItem()) != 1,
            source="context menu",
        )

    def _move_selected_tracks(self, action: str):
        selected_indexes = self.selected_track_row_indexes()
        if not selected_indexes:
            return
        reordered = reordered_selected_items(self.tracks, selected_indexes, action)
        if reordered == self.tracks:
            return
        if hasattr(self, "push_track_order_undo"):
            self.push_track_order_undo()
        else:
            self.push_undo()
        self.tracks = reordered
        self.mark_dirty(
            f"Moved {len(selected_indexes)} selected track(s) {action}.",
            MutationKind.ORDER,
            {track.nr for track in reordered},
        )
        self.refresh_open_plot_views()

    @objc.IBAction
    def moveSelectedTracksUp_(self, _sender):
        self._move_selected_tracks("up")

    @objc.IBAction
    def moveSelectedTracksDown_(self, _sender):
        self._move_selected_tracks("down")

    @objc.IBAction
    def moveSelectedTracksToTop_(self, _sender):
        self._move_selected_tracks("top")

    @objc.IBAction
    def moveSelectedTracksToBottom_(self, _sender):
        self._move_selected_tracks("bottom")

    def _set_selected_tracks_hidden(self, hidden: bool):
        if hasattr(self, "ensure_track_mutations_available") and not self.ensure_track_mutations_available():
            return
        selected = self.selected_tracks()
        changed = [track for track in selected if track.hidden != hidden]
        if not changed:
            return
        before = {track.nr: track.hidden for track in changed}
        after = {track.nr: hidden for track in changed}
        self.push_visibility_undo(before, after)
        for track in changed:
            track.set_hidden(hidden)
        action = "Hidden" if hidden else "Shown"
        self.mark_visibility_changed(
            {track.nr for track in changed},
            f"{action} {len(changed)} selected track(s).",
        )

    @objc.IBAction
    def showSelectedTracks_(self, _sender):
        self._set_selected_tracks_hidden(False)

    @objc.IBAction
    def hideSelectedTracks_(self, _sender):
        self._set_selected_tracks_hidden(True)

    @objc.IBAction
    def duplicateSelectedTracks_(self, _sender):
        selected_indexes = self.selected_track_row_indexes()
        if not selected_indexes:
            return
        self.push_undo()
        self.tracks, duplicates, self.next_nr = duplicate_track_records(
            self.tracks, selected_indexes, self.next_nr
        )
        self.selected_nrs = [track.nr for track in duplicates]
        self.update_selection_field()
        self.mark_dirty(
            f"Duplicated {len(duplicates)} selected track(s).",
            MutationKind.STRUCTURE,
            {track.nr for track in duplicates},
        )
        self.refresh_open_plot_views()

    @objc.IBAction
    def renumberTracks_(self, _sender):
        if not self.tracks or not self.ensure_track_mutations_available():
            return
        result = self.track_metadata_options("Renumber", numbering=True)
        if result is None:
            return
        mode, scope, include_hidden, start, action = result
        try:
            proposal = numbering_proposal(self.tracks, self.selected_nrs, mode=mode,
                scope=scope, include_hidden=include_hidden, start=start, action=action)
            if not proposal.changes:
                self.set_status("No numbers need changing.")
                return
            copies = [copy.copy(track) for track in self.tracks]
            by_number = {track.nr: track for track in copies}
            originals = {track.nr: track for track in self.tracks}
            for old, new in proposal.changes:
                track = by_number[old]
                # A tiny metadata-only preview must never mutate source XML.
                track.element = ET.Element(qname("trk"))
                source_track = originals[old]
                get_or_create_track_name(track.element).text = source_track.name
                track.set_stage_number(source_track.stage_number)
                track.set_order_number(source_track.track_number)
            apply_numbering_proposal(copies, proposal)
            ids = track_ids(copies)
            label = lambda value: str(value) if value is not None else "Unassigned"
            lines = []
            for identity, _new in proposal.changes:
                old, new = originals[identity], by_number[identity]
                changes = []
                if mode in {"track", "both"}:
                    changes.append(f"Track: {label(old.track_number)} → {label(new.track_number)}")
                if mode in {"stage", "both"}:
                    changes.append(f"Stage: {label(old.stage_number)} → {label(new.stage_number)}")
                lines.append(f"{old.name}:  {', '.join(changes)}  (Track ID: {ids[identity] or 'Unassigned'})")
            preview = "\n".join(lines)
        except (ValueError, TypeError) as exc:
            show_alert("Cannot renumber tracks.", str(exc))
            return
        if not self.confirm_numbering_preview(preview):
            return
        affected = [originals[old] for old, _ in proposal.changes]
        self.push_metadata_undo(affected)
        apply_numbering_proposal(self.tracks, proposal)
        self.metadata_changed("Cleared numbers." if action == "clear" else "Renumbered tracks.")

    def confirm_numbering_preview(self, preview):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Apply these numbers?")
        alert.setInformativeText_("Review the visible numbers and resulting Track IDs. Only the chosen scope changes; internal identities and file references stay unchanged.")
        alert.addButtonWithTitle_("Apply Numbers")
        alert.addButtonWithTitle_("Cancel")
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 430, 240))
        scroll.setHasVerticalScroller_(True)
        text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 410, 240))
        text.setEditable_(False)
        text.setFont_(NSFont.userFixedPitchFontOfSize_(12))
        text.setString_(preview)
        scroll.setDocumentView_(text)
        alert.setAccessoryView_(scroll)
        return int(alert.runModal()) == 1000

    def track_metadata_options(self, title, numbering=False, selection=False):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.addButtonWithTitle_("Preview" if numbering else "Select" if selection else "Apply")
        alert.addButtonWithTitle_("Cancel")
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 164 if numbering else 126))
        operation = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 134, 390, 24), False)
        operation.addItemsWithTitles_(["Action: Assign Numbers", "Action: Clear Numbers"])
        choice = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 96, 390, 24), False)
        choice.addItemsWithTitles_(["Track Numbers", "Stage Numbers", "Both Track and Stage Numbers"] if numbering else list(TYPE_LABELS.values()))
        if numbering:
            choice.menu().setAutoenablesItems_(False)
        scope = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 64, 230, 24), False)
        scope.addItemsWithTitles_(["Selected tracks", "All tracks"])
        hidden = NSButton.alloc().initWithFrame_(NSMakeRect(0, 34, 230, 24))
        hidden.setButtonType_(NSButtonTypeSwitch)
        hidden.setTitle_("Include hidden tracks")
        hidden.setState_(NSControlStateValueOn)
        start = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 2, 390, 24))
        start.setPlaceholderString_("First number (blank: continue preceding row)")
        for view in ((operation, choice, scope, hidden, start) if numbering else
                     (choice, hidden) if selection else (choice, scope, hidden)):
            accessory.addSubview_(view)
        alert.setAccessoryView_(accessory)
        if numbering:
            self.numbering_dialog_controls = (operation, choice, start)
            operation.setTarget_(self)
            operation.setAction_("numberingOptionsChanged:")
            self.numberingOptionsChanged_(None)
        try:
            if int(alert.runModal()) != 1000:
                return None
            value = ("track", "stage", "both")[choice.indexOfSelectedItem()] if numbering else tuple(TYPE_LABELS)[choice.indexOfSelectedItem()]
            result = value, ("selected", "all")[scope.indexOfSelectedItem()], bool(hidden.state()), str(start.stringValue()).strip() or None
            return (*result, "clear" if operation.indexOfSelectedItem() == 1 else "assign") if numbering else result
        finally:
            if numbering:
                self.numbering_dialog_controls = None

    @objc.IBAction
    def numberingOptionsChanged_(self, _sender):
        controls = getattr(self, "numbering_dialog_controls", None)
        if controls is None:
            return
        operation, choice, start = controls
        clearing = operation.indexOfSelectedItem() == 1
        choice.itemAtIndex_(2).setEnabled_(clearing)
        if not clearing and choice.indexOfSelectedItem() == 2:
            choice.selectItemAtIndex_(0)
        start.setEnabled_(not clearing)

    @objc.IBAction
    def setTrackType_(self, _sender):
        if not self.ensure_track_mutations_available():
            return
        result = self.track_metadata_options("Set Track Type")
        if result is None:
            return
        kind, scope, include_hidden, _ = result
        selected = set(self.selected_nrs)
        tracks = [track for track in self.tracks if (scope == "all" or track.nr in selected)
                  and (include_hidden or not track.hidden)]
        if not tracks:
            return
        self.push_metadata_undo(tracks)
        for track in tracks:
            track.set_track_type(kind)
        self.metadata_changed(f"Set {len(tracks)} track(s) to {TYPE_LABELS[kind]}.")

    @objc.IBAction
    def selectTracksByType_(self, _sender):
        result = self.track_metadata_options("Select Tracks by Type", selection=True)
        if result is not None:
            kind, _, include_hidden, _ = result
            self.selected_nrs = [track.nr for track in self.tracks if track.track_type == kind
                                 and (include_hidden or not track.hidden)]
            self.update_selection_field()
            self.highlight_selected_rows()
            self.redraw_open_plot_views()

    @objc.IBAction
    def saveSelectedTracksAs_(self, _sender):
        selected_numbers = set(self.selected_nrs)
        selected = [track for track in self.tracks if track.nr in selected_numbers]
        if not selected:
            show_alert("Select one or more tracks to export.")
            return
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["gpx"])
        panel.setCanCreateDirectories_(True)
        panel.setNameFieldStringValue_(selected_tracks_export_filename(selected))
        project_directory = self.media_context.get("project_directory")
        if self.last_track_export_dir is not None:
            directory = self.last_track_export_dir
        elif project_directory:
            directory = Path(str(project_directory)).expanduser().resolve(strict=False)
        else:
            directory = self.default_save_directory()
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(directory)))
        if not file_panel_ok(panel.runModal()) or panel.URL() is None:
            return
        output_path = Path(str(panel.URL().path())).expanduser().resolve(strict=False)
        if output_path.suffix.lower() != ".gpx":
            output_path = output_path.with_suffix(".gpx")
        try:
            count = write_selected_tracks_gpx(
                selected,
                output_path,
                project_name=selected[0].name,
                anchor=self.anchor,
            )
        except (OSError, ValueError) as exc:
            show_alert("Could not export the selected tracks.", str(exc))
            self.set_status("Selected tracks were not exported.")
            return
        self.last_track_export_dir = output_path.parent
        self.set_status(f"Exported {count} selected track(s) to {output_path}.")

    def sort_by_column(
        self,
        column: str,
        ascending: bool | None = None,
        update_header: bool = True,
        source: str = "direct",
        push_undo_step: bool = True,
    ):
        if hasattr(self, "ensure_track_mutations_available") and not self.ensure_track_mutations_available():
            return
        if not self.tracks:
            return
        if column not in {identifier for identifier, _title, _width, _editable in self.columns}:
            self.set_status(f"Ignored unknown sort column: {column}")
            return
        if push_undo_step:
            if hasattr(self, "push_track_order_undo"):
                self.push_track_order_undo()
            else:
                self.push_undo()
        before_order = [track.nr for track in self.tracks[:6]]
        self.set_status(f"Sorting by {column} ({source})...")
        if ascending is not None:
            self.sort_column = column
            self.sort_ascending = ascending
        elif self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        reverse = not self.sort_ascending
        metric_cache = {id(track): self.compute_metrics(track) for track in self.tracks}
        position_cache = {id(track): index for index, track in enumerate(self.tracks)}
        selected_indexes = self.selected_track_row_indexes()
        scoped_sort = len(selected_indexes) > 1
        scope_tracks = [self.tracks[index] for index in selected_indexes] if scoped_sort else list(self.tracks)
        if column == "date":
            sorted_scope = self.date_sorted_tracks(scope_tracks, metric_cache)
            if reverse:
                sorted_scope.reverse()
            if scoped_sort:
                for target_index, track in zip(selected_indexes, sorted_scope):
                    self.tracks[target_index] = track
                self.selected_nrs = [track.nr for track in sorted_scope]
                self.update_selection_field()
            else:
                self.tracks = sorted_scope
            self.recalculate()
            self.dirty = True
            if scoped_sort:
                self.highlight_selected_rows()
            if update_header:
                self.update_sort_descriptor()
            self.refresh_open_plot_views()
            direction = "ascending" if self.sort_ascending else "descending"
            after_order = [track.nr for track in self.tracks[:6]]
            scope_text = f"selected {len(selected_indexes)} row(s)" if scoped_sort else "all rows"
            message = f"Sorted {scope_text} by {column} with date exceptions ({direction}, {source}): {before_order} -> {after_order}."
            self.set_status(message)
            if hasattr(self, "_notify_workspace_mutation"):
                self._notify_workspace_mutation(
                    MutationKind.ORDER, message, [track.nr for track in scope_tracks]
                )
            return
        def key(track):
            metrics = metric_cache[id(track)]
            current_index = position_cache[id(track)]
            mapping = {
                "row": current_index,
                "nr": track.track_number if track.track_number is not None else float("inf"),
                "stage": track.stage_number if track.stage_number is not None else float("inf"),
                "track_id": (track.stage_number if track.stage_number is not None else float("inf"), track.nr),
                "type": TYPE_LABELS[track.track_type],
                "name": track.name.casefold(),
                "date": metrics["time"] or datetime.max.replace(tzinfo=UTC),
                "length": metrics["length_km"],
                "duration": metrics["duration"].total_seconds() if metrics["duration"] else float("inf"),
                "sum": current_index,
                "distance": metrics["distance_km"] if metrics["distance_km"] is not None else float("inf"),
                "speed": metrics["speed_kmh"] if metrics["speed_kmh"] is not None else float("inf"),
                "moving_speed": metrics["moving_speed_kmh"] if metrics["moving_speed_kmh"] is not None else float("inf"),
                "ascent": metrics["ascent_m"],
                "descent": metrics["descent_m"],
                "npoints": metrics["npoints"],
            }
            return (mapping.get(column, current_index), track.nr)
        sorted_scope = sorted(scope_tracks, key=key, reverse=reverse)
        if scoped_sort:
            for target_index, track in zip(selected_indexes, sorted_scope):
                self.tracks[target_index] = track
            self.selected_nrs = [track.nr for track in sorted_scope]
            self.update_selection_field()
        else:
            self.tracks = sorted_scope
        self.recalculate()
        self.dirty = True
        if scoped_sort:
            self.highlight_selected_rows()
        if update_header:
            self.update_sort_descriptor()
        self.refresh_open_plot_views()
        direction = "ascending" if self.sort_ascending else "descending"
        after_order = [track.nr for track in self.tracks[:6]]
        scope_text = f"selected {len(selected_indexes)} row(s)" if scoped_sort else "all rows"
        message = f"Sorted {scope_text} by {column} ({direction}, {source}): {before_order} -> {after_order}."
        self.set_status(message)
        if hasattr(self, "_notify_workspace_mutation"):
            self._notify_workspace_mutation(
                MutationKind.ORDER, message, [track.nr for track in scope_tracks]
            )

    def selected_track_row_indexes(self) -> list[int]:
        indexes = self.track_table.selectedRowIndexes()
        if indexes.count() == 0:
            return []
        return [
            index
            for index in range(indexes.firstIndex(), indexes.lastIndex() + 1)
            if indexes.containsIndex_(index) and index < len(self.tracks)
        ]

    def update_sort_descriptor(self):
        if not self.sort_column:
            return
        descriptor = NSSortDescriptor.sortDescriptorWithKey_ascending_(self.sort_column, self.sort_ascending)
        self.suppress_sort_descriptor_change = True
        try:
            self.track_table.setSortDescriptors_([descriptor])
        finally:
            self.suppress_sort_descriptor_change = False

    def sort_date_with_exceptions(self, metric_cache: dict[int, dict] | None = None):
        metric_cache = metric_cache or {id(track): self.compute_metrics(track) for track in self.tracks}
        self.tracks = self.date_sorted_tracks(self.tracks, metric_cache)

    def date_sorted_tracks(self, tracks: list[TrackRecord], metric_cache: dict[int, dict]) -> list[TrackRecord]:
        for track in tracks:
            track.metrics = metric_cache[id(track)]
        source_order = sorted(tracks, key=lambda track: track.nr)
        dated = [
            track
            for track in source_order
            if track.metrics.get("time") is not None
        ]
        if not dated:
            return source_order
        leading = []
        attached: dict[int, list[TrackRecord]] = {}
        preceding = None
        for track in source_order:
            if track.metrics.get("time") is not None:
                preceding = track
            elif preceding is None:
                leading.append(track)
            else:
                attached.setdefault(id(preceding), []).append(track)
        merged = list(leading)
        for track in sorted(
            dated,
            key=lambda item: (item.metrics["time"], item.name.casefold()),
        ):
            merged.append(track)
            merged.extend(attached.get(id(track), ()))
        return merged

    def is_exceptional(self, track: TrackRecord) -> bool:
        duration = track.metrics.get("duration") if track.metrics else self.compute_metrics(track)["duration"]
        return duration is None or duration.total_seconds() <= 0

    @objc.IBAction
    def selectAllTracks_(self, _sender):
        self.selected_nrs = [track.nr for track in self.tracks]
        self.update_selection_field()
        self.highlight_selected_rows()
        self.redraw_open_plot_views()
        self.set_status(f"Selected all {len(self.selected_nrs)} tracks.")

    @objc.IBAction
    def unselectAllTracks_(self, _sender):
        self.selected_nrs = []
        self.update_selection_field()
        self.highlight_selected_rows()
        self.redraw_open_plot_views()
        self.set_status("Selection cleared.")

    @objc.IBAction
    def invertTrackSelection_(self, _sender):
        selected = set(self.selected_nrs)
        self.selected_nrs = [track.nr for track in self.tracks if track.nr not in selected]
        self.update_selection_field()
        self.highlight_selected_rows()
        self.redraw_open_plot_views()
        self.set_status(f"Inverted selection: {len(self.selected_nrs)} track(s) selected.")

    @objc.IBAction
    def selectionFieldCommitted_(self, _sender):
        by_number = {track.track_number: track.nr for track in self.tracks if track.track_number is not None}
        self.selected_nrs = [by_number[number] for number in parse_track_numbers(str(self.selection_field.stringValue())) if number in by_number]
        self.update_selection_field()
        self.highlight_selected_rows()
        self.redraw_open_plot_views()

    def table_selection_changed(self):
        if self.suppress_selection_change:
            return
        indexes = self.track_table.selectedRowIndexes()
        if indexes.count() == 0:
            self.selected_nrs = []
        else:
            selected_rows = [
                index
                for index in range(indexes.firstIndex(), indexes.lastIndex() + 1)
                if indexes.containsIndex_(index) and index < len(self.tracks)
            ]
            self.selected_nrs = [self.tracks[row].nr for row in selected_rows]
        self.update_selection_field()
        self.redraw_open_plot_views()

    def update_selection_field(self):
        by_id = {track.nr: track for track in self.tracks}
        selected = [by_id[identity] for identity in self.selected_nrs if identity in by_id]
        self.selection_field.setStringValue_(compress_track_numbers([track.track_number for track in selected if track.track_number is not None]))
        unnumbered = sum(track.track_number is None for track in selected)
        self.selection_field.setToolTip_(f"{len(selected)} track(s) selected; {unnumbered} unnumbered. Enter visible track numbers to replace the selection.")

    def highlight_selected_rows(self):
        mutable = objc.lookUpClass("NSMutableIndexSet").alloc().init()
        selected = set(self.selected_nrs)
        for index, track in enumerate(self.tracks):
            if track.nr in selected:
                mutable.addIndex_(index)
        self.suppress_selection_change = True
        try:
            self.track_table.selectRowIndexes_byExtendingSelection_(mutable, False)
        finally:
            self.suppress_selection_change = False

    def move_rows(self, indexes: list[int], target_row: int):
        if hasattr(self, "ensure_track_mutations_available") and not self.ensure_track_mutations_available():
            return
        indexes = sorted(set(index for index in indexes if 0 <= index < len(self.tracks)))
        if not indexes:
            return
        if hasattr(self, "push_track_order_undo"):
            self.push_track_order_undo()
        else:
            self.push_undo()
        moving = [self.tracks[index] for index in indexes]
        remaining = [track for index, track in enumerate(self.tracks) if index not in indexes]
        before_target = sum(1 for index in indexes if index < target_row)
        target = max(0, min(len(remaining), target_row - before_target))
        self.tracks = remaining[:target] + moving + remaining[target:]
        self.mark_dirty(
            "Tracks reordered by drag and drop.",
            MutationKind.ORDER,
            {track.nr for track in moving},
        )

    def delete_selected_tracks(self):
        if not self.ensure_track_mutations_available():
            return
        selected = self.selected_tracks()
        if not selected:
            return
        label = ", ".join(f"{track.display_label}: {track.name}" for track in selected[:12])
        if len(selected) > 12:
            label += ", ..."
        plural = "track" if len(selected) == 1 else "tracks"
        if not confirm(f"Delete selected {plural}?", f"Delete {len(selected)} {plural}: {label}"):
            return
        self.push_undo()
        selected_numbers = {track.nr for track in selected}
        self.tracks = [track for track in self.tracks if track.nr not in selected_numbers]
        self.selected_nrs = []
        self.update_selection_field()
        self.mark_dirty(
            f"Deleted {len(selected)} selected {plural}.",
            MutationKind.STRUCTURE,
            selected_numbers,
        )
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None and hasattr(workspace, "workspace_tracks_deleted"):
            workspace.workspace_tracks_deleted(selected_numbers)
        else:
            self.refresh_open_plot_views()

    @objc.IBAction
    def joinTracks_(self, _sender):
        selected = self.selected_tracks()
        if len(selected) < 2:
            show_alert("Select at least two tracks to join.")
            return
        metadata_source = self.choose_join_metadata_track(selected)
        if metadata_source is None:
            self.set_status("Join cancelled.")
            return
        self.push_undo()
        primary = selected[0]
        self.apply_track_metadata(primary, metadata_source)
        primary_segment = first_segment(primary.element)
        previous_last_time = None
        for track in selected:
            points = track.points()
            if points and points[-1].time:
                previous_last_time = points[-1].time
            elif points and previous_last_time:
                self.assign_missing_timestamps(track, previous_last_time + timedelta(seconds=1))
                previous_last_time = track.points()[-1].time
        for track in selected[1:]:
            for point in list(iter_track_points(track.element)):
                primary_segment.append(copy.deepcopy(point))
        self.tracks = [track for track in self.tracks if track is primary or track not in selected[1:]]
        self.invalidate_track_metrics(primary)
        self.selected_nrs = [primary.nr]
        self.update_selection_field()
        self.mark_dirty(
            f"Joined {len(selected)} tracks into {primary.display_label} using metadata from {metadata_source.display_label}.",
            MutationKind.STRUCTURE,
            {track.nr for track in selected},
        )
        self.refresh_open_plot_views()

    def choose_join_metadata_track(self, tracks: list[TrackRecord]) -> TrackRecord | None:
        rows = []
        for track in tracks:
            metrics = self.compute_metrics(track)
            rows.append(
                {
                    "nr": str(track.track_number or ""),
                    "name": track.name,
                    "time": format_datetime_local(metrics.get("start_time") or metrics.get("time")),
                    "length": f"{metrics['length_km']:.1f}",
                }
            )
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Choose Metadata Source")
        alert.setInformativeText_("Select which track should provide the joined track name, time, and other track metadata.")
        alert.addButtonWithTitle_("Join")
        alert.addButtonWithTitle_("Cancel")
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 230))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 230))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(1)
        table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 230))
        table.setAllowsMultipleSelection_(False)
        table.setAllowsEmptySelection_(False)
        for identifier, title, width in [
            ("nr", "Nr.", 55),
            ("name", "Name", 285),
            ("time", "Start Time", 140),
            ("length", "Length km", 80),
        ]:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
            column.headerCell().setStringValue_(title)
            column.setWidth_(width)
            table.addTableColumn_(column)
        data_source = TrackMetadataChoiceDataSource.alloc().initWithRows_(rows)
        table.setDataSource_(data_source)
        table.selectRowIndexes_byExtendingSelection_(objc.lookUpClass("NSIndexSet").indexSetWithIndex_(0), False)
        scroll.setDocumentView_(table)
        accessory.addSubview_(scroll)
        alert.setAccessoryView_(accessory)
        result = alert.runModal()
        if result != 1000:
            return None
        row = table.selectedRow()
        if row < 0 or row >= len(tracks):
            return None
        return tracks[row]

    def apply_track_metadata(self, target: TrackRecord, source: TrackRecord):
        if target is source:
            return
        number, stage = target.track_number, target.stage_number
        for child in list(target.element):
            if child.tag != qname("trkseg"):
                target.element.remove(child)
        insert_at = 0
        for child in list(source.element):
            if child.tag == qname("trkseg"):
                continue
            target.element.insert(insert_at, copy.deepcopy(child))
            insert_at += 1
        get_or_create_mycamino_track_extension(target.element).set("internal_id", str(target.internal_id))
        target.set_order_number(number)
        target.set_stage_number(stage)

    @objc.IBAction
    def plotAll_(self, _sender):
        selected = self.selected_tracks()
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None and getattr(workspace, "plot_view", None) is not None:
            if selected and hasattr(workspace, "focus_workspace_tracks"):
                workspace.focus_workspace_tracks(selected, mode="overview", reset_viewport=True)
            elif hasattr(workspace, "show_workspace_overview"):
                workspace.show_workspace_overview()
            return
        if selected:
            self.open_plot_window("overview", recreate_existing=True, tracks_override=selected)
        else:
            self.open_plot_window("overview", recreate_existing=False)

    @objc.IBAction
    def plotSelected_(self, _sender):
        if not self.selected_tracks() and self.tracks:
            self.selected_nrs = [self.tracks[0].nr]
            self.update_selection_field()
        selected = self.selected_tracks()
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if (
            workspace is not None
            and selected
            and getattr(workspace, "plot_view", None) is not None
        ):
            if hasattr(workspace, "focus_workspace_tracks"):
                workspace.focus_workspace_tracks(selected, mode="track", reset_viewport=True)
            else:
                workspace.focus_workspace_track(selected[0], reset_viewport=True)
            return
        existing = self.existing_plot_view("track")
        if existing is not None:
            _window, existing_view = existing
            inspector = getattr(existing_view, "inspector", None)
            if inspector is not None:
                inspector.owns_plot_window = False
        recreate = self.track_plot_selection_changed()
        self.open_plot_window("track", recreate_existing=recreate)

    def track_plot_selection_changed(self) -> bool:
        existing = self.existing_plot_view("track")
        if existing is None:
            return True
        _window, view = existing
        selected = self.selected_tracks() or self.tracks[:1]
        selected_numbers = [track.nr for track in selected]
        loaded_numbers = [track.nr for track in view.track_sequence()]
        return selected_numbers != loaded_numbers

    def gpx_summary_track(self, track: TrackRecord, table_number: int) -> dict:
        metrics = self.compute_metrics(track)
        processed = self.processed_track(track)
        points = processed.points
        first_point = (points[0].lat, points[0].lon) if points else None
        last_point = (points[-1].lat, points[-1].lon) if points else None
        return {
            "name": track.name,
            "time": metrics["time"],
            "start_time": metrics["start_time"],
            "end_time": metrics["end_time"],
            "duration": metrics["duration"],
            "length_km": metrics["length_km"],
            "first_point": first_point,
            "last_point": last_point,
            "points": [(point.lat, point.lon) for point in points],
            "point_records": [point.as_record() for point in points],
            "segments": [
                [(point.lat, point.lon) for point in segment.points]
                for segment in processed.segments
            ],
            "segment_records": [
                [point.as_record() for point in segment.points]
                for segment in processed.segments
            ],
            "distance_km": metrics["distance_km"],
            "raw_points": [(point.lat, point.lon) for point in processed.raw_points],
            "filtered_point_count": processed.retained_point_count,
            "raw_point_count": processed.raw_point_count,
            "ascent_m": processed.ascent_m,
            "descent_m": processed.descent_m,
            "rejection_counts": processed.rejection_counts,
            "processing_options": processed.options.as_dict(),
            "original_sequence_number": track.nr,
            "internal_id": track.internal_id,
            "display_track_number": track.track_number,
            "track_type": track.track_type,
            "stage_number": track.stage_number,
            "track_id": track_ids(self.tracks)[track.nr],
            "table_number": table_number,
        }

    def interactive_plot_info(
        self,
        mode: str,
        tracks_override: list[TrackRecord] | None = None,
        include_hidden: bool = False,
    ) -> dict | None:
        """Build a lightweight plot model; PlotView fills tiles asynchronously."""
        axes = {"left": 0.0, "bottom": 0.0, "width": 1.0, "height": 1.0}

        def entry_for(track_records, zoom):
            extent = self.extent_for_track_records(track_records)
            if extent is None:
                return None
            if any(track.points() for track in track_records):
                extent = self.fit_extent_to_aspect(extent, (1000, 720))
            else:
                extent = clamp_extent_to_web_mercator(extent)
            zoom, diagnostics = self.effective_tile_zoom(extent, zoom)
            return {
                "image": None,
                "png_data": None,
                "metadata": {
                    "crs": "EPSG:3857",
                    "extent_mercator": extent,
                    "axes_box_fraction": dict(axes),
                    "basemap": provider_display_name(self.map_provider),
                    "tile_count": diagnostics["count"],
                },
                "image_path": None,
                "zoom_level": int(zoom),
                "tile_zoom_level": int(zoom),
                "base_extent_mercator": dict(extent),
            }

        if mode == "overview":
            tracks = [
                track
                for track in (tracks_override if tracks_override is not None else
                              self.tracks if include_hidden else self.visible_tracks())
                if include_hidden or not track.hidden
            ]
            zoom = self.overview_zoom_for_tracks(tracks, self.overview_zoom)
            return entry_for(tracks, zoom)

        selected = tracks_override or self.selected_tracks() or self.tracks[:1]
        per_track = {}
        for track in selected:
            entry = entry_for([track], self.track_zoom)
            if entry is not None:
                per_track[track.nr] = entry
        if not per_track:
            return None
        first_nr = next(iter(per_track))
        result = dict(per_track[first_nr])
        result["tracks"] = per_track
        result["current_track_nr"] = first_nr
        return result

    def render_summary_plot(self, mode: str, zoom_level: int | None = None, tracks_override: list[TrackRecord] | None = None) -> dict | None:
        if render_track_plot is None:
            show_alert("Plotting is unavailable.", "The map drawing support could not be loaded.")
            return None
        try:
            import contextily as cx
            configure_contextily_cache(cx, self.tile_cache_dir)
        except ImportError:
            pass
        output_dir = Path(tempfile.mkdtemp(prefix="mycamino-gpx-editor-"))
        try:
            if mode == "overview":
                timing_start = time.perf_counter()
                effective_requested_zoom = self.overview_zoom if zoom_level is None else zoom_level
                if tracks_override:
                    tracks_override = [track for track in tracks_override if not track.hidden]
                    selected_extent = self.extent_for_track_records(tracks_override)
                    selected_zoom = self.overview_zoom_for_tracks(tracks_override, effective_requested_zoom)
                    rendered = self.render_viewport_plot("overview", self.visible_tracks(), selected_extent, selected_zoom)
                    if rendered is None:
                        return None
                    rendered["zoom_level"] = selected_zoom
                    rendered["tile_zoom_level"] = rendered.get("tile_zoom_level", selected_zoom)
                    rendered["base_extent_mercator"] = rendered.get("metadata", {}).get("extent_mercator")
                    return rendered
                overview_tracks = self.visible_tracks()
                summary_done = time.perf_counter()
                extent = self.extent_for_track_records(overview_tracks)
                rendered = self.render_viewport_plot("overview", overview_tracks, extent, effective_requested_zoom)
                if rendered is None:
                    return None
                render_done = time.perf_counter()
                image = rendered["image"]
                image_done = time.perf_counter()
                if self.debug:
                    print(
                        "GPXEditor benchmark: overview "
                        f"summary={summary_done - timing_start:.3f}s "
                        f"render_map={render_done - summary_done:.3f}s "
                        f"image={image_done - render_done:.3f}s "
                        f"total={image_done - timing_start:.3f}s",
                        flush=True,
                    )
                rendered["zoom_level"] = effective_requested_zoom
                rendered["tile_zoom_level"] = rendered.get("tile_zoom_level", effective_requested_zoom)
                rendered["base_extent_mercator"] = rendered.get("metadata", {}).get("extent_mercator")
                return rendered

            selected = self.selected_tracks() or self.tracks[:1]
            effective_requested_zoom = self.track_zoom if zoom_level is None else zoom_level
            per_track = {}
            for track in selected:
                table_number = self.tracks.index(track) + 1
                extent = self.extent_for_track_records([track])
                rendered = self.render_viewport_plot("track", [track], extent, effective_requested_zoom)
                if rendered is None:
                    return None
                per_track[track.nr] = {
                    "image": rendered["image"],
                    "png_data": rendered.get("png_data"),
                    "metadata": rendered["metadata"],
                    "image_path": rendered["image_path"],
                    "zoom_level": effective_requested_zoom,
                    "tile_zoom_level": rendered.get("tile_zoom_level", effective_requested_zoom),
                    "base_extent_mercator": rendered.get("metadata", {}).get("extent_mercator"),
                }
            first_info = per_track[selected[0].nr].copy()
            first_info["tracks"] = per_track
            first_info["current_track_nr"] = selected[0].nr
            first_info["zoom_level"] = effective_requested_zoom
            first_info["tile_zoom_level"] = first_info.get("tile_zoom_level", effective_requested_zoom)
            first_info["base_extent_mercator"] = first_info.get("metadata", {}).get("extent_mercator")
            return first_info
        except RuntimeError as exc:
            show_alert("Could not render the map.", str(exc))
            return None

    def extent_for_track_records(self, tracks: list[TrackRecord], padding_fraction: float | None = None) -> dict:
        if padding_fraction is None:
            padding_fraction = self.map_padding_fraction
        projected = []
        for track in tracks:
            payload = track.display_geometry or {}
            extent = payload.get("extent_mercator") if isinstance(payload, dict) else None
            if isinstance(extent, dict):
                try:
                    projected.extend(
                        [
                            (float(extent["min_x"]), float(extent["min_y"])),
                            (float(extent["max_x"]), float(extent["max_y"])),
                        ]
                    )
                    continue
                except (KeyError, TypeError, ValueError):
                    pass
            projected.extend(
                lonlat_to_web_mercator(point.lon, point.lat)
                for point in self.display_processed_track(track).points
            )
        if not projected:
            limit = math.pi * 6378137.0
            return {"min_x": -limit, "max_x": limit, "min_y": -limit, "max_y": limit}
        xs = [point[0] for point in projected]
        ys = [point[1] for point in projected]
        if len(projected) == 1:
            # Roughly three tiles across at the configured track zoom.
            span = (2.0 * math.pi * 6378137.0) / (2 ** max(0, self.track_zoom)) * 3.0
            return {
                "min_x": xs[0] - span / 2.0,
                "max_x": xs[0] + span / 2.0,
                "min_y": ys[0] - span / 2.0,
                "max_y": ys[0] + span / 2.0,
            }
        span_x = max(max(xs) - min(xs), 1.0)
        span_y = max(max(ys) - min(ys), 1.0)
        padding_x = span_x * padding_fraction
        padding_y = span_y * padding_fraction
        return {
            "min_x": min(xs) - padding_x,
            "max_x": max(xs) + padding_x,
            "min_y": min(ys) - padding_y,
            "max_y": max(ys) + padding_y,
        }

    def extent_for_point_infos(self, points: list[PointInfo], padding_fraction: float | None = None) -> dict:
        padding_fraction = self.map_padding_fraction if padding_fraction is None else padding_fraction
        projected = [lonlat_to_web_mercator(point.lon, point.lat) for point in points]
        if not projected:
            return self.extent_for_track_records([])
        if len(projected) == 1:
            span = (2.0 * math.pi * 6378137.0) / (2 ** max(0, self.track_zoom)) * 3.0
            x, y = projected[0]
            return {"min_x": x - span / 2, "max_x": x + span / 2, "min_y": y - span / 2, "max_y": y + span / 2}
        xs, ys = [item[0] for item in projected], [item[1] for item in projected]
        span_x, span_y = max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)
        return {
            "min_x": min(xs) - span_x * padding_fraction,
            "max_x": max(xs) + span_x * padding_fraction,
            "min_y": min(ys) - span_y * padding_fraction,
            "max_y": max(ys) + span_y * padding_fraction,
        }

    def overview_zoom_for_tracks(self, selected_tracks: list[TrackRecord], base_zoom: int | None = None) -> int:
        if base_zoom is None:
            base_zoom = self.overview_zoom
        if not selected_tracks or len(selected_tracks) == len(self.tracks):
            return base_zoom
        full_extent = self.extent_for_track_records(self.tracks)
        selected_extent = self.extent_for_track_records(selected_tracks)
        full_span = max(full_extent["max_x"] - full_extent["min_x"], full_extent["max_y"] - full_extent["min_y"], 1.0)
        selected_span = max(selected_extent["max_x"] - selected_extent["min_x"], selected_extent["max_y"] - selected_extent["min_y"], 1.0)
        zoom_delta = max(0, int(round(math.log2(full_span / selected_span)))) if selected_span > 0 else 0
        return max(0, min(18, base_zoom + zoom_delta))

    def fit_extent_to_aspect(self, extent: dict, image_size=(1920, 1080)) -> dict:
        target_ratio = image_size[0] / image_size[1]
        min_x = float(extent["min_x"])
        max_x = float(extent["max_x"])
        min_y = float(extent["min_y"])
        max_y = float(extent["max_y"])
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        current_ratio = span_x / span_y
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        if current_ratio < target_ratio:
            span_x = span_y * target_ratio
        else:
            span_y = span_x / target_ratio
        return {
            "min_x": center_x - span_x / 2.0,
            "max_x": center_x + span_x / 2.0,
            "min_y": center_y - span_y / 2.0,
            "max_y": center_y + span_y / 2.0,
        }

    def add_basemap_with_timeout(self, cx, ax, tile_zoom: int, provider: str | None = None):
        """Call Contextily with provider policy and per-tile PDF retry options."""
        selected_provider = str(provider or self.map_provider)
        with contextily_request_timeout(
            cx, self.map_request_timeout_seconds, selected_provider,
            cache_only=bool(getattr(self, "map_cache_only", False)),
            max_attempts=getattr(self, "map_tile_attempts", 1),
            minimum_read_timeout=getattr(self, "map_minimum_read_timeout", 0.0),
            cancel_event=getattr(self, "cancel", None),
            progress=self.set_status if getattr(self, "map_tile_attempts", 1) > 1 else None,
            provider_name=provider_display_name(selected_provider, getattr(self, "geoapify_style", "osm-bright")),
            wait_for_network=getattr(self, "map_wait_for_network", True),
        ):
            with tolerate_missing_tiles(cx) as missing_tile_report:
                add_headless_basemap(
                    cx, ax,
                    source=contextily_provider(
                        cx,
                        selected_provider,
                        self.custom_map_url,
                        self.custom_map_attribution,
                        self.maximum_map_zoom,
                        self.map_credential_id,
                        geoapify_style=getattr(self, "geoapify_style", "osm-bright"),
                    ),
                    zoom=min(tile_zoom, self.maximum_map_zoom),
                )
        return missing_tile_report.count

    @objc.python_method
    @serialized_plotting
    def render_viewport_plot(self, mode: str, tracks: list[TrackRecord], extent: dict, tile_zoom: int) -> dict | None:
        timing_start = time.perf_counter()
        import_done = timing_start
        try:
            import contextily as cx
            configure_contextily_cache(cx, self.tile_cache_dir)
            import_done = time.perf_counter()
        except ImportError as exc:
            show_alert("Could not render the map viewport.", f"Missing plotting dependency: {exc}")
            return None

        requested_tile_zoom = tile_zoom
        extent = self.fit_extent_to_aspect(extent, (1920, 1080))
        tile_zoom, diagnostics = self.effective_tile_zoom(extent, requested_tile_zoom)
        cache_before = self.count_tile_cache_files()
        provider_name = provider_display_name(self.map_provider)
        try:
            missing_tiles = self.missing_map_tile_count(cx, diagnostics, tile_zoom)
        except (RuntimeError, TypeError, ValueError):
            # Cache diagnostics must never prevent the actual provider request.
            missing_tiles = diagnostics["count"]
        total_tiles = diagnostics["count"]
        width_px, height_px = 1920, 1080
        dpi = 100
        fig = new_figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
        fig.patch.set_facecolor("black")
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
        ax.set_facecolor("black")
        ax.set_xlim(extent["min_x"], extent["max_x"])
        ax.set_ylim(extent["min_y"], extent["max_y"])
        ax.set_aspect("equal", adjustable="box")
        setup_done = time.perf_counter()
        if missing_tiles:
            self.set_status(f"Checking {provider_name} cache for {total_tiles} tile(s) at zoom {tile_zoom}...")
        else:
            self.set_status(f"Loading {total_tiles} cached {provider_name} tile(s) at zoom {tile_zoom}...")
        missing_basemap_tiles = 0
        try:
            if missing_tiles:
                self.set_status(
                    f"Connecting to map server for {missing_tiles} tile(s) at zoom {tile_zoom} "
                    f"(timeout {self.map_request_timeout_seconds:.0f}s per request)..."
                )
            missing_basemap_tiles = self.add_basemap_with_timeout(cx, ax, tile_zoom)
        except Exception as exc:
            fig.clear()
            message = (
                "Could not download the map. The configured map server may be unavailable, "
                "the network connection may be offline, or the request timed out."
            )
            self.set_status(f"{message} {exc}")
            show_alert("Could not download the map.", f"{message}\n\n{exc}")
            return None
        basemap_done = time.perf_counter()
        cache_after = self.count_tile_cache_files()
        if missing_tiles:
            self.cached_map_tile_urls = None
        for track in tracks:
            for segment in self.display_processed_track(track).segments:
                projected = [lonlat_to_web_mercator(point.lon, point.lat) for point in segment.points]
                for run in visible_simplified_polyline_runs(
                    projected,
                    extent,
                    (width_px, height_px),
                ):
                    xs = [point[0] for point in run]
                    ys = [point[1] for point in run]
                    ax.plot(
                        xs,
                        ys,
                        color=track_color(track, self.project_parameters),
                        linestyle="--" if track.hidden else "-",
                        linewidth=4.0,
                        solid_capstyle="round",
                        solid_joinstyle="round",
                        zorder=3,
                    )
        tracks_done = time.perf_counter()
        ax.axis("off")
        png_buffer = io.BytesIO()
        fig.savefig(png_buffer, format="png", dpi=dpi, facecolor="black", bbox_inches=None, pad_inches=0)
        fig.clear()
        save_done = time.perf_counter()
        png_data = png_buffer.getvalue()
        image = nsimage_from_png_bytes(png_data)
        image_done = time.perf_counter()
        map_seconds = basemap_done - setup_done
        total_seconds = image_done - timing_start
        if missing_tiles:
            downloaded_tiles = max(0, missing_tiles - missing_basemap_tiles)
            status_message = (
                f"Downloaded {downloaded_tiles} {provider_name} tile(s) in {map_seconds:.2f}s; "
                f"map rendered in {total_seconds:.2f}s."
            )
        else:
            status_message = (
                f"Loaded {total_tiles} cached {provider_name} tile(s) in {map_seconds:.2f}s; "
                f"map rendered in {total_seconds:.2f}s."
            )
        if missing_basemap_tiles:
            status_message = (
                f"{status_message} {missing_basemap_tiles} unavailable map "
                f"tile{'s' if missing_basemap_tiles != 1 else ''} skipped."
            )
        self.set_status(status_message)
        if self.debug:
            print(
                "GPXEditor benchmark: viewport "
                f"mode={mode} requested_tile_zoom={requested_tile_zoom} tile_zoom={tile_zoom} tracks={len(tracks)} "
                f"extent={diagnostics['width_m']:.0f}x{diagnostics['height_m']:.0f}m "
                f"tiles={diagnostics['nx']}x{diagnostics['ny']}={diagnostics['count']} "
                f"x={diagnostics['x0']}-{diagnostics['x1']} y={diagnostics['y0']}-{diagnostics['y1']} "
                f"cache_files={cache_before}->{cache_after} "
                f"missing_tiles={missing_tiles} "
                f"import={import_done - timing_start:.3f}s "
                f"setup={setup_done - import_done:.3f}s "
                f"map={basemap_done - setup_done:.3f}s "
                f"track={tracks_done - basemap_done:.3f}s "
                f"png_memory={save_done - tracks_done:.3f}s "
                f"image={image_done - save_done:.3f}s "
                f"total={image_done - timing_start:.3f}s",
                flush=True,
            )
        metadata = {
            "crs": "EPSG:3857",
            "image_size_px": {"width": width_px, "height": height_px},
            "axes_box_fraction": {"left": 0.0, "bottom": 0.0, "width": 1.0, "height": 1.0},
            "extent_mercator": dict(extent),
            "basemap": provider_display_name(self.map_provider),
            "effective_zoom": tile_zoom,
            "missing_basemap_tiles": missing_basemap_tiles,
        }
        return {
            "image": image,
            "png_data": png_data,
            "metadata": metadata,
            "image_path": None,
            "zoom_level": tile_zoom,
            "tile_zoom_level": tile_zoom,
            "status_message": status_message,
        }

    def open_plot_window(
        self,
        mode: str,
        recreate_existing: bool = True,
        tracks_override: list[TrackRecord] | None = None,
        open_profile: bool = True,
    ):
        if not self.tracks:
            show_alert("No tracks to plot.")
            return
        workspace = getattr(self, "adventure_workspace_delegate", None)
        if workspace is not None and getattr(workspace, "plot_view", None) is not None:
            workspace.plot_view.reset_plot_interaction()
            selected = list(tracks_override or self.selected_tracks() or self.tracks[:1])
            if mode == "overview" and tracks_override is None:
                return workspace.show_workspace_overview()
            if hasattr(workspace, "focus_workspace_tracks"):
                return workspace.focus_workspace_tracks(
                    selected,
                    mode=mode,
                    reset_viewport=recreate_existing,
                )
            return workspace.focus_workspace_track(
                selected[0],
                reset_viewport=recreate_existing,
            )
        existing = self.existing_plot_view(mode)
        if existing is not None:
            existing[1].reset_plot_interaction()
        if existing is not None and not recreate_existing:
            window, view = existing
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            view.update_window_title()
            view.setNeedsDisplay_(True)
            if open_profile:
                self.open_elevation_profile_for_plot_view(view)
            window.makeKeyAndOrderFront_(None)
            window.makeFirstResponder_(view)
            return view
        plot_info = self.interactive_plot_info(mode, tracks_override=tracks_override)
        if plot_info is None:
            return
        if existing is not None:
            window, view = existing
            view.replace_plot_info(plot_info)
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            view.update_window_title()
            view.setNeedsDisplay_(True)
            if open_profile:
                self.open_elevation_profile_for_plot_view(view)
            window.makeKeyAndOrderFront_(None)
            window.makeFirstResponder_(view)
            return view
        content_width, content_height = self.plot_window_content_size(plot_info)
        title = "Overview" if mode == "overview" else "Track"
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(160, 120, content_width, content_height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setReleasedWhenClosed_(False)
        window.setContentAspectRatio_(NSMakeSize(content_width, content_height))
        window.setTitle_(f"{PROGRAM_TITLE} - {title}")
        view = PlotView.alloc().initWithController_mode_plotInfo_(self, mode, plot_info)
        window.setContentView_(view)
        delegate = PlotWindowDelegate.alloc().initWithController_view_(self, view)
        window.setDelegate_(delegate)
        window.makeKeyAndOrderFront_(None)
        window.makeFirstResponder_(view)
        view.update_window_title()
        self.plot_windows.append((window, delegate, view))
        self.register_auxiliary_window(window)
        if open_profile:
            self.open_elevation_profile_for_plot_view(view)
        window.makeKeyAndOrderFront_(None)
        window.makeFirstResponder_(view)
        return view

    def plot_window_content_size(self, plot_info: dict) -> tuple[float, float]:
        image = plot_info.get("image")
        if image is None:
            return 1000.0, 720.0
        image_size = image.size()
        image_width = max(float(image_size.width), 1.0)
        image_height = max(float(image_size.height), 1.0)
        scale = min(1.0, 1280.0 / image_width, 780.0 / image_height)
        return image_width * scale, image_height * scale

    def distance_to_anchor(self, point: PointInfo) -> float:
        if self.anchor is None:
            return 0.0
        return haversine_km(point.lat, point.lon, self.anchor[0], self.anchor[1])

    def distance_from_track_start(self, track: TrackRecord, point_index: int) -> float:
        raw_points = extract_raw_track_points(track.element)
        if not raw_points:
            return 0.0
        point_index = max(0, min(int(point_index), len(raw_points) - 1))
        return self.processed_track(track).source_distances_km().get(
            raw_points[point_index].source_index,
            0.0,
        )

    def elapsed_and_remaining(self, track: TrackRecord, point_index: int) -> tuple[timedelta | None, timedelta | None]:
        points = track.points()
        if not points or point_index < 0 or point_index >= len(points):
            return None, None
        start_time = points[0].time
        cursor_time = points[point_index].time
        end_time = points[-1].time
        elapsed = cursor_time - start_time if start_time is not None and cursor_time is not None else None
        remaining = end_time - cursor_time if end_time is not None and cursor_time is not None else None
        return elapsed, remaining

    def set_anchor_from_point(self, point: PointInfo):
        self.push_anchor_undo()
        self.anchor = (point.lat, point.lon)
        self.mark_dirty(
            f"Anchor point set to {point.lat:.6f}, {point.lon:.6f}.",
            MutationKind.METADATA,
        )

    def delete_points(self, track: TrackRecord, start: int, end: int, *, refresh=True):
        points = track.points()
        delete_elements = {point.element for point in points[start:end + 1]}
        if not delete_elements:
            return
        self.push_undo()
        for segment in track.element.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                if point in delete_elements:
                    segment.remove(point)
        self.invalidate_track_metrics(track)
        self.mark_dirty(
            f"Deleted {len(delete_elements)} point(s) from {track.display_label}.",
            MutationKind.STRUCTURE,
            {track.nr},
        )
        if refresh:
            self.refresh_open_plot_views()

    def cut_track(self, track: TrackRecord, index: int, *, refresh=True):
        points = track.points()
        if index <= 0 or index >= len(points) - 1:
            show_alert("Cut point must leave points on both sides.")
            return
        self.push_undo()
        new_track = copy.deepcopy(track.element)
        for segment in new_track.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                segment.remove(point)
        new_segment = first_segment(new_track)
        move_elements = {point.element for point in points[index + 1:]}
        for point in points[index + 1:]:
            new_segment.append(copy.deepcopy(point.element))
        for segment in track.element.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                if point in move_elements:
                    segment.remove(point)
        get_or_create_track_name(new_track).text = f"{track.name} part 2"
        new_record = TrackRecord(self.next_nr, new_track, track.source_file)
        new_record.set_order_number(max((item.track_number or 0 for item in self.tracks), default=0) + 1)
        self.next_nr += 1
        insert_at = self.tracks.index(track) + 1
        self.tracks.insert(insert_at, new_record)
        self.invalidate_track_metrics(track)
        self.invalidate_track_metrics(new_record)
        self.selected_nrs = [track.nr, new_record.nr]
        self.update_selection_field()
        self.mark_dirty(
            f"Cut {track.display_label}; new {new_record.display_label} created.",
            MutationKind.STRUCTURE,
            {track.nr, new_record.nr},
        )
        if refresh:
            self.refresh_open_plot_views()

    def unique_split_track_name(self, base_name: str) -> str:
        existing = {track.name for track in self.tracks}
        index = 2
        while True:
            candidate = f"{base_name} ({index})"
            if candidate not in existing:
                return candidate
            index += 1

    def split_track_from_index(self, track: TrackRecord, start_index: int) -> TrackRecord | None:
        points = track.points()
        if start_index <= 0 or start_index >= len(points):
            show_alert("Split Track Above must leave points in both tracks.")
            return None
        self.push_undo()
        new_track = copy.deepcopy(track.element)
        for segment in new_track.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                segment.remove(point)
        new_segment = first_segment(new_track)
        move_elements = {point.element for point in points[start_index:]}
        for point in points[start_index:]:
            new_segment.append(copy.deepcopy(point.element))
        for segment in track.element.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                if point in move_elements:
                    segment.remove(point)
        get_or_create_track_name(new_track).text = self.unique_split_track_name(track.name)
        first_new_time = points[start_index].time
        if first_new_time is not None:
            get_or_create_track_time(new_track).text = format_gpx_time(first_new_time)
        new_record = TrackRecord(self.next_nr, new_track, track.source_file)
        new_record.set_order_number(max((item.track_number or 0 for item in self.tracks), default=0) + 1)
        self.next_nr += 1
        insert_at = self.tracks.index(track) + 1
        self.tracks.insert(insert_at, new_record)
        self.invalidate_track_metrics(track)
        self.invalidate_track_metrics(new_record)
        self.mark_dirty(
            f"Split {track.display_label} above point {start_index + 1}; "
            f"new {new_record.display_label} created.",
            MutationKind.STRUCTURE,
            {track.nr, new_record.nr},
        )
        return new_record

    @objc.IBAction
    def undo_(self, _sender):
        if not self.undo_stack:
            self.set_status("Nothing to undo.")
            return
        entry = self.undo_stack.pop()
        if isinstance(entry, TimestampUndoEntry):
            try:
                inverse = self.restore_timestamp_states(entry)
            except (ValueError, IndexError) as exc:
                self.undo_stack.append(entry)
                self.set_status(str(exc))
                return
            self.redo_stack.append(inverse)
            for track in self.tracks:
                inspector = self.existing_inspector_for_track(track) if track.nr in entry.values else None
                if inspector is not None and inspector.undo_stack and inspector.undo_stack[-1] is entry:
                    inspector.undo_stack.pop()
                    inspector.redo_stack.append(inverse)
            return
        if isinstance(entry, TrackMetadataUndoEntry):
            self.redo_stack.append(self.restore_metadata(entry))
            return
        if isinstance(entry, VisibilityUndoEntry):
            self.redo_stack.append(entry)
            self.redo_stack = self.redo_stack[-10:]
            self.apply_visibility_states(entry.before, "Undid track visibility change.")
            return
        if isinstance(entry, TrackElementsUndoEntry):
            tracks = {track.nr: track for track in self.tracks}
            inverse = TrackElementsUndoEntry(
                {
                    number: ET.tostring(tracks[number].element, encoding="unicode")
                    for number in entry.elements
                    if number in tracks
                },
                entry.kind,
            )
            self.redo_stack.append(inverse)
            for number, xml_text in entry.elements.items():
                track = tracks.get(number)
                if track is None:
                    continue
                track.element = ET.fromstring(xml_text)
                if entry.kind in {
                    MutationKind.TIMING,
                    MutationKind.ELEVATION,
                    MutationKind.GEOMETRY,
                    MutationKind.STRUCTURE,
                }:
                    self.invalidate_track_metrics(track)
            self.mark_dirty(
                "Undid track edit.", entry.kind, entry.elements.keys()
            )
            self.refresh_open_plot_views()
            return
        if isinstance(entry, TrackOrderUndoEntry):
            current = TrackOrderUndoEntry(tuple(track.nr for track in self.tracks))
            by_number = {track.nr: track for track in self.tracks}
            if set(entry.order) == set(by_number):
                self.redo_stack.append(current)
                self.tracks = [by_number[number] for number in entry.order]
                self.mark_dirty(
                    "Undid track order change.", MutationKind.ORDER, entry.order
                )
            return
        if isinstance(entry, AnchorUndoEntry):
            self.redo_stack.append(AnchorUndoEntry(self.anchor))
            self.anchor = entry.anchor
            self.mark_dirty("Undid anchor change.", MutationKind.METADATA)
            return
        self.redo_stack.append(self.snapshot())
        snapshot = entry
        self.restore_snapshot(snapshot)
        self.dirty = True
        self.set_status("Undo applied.")
        self._notify_workspace_mutation(
            MutationKind.STRUCTURE, "Undo applied.", [track.nr for track in self.tracks]
        )
        self.refresh_open_plot_views()

    @objc.IBAction
    def redo_(self, _sender):
        if not self.redo_stack:
            self.set_status("Nothing to redo.")
            return
        entry = self.redo_stack.pop()
        if isinstance(entry, TimestampUndoEntry):
            try:
                inverse = self.restore_timestamp_states(entry)
            except (ValueError, IndexError) as exc:
                self.redo_stack.append(entry)
                self.set_status(str(exc))
                return
            self.undo_stack.append(inverse)
            for track in self.tracks:
                inspector = self.existing_inspector_for_track(track) if track.nr in entry.values else None
                if inspector is not None and inspector.redo_stack and inspector.redo_stack[-1] is entry:
                    inspector.redo_stack.pop()
                    inspector.undo_stack.append(inverse)
            return
        if isinstance(entry, TrackMetadataUndoEntry):
            self.undo_stack.append(self.restore_metadata(entry))
            return
        if isinstance(entry, VisibilityUndoEntry):
            self.undo_stack.append(entry)
            self.undo_stack = self.undo_stack[-10:]
            self.apply_visibility_states(entry.after, "Redid track visibility change.")
            return
        if isinstance(entry, TrackElementsUndoEntry):
            tracks = {track.nr: track for track in self.tracks}
            inverse = TrackElementsUndoEntry(
                {
                    number: ET.tostring(tracks[number].element, encoding="unicode")
                    for number in entry.elements
                    if number in tracks
                },
                entry.kind,
            )
            self.undo_stack.append(inverse)
            self.undo_stack = self.undo_stack[-10:]
            for number, xml_text in entry.elements.items():
                track = tracks.get(number)
                if track is None:
                    continue
                track.element = ET.fromstring(xml_text)
                if entry.kind in {
                    MutationKind.TIMING,
                    MutationKind.ELEVATION,
                    MutationKind.GEOMETRY,
                    MutationKind.STRUCTURE,
                }:
                    self.invalidate_track_metrics(track)
            self.mark_dirty(
                "Redid track edit.", entry.kind, entry.elements.keys()
            )
            self.refresh_open_plot_views()
            return
        if isinstance(entry, TrackOrderUndoEntry):
            current = TrackOrderUndoEntry(tuple(track.nr for track in self.tracks))
            by_number = {track.nr: track for track in self.tracks}
            if set(entry.order) == set(by_number):
                self.undo_stack.append(current)
                self.undo_stack = self.undo_stack[-10:]
                self.tracks = [by_number[number] for number in entry.order]
                self.mark_dirty(
                    "Redid track order change.", MutationKind.ORDER, entry.order
                )
            return
        if isinstance(entry, AnchorUndoEntry):
            self.undo_stack.append(AnchorUndoEntry(self.anchor))
            self.undo_stack = self.undo_stack[-10:]
            self.anchor = entry.anchor
            self.mark_dirty("Redid anchor change.", MutationKind.METADATA)
            return
        self.undo_stack.append(self.snapshot())
        snapshot = entry
        self.restore_snapshot(snapshot)
        self.dirty = True
        self.set_status("Redo applied.")
        self._notify_workspace_mutation(
            MutationKind.STRUCTURE, "Redo applied.", [track.nr for track in self.tracks]
        )
        self.refresh_open_plot_views()

    def confirm_quit(self) -> bool:
        if not self.dirty:
            return True
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Save changes before quitting?")
        alert.setInformativeText_("The GPX data has unsaved changes.")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Don't Save")
        alert.addButtonWithTitle_("Cancel")
        result = alert.runModal()
        if result == 1000:
            return self.save_current()
        if result == 1001:
            return True
        return False

    @objc.IBAction
    def quit_(self, _sender):
        if self.hide_main_editor_for_workspace():
            return
        if not self.confirm_quit():
            return
        self.quit_confirmed = True
        self.close_main_editor_window(delete_recovery=True)

    @objc.IBAction
    def help_(self, _sender):
        text = (
            "FREE BETA-TEST SOFTWARE — NO WARRANTY\n"
            "This testing version may still contain bugs. Please report any problem using the prominent Report a Bug button below. Your feedback helps improve myCamino for everyone.\n"
            f"Bug-report page: {BUG_REPORT_URL}\n\n"
            f"Feature-request page: {FEATURE_REQUEST_URL}\n\n"
            "A launch without input starts with one empty track. New Track adds another. Add Tracks loads GPX files into the table. Add Points From Clipboard accepts decimal, DMS, geo:, expanded map-link, KML, and GPX point coordinates. Add Media Points reuses or creates sidecars for only the selected media and adds every located medium as a linked raw waypoint; it never performs place lookup, weather retrieval, map generation, or slideshow generation.\n\n"
            "Estimate Track Times fills missing timestamps from neighbours, existing Adventure media, and measured speeds without changing the route. Find Missing Timestamps locates gaps in their original waypoint rows; the inspector's Previous Missing and Next Missing buttons navigate them. The Time Source column distinguishes recorded, manual and estimated values. Ignore with Do not ask again suppresses only automatic reminders for those tracks in this Adventure; explicit tools and new-media offers remain available. Rebuild Route Times from Media is the separate full-retiming/direction operation. Edit a track name or date directly in the table and press Enter; Esc cancels the edit.\n\n"
            "Click a table row to select a track. Shift-click or drag to select more than one. Double-click a track row to open its waypoint table and fit the track in the map, like pressing Plot. Right-click a selected row to inspect, plot, sort, renumber, duplicate, move, show/hide, join, or delete the complete selection. Right-clicking an unselected track selects only that track; the Average / Total row has no track menu. Duplicates are inserted together after the last selected row. Escape cancels an active table-field edit; Cmd-Z reverses a change that was already committed. While uncached track data is loading, scrolling and selection remain available, and editing is enabled when processing finishes.\n\n"
            "Sort the table by clicking a column header; clicking the same header again reverses the direction. Date & Time sorting uses the special placement rule for untimed or zero-duration tracks. The context-menu Sort Selected Tracks dialog provides criterion and direction explicitly. If more than one table row is selected, sorting only reorders those selected rows and leaves all other rows fixed in place. If zero or one row is selected, header sorting applies to the full table. Drag selected rows to reorder tracks. Renumber… previews Track Numbers or Stage Numbers for Selected or All tracks, optionally including hidden tracks. Enter the first number or leave it blank to continue the preceding row. Conflicts with untouched tracks are blocked. Edit Stage cells directly to group tracks; Track IDs remain stable in persistent Track Number order, including hidden tracks. Set Type… assigns a type, and Select by Type… replaces the selection. Types and colors are configured under Track Types in Settings. These metadata edits retain cached metrics and the current viewport. Hidden rows remain numbered but use dashed light gray routes and excluded from the bold summary row's visible-track count. Press Backspace/Delete to delete selected tracks after confirmation.\n\n"
            "Use the selection field to type visible track numbers such as 1,3-5; unnumbered tracks can be selected in the table. Select All and Unselect All change the current track selection. Invert Selection selects unselected tracks and deselects the others, including hidden tracks. Renumber offers Assign Numbers or Clear Numbers, Track Numbers or Stage Numbers, Selected or All, and Include Hidden; clearing also offers Both. Preview and confirm the changes. A Stage cell may also be cleared directly. Undo restores changed values. Internal identities and file references never change when renumbering. Join Tracks merges selected tracks into the first selected track. Set Anchorpoint uses the first point of the current first selected track for distance calculations.\n\n"
            "Plot Overview opens an overview using the configured map service. Selected tracks are dark blue, other tracks are light blue, and marked waypoint ranges remain red. Click a track in the overview to select it in the table. In the Adventure Map, double-click a track to open the Track Table; double-click it there to open the waypoint table. The map context menu can open either table directly.\n\n"
            "Plot Track(s) opens a detailed map for the selected track; an empty track starts on a world map. Right-click Create Waypoint Here to add its first or a later point. View File opens the original GPX source file in TextEdit. Click a map to move the cursor dot and direction arrow to the nearest waypoint; drag to pan. Double-click a linked media point to view its photo or video, or another point to open its waypoint inspector. Press i to show or hide point information; Escape hides it; h shows map keys; a sets the anchorpoint; + and - zoom; c centers on the cursor; z zooms to the current selection; Shift-Z fits the current track; A shows all Adventure tracks; p changes photo-thumbnail display; s saves PNG; q closes the plot window. In the track map, m or Shift-click starts a marked range. Its red segment can be copied, cut, deleted, or dragged through its context menu and long-click gesture. Right-click elsewhere to paste copied points after the nearest waypoint.\n\n"
            "Inspect Track opens all raw waypoints of one track. The read-only XY and Elevation status columns explain quality filtering and interpolation; the header shows retained/raw counts. The processing summary above the main table shows the active XY smoothing, spacing, elevation smoothing, error, and HDOP/VDOP limits. Statistics, maps, PDFs, profiles, and timing use this common segment-aware processed geometry while saved GPX points remain unchanged.\n\n"
            "The gear button edits GPX processing, PDF, and map-service settings. Manage Map Provider opens the shared provider setup, account links, and secure Keychain registration; Esri and Custom XYZ are completed manually in Map Service. Set a smoothing, spacing, or quality limit to zero to disable it. A standalone editor keeps these settings for future sessions; an editor opened from an Adventure stores them with that Adventure. The Output file field shows where the GPX will be written; edit it and press Enter, use the folder button, or press Save to save there. Existing GPX files are backed up as .bak before they are overwritten. PNG saves the currently open track plot image. PDF exports the track table and lets you choose columns, page orientation, folder, and filename. Save & Exit saves and closes the editor. Quit asks whether to save unsaved changes. A recovery file is written periodically while there are unsaved changes."
        )
        self.show_scrollable_help("myCamino GPX Editor Help", text)

    @objc.IBAction
    def showQuickStart_(self, _sender):
        from cocoa_help_document import show_help_document
        show_help_document("myCamino Quick Start", "MYCAMINO_QUICK_START.md")

    def _show_project_document(self, kind, title):
        window = self.license_document_windows.get(kind)
        if window is not None:
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            return
        try:
            document = read_license_document(kind)
        except (OSError, ValueError) as exc:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Document unavailable")
            alert.setInformativeText_(str(exc))
            alert.runModal()
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(240, 160, 760, 600),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_(title)
        scroll = NSScrollView.alloc().initWithFrame_(window.contentView().bounds())
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        text_view = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setRichText_(False)
        text_view.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        text_view.setString_(document)
        scroll.setDocumentView_(text_view)
        window.contentView().addSubview_(scroll)
        self.register_auxiliary_window(window)
        self.license_document_windows[kind] = window
        window.makeKeyAndOrderFront_(None)

    @objc.IBAction
    def showProjectLicense_(self, _sender):
        self._show_project_document("license", "myCamino License — GPL-3.0-or-later")

    @objc.IBAction
    def showAppStoreException_(self, _sender):
        self._show_project_document(
            "app_store_exception", "Apple App Store Distribution Exception"
        )

    @objc.IBAction
    def showAppStoreEULA_(self, _sender):
        self._show_project_document("app_store_eula", "Apple App Store EULA — Draft")

    @objc.IBAction
    def showContributorAgreement_(self, _sender):
        self._show_project_document(
            "contributor_agreement", "Contributor License Agreement"
        )

    @objc.IBAction
    def showAbout_(self, _sender):
        show_alert(
            "myCamino GPX Editor",
            "Create, inspect, edit, combine, and export GPX tracks with myCamino.",
        )

    @objc.IBAction
    def showThirdPartyNotices_(self, _sender):
        self._show_project_document("third_party", "Third-Party Notices")

    @objc.IBAction
    def showSourceCodeInfo_(self, _sender):
        self._show_project_document("source", "Source Code Information")

    @objc.IBAction
    def openBugReport_(self, _sender):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(BUG_REPORT_URL))

    @objc.IBAction
    def openFeatureRequest_(self, _sender):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(FEATURE_REQUEST_URL))

    def show_first_launch_beta_notice(self):
        """Show the beta and warranty warning once for this notice version."""
        defaults = NSUserDefaults.standardUserDefaults()
        if not beta_notice_should_be_shown(defaults.integerForKey_(BETA_NOTICE_PREFERENCE_KEY)):
            return

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Welcome to the free myCamino beta test")
        alert.setInformativeText_(
            "This is beta-test software supplied free of charge and without any warranty. "
            "It may contain bugs. Please report problems so that myCamino can be improved "
            "for everyone. The bug-report link is always available from Help."
        )
        alert.addButtonWithTitle_("Continue")
        alert.addButtonWithTitle_("Report a Bug")
        response = int(alert.runModal())
        defaults.setInteger_forKey_(BETA_NOTICE_VERSION, BETA_NOTICE_PREFERENCE_KEY)
        defaults.synchronize()
        if response == 1001:
            self.openBugReport_(None)

    def show_scrollable_help(self, title: str, text: str):
        if self.help_window is not None:
            self.help_window.makeKeyAndOrderFront_(None)
            self.help_window.orderFrontRegardless()
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180, 180, 900, 560),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_(title)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(18, 58, 864, 484))
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        text_view = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        text_view.setMinSize_(NSMakeSize(0, 0))
        text_view.setMaxSize_(NSMakeSize(1.0e7, 1.0e7))
        text_view.setVerticallyResizable_(True)
        text_view.setHorizontallyResizable_(False)
        text_view.setAutoresizingMask_(NSViewWidthSizable)
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setFont_(NSFont.systemFontOfSize_(14))
        text_view.setString_(text)
        text_view.textContainer().setContainerSize_(NSMakeSize(scroll.contentView().bounds().size.width, 1.0e7))
        text_view.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(text_view)
        window.contentView().addSubview_(scroll)
        for title, action, x_pos, width in (
            ("License", "showProjectLicense:", 18, 100),
            ("Third-Party Notices", "showThirdPartyNotices:", 128, 170),
            ("Source Code", "showSourceCodeInfo:", 308, 120),
            ("Report a Bug", "openBugReport:", 438, 120),
            ("Request a Feature", "openFeatureRequest:", 568, 150),
        ):
            button = NSButton.alloc().initWithFrame_(NSMakeRect(x_pos, 18, width, 28))
            button.setTitle_(title)
            button.setTarget_(self)
            button.setAction_(action)
            apply_liquid_glass_button_style(button)
            window.contentView().addSubview_(button)
        window.makeKeyAndOrderFront_(None)
        self.help_window_delegate = ReusableToolWindowDelegate.alloc().init()
        window.setDelegate_(self.help_window_delegate)
        self.register_auxiliary_window(window)
        self.help_window = window

    @objc.IBAction
    def autosave_(self, _timer):
        if not self.dirty or (not self.tracks and not getattr(self, "document_ids_initialized", False)):
            return
        try:
            self.save_to_path(RECOVERY_PATH, autosave=True)
        except OSError as exc:
            self.set_status(f"Autosave failed: {exc}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="GPXEditor.py",
        description="Open the myCamino GPX Editor.",
    )
    parser.add_argument(
        "gpx_files",
        nargs="*",
        help="Optional .gpx file(s) to load sequentially when the editor starts.",
    )
    parser.add_argument(
        "--output-file",
        metavar="ofile.gpx",
        help="Default GPX filename, including directory, used by Save and Save & Exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print plot rendering benchmark diagnostics to the terminal.",
    )
    return parser


def startup_options_from_args(args, parser: argparse.ArgumentParser | None = None):
    """Validate parsed CLI options and return ``(startup_paths, output_path, debug)``.

    This helper is shared by the standalone CLI and the importable
    ``show_gpx_editor_from_cli_args()`` entry point so both interfaces accept
    the same arguments.
    """
    startup_paths = []
    output_path = None
    if args.output_file:
        candidate = Path(args.output_file).expanduser()
        if candidate.suffix.lower() != ".gpx":
            if parser is not None:
                parser.error("--output-file must have a .gpx extension")
            raise ValueError("--output-file must have a .gpx extension")
        output_path = candidate.resolve()
    for value in args.gpx_files:
        startup_path = Path(value).expanduser()
        if startup_path.suffix.lower() != ".gpx":
            if parser is not None:
                parser.error("startup files must have a .gpx extension")
            raise ValueError(f"startup file must have a .gpx extension: {startup_path}")
        if not startup_path.exists():
            if parser is not None:
                parser.error(f"file not found: {startup_path}")
            raise FileNotFoundError(startup_path)
        startup_paths.append(startup_path.resolve())
    return startup_paths, output_path, bool(args.debug)


def build_gpx_editor_menu(controller, window_menu_coordinator=None):
    """Build the native menu used by the standalone GPX Editor."""
    main_menu = NSMenu.alloc().initWithTitle_("Main Menu")
    app_menu = add_menu(main_menu, "myCamino")
    app_menu.addItem_(menu_item("About myCamino", "showAbout:", controller))
    app_menu.addItem_(menu_item("Settings…", "showSettings:", controller, ",", NSEventModifierFlagCommand))
    app_menu.addItem_(NSMenuItem.separatorItem())
    app_menu.addItem_(menu_item("License", "showProjectLicense:", controller))
    app_menu.addItem_(menu_item("App Store Distribution Exception", "showAppStoreException:", controller))
    app_menu.addItem_(menu_item("App Store EULA Draft", "showAppStoreEULA:", controller))
    app_menu.addItem_(menu_item("Contributor Agreement", "showContributorAgreement:", controller))
    app_menu.addItem_(menu_item("Third-Party Notices", "showThirdPartyNotices:", controller))
    install_application_news(app_menu)
    app_menu.addItem_(NSMenuItem.separatorItem())
    app_menu.addItem_(menu_item("Quit myCamino", "terminate:", NSApp(), "q", NSEventModifierFlagCommand))

    file_menu = add_menu(main_menu, "File")
    file_menu.addItem_(menu_item("Add GPX Files…", "addTracks:", controller, "o", NSEventModifierFlagCommand))
    file_menu.addItem_(menu_item("Save", "save:", controller, "s", NSEventModifierFlagCommand))
    file_menu.addItem_(menu_item("Save Selected Tracks As…", "saveSelectedTracksAs:", controller))
    file_menu.addItem_(menu_item("PDF Summary…", "exportPdf:", controller))

    edit_menu = add_menu(main_menu, "Edit")
    edit_menu.addItem_(menu_item("Undo", "undo:", controller, "z", NSEventModifierFlagCommand))
    edit_menu.addItem_(menu_item("Redo", "redo:", controller, "Z", NSEventModifierFlagCommand | NSEventModifierFlagShift))

    track_menu = add_menu(main_menu, "Track")
    for title, action in (
        ("New Track", "newTrack:"),
        ("Track Table & Editor", "showTrackTable:"),
        ("Waypoint Table", "inspectTrack:"),
        ("Plot Track(s)", "plotSelected:"),
        ("Elevation Profile", "contextElevationProfile:"),
        ("Estimate Track Times…", "estimateTrackTimes:"),
        ("Find Missing Timestamps…", "findMissingTimestamps:"),
        ("Rebuild Route Times from Media…", "rebuildRouteTimesFromMedia:"),
        ("Renumber…", "renumberTracks:"),
        ("Set Track Type…", "setTrackType:"),
        ("Select Tracks by Type…", "selectTracksByType:"),
        ("Invert Selection", "invertTrackSelection:"),
        ("Save Selected Tracks As…", "saveSelectedTracksAs:"),
    ):
        track_menu.addItem_(menu_item(title, action, controller))

    window_menu = add_menu(main_menu, "Window")
    coordinator = window_menu_coordinator or WindowMenuCoordinator.alloc().init()
    coordinator.owner = controller
    coordinator.attach(window_menu)
    controller.native_window_menu_coordinator = coordinator

    help_menu = add_menu(main_menu, "Help")
    help_menu.addItem_(menu_item("Quick Start", "showQuickStart:", controller))
    help_menu.addItem_(menu_item("GPX Editor Help", "help:", controller))
    help_menu.addItem_(menu_item("Report a Bug", "openBugReport:", controller))
    help_menu.addItem_(menu_item("Request a Feature", "openFeatureRequest:", controller))
    return main_menu


class AppDelegate(NSObject):
    def initWithStartupPaths_outputPath_debug_(self, startup_paths, output_path, debug):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.startup_paths = startup_paths
        self.output_path = output_path
        self.debug = debug
        return self

    def applicationDidFinishLaunching_(self, _notification):
        configure_mycamino_branding(bundled_resource_path("MyCaminoLogo-ohneText.png"))
        self.controller = GPXEditorController.alloc().initStandalone_(True)
        self.window_menu_coordinator = WindowMenuCoordinator.alloc().init()
        self.main_menu = build_gpx_editor_menu(
            self.controller,
            self.window_menu_coordinator,
        )
        NSApp().setMainMenu_(self.main_menu)
        self.controller.debug = bool(self.debug)
        if self.output_path is not None:
            self.controller.set_output_path(self.output_path)
            self.controller.set_status(f"Default save file: {self.output_path}")
        self.controller.show()
        NSApp().activateIgnoringOtherApps_(True)
        self.controller.show_first_launch_beta_notice()
        loaded_recovery = self.controller.offer_recovery_load()
        if self.startup_paths:
            self.controller.load_gpx_paths(self.startup_paths, mark_dirty=False)
        elif loaded_recovery:
            self.controller.recalculate()
        else:
            track = self.controller.create_empty_track(mark_dirty=True)
            self.controller.open_plot_window(
                "track",
                tracks_override=[track],
                open_profile=False,
            )


def show_gpx_editor(
    gpx_paths: list[str | os.PathLike] | None = None,
    standalone: bool = False,
    output_file: str | os.PathLike | None = None,
    debug: bool = False,
    on_close=None,
    on_save=None,
    on_settings_change=None,
    on_initial_load_complete=None,
    settings=None,
    media_context=None,
    show_window=True,
    open_pdf_summary=False,
):
    controller = GPXEditorController.alloc().initStandalone_(standalone)
    if settings is not None:
        controller.apply_project_parameters(settings)
    controller.debug = bool(debug)
    controller.on_close_callback = on_close
    controller.on_save_callback = on_save
    controller.on_settings_change_callback = on_settings_change
    controller.on_initial_load_complete_callback = on_initial_load_complete
    controller.media_context = dict(media_context or {})
    if output_file is not None:
        controller.set_output_path(Path(output_file).expanduser().resolve())
    controller.pdf_only_loading = bool(open_pdf_summary and not show_window)
    if show_window:
        controller.show()
    if open_pdf_summary:
        controller.open_pdf_summary_window()
    loaded_recovery = controller.offer_recovery_load() if show_window else False
    if gpx_paths:
        controller.load_gpx_paths([Path(path).expanduser().resolve() for path in gpx_paths], mark_dirty=False)
    elif loaded_recovery:
        controller.recalculate()
        controller.notify_initial_load_complete()
    else:
        track = controller.create_empty_track(mark_dirty=True)
        controller.open_plot_window(
            "track",
            tracks_override=[track],
            open_profile=False,
        )
        controller.notify_initial_load_complete()
    return controller


def show_gpx_editor_from_cli_args(
    cli_args: list[str] | tuple[str, ...] | None = None,
    standalone: bool = False,
    on_close=None,
    on_save=None,
    on_settings_change=None,
    on_initial_load_complete=None,
    settings=None,
    media_context=None,
    show_window=True,
    open_pdf_summary=False,
):
    """Open the editor from another Python program using CLI-style arguments.

    Example from an already running PyObjC/Cocoa application::

        from GPXEditor import show_gpx_editor_from_cli_args

        controller = show_gpx_editor_from_cli_args([
            "--output-file", "/tmp/edited.gpx",
            "--debug",
            "/tmp/day1.gpx",
            "/tmp/day2.gpx",
        ])

    The accepted arguments are exactly the same as the standalone command
    line: zero or more ``*.gpx`` files, ``--output-file ofile.gpx``, and
    ``--debug``.  The GPX files are loaded sequentially in the order supplied.

    By default this shows the editor. Use ``show_window=False`` with
    ``open_pdf_summary=True`` to open report options immediately instead.
    It does not start the AppKit event loop. From a non-GUI script, create/run
    ``NSApplication`` yourself or use ``run_gpx_editor_from_cli_args()`` below.
    """
    parser = build_argument_parser()
    args = parser.parse_args(list(cli_args or []))
    startup_paths, output_path, debug = startup_options_from_args(args, parser=None)
    return show_gpx_editor(
        startup_paths,
        standalone=standalone,
        output_file=output_path,
        debug=debug,
        on_close=on_close,
        on_save=on_save,
        on_settings_change=on_settings_change,
        on_initial_load_complete=on_initial_load_complete,
        settings=settings,
        media_context=media_context,
        show_window=show_window,
        open_pdf_summary=open_pdf_summary,
    )


def export_pdf_summary_from_paths(
    gpx_paths: list[str | os.PathLike] | tuple[str | os.PathLike, ...],
    output_file: str | os.PathLike | None = None,
    project_name: str | None = None,
    debug: bool = False,
    settings=None,
) -> GPXEditorController | None:
    """Return a retained, nonmodal PDF controller; its editor stays hidden."""
    paths = [Path(path).expanduser().resolve() for path in gpx_paths or []]
    if not paths:
        show_alert("No GPX file selected.", "Select a GPX file before exporting a PDF summary.")
        return None
    controller = show_gpx_editor(
        paths, output_file=output_file, debug=debug, settings=settings,
        show_window=False, open_pdf_summary=True,
    )
    if project_name:
        controller.project_name = str(project_name)
        controller.project_field.setStringValue_(controller.project_name)
    return controller


def run_gpx_editor_from_cli_args(cli_args: list[str] | tuple[str, ...] | None = None) -> int:
    """Run the standalone editor application from CLI-style argument strings.

    This is useful for another Python launcher that wants to pass the same
    arguments accepted by ``GPXEditor.py`` and let this module own the AppKit
    event loop.
    """
    parser = build_argument_parser()
    args = parser.parse_args(list(cli_args or []))
    startup_paths, output_path, debug = startup_options_from_args(args, parser=parser)
    configure_mycamino_branding()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().initWithStartupPaths_outputPath_debug_(startup_paths, output_path, debug)
    app.setDelegate_(delegate)
    app.run()
    return 0


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    startup_paths, output_path, debug = startup_options_from_args(args, parser=parser)

    configure_mycamino_branding()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().initWithStartupPaths_outputPath_debug_(startup_paths, output_path, debug)
    app.setDelegate_(delegate)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
