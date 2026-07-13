#!/Users/falcke/Dropbox/Documents/Python/trackit/.venv/bin/python
"""Native macOS GPX track editor using Cocoa/AppKit.

The module can be launched standalone or imported by another PyObjC GUI.  The
public entry point for embedding is ``show_gpx_editor``.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

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
    NSButtonTypeSwitch,
    NSColor,
    NSCompositingOperationSourceOver,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageAlignCenter,
    NSImageNameFolder,
    NSImageOnly,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSMakeSize,
    NSZeroRect,
    NSOpenPanel,
    NSPopUpButton,
    NSRoundLineCapStyle,
    NSRoundLineJoinStyle,
    NSSavePanel,
    NSScrollView,
    NSSortDescriptor,
    NSTableColumn,
    NSTableView,
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
    NSString,
    NSTimer,
    NSURL,
)

from cocoa_button_style import apply_liquid_glass_button_style, make_liquid_glass_button
from basemap_tile_utils import tolerate_missing_tiles
from adventure_parameters import (
    EDITOR_PARAMETER_KEYS,
    EDITOR_PARAMETER_SECTIONS,
    default_parameters,
    normalize_parameters,
    parameter_subset,
)
from cocoa_parameter_editor import CocoaParameterEditor
from json_storage import atomic_write_json, load_parameter_subset, parameter_subset_payload
from map_provider_utils import contextily_provider, contextily_request_timeout, provider_display_name
from track_timing_utils import timestamps_from_start

try:
    from gpx_tracks_table import (
        format_datetime_local,
        haversine_km,
        lonlat_to_web_mercator,
        parse_time,
        render_track_plot,
    )
except ImportError:  # pragma: no cover - fallback for unusual embedding paths
    def parse_time(value):
        if not value:
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def format_datetime_local(dt_value):
        return "N/A" if dt_value is None else dt_value.astimezone().strftime("%d.%m.%Y %H:%M")

    def haversine_km(lat1, lon1, lat2, lon2):
        radius_km = 6371.0088
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return radius_km * 2 * math.asin(math.sqrt(a))

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
APP_CACHE_DIR = Path.home() / "Library" / "Caches" / "myCamino-GPXEditor"
TILE_CACHE_DIR = APP_CACHE_DIR / "tiles"
MPL_CACHE_DIR = APP_CACHE_DIR / "matplotlib"
RECOVERY_PATH = Path(tempfile.gettempdir()) / "myCamino-GPXEditor-recovery.gpx"
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE_DIR))


def nsstring(value: str) -> NSString:
    return NSString.stringWithString_(value)


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
    removed_invalid_coordinates = 0
    removed_invalid_timestamps = 0
    removed_out_of_order = 0
    valid_points: list[tuple[ET.Element, ET.Element, datetime]] = []
    for segment in track_element.findall("gpx:trkseg", NS):
        for point in list(segment.findall("gpx:trkpt", NS)):
            try:
                lat = float(point.attrib["lat"])
                lon = float(point.attrib["lon"])
            except (KeyError, TypeError, ValueError):
                segment.remove(point)
                removed_invalid_coordinates += 1
                continue
            if not (math.isfinite(lat) and math.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                segment.remove(point)
                removed_invalid_coordinates += 1
                continue
            time_text = point.findtext("gpx:time", default="", namespaces=NS)
            if not time_text:
                segment.remove(point)
                removed_invalid_timestamps += 1
                continue
            parsed_time = parse_time(time_text)
            if parsed_time is None:
                segment.remove(point)
                removed_invalid_timestamps += 1
                continue
            valid_points.append((segment, point, parsed_time))
    last_time = None
    for segment, point, parsed_time in valid_points:
        if last_time is not None and parsed_time < last_time:
            segment.remove(point)
            removed_out_of_order += 1
            continue
        last_time = parsed_time
    return {
        "coordinates": removed_invalid_coordinates,
        "timestamps": removed_invalid_timestamps,
        "out_of_order": removed_out_of_order,
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


def format_gpx_time(dt_value: datetime) -> str:
    return dt_value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


@dataclass
class TrackRecord:
    nr: int
    element: ET.Element
    source_file: str = ""
    metrics: dict = field(default_factory=dict)
    metrics_dirty: bool = True

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
        for point in iter_track_points(self.element):
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
            points.append(PointInfo(point, lat, lon, ele, parse_time(point.findtext("gpx:time", default="", namespaces=NS))))
        return points


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
        return self.controller.table_rows[row].get(str(table_column.identifier()), "")

    def tableView_setObjectValue_forTableColumn_row_(self, _table_view, value, table_column, row):
        self.controller.edit_table_value(row, str(table_column.identifier()), str(value))

    def tableView_shouldSelectRow_(self, _table_view, row):
        clicked_column = _table_view.clickedColumn()
        if clicked_column >= 0:
            column = _table_view.tableColumns()[clicked_column]
            if str(column.identifier()) == "show":
                current = self.controller.table_rows[row].get("show", "") if row < len(self.controller.table_rows) else ""
                self.controller.edit_table_value(row, "show", "no" if current == "yes" else "yes")
                return False
        return True

    def tableView_shouldEditTableColumn_row_(self, _table_view, table_column, row):
        return self.controller.is_editable_cell(row, str(table_column.identifier()))

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
        rows = [index for index in range(row_indexes.firstIndex(), row_indexes.lastIndex() + 1) if row_indexes.containsIndex_(index)]
        if not rows:
            return False
        pasteboard.declareTypes_owner_([DRAG_TYPE], None)
        pasteboard.setString_forType_(",".join(str(row) for row in rows), DRAG_TYPE)
        return True

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, _table_view, _info, row, _operation):
        return 1 if 0 <= row <= len(self.controller.tracks) else 0

    def tableView_acceptDrop_row_dropOperation_(self, _table_view, info, row, _operation):
        text = str(info.draggingPasteboard().stringForType_(DRAG_TYPE) or "")
        indexes = [int(part) for part in text.split(",") if part.isdigit()]
        if not indexes:
            return False
        self.controller.move_rows(indexes, row)
        return True


class EditorTableView(NSTableView):
    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        if key in {"\x7f", "\uf728"} and getattr(self, "controller", None) is not None:
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
        self.show_help = False
        self.show_track_numbers = False
        self.show_endpoint_markers = True
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
        self.initial_plot_info = self.clone_plot_info(self.plot_info)
        self.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        return self

    def acceptsFirstResponder(self):
        return True

    def drawRect_(self, _rect):
        bounds = self.bounds()
        NSColor.windowBackgroundColor().setFill()
        NSBezierPath.fillRect_(bounds)
        self.draw_plot_image(bounds)
        tracks = self.display_tracks()
        points = [point for track in tracks for point in track.points()]
        if not points:
            self.draw_text("No track points available.", 20, bounds.size.height - 40, 16)
            return
        transformer = self._metadata_transformer(points, bounds) or self._point_transformer(points, bounds)
        if self.plot_info.get("image") is None:
            for track in tracks:
                self.draw_track(track.points(), transformer, NSColor.systemBlueColor(), 3.5)
        if self.mode == "overview" and self.show_endpoint_markers:
            self.draw_overview_track_endpoint_dots(transformer)
        self.draw_selected_tracks_overlay(transformer)
        self.draw_selection_overlay(transformer)
        if self.mode == "overview":
            self.draw_overview_endpoint_markers(transformer)
        if self.mode == "track" and tracks:
            self.draw_track_start_end_markers(tracks[0], transformer)
        if self.mode == "overview" and self.show_track_numbers:
            self.draw_track_number_labels(transformer)
        if self.cursor is not None:
            point = self.cursor[1]
            x, y = transformer(point)
            self.draw_cursor_arrow(transformer, bounds)
            NSColor.whiteColor().setFill()
            NSColor.systemBlueColor().setStroke()
            dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 7, y - 7, 14, 14))
            dot.setLineWidth_(2.0)
            dot.fill()
            dot.stroke()
            if self.show_info:
                self.draw_overlay(point, self.cursor[0], bounds)
        if self.show_help:
            self.draw_help(bounds)
        elif self.transient_help_until and time.monotonic() < self.transient_help_until:
            self.draw_overlay_panel(["Press h for help on keys"], bounds, width=260.0, centered=True)
        elif self.transient_help_until:
            self.transient_help_until = None

    def clearTransientHelp_(self, _timer):
        if self.transient_help_until is not None and time.monotonic() >= self.transient_help_until:
            self.transient_help_until = None
            self.setNeedsDisplay_(True)

    def display_tracks(self) -> list[TrackRecord]:
        if self.mode == "overview":
            return self.controller.visible_tracks()
        sequence = self.track_sequence()
        if not sequence:
            return []
        self.track_index = max(0, min(self.track_index, len(sequence) - 1))
        return [sequence[self.track_index]]

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
            tracks_info = self.plot_info.get("tracks")
            self.plot_info.update(next_info)
            if tracks_info is not None:
                self.plot_info["tracks"] = tracks_info
            self.plot_info["current_track_nr"] = track.nr
            self.plot_info.setdefault("base_extent_mercator", self.plot_info.get("metadata", {}).get("extent_mercator"))
        self.update_window_title()

    def current_track_for_title(self):
        if self.mode != "track":
            return None
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
        if self.mode == "overview":
            window.setTitle_(f"{PROGRAM_TITLE} - Overview")
            return
        track = self.current_track_for_title()
        if track is None:
            window.setTitle_(f"{PROGRAM_TITLE} - Track")
        else:
            window.setTitle_(f"{PROGRAM_TITLE} - Track #{track.nr}: {track.name}")

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
        image = self.plot_info.get("image")
        if image is None:
            return
        image.drawInRect_fromRect_operation_fraction_(
            self.image_rect(bounds),
            NSZeroRect,
            NSCompositingOperationSourceOver,
            1.0,
        )

    def _metadata_transformer(self, points: list[PointInfo], bounds):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator") or {}
        axes = metadata.get("axes_box_fraction") or {}
        image = self.plot_info.get("image")
        required = ("min_x", "max_x", "min_y", "max_y")
        if image is None or any(key not in extent for key in required):
            return None
        image_rect = self.image_rect(bounds)
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

    def draw_track(self, points: list[PointInfo], transformer, color, width):
        if len(points) < 2:
            return
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(width)
        path.setLineJoinStyle_(NSRoundLineJoinStyle)
        path.setLineCapStyle_(NSRoundLineCapStyle)
        x, y = transformer(points[0])
        path.moveToPoint_((x, y))
        for point in points[1:]:
            x, y = transformer(point)
            path.lineToPoint_((x, y))
        color.setStroke()
        path.stroke()

    def draw_selected_tracks_overlay(self, transformer):
        if self.mode != "overview":
            return
        selected = set(self.controller.selected_nrs)
        if not selected:
            return
        tracks = [track for track in self.controller.tracks if track.nr in selected and not track.hidden]
        for track in tracks:
            points = track.points()
            self.draw_track(points, transformer, NSColor.systemRedColor(), 7.0)

    def draw_overview_endpoint_markers(self, transformer):
        if self.mode != "overview":
            return
        tracks_with_points = [(track, track.points()) for track in self.display_tracks() if track.points()]
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
            points = track.points()
            if not points:
                continue
            stroke_color = NSColor.systemRedColor() if track.nr in selected else NSColor.systemBlueColor()
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
            points = track.points()
            if not points:
                continue
            point = points[len(points) // 2]
            x, y = transformer(point)
            self.draw_label_box(str(track.nr), x + 8, y + 8)

    def draw_track_start_end_markers(self, track: TrackRecord, transformer):
        points = track.points()
        if not points:
            return
        for label, point in (("Start", points[0]), ("End", points[-1])):
            x, y = transformer(point)
            if self.show_endpoint_markers:
                NSColor.whiteColor().setFill()
                NSColor.blackColor().setStroke()
                dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 6, y - 6, 12, 12))
                dot.setLineWidth_(2.0)
                dot.fill()
                dot.stroke()
            self.draw_label_box(label, x + 9, y + 7)

    def draw_label_box(self, text, x, y):
        width = max(44.0, len(text) * 7.0 + 14.0)
        height = 20.0
        rect = NSMakeRect(x, y, width, height)
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.72).setFill()
        panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 5.0, 5.0)
        panel.fill()
        self.draw_text(text, x + 7, y + 4, 12, NSColor.whiteColor())

    def draw_selection_overlay(self, transformer):
        if self.mode != "track":
            return
        displayed = self.display_tracks()
        if not displayed:
            return
        track = displayed[0]
        for start, end in self.selected_ranges_for_track(track):
            self.draw_selected_point_range(track, transformer, start, end)

    def selected_ranges_for_track(self, track: TrackRecord):
        ranges = []
        if self.marker is not None and self.cursor is not None and self.cursor[2] is track:
            ranges.append((min(self.marker, self.cursor[0]), max(self.marker, self.cursor[0])))
        elif self.inspector is not None and track is self.inspector.track:
            ranges.extend(self.group_point_indexes(self.inspector.selected_row_indexes()))
        return ranges

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
        if len(segment) >= 2:
            self.draw_track(segment, transformer, NSColor.systemRedColor(), 8.0)
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
        if point_index <= 0:
            first, second = points[0], points[1]
        elif point_index >= len(points) - 1:
            first, second = points[-2], points[-1]
        else:
            first, second = points[point_index - 1], points[point_index + 1]
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
        tip_x = pixel_x + normal_x * radius * 2.0
        tip_y = pixel_y + normal_y * radius * 2.0
        head_base_x = tip_x + normal_x * head_length
        head_base_y = tip_y + normal_y * head_length
        shaft_base_x = tip_x + normal_x * arrow_length
        shaft_base_y = tip_y + normal_y * arrow_length
        side_x, side_y = -normal_y, normal_x
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

    def draw_overlay_panel(self, lines, bounds, title=None, width=440.0, centered=False):
        line_height = 19.0
        title_height = 24.0 if title else 0.0
        padding = 16.0
        panel_height = padding * 2 + title_height + line_height * len(lines)
        panel_width = min(width, max(260.0, bounds.size.width - 32.0))
        if centered:
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
        rows = [
            f"Track Point: {point_index + 1}/{len(track.points())}   Time: {point_time}   Lat/Lon: {point.lat:.6f}, {point.lon:.6f}   Height: {point_height}",
            f"Track #{track.nr}: {track.name}",
            f"Length: {metrics['length_km']:.1f} km   Duration: {format_duration(metrics['duration'], allow_days=True)}   Avg: {format_speed(metrics.get('speed_kmh'))}",
            "Track time: "
            f"{format_datetime_local(metrics.get('start_time')) if metrics.get('start_time') else 'N/A'} - "
            f"{format_datetime_local(metrics.get('end_time')) if metrics.get('end_time') else 'N/A'}",
            f"Anchor: {distance_anchor:.1f} km   Start: {distance_start:.1f} km",
            f"Elapsed: {format_duration(elapsed, allow_days=True)}   Left: {format_duration(remaining, allow_days=True)}",
        ]
        if selected_metrics is not None:
            rows.insert(
                3,
                "Selected: "
                f"{selected_metrics['length_km']:.1f} km, "
                f"{format_duration(selected_metrics['duration'], allow_days=True)}, "
                f"{format_speed(selected_metrics['speed_kmh'])}, "
                f"+{selected_metrics['ascent_m']:.1f}/-{selected_metrics['descent_m']:.1f} m",
            )
            rows.insert(
                4,
                "Selected time: "
                f"{format_datetime_local(selected_metrics['start_time']) if selected_metrics['start_time'] else 'N/A'} - "
                f"{format_datetime_local(selected_metrics['end_time']) if selected_metrics['end_time'] else 'N/A'}",
            )
        self.draw_overlay_panel(rows, bounds, title=None, width=720.0)

    def draw_help(self, bounds):
        help_lines = [
            "h: toggle this help",
            "i: toggle point information",
            "a: set current point as anchor for table distances",
            "+ / -: zoom in / out around the current view",
            "c: center map on current cursor point",
            "r: zoom out to the full map extent",
            "z: zoom to selected tracks or selected points",
            "q: close this plot window",
            "p: save current plot as PNG",
            "u: clear current plot selection",
            "e: open elevation profile",
            "d: toggle start/end dots",
            "click/drag: move cursor to nearest point",
            "shift-click track point: set marker",
            "overview double-click: open that track and waypoint inspector",
            "double-click track point: open the waypoint inspector",
            "overview: n toggles track numbers",
            "track: arrows/space next track, m marker, delete range, x cut",
        ]
        self.draw_overlay_panel(help_lines, bounds, title="myCamino GPX Editor Keys", width=560.0, centered=True)

    def clone_plot_info(self, info):
        if isinstance(info, dict):
            return {key: self.clone_plot_info(value) for key, value in info.items()}
        if isinstance(info, list):
            return [self.clone_plot_info(value) for value in info]
        return info

    def mouseDown_(self, event):
        self.move_cursor_to_event(event)
        if self.mode == "track" and self.cursor is not None and event.modifierFlags() & NSEventModifierFlagShift:
            self.marker = self.cursor[0]
            self.sync_inspector_selection()
            self.controller.refresh_elevation_profile_for_plot_view(self)
            self.controller.set_status(f"Marker set at point {self.marker + 1} of track #{self.cursor[2].nr}.")
        if self.mode == "overview" and event.clickCount() >= 2 and self.cursor is not None:
            self.controller.open_track_workflow_at_point(self.cursor[2], self.cursor[0])
        elif self.mode == "track" and event.clickCount() >= 2 and self.cursor is not None:
            inspector = self.controller.open_inspector_for_track(self.cursor[2])
            if inspector is not None:
                self.inspector = inspector
                inspector.plot_view = self
                inspector.select_point_index(self.cursor[0])
                inspector.window.makeKeyAndOrderFront_(None)
                inspector.window.orderFrontRegardless()

    def mouseDragged_(self, event):
        self.move_cursor_to_event(event)

    def scrollWheel_(self, event):
        self.pan_map(float(event.scrollingDeltaX()), float(event.scrollingDeltaY()))

    def pan_map(self, delta_x: float, delta_y: float):
        extent = self.shifted_extent(delta_x, delta_y)
        if extent is None:
            return
        tile_zoom = int(self.plot_info.get("tile_zoom_level", self.plot_info.get("zoom_level", 14 if self.mode == "track" else 8)))
        tracks = self.display_tracks()
        new_info = self.controller.render_viewport_plot(self.mode, tracks, extent, tile_zoom)
        if new_info is None:
            return
        current_track_nr = self.plot_info.get("current_track_nr")
        base_extent = self.plot_info.get("base_extent_mercator")
        self.plot_info.update(new_info)
        if current_track_nr is not None:
            self.plot_info["current_track_nr"] = current_track_nr
        if base_extent is not None:
            self.plot_info["base_extent_mercator"] = base_extent
        self.zoom = 1.0
        self.setNeedsDisplay_(True)

    def render_extent(self, extent: dict, requested_tile_zoom: int | None = None, status: str = "Rendered OSM viewport."):
        if self.rendering_map:
            return
        if requested_tile_zoom is None:
            requested_tile_zoom = int(self.plot_info.get("tile_zoom_level", self.plot_info.get("zoom_level", 14 if self.mode == "track" else 8)))
        tracks = self.display_tracks() if self.mode == "track" else self.controller.visible_tracks()
        signature = self.controller.viewport_signature(self.mode, tracks, extent, requested_tile_zoom)
        if signature == self.last_viewport_signature:
            self.controller.set_status("Skipped duplicate map render.")
            return
        self.rendering_map = True
        try:
            new_info = self.controller.render_viewport_plot(self.mode, tracks, extent, requested_tile_zoom)
        finally:
            self.rendering_map = False
        if new_info is None:
            return
        current_track_nr = self.plot_info.get("current_track_nr")
        base_extent = self.plot_info.get("base_extent_mercator")
        tracks_info = self.plot_info.get("tracks")
        self.plot_info.update(new_info)
        if tracks_info is not None:
            self.plot_info["tracks"] = tracks_info
        if current_track_nr is not None:
            self.plot_info["current_track_nr"] = current_track_nr
        if base_extent is not None:
            self.plot_info["base_extent_mercator"] = base_extent
        self.zoom = 1.0
        self.last_viewport_signature = signature
        self.update_window_title()
        tile_status = new_info.get("status_message")
        self.controller.set_status(f"{status} {tile_status}" if tile_status else status)
        self.setNeedsDisplay_(True)

    def rerender_current_map(self):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not extent:
            self.setNeedsDisplay_(True)
            return
        tile_zoom = int(self.plot_info.get("tile_zoom_level", self.plot_info.get("zoom_level", 14 if self.mode == "track" else 8)))
        tracks = self.display_tracks()
        new_info = self.controller.render_viewport_plot(self.mode, tracks, dict(extent), tile_zoom)
        if new_info is None:
            self.setNeedsDisplay_(True)
            return
        current_track_nr = self.plot_info.get("current_track_nr")
        base_extent = self.plot_info.get("base_extent_mercator")
        self.plot_info.update(new_info)
        if current_track_nr is not None:
            self.plot_info["current_track_nr"] = current_track_nr
        if base_extent is not None:
            self.plot_info["base_extent_mercator"] = base_extent
        self.cursor = None
        self.marker = None
        self.setNeedsDisplay_(True)

    def shifted_extent(self, delta_x: float, delta_y: float):
        metadata = self.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator")
        if not extent:
            return None
        bounds = self.bounds()
        width = max(bounds.size.width, 1.0)
        height = max(bounds.size.height, 1.0)
        min_x = float(extent["min_x"])
        max_x = float(extent["max_x"])
        min_y = float(extent["min_y"])
        max_y = float(extent["max_y"])
        span_x = max_x - min_x
        span_y = max_y - min_y
        shift_x = -delta_x / width * span_x
        shift_y = delta_y / height * span_y
        return {
            "min_x": min_x + shift_x,
            "max_x": max_x + shift_x,
            "min_y": min_y + shift_y,
            "max_y": max_y + shift_y,
        }

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
        if self.mode == "overview":
            tracks = self.controller.selected_tracks()
            if not tracks:
                tracks = self.controller.visible_tracks()
            tracks = [track for track in tracks if not track.hidden]
            extent = self.controller.extent_for_track_records(tracks)
            requested_zoom = self.controller.overview_zoom_for_tracks(
                tracks, int(self.plot_info.get("zoom_level", self.controller.overview_zoom))
            )
            self.render_extent(extent, requested_zoom, "Zoomed overview to selected tracks.")
            return
        tracks = self.display_tracks()
        if not tracks:
            return
        track = tracks[0]
        points = track.points()
        selected_indexes = []
        if self.marker is not None and self.cursor is not None and self.cursor[2] is track:
            selected_indexes = list(range(min(self.marker, self.cursor[0]), max(self.marker, self.cursor[0]) + 1))
        elif self.inspector is not None and self.inspector.track is track:
            selected_indexes = self.inspector.selected_row_indexes()
        selected_points = [points[index] for index in selected_indexes if 0 <= index < len(points)]
        if not selected_points:
            selected_points = points
        extent = self.extent_for_points(selected_points)
        if extent is None:
            return
        self.render_extent(extent, status="Zoomed track map to selected points.")

    def move_cursor_to_event(self, event):
        tracks = self.display_tracks()
        points = [(track, index, point) for track in tracks for index, point in enumerate(track.points())]
        if not points:
            return
        all_points = [item[2] for item in points]
        transformer = self._metadata_transformer(all_points, self.bounds()) or self._point_transformer(all_points, self.bounds())
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        best = min(points, key=lambda item: (transformer(item[2])[0] - location.x) ** 2 + (transformer(item[2])[1] - location.y) ** 2)
        self.cursor = (best[1], best[2], best[0])
        self.show_info = True
        self.controller.select_track_in_table(best[0].nr)
        self.sync_inspector_selection()
        self.controller.refresh_elevation_profile_for_plot_view(self)
        self.setNeedsDisplay_(True)

    def move_cursor_to_track_point(self, track: TrackRecord, point_index: int, inspector=None, sync_table: bool = True):
        points = track.points()
        if point_index < 0 or point_index >= len(points):
            return
        sequence = self.track_sequence()
        for index, selected_track in enumerate(sequence):
            if selected_track is track:
                self.track_index = index
                break
        self.update_track_plot_info(track)
        self.cursor = (point_index, points[point_index], track)
        self.show_info = True
        self.marker = None
        if inspector is not None:
            self.inspector = inspector
            inspector.plot_view = self
            if sync_table:
                inspector.select_point_index(point_index)
        window = self.window()
        if window is not None:
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
        self.setNeedsDisplay_(True)

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        key_code = event.keyCode()
        command_down = bool(event.modifierFlags() & NSEventModifierFlagCommand)
        self.transient_help_until = None
        if key not in {"h", "H"}:
            self.show_help = False
        if key in {"+", "=", "-", "_", "r", "R", "c", "C", "z", "Z"} and event.isARepeat():
            return
        if key in {"i", "I"}:
            self.show_info = not self.show_info
        elif key in {"h", "H"}:
            self.show_help = not self.show_help
        elif key in {"q", "Q"}:
            window = self.window()
            if window is not None:
                window.close()
            return
        elif key in {"u", "U"}:
            self.unselect_plot_items()
        elif key in {"p", "P"}:
            self.controller.save_plot_view_png(self)
        elif key in {"e", "E"}:
            self.controller.open_elevation_profile_for_plot_view(self)
        elif self.mode == "overview" and key in {"n", "N"}:
            self.show_track_numbers = not self.show_track_numbers
        elif key in {"d", "D"}:
            self.show_endpoint_markers = not self.show_endpoint_markers
            self.controller.set_status(
                "Start/end dots shown." if self.show_endpoint_markers else "Start/end dots hidden."
            )
        elif key in {"a", "A"} and self.cursor is not None:
            self.controller.set_anchor_from_point(self.cursor[1])
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
        elif key in {"z", "Z"}:
            self.zoom_to_selected()
        elif key in {"r", "R"}:
            self.reset_view()
        elif self.mode == "track" and (key in {" ", "\uf703", "\uf701"} or key_code in {124, 125}):
            self.switch_track(1)
            return
        elif self.mode == "track" and (key in {"\uf702", "\uf700"} or key_code in {123, 126}):
            self.switch_track(-1)
            return
        elif self.mode == "track" and key in {"m", "M"} and self.cursor is not None:
            previous_show_info = self.show_info
            self.marker = self.cursor[0]
            if self.inspector is not None and self.cursor[2] is self.inspector.track:
                self.inspector.select_point_index(self.marker)
            self.controller.refresh_elevation_profile_for_plot_view(self)
            self.show_info = previous_show_info
        elif self.mode == "track" and key in {"\x7f", "\uf728"}:
            self.delete_marked_points()
        elif self.mode == "track" and key in {"x", "X"}:
            self.cut_track()
        if self.inspector is not None and self.marker is not None and self.cursor is not None and self.cursor[2] is self.inspector.track:
            self.inspector.select_point_range(min(self.marker, self.cursor[0]), max(self.marker, self.cursor[0]))
        sequence_length = len(self.track_sequence()) if self.mode == "track" else len(self.controller.tracks)
        self.track_index = max(0, min(self.track_index, max(0, sequence_length - 1)))
        self.setNeedsDisplay_(True)

    def reset_view(self):
        selected_point_index = None
        selected_track = None
        if self.inspector is not None:
            rows = self.inspector.selected_row_indexes()
            if rows:
                selected_point_index = rows[-1]
                selected_track = self.inspector.track
        self.zoom = 1.0
        self.marker = None
        self.last_viewport_signature = None
        if self.mode == "overview":
            tracks = self.controller.tracks
            extent = self.controller.extent_for_track_records(tracks)
            if extent is not None:
                requested_zoom = self.controller.overview_zoom_for_tracks(tracks, self.controller.overview_zoom)
                self.render_extent(extent, requested_zoom, "Reset overview to all tracks.")
                return
        else:
            tracks = self.display_tracks()
            extent = self.controller.extent_for_track_records(tracks)
            if extent is not None:
                self.render_extent(extent, self.controller.track_zoom, "Reset track map to the full track extent.")
                if any(track is selected_track for track in tracks) and selected_point_index is not None:
                    self.move_cursor_to_track_point(selected_track, selected_point_index, self.inspector, sync_table=False)
                return
        self.plot_info = self.clone_plot_info(self.initial_plot_info)
        self.zoom = 1.0
        self.marker = None
        self.last_viewport_signature = None
        if selected_track is not None and selected_point_index is not None:
            self.move_cursor_to_track_point(selected_track, selected_point_index, self.inspector, sync_table=False)
        self.controller.set_status("Reset plot map to full extent.")
        self.setNeedsDisplay_(True)

    def change_view_zoom(self, factor: float):
        if self.rendering_map:
            return
        extent = self.zoomed_extent(factor)
        if extent is None:
            return
        current_zoom = int(self.plot_info.get("tile_zoom_level", self.plot_info.get("zoom_level", 14 if self.mode == "track" else 8)))
        tile_delta = 1 if factor > 1.0 else -1
        new_tile_zoom = max(0, min(19, current_zoom + tile_delta))
        self.render_extent(extent, new_tile_zoom, f"Rendered OSM viewport at tile zoom {new_tile_zoom}.")

    def zoomed_extent(self, factor: float):
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
        if self.cursor is not None:
            center_x, center_y = lonlat_to_web_mercator(self.cursor[1].lon, self.cursor[1].lat)
        span_x = max((max_x - min_x) / factor, 1.0)
        span_y = max((max_y - min_y) / factor, 1.0)
        if factor < 1.0:
            span_x *= 1.15
            span_y *= 1.15
        new_extent = {
            "min_x": center_x - span_x / 2.0,
            "max_x": center_x + span_x / 2.0,
            "min_y": center_y - span_y / 2.0,
            "max_y": center_y + span_y / 2.0,
        }
        return new_extent

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
        if confirm("Delete selected points?", f"Delete points {start + 1} through {end + 1} from track #{track.nr}?"):
            self.controller.delete_points(track, start, end)
            self.marker = None
            self.cursor = None

    def cut_track(self):
        if self.cursor is None:
            return
        track = self.cursor[2]
        index = self.cursor[0]
        if confirm("Cut track in two?", f"Cut track #{track.nr} after point {index + 1}?"):
            self.controller.cut_track(track, index)
            self.cursor = None
            self.marker = None


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

    def windowWillClose_(self, _notification):
        window = _notification.object()
        if self.controller is not None:
            self.controller.unregister_auxiliary_window(window)
        if self.controller is not None and not self.controller.closing_auxiliary_windows and self.view is not None:
            self.controller.plot_window_closing(self.view)


class ElevationProfileView(NSView):
    def initWithController_plotView_(self, controller, plot_view):
        self = objc.super(ElevationProfileView, self).initWithFrame_(NSMakeRect(0, 0, 1000, 180))
        if self is None:
            return None
        self.controller = controller
        self.plot_view = plot_view
        self.x_zoom = 1.0
        self.x_center = None
        self.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        return self

    def acceptsFirstResponder(self):
        return True

    def profile_title(self):
        if self.plot_view.mode == "overview":
            return "Overview Elevation Profile"
        track = self.current_track()
        if track is None:
            return "Track Elevation Profile"
        return f"Track #{track.nr}: {track.name} - Elevation Profile"

    def current_track(self):
        tracks = self.plot_view.display_tracks()
        return tracks[0] if tracks else None

    def profile_tracks(self):
        if self.plot_view.mode == "overview":
            return self.controller.visible_tracks()
        tracks = self.plot_view.display_tracks()
        return tracks

    def profile_data(self):
        rows = []
        track_ranges = {}
        total_distance = 0.0
        for track in self.profile_tracks():
            points = track.points()
            if not points:
                continue
            start_distance = total_distance
            previous = None
            for index, point in enumerate(points):
                if previous is not None:
                    total_distance += haversine_km(previous.lat, previous.lon, point.lat, point.lon)
                rows.append(
                    {
                        "track": track,
                        "index": index,
                        "point": point,
                        "distance": total_distance,
                        "elevation": point.ele,
                    }
                )
                previous = point
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
        y_min, y_max = self.fixed_elevation_range(elevations)
        x_min, x_max = self.visible_distance_range(total_distance)

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
        current_track = None
        path = None
        for row in rows:
            elevation = row["elevation"]
            if elevation is None or row["distance"] < x_min or row["distance"] > x_max:
                if path is not None:
                    NSColor.systemBlueColor().setStroke()
                    path.setLineWidth_(2.0)
                    path.stroke()
                path = None
                current_track = None
                continue
            if row["track"] is not current_track or path is None:
                if path is not None:
                    NSColor.systemBlueColor().setStroke()
                    path.setLineWidth_(2.0)
                    path.stroke()
                path = NSBezierPath.bezierPath()
                x, y = transform(row["distance"], elevation)
                path.moveToPoint_((x, y))
                current_track = row["track"]
            else:
                x, y = transform(row["distance"], elevation)
                path.lineToPoint_((x, y))
        if path is not None:
            NSColor.systemBlueColor().setStroke()
            path.setLineWidth_(2.0)
            path.stroke()

    def draw_selected_ranges(self, rows, track_ranges, transform, x_min, x_max):
        if self.plot_view.marker is not None and self.plot_view.cursor is not None:
            track = self.plot_view.cursor[2]
            start = min(self.plot_view.marker, self.plot_view.cursor[0])
            end = max(self.plot_view.marker, self.plot_view.cursor[0])
            selected_rows = [
                row
                for row in rows
                if row["track"] is track and start <= row["index"] <= end and row["elevation"] is not None
            ]
            self.draw_profile_segment(selected_rows, transform, x_min, x_max, NSColor.systemRedColor(), 4.0)
        if self.plot_view.mode == "overview":
            selected = set(self.controller.selected_nrs)
            if not selected:
                return
            NSColor.systemRedColor().setFill()
            for track_nr in selected:
                if track_nr not in track_ranges:
                    continue
                start, end = track_ranges[track_nr]
                left = max(start, x_min)
                right = min(end, x_max)
                if right <= left:
                    continue
                x1, _ = transform(left, self.elevation_baseline(rows))
                x2, _ = transform(right, self.elevation_baseline(rows))
                rect = NSMakeRect(x1, 32, max(x2 - x1, 2.0), max(self.bounds().size.height - 58, 10))
                NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.0, 0.0, 0.12).setFill()
                NSBezierPath.fillRect_(rect)
            return
        track = self.current_track()
        if track is None:
            return
        ranges = self.plot_view.selected_ranges_for_track(track)
        if self.plot_view.marker is not None and self.plot_view.cursor is not None and self.plot_view.cursor[2] is track:
            self.plot_view.sync_inspector_selection()
        for start, end in ranges:
            points = track.points()
            if start >= len(points) or end >= len(points):
                continue
            selected_rows = [row for row in rows if row["track"] is track and start <= row["index"] <= end and row["elevation"] is not None]
            self.draw_profile_segment(selected_rows, transform, x_min, x_max, NSColor.systemRedColor(), 4.0)

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

    def mouseDown_(self, event):
        self.move_cursor_to_event(event)
        if event.modifierFlags() & NSEventModifierFlagShift and self.plot_view.cursor is not None:
            self.plot_view.marker = self.plot_view.cursor[0]
            self.plot_view.sync_inspector_selection()
            self.controller.set_status(f"Profile marker set at point {self.plot_view.marker + 1}.")
        if event.clickCount() >= 2 and self.plot_view.cursor is not None:
            if self.plot_view.mode == "overview":
                self.controller.open_track_workflow_at_point(self.plot_view.cursor[2], self.plot_view.cursor[0])
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
        previous_marker = self.plot_view.marker
        previous_marker_track = self.plot_view.cursor[2] if self.plot_view.cursor is not None else track
        self.controller.select_track_in_table(track.nr)
        self.plot_view.move_cursor_to_track_point(track, index)
        if previous_marker is not None and previous_marker_track is track:
            self.plot_view.marker = previous_marker
        self.plot_view.sync_inspector_selection()
        self.plot_view.setNeedsDisplay_(True)
        self.setNeedsDisplay_(True)

    def nearest_row_for_event(self, event):
        rows, _track_ranges, total_distance = self.profile_data()
        rows = [row for row in rows if row["elevation"] is not None]
        if not rows:
            return None
        bounds = self.bounds()
        plot = NSMakeRect(58, 32, max(bounds.size.width - 82, 10), max(bounds.size.height - 58, 10))
        elevations = [row["elevation"] for row in rows]
        y_min, y_max = self.fixed_elevation_range(elevations)
        x_min, x_max = self.visible_distance_range(total_distance)
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
        if key in {"q", "Q"}:
            window = self.window()
            if window is not None:
                window.close()
            return
        if key in {"r", "R"}:
            self.x_zoom = 1.0
            self.x_center = None
            self.setNeedsDisplay_(True)
            return
        if key in {"+", "="}:
            self.change_zoom(2.0)
            return
        if key in {"-", "_"}:
            self.change_zoom(0.5)
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
        if self.plot_view.mode == "track" and key in {"\x7f", "\uf728"}:
            self.plot_view.delete_marked_points()
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
        if key in {"m", "M"} and self.plot_view.cursor is not None:
            self.plot_view.marker = self.plot_view.cursor[0]
            self.plot_view.sync_inspector_selection()
            self.plot_view.setNeedsDisplay_(True)
            self.setNeedsDisplay_(True)
            self.controller.set_status(f"Profile marker set at point {self.plot_view.marker + 1}.")
            return
        objc.super(ElevationProfileView, self).keyDown_(event)

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


class InspectorPointTableView(NSTableView):
    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        if key in {"\x7f", "\uf728"} and getattr(self, "controller", None) is not None:
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
        self.controller.edit_point_value(row, str(table_column.identifier()), str(value))

    def tableViewSelectionDidChange_(self, _notification):
        self.controller.selection_changed()


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
        self.rows = []
        self.extra_columns = []
        self.field_refs = {}
        self.suppress_selection_change = False
        self.plot_view = None
        self.velocity_field_touched = False
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
        self.window.setTitle_(f"Inspect Track #{self.track.nr}")
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
        self.table = InspectorPointTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.table.controller = self
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setAllowsMultipleSelection_(True)
        self.table.setAllowsEmptySelection_(True)
        self.table.setRowHeight_(22)
        self.table.setTarget_(self)
        self.table.setDoubleAction_("pointDoubleClicked:")
        self.data_source = TrackPointDataSource.alloc().initWithController_(self)
        self.table.setDataSource_(self.data_source)
        self.table.setDelegate_(self.data_source)
        self.table_scroll.setDocumentView_(self.table)
        root.addSubview_(self.table_scroll)

        self.buttons = {}
        for title, action in [
            ("Undo", "undo:"),
            ("Plot", "plot:"),
            ("PNG", "savePng:"),
            ("Split Track", "splitTrack:"),
            ("Readjust Time", "readjustTime:"),
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

    def layout_window(self):
        bounds = self.root.bounds()
        width = bounds.size.width
        height = bounds.size.height
        self.info_label.setFrame_(NSMakeRect(PADDING, height - 84, width - 2 * PADDING, 70))
        self.velocity_label.setFrame_(NSMakeRect(PADDING, height - 114, 95, FIELD_HEIGHT))
        self.velocity_field.setFrame_(NSMakeRect(PADDING + 100, height - 114, 82, FIELD_HEIGHT))
        self.table_scroll.setFrame_(NSMakeRect(PADDING, 60, width - 2 * PADDING, height - 152))
        x = PADDING
        for title in ["Undo", "Plot", "PNG", "Split Track", "Readjust Time", "Save", "Save & Exit", "Help", "Quit"]:
            w = 120 if title == "Save & Exit" else 100
            if title in {"Split Track", "Readjust Time"}:
                w = 120
            self.buttons[title].setFrame_(NSMakeRect(x, PADDING, w, BUTTON_HEIGHT))
            x += w + 8

    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        self.window.makeFirstResponder_(self.table)

    def windowWillClose_(self, notification):
        if self.parent is not None:
            self.parent.unregister_auxiliary_window(notification.object())
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
        velocity_by_element = self.point_velocity_text_by_element(point_infos)
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
                "lat": fields.get("@lat", ""),
                "lon": fields.get("@lon", ""),
                "ele": fields.get("ele", ""),
                "time": fields.get("time", ""),
                "calc_velocity": velocity_by_element.get(point, "N/A"),
            }
            for col, key in {"lat": "@lat", "lon": "@lon", "ele": "ele", "time": "time"}.items():
                if key in refs:
                    self.field_refs[(index - 1, col)] = refs[key]
            for name in self.extra_columns:
                row[name] = fields.get(name, "")
                if name in refs:
                    self.field_refs[(index - 1, name)] = refs[name]
            self.rows.append(row)
        self.update_info_label()
        self.refresh_velocity_field()
        self.table.reloadData()

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
            f"Track #{self.track.nr}: {self.track.name} | Points: {len(points)} | "
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
        widths = {"index": 60, "lat": 120, "lon": 120, "ele": 90, "time": 180, "calc_velocity": 105}
        titles = {"calc_velocity": "Velocity\nkm/h"}
        readonly = {"index", "calc_velocity"}
        for identifier in ["index", "lat", "lon", "ele", "time", "calc_velocity"] + self.extra_columns:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
            column.headerCell().setStringValue_(titles.get(identifier, identifier))
            column.setWidth_(widths.get(identifier, 140))
            column.setEditable_(identifier not in readonly)
            self.table.addTableColumn_(column)

    def push_undo(self):
        self.undo_stack.append(ET.tostring(self.track.element, encoding="unicode"))
        self.undo_stack = self.undo_stack[-10:]

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
        self.parent.invalidate_track_metrics(self.track)
        return True

    def refresh_after_point_table_change(self, selected_rows=None):
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        self.parent.refresh_track_plot_for_track(self.track, self)
        if self.plot_view is not None:
            rows = selected_rows if selected_rows is not None else self.selected_row_indexes()
            points = self.track.points()
            if rows and points:
                point_index = max(0, min(int(rows[-1]), len(points) - 1))
                self.plot_view.move_cursor_to_track_point(self.track, point_index, self, sync_table=False)
            else:
                self.plot_view.setNeedsDisplay_(True)
            self.parent.refresh_elevation_profile_for_plot_view(self.plot_view)
        self.parent.redraw_open_plot_views()

    def edit_point_value(self, row, column, value):
        points = self.point_elements()
        if row < 0 or row >= len(points) or column in {"index", "calc_velocity"}:
            return
        point = points[row]
        self.push_undo()
        text = value.strip()
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
        self.parent.invalidate_track_metrics(self.track)
        if row == 0 and column == "time":
            self.sync_track_start_time_from_first_point()
        self.reload_rows()
        self.refresh_calculated_velocity_rows([row])
        self.parent.mark_dirty(f"Edited point {row + 1} in track #{self.track.nr}.")
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

    def delete_selected_points(self):
        rows = self.selected_row_indexes()
        if not rows:
            return
        if not confirm("Delete selected waypoints?", f"Delete {len(rows)} waypoint(s) from track #{self.track.nr}?"):
            return
        self.push_undo()
        delete_set = {self.point_elements()[index] for index in rows if index < len(self.point_elements())}
        for segment in self.track.element.findall("gpx:trkseg", NS):
            for point in list(segment.findall("gpx:trkpt", NS)):
                if point in delete_set:
                    segment.remove(point)
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.parent.mark_dirty(f"Deleted {len(delete_set)} point(s) from track #{self.track.nr}.")
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
            if rows and getattr(self.plot_view, "marker", None) is None:
                self.plot_view.move_cursor_to_track_point(self.track, rows[-1], self, sync_table=False)
            self.plot_view.setNeedsDisplay_(True)
            self.parent.refresh_elevation_profile_for_plot_view(self.plot_view)

    def undo(self):
        if not self.undo_stack:
            return
        restored = ET.fromstring(self.undo_stack.pop())
        index = self.parent.tracks.index(self.track)
        self.track.element = restored
        self.parent.tracks[index] = self.track
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.parent.mark_dirty(f"Undid point edit in track #{self.track.nr}.")
        self.refresh_after_point_table_change()

    @objc.IBAction
    def undo_(self, _sender):
        self.undo()

    @objc.IBAction
    def plot_(self, _sender):
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        view = self.parent.open_plot_window("track")
        if view is not None:
            view.inspector = self
            self.plot_view = view
            view.setNeedsDisplay_(True)

    @objc.IBAction
    def savePng_(self, _sender):
        self.parent.selected_nrs = [self.track.nr]
        self.parent.update_selection_field()
        self.parent.highlight_selected_rows()
        view = self.parent.open_or_reload_track_plot_for_track(self.track)
        if view is not None:
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
        view = self.parent.open_plot_window("track")
        if view is None:
            return
        self.select_point_index(row)
        view.move_cursor_to_track_point(self.track, row, self)
        self.parent.set_status(f"Moved plot cursor to point {row + 1} of track #{self.track.nr}.")

    @objc.IBAction
    def splitTrack_(self, _sender):
        rows = self.selected_row_indexes()
        if not rows:
            show_alert("Select a waypoint row to split the track.")
            return
        split_at = min(rows)
        if confirm("Split track?", f"Move waypoint {split_at + 1} and all following waypoints to a new track?"):
            new_record = self.parent.split_track_from_index(self.track, split_at)
            if new_record is not None:
                self.reload_rows()
                self.parent.selected_nrs = [self.track.nr]
                self.parent.update_selection_field()
                self.parent.highlight_selected_rows()
                self.parent.refresh_track_plot_for_track(self.track, self)
                self.parent.redraw_open_plot_views()

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
            previous = first_point
            for index in selected[1:]:
                point = points[index]
                total_distance += haversine_km(previous.lat, previous.lon, point.lat, point.lon)
                previous = point
            if total_distance <= 0:
                show_alert("No distance.", "The selected points do not contain a measurable distance.")
                return
            interpolated_speed = total_distance / total_hours
            self.velocity_field.setStringValue_(f"{interpolated_speed:.1f}")
            self.push_undo()
            elapsed_hours = 0.0
            previous = first_point
            for index in selected:
                point = points[index]
                if index != selected[0]:
                    elapsed_hours += haversine_km(previous.lat, previous.lon, point.lat, point.lon) / interpolated_speed
                get_or_create_point_time(point.element).text = format_gpx_time(first_point.time + timedelta(hours=elapsed_hours))
                previous = point
            if 0 in selected:
                self.sync_track_start_time_from_first_point()
            self.velocity_field_touched = True
            self.parent.invalidate_track_metrics(self.track)
            self.reload_rows()
            self.refresh_calculated_velocity_rows(selected)
            self.parent.mark_dirty(f"Interpolated timestamps for {len(selected)} point(s) in track #{self.track.nr}.")
            self.parent.refresh_open_plot_views()
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
            previous = points[selected[0]]
            for index in selected:
                point = points[index]
                if index != selected[0]:
                    segment_speed = self.speed_for_segment(previous, point, speed_mode, velocity)
                    elapsed_hours += haversine_km(previous.lat, previous.lon, point.lat, point.lon) / segment_speed
                get_or_create_point_time(point.element).text = format_gpx_time(anchor_time + timedelta(hours=elapsed_hours))
                previous = point
        else:
            elapsed_hours = 0.0
            previous = points[selected[-1]]
            for index in reversed(selected):
                point = points[index]
                if index != selected[-1]:
                    segment_speed = self.speed_for_segment(point, previous, speed_mode, velocity)
                    elapsed_hours += haversine_km(point.lat, point.lon, previous.lat, previous.lon) / segment_speed
                get_or_create_point_time(point.element).text = format_gpx_time(anchor_time - timedelta(hours=elapsed_hours))
                previous = point
        if 0 in selected:
            self.sync_track_start_time_from_first_point()
        self.velocity_field_touched = True
        self.parent.invalidate_track_metrics(self.track)
        self.reload_rows()
        self.refresh_calculated_velocity_rows(selected)
        self.parent.mark_dirty(f"Readjusted timestamps for {len(selected)} point(s) in track #{self.track.nr}.")
        self.parent.refresh_open_plot_views()

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
        previous = first_point
        for index in selected[1:]:
            point = points[index]
            total_distance += haversine_km(previous.lat, previous.lon, point.lat, point.lon)
            previous = point
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
        self.parent.mark_dirty(f"Track #{self.track.nr} waypoint edits saved in memory.")
        self.parent.refresh_open_plot_views()

    @objc.IBAction
    def saveAndExit_(self, _sender):
        self.save_(None)
        self.window.close()

    @objc.IBAction
    def help_(self, _sender):
        show_alert(
            f"Inspect Track #{self.track.nr} Help",
            "This window shows every waypoint of the selected track. Scroll the table to inspect coordinates, height, time, accuracy fields, and any extra data stored with each point.\n\n"
            "Click a row to select a waypoint. Shift-click or drag in the table to select a range. Double-click a row to open the track map if needed and move the white cursor dot and arrow to that waypoint.\n\n"
            "Edit a table cell and press Enter to change a waypoint value. Undo restores recent inspector edits. Backspace/Delete removes selected waypoints after confirmation.\n\n"
            "Plot Track opens the track map. When this inspector and the map are both open, selecting points in the table highlights them on the map; clicking the map selects the nearest waypoint here. If a map marker is active, the selected range is shown in red.\n\n"
            "Split Track moves the selected waypoint and all following waypoints into a new track. Readjust Time recalculates selected waypoint timestamps from the first selected timestamp and the velocity field. Save keeps inspector edits in memory; Save & Exit keeps them and closes this window. The main editor Save writes everything to disk.",
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
        if not self.controller.quit_confirmed and not self.controller.confirm_quit():
            return False
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

    def windowWillClose_(self, notification):
        window = notification.object()
        self.controller.unregister_auxiliary_window(window)
        self.controller.pdf_summary_window = None
        self.controller.pdf_summary_browse_button = None
        self.controller.pdf_summary_export_button = None
        self.controller.pdf_summary_close_button = None
        self.controller.pdf_summary_delegate = None


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
        self.anchor: tuple[float, float] | None = None
        self.project_name = ""
        self.last_save_path: Path | None = None
        self.last_load_dir: Path | None = None
        self.last_pdf_path: Path | None = None
        self.last_png_dir: Path | None = None
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
        self.on_close_callback = None
        self.on_save_callback = None
        self.on_settings_change_callback = None
        self.settings_controller = None
        self.parameter_load_warnings = []
        self.help_window = None
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
        self.undo_stack: list[list[tuple[int, str]]] = []
        self.redo_stack: list[list[tuple[int, str]]] = []
        self.plot_windows = []
        self.plot_window_refs = []
        self.auxiliary_windows = []
        self.closing_auxiliary_windows = False
        if self.standalone:
            self.project_parameters, self.parameter_load_warnings = load_parameter_subset(
                STANDALONE_SETTINGS_PATH,
                EDITOR_PARAMETER_KEYS,
            )
        else:
            self.project_parameters = default_parameters()
        self._apply_parameter_attributes()
        self.tile_cache_dir = TILE_CACHE_DIR
        self.tile_cache_dir.mkdir(parents=True, exist_ok=True)
        self.cached_osm_tile_urls: set[str] | None = None
        MPL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.prune_tile_cache(max_age_seconds=self.map_cache_retention_hours * 3600.0)
        self.columns = [
            ("row", "Row\n", 56, False),
            ("nr", "Nr.\n", 56, False),
            ("show", "Show\n", 58, True),
            ("name", "Name\n", 220, True),
            ("date", "Date & Time\n", 155, True),
            ("length", "Length\n[km]", 92, False),
            ("duration", "Duration\n[hh:mm]", 95, False),
            ("sum", "Sum\n[km]", 82, False),
            ("distance", "Distance\n[km]", 100, False),
            ("speed", "Avg Speed\n[km/h]", 105, False),
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
        self.elevation_headroom_fraction = float(values["gpx.elevation_headroom_fraction"])
        self.maximum_map_tiles = int(values["gpx.maximum_map_tiles"])
        self.pdf_document_dpi = int(values["pdf.document_dpi"])
        self.pdf_map_dpi = float(values["pdf.map_dpi"])
        self.pdf_overview_zoom = int(values["pdf.overview_zoom"])
        self.pdf_track_zoom = int(values["pdf.track_zoom"])
        self.pdf_maximum_map_tiles = int(values["pdf.maximum_map_tiles"])
        self.map_provider = str(values["maps.provider"])
        self.custom_map_url = str(values["maps.custom_url"])
        self.custom_map_attribution = str(values["maps.custom_attribution"])
        self.maximum_map_zoom = int(values["maps.maximum_zoom"])
        self.map_request_timeout_seconds = float(values["maps.request_timeout_seconds"])
        self.map_cache_retention_hours = float(values["maps.cache_retention_hours"])

    def apply_project_parameters(self, settings=None):
        """Apply project-scoped settings to an embedded editor instance."""
        self.project_parameters = normalize_parameters(settings)
        self._apply_parameter_attributes()
        timer = getattr(self, "autosave_timer", None)
        if timer is not None:
            timer.invalidate()
            self.autosave_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.editor_autosave_seconds, self, "autosave:", None, True
            )
        self.cached_osm_tile_urls = None
        self.prune_tile_cache(max_age_seconds=self.map_cache_retention_hours * 3600.0)
        if self.settings_controller is not None:
            self.settings_controller.update_values(self.project_parameters)

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
        if self.settings_controller is None:
            self.settings_controller = CocoaParameterEditor.alloc().init()
            self.settings_controller.configure(
                title="GPX Editor Settings",
                sections=EDITOR_PARAMETER_SECTIONS,
                values=self.project_parameters,
                apply_callback=self._apply_editor_settings,
            )
        else:
            self.settings_controller.update_values(self.project_parameters)
        self.settings_controller.show()

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
        self.track_table.registerForDraggedTypes_([DRAG_TYPE])
        table_font = self.table_font(12)
        header_font = self.table_font(11)
        numeric_columns = {"row", "nr", "length", "duration", "sum", "distance", "speed", "ascent", "descent", "npoints"}
        for identifier, title, width, editable in self.columns:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
            column.headerCell().setStringValue_(title)
            if hasattr(column.headerCell(), "setWraps_"):
                column.headerCell().setWraps_(True)
            column.headerCell().setFont_(header_font)
            column.headerCell().setAlignment_(0)
            column.dataCell().setFont_(table_font)
            column.dataCell().setAlignment_(1 if identifier in numeric_columns else 0)
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
            ("Add Tracks", "addTracks:", "Load one or more .gpx files and append their tracks."),
            ("Save", "save:", "Save all tracks to the output GPX filename."),
            ("Save & Exit", "saveAndExit:", "Save the GPX file and exit the editor."),
            ("PNG", "savePng:", "Save the current selected-track plot as a PNG image."),
            ("PDF", "exportPdf:", "Export the track table to a PDF file."),
            ("Sort", "sortByDate:", "Sort by Date & Time using the special distance placement for untimed or zero-duration tracks."),
            ("Select All", "selectAllTracks:", "Select all tracks."),
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
        self.buttons["Add Tracks"].setFrame_(NSMakeRect(PADDING, project_y, 104, BUTTON_HEIGHT))
        project_x = PADDING + 104 + gap
        self.project_label.setFrame_(NSMakeRect(project_x, project_y + FIELD_HEIGHT + 2, 220, 18))
        self.project_field.setFrame_(NSMakeRect(project_x, project_y, 310, FIELD_HEIGHT))
        undo_size = BUTTON_HEIGHT
        redo_x = width - PADDING - undo_size
        undo_x = redo_x - gap - undo_size
        self.buttons["Undo"].setFrame_(NSMakeRect(undo_x, project_y, undo_size, BUTTON_HEIGHT))
        self.buttons["Redo"].setFrame_(NSMakeRect(redo_x, project_y, undo_size, BUTTON_HEIGHT))
        top -= FIELD_HEIGHT + 10
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
            "Join Tracks": 104,
            "Inspect Track": 116,
        }
        for title in ["Select All", "Unselect All", "Join Tracks", "Inspect Track"]:
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
        file_button_widths = [("PDF", 58), ("PNG", 62), ("Plot Overview", 112), ("Plot Track(s)", 112), ("View File", 88)]
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
        self.status_label.setFrame_(NSMakeRect(PADDING, PADDING, width - 2 * PADDING, STATUS_HEIGHT))

    def configure_key_loop(self):
        order = [
            self.buttons["Help"],
            self.settings_button,
            self.buttons["Add Tracks"],
            self.project_field,
            self.buttons["Undo"],
            self.buttons["Redo"],
            self.track_table,
            self.selection_field,
            self.buttons["Select All"],
            self.buttons["Unselect All"],
            self.buttons["Join Tracks"],
            self.buttons["Inspect Track"],
            self.buttons["Set Anchorpoint"],
            self.buttons["Save"],
            self.output_field,
            self.output_browse_button,
            self.buttons["PDF"],
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
        if self.settings_controller is not None:
            self.settings_controller.close()
            self.settings_controller = None
        self.close_auxiliary_windows()
        self.notify_close_callback()
        if delete_recovery:
            self.delete_recovery_file()

    def close_main_editor_window(self, delete_recovery=True):
        self.finalize_editor_close(delete_recovery=delete_recovery)
        try:
            self.window.setDelegate_(None)
        except Exception:
            pass
        try:
            self.window.orderOut_(None)
        except Exception:
            pass
        try:
            self.window.close()
        except Exception:
            pass
        if self.standalone:
            NSApp().terminate_(None)

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
        cutoff = time.time() - max_age_seconds
        try:
            for path in self.tile_cache_dir.rglob("*"):
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
        except OSError as exc:
            print(f"GPXEditor cache cleanup warning: {exc}", flush=True)

    def count_tile_cache_files(self) -> int:
        try:
            return sum(1 for path in self.tile_cache_dir.rglob("*") if path.is_file())
        except OSError:
            return -1

    def cached_tile_urls(self) -> set[str]:
        if self.cached_osm_tile_urls is not None:
            return self.cached_osm_tile_urls
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
        self.cached_osm_tile_urls = urls
        return urls

    def osm_tile_urls(self, diagnostics: dict, zoom: int) -> list[str]:
        return [
            f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            for x in range(diagnostics["x0"], diagnostics["x1"] + 1)
            for y in range(diagnostics["y0"], diagnostics["y1"] + 1)
        ]

    def missing_osm_tile_count(self, diagnostics: dict, zoom: int) -> int:
        cached = self.cached_tile_urls()
        return sum(1 for url in self.osm_tile_urls(diagnostics, zoom) if url not in cached)

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

    def snapshot(self) -> list[tuple[int, str]]:
        return [(track.nr, ET.tostring(track.element, encoding="unicode")) for track in self.tracks]

    def restore_snapshot(self, snapshot: list[tuple[int, str]]):
        current_by_nr = {track.nr: track for track in self.tracks}
        restored = []
        for nr, xml_text in snapshot:
            source = current_by_nr.get(nr).source_file if nr in current_by_nr else ""
            restored.append(TrackRecord(nr, ET.fromstring(xml_text), source))
        self.tracks = restored
        self.next_nr = max([track.nr for track in self.tracks], default=0) + 1
        self.dirty = True
        self.recalculate()

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def mark_dirty(self, message: str):
        self.dirty = True
        self.recalculate()
        self.set_status(message)

    def invalidate_track_metrics(self, track: TrackRecord):
        track.metrics_dirty = True

    def invalidate_all_metrics(self):
        for track in self.tracks:
            self.invalidate_track_metrics(track)

    def refresh_open_plot_views(self):
        live_windows = []
        for window, delegate, view in self.plot_windows:
            if window is None or not window.isVisible():
                continue
            if hasattr(view, "rerender_current_map"):
                if isinstance(view, PlotView):
                    view.cursor = None
                    view.marker = None
                    view.last_viewport_signature = None
                view.rerender_current_map()
            elif isinstance(view, ElevationProfileView):
                view.setNeedsDisplay_(True)
            live_windows.append((window, delegate, view))
        self.plot_windows = live_windows

    def redraw_open_plot_views(self):
        live_windows = []
        for window, delegate, view in self.plot_windows:
            if window is None or not window.isVisible():
                continue
            if isinstance(view, (PlotView, ElevationProfileView)):
                view.setNeedsDisplay_(True)
            live_windows.append((window, delegate, view))
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
            if window is None or not window.isVisible():
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
            if window is None or not window.isVisible():
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
        for window, _delegate, _view in list(self.plot_windows):
            if window is not None and not any(existing == window for existing in windows):
                windows.append(window)
        if self.help_window is not None and not any(existing == self.help_window for existing in windows):
            windows.append(self.help_window)

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
        plot_info = self.render_summary_plot("track")
        if plot_info is None:
            return
        view.track_index = 0
        view.plot_info = plot_info
        view.initial_plot_info = view.clone_plot_info(plot_info)
        view.cursor = None
        view.marker = None
        view.last_viewport_signature = None
        if inspector is not None:
            view.inspector = inspector
            inspector.plot_view = view
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        window.makeFirstResponder_(view)
        view.update_window_title()
        view.setNeedsDisplay_(True)

    def open_track_workflow_at_point(self, track: TrackRecord, point_index: int):
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
        self.set_status(f"Opened track #{track.nr} at point {point_index + 1}.")
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
        plot_info = self.render_summary_plot("track")
        if plot_info is None:
            return None
        if existing is not None:
            window, view = existing
            view.track_index = 0
            view.plot_info = plot_info
            view.initial_plot_info = view.clone_plot_info(plot_info)
            view.cursor = None
            view.marker = None
            view.last_viewport_signature = None
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            view.update_window_title()
            view.setNeedsDisplay_(True)
            return view
        return None

    def plot_window_closing(self, closing_view):
        if isinstance(closing_view, PlotView):
            self.close_elevation_profile_for_plot_view(closing_view)
        elif isinstance(closing_view, ElevationProfileView):
            return
        inspector = getattr(closing_view, "inspector", None)
        if inspector is not None and getattr(inspector, "plot_view", None) is closing_view:
            inspector.plot_view = None
        if hasattr(closing_view, "inspector"):
            closing_view.inspector = None

    def selected_tracks(self) -> list[TrackRecord]:
        by_nr = {track.nr: track for track in self.tracks}
        return [by_nr[nr] for nr in self.selected_nrs if nr in by_nr]

    def visible_tracks(self) -> list[TrackRecord]:
        return [track for track in self.tracks if not track.hidden]

    def select_track_in_table(self, nr: int):
        if nr not in {track.nr for track in self.tracks}:
            return
        self.selected_nrs = [nr]
        self.update_selection_field()
        self.highlight_selected_rows()
        self.redraw_open_plot_views()

    def recalculate(self):
        if self.anchor is None:
            for track in self.tracks:
                points = track.points()
                if points:
                    self.anchor = (points[0].lat, points[0].lon)
                    break
        rows: list[dict[str, str]] = []
        cumulative = 0.0
        values = {"length": [], "duration": [], "speed": [], "ascent": [], "descent": []}
        for index, track in enumerate(self.tracks, 1):
            metrics = self.compute_metrics(track)
            track.metrics = metrics
            self.maybe_log_duration_diagnostic(track, metrics)
            included = not track.hidden
            if included:
                cumulative += metrics["length_km"]
            if included and metrics["length_km"] > 0:
                values["length"].append(metrics["length_km"])
            if included and metrics["duration"] is not None and metrics["duration"].total_seconds() > 0:
                values["duration"].append(metrics["duration"])
            if included and metrics["speed_kmh"] is not None:
                values["speed"].append(metrics["speed_kmh"])
            if included:
                values["ascent"].append(metrics["ascent_m"])
                values["descent"].append(metrics["descent_m"])
            rows.append(
                {
                    "row": str(index),
                    "nr": str(track.nr),
                    "show": "no" if track.hidden else "yes",
                    "name": track.name,
                    "date": format_datetime_local(metrics["time"]),
                    "length": f"{metrics['length_km']:.1f}",
                    "duration": format_duration(metrics["duration"]),
                    "sum": f"{cumulative:.1f}",
                    "distance": "N/A" if metrics["distance_km"] is None else f"{metrics['distance_km']:.1f}",
                    "speed": "N/A" if metrics["speed_kmh"] is None else f"{metrics['speed_kmh']:.1f}",
                    "ascent": f"{metrics['ascent_m']:.1f}",
                    "descent": f"{metrics['descent_m']:.1f}",
                    "npoints": str(metrics["npoints"]),
                    "_summary": False,
                }
            )
        if self.tracks:
            rows.append(
                {
                    "row": str(len(self.tracks) + 1),
                    "nr": "",
                    "show": "",
                    "name": "Average / Total",
                    "date": "",
                    "length": self.average_text(values["length"]),
                    "duration": format_total_duration(sum(values["duration"], timedelta())) if values["duration"] else "N/A",
                    "sum": f"{cumulative:.1f}",
                    "distance": "",
                    "speed": self.average_text(values["speed"]),
                    "ascent": self.average_text(values["ascent"]),
                    "descent": self.average_text(values["descent"]),
                    "npoints": str(sum(track.metrics.get("npoints", 0) for track in self.tracks if not track.hidden)),
                    "_summary": True,
                }
            )
        self.table_rows = rows
        self.track_table.reloadData()
        if self.tracks and not self.did_auto_size_track_columns:
            self.auto_size_track_columns()
            self.did_auto_size_track_columns = True
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
            elif identifier in {"length", "duration", "sum", "distance", "speed", "ascent", "descent", "npoints"}:
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
        points = track.points()
        length = 0.0
        ascent = 0.0
        descent = 0.0
        for previous, current in zip(points, points[1:]):
            length += haversine_km(previous.lat, previous.lon, current.lat, current.lon)
            if previous.ele is not None and current.ele is not None:
                delta = current.ele - previous.ele
                if delta > 0:
                    ascent += delta
                else:
                    descent += abs(delta)
        first_point_time = points[0].time if points and points[0].time is not None else None
        filtered_times = self.filtered_track_times(points)
        times = [time for _index, time in filtered_times]
        start_time = times[0] if times else None
        end_time = times[-1] if times else None
        duration = (end_time - start_time) if start_time and end_time else None
        track_time = parse_time(track.element.findtext("gpx:time", default="", namespaces=NS)) or first_point_time
        speed = None
        if duration is not None and duration.total_seconds() > 0:
            speed = length / (duration.total_seconds() / 3600.0)
        distance = None
        if self.anchor is not None and points:
            distance = haversine_km(points[-1].lat, points[-1].lon, self.anchor[0], self.anchor[1])
        metrics = {
            "time": track_time,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "length_km": length,
            "distance_km": distance,
            "speed_kmh": speed,
            "ascent_m": ascent,
            "descent_m": descent,
            "npoints": len(points),
            "last_lat": points[-1].lat if points else None,
            "last_lon": points[-1].lon if points else None,
            "_anchor_key": current_anchor_key,
        }
        track.metrics = metrics
        track.metrics_dirty = False
        return metrics

    def compute_point_range_metrics(self, track: TrackRecord, ranges: list[tuple[int, int]]) -> dict | None:
        points = track.points()
        if not points or not ranges:
            return None
        normalized = []
        for start, end in ranges:
            start = max(0, min(int(start), len(points) - 1))
            end = max(0, min(int(end), len(points) - 1))
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
        duration = timedelta()
        has_duration = False
        for start, end in normalized:
            segment = points[start : end + 1]
            for previous, current in zip(segment, segment[1:]):
                length += haversine_km(previous.lat, previous.lon, current.lat, current.lon)
                if previous.ele is not None and current.ele is not None:
                    delta = current.ele - previous.ele
                    if delta > 0:
                        ascent += delta
                    else:
                        descent += abs(delta)
            if segment and segment[0].time is not None and segment[-1].time is not None:
                segment_duration = segment[-1].time - segment[0].time
                if segment_duration.total_seconds() >= 0:
                    duration += segment_duration
                    has_duration = True
        duration_value = duration if has_duration else None
        speed = None
        if duration_value is not None and duration_value.total_seconds() > 0:
            speed = length / (duration_value.total_seconds() / 3600.0)
        return {
            "start_index": first_index,
            "end_index": last_index,
            "npoints": sum(end - start + 1 for start, end in normalized),
            "start_time": points[first_index].time,
            "end_time": points[last_index].time,
            "duration": duration_value,
            "length_km": length,
            "speed_kmh": speed,
            "ascent_m": ascent,
            "descent_m": descent,
        }

    def is_editable_cell(self, row: int, column: str) -> bool:
        return row < len(self.tracks) and column in {"show", "name", "date"}

    def edit_table_value(self, row: int, column: str, value: str):
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
                self.recalculate()
                return
            self.push_undo()
            track.set_hidden(hidden)
            self.mark_dirty(f"Track #{track.nr} is now {'hidden' if hidden else 'shown'} for statistics, overview, and PDF export.")
        elif column == "name":
            old = track.name
            new = value.strip()
            if not new or new == old:
                self.recalculate()
                return
            self.push_undo()
            get_or_create_track_name(track.element).text = new
            self.mark_dirty(f"Track #{track.nr} name changed from '{old}' to '{new}'.")
        elif column == "date":
            new_time = parse_user_datetime(value)
            if new_time is None:
                self.recalculate()
                self.set_status(f"Ignored invalid date for track #{track.nr}. Use dd.mm.yyyy hh:mm.")
                return
            self.push_undo()
            self.apply_track_start_time(track, new_time)
            self.mark_dirty(f"Track #{track.nr} start date changed to {format_datetime_local(new_time)}.")

    def apply_track_start_time(self, track: TrackRecord, new_time: datetime):
        points = track.points()
        old_first_time = next((point.time for point in points if point.time is not None), None)
        get_or_create_track_time(track.element).text = format_gpx_time(new_time)
        if old_first_time is not None:
            offset = new_time - old_first_time
            for point in points:
                if point.time is not None:
                    get_or_create_point_time(point.element).text = format_gpx_time(point.time + offset)
            self.invalidate_track_metrics(track)
            return
        self.assign_missing_timestamps(track, new_time)
        desc = get_or_create_child(track.element, "desc", {qname("name"), qname("cmt")})
        note = "Timestamp edited manually; original track had no usable point timestamps."
        desc.text = f"{desc.text or ''}\n{note}".strip()
        self.invalidate_track_metrics(track)

    def assign_missing_timestamps(self, track: TrackRecord, start_time: datetime, end_time: datetime | None = None):
        points = track.points()
        if not points:
            return
        repaired_times = timestamps_from_start(
            [{"lat": point.lat, "lon": point.lon} for point in points],
            start_time,
            end_time,
            self.average_speed() or self.project_parameters["gpx.fallback_walking_speed_kmh"],
        )
        for point, point_time in zip(points, repaired_times):
            get_or_create_point_time(point.element).text = format_gpx_time(point_time)
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

    def load_gpx_paths(self, paths: list[Path], mark_dirty: bool = True):
        if not paths:
            return
        if mark_dirty:
            self.push_undo()
        added = 0
        removed_invalid_coordinates = 0
        removed_invalid_timestamps = 0
        removed_out_of_order = 0
        self.last_load_dir = paths[0].parent
        for path in paths:
            try:
                tree = ET.parse(path)
                root = tree.getroot()
            except (OSError, ET.ParseError) as exc:
                show_alert("Could not read GPX file.", f"{path}\n\n{exc}")
                continue
            if root.tag != qname("gpx"):
                show_alert("Unsupported GPX file.", f"{path} is not a GPX 1.1 document.")
                continue
            if not self.tracks and not self.project_name:
                self.project_name = path.stem
                self.project_field.setStringValue_(self.project_name)
                if self.last_save_path is None and not str(self.output_field.stringValue()).strip():
                    self.set_output_path(path.with_suffix(".gpx").resolve())
            for trk in root.findall("gpx:trk", NS):
                record = TrackRecord(self.next_nr, copy.deepcopy(trk), str(path))
                removed = sanitize_track_points(record.element)
                removed_invalid_coordinates += removed["coordinates"]
                removed_invalid_timestamps += removed["timestamps"]
                removed_out_of_order += removed["out_of_order"]
                self.populate_track_time_from_first_point(record)
                self.tracks.append(record)
                self.next_nr += 1
                added += 1
        ignored_parts = []
        if removed_invalid_coordinates:
            ignored_parts.append(f"{removed_invalid_coordinates} point(s) with invalid coordinates")
        if removed_invalid_timestamps:
            ignored_parts.append(f"{removed_invalid_timestamps} point(s) with invalid timestamps")
        if removed_out_of_order:
            ignored_parts.append(f"{removed_out_of_order} out-of-order point(s)")
        ignored_suffix = f" Ignored {', '.join(ignored_parts)}." if ignored_parts else ""
        if mark_dirty:
            self.mark_dirty(f"Added {added} track(s) from {len(paths)} GPX file(s).{ignored_suffix}")
        else:
            self.recalculate()
            self.dirty = False
            self.set_status(f"Loaded {added} track(s) from {len(paths)} GPX file(s).{ignored_suffix}")

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
                info = (view.plot_info.get("tracks") or {}).get(track.nr, view.plot_info)
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
        if self.write_png_data(path, self.png_data_for_plot_view(view)):
            self.set_status(f"Saved plot PNG to {path}.")
        else:
            self.set_status("No PNG file was saved.")

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

    def png_data_for_plot_view(self, view: PlotView):
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
        if bitmap is None:
            return self.png_data_for_plot_info(view.plot_info)
        context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap)
        if context is None:
            return self.png_data_for_plot_info(view.plot_info)
        previous_context = NSGraphicsContext.currentContext()
        try:
            NSGraphicsContext.setCurrentContext_(context)
            view.displayRectIgnoringOpacity_inContext_(bounds, context)
        finally:
            NSGraphicsContext.setCurrentContext_(previous_context)
        data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        return bytes(data) if data is not None else self.png_data_for_plot_info(view.plot_info)

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
        if not self.table_rows:
            show_alert("No table data to export.", "Load tracks before exporting the table to PDF.")
            return
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

    def open_pdf_summary_window(self):
        if self.pdf_summary_window is not None and self.pdf_summary_window.isVisible():
            self.pdf_summary_window.makeKeyAndOrderFront_(None)
            self.pdf_summary_window.orderFrontRegardless()
            NSApp().activateIgnoringOtherApps_(True)
            return
        self.build_pdf_summary_window()
        self.pdf_summary_window.makeKeyAndOrderFront_(None)
        self.pdf_summary_window.orderFrontRegardless()
        NSApp().activateIgnoringOtherApps_(True)

    def build_pdf_summary_window(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180, 180, 760, 460), style, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("PDF Summary")
        window.setMinSize_(NSMakeSize(700, 430))
        root = NSView.alloc().initWithFrame_(window.contentView().bounds())
        root.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        window.contentView().addSubview_(root)

        title = self.make_label("PDF Summary", 17, True, 0)
        title.setFrame_(NSMakeRect(20, 416, 220, 26))
        root.addSubview_(title)
        hint = self.make_label("The GPX Editor table remains available. Select tracks there before exporting if needed.", 12, False, 0)
        hint.setFrame_(NSMakeRect(20, 390, 700, 22))
        hint.setAutoresizingMask_(NSViewWidthSizable)
        root.addSubview_(hint)

        path_label = self.make_label("PDF file", 12, False, 0)
        path_label.setFrame_(NSMakeRect(20, 354, 70, FIELD_HEIGHT))
        root.addSubview_(path_label)
        self.pdf_summary_path_field = NSTextField.alloc().initWithFrame_(NSMakeRect(92, 354, 548, FIELD_HEIGHT))
        self.pdf_summary_path_field.setStringValue_(str(self.default_pdf_path()))
        self.pdf_summary_path_field.setToolTip_("Full path of the PDF file to write.")
        self.pdf_summary_path_field.setAutoresizingMask_(NSViewWidthSizable)
        root.addSubview_(self.pdf_summary_path_field)
        browse_button = make_liquid_glass_button(NSMakeRect(650, 354, 84, BUTTON_HEIGHT))
        browse_button.setTitle_("Browse")
        browse_button.setTarget_(self)
        browse_button.setAction_("choosePdfSummaryOutput:")
        browse_button.setToolTip_("Choose the PDF output file.")
        browse_button.setAutoresizingMask_(0)
        apply_liquid_glass_button_style(browse_button)
        root.addSubview_(browse_button)
        self.pdf_summary_browse_button = browse_button

        columns_title = self.make_label("Columns", 13, True, 0)
        columns_title.setFrame_(NSMakeRect(20, 316, 120, 22))
        root.addSubview_(columns_title)
        self.pdf_summary_checkboxes = {}
        default_off = {"nr", "show", "speed", "ascent", "descent", "npoints"}
        for index, (identifier, title_text, _width, _editable) in enumerate(self.columns):
            column_index = index % 2
            row_index = index // 2
            x = 20 + column_index * 265
            y = 286 - row_index * 27
            checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 245, 24))
            checkbox.setButtonType_(NSButtonTypeSwitch)
            checkbox.setTitle_(title_text.replace("\n", " ").strip())
            checkbox.setState_(NSControlStateValueOff if identifier in default_off else NSControlStateValueOn)
            checkbox.setToolTip_(f"Include {title_text.replace(chr(10), ' ').strip()} in the PDF table.")
            root.addSubview_(checkbox)
            self.pdf_summary_checkboxes[identifier] = checkbox

        maps_title = self.make_label("Maps", 13, True, 0)
        maps_title.setFrame_(NSMakeRect(20, 76, 80, 22))
        root.addSubview_(maps_title)
        include_overview = NSButton.alloc().initWithFrame_(NSMakeRect(20, 50, 170, 24))
        include_overview.setButtonType_(NSButtonTypeSwitch)
        include_overview.setTitle_("Overview plot")
        include_overview.setState_(NSControlStateValueOff)
        include_overview.setToolTip_("Add an overview map page after the table.")
        root.addSubview_(include_overview)
        elevation_profile = NSButton.alloc().initWithFrame_(NSMakeRect(190, 50, 170, 24))
        elevation_profile.setButtonType_(NSButtonTypeSwitch)
        elevation_profile.setTitle_("Elevation Profile")
        elevation_profile.setState_(NSControlStateValueOn)
        elevation_profile.setToolTip_("Add an elevation profile above each PDF map.")
        root.addSubview_(elevation_profile)
        rotate_maps = NSButton.alloc().initWithFrame_(NSMakeRect(360, 50, 130, 24))
        rotate_maps.setButtonType_(NSButtonTypeSwitch)
        rotate_maps.setTitle_("Rotate maps")
        rotate_maps.setState_(NSControlStateValueOff)
        rotate_maps.setToolTip_("Rotate each map image by 90 degrees on the PDF page.")
        root.addSubview_(rotate_maps)

        track_label = self.make_label("Track plots", 12, False, 0)
        track_label.setFrame_(NSMakeRect(20, 20, 80, FIELD_HEIGHT))
        root.addSubview_(track_label)
        track_menu = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(100, 20, 135, FIELD_HEIGHT), False)
        track_menu.addItemsWithTitles_(["None", "All", "Visible", "Selected"])
        track_menu.selectItemAtIndex_(0)
        track_menu.setToolTip_("All includes tracks hidden in the Show column. Selected uses the current GPX Editor table selection.")
        root.addSubview_(track_menu)
        orientation_label = self.make_label("Page", 12, False, 0)
        orientation_label.setFrame_(NSMakeRect(250, 20, 42, FIELD_HEIGHT))
        root.addSubview_(orientation_label)
        self.pdf_summary_orientation_menu = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(294, 20, 120, FIELD_HEIGHT), False)
        self.pdf_summary_orientation_menu.addItemsWithTitles_(["Portrait", "Landscape"])
        self.pdf_summary_orientation_menu.selectItemAtIndex_(0)
        self.pdf_summary_orientation_menu.setToolTip_("Choose the PDF page orientation.")
        root.addSubview_(self.pdf_summary_orientation_menu)
        export_button = make_liquid_glass_button(NSMakeRect(570, 20, 82, BUTTON_HEIGHT))
        export_button.setTitle_("Export")
        export_button.setTarget_(self)
        export_button.setAction_("exportPdfSummaryNow:")
        export_button.setAutoresizingMask_(0)
        apply_liquid_glass_button_style(export_button)
        root.addSubview_(export_button)
        self.pdf_summary_export_button = export_button
        close_button = make_liquid_glass_button(NSMakeRect(660, 20, 74, BUTTON_HEIGHT))
        close_button.setTitle_("Close")
        close_button.setTarget_(self)
        close_button.setAction_("closePdfSummary:")
        close_button.setAutoresizingMask_(0)
        apply_liquid_glass_button_style(close_button)
        root.addSubview_(close_button)
        self.pdf_summary_close_button = close_button

        self.pdf_summary_status_label = self.make_label("Ready. Select tracks in the GPX Editor table if using Selected.", 11, False, 0)
        self.pdf_summary_status_label.setFrame_(NSMakeRect(20, 4, 720, 16))
        self.pdf_summary_status_label.setAutoresizingMask_(NSViewWidthSizable)
        root.addSubview_(self.pdf_summary_status_label)
        self.pdf_summary_options = {
            "include_overview": include_overview,
            "track_menu": track_menu,
            "elevation_profile": elevation_profile,
            "rotate_maps": rotate_maps,
        }
        self.pdf_summary_window = window
        self.pdf_summary_delegate = GPXEditorPdfSummaryWindowDelegate.alloc().initWithController_(self)
        window.setDelegate_(self.pdf_summary_delegate)
        self.register_auxiliary_window(window)
        self.layout_pdf_summary_window()

    def layout_pdf_summary_window(self):
        if self.pdf_summary_window is None:
            return
        width = self.pdf_summary_window.contentView().bounds().size.width
        right = max(width - 20.0, 680.0)
        if self.pdf_summary_browse_button is not None:
            self.pdf_summary_browse_button.setFrame_(NSMakeRect(right - 84.0, 354.0, 84.0, BUTTON_HEIGHT))
        if self.pdf_summary_path_field is not None:
            self.pdf_summary_path_field.setFrame_(NSMakeRect(92.0, 354.0, max(120.0, right - 92.0 - 94.0), FIELD_HEIGHT))
        if self.pdf_summary_close_button is not None:
            self.pdf_summary_close_button.setFrame_(NSMakeRect(right - 74.0, 20.0, 74.0, BUTTON_HEIGHT))
        if self.pdf_summary_export_button is not None:
            self.pdf_summary_export_button.setFrame_(NSMakeRect(right - 164.0, 20.0, 82.0, BUTTON_HEIGHT))
        if self.pdf_summary_status_label is not None:
            self.pdf_summary_status_label.setFrame_(NSMakeRect(20.0, 4.0, max(120.0, right - 20.0), 16.0))

    @objc.IBAction
    def choosePdfSummaryOutput_(self, _sender):
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["pdf"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.default_pdf_directory())))
        panel.setNameFieldStringValue_(Path(str(self.pdf_summary_path_field.stringValue())).name or f"{self.default_pdf_basename()}.pdf")
        if panel.runModal():
            url = panel.URL()
            if url is not None:
                self.pdf_summary_path_field.setStringValue_(str(Path(str(url.path())).resolve()))

    @objc.IBAction
    def exportPdfSummaryNow_(self, _sender):
        selected_columns = [
            identifier
            for identifier, checkbox in self.pdf_summary_checkboxes.items()
            if checkbox.state() == NSControlStateValueOn
        ]
        if not selected_columns:
            show_alert("No columns selected.", "Select at least one column for the PDF table.")
            return
        value = str(self.pdf_summary_path_field.stringValue()).strip()
        if not value:
            show_alert("No PDF file selected.", "Choose a PDF filename before exporting.")
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.default_pdf_directory() / path
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        path = path.resolve()
        orientation = "landscape" if self.pdf_summary_orientation_menu.indexOfSelectedItem() == 1 else "portrait"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.set_status(f"PDF export: writing {path}.")
            if self.pdf_summary_status_label is not None:
                self.pdf_summary_status_label.setStringValue_("Exporting PDF...")
                self.pdf_summary_status_label.displayIfNeeded()
            self.write_track_table_pdf(path, selected_columns, orientation, self.pdf_options_from_controls(self.pdf_summary_options))
        except Exception as exc:
            show_alert("PDF export failed.", str(exc))
            if self.pdf_summary_status_label is not None:
                self.pdf_summary_status_label.setStringValue_("PDF export failed.")
            return
        self.last_pdf_path = path
        self.pdf_summary_path_field.setStringValue_(str(path))
        self.set_status(f"Saved track table PDF to {path}.")
        if self.pdf_summary_status_label is not None:
            self.pdf_summary_status_label.setStringValue_(f"Saved {path.name}.")

    @objc.IBAction
    def closePdfSummary_(self, _sender):
        if self.pdf_summary_window is not None:
            self.pdf_summary_window.close()

    def pdf_export_accessory(self):
        accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 340))
        title = NSTextField.labelWithString_("Columns")
        title.setFont_(NSFont.boldSystemFontOfSize_(13))
        title.setFrame_(NSMakeRect(0, 312, 160, 22))
        accessory.addSubview_(title)
        checkboxes = {}
        default_off = {"nr", "show", "speed", "ascent", "descent", "npoints"}
        for index, (identifier, title_text, _width, _editable) in enumerate(self.columns):
            column_index = index % 2
            row_index = index // 2
            x = column_index * 250
            y = 280 - row_index * 28
            checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 235, 24))
            checkbox.setButtonType_(NSButtonTypeSwitch)
            checkbox.setTitle_(title_text.replace("\n", " ").strip())
            checkbox.setState_(NSControlStateValueOff if identifier in default_off else NSControlStateValueOn)
            checkbox.setToolTip_(f"Include {title_text.replace(chr(10), ' ').strip()} in the PDF table.")
            accessory.addSubview_(checkbox)
            checkboxes[identifier] = checkbox
        options_title = NSTextField.labelWithString_("Maps")
        options_title.setFont_(NSFont.boldSystemFontOfSize_(13))
        options_title.setFrame_(NSMakeRect(0, 78, 160, 22))
        accessory.addSubview_(options_title)
        include_overview = NSButton.alloc().initWithFrame_(NSMakeRect(0, 52, 180, 24))
        include_overview.setButtonType_(NSButtonTypeSwitch)
        include_overview.setTitle_("Include overview plot")
        include_overview.setState_(NSControlStateValueOff)
        include_overview.setToolTip_("Add an overview map page after the table.")
        accessory.addSubview_(include_overview)
        elevation_profile = NSButton.alloc().initWithFrame_(NSMakeRect(190, 52, 190, 24))
        elevation_profile.setButtonType_(NSButtonTypeSwitch)
        elevation_profile.setTitle_("Elevation Profile")
        elevation_profile.setState_(NSControlStateValueOn)
        elevation_profile.setToolTip_("Add an elevation profile above each PDF map.")
        accessory.addSubview_(elevation_profile)
        rotate_maps = NSButton.alloc().initWithFrame_(NSMakeRect(390, 52, 150, 24))
        rotate_maps.setButtonType_(NSButtonTypeSwitch)
        rotate_maps.setTitle_("Rotate maps")
        rotate_maps.setState_(NSControlStateValueOff)
        rotate_maps.setToolTip_("Rotate each map image by 90 degrees on the PDF page.")
        accessory.addSubview_(rotate_maps)
        track_label = NSTextField.labelWithString_("Track plots")
        track_label.setFrame_(NSMakeRect(0, 22, 80, FIELD_HEIGHT))
        accessory.addSubview_(track_label)
        track_menu = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(90, 22, 190, FIELD_HEIGHT), False)
        track_menu.addItemsWithTitles_(["None", "All", "Visible", "Selected"])
        track_menu.selectItemAtIndex_(0)
        track_menu.setToolTip_("Choose which individual track maps to include after the overview. All includes tracks hidden in the Show column.")
        accessory.addSubview_(track_menu)
        orientation_label = NSTextField.labelWithString_("Page")
        orientation_label.setFrame_(NSMakeRect(300, 22, 50, FIELD_HEIGHT))
        accessory.addSubview_(orientation_label)
        orientation_menu = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(350, 22, 130, FIELD_HEIGHT), False)
        orientation_menu.addItemsWithTitles_(["Portrait", "Landscape"])
        orientation_menu.selectItemAtIndex_(0)
        orientation_menu.setToolTip_("Choose the PDF page orientation.")
        accessory.addSubview_(orientation_menu)
        pdf_options = {
            "include_overview": include_overview,
            "track_menu": track_menu,
            "elevation_profile": elevation_profile,
            "rotate_maps": rotate_maps,
        }
        return accessory, checkboxes, orientation_menu, pdf_options

    def pdf_options_from_controls(self, controls: dict) -> dict:
        track_modes = ["none", "all", "visible", "selected"]
        track_index = max(0, min(controls["track_menu"].indexOfSelectedItem(), len(track_modes) - 1))
        return {
            "include_overview": controls["include_overview"].state() == NSControlStateValueOn,
            "track_mode": track_modes[track_index],
            "elevation_profile": controls["elevation_profile"].state() == NSControlStateValueOn,
            "rotate_maps": controls["rotate_maps"].state() == NSControlStateValueOn,
        }

    def write_track_table_pdf(self, path: Path, selected_columns: list[str], orientation: str, pdf_options: dict | None = None):
        MPL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(MPL_CACHE_DIR)
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages

        column_meta = {identifier: (title, width) for identifier, title, width, _editable in self.columns}
        headers = [column_meta[identifier][0].strip() for identifier in selected_columns]
        pdf_source_rows = [row for row in self.table_rows if row.get("_summary") or row.get("show") != "no"]
        rows = [[row.get(identifier, "") for identifier in selected_columns] for row in pdf_source_rows]
        if not rows:
            rows = [["" for _identifier in selected_columns]]
        page_size = (11.69, 8.27) if orientation == "landscape" else (8.27, 11.69)
        left_margin = 0.30
        right_margin = 0.30
        title_top = 0.975
        table_top = 0.91
        bottom_margin = 0.05
        row_height_in = 0.24
        printable_height = max((table_top - bottom_margin) * page_size[1], row_height_in * 4)
        rows_per_page = max(1, int(printable_height / row_height_in) - 1)
        font_size = max(5.5, min(8.5, 10.5 - len(selected_columns) * 0.18))
        widths = [max(self.current_table_column_width(identifier, column_meta[identifier][1]), 40.0) for identifier in selected_columns]
        total_width = sum(widths) or 1.0
        pixels_per_inch = 72.0
        requested_table_width_in = total_width / pixels_per_inch
        printable_width_in = max(page_size[0] - left_margin - right_margin, 1.0)
        table_width_in = min(requested_table_width_in, printable_width_in)
        table_width_fraction = table_width_in / page_size[0]
        table_left_fraction = left_margin / page_size[0]
        col_widths = [width / total_width for width in widths]
        row_height_fraction = row_height_in / page_size[1]
        numeric_columns = {"row", "nr", "length", "duration", "sum", "distance", "speed", "ascent", "descent", "npoints"}
        with PdfPages(path) as pdf:
            for page_index, start in enumerate(range(0, len(rows), rows_per_page), start=1):
                page_rows = rows[start : start + rows_per_page]
                table_height = (len(page_rows) + 1) * row_height_fraction
                fig, ax = plt.subplots(figsize=page_size, dpi=self.pdf_document_dpi)
                fig.patch.set_facecolor("white")
                ax.axis("off")
                ax.text(
                    0.02,
                    0.975,
                    self.project_name or PROGRAM_TITLE,
                    transform=ax.transAxes,
                    fontsize=14,
                    fontweight="bold",
                    va="top",
                    color="#1f2933",
                )
                ax.text(
                    0.98,
                    0.975,
                    f"Track table - page {page_index}",
                    transform=ax.transAxes,
                    fontsize=9,
                    va="top",
                    ha="right",
                    color="#52606d",
                )
                table = ax.table(
                    cellText=page_rows,
                    colLabels=headers,
                    colWidths=col_widths,
                    loc="center",
                    cellLoc="left",
                    bbox=[table_left_fraction, table_top - table_height, table_width_fraction, table_height],
                )
                table.auto_set_font_size(False)
                table.set_fontsize(font_size)
                for (row_index, col_index), cell in table.get_celld().items():
                    cell.set_edgecolor("#c9d1d9")
                    cell.set_linewidth(0.45)
                    text = cell.get_text()
                    text.set_fontfamily("DejaVu Sans")
                    if row_index == 0:
                        cell.set_facecolor("#1f4e79")
                        text.set_color("white")
                        text.set_fontweight("bold")
                        text.set_ha("center")
                    else:
                        if selected_columns[col_index] in numeric_columns:
                            text.set_ha("right")
                        else:
                            text.set_ha("left")
                        source_row = pdf_source_rows[start + row_index - 1]
                        if source_row.get("_summary"):
                            cell.set_facecolor("#e8f1f8")
                            text.set_fontweight("bold")
                        elif row_index % 2 == 0:
                            cell.set_facecolor("#f7f9fb")
                fig.tight_layout(pad=0.3)
                pdf.savefig(fig, dpi=self.pdf_document_dpi)
                plt.close(fig)
            self.write_pdf_map_pages(pdf, page_size, orientation, pdf_options or {}, plt)

    def write_pdf_map_pages(self, pdf, page_size: tuple[float, float], orientation: str, options: dict, plt):
        if not options:
            return
        visible_tracks = self.visible_tracks()
        if not visible_tracks:
            return
        if options.get("include_overview"):
            self.set_status(f"PDF export: rendering overview map for {len(visible_tracks)} visible track(s).")
            fig = self.make_pdf_map_figure(
                page_size,
                "overview",
                visible_tracks,
                "Overview",
                orientation,
                options,
                plt,
            )
            if fig is not None:
                save_start = time.perf_counter()
                pdf.savefig(fig, dpi=self.pdf_document_dpi)
                save_done = time.perf_counter()
                if self.debug:
                    print(f"GPXEditor PDF benchmark: save overview page={save_done - save_start:.3f}s", flush=True)
                plt.close(fig)
        track_mode = options.get("track_mode", "none")
        if track_mode == "all":
            tracks = list(self.tracks)
        elif track_mode == "visible":
            tracks = visible_tracks
        elif track_mode == "selected":
            selected = {track.nr for track in self.selected_tracks()}
            tracks = [track for track in self.tracks if track.nr in selected]
        else:
            tracks = []
        total = len(tracks)
        for index, track in enumerate(tracks, start=1):
            self.set_status(f"PDF export: rendering track map {index}/{total} for track #{track.nr}: {track.name}.")
            fig = self.make_pdf_map_figure(
                page_size,
                "track",
                [track],
                f"Track #{track.nr}: {track.name}",
                orientation,
                options,
                plt,
            )
            if fig is not None:
                save_start = time.perf_counter()
                pdf.savefig(fig, dpi=self.pdf_document_dpi)
                save_done = time.perf_counter()
                if self.debug:
                    print(
                        f"GPXEditor PDF benchmark: save track #{track.nr} page={save_done - save_start:.3f}s",
                        flush=True,
                    )
                plt.close(fig)
                self.set_status(f"PDF export: wrote track map {index}/{total} for track #{track.nr}.")

    def make_pdf_map_figure(self, page_size, mode: str, tracks: list[TrackRecord], title: str, orientation: str, options: dict, plt):
        if not tracks:
            return None
        timing_start = time.perf_counter()
        import contextily as cx
        if hasattr(cx, "tile") and hasattr(cx.tile, "set_cache_dir"):
            cx.tile.set_cache_dir(str(self.tile_cache_dir))
        import_done = time.perf_counter()
        fig = plt.figure(figsize=page_size, dpi=self.pdf_document_dpi)
        fig.patch.set_facecolor("white")
        page_w, page_h = page_size
        left_inches = 0.72 if options.get("elevation_profile") else 0.42
        left = left_inches / page_w
        right = 0.25 / page_w
        top_summary_h = 0.78 / page_h
        bottom = 0.28 / page_h
        profile_h = 0.0
        gap = 0.08 / page_h
        if options.get("elevation_profile"):
            profile_h = 0.125
        available_h = 1.0 - bottom - top_summary_h - (profile_h + gap if profile_h else 0.0) - 0.02
        map_rect = [left, bottom, 1.0 - left - right, max(0.25, available_h)]
        if options.get("rotate_maps"):
            map_rect = [left, bottom, 1.0 - left - right, max(0.25, available_h)]
        summary = self.pdf_track_summary_text(mode, tracks, title)
        ax_text = fig.add_axes([left, 1.0 - top_summary_h + 0.01, 1.0 - left - right, top_summary_h - 0.02])
        ax_text.axis("off")
        ax_text.text(0.0, 0.92, title, ha="left", va="top", fontsize=11, fontweight="bold", color="#1f2933")
        ax_text.text(0.0, 0.58, summary, ha="left", va="top", fontsize=7.8, color="#38424d", linespacing=1.25)
        summary_done = time.perf_counter()
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
        requested_zoom = self.pdf_overview_zoom if mode == "overview" else self.pdf_track_zoom
        if mode == "overview":
            requested_zoom = self.overview_zoom_for_tracks(tracks, requested_zoom)
        tile_zoom, diagnostics = self.effective_tile_zoom_for_limit(
            extent, requested_zoom, self.pdf_maximum_map_tiles
        )
        missing_tiles = self.missing_osm_tile_count(diagnostics, tile_zoom)
        pixel_width = int(max(map_rect[2] * page_w * self.pdf_map_dpi, 1.0))
        pixel_height = int(max(map_rect[3] * page_h * self.pdf_map_dpi, 1.0))
        map_label = "overview" if mode == "overview" else f"track #{tracks[0].nr}"
        self.set_status(
            f"PDF export: plotting {map_label} at {pixel_width}x{pixel_height}px, "
            f"tile zoom {tile_zoom}, tiles {diagnostics['nx']}x{diagnostics['ny']}={diagnostics['count']}."
        )
        missing_basemap_tiles = 0
        try:
            if missing_tiles:
                self.set_status(
                    f"PDF export: connecting to map server for {missing_tiles} missing tile(s) "
                    f"for {map_label} at zoom {tile_zoom}."
                )
            else:
                self.set_status(f"PDF export: using cached OSM tiles for {map_label} at zoom {tile_zoom}.")
            missing_basemap_tiles = self.add_osm_basemap_with_timeout(cx, ax, tile_zoom)
        except Exception as exc:
            self.set_status(f"PDF export: map unavailable for {map_label}: {exc}")
            ax.text(0.5, 0.5, f"Map unavailable\n{exc}", transform=ax.transAxes, ha="center", va="center", color="white", fontsize=10)
        map_done = time.perf_counter()
        if missing_basemap_tiles:
            self.set_status(
                f"PDF export: {map_label} created with {missing_basemap_tiles} unavailable "
                f"map tile{'s' if missing_basemap_tiles != 1 else ''} skipped."
            )
        selected = {track.nr for track in self.selected_tracks()}
        for track in tracks:
            points = track.points()
            projected = [lonlat_to_web_mercator(point.lon, point.lat) for point in points]
            if len(projected) < 2:
                continue
            xs = [point[0] for point in projected]
            ys = [point[1] for point in projected]
            color = "red" if mode == "overview" and track.nr in selected else "blue"
            width = 5.5 if color == "red" else 3.6
            ax.plot(xs, ys, color=color, linewidth=width, solid_capstyle="round", zorder=3)
            if mode == "overview" and self.pdf_overview_show_endpoint_dots():
                ax.scatter([xs[0], xs[-1]], [ys[0], ys[-1]], s=26, c="white", edgecolors=color, linewidths=1.2, zorder=4)
            if mode == "track" and self.pdf_track_show_endpoint_dots():
                ax.scatter([xs[0], xs[-1]], [ys[0], ys[-1]], s=38, c="white", edgecolors="black", linewidths=1.2, zorder=4)
        self.draw_pdf_start_end_labels(ax, tracks, mode)
        if mode == "overview" and self.pdf_overview_show_track_numbers():
            for track in tracks:
                points = track.points()
                if points:
                    point = points[len(points) // 2]
                    x, y = lonlat_to_web_mercator(point.lon, point.lat)
                    ax.text(x, y, str(track.nr), ha="center", va="center", fontsize=7, color="white", bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.75, edgecolor="none"), zorder=5)
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
        row = next((row for row in self.table_rows if row.get("nr") == str(track.nr)), None)
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
            previous = None
            for index, point in enumerate(track.points()):
                if previous is not None:
                    distance += haversine_km(previous.lat, previous.lon, point.lat, point.lon)
                if point.ele is not None:
                    rows.append((distance, point.ele, track, index, point.time))
                previous = point
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
        ax.plot(xs, ys, color="blue", linewidth=1.4)
        if mode == "overview":
            selected = {track.nr for track in self.selected_tracks()}
            for track in tracks:
                if track.nr not in selected:
                    continue
                selected_rows = [row for row in rows if row[2] is track]
                if selected_rows:
                    ax.plot([row[0] for row in selected_rows], [row[1] for row in selected_rows], color="red", linewidth=2.4)
        ax.set_xlim(min(xs), max(xs))
        ax.set_ylim(y_min, y_max)
        ax.set_xlabel("Distance [km]", fontsize=7)
        ax.set_ylabel("Height [m]" if y_max < 3000 else "Height [km]", fontsize=7, labelpad=2)
        ax.tick_params(labelsize=6, length=2)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
        base_done = time.perf_counter()
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
        first_points = tracks[0].points()
        last_points = tracks[-1].points()
        if not first_points or not last_points:
            return
        endpoints = [("Start", first_points[0]), ("End", last_points[-1])]
        for label, point in endpoints:
            x, y = lonlat_to_web_mercator(point.lon, point.lat)
            edge = "black" if mode == "track" else "blue"
            ax.scatter([x], [y], s=42, c="white", edgecolors=edge, linewidths=1.3, zorder=6)
            ax.text(x, y, f" {label} ", ha="left", va="bottom", fontsize=7, color="white", bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.75, edgecolor="none"), zorder=7)

    def pdf_overview_show_track_numbers(self) -> bool:
        existing = self.existing_plot_view("overview")
        return bool(existing and existing[1].show_track_numbers)

    def pdf_overview_show_endpoint_dots(self) -> bool:
        existing = self.existing_plot_view("overview")
        return True if existing is None else bool(existing[1].show_endpoint_markers)

    def pdf_track_show_endpoint_dots(self) -> bool:
        existing = self.existing_plot_view("track")
        return True if existing is None else bool(existing[1].show_endpoint_markers)

    def current_table_column_width(self, identifier: str, fallback: float) -> float:
        try:
            column = self.track_table.tableColumnWithIdentifier_(nsstring(identifier))
            if column is not None:
                return float(column.width())
        except Exception:
            pass
        return float(fallback)

    def save_current(self) -> bool:
        if not self.tracks:
            show_alert("No tracks to save.")
            return False
        if not str(self.output_field.stringValue()).strip():
            return self.save_with_panel()
        field_path = self.output_path_from_field()
        if field_path is not None:
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
                if not self.tracks:
                    self.set_status(f"Output file set to {self.last_save_path}. Add tracks before saving.")
                    return False
                self.save_current()
                return True
        return False

    def build_root(self) -> ET.Element:
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
            root.append(copy.deepcopy(track.element))
        return root

    def write_gpx_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(self.build_root()).write(path, encoding="utf-8", xml_declaration=True)

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
        self.load_gpx_paths([RECOVERY_PATH], mark_dirty=False)
        self.dirty = True
        self.set_status(f"Loaded recovery file {RECOVERY_PATH}. Save it to keep the recovered edits.")
        return True

    def save_to_path(self, path: Path, autosave: bool = False):
        if not autosave:
            self.backup_existing_file(path)
        self.write_gpx_file(path)
        if autosave:
            self.set_status(f"Recovery autosave written to {path}.")
            return
        self.set_output_path(path)
        self.dirty = False
        self.delete_recovery_file()
        self.set_status(("Autosaved" if autosave else "Saved") + f" {len(self.tracks)} track(s) to {path}.")
        self.notify_save_callback(path)

    def write_track_json(self, directory: Path, base: str):
        for row, track in zip(self.table_rows, self.tracks):
            data = {
                "source_gpx": str(self.last_save_path) if self.last_save_path else None,
                "nr": track.nr,
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
        self.anchor = (points[0].lat, points[0].lon)
        self.mark_dirty(f"Anchorpoint set to the first point of track #{track.nr}.")

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
            if window is None or not window.isVisible():
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
        inspector.plot_(None)
        if inspector.plot_view is not None:
            self.raise_elevation_profile_for_plot_view(inspector.plot_view)
        self.set_status(f"Opened inspector, track map, and elevation profile for track #{track.nr}.")

    def sort_by_column(self, column: str, ascending: bool | None = None, update_header: bool = True, source: str = "direct"):
        if not self.tracks:
            return
        if column not in {identifier for identifier, _title, _width, _editable in self.columns}:
            self.set_status(f"Ignored unknown sort column: {column}")
            return
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
            direction = "ascending" if self.sort_ascending else "descending"
            after_order = [track.nr for track in self.tracks[:6]]
            scope_text = f"selected {len(selected_indexes)} row(s)" if scoped_sort else "all rows"
            self.set_status(f"Sorted {scope_text} by {column} with date exceptions ({direction}, {source}): {before_order} -> {after_order}.")
            return
        def key(track):
            metrics = metric_cache[id(track)]
            current_index = position_cache[id(track)]
            mapping = {
                "row": current_index,
                "nr": track.nr,
                "name": track.name.casefold(),
                "date": metrics["time"] or datetime.max.replace(tzinfo=UTC),
                "length": metrics["length_km"],
                "duration": metrics["duration"].total_seconds() if metrics["duration"] else float("inf"),
                "sum": current_index,
                "distance": metrics["distance_km"] if metrics["distance_km"] is not None else float("inf"),
                "speed": metrics["speed_kmh"] if metrics["speed_kmh"] is not None else float("inf"),
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
        direction = "ascending" if self.sort_ascending else "descending"
        after_order = [track.nr for track in self.tracks[:6]]
        scope_text = f"selected {len(selected_indexes)} row(s)" if scoped_sort else "all rows"
        self.set_status(f"Sorted {scope_text} by {column} ({direction}, {source}): {before_order} -> {after_order}.")

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
        regular = [track for track in tracks if not self.is_exceptional(track)]
        exceptional = [track for track in tracks if self.is_exceptional(track)]
        regular.sort(key=lambda track: (track.metrics["time"] or datetime.max.replace(tzinfo=UTC), track.name.casefold()))
        exceptional.sort(key=lambda track: (track.metrics["distance_km"] if track.metrics["distance_km"] is not None else float("inf"), track.name.casefold()))
        merged = list(regular)
        for track in exceptional:
            distance = track.metrics["distance_km"] if track.metrics["distance_km"] is not None else float("inf")
            insert_at = len(merged)
            for index, candidate in enumerate(merged):
                candidate_distance = candidate.metrics["distance_km"] if candidate.metrics["distance_km"] is not None else float("inf")
                if distance <= candidate_distance:
                    insert_at = index
                    break
            merged.insert(insert_at, track)
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
    def selectionFieldCommitted_(self, _sender):
        valid = {track.nr for track in self.tracks}
        self.selected_nrs = [number for number in parse_track_numbers(str(self.selection_field.stringValue())) if number in valid]
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
        self.selection_field.setStringValue_(compress_track_numbers(self.selected_nrs))

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
        indexes = sorted(set(index for index in indexes if 0 <= index < len(self.tracks)))
        if not indexes:
            return
        self.push_undo()
        moving = [self.tracks[index] for index in indexes]
        remaining = [track for index, track in enumerate(self.tracks) if index not in indexes]
        before_target = sum(1 for index in indexes if index < target_row)
        target = max(0, min(len(remaining), target_row - before_target))
        self.tracks = remaining[:target] + moving + remaining[target:]
        self.mark_dirty("Tracks reordered by drag and drop.")

    def delete_selected_tracks(self):
        selected = self.selected_tracks()
        if not selected:
            return
        label = ", ".join(f"#{track.nr}" for track in selected[:12])
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
        self.mark_dirty(f"Deleted {len(selected)} selected {plural}.")
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
        self.mark_dirty(f"Joined {len(selected)} tracks into track #{primary.nr} using metadata from track #{metadata_source.nr}.")
        self.refresh_open_plot_views()

    def choose_join_metadata_track(self, tracks: list[TrackRecord]) -> TrackRecord | None:
        rows = []
        for track in tracks:
            metrics = self.compute_metrics(track)
            rows.append(
                {
                    "nr": str(track.nr),
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
        for child in list(target.element):
            if child.tag != qname("trkseg"):
                target.element.remove(child)
        insert_at = 0
        for child in list(source.element):
            if child.tag == qname("trkseg"):
                continue
            target.element.insert(insert_at, copy.deepcopy(child))
            insert_at += 1

    @objc.IBAction
    def plotAll_(self, _sender):
        selected = self.selected_tracks()
        if selected:
            self.open_plot_window("overview", recreate_existing=True, tracks_override=selected)
        else:
            self.open_plot_window("overview", recreate_existing=False)

    @objc.IBAction
    def plotSelected_(self, _sender):
        if not self.selected_tracks() and self.tracks:
            self.selected_nrs = [self.tracks[0].nr]
            self.update_selection_field()
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
        points = track.points()
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
            "distance_km": metrics["distance_km"],
            "raw_points": [(point.lat, point.lon) for point in points],
            "filtered_point_count": len(points),
            "raw_point_count": len(points),
            "original_sequence_number": track.nr,
            "table_number": table_number,
        }

    def render_summary_plot(self, mode: str, zoom_level: int | None = None, tracks_override: list[TrackRecord] | None = None) -> dict | None:
        if render_track_plot is None:
            show_alert("Plotting is unavailable.", "The map drawing support could not be loaded.")
            return None
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
            import contextily as cx
            if hasattr(cx, "tile") and hasattr(cx.tile, "set_cache_dir"):
                cx.tile.set_cache_dir(str(self.tile_cache_dir))
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
        projected = [
            lonlat_to_web_mercator(point.lon, point.lat)
            for track in tracks
            for point in track.points()
        ]
        if not projected:
            return {"min_x": -1.0, "max_x": 1.0, "min_y": -1.0, "max_y": 1.0}
        xs = [point[0] for point in projected]
        ys = [point[1] for point in projected]
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

    def add_osm_basemap_with_timeout(self, cx, ax, tile_zoom: int):
        """Call Contextily with the configured provider and bounded timeout."""
        try:
            import requests
        except ImportError:
            requests = None
        try:
            with contextily_request_timeout(cx, self.map_request_timeout_seconds):
                with tolerate_missing_tiles(cx) as missing_tile_report:
                    cx.add_basemap(
                        ax,
                        source=contextily_provider(
                            cx,
                            self.map_provider,
                            self.custom_map_url,
                            self.custom_map_attribution,
                            self.maximum_map_zoom,
                        ),
                        zoom=min(tile_zoom, self.maximum_map_zoom),
                    )
            return missing_tile_report.count
        except Exception as exc:
            if requests is not None and isinstance(exc, (requests.Timeout, requests.ConnectionError)):
                raise TimeoutError(f"Map tile request timed out after {self.map_request_timeout_seconds:.0f}s.") from exc
            raise

    def render_viewport_plot(self, mode: str, tracks: list[TrackRecord], extent: dict, tile_zoom: int) -> dict | None:
        timing_start = time.perf_counter()
        import_done = timing_start
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
            import contextily as cx
            import matplotlib.pyplot as plt
            if hasattr(cx, "tile") and hasattr(cx.tile, "set_cache_dir"):
                cx.tile.set_cache_dir(str(self.tile_cache_dir))
            import_done = time.perf_counter()
        except ImportError as exc:
            show_alert("Could not render the map viewport.", f"Missing plotting dependency: {exc}")
            return None

        requested_tile_zoom = tile_zoom
        extent = self.fit_extent_to_aspect(extent, (1920, 1080))
        tile_zoom, diagnostics = self.effective_tile_zoom(extent, requested_tile_zoom)
        cache_before = self.count_tile_cache_files()
        missing_tiles = self.missing_osm_tile_count(diagnostics, tile_zoom)
        total_tiles = diagnostics["count"]
        width_px, height_px = 1920, 1080
        dpi = 100
        fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
        fig.patch.set_facecolor("black")
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
        ax.set_facecolor("black")
        ax.set_xlim(extent["min_x"], extent["max_x"])
        ax.set_ylim(extent["min_y"], extent["max_y"])
        ax.set_aspect("equal", adjustable="box")
        setup_done = time.perf_counter()
        if missing_tiles:
            self.set_status(f"Checking OSM cache for {total_tiles} tile(s) at zoom {tile_zoom}...")
        else:
            self.set_status(f"Loading {total_tiles} cached OSM tile(s) at zoom {tile_zoom}...")
        missing_basemap_tiles = 0
        try:
            if missing_tiles:
                self.set_status(
                    f"Connecting to map server for {missing_tiles} tile(s) at zoom {tile_zoom} "
                    f"(timeout {OSM_REQUEST_TIMEOUT_SECONDS:.0f}s per request)..."
                )
            missing_basemap_tiles = self.add_osm_basemap_with_timeout(cx, ax, tile_zoom)
        except Exception as exc:
            plt.close(fig)
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
            self.cached_osm_tile_urls = None
        for track in tracks:
            projected = [lonlat_to_web_mercator(point.lon, point.lat) for point in track.points()]
            if len(projected) < 2:
                continue
            xs = [point[0] for point in projected]
            ys = [point[1] for point in projected]
            ax.plot(xs, ys, color="blue", linewidth=4.0, solid_capstyle="butt", zorder=3)
        tracks_done = time.perf_counter()
        ax.axis("off")
        png_buffer = io.BytesIO()
        fig.savefig(png_buffer, format="png", dpi=dpi, facecolor="black", bbox_inches=None, pad_inches=0)
        plt.close(fig)
        save_done = time.perf_counter()
        png_data = png_buffer.getvalue()
        image = nsimage_from_png_bytes(png_data)
        image_done = time.perf_counter()
        map_seconds = basemap_done - setup_done
        total_seconds = image_done - timing_start
        if missing_tiles:
            status_message = (
                f"Downloaded {missing_tiles} OSM tile(s) in {map_seconds:.2f}s; "
                f"map rendered in {total_seconds:.2f}s."
            )
        else:
            status_message = (
                f"Loaded {total_tiles} cached OSM tile(s) in {map_seconds:.2f}s; "
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

    def open_plot_window(self, mode: str, recreate_existing: bool = True, tracks_override: list[TrackRecord] | None = None):
        if not self.tracks:
            show_alert("No tracks to plot.")
            return
        existing = self.existing_plot_view(mode)
        if existing is not None and not recreate_existing:
            window, view = existing
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            view.update_window_title()
            view.setNeedsDisplay_(True)
            self.open_elevation_profile_for_plot_view(view)
            window.makeKeyAndOrderFront_(None)
            window.makeFirstResponder_(view)
            return view
        plot_info = self.render_summary_plot(mode, tracks_override=tracks_override)
        if plot_info is None:
            return
        if existing is not None:
            window, view = existing
            view.plot_info = plot_info
            view.initial_plot_info = view.clone_plot_info(plot_info)
            view.cursor = None
            view.marker = None
            view.last_viewport_signature = None
            window.makeKeyAndOrderFront_(None)
            window.orderFrontRegardless()
            window.makeFirstResponder_(view)
            view.update_window_title()
            view.setNeedsDisplay_(True)
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
        points = track.points()
        total = 0.0
        for previous, current in zip(points[:point_index], points[1:point_index + 1]):
            total += haversine_km(previous.lat, previous.lon, current.lat, current.lon)
        return total

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
        self.anchor = (point.lat, point.lon)
        self.mark_dirty(f"Anchor point set to {point.lat:.6f}, {point.lon:.6f}.")

    def delete_points(self, track: TrackRecord, start: int, end: int):
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
        self.mark_dirty(f"Deleted {len(delete_elements)} point(s) from track #{track.nr}.")
        self.refresh_open_plot_views()

    def cut_track(self, track: TrackRecord, index: int):
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
        self.next_nr += 1
        insert_at = self.tracks.index(track) + 1
        self.tracks.insert(insert_at, new_record)
        self.invalidate_track_metrics(track)
        self.invalidate_track_metrics(new_record)
        self.selected_nrs = [track.nr, new_record.nr]
        self.update_selection_field()
        self.mark_dirty(f"Cut track #{track.nr}; new track #{new_record.nr} created.")
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
            show_alert("Split point must leave points in both tracks.")
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
        self.next_nr += 1
        insert_at = self.tracks.index(track) + 1
        self.tracks.insert(insert_at, new_record)
        self.invalidate_track_metrics(track)
        self.invalidate_track_metrics(new_record)
        self.mark_dirty(f"Split track #{track.nr}; new track #{new_record.nr} created.")
        return new_record

    @objc.IBAction
    def undo_(self, _sender):
        if not self.undo_stack:
            self.set_status("Nothing to undo.")
            return
        self.redo_stack.append(self.snapshot())
        snapshot = self.undo_stack.pop()
        self.restore_snapshot(snapshot)
        self.dirty = True
        self.set_status("Undo applied.")
        self.refresh_open_plot_views()

    @objc.IBAction
    def redo_(self, _sender):
        if not self.redo_stack:
            self.set_status("Nothing to redo.")
            return
        self.undo_stack.append(self.snapshot())
        snapshot = self.redo_stack.pop()
        self.restore_snapshot(snapshot)
        self.dirty = True
        self.set_status("Redo applied.")
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
        if not self.confirm_quit():
            return
        self.quit_confirmed = True
        self.close_main_editor_window(delete_recovery=True)

    @objc.IBAction
    def help_(self, _sender):
        text = (
            "Add Tracks loads GPX files into the table. Edit a track name or date directly in the table and press Enter; Esc cancels the edit.\n\n"
            "Click a table row to select a track. Shift-click or drag to select more than one. Double-click a track row to open its waypoint inspector. Sort the table by clicking a column header; clicking the same header again reverses the direction. Date & Time sorting uses the special placement rule for untimed or zero-duration tracks. If more than one table row is selected, sorting only reorders those selected rows and leaves all other rows fixed in place. If zero or one row is selected, sorting applies to the full table. Drag selected rows to reorder tracks. Press Backspace/Delete to delete selected tracks after confirmation.\n\n"
            "Use the selection field to type track numbers such as 1,3-5. Select All and Unselect All change the current track selection. Join Tracks merges selected tracks into the first selected track. Set Anchorpoint uses the first point of the current first selected track for distance calculations.\n\n"
            "Plot Overview opens an OpenStreetMap overview. If tracks are selected, the overview zooms to those tracks and highlights them in red; selecting different tracks in the table updates the red highlight. Click a track in the overview to select it in the table. Double-click a point in the overview to open that track in the inspector and track map at the same point.\n\n"
            "Plot Track(s) opens a detailed map for the selected track. View File opens the original GPX source file of the selected track in TextEdit and asks whether unsaved edits should be saved first. Click or drag on a map to move the white cursor dot and arrow to the nearest waypoint. Double-click a track point to open its waypoint inspector. Press i to show or hide point information; h shows map keys; a sets the anchorpoint; + and - zoom; c centers on the cursor; z zooms to the current selection; r zooms out to the full map extent; q closes the plot window. In the track map, m or Shift-click sets a marker, Delete removes the marker-to-cursor point range after confirmation, and x cuts the track at the cursor after confirmation.\n\n"
            "Inspect Track opens all waypoints of one track. The gear button edits GPX processing, PDF, and map-service settings. A standalone editor keeps these settings for future sessions; an editor opened from an Adventure stores them with that Adventure. The Output file field shows where the GPX will be written; edit it and press Enter, use the folder button, or press Save to save there. Existing GPX files are backed up as .bak before they are overwritten. PNG saves the currently open track plot image. PDF exports the track table and lets you choose columns, page orientation, folder, and filename. Save & Exit saves and closes the editor. Quit asks whether to save unsaved changes. A recovery file is written periodically while there are unsaved changes."
        )
        self.show_scrollable_help("myCamino GPX Editor Help", text)

    def show_scrollable_help(self, title: str, text: str):
        if self.help_window is not None and self.help_window.isVisible():
            self.help_window.makeKeyAndOrderFront_(None)
            self.help_window.orderFrontRegardless()
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(220, 180, 760, 560),
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
        window.makeKeyAndOrderFront_(None)
        self.register_auxiliary_window(window)
        self.help_window = window

    @objc.IBAction
    def autosave_(self, _timer):
        if not self.dirty or not self.tracks:
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
        self.controller = GPXEditorController.alloc().initStandalone_(True)
        self.controller.debug = bool(self.debug)
        if self.output_path is not None:
            self.controller.set_output_path(self.output_path)
            self.controller.set_status(f"Default save file: {self.output_path}")
        loaded_recovery = self.controller.offer_recovery_load()
        if self.startup_paths:
            self.controller.load_gpx_paths(self.startup_paths, mark_dirty=False)
        elif loaded_recovery:
            self.controller.recalculate()
        self.controller.show()
        NSApp().activateIgnoringOtherApps_(True)


def show_gpx_editor(
    gpx_paths: list[str | os.PathLike] | None = None,
    standalone: bool = False,
    output_file: str | os.PathLike | None = None,
    debug: bool = False,
    on_close=None,
    on_save=None,
    on_settings_change=None,
    settings=None,
):
    controller = GPXEditorController.alloc().initStandalone_(standalone)
    if settings is not None:
        controller.apply_project_parameters(settings)
    controller.debug = bool(debug)
    controller.on_close_callback = on_close
    controller.on_save_callback = on_save
    controller.on_settings_change_callback = on_settings_change
    if output_file is not None:
        controller.set_output_path(Path(output_file).expanduser().resolve())
    loaded_recovery = controller.offer_recovery_load()
    if gpx_paths:
        controller.load_gpx_paths([Path(path).expanduser().resolve() for path in gpx_paths], mark_dirty=False)
    elif loaded_recovery:
        controller.recalculate()
    controller.show()
    return controller


def show_gpx_editor_from_cli_args(
    cli_args: list[str] | tuple[str, ...] | None = None,
    standalone: bool = False,
    on_close=None,
    on_save=None,
    on_settings_change=None,
    settings=None,
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

    This function creates and shows the editor window but does not start the
    AppKit event loop.  If you call it from a non-GUI script, create/run
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
        settings=settings,
    )


def export_pdf_summary_from_paths(
    gpx_paths: list[str | os.PathLike] | tuple[str | os.PathLike, ...],
    output_file: str | os.PathLike | None = None,
    project_name: str | None = None,
    debug: bool = False,
    settings=None,
) -> Path | None:
    """Open the standard GPXEditor PDF export panel for GPX paths."""
    paths = [Path(path).expanduser().resolve() for path in gpx_paths or []]
    if not paths:
        show_alert("No GPX file selected.", "Select a GPX file before exporting a PDF summary.")
        return None
    controller = GPXEditorController.alloc().initStandalone_(False)
    if settings is not None:
        controller.apply_project_parameters(settings)
    controller.debug = bool(debug)
    if output_file is not None:
        controller.set_output_path(Path(output_file).expanduser().resolve())
    if project_name:
        controller.project_name = str(project_name)
        controller.project_field.setStringValue_(controller.project_name)
    try:
        controller.load_gpx_paths(paths, mark_dirty=False)
        controller.exportPdf_(None)
        return controller.last_pdf_path
    finally:
        try:
            controller.autosave_timer.invalidate()
        except Exception:
            pass
        controller.close_main_editor_window(delete_recovery=False)


def run_gpx_editor_from_cli_args(cli_args: list[str] | tuple[str, ...] | None = None) -> int:
    """Run the standalone editor application from CLI-style argument strings.

    This is useful for another Python launcher that wants to pass the same
    arguments accepted by ``GPXEditor.py`` and let this module own the AppKit
    event loop.
    """
    parser = build_argument_parser()
    args = parser.parse_args(list(cli_args or []))
    startup_paths, output_path, debug = startup_options_from_args(args, parser=parser)
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

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().initWithStartupPaths_outputPath_debug_(startup_paths, output_path, debug)
    app.setDelegate_(delegate)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
