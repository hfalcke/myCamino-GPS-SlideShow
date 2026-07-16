#!/Users/falcke/Dropbox/Documents/Python/trackit/.venv/bin/python
"""Native macOS controller window for GPX adventure setup."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBox,
    NSBoxSeparator,
    NSButton,
    NSButtonCell,
    NSButtonTypeRadio,
    NSButtonTypeSwitch,
    NSColor,
    NSColorSpace,
    NSColorWell,
    NSComboBox,
    NSComboBoxCell,
    NSFont,
    NSFontAttributeName,
    NSFontManager,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageAlignCenter,
    NSImageCell,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSImageNameFolder,
    NSMakePoint,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSInsetRect,
    NSOpenPanel,
    NSPasteboard,
    NSPopUpButton,
    NSProgressIndicator,
    NSScrollView,
    NSShadow,
    NSSavePanel,
    NSSearchField,
    NSSortDescriptor,
    NSStepper,
    NSTableColumn,
    NSTableView,
    NSTableViewSolidVerticalGridLineMask,
    NSTextField,
    NSTextView,
    NSView,
    NSViewHeightSizable,
    NSViewMaxXMargin,
    NSViewMinXMargin,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSAlert,
    NSWorkspace,
    NSControlTextDidEndEditingNotification,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSDragOperationMove,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagShift,
    NSEventTypeKeyDown,
    NSItalicFontMask,
    NSBoldFontMask,
    NSPasteboardTypeString,
    NSTableViewDropAbove,
    NSTerminateCancel,
    NSTerminateNow,
)
from Foundation import NSDate, NSIndexSet, NSObject, NSAttributedString, NSMakeSize, NSNotificationCenter, NSRunLoop, NSString, NSURL, NSPredicate, NSTimer

try:
    from AVFoundation import AVPlayer
    from AVKit import AVPlayerView, AVPlayerViewControlsStyleInline
    from CoreMedia import CMTimeMake

    AVKIT_MEDIA_VIEWER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    AVKIT_MEDIA_VIEWER_AVAILABLE = False
    AVPlayer = None
    AVPlayerView = None
    AVPlayerViewControlsStyleInline = 1
    CMTimeMake = None

from GetGeoLocations import (
    GPS_NOT_AVAILABLE,
    PLACE_NOT_AVAILABLE,
    PLACE_FAILED,
    PLACE_NOT_REQUESTED,
    ProcessingCancelled as GeoLocationsCancelled,
    analyze_control_file_updates,
    analyze_track_map_reference_updates,
    build_sorted_output_path,
    commit_control_file_update_plan,
    discover_media_update_candidates,
    load_tracks_summary,
    normalize_filename_for_match,
    parse_control_file_entries,
    media_map_specs_from_control_entries,
    control_media_points,
    media_map_output_filename,
    project_map_plan_from_sidecars,
    render_project_map_plan,
    render_media_map_specs,
    run_with_options as run_geolocations_with_options,
    track_endpoint_entry_matches,
)
from gpx_tracks_table import (
    MINIMUM_MAP_SHORT_DIMENSION_M,
    media_coordinates_fingerprint,
    media_map_metadata_matches_coordinates,
    media_overview_fingerprint,
    parse_track_selection,
    parse_gpx_file,
    prepare_with_options,
    execute_map_variants_from_context,
    run_with_options as run_gpx_tracks_table_with_options,
    render_media_overview_map,
    selected_track_numbers,
)
from plot_metadata_utils import (
    media_sidecar_freshness,
    read_photo_metadata,
    read_plot_metadata,
    read_table_data,
    validate_media_sidecar,
)
from cocoa_button_style import apply_liquid_glass_button_style, make_liquid_glass_button
from track_map_layout_utils import (
    DEFAULT_TRACK_EDGE_MARGIN_FRACTION,
    resolve_track_map_variant,
    time_lapse_track_map_name,
)
from adventure_parameters import (
    PARAMETER_SPECS,
    SECTION_ORDER,
    SPECS_BY_KEY,
    changed_parameter_keys,
    default_parameters,
    map_affecting_parameter_keys,
    normalize_parameter_value,
    normalize_parameters,
    parameter_payload,
    parameter_subset,
    validate_parameters,
    visible_specs_for_section,
)
from cocoa_parameter_editor import CocoaParameterEditor
from json_storage import atomic_write_json
from adventure_files import (
    ADVENTURE_FORMAT_VERSION,
    AdventureFormatError,
    discover_adventures,
    filename_base as adventure_filename_base,
    load_adventure,
    project_file_names,
    rename_or_copy_adventure,
    shared_references,
    validate_adventure_payload,
)
from audio_playlist import (
    AUDIO_EXTENSIONS,
    album_directories,
    audio_files_in_directory,
    generated_playlist_text,
    load_audio_playlist,
    updated_playlist_text,
)
from slideshow_control_format import MusicSyntaxError, is_music_directive, parse_music_parameters
from map_overlay import MAP_CONTENT_VERSION
from workflow_assistant import (
    assistant_bubble_size,
    assistant_stage_uses_text_input,
    bubble_geometry,
    detected_gpx_choices,
    next_assistant_stage,
    normalize_assistant_state,
    relocated_bubble_geometry,
)


class ProjectStatusRefreshSuperseded(Exception):
    """Stop an obsolete cooperative project-status calculation."""


def resolve_gui_startup_paths(project_directory=None, startup_path=None):
    """Classify the optional positional path as an Adventure or project directory."""
    project_dir = (
        Path(str(project_directory)).expanduser().resolve(strict=False)
        if project_directory
        else None
    )
    adventure_file = None
    if startup_path:
        candidate = Path(str(startup_path)).expanduser().resolve(strict=False)
        if candidate.is_dir():
            if project_dir is not None and project_dir != candidate:
                raise ValueError(
                    "The positional project directory and --project-directory refer to different directories."
                )
            project_dir = candidate
        else:
            adventure_file = candidate
    return project_dir, adventure_file


PHOTOS_FRAMEWORK_AVAILABLE = True
try:
    objc.loadBundle("Photos", globals(), bundle_path="/System/Library/Frameworks/Photos.framework")
    PHAsset = objc.lookUpClass("PHAsset")
    PHAssetCollection = objc.lookUpClass("PHAssetCollection")
    PHAssetResource = objc.lookUpClass("PHAssetResource")
    PHContentEditingInputRequestOptions = objc.lookUpClass("PHContentEditingInputRequestOptions")
    PHFetchOptions = objc.lookUpClass("PHFetchOptions")
    PHPhotoLibrary = objc.lookUpClass("PHPhotoLibrary")
except Exception:
    PHOTOS_FRAMEWORK_AVAILABLE = False
    PHAsset = None
    PHAssetCollection = None
    PHAssetResource = None
    PHContentEditingInputRequestOptions = None
    PHFetchOptions = None
    PHPhotoLibrary = None


PROGRAM_TITLE = "myCamino GPS Track Show"
MIN_WINDOW_WIDTH = 920
MIN_WINDOW_HEIGHT = 720
LABEL_WIDTH = 140.0
BUTTON_WIDTH = 110.0
FILE_BUTTON_WIDTH = 28.0
EDIT_BUTTON_WIDTH = 150.0
SMALL_BUTTON_WIDTH = 90.0
PLOT_BUTTON_WIDTH = 110.0
FIELD_HEIGHT = 28.0
PADDING = 18.0
BLOCK_GAP = 8.0
INNER_GAP = 10.0
ROW_GAP = 8.0
DESCRIPTION_HEIGHT = 48.0
STATUS_HEIGHT = 24.0
PROGRESS_HEIGHT = 18.0
PLOT_VIEWER_CAPTION_HEIGHT = 32.0
SECTION_STATUS_SIZE = 24.0
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff",
    ".bmp", ".webp", ".mp4", ".mov", ".m4v", ".avi", ".mts", ".m2ts",
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff",
    ".bmp", ".webp",
}
VIDEO_EXTENSIONS = MEDIA_EXTENSIONS - IMAGE_EXTENSIONS
CONTROL_TABLE_COLUMNS = ["preview", "file_datetime", "type", "name", "time", "gps", "place"]
CONTROL_TABLE_TITLES = {
    "preview": "",
    "file_datetime": "File date",
    "type": "Type",
    "name": "File / Date / Map",
    "time": "Time",
    "gps": "GPS coordinates",
    "place": "Place",
}
CONTROL_ROW_TYPE_KEYWORDS = {
    "MAP": "Overviewmap",
    "TRK": "Map",
    "BEF": "MapBefore",
    "AFT": "MapAfter",
    "LOC": "MediaMap",
    "DAT": "Datum",
    "MUS": "MUSIC",
}
CONTROL_ROW_TYPE_DESCRIPTIONS = {
    "IMG": "Image",
    "VID": "Video",
    "MAP": "Overview map",
    "TRK": "Track map",
    "BEF": "Day before map",
    "AFT": "Day after map",
    "LOC": "Media location map",
    "DAT": "Date",
    "MUS": "Music control",
}
CONTROL_ROW_TYPES = tuple(CONTROL_ROW_TYPE_DESCRIPTIONS)
CONTROL_ROW_TYPE_CHOICES = tuple(
    f"{row_type} - {description}"
    for row_type, description in CONTROL_ROW_TYPE_DESCRIPTIONS.items()
)
CONTROL_TABLE_DRAG_TYPE = NSPasteboardTypeString
PH_AUTH_NOT_DETERMINED = 0
PH_AUTH_RESTRICTED = 1
PH_AUTH_DENIED = 2
PH_AUTH_AUTHORIZED = 3
PH_AUTH_LIMITED = 4
PH_COLLECTION_TYPE_ALBUM = 1
PH_COLLECTION_TYPE_SMART_ALBUM = 2
PH_COLLECTION_SUBTYPE_ANY = 9223372036854775807
PH_ASSET_MEDIA_TYPE_IMAGE = 1
PROJECT_AUDIO_DIRECTORY_NAME = "audio"


def control_file_update_requires_review(plan) -> bool:
    """Return whether applying a plan would move an existing media row."""
    if plan is None or getattr(plan, "media", None) is None:
        return False
    return any(
        bool(item.apply_update)
        and int(item.included_count) > 0
        and bool(item.reposition)
        for item in plan.media.items
    )


def track_endpoint_place_completeness(track, metadata_variants, radius_m):
    """Return expected and complete current endpoint-place entries for one track."""
    fingerprint = str(track.get("track_fingerprint") or "").strip()
    matching_metadata = [
        metadata
        for metadata in metadata_variants
        if isinstance(metadata, dict)
        and (
            not fingerprint
            or str(metadata.get("track_fingerprint") or "").strip() == fingerprint
        )
    ]
    expected = 0
    complete = 0
    for endpoint_name, point_key in (("start", "first_point"), ("end", "last_point")):
        point = track.get(point_key)
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            latitude = float(point[0])
            longitude = float(point[1])
        except (TypeError, ValueError):
            continue
        expected += 1
        if matching_metadata and all(
            track_endpoint_entry_matches(
                metadata.get("track_endpoint_places", {}).get(endpoint_name)
                if isinstance(metadata.get("track_endpoint_places"), dict)
                else None,
                latitude,
                longitude,
                radius_m,
            )
            for metadata in matching_metadata
        ):
            complete += 1
    return expected, complete


def ensure_project_audio_directory(project_dir) -> Path:
    """Return the fixed project audio directory, creating it when necessary."""
    audio_dir = (
        Path(project_dir).expanduser().resolve(strict=False)
        / PROJECT_AUDIO_DIRECTORY_NAME
    )
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir.resolve(strict=False)


def playlist_belongs_to_audio_directory(playlist_path, audio_dir) -> bool:
    """Return whether a playlist is a direct child of the project audio folder."""
    if playlist_path is None or audio_dir is None:
        return False
    playlist = Path(playlist_path).expanduser().resolve(strict=False)
    directory = Path(audio_dir).expanduser().resolve(strict=False)
    return (
        playlist.parent == directory
        and playlist.suffix.casefold() == ".playlist"
    )


def control_file_signature(path):
    """Return the stable identity used to detect an unchanged control file."""
    candidate = Path(path).expanduser().resolve(strict=False)
    try:
        stat_result = candidate.stat()
    except OSError:
        return None
    return (str(candidate), int(stat_result.st_size), int(stat_result.st_mtime_ns))


def control_file_recovery_is_newer(control_path, recovery_path):
    """Return whether a recovery copy should take precedence over the saved list."""
    recovery_signature = control_file_signature(recovery_path)
    if recovery_signature is None:
        return False
    control_signature = control_file_signature(control_path)
    return control_signature is None or recovery_signature[2] > control_signature[2]


def show_alert(message: str, informative: str = "") -> None:
    """Show a modal alert."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_(message)
    if informative:
        alert.setInformativeText_(informative)
    alert.runModal()


def confirm_alert(message: str, informative: str = "", confirm_title: str = "Proceed", cancel_title: str = "Cancel") -> bool:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(message)
    if informative:
        alert.setInformativeText_(informative)
    alert.addButtonWithTitle_(confirm_title)
    alert.addButtonWithTitle_(cancel_title)
    return int(alert.runModal()) == 1000


def control_table_backup_directory(control_path: Path) -> Path:
    """Return the private recovery directory beside a control file."""
    return control_path.parent / ".mycamino-control-backups"


def control_table_recovery_path(control_path: Path) -> Path:
    """Return the stable crash-recovery path for an editable control file."""
    return control_table_backup_directory(control_path) / f"{control_path.name}.recovery.lst"


def write_text_atomic(path: Path, text: str) -> None:
    """Write UTF-8 text atomically in the destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def nsstring(value: str) -> NSString:
    """Return an NSString for AppKit APIs."""
    return NSString.stringWithString_(value)


def local_datetime_text(value):
    """Format a timezone-aware datetime for the UI."""
    if value is None:
        return "N/A"
    return value.astimezone().strftime("%d.%m.%Y %H:%M")


def project_filename_base(text: str) -> str:
    """Return a filesystem-safe project base name."""
    cleaned = (text or "").strip().replace("/", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "project"


def ensure_default_project_playlist(project_dir, project_name) -> Path:
    """Create the empty default playlist for one Adventure when necessary."""
    audio_dir = ensure_project_audio_directory(project_dir)
    playlist_path = audio_dir / f"{project_filename_base(project_name)}.playlist"
    if not playlist_path.exists():
        write_text_atomic(playlist_path, "")
    return playlist_path.resolve(strict=False)


def bundled_resource_path(filename: str) -> Path:
    """Return a source-tree or PyInstaller-bundled resource path."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / filename
    return Path(__file__).resolve().parent / filename


def default_gpx_summary_text() -> str:
    """Return the default GPX helper text shown when no usable single GPX exists."""
    return "Missing or multiple files will open GPXEditor to produce a single file."


def is_missing_gps_text(value: str) -> bool:
    """Return True when a GPS cell represents missing coordinates."""
    return str(value).strip() == GPS_NOT_AVAILABLE


def is_missing_place_text(value: str) -> bool:
    """Return True when a place cell represents unavailable place data."""
    return str(value).strip() in {"", PLACE_NOT_AVAILABLE, PLACE_NOT_REQUESTED, PLACE_FAILED}


def slideshow_row_type_for_filename(filename: str) -> str:
    """Classify a media filename for the slideshow table."""
    suffix = Path(str(filename).strip()).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "IMG"
    if suffix in VIDEO_EXTENSIONS:
        return "VID"
    return "IMG"


def parse_slideshow_control_line(line: str) -> dict:
    """Parse one sorted slideshow control-file line into a table row."""
    text = str(line).rstrip("\r\n")
    if text.startswith("#"):
        body = text[1:]
        keyword, separator, value = body.partition(":")
        keyword = keyword.strip()
        value = value.strip() if separator else body.strip()
        normalized = keyword.lower()
        if normalized == "overviewmap":
            row_type = "MAP"
        elif normalized == "map":
            row_type = "TRK"
        elif normalized == "mapbefore":
            row_type = "BEF"
        elif normalized == "mapafter":
            row_type = "AFT"
        elif normalized == "mediamap":
            row_type = "LOC"
        elif normalized in {"date", "datum"}:
            row_type = "DAT"
        elif normalized == "music":
            row_type = "MUS"
        else:
            row_type = keyword.upper() if keyword else "#"
        return {
            "type": row_type,
            "name": value,
            "time": "",
            "gps": "",
            "place": "",
            "keyword": keyword,
            "is_keyword": True,
        }

    parts = [part.strip() for part in text.split("|")]
    filename = parts[0] if parts else ""
    time_text = parts[1] if len(parts) > 1 else ""
    gps_text = parts[2] if len(parts) > 2 else ""
    place_text = parts[3] if len(parts) > 3 else ""
    return {
        "type": slideshow_row_type_for_filename(filename),
        "name": filename,
        "time": time_text,
        "gps": "" if is_missing_gps_text(gps_text) else gps_text,
        "place": "" if is_missing_place_text(place_text) else place_text,
        "keyword": "",
        "is_keyword": False,
    }


def serialize_slideshow_control_row(row: dict) -> str:
    """Serialize one editable slideshow row back to the sorted-list format."""
    row_type = str(row.get("type", "")).strip().upper()
    name = str(row.get("name", "")).strip()
    if row.get("is_keyword") or row_type in {"MAP", "TRK", "BEF", "AFT", "LOC", "DAT"}:
        keyword = str(row.get("keyword", "")).strip()
        if not keyword:
            keyword = CONTROL_ROW_TYPE_KEYWORDS.get(row_type, row_type)
        return f"#{keyword}: {name}"

    time_text = str(row.get("time", "")).strip()
    gps_text = str(row.get("gps", "")).strip() or GPS_NOT_AVAILABLE
    place_text = str(row.get("place", "")).strip() or PLACE_NOT_REQUESTED
    return f"{name} | {time_text} | {gps_text} | {place_text}"


def clone_slideshow_row(row: dict) -> dict:
    """Return a mutable copy of a slideshow table row."""
    return dict(row)


def control_table_search_indexes(rows, query):
    """Return rows whose serialized control-file text contains ``query``."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return []
    return [
        index
        for index, row in enumerate(rows)
        if needle in serialize_slideshow_control_row(row).casefold()
    ]


def next_control_table_search_position(current, count, direction):
    """Return the wrapped next/previous result position."""
    if count <= 0:
        return -1
    if current < 0:
        return 0 if direction >= 0 else count - 1
    return (current + direction) % count


def visible_control_row_indexes(rows, hide_media=False):
    """Return model indexes exposed by the control-table data source."""
    if not hide_media:
        return list(range(len(rows)))
    return [
        index
        for index, row in enumerate(rows)
        if str(row.get("type", "")).upper() not in {"IMG", "VID"}
    ]


def control_table_filter_anchor_index(rows, selected_indexes, hide_media=False):
    """Return the selected or nearest row that remains visible after filtering."""
    visible = visible_control_row_indexes(rows, hide_media)
    if not visible:
        return None
    selected = [int(index) for index in selected_indexes if 0 <= int(index) < len(rows)]
    for index in selected:
        if index in visible:
            return index
    if not selected:
        return visible[0]
    pivot = selected[0]
    return min(visible, key=lambda index: (abs(index - pivot), 0 if index <= pivot else 1))


def media_viewer_control_row_index(source, items, index):
    """Return the originating control-table row for the visible viewer item."""
    if source != "control" or not items or index < 0 or index >= len(items):
        return None
    try:
        return int(items[index]["index"])
    except (KeyError, TypeError, ValueError):
        return None


def normalize_control_row_type(value: str) -> str:
    """Return the canonical short type accepted from a combo-box entry."""
    text = str(value or "").strip()
    candidate = text.split(" - ", 1)[0].strip().upper()
    if candidate in CONTROL_ROW_TYPE_DESCRIPTIONS:
        return candidate
    folded = text.casefold()
    for row_type, description in CONTROL_ROW_TYPE_DESCRIPTIONS.items():
        if folded == description.casefold():
            return row_type
    return text.upper()


def display_control_row_type(value: str) -> str:
    """Return a readable combo-box label for a canonical control-row type."""
    row_type = normalize_control_row_type(value)
    description = CONTROL_ROW_TYPE_DESCRIPTIONS.get(row_type)
    return f"{row_type} - {description}" if description else str(value or "")


def update_slideshow_control_row_cell(row: dict, column_id: str, value: str) -> None:
    """Apply one table edit while keeping type and directive keyword consistent."""
    text = str(value or "")
    if column_id != "type":
        row[column_id] = text
        return
    row_type = normalize_control_row_type(text)
    row["type"] = row_type
    if row_type in CONTROL_ROW_TYPE_KEYWORDS:
        row["keyword"] = CONTROL_ROW_TYPE_KEYWORDS[row_type]
        row["is_keyword"] = True
    elif row_type in {"IMG", "VID"}:
        row["keyword"] = ""
        row["is_keyword"] = False
    elif row.get("is_keyword"):
        row["keyword"] = row_type


def disable_field_editor_text_checking(editor) -> None:
    """Disable macOS writing aids that can deadlock programmatic text updates."""
    if editor is None:
        return
    for selector in (
        "setContinuousSpellCheckingEnabled_",
        "setGrammarCheckingEnabled_",
        "setAutomaticSpellingCorrectionEnabled_",
        "setAutomaticTextReplacementEnabled_",
        "setAutomaticQuoteSubstitutionEnabled_",
        "setAutomaticDashSubstitutionEnabled_",
    ):
        method = getattr(editor, selector, None)
        if method is not None:
            try:
                method(False)
            except Exception:
                pass


def is_media_row_type(row_type: str) -> bool:
    return str(row_type).upper() in {"IMG", "VID", "MAP", "TRK", "BEF", "AFT", "LOC"}


class GPSTrackShowGUITableDataSource(NSObject):
    """NSTableView datasource/delegate wrapper."""

    def init(self):
        self = objc.super(GPSTrackShowGUITableDataSource, self).init()
        if self is None:
            return None
        self.rows = []
        self.columns = []
        self.controller = None
        return self

    def setRows_columns_(self, rows, columns):
        self.rows = list(rows)
        self.columns = list(columns)

    def setController_(self, controller):
        self.controller = controller

    def numberOfRowsInTableView_(self, _table_view):
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(self, _table_view, table_column, row_index):
        if row_index < 0 or row_index >= len(self.rows):
            return ""
        identifier = str(table_column.identifier())
        return self.rows[row_index].get(identifier, "")

    def tableViewSelectionDidChange_(self, notification):
        if self.controller is not None:
            self.controller.plotSelectionTableSelectionDidChange_(notification)


class GPSTrackShowGUIMediaBrowserDataSource(GPSTrackShowGUITableDataSource):
    """Sortable datasource/delegate for the merge-media browser."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIMediaBrowserDataSource, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def tableView_sortDescriptorsDidChange_(self, table_view, _old_descriptors):
        descriptors = list(table_view.sortDescriptors() or [])
        if not descriptors:
            return
        descriptor = descriptors[0]
        self.controller.sortMediaBrowserByColumn_ascending_(
            str(descriptor.key()),
            bool(descriptor.ascending()),
        )


class GPSTrackShowGUIMediaUpdatePreviewDataSource(GPSTrackShowGUITableDataSource):
    """Datasource for the selective media-update preview."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIMediaUpdatePreviewDataSource, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def tableView_setObjectValue_forTableColumn_row_(self, _table, value, column, row_index):
        identifier = str(column.identifier())
        if identifier not in {"apply_update", "reposition"}:
            return
        plan = self.controller.media_update_plan
        if plan is None or row_index < 0 or row_index >= len(plan.items):
            return
        if identifier == "apply_update":
            plan.items[row_index].apply_update = bool(value)
        else:
            plan.items[row_index].reposition = bool(value)
        self.rows[row_index][identifier] = bool(value)


class SlideShowControlTableDataSource(NSObject):
    """Editable datasource for the slide-show control-file table."""

    def initWithController_(self, controller):
        self = objc.super(SlideShowControlTableDataSource, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def numberOfRowsInTableView_(self, _table_view):
        return len(self.controller.control_table_view_model_indexes)

    def tableView_objectValueForTableColumn_row_(self, _table_view, table_column, row_index):
        model_index = self.controller.control_table_model_index_for_view_row(row_index)
        if model_index is None:
            return ""
        identifier = str(table_column.identifier())
        if identifier == "preview":
            return self.controller.preview_image_for_control_row(model_index)
        if identifier == "file_datetime":
            return self.controller.file_datetime_for_control_row(model_index)
        if identifier == "type":
            return normalize_control_row_type(
                self.controller.control_table_rows[model_index].get(identifier, "")
            )
        return self.controller.control_table_rows[model_index].get(identifier, "")

    def tableView_setObjectValue_forTableColumn_row_(self, _table_view, value, table_column, row_index):
        model_index = self.controller.control_table_model_index_for_view_row(row_index)
        if model_index is None:
            return
        identifier = str(table_column.identifier())
        if identifier in {"preview", "file_datetime"} or identifier not in CONTROL_TABLE_COLUMNS:
            return
        self.controller.update_control_table_cell(model_index, identifier, str(value or ""))

    def tableView_shouldEditTableColumn_row_(self, table_view, _table_column, _row_index):
        if str(_table_column.identifier()) in {"preview", "file_datetime"}:
            return False
        window = table_view.window()
        if window is not None:
            disable_field_editor_text_checking(
                window.fieldEditor_forObject_(True, table_view)
            )
        return True

    def tableViewSelectionDidChange_(self, notification):
        table_view = notification.object()
        for model_index in self.controller._selected_control_table_indexes():
            if str(self.controller.control_table_rows[model_index].get("type", "")).upper() == "MUS":
                self.controller.showMusicDirectiveHelp_(table_view)
                break

    def tableView_willDisplayCell_forTableColumn_row_(self, _table_view, cell, _table_column, row_index):
        model_index = self.controller.control_table_model_index_for_view_row(row_index)
        if model_index is None:
            return
        row_type = str(self.controller.control_table_rows[model_index].get("type", "")).upper()
        base_font = NSFont.systemFontOfSize_(13.0)
        if row_type == "DAT":
            font = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(base_font, NSBoldFontMask)
        elif row_type in {"MAP", "TRK", "BEF", "AFT", "LOC", "MUS"}:
            font = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(base_font, NSItalicFontMask)
        else:
            font = base_font
        cell.setFont_(font)

    def tableView_writeRowsWithIndexes_toPasteboard_(self, _table_view, row_indexes, pasteboard):
        indexes = []
        for view_index in range(len(self.controller.control_table_view_model_indexes)):
            if row_indexes.containsIndex_(view_index):
                model_index = self.controller.control_table_model_index_for_view_row(view_index)
                if model_index is not None:
                    indexes.append(model_index)
        if not indexes:
            return False
        self.controller.control_table_drag_indexes = indexes
        pasteboard.declareTypes_owner_([CONTROL_TABLE_DRAG_TYPE], None)
        pasteboard.setString_forType_(",".join(str(index) for index in indexes), CONTROL_TABLE_DRAG_TYPE)
        return True

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, table_view, _info, row, _operation):
        table_view.setDropRow_dropOperation_(row, NSTableViewDropAbove)
        return NSDragOperationMove

    def tableView_acceptDrop_row_dropOperation_(self, _table_view, _info, row, _operation):
        model_row = self.controller.control_table_model_insertion_index_for_view_row(row)
        return self.controller.move_control_rows_by_drag(model_row)


class SlideShowControlTableView(NSTableView):
    """Table view with standard editing keyboard shortcuts."""

    def initWithController_(self, controller):
        self = objc.super(SlideShowControlTableView, self).initWithFrame_(NSMakeRect(0, 0, 100, 100))
        if self is None:
            return None
        self.controller = controller
        return self

    def drawRect_(self, dirty_rect):
        objc.super(SlideShowControlTableView, self).drawRect_(dirty_rect)
        type_column = self.columnWithIdentifier_(nsstring("type"))
        if type_column < 0:
            return
        column_rect = self.rectOfColumn_(type_column)
        x = column_rect.origin.x + column_rect.size.width
        bounds = self.bounds()
        NSColor.separatorColor().setStroke()
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(2.0)
        path.moveToPoint_(NSMakePoint(x, bounds.origin.y))
        path.lineToPoint_(NSMakePoint(x, bounds.origin.y + bounds.size.height))
        path.stroke()

    def menuForEvent_(self, event):
        """Select the right-clicked row and return its editing context menu."""
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        row_index = int(self.rowAtPoint_(point))
        if row_index < 0:
            return None
        selected = self.selectedRowIndexes()
        if not selected.containsIndex_(row_index):
            self.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(row_index),
                False,
            )

        menu = NSMenu.alloc().initWithTitle_("Control File Row")

        def add_item(title, action, enabled=True):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self.controller)
            item.setEnabled_(bool(enabled))
            menu.addItem_(item)

        add_item("Delete", "deleteControlRows:")
        add_item("Cut", "cutControlRows:")
        add_item("Copy", "copyControlRowsToPasteboard:")
        add_item("Paste", "pasteControlRows:")
        add_item("Insert Row", "insertControlRow:")
        menu.addItem_(NSMenuItem.separatorItem())
        has_viewable_file = self.controller.selectedControlRowsHaveViewableFiles()
        add_item("Preview", "openSelectedControlMedia:", has_viewable_file)
        add_item("Open in Finder", "openSelectedControlRowsInFinder:", has_viewable_file)
        menu.addItem_(NSMenuItem.separatorItem())
        add_item(
            "Start Slide Show Here",
            "startSlideShowFromSelectedControlRow:",
            self.controller.selectedControlRowsCanStartSlideShow(),
        )
        return menu

    def keyDown_(self, event):
        if self.currentEditor() is not None:
            objc.super(SlideShowControlTableView, self).keyDown_(event)
            return
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        key_code = int(event.keyCode())
        modifiers = int(event.modifierFlags())
        shortcut_down = bool(
            modifiers & (int(NSEventModifierFlagCommand) | int(NSEventModifierFlagControl))
        )
        shift_down = bool(modifiers & int(NSEventModifierFlagShift))
        if shortcut_down:
            lower_key = key.lower()
            if lower_key == "c":
                self.controller.copyControlRowsToPasteboard_(self)
                return
            if lower_key == "x":
                self.controller.cutControlRows_(self)
                return
            if lower_key == "v":
                self.controller.pasteControlRows_(self)
                return
            if lower_key == "i":
                self.controller.insertControlRow_(self)
                return
            if lower_key == "z" and shift_down:
                self.controller.redoControlTable_(self)
                return
            if lower_key == "z":
                self.controller.undoControlTable_(self)
                return
        if key_code in {51, 117}:
            self.controller.deleteControlRows_(self)
            return
        objc.super(SlideShowControlTableView, self).keyDown_(event)

    def _send_to_field_editor(self, selector, sender):
        editor = self.currentEditor()
        if editor is None:
            return False
        if editor.respondsToSelector_(selector):
            editor.performSelector_withObject_(selector, sender)
            return True
        return False

    def copy_(self, sender):
        if self._send_to_field_editor("copy:", sender):
            return
        self.controller.copyControlRowsToPasteboard_(sender)

    def cut_(self, sender):
        if self._send_to_field_editor("cut:", sender):
            return
        self.controller.cutControlRows_(sender)

    def paste_(self, sender):
        if self._send_to_field_editor("paste:", sender):
            return
        self.controller.pasteControlRows_(sender)

    def delete_(self, sender):
        if self._send_to_field_editor("delete:", sender):
            return
        self.controller.deleteControlRows_(sender)

    def deleteBackward_(self, sender):
        if self._send_to_field_editor("deleteBackward:", sender):
            return
        self.controller.deleteControlRows_(sender)

    def undo_(self, sender):
        if self._send_to_field_editor("undo:", sender):
            return
        self.controller.undoControlTable_(sender)

    def redo_(self, sender):
        if self._send_to_field_editor("redo:", sender):
            return
        self.controller.redoControlTable_(sender)


class SlideShowControlTableWindow(NSWindow):
    """Control-table window with window-wide search shortcuts."""

    def sendEvent_(self, event):
        """Give cell text editing priority over whole-row clipboard actions."""
        if int(event.type()) == int(NSEventTypeKeyDown):
            modifiers = int(event.modifierFlags())
            shortcut_down = bool(
                modifiers & (int(NSEventModifierFlagCommand) | int(NSEventModifierFlagControl))
            )
            key = str(event.charactersIgnoringModifiers() or "").lower()
            table = getattr(self.controller, "control_table_view", None)
            editor = table.currentEditor() if table is not None else None
            selector = {"c": "copy_", "x": "cut_", "v": "paste_"}.get(key)
            if shortcut_down and editor is not None and selector is not None:
                method = getattr(editor, selector, None)
                if method is not None:
                    method(None)
                    return
        objc.super(SlideShowControlTableWindow, self).sendEvent_(event)

    def performKeyEquivalent_(self, event):
        key = str(event.charactersIgnoringModifiers() or "").lower()
        modifiers = int(event.modifierFlags())
        if modifiers & int(NSEventModifierFlagCommand) and key == "f":
            if modifiers & int(NSEventModifierFlagShift):
                self.controller.findPreviousControlTableMatch_(self)
            else:
                self.controller.findNextControlTableMatch_(self)
            return True
        if modifiers & int(NSEventModifierFlagCommand) and key == "i":
            self.controller.insertControlRow_(self)
            return True
        return objc.super(SlideShowControlTableWindow, self).performKeyEquivalent_(event)


class GPSTrackShowGUIWindowDelegate(NSObject):
    """Window lifecycle hooks."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowDidResize_(self, _notification):
        self.controller.layout_window()

    def windowShouldClose_(self, _sender):
        if not self.controller.confirm_close():
            return False
        NSApp().terminate_(None)
        return True


class GPSTrackShowGUIParameterWindowDelegate(NSObject):
    """Keep the parameter editor modal-like without blocking the main GUI."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIParameterWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowDidResize_(self, _notification):
        self.controller.layoutParameterWindow()

    def windowShouldClose_(self, _sender):
        self.controller.cancelParameterEditor_(None)
        return False


class GPSTrackShowGUIAlbumSelectionDelegate(NSObject):
    """Album selection modal delegate."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIAlbumSelectionDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowShouldClose_(self, _sender):
        self.controller.albumSelectionCancel_(None)
        return False


class GPSTrackShowGUIPlotViewerWindowDelegate(NSObject):
    """Plot viewer lifecycle hooks."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIPlotViewerWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowShouldClose_(self, _sender):
        self.controller.hide_plot_viewer()
        return False

    def windowWillClose_(self, notification):
        self.controller.plotViewerWindowWillClose_(notification.object())


class GPSTrackShowGUIGeoLocationsWindowDelegate(NSObject):
    """Output window lifecycle hooks."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIGeoLocationsWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowShouldClose_(self, _sender):
        self.controller.closeGeoLocationsWindow_(None)
        return False


class GPSTrackShowGUIControlTableWindowDelegate(NSObject):
    """Hide the reusable control-table window instead of releasing it."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIControlTableWindowDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowShouldClose_(self, _sender):
        self.controller.closeControlTable_(None)
        return False


class GPSTrackShowGUIPlotViewerView(NSView):
    """Key-aware plot viewer content view."""

    def initWithController_(self, controller):
        self = objc.super(GPSTrackShowGUIPlotViewerView, self).initWithFrame_(NSMakeRect(0, 0, 1200, 800))
        if self is None:
            return None
        self.controller = controller
        return self

    def acceptsFirstResponder(self):
        return True

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        key_code = int(event.keyCode())
        if key in {"q", "Q"}:
            self.controller.performSelector_withObject_afterDelay_("requestHidePlotViewer:", None, 0.0)
            return
        if key in {"h", "H"}:
            self.controller.toggle_plot_help()
            return
        if key_code in {123, 126}:
            self.controller.show_previous_plot()
            return
        if key_code in {124, 125}:
            self.controller.show_next_plot()
            return
        objc.super(GPSTrackShowGUIPlotViewerView, self).keyDown_(event)


class SlideShowMediaViewerDelegate(NSObject):
    """Lifecycle hooks for the slide-show media preview window."""

    def initWithController_(self, controller):
        self = objc.super(SlideShowMediaViewerDelegate, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def windowWillClose_(self, _notification):
        self.controller.stop_media_viewer_video()
        self.controller.media_viewer_window = None
        self.controller.media_viewer_view = None
        self.controller.media_viewer_delegate = None


class SlideShowMediaViewerView(NSView):
    """Key-aware image/video preview view for slide-show control rows."""

    def initWithController_(self, controller):
        self = objc.super(SlideShowMediaViewerView, self).initWithFrame_(NSMakeRect(0, 0, 960, 640))
        if self is None:
            return None
        self.controller = controller
        self.show_info = True
        self.show_help = False
        self.hint_until = 0.0
        return self

    def acceptsFirstResponder(self):
        return True

    def isFlipped(self):
        return False

    def keyDown_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        key_code = int(event.keyCode())
        if key in {"q", "Q"}:
            self.controller.close_media_viewer()
            return
        if key in {"h", "H"}:
            self.show_help = not self.show_help
            self.setNeedsDisplay_(True)
            return
        if key in {"i", "I"}:
            self.show_info = not self.show_info
            self.setNeedsDisplay_(True)
            return
        if key in {"s", "S"}:
            self.controller.toggle_media_viewer_sort()
            return
        if key_code in {36, 49}:
            item = self.controller.current_media_viewer_item()
            if item is not None and item.get("kind") == "video":
                self.controller.toggle_media_viewer_video_playback()
            return
        if key_code in {123, 126}:
            self.controller.show_previous_control_media()
            return
        if key_code in {124, 125}:
            self.controller.show_next_control_media()
            return
        objc.super(SlideShowMediaViewerView, self).keyDown_(event)

    def drawRect_(self, dirty_rect):
        NSColor.blackColor().setFill()
        NSBezierPath.fillRect_(dirty_rect)
        item = self.controller.current_media_viewer_item()
        bounds = self.bounds()
        if item is None:
            self._draw_panel(["No media selected."], bounds)
            return

        if item.get("kind") == "image" and item.get("image") is not None:
            image = item["image"]
            image.drawInRect_fromRect_operation_fraction_(
                self._image_rect(image, bounds),
                NSMakeRect(0, 0, image.size().width, image.size().height),
                2,
                1.0,
            )
        else:
            self._draw_panel(
                [
                    "Video clip",
                    str(item.get("path", "")),
                    "Press Return or double-click the file in Finder to play with the default viewer.",
                ],
                bounds,
                width=680.0,
            )

        if self.show_info:
            self._draw_info_overlay(item, bounds)
        if not self.show_help and time.time() < self.hint_until:
            self._draw_panel(
                [
                    "Press h for help.",
                    "Use cursor keys to move photos backward and forward.",
                ],
                bounds,
                width=430.0,
            )
        if self.show_help:
            self._draw_panel(
                [
                    "Keys",
                    "Left/Up: previous media",
                    "Right/Down: next media",
                    "Space/Return: play or pause video",
                    "i: show/hide info overlay",
                    "s: sort by filename/date",
                    "h: show/hide this help",
                    "q: close window",
                ],
                bounds,
                width=360.0,
            )

    def mouseDown_(self, event):
        if int(event.clickCount()) >= 2:
            item = self.controller.current_media_viewer_item()
            if item is not None and item.get("kind") == "video":
                self.controller.toggle_media_viewer_video_playback()

    def _image_rect(self, image, bounds):
        image_size = image.size()
        image_width = max(float(image_size.width), 1.0)
        image_height = max(float(image_size.height), 1.0)
        scale = min(bounds.size.width / image_width, bounds.size.height / image_height)
        width = image_width * scale
        height = image_height * scale
        return NSMakeRect(
            bounds.origin.x + (bounds.size.width - width) / 2.0,
            bounds.origin.y + (bounds.size.height - height) / 2.0,
            width,
            height,
        )

    def _draw_info_overlay(self, item, bounds):
        lines = [str(item.get("name", ""))]
        if item.get("time"):
            lines.append(f"Date/time: {item.get('time')}")
        if item.get("gps"):
            lines.append(f"GPS: {item.get('gps')}")
        if item.get("place"):
            lines.append(f"Place: {item.get('place')}")
        self._draw_panel(lines, bounds, width=520.0, bottom=True)

    def _draw_panel(self, lines, bounds, width=420.0, bottom=False):
        panel_height = 22.0 + 18.0 * len(lines)
        x = bounds.origin.x + 18.0
        y = bounds.origin.y + 18.0 if bottom else bounds.origin.y + bounds.size.height - panel_height - 18.0
        rect = NSMakeRect(x, y, min(width, bounds.size.width - 36.0), panel_height)
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.68).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 10.0, 10.0).fill()
        text = "\n".join(lines)
        attrs = {
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSFontAttributeName: NSFont.systemFontOfSize_(13.0),
        }
        NSString.stringWithString_(text).drawInRect_withAttributes_(NSInsetRect(rect, 12.0, 8.0), attrs)


class GeoLocationsOutputWriter:
    """Thread-safe line writer that forwards output into the Cocoa UI."""

    def __init__(self, controller):
        self.controller = controller
        self.buffer = ""

    def write(self, text):
        if not text:
            return 0
        self.buffer += str(text)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                "appendGeoLocationsOutputLine:",
                line,
                False,
            )
        return len(text)

    def flush(self):
        if self.buffer:
            self.controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                "appendGeoLocationsOutputLine:",
                self.buffer,
                False,
            )
            self.buffer = ""


class WorkflowAssistantBubbleView(NSView):
    """Small retained overlay that points at the next workflow control."""

    def initWithController_(self, controller):
        self = objc.super(WorkflowAssistantBubbleView, self).initWithFrame_(NSMakeRect(0, 0, 350, 112))
        if self is None:
            return None
        self.controller = controller
        self.pointer_side = "bottom"
        self.pointer_offset = 50.0
        self.target_rect = (0.0, 0.0, 1.0, 1.0)
        self.user_positioned = False
        self.user_origin = None
        self.position_stage = None
        self.drag_start_point = None
        self.drag_start_origin = None
        self.setHidden_(True)
        self.setToolTip_("Drag the assistant bubble to move it.")

        self.message_label = NSTextField.alloc().initWithFrame_(NSMakeRect(18, 18, 300, 76))
        self.message_label.setEditable_(False)
        self.message_label.setSelectable_(False)
        self.message_label.setBordered_(False)
        self.message_label.setDrawsBackground_(False)
        self.message_label.setFont_(NSFont.systemFontOfSize_(13.0))
        self.message_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.12, 1.0))
        self.message_label.setLineBreakMode_(0)
        self.message_label.setUsesSingleLineMode_(False)
        self.addSubview_(self.message_label)

        self.close_button = NSButton.alloc().initWithFrame_(NSMakeRect(318, 82, 22, 22))
        self.close_button.setTitle_("x")
        self.close_button.setBordered_(False)
        self.close_button.setFont_(NSFont.boldSystemFontOfSize_(13.0))
        self.close_button.setTarget_(controller)
        self.close_button.setAction_("disableWorkflowAssistant:")
        self.close_button.setToolTip_("Turn off the workflow assistant for this Adventure.")
        self.addSubview_(self.close_button)

        self.choice_buttons = []
        self.choice_ids = []
        self.selected_choice_index = -1
        self.action_buttons = []
        self.action_ids = []
        for index in range(3):
            button = NSButton.alloc().initWithFrame_(NSMakeRect(16 + index * 104, 14, 98, 26))
            button.setTarget_(controller)
            button.setAction_("workflowAssistantAction:")
            button.setTag_(index)
            button.setHidden_(True)
            self.addSubview_(button)
            self.action_buttons.append(button)
        return self

    def setMessage_geometry_(self, message, geometry):
        self.setContent_geometry_({"message": message}, geometry)

    def setTargetRect_(self, target_rect):
        self.target_rect = tuple(float(value) for value in target_rect)

    def setAssistantStage_(self, stage):
        stage = str(stage or "")
        if stage != self.position_stage:
            self.user_positioned = False
            self.user_origin = None
            self.position_stage = stage

    def hasUserPosition(self):
        return bool(self.user_positioned and self.user_origin is not None)

    def setContent_geometry_(self, content, geometry):
        self.pointer_side = geometry.pointer_side
        self.pointer_offset = geometry.pointer_offset
        self.setFrame_(NSMakeRect(*geometry.frame))
        if self.user_positioned:
            self.user_origin = (geometry.frame[0], geometry.frame[1])
        width = geometry.frame[2]
        height = geometry.frame[3]
        pointer_inset = 12.0
        left = pointer_inset if self.pointer_side == "left" else 0.0
        bottom = pointer_inset if self.pointer_side == "bottom" else 0.0
        right = pointer_inset if self.pointer_side == "right" else 0.0
        top = pointer_inset if self.pointer_side == "top" else 0.0
        choices = list(content.get("choices") or [])
        actions = list(content.get("actions") or [])[:3]
        controls_height = (24.0 * len(choices) + 5.0 if choices else 0.0) + (
            34.0 if actions else 0.0
        )
        self.message_label.setFrame_(
            NSMakeRect(
                left + 16.0,
                bottom + 13.0 + controls_height,
                width - left - right - 46.0,
                max(40.0, height - bottom - top - 25.0 - controls_height),
            )
        )
        self.message_label.setStringValue_(str(content.get("message", "")))
        self.close_button.setFrame_(NSMakeRect(width - right - 28.0, height - top - 28.0, 20.0, 20.0))

        previous_choice = self.selectedActionId()
        while len(self.choice_buttons) < len(choices):
            button = NSButton.alloc().initWithFrame_(NSMakeRect(16.0, 16.0, 300.0, 22.0))
            button.setButtonType_(NSButtonTypeRadio)
            button.setFont_(NSFont.systemFontOfSize_(12.0))
            button.setTarget_(self)
            button.setAction_("selectWorkflowAssistantChoice:")
            button.setHidden_(True)
            self.addSubview_(button)
            self.choice_buttons.append(button)
        self.choice_ids = [str(item[0]) for item in choices]
        selected_choice = str(content.get("selected_choice") or previous_choice)
        self.selected_choice_index = (
            self.choice_ids.index(selected_choice)
            if selected_choice in self.choice_ids
            else (0 if choices else -1)
        )
        choice_base_y = bottom + (48.0 if actions else 15.0)
        for index, button in enumerate(self.choice_buttons):
            visible = index < len(choices)
            button.setHidden_(not visible)
            if not visible:
                continue
            button.setTag_(index)
            button.setTitle_(str(choices[index][1]))
            button.setState_(
                NSControlStateValueOn if index == self.selected_choice_index else NSControlStateValueOff
            )
            button.setFrame_(
                NSMakeRect(
                    left + 16.0,
                    choice_base_y + (len(choices) - index - 1) * 24.0,
                    width - left - right - 32.0,
                    22.0,
                )
            )

        self.action_ids = [str(item[0]) for item in actions]
        action_width = max(88.0, min(148.0, (width - left - right - 32.0) / max(len(actions), 1)))
        for index, button in enumerate(self.action_buttons):
            visible = index < len(actions)
            button.setHidden_(not visible)
            if not visible:
                continue
            button.setTitle_(str(actions[index][1]))
            button.setFrame_(NSMakeRect(left + 16.0 + index * action_width, bottom + 14.0, action_width - 6.0, 27.0))
            button.setKeyEquivalent_("\r" if index == 0 else "")
        self.setNeedsDisplay_(True)

    def hitTest_(self, point):
        hit = objc.super(WorkflowAssistantBubbleView, self).hitTest_(point)
        if hit is self.message_label or hit == self.message_label:
            return self
        return hit

    def mouseDown_(self, event):
        point = event.locationInWindow()
        frame = self.frame()
        self.drag_start_point = (point.x, point.y)
        self.drag_start_origin = (frame.origin.x, frame.origin.y)

    def mouseDragged_(self, event):
        if self.drag_start_point is None or self.superview() is None:
            return
        point = event.locationInWindow()
        origin = (
            self.drag_start_origin[0] + point.x - self.drag_start_point[0],
            self.drag_start_origin[1] + point.y - self.drag_start_point[1],
        )
        bounds = self.superview().bounds()
        frame = self.frame()
        geometry = relocated_bubble_geometry(
            (bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height),
            self.target_rect,
            (frame.size.width, frame.size.height),
            origin,
        )
        self.user_positioned = True
        self.user_origin = (geometry.frame[0], geometry.frame[1])
        self.pointer_side = geometry.pointer_side
        self.pointer_offset = geometry.pointer_offset
        self.setFrame_(NSMakeRect(*geometry.frame))
        self.setNeedsDisplay_(True)

    def mouseUp_(self, _event):
        self.drag_start_point = None
        self.drag_start_origin = None

    def selectedActionId(self):
        if not self.choice_ids:
            return ""
        index = int(self.selected_choice_index)
        return self.choice_ids[index] if 0 <= index < len(self.choice_ids) else ""

    def selectWorkflowAssistantChoice_(self, sender):
        selected = int(sender.tag())
        if selected < 0 or selected >= len(self.choice_ids):
            return
        self.selected_choice_index = selected
        for index, button in enumerate(self.choice_buttons):
            button.setState_(
                NSControlStateValueOn if index == selected else NSControlStateValueOff
            )

    def drawRect_(self, _dirty_rect):
        bounds = self.bounds()
        inset = 12.0
        body = NSMakeRect(bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height)
        if self.pointer_side == "bottom":
            body.origin.y += inset
            body.size.height -= inset
        elif self.pointer_side == "top":
            body.size.height -= inset
        elif self.pointer_side == "left":
            body.origin.x += inset
            body.size.width -= inset
        else:
            body.size.width -= inset

        context = NSGraphicsContext.currentContext()
        context.saveGraphicsState()
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.28))
        shadow.setShadowBlurRadius_(7.0)
        shadow.setShadowOffset_(NSMakeSize(0.0, -2.0))
        shadow.set()
        fill = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.98, 0.95, 0.78, 0.98)
        fill.setFill()
        NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0).setStroke()
        body_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, 12.0, 12.0)
        body_path.setLineWidth_(1.0)
        body_path.fill()
        body_path.stroke()

        triangle = NSBezierPath.bezierPath()
        offset = self.pointer_offset
        if self.pointer_side == "bottom":
            triangle.moveToPoint_(NSMakePoint(offset - 10.0, inset + 1.0))
            triangle.lineToPoint_(NSMakePoint(offset, 0.0))
            triangle.lineToPoint_(NSMakePoint(offset + 10.0, inset + 1.0))
        elif self.pointer_side == "top":
            top = bounds.size.height - inset - 1.0
            triangle.moveToPoint_(NSMakePoint(offset - 10.0, top))
            triangle.lineToPoint_(NSMakePoint(offset, bounds.size.height))
            triangle.lineToPoint_(NSMakePoint(offset + 10.0, top))
        elif self.pointer_side == "left":
            triangle.moveToPoint_(NSMakePoint(inset + 1.0, offset - 10.0))
            triangle.lineToPoint_(NSMakePoint(0.0, offset))
            triangle.lineToPoint_(NSMakePoint(inset + 1.0, offset + 10.0))
        else:
            right = bounds.size.width - inset - 1.0
            triangle.moveToPoint_(NSMakePoint(right, offset - 10.0))
            triangle.lineToPoint_(NSMakePoint(bounds.size.width, offset))
            triangle.lineToPoint_(NSMakePoint(right, offset + 10.0))
        triangle.closePath()
        triangle.fill()
        context.restoreGraphicsState()


class GPXTrackerController(NSObject):
    """Main Cocoa controller."""

    def initWithProjectDirectory_projectFile_(self, project_directory, project_file):
        self = objc.super(GPXTrackerController, self).init()
        if self is None:
            return None
        self.base_dir = Path(__file__).resolve().parent
        self.current_project_dir = None
        self.startup_project_directory = None
        self.startup_project_file = None
        if project_directory:
            self.startup_project_directory = Path(str(project_directory)).expanduser()
        if project_file:
            self.startup_project_file = Path(str(project_file)).expanduser()
        self.rows = []
        self.columns = []
        self.current_project_file = None
        self.current_control_file = None
        self.track_map_base = ""
        self.adventure_records = []
        self.adventure_combo_paths = []
        self.project_file_menu_refreshing = False
        self.committed_adventure_name = ""
        self.adventure_name_commit_in_progress = False
        self.shared_asset_warning_accepted = set()
        self.recent_adventures = self._load_recent_adventures()
        self.last_picture_import_directory = None
        self.title_image = None
        self.media_import_panel = None
        self.media_import_completion_handler = None
        self.media_import_prepare_existing_on_cancel = False
        self.music_source = None
        self.music_playlist = None
        self.parameters = default_parameters()
        self.time_lapse_media_min_fraction = self.parameters["timelapse.media_min_fraction"]
        self.track_map_edge_margin_fraction = self.parameters["trackmaps.edge_margin_fraction"]
        self.slideshow_resume_position = None
        self.project_dirty = False
        self.skip_next_project_dir_dirty = False
        self.adventure_autosave_timer = None
        self.adventure_autosave_suspended = 0
        self.adventure_autosave_error = None
        self.directory_activation_in_progress = False
        self.gpx_field_manually_changed = False
        self.progress_total = 0
        self.skipped_media_windows = []
        self.plot_viewer_window = None
        self.plot_viewer_view = None
        self.plot_viewer_image_view = None
        self.plot_viewer_caption = None
        self.plot_viewer_help_view = None
        self.plot_viewer_paths = []
        self.plot_viewer_index = 0
        self.plot_viewer_delegate = None
        self.plot_viewer_closing = False
        self.album_selection_window = None
        self.album_selection_table = None
        self.album_selection_rows = []
        self.album_selection_result = None
        self.slideshow_process = None
        self.album_selection_data_source = None
        self.album_selection_delegate = None
        self.geolocations_window = None
        self.geolocations_text_view = None
        self.geolocations_cancel_button = None
        self.geolocations_skip_button = None
        self.geolocations_close_button = None
        self.geolocations_window_delegate = None
        self.geolocations_thread = None
        self.geolocations_cancel_event = None
        self.geolocations_running = False
        self.geolocations_result_path = None
        self.geolocations_mode = None
        self.geolocations_temp_paths = []
        self.geolocations_places_overwrite = False
        self.geolocations_skip_place_names = False
        self.processing_phase_title = None
        self.preserve_processing_output_next = False
        self.assistant_control_skip_maps = False
        self.status_refresh_generation = 0
        self.project_status_processing_gate = threading.Event()
        self.project_status_processing_gate.set()
        self.media_counts_cache = None
        self.control_ready_cache = None
        self.track_maps_status_cache = None
        self.track_endpoint_place_missing_cache = 0
        self.gpx_ready_cache = None
        self.project_status_pending = False
        self.workflow_assistant_state = normalize_assistant_state(None, existing_adventure=False)
        self.workflow_assistant_stage = None
        self.assistant_place_names_pending_save = False
        self.control_table_window = None
        self.control_table_window_delegate = None
        self.control_table_view = None
        self.control_table_data_source = None
        self.control_table_rows = []
        self.control_table_view_model_indexes = []
        self.control_table_model_to_view_index = {}
        self.control_table_path = None
        self.control_table_file_signature = None
        self.control_table_pending_refresh = None
        self.control_table_undo_stack = []
        self.control_table_redo_stack = []
        self.control_table_hint_label = None
        self.control_table_drag_indexes = []
        self.control_table_preview_cache = {}
        self.control_table_file_datetime_cache = {}
        self.control_table_show_previews = False
        self.control_table_media_hidden = False
        self.control_table_media_filter_button = None
        self.control_table_preview_checkbox = None
        self.control_table_search_field = None
        self.control_table_search_status = None
        self.control_table_search_matches = []
        self.control_table_search_position = -1
        self.control_table_search_query = ""
        self.control_table_recovery_timer = None
        self.control_table_dirty = False
        self.control_table_last_snapshot_time = 0.0
        self.music_directive_help_window = None
        self.music_directive_help_text = None
        self.saved_project_payload = None
        self.media_viewer_window = None
        self.media_viewer_view = None
        self.media_viewer_delegate = None
        self.media_viewer_items = []
        self.media_viewer_index = 0
        self.media_viewer_source = "control"
        self.media_viewer_sort_mode = "filename"
        self.media_viewer_image_cache = {}
        self.media_viewer_hint_timer = None
        self.media_viewer_video_player = None
        self.media_viewer_video_view = None
        self.media_viewer_video_path = None
        self.media_viewer_video_preroll = None
        self.media_browser_window = None
        self.media_browser_table = None
        self.media_browser_data_source = None
        self.media_browser_merge_button = None
        self.media_browser_hint_label = None
        self.media_browser_rows = []
        self.media_browser_items = []
        self.media_browser_mode = "view"
        self.media_browser_sort_column = "name"
        self.media_browser_sort_ascending = True
        self.media_update_plan = None
        self.control_file_update_plan = None
        self.media_update_pending_plan = None
        self.control_file_update_result = None
        self.media_update_preview_window = None
        self.media_update_preview_table = None
        self.media_update_preview_data_source = None
        self.media_update_preview_place_checkbox = None
        self.media_update_preview_apply_button = None
        self.media_update_preview_track_text = None
        self.main_help_window = None
        self.parameter_window = None
        self.parameter_window_delegate = None
        self.parameter_form_scroll = None
        self.parameter_form_view = None
        self.parameter_section_buttons = []
        self.parameter_controls = {}
        self.parameter_steppers = {}
        self.parameter_tag_to_key = {}
        self.parameter_draft = {}
        self.parameter_current_section = SECTION_ORDER[0]
        self.parameter_show_advanced = False
        self.parameter_advanced_checkbox = None
        self.parameter_error_label = None
        self.parameter_apply_button = None
        self.parameter_editor_controller = None
        self.gpx_editor_controller = None
        self.plot_creation_thread = None
        self.plot_cancel_event = None
        self.plot_creation_image_paths = []
        self.plot_selection_window = None
        self.plot_selection_table = None
        self.plot_selection_data_source = None
        self.plot_selection_range_field = None
        self.plot_selection_all_checkbox = None
        self.plot_selection_result = None
        self.plot_selection_rows = []
        self._build_window()
        self.saved_project_payload = self._collect_project_payload()
        return self

    def init(self):
        return self.initWithProjectDirectory_projectFile_(None, None)

    def _build_window(self):
        rect = NSMakeRect(120.0, 48.0, 980.0, 740.0)
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(PROGRAM_TITLE)
        self.window.setMinSize_((MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT))

        content = self.window.contentView()
        self.root_view = NSView.alloc().initWithFrame_(content.bounds())
        self.root_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self.root_view)

        self.header_text_label = self._make_label("myCamino GPS Track Slide Show", 26.0, bold=True, centered=False)
        self.root_view.addSubview_(self.header_text_label)

        self.header_logo_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.header_logo_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self.header_logo_view.setImageAlignment_(NSImageAlignCenter)
        logo_path = bundled_resource_path("myCaminoTrackLogo.PNG")
        if logo_path.exists():
            self.header_logo_view.setImage_(NSImage.alloc().initWithContentsOfFile_(str(logo_path)))
        self.root_view.addSubview_(self.header_logo_view)

        self.parameter_button = self._make_icon_button(
            ["sf:gearshape", "NSActionTemplate"],
            "Settings",
            "showParameterEditor:",
        )
        self.parameter_button.setToolTip_("Edit project-specific workflow, slide-show, map, GPX, PDF, location, and map-service settings.")
        self.root_view.addSubview_(self.parameter_button)

        self.adventure_status_checkbox = self._make_section_status_checkbox()
        self.root_view.addSubview_(self.adventure_status_checkbox)
        self.adventure_box = self._make_box_label("Adventure")
        self.root_view.addSubview_(self.adventure_box)
        self.adventure_divider = self._make_section_divider()

        self.project_dir_label = self._make_label("Project directory")
        self.root_view.addSubview_(self.project_dir_label)
        self.project_dir_field = NSComboBox.alloc().initWithFrame_(NSMakeRect(0, 0, 100, FIELD_HEIGHT))
        self.project_dir_field.setFont_(NSFont.systemFontOfSize_(13.0))
        self.project_dir_field.setPlaceholderString_("Enter/select a project directory")
        self.project_dir_field.setCompletes_(True)
        self.project_dir_field.setUsesDataSource_(False)
        self.project_dir_field.addItemsWithObjectValues_([str(path) for path in self.recent_adventures])
        self.project_dir_field.setDelegate_(self)
        self.project_dir_field.setTag_(101)
        self.project_dir_field.setTarget_(self)
        self.project_dir_field.setAction_("projectDirectoryCommitted:")
        self.root_view.addSubview_(self.project_dir_field)

        self.project_dir_button = self._make_file_icon_button("chooseProjectDirectory:", "Choose or create the project directory.")
        self.root_view.addSubview_(self.project_dir_button)

        self.title_field_label = self._make_label("Adventure name")
        self.root_view.addSubview_(self.title_field_label)
        self.title_field = NSComboBox.alloc().initWithFrame_(NSMakeRect(0, 0, 100, FIELD_HEIGHT))
        self.title_field.setFont_(NSFont.systemFontOfSize_(13.0))
        self.title_field.setPlaceholderString_("Select or enter an Adventure name")
        self.title_field.setCompletes_(True)
        self.title_field.setUsesDataSource_(False)
        self.title_field.setDelegate_(self)
        self.title_field.setTag_(102)
        self.title_field.setTarget_(self)
        self.title_field.setAction_("adventureNameCommitted:")
        self.root_view.addSubview_(self.title_field)

        self.description_label = self._make_label("Description")
        self.root_view.addSubview_(self.description_label)
        self.description_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.description_scroll.setHasVerticalScroller_(True)
        self.description_scroll.setHasHorizontalScroller_(False)
        self.description_scroll.setBorderType_(1)
        self.description_scroll.setAutoresizingMask_(0)
        self.description_text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.description_text.setVerticallyResizable_(True)
        self.description_text.setHorizontallyResizable_(False)
        self.description_text.setRichText_(False)
        self.description_text.setFont_(NSFont.systemFontOfSize_(13.0))
        self.description_text.setString_("")
        self.description_text.setAutoresizingMask_(NSViewWidthSizable)
        description_container = self.description_text.textContainer()
        if description_container is not None:
            description_container.setWidthTracksTextView_(True)
        self.description_scroll.setDocumentView_(self.description_text)
        self.root_view.addSubview_(self.description_scroll)

        self.title_image_label = self._make_label("Title image")
        self.title_image_field = self._make_text_field(
            "First image in the slide-show control file"
        )
        self.title_image_field.setEditable_(False)
        self.title_image_choose_button = self._make_file_icon_button(
            "chooseTitleImage:",
            "Choose the image shown below the Intro description.",
        )
        self.title_image_default_button = self._make_button(
            "Use First",
            "useFirstTitleImage:",
        )
        self.root_view.addSubview_(self.title_image_label)
        self.root_view.addSubview_(self.title_image_field)
        self.root_view.addSubview_(self.title_image_choose_button)
        self.root_view.addSubview_(self.title_image_default_button)

        self.help_button = self._make_button("Help", "showMainHelp:")
        self.assistant_checkbox = self._make_checkbox("Assistant", "toggleWorkflowAssistant:")
        self.assistant_checkbox.setState_(NSControlStateValueOn)
        self.quit_button = self._make_button("Quit", "quit:")
        self.root_view.addSubview_(self.help_button)
        self.root_view.addSubview_(self.assistant_checkbox)
        self.root_view.addSubview_(self.quit_button)

        self.gpx_status_checkbox = self._make_section_status_checkbox()
        self.root_view.addSubview_(self.gpx_status_checkbox)
        self.gpx_box = self._make_box_label("GPX Files")
        self.root_view.addSubview_(self.gpx_box)
        self.gpx_divider = self._make_section_divider()

        self.gpx_label = self._make_label("Selected GPX file(s)")
        self.root_view.addSubview_(self.gpx_label)
        self.gpx_no_gpx_checkbox = self._make_checkbox(
            "No GPX file - use only photos",
            "noGPXNeededChanged:",
        )
        self.root_view.addSubview_(self.gpx_no_gpx_checkbox)
        self.gpx_field = NSComboBox.alloc().initWithFrame_(NSMakeRect(0, 0, 100, FIELD_HEIGHT))
        self.gpx_field.setFont_(NSFont.systemFontOfSize_(13.0))
        self.gpx_field.setPlaceholderString_("Choose one or more .gpx files")
        self.gpx_field.setCompletes_(True)
        self.gpx_field.setUsesDataSource_(False)
        self.gpx_field.setDelegate_(self)
        self.gpx_field.setTag_(103)
        self.gpx_field.setTarget_(self)
        self.gpx_field.setAction_("gpxSelectionCommitted:")
        self.root_view.addSubview_(self.gpx_field)

        self.gpx_button = self._make_button("Choose", "selectGPXFile:")
        self.root_view.addSubview_(self.gpx_button)
        self.gpx_folder_button = self._make_file_icon_button("openGPXFolder:", "Open the project folder in Finder to manage GPX files.")
        self.root_view.addSubview_(self.gpx_folder_button)
        self.gpx_edit_button = self._make_button("Add & Edit Tracks", "addAndEditTracks:")

        self.track_maps_status_checkbox = self._make_section_status_checkbox()
        self.root_view.addSubview_(self.track_maps_status_checkbox)
        self.track_maps_box = self._make_box_label("Map Generation")
        self.root_view.addSubview_(self.track_maps_box)
        self.track_maps_divider = self._make_section_divider()
        self.gpx_track_images_label = self._make_label("Maps")
        self.gpx_make_plots_button = self._make_button("Generate and Update Maps", "makePlots:")
        self.gpx_view_plots_button = self._make_button("View Maps", "viewPlots:")
        self.gpx_edit_plots_button = self._make_file_icon_button("editPlots:")
        self.gpx_cancel_plots_button = self._make_button("Cancel", "cancelMakePlots:")
        self.gpx_cancel_plots_button.setHidden_(True)
        self.root_view.addSubview_(self.gpx_edit_button)
        self.root_view.addSubview_(self.gpx_track_images_label)
        self.root_view.addSubview_(self.gpx_make_plots_button)
        self.root_view.addSubview_(self.gpx_view_plots_button)
        self.root_view.addSubview_(self.gpx_edit_plots_button)
        self.root_view.addSubview_(self.gpx_cancel_plots_button)

        self.gpx_summary_label = self._make_label(default_gpx_summary_text(), size=12.0)
        self.root_view.addSubview_(self.gpx_summary_label)
        self.track_maps_summary_label = self._make_label("No GPX file selected.", size=12.0)
        self.root_view.addSubview_(self.track_maps_summary_label)

        self.media_status_checkbox = self._make_section_status_checkbox()
        self.root_view.addSubview_(self.media_status_checkbox)
        self.media_box = self._make_box_label("Photos and Video Clips")
        self.root_view.addSubview_(self.media_box)
        self.media_divider = self._make_section_divider()
        self.media_label = self._make_label("Photos & Video Clips:")
        self.root_view.addSubview_(self.media_label)
        self.media_import_button = self._make_button("Import", "importMediaFiles:")
        self.media_view_button = self._make_button("View", "viewMediaFiles:")
        self.media_edit_button = self._make_file_icon_button("editMediaFiles:")
        self.media_metadata_button = self._make_button(
            "Update Metadata Extraction",
            "updateMediaMetadata:",
        )
        self.media_place_names_checkbox = self._make_checkbox(
            "Add place names",
            "fieldChanged:",
        )
        self.root_view.addSubview_(self.media_import_button)
        self.root_view.addSubview_(self.media_view_button)
        self.root_view.addSubview_(self.media_edit_button)
        self.root_view.addSubview_(self.media_metadata_button)
        self.root_view.addSubview_(self.media_place_names_checkbox)
        self.media_summary_label = self._make_label("No project directory selected.", size=12.0)
        self.root_view.addSubview_(self.media_summary_label)

        self.control_file_status_checkbox = self._make_section_status_checkbox()
        self.root_view.addSubview_(self.control_file_status_checkbox)
        self.control_box = self._make_box_label("Slide Show Control File")
        self.root_view.addSubview_(self.control_box)
        self.control_divider = self._make_section_divider()
        self.control_file_label = self._make_label("Control file")
        self.root_view.addSubview_(self.control_file_label)
        self.control_file_field = NSComboBox.alloc().initWithFrame_(NSMakeRect(0, 0, 100, FIELD_HEIGHT))
        self.control_file_field.setFont_(NSFont.systemFontOfSize_(13.0))
        self.control_file_field.setPlaceholderString_("Select or enter a .lst filename")
        self.control_file_field.setCompletes_(True)
        self.control_file_field.setUsesDataSource_(False)
        self.control_file_field.setDelegate_(self)
        self.control_file_field.setTag_(104)
        self.control_file_field.setTarget_(self)
        self.control_file_field.setAction_("controlFileCommitted:")
        self.root_view.addSubview_(self.control_file_field)
        self.control_file_create_button = self._make_button("Create", "createControlFile:")
        self.control_file_edit_button = self._make_button("Edit", "editControlFile:")
        self.control_file_update_button = self._make_button("Update Control File", "updateControlFile:")
        self.root_view.addSubview_(self.control_file_create_button)
        self.root_view.addSubview_(self.control_file_edit_button)
        self.root_view.addSubview_(self.control_file_update_button)
        self.control_file_summary_label = self._make_label("No slide show control file available.", size=12.0)
        self.root_view.addSubview_(self.control_file_summary_label)

        self.music_box = self._make_box_label("Music")
        self.root_view.addSubview_(self.music_box)
        self.music_divider = self._make_section_divider()
        self.no_music_checkbox = self._make_checkbox("No Music", "noMusicChanged:")
        self.root_view.addSubview_(self.no_music_checkbox)
        self.music_help_button = self._make_link_button("Help", "showMusicDirectiveHelp:")
        self.root_view.addSubview_(self.music_help_button)
        self.music_label = self._make_label("Playlist")
        self.music_field = self._make_text_field(
            "No playlist selected - alphabetical order"
        )
        self.music_field.setTarget_(self)
        self.music_field.setAction_("musicPlaylistCommitted:")
        self.music_playlist_select_button = self._make_button(
            "Choose",
            "chooseMusicPlaylist:",
        )
        self.music_choose_button = self._make_file_icon_button(
            "openMusicFolder:",
            "Open the project audio folder.",
        )
        self.music_playlist_button = self._make_button("Create Playlist", "createMusicPlaylist:")
        self.music_playlist_update_button = self._make_button("Update Playlist", "updateMusicPlaylist:")
        self.music_playlist_edit_button = self._make_button("Edit Playlist", "editMusicPlaylist:")
        self.music_summary_label = self._make_label(
            "No playlist selected; alphabetical order.",
            size=12.0,
        )
        for control in (
            self.music_label,
            self.music_field,
            self.music_playlist_select_button,
            self.music_choose_button,
            self.music_playlist_button,
            self.music_playlist_update_button,
            self.music_playlist_edit_button,
            self.music_summary_label,
        ):
            self.root_view.addSubview_(control)

        self.slideshow_box = self._make_box_label("Start Slide Show")
        self.root_view.addSubview_(self.slideshow_box)
        self.slideshow_divider = self._make_section_divider()
        self.slideshow_label = self._make_label("Slide show")
        self.root_view.addSubview_(self.slideshow_label)
        self.slideshow_start_button = self._make_button("Start", "startSelectedSlideShow:")
        self.slideshow_continue_button = self._make_button("Continue", "continueSelectedSlideShow:")
        self.slideshow_continue_button.setEnabled_(False)
        self.pdf_summary_button = self._make_button("PDF Summary", "exportPdfSummary:")
        self.root_view.addSubview_(self.slideshow_start_button)
        self.root_view.addSubview_(self.slideshow_continue_button)
        self.root_view.addSubview_(self.pdf_summary_button)

        self.status_label = self._make_status_label("Choose or create an adventure directory to begin.")
        self.root_view.addSubview_(self.status_label)
        self.progress_bar = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 100, PROGRESS_HEIGHT))
        self.progress_bar.setIndeterminate_(False)
        self.progress_bar.setMinValue_(0.0)
        self.progress_bar.setMaxValue_(1.0)
        self.progress_bar.setDoubleValue_(0.0)
        self.progress_bar.setHidden_(True)
        self.root_view.addSubview_(self.progress_bar)

        self.workflow_assistant_bubble = WorkflowAssistantBubbleView.alloc().initWithController_(self)
        self.root_view.addSubview_(self.workflow_assistant_bubble)

        self.window_delegate = GPSTrackShowGUIWindowDelegate.alloc().initWithController_(self)
        self.window.setDelegate_(self.window_delegate)

        self.notification_center = NSNotificationCenter.defaultCenter()
        self.notification_center.addObserver_selector_name_object_(
            self,
            "controlTextDidEndEditing:",
            NSControlTextDidEndEditingNotification,
            None,
        )
        self.notification_center.addObserver_selector_name_object_(
            self,
            "textDidChange:",
            "NSTextDidChangeNotification",
            self.description_text,
        )
        self.notification_center.addObserver_selector_name_object_(
            self,
            "projectDirectoryComboSelectionChanged:",
            "NSComboBoxSelectionDidChangeNotification",
            self.project_dir_field,
        )
        for combo in (self.title_field, self.gpx_field, self.control_file_field):
            self.notification_center.addObserver_selector_name_object_(
                self,
                "projectFileComboSelectionChanged:",
                "NSComboBoxSelectionDidChangeNotification",
                combo,
            )

        self._sync_legacy_parameter_controls()
        self.layout_window()
        self._configure_tooltips()
        self._configure_key_view_loop()
        self.refresh_section_status_indicators()

    def _make_box_label(self, text):
        label = self._make_label(text, size=14.0, bold=True, centered=False)
        label.setTextColor_(NSColor.controlAccentColor())
        return label

    def _make_section_divider(self):
        divider = NSBox.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 100.0, 1.0))
        divider.setBoxType_(NSBoxSeparator)
        self.root_view.addSubview_(divider)
        return divider

    def _make_label(self, text, size=13.0, bold=False, centered=False):
        field = NSTextField.labelWithString_(text)
        font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        field.setFont_(font)
        if centered:
            field.setAlignment_(2)
        return field

    def _make_status_label(self, text):
        field = NSTextField.labelWithString_(text)
        field.setFont_(NSFont.systemFontOfSize_(12.0))
        field.setDrawsBackground_(True)
        field.setBackgroundColor_(NSColor.windowBackgroundColor())
        field.setBezeled_(True)
        return field

    def _make_text_field(self, placeholder):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 100, FIELD_HEIGHT))
        field.setFont_(NSFont.systemFontOfSize_(13.0))
        field.setPlaceholderString_(placeholder)
        return field

    def _recent_adventures_store_path(self):
        return Path.home() / "Library" / "Application Support" / "myCamino GPS Track Show" / "recent_adventures.json"

    def _load_recent_adventures(self):
        store_path = self._recent_adventures_store_path()
        try:
            payload = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        recent = []
        seen = set()
        for item in payload:
            try:
                path = Path(str(item)).expanduser().resolve(strict=False)
            except OSError:
                continue
            if path.suffix.lower() == ".adv":
                continue
            key = str(path)
            if path.exists() and path.is_dir() and key not in seen:
                recent.append(path)
                seen.add(key)
            if len(recent) >= 10:
                break
        if len(recent) != len(payload):
            self.recent_adventures = recent
            self._save_recent_adventures()
        return recent

    def _save_recent_adventures(self):
        store_path = self._recent_adventures_store_path()
        try:
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps([str(path) for path in self.recent_adventures[:10]], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _refresh_recent_adventure_menu(self):
        if not hasattr(self, "project_dir_field"):
            return
        self.project_dir_field.removeAllItems()
        self.project_dir_field.addItemsWithObjectValues_([str(path) for path in self.recent_adventures[:10]])

    def _remember_recent_adventure(self, project_dir):
        try:
            path = Path(project_dir).expanduser().resolve(strict=False)
        except OSError:
            return
        if path.suffix.lower() == ".adv":
            path = path.parent
        if not path.exists() or not path.is_dir():
            return
        path_key = str(path)
        self.recent_adventures = [item for item in self.recent_adventures if str(item) != path_key]
        self.recent_adventures.insert(0, path)
        normalized = []
        seen = set()
        for item in self.recent_adventures:
            key = str(item)
            if key not in seen:
                normalized.append(item)
                seen.add(key)
            if len(normalized) >= 10:
                break
        self.recent_adventures = normalized
        self._save_recent_adventures()
        self._refresh_recent_adventure_menu()

    def _make_button(self, title, action):
        button = make_liquid_glass_button(NSMakeRect(0, 0, BUTTON_WIDTH, FIELD_HEIGHT))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    def _make_checkbox(self, title, action):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 100, FIELD_HEIGHT))
        button.setButtonType_(NSButtonTypeSwitch)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    def _make_link_button(self, title, action):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 44.0, FIELD_HEIGHT))
        button.setBordered_(False)
        button.setTitle_(title)
        button.setFont_(NSFont.systemFontOfSize_(11.0))
        button.setContentTintColor_(NSColor.linkColor())
        button.setTarget_(self)
        button.setAction_(action)
        return button

    def _make_section_status_checkbox(self):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, SECTION_STATUS_SIZE, FIELD_HEIGHT))
        button.setButtonType_(NSButtonTypeSwitch)
        button.setEnabled_(False)
        button.setTitle_("")
        button.setToolTip_("Shows whether the minimum required step for this section is complete.")
        return button

    def _set_section_status_checkbox(self, button, complete, tooltip):
        color = NSColor.systemGreenColor() if complete else NSColor.systemRedColor()
        title = "✓" if complete else "✗"
        attributes = {
            NSForegroundColorAttributeName: color,
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(14.0),
        }
        attributed_title = NSAttributedString.alloc().initWithString_attributes_(title, attributes)
        button.setState_(NSControlStateValueOn if complete else 0)
        button.setAttributedTitle_(attributed_title)
        button.setToolTip_(tooltip)

    def _make_file_icon_button(self, action, tooltip="Choose a file."):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, FILE_BUTTON_WIDTH, FILE_BUTTON_WIDTH))
        browse_image = NSImage.imageNamed_(NSImageNameFolder)
        if browse_image is not None:
            button.setImage_(browse_image)
            button.setImagePosition_(2)
            button.setTitle_("")
        else:
            button.setTitle_("...")
        button.setBordered_(False)
        button.setTarget_(self)
        button.setAction_(action)
        button.setToolTip_(tooltip)
        return button

    def _configure_tooltips(self):
        self.adventure_box.setToolTip_("Project identity: choose the adventure folder, name, and description.")
        self.project_dir_label.setToolTip_("Working directory where the adventure data, plots, media, and control files are stored.")
        self.project_dir_field.setToolTip_("Type or choose the project directory. The dropdown offers the last ten adventure folders. Missing directories are created when selected.")
        self.project_dir_button.setToolTip_("Choose or create the project directory.")
        self.title_field_label.setToolTip_("Select, create, rename, or copy an Adventure in the current project directory.")
        self.title_field.setToolTip_("The dropdown lists Adventures in this directory. Commit an edited name to rename or copy the active Adventure.")
        self.description_label.setToolTip_("Optional short description of this adventure.")
        self.description_text.setToolTip_("Enter a short free-text adventure description.")
        self.title_image_label.setToolTip_(
            "Optional image shown below the description on the Intro page."
        )
        self.title_image_field.setToolTip_(
            "If empty, the first still image in the control file is used."
        )
        self.title_image_default_button.setToolTip_(
            "Use the first still image in the control file."
        )

        self.gpx_box.setToolTip_("Optional measured GPX track input and editing. GPX is not required when Journey source is Media locations.")
        self.gpx_label.setToolTip_("Selected GPX file or files for this adventure.")
        self.gpx_no_gpx_checkbox.setToolTip_(
            "Select this when no GPX track is available. The slide show will use GPS positions stored with the photos and videos."
        )
        self.gpx_field.setToolTip_("GPX filename in the project directory. External files are copied into the project directory after confirmation.")
        self.gpx_button.setToolTip_(
            "Choose one or more GPX files. Multiple files are joined into one Adventure GPX in the GPX Editor."
        )
        self.gpx_folder_button.setToolTip_("Open Finder with GPX files in the project directory so they can be renamed or deleted.")
        self.gpx_edit_button.setToolTip_("Open GPXEditor to add or edit tracks and save the active GPX file.")
        self.track_maps_box.setToolTip_("Maps are generated automatically after media preparation. Use this section to repair or deliberately regenerate the shared overview and both map variants.")
        self.gpx_track_images_label.setToolTip_("Map-generation actions.")
        self.gpx_make_plots_button.setToolTip_("Select logical stages and generate missing or stale Standard and Time-Lapse maps together.")
        self.gpx_view_plots_button.setToolTip_("Open the map viewer for the overview and existing Standard and Time-Lapse maps.")
        self.gpx_edit_plots_button.setToolTip_("Open the trackimages folder in Finder.")
        self.gpx_cancel_plots_button.setToolTip_("Cancel plot generation after the current image finishes.")
        self.gpx_summary_label.setToolTip_("Summary of the selected GPX file.")
        self.track_maps_summary_label.setToolTip_("Shows how many measured-track or media-stage maps exist and whether the overview is current.")

        self.media_box.setToolTip_("Import and manage photos and video clips for this adventure.")
        self.media_label.setToolTip_("Photo and video media stored in the project directory.")
        self.media_import_button.setToolTip_("Import selected image and video files into the project directory without duplicating existing files.")
        self.media_view_button.setToolTip_("Open the project photos and videos in the built-in viewer.")
        self.media_edit_button.setToolTip_("Open the project directory in Finder.")
        self.media_metadata_button.setToolTip_(
            "Check media metadata and refresh only missing, invalid, legacy, or changed sidecars. "
            "The selected place-name options are applied afterwards."
        )
        self.media_place_names_checkbox.setToolTip_(
            "Add readable place names from GPS coordinates while metadata is prepared or updated."
        )
        self.media_summary_label.setToolTip_("Current count of imported photos and video clips.")

        self.control_box.setToolTip_("Create and edit the slide-show control file used to merge media, dates, maps, and geolocations.")
        self.control_file_label.setToolTip_("Select an existing .lst file or enter the filename that Create should produce.")
        self.control_file_field.setToolTip_("The active slide-show control file. The dropdown lists .lst files in this project directory.")
        self.control_file_create_button.setToolTip_("Create the sorted slide-show control file. Missing media GPS is inferred from a uniquely matching timed track when current generated maps are available; adjacent media is then associated with nearby stages.")
        self.control_file_edit_button.setToolTip_("Open the editable control-file table.")
        self.control_file_update_button.setToolTip_("Update the existing control file with current map references and imported, missing, invalid, legacy, or changed media. Map rendering remains in Map Generation.")
        self.control_file_summary_label.setToolTip_("Shows whether a control file exists and counts images, videos, track maps, media-location maps, dates, and the overview map.")

        self.music_box.setToolTip_("Optional background music and playlist controls.")
        self.no_music_checkbox.setToolTip_(
            "Hide the music controls and start slide shows without background music."
        )
        self.music_help_button.setToolTip_(
            "Explain #MUSIC directives, playlist labels, albums, loops, volume, and audio switching."
        )
        self.slideshow_box.setToolTip_("Launch the final slide show or export a GPX track PDF summary.")
        self.slideshow_label.setToolTip_("Choose Standard or Time-Lapse playback before starting.")
        self.slideshow_label.setToolTip_("The initial playback style is selected in Settings. Use t and Shift-t during playback to change it.")
        self.slideshow_start_button.setToolTip_("Launch the selected slide-show type from the beginning.")
        self.slideshow_continue_button.setToolTip_("Continue the selected slide-show type from its last automatically saved position.")
        self.pdf_summary_button.setToolTip_("Export a PDF summary of the current GPX tracks using the GPX Editor PDF options.")
        self.music_label.setToolTip_("The active playlist inside the project's fixed audio folder.")
        self.music_field.setToolTip_(
            "Shows the selected .playlist file. Without a selected playlist, "
            "audio files play in alphabetical relative-path order."
        )
        self.music_playlist_select_button.setToolTip_(
            "Choose an existing .playlist file from the project's audio folder."
        )
        self.music_choose_button.setToolTip_(
            "Open the project audio folder in Finder. Copy audio files and album "
            "directories into this folder."
        )
        self.music_playlist_button.setToolTip_(
            "Create a new named playlist in the project audio folder with unique "
            "$album and $file labels."
        )
        self.music_playlist_update_button.setToolTip_("Preserve the playlist and append newly discovered audio files with unique labels.")
        self.music_playlist_edit_button.setToolTip_(
            "Open the active playlist in TextEdit. If needed, create one first "
            "from the project audio folder."
        )
        self.music_summary_label.setToolTip_("Shows the number of playable audio files and whether playlist or alphabetical ordering is used.")

        self.help_button.setToolTip_("Show a simple overview of the program and the recommended workflow.")
        self.assistant_checkbox.setToolTip_(
            "Show or hide the Adventure-specific assistant that points to the next required workflow step."
        )
        self.quit_button.setToolTip_("Auto-save pending Adventure changes and quit the program.")
        self.status_label.setToolTip_("Current operation status.")
        self.progress_bar.setToolTip_("Progress for longer-running operations.")

    def _configure_key_view_loop(self):
        self.window.setInitialFirstResponder_(self.project_dir_field)
        self.project_dir_field.setNextKeyView_(self.project_dir_button)
        self.project_dir_button.setNextKeyView_(self.title_field)
        self.title_field.setNextKeyView_(self.description_text)
        self.description_text.setNextKeyView_(self.title_image_choose_button)
        self.title_image_choose_button.setNextKeyView_(self.title_image_default_button)
        self.title_image_default_button.setNextKeyView_(self.gpx_field)
        self.gpx_field.setNextKeyView_(self.gpx_button)
        self.gpx_button.setNextKeyView_(self.gpx_folder_button)
        self.gpx_folder_button.setNextKeyView_(self.gpx_edit_button)
        self.gpx_edit_button.setNextKeyView_(self.gpx_no_gpx_checkbox)
        self.gpx_no_gpx_checkbox.setNextKeyView_(self.media_import_button)
        self.media_import_button.setNextKeyView_(self.media_view_button)
        self.media_view_button.setNextKeyView_(self.media_edit_button)
        self.media_edit_button.setNextKeyView_(self.media_place_names_checkbox)
        self.media_place_names_checkbox.setNextKeyView_(self.media_metadata_button)
        self.media_metadata_button.setNextKeyView_(self.gpx_make_plots_button)
        self.gpx_make_plots_button.setNextKeyView_(self.gpx_view_plots_button)
        self.gpx_view_plots_button.setNextKeyView_(self.gpx_edit_plots_button)
        self.gpx_edit_plots_button.setNextKeyView_(self.gpx_cancel_plots_button)
        self.gpx_cancel_plots_button.setNextKeyView_(self.control_file_field)
        self.control_file_field.setNextKeyView_(self.control_file_create_button)
        self.control_file_create_button.setNextKeyView_(self.control_file_edit_button)
        self.control_file_edit_button.setNextKeyView_(self.control_file_update_button)
        self.control_file_update_button.setNextKeyView_(self.no_music_checkbox)
        self.no_music_checkbox.setNextKeyView_(self.music_help_button)
        self.music_help_button.setNextKeyView_(self.music_field)
        self.music_field.setNextKeyView_(self.music_playlist_select_button)
        self.music_playlist_select_button.setNextKeyView_(self.music_choose_button)
        self.music_choose_button.setNextKeyView_(self.music_playlist_button)
        self.music_playlist_button.setNextKeyView_(self.music_playlist_update_button)
        self.music_playlist_update_button.setNextKeyView_(self.music_playlist_edit_button)
        self.music_playlist_edit_button.setNextKeyView_(self.slideshow_start_button)
        self.slideshow_start_button.setNextKeyView_(self.slideshow_continue_button)
        self.slideshow_continue_button.setNextKeyView_(self.pdf_summary_button)
        self.pdf_summary_button.setNextKeyView_(self.quit_button)
        self.quit_button.setNextKeyView_(self.assistant_checkbox)
        self.assistant_checkbox.setNextKeyView_(self.help_button)
        self.help_button.setNextKeyView_(self.parameter_button)
        self.parameter_button.setNextKeyView_(self.project_dir_field)

    def layout_window(self):
        bounds = self.root_view.bounds()
        width = bounds.size.width
        height = bounds.size.height

        left_x = PADDING
        field_x = left_x + LABEL_WIDTH + INNER_GAP
        usable_width = width - 2 * PADDING
        description_width = usable_width - LABEL_WIDTH - INNER_GAP
        project_field_width = description_width - FILE_BUTTON_WIDTH - INNER_GAP
        gpx_choose_width = 76.0
        gpx_field_width = description_width - gpx_choose_width - FILE_BUTTON_WIDTH - EDIT_BUTTON_WIDTH - 3 * INNER_GAP

        current_top = height - PADDING

        title_height = 50.0
        logo_size = 112.0
        logo_x = field_x + description_width - logo_size
        parameter_button_size = 34.0
        parameter_button_x = logo_x - parameter_button_size - INNER_GAP
        help_button_width = 62.0
        help_button_x = parameter_button_x - help_button_width - INNER_GAP
        assistant_width = 92.0
        assistant_x = help_button_x - assistant_width - INNER_GAP
        text_width = max(180.0, assistant_x - left_x - INNER_GAP)
        self.header_text_label.setFrame_(
            NSMakeRect(left_x, current_top - title_height + 13.0, text_width, 34.0)
        )
        self.parameter_button.setFrame_(
            NSMakeRect(parameter_button_x, current_top - title_height + 12.0, parameter_button_size, parameter_button_size)
        )
        self.help_button.setFrame_(
            NSMakeRect(help_button_x, current_top - title_height + 12.0, help_button_width, parameter_button_size)
        )
        self.assistant_checkbox.setFrame_(
            NSMakeRect(assistant_x, current_top - title_height + 14.0, assistant_width, parameter_button_size)
        )
        self.header_logo_view.setFrame_(
            NSMakeRect(logo_x, current_top - logo_size + 26.0, logo_size, logo_size)
        )
        current_top -= title_height + 4.0

        section_label_x = PADDING + SECTION_STATUS_SIZE + 4.0
        self.adventure_status_checkbox.setFrame_(NSMakeRect(PADDING, current_top - FIELD_HEIGHT + 5.0, SECTION_STATUS_SIZE, FIELD_HEIGHT))
        self.adventure_box.setFrame_(NSMakeRect(section_label_x, current_top - 18.0, 86.0, 18.0))
        self.adventure_divider.setFrame_(
            NSMakeRect(
                section_label_x,
                current_top + 7.0,
                max(20.0, width - PADDING - section_label_x),
                1.0,
            )
        )
        current_top -= 22.0

        row_y = current_top - FIELD_HEIGHT
        self.project_dir_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
        self.project_dir_field.setFrame_(NSMakeRect(field_x, row_y, project_field_width, FIELD_HEIGHT))
        self.project_dir_button.setFrame_(NSMakeRect(field_x + project_field_width + INNER_GAP, row_y, FILE_BUTTON_WIDTH, FILE_BUTTON_WIDTH))

        row_y -= FIELD_HEIGHT + ROW_GAP
        self.title_field_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
        self.title_field.setFrame_(NSMakeRect(field_x, row_y, description_width, FIELD_HEIGHT))

        row_y -= DESCRIPTION_HEIGHT + ROW_GAP
        self.description_label.setFrame_(NSMakeRect(left_x, row_y + DESCRIPTION_HEIGHT - 18.0, LABEL_WIDTH, 18.0))
        self.description_scroll.setFrame_(NSMakeRect(field_x, row_y, description_width, DESCRIPTION_HEIGHT))
        self.description_text.setFrame_(NSMakeRect(0, 0, description_width, DESCRIPTION_HEIGHT))
        description_container = self.description_text.textContainer()
        if description_container is not None:
            description_container.setContainerSize_(NSMakeSize(description_width, 10_000_000.0))
            description_container.setWidthTracksTextView_(True)

        row_y -= FIELD_HEIGHT + ROW_GAP
        title_image_button_width = 82.0
        title_image_field_width = (
            description_width
            - FILE_BUTTON_WIDTH
            - title_image_button_width
            - 2.0 * INNER_GAP
        )
        self.title_image_label.setFrame_(
            NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0)
        )
        self.title_image_field.setFrame_(
            NSMakeRect(field_x, row_y, title_image_field_width, FIELD_HEIGHT)
        )
        self.title_image_choose_button.setFrame_(
            NSMakeRect(
                field_x + title_image_field_width + INNER_GAP,
                row_y,
                FILE_BUTTON_WIDTH,
                FILE_BUTTON_WIDTH,
            )
        )
        self.title_image_default_button.setFrame_(
            NSMakeRect(
                field_x
                + title_image_field_width
                + INNER_GAP
                + FILE_BUTTON_WIDTH
                + INNER_GAP,
                row_y,
                title_image_button_width,
                FIELD_HEIGHT,
            )
        )

        current_top = row_y - BLOCK_GAP

        self.gpx_status_checkbox.setFrame_(NSMakeRect(PADDING, current_top - FIELD_HEIGHT + 5.0, SECTION_STATUS_SIZE, FIELD_HEIGHT))
        self.gpx_box.setFrame_(NSMakeRect(section_label_x, current_top - 18.0, 78.0, 18.0))
        self.gpx_no_gpx_checkbox.setFrame_(
            NSMakeRect(field_x, current_top - 21.0, 220.0, FIELD_HEIGHT)
        )
        self.gpx_divider.setFrame_(
            NSMakeRect(
                section_label_x,
                current_top + 7.0,
                max(20.0, width - PADDING - section_label_x),
                1.0,
            )
        )
        current_top -= 22.0
        media_only = str(self.parameters.get("trackmaps.route_source", "automatic")) == "media"
        if media_only:
            row_y = current_top
        else:
            row_y = current_top - FIELD_HEIGHT
            self.gpx_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
            self.gpx_field.setFrame_(NSMakeRect(field_x, row_y, gpx_field_width, FIELD_HEIGHT))
            self.gpx_button.setFrame_(
                NSMakeRect(field_x + gpx_field_width + INNER_GAP, row_y, gpx_choose_width, FIELD_HEIGHT)
            )
            self.gpx_folder_button.setFrame_(
                NSMakeRect(
                    field_x + gpx_field_width + gpx_choose_width + 2 * INNER_GAP,
                    row_y,
                    FILE_BUTTON_WIDTH,
                    FILE_BUTTON_WIDTH,
                )
            )
            self.gpx_edit_button.setFrame_(
                NSMakeRect(
                    field_x + gpx_field_width + gpx_choose_width + FILE_BUTTON_WIDTH + 3 * INNER_GAP,
                    row_y - 1.0,
                    EDIT_BUTTON_WIDTH,
                    FIELD_HEIGHT + 2.0,
                )
            )
            row_y -= 18.0
            self.gpx_summary_label.setFrame_(
                NSMakeRect(
                    field_x,
                    row_y,
                    description_width,
                    18.0,
                )
            )

        current_top = row_y - BLOCK_GAP
        self.media_status_checkbox.setFrame_(NSMakeRect(PADDING, current_top - FIELD_HEIGHT + 5.0, SECTION_STATUS_SIZE, FIELD_HEIGHT))
        self.media_box.setFrame_(NSMakeRect(section_label_x, current_top - 18.0, 168.0, 18.0))
        self.media_divider.setFrame_(
            NSMakeRect(
                section_label_x,
                current_top + 7.0,
                max(20.0, width - PADDING - section_label_x),
                1.0,
            )
        )
        current_top -= 22.0
        row_y = current_top - FIELD_HEIGHT
        self.media_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
        media_button_start = field_x
        self.media_import_button.setFrame_(NSMakeRect(media_button_start, row_y, SMALL_BUTTON_WIDTH, FIELD_HEIGHT))
        self.media_view_button.setFrame_(NSMakeRect(media_button_start + SMALL_BUTTON_WIDTH + INNER_GAP, row_y, SMALL_BUTTON_WIDTH, FIELD_HEIGHT))
        self.media_edit_button.setFrame_(NSMakeRect(media_button_start + 2 * (SMALL_BUTTON_WIDTH + INNER_GAP), row_y, FILE_BUTTON_WIDTH, FILE_BUTTON_WIDTH))
        place_names_x = media_button_start + 2 * (SMALL_BUTTON_WIDTH + INNER_GAP) + FILE_BUTTON_WIDTH + 2 * INNER_GAP
        self.media_place_names_checkbox.setFrame_(NSMakeRect(place_names_x, row_y, 180.0, FIELD_HEIGHT))
        metadata_width = 180.0
        metadata_x = field_x + description_width - metadata_width
        self.media_metadata_button.setFrame_(NSMakeRect(metadata_x, row_y, metadata_width, FIELD_HEIGHT))
        media_summary_y = row_y - 18.0
        self.media_summary_label.setFrame_(
            NSMakeRect(
                field_x,
                media_summary_y,
                max(120.0, width - PADDING - field_x),
                18.0,
            )
        )

        current_top = media_summary_y - BLOCK_GAP
        self.track_maps_status_checkbox.setFrame_(NSMakeRect(PADDING, current_top - FIELD_HEIGHT + 5.0, SECTION_STATUS_SIZE, FIELD_HEIGHT))
        self.track_maps_box.setFrame_(NSMakeRect(section_label_x, current_top - 18.0, 112.0, 18.0))
        self.track_maps_divider.setFrame_(
            NSMakeRect(
                section_label_x,
                current_top + 7.0,
                max(20.0, width - PADDING - section_label_x),
                1.0,
            )
        )
        current_top -= 22.0
        row_y = current_top - FIELD_HEIGHT
        self.gpx_track_images_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
        track_button_x = field_x
        self.gpx_make_plots_button.setFrame_(NSMakeRect(track_button_x, row_y, 190.0, FIELD_HEIGHT))
        view_x = track_button_x + 190.0 + INNER_GAP
        self.gpx_view_plots_button.setFrame_(NSMakeRect(view_x, row_y, 92.0, FIELD_HEIGHT))
        folder_x = view_x + 92.0 + INNER_GAP
        self.gpx_edit_plots_button.setFrame_(NSMakeRect(folder_x, row_y, FILE_BUTTON_WIDTH, FILE_BUTTON_WIDTH))
        cancel_x = folder_x + FILE_BUTTON_WIDTH + INNER_GAP
        self.gpx_cancel_plots_button.setFrame_(NSMakeRect(cancel_x, row_y, 72.0, FIELD_HEIGHT))
        row_y -= 18.0
        self.track_maps_summary_label.setFrame_(
            NSMakeRect(
                field_x,
                row_y,
                description_width,
                18.0,
            )
        )

        current_top = row_y - BLOCK_GAP
        self.control_file_status_checkbox.setFrame_(NSMakeRect(PADDING, current_top - FIELD_HEIGHT + 5.0, SECTION_STATUS_SIZE, FIELD_HEIGHT))
        self.control_box.setFrame_(NSMakeRect(section_label_x, current_top - 18.0, 182.0, 18.0))
        self.control_divider.setFrame_(
            NSMakeRect(
                section_label_x,
                current_top + 7.0,
                max(20.0, width - PADDING - section_label_x),
                1.0,
            )
        )
        current_top -= 22.0
        row_y = current_top - FIELD_HEIGHT
        self.control_file_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
        self.control_file_field.setFrame_(NSMakeRect(field_x, row_y, description_width, FIELD_HEIGHT))
        row_y -= FIELD_HEIGHT + 2.0
        self.control_file_create_button.setFrame_(NSMakeRect(field_x, row_y, SMALL_BUTTON_WIDTH, FIELD_HEIGHT))
        edit_x = field_x + SMALL_BUTTON_WIDTH + INNER_GAP
        self.control_file_edit_button.setFrame_(NSMakeRect(edit_x, row_y, SMALL_BUTTON_WIDTH, FIELD_HEIGHT))
        update_x = edit_x + SMALL_BUTTON_WIDTH + INNER_GAP
        self.control_file_update_button.setFrame_(NSMakeRect(update_x, row_y, 154.0, FIELD_HEIGHT))
        summary_y = row_y - 18.0
        self.control_file_summary_label.setFrame_(
            NSMakeRect(
                field_x,
                summary_y,
                max(120.0, width - PADDING - field_x),
                18.0,
            )
        )

        current_top = summary_y - BLOCK_GAP
        self.music_box.setFrame_(NSMakeRect(PADDING, current_top - 18.0, 52.0, 18.0))
        self.no_music_checkbox.setFrame_(
            NSMakeRect(field_x, current_top - 21.0, 110.0, FIELD_HEIGHT)
        )
        self.music_help_button.setFrame_(
            NSMakeRect(field_x + 112.0, current_top - 21.0, 42.0, FIELD_HEIGHT)
        )
        self.music_divider.setFrame_(
            NSMakeRect(
                PADDING,
                current_top + 7.0,
                max(20.0, width - 2 * PADDING),
                1.0,
            )
        )
        current_top -= 22.0
        music_enabled = bool(self.parameters.get("audio.enabled", False))
        if music_enabled:
            row_y = current_top - FIELD_HEIGHT
            self.music_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
            music_action_width = (
                68.0
                + FILE_BUTTON_WIDTH
                + 100.0
                + 100.0
                + 84.0
                + 5 * INNER_GAP
            )
            music_field_width = max(200.0, description_width - music_action_width)
            self.music_field.setFrame_(NSMakeRect(field_x, row_y, music_field_width, FIELD_HEIGHT))
            playlist_select_x = field_x + music_field_width + INNER_GAP
            self.music_playlist_select_button.setFrame_(
                NSMakeRect(playlist_select_x, row_y, 68.0, FIELD_HEIGHT)
            )
            music_choose_x = playlist_select_x + 68.0 + INNER_GAP
            self.music_choose_button.setFrame_(
                NSMakeRect(music_choose_x, row_y, FILE_BUTTON_WIDTH, FILE_BUTTON_WIDTH)
            )
            playlist_x = music_choose_x + FILE_BUTTON_WIDTH + INNER_GAP
            self.music_playlist_button.setFrame_(NSMakeRect(playlist_x, row_y, 100.0, FIELD_HEIGHT))
            playlist_update_x = playlist_x + 100.0 + INNER_GAP
            self.music_playlist_update_button.setFrame_(
                NSMakeRect(playlist_update_x, row_y, 100.0, FIELD_HEIGHT)
            )
            playlist_edit_x = playlist_update_x + 100.0 + INNER_GAP
            self.music_playlist_edit_button.setFrame_(
                NSMakeRect(playlist_edit_x, row_y, 84.0, FIELD_HEIGHT)
            )
            music_summary_y = row_y - 18.0
            self.music_summary_label.setFrame_(
                NSMakeRect(field_x, music_summary_y, description_width, 18.0)
            )
            current_top = music_summary_y - BLOCK_GAP
        else:
            current_top -= BLOCK_GAP

        self.slideshow_box.setFrame_(NSMakeRect(PADDING, current_top - 18.0, 118.0, 18.0))
        self.slideshow_divider.setFrame_(
            NSMakeRect(
                PADDING,
                current_top + 7.0,
                max(20.0, width - 2 * PADDING),
                1.0,
            )
        )
        current_top -= 22.0
        row_y = current_top - FIELD_HEIGHT
        self.slideshow_label.setFrame_(NSMakeRect(left_x, row_y + 4.0, LABEL_WIDTH, 18.0))
        start_x = field_x
        self.slideshow_start_button.setFrame_(NSMakeRect(start_x, row_y, SMALL_BUTTON_WIDTH, FIELD_HEIGHT))
        continue_x = start_x + SMALL_BUTTON_WIDTH + INNER_GAP
        self.slideshow_continue_button.setFrame_(NSMakeRect(continue_x, row_y, SMALL_BUTTON_WIDTH, FIELD_HEIGHT))
        pdf_x = continue_x + SMALL_BUTTON_WIDTH + INNER_GAP
        self.pdf_summary_button.setFrame_(NSMakeRect(pdf_x, row_y, 124.0, FIELD_HEIGHT))
        quit_x = field_x + description_width - BUTTON_WIDTH
        self.quit_button.setFrame_(NSMakeRect(quit_x, row_y, BUTTON_WIDTH, FIELD_HEIGHT))
        progress_visible = not bool(self.progress_bar.isHidden())
        status_bottom = row_y - STATUS_HEIGHT - 4.0
        progress_bottom = status_bottom - PROGRESS_HEIGHT - 4.0
        if not progress_visible:
            status_bottom = progress_bottom
        self.status_label.setFrame_(NSMakeRect(field_x, status_bottom, description_width, STATUS_HEIGHT))
        self.progress_bar.setFrame_(
            NSMakeRect(
                field_x,
                progress_bottom,
                description_width,
                PROGRESS_HEIGHT if progress_visible else 0.0,
            )
        )
        self.layout_workflow_assistant()

    def set_status(self, message: str):
        self.status_label.setStringValue_(message)
        self.status_label.setNeedsDisplay_(True)

    def set_progress(self, current=0.0, total=1.0):
        total = max(float(total), 1.0)
        current = min(max(float(current), 0.0), total)
        self.progress_bar.stopAnimation_(None)
        self.progress_bar.setIndeterminate_(False)
        self.progress_bar.setHidden_(False)
        self.progress_bar.setMaxValue_(total)
        self.progress_bar.setDoubleValue_(current)
        self.progress_bar.setNeedsDisplay_(True)
        self.layout_window()

    def reset_progress(self):
        self.progress_bar.stopAnimation_(None)
        self.progress_bar.setIndeterminate_(False)
        self.progress_bar.setDoubleValue_(0.0)
        self.progress_bar.setHidden_(True)
        self.progress_bar.setNeedsDisplay_(True)
        self.layout_window()

    def setProgressFromWorker_(self, progress_pair):
        current, total = progress_pair
        self.set_progress(current, total)

    def setStatusFromWorker_(self, message):
        text = str(message)
        self.set_status(text)
        if (
            self.geolocations_running
            and self.geolocations_window is not None
            and self.processing_phase_title
        ):
            self.geolocations_window.setTitle_(
                f"{self.processing_phase_title} — {text}"
            )

    def mark_dirty(self, immediate=False):
        self.project_dirty = True
        if self.adventure_autosave_suspended == 0 and self.current_project_file is not None:
            self.schedule_adventure_autosave(immediate=immediate)

    def mark_clean(self):
        self.project_dirty = False
        self.adventure_autosave_error = None
        self.saved_project_payload = self._collect_project_payload()

    def cancel_adventure_autosave(self):
        timer = self.adventure_autosave_timer
        self.adventure_autosave_timer = None
        if timer is not None:
            timer.invalidate()

    def schedule_adventure_autosave(self, immediate=False):
        if self.adventure_autosave_suspended or self.current_project_file is None:
            return
        self.cancel_adventure_autosave()
        delay = 0.0 if immediate else 0.5
        self.adventure_autosave_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay,
            self,
            "performAdventureAutosave:",
            None,
            False,
        )

    def performAdventureAutosave_(self, _timer):
        self.adventure_autosave_timer = None
        self._write_project_configuration(status_prefix="Auto-saved adventure")

    def flush_adventure_autosave(self):
        self.cancel_adventure_autosave()
        if self.current_project_file is None or not self.project_payload_changed():
            return True
        return self._write_project_configuration(status_prefix="Auto-saved adventure")

    def project_payload_changed(self):
        if self.saved_project_payload is None:
            return True
        return self._collect_project_payload() != self.saved_project_payload

    def _project_adv_path(self):
        if self.current_project_file is not None:
            return Path(self.current_project_file).expanduser().resolve(strict=False)
        if self.current_project_dir is None:
            return None
        base_name = project_filename_base(str(self.title_field.stringValue()).strip() or self.current_project_dir.name)
        return self.current_project_dir / f"{base_name}.adv"

    def _control_file_base_name(self):
        path = self._control_file_path()
        if path is not None:
            stem = path.stem
            return stem[:-7] if stem.endswith("-sorted") else stem
        return project_filename_base(self._current_project_name() or "project")

    def _control_file_path(self):
        if self.current_project_dir is None:
            return None
        value = str(self.control_file_field.stringValue()).strip() if hasattr(self, "control_file_field") else ""
        if value:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = self.current_project_dir / path
            return path.resolve(strict=False)
        if self.current_control_file is not None:
            return Path(self.current_control_file).resolve(strict=False)
        base = project_filename_base(self._current_project_name() or self.current_project_dir.name)
        return (self.current_project_dir / f"{base}-sorted.lst").resolve(strict=False)

    def _use_track_order(self):
        return str(self.parameters.get("trackmaps.ordering", "track_number")) == "track_number"

    def _tracks_summary_json_path(self):
        """Return the deterministic summary path without processing the GPX file."""
        trackimages_dir = self._track_images_dir()
        output_base = str(self.track_map_base or self._current_project_name()).strip()
        if trackimages_dir is None or not output_base:
            return None
        return (trackimages_dir / f"{output_base}-summary.json").resolve(strict=False)

    def refresh_control_file_display(self, rows=None, control_lines=None):
        if not hasattr(self, "control_file_summary_label"):
            return
        control_file_path = self._control_file_path()
        if control_file_path is None:
            self.control_file_summary_label.setStringValue_("No project directory selected.")
            self.refresh_section_status_indicators()
            return
        if not control_file_path.exists():
            self.control_file_summary_label.setStringValue_("No control file available yet.")
            self.refresh_section_status_indicators()
            return
        if rows is None:
            try:
                control_lines = control_file_path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                self.control_file_summary_label.setStringValue_(f"Could not read control file: {exc}")
                self.refresh_section_status_indicators()
                return
            rows = [
                parse_slideshow_control_line(line)
                for line in control_lines
                if line.strip()
            ]
        elif control_lines is None:
            control_lines = [serialize_slideshow_control_row(row) for row in rows]
        summary = self._control_file_summary_text(rows, control_file_path)
        summary += self._control_track_map_sync_suffix(
            control_file_path,
            control_lines=control_lines,
        )
        self.control_ready_cache = any(
            str(row.get("type", "")).upper() in {"IMG", "VID"}
            for row in rows
        )
        self.control_file_summary_label.setStringValue_(summary)
        self.refresh_section_status_indicators()

    def _control_file_summary_text(self, rows, control_file_path=None):
        counts = {"IMG": 0, "VID": 0, "TRK": 0, "BEF": 0, "AFT": 0, "LOC": 0, "DAT": 0}
        overview_present = False
        images_with_places = 0
        for row in rows:
            row_type = str(row.get("type", "")).upper()
            if row_type in counts:
                counts[row_type] += 1
            if row_type == "MAP":
                overview_present = True
            if row_type == "IMG" and not is_missing_place_text(str(row.get("place", ""))):
                images_with_places += 1
        changed_text = ""
        if control_file_path is not None:
            try:
                changed = datetime.fromtimestamp(Path(control_file_path).stat().st_mtime)
                changed_text = f", changed {changed.strftime('%d.%m.%Y %H:%M')}"
            except OSError:
                changed_text = ""
        return (
            f"{counts['IMG']} images, {counts['VID']} videos, "
            f"{images_with_places} images with places, "
            f"{counts['TRK']} track maps, {counts['BEF'] + counts['AFT']} adjacent-day maps, "
            f"{counts['LOC']} media maps, {counts['DAT']} dates, "
            f"overview: {'yes' if overview_present else 'no'}{changed_text}"
        )

    def _current_tracks_summary_path_if_available(self):
        if self._uses_media_only_maps():
            return None
        tracks_summary_path = self._tracks_summary_json_path()
        if tracks_summary_path is None or not tracks_summary_path.exists():
            return None
        return tracks_summary_path

    def _control_track_map_sync_status(
        self,
        control_file_path=None,
        tracks_summary_path=None,
        control_lines=None,
    ):
        control_file_path = control_file_path or self._control_file_path()
        tracks_summary_path = tracks_summary_path or self._current_tracks_summary_path_if_available()
        if self.current_project_dir is None or control_file_path is None:
            return analyze_track_map_reference_updates(
                self.current_project_dir or Path.cwd(),
                (self.current_project_dir or Path.cwd()) / ".missing-control-file.lst",
                tracks_summary_path,
                summary_current=False,
            )
        entries = None
        try:
            if control_lines is not None:
                entries = parse_control_file_entries(control_lines)
        except (ValueError, TypeError):
            entries = None
        status = self.track_maps_status_cache
        summary_current = False
        if status is not None:
            summary_current = bool(
                status.get("summary_exists")
                and not status.get("summary_out_of_date")
                and status.get("overview_exists")
                and not status.get("overview_out_of_date")
                and status.get("track_count") == status.get("track_total")
            )
        return analyze_track_map_reference_updates(
            self.current_project_dir,
            control_file_path,
            tracks_summary_path,
            control_entries=entries,
            summary_current=summary_current,
            sort_date_sections_by_tracks=self._use_track_order(),
        )

    def _control_track_map_sync_suffix(self, control_file_path=None, control_lines=None):
        status = self._control_track_map_sync_status(
            control_file_path=control_file_path,
            control_lines=control_lines,
        )
        if status.warning:
            return " | Map Generation is required before the control file can be updated"
        if status.change_count:
            return f" | Control file needs update: {status.change_count} Track Map reference(s)"
        return " | Control file up to date"

    def _selected_gpx_display_value(self):
        return str(self.gpx_field.stringValue()).strip()

    def _gpx_field_basename(self):
        text = str(self.gpx_field.stringValue()).strip()
        if not text:
            default_path = self._default_gpx_path()
            return default_path.name if default_path is not None else ""
        first = text.split(";")[0].strip()
        return Path(first).name

    def _track_images_dir(self):
        if self.current_project_dir is None:
            return None
        return self.current_project_dir / "trackimages"

    def _current_single_gpx_path(self):
        gpx_paths = self._gpx_paths()
        if len(gpx_paths) != 1:
            return None
        return gpx_paths[0].resolve(strict=False)

    def _current_project_name(self):
        return str(self.title_field.stringValue()).strip()

    def _plot_context_for_gpx(self, gpx_path):
        project_name = self._current_project_name()
        trackimages_dir = self._track_images_dir()
        if not project_name or gpx_path is None or trackimages_dir is None:
            return None
        context = self._plot_context_for_values(
            gpx_path,
            project_name,
            trackimages_dir,
            self._use_track_order(),
            self.track_map_base or project_name,
        )
        self._add_media_maps_to_plot_context(context)
        return context

    def _add_media_maps_to_plot_context(
        self,
        context,
        *,
        plan=None,
        parameter_values=None,
        control_path=None,
    ):
        """Attach sidecar-planned media maps to the shared map selection context."""
        if not isinstance(context, dict):
            return
        if plan is None:
            plan = self._project_map_plan()
        if plan is None:
            context["media_map_items"] = []
            return
        values = self.parameters if parameter_values is None else parameter_values
        context["project_map_plan"] = plan
        first_number = len(context.get("tracks", [])) + 1
        output_dir = Path(context["output_dir"])
        items = []
        for offset, spec in enumerate(plan.media_map_specs):
            stale_variants = []
            for layout in ("standard", "time-lapse"):
                output_name = media_map_output_filename(spec["filename"], layout)
                output_path = output_dir / output_name
                metadata = self._plot_metadata_for_image(output_path) if output_path.exists() else None
                if (
                    not output_path.exists()
                    or not self._metadata_parameters_are_current(
                        metadata,
                        self._track_map_parameter_signature(layout, values),
                        layout,
                    )
                    or not isinstance(metadata, dict)
                    or not media_map_metadata_matches_coordinates(
                        metadata,
                        spec["date"],
                        spec["coordinates"],
                    )
                ):
                    stale_variants.append(layout)
            items.append(
                {
                    **spec,
                    "selection_number": first_number + offset,
                    "output_image": str(output_dir / spec["filename"]),
                    "needs_update": bool(stale_variants),
                    "stale_variants": stale_variants,
                    "control_file": str(
                        control_path
                        if control_path is not None
                        else self._control_file_path() or ""
                    ),
                }
            )
        context["media_map_items"] = items

    def _project_map_plan_for_values(
        self,
        project_dir,
        control_path,
        *,
        summary_path,
        use_track_order,
        parameter_values,
        control_file_base,
        infer_gps_from_tracks,
    ):
        """Build a map plan from immutable values, safe for worker threads."""
        if project_dir is None or control_path is None:
            return None
        trackimages_dir = Path(project_dir) / "trackimages"
        options = self._media_map_options_from_values(
            trackimages_dir,
            control_file_base,
            parameter_values,
        )
        options.update(
            {
                "map_layouts": ("standard", "time-lapse"),
                "only_missing_or_stale": True,
                "adventure_render_parameters_by_layout": {
                    "standard": self._track_map_parameter_signature(
                        "standard", parameter_values
                    ),
                    "time-lapse": self._track_map_parameter_signature(
                        "time-lapse", parameter_values
                    ),
                },
            }
        )
        return project_map_plan_from_sidecars(
            project_dir,
            control_path,
            tracks_summary_path=summary_path,
            sort_date_sections_by_tracks=use_track_order,
            media_map_options=options,
            infer_gps_from_tracks=infer_gps_from_tracks,
            place_equivalence_m=parameter_values["locations.reuse_radius_m"],
        )

    def _project_map_plan(self):
        """Build the shared sidecar-based map plan without requiring a control file."""
        project_dir = self._resolve_project_directory(
            allow_create=False,
            update_gpx_field=False,
        )
        control_path = self._control_file_path()
        if project_dir is None or control_path is None:
            return None
        summary_path = None if self._uses_media_only_maps() else self._tracks_summary_json_path()
        if summary_path is not None and not summary_path.is_file():
            summary_path = None
        return self._project_map_plan_for_values(
            project_dir,
            control_path,
            summary_path=summary_path,
            use_track_order=self._use_track_order(),
            parameter_values=dict(self.parameters),
            control_file_base=self._control_file_base_name(),
            infer_gps_from_tracks=summary_path is not None,
        )

    def _plot_context_for_values(
        self,
        gpx_path,
        project_name,
        trackimages_dir,
        use_track_order,
        track_map_base=None,
        track_processing_callback=None,
        parameter_values=None,
    ):
        options = self._plot_common_options(
            project_name,
            trackimages_dir,
            track_map_base or project_name,
            parameter_values=parameter_values,
            use_track_order=use_track_order,
        )
        options.update(
            {
                "plot_tracks": "all",
                "create_output_dir": False,
                "sort_original": use_track_order,
                "sort_date": not use_track_order,
                "track_processing_callback": track_processing_callback,
            }
        )
        return prepare_with_options(str(gpx_path), **options)

    def _regenerate_tracks_summary_json(self, gpx_path):
        project_name = self._current_project_name()
        trackimages_dir = self._track_images_dir()
        if not project_name or gpx_path is None or trackimages_dir is None:
            return None
        trackimages_dir.mkdir(parents=True, exist_ok=True)
        use_track_order = self._use_track_order()
        result = run_gpx_tracks_table_with_options(
            str(gpx_path),
            print_table_output=False,
            plot_overview=False,
            plot_tracks=None,
            output_dir=str(trackimages_dir),
            output_base=self.track_map_base or project_name,
            nojson=False,
            pdf=False,
            verbose=False,
            sort_original=use_track_order,
            sort_date=not use_track_order,
            remove_prefix=self.parameters["trackmaps.remove_name_prefix"],
            gpx_threshold_distance=self.parameters["gpx.minimum_point_spacing_m"],
            gpx_threshold_accuracy=self.parameters["gpx.maximum_accuracy_m"],
            gpx_horizontal_smoothing_distance=self.parameters["gpx.horizontal_smoothing_distance_m"],
            gpx_elevation_smoothing_distance=self.parameters["gpx.elevation_smoothing_distance_m"],
            gpx_maximum_vertical_accuracy=self.parameters["gpx.maximum_vertical_accuracy_m"],
            gpx_maximum_hdop=self.parameters["gpx.maximum_hdop"],
            gpx_maximum_vdop=self.parameters["gpx.maximum_vdop"],
            fallback_walking_speed_kmh=self.parameters["gpx.fallback_walking_speed_kmh"],
            adventure_processing_parameters=self._track_summary_parameter_signature(),
        )
        table_json_path = result.get("table_json_path")
        return Path(table_json_path).resolve(strict=False) if table_json_path else None

    def _track_plot_match_key(self, path_or_name):
        name = Path(str(path_or_name)).name
        match = re.match(r"^0*(\d+)_(.+)$", name)
        if match:
            return f"{int(match.group(1))}_{match.group(2)}".lower()
        return name.lower()

    def _track_plot_exists(self, output_image):
        return self._existing_track_plot_path(output_image) is not None

    def _existing_track_plot_path(self, output_image):
        image_path = Path(output_image).resolve(strict=False)
        if image_path.exists():
            return image_path
        expected_key = self._track_plot_match_key(image_path.name)
        try:
            for candidate in image_path.parent.iterdir():
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() == image_path.suffix.lower()
                    and self._track_plot_match_key(candidate.name) == expected_key
                ):
                    return candidate.resolve(strict=False)
        except OSError:
            return None
        return None

    def _cleanup_obsolete_track_map_files(self, context):
        """Delete numbered track-map files that no longer belong to current GPX tracks."""
        output_dir = Path(context["output_dir"]).resolve(strict=False)
        expected_names = set()
        for track in context.get("tracks", []):
            for key in ("track_plot_image_filename", "track_plot_time_lapse_image_filename"):
                filename = track.get(key)
                if not filename:
                    continue
                expected_names.add(Path(filename).name)
                expected_names.add(Path(filename).with_suffix(".json").name)
        removed = []
        try:
            candidates = list(output_dir.iterdir())
        except OSError:
            return removed
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in {".png", ".json"}:
                continue
            if not re.match(r"^\d+_.+", candidate.name):
                continue
            if candidate.name in expected_names:
                continue
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                pass
        return removed

    def _cleanup_obsolete_media_map_files(self, plan, output_dir):
        """Remove obsolete media maps only from the active Adventure map family."""
        if plan is None:
            return []
        output_dir = Path(output_dir).resolve(strict=False)
        expected = set()
        family_prefixes = set()
        for spec in plan.media_map_specs:
            standard_name = Path(str(spec["filename"])).name
            expected.update(
                {
                    standard_name,
                    Path(standard_name).with_suffix(".json").name,
                    media_map_output_filename(standard_name, "time-lapse"),
                    Path(
                        media_map_output_filename(standard_name, "time-lapse")
                    ).with_suffix(".json").name,
                }
            )
            if "-media-" in standard_name:
                family_prefixes.add(standard_name.split("-media-", 1)[0])
        if not family_prefixes:
            base = re.sub(
                r"[^\w.-]+",
                "_",
                self._control_file_base_name(),
                flags=re.UNICODE,
            ).strip("_")
            if base:
                family_prefixes.add(base)
        removed = []
        try:
            candidates = list(output_dir.iterdir())
        except OSError:
            return removed
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in {".png", ".json"}:
                continue
            if not any(
                candidate.name.startswith(f"{prefix}-media-")
                for prefix in family_prefixes
            ):
                continue
            if candidate.name in expected:
                continue
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                pass
        return removed

    def _track_map_update_numbers(self, gpx_path, context):
        """Return logical map rows where either required variant is missing or stale."""
        update_numbers = set()
        current_fingerprints = self._track_fingerprints_by_table_number(context)
        expected_overview_parameters = self._track_map_parameter_signature("overview")
        try:
            gpx_mtime = Path(gpx_path).stat().st_mtime
        except OSError:
            gpx_mtime = None

        overview_path = Path(context["overview_path"]).resolve(strict=False)
        if not overview_path.exists():
            update_numbers.add(0)
        else:
            overview_metadata = self._plot_metadata_for_image(overview_path)
            if not self._metadata_parameters_are_current(
                overview_metadata,
                expected_overview_parameters,
                "overview",
            ):
                update_numbers.add(0)
            saved_fingerprints = overview_metadata.get("source_track_fingerprints") if isinstance(overview_metadata, dict) else None
            if isinstance(saved_fingerprints, list) and current_fingerprints:
                if [str(item) for item in saved_fingerprints] != list(current_fingerprints.values()):
                    update_numbers.add(0)
            elif gpx_mtime is not None:
                try:
                    if overview_path.stat().st_mtime < gpx_mtime:
                        update_numbers.add(0)
                except OSError:
                    update_numbers.add(0)

        output_dir = Path(context["output_dir"])
        for track in context.get("tracks", []):
            try:
                track_number = int(track["table_number"])
            except (TypeError, ValueError):
                continue
            current_fingerprint = current_fingerprints.get(track_number)
            for layout, filename_key in (
                ("standard", "track_plot_image_filename"),
                ("time-lapse", "track_plot_time_lapse_image_filename"),
            ):
                filename = track.get(filename_key)
                existing = self._existing_track_plot_path(output_dir / str(filename or ""))
                if existing is None:
                    update_numbers.add(track_number)
                    break
                plot_metadata = self._plot_metadata_for_image(existing)
                if not self._metadata_parameters_are_current(
                    plot_metadata,
                    self._track_map_parameter_signature(layout),
                    layout,
                ):
                    update_numbers.add(track_number)
                    break
                saved_fingerprint = (
                    plot_metadata.get("track_fingerprint")
                    if isinstance(plot_metadata, dict)
                    else None
                )
                if saved_fingerprint:
                    if current_fingerprint and str(saved_fingerprint) != current_fingerprint:
                        update_numbers.add(track_number)
                        break
                elif gpx_mtime is not None:
                    try:
                        if Path(existing).stat().st_mtime < gpx_mtime:
                            update_numbers.add(track_number)
                            break
                    except OSError:
                        update_numbers.add(track_number)
                        break
        for item in context.get("media_map_items", []):
            if item.get("needs_update"):
                update_numbers.add(int(item["selection_number"]))
        return sorted(update_numbers)

    def _plot_status_for_gpx(self, gpx_path):
        try:
            context = self._plot_context_for_gpx(gpx_path)
        except (OSError, ValueError, FileNotFoundError, RuntimeError):
            return ""
        if context is None:
            return ""
        status = self._track_maps_status_from_context(gpx_path, context, self._existing_track_plot_path)
        return (
            f" | Maps: Standard {status['standard_count']}/{status['track_total']}, "
            f"Time-Lapse {status['time_lapse_count']}/{status['track_total']} | "
            f"Overview: {'yes' if status['overview_exists'] else 'no'}"
        )

    def _summary_fingerprints_by_track_number(self, table_json_path):
        try:
            payload = read_table_data(table_json_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        tracks = payload.get("tracks") if isinstance(payload, dict) else None
        if not isinstance(tracks, list):
            return {}
        fingerprints = {}
        for item in tracks:
            if not isinstance(item, dict):
                continue
            fingerprint = item.get("track_fingerprint")
            if not fingerprint:
                continue
            try:
                number = int(item.get("nr"))
            except (TypeError, ValueError):
                continue
            fingerprints[number] = str(fingerprint)
        return fingerprints

    def _summary_processing_parameters(self, table_json_path):
        try:
            payload = read_table_data(table_json_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        value = payload.get("adventure_processing_parameters") if isinstance(payload, dict) else None
        return value if isinstance(value, dict) else None

    def _track_fingerprints_by_table_number(self, context):
        fingerprints = {}
        for track in context.get("tracks", []):
            try:
                number = int(track.get("table_number"))
            except (TypeError, ValueError):
                continue
            fingerprint = track.get("track_fingerprint")
            if fingerprint:
                fingerprints[number] = str(fingerprint)
        return fingerprints

    def _plot_metadata_for_image(self, image_path):
        try:
            return read_plot_metadata(Path(image_path).with_suffix(".json"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _track_maps_status_from_context(
        self,
        gpx_path,
        context,
        existing_plot_path_callback,
        parameter_values=None,
    ):
        values = self.parameters if parameter_values is None else parameter_values
        overview_path = Path(context["overview_path"])
        overview_exists = overview_path.exists()
        current_fingerprints = self._track_fingerprints_by_table_number(context)
        summary_fingerprints = self._summary_fingerprints_by_track_number(context["table_json_path"])
        summary_has_fingerprints = bool(summary_fingerprints)
        summary_path = Path(context["table_json_path"])
        summary_exists = summary_path.exists()
        summary_parameters = self._summary_processing_parameters(summary_path) if summary_exists else None
        expected_summary_parameters = self._track_summary_parameter_signature(values)
        tracks = context.get("tracks", [])
        track_total = len(tracks)
        standard_count = 0
        time_lapse_count = 0
        standard_stale_count = 0
        time_lapse_stale_count = 0
        complete_track_count = 0
        missing_metadata_count = 0
        endpoint_place_expected_count = 0
        endpoint_place_complete_count = 0
        try:
            gpx_mtime = Path(gpx_path).stat().st_mtime
        except OSError:
            gpx_mtime = None
        summary_out_of_date = bool(
            current_fingerprints
            and summary_has_fingerprints
            and summary_fingerprints != current_fingerprints
        )
        if summary_exists:
            if summary_parameters is not None:
                summary_out_of_date = summary_out_of_date or summary_parameters != expected_summary_parameters
            else:
                summary_out_of_date = True
        if not summary_has_fingerprints and summary_exists and gpx_mtime is not None:
            try:
                summary_out_of_date = summary_out_of_date or summary_path.stat().st_mtime < gpx_mtime
            except OSError:
                summary_out_of_date = True

        output_dir = Path(context["output_dir"])
        for track in tracks:
            try:
                track_number = int(track["table_number"])
            except (TypeError, ValueError):
                continue
            current_fingerprint = current_fingerprints.get(track_number)
            current_variant_count = 0
            track_variant_metadata = []
            for variant_key, count_key in (
                ("track_plot_image_filename", "standard"),
                ("track_plot_time_lapse_image_filename", "time_lapse"),
            ):
                filename = track.get(variant_key)
                existing = existing_plot_path_callback(output_dir / filename) if filename else None
                if existing is None:
                    continue
                if count_key == "standard":
                    standard_count += 1
                else:
                    time_lapse_count += 1
                plot_metadata = self._plot_metadata_for_image(existing)
                if isinstance(plot_metadata, dict):
                    track_variant_metadata.append(plot_metadata)
                saved_fingerprint = plot_metadata.get("track_fingerprint") if isinstance(plot_metadata, dict) else None
                variant_name = "standard" if count_key == "standard" else "time-lapse"
                stale = not self._metadata_parameters_are_current(
                    plot_metadata,
                    self._track_map_parameter_signature(variant_name, values),
                    variant_name,
                )
                if saved_fingerprint:
                    stale = stale or bool(current_fingerprint and str(saved_fingerprint) != current_fingerprint)
                else:
                    missing_metadata_count += 1
                    if gpx_mtime is not None:
                        try:
                            stale = stale or Path(existing).stat().st_mtime < gpx_mtime
                        except OSError:
                            stale = True
                if stale:
                    if count_key == "standard":
                        standard_stale_count += 1
                    else:
                        time_lapse_stale_count += 1
                else:
                    current_variant_count += 1
            if current_variant_count == 2:
                complete_track_count += 1
            expected_endpoints, complete_endpoints = track_endpoint_place_completeness(
                track,
                track_variant_metadata,
                values["locations.reuse_radius_m"],
            )
            endpoint_place_expected_count += expected_endpoints
            endpoint_place_complete_count += complete_endpoints

        overview_out_of_date = False
        if overview_exists:
            overview_metadata = self._plot_metadata_for_image(overview_path)
            overview_out_of_date = not self._metadata_parameters_are_current(
                overview_metadata,
                self._track_map_parameter_signature("overview", values),
                "overview",
            )
            saved_fingerprints = None
            if isinstance(overview_metadata, dict):
                saved_fingerprints = overview_metadata.get("source_track_fingerprints")
            if isinstance(saved_fingerprints, list) and current_fingerprints:
                if [str(item) for item in saved_fingerprints] != list(current_fingerprints.values()):
                    overview_out_of_date = True
            elif gpx_mtime is not None:
                try:
                    if overview_path.stat().st_mtime < gpx_mtime:
                        overview_out_of_date = True
                except OSError:
                    overview_out_of_date = True

        media_items = context.get("media_map_items", [])
        media_standard_count = 0
        media_time_lapse_count = 0
        media_standard_stale_count = 0
        media_time_lapse_stale_count = 0
        media_stale_count = 0
        media_complete_count = 0
        for item in media_items:
            current_variant_count = 0
            for layout, filename in (
                ("standard", item["filename"]),
                ("time-lapse", time_lapse_track_map_name(item["filename"])),
            ):
                path = output_dir / filename
                if not path.exists():
                    continue
                if layout == "standard":
                    media_standard_count += 1
                else:
                    media_time_lapse_count += 1
                metadata = self._plot_metadata_for_image(path)
                stale = (
                    not self._metadata_parameters_are_current(
                        metadata,
                        self._track_map_parameter_signature(layout, values),
                        layout,
                    )
                    or not isinstance(metadata, dict)
                    or not media_map_metadata_matches_coordinates(
                        metadata,
                        item["date"],
                        item["coordinates"],
                    )
                )
                if stale:
                    media_stale_count += 1
                    if layout == "standard":
                        media_standard_stale_count += 1
                    else:
                        media_time_lapse_stale_count += 1
                else:
                    current_variant_count += 1
            if current_variant_count == 2:
                media_complete_count += 1

        return {
            "overview_exists": overview_exists,
            "overview_out_of_date": overview_out_of_date,
            "track_total": track_total,
            "track_count": complete_track_count,
            "standard_count": standard_count,
            "time_lapse_count": time_lapse_count,
            "standard_stale_count": standard_stale_count,
            "time_lapse_stale_count": time_lapse_stale_count,
            "stale_track_count": standard_stale_count + time_lapse_stale_count,
            "missing_metadata_count": missing_metadata_count,
            "endpoint_place_expected_count": endpoint_place_expected_count,
            "endpoint_place_complete_count": endpoint_place_complete_count,
            "endpoint_place_missing_count": max(
                0,
                endpoint_place_expected_count - endpoint_place_complete_count,
            ),
            "media_map_total": len(media_items),
            "media_map_count": media_complete_count,
            "media_standard_count": media_standard_count,
            "media_time_lapse_count": media_time_lapse_count,
            "media_standard_stale_count": media_standard_stale_count,
            "media_time_lapse_stale_count": media_time_lapse_stale_count,
            "media_stale_count": media_stale_count,
            "summary_exists": summary_exists,
            "summary_out_of_date": summary_out_of_date,
            "out_of_date": bool(
                complete_track_count < track_total
                or not summary_exists
                or overview_out_of_date
                or summary_out_of_date
                or media_complete_count < len(media_items)
            ),
        }

    def _track_maps_status(self):
        route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
        if route_source == "media":
            return self._media_only_track_maps_status()
        gpx_path = self._current_single_gpx_path()
        if gpx_path is None or not gpx_path.is_file():
            return self._media_only_track_maps_status() if route_source == "automatic" else None
        try:
            context = self._plot_context_for_gpx(gpx_path)
        except (OSError, ValueError, FileNotFoundError, RuntimeError):
            return None
        if context is None:
            return None
        return self._track_maps_status_from_context(gpx_path, context, self._existing_track_plot_path)

    def _media_only_track_maps_status(self):
        """Return Map Generation readiness for sidecar-planned media stages."""
        trackimages_dir = self._track_images_dir()
        plan = self._project_map_plan()
        if plan is None or trackimages_dir is None:
            return None
        return self._media_only_track_maps_status_from_plan(
            plan,
            trackimages_dir,
        )

    def _media_only_track_maps_status_from_plan(
        self,
        plan,
        trackimages_dir,
        parameter_values=None,
    ):
        """Calculate media-stage map status from one shared project map plan."""
        values = self.parameters if parameter_values is None else parameter_values
        specs = list(plan.media_map_specs)
        overview_path = trackimages_dir / str(plan.overview_name or "")
        standard_count = 0
        time_lapse_count = 0
        standard_stale_count = 0
        time_lapse_stale_count = 0
        stale_count = 0
        for spec in specs:
            standard_path = trackimages_dir / spec["filename"]
            time_lapse_path = trackimages_dir / media_map_output_filename(spec["filename"], "time-lapse")
            standard_count += int(standard_path.is_file())
            time_lapse_count += int(time_lapse_path.is_file())
            variants_current = True
            for variant, path in (("standard", standard_path), ("time-lapse", time_lapse_path)):
                metadata = self._plot_metadata_for_image(path) if path.is_file() else None
                if (
                    not path.is_file()
                    or not isinstance(metadata, dict)
                    or not media_map_metadata_matches_coordinates(
                        metadata,
                        spec["date"],
                        spec["coordinates"],
                    )
                    or not self._metadata_parameters_are_current(
                        metadata,
                        self._track_map_parameter_signature(variant, values),
                        variant,
                    )
                ):
                    variants_current = False
                    if variant == "standard":
                        standard_stale_count += 1
                    else:
                        time_lapse_stale_count += 1
            if not variants_current:
                stale_count += 1
        total = len(specs)
        overview_exists = bool(overview_path and overview_path.is_file())
        overview_metadata = self._plot_metadata_for_image(overview_path) if overview_exists else None
        overview_points = list(plan.overview_points)
        overview_fingerprint = media_overview_fingerprint(
            [(point["lat"], point["lon"]) for point in overview_points]
        )
        overview_out_of_date = overview_exists and (
            not self._metadata_parameters_are_current(
                overview_metadata,
                self._track_map_parameter_signature("overview", values),
                "overview",
            )
            or not isinstance(overview_metadata, dict)
            or str(overview_metadata.get("media_fingerprint", "")) != overview_fingerprint
        )
        return {
            "media_only": True,
            "overview_exists": overview_exists,
            "overview_out_of_date": overview_out_of_date,
            "summary_exists": False,
            "summary_out_of_date": False,
            "track_total": 0,
            "track_count": 0,
            "standard_count": 0,
            "time_lapse_count": 0,
            "stale_track_count": 0,
            "endpoint_place_expected_count": 0,
            "endpoint_place_complete_count": 0,
            "endpoint_place_missing_count": 0,
            "media_map_total": total,
            "media_standard_count": standard_count,
            "media_time_lapse_count": time_lapse_count,
            "media_standard_stale_count": standard_stale_count,
            "media_time_lapse_stale_count": time_lapse_stale_count,
            "media_stale_count": stale_count,
            "out_of_date": (
                not overview_exists
                or overview_out_of_date
                or total == 0
                or standard_count < total
                or time_lapse_count < total
                or stale_count > 0
            ),
        }

    def _format_track_maps_summary_from_status(self, status):
        if status is None:
            return "No GPX file selected."
        overview_text = "yes" if status["overview_exists"] else "no"
        stale_count = int(status.get("stale_track_count") or 0)
        freshness_parts = []
        if stale_count:
            freshness_parts.append(f"{stale_count} map variant{'s' if stale_count != 1 else ''} not up-to-date")
        if status.get("overview_out_of_date"):
            freshness_parts.append("overview not up-to-date")
        if not status.get("media_only") and not status.get("summary_exists"):
            freshness_parts.append("track summary missing")
        elif not status.get("media_only") and status.get("summary_out_of_date"):
            freshness_parts.append("track summary not up-to-date")
        if status.get("media_stale_count"):
            freshness_parts.append(f"{status['media_stale_count']} media map variant(s) not up-to-date")
        missing_endpoints = int(status.get("endpoint_place_missing_count") or 0)
        if missing_endpoints:
            freshness_parts.append(
                f"{missing_endpoints} track endpoint place name"
                f"{'s' if missing_endpoints != 1 else ''} missing; "
                "run Update Metadata Extraction"
            )
        freshness_text = f" | {', '.join(freshness_parts)}" if freshness_parts else ""
        if status.get("media_only"):
            return (
                f"Media stages Standard: {status.get('media_standard_count', 0)}/{status.get('media_map_total', 0)} | "
                f"Time-Lapse: {status.get('media_time_lapse_count', 0)}/{status.get('media_map_total', 0)} | "
                f"overview: {overview_text}{freshness_text}"
            )
        return (
            f"Standard: {status.get('standard_count', 0)}/{status['track_total']} | "
            f"Time-Lapse: {status.get('time_lapse_count', 0)}/{status['track_total']} | "
            f"Media maps Standard: {status.get('media_standard_count', 0)}/{status.get('media_map_total', 0)} | "
            f"Time-Lapse: {status.get('media_time_lapse_count', 0)}/{status.get('media_map_total', 0)} | "
            f"overview: {overview_text}{freshness_text}"
        )

    def refresh_track_maps_summary(self):
        if not hasattr(self, "track_maps_summary_label"):
            return
        status = self._track_maps_status()
        self.track_maps_status_cache = status
        self.track_endpoint_place_missing_cache = int(
            status.get("endpoint_place_missing_count", 0)
            if isinstance(status, dict)
            else 0
        )
        self.track_maps_summary_label.setStringValue_(self._format_track_maps_summary_from_status(status))
        self.media_metadata_button.setTitle_(
            "Update Metadata Extraction *"
            if self.track_endpoint_place_missing_cache
            else "Update Metadata Extraction"
        )
        self.refresh_media_summary()
        self.refresh_section_status_indicators()

    @staticmethod
    def _format_gpx_summary_from_tracks(gpx_path, tracks):
        if not tracks:
            return f"Tracks: 0 | Name: {gpx_path.name} | Date range: N/A"
        start_candidates = [track.get("start_time") or track.get("time") for track in tracks if (track.get("start_time") or track.get("time")) is not None]
        end_candidates = [track.get("end_time") or track.get("time") for track in tracks if (track.get("end_time") or track.get("time")) is not None]
        start_text = local_datetime_text(min(start_candidates)) if start_candidates else "N/A"
        end_text = local_datetime_text(max(end_candidates)) if end_candidates else "N/A"
        return f"Tracks: {len(tracks)} | Name: {gpx_path.name} | Date range: {start_text} - {end_text}"

    def _format_gpx_summary(self, gpx_path):
        tracks = parse_gpx_file(
            str(gpx_path),
            self.parameters["trackmaps.remove_name_prefix"],
            self.parameters["gpx.minimum_point_spacing_m"],
            self.parameters["gpx.maximum_accuracy_m"],
            False,
            self.parameters["gpx.horizontal_smoothing_distance_m"],
            self.parameters["gpx.elevation_smoothing_distance_m"],
            self.parameters["gpx.maximum_vertical_accuracy_m"],
            self.parameters["gpx.maximum_hdop"],
            self.parameters["gpx.maximum_vdop"],
        )
        return self._format_gpx_summary_from_tracks(gpx_path, tracks)

    def update_gpx_summary(self, gpx_path):
        self.gpx_summary_label.setStringValue_(self._format_gpx_summary(gpx_path))
        self.refresh_track_maps_summary()
        self.refresh_section_status_indicators()

    def clear_gpx_summary(self):
        self.gpx_summary_label.setStringValue_(default_gpx_summary_text())
        if hasattr(self, "track_maps_summary_label"):
            if self._uses_media_only_maps():
                self.refresh_track_maps_summary()
                return
            self.track_maps_summary_label.setStringValue_("No GPX file selected.")
        self.refresh_section_status_indicators()

    def media_counts(self):
        project_dir = self.current_project_dir
        if project_dir is None:
            return 0, 0
        if not project_dir.exists() or not project_dir.is_dir():
            return 0, 0
        photo_count = 0
        video_count = 0
        video_extensions = MEDIA_EXTENSIONS - IMAGE_EXTENSIONS
        for path in project_dir.iterdir():
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                photo_count += 1
            elif suffix in video_extensions:
                video_count += 1
        return photo_count, video_count

    def refresh_media_summary(self):
        project_dir = self.current_project_dir
        if project_dir is None:
            self.media_summary_label.setStringValue_("No project directory selected.")
            self.refresh_section_status_indicators()
            return
        photo_count, video_count = self.media_counts()
        self.media_counts_cache = (photo_count, video_count)
        photo_text = "photo" if photo_count == 1 else "photos"
        video_text = "video clip" if video_count == 1 else "video clips"
        summary = f"{photo_count} {photo_text}, {video_count} {video_text}"
        missing_endpoints = int(self.track_endpoint_place_missing_cache or 0)
        if missing_endpoints:
            summary += (
                f" | {missing_endpoints} track endpoint place name"
                f"{'s' if missing_endpoints != 1 else ''} missing; "
                "run Update Metadata Extraction"
            )
        self.media_summary_label.setStringValue_(summary)
        self.refresh_section_status_indicators()

    def _has_existing_gpx_file(self):
        return any(path.exists() and path.is_file() and path.suffix.lower() == ".gpx" for path in self._gpx_paths())

    def _control_file_has_media(self):
        control_file_path = self._control_file_path()
        if control_file_path is None or not control_file_path.exists():
            return False
        try:
            rows = [
                parse_slideshow_control_line(line)
                for line in control_file_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError:
            return False
        return any(str(row.get("type", "")).upper() in {"IMG", "VID"} for row in rows)

    def _workflow_readiness(self):
        project_ready = bool(
            self.current_project_dir is not None
            and self.current_project_dir.exists()
            and self.current_project_dir.is_dir()
        )
        adventure_ready = bool(
            project_ready
            and self.current_project_file is not None
            and Path(self.current_project_file).exists()
            and str(self.title_field.stringValue()).strip()
        )
        gpx_ready = self.gpx_ready_cache if self.gpx_ready_cache is not None else self._has_existing_gpx_file()
        if self.media_counts_cache is None:
            self.media_counts_cache = self.media_counts()
        photo_count, video_count = self.media_counts_cache
        control_ready = self.control_ready_cache if self.control_ready_cache is not None else self._control_file_has_media()
        track_maps_status = self.track_maps_status_cache
        if track_maps_status and track_maps_status.get("media_only"):
            media_total = int(track_maps_status.get("media_map_total", 0))
            track_maps_ready = bool(
                track_maps_status.get("overview_exists")
                and media_total > 0
                and int(track_maps_status.get("media_standard_count", 0)) == media_total
                and int(track_maps_status.get("media_time_lapse_count", 0)) == media_total
                and not track_maps_status.get("overview_out_of_date")
                and not track_maps_status.get("media_stale_count")
            )
        else:
            track_total = int(track_maps_status.get("track_total", 0)) if track_maps_status else 0
            media_total = int(track_maps_status.get("media_map_total", 0)) if track_maps_status else 0
            track_maps_ready = bool(
                track_maps_status
                and track_maps_status["overview_exists"]
                and track_maps_status.get("summary_exists")
                and track_total > 0
                and int(track_maps_status.get("standard_count", 0)) == track_total
                and int(track_maps_status.get("time_lapse_count", 0)) == track_total
                and int(track_maps_status.get("media_standard_count", 0)) == media_total
                and int(track_maps_status.get("media_time_lapse_count", 0)) == media_total
                and not track_maps_status.get("overview_out_of_date")
                and not track_maps_status.get("summary_out_of_date")
                and not track_maps_status.get("stale_track_count")
                and not track_maps_status.get("media_stale_count")
            )
        route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
        source_confirmed = bool(self.workflow_assistant_state.get("journey_source_confirmed", False))
        gpx_ready = source_confirmed and (
            route_source == "media"
            or (route_source in {"automatic", "gpx"} and bool(gpx_ready))
        )
        media_ready = bool(self.workflow_assistant_state.get("media_confirmed", False)) and (
            photo_count + video_count
        ) > 0
        return {
            "project": project_ready,
            "adventure": adventure_ready,
            "gpx": bool(gpx_ready),
            "track_maps": track_maps_ready,
            "media": media_ready,
            "metadata": bool(
                self.workflow_assistant_state.get("metadata_prepared", False)
                and not int(self.track_endpoint_place_missing_cache or 0)
            ),
            "control": bool(control_ready),
        }

    def refresh_section_status_indicators(self):
        if not hasattr(self, "adventure_status_checkbox"):
            return
        readiness = self._workflow_readiness()
        project_ready = readiness["project"]
        gpx_ready = readiness["gpx"]
        media_ready = readiness["media"]
        control_ready = readiness["control"]
        track_maps_ready = readiness["track_maps"]
        media_route = self._uses_media_only_maps()
        self._set_section_status_checkbox(
            self.adventure_status_checkbox,
            project_ready,
            "Adventure complete: project directory exists." if project_ready else "Adventure incomplete: create or select a project directory.",
        )
        self._set_section_status_checkbox(
            self.gpx_status_checkbox,
            gpx_ready,
            (
                "GPX optional: this Adventure builds stages from media locations."
                if media_route
                else ("GPX complete: at least one GPX track file exists." if gpx_ready else "GPX incomplete: select or create a GPX file.")
            ),
        )
        if hasattr(self, "track_maps_status_checkbox"):
            self._set_section_status_checkbox(
                self.track_maps_status_checkbox,
                track_maps_ready,
                (
            "Map Generation complete: overview and all map variants are up-to-date."
                    if track_maps_ready
                    else "Map Generation incomplete or not up-to-date: generate or update the required maps."
                ),
            )
        self._set_section_status_checkbox(
            self.media_status_checkbox,
            media_ready,
            "Photos complete: at least one photo or video is available." if media_ready else "Photos incomplete: import at least one photo or video.",
        )
        self._set_section_status_checkbox(
            self.control_file_status_checkbox,
            control_ready,
            "Control file complete: file exists and contains at least one photo or video." if control_ready else "Control file incomplete: create a slide-show control file with at least one photo or video.",
        )
        self.refresh_workflow_assistant(readiness)

    def _assistant_operation_active(self):
        return bool(
            self.project_status_pending
            or self.geolocations_running
            or (self.plot_creation_thread is not None and self.plot_creation_thread.is_alive())
        )

    def _assistant_stage_details(self, stage):
        details = {
            "project": (
                self.project_dir_field,
                "Choose the folder that will hold this Adventure and all files created for it.",
            ),
            "adventure": (
                self.title_field,
                "Enter an Adventure name and press Return or leave the field to create the Adventure.",
            ),
            "gpx": (
                self.gpx_field,
                "Confirm the journey source. If you have a GPS track of your adventure, provide a GPX file here; otherwise the slide show will use the GPS information from your photos. If you select multiple GPX files, they will automatically be joined into one file for your entire adventure. Individual tracks can be rearranged, split, joined, inspected, plotted, and edited in the GPX Editor.",
            ),
            "media": (
                self.media_import_button,
                "Confirm the photos and videos already in this folder, or import more. Existing files are not copied again.",
            ),
            "metadata": (
                self.media_metadata_button,
                "Prepare missing, invalid, legacy, or changed media metadata and complete missing track start/destination place names. The visible Add place names option will be used automatically.",
            ),
            "track_maps": (
                self.gpx_make_plots_button,
                "Generate the missing or outdated overview and both Standard and Time-Lapse maps. This normally runs automatically after metadata preparation.",
            ),
            "control": (
                self.control_file_create_button,
                "Create the slide-show control file. Afterwards, use Edit to change the order of photos and videos.",
            ),
            "slideshow": (
                self.slideshow_start_button,
                "Start the slide show for the first time. You can later continue from its last saved position.",
            ),
        }
        return details.get(stage, (None, ""))

    def _assistant_detected_gpx_paths(self):
        if self.current_project_dir is None or not self.current_project_dir.is_dir():
            return []
        return sorted(self.current_project_dir.glob("*.gpx"), key=lambda path: path.name.casefold())

    def _assistant_stage_content(self, stage, message):
        if stage == "gpx":
            detected_paths = self._assistant_detected_gpx_paths()
            route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
            if route_source == "media":
                selected_choice = "no_gpx"
            elif len(detected_paths) == 1:
                selected_choice = "use_detected_gpx"
            elif len(detected_paths) > 1:
                selected_choice = "join_detected_gpx"
            else:
                selected_choice = "choose_other_gpx"
            return {
                "message": message,
                "choices": detected_gpx_choices(detected_paths),
                "selected_choice": selected_choice,
                "actions": (("apply_gpx_choice", "Continue"),),
            }
        if stage == "media":
            photo_count, video_count = self.media_counts_cache or self.media_counts()
            if photo_count + video_count:
                return {
                    "message": f"{photo_count} photo(s) and {video_count} video(s) are already in this folder. Use all of them for this Adventure?",
                    "choices": (
                        ("use_existing_media", "use existing media"),
                        ("use_existing_and_import_more", "use existing media and import more"),
                        ("media_not_yet", "not yet"),
                    ),
                    "selected_choice": "use_existing_media",
                    "actions": (("apply_media_choice", "Continue"),),
                }
            return {
                "message": message,
                "actions": (("import_media", "Import media"),),
            }
        if stage == "metadata":
            return {
                "message": message,
                "actions": (("prepare_metadata", "Continue"),),
            }
        if stage == "track_maps":
            return {
                "message": message,
                "actions": (("update_track_maps", "Generate and Update Maps"),),
            }
        if stage == "control":
            return {
                "message": message,
                "actions": (("create_control_assets", "Continue"),),
            }
        if stage == "slideshow":
            return {
                "message": message,
                "actions": (
                    ("start_slideshow", "Start Slide Show"),
                    ("slideshow_later", "Later"),
                ),
            }
        return {"message": message}

    def refresh_workflow_assistant(self, readiness=None):
        if not hasattr(self, "workflow_assistant_bubble"):
            return
        enabled = bool(self.workflow_assistant_state.get("enabled", True))
        self.assistant_checkbox.setState_(NSControlStateValueOn if enabled else NSControlStateValueOff)
        if not enabled or self._assistant_operation_active():
            self.workflow_assistant_stage = None
            self.workflow_assistant_bubble.setHidden_(True)
            return
        readiness = readiness or self._workflow_readiness()
        stage = next_assistant_stage(readiness, self.workflow_assistant_state)
        self.workflow_assistant_stage = stage
        if stage is None:
            self.workflow_assistant_bubble.setHidden_(True)
            return
        target, message = self._assistant_stage_details(stage)
        if target is None:
            self.workflow_assistant_bubble.setHidden_(True)
            return
        content = self._assistant_stage_content(stage, message)
        container = (0.0, 0.0, self.root_view.bounds().size.width, self.root_view.bounds().size.height)
        converted_target = self.root_view.convertRect_fromView_(target.bounds(), target)
        target_rect = (
            converted_target.origin.x,
            converted_target.origin.y,
            converted_target.size.width,
            converted_target.size.height,
        )
        size = assistant_bubble_size(content)
        stage_changed = stage != self.workflow_assistant_bubble.position_stage
        self.workflow_assistant_bubble.setAssistantStage_(stage)
        self.workflow_assistant_bubble.setTargetRect_(target_rect)
        if self.workflow_assistant_bubble.hasUserPosition():
            geometry = relocated_bubble_geometry(
                container,
                target_rect,
                size,
                self.workflow_assistant_bubble.user_origin,
            )
        else:
            geometry = bubble_geometry(container, target_rect, size=size)
        self.workflow_assistant_bubble.setContent_geometry_(content, geometry)
        self.workflow_assistant_bubble.setHidden_(False)
        if stage_changed and assistant_stage_uses_text_input(stage):
            self.performSelector_withObject_afterDelay_(
                "focusWorkflowAssistantTarget:",
                stage,
                0.0,
            )

    def focusWorkflowAssistantTarget_(self, stage):
        stage = str(stage or "")
        if stage != self.workflow_assistant_stage or not assistant_stage_uses_text_input(stage):
            return
        target, _message = self._assistant_stage_details(stage)
        if target is not None and not self.workflow_assistant_bubble.isHidden():
            self.window.makeFirstResponder_(target)

    @objc.IBAction
    def workflowAssistantAction_(self, sender):
        tag = int(sender.tag())
        action = self.workflow_assistant_bubble.action_ids[tag] if tag < len(self.workflow_assistant_bubble.action_ids) else ""
        if action in {"apply_gpx_choice", "apply_media_choice"}:
            action = self.workflow_assistant_bubble.selectedActionId()
        self._perform_workflow_assistant_action(action)

    def _perform_workflow_assistant_action(self, action):
        if action == "use_detected_gpx":
            detected_paths = self._assistant_detected_gpx_paths()
            if len(detected_paths) != 1:
                self.set_status("The detected GPX files changed; choose the journey source again.")
            else:
                self._accept_existing_project_gpx(detected_paths[0])
        elif action == "join_detected_gpx":
            detected_paths = self._assistant_detected_gpx_paths()
            if len(detected_paths) < 2:
                self.set_status("The detected GPX files changed; choose the journey source again.")
            else:
                default_path = self._default_gpx_path()
                if default_path is not None:
                    self._set_gpx_field_value(default_path.name, manual=True)
                    self._open_gpx_editor(
                        input_paths=detected_paths,
                        output_path=default_path.resolve(strict=False),
                    )
        elif action == "choose_other_gpx":
            self.selectGPXFile_(None)
        elif action == "no_gpx":
            self._set_no_gpx_mode(True)
        elif action == "use_existing_media":
            self.workflow_assistant_state["media_confirmed"] = True
            self.mark_dirty(immediate=True)
            self._start_automatic_media_metadata()
        elif action == "use_existing_and_import_more":
            self.workflow_assistant_state["media_confirmed"] = True
            self.mark_dirty(immediate=True)
            self._begin_media_import(prepare_existing_on_cancel=True)
        elif action == "import_media":
            self._begin_media_import(prepare_existing_on_cancel=False)
        elif action == "media_not_yet":
            self.set_status("Existing media was not accepted yet; the assistant will ask again later.")
        elif action == "prepare_metadata":
            self._start_assistant_metadata_preparation(
                bool(self.parameters.get("locations.add_place_names", True))
            )
        elif action == "create_control_assets":
            self._start_assistant_control_assets_creation()
        elif action == "update_track_maps":
            self.makePlots_(None)
        elif action in {"start_slideshow", "start_timelapse", "start_standard"}:
            self.startSelectedSlideShow_(None)
        elif action == "slideshow_later":
            self.workflow_assistant_state["slideshow_started"] = True
            self.mark_dirty(immediate=True)
        self.refresh_section_status_indicators()

    def layout_workflow_assistant(self):
        if hasattr(self, "workflow_assistant_bubble") and not self.workflow_assistant_bubble.isHidden():
            self.refresh_workflow_assistant()

    @objc.IBAction
    def toggleWorkflowAssistant_(self, sender):
        self.workflow_assistant_state["enabled"] = int(sender.state()) == NSControlStateValueOn
        self.mark_dirty(immediate=True)
        self.refresh_workflow_assistant()

    @objc.IBAction
    def disableWorkflowAssistant_(self, _sender):
        self.workflow_assistant_state["enabled"] = False
        self.assistant_checkbox.setState_(NSControlStateValueOff)
        self.mark_dirty(immediate=True)
        self.refresh_workflow_assistant()

    def _collect_project_payload(self):
        return {
            "adventure_format_version": ADVENTURE_FORMAT_VERSION,
            "project_name": str(self.title_field.stringValue()).strip(),
            "project_directory": str(self.current_project_dir) if self.current_project_dir else "",
            "description": self._description_string(),
            "title_image": self._stored_optional_project_path(self.title_image),
            "gpx_file": self._gpx_field_basename(),
            "control_file": self._control_file_path().name if self._control_file_path() is not None else "",
            "track_map_base": self.track_map_base or project_filename_base(self._current_project_name()),
            "last_picture_import_directory": str(self.last_picture_import_directory) if self.last_picture_import_directory else "",
            "parameters": parameter_payload(self.parameters),
            "slideshow_resume_position": self.slideshow_resume_position,
            "music_source": self._stored_optional_project_path(self.music_source),
            "music_playlist": self._stored_optional_project_path(self.music_playlist),
            "workflow_assistant": dict(self.workflow_assistant_state),
        }

    def _stored_optional_project_path(self, value):
        """Store project-contained optional paths relatively and external paths absolutely."""
        if value is None:
            return ""
        path = Path(value).expanduser().resolve(strict=False)
        if self.current_project_dir is not None:
            project_dir = self.current_project_dir.resolve(strict=False)
            try:
                return str(path.relative_to(project_dir))
            except ValueError:
                pass
        return str(path)

    def _resolve_optional_project_path(self, value):
        text = str(value or "").strip()
        if not text:
            return None
        path = Path(text).expanduser()
        if not path.is_absolute() and self.current_project_dir is not None:
            path = self.current_project_dir / path
        return path.resolve(strict=False)

    def _ensure_project_audio_directory(self, show_errors=True):
        """Create and select the fixed audio directory for the active project."""
        if self.current_project_dir is None:
            return None
        try:
            audio_dir = ensure_project_audio_directory(self.current_project_dir)
        except OSError as exc:
            if show_errors:
                show_alert("Could not create the project audio folder.", str(exc))
            return None
        self.music_source = audio_dir
        return audio_dir

    def _set_music_playlist(self, playlist, *, mark_dirty=False):
        """Select one playlist from the fixed audio folder, or use alphabetical order."""
        audio_dir = self._ensure_project_audio_directory(show_errors=False)
        candidate = (
            Path(playlist).expanduser().resolve(strict=False)
            if playlist is not None
            else None
        )
        if candidate is not None and not playlist_belongs_to_audio_directory(
            candidate,
            audio_dir,
        ):
            candidate = None
        changed = candidate != self.music_playlist
        self.music_playlist = candidate
        self.music_field.setStringValue_(
            str(candidate) if candidate is not None else ""
        )
        self._refresh_music_summary()
        if mark_dirty and changed:
            self.mark_dirty(immediate=True)
        return candidate

    def _ensure_default_music_playlist(self, show_errors=True):
        """Return the Adventure-named default playlist, creating it if absent."""
        if self.current_project_dir is None:
            return None
        adventure_name = str(self.committed_adventure_name or "").strip()
        if not adventure_name and self.current_project_file is not None:
            adventure_name = self.current_project_file.stem
        if not adventure_name:
            return None
        try:
            return ensure_default_project_playlist(
                self.current_project_dir,
                adventure_name,
            )
        except OSError as exc:
            if show_errors:
                show_alert("Could not create the default music playlist.", str(exc))
            return None

    def _preferred_music_playlist(self, source=None):
        source_path = (
            Path(source or self.music_source).expanduser().resolve(strict=False)
            if (source or self.music_source)
            else self._ensure_project_audio_directory(show_errors=False)
        )
        if source_path is None or not source_path.is_dir():
            return None
        name = project_filename_base(self._current_project_name())
        candidate = source_path / f"{name}.playlist"
        return candidate if candidate.is_file() else None

    def _refresh_music_summary(self):
        if not hasattr(self, "music_summary_label"):
            return
        source = self._ensure_project_audio_directory(show_errors=False)
        if source is None:
            self.music_summary_label.setStringValue_("No project audio folder available.")
            self.music_playlist_select_button.setEnabled_(False)
            self.music_choose_button.setEnabled_(False)
            self.music_playlist_button.setEnabled_(False)
            self.music_playlist_update_button.setEnabled_(False)
            self.music_playlist_edit_button.setEnabled_(False)
            return
        source = Path(source).expanduser().resolve(strict=False)
        self.music_playlist_select_button.setEnabled_(True)
        self.music_choose_button.setEnabled_(True)
        files = audio_files_in_directory(source)
        self.music_playlist_button.setEnabled_(bool(files))
        self.music_playlist_update_button.setEnabled_(bool(files))
        album_count = len(album_directories(files, source))
        playlist = self.music_playlist
        if playlist is None or not Path(playlist).is_file():
            playlist = self._preferred_music_playlist(source)
            if playlist is None:
                playlist = self._ensure_default_music_playlist(show_errors=False)
            self.music_playlist = playlist
            self.music_field.setStringValue_(
                str(playlist) if playlist is not None else ""
            )
        playlist_count = len(list(source.glob("*.playlist")))
        if playlist is not None and Path(playlist).is_file():
            parsed = load_audio_playlist(source, Path(playlist))
            details = [
                f"{len(files)} audio",
                f"{album_count} albums",
                f"{playlist_count} playlists",
                Path(playlist).name,
            ]
            if parsed.unlisted_files:
                details.append(f"{len(parsed.unlisted_files)} unlisted")
            if parsed.warnings:
                details.append(f"{len(parsed.warnings)} warnings")
            self.music_summary_label.setStringValue_(", ".join(details))
        else:
            self.music_summary_label.setStringValue_(
                f"{len(files)} audio, {album_count} albums, "
                f"{playlist_count} playlists, alphabetical order. "
                "Copy audio files and album folders into the audio folder."
            )
        self.music_playlist_edit_button.setEnabled_(bool(files) or bool(playlist and Path(playlist).is_file()))

    def _confirm_shared_asset_change(self, field, value, label):
        if self.current_project_dir is None or self.current_project_file is None or not value:
            return True
        key = (field, str(value))
        if key in self.shared_asset_warning_accepted:
            return True
        others = shared_references(
            self.current_project_dir,
            Path(self.current_project_file).resolve(strict=False),
            field,
            str(value),
        )
        if not others:
            return True
        names = "\n".join(f"- {path.name}" for path in others)
        if not confirm_alert(
            f"Shared {label}",
            f"This {label} is also used by:\n{names}\n\nChanges will affect those Adventures too.",
            "Continue",
            "Cancel",
        ):
            return False
        self.shared_asset_warning_accepted.add(key)
        return True

    def _refresh_project_file_menus(self):
        if self.current_project_dir is None or not self.current_project_dir.is_dir():
            return
        self.project_file_menu_refreshing = True
        try:
            records, errors = discover_adventures(self.current_project_dir)
            self.adventure_records = records
            self.adventure_combo_paths = [record.path for record in records]
            current_name = str(self.title_field.stringValue()).strip()
            self.title_field.removeAllItems()
            self.title_field.addItemsWithObjectValues_([record.project_name for record in records])
            self.title_field.setStringValue_(current_name)

            gpx_value = str(self.gpx_field.stringValue()).strip()
            self.gpx_field.removeAllItems()
            self.gpx_field.addItemsWithObjectValues_(project_file_names(self.current_project_dir, ".gpx"))
            self.gpx_field.setStringValue_(gpx_value)

            control_value = str(self.control_file_field.stringValue()).strip()
            self.control_file_field.removeAllItems()
            self.control_file_field.addItemsWithObjectValues_(project_file_names(self.current_project_dir, ".lst"))
            self.control_file_field.setStringValue_(control_value)
        finally:
            self.project_file_menu_refreshing = False
        if errors:
            show_alert("Some Adventure files could not be used.", "\n".join(errors))

    def _find_adventure_file_in_directory(self, project_dir):
        records, _errors = discover_adventures(project_dir)
        return records[0].path if records else None

    def _auto_load_adventure_from_directory(self, project_dir):
        adv_path = self._find_adventure_file_in_directory(project_dir)
        if adv_path is None:
            return False
        loaded = self.load_project_configuration(adv_path)
        if loaded:
            self.skip_next_project_dir_dirty = True
        return loaded

    def _restore_project_directory_field(self, previous_directory):
        if previous_directory is None:
            self.project_dir_field.setStringValue_("")
        else:
            self.project_dir_field.setStringValue_(str(previous_directory))

    def _activate_project_directory(self, project_dir, allow_create=True):
        candidate = Path(project_dir).expanduser()
        if not candidate.is_absolute():
            candidate = (self.current_project_dir or self.base_dir) / candidate
        candidate = candidate.resolve(strict=False)
        previous_directory = self.current_project_dir
        if (
            previous_directory is not None
            and previous_directory.resolve(strict=False) == candidate
            and self.current_project_file is not None
        ):
            self.project_dir_field.setStringValue_(str(candidate))
            self._ensure_project_audio_directory(show_errors=True)
            self._refresh_music_summary()
            return True
        if candidate.exists() and not candidate.is_dir():
            show_alert("The selected path is not a directory.", str(candidate))
            self._restore_project_directory_field(previous_directory)
            return False
        if not candidate.exists() and not allow_create:
            show_alert("Directory does not exist!", str(candidate))
            self._restore_project_directory_field(previous_directory)
            return False

        if not self.flush_adventure_autosave():
            self._restore_project_directory_field(previous_directory)
            return False

        self.project_dir_field.setStringValue_(str(candidate))
        self.directory_activation_in_progress = True
        try:
            resolved = self._resolve_project_directory(allow_create=allow_create)
        finally:
            self.directory_activation_in_progress = False
        if resolved is None:
            self._restore_project_directory_field(previous_directory)
            return False
        records, _errors = discover_adventures(resolved)
        if records:
            adventure_file = records[0].path
            loaded = self.load_project_configuration(adventure_file, flush_current=False)
            if not loaded:
                self.current_project_dir = previous_directory
                self._restore_project_directory_field(previous_directory)
                if previous_directory is not None:
                    try:
                        os.chdir(previous_directory)
                    except OSError:
                        pass
            return loaded

        self.adventure_autosave_suspended += 1
        try:
            self.parameters = default_parameters()
            self._sync_legacy_parameter_controls()
            self.current_project_file = None
            suggested = project_filename_base(resolved.name)
            self.title_field.setStringValue_(suggested)
            self.committed_adventure_name = ""
            self.track_map_base = suggested
            self._set_gpx_field_value(f"{suggested}.gpx", manual=False)
            self.current_control_file = resolved / f"{suggested}-sorted.lst"
            self.control_file_field.setStringValue_(self.current_control_file.name)
            self.music_source = self._ensure_project_audio_directory(
                show_errors=True
            )
            self._set_music_playlist(
                self._ensure_default_music_playlist(show_errors=True),
                mark_dirty=False,
            )
            self._refresh_music_summary()
            self.description_text.setString_("")
            self.title_image = None
            self.title_image_field.setStringValue_("")
            self.slideshow_resume_position = None
            assistant_enabled = bool(self.workflow_assistant_state.get("enabled", True))
            self.workflow_assistant_state = normalize_assistant_state(None, existing_adventure=False)
            self.workflow_assistant_state["enabled"] = assistant_enabled
            self.assistant_place_names_pending_save = False
        finally:
            self.adventure_autosave_suspended = max(0, self.adventure_autosave_suspended - 1)
        self.saved_project_payload = None
        self._refresh_project_file_menus()
        self._remember_recent_adventure(resolved)
        self.refresh_section_status_indicators()
        self.set_status("Enter or confirm the Adventure name to create it.")
        return True

    def _update_loaded_project_fields(self, data):
        self.adventure_autosave_suspended += 1
        try:
            self._update_loaded_project_fields_without_autosave(data)
        finally:
            self.adventure_autosave_suspended = max(0, self.adventure_autosave_suspended - 1)

    def _update_loaded_project_fields_without_autosave(self, data):
        project_directory = str(data.get("project_directory", "") or "")
        self.project_dir_field.setStringValue_(project_directory)
        self.current_project_dir = Path(project_directory).expanduser().resolve(strict=False) if project_directory else None
        if self.current_project_dir is not None:
            try:
                os.chdir(self.current_project_dir)
            except OSError as exc:
                show_alert("Could not make the loaded project directory the current working directory.", str(exc))

        self.title_field.setStringValue_(str(data.get("project_name", "") or ""))
        self.committed_adventure_name = str(data.get("project_name", "") or "")
        self.track_map_base = str(data["track_map_base"])
        self.description_text.setString_(str(data.get("description", "") or ""))
        self.title_image = self._resolve_optional_project_path(
            data.get("title_image")
        )
        if self.title_image is not None and not self.title_image.is_file():
            self.title_image = None
        self.title_image_field.setStringValue_(
            self._stored_optional_project_path(self.title_image)
            if self.title_image is not None
            else ""
        )
        raw_parameters = data["parameters"]
        raw_values = (
            raw_parameters.get("values", {})
            if isinstance(raw_parameters, dict) and isinstance(raw_parameters.get("values"), dict)
            else raw_parameters
        )
        if isinstance(raw_values, dict) and "audio.enabled" not in raw_values:
            migrated_values = dict(raw_values)
            migrated_values["audio.enabled"] = bool(str(data.get("music_source", "") or "").strip())
            raw_parameters = {
                "version": raw_parameters.get("version", 1)
                if isinstance(raw_parameters, dict)
                else 1,
                "values": migrated_values,
            }
        self.parameters = normalize_parameters(raw_parameters)
        self._sync_legacy_parameter_controls()
        loaded_gpx_value = str(data["gpx_file"]).strip()
        loaded_gpx_path = self.current_project_dir / loaded_gpx_value
        self._set_gpx_field_value(loaded_gpx_value, manual=not self._is_default_gpx_path(loaded_gpx_path))
        self.current_control_file = (self.current_project_dir / str(data["control_file"])).resolve(strict=False)
        self.control_file_field.setStringValue_(self.current_control_file.name)
        last_import_directory = str(data.get("last_picture_import_directory", "") or "")
        self.last_picture_import_directory = (
            Path(last_import_directory).expanduser().resolve(strict=False) if last_import_directory else None
        )
        loaded_resume_position = data.get("slideshow_resume_position")
        self.slideshow_resume_position = loaded_resume_position if isinstance(loaded_resume_position, dict) else None
        self.music_source = self._ensure_project_audio_directory(show_errors=True)
        loaded_playlist = self._resolve_optional_project_path(
            data.get("music_playlist")
        )
        if not playlist_belongs_to_audio_directory(
            loaded_playlist,
            self.music_source,
        ):
            loaded_playlist = self._preferred_music_playlist(self.music_source)
        if loaded_playlist is None:
            loaded_playlist = self._ensure_default_music_playlist(show_errors=True)
        self._set_music_playlist(loaded_playlist, mark_dirty=False)
        self.workflow_assistant_state = normalize_assistant_state(
            data.get("workflow_assistant"),
            existing_adventure=True,
        )
        route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
        if route_source == "media":
            self.workflow_assistant_state["journey_source_confirmed"] = True
        elif loaded_gpx_path.is_file():
            self.workflow_assistant_state["journey_source_confirmed"] = True
        self.assistant_place_names_pending_save = False
        self._update_slideshow_continue_button()
        if self.parameter_editor_controller is not None:
            self.parameter_editor_controller.update_values(self.parameters)

    def _refresh_loaded_gpx_summary(self):
        self.start_async_project_status_refresh("loaded adventure")

    def _set_project_status_pending(self):
        self.project_status_pending = True
        self.media_counts_cache = (0, 0)
        self.control_ready_cache = False
        self.track_maps_status_cache = None
        self.track_endpoint_place_missing_cache = 0
        self.gpx_ready_cache = False
        if hasattr(self, "gpx_summary_label"):
            self.gpx_summary_label.setStringValue_("Updating GPX summary...")
        if hasattr(self, "track_maps_summary_label"):
            self.track_maps_summary_label.setStringValue_("Updating map status...")
        if hasattr(self, "media_summary_label"):
            self.media_summary_label.setStringValue_("Updating media count...")
        if hasattr(self, "control_file_summary_label"):
            self.control_file_summary_label.setStringValue_("Updating slide show control file status...")
        self.refresh_section_status_indicators()

    def _wait_for_project_status_processing(self, generation):
        """Wait cooperatively until GPX status processing may continue."""
        while True:
            if int(generation) != self.status_refresh_generation:
                raise ProjectStatusRefreshSuperseded()
            if self.project_status_processing_gate.wait(0.05):
                if int(generation) != self.status_refresh_generation:
                    raise ProjectStatusRefreshSuperseded()
                return

    def _pause_project_status_for_editor_startup(self):
        self.project_status_processing_gate.clear()

    def _resume_project_status_after_editor_startup(self):
        self.project_status_processing_gate.set()

    def _async_gpx_paths_from_text(self, gpx_text, project_dir):
        text = str(gpx_text or "").strip()
        if not text:
            default_path = project_dir / f"{project_dir.name}.gpx" if project_dir is not None else None
            return [default_path] if default_path is not None else []
        paths = []
        for part in text.split(";"):
            item = part.strip()
            if not item:
                continue
            path = Path(item).expanduser()
            if not path.is_absolute() and project_dir is not None:
                path = project_dir / path
            paths.append(path)
        return paths

    def _async_track_plot_exists(self, output_image):
        image_path = Path(output_image).resolve(strict=False)
        if image_path.exists():
            return image_path
        expected_key = self._track_plot_match_key(image_path.name)
        try:
            for candidate in image_path.parent.iterdir():
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() == image_path.suffix.lower()
                    and self._track_plot_match_key(candidate.name) == expected_key
                ):
                    return candidate.resolve(strict=False)
        except OSError:
            return None
        return None

    def _compute_async_project_status(self, snapshot):
        project_dir = snapshot["project_dir"]
        project_name = snapshot["project_name"]
        parameter_values = snapshot["parameters"]
        gpx_paths = self._async_gpx_paths_from_text(snapshot["gpx_text"], project_dir)
        result = {
            "generation": snapshot["generation"],
            "media_counts": (0, 0),
            "gpx_ready": any(path and path.exists() and path.is_file() and path.suffix.lower() == ".gpx" for path in gpx_paths),
            "gpx_summary": default_gpx_summary_text(),
            "track_maps_status": None,
            "control_summary": "No control file available yet.",
            "control_ready": False,
            "endpoint_place_missing_count": 0,
        }
        if project_dir is None or not project_dir.exists() or not project_dir.is_dir():
            result["media_summary"] = "No project directory selected."
            result["control_summary"] = "No project directory selected."
            return result

        photo_count = 0
        video_count = 0
        video_extensions = MEDIA_EXTENSIONS - IMAGE_EXTENSIONS
        try:
            for path in project_dir.iterdir():
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if suffix in IMAGE_EXTENSIONS:
                    photo_count += 1
                elif suffix in video_extensions:
                    video_count += 1
        except OSError:
            pass
        result["media_counts"] = (photo_count, video_count)
        result["media_summary"] = f"{photo_count} {'photo' if photo_count == 1 else 'photos'}, {video_count} {'video clip' if video_count == 1 else 'video clips'}"

        control_file_path = snapshot.get("control_file_path")
        control_lines = None
        if control_file_path is not None and control_file_path.exists():
            try:
                control_lines = control_file_path.read_text(encoding="utf-8").splitlines()
                rows = [
                    parse_slideshow_control_line(line)
                    for line in control_lines
                    if line.strip()
                ]
                result["control_summary"] = self._control_file_summary_text(rows, control_file_path)
                result["control_ready"] = any(str(row.get("type", "")).upper() in {"IMG", "VID"} for row in rows)
            except OSError as exc:
                result["control_summary"] = f"Could not read control file: {exc}"

        route_source = str(snapshot.get("route_source", "automatic"))
        use_gpx = route_source != "media" and len(gpx_paths) == 1 and gpx_paths[0] is not None and gpx_paths[0].exists()
        if use_gpx:
            gpx_path = gpx_paths[0].resolve(strict=False)
            generation = int(snapshot["generation"])

            def wait_for_status_turn():
                self._wait_for_project_status_processing(generation)

            try:
                trackimages_dir = project_dir / "trackimages"
                context = self._plot_context_for_values(
                    gpx_path,
                    project_name,
                    trackimages_dir,
                    snapshot["use_track_order"],
                    snapshot["track_map_base"],
                    track_processing_callback=wait_for_status_turn,
                    parameter_values=parameter_values,
                )
                summary_path = (
                    project_dir
                    / "trackimages"
                    / f"{snapshot['track_map_base']}-summary.json"
                )
                map_plan = self._project_map_plan_for_values(
                    project_dir,
                    control_file_path,
                    summary_path=summary_path if summary_path.is_file() else None,
                    use_track_order=snapshot["use_track_order"],
                    parameter_values=parameter_values,
                    control_file_base=snapshot["control_file_base"],
                    infer_gps_from_tracks=False,
                )
                self._add_media_maps_to_plot_context(
                    context,
                    plan=map_plan,
                    parameter_values=parameter_values,
                    control_path=control_file_path,
                )
                result["gpx_summary"] = self._format_gpx_summary_from_tracks(gpx_path, context.get("tracks", []))
                result["track_maps_status"] = self._track_maps_status_from_context(
                    gpx_path,
                    context,
                    self._async_track_plot_exists,
                    parameter_values,
                )
                result["endpoint_place_missing_count"] = int(
                    result["track_maps_status"].get(
                        "endpoint_place_missing_count",
                        0,
                    )
                )
            except (OSError, RuntimeError, ValueError, FileNotFoundError):
                result["track_maps_status"] = None
        elif route_source != "gpx":
            try:
                map_plan = self._project_map_plan_for_values(
                    project_dir,
                    control_file_path,
                    summary_path=None,
                    use_track_order=snapshot["use_track_order"],
                    parameter_values=parameter_values,
                    control_file_base=snapshot["control_file_base"],
                    infer_gps_from_tracks=False,
                )
                result["track_maps_status"] = (
                    self._media_only_track_maps_status_from_plan(
                        map_plan,
                        project_dir / "trackimages",
                        parameter_values,
                    )
                    if map_plan is not None
                    else None
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                result["track_maps_status"] = None
        missing_endpoints = int(result.get("endpoint_place_missing_count") or 0)
        if missing_endpoints:
            result["media_summary"] += (
                f" | {missing_endpoints} track endpoint place name"
                f"{'s' if missing_endpoints != 1 else ''} missing; "
                "run Update Metadata Extraction"
            )
        if control_lines is not None:
            track_status = result.get("track_maps_status")
            if track_status and track_status.get("media_only"):
                if track_status.get("out_of_date"):
                    result["control_summary"] += " | Media Maps need updating"
                else:
                    result["control_summary"] += " | Control file up to date"
                return result
            summary_current = bool(
                track_status
                and track_status.get("summary_exists")
                and not track_status.get("summary_out_of_date")
                and track_status.get("overview_exists")
                and not track_status.get("overview_out_of_date")
                and track_status.get("track_count") == track_status.get("track_total")
            )
            summary_path = project_dir / "trackimages" / f"{snapshot['track_map_base']}-summary.json"
            try:
                sync_plan = analyze_track_map_reference_updates(
                    project_dir,
                    control_file_path,
                    summary_path,
                    control_entries=parse_control_file_entries(control_lines),
                    summary_current=summary_current,
                    sort_date_sections_by_tracks=(
                        str(parameter_values.get("trackmaps.ordering", "track_number"))
                        == "track_number"
                    ),
                )
                if sync_plan.warning:
                    result["control_summary"] += (
                        " | Map Generation is required before the control file can be updated"
                    )
                elif sync_plan.change_count:
                    result["control_summary"] += (
                        f" | Control file needs update: {sync_plan.change_count} Track Map reference(s)"
                    )
                else:
                    result["control_summary"] += " | Control file up to date"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                result["control_summary"] += " | Could not check Track Map references"
        return result

    def start_async_project_status_refresh(self, reason=""):
        self.status_refresh_generation += 1
        generation = self.status_refresh_generation
        project_dir = self.current_project_dir
        snapshot = {
            "generation": generation,
            "project_dir": project_dir.resolve(strict=False) if project_dir is not None else None,
            "project_name": self._current_project_name(),
            "track_map_base": self.track_map_base or self._current_project_name(),
            "gpx_text": str(self.gpx_field.stringValue()).strip(),
            "control_file_path": self._control_file_path(),
            "control_file_base": self._control_file_base_name(),
            "use_track_order": self._use_track_order(),
            "route_source": str(self.parameters.get("trackmaps.route_source", "automatic")),
            "parameters": dict(self.parameters),
        }
        self._set_project_status_pending()
        self.set_status(f"Updating project status{f' ({reason})' if reason else ''}...")

        def run_task():
            try:
                result = self._compute_async_project_status(snapshot)
            except ProjectStatusRefreshSuperseded:
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_("applyAsyncProjectStatus:", result, True)

        threading.Thread(target=run_task, name="project-status-refresh", daemon=True).start()

    def applyAsyncProjectStatus_(self, result):
        if int(result.get("generation", -1)) != self.status_refresh_generation:
            return
        self.project_status_pending = False
        self.media_counts_cache = tuple(result.get("media_counts", (0, 0)))
        self.control_ready_cache = bool(result.get("control_ready", False))
        self.track_maps_status_cache = result.get("track_maps_status")
        self.track_endpoint_place_missing_cache = int(
            result.get("endpoint_place_missing_count") or 0
        )
        self.gpx_ready_cache = bool(result.get("gpx_ready", False))
        self.gpx_summary_label.setStringValue_(str(result.get("gpx_summary", default_gpx_summary_text())))
        self.track_maps_summary_label.setStringValue_(self._format_track_maps_summary_from_status(self.track_maps_status_cache))
        self.media_summary_label.setStringValue_(str(result.get("media_summary", "No project directory selected.")))
        self.media_metadata_button.setTitle_(
            "Update Metadata Extraction *"
            if self.track_endpoint_place_missing_cache
            else "Update Metadata Extraction"
        )
        if self.track_endpoint_place_missing_cache:
            self.media_metadata_button.setToolTip_(
                f"{self.track_endpoint_place_missing_cache} track start/destination "
                "place name(s) are missing. Run this operation to continue place-name extraction."
            )
        else:
            self.media_metadata_button.setToolTip_(
                "Check media metadata and refresh only missing, invalid, legacy, or changed sidecars. "
                "The selected place-name options are applied afterwards."
            )
        self.control_file_summary_label.setStringValue_(str(result.get("control_summary", "No control file available yet.")))
        self.refresh_section_status_indicators()
        if self.track_endpoint_place_missing_cache:
            self.set_status(
                f"Project status updated: {self.track_endpoint_place_missing_cache} "
                "track endpoint place name(s) need Update Metadata Extraction."
            )
        else:
            self.set_status("Project status updated.")
        pending_value = getattr(self, "pending_control_update_after_status", None)
        self.pending_control_update_after_status = None
        if pending_value is not None:
            self.performSelector_withObject_afterDelay_(
                "startPendingImportedMediaUpdate:",
                list(pending_value),
                0.0,
            )

    def refresh_current_gpx_summary(self):
        gpx_path = self._current_single_gpx_path()
        if gpx_path is None:
            self.clear_gpx_summary()
            return
        if gpx_path.exists():
            try:
                self.update_gpx_summary(gpx_path)
                return
            except (OSError, RuntimeError, ValueError):
                pass
        self.clear_gpx_summary()

    def _write_project_configuration(self, status_prefix="Saved adventure"):
        project_dir = self.current_project_dir
        if project_dir is None or self.current_project_file is None:
            return False
        adv_path = self._project_adv_path()
        if adv_path is None:
            return False
        payload = self._collect_project_payload()
        if adv_path.exists() and self.saved_project_payload == payload:
            self.mark_clean()
            return True
        try:
            validate_adventure_payload(payload, adv_path)
            atomic_write_json(adv_path, payload)
        except (OSError, AdventureFormatError) as exc:
            self.project_dirty = True
            self.adventure_autosave_error = str(exc)
            show_alert("Could not auto-save the Adventure.", str(exc))
            self.set_status(f"Auto-save failed: {exc}")
            return False
        self.current_project_file = adv_path
        self._remember_recent_adventure(project_dir)
        self.mark_clean()
        self.set_status(f"{status_prefix}: {adv_path.name}")
        return True

    def save_project_configuration(self):
        """Compatibility wrapper for operations that require an immediate flush."""
        if self.current_project_file is None:
            if self._project_dir_path() is None:
                return False
            self.adventureNameCommitted_(self.title_field)
            return self.current_project_file is not None
        return self.flush_adventure_autosave()

    def load_project_configuration(self, adv_path, flush_current=True):
        if flush_current and self.current_project_file is not None and not self.flush_adventure_autosave():
            return False
        try:
            record = load_adventure(adv_path)
            data = record.payload
        except AdventureFormatError as exc:
            show_alert("Could not load adventure.", str(exc))
            return False

        self._update_loaded_project_fields(data)
        normalized_music_source = self._stored_optional_project_path(
            self.music_source
        )
        normalized_music_playlist = self._stored_optional_project_path(
            self.music_playlist
        )
        music_paths_changed = (
            str(data.get("music_source", "") or "") != normalized_music_source
            or str(data.get("music_playlist", "") or "")
            != normalized_music_playlist
        )
        self.current_project_file = record.path
        self._remember_recent_adventure(self.current_project_file.parent)
        self._refresh_project_file_menus()
        self.set_status(f"Loaded adventure from {self.current_project_file}")
        self.reset_progress()
        self._refresh_loaded_gpx_summary()
        self.layout_window()
        self.mark_clean()
        if music_paths_changed:
            self.saved_project_payload = dict(data)
            self.mark_dirty(immediate=True)
        return True

    def confirm_close(self):
        if not self._resolve_unsaved_control_table_changes():
            return False
        if self.flush_adventure_autosave():
            return True
        while True:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Adventure auto-save failed")
            alert.setInformativeText_(
                "The latest Adventure changes could not be saved. Retry, quit without those changes, or cancel quitting."
            )
            alert.addButtonWithTitle_("Retry")
            alert.addButtonWithTitle_("Quit Without Saving")
            alert.addButtonWithTitle_("Cancel")
            response = int(alert.runModal())
            if response == 1000:
                if self.flush_adventure_autosave():
                    return True
                continue
            if response == 1001:
                return True
            return False

    def _apply_parameter_values(self, values, propagate_to_editor=True):
        old_parameters = dict(self.parameters)
        self.parameters = normalize_parameters(values)
        changed = changed_parameter_keys(old_parameters, self.parameters)
        self._sync_legacy_parameter_controls()
        if changed & map_affecting_parameter_keys():
            self.track_maps_status_cache = None
            self.track_maps_summary_label.setStringValue_("Track-map settings changed; use Update to refresh affected maps.")
            self._set_section_status_checkbox(
                self.track_maps_status_checkbox,
                False,
                "Track-map settings changed; update the maps before the slide show.",
            )
        editor_controller = getattr(self, "gpx_editor_controller", None)
        if propagate_to_editor and editor_controller is not None:
            editor_controller.apply_project_parameters(self.parameters)
        if changed:
            self.mark_dirty(immediate=True)
            self.set_status(f"Applied {len(changed)} Adventure setting(s); auto-save scheduled.")
            if changed & {"trackmaps.route_source", "audio.enabled"}:
                self.layout_window()
            if "trackmaps.route_source" in changed:
                route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
                if route_source == "media":
                    self.workflow_assistant_state["journey_source_confirmed"] = True
                elif route_source in {"automatic", "gpx"}:
                    self.workflow_assistant_state["journey_source_confirmed"] = self._has_existing_gpx_file()
                self.start_async_project_status_refresh("journey source changed")
        else:
            self.set_status("Adventure settings unchanged.")
        return True

    def _apply_parameters_from_shared_editor(self, values, _changed):
        return self._apply_parameter_values(values, propagate_to_editor=True)

    def _apply_embedded_editor_parameters(self, editor_values):
        merged = dict(self.parameters)
        merged.update(editor_values)
        return self._apply_parameter_values(merged, propagate_to_editor=False)

    @objc.IBAction
    def showMainHelp_(self, _sender):
        help_text = (
            "myCamino GPS Track Show helps you build one travel slide show from tracks, photos, videos, and maps.\n\n"
            "Recommended workflow:\n"
            "1. Choose an Adventure folder. This folder is where all material for this journey is collected.\n"
            "2. Choose an Adventure from the Adventure name menu. In an empty folder, confirm the suggested name to create one. Editing an existing name offers Rename or Copy, optionally including its GPX, control file, and generated maps.\n"
            "3. Use the gear beside the myCamino logo to adjust project settings. Common settings are shown first; Show Advanced Settings reveals technical map, GPX, PDF, location, and server controls. GPX Processing has separate defaults for horizontal smoothing (10 m), point spacing (10 m), elevation smoothing (50 m), horizontal/vertical error (10/20 m), and HDOP/VDOP (20/20); zero disables an individual operation. Statistics, maps, PDFs, and Time-Lapse motion use these settings consistently. Applied changes are auto-saved with the Adventure.\n"
            "4. For a new Adventure, confirm a detected GPX file, choose other GPX files, or select No GPX file - use only photos. One detected file is used directly; several detected or selected files are joined in the GPX Editor. Cancelling a chooser leaves the source unconfirmed. The same media-only choice remains visible in the GPX Files section and is saved with the Adventure.\n"
            "5. Accept photos and videos already in the Adventure folder or import more. Existing files are skipped rather than copied again. One retained Adventure Processing window then shows metadata extraction, optional place-name lookup, map generation, and control-file work with phase headings and progress in its title. Add place names is selected by default; Skip omits only that slower phase.\n"
            "6. Map Generation automatically creates only missing or outdated maps and always creates Standard and Time-Lapse variants consecutively for each affected stage. The manual Generate and Update Maps button is mainly for repairs, changed settings, or retried downloads. Completed metadata and maps remain available if a later phase fails or is cancelled.\n"
            "7. If no control file exists, Adventure Processing creates it after successful map generation. If an edited control file already exists, the program asks before opening Update Control File and never replaces it automatically. GPX journeys combine media with measured stages; media-only journeys group located media by date. Missing media GPS can be inferred when its exposure time falls inside exactly one timed GPX track.\n"
            "8. Press Edit to review the slide-show list. You can move, copy, delete, and edit rows, then save the list again. Unsaved table edits are regularly backed up and can be restored after an interruption.\n"
            "9. For later additions or corrections, press Update Control File instead of recreating the complete list. It checks map references and automatically finds imported, missing, invalid, legacy, and changed media. One review shows required reference corrections and recommended media updates. Map images themselves are rendered in Map Generation. Choose Other Media deliberately rechecks otherwise current files; changed GPS can trigger a targeted place-name update.\n"
            "11. Music is a separate optional section and new Adventures start with No Music selected. Every project has a fixed audio folder, which is created automatically and recreated if missing. Clear No Music, click the folder icon, and copy audio files or complete album directories into that folder. Choose selects one of the .playlist files stored there; without a playlist, titles play alphabetically. Create Playlist makes another named playlist, and Update Playlist appends newly found audio without replacing your edits. Insert MUS rows in the control-file table to add #MUSIC commands for jumps, queues, loops, gate control, and internal volume. A manual Audio Off with the a key always has priority.\n"
            "12. Use PDF Summary near Start if you want a printable GPX track table and optional map pages.\n"
            "13. Update Metadata Extraction in the Photos and Video Clips section can also add readable place names. For GPX Adventures it resolves each track's start and destination first and stores them with both map variants for the default PLACE1 - PLACE2 stage title, then processes GPS already stored with each photo or video. Skip in the output window omits that slow phase for the current run without clearing the saved option.\n"
            "14. Press Start to begin at the start, or Continue to resume from the last automatically saved stage, phase, medium, and Time-Lapse position. The initial style is selected in Settings and defaults to Time-Lapse. During playback, t cycles forward and Shift-t backward through Time-Lapse, Blend, Fade, Switch, Expand, Collage, Quad, and Random.\n"
            "15. In the control-file editor, right-click any row and choose Start Slide Show Here to launch once from that exact map, medium, date, or music directive without first replacing the saved Continue position.\n"
            "15. Window mode is Automatic by default: one screen uses one slide-show window, while two screens use a separate overview window. In one-window Time-Lapse mode the overview is shown by default as a framed image over the track map before each stage and advances automatically in Auto mode. Settings can instead make it full-screen. Press w during either show to add or remove the separate overview window. Closing only that window continues the show.\n"
            "16. A fresh Start begins with the Adventure title, summary, description, and optional title image over the Tour Overview, followed by the clean overview. Choose the title image below Description; Use First selects the first still image in the control file. Each one-window stage then shows its Stage Map, marked Tour Overview, and media. Cursor keys move through those phases and across stage boundaries; Command-cursor jumps directly between Stage Maps. Settings can optionally show the marked track map again before every photo or video. Press a during either show to pause or resume background audio.\n\n"
            "Workflow Assistant:\n"
            "- Assistant in the header is enabled for new Adventures and offers a recommended action for the next incomplete step. Return activates the default action.\n"
            "- It guides Project directory, Adventure name, the two explicit GPX or photo-only choices, explicit media acceptance/import, metadata using the visible place-name option, combined control/map creation, and the first slide-show start.\n"
            "- Click the bubble's x or uncheck Assistant to turn it off for the active Adventure. The choice is auto-saved.\n\n"
            "Files and folders you will see:\n"
            "- The Adventure file stores the explicit GPX, slide-show list, generated map family, folder, and other settings.\n"
            "- The GPX track file contains the travel route.\n"
            "- The trackimages folder contains background maps plus companion information used to draw measured routes or media-derived positions and titles at display time. Media-only Adventures contain one map per dated stage and a shared overview.\n"
            "- The slide-show list controls the final order of photos, videos, date headings, maps, and optional #MUSIC directives.\n"
            "- A .playlist file controls music order and names synchronization positions.\n"
            "- Small companion files next to photos, videos, and maps store dates, positions, and place names used by the program.\n\n"
            "The colored marks at the left of each section show whether the minimum required step is complete. "
            "The status lines also warn when maps or track entries need updating."
        )
        if self.main_help_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(220.0, 180.0, 680.0, 520.0),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setTitle_("myCamino GPS Track Show Help")
            content = window.contentView()
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(18.0, 58.0, 644.0, 444.0))
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(False)
            scroll.setBorderType_(1)
            text_view = NSTextView.alloc().initWithFrame_(scroll.bounds())
            text_view.setEditable_(False)
            text_view.setSelectable_(True)
            text_view.setRichText_(False)
            text_view.setFont_(NSFont.systemFontOfSize_(14.0))
            text_view.setString_(help_text)
            scroll.setDocumentView_(text_view)
            content.addSubview_(scroll)
            close_button = self._make_button("Close", "closeMainHelp:")
            close_button.setFrame_(NSMakeRect(562.0, 18.0, 100.0, FIELD_HEIGHT))
            close_button.setAutoresizingMask_(0)
            content.addSubview_(close_button)
            self.main_help_window = window
        self.main_help_window.makeKeyAndOrderFront_(None)

    @objc.IBAction
    def closeMainHelp_(self, _sender):
        if self.main_help_window is not None:
            self.main_help_window.orderOut_(None)

    def _show_scrollable_list(self, title, lines):
        if not lines:
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180.0, 180.0, 540.0, 360.0),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(title)
        scroll = NSScrollView.alloc().initWithFrame_(window.contentView().bounds())
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(True)
        text_view = NSTextView.alloc().initWithFrame_(window.contentView().bounds())
        text_view.setEditable_(False)
        text_view.setString_("\n".join(lines))
        scroll.setDocumentView_(text_view)
        window.contentView().addSubview_(scroll)
        window.makeKeyAndOrderFront_(None)
        self.skipped_media_windows.append(window)

    def _ensure_geolocations_window(self):
        if self.geolocations_window is not None:
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(220.0, 220.0, 820.0, 260.0),
            NSWindowStyleMaskTitled | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("Create Slide Show Control File")
        content = window.contentView()

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16.0, 56.0, 788.0, 188.0))
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(True)
        text_view = NSTextView.alloc().initWithFrame_(scroll.bounds())
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setRichText_(False)
        disable_field_editor_text_checking(text_view)
        scroll.setDocumentView_(text_view)
        content.addSubview_(scroll)

        cancel_button = self._make_button("Cancel", "cancelGeoLocationsRun:")
        cancel_button.setFrame_(NSMakeRect(454.0, 16.0, 110.0, FIELD_HEIGHT))
        cancel_button.setAutoresizingMask_(1)
        content.addSubview_(cancel_button)

        skip_button = self._make_button("Skip", "skipPlaceNameExtraction:")
        skip_button.setFrame_(NSMakeRect(574.0, 16.0, 110.0, FIELD_HEIGHT))
        skip_button.setAutoresizingMask_(1)
        skip_button.setHidden_(True)
        skip_button.setEnabled_(False)
        skip_button.setToolTip_(
            "Skip place-name extraction for this run without changing the Add place names option."
        )
        content.addSubview_(skip_button)

        close_button = self._make_button("Close", "closeGeoLocationsWindow:")
        close_button.setFrame_(NSMakeRect(694.0, 16.0, 110.0, FIELD_HEIGHT))
        close_button.setAutoresizingMask_(1)
        close_button.setEnabled_(False)
        content.addSubview_(close_button)

        delegate = GPSTrackShowGUIGeoLocationsWindowDelegate.alloc().initWithController_(self)
        window.setDelegate_(delegate)
        self.geolocations_window = window
        self.geolocations_text_view = text_view
        self.geolocations_cancel_button = cancel_button
        self.geolocations_skip_button = skip_button
        self.geolocations_close_button = close_button
        self.geolocations_window_delegate = delegate

    def _prepare_geolocations_window(self, title):
        self._ensure_geolocations_window()
        preserve_output = bool(self.preserve_processing_output_next)
        self.preserve_processing_output_next = False
        self.processing_phase_title = str(title)
        self.geolocations_window.setTitle_(str(title))
        if not preserve_output:
            self.geolocations_text_view.setString_("")
        else:
            self.appendGeoLocationsOutputLine_("")
        self.appendGeoLocationsOutputLine_(f"=== {title} ===")
        self.geolocations_running = True
        self.geolocations_skip_place_names = False
        self.refresh_workflow_assistant()
        self.geolocations_cancel_button.setEnabled_(True)
        if self.geolocations_skip_button is not None:
            self.geolocations_skip_button.setHidden_(True)
            self.geolocations_skip_button.setEnabled_(False)
        if self.geolocations_close_button is not None:
            self.geolocations_close_button.setEnabled_(False)
        self.geolocations_window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)

    def _continue_adventure_processing(self, status_text):
        """Finish one phase while retaining the output window for the next."""
        if self.geolocations_skip_button is not None:
            self.geolocations_skip_button.setHidden_(True)
            self.geolocations_skip_button.setEnabled_(False)
        self.appendGeoLocationsOutputLine_(str(status_text))
        self.geolocations_thread = None
        self.geolocations_cancel_event = None
        self.geolocations_mode = None
        self.geolocations_skip_place_names = False
        self.preserve_processing_output_next = True

    def appendGeoLocationsOutputLine_(self, line):
        if self.geolocations_text_view is None:
            return
        addition = NSAttributedString.alloc().initWithString_(f"{line}\n")
        storage = self.geolocations_text_view.textStorage()
        storage.appendAttributedString_(addition)
        self.geolocations_text_view.scrollRangeToVisible_((storage.length(), 0))

    def _finish_geolocations_run(self, status_text, status_message):
        if self.geolocations_cancel_button is not None:
            self.geolocations_cancel_button.setEnabled_(False)
        if self.geolocations_close_button is not None:
            self.geolocations_close_button.setEnabled_(True)
        if self.geolocations_skip_button is not None:
            self.geolocations_skip_button.setHidden_(True)
            self.geolocations_skip_button.setEnabled_(False)
        self.reset_progress()
        self.set_status(status_message)
        self.appendGeoLocationsOutputLine_(status_text)
        if self.geolocations_window is not None:
            self.geolocations_window.setTitle_("Adventure Processing — Complete")
        self.geolocations_thread = None
        self.geolocations_cancel_event = None
        self.geolocations_running = False
        self.geolocations_mode = None
        self.geolocations_skip_place_names = False
        self.processing_phase_title = None
        self.refresh_workflow_assistant()

    def setPlaceNameSkipAvailable_(self, available):
        if self.geolocations_skip_button is None:
            return
        enabled = bool(available)
        self.geolocations_skip_button.setHidden_(not enabled)
        self.geolocations_skip_button.setEnabled_(enabled)

    def _cleanup_geolocations_temp_paths(self):
        for temp_path in list(self.geolocations_temp_paths):
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass
        self.geolocations_temp_paths = []

    def geoLocationsRunFinished_(self, status_text):
        status = str(status_text)
        mode = self.geolocations_mode
        if status == "place_names_skipped":
            self._cleanup_geolocations_temp_paths()
            self._finish_geolocations_run(
                "Skipped.",
                "Place-name extraction was skipped for now.",
            )
            return
        if status == "cancelled":
            if mode == "places":
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run("Aborted.", "Reverse geolocation cancelled.")
            elif mode == "assistant_metadata":
                self.pending_imported_media_for_control_update = []
                self.assistant_metadata_candidate_paths = []
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run("Aborted.", "Metadata update cancelled.")
            elif mode == "automatic_maps":
                self.automatic_map_pending_imported_paths = []
                self.automatic_map_review_control = False
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run("Aborted.", "Map generation cancelled.")
            elif mode in {"media_update_analysis", "media_update_commit"}:
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run("Aborted.", "Control-file update cancelled; project files were retained.")
            elif mode == "assistant_control_assets":
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run("Aborted.", "Control-file and map creation cancelled.")
            else:
                self._finish_geolocations_run("Aborted.", "Control-file creation cancelled.")
            return
        if status.startswith("error:"):
            if mode == "places":
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(status, "Reverse geolocation failed.")
            elif mode == "assistant_metadata":
                self.pending_imported_media_for_control_update = []
                self.assistant_metadata_candidate_paths = []
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(status, "Metadata update failed.")
            elif mode == "automatic_maps":
                self.automatic_map_pending_imported_paths = []
                self.automatic_map_review_control = False
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(status, "Map generation failed.")
            elif mode in {"media_update_analysis", "media_update_commit"}:
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(status, "Control-file update failed; project files were retained.")
            elif mode == "assistant_control_assets":
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(status, "Control-file and map creation failed.")
            else:
                self._finish_geolocations_run(status, "Control-file creation failed.")
            return
        if mode == "assistant_metadata":
            metadata_result = getattr(self, "assistant_metadata_result", {}) or {}
            total_count = int(metadata_result.get("total", 0))
            candidate_count = int(metadata_result.get("candidates", 0))
            update_result = metadata_result.get("result")
            refreshed_count = int(
                getattr(update_result, "refreshed_sidecars", 0)
            )
            skipped_count = max(0, total_count - candidate_count)
            gps_inferred = int(getattr(update_result, "gps_inferred", 0))
            gps_refreshed = int(getattr(update_result, "gps_refreshed", 0))
            places_preserved = int(getattr(update_result, "places_preserved", 0))
            requested_places = bool(getattr(self, "assistant_metadata_place_names", False))
            places_skipped = bool(getattr(self, "assistant_metadata_places_skipped", False))
            pending_imported_paths = list(
                getattr(self, "pending_imported_media_for_control_update", [])
            )
            pending_imported_paths = sorted(
                {
                    str(Path(path).resolve(strict=False))
                    for path in (
                        pending_imported_paths
                        + list(getattr(self, "assistant_metadata_candidate_paths", []))
                    )
                }
            )
            self.assistant_metadata_candidate_paths = []
            self.pending_imported_media_for_control_update = []
            self.workflow_assistant_state["metadata_prepared"] = True
            self.workflow_assistant_state["place_names_requested"] = requested_places
            self.workflow_assistant_state["place_names_completed"] = bool(
                not requested_places or not places_skipped
            )
            updated_places = 0
            control_path = self._control_file_path()
            if (
                requested_places
                and not places_skipped
                and control_path is not None
                and control_path.is_file()
            ):
                updated_places = self.update_place_names_from_sidecars(
                    overwrite=False
                ) or 0
                if updated_places:
                    self._save_control_table_changes()
            self.mark_dirty(immediate=True)
            summary = (
                f"Metadata prepared: refreshed {refreshed_count} sidecar(s), "
                f"skipped {skipped_count} current sidecar(s), inferred GPS for "
                f"{gps_inferred} and refreshed inferred GPS for {gps_refreshed} media file(s)."
            )
            if places_preserved:
                summary += f" Preserved {places_preserved} existing place name(s)."
            if requested_places and places_skipped:
                summary += " Place-name extraction was skipped for now."
            elif requested_places:
                place_report = getattr(self, "assistant_metadata_place_report", None)
                looked_up = int(getattr(place_report, "updated", 0))
                already_complete = int(
                    getattr(place_report, "already_complete", 0)
                )
                summary += (
                    f" Looked up {looked_up} place name(s) and kept "
                    f"{already_complete} current place name(s)."
                )
                if updated_places:
                    summary += f" Updated {updated_places} control-file row(s)."
            self.refresh_media_summary()
            self.set_status(summary)
            self._continue_adventure_processing(summary)
            self.refresh_section_status_indicators()
            self.performSelector_withObject_afterDelay_(
                "startAutomaticMapGeneration:",
                pending_imported_paths,
                0.0,
            )
            return
        if mode == "automatic_maps":
            result = getattr(self, "automatic_map_generation_result", {}) or {}
            created_count = int(result.get("created_count", 0))
            failures = list(result.get("failures", []))
            pending_imported_paths = list(
                getattr(self, "automatic_map_pending_imported_paths", [])
            )
            self.automatic_map_pending_imported_paths = []
            summary = f"Map generation checked all stages and created {created_count} map file(s)."
            if failures:
                summary += (
                    f" {len(failures)} map operation(s) failed; completed maps were retained."
                )
                self._show_scrollable_list(
                    "Maps Not Created",
                    [
                        f"{item.get('filename', 'Map')}: {item.get('error', 'Unknown error')}"
                        for item in failures
                    ],
                )
            self.set_status(summary)
            self._continue_adventure_processing(summary)
            control_path = self._control_file_path()
            review_existing = bool(
                getattr(self, "automatic_map_review_control", False)
            )
            self.automatic_map_review_control = False
            self.start_async_project_status_refresh("automatic map generation")
            if not failures and control_path is not None:
                if not control_path.is_file():
                    self.assistant_control_skip_maps = True
                    self.performSelector_withObject_afterDelay_(
                        "startControlCreationAfterMaps:",
                        None,
                        0.0,
                    )
                elif review_existing:
                    self.pending_control_update_after_status = pending_imported_paths
                    self.performSelector_withObject_afterDelay_(
                        "offerControlUpdateAfterMaps:",
                        None,
                        0.0,
                    )
                else:
                    self._finish_geolocations_run(summary, summary)
            else:
                self._finish_geolocations_run(summary, summary)
            return
        if mode == "assistant_control_assets":
            control_path = self._control_file_path()
            if control_path is not None and control_path.is_file():
                self.load_slideshow_control_file(control_path, force_reload=True)
                if self.workflow_assistant_state.get("place_names_requested", False):
                    updated_places = self.update_place_names_from_sidecars(
                        overwrite=False
                    )
                    if updated_places:
                        self._save_control_table_changes()
                self._refresh_project_file_menus()
            failures = list(getattr(self, "assistant_control_map_failures", []))
            if failures:
                lines = [f"{item.get('filename')}: {item.get('error')}" for item in failures]
                self._show_scrollable_list("Maps Not Created", lines)
                summary = (
                    f"Created the control file, but {len(failures)} map operation(s) failed. "
                    "Use Generate and Update Maps when the map service is available."
                )
            else:
                summary = "Created the control file, overview, and both map variants."
            if bool(getattr(self, "assistant_control_places_skipped", False)):
                summary += " Place-name extraction was skipped for now."
            self.mark_dirty(immediate=True)
            self._finish_geolocations_run(summary, summary)
            self.start_async_project_status_refresh("guided control and maps creation")
            return
        if mode == "media_update_analysis":
            self.control_file_update_plan = self.media_update_pending_plan
            self.media_update_pending_plan = None
            self.media_update_plan = (
                self.control_file_update_plan.media
                if self.control_file_update_plan is not None else None
            )
            item_count = len(self.media_update_plan.items) if self.media_update_plan is not None else 0
            warning_count = len(self.media_update_plan.warnings) if self.media_update_plan is not None else 0
            map_count = (
                self.control_file_update_plan.track_maps.change_count
                if self.control_file_update_plan is not None else 0
            )
            self._finish_geolocations_run(
                f"Analysis complete: {map_count} Track Map reference change(s), "
                f"{item_count} media file(s), {warning_count} warning(s).",
                f"Analyzed the control file: {map_count} Track Map reference change(s), "
                f"{item_count} media update(s).",
            )
            if self.geolocations_window is not None:
                self.geolocations_window.orderOut_(None)
            if item_count == 0 and map_count == 0:
                self._show_no_control_file_updates()
                return
            if not control_file_update_requires_review(self.control_file_update_plan):
                self._start_control_file_update_commit(
                    update_places=bool(
                        self.parameters.get("locations.add_place_names", True)
                    )
                    and any(
                        item.apply_update and item.place_update_recommended
                        for item in self.media_update_plan.items
                    )
                )
                return
            self.show_media_update_preview()
            return
        if mode == "media_update_commit":
            combined_result = self.control_file_update_result
            result = combined_result.media if combined_result is not None else None
            control_path = self._control_file_path()
            if control_path is not None and control_path.exists():
                self.load_slideshow_control_file(control_path, force_reload=True)
            self.refresh_media_summary()
            self.refresh_control_file_display()
            if result is None:
                summary = "Control-file update completed."
            else:
                summary = (
                    f"Track Map references: added {combined_result.map_entries_added}, "
                    f"replaced {combined_result.map_entries_replaced}, "
                    f"reordered {combined_result.map_entries_reordered}, "
                    f"removed {combined_result.map_entries_removed}; "
                    f"updated {result.refreshed_sidecars} sidecar(s); added {result.rows_added}, "
                    f"updated {result.rows_updated}, moved {result.rows_moved} control row(s); "
                    f"regenerated {result.media_maps_regenerated} media map(s); "
                    f"inferred GPS for {result.gps_inferred}, refreshed inferred GPS for "
                    f"{result.gps_refreshed}; preserved {result.places_preserved} and "
                    f"updated {result.places_updated} place name(s)."
                )
                if result.control_rows_pending:
                    summary += f" {result.control_rows_pending} control row(s) still need manual review."
                if self.media_update_plan is not None and self.media_update_plan.control_file is None:
                    summary += " No control file exists yet; use Create to build the initial slide-show list."
            self._finish_geolocations_run(summary, summary)
            self.start_async_project_status_refresh("control-file update")
            return
        if mode == "places":
            overwrite = bool(getattr(self, "geolocations_places_overwrite", False))
            control_path = self._control_file_path()
            if control_path is None or not control_path.is_file():
                self.assistant_place_names_pending_save = False
                self.workflow_assistant_state["place_names_requested"] = True
                self.workflow_assistant_state["place_names_completed"] = True
                self.mark_dirty(immediate=True)
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(
                    "Finished. Place names were stored in media metadata.",
                    "Reverse geolocation finished; a future control file will use the stored place names.",
                )
                return
            updated_count = self.update_place_names_from_sidecars(overwrite=overwrite)
            if updated_count is None:
                self._cleanup_geolocations_temp_paths()
                self._finish_geolocations_run(
                    "Finished, but the control table could not be updated.",
                    "Reverse geolocation finished, but updating the control table failed.",
                )
                return
            if updated_count > 0:
                self.assistant_place_names_pending_save = True
            else:
                self.assistant_place_names_pending_save = False
                self.workflow_assistant_state["place_names_completed"] = True
                self.mark_dirty(immediate=True)
            self._cleanup_geolocations_temp_paths()
            self._finish_geolocations_run(
                f"Finished. Updated {updated_count} place name(s) in the table.",
                f"Reverse geolocation finished; updated {updated_count} place name(s). Save the control table to keep them.",
            )
            return
        if self.geolocations_result_path is not None:
            self.mark_dirty()
            self.load_slideshow_control_file(self.geolocations_result_path)
            self._refresh_project_file_menus()
        self._finish_geolocations_run("Finished.", f"Created slide show control file {self.geolocations_result_path}")

    @objc.IBAction
    def cancelGeoLocationsRun_(self, _sender):
        if not self.geolocations_running or self.geolocations_thread is None:
            if self.geolocations_cancel_button is not None:
                self.geolocations_cancel_button.setEnabled_(False)
            return
        if self.geolocations_cancel_event is not None:
            self.geolocations_cancel_event.set()
            if self.geolocations_cancel_button is not None:
                self.geolocations_cancel_button.setEnabled_(False)
            if self.geolocations_mode == "places":
                self.set_status("Cancelling reverse geolocation...")
            elif self.geolocations_mode in {"media_update_analysis", "media_update_commit"}:
                self.set_status("Cancelling control-file update...")
            else:
                self.set_status("Cancelling control-file creation...")

    @objc.IBAction
    def skipPlaceNameExtraction_(self, _sender):
        if not self.geolocations_running or self.geolocations_cancel_event is None:
            return
        self.geolocations_skip_place_names = True
        self.geolocations_cancel_event.set()
        if self.geolocations_skip_button is not None:
            self.geolocations_skip_button.setEnabled_(False)
        self.set_status("Skipping place-name extraction...")

    @objc.IBAction
    def closeGeoLocationsWindow_(self, _sender):
        if self.geolocations_running and self.geolocations_thread is not None:
            show_alert("Operation still running.", "Press Cancel first if you want to stop the operation.")
            return
        if self.geolocations_window is not None:
            self.geolocations_window.orderOut_(None)

    def _make_icon_button(self, image_name, fallback_title, action):
        button = make_liquid_glass_button(NSMakeRect(0, 0, FIELD_HEIGHT, FIELD_HEIGHT))
        image_names = image_name if isinstance(image_name, (list, tuple)) else [image_name]
        image = None
        for candidate in image_names:
            if str(candidate).startswith("sf:") and hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
                image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    str(candidate)[3:],
                    fallback_title,
                )
            else:
                image = NSImage.imageNamed_(candidate)
            if image is not None:
                break
        if image is not None:
            button.setImage_(image)
            button.setImagePosition_(2)
            button.setTitle_("")
        else:
            button.setTitle_(fallback_title)
        button.setTarget_(self)
        button.setAction_(action)
        button.setToolTip_(fallback_title)
        return apply_liquid_glass_button_style(button, compact=True)

    def _ensure_parameter_window(self):
        if self.parameter_window is not None:
            return
        rect = NSMakeRect(180.0, 100.0, 900.0, 680.0)
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.parameter_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.parameter_window.setTitle_("Adventure Settings")
        self.parameter_window.setMinSize_((760.0, 540.0))
        self.parameter_window_delegate = GPSTrackShowGUIParameterWindowDelegate.alloc().initWithController_(self)
        self.parameter_window.setDelegate_(self.parameter_window_delegate)
        content = self.parameter_window.contentView()

        self.parameter_section_buttons = []
        for index, section in enumerate(SECTION_ORDER):
            button = self._make_button(section, "selectParameterSection:")
            button.setTag_(7000 + index)
            button.setToolTip_(f"Show {section} settings.")
            content.addSubview_(button)
            self.parameter_section_buttons.append(button)

        self.parameter_form_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.parameter_form_scroll.setHasVerticalScroller_(True)
        self.parameter_form_scroll.setHasHorizontalScroller_(False)
        self.parameter_form_scroll.setBorderType_(1)
        content.addSubview_(self.parameter_form_scroll)

        self.parameter_advanced_checkbox = self._make_checkbox("Show Advanced Settings", "toggleAdvancedParameters:")
        content.addSubview_(self.parameter_advanced_checkbox)
        self.parameter_error_label = self._make_label("", size=11.0)
        self.parameter_error_label.setTextColor_(NSColor.systemRedColor())
        content.addSubview_(self.parameter_error_label)
        self.parameter_reset_all_button = self._make_button("Reset All", "resetAllParameters:")
        self.parameter_cancel_button = self._make_button("Cancel", "cancelParameterEditor:")
        self.parameter_apply_button = self._make_button("Apply", "applyParameterEditor:")
        content.addSubview_(self.parameter_reset_all_button)
        content.addSubview_(self.parameter_cancel_button)
        content.addSubview_(self.parameter_apply_button)
        self.layoutParameterWindow()

    def layoutParameterWindow(self):
        if self.parameter_window is None:
            return
        bounds = self.parameter_window.contentView().bounds()
        width, height = float(bounds.size.width), float(bounds.size.height)
        sidebar_width = 178.0
        footer_height = 70.0
        top_padding = 16.0
        section_height = 34.0
        for index, button in enumerate(self.parameter_section_buttons):
            button.setFrame_(
                NSMakeRect(
                    14.0,
                    height - top_padding - (index + 1) * section_height,
                    sidebar_width - 28.0,
                    28.0,
                )
            )
        form_x = sidebar_width
        self.parameter_form_scroll.setFrame_(
            NSMakeRect(form_x, footer_height, max(420.0, width - form_x - 14.0), max(300.0, height - footer_height - 14.0))
        )
        self.parameter_advanced_checkbox.setFrame_(NSMakeRect(18.0, 40.0, sidebar_width - 24.0, 24.0))
        self.parameter_reset_all_button.setFrame_(NSMakeRect(18.0, 8.0, 110.0, 28.0))
        self.parameter_apply_button.setFrame_(NSMakeRect(width - 116.0, 18.0, 100.0, 30.0))
        self.parameter_cancel_button.setFrame_(NSMakeRect(width - 226.0, 18.0, 100.0, 30.0))
        self.parameter_error_label.setFrame_(NSMakeRect(form_x + 12.0, 20.0, max(220.0, width - form_x - 250.0), 24.0))
        if self.parameter_form_view is not None:
            self._capture_parameter_controls(update_error=False)
            self._render_parameter_section()

    def _parameter_color(self, value):
        text = str(value).strip().lower()
        named = {
            "black": NSColor.blackColor(),
            "white": NSColor.whiteColor(),
            "red": NSColor.redColor(),
            "blue": NSColor.blueColor(),
            "green": NSColor.greenColor(),
            "yellow": NSColor.yellowColor(),
            "gray": NSColor.grayColor(),
            "grey": NSColor.grayColor(),
            "orange": NSColor.orangeColor(),
            "cyan": NSColor.cyanColor(),
            "magenta": NSColor.magentaColor(),
        }
        if text in named:
            return named[text]
        if re.fullmatch(r"#[0-9a-f]{6}", text):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(
                int(text[1:3], 16) / 255.0,
                int(text[3:5], 16) / 255.0,
                int(text[5:7], 16) / 255.0,
                1.0,
            )
        return NSColor.blackColor()

    def _parameter_color_text(self, color):
        converted = color.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()) if color is not None else None
        if converted is None:
            return "#000000"
        return "#{:02X}{:02X}{:02X}".format(
            round(float(converted.redComponent()) * 255.0),
            round(float(converted.greenComponent()) * 255.0),
            round(float(converted.blueComponent()) * 255.0),
        )

    def _display_parameter_value(self, spec, value):
        if spec.value_type == "fraction":
            return f"{float(value) * 100.0:g}"
        return str(value)

    def _parameter_stepper_values(self, spec, value):
        scale = 100.0 if spec.value_type == "fraction" else 1.0
        displayed = float(value) * scale
        minimum = float(spec.minimum) * scale if spec.minimum is not None else -1.0e9
        maximum = float(spec.maximum) * scale if spec.maximum is not None else 1.0e9
        if spec.value_type in {"int", "fraction"}:
            increment = 1.0
        else:
            increment = 0.1 if max(abs(displayed), 1.0) < 10.0 else 1.0
        return displayed, minimum, maximum, increment

    def _render_parameter_section(self):
        if self.parameter_form_scroll is None:
            return
        specs = visible_specs_for_section(
            self.parameter_current_section,
            self.parameter_draft,
            self.parameter_show_advanced,
        )
        visible_height = max(300.0, float(self.parameter_form_scroll.contentSize().height))
        row_height = 64.0
        document_height = max(visible_height, 24.0 + row_height * len(specs))
        document_width = max(520.0, float(self.parameter_form_scroll.contentSize().width))
        form_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, document_width, document_height))
        self.parameter_controls = {}
        self.parameter_steppers = {}
        self.parameter_tag_to_key = {}
        for row, spec in enumerate(specs):
            y = document_height - 18.0 - (row + 1) * row_height
            label = self._make_label(spec.label, size=13.0, bold=True)
            label.setFrame_(NSMakeRect(16.0, y + 34.0, 215.0, 20.0))
            label.setToolTip_(spec.help_text)
            form_view.addSubview_(label)
            help_label = self._make_label(spec.help_text, size=10.5)
            help_label.setTextColor_(NSColor.secondaryLabelColor())
            help_label.setFrame_(NSMakeRect(16.0, y + 6.0, max(220.0, document_width - 72.0), 28.0))
            help_label.setToolTip_(spec.help_text)
            form_view.addSubview_(help_label)

            tag = 8000 + row
            value = self.parameter_draft.get(spec.key, spec.default)
            control_x = min(245.0, max(220.0, document_width * 0.42))
            is_numeric = spec.value_type in {"int", "float", "fraction"}
            reset_x = document_width - 36.0
            stepper_x = reset_x - 26.0 if is_numeric else reset_x
            unit_width = 42.0 if spec.unit else 0.0
            unit_x = stepper_x - unit_width - (6.0 if spec.unit else 0.0)
            control_right = (unit_x if spec.unit else stepper_x) - 8.0
            control_width = max(110.0, control_right - control_x)
            if spec.value_type == "bool":
                control = self._make_checkbox("", "parameterValueChanged:")
                control.setState_(NSControlStateValueOn if bool(value) else NSControlStateValueOff)
            elif spec.value_type == "choice":
                control = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(0, 0, control_width, 26.0))
                control.addItemsWithTitles_([label_text for _stored, label_text in spec.choices])
                selected_index = next((index for index, item in enumerate(spec.choices) if item[0] == value), 0)
                control.selectItemAtIndex_(selected_index)
                control.setTarget_(self)
                control.setAction_("parameterValueChanged:")
            elif spec.value_type == "color":
                control = NSColorWell.alloc().initWithFrame_(NSMakeRect(0, 0, min(90.0, control_width), 26.0))
                control.setColor_(self._parameter_color(value))
                control.setTarget_(self)
                control.setAction_("parameterValueChanged:")
            else:
                control = self._make_text_field("")
                control.setStringValue_(self._display_parameter_value(spec, value))
                control.setDelegate_(self)
                control.setTarget_(self)
                control.setAction_("parameterValueChanged:")
            control.setTag_(tag)
            control.setFrame_(NSMakeRect(control_x, y + 31.0, control_width, 27.0))
            control.setToolTip_(spec.help_text)
            form_view.addSubview_(control)
            self.parameter_controls[spec.key] = control
            self.parameter_tag_to_key[tag] = spec.key

            if spec.unit:
                unit = self._make_label(spec.unit, size=11.0)
                unit.setFrame_(NSMakeRect(unit_x, y + 35.0, unit_width, 18.0))
                form_view.addSubview_(unit)
            if is_numeric:
                displayed, minimum, maximum, increment = self._parameter_stepper_values(spec, value)
                stepper = NSStepper.alloc().initWithFrame_(NSMakeRect(stepper_x, y + 31.0, 20.0, 27.0))
                stepper.setMinValue_(minimum)
                stepper.setMaxValue_(maximum)
                stepper.setIncrement_(increment)
                stepper.setDoubleValue_(displayed)
                stepper.setValueWraps_(False)
                stepper.setAutorepeat_(True)
                stepper.setTarget_(self)
                stepper.setAction_("parameterStepperChanged:")
                stepper.setTag_(tag)
                stepper.setToolTip_(f"Increase or decrease {spec.label}.")
                form_view.addSubview_(stepper)
                self.parameter_steppers[spec.key] = stepper
            reset = self._make_icon_button(["sf:arrow.counterclockwise", "NSRefreshTemplate"], "Reset", "resetParameter:")
            reset.setTag_(tag)
            reset.setFrame_(NSMakeRect(reset_x, y + 31.0, 26.0, 26.0))
            reset.setToolTip_(f"Reset {spec.label} to {spec.default}.")
            form_view.addSubview_(reset)

        self.parameter_form_view = form_view
        self.parameter_form_scroll.setDocumentView_(form_view)
        key_views = list(self.parameter_controls.values())
        for current, following in zip(key_views, key_views[1:]):
            current.setNextKeyView_(following)
        if key_views:
            key_views[-1].setNextKeyView_(self.parameter_advanced_checkbox)
            self.parameter_advanced_checkbox.setNextKeyView_(self.parameter_cancel_button)
            self.parameter_cancel_button.setNextKeyView_(self.parameter_apply_button)
            self.parameter_apply_button.setNextKeyView_(key_views[0])
        for index, button in enumerate(self.parameter_section_buttons):
            button.setEnabled_(SECTION_ORDER[index] != self.parameter_current_section)
        self._validate_parameter_draft()

    def _control_parameter_value(self, spec, control):
        if spec.value_type == "bool":
            raw = control.state() == NSControlStateValueOn
        elif spec.value_type == "choice":
            index = int(control.indexOfSelectedItem())
            raw = spec.choices[index][0]
        elif spec.value_type == "color":
            raw = self._parameter_color_text(control.color())
        else:
            raw = str(control.stringValue())
        return normalize_parameter_value(spec, raw)

    def _capture_parameter_controls(self, update_error=True):
        field_errors = {}
        for key, control in self.parameter_controls.items():
            spec = SPECS_BY_KEY[key]
            try:
                self.parameter_draft[key] = self._control_parameter_value(spec, control)
                stepper = self.parameter_steppers.get(key)
                if stepper is not None:
                    displayed, _minimum, _maximum, _increment = self._parameter_stepper_values(
                        spec, self.parameter_draft[key]
                    )
                    stepper.setDoubleValue_(displayed)
            except (TypeError, ValueError) as exc:
                field_errors[key] = str(exc)
        if update_error:
            self._validate_parameter_draft(field_errors)
        return field_errors

    def _validate_parameter_draft(self, field_errors=None):
        errors = dict(field_errors or {})
        if not errors:
            errors.update(validate_parameters(self.parameter_draft))
        if self.parameter_apply_button is not None:
            self.parameter_apply_button.setEnabled_(not errors)
        if self.parameter_error_label is not None:
            if errors:
                key, message = next(iter(errors.items()))
                label = SPECS_BY_KEY[key].label if key in SPECS_BY_KEY else key
                self.parameter_error_label.setStringValue_(f"{label}: {message}")
            else:
                self.parameter_error_label.setStringValue_("")
        return errors

    @objc.IBAction
    def showParameterEditor_(self, _sender):
        if self.parameter_editor_controller is None:
            self.parameter_editor_controller = CocoaParameterEditor.alloc().init()
            self.parameter_editor_controller.configure(
                title="Adventure Settings",
                sections=SECTION_ORDER,
                values=self.parameters,
                apply_callback=self._apply_parameters_from_shared_editor,
            )
        else:
            self.parameter_editor_controller.update_values(self.parameters)
        self.parameter_editor_controller.show()

    @objc.IBAction
    def selectParameterSection_(self, sender):
        self._capture_parameter_controls()
        index = int(sender.tag()) - 7000
        if 0 <= index < len(SECTION_ORDER):
            self.parameter_current_section = SECTION_ORDER[index]
            self._render_parameter_section()

    @objc.IBAction
    def toggleAdvancedParameters_(self, sender):
        self._capture_parameter_controls()
        self.parameter_show_advanced = sender.state() == NSControlStateValueOn
        self._render_parameter_section()

    @objc.IBAction
    def parameterValueChanged_(self, _sender):
        key = self.parameter_tag_to_key.get(int(_sender.tag()))
        self._capture_parameter_controls()
        if key == "maps.provider":
            self.performSelector_withObject_afterDelay_("refreshParameterSection:", None, 0.0)

    def refreshParameterSection_(self, _payload):
        self._render_parameter_section()

    @objc.IBAction
    def parameterStepperChanged_(self, sender):
        key = self.parameter_tag_to_key.get(int(sender.tag()))
        if key is None or key not in self.parameter_controls:
            return
        spec = SPECS_BY_KEY[key]
        value = float(sender.doubleValue())
        if spec.value_type == "int":
            text = str(int(round(value)))
        else:
            text = f"{value:g}"
        self.parameter_controls[key].setStringValue_(text)
        self._capture_parameter_controls()

    @objc.IBAction
    def resetParameter_(self, sender):
        key = self.parameter_tag_to_key.get(int(sender.tag()))
        if key is None:
            return
        self.parameter_draft[key] = SPECS_BY_KEY[key].default
        self._render_parameter_section()

    @objc.IBAction
    def resetAllParameters_(self, _sender):
        self.parameter_draft = default_parameters()
        self._render_parameter_section()

    @objc.IBAction
    def cancelParameterEditor_(self, _sender):
        self.parameter_draft = dict(self.parameters)
        if self.parameter_window is not None:
            self.parameter_window.orderOut_(None)

    def _sync_legacy_parameter_controls(self):
        self.time_lapse_media_min_fraction = float(self.parameters["timelapse.media_min_fraction"])
        self.track_map_edge_margin_fraction = float(self.parameters["trackmaps.edge_margin_fraction"])
        if hasattr(self, "media_place_names_checkbox"):
            add_places = bool(self.parameters.get("locations.add_place_names", True))
            self.media_place_names_checkbox.setState_(
                NSControlStateValueOn if add_places else NSControlStateValueOff
            )
        self._sync_gpx_source_controls()
        self._sync_music_controls()

    def _sync_gpx_source_controls(self):
        if not hasattr(self, "gpx_no_gpx_checkbox"):
            return
        media_only = str(self.parameters.get("trackmaps.route_source", "automatic")) == "media"
        self.gpx_no_gpx_checkbox.setState_(
            NSControlStateValueOn if media_only else NSControlStateValueOff
        )
        for control in (
            self.gpx_label,
            self.gpx_field,
            self.gpx_button,
            self.gpx_folder_button,
            self.gpx_edit_button,
            self.gpx_summary_label,
        ):
            control.setHidden_(media_only)

    def _select_gpx_journey_source(self):
        self.parameters["trackmaps.route_source"] = "gpx"
        self.workflow_assistant_state["journey_source_confirmed"] = True
        self._sync_gpx_source_controls()

    def _accept_existing_project_gpx(self, selected_path):
        """Select one existing project GPX without synchronously parsing it."""
        project_dir = self._resolve_project_directory(
            allow_create=False,
            update_gpx_field=False,
        )
        if project_dir is None:
            return False
        path = Path(selected_path).expanduser().resolve(strict=False)
        if (
            not path.is_file()
            or path.suffix.lower() != ".gpx"
            or path.parent != project_dir.resolve(strict=False)
        ):
            show_alert("The detected GPX file is no longer available.", str(path))
            return False
        self._set_gpx_field_value(path.name, manual=True)
        self._select_gpx_journey_source()
        self.gpx_ready_cache = None
        self.track_maps_status_cache = None
        self._refresh_project_file_menus()
        self.mark_dirty(immediate=True)
        self.start_async_project_status_refresh("GPX selected")
        self.set_status(f"Using GPX file {path.name}.")
        return True

    def _set_no_gpx_mode(self, media_only):
        if media_only:
            self.parameters["trackmaps.route_source"] = "media"
            self.workflow_assistant_state["journey_source_confirmed"] = True
            self.set_status(
                "No GPX selected; journey stages will use GPS positions from photos and videos."
            )
        else:
            has_gpx = self._has_existing_gpx_file()
            self.parameters["trackmaps.route_source"] = "gpx" if has_gpx else "automatic"
            self.workflow_assistant_state["journey_source_confirmed"] = bool(has_gpx)
            self.set_status(
                "GPX input enabled. Choose one or more GPX files for the Adventure."
            )
        self.gpx_ready_cache = None
        self.track_maps_status_cache = None
        self._sync_gpx_source_controls()
        self.mark_dirty(immediate=True)
        self.start_async_project_status_refresh("journey source changed")
        self.layout_window()
        self.refresh_section_status_indicators()

    def _sync_music_controls(self):
        if not hasattr(self, "no_music_checkbox"):
            return
        enabled = bool(self.parameters.get("audio.enabled", False))
        self.no_music_checkbox.setState_(
            NSControlStateValueOff if enabled else NSControlStateValueOn
        )
        for control in (
            self.music_label,
            self.music_field,
            self.music_playlist_select_button,
            self.music_choose_button,
            self.music_playlist_button,
            self.music_playlist_update_button,
            self.music_playlist_edit_button,
            self.music_summary_label,
        ):
            control.setHidden_(not enabled)

    @objc.IBAction
    def noMusicChanged_(self, sender):
        enabled = int(sender.state()) != NSControlStateValueOn
        self.parameters["audio.enabled"] = enabled
        if enabled:
            self._ensure_project_audio_directory(show_errors=True)
            self._refresh_music_summary()
        self._sync_music_controls()
        self.layout_window()
        self.mark_dirty(immediate=True)
        self.set_status(
            "Background music enabled. Use the folder icon to copy audio files "
            "and album directories into the project audio folder."
            if enabled
            else "No Music selected; slide shows will start without background music."
        )

    @objc.IBAction
    def noGPXNeededChanged_(self, sender):
        self._set_no_gpx_mode(int(sender.state()) == NSControlStateValueOn)

    @objc.IBAction
    def applyParameterEditor_(self, _sender):
        field_errors = self._capture_parameter_controls()
        errors = self._validate_parameter_draft(field_errors)
        if errors:
            return
        self._apply_parameter_values(self.parameter_draft, propagate_to_editor=True)
        self.parameter_window.orderOut_(None)

    def _ensure_control_table_window(self):
        if self.control_table_window is not None:
            return
        window = SlideShowControlTableWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180.0, 140.0, 980.0, 383.0),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.controller = self
        window.setTitle_("Slide Show Control File")
        window.setReleasedWhenClosed_(False)
        delegate = GPSTrackShowGUIControlTableWindowDelegate.alloc().initWithController_(self)
        window.setDelegate_(delegate)
        content = window.contentView()

        preview_checkbox = self._make_checkbox("Previews", "toggleControlTablePreviews:")
        preview_checkbox.setState_(NSControlStateValueOn if self.control_table_show_previews else 0)
        preview_checkbox.setFrame_(NSMakeRect(16.0, 344.0, 110.0, FIELD_HEIGHT))
        preview_checkbox.setToolTip_("Show or hide image preview icons. Keep this off for faster scrolling.")
        preview_checkbox.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
        content.addSubview_(preview_checkbox)

        media_filter_button = self._make_button(
            "Show Media Rows" if self.control_table_media_hidden else "Hide Media Rows",
            "toggleControlTableMediaRows:",
        )
        media_filter_button.setFrame_(NSMakeRect(132.0, 344.0, 160.0, FIELD_HEIGHT))
        media_filter_button.setToolTip_("Hide photo and video rows so stage, date, and map rows are easier to edit.")
        media_filter_button.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
        content.addSubview_(media_filter_button)

        insert_row_button = self._make_button("Insert Row", "insertControlRow:")
        insert_row_button.setFrame_(NSMakeRect(300.0, 344.0, 96.0, FIELD_HEIGHT))
        insert_row_button.setToolTip_("Insert a row after the selection (Cmd-I), then choose its directive type.")
        insert_row_button.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
        content.addSubview_(insert_row_button)

        search_field = NSSearchField.alloc().initWithFrame_(NSMakeRect(670.0, 344.0, 210.0, FIELD_HEIGHT))
        search_field.setPlaceholderString_("Search control file")
        search_field.setFont_(NSFont.systemFontOfSize_(13.0))
        search_field.setTarget_(self)
        search_field.setAction_("findNextControlTableMatch:")
        # NSSearchField may otherwise send its action after each character,
        # which selects a table row and retires the active field editor.
        search_field.setSendsSearchStringImmediately_(False)
        search_field.setSendsWholeSearchString_(True)
        search_field.setDelegate_(self)
        search_field.setToolTip_("Search all text and numbers in the control file. Cmd-F finds next; Shift-Cmd-F finds previous.")
        search_field.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        content.addSubview_(search_field)

        previous_search_button = self._make_icon_button(
            ["sf:chevron.up", "NSGoUpTemplate"],
            "Previous search result",
            "findPreviousControlTableMatch:",
        )
        previous_search_button.setFrame_(NSMakeRect(886.0, 344.0, FIELD_HEIGHT, FIELD_HEIGHT))
        previous_search_button.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        content.addSubview_(previous_search_button)

        next_search_button = self._make_icon_button(
            ["sf:chevron.down", "NSGoDownTemplate"],
            "Next search result",
            "findNextControlTableMatch:",
        )
        next_search_button.setFrame_(NSMakeRect(920.0, 344.0, FIELD_HEIGHT, FIELD_HEIGHT))
        next_search_button.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        content.addSubview_(next_search_button)

        search_status = self._make_label("", size=11.0)
        search_status.setAlignment_(2)
        search_status.setFrame_(NSMakeRect(600.0, 344.0, 64.0, FIELD_HEIGHT))
        search_status.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        content.addSubview_(search_status)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16.0, 72.0, 948.0, 265.0))
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(True)

        table = SlideShowControlTableView.alloc().initWithController_(self)
        table.setFrame_(scroll.bounds())
        table.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        table.setAllowsMultipleSelection_(True)
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setGridStyleMask_(NSTableViewSolidVerticalGridLineMask)
        table.setRowHeight_(22.0)
        table.registerForDraggedTypes_([CONTROL_TABLE_DRAG_TYPE])
        table.setDraggingSourceOperationMask_forLocal_(NSDragOperationMove, True)

        column_widths = {
            "preview": 64.0,
            "file_datetime": 128.0,
            "type": 65.0,
            "name": 230.0,
            "time": 54.0,
            "gps": 138.0,
            "place": 300.0,
        }
        for column_id in CONTROL_TABLE_COLUMNS:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(column_id))
            column.setWidth_(column_widths[column_id])
            column.setMinWidth_(62.0 if column_id == "type" else 45.0)
            column.headerCell().setStringValue_(CONTROL_TABLE_TITLES[column_id])
            if column_id == "preview":
                image_cell = NSImageCell.alloc().init()
                image_cell.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                column.setDataCell_(image_cell)
            elif column_id == "type":
                type_cell = NSComboBoxCell.alloc().init()
                type_cell.addItemsWithObjectValues_(list(CONTROL_ROW_TYPE_CHOICES))
                type_cell.setCompletes_(True)
                column.setDataCell_(type_cell)
            table.addTableColumn_(column)

        data_source = SlideShowControlTableDataSource.alloc().initWithController_(self)
        table.setDataSource_(data_source)
        table.setDelegate_(data_source)
        table.setTarget_(self)
        table.setDoubleAction_("openSelectedControlMedia:")
        disable_field_editor_text_checking(
            window.fieldEditor_forObject_(True, table)
        )
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)

        undo_button = self._make_icon_button(
            ["sf:arrow.uturn.backward", "NSUndoTemplate", "NSGoLeftTemplate"],
            "Undo",
            "undoControlTable:",
        )
        redo_button = self._make_icon_button(
            ["sf:arrow.uturn.forward", "NSRedoTemplate", "NSGoRightTemplate"],
            "Redo",
            "redoControlTable:",
        )
        up_button = self._make_button("Up", "moveControlRowsUp:")
        down_button = self._make_button("Down", "moveControlRowsDown:")
        copy_button = self._make_button("Copy", "copyControlRows:")
        cut_button = self._make_button("Cut", "cutControlRows:")
        delete_button = self._make_button("Delete", "deleteControlRows:")
        save_button = self._make_button("Save", "saveControlTable:")
        revert_button = self._make_button("Revert", "revertControlTable:")
        close_button = self._make_button("Close", "closeControlTable:")
        buttons = [
            (undo_button, FIELD_HEIGHT),
            (redo_button, FIELD_HEIGHT),
            (up_button, 86.0),
            (down_button, 86.0),
            (copy_button, 76.0),
            (cut_button, 70.0),
            (delete_button, 76.0),
            (save_button, 76.0),
            (revert_button, 76.0),
            (close_button, 76.0),
        ]
        x = 16.0
        for button, width in buttons:
            button.setFrame_(NSMakeRect(x, 28.0, width, FIELD_HEIGHT))
            button.setAutoresizingMask_(1)
            content.addSubview_(button)
            x += width + 8.0

        hint = self._make_label(
            "Double-click previews media; right-click for Finder or Start Slide Show Here. Drag rows. Cmd-I inserts; Cmd-F/Shift-Cmd-F searches; Cmd-C/V/X/Z edits.",
            size=12.0,
        )
        hint.setFrame_(NSMakeRect(16.0, 8.0, 948.0, 16.0))
        hint.setAutoresizingMask_(NSViewWidthSizable)
        content.addSubview_(hint)

        self.control_table_window = window
        self.control_table_window_delegate = delegate
        self.control_table_view = table
        self.control_table_data_source = data_source
        self.control_table_hint_label = hint
        self.control_table_preview_checkbox = preview_checkbox
        self.control_table_media_filter_button = media_filter_button
        self.control_table_search_field = search_field
        self.control_table_search_status = search_status

    def _control_table_search_indexes(self, query):
        """Return rows containing a case-insensitive control-file substring."""
        matches = control_table_search_indexes(self.control_table_rows, query)
        if self.control_table_media_hidden:
            matches = [
                index for index in matches
                if str(self.control_table_rows[index].get("type", "")).upper() not in {"IMG", "VID"}
            ]
        return matches

    def _refresh_control_table_search(self, select_first=False):
        if self.control_table_search_field is None:
            return
        query = str(self.control_table_search_field.stringValue() or "")
        changed = query.casefold() != self.control_table_search_query.casefold()
        self.control_table_search_query = query
        self.control_table_search_matches = self._control_table_search_indexes(query)
        if changed:
            self.control_table_search_position = -1
        if not self.control_table_search_matches:
            self.control_table_search_position = -1
        elif self.control_table_search_position >= len(self.control_table_search_matches):
            self.control_table_search_position = -1
        if select_first and self.control_table_search_matches:
            self.control_table_search_position = 0
            self._show_control_table_search_match()
        self._update_control_table_search_status()

    def _update_control_table_search_status(self):
        if self.control_table_search_status is None:
            return
        count = len(self.control_table_search_matches)
        if not self.control_table_search_query.strip():
            text = ""
        elif not count:
            text = "0/0"
        elif self.control_table_search_position < 0:
            text = f"0/{count}"
        else:
            text = f"{self.control_table_search_position + 1}/{count}"
        self.control_table_search_status.setStringValue_(text)

    def _show_control_table_search_match(self):
        if not self.control_table_search_matches or self.control_table_search_position < 0:
            return
        editor = self.control_table_search_field.currentEditor() if self.control_table_search_field is not None else None
        selected_range = editor.selectedRange() if editor is not None else None
        row_index = self.control_table_search_matches[self.control_table_search_position]
        self._select_control_table_indexes([row_index])
        self._scroll_control_table_model_row_to_visible(row_index)
        if self.control_table_window is not None and self.control_table_search_field is not None:
            self.control_table_window.makeFirstResponder_(self.control_table_search_field)
            restored_editor = self.control_table_search_field.currentEditor()
            if restored_editor is not None and selected_range is not None:
                restored_editor.setSelectedRange_(selected_range)
        self._update_control_table_search_status()

    def _navigate_control_table_search(self, direction):
        if self.control_table_search_field is None or self.control_table_window is None:
            return
        self.control_table_window.makeFirstResponder_(self.control_table_search_field)
        self._refresh_control_table_search(select_first=False)
        if not self.control_table_search_matches:
            return
        self.control_table_search_position = next_control_table_search_position(
            self.control_table_search_position,
            len(self.control_table_search_matches),
            direction,
        )
        self._show_control_table_search_match()

    @objc.IBAction
    def findNextControlTableMatch_(self, _sender):
        self._navigate_control_table_search(1)

    @objc.IBAction
    def findPreviousControlTableMatch_(self, _sender):
        self._navigate_control_table_search(-1)

    def _selected_control_table_indexes(self):
        if self.control_table_view is None:
            return []
        selected = self.control_table_view.selectedRowIndexes()
        indexes = []
        for view_index, model_index in enumerate(self.control_table_view_model_indexes):
            if selected.containsIndex_(view_index):
                indexes.append(model_index)
        return indexes

    def _rebuild_control_table_view_index(self):
        self.control_table_view_model_indexes = visible_control_row_indexes(
            self.control_table_rows,
            self.control_table_media_hidden,
        )
        self.control_table_model_to_view_index = {
            model_index: view_index
            for view_index, model_index in enumerate(self.control_table_view_model_indexes)
        }

    def control_table_model_index_for_view_row(self, view_index):
        try:
            return self.control_table_view_model_indexes[int(view_index)]
        except (IndexError, TypeError, ValueError):
            return None

    def control_table_model_insertion_index_for_view_row(self, view_index):
        view_index = max(0, int(view_index))
        if view_index < len(self.control_table_view_model_indexes):
            return self.control_table_view_model_indexes[view_index]
        return len(self.control_table_rows)

    def _scroll_control_table_model_row_to_visible(self, model_index):
        if self.control_table_view is None:
            return
        view_index = self.control_table_model_to_view_index.get(int(model_index))
        if view_index is not None:
            self.control_table_view.scrollRowToVisible_(view_index)

    def _select_control_table_indexes(self, indexes):
        if self.control_table_view is None:
            return
        view_indexes = sorted(
            self.control_table_model_to_view_index[index]
            for index in indexes
            if index in self.control_table_model_to_view_index
        )
        if not view_indexes:
            self.control_table_view.deselectAll_(None)
            return
        selection = NSIndexSet.indexSetWithIndexesInRange_((view_indexes[0], 1))
        mutable_selection = selection.mutableCopy()
        for index in view_indexes[1:]:
            mutable_selection.addIndex_(index)
        self.control_table_view.selectRowIndexes_byExtendingSelection_(mutable_selection, False)

    def _snapshot_control_table(self):
        return [clone_slideshow_row(row) for row in self.control_table_rows]

    def _push_control_table_undo(self):
        self.control_table_undo_stack.append(self._snapshot_control_table())
        self.control_table_redo_stack.clear()
        self._schedule_control_table_recovery()

    def _schedule_control_table_recovery(self):
        """Debounce a crash-recovery write while the table is being edited."""
        self.control_table_dirty = True
        if self.control_table_recovery_timer is not None:
            self.control_table_recovery_timer.invalidate()
        self.control_table_recovery_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0,
            self,
            "autosaveControlTableRecovery:",
            None,
            False,
        )

    def _control_table_text(self):
        lines = [serialize_slideshow_control_row(row) for row in self.control_table_rows]
        return "\n".join(lines) + ("\n" if lines else "")

    def _validate_control_table_music_rows(self):
        for index, row in enumerate(self.control_table_rows, start=1):
            row_type = str(row.get("type", "")).strip().upper()
            if not row_type:
                show_alert(f"Row {index} has no type.", "Choose a Type before saving the control file.")
                return False
            if row_type != "MUS":
                continue
            try:
                parse_music_parameters(str(row.get("name", "")))
            except MusicSyntaxError as exc:
                show_alert(f"Invalid music directive in row {index}.", str(exc))
                return False
        return True

    @objc.IBAction
    def autosaveControlTableRecovery_(self, _sender):
        """Persist the current edit state without replacing the user's list."""
        self.control_table_recovery_timer = None
        if not self.control_table_dirty or self.control_table_path is None:
            return
        try:
            recovery_path = control_table_recovery_path(self.control_table_path)
            text = self._control_table_text()
            write_text_atomic(recovery_path, text)
            now = time.time()
            if now - self.control_table_last_snapshot_time >= 60.0:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                snapshot = recovery_path.parent / f"{self.control_table_path.name}.{stamp}.autosave.lst"
                write_text_atomic(snapshot, text)
                self.control_table_last_snapshot_time = now
                snapshots = sorted(
                    recovery_path.parent.glob(f"{self.control_table_path.name}.*.autosave.lst"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                for obsolete in snapshots[20:]:
                    obsolete.unlink(missing_ok=True)
            self.set_status(f"Backed up unsaved control-file edits for {self.control_table_path.name}")
        except OSError as exc:
            self.set_status(f"Could not back up control-file edits: {exc}")

    def _reload_control_table(self):
        selected_model_indexes = self._selected_control_table_indexes()
        self._rebuild_control_table_view_index()
        if self.control_table_view is not None:
            self.control_table_view.reloadData()
            self._select_control_table_indexes(selected_model_indexes)
        self._refresh_control_table_search(select_first=False)

    def resolve_control_row_path(self, row):
        name = str(row.get("name", "")).strip()
        if not name:
            return None
        row_type = str(row.get("type", "")).upper()
        path = Path(name).expanduser()
        if path.is_absolute():
            if row_type in {"MAP", "TRK", "BEF", "AFT", "LOC"}:
                variant = resolve_track_map_variant(
                    path,
                    prefer_time_lapse=False,
                )
                if variant is not None:
                    return variant.resolve(strict=False)
            return path
        candidates = []
        if self.control_table_path is not None:
            candidates.append(self.control_table_path.parent / path)
            candidates.append(self.control_table_path.parent / "trackimages" / path.name)
        if self.current_project_dir is not None:
            candidates.append(self.current_project_dir / path)
            candidates.append(self.current_project_dir / "trackimages" / path.name)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve(strict=False)
            if row_type in {"MAP", "TRK", "BEF", "AFT", "LOC"}:
                variant = resolve_track_map_variant(
                    candidate,
                    prefer_time_lapse=False,
                )
                if variant is not None:
                    return variant.resolve(strict=False)
        return candidates[0].resolve(strict=False) if candidates else path.resolve(strict=False)

    def preview_image_for_control_row(self, row_index):
        if not self.control_table_show_previews:
            return None
        if row_index < 0 or row_index >= len(self.control_table_rows):
            return None
        row = self.control_table_rows[row_index]
        row_type = str(row.get("type", "")).upper()
        if not is_media_row_type(row_type):
            return None
        path = self.resolve_control_row_path(row)
        if path is None:
            return None
        cache_key = (str(path), row_type)
        if cache_key in self.control_table_preview_cache:
            return self.control_table_preview_cache[cache_key]
        image = None
        if row_type in {"IMG", "MAP", "TRK", "BEF", "AFT", "LOC"} and path.exists():
            image = NSImage.alloc().initWithContentsOfFile_(str(path))
        if image is None and path.exists():
            image = NSWorkspace.sharedWorkspace().iconForFile_(str(path))
        self.control_table_preview_cache[cache_key] = image
        return image

    def file_datetime_for_control_row(self, row_index):
        if row_index < 0 or row_index >= len(self.control_table_rows):
            return ""
        row = self.control_table_rows[row_index]
        row_type = str(row.get("type", "")).upper()
        if not is_media_row_type(row_type):
            return ""
        if row_type in {"IMG", "VID"}:
            time_text = str(row.get("time", "")).strip()
            date_text = ""
            for previous_row in reversed(self.control_table_rows[:row_index]):
                if str(previous_row.get("type", "")).upper() == "DAT":
                    date_text = str(previous_row.get("name", "")).strip()
                    break
            date_match = re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", date_text)
            compact_date = date_match.group(0) if date_match else date_text
            if compact_date and time_text:
                return f"{compact_date} {time_text}"
            if compact_date:
                return compact_date
        path = self.resolve_control_row_path(row)
        if path is None or not path.exists():
            return ""
        cache_key = str(path)
        if cache_key in self.control_table_file_datetime_cache:
            return self.control_table_file_datetime_cache[cache_key]
        if row_type in {"TRK", "BEF", "AFT", "LOC"}:
            value = self.track_datetime_from_sidecar(path)
            if value:
                self.control_table_file_datetime_cache[cache_key] = value
                return value
        try:
            value = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        except OSError:
            value = ""
        self.control_table_file_datetime_cache[cache_key] = value
        return value

    def track_datetime_from_sidecar(self, image_path):
        sidecar_path = Path(image_path).with_suffix(".json")
        if not sidecar_path.exists():
            return ""
        try:
            metadata = read_photo_metadata(sidecar_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(metadata, dict):
            return ""
        for key in ("track_start_time", "start_time", "track_datetime", "media_map_date"):
            value = str(metadata.get(key, "") or "").strip()
            if not value:
                continue
            parsed = self._parse_sidecar_datetime(value)
            if parsed is not None:
                return parsed.strftime("%d.%m.%Y %H:%M")
            return value
        track_date = str(metadata.get("track_date", "") or "").strip()
        if track_date:
            match = re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", track_date)
            return match.group(0) if match else track_date
        return ""

    def _parse_sidecar_datetime(self, value):
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.astimezone() if parsed.tzinfo is not None else parsed
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.astimezone() if parsed.tzinfo is not None else parsed

    @objc.IBAction
    def toggleControlTablePreviews_(self, _sender):
        self.control_table_show_previews = bool(
            self.control_table_preview_checkbox is not None
            and self.control_table_preview_checkbox.state() == NSControlStateValueOn
        )
        if not self.control_table_show_previews:
            self.control_table_preview_cache = {}
        self._reload_control_table()

    @objc.IBAction
    def toggleControlTableMediaRows_(self, _sender):
        selected_indexes = self._selected_control_table_indexes()
        hide_media = not self.control_table_media_hidden
        anchor_index = control_table_filter_anchor_index(
            self.control_table_rows,
            selected_indexes,
            hide_media,
        )
        self.control_table_media_hidden = hide_media
        if self.control_table_media_filter_button is not None:
            self.control_table_media_filter_button.setTitle_(
                "Show Media Rows" if hide_media else "Hide Media Rows"
            )
            self.control_table_media_filter_button.setToolTip_(
                "Show photo and video rows while keeping the selected stage visible." if hide_media
                else "Hide photo and video rows so stage, date, and map rows are easier to edit."
            )
        self._reload_control_table()
        if anchor_index is not None:
            self._select_control_table_indexes([anchor_index])
            self._scroll_control_table_model_row_to_visible(anchor_index)

    def control_media_items(self):
        items = []
        current_date = ""
        for index, row in enumerate(self.control_table_rows):
            row_type = str(row.get("type", "")).upper()
            if row_type == "DAT":
                current_date = str(row.get("name", ""))
                continue
            if not is_media_row_type(row_type):
                continue
            path = self.resolve_control_row_path(row)
            if path is None or not path.exists():
                continue
            kind = "video" if row_type == "VID" else "image"
            time_text = str(row.get("time", ""))
            date_time_text = " ".join(part for part in (current_date, time_text) if part).strip()
            items.append({
                "index": index,
                "row": row,
                "path": path,
                "kind": kind,
                "image": None,
                "name": str(row.get("name", "")),
                "time": date_time_text,
                "gps": str(row.get("gps", "")),
                "place": str(row.get("place", "")),
            })
        return items

    def project_media_items(self):
        project_dir = self.current_project_dir
        if project_dir is None or not project_dir.exists():
            return []
        items = []
        media_paths = sorted(
            [
                path
                for path in project_dir.iterdir()
                if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
            ],
            key=lambda path: path.name.lower(),
        )
        for index, path in enumerate(media_paths):
            row_type = "VID" if path.suffix.lower() in VIDEO_EXTENSIONS else "IMG"
            metadata = self.metadata_for_project_media_path(path)
            time_text = metadata.get("time", "")
            gps_text = metadata.get("gps", "")
            place_text = metadata.get("place", "")
            items.append({
                "index": index,
                "row": {
                    "type": row_type,
                    "name": path.name,
                    "time": time_text,
                    "gps": gps_text,
                    "place": place_text,
                },
                "path": path.resolve(strict=False),
                "kind": "video" if row_type == "VID" else "image",
                "image": None,
                "name": path.name,
                "metadata": metadata.get("metadata", "Invalid"),
                "metadata_reason": metadata.get("metadata_reason", ""),
                "freshness": metadata.get("freshness", "Unknown"),
                "time": time_text,
                "gps": gps_text,
                "place": place_text,
                "sort_name": path.name.lower(),
                "sort_datetime": metadata.get("sort_datetime"),
            })
        return items

    def included_control_media_names(self):
        control_file_path = self._control_file_path()
        rows = self.control_table_rows
        if control_file_path is not None and control_file_path.exists():
            try:
                rows = [
                    parse_slideshow_control_line(line)
                    for line in control_file_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except OSError:
                rows = self.control_table_rows
        names = set()
        for row in rows:
            if str(row.get("type", "")).upper() in {"IMG", "VID"}:
                names.add(Path(str(row.get("name", "")).strip()).name.lower())
        return names

    def media_browser_table_rows(self):
        included_names = self.included_control_media_names()
        rows = []
        items = self.project_media_items()
        for index, item in enumerate(items):
            path = Path(item["path"])
            row_type = "VID" if path.suffix.lower() in VIDEO_EXTENSIONS else "IMG"
            rows.append({
                "included": "yes" if path.name.lower() in included_names else "",
                "type": row_type,
                "metadata": str(item.get("metadata", "Invalid")),
                "freshness": str(item.get("freshness", "Unknown")),
                "name": path.name,
                "time": str(item.get("time", "")),
                "gps": str(item.get("gps", "")),
                "place": str(item.get("place", "")),
            })
            item["index"] = index
        return self.sorted_media_browser_rows_and_items(rows, items)

    def sorted_media_browser_rows_and_items(self, rows, items):
        column = self.media_browser_sort_column or "name"
        ascending = bool(self.media_browser_sort_ascending)

        def sort_value(pair):
            row, item = pair
            if column == "included":
                return (0 if row.get("included") else 1, row.get("name", "").lower())
            if column == "time":
                timestamp = item.get("sort_datetime")
                try:
                    date_value = timestamp.timestamp() if timestamp is not None else float("inf")
                except (AttributeError, OSError, OverflowError, ValueError):
                    date_value = float("inf")
                return (date_value, row.get("name", "").lower())
            return (str(row.get(column, "")).lower(), row.get("name", "").lower())

        pairs = list(zip(rows, items))
        if column == "time":
            dated_pairs = [pair for pair in pairs if pair[1].get("sort_datetime") is not None]
            undated_pairs = [pair for pair in pairs if pair[1].get("sort_datetime") is None]
            dated_pairs.sort(key=sort_value, reverse=not ascending)
            undated_pairs.sort(key=lambda pair: pair[0].get("name", "").lower())
            pairs = dated_pairs + undated_pairs
        else:
            pairs.sort(key=sort_value, reverse=not ascending)
        sorted_rows = []
        sorted_items = []
        for index, (row, item) in enumerate(pairs):
            sorted_rows.append(row)
            item["index"] = index
            sorted_items.append(item)
        return sorted_rows, sorted_items

    def metadata_for_project_media_path(self, path):
        media_path = Path(path)
        status, metadata, reason = validate_media_sidecar(media_path)
        if status != "available" or not isinstance(metadata, dict):
            return {
                "metadata": "Missing" if status == "missing" else "Invalid",
                "freshness": "Missing" if status == "missing" else "Invalid",
                "metadata_reason": str(reason or ""),
                "time": "",
                "gps": "",
                "place": "",
                "sort_datetime": None,
            }

        sort_datetime = None
        time_text = ""
        datetime_text = str(metadata.get("datetime_iso", "")).strip()
        if datetime_text:
            try:
                sort_datetime = datetime.fromisoformat(datetime_text.replace("Z", "+00:00"))
                time_text = sort_datetime.astimezone().strftime("%d.%m.%Y %H:%M")
            except ValueError:
                return {
                    "metadata": "Invalid",
                    "freshness": "Invalid",
                    "metadata_reason": "sidecar has no valid exposure date/time",
                    "time": "",
                    "gps": "",
                    "place": "",
                    "sort_datetime": None,
                }

        latitude = metadata.get("latitude")
        longitude = metadata.get("longitude")
        gps_text = ""
        try:
            if latitude is not None and longitude is not None:
                gps_text = f"{float(latitude):.6f}, {float(longitude):.6f}"
        except (TypeError, ValueError):
            gps_text = ""
        place = metadata.get("place")
        place_text = str(place).strip() if isinstance(place, str) and not is_missing_place_text(place) else ""
        return {
            "metadata": "Available",
            "freshness": media_sidecar_freshness(media_path, metadata).title(),
            "metadata_reason": "",
            "time": time_text,
            "gps": gps_text,
            "place": place_text,
            "sort_datetime": sort_datetime,
        }

    def load_media_viewer_image(self, item):
        if item is None or item.get("kind") != "image":
            return
        path = Path(item["path"])
        cache_key = str(path)
        if cache_key in self.media_viewer_image_cache:
            item["image"] = self.media_viewer_image_cache[cache_key]
            return
        self.set_status(f"Loading image {path.name}...")
        self.set_progress(0.0, 1.0)
        image = NSImage.alloc().initWithContentsOfFile_(str(path))
        self.set_progress(1.0, 1.0)
        self.reset_progress()
        self.set_status(f"Loaded image {path.name}")
        self.media_viewer_image_cache[cache_key] = image
        item["image"] = image

    def current_media_viewer_item(self):
        if not self.media_viewer_items:
            return None
        self.media_viewer_index = max(0, min(self.media_viewer_index, len(self.media_viewer_items) - 1))
        item = self.media_viewer_items[self.media_viewer_index]
        self.load_media_viewer_image(item)
        return item

    def stop_media_viewer_video(self):
        """Pause and detach the embedded preview player safely."""
        if self.media_viewer_video_player is not None:
            try:
                if hasattr(self.media_viewer_video_player, "cancelPendingPrerolls"):
                    self.media_viewer_video_player.cancelPendingPrerolls()
                self.media_viewer_video_player.pause()
            except Exception:
                pass
        if self.media_viewer_video_view is not None:
            try:
                self.media_viewer_video_view.setPlayer_(None)
                self.media_viewer_video_view.removeFromSuperview()
            except Exception:
                pass
        self.media_viewer_video_player = None
        self.media_viewer_video_view = None
        self.media_viewer_video_path = None
        self.media_viewer_video_preroll = None

    def show_media_viewer_video(self, item):
        """Show one paused video at its first frame with native controls."""
        path = Path(item["path"]).resolve(strict=False)
        if self.media_viewer_video_path == path and self.media_viewer_video_view is not None:
            return
        self.stop_media_viewer_video()
        if (
            not AVKIT_MEDIA_VIEWER_AVAILABLE
            or AVPlayer is None
            or AVPlayerView is None
            or self.media_viewer_view is None
        ):
            return
        player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(path)))
        bounds = self.media_viewer_view.bounds()
        video_frame = NSMakeRect(
            bounds.origin.x + 18.0,
            bounds.origin.y + 120.0,
            max(1.0, bounds.size.width - 36.0),
            max(1.0, bounds.size.height - 138.0),
        )
        player_view = AVPlayerView.alloc().initWithFrame_(video_frame)
        player_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        if hasattr(player_view, "setControlsStyle_"):
            player_view.setControlsStyle_(AVPlayerViewControlsStyleInline)
        if hasattr(player_view, "setShowsFullScreenToggleButton_"):
            player_view.setShowsFullScreenToggleButton_(True)
        player_view.setPlayer_(player)
        self.media_viewer_view.addSubview_(player_view)
        self.media_viewer_video_player = player
        self.media_viewer_video_view = player_view
        self.media_viewer_video_path = path
        if CMTimeMake is not None:
            try:
                player.seekToTime_(CMTimeMake(0, 600))
            except Exception:
                pass
        player.pause()
        if hasattr(player, "prerollAtRate_completionHandler_"):
            def preroll_finished(_finished):
                if self.media_viewer_video_player is player:
                    player.pause()

            self.media_viewer_video_preroll = preroll_finished
            try:
                player.prerollAtRate_completionHandler_(1.0, preroll_finished)
            except Exception:
                self.media_viewer_video_preroll = None

    def toggle_media_viewer_video_playback(self):
        """Toggle the embedded video's play/pause state."""
        player = self.media_viewer_video_player
        if player is None:
            return
        try:
            if float(player.rate()) > 0.0:
                player.pause()
            else:
                player.play()
        except Exception:
            pass

    def sync_control_table_to_media_viewer_item(self):
        """Select the control row represented by the visible viewer item."""
        row_index = media_viewer_control_row_index(
            self.media_viewer_source,
            self.media_viewer_items,
            self.media_viewer_index,
        )
        if row_index is None:
            return
        self._select_control_table_indexes([row_index])
        self._scroll_control_table_model_row_to_visible(row_index)

    def update_media_viewer_content(self):
        """Update image/video presentation and its originating table row."""
        item = self.current_media_viewer_item()
        if item is not None and item.get("kind") == "video":
            self.show_media_viewer_video(item)
        else:
            self.stop_media_viewer_video()
        self.sync_control_table_to_media_viewer_item()
        if self.media_viewer_view is not None:
            self.media_viewer_view.setNeedsDisplay_(True)

    def update_media_viewer_title(self):
        if self.media_viewer_window is None:
            return
        item = self.current_media_viewer_item()
        if item is None:
            self.media_viewer_window.setTitle_("Slide Show Media")
            return
        self.media_viewer_window.setTitle_(
            f"Slide Show Media {self.media_viewer_index + 1}/{len(self.media_viewer_items)} - {Path(item['path']).name}"
        )

    def sorted_project_media_items(self, items):
        def datetime_sort_value(item):
            value = item.get("sort_datetime")
            if value is None:
                return float("inf")
            try:
                return value.timestamp()
            except (AttributeError, OSError, OverflowError, ValueError):
                return float("inf")

        if self.media_viewer_sort_mode == "date":
            return sorted(
                items,
                key=lambda item: (
                    datetime_sort_value(item),
                    item.get("sort_name", "").lower(),
                ),
            )
        return sorted(items, key=lambda item: item.get("sort_name", str(item.get("name", "")).lower()))

    def toggle_media_viewer_sort(self):
        if self.media_viewer_source != "project" or not self.media_viewer_items:
            self.set_status("Sort toggle is available for the Photos & Video Clips viewer.")
            return
        current_item = self.current_media_viewer_item()
        current_path = Path(current_item["path"]) if current_item is not None else None
        self.media_viewer_sort_mode = "date" if self.media_viewer_sort_mode == "filename" else "filename"
        self.media_viewer_items = self.sorted_project_media_items(self.media_viewer_items)
        if current_path is not None:
            for index, item in enumerate(self.media_viewer_items):
                if Path(item["path"]) == current_path:
                    self.media_viewer_index = index
                    break
        self.update_media_viewer_title()
        self.media_viewer_view.setNeedsDisplay_(True)
        label = "photo creation date" if self.media_viewer_sort_mode == "date" else "filename"
        self.set_status(f"Media viewer sorted by {label}.")

    def show_media_viewer_item(self, index, items=None):
        requested_index = int(index)
        self.media_viewer_source = "project" if items is not None else "control"
        self.media_viewer_items = list(items) if items is not None else self.control_media_items()
        target_path = None
        if 0 <= requested_index < len(self.media_viewer_items):
            try:
                target_path = Path(self.media_viewer_items[requested_index]["path"])
            except (KeyError, TypeError):
                target_path = None
        if self.media_viewer_source == "project":
            self.media_viewer_items = self.sorted_project_media_items(self.media_viewer_items)
        if not self.media_viewer_items:
            show_alert("No media file is available for the selected row.")
            return
        self.media_viewer_index = max(0, min(requested_index, len(self.media_viewer_items) - 1))
        if target_path is not None:
            for sorted_index, item in enumerate(self.media_viewer_items):
                if Path(item["path"]) == target_path:
                    self.media_viewer_index = sorted_index
                    break
        if self.media_viewer_window is None:
            view = SlideShowMediaViewerView.alloc().initWithController_(self)
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(220.0, 140.0, 960.0, 640.0),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setReleasedWhenClosed_(False)
            window.setContentAspectRatio_(NSMakeSize(3.0, 2.0))
            close_button = self._make_button("Close", "closeMediaViewer:")
            close_button.setFrame_(NSMakeRect(12.0, 10.0, 82.0, FIELD_HEIGHT))
            close_button.setAutoresizingMask_(0)
            content = NSView.alloc().initWithFrame_(window.contentView().bounds())
            content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            view.setFrame_(NSMakeRect(0.0, FIELD_HEIGHT + 18.0, content.bounds().size.width, content.bounds().size.height - FIELD_HEIGHT - 18.0))
            view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            content.addSubview_(view)
            content.addSubview_(close_button)
            window.setContentView_(content)
            delegate = SlideShowMediaViewerDelegate.alloc().initWithController_(self)
            window.setDelegate_(delegate)
            self.media_viewer_window = window
            self.media_viewer_view = view
            self.media_viewer_delegate = delegate
        self.update_media_viewer_title()
        self.update_media_viewer_content()
        self.media_viewer_view.hint_until = time.time() + 3.0
        self.media_viewer_view.setNeedsDisplay_(True)
        if self.media_viewer_hint_timer is not None:
            self.media_viewer_hint_timer.invalidate()
        self.media_viewer_hint_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            3.05, self, "hideMediaViewerHint:", None, False
        )
        self.media_viewer_window.makeKeyAndOrderFront_(None)
        self.media_viewer_window.makeFirstResponder_(self.media_viewer_view)

    @objc.IBAction
    def hideMediaViewerHint_(self, _sender):
        self.media_viewer_hint_timer = None
        if self.media_viewer_view is not None:
            self.media_viewer_view.hint_until = 0.0
            self.media_viewer_view.setNeedsDisplay_(True)
        NSApp().activateIgnoringOtherApps_(True)

    @objc.IBAction
    def openSelectedControlMedia_(self, _sender):
        indexes = self._selected_control_table_indexes()
        if not indexes:
            return
        media_indexes = [item["index"] for item in self.control_media_items()]
        selected_index = next((index for index in indexes if index in media_indexes), None)
        if selected_index is None:
            show_alert("Selected row has no associated media file.")
            return
        self.show_media_viewer_item(media_indexes.index(selected_index))

    def selectedControlRowsHaveViewableFiles(self):
        """Return whether the current table selection resolves to a file."""
        for index in self._selected_control_table_indexes():
            if index < 0 or index >= len(self.control_table_rows):
                continue
            row = self.control_table_rows[index]
            if not is_media_row_type(str(row.get("type", ""))):
                continue
            path = self.resolve_control_row_path(row)
            if path is not None and path.is_file():
                return True
        return False

    def selectedControlRowsCanStartSlideShow(self):
        """Return whether one selected model row can be used as a player start."""
        indexes = self._selected_control_table_indexes()
        return bool(
            indexes
            and self.control_table_path is not None
            and self.control_table_path.is_file()
        )

    @objc.IBAction
    def startSlideShowFromSelectedControlRow_(self, _sender):
        """Launch the player once at the selected control-file model row."""
        indexes = self._selected_control_table_indexes()
        if not indexes or self.control_table_path is None:
            show_alert("Select a control-file row first.")
            return
        if self.slideshow_process is not None and self.slideshow_process.poll() is None:
            self.set_status("Slide show is already running.")
            return
        if self.control_table_dirty:
            if self.control_table_window is not None:
                self.control_table_window.makeFirstResponder_(None)
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Save control-file changes and start here?")
            alert.setInformativeText_(
                "The slide show can start at the selected row after the current edits are saved."
            )
            alert.addButtonWithTitle_("Save and Start")
            alert.addButtonWithTitle_("Cancel")
            if int(alert.runModal()) != 1000:
                return
            if not self._save_control_table_changes():
                return
        control_path = self._control_file_path()
        if (
            control_path is None
            or control_path.resolve(strict=False)
            != self.control_table_path.resolve(strict=False)
        ):
            show_alert(
                "The editor is not showing the active control file.",
                "Select this control file in the main window before starting the slide show.",
            )
            return
        try:
            lines = [
                line.strip()
                for line in control_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError as exc:
            show_alert("Could not read the slide show control file.", str(exc))
            return
        row_index = min(indexes)
        if not 0 <= row_index < len(lines):
            show_alert("The selected control-file row is no longer available.")
            return
        transient_position = {
            "version": 2,
            "completed": False,
            "control_file": str(control_path.resolve(strict=False)),
            "playlist_index": row_index,
            "line_text": lines[row_index],
        }
        self._start_slide_show(
            continue_previous=False,
            transient_resume_position=transient_position,
        )

    @objc.IBAction
    def openSelectedControlRowsInFinder_(self, _sender):
        """Reveal selected control-file media or map files in Finder."""
        paths = []
        seen = set()
        for index in self._selected_control_table_indexes():
            if index < 0 or index >= len(self.control_table_rows):
                continue
            row = self.control_table_rows[index]
            if not is_media_row_type(str(row.get("type", ""))):
                continue
            path = self.resolve_control_row_path(row)
            if path is None or not path.is_file():
                continue
            resolved = path.resolve(strict=False)
            if str(resolved) not in seen:
                paths.append(resolved)
                seen.add(str(resolved))
        if not paths:
            show_alert("Selected row has no associated file.")
            return
        urls = [NSURL.fileURLWithPath_(str(path)) for path in paths]
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(urls)
        self.set_status(f"Opened Finder with {len(paths)} selected file(s).")

    @objc.IBAction
    def closeMediaViewer_(self, _sender):
        self.close_media_viewer()

    def close_media_viewer(self):
        if self.media_viewer_hint_timer is not None:
            self.media_viewer_hint_timer.invalidate()
            self.media_viewer_hint_timer = None
        if self.media_viewer_window is not None:
            self.stop_media_viewer_video()
            self.media_viewer_window.close()

    def show_previous_control_media(self):
        if not self.media_viewer_items:
            return
        self.media_viewer_index = (self.media_viewer_index - 1) % len(self.media_viewer_items)
        self.update_media_viewer_title()
        self.update_media_viewer_content()

    def show_next_control_media(self):
        if not self.media_viewer_items:
            return
        self.media_viewer_index = (self.media_viewer_index + 1) % len(self.media_viewer_items)
        self.update_media_viewer_title()
        self.update_media_viewer_content()

    def move_control_rows_by_drag(self, drop_row):
        indexes = sorted(index for index in self.control_table_drag_indexes if 0 <= index < len(self.control_table_rows))
        self.control_table_drag_indexes = []
        if not indexes:
            return False
        drop_row = max(0, min(int(drop_row), len(self.control_table_rows)))
        if drop_row in indexes or drop_row == indexes[-1] + 1:
            return False
        self._push_control_table_undo()
        moving_rows = [self.control_table_rows[index] for index in indexes]
        remaining_rows = [
            row for index, row in enumerate(self.control_table_rows)
            if index not in set(indexes)
        ]
        adjusted_drop_row = drop_row - sum(1 for index in indexes if index < drop_row)
        self.control_table_rows = (
            remaining_rows[:adjusted_drop_row]
            + moving_rows
            + remaining_rows[adjusted_drop_row:]
        )
        self._reload_control_table()
        self._select_control_table_indexes(range(adjusted_drop_row, adjusted_drop_row + len(moving_rows)))
        return True

    def _show_control_table_window(self, path):
        """Raise the retained editor without rebuilding its table model."""
        self._ensure_control_table_window()
        self.control_table_window.setTitle_(f"Slide Show Control File: {Path(path).name}")
        self.control_table_window.makeKeyAndOrderFront_(None)
        self.control_table_window.displayIfNeeded()
        NSApp().activateIgnoringOtherApps_(True)

    def refreshControlFileDisplayAfterOpen_(self, _sender):
        """Finish nonessential control-file status work after the editor is visible."""
        pending = self.control_table_pending_refresh
        self.control_table_pending_refresh = None
        if pending is None:
            return
        pending_path, control_lines = pending
        if self.control_table_path is None:
            return
        if self.control_table_path.resolve(strict=False) != pending_path.resolve(strict=False):
            return
        active_path = self._control_file_path()
        if active_path is None or active_path.resolve(strict=False) != pending_path.resolve(strict=False):
            return
        self.refresh_control_file_display(
            rows=self.control_table_rows,
            control_lines=control_lines,
        )

    def load_slideshow_control_file(self, control_file_path, force_reload=False):
        path = Path(control_file_path).expanduser().resolve(strict=False)
        recovery_path = control_table_recovery_path(path)
        recovery_is_newer = control_file_recovery_is_newer(path, recovery_path)
        current_signature = control_file_signature(path)
        same_loaded_path = bool(
            self.control_table_path is not None
            and self.control_table_path.resolve(strict=False) == path
        )
        if (
            not force_reload
            and same_loaded_path
            and self.control_table_window is not None
            and not self.control_table_dirty
            and not recovery_is_newer
            and current_signature is not None
            and current_signature == self.control_table_file_signature
        ):
            self._show_control_table_window(path)
            return True

        source_path = path
        if recovery_is_newer:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Unsaved control-file edits were found.")
            alert.setInformativeText_(
                "The previous editing session did not save these changes. Restore the autosaved version?"
            )
            alert.addButtonWithTitle_("Restore")
            alert.addButtonWithTitle_("Ignore")
            if int(alert.runModal()) == 1000:
                source_path = recovery_path
            else:
                recovery_path.unlink(missing_ok=True)
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            show_alert("Could not load slide show control file.", str(exc))
            return False
        self.control_table_path = path
        self.control_table_rows = [parse_slideshow_control_line(line) for line in lines if line.strip()]
        self.control_table_undo_stack = []
        self.control_table_redo_stack = []
        self.control_table_dirty = source_path == recovery_path
        self.control_table_last_snapshot_time = time.time() if self.control_table_dirty else 0.0
        self.control_table_file_signature = current_signature
        self.control_table_preview_cache = {}
        self.control_table_file_datetime_cache = {}
        self._ensure_control_table_window()
        self._reload_control_table()
        if hasattr(self, "control_file_summary_label"):
            self.control_file_summary_label.setStringValue_(
                self._control_file_summary_text(self.control_table_rows, path)
            )
        self.control_ready_cache = any(
            str(row.get("type", "")).upper() in {"IMG", "VID"}
            for row in self.control_table_rows
        )
        self._show_control_table_window(path)
        self.control_table_pending_refresh = (path, tuple(lines))
        self.performSelector_withObject_afterDelay_(
            "refreshControlFileDisplayAfterOpen:",
            None,
            0.01,
        )
        return True

    def place_name_from_sidecar_for_row(self, row):
        """Return a resolved place name from a media sidecar JSON, if available."""
        row_type = str(row.get("type", "")).upper()
        if row_type not in {"IMG", "VID"}:
            return None
        media_path = self.resolve_control_row_path(row)
        if media_path is None:
            return None
        status, metadata, _reason = validate_media_sidecar(media_path)
        if status != "available" or not isinstance(metadata, dict):
            return None
        details = metadata.get("place_details")
        if isinstance(details, dict):
            place_from_details = self.format_place_details_for_display(details)
            if place_from_details and not is_missing_place_text(place_from_details):
                return place_from_details
        place = metadata.get("place")
        place_text = str(place).strip() if isinstance(place, str) else ""
        if is_missing_place_text(place_text):
            return None
        return place_text

    def format_place_details_for_display(self, details):
        city = str(details.get("locality") or "").strip() if isinstance(details, dict) else ""
        sublocality = str(details.get("subLocality") or "").strip() if isinstance(details, dict) else ""
        administrative_area = str(details.get("administrativeArea") or "").strip() if isinstance(details, dict) else ""
        name = str(details.get("name") or "").strip() if isinstance(details, dict) else ""
        areas = details.get("areasOfInterest") if isinstance(details, dict) else None
        area_text = ""
        if isinstance(areas, list):
            area_text = ", ".join(str(item).strip() for item in areas if str(item).strip())
        elif areas:
            area_text = str(areas).strip()
        primary = city
        if sublocality and sublocality != primary:
            primary = f"{primary}-{sublocality}" if primary else sublocality
        if administrative_area and administrative_area not in {city, sublocality}:
            primary = f"{primary} ({administrative_area})" if primary else administrative_area
        secondary = name or area_text
        if primary and secondary and secondary != primary:
            return f"{primary}, {secondary}"
        return primary or secondary or None

    def update_place_names_from_sidecars(self, overwrite=False):
        """Merge newly reverse-geocoded place names into the editable control table."""
        control_file_path = self._control_file_path()
        if control_file_path is None:
            return 0
        resolved_control_file_path = control_file_path.resolve(strict=False)
        current_table_path = self.control_table_path.resolve(strict=False) if self.control_table_path is not None else None
        if not self.control_table_rows or current_table_path != resolved_control_file_path:
            if not self.load_slideshow_control_file(control_file_path):
                return None
        updated_count = 0
        rows_changed = False
        for row in self.control_table_rows:
            row_type = str(row.get("type", "")).upper()
            if row_type not in {"IMG", "VID"}:
                continue
            if not overwrite and not is_missing_place_text(str(row.get("place", ""))):
                continue
            place_text = self.place_name_from_sidecar_for_row(row)
            if place_text is None:
                continue
            if str(row.get("place", "")).strip() == place_text:
                continue
            if not rows_changed:
                self._push_control_table_undo()
                rows_changed = True
            row["place"] = place_text
            updated_count += 1
        if rows_changed:
            self._reload_control_table()
        if self.control_file_summary_label is not None:
            self.control_file_summary_label.setStringValue_(
                self._control_file_summary_text(self.control_table_rows, control_file_path)
            )
        if self.control_table_window is not None:
            self.control_table_window.makeKeyAndOrderFront_(None)
        return updated_count

    def reloadControlTableAfterEditing_(self, _sender):
        """Refresh row styling only after AppKit has retired its field editor."""
        table = self.control_table_view
        if table is None:
            return
        if table.currentEditor() is None:
            self._reload_control_table()
        else:
            table.setNeedsDisplay_(True)

    def update_control_table_cell(self, row_index, column_id, value):
        if row_index < 0 or row_index >= len(self.control_table_rows):
            return
        current = str(self.control_table_rows[row_index].get(column_id, ""))
        if current == value:
            return
        candidate = clone_slideshow_row(self.control_table_rows[row_index])
        update_slideshow_control_row_cell(candidate, column_id, value)
        if str(candidate.get("type", "")).upper() == "MUS" and str(candidate.get("name", "")).strip():
            try:
                parse_music_parameters(str(candidate.get("name", "")))
            except MusicSyntaxError as exc:
                show_alert("Invalid #MUSIC directive.", str(exc))
                return
        self._push_control_table_undo()
        self.control_table_rows[row_index] = candidate
        if self.control_table_view is not None:
            self.control_table_view.setNeedsDisplay_(True)
        if str(candidate.get("type", "")).upper() == "MUS":
            self.performSelector_withObject_afterDelay_("showMusicDirectiveHelp:", None, 0.01)
        self.performSelector_withObject_afterDelay_(
            "reloadControlTableAfterEditing:",
            None,
            0.01,
        )

    @objc.IBAction
    def insertControlRow_(self, _sender):
        """Insert an empty directive row and begin editing its Type cell."""
        indexes = self._selected_control_table_indexes()
        insert_at = indexes[-1] + 1 if indexes else len(self.control_table_rows)
        self._push_control_table_undo()
        self.control_table_rows.insert(
            insert_at,
            {
                "type": "",
                "name": "",
                "time": "",
                "gps": "",
                "place": "",
                "keyword": "",
                "is_keyword": True,
            },
        )
        self._reload_control_table()
        self._select_control_table_indexes([insert_at])
        table = self.control_table_view
        if table is None:
            return
        type_column = int(table.columnWithIdentifier_(nsstring("type")))
        view_row = self.control_table_model_to_view_index.get(insert_at)
        if type_column >= 0 and view_row is not None:
            table.editColumn_row_withEvent_select_(type_column, int(view_row), None, True)

    def _music_directive_help_content(self):
        labels = []
        if self.music_source is not None:
            playlist_path = self.music_playlist or self._preferred_music_playlist(self.music_source)
            parsed = load_audio_playlist(Path(self.music_source), Path(playlist_path) if playlist_path else None)
            labels = sorted(
                {f"${label}" for labels in parsed.labels_at_index.values() for label in labels},
                key=str.casefold,
            )
        label_text = ", ".join(labels) if labels else "No playlist labels are currently available."
        return (
            "#MUSIC: commands\n\n"
            "The Slide Show control file can contain directives for Music of the form "
            "'#MUSIC: Parameters', explained below. You can use them to jump to a part "
            "of the playlist, play a particular piece, determine loop behaviour, change "
            "volume, or switch music off or on at that location.\n\n"
            "The playlist can be edited and contains labels of the form $LABEL for each "
            "song and each album. You can also add custom labels to jump to a particular "
            "location. The music is stored in the audio subdirectory as files or albums, "
            "which are simply folders containing songs.\n\n"
            "Separate entries with commas. Pathnames containing spaces or commas may be "
            "put in double quotes; commas require quotes. Commands and $labels are "
            "case-insensitive.\n\n"
            "$LABEL or pathname   queue titles, then resume the interrupted title\n"
            "#JUMP $LABEL         continue the playlist from a label\n"
            "#ON / #OFF           open or close the control-file audio gate\n"
            "#CONTINUE            cancel a queue or loop without a hard cut\n"
            "#LOOPLINE            loop later labels/files on this line\n"
            "#LOOPONE             loop the current or most recent title\n"
            "#LOOPRANGE $A $B     loop an inclusive playlist range\n"
            "#LOOPALBUM, $LABEL   loop the album containing the target\n"
            "#LOOPALL             loop the complete playlist\n"
            "#VOLUME+ / #VOLUME-  change the internal level by one\n"
            "#VOLUME N            set level 0 through 9\n\n"
            "Example:\n"
            '#ON, #LOOPLINE, $INTRO, "Song1.mp3"\n\n'
            f"Available labels:\n{label_text}"
        )

    def _position_music_directive_help_window(self, anchor_view=None):
        """Place music help above or below the active table cell without covering it."""
        help_window = self.music_directive_help_window
        table = self.control_table_view
        if help_window is None:
            return

        target_window = None
        if anchor_view is self.music_help_button and anchor_view.window() is not None:
            window_rect = anchor_view.convertRect_toView_(anchor_view.bounds(), None)
            target_window = anchor_view.window()
            target_rect = target_window.convertRectToScreen_(window_rect)
        else:
            if table is None or table.window() is None:
                return
            row = int(table.editedRow())
            if row < 0:
                row = int(table.selectedRow())
            if row < 0:
                return
            column = int(table.editedColumn())
            if column < 0:
                column = int(table.columnWithIdentifier_(nsstring("name")))
            if column < 0:
                column = int(table.columnWithIdentifier_(nsstring("type")))
            if column < 0:
                return
            cell_rect = table.frameOfCellAtColumn_row_(column, row)
            window_rect = table.convertRect_toView_(cell_rect, None)
            target_window = table.window()
            target_rect = target_window.convertRectToScreen_(window_rect)

        screen = target_window.screen()
        if screen is None:
            return

        visible = screen.visibleFrame()
        frame = help_window.frame()
        gap = 10.0
        target_left = target_rect.origin.x
        target_bottom = target_rect.origin.y
        target_right = target_left + target_rect.size.width
        target_top = target_bottom + target_rect.size.height
        visible_left = visible.origin.x
        visible_bottom = visible.origin.y
        visible_right = visible_left + visible.size.width
        visible_top = visible_bottom + visible.size.height

        x = target_left + (target_right - target_left - frame.size.width) / 2.0
        x = max(visible_left, min(x, visible_right - frame.size.width))
        space_above = visible_top - target_top - gap
        space_below = target_bottom - visible_bottom - gap
        if space_above >= frame.size.height or space_above >= space_below:
            y = target_top + gap
        else:
            y = target_bottom - frame.size.height - gap
        y = max(visible_bottom, min(y, visible_top - frame.size.height))
        help_window.setFrameOrigin_(NSMakePoint(x, y))

    def showMusicDirectiveHelp_(self, _sender):
        """Raise the retained, nonmodal #MUSIC syntax reference."""
        if self.music_directive_help_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(260.0, 180.0, 360.0, 250.0),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setTitle_("Music Directive Help")
            window.setReleasedWhenClosed_(False)
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(8.0, 8.0, 344.0, 234.0))
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            text_view = NSTextView.alloc().initWithFrame_(scroll.bounds())
            text_view.setEditable_(False)
            text_view.setSelectable_(True)
            text_view.setFont_(NSFont.systemFontOfSize_(13.0))
            disable_field_editor_text_checking(text_view)
            scroll.setDocumentView_(text_view)
            window.contentView().addSubview_(scroll)
            self.music_directive_help_window = window
            self.music_directive_help_text = text_view
        self.music_directive_help_text.setString_(self._music_directive_help_content())
        self._position_music_directive_help_window(_sender)
        self.music_directive_help_window.orderFront_(None)

    @objc.IBAction
    def moveControlRowsUp_(self, _sender):
        indexes = self._selected_control_table_indexes()
        if not indexes or indexes[0] == 0:
            return
        self._push_control_table_undo()
        for index in indexes:
            self.control_table_rows[index - 1], self.control_table_rows[index] = self.control_table_rows[index], self.control_table_rows[index - 1]
        self._reload_control_table()
        self._select_control_table_indexes([index - 1 for index in indexes])

    @objc.IBAction
    def moveControlRowsDown_(self, _sender):
        indexes = self._selected_control_table_indexes()
        if not indexes or indexes[-1] >= len(self.control_table_rows) - 1:
            return
        self._push_control_table_undo()
        for index in reversed(indexes):
            self.control_table_rows[index + 1], self.control_table_rows[index] = self.control_table_rows[index], self.control_table_rows[index + 1]
        self._reload_control_table()
        self._select_control_table_indexes([index + 1 for index in indexes])

    @objc.IBAction
    def copyControlRows_(self, _sender):
        indexes = self._selected_control_table_indexes()
        if not indexes:
            return
        self._push_control_table_undo()
        insert_at = indexes[-1] + 1
        copied_rows = [clone_slideshow_row(self.control_table_rows[index]) for index in indexes]
        self.control_table_rows[insert_at:insert_at] = copied_rows
        self._reload_control_table()
        self._select_control_table_indexes(range(insert_at, insert_at + len(copied_rows)))

    @objc.IBAction
    def copyControlRowsToPasteboard_(self, _sender):
        indexes = self._selected_control_table_indexes()
        if not indexes:
            return
        lines = [serialize_slideshow_control_row(self.control_table_rows[index]) for index in indexes]
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_("\n".join(lines), NSPasteboardTypeString)

    @objc.IBAction
    def cutControlRows_(self, sender):
        self.copyControlRowsToPasteboard_(sender)
        self.deleteControlRows_(sender)

    @objc.IBAction
    def pasteControlRows_(self, _sender):
        pasteboard = NSPasteboard.generalPasteboard()
        text = pasteboard.stringForType_(NSPasteboardTypeString)
        if text is None:
            return
        pasted_rows = [
            parse_slideshow_control_line(line)
            for line in str(text).splitlines()
            if line.strip()
        ]
        if not pasted_rows:
            return
        indexes = self._selected_control_table_indexes()
        insert_at = indexes[-1] + 1 if indexes else len(self.control_table_rows)
        self._push_control_table_undo()
        self.control_table_rows[insert_at:insert_at] = pasted_rows
        self._reload_control_table()
        self._select_control_table_indexes(range(insert_at, insert_at + len(pasted_rows)))

    @objc.IBAction
    def deleteControlRows_(self, _sender):
        indexes = self._selected_control_table_indexes()
        if not indexes:
            return
        self._push_control_table_undo()
        for index in reversed(indexes):
            del self.control_table_rows[index]
        self._reload_control_table()
        self._select_control_table_indexes([min(indexes[0], len(self.control_table_rows) - 1)])

    @objc.IBAction
    def undoControlTable_(self, _sender):
        if not self.control_table_undo_stack:
            return
        self.control_table_redo_stack.append(self._snapshot_control_table())
        self.control_table_rows = self.control_table_undo_stack.pop()
        self._schedule_control_table_recovery()
        self._reload_control_table()

    @objc.IBAction
    def redoControlTable_(self, _sender):
        if not self.control_table_redo_stack:
            return
        self.control_table_undo_stack.append(self._snapshot_control_table())
        self.control_table_rows = self.control_table_redo_stack.pop()
        self._schedule_control_table_recovery()
        self._reload_control_table()

    @objc.IBAction
    def saveControlTable_(self, _sender):
        self._save_control_table_changes()

    def _save_control_table_changes(self):
        """Save the editable control table and return whether it succeeded."""
        if self.control_table_path is None:
            show_alert("No slide show control file is loaded.")
            return False
        if not self._validate_control_table_music_rows():
            return False
        control_text = self._control_table_text()
        try:
            write_text_atomic(self.control_table_path, control_text)
            control_table_recovery_path(self.control_table_path).unlink(missing_ok=True)
        except OSError as exc:
            show_alert("Could not save slide show control file.", str(exc))
            return False
        if self.control_table_recovery_timer is not None:
            self.control_table_recovery_timer.invalidate()
            self.control_table_recovery_timer = None
        self.control_table_dirty = False
        self.control_table_pending_refresh = None
        self.control_table_file_signature = control_file_signature(self.control_table_path)
        if self.assistant_place_names_pending_save:
            self.assistant_place_names_pending_save = False
            self.workflow_assistant_state["place_names_completed"] = True
            self.mark_dirty(immediate=True)
        self.refresh_control_file_display(
            rows=self.control_table_rows,
            control_lines=control_text.splitlines(),
        )
        self.refresh_workflow_assistant()
        self.set_status(f"Saved slide show control file {self.control_table_path}")
        return True

    def _discard_control_table_changes(self):
        """Restore the table from disk and remove unsaved recovery state."""
        if self.control_table_path is None:
            return False
        try:
            lines = self.control_table_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            show_alert("Could not discard slide show control-file changes.", str(exc))
            return False
        if self.control_table_recovery_timer is not None:
            self.control_table_recovery_timer.invalidate()
            self.control_table_recovery_timer = None
        try:
            control_table_recovery_path(self.control_table_path).unlink(missing_ok=True)
        except OSError as exc:
            show_alert("Could not discard the recovery copy.", str(exc))
            return False
        self.control_table_rows = [parse_slideshow_control_line(line) for line in lines if line.strip()]
        self.control_table_undo_stack = []
        self.control_table_redo_stack = []
        self.control_table_dirty = False
        self.control_table_pending_refresh = None
        self.assistant_place_names_pending_save = False
        self.control_table_last_snapshot_time = 0.0
        self.control_table_file_signature = control_file_signature(self.control_table_path)
        self.control_table_preview_cache = {}
        self.control_table_file_datetime_cache = {}
        self._reload_control_table()
        self.refresh_control_file_display(
            rows=self.control_table_rows,
            control_lines=lines,
        )
        self.set_status(f"Discarded unsaved changes to {self.control_table_path.name}")
        return True

    def _resolve_unsaved_control_table_changes(self):
        """Ask whether dirty control-table edits should be saved."""
        if self.control_table_window is not None:
            self.control_table_window.makeFirstResponder_(None)
        if not self.control_table_dirty:
            return True
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Save changes to the slide show control file?")
        filename = self.control_table_path.name if self.control_table_path is not None else "the control file"
        alert.setInformativeText_(
            f"There are unsaved changes in {filename}. Save them before closing the editor?"
        )
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Don't Save")
        alert.addButtonWithTitle_("Cancel")
        response = int(alert.runModal())
        if response == 1000:
            return self._save_control_table_changes()
        if response == 1001:
            return self._discard_control_table_changes()
        return False

    def _save_control_table_before_external_update(self):
        """Require dirty table edits to be saved before a staged update."""
        if not self.control_table_dirty:
            return True
        if self.control_table_window is not None:
            self.control_table_window.makeFirstResponder_(None)
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Save control-file edits before updating media?")
        alert.setInformativeText_(
            "The selective update must start from the current saved table. Save the edits or cancel the update."
        )
        alert.addButtonWithTitle_("Save and Continue")
        alert.addButtonWithTitle_("Cancel")
        if int(alert.runModal()) != 1000:
            return False
        return self._save_control_table_changes()

    @objc.IBAction
    def revertControlTable_(self, _sender):
        if self.control_table_path is None:
            show_alert("No slide show control file is loaded.")
            return
        control_table_recovery_path(self.control_table_path).unlink(missing_ok=True)
        self.control_table_dirty = False
        self.load_slideshow_control_file(self.control_table_path, force_reload=True)

    @objc.IBAction
    def closeControlTable_(self, _sender):
        if not self._resolve_unsaved_control_table_changes():
            return
        if self.control_table_window is not None:
            self.control_table_window.orderOut_(None)
        if self.music_directive_help_window is not None:
            self.music_directive_help_window.orderOut_(None)

    def _collect_media_candidates(self, paths, images_only=False):
        allowed = IMAGE_EXTENSIONS if images_only else MEDIA_EXTENSIONS
        collected = []
        for path in paths:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and child.suffix.lower() in allowed:
                        collected.append(child)
            elif path.is_file() and path.suffix.lower() in allowed:
                collected.append(path)
        return collected

    def _import_media_files(self, source_paths, images_only=False, force_jpeg=False):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            return []
        media_files = self._collect_media_candidates(source_paths, images_only=images_only)
        if not media_files:
            self.set_status("No matching media files selected.")
            self.reset_progress()
            return []
        copied = 0
        imported_paths = []
        skipped = []
        failed = []
        self.workflow_assistant_bubble.setHidden_(True)
        self.set_progress(0.0, len(media_files))
        for index, source_path in enumerate(media_files, start=1):
            target_name = source_path.stem + ".jpeg" if force_jpeg else source_path.name
            target_path = project_dir / target_name
            if target_path.exists():
                skipped.append(target_name)
                self.set_status(f"Skipping existing {index}/{len(media_files)}: {target_name}")
                self.set_progress(index, len(media_files))
                continue
            self.set_status(f"Importing {index}/{len(media_files)}: {source_path.name}")
            try:
                if force_jpeg and source_path.suffix.lower() not in {".jpg", ".jpeg"}:
                    completed = subprocess.run(
                        ["sips", "-s", "format", "jpeg", str(source_path), "--out", str(target_path)],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if completed.returncode != 0:
                        failed.append(f"{target_name}: conversion failed")
                else:
                    shutil.copy2(source_path, target_path)
            except OSError as exc:
                failed.append(f"{target_name}: {exc}")
            if target_path.exists():
                copied += 1
                imported_paths.append(target_path.resolve(strict=False))
            elif not any(item.startswith(f"{target_name}:") for item in failed):
                failed.append(f"{target_name}: output was not created")
            self.set_progress(index, len(media_files))
        self.last_picture_import_directory = source_paths[0].parent if source_paths else self.last_picture_import_directory
        if imported_paths or skipped:
            self.workflow_assistant_state["media_confirmed"] = True
        self.mark_dirty()
        self.refresh_media_summary()
        self.set_status(
            f"Imported {copied} file(s), skipped {len(skipped)} existing file(s), failed {len(failed)} file(s)."
        )
        if skipped or failed:
            lines = [f"Already present: {name}" for name in skipped] + failed
            self._show_scrollable_list("Skipped Media", lines)
        self.refresh_workflow_assistant()
        if imported_paths or skipped:
            self._start_automatic_media_metadata(imported_paths)
        return imported_paths

    def _cleanup_album_selection_window(self):
        if self.album_selection_window is not None:
            self.album_selection_window.setDelegate_(None)
            self.album_selection_window.close()
        self.album_selection_window = None
        self.album_selection_table = None
        self.album_selection_rows = []
        self.album_selection_data_source = None
        self.album_selection_delegate = None

    def _choose_photos_album(self, albums):
        self.album_selection_rows = list(albums)
        self.album_selection_result = None

        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(220.0, 180.0, 620.0, 460.0),
            style,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("Choose Photos Album")

        content = window.contentView()
        title_label = self._make_label("Select an album to export", 15.0, bold=True)
        title_label.setFrame_(NSMakeRect(20.0, 420.0, 400.0, 24.0))
        content.addSubview_(title_label)

        info_label = self._make_label("Use the button below or double-click an album.", 12.0)
        info_label.setFrame_(NSMakeRect(20.0, 398.0, 420.0, 18.0))
        content.addSubview_(info_label)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20.0, 70.0, 580.0, 316.0))
        scroll.setHasVerticalScroller_(True)
        table = NSTableView.alloc().initWithFrame_(scroll.bounds())

        title_column = NSTableColumn.alloc().initWithIdentifier_(nsstring("display"))
        title_column.setWidth_(470.0)
        title_column.headerCell().setStringValue_("Album")
        table.addTableColumn_(title_column)

        count_column = NSTableColumn.alloc().initWithIdentifier_(nsstring("count"))
        count_column.setWidth_(90.0)
        count_column.headerCell().setStringValue_("Photos")
        table.addTableColumn_(count_column)

        data_source = GPSTrackShowGUITableDataSource.alloc().init()
        rows = [{"display": item["display"], "count": str(item["count"])} for item in albums]
        data_source.setRows_columns_(rows, ["display", "count"])
        table.setDataSource_(data_source)
        table.setDelegate_(data_source)
        table.setAllowsMultipleSelection_(False)
        table.setTarget_(self)
        table.setDoubleAction_("albumSelectionConfirm:")
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)

        export_button = self._make_button("Export Selected Album", "albumSelectionConfirm:")
        export_button.setFrame_(NSMakeRect(310.0, 20.0, 170.0, 32.0))
        content.addSubview_(export_button)

        cancel_button = self._make_button("Cancel", "albumSelectionCancel:")
        cancel_button.setFrame_(NSMakeRect(490.0, 20.0, 110.0, 32.0))
        content.addSubview_(cancel_button)

        self.album_selection_window = window
        self.album_selection_table = table
        self.album_selection_data_source = data_source
        self.album_selection_delegate = GPSTrackShowGUIAlbumSelectionDelegate.alloc().initWithController_(self)
        self.album_selection_window.setDelegate_(self.album_selection_delegate)

        table.reloadData()
        if albums:
            table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(0), False)
        window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)
        NSApp().runModalForWindow_(window)
        result = self.album_selection_result
        self._cleanup_album_selection_window()
        return result

    @objc.IBAction
    def albumSelectionConfirm_(self, _sender):
        if self.album_selection_table is None:
            return
        row = int(self.album_selection_table.selectedRow())
        if row < 0 or row >= len(self.album_selection_rows):
            show_alert("Please select an album first.")
            return
        self.album_selection_result = self.album_selection_rows[row]
        NSApp().stopModal()
        if self.album_selection_window is not None:
            self.album_selection_window.setDelegate_(None)
            self.album_selection_window.close()

    @objc.IBAction
    def albumSelectionCancel_(self, _sender):
        self.album_selection_result = None
        NSApp().stopModal()
        if self.album_selection_window is not None:
            self.album_selection_window.setDelegate_(None)
            self.album_selection_window.close()

    def _photos_authorization_granted(self):
        if not PHOTOS_FRAMEWORK_AVAILABLE:
            show_alert("The Photos framework is not available in this Python environment.")
            return False
        status = int(PHPhotoLibrary.authorizationStatus())
        if status in {PH_AUTH_AUTHORIZED, PH_AUTH_LIMITED}:
            return True
        if status == PH_AUTH_NOT_DETERMINED:
            event = threading.Event()
            result = {"status": status}

            def handler(new_status):
                result["status"] = int(new_status)
                event.set()

            PHPhotoLibrary.requestAuthorization_(handler)
            while not event.wait(0.05):
                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
            status = result["status"]
            if status in {PH_AUTH_AUTHORIZED, PH_AUTH_LIMITED}:
                return True
        show_alert(
            "Access to Photos is not available.",
            "Please allow photo library access for this app in System Settings and try again.",
        )
        return False

    def _image_fetch_options(self):
        options = PHFetchOptions.alloc().init()
        options.setPredicate_(NSPredicate.predicateWithFormat_("mediaType == %d", PH_ASSET_MEDIA_TYPE_IMAGE))
        return options

    def _fetch_photos_albums(self):
        albums = []
        image_options = self._image_fetch_options()
        for collection_type, kind in (
            (PH_COLLECTION_TYPE_ALBUM, "Album"),
            (PH_COLLECTION_TYPE_SMART_ALBUM, "Smart Album"),
        ):
            results = PHAssetCollection.fetchAssetCollectionsWithType_subtype_options_(
                collection_type,
                PH_COLLECTION_SUBTYPE_ANY,
                None,
            )
            for index in range(int(results.count())):
                collection = results.objectAtIndex_(index)
                title = str(collection.localizedTitle() or "").strip()
                if not title:
                    continue
                assets = PHAsset.fetchAssetsInAssetCollection_options_(collection, image_options)
                count = int(assets.count())
                if count == 0:
                    continue
                albums.append(
                    {
                        "title": title,
                        "kind": kind,
                        "count": count,
                        "collection": collection,
                        "display": f"{title} ({count})",
                    }
                )
        albums.sort(key=lambda item: (item["title"].lower(), item["kind"]))
        return albums

    def _choose_photos_album(self, albums):
        if not albums:
            return None
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Choose a Photos album")
        alert.setInformativeText_(
            "Select an album from the list below, then click 'Export Selected Album' to start exporting JPEG files into the project directory."
        )
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0.0, 0.0, 420.0, 28.0), False)
        popup.addItemsWithTitles_([item["display"] for item in albums])
        alert.setAccessoryView_(popup)
        alert.addButtonWithTitle_("Export Selected Album")
        alert.addButtonWithTitle_("Cancel")
        if int(alert.runModal()) != 1000:
            return None
        index = int(popup.indexOfSelectedItem())
        if index < 0 or index >= len(albums):
            return None
        return albums[index]

    def _full_size_image_path_for_asset(self, asset):
        event = threading.Event()
        result = {"path": None}
        options = PHContentEditingInputRequestOptions.alloc().init()
        options.setNetworkAccessAllowed_(True)

        def handler(content_input, _info):
            if content_input is not None:
                image_url = content_input.fullSizeImageURL()
                if image_url is not None:
                    result["path"] = Path(str(image_url.path())).resolve(strict=False)
            event.set()

        asset.requestContentEditingInputWithOptions_completionHandler_(options, handler)
        while not event.wait(0.05):
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        return result["path"]

    def _asset_output_name(self, asset):
        resources = list(PHAssetResource.assetResourcesForAsset_(asset))
        if resources:
            original_name = str(resources[0].originalFilename())
            return f"{Path(original_name).stem}.jpeg"
        return f"{asset.localIdentifier().split('/', 1)[0]}.jpeg"

    def _export_selected_photos_album(self, album_info, project_dir):
        image_options = self._image_fetch_options()
        assets = PHAsset.fetchAssetsInAssetCollection_options_(album_info["collection"], image_options)
        total = int(assets.count())
        if total == 0:
            self.set_status(f"No photos found in album '{album_info['title']}'.")
            self.reset_progress()
            return
        copied = 0
        imported_paths = []
        skipped = []
        failed = []
        self.workflow_assistant_bubble.setHidden_(True)
        self.set_progress(0.0, total)
        for index in range(total):
            asset = assets.objectAtIndex_(index)
            target_name = self._asset_output_name(asset)
            target_path = project_dir / target_name
            if target_path.exists():
                skipped.append(target_name)
                self.set_status(f"Skipping existing {index + 1}/{total}: {target_name}")
                self.set_progress(index + 1, total)
                continue
            self.set_status(f"Importing {index + 1}/{total}: {target_name}")
            source_path = self._full_size_image_path_for_asset(asset)
            if source_path is None or not source_path.exists():
                failed.append(f"{target_name}: source photo was unavailable")
                self.set_progress(index + 1, total)
                continue
            completed = subprocess.run(
                ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "best", str(source_path), "--out", str(target_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 or not target_path.exists():
                failed.append(f"{target_name}: export failed")
            else:
                copied += 1
                imported_paths.append(target_path.resolve(strict=False))
            self.set_progress(index + 1, total)
        self.last_picture_import_directory = project_dir
        if imported_paths or skipped:
            self.workflow_assistant_state["media_confirmed"] = True
        self.mark_dirty()
        self.set_status(
            f"Imported album '{album_info['title']}' with {copied} photo(s), "
            f"skipped {len(skipped)} existing file(s), failed {len(failed)} file(s)."
        )
        if skipped or failed:
            lines = [f"Already present: {name}" for name in skipped] + failed
            self._show_scrollable_list("Skipped Gallery Photos", lines)
        self.refresh_media_summary()
        self.refresh_workflow_assistant()
        if imported_paths or skipped:
            self._start_automatic_media_metadata(imported_paths)

    def _project_dir_path(self):
        text = str(self.project_dir_field.stringValue()).strip()
        if not text:
            return None
        return Path(text).expanduser()

    def _default_gpx_path(self):
        if self.current_project_dir is None:
            return None
        base = project_filename_base(self._current_project_name() or self.current_project_dir.name)
        return self.current_project_dir / f"{base}.gpx"

    def _is_default_gpx_path(self, path):
        default_path = self._default_gpx_path()
        if default_path is None or path is None:
            return False
        try:
            return path.resolve(strict=False) == default_path.resolve(strict=False)
        except OSError:
            return False

    def _set_gpx_field_value(self, value, manual=None):
        text = str(value or "")
        if text and ";" not in text:
            path = Path(text).expanduser()
            if self.current_project_dir is not None:
                try:
                    resolved = path.resolve(strict=False) if path.is_absolute() else (self.current_project_dir / path).resolve(strict=False)
                    if resolved.parent == self.current_project_dir.resolve(strict=False):
                        text = resolved.name
                except OSError:
                    text = path.name
            else:
                text = path.name
        self.gpx_field.setStringValue_(text)
        if manual is not None:
            self.gpx_field_manually_changed = bool(manual)

    def _update_default_gpx_field(self, force=False):
        default_path = self._default_gpx_path()
        if default_path is None:
            self._set_gpx_field_value("", manual=False)
            return
        current_text = str(self.gpx_field.stringValue()).strip()
        if force or not current_text or not self.gpx_field_manually_changed:
            self._set_gpx_field_value(default_path.name, manual=False)

    def _gpx_paths(self):
        text = str(self.gpx_field.stringValue()).strip()
        if not text:
            default_path = self._default_gpx_path()
            return [default_path] if default_path is not None else []
        paths = []
        for part in text.split(";"):
            item = part.strip()
            if not item:
                continue
            path = Path(item).expanduser()
            if not path.is_absolute() and self.current_project_dir is not None:
                path = self.current_project_dir / path
            paths.append(path)
        return paths

    def _resolve_project_directory(self, allow_create=False, update_gpx_field=True):
        project_dir = self._project_dir_path()
        if project_dir is None:
            show_alert("Please select a project directory first.")
            self.window.makeFirstResponder_(self.project_dir_field)
            return None
        candidate = project_dir.expanduser().resolve(strict=False)
        current = self.current_project_dir.resolve(strict=False) if self.current_project_dir is not None else None
        if not self.directory_activation_in_progress and (
            current != candidate or self.current_project_file is None
        ):
            if self._activate_project_directory(candidate, allow_create=allow_create):
                return self.current_project_dir
            return None
        if project_dir.exists():
            if not project_dir.is_dir():
                show_alert("The selected path is not a directory.")
                self.window.makeFirstResponder_(self.project_dir_field)
                return None
        else:
            if allow_create:
                project_dir.mkdir(parents=True, exist_ok=True)
            else:
                show_alert("Directory does not exist!")
                self.window.makeFirstResponder_(self.project_dir_field)
                return None

        resolved = project_dir.resolve()
        self.project_dir_field.setStringValue_(str(resolved))
        previous_project_dir = self.current_project_dir
        self.current_project_dir = resolved
        try:
            os.chdir(resolved)
        except OSError as exc:
            show_alert("Could not make the project directory the current working directory.", str(exc))
            self.set_status("Project directory handling failed.")
            return None
        if not str(self.title_field.stringValue()).strip():
            self.title_field.setStringValue_(resolved.name)
        if update_gpx_field:
            previous_default = None if previous_project_dir is None else previous_project_dir / f"{previous_project_dir.name}.gpx"
            current_text = str(self.gpx_field.stringValue()).strip()
            if (
                not current_text
                or not self.gpx_field_manually_changed
                or (previous_default is not None and current_text in {str(previous_default), previous_default.name})
            ):
                self._update_default_gpx_field(force=True)
        if update_gpx_field:
            self.start_async_project_status_refresh("project directory")
        else:
            self.refresh_section_status_indicators()
        return resolved

    def _description_string(self):
        return str(self.description_text.string())

    def populate_track_summary(self, gpx_path):
        self.update_gpx_summary(gpx_path)
        self.rows = []
        self.set_status(f"Loaded GPX summary for {gpx_path.name}")

    @objc.IBAction
    def projectDirectoryCommitted_(self, _sender):
        text = str(self.project_dir_field.stringValue()).strip()
        if text and self._activate_project_directory(Path(text), allow_create=True):
            next_view = self.project_dir_field.nextKeyView()
            if next_view is not None:
                self.window.makeFirstResponder_(next_view)

    def _selected_project_directory_text(self):
        try:
            selected_index = int(self.project_dir_field.indexOfSelectedItem())
        except Exception:
            selected_index = -1
        if selected_index >= 0:
            try:
                value = self.project_dir_field.objectValueOfSelectedItem()
                if value is not None:
                    return str(value).strip()
            except Exception:
                pass
        return str(self.project_dir_field.stringValue()).strip()

    def _activate_selected_project_directory(self):
        text = self._selected_project_directory_text()
        if not text:
            return False
        project_dir = Path(text).expanduser()
        if not project_dir.is_absolute():
            project_dir = (self.current_project_dir or self.base_dir) / project_dir
        if not self._activate_project_directory(project_dir, allow_create=True):
            return False
        self.set_status(f"Selected project directory: {self.current_project_dir}")
        return True

    def comboBoxSelectionDidChange_(self, notification):
        if notification.object() is not self.project_dir_field:
            return
        self._activate_selected_project_directory()

    def projectDirectoryComboSelectionChanged_(self, notification):
        if notification.object() is not self.project_dir_field:
            return
        self._activate_selected_project_directory()

    def projectFileComboSelectionChanged_(self, notification):
        if self.project_file_menu_refreshing:
            return
        field = notification.object()
        if field is self.title_field:
            index = int(self.title_field.indexOfSelectedItem())
            if 0 <= index < len(self.adventure_combo_paths):
                self.load_project_configuration(self.adventure_combo_paths[index])
        elif field is self.gpx_field:
            self.gpxSelectionCommitted_(self.gpx_field)
        elif field is self.control_file_field:
            self.controlFileCommitted_(self.control_file_field)

    @objc.IBAction
    def adventureNameCommitted_(self, _sender):
        if self.project_file_menu_refreshing or self.adventure_name_commit_in_progress:
            return
        self.adventure_name_commit_in_progress = True
        try:
            requested_name = project_filename_base(str(self.title_field.stringValue()).strip())
            if not requested_name or self.current_project_dir is None:
                self.title_field.setStringValue_(self.committed_adventure_name)
                return

            for record in self.adventure_records:
                if record.project_name.casefold() == requested_name.casefold() and record.path != self.current_project_file:
                    self.load_project_configuration(record.path)
                    return

            if self.current_project_file is None:
                target = self.current_project_dir / f"{requested_name}.adv"
                if target.exists():
                    self.load_project_configuration(target)
                    return
                self.title_field.setStringValue_(requested_name)
                self.committed_adventure_name = requested_name
                self.track_map_base = requested_name
                self._set_gpx_field_value(f"{requested_name}.gpx", manual=False)
                self.current_control_file = self.current_project_dir / f"{requested_name}-sorted.lst"
                self.control_file_field.setStringValue_(self.current_control_file.name)
                self._set_music_playlist(
                    self._ensure_default_music_playlist(show_errors=True),
                    mark_dirty=False,
                )
                self.current_project_file = target
                self.project_dirty = True
                if not self._write_project_configuration(status_prefix="Created adventure"):
                    self.current_project_file = None
                    return
                self._refresh_project_file_menus()
                self.start_async_project_status_refresh("new adventure")
                return

            if requested_name == self.committed_adventure_name:
                self.title_field.setStringValue_(self.committed_adventure_name)
                return

            edited_name = requested_name
            self.title_field.setStringValue_(self.committed_adventure_name)
            if not self.flush_adventure_autosave():
                return
            try:
                source_payload = load_adventure(self.current_project_file).payload
            except AdventureFormatError as exc:
                show_alert("Could not read the active Adventure.", str(exc))
                return
            self.title_field.setStringValue_(edited_name)

            alert = NSAlert.alloc().init()
            alert.setMessageText_("Change Adventure name")
            alert.setInformativeText_(
                f"Rename the current Adventure to '{edited_name}', create an independent copy, or cancel?"
            )
            alert.addButtonWithTitle_("Rename")
            alert.addButtonWithTitle_("Copy")
            alert.addButtonWithTitle_("Cancel")
            related_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 430, FIELD_HEIGHT))
            related_checkbox.setButtonType_(NSButtonTypeSwitch)
            related_checkbox.setTitle_("Also rename/copy GPX, control file, generated maps, and music playlist")
            related_checkbox.setState_(NSControlStateValueOn)
            alert.setAccessoryView_(related_checkbox)
            response = int(alert.runModal())
            if response not in {1000, 1001}:
                self.title_field.setStringValue_(self.committed_adventure_name)
                self.set_status("Adventure name change cancelled.")
                return
            operation = "rename" if response == 1000 else "copy"
            include_related = related_checkbox.state() == NSControlStateValueOn
            try:
                self.set_status(f"Preparing Adventure {operation}...")
                target_path, _payload = rename_or_copy_adventure(
                    self.current_project_file,
                    source_payload,
                    edited_name,
                    operation,
                    include_related=include_related,
                    progress=lambda current, total: self.set_progress(current, total),
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.reset_progress()
                self.title_field.setStringValue_(self.committed_adventure_name)
                show_alert(f"Could not {operation} the Adventure.", str(exc))
                self.set_status(f"Adventure {operation} failed.")
                return
            self.reset_progress()
            self.load_project_configuration(target_path, flush_current=False)
            self.set_status(f"Adventure {operation} completed: {target_path.name}")
        finally:
            self.adventure_name_commit_in_progress = False

    @objc.IBAction
    def controlFileCommitted_(self, _sender):
        if self.project_file_menu_refreshing or self.current_project_dir is None:
            return
        value = str(self.control_file_field.stringValue()).strip()
        if not value:
            value = f"{project_filename_base(self._current_project_name())}-sorted.lst"
        name = Path(value).name
        if Path(name).suffix.lower() != ".lst":
            show_alert("Control file must use the .lst extension.", name)
            if self.current_control_file is not None:
                self.control_file_field.setStringValue_(self.current_control_file.name)
            return
        self.current_control_file = (self.current_project_dir / name).resolve(strict=False)
        self.control_file_field.setStringValue_(name)
        self.control_table_rows = []
        self.control_table_path = None
        self.control_table_file_signature = None
        self.control_table_pending_refresh = None
        self.mark_dirty(immediate=True)
        self.refresh_control_file_display()
        self._update_slideshow_continue_button()

    @objc.IBAction
    def chooseProjectDirectory_(self, _sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        if hasattr(panel, "setCanCreateDirectories_"):
            panel.setCanCreateDirectories_(True)
        if self.current_project_dir is not None:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.current_project_dir)))
        if panel.runModal():
            url = panel.URL()
            if url is None:
                return
            project_dir = Path(str(url.path())).expanduser().resolve()
            if self._activate_project_directory(project_dir, allow_create=True):
                self.set_status(f"Selected project directory: {self.current_project_dir}")

    @objc.IBAction
    def chooseTitleImage_(self, _sender):
        project_dir = self._resolve_project_directory(
            allow_create=False,
            update_gpx_field=False,
        )
        if project_dir is None:
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(
            sorted(extension.lstrip(".") for extension in IMAGE_EXTENSIONS)
        )
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(project_dir)))
        if not panel.runModal():
            return
        url = panel.URL()
        if url is None:
            return
        selected = Path(str(url.path())).expanduser().resolve(strict=False)
        self.title_image = selected
        self.title_image_field.setStringValue_(
            self._stored_optional_project_path(selected)
        )
        self.mark_dirty(immediate=True)
        self.set_status(f"Selected Intro title image: {selected.name}")

    @objc.IBAction
    def useFirstTitleImage_(self, _sender):
        self.title_image = None
        self.title_image_field.setStringValue_("")
        self.mark_dirty(immediate=True)
        self.set_status("The first still image in the control file will be used for the Intro.")

    @objc.IBAction
    def createAdventure_(self, _sender):
        project_dir = self._project_dir_path()
        if project_dir is not None:
            self._activate_project_directory(project_dir, allow_create=True)

    @objc.IBAction
    def editAdventure_(self, _sender):
        self.save_project_configuration()

    @objc.IBAction
    def saveAndExit_(self, _sender):
        if self.confirm_close():
            NSApp().terminate_(None)

    @objc.IBAction
    def quit_(self, _sender):
        if self.confirm_close():
            NSApp().terminate_(None)

    @objc.IBAction
    def gpxSelectionCommitted_(self, _sender):
        if self.handle_gpx_file_selection(show_errors=True):
            self._select_gpx_journey_source()
            self.gpx_ready_cache = True
            self.mark_dirty(immediate=True)

    def _copy_external_gpx_to_project(self, source_path: Path, target_path: Path, show_errors=True):
        source_path = source_path.expanduser().resolve(strict=False)
        target_path = target_path.expanduser().resolve(strict=False)
        if source_path.parent == target_path.parent and source_path.name == target_path.name:
            return target_path
        if not source_path.exists():
            if show_errors:
                show_alert("GPX file does not exist.", str(source_path))
            return None
        if not source_path.parent.exists() or not source_path.parent.is_dir():
            if show_errors:
                show_alert("Das Directory des GPX-Files existiert nicht.", str(source_path.parent))
            return None
        if not confirm_alert(
            "Copy GPX file into the project directory?",
            f"The selected file is outside the project directory:\n{source_path}\n\nIt will be copied to:\n{target_path}",
            "Copy",
            "Cancel",
        ):
            self.set_status("GPX copy cancelled.")
            return None
        if target_path.exists() and not confirm_alert(
            "Overwrite existing GPX file?",
            f"The file already exists in the project directory:\n{target_path}\n\nOverwrite it?",
            "Overwrite",
            "Cancel",
        ):
            self.set_status("GPX overwrite cancelled.")
            return None
        try:
            shutil.copy2(source_path, target_path)
        except OSError as exc:
            if show_errors:
                show_alert("Could not copy GPX file into the project directory.", str(exc))
            self.set_status("GPX copy failed.")
            return None
        self.set_status(f"Copied GPX file to {target_path.name}.")
        return target_path

    def _adopt_single_gpx_path(self, selected_path: Path, show_errors=True, target_basename: str | None = None):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            return None
        selected_path = selected_path.expanduser().resolve(strict=False)
        if selected_path.suffix.lower() != ".gpx":
            if show_errors:
                show_alert("Only .gpx files are supported.", str(selected_path))
            return None
        if not selected_path.parent.exists() or not selected_path.parent.is_dir():
            if show_errors:
                show_alert("Das Directory des GPX-Files existiert nicht.", str(selected_path.parent))
            return None
        if selected_path.parent == project_dir.resolve(strict=False):
            self._set_gpx_field_value(selected_path.name, manual=True)
            return selected_path
        basename = (target_basename or self._gpx_field_basename() or selected_path.name).strip()
        if not basename.lower().endswith(".gpx"):
            basename += ".gpx"
        target_path = project_dir / Path(basename).name
        copied_path = self._copy_external_gpx_to_project(selected_path, target_path, show_errors=show_errors)
        if copied_path is not None:
            self._set_gpx_field_value(copied_path.name, manual=True)
        return copied_path

    @objc.IBAction
    def selectGPXFile_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setAllowedFileTypes_(["gpx"])
        if hasattr(panel, "setDirectoryURL_"):
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(project_dir)))
        if panel.runModal():
            urls = panel.URLs()
            if urls is None:
                return
            selected_paths = [Path(str(url.path())).expanduser().resolve(strict=False) for url in urls]
            if len(selected_paths) == 1:
                adopted_path = self._adopt_single_gpx_path(selected_paths[0], show_errors=True)
                if adopted_path is None:
                    return
                self._accept_existing_project_gpx(adopted_path)
            else:
                target_name = self._gpx_field_basename()
                if not target_name:
                    default_path = self._default_gpx_path()
                    target_name = default_path.name if default_path is not None else "project.gpx"
                target_path = project_dir / Path(target_name).name
                self._set_gpx_field_value(target_path.name, manual=True)
                self._open_gpx_editor(input_paths=selected_paths, output_path=target_path.resolve(strict=False))
                self.mark_dirty()
                return

    @objc.IBAction
    def openGPXFolder_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        gpx_files = sorted(project_dir.glob("*.gpx"), key=lambda path: path.name.lower())
        if gpx_files:
            urls = [NSURL.fileURLWithPath_(str(path)) for path in gpx_files]
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(urls)
            self.set_status(f"Opened Finder with {len(gpx_files)} GPX file(s) selected.")
        else:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(project_dir)))
            self.set_status("Opened project directory. No GPX files found yet.")

    def _geolocation_options(self, *, include_tracks=False):
        options = {
            "distance": self.parameters["locations.reuse_radius_m"],
            "geocode_timeout_seconds": self.parameters["locations.timeout_seconds"],
            "geocode_pacing_min_seconds": self.parameters["locations.pacing_min_seconds"],
            "geocode_pacing_max_seconds": self.parameters["locations.pacing_max_seconds"],
        }
        if include_tracks:
            summary_path = self._current_tracks_summary_path_if_available()
            if summary_path is not None and summary_path.is_file():
                options["tracks"] = summary_path
        return options

    def _media_map_options_from_values(
        self,
        trackimages_dir,
        filename_base,
        parameter_values,
    ):
        """Return media-map settings without reading Cocoa controls."""
        maximum_zoom = int(parameter_values["maps.maximum_zoom"])
        return {
            "output_dir": str(trackimages_dir) if trackimages_dir is not None else "",
            "filename_base": filename_base,
            "zoom_level": min(int(parameter_values["trackmaps.zoom"]), maximum_zoom),
            "image_size": self._track_map_image_size(parameter_values),
            "font_factor": parameter_values["trackmaps.font_factor"],
            "background_color": parameter_values["trackmaps.background_color"],
            "title_color": parameter_values["trackmaps.title_color"],
            "map_provider": parameter_values["maps.provider"],
            "custom_map_url": parameter_values["maps.custom_url"],
            "custom_map_attribution": parameter_values["maps.custom_attribution"],
            "maximum_map_zoom": maximum_zoom,
            "map_request_timeout_seconds": parameter_values["maps.request_timeout_seconds"],
            "map_layout": "standard",
            "map_layouts": ("standard", "time-lapse"),
            "track_edge_margin_fraction": parameter_values["trackmaps.edge_margin_fraction"],
            "adventure_render_parameters": self._track_map_parameter_signature(
                "standard", parameter_values
            ),
            "adventure_overview_render_parameters": self._track_map_parameter_signature(
                "overview", parameter_values
            ),
            "adventure_render_parameters_by_layout": {
                "standard": self._track_map_parameter_signature(
                    "standard", parameter_values
                ),
                "time-lapse": self._track_map_parameter_signature(
                    "time-lapse", parameter_values
                ),
            },
        }

    def _media_map_options(self):
        """Return map-rendering settings used for control-file media maps."""
        return self._media_map_options_from_values(
            self._track_images_dir(),
            self._control_file_base_name(),
            self.parameters,
        )
    def _generate_missing_project_maps_worker(
        self,
        *,
        project_dir,
        control_path,
        cancel_event,
        output_writer=None,
        status_callback=None,
    ):
        """Generate only missing/stale project maps through the shared stage plan."""
        project_dir = Path(project_dir).resolve(strict=False)
        control_path = Path(control_path).resolve(strict=False)
        trackimages_dir = project_dir / "trackimages"
        trackimages_dir.mkdir(parents=True, exist_ok=True)
        project_name = self._current_project_name()
        media_only = self._uses_media_only_maps()
        gpx_path = None if media_only else self._current_single_gpx_path()
        failures = []
        created_paths = []

        def report(message):
            if output_writer is not None:
                output_writer.write(f"{message}\n")
            if status_callback is not None:
                status_callback(message)

        tracks_summary_path = None
        skip_remaining_maps = False
        if not media_only:
            if gpx_path is None or not gpx_path.is_file():
                raise ValueError(
                    "No confirmed GPX file is available. Choose a GPX file or use media-only mode."
                )
            report("Preparing GPX tracks and checking generated maps...")

            def check_cancelled_at_track_boundary():
                if cancel_event.is_set():
                    raise RuntimeError("Map generation cancelled")

            common_options = self._plot_common_options(project_name, trackimages_dir)
            common_options["track_processing_callback"] = check_cancelled_at_track_boundary
            context = prepare_with_options(
                str(gpx_path),
                plot_tracks="all",
                **common_options,
            )
            context["args"].cancel_event = cancel_event
            removed = self._cleanup_obsolete_track_map_files(context)
            if removed:
                report(f"Removed {len(removed)} obsolete map file(s).")
            update_numbers = self._track_map_update_numbers(gpx_path, context)
            include_overview = 0 in update_numbers
            track_numbers = [number for number in update_numbers if number > 0]

            def paired_progress(_current, _total, name, layout):
                if cancel_event.is_set():
                    raise RuntimeError("Map generation cancelled")
                report(
                    "Generating overview..."
                    if layout == "overview"
                    else f"Generating {layout} map: {name}"
                )

            try:
                result = execute_map_variants_from_context(
                    context,
                    selected_track_numbers=track_numbers,
                    plot_overview=include_overview,
                    map_layouts=("standard", "time-lapse"),
                    render_parameters_by_layout={
                        "standard": self._track_map_parameter_signature("standard"),
                        "time-lapse": self._track_map_parameter_signature("time-lapse"),
                    },
                    progress_callback=paired_progress,
                )
                created_paths.extend(result.get("created_paths", []))
            except Exception as exc:
                if cancel_event.is_set():
                    raise GeoLocationsCancelled("Aborted.") from exc
                failures.append({"filename": "GPX maps", "error": str(exc)})
                skip_remaining_maps = True
                report(f"Stopped further map downloads after a provider failure: {exc}")
            candidate_summary = Path(context["table_json_path"]).resolve(strict=False)
            if candidate_summary.is_file():
                tracks_summary_path = candidate_summary

        if cancel_event.is_set():
            raise GeoLocationsCancelled("Aborted.")
        report("Checking media-stage maps...")
        media_options = self._media_map_options()
        media_options.update(
            {
                "map_layouts": ("standard", "time-lapse"),
                "only_missing_or_stale": True,
                "continue_on_map_error": True,
                "map_failures": failures,
                "skip_rendering": skip_remaining_maps,
                "adventure_render_parameters_by_layout": {
                    "standard": self._track_map_parameter_signature("standard"),
                    "time-lapse": self._track_map_parameter_signature("time-lapse"),
                },
            }
        )
        plan = project_map_plan_from_sidecars(
            project_dir,
            control_path,
            tracks_summary_path=tracks_summary_path,
            sort_date_sections_by_tracks=self._use_track_order(),
            media_map_options=media_options,
            infer_gps_from_tracks=tracks_summary_path is not None,
            place_equivalence_m=self.parameters["locations.reuse_radius_m"],
        )
        removed_media = self._cleanup_obsolete_media_map_files(
            plan,
            trackimages_dir,
        )
        if removed_media:
            report(f"Removed {len(removed_media)} obsolete media map file(s).")
        created_paths.extend(
            render_project_map_plan(
                plan,
                control_path,
                media_options,
                output_writer=output_writer,
            )
        )
        return {
            "created_count": len(created_paths),
            "created_paths": [str(path) for path in created_paths],
            "failures": failures,
            "tracks_summary_path": (
                str(tracks_summary_path) if tracks_summary_path is not None else None
            ),
        }

    def startAutomaticMapGeneration_(self, imported_paths):
        """Run selective map maintenance after automatic metadata preparation."""
        if self.geolocations_thread is not None and self.geolocations_thread.is_alive():
            return
        project_dir = self._resolve_project_directory(
            allow_create=False,
            update_gpx_field=False,
        )
        control_path = self._control_file_path()
        if project_dir is None or control_path is None:
            return
        self.automatic_map_pending_imported_paths = list(imported_paths or [])
        self.automatic_map_review_control = control_path.is_file()
        self._prepare_geolocations_window("Generate and Update Maps")
        self.appendGeoLocationsOutputLine_(
            "Checking the shared overview and paired Standard/Time-Lapse maps."
        )
        self.set_status("Checking missing and outdated maps...")
        self.progress_bar.setIndeterminate_(True)
        self.progress_bar.setHidden_(False)
        self.progress_bar.startAnimation_(None)
        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_mode = "automatic_maps"
        output_writer = GeoLocationsOutputWriter(self)

        def status_callback(message):
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setStatusFromWorker:",
                str(message),
                False,
            )

        def run_task():
            try:
                self.automatic_map_generation_result = (
                    self._generate_missing_project_maps_worker(
                        project_dir=project_dir,
                        control_path=control_path,
                        cancel_event=cancel_event,
                        output_writer=output_writer,
                        status_callback=status_callback,
                    )
                )
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:",
                    "cancelled",
                    True,
                )
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:",
                    f"error: {exc}",
                    True,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "geoLocationsRunFinished:",
                "success",
                True,
            )

        self.geolocations_thread = threading.Thread(
            target=run_task,
            name="automatic-map-generation",
            daemon=True,
        )
        self.geolocations_thread.start()

    def startControlCreationAfterMaps_(self, _sender):
        """Continue the retained processing run with control-file creation."""
        self._start_assistant_control_assets_creation()

    def offerControlUpdateAfterMaps_(self, _sender):
        """Ask before synchronizing an existing, potentially edited control file."""
        if confirm_alert(
            "Update the existing slide-show control file?",
            "Metadata and maps are ready. Update Control File will review required "
            "map references and media changes without replacing manual ordering.",
            "Review Update",
            "Keep Existing File",
        ):
            pending = list(self.pending_control_update_after_status or [])
            self.pending_control_update_after_status = None
            self._start_control_file_update_analysis(imported_paths=pending)
            return
        self.pending_control_update_after_status = None
        self._finish_geolocations_run(
            "Adventure processing complete; the existing control file was retained.",
            "Maps updated; existing control file retained.",
        )

    @objc.IBAction
    def updateMediaMetadata_(self, _sender):
        include_place_names = bool(
            self.media_place_names_checkbox.state() == NSControlStateValueOn
        )
        self._start_assistant_metadata_preparation(include_place_names)

    def _start_automatic_media_metadata(self, imported_paths=None):
        """Prepare sidecars after media acceptance and defer control updates until completion."""
        pending = {
            str(Path(path).resolve(strict=False))
            for path in (imported_paths or [])
        }
        existing = {
            str(Path(path).resolve(strict=False))
            for path in getattr(self, "pending_imported_media_for_control_update", [])
        }
        self.pending_imported_media_for_control_update = sorted(existing | pending)
        self._start_assistant_metadata_preparation(
            bool(self.parameters.get("locations.add_place_names", True))
        )

    def startPendingImportedMediaUpdate_(self, paths):
        pending_paths = [Path(str(path)).resolve(strict=False) for path in (paths or [])]
        if (
            self._control_file_path() is not None
            and self._control_file_path().is_file()
        ):
            self._start_control_file_update_analysis(imported_paths=pending_paths)

    def _start_assistant_metadata_preparation(self, include_place_names):
        """Prepare only missing, invalid, legacy, or changed sidecars for onboarding."""
        if self.geolocations_thread is not None and self.geolocations_thread.is_alive():
            show_alert("A metadata operation is already running.")
            return
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            return
        media_paths = [Path(item["path"]) for item in self.project_media_items()]
        if not media_paths:
            self.pending_imported_media_for_control_update = []
            show_alert("No photos or videos are available.")
            return
        self._prepare_geolocations_window("Prepare Media Metadata")
        self.appendGeoLocationsOutputLine_(
            "Checking sidecars; current metadata is reused and only missing, invalid, legacy, or changed files are prepared."
        )
        self.set_status("Checking media metadata...")
        self.set_progress(0.0, max(len(media_paths), 1))
        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_mode = "assistant_metadata"
        self.geolocations_temp_paths = []
        self.assistant_metadata_place_names = bool(include_place_names)
        self.assistant_metadata_places_skipped = False
        self.assistant_metadata_place_report = None
        self.assistant_metadata_candidate_paths = []
        self.workflow_assistant_state["place_names_requested"] = bool(include_place_names)
        overwrite_place_names = False
        output_writer = GeoLocationsOutputWriter(self)
        media_only = self._uses_media_only_maps()

        def progress_callback(current, total, filename):
            label = f"Preparing metadata {current}/{total}: {filename}" if filename else "Checking media metadata..."
            self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusFromWorker:", label, True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setProgressFromWorker:", (float(current), float(max(total, 1))), True
            )

        def detail_callback(line):
            output_writer.write(f"{line}\n")

        def run_task():
            try:
                candidates = discover_media_update_candidates(
                    project_dir,
                    progress_callback=progress_callback,
                    detail_callback=detail_callback,
                    cancel_event=cancel_event,
                )
                self.assistant_metadata_candidate_paths = [
                    str(item.media_path.resolve(strict=False))
                    for item in candidates
                ]
                actions = {str(item.media_path): item.action for item in candidates}
                plan = analyze_control_file_updates(
                    project_dir,
                    [item.media_path for item in candidates],
                    control_file=None,
                    tracks_summary_path=None,
                    actions=actions,
                    media_only=media_only,
                    progress_callback=progress_callback,
                    detail_callback=detail_callback,
                    cancel_event=cancel_event,
                    place_equivalence_m=self.parameters["locations.reuse_radius_m"],
                )
                result = commit_control_file_update_plan(
                    plan,
                    update_place_names=False,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
                self.assistant_metadata_result = {
                    "total": len(media_paths),
                    "candidates": len(candidates),
                    "result": result.media,
                }
                if include_place_names:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setPlaceNameSkipAvailable:", True, True
                    )
                    try:
                        place_result = run_geolocations_with_options(
                            project_dir,
                            redo_reverse_geolocation=True,
                            overwrite_reverse_geolocation=overwrite_place_names,
                            infer_gps_from_tracks=False,
                            progress_callback=progress_callback,
                            stdout=output_writer,
                            stderr=output_writer,
                            cancel_event=cancel_event,
                            **self._geolocation_options(include_tracks=True),
                        )
                        self.assistant_metadata_place_report = place_result.get(
                            "place_update_report"
                        )
                    except GeoLocationsCancelled:
                        if not self.geolocations_skip_place_names:
                            raise
                        self.assistant_metadata_places_skipped = True
                    finally:
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setPlaceNameSkipAvailable:", False, True
                        )
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:", "cancelled", True
                )
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:", f"error: {exc}", True
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "geoLocationsRunFinished:", "success", True
            )

        self.geolocations_thread = threading.Thread(
            target=run_task, name="assistant-metadata", daemon=True
        )
        self.geolocations_thread.start()

    def _start_assistant_control_assets_creation(self):
        """Create the control file plus both map variants as one guided operation."""
        if self.geolocations_thread is not None and self.geolocations_thread.is_alive():
            show_alert("A control-file or metadata operation is already running.")
            return
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        control_path = self._control_file_path()
        trackimages_dir = self._track_images_dir()
        if project_dir is None or control_path is None or trackimages_dir is None:
            return
        if control_path.exists():
            if not confirm_alert(
                "Replace the existing slide-show control file?",
                "The current file will be replaced only after the new list has been prepared successfully.",
                "Replace",
                "Cancel",
            ):
                return
        media_only = self._uses_media_only_maps()
        skip_map_generation = bool(self.assistant_control_skip_maps)
        self.assistant_control_skip_maps = False
        gpx_path = None if media_only else self._current_single_gpx_path()
        if not media_only and (gpx_path is None or not gpx_path.is_file()):
            show_alert(
                "No confirmed GPX file is available.",
                "Choose a GPX file or select No GPX file - use only photos in the GPX Files section first.",
            )
            return

        trackimages_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=".mycamino-control-", dir=project_dir))
        staged_photolist = staging_dir / control_path.name
        staged_control = build_sorted_output_path(staged_photolist)
        self.geolocations_temp_paths = [staged_photolist, staged_control]
        self._prepare_geolocations_window("Create Control File and Maps")
        self.appendGeoLocationsOutputLine_("Creating the overview and both Standard and Time-Lapse map variants.")
        self.set_status("Preparing control file and maps...")
        self.set_progress(0.0, 4.0)
        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_mode = "assistant_control_assets"
        self.geolocations_result_path = control_path
        output_writer = GeoLocationsOutputWriter(self)
        media_map_options = self._media_map_options()
        media_map_options.update(
            {
                "map_layouts": ("standard", "time-lapse"),
                "only_missing_or_stale": True,
                "continue_on_map_error": True,
                "map_failures": [],
                "adventure_render_parameters_by_layout": {
                    "standard": self._track_map_parameter_signature("standard"),
                    "time-lapse": self._track_map_parameter_signature("time-lapse"),
                },
            }
        )
        if skip_map_generation:
            media_map_options["skip_rendering"] = True
        geolocation_options = self._geolocation_options()
        requested_places = bool(self.parameters.get("locations.add_place_names", True))
        overwrite_requested_places = False
        self.workflow_assistant_state["place_names_requested"] = requested_places
        self.assistant_control_places_skipped = False
        project_name = self._current_project_name()
        sort_by_tracks = self._use_track_order()

        def report_progress(value, label):
            self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusFromWorker:", label, True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setProgressFromWorker:", (float(value), 4.0), True
            )

        def media_progress(current, total, filename):
            fraction = float(current) / float(max(total, 1))
            report_progress(2.0 + fraction, f"Creating control file {current}/{total}: {filename}" if filename else "Creating control file...")

        def run_task():
            map_failures = media_map_options["map_failures"]
            try:
                if skip_map_generation:
                    summary_path = None if media_only else self._tracks_summary_json_path()
                    map_result = {
                        "failures": [],
                        "tracks_summary_path": (
                            str(summary_path)
                            if summary_path is not None and summary_path.is_file()
                            else None
                        ),
                    }
                    report_progress(1.0, "Maps already prepared; creating the control file.")
                else:
                    map_result = self._generate_missing_project_maps_worker(
                        project_dir=project_dir,
                        control_path=control_path,
                        cancel_event=cancel_event,
                        output_writer=output_writer,
                        status_callback=lambda message: report_progress(1.0, message),
                    )
                for failure in map_result.get("failures", []):
                    if failure not in map_failures:
                        map_failures.append(failure)
                summary_text = map_result.get("tracks_summary_path")
                tracks_summary_path = (
                    Path(summary_text).resolve(strict=False)
                    if summary_text
                    else None
                )
                if map_failures:
                    media_map_options["skip_rendering"] = True

                run_geolocations_with_options(
                    project_dir,
                    photolist=staged_photolist,
                    tracks=tracks_summary_path,
                    infer_gps_from_tracks=bool(tracks_summary_path),
                    sort_date_sections_by_tracks=sort_by_tracks,
                    media_map_options=media_map_options,
                    progress_callback=media_progress,
                    stdout=output_writer,
                    stderr=output_writer,
                    cancel_event=cancel_event,
                    **geolocation_options,
                )
                if cancel_event.is_set():
                    raise GeoLocationsCancelled("Aborted.")
                os.replace(staged_control, control_path)
                report_progress(3.5, "Installing control file...")
                if requested_places and tracks_summary_path is not None:
                    self.assistant_control_places_skipped = False
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setPlaceNameSkipAvailable:", True, True
                    )
                    try:
                        run_geolocations_with_options(
                            project_dir,
                            tracks=tracks_summary_path,
                            redo_reverse_geolocation=True,
                            overwrite_reverse_geolocation=overwrite_requested_places,
                            infer_gps_from_tracks=False,
                            progress_callback=media_progress,
                            stdout=output_writer,
                            stderr=output_writer,
                            cancel_event=cancel_event,
                            **geolocation_options,
                        )
                    except GeoLocationsCancelled:
                        if not self.geolocations_skip_place_names:
                            raise
                        self.assistant_control_places_skipped = True
                    finally:
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setPlaceNameSkipAvailable:", False, True
                        )
                self.assistant_control_map_failures = list(map_failures)
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:", "cancelled", True
                )
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:", f"error: {exc}", True
                )
                return
            finally:
                try:
                    shutil.rmtree(staging_dir)
                except OSError:
                    pass
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "geoLocationsRunFinished:", "success", True
            )

        self.geolocations_thread = threading.Thread(
            target=run_task, name="assistant-control-assets", daemon=True
        )
        self.geolocations_thread.start()

    @objc.IBAction
    def createControlFile_(self, _sender):
        self._start_assistant_control_assets_creation()

    def _createControlFileLegacy_(self, _sender):
        """Previous control-only workflow retained temporarily for API compatibility."""
        if self.geolocations_thread is not None and self.geolocations_thread.is_alive():
            show_alert("Control-file creation is already running.")
            return
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        control_file_path = self._control_file_path()
        if control_file_path is None:
            show_alert("Please choose a project directory first.")
            return
        if not self._confirm_shared_asset_change("control_file", control_file_path.name, "control file"):
            return

        route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
        media_only = self._uses_media_only_maps()
        tracks_summary_path = None if media_only else self._tracks_summary_json_path()
        if not media_only and (tracks_summary_path is None or not tracks_summary_path.exists()):
            alert = NSAlert.alloc().init()
            alert.setMessageText_("No tracks summary was found.")
            alert.setInformativeText_(
                "Run Generate and Update Maps first to create the track summary, or continue without tracks."
            )
            alert.addButtonWithTitle_("Generate and Update Maps")
            if route_source == "automatic":
                alert.addButtonWithTitle_("Continue Without Tracks")
            alert.addButtonWithTitle_("Cancel")
            response = int(alert.runModal())
            if response == 1000:
                self.makePlots_(None)
                tracks_summary_path = self._tracks_summary_json_path()
                if tracks_summary_path is None or not tracks_summary_path.exists():
                    show_alert("No tracks were found.", "Generate and Update Maps first or continue without tracks.")
                    return
            elif response == 1001 and route_source == "automatic":
                tracks_summary_path = None
            else:
                return

        if control_file_path.exists():
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Slide show control file already exists.")
            alert.setInformativeText_(f"Do you want to overwrite\n{control_file_path}?")
            alert.addButtonWithTitle_("Overwrite")
            alert.addButtonWithTitle_("Cancel")
            if int(alert.runModal()) != 1000:
                return

        self._prepare_geolocations_window(f"Create Slide Show Control File: {control_file_path.name}")
        self.appendGeoLocationsOutputLine_(f"Project directory: {project_dir}")
        self.appendGeoLocationsOutputLine_(f"Output file: {control_file_path}")
        if tracks_summary_path is not None:
            self.appendGeoLocationsOutputLine_(f"Tracks summary: {tracks_summary_path}")
        else:
            self.appendGeoLocationsOutputLine_("Tracks summary: none")
        self.set_status("Creating slide show control file...")
        self.set_progress(0.0, 1.0)

        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_result_path = control_file_path
        self.geolocations_mode = "create"
        self.geolocations_temp_paths = []
        output_writer = GeoLocationsOutputWriter(self)
        media_map_options = self._media_map_options()
        sort_date_sections_by_tracks = self._use_track_order()
        geolocation_options = self._geolocation_options()

        def progress_callback(current, total, filename):
            label = f"Processing {current}/{total}: {filename}" if filename else "Preparing media list..."
            self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusFromWorker:", label, True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setProgressFromWorker:",
                (float(current), float(max(total, 1))),
                True,
            )

        def run_task():
            try:
                run_geolocations_with_options(
                    project_dir,
                    photolist=control_file_path,
                    tracks=tracks_summary_path,
                    infer_gps_from_tracks=True,
                    sort_date_sections_by_tracks=sort_date_sections_by_tracks,
                    media_map_options=media_map_options,
                    progress_callback=progress_callback,
                    stdout=output_writer,
                    stderr=output_writer,
                    cancel_event=cancel_event,
                    **geolocation_options,
                )
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "cancelled", True)
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:",
                    f"error: {exc}",
                    True,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "success", True)

        self.geolocations_thread = threading.Thread(target=run_task, name="geolocations-create", daemon=True)
        self.geolocations_thread.start()

    @objc.IBAction
    def getPlaceNames_(self, _sender):
        if self.geolocations_thread is not None and self.geolocations_thread.is_alive():
            show_alert("A GetGeoLocations operation is already running.")
            return
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        control_file_path = self._control_file_path()
        if control_file_path is None:
            show_alert("Please choose a project directory first.")
            return
        if control_file_path.exists() and not self._confirm_shared_asset_change("control_file", control_file_path.name, "control file"):
            return

        self._prepare_geolocations_window("Update Media Metadata: Place Names")
        self.appendGeoLocationsOutputLine_(f"Project directory: {project_dir}")
        self.appendGeoLocationsOutputLine_(
            "Using existing media sidecars only; missing or invalid metadata is skipped."
        )
        overwrite_places = False
        self.geolocations_places_overwrite = False
        self.appendGeoLocationsOutputLine_(
            "Reverse geocoding missing or GPS-invalidated place names from sidecar metadata."
        )
        self.set_status("Reverse geocoding place names...")
        self.set_progress(0.0, 1.0)

        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_result_path = control_file_path
        self.geolocations_mode = "places"
        self.geolocations_temp_paths = []
        self.setPlaceNameSkipAvailable_(True)
        output_writer = GeoLocationsOutputWriter(self)

        def progress_callback(current, total, filename):
            label = f"Reverse geocoding {current}/{total}: {filename}" if filename else "Preparing media list..."
            self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusFromWorker:", label, True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setProgressFromWorker:",
                (float(current), float(max(total, 1))),
                True,
            )

        def run_task():
            try:
                run_geolocations_with_options(
                    project_dir,
                    redo_reverse_geolocation=True,
                    overwrite_reverse_geolocation=overwrite_places,
                    infer_gps_from_tracks=False,
                    progress_callback=progress_callback,
                    stdout=output_writer,
                    stderr=output_writer,
                    cancel_event=cancel_event,
                    **self._geolocation_options(include_tracks=True),
                )
            except GeoLocationsCancelled:
                if self.geolocations_skip_place_names:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "geoLocationsRunFinished:", "place_names_skipped", True
                    )
                    return
                self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "cancelled", True)
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:",
                    f"error: {exc}",
                    True,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "success", True)

        self.geolocations_thread = threading.Thread(target=run_task, name="geolocations-places", daemon=True)
        self.geolocations_thread.start()

    def prepare_media_browser(self):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        media_files = [
            path
            for path in project_dir.iterdir()
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
        ]
        if not media_files:
            show_alert("No media files are available.", "Import photos or videos into the project directory first.")
            return
        self.open_media_browser_window()

    def open_media_browser_window(self):
        rows, items = self.media_browser_table_rows()
        if not rows:
            show_alert("No media files are available.", "Import photos or videos into the project directory first.")
            return
        if self.media_browser_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(180.0, 160.0, 1100.0, 430.0),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setTitle_("Select Media Files")
            content = window.contentView()
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16.0, 84.0, 1068.0, 314.0))
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(True)
            table = NSTableView.alloc().initWithFrame_(scroll.bounds())
            table.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            table.setAllowsMultipleSelection_(True)
            table.setUsesAlternatingRowBackgroundColors_(True)
            table.setDoubleAction_("openSelectedMediaBrowserItem:")
            table.setTarget_(self)
            columns = [
                ("included", "Included", 70.0),
                ("type", "Type", 54.0),
                ("metadata", "Metadata", 90.0),
                ("freshness", "Freshness", 82.0),
                ("name", "File", 230.0),
                ("time", "Date/time", 145.0),
                ("gps", "GPS", 142.0),
                ("place", "Place", 220.0),
            ]
            for identifier, title, width in columns:
                column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
                column.setWidth_(width)
                column.headerCell().setStringValue_(title)
                column.setSortDescriptorPrototype_(
                    NSSortDescriptor.sortDescriptorWithKey_ascending_(identifier, True)
                )
                table.addTableColumn_(column)
            data_source = GPSTrackShowGUIMediaBrowserDataSource.alloc().initWithController_(self)
            table.setDataSource_(data_source)
            table.setDelegate_(data_source)
            scroll.setDocumentView_(table)
            content.addSubview_(scroll)

            merge_button = self._make_button("Recheck Selected", "analyzeSelectedMediaUpdates:")
            view_button = self._make_button("View", "openSelectedMediaBrowserItem:")
            close_button = self._make_button("Close", "closeMediaBrowser:")
            merge_button.setFrame_(NSMakeRect(16.0, 24.0, 142.0, FIELD_HEIGHT))
            view_button.setFrame_(NSMakeRect(168.0, 24.0, 86.0, FIELD_HEIGHT))
            close_button.setFrame_(NSMakeRect(998.0, 24.0, 86.0, FIELD_HEIGHT))
            close_button.setAutoresizingMask_(1)
            content.addSubview_(merge_button)
            content.addSubview_(view_button)
            content.addSubview_(close_button)
            hint = self._make_label(
                "Select files only when you deliberately want to re-read their embedded metadata, then press Recheck Selected.",
                size=12.0,
            )
            hint.setFrame_(NSMakeRect(16.0, 60.0, 1068.0, 18.0))
            hint.setAutoresizingMask_(NSViewWidthSizable)
            content.addSubview_(hint)

            self.media_browser_window = window
            self.media_browser_table = table
            self.media_browser_data_source = data_source
            self.media_browser_merge_button = merge_button
            self.media_browser_hint_label = hint

        self.media_browser_rows = rows
        self.media_browser_items = items
        self.media_browser_data_source.setRows_columns_(
            rows,
            ["included", "type", "metadata", "freshness", "name", "time", "gps", "place"],
        )
        if self.media_browser_merge_button is not None:
            self.media_browser_merge_button.setHidden_(self.media_browser_mode != "recheck")
        self.media_browser_window.setTitle_(
            "Choose Media to Recheck" if self.media_browser_mode == "recheck" else "Project Media Files"
        )
        self.media_browser_table.reloadData()
        self.media_browser_table.deselectAll_(None)
        self.media_browser_window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)
        missing_count = sum(row.get("metadata") == "Missing" for row in rows)
        invalid_count = sum(row.get("metadata") == "Invalid" for row in rows)
        changed_count = sum(str(row.get("freshness", "")).lower() == "changed" for row in rows)
        unknown_count = sum(str(row.get("freshness", "")).lower() == "unknown" for row in rows)
        self.set_status(
            f"Media browser: {len(rows)} file(s), {missing_count} missing and "
            f"{invalid_count} invalid, {changed_count} changed, {unknown_count} freshness unknown."
        )

    def sortMediaBrowserByColumn_ascending_(self, column, ascending):
        self.media_browser_sort_column = str(column)
        self.media_browser_sort_ascending = bool(ascending)
        rows, items = self.media_browser_table_rows()
        self.media_browser_rows = rows
        self.media_browser_items = items
        if self.media_browser_data_source is not None:
            self.media_browser_data_source.setRows_columns_(
                rows,
                ["included", "type", "metadata", "freshness", "name", "time", "gps", "place"],
            )
        if self.media_browser_table is not None:
            self.media_browser_table.reloadData()

    def selected_media_browser_items(self):
        if self.media_browser_table is None:
            return []
        selected = self.media_browser_table.selectedRowIndexes()
        return [
            self.media_browser_items[index]
            for index in range(len(self.media_browser_items))
            if selected.containsIndex_(index)
        ]

    @objc.IBAction
    def openSelectedMediaBrowserItem_(self, _sender):
        if self.media_browser_table is None:
            return
        row = self.media_browser_table.clickedRow()
        if row < 0:
            row = self.media_browser_table.selectedRow()
        if row < 0 or row >= len(self.media_browser_items):
            return
        self.show_media_viewer_item(row, items=self.media_browser_items)

    @objc.IBAction
    def analyzeSelectedMediaUpdates_(self, _sender):
        if self.media_browser_mode != "recheck":
            return
        items = self.selected_media_browser_items()
        if not items:
            show_alert("No media files selected.", "Select one or more media rows to recheck.")
            return
        paths = [Path(item["path"]).resolve(strict=False) for item in items]
        actions = {
            Path(item["path"]).name: (
                "repair" if item.get("metadata") in {"Missing", "Invalid"} else "refresh"
            )
            for item in items
        }
        if self.media_browser_window is not None:
            self.media_browser_window.orderOut_(None)
        self._start_control_file_update_analysis(paths=paths, actions=actions, append=self.media_update_plan is not None)

    def _start_control_file_update_analysis(
        self,
        *,
        paths=None,
        actions=None,
        imported_paths=None,
        append=False,
    ):
        if self.control_table_dirty and not self._save_control_table_before_external_update():
            return
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            return
        control_path = self._control_file_path()
        if control_path is not None and not control_path.exists():
            control_path = None
        tracks_path = self._tracks_summary_json_path()
        if tracks_path is not None and not tracks_path.exists():
            tracks_path = None
        media_only = self._uses_media_only_maps()
        if media_only:
            tracks_path = None
        sort_by_tracks = self._use_track_order()
        track_status = self.track_maps_status_cache
        summary_current = media_only
        if not media_only and track_status is not None:
            summary_current = bool(
                track_status.get("summary_exists")
                and not track_status.get("summary_out_of_date")
                and track_status.get("overview_exists")
                and not track_status.get("overview_out_of_date")
                and track_status.get("track_count") == track_status.get("track_total")
            )
        automatic = paths is None
        self._prepare_geolocations_window("Analyze Control File Updates")
        self.set_status("Analyzing control file..." if automatic else "Rechecking selected media...")
        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_mode = "media_update_analysis"
        self.geolocations_temp_paths = []
        self.media_update_pending_plan = None
        if not append:
            self.media_update_plan = None
            self.control_file_update_plan = None
        imported = [Path(path).resolve(strict=False) for path in (imported_paths or ())]
        selected_paths = [Path(path).resolve(strict=False) for path in (paths or ())]
        selected_actions = dict(actions or {})

        def progress_callback(current, total, filename):
            if current not in {0, total} and current % 10:
                return
            label = f"Analyzing {current}/{total}: {filename}" if filename else "Media analysis complete."
            self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusFromWorker:", label, True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setProgressFromWorker:", (float(current), float(max(total, 1))), True,
            )

        def run_task():
            try:
                if automatic:
                    def discovery_progress(current, total, filename):
                        if current not in {0, total} and current % 25:
                            return
                        label = f"Scanning {current}/{total}: {filename}" if filename else "Scanning project media..."
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setStatusFromWorker:", label, True,
                        )
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setProgressFromWorker:", (float(current), float(max(total, 1))), True,
                        )

                    candidates = discover_media_update_candidates(
                        project_dir,
                        imported_paths=imported,
                        only_imported=bool(imported),
                        progress_callback=discovery_progress,
                        cancel_event=cancel_event,
                    )
                    analysis_paths = [candidate.media_path for candidate in candidates]
                    analysis_actions = {
                        candidate.media_path.name: candidate.action for candidate in candidates
                    }
                else:
                    analysis_paths = selected_paths
                    analysis_actions = selected_actions
                pending_plan = analyze_control_file_updates(
                    project_dir,
                    analysis_paths,
                    control_file=control_path,
                    tracks_summary_path=tracks_path,
                    actions=analysis_actions,
                    sort_date_sections_by_tracks=sort_by_tracks,
                    summary_current=summary_current,
                    media_only=media_only,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    place_equivalence_m=self.parameters["locations.reuse_radius_m"],
                )
                if append and self.control_file_update_plan is not None:
                    existing = self.control_file_update_plan
                    if existing.media.control_signature != pending_plan.media.control_signature:
                        raise RuntimeError("The control file changed; run Update Control File again.")
                    replacement = {
                        str(item.media_path.resolve(strict=False)): item
                        for item in pending_plan.media.items
                    }
                    combined = []
                    for item in existing.media.items:
                        key = str(item.media_path.resolve(strict=False))
                        combined.append(replacement.pop(key, item))
                    combined.extend(replacement.values())
                    pending_plan.media.items = combined
                    pending_plan.media.warnings = list(
                        dict.fromkeys(existing.media.warnings + pending_plan.media.warnings)
                    )
                self.media_update_pending_plan = pending_plan
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "cancelled", True)
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:", f"error: {exc}", True,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "success", True)

        self.geolocations_thread = threading.Thread(target=run_task, name="control-file-update-analysis", daemon=True)
        self.geolocations_thread.start()

    def _show_no_control_file_updates(self):
        alert = NSAlert.alloc().init()
        track_plan = (
            self.control_file_update_plan.track_maps
            if self.control_file_update_plan is not None else None
        )
        if track_plan is not None and track_plan.warning:
            if "No slide show control file" in track_plan.warning:
                alert.setMessageText_("No control file is available yet.")
                alert.setInformativeText_(
                    "Use Create to build the initial slide-show list. Choose files manually only "
                    "when you want to prepare or re-read their metadata first."
                )
                alert.addButtonWithTitle_("Create Control File")
                alert.addButtonWithTitle_("Choose Files to Recheck...")
                alert.addButtonWithTitle_("Close")
                response = int(alert.runModal())
                if response == 1000:
                    self.createControlFile_(None)
                elif response == 1001:
                    self.chooseOtherMediaForUpdate_(None)
                return
            alert.setMessageText_("Maps must be generated or updated first.")
            alert.setInformativeText_(track_plan.warning)
            alert.addButtonWithTitle_("Generate and Update Maps")
            alert.addButtonWithTitle_("Choose Files to Recheck...")
            alert.addButtonWithTitle_("Close")
            response = int(alert.runModal())
            if response == 1000:
                self.updatePlots_(None)
            elif response == 1001:
                self.chooseOtherMediaForUpdate_(None)
            return
        alert.setMessageText_("Control file is up to date.")
        alert.setInformativeText_(
            "Track Map references and automatic media checks found no changes. "
            "Choose files manually only when you want to re-read otherwise unchanged metadata."
        )
        alert.addButtonWithTitle_("Choose Files to Recheck...")
        alert.addButtonWithTitle_("Close")
        if int(alert.runModal()) == 1000:
            self.chooseOtherMediaForUpdate_(None)

    @objc.IBAction
    def mergeSelectedMediaBrowserItems_(self, sender):
        """Compatibility selector retained for older nibless button state."""
        self.analyzeSelectedMediaUpdates_(sender)

    @objc.IBAction
    def closeMediaBrowser_(self, _sender):
        if self.media_browser_window is not None:
            self.media_browser_window.orderOut_(None)

    @staticmethod
    def _media_update_record_time(record):
        return local_datetime_text(record.photo_datetime) if record is not None else "-"

    @staticmethod
    def _media_update_record_gps(record):
        if record is None or record.latitude is None or record.longitude is None:
            return "-"
        return f"{float(record.latitude):.6f}, {float(record.longitude):.6f}"

    def _media_update_preview_rows(self):
        if self.media_update_plan is None:
            return []
        rows = []
        for item in self.media_update_plan.items:
            if item.control_update_pending:
                action = "Metadata only; generate or update maps"
            elif item.control_conflict and item.included_count > 1:
                action = "Conflict"
            elif item.included_count == 0:
                action = "Add + repair" if item.action == "repair" else "Add"
            elif item.reposition:
                action = "Refresh + move" if item.action == "refresh" else "Move"
            elif item.action == "repair":
                action = "Repair metadata"
            elif item.action == "refresh":
                action = "Refresh metadata"
            else:
                action = "Update row"
            place_action = "Update" if item.place_update_recommended else "Preserve"
            rows.append({
                "apply_update": bool(item.apply_update),
                "reposition": bool(item.reposition),
                "name": item.media_path.name,
                "old_time": self._media_update_record_time(item.old_record),
                "new_time": self._media_update_record_time(item.new_record),
                "old_gps": self._media_update_record_gps(item.old_record),
                "new_gps": self._media_update_record_gps(item.new_record),
                "old_place": str(item.old_record.place or "-") if item.old_record is not None else "-",
                "new_place": str(item.new_record.place or ("Lookup on apply" if item.place_update_recommended else "-")),
                "current": item.current_section,
                "proposed": item.proposed_section,
                "place_action": place_action,
                "action": action,
            })
        return rows

    def _track_map_update_review_text(self):
        if self.control_file_update_plan is None:
            return "Track Map references were not analyzed."
        plan = self.control_file_update_plan.track_maps
        if plan.warning:
            return f"Track Maps: {plan.warning}"
        if not plan.change_count:
            return "Track Map references are current."
        lines = ["Track Map reference changes (applied together for consistency):"]
        for name in plan.missing_overview + plan.missing_tracks:
            lines.append(f"Add: {name}")
        for old, new, _entry_type in plan.special_updates:
            lines.append(f"Replace: {old} -> {new}")
        for name in plan.reordered_tracks:
            lines.append(f"Move to canonical stage position: {name}")
        for name in plan.obsolete_overview + plan.obsolete_tracks:
            lines.append(f"Remove: {name}")
        return "\n".join(lines)

    def show_media_update_preview(self):
        plan = self.media_update_plan
        map_count = (
            self.control_file_update_plan.track_maps.change_count
            if self.control_file_update_plan is not None else 0
        )
        if plan is None or (not plan.items and not map_count):
            show_alert("No control-file updates were produced.")
            return
        if self.media_update_preview_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(100.0, 80.0, 1320.0, 600.0),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setTitle_("Review Control File Updates")
            content = window.contentView()
            map_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16.0, 500.0, 1288.0, 76.0))
            map_scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
            map_scroll.setHasVerticalScroller_(True)
            map_scroll.setBorderType_(1)
            map_text = NSTextView.alloc().initWithFrame_(map_scroll.bounds())
            map_text.setEditable_(False)
            map_text.setSelectable_(True)
            map_text.setFont_(NSFont.systemFontOfSize_(11.0))
            map_scroll.setDocumentView_(map_text)
            content.addSubview_(map_scroll)
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16.0, 92.0, 1288.0, 398.0))
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(True)
            table = NSTableView.alloc().initWithFrame_(scroll.bounds())
            table.setAllowsMultipleSelection_(True)
            table.setUsesAlternatingRowBackgroundColors_(True)
            table.setTarget_(self)
            table.setDoubleAction_("viewSelectedMediaUpdate:")
            columns = [
                ("apply_update", "Update", 58.0),
                ("reposition", "Move", 48.0),
                ("name", "File", 175.0),
                ("old_time", "Old date/time", 130.0),
                ("new_time", "New date/time", 130.0),
                ("old_gps", "Old GPS", 145.0),
                ("new_gps", "New GPS", 145.0),
                ("old_place", "Old place", 150.0),
                ("new_place", "New place", 150.0),
                ("current", "Current section", 150.0),
                ("proposed", "Proposed section", 150.0),
                ("place_action", "Place", 70.0),
                ("action", "Control action", 90.0),
            ]
            for identifier, title, width in columns:
                column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
                column.setWidth_(width)
                column.headerCell().setStringValue_(title)
                if identifier in {"apply_update", "reposition"}:
                    checkbox_cell = NSButtonCell.alloc().init()
                    checkbox_cell.setButtonType_(NSButtonTypeSwitch)
                    checkbox_cell.setTitle_("")
                    column.setDataCell_(checkbox_cell)
                table.addTableColumn_(column)
            data_source = GPSTrackShowGUIMediaUpdatePreviewDataSource.alloc().initWithController_(self)
            table.setDataSource_(data_source)
            table.setDelegate_(data_source)
            scroll.setDocumentView_(table)
            content.addSubview_(scroll)
            place_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(16.0, 52.0, 330.0, FIELD_HEIGHT))
            place_checkbox.setButtonType_(NSButtonTypeSwitch)
            place_checkbox.setTitle_("Add/update place names when required")
            content.addSubview_(place_checkbox)
            choose_button = self._make_button("Choose Other Media...", "chooseOtherMediaForUpdate:")
            view_button = self._make_button("View", "viewSelectedMediaUpdate:")
            apply_button = self._make_button("Apply Updates", "applyMediaUpdatePlan:")
            cancel_button = self._make_button("Cancel", "cancelMediaUpdatePreview:")
            choose_button.setFrame_(NSMakeRect(16.0, 20.0, 164.0, FIELD_HEIGHT))
            view_button.setFrame_(NSMakeRect(190.0, 20.0, 82.0, FIELD_HEIGHT))
            apply_button.setFrame_(NSMakeRect(1062.0, 20.0, 118.0, FIELD_HEIGHT))
            cancel_button.setFrame_(NSMakeRect(1190.0, 20.0, 114.0, FIELD_HEIGHT))
            apply_button.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
            cancel_button.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
            content.addSubview_(choose_button)
            content.addSubview_(view_button)
            content.addSubview_(apply_button)
            content.addSubview_(cancel_button)
            hint = self._make_label(
                "Track Map reference changes are applied as one consistency unit. Uncheck media Update or Move to preserve manual choices.",
                size=11.0,
            )
            hint.setFrame_(NSMakeRect(360.0, 54.0, 930.0, 18.0))
            hint.setAutoresizingMask_(NSViewWidthSizable)
            content.addSubview_(hint)
            self.media_update_preview_window = window
            self.media_update_preview_table = table
            self.media_update_preview_data_source = data_source
            self.media_update_preview_place_checkbox = place_checkbox
            self.media_update_preview_apply_button = apply_button
            self.media_update_preview_track_text = map_text
        rows = self._media_update_preview_rows()
        self.media_update_preview_data_source.setRows_columns_(
            rows,
            ["apply_update", "reposition", "name", "old_time", "new_time", "old_gps", "new_gps", "old_place", "new_place", "current", "proposed", "place_action", "action"],
        )
        self.media_update_preview_place_checkbox.setState_(
            NSControlStateValueOn
            if (
                bool(self.parameters.get("locations.add_place_names", True))
                and any(item.apply_update and item.place_update_recommended for item in plan.items)
            )
            else NSControlStateValueOff
        )
        self.media_update_preview_place_checkbox.setEnabled_(bool(plan.items))
        if self.media_update_preview_track_text is not None:
            self.media_update_preview_track_text.setString_(self._track_map_update_review_text())
        self.media_update_preview_table.reloadData()
        self.media_update_preview_window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)

    @objc.IBAction
    def chooseOtherMediaForUpdate_(self, _sender):
        if self.media_update_preview_window is not None:
            self.media_update_preview_window.orderOut_(None)
        self.media_browser_mode = "recheck"
        self.prepare_media_browser()

    @objc.IBAction
    def viewSelectedMediaUpdate_(self, _sender):
        if self.media_update_plan is None or self.media_update_preview_table is None:
            return
        row = self.media_update_preview_table.clickedRow()
        if row < 0:
            row = self.media_update_preview_table.selectedRow()
        if row < 0 or row >= len(self.media_update_plan.items):
            return
        item = self.media_update_plan.items[row]
        record = item.new_record
        viewer_item = {
            "path": item.media_path,
            "kind": "video" if item.media_path.suffix.lower() in VIDEO_EXTENSIONS else "image",
            "image": None,
            "name": item.media_path.name,
            "time": self._media_update_record_time(record),
            "gps": self._media_update_record_gps(record),
            "place": str(record.place or ""),
        }
        self.show_media_viewer_item(0, items=[viewer_item])

    @objc.IBAction
    def cancelMediaUpdatePreview_(self, _sender):
        if self.media_update_preview_window is not None:
            self.media_update_preview_window.orderOut_(None)
        self.media_update_plan = None
        self.control_file_update_plan = None

    @objc.IBAction
    def applyMediaUpdatePlan_(self, _sender):
        if self.media_update_plan is None or self.control_file_update_plan is None:
            return
        map_count = self.control_file_update_plan.track_maps.change_count
        if not map_count and not any(item.apply_update for item in self.media_update_plan.items):
            show_alert("No updates selected.", "Select at least one media Update checkbox before applying.")
            return
        if self.media_update_preview_window is not None:
            self.media_update_preview_window.orderOut_(None)
        self._start_control_file_update_commit(
            update_places=(
                self.media_update_preview_place_checkbox is not None
                and self.media_update_preview_place_checkbox.state()
                == NSControlStateValueOn
            )
        )

    def _start_control_file_update_commit(self, *, update_places):
        """Commit the analyzed plan, with or without a preceding review window."""
        if self.media_update_plan is None or self.control_file_update_plan is None:
            return
        self._prepare_geolocations_window("Apply Control File Updates")
        self.set_status("Applying control-file updates...")
        cancel_event = threading.Event()
        self.geolocations_cancel_event = cancel_event
        self.geolocations_mode = "media_update_commit"
        self.geolocations_temp_paths = []
        update_places = bool(update_places)
        geolocation_options = self._geolocation_options()
        media_map_options = self._media_map_options()

        def progress_callback(current, total, filename):
            label = f"Updating {current}/{total}: {filename}" if filename else "Committing control-file update..."
            self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatusFromWorker:", label, True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setProgressFromWorker:", (float(current), float(max(total, 1))), True,
            )

        def run_task():
            try:
                self.control_file_update_result = commit_control_file_update_plan(
                    self.control_file_update_plan,
                    update_place_names=update_places,
                    place_distance_m=geolocation_options["distance"],
                    geocode_timeout_seconds=geolocation_options["geocode_timeout_seconds"],
                    geocode_pacing_min_seconds=geolocation_options["geocode_pacing_min_seconds"],
                    geocode_pacing_max_seconds=geolocation_options["geocode_pacing_max_seconds"],
                    media_map_options=media_map_options,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "cancelled", True)
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "geoLocationsRunFinished:", f"error: {exc}", True,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_("geoLocationsRunFinished:", "success", True)

        self.geolocations_thread = threading.Thread(target=run_task, name="control-file-update-commit", daemon=True)
        self.geolocations_thread.start()

    @objc.IBAction
    def updateControlFile_(self, _sender):
        control_path = self._control_file_path()
        if control_path is not None and not self._confirm_shared_asset_change(
            "control_file", control_path.name, "control file"
        ):
            return
        self._start_control_file_update_analysis()

    @objc.IBAction
    def editControlFile_(self, _sender):
        control_file_path = self._control_file_path()
        if control_file_path is None:
            show_alert("Please choose a project directory first.")
            return
        if not control_file_path.exists():
            show_alert("Slide show control file does not exist.", str(control_file_path))
            return
        if not self._confirm_shared_asset_change("control_file", control_file_path.name, "control file"):
            return
        if self.load_slideshow_control_file(control_file_path):
            self.set_status(f"Loaded slide show control file {control_file_path}")

    @objc.IBAction
    def startSelectedSlideShow_(self, _sender):
        self._start_slide_show(continue_previous=False)

    @objc.IBAction
    def continueSelectedSlideShow_(self, _sender):
        self._start_slide_show(continue_previous=True)

    @objc.IBAction
    def startSlideShow_(self, _sender):
        self.startSelectedSlideShow_(_sender)

    @objc.IBAction
    def startTimeLapseShow_(self, _sender):
        self.parameters["slideshow.transition"] = "time_lapse"
        self.mark_dirty(immediate=True)
        self._start_slide_show()

    def _commit_music_playlist(self, show_errors=True):
        audio_dir = self._ensure_project_audio_directory(show_errors=show_errors)
        if audio_dir is None:
            return False
        text = str(self.music_field.stringValue()).strip()
        if not text:
            self._set_music_playlist(None, mark_dirty=True)
            return True
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = audio_dir / path
        path = path.resolve(strict=False)
        if (
            not path.is_file()
            or not playlist_belongs_to_audio_directory(path, audio_dir)
        ):
            if show_errors:
                show_alert(
                    "Playlist is not usable.",
                    "Choose an existing .playlist file directly inside the "
                    "project's audio folder.",
                )
            self._refresh_music_summary()
            return False
        self._set_music_playlist(path, mark_dirty=True)
        return True

    @objc.IBAction
    def musicPlaylistCommitted_(self, _sender):
        self._commit_music_playlist(show_errors=True)

    @objc.IBAction
    def musicSourceCommitted_(self, _sender):
        """Compatibility action for older field/action references."""
        self._commit_music_playlist(show_errors=True)

    @objc.IBAction
    def chooseMusicPlaylist_(self, _sender):
        audio_dir = self._ensure_project_audio_directory(show_errors=True)
        if audio_dir is None:
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["playlist"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(audio_dir)))
        if panel.runModal() != NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        playlist_path = Path(str(url.path())).resolve(strict=False)
        if not playlist_belongs_to_audio_directory(playlist_path, audio_dir):
            show_alert(
                "Choose a playlist from the project audio folder.",
                str(audio_dir),
            )
            return
        self._set_music_playlist(playlist_path, mark_dirty=True)
        self.set_status(f"Selected music playlist {playlist_path.name}.")

    @objc.IBAction
    def openMusicFolder_(self, _sender):
        audio_dir = self._ensure_project_audio_directory(show_errors=True)
        if audio_dir is None:
            return
        opened = bool(
            NSWorkspace.sharedWorkspace().openURL_(
                NSURL.fileURLWithPath_(str(audio_dir))
            )
        )
        if not opened:
            show_alert("Could not open the project audio folder.", str(audio_dir))
            return
        self.set_status(
            "Opened the audio folder. Copy audio files and album directories "
            "into this folder."
        )

    @objc.IBAction
    def chooseMusicSource_(self, _sender):
        """Compatibility action for older button/action references."""
        self.openMusicFolder_(_sender)

    @objc.IBAction
    def createMusicPlaylist_(self, _sender):
        directory = self._ensure_project_audio_directory(show_errors=True)
        if directory is None:
            return
        files = audio_files_in_directory(directory)
        if not files:
            show_alert(
                "No supported audio files found.",
                "Copy audio files or album directories into:\n"
                f"{directory}",
            )
            return
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["playlist"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(directory)))
        panel.setNameFieldStringValue_(
            f"{project_filename_base(self._current_project_name())}.playlist"
        )
        if panel.runModal() != NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        playlist_path = Path(str(url.path())).resolve(strict=False)
        if playlist_path.suffix.casefold() != ".playlist":
            playlist_path = playlist_path.with_suffix(".playlist")
        if not playlist_belongs_to_audio_directory(playlist_path, directory):
            show_alert(
                "Create the playlist in the project audio folder.",
                str(directory),
            )
            return
        try:
            write_text_atomic(playlist_path, generated_playlist_text(files, directory))
        except OSError as exc:
            show_alert("Could not create the music playlist.", str(exc))
            return
        self._set_music_playlist(playlist_path, mark_dirty=True)
        self.set_status(f"Created music playlist {playlist_path.name} with {len(files)} audio files.")

    @objc.IBAction
    def updateMusicPlaylist_(self, sender):
        directory = self._ensure_project_audio_directory(show_errors=True)
        if directory is None:
            return
        files = audio_files_in_directory(directory)
        if not files:
            show_alert(
                "No supported audio files found.",
                "Copy audio files or album directories into:\n"
                f"{directory}",
            )
            return
        playlist_path = Path(self.music_playlist) if self.music_playlist is not None else self._preferred_music_playlist(directory)
        if playlist_path is None or not playlist_path.is_file():
            self.createMusicPlaylist_(sender)
            return
        try:
            existing = playlist_path.read_text(encoding="utf-8")
            updated, missing = updated_playlist_text(existing, files, directory)
            if missing:
                write_text_atomic(playlist_path, updated)
        except (OSError, UnicodeError) as exc:
            show_alert("Could not update the music playlist.", str(exc))
            return
        self._set_music_playlist(playlist_path, mark_dirty=True)
        if missing:
            self.set_status(f"Added {len(missing)} audio file(s) to {playlist_path.name}.")
        else:
            self.set_status(f"{playlist_path.name} already lists all discovered audio files.")

    @objc.IBAction
    def editMusicPlaylist_(self, sender):
        directory = self._ensure_project_audio_directory(show_errors=True)
        if directory is None:
            return
        playlist_path = Path(self.music_playlist) if self.music_playlist is not None else self._preferred_music_playlist(directory)
        if playlist_path is None or not playlist_path.is_file():
            self.createMusicPlaylist_(sender)
            playlist_path = Path(self.music_playlist) if self.music_playlist is not None else None
        if playlist_path is None or not playlist_path.is_file():
            return
        workspace = NSWorkspace.sharedWorkspace()
        try:
            opened = bool(workspace.openFile_withApplication_(str(playlist_path), "TextEdit"))
        except Exception:
            opened = bool(workspace.openURL_(NSURL.fileURLWithPath_(str(playlist_path))))
        if not opened:
            show_alert("Could not open the music playlist.", str(playlist_path))
            return
        self.set_status(f"Opened {playlist_path.name} in TextEdit.")

    def _validated_slideshow_resume_position(self, control_file_path):
        position = self.slideshow_resume_position
        if not isinstance(position, dict) or position.get("completed"):
            return None
        try:
            stored_control = Path(str(position.get("control_file", ""))).expanduser().resolve(strict=False)
            current_control = Path(control_file_path).expanduser().resolve(strict=False)
            playlist_index = int(position["playlist_index"])
            lines = [line.strip() for line in current_control.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (KeyError, OSError, TypeError, ValueError):
            return None
        if stored_control != current_control or not 0 <= playlist_index < len(lines):
            return None
        stored_line = position.get("line_text")
        if isinstance(stored_line, str) and stored_line != lines[playlist_index]:
            return None
        return dict(position)

    def _update_slideshow_continue_button(self):
        if not hasattr(self, "slideshow_continue_button"):
            return
        position = self.slideshow_resume_position
        self.slideshow_continue_button.setEnabled_(
            isinstance(position, dict) and not bool(position.get("completed"))
        )

    def _start_slide_show(
        self,
        time_lapse: bool | None = None,
        continue_previous: bool = False,
        transient_resume_position=None,
    ):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        control_file_path = self._control_file_path()
        if control_file_path is None or not control_file_path.exists():
            show_alert(
                "Slide show control file does not exist.",
                "Create it first in the Slide Show Control File section.",
            )
            self.set_status("Slide show control file missing.")
            return
        trackimages_dir = self._track_images_dir()
        if trackimages_dir is None or not trackimages_dir.exists() or not trackimages_dir.is_dir():
            show_alert(
                "Track images directory does not exist.",
                "Run Generate and Update Maps first to create the map directory and images.",
            )
            self.set_status("Track images directory missing.")
            return
        if self.slideshow_process is not None and self.slideshow_process.poll() is None:
            self.set_status("Slide show is already running.")
            return

        resume_position = (
            dict(transient_resume_position)
            if isinstance(transient_resume_position, dict)
            else (
                self._validated_slideshow_resume_position(control_file_path)
                if continue_previous
                else None
            )
        )
        if continue_previous and resume_position is None:
            self.slideshow_resume_position = None
            self.mark_dirty(immediate=True)
            show_alert(
                "No saved slide-show position is available.",
                "Use Start to begin at the start of the current slide-show control file.",
            )
            self._update_slideshow_continue_button()
            return
        state_file = project_dir / ".mycamino-slideshow-state.json"
        try:
            state_file.unlink(missing_ok=True)
        except OSError as exc:
            show_alert("Could not prepare slide-show state file.", str(exc))
            return

        try:
            command = self._slideshow_command(
                project_dir,
                control_file_path,
                trackimages_dir,
                resume_position=resume_position,
                state_file=state_file,
            )
            self.slideshow_process = subprocess.Popen(
                command,
                cwd=str(self.base_dir),
            )
            self.workflow_assistant_state["slideshow_started"] = True
            self.mark_dirty(immediate=True)
            self.refresh_workflow_assistant()
            if (
                not continue_previous
                and transient_resume_position is None
                and self.slideshow_resume_position is not None
            ):
                self.slideshow_resume_position = None
                self._update_slideshow_continue_button()
                self.mark_dirty(immediate=True)
        except Exception as exc:
            show_alert("Could not start slide show.", str(exc))
            self.set_status("Slide show failed to start.")
            return
        watcher = threading.Thread(
            target=self._watch_slideshow_process,
            args=(self.slideshow_process, state_file),
            daemon=True,
        )
        watcher.start()
        style = str(self.parameters.get("slideshow.transition", "time_lapse")).replace("_", " ")
        self.set_status(
            f"Started slide show with {style} as the initial style, "
            f"{control_file_path.name}, and trackdir {trackimages_dir}."
        )

    @objc.IBAction
    def exportPdfSummary_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        gpx_path = self._current_single_gpx_path()
        if gpx_path is None or not gpx_path.exists():
            show_alert("Please select exactly one existing GPX file first.")
            return
        controller = self._open_gpx_editor(
            input_paths=[gpx_path],
            output_path=gpx_path,
            open_pdf_summary=True,
            project_name=self._current_project_name() or project_dir.name,
        )
        if controller is not None:
            self.set_status("Opened GPXEditor and PDF Summary options.")

    def _watch_slideshow_process(self, process, state_file):
        return_code = process.wait()
        resume_state = None
        try:
            if state_file.is_file():
                with state_file.open("r", encoding="utf-8") as handle:
                    loaded_state = json.load(handle)
                if isinstance(loaded_state, dict):
                    resume_state = loaded_state
        except (OSError, json.JSONDecodeError):
            resume_state = None
        finally:
            try:
                state_file.unlink(missing_ok=True)
            except OSError:
                pass
        if return_code == 0:
            message = "Slide show closed."
        else:
            message = f"Slide show exited with code {return_code}."
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "slideShowProcessFinished:",
            {
                "pid": process.pid,
                "message": message,
                "resume_state": resume_state,
            },
            False,
        )

    def slideShowProcessFinished_(self, result):
        active_process = self.slideshow_process
        if active_process is not None and active_process.pid == int(result.get("pid", -1)):
            self.slideshow_process = None
        resume_state = result.get("resume_state")
        if isinstance(resume_state, dict):
            new_position = None if resume_state.get("completed") else resume_state
            if new_position != self.slideshow_resume_position:
                self.slideshow_resume_position = new_position
                self._update_slideshow_continue_button()
                if self.save_project_configuration():
                    suffix = " Resume position saved automatically." if new_position is not None else " Resume position cleared."
                    self.set_status(str(result.get("message", "Slide show closed.")) + suffix)
                    return
        self.set_status(str(result.get("message", "Slide show closed.")))

    def _slideshow_command(
        self,
        project_dir,
        control_file_path,
        trackimages_dir,
        resume_position=None,
        state_file=None,
    ):
        settings = self.parameters
        initial_style = str(settings["slideshow.transition"]).upper()
        if isinstance(resume_position, dict):
            saved_style = str(resume_position.get("style") or "").strip().upper()
            if saved_style in {
                "TIME_LAPSE",
                "BLEND",
                "FADE",
                "SWITCH",
                "EXPAND",
                "COLLAGE",
                "QUAD",
                "RANDOM",
            }:
                initial_style = saved_style
            elif str(resume_position.get("mode") or "").casefold() == "time-lapse":
                initial_style = "TIME_LAPSE"
        args = [
            str(project_dir),
            "--inputlist",
            str(control_file_path),
            "--trackdir",
            str(trackimages_dir.resolve(strict=False)),
            "--duration",
            str(settings["slideshow.media_duration_seconds"]),
            "--transition-duration-ms",
            str(settings["slideshow.transition_duration_ms"]),
            "--transition",
            initial_style,
            "--adventure-title",
            self._current_project_name() or project_dir.name,
            "--adventure-description",
            self._description_string(),
            "--background-color",
            str(settings["slideshow.background_color"]),
            "--font-color",
            str(settings["slideshow.font_color"]),
            "--font-size",
            str(settings["slideshow.font_size"]),
            "--dot-color",
            str(settings["slideshow.marker_color"]),
            "--dot-size",
            str(settings["slideshow.marker_radius"]),
            "--arrow-length",
            str(settings["slideshow.arrow_scale"]),
            "--gpx-overlay",
            str(settings["trackmaps.gpx_overlay"]),
            "--media-overlay",
            str(settings["trackmaps.media_overlay"]),
            "--route-color",
            str(settings["trackmaps.route_color"]),
            "--route-width",
            str(settings["trackmaps.route_width"]),
            "--endpoint-color",
            str(settings["trackmaps.endpoint_color"]),
            "--endpoint-size",
            str(settings["trackmaps.endpoint_size"]),
            "--media-point-color",
            str(settings["trackmaps.media_point_color"]),
            "--media-point-size",
            str(2.0 * float(settings["slideshow.marker_radius"])),
            "--dynamic-map-header",
            "on" if settings["trackmaps.dynamic_header"] else "off",
            "--map-header-color",
            str(settings["trackmaps.title_color"]),
            "--map-header-font-factor",
            str(settings["trackmaps.font_factor"]),
            "--track-title",
            str(settings["trackmaps.track_title"]),
            "--clock",
            "on" if settings["slideshow.clock"] else "off",
            "--placenames",
            "on" if settings["slideshow.place_names"] else "off",
            "--collage-size-range",
            str(settings["slideshow.collage_size_range"]),
            "--collage-max-images",
            str(settings["slideshow.collage_max_images"]),
            "--time-lapse-duration",
            str(settings["timelapse.stage_duration_seconds"]),
            "--time-lapse-media-min-fraction",
            str(settings["timelapse.media_min_fraction"]),
            "--time-lapse-marker",
            str(settings["timelapse.marker_style"]),
            "--audio-crossfade-seconds",
            str(settings["audio.crossfade_seconds"]),
        ]
        if self.title_image is not None and Path(self.title_image).is_file():
            args.extend(
                [
                    "--adventure-title-image",
                    str(Path(self.title_image).resolve(strict=False)),
                ]
            )
        if settings.get("audio.enabled", False) and self.music_source is not None:
            args.extend(["--music", str(Path(self.music_source).resolve(strict=False))])
        if settings.get("audio.enabled", False):
            playlist_path = self.music_playlist
            if playlist_path is None or not Path(playlist_path).is_file():
                playlist_path = self._preferred_music_playlist(self.music_source)
            if playlist_path is not None and Path(playlist_path).is_file():
                args.extend(["--music-playlist", str(Path(playlist_path).resolve(strict=False))])
        if not settings["timelapse.overview_as_media"]:
            args.append("--time-lapse-overview-fullscreen")
        if settings["slideshow.track_map_before_media"]:
            args.append("--track-map-before-media")
        window_mode = settings["slideshow.window_mode"]
        if settings["slideshow.join_windows"]:
            args.extend(["--mapwindow", "--join-windows"])
        elif window_mode == "multiple":
            args.append("--mapwindow")
        elif window_mode == "single":
            args.append("--no-mapwindow")
        if settings["slideshow.display_swap"]:
            args.append("--switch-display")
        if settings["slideshow.repeat"]:
            args.append("--repeat")
        if settings["slideshow.manual_start"]:
            args.append("--keypressed")
        fullscreen_mode = settings["slideshow.fullscreen"]
        if fullscreen_mode == "on":
            args.append("--fullscreen")
        elif fullscreen_mode == "off":
            args.append("--no-fullscreen")
        if state_file is not None:
            args.extend(["--state-file", str(state_file)])
        if isinstance(resume_position, dict):
            args.extend(["--resume-index", str(int(resume_position["playlist_index"]))])
            progress = resume_position.get("time_lapse_progress")
            if isinstance(progress, (int, float)):
                args.extend(["--resume-progress", str(max(0.0, min(1.0, float(progress))))])
            media_index = resume_position.get("media_index")
            if isinstance(media_index, int) and media_index >= 0:
                args.extend(["--resume-media-index", str(media_index)])
            phase = resume_position.get("phase")
            if isinstance(phase, str) and phase:
                args.extend(["--resume-phase", phase])
        if getattr(sys, "frozen", False):
            executable = self._bundled_slideshow_executable()
            if executable is None:
                raise RuntimeError("Bundled GPSTrackShow executable was not found.")
            return [str(executable), *args]
        return [sys.executable, str(self.base_dir / "GPSTrackShow.py"), *args]

    def _bundled_slideshow_executable(self):
        candidates = []
        executable_path = Path(sys.executable).resolve()
        candidates.append(executable_path.parent / "GPSTrackShow")
        bundle_contents = executable_path.parent.parent
        candidates.append(bundle_contents / "Resources" / "GPSTrackShow" / "GPSTrackShow")
        candidates.append(bundle_contents / "Frameworks" / "GPSTrackShow" / "GPSTrackShow")
        candidates.append(bundle_contents / "Resources" / "GPSTrackShow")
        candidates.append(bundle_contents / "Frameworks" / "GPSTrackShow")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "GPSTrackShow" / "GPSTrackShow")
            candidates.append(Path(meipass) / "GPSTrackShow")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @objc.IBAction
    def importMediaFiles_(self, _sender):
        self._begin_media_import(prepare_existing_on_cancel=False)

    def _begin_media_import(self, *, prepare_existing_on_cancel):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        if self.media_import_panel is not None:
            self.media_import_panel.makeKeyAndOrderFront_(None)
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(True)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.last_picture_import_directory or project_dir)))
        self.media_import_panel = panel
        self.media_import_prepare_existing_on_cancel = bool(prepare_existing_on_cancel)
        self.workflow_assistant_bubble.setHidden_(True)

        def completion_handler(response):
            self.mediaImportPanelFinished_(int(response))

        self.media_import_completion_handler = completion_handler
        panel.beginSheetModalForWindow_completionHandler_(
            self.window,
            completion_handler,
        )

    def mediaImportPanelFinished_(self, response):
        panel = self.media_import_panel
        prepare_existing = self.media_import_prepare_existing_on_cancel
        self.media_import_panel = None
        self.media_import_completion_handler = None
        self.media_import_prepare_existing_on_cancel = False
        if panel is not None and int(response) == int(NSModalResponseOK):
            urls = panel.URLs()
            paths = [
                Path(str(url.path())).resolve()
                for url in (urls or [])
                if url is not None
            ]
            if paths:
                self.last_picture_import_directory = paths[0].parent if paths[0].is_file() else paths[0]
                self._import_media_files(paths, images_only=False, force_jpeg=False)
                return
        self.set_status("Media import cancelled.")
        if prepare_existing:
            self._start_automatic_media_metadata()
        else:
            self.refresh_section_status_indicators()

    def _plot_viewer_help_text(self):
        return (
            "Left or Up: previous image\n"
            "Right or Down: next image\n"
            "h: toggle this help\n"
            "q: hide the plot window"
        )

    def _ensure_plot_viewer(self, title):
        if self.plot_viewer_window is not None:
            self.plot_viewer_window.setTitle_(title)
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(160.0, 100.0, 1280.0, 860.0),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(title)
        window.setContentAspectRatio_(NSMakeSize(1920.0, 1080.0 + PLOT_VIEWER_CAPTION_HEIGHT))
        content = GPSTrackShowGUIPlotViewerView.alloc().initWithController_(self)
        content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        window.setContentView_(content)

        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0.0, 32.0, 1280.0, 828.0))
        image_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        content.addSubview_(image_view)

        caption = self._make_status_label("")
        caption.setFrame_(NSMakeRect(0.0, 0.0, 1280.0, 32.0))
        caption.setAutoresizingMask_(NSViewWidthSizable)
        content.addSubview_(caption)

        help_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(24.0, 24.0, 360.0, 120.0))
        help_scroll.setHasVerticalScroller_(False)
        help_scroll.setHasHorizontalScroller_(False)
        help_scroll.setBorderType_(1)
        help_scroll.setAutoresizingMask_(0)
        help_text = NSTextView.alloc().initWithFrame_(help_scroll.bounds())
        help_text.setEditable_(False)
        help_text.setSelectable_(False)
        help_text.setRichText_(False)
        help_text.setDrawsBackground_(True)
        help_text.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.72))
        help_text.setTextColor_(NSColor.whiteColor())
        help_text.setFont_(NSFont.systemFontOfSize_(14.0))
        help_text.setString_(self._plot_viewer_help_text())
        help_scroll.setDocumentView_(help_text)
        help_scroll.setHidden_(True)
        content.addSubview_(help_scroll)

        self.plot_viewer_window = window
        self.plot_viewer_view = content
        self.plot_viewer_image_view = image_view
        self.plot_viewer_caption = caption
        self.plot_viewer_help_view = help_scroll
        self.plot_viewer_delegate = GPSTrackShowGUIPlotViewerWindowDelegate.alloc().initWithController_(self)
        self.plot_viewer_window.setDelegate_(self.plot_viewer_delegate)

    def _release_plot_viewer_references(self, closed_window=None):
        if closed_window is not None and self.plot_viewer_window is not None and closed_window != self.plot_viewer_window:
            return
        self.plot_viewer_window = None
        self.plot_viewer_view = None
        self.plot_viewer_image_view = None
        self.plot_viewer_caption = None
        self.plot_viewer_help_view = None
        self.plot_viewer_paths = []
        self.plot_viewer_index = 0
        self.plot_viewer_delegate = None
        self.plot_viewer_closing = False

    def plotViewerWindowWillClose_(self, window):
        self._release_plot_viewer_references(window)

    def requestHidePlotViewer_(self, _sender):
        self.hide_plot_viewer()

    def requestClosePlotViewer_(self, _sender):
        self.close_plot_viewer()

    def hide_plot_viewer(self):
        window = self.plot_viewer_window
        if window is None:
            return
        if self.plot_viewer_help_view is not None:
            self.plot_viewer_help_view.setHidden_(True)
        window.orderOut_(None)

    def close_plot_viewer(self):
        window = self.plot_viewer_window
        if window is None:
            self._release_plot_viewer_references()
            return
        if self.plot_viewer_closing:
            return
        self.plot_viewer_closing = True
        window.performClose_(None)

    def _display_plot_viewer_index(self):
        if self.plot_viewer_window is None or self.plot_viewer_image_view is None or not self.plot_viewer_paths:
            return
        self.plot_viewer_index = max(0, min(self.plot_viewer_index, len(self.plot_viewer_paths) - 1))
        image_path = self.plot_viewer_paths[self.plot_viewer_index]
        image = NSImage.alloc().initWithContentsOfFile_(str(image_path))
        metadata = self._plot_metadata_for_image(image_path)
        if image is not None and isinstance(metadata, dict) and metadata.get("background_only"):
            from GPSTrackShow import draw_dynamic_map_overlay, parse_color_option

            overlay_config = argparse.Namespace(
                gpx_overlay_mode=str(self.parameters["trackmaps.gpx_overlay"]),
                media_overlay_mode=str(self.parameters["trackmaps.media_overlay"]),
                route_color=parse_color_option(self.parameters["trackmaps.route_color"], "route color"),
                route_width=float(self.parameters["trackmaps.route_width"]),
                endpoint_color=parse_color_option(self.parameters["trackmaps.endpoint_color"], "endpoint color"),
                endpoint_size=float(self.parameters["trackmaps.endpoint_size"]),
                media_point_color=parse_color_option(self.parameters["trackmaps.media_point_color"], "media point color"),
                media_point_size=2.0 * float(self.parameters["slideshow.marker_radius"]),
                dynamic_map_header=bool(self.parameters["trackmaps.dynamic_header"]),
                font_size=max(8, int(14 * float(self.parameters["trackmaps.font_factor"]))),
                map_header_color=parse_color_option(self.parameters["trackmaps.title_color"], "map header color"),
                map_header_font_factor=float(self.parameters["trackmaps.font_factor"]),
                track_title_mode=str(self.parameters["trackmaps.track_title"]),
            )
            image = draw_dynamic_map_overlay(image, metadata, overlay_config)
        if image is not None:
            image_size = image.size()
            if image_size.width > 0 and image_size.height > 0:
                self.plot_viewer_window.setContentAspectRatio_(
                    NSMakeSize(image_size.width, image_size.height + PLOT_VIEWER_CAPTION_HEIGHT)
                )
        self.plot_viewer_image_view.setImage_(image)
        if self.plot_viewer_caption is not None:
            self.plot_viewer_caption.setStringValue_(
                f"{self.plot_viewer_index + 1}/{len(self.plot_viewer_paths)}  {image_path.name}"
            )
        self.plot_viewer_window.makeKeyAndOrderFront_(None)
        self.plot_viewer_window.makeFirstResponder_(self.plot_viewer_view)
        NSApp().activateIgnoringOtherApps_(True)

    def show_plot_viewer(self, image_paths, title, start_index=0):
        paths = [Path(path).resolve(strict=False) for path in image_paths if Path(path).exists()]
        if not paths:
            show_alert("No plot images available.")
            return
        self._ensure_plot_viewer(title)
        self.plot_viewer_window.setTitle_(title)
        self.plot_viewer_paths = paths
        self.plot_viewer_index = max(0, min(int(start_index), len(paths) - 1))
        if self.plot_viewer_help_view is not None:
            self.plot_viewer_help_view.setHidden_(True)
        self._display_plot_viewer_index()

    def toggle_plot_help(self):
        if self.plot_viewer_help_view is None:
            return
        self.plot_viewer_help_view.setHidden_(not bool(self.plot_viewer_help_view.isHidden()))

    def show_previous_plot(self):
        if not self.plot_viewer_paths:
            return
        self.plot_viewer_index = (self.plot_viewer_index - 1) % len(self.plot_viewer_paths)
        self._display_plot_viewer_index()

    def show_next_plot(self):
        if not self.plot_viewer_paths:
            return
        self.plot_viewer_index = (self.plot_viewer_index + 1) % len(self.plot_viewer_paths)
        self._display_plot_viewer_index()

    def _validate_plot_request(self):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return None
        project_name = self._current_project_name()
        if not project_name:
            show_alert("Please set the project name first.")
            return None
        gpx_path = self._current_single_gpx_path()
        if gpx_path is None:
            show_alert("Please select exactly one GPX file first.")
            return None
        if not gpx_path.exists():
            show_alert("The selected GPX file does not exist.", str(gpx_path))
            return None
        trackimages_dir = self._track_images_dir()
        if trackimages_dir is None:
            show_alert("Please choose a project directory first.")
            return None
        trackimages_dir.mkdir(parents=True, exist_ok=True)
        return project_name, gpx_path, trackimages_dir

    def _uses_media_only_maps(self):
        route_source = str(self.parameters.get("trackmaps.route_source", "automatic"))
        gpx_path = self._current_single_gpx_path()
        return route_source == "media" or (
            route_source == "automatic"
            and (gpx_path is None or not gpx_path.is_file())
        )

    def _media_only_plot_context(self):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        control_path = self._control_file_path()
        output_dir = self._track_images_dir()
        if project_dir is None or control_path is None or output_dir is None:
            return None
        plan = self._project_map_plan()
        if plan is None:
            return None
        specs = plan.media_map_specs
        points = plan.overview_points
        if not specs or not points:
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        overview_name = plan.overview_name or f"{self.track_map_base or self._current_project_name()}.png"
        items = []
        for number, spec in enumerate(specs, start=1):
            stale_variants = []
            for layout in ("standard", "time-lapse"):
                output_path = output_dir / media_map_output_filename(spec["filename"], layout)
                metadata = self._plot_metadata_for_image(output_path) if output_path.is_file() else None
                if (
                    not output_path.is_file()
                    or not self._metadata_parameters_are_current(
                        metadata,
                        self._track_map_parameter_signature(layout),
                        layout,
                    )
                    or not isinstance(metadata, dict)
                    or not media_map_metadata_matches_coordinates(
                        metadata,
                        spec["date"],
                        spec["coordinates"],
                    )
                ):
                    stale_variants.append(layout)
            items.append(
                {
                    **spec,
                    "selection_number": number,
                    "output_image": str(output_dir / spec["filename"]),
                    "needs_update": bool(stale_variants),
                    "stale_variants": stale_variants,
                }
            )
        overview_path = output_dir / overview_name
        overview_metadata = self._plot_metadata_for_image(overview_path) if overview_path.is_file() else None
        overview_fingerprint = media_overview_fingerprint(
            [(point["lat"], point["lon"]) for point in points]
        )
        overview_needs_update = (
            not overview_path.is_file()
            or not isinstance(overview_metadata, dict)
            or str(overview_metadata.get("media_fingerprint", "")) != overview_fingerprint
            or not self._metadata_parameters_are_current(
                overview_metadata,
                self._track_map_parameter_signature("overview"),
                "overview",
            )
        )
        return {
            "project_dir": project_dir,
            "control_path": control_path,
            "output_dir": str(output_dir),
            "overview_path": str(overview_path),
            "overview_needs_update": overview_needs_update,
            "media_points": points,
            "media_map_items": items,
            "map_plan": plan,
            "tracks": [],
        }

    def _start_media_only_plot_creation(self, context, selected_numbers):
        selected = set(range(0, len(context["media_map_items"]) + 1)) if selected_numbers == "all" else {
            int(number) for number in selected_numbers
        }
        options = self._media_map_options()
        options.update(
            {
                "map_layouts": ("standard", "time-lapse"),
                "only_missing_or_stale": False,
                "adventure_render_parameters_by_layout": {
                    "standard": self._track_map_parameter_signature("standard"),
                    "time-lapse": self._track_map_parameter_signature("time-lapse"),
                },
            }
        )
        project_name = self._current_project_name()
        output_dir = Path(context["output_dir"])
        control_path = Path(context["control_path"])
        self._cleanup_obsolete_media_map_files(
            context.get("map_plan"),
            output_dir,
        )
        cancel_event = threading.Event()
        self.plot_cancel_event = cancel_event
        self.geolocations_cancel_event = cancel_event
        self.geolocations_mode = "manual_maps"
        self._prepare_geolocations_window("Map Generation")
        output_writer = GeoLocationsOutputWriter(self)
        self.plot_creation_image_paths = []
        self.gpx_cancel_plots_button.setHidden_(False)
        self.gpx_cancel_plots_button.setEnabled_(True)
        output_total = int(0 in selected) + 2 * len([number for number in selected if number != 0])
        self.set_progress(0.0, max(1, output_total))

        def run_task():
            created = 0
            try:
                if 0 in selected:
                    points = context["media_points"]
                    output_writer.write(
                        f"Creating media Tour Overview: {Path(context['overview_path']).name}\n"
                    )
                    render_media_overview_map(
                        [(point["lat"], point["lon"]) for point in points],
                        context["overview_path"],
                        media_points=points,
                        header=project_name,
                        zoom_level=int(options["zoom_level"]),
                        image_size=tuple(options["image_size"]),
                        font_factor=float(options["font_factor"]),
                        background_color=str(options["background_color"]),
                        title_color=str(options["title_color"]),
                        map_provider=str(options["map_provider"]),
                        custom_map_url=str(options["custom_map_url"]),
                        custom_map_attribution=str(options["custom_map_attribution"]),
                        maximum_map_zoom=int(options["maximum_map_zoom"]),
                        map_request_timeout_seconds=float(options["map_request_timeout_seconds"]),
                        adventure_render_parameters=self._track_map_parameter_signature("overview"),
                    )
                    created += 1
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "plotImageCreated:", (str(context["overview_path"]), "Media overview"), True
                    )
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setProgressFromWorker:", (float(created), float(max(1, output_total))), True
                    )
                for item in context["media_map_items"]:
                    if cancel_event.is_set():
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "plotCreationFinished:", ("cancelled", created, str(output_dir)), False
                        )
                        return
                    if int(item["selection_number"]) not in selected:
                        continue
                    rendered = render_media_map_specs(
                        [item],
                        control_path,
                        options,
                        output_writer=output_writer,
                    )
                    created += len(rendered)
                    for path in rendered:
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "plotImageCreated:", (str(path), f"Media stage {item['date']}"), True
                        )
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setProgressFromWorker:", (float(created), float(max(1, output_total))), True
                    )
            except GeoLocationsCancelled:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "plotCreationFinished:", ("cancelled", created, str(output_dir)), False
                )
                return
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "plotCreationFinished:", (f"error:{exc}", created, str(output_dir)), False
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "plotCreationFinished:", ("success", created, str(output_dir)), False
            )

        self.plot_creation_thread = threading.Thread(target=run_task, name="make-media-maps", daemon=True)
        self.geolocations_thread = self.plot_creation_thread
        self.plot_creation_thread.start()
        self.refresh_workflow_assistant()

    def _track_plot_selection_rows(self, plot_context, update_numbers=None):
        update_numbers = set(update_numbers or [])
        overview_name = "Overview plot"
        if 0 in update_numbers:
            overview_name = f"* {overview_name}"
        rows = [{"nr": "0", "name": overview_name, "date": ""}]
        for track in plot_context.get("tracks", []):
            try:
                track_number = int(track.get("table_number", 0))
            except (TypeError, ValueError):
                track_number = 0
            track_time = track.get("time")
            if track_time is None:
                date_text = ""
            else:
                try:
                    date_text = local_datetime_text(track_time)
                except Exception:
                    date_text = str(track_time)
            track_name = str(track.get("name", ""))
            if track_number in update_numbers:
                track_name = f"* {track_name}"
            rows.append({
                "nr": str(track.get("table_number", "")),
                "name": track_name,
                "date": date_text,
            })
        for item in plot_context.get("media_map_items", []):
            number = int(item["selection_number"])
            name = "Media locations"
            if number in update_numbers:
                name = f"* {name}"
            media_day = item.get("date")
            date_text = media_day.strftime("%d.%m.%Y") if hasattr(media_day, "strftime") else str(media_day or "")
            rows.append({"nr": str(number), "name": name, "date": date_text})
        return rows

    def _parse_plot_selection_text(self, range_text):
        text = str(range_text or "").strip()
        if not text:
            return []
        if text.lower() == "all":
            return [int(row["nr"]) for row in self.plot_selection_rows]
        numbers = set()
        max_number = max(int(row["nr"]) for row in self.plot_selection_rows)
        for part in [item.strip() for item in text.split(",")]:
            if not part:
                raise ValueError("Range contains an empty item.")
            if re.fullmatch(r"\d+", part):
                start = end = int(part)
            else:
                match = re.fullmatch(r"(\d+)-(\d+)", part)
                if not match:
                    raise ValueError("Use numbers, commas, and ranges like 0,1,3-6.")
                start = int(match.group(1))
                end = int(match.group(2))
                if start > end:
                    raise ValueError("Ranges must use ascending order.")
            for number in range(start, end + 1):
                if 0 <= number <= max_number:
                    numbers.add(number)
        return sorted(numbers)

    def _format_plot_selection_numbers(self, numbers):
        ordered = sorted({int(number) for number in numbers})
        if not ordered:
            return ""
        ranges = []
        start = previous = ordered[0]
        for number in ordered[1:]:
            if number == previous + 1:
                previous = number
                continue
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = number
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        return ",".join(ranges)

    def _selected_plot_table_numbers(self):
        if self.plot_selection_table is None:
            return []
        selected = self.plot_selection_table.selectedRowIndexes()
        numbers = []
        for index, row in enumerate(self.plot_selection_rows):
            if selected.containsIndex_(index):
                try:
                    numbers.append(int(row["nr"]))
                except (TypeError, ValueError):
                    pass
        return numbers

    def plotSelectionTableSelectionDidChange_(self, notification):
        if self.plot_selection_table is None or notification.object() != self.plot_selection_table:
            return
        numbers = self._selected_plot_table_numbers()
        if not numbers:
            return
        if self.plot_selection_all_checkbox is not None:
            self.plot_selection_all_checkbox.setState_(0)
        if self.plot_selection_range_field is not None:
            self.plot_selection_range_field.setEnabled_(True)
            self.plot_selection_range_field.setStringValue_(self._format_plot_selection_numbers(numbers))

    def _cleanup_plot_selection_window(self):
        if self.plot_selection_window is not None:
            self.plot_selection_window.setDelegate_(None)
            self.plot_selection_window.orderOut_(None)
        self.plot_selection_window = None
        self.plot_selection_table = None
        self.plot_selection_data_source = None
        self.plot_selection_range_field = None
        self.plot_selection_all_checkbox = None

    def _select_plot_table_numbers(self, numbers):
        if self.plot_selection_table is None:
            return
        wanted = {int(number) for number in numbers}
        clean_indexes = []
        for index, row in enumerate(self.plot_selection_rows):
            try:
                number = int(row["nr"])
            except (TypeError, ValueError):
                continue
            if number in wanted:
                clean_indexes.append(index)
        if not clean_indexes:
            return
        selection = NSIndexSet.indexSetWithIndexesInRange_((clean_indexes[0], 1))
        mutable_selection = selection.mutableCopy()
        for index in clean_indexes[1:]:
            mutable_selection.addIndex_(index)
        self.plot_selection_table.selectRowIndexes_byExtendingSelection_(mutable_selection, False)

    def choose_plot_tracks(self, plot_context, default_numbers=None, default_all=True, action_title="Generate"):
        self._cleanup_plot_selection_window()
        self.plot_selection_result = None
        default_numbers = sorted({int(number) for number in (default_numbers or [])})
        self.plot_selection_rows = self._track_plot_selection_rows(plot_context, default_numbers)
        if not self.plot_selection_rows:
            show_alert("No tracks are available for plotting.")
            return None

        visible_rows = min(10, max(3, len(self.plot_selection_rows)))
        table_height = visible_rows * 24.0 + 26.0
        window_height = table_height + 186.0
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(260.0, 220.0, 720.0, window_height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("Generate and Update Maps")
        content = window.contentView()

        all_checkbox = self._make_checkbox("Generate all maps", "plotSelectionAllChanged:")
        all_checkbox.setState_(0 if default_numbers or not default_all else NSControlStateValueOn)
        all_checkbox.setFrame_(NSMakeRect(16.0, window_height - 42.0, 220.0, FIELD_HEIGHT))
        content.addSubview_(all_checkbox)

        range_label = self._make_label("Range")
        range_label.setFrame_(NSMakeRect(250.0, window_height - 36.0, 48.0, 18.0))
        content.addSubview_(range_label)
        range_field = self._make_text_field("e.g. 0,1,2,3-6,8")
        range_field.setFrame_(NSMakeRect(304.0, window_height - 42.0, 220.0, FIELD_HEIGHT))
        range_field.setEnabled_(bool(default_numbers) or not default_all)
        if default_numbers:
            range_field.setStringValue_(self._format_plot_selection_numbers(default_numbers))
        content.addSubview_(range_field)

        hint = self._make_label("Rows marked with * need update.", size=12.0)
        hint.setFrame_(NSMakeRect(16.0, window_height - 70.0, 420.0, 22.0))
        content.addSubview_(hint)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16.0, 62.0, 688.0, table_height))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(True)
        table = NSTableView.alloc().initWithFrame_(scroll.bounds())
        table.setAllowsMultipleSelection_(True)
        table.setUsesAlternatingRowBackgroundColors_(True)
        columns = [("nr", "Nr", 54.0), ("name", "Track name", 430.0), ("date", "Date", 180.0)]
        for identifier, title, col_width in columns:
            column = NSTableColumn.alloc().initWithIdentifier_(nsstring(identifier))
            column.setWidth_(col_width)
            column.headerCell().setStringValue_(title)
            table.addTableColumn_(column)
        data_source = GPSTrackShowGUITableDataSource.alloc().init()
        data_source.setRows_columns_(self.plot_selection_rows, [item[0] for item in columns])
        data_source.setController_(self)
        table.setDataSource_(data_source)
        table.setDelegate_(data_source)
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)

        create_button = self._make_button(action_title, "plotSelectionCreate:")
        cancel_button = self._make_button("Cancel", "plotSelectionCancel:")
        create_button.setFrame_(NSMakeRect(414.0, 18.0, 170.0, FIELD_HEIGHT))
        cancel_button.setFrame_(NSMakeRect(604.0, 18.0, 100.0, FIELD_HEIGHT))
        content.addSubview_(create_button)
        content.addSubview_(cancel_button)

        self.plot_selection_window = window
        self.plot_selection_table = table
        self.plot_selection_data_source = data_source
        self.plot_selection_range_field = range_field
        self.plot_selection_all_checkbox = all_checkbox

        window.makeKeyAndOrderFront_(None)
        if default_numbers:
            self._select_plot_table_numbers(default_numbers)
        NSApp().runModalForWindow_(window)
        result = self.plot_selection_result
        self._cleanup_plot_selection_window()
        return result

    @objc.IBAction
    def plotSelectionAllChanged_(self, _sender):
        if self.plot_selection_range_field is not None and self.plot_selection_all_checkbox is not None:
            use_all = self.plot_selection_all_checkbox.state() == NSControlStateValueOn
            self.plot_selection_range_field.setEnabled_(not use_all)
            if use_all:
                self.plot_selection_range_field.setStringValue_("")

    @objc.IBAction
    def plotSelectionCreate_(self, _sender):
        try:
            if self.plot_selection_all_checkbox is not None and self.plot_selection_all_checkbox.state() == NSControlStateValueOn:
                self.plot_selection_result = "all"
            else:
                range_text = str(self.plot_selection_range_field.stringValue()).strip() if self.plot_selection_range_field is not None else ""
                if range_text:
                    self.plot_selection_result = self._parse_plot_selection_text(range_text)
                else:
                    numbers = self._selected_plot_table_numbers()
                    if not numbers:
                        show_alert("Select tracks or enter a range.", "Use a range like 1,2,3-6,8 or select table rows.")
                        return
                    self.plot_selection_result = numbers
        except Exception as exc:
            show_alert("Invalid track range.", str(exc))
            return
        NSApp().stopModal()

    @objc.IBAction
    def plotSelectionCancel_(self, _sender):
        self.plot_selection_result = None
        NSApp().stopModal()

    def _track_map_image_size(self, values=None):
        source = self.parameters if values is None else values
        width_text, height_text = str(source["trackmaps.image_size"]).lower().split("x", 1)
        return int(width_text), int(height_text)

    def _track_summary_parameter_signature(self, values=None):
        source = self.parameters if values is None else values
        keys = (
            "trackmaps.ordering",
            "trackmaps.remove_name_prefix",
            "gpx.fallback_walking_speed_kmh",
            "gpx.horizontal_smoothing_distance_m",
            "gpx.minimum_point_spacing_m",
            "gpx.maximum_accuracy_m",
            "gpx.maximum_vertical_accuracy_m",
            "gpx.maximum_hdop",
            "gpx.maximum_vdop",
            "gpx.elevation_smoothing_distance_m",
        )
        return parameter_subset(source, keys)

    def _track_map_parameter_signature(self, variant, values=None):
        source = self.parameters if values is None else values
        keys = tuple(map_affecting_parameter_keys())
        signature = parameter_subset(source, keys)
        if variant != "time-lapse":
            signature.pop("trackmaps.edge_margin_fraction", None)
        signature["trackmaps.rendered_layout"] = str(variant)
        signature["trackmaps.map_content_version"] = MAP_CONTENT_VERSION
        return signature

    def _metadata_parameters_are_current(self, metadata, expected, variant):
        if not isinstance(metadata, dict):
            return False
        if variant != "overview":
            extent = metadata.get("extent_mercator")
            if isinstance(extent, dict):
                try:
                    short_dimension = min(
                        float(extent["max_x"]) - float(extent["min_x"]),
                        float(extent["max_y"]) - float(extent["min_y"]),
                    )
                except (KeyError, TypeError, ValueError):
                    short_dimension = MINIMUM_MAP_SHORT_DIMENSION_M
                if short_dimension + 1.0 < MINIMUM_MAP_SHORT_DIMENSION_M:
                    return False
                try:
                    saved_minimum = float(
                        metadata.get(
                            "minimum_short_dimension_m",
                            MINIMUM_MAP_SHORT_DIMENSION_M,
                        )
                    )
                except (TypeError, ValueError):
                    return False
                if (
                    not math.isclose(
                        saved_minimum,
                        MINIMUM_MAP_SHORT_DIMENSION_M,
                        abs_tol=1.0,
                    )
                    and short_dimension
                    <= max(saved_minimum, MINIMUM_MAP_SHORT_DIMENSION_M) + 1.0
                ):
                    return False
        saved = metadata.get("adventure_render_parameters")
        if isinstance(saved, dict):
            return saved == expected
        return False

    def _plot_common_options(
        self,
        project_name,
        trackimages_dir,
        output_base=None,
        *,
        parameter_values=None,
        use_track_order=None,
    ):
        values = self.parameters if parameter_values is None else parameter_values
        if use_track_order is None:
            use_track_order = self._use_track_order()
        labels = "none" if values["trackmaps.overview_labels"] == "none" else None
        maximum_zoom = int(values["maps.maximum_zoom"])
        resolved_output_base = output_base
        if not resolved_output_base and parameter_values is None:
            resolved_output_base = self.track_map_base
        return {
            "print_labels": labels,
            "line_width": values["trackmaps.route_width"],
            "line_color": values["trackmaps.route_color"],
            "dot_color": values["trackmaps.endpoint_color"],
            "dot_size": values["trackmaps.endpoint_size"],
            "background_color": values["trackmaps.background_color"],
            "title_color": values["trackmaps.title_color"],
            "size": self._track_map_image_size(values),
            "fontsize": values["trackmaps.font_factor"],
            "zoom": min(int(values["trackmaps.zoom"]), maximum_zoom),
            "header": project_name,
            "output_dir": str(trackimages_dir),
            "output_base": resolved_output_base or project_name,
            "nojson": False,
            "verbose": False,
            "sort_original": use_track_order,
            "sort_date": not use_track_order,
            "remove_prefix": values["trackmaps.remove_name_prefix"],
            "gpx_threshold_distance": values["gpx.minimum_point_spacing_m"],
            "gpx_threshold_accuracy": values["gpx.maximum_accuracy_m"],
            "gpx_horizontal_smoothing_distance": values["gpx.horizontal_smoothing_distance_m"],
            "gpx_elevation_smoothing_distance": values["gpx.elevation_smoothing_distance_m"],
            "gpx_maximum_vertical_accuracy": values["gpx.maximum_vertical_accuracy_m"],
            "gpx_maximum_hdop": values["gpx.maximum_hdop"],
            "gpx_maximum_vdop": values["gpx.maximum_vdop"],
            "fallback_walking_speed_kmh": values["gpx.fallback_walking_speed_kmh"],
            "map_layout": "standard",
            "track_edge_margin_fraction": values["trackmaps.edge_margin_fraction"],
            "map_provider": values["maps.provider"],
            "custom_map_url": values["maps.custom_url"],
            "custom_map_attribution": values["maps.custom_attribution"],
            "maximum_map_zoom": maximum_zoom,
            "map_request_timeout_seconds": values["maps.request_timeout_seconds"],
            "adventure_render_parameters": self._track_map_parameter_signature(
                "standard", values
            ),
            "adventure_overview_render_parameters": self._track_map_parameter_signature(
                "overview", values
            ),
            "adventure_processing_parameters": self._track_summary_parameter_signature(values),
        }

    def _existing_expected_track_map_count(self, selection_context):
        count = 1 if Path(selection_context["overview_path"]).exists() else 0
        for item in selection_context.get("track_plot_paths", []):
            if self._existing_track_plot_path(item["output_image"]) is not None:
                count += 1
        count += sum(
            1
            for item in selection_context.get("media_map_items", [])
            if Path(item["output_image"]).exists()
        )
        return count

    def _start_plot_creation(self, project_name, gpx_path, trackimages_dir, common_options, selection_context, selected_numbers):
        removed_obsolete = self._cleanup_obsolete_track_map_files(selection_context)
        removed_obsolete.extend(
            self._cleanup_obsolete_media_map_files(
                selection_context.get("project_map_plan"),
                trackimages_dir,
            )
        )
        media_items_by_number = {
            int(item["selection_number"]): item
            for item in selection_context.get("media_map_items", [])
        }
        if selected_numbers == "all":
            include_overview = True
            track_numbers = [item["track_number"] for item in selection_context.get("track_plot_paths", [])]
            selected_media_items = list(media_items_by_number.values())
            plot_tracks_option = "all"
        else:
            selected_numbers = [int(number) for number in selected_numbers]
            include_overview = 0 in selected_numbers
            selected_media_items = [
                media_items_by_number[number]
                for number in selected_numbers
                if number in media_items_by_number
            ]
            track_numbers = [
                number
                for number in selected_numbers
                if number > 0 and number not in media_items_by_number
            ]
            if not include_overview and not track_numbers and not selected_media_items:
                show_alert("No images selected.")
                return
            plot_tracks_option = ",".join(str(number) for number in track_numbers) if track_numbers else None
        media_map_options = self._media_map_options()
        media_map_options.update(
            {
                "map_layouts": ("standard", "time-lapse"),
                "only_missing_or_stale": False,
                "adventure_render_parameters_by_layout": {
                    "standard": self._track_map_parameter_signature("standard"),
                    "time-lapse": self._track_map_parameter_signature("time-lapse"),
                },
            }
        )
        self.plot_cancel_event = threading.Event()
        self.geolocations_cancel_event = self.plot_cancel_event
        self.geolocations_mode = "manual_maps"
        self._prepare_geolocations_window("Map Generation")
        output_writer = GeoLocationsOutputWriter(self)
        selection_context["args"].cancel_event = self.plot_cancel_event
        self.plot_creation_image_paths = []
        self.gpx_cancel_plots_button.setHidden_(False)
        cleanup_text = f"Removed {len(removed_obsolete)} obsolete track-map file(s). " if removed_obsolete else ""
        action_text = "Generating overview..." if include_overview else "Generating maps..."
        self.set_status(f"{cleanup_text}{action_text}")
        total_outputs = int(include_overview) + 2 * (len(track_numbers) + len(selected_media_items))
        self.set_progress(0.0, float(max(total_outputs, 1)))

        def run_task():
            created_count = 0
            try:
                def paired_progress(current, _total, name, layout):
                    label = (
                        "Generating overview..."
                        if layout == "overview"
                        else f"Generating {layout} map: {name}"
                    )
                    output_writer.write(f"{label}\n")
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setStatusFromWorker:",
                        label,
                        False,
                    )
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setProgressFromWorker:",
                        (float(current), float(max(total_outputs, 1))),
                        False,
                    )

                paired_result = execute_map_variants_from_context(
                    selection_context,
                    selected_track_numbers=track_numbers,
                    plot_overview=include_overview,
                    map_layouts=("standard", "time-lapse"),
                    render_parameters_by_layout={
                        "standard": self._track_map_parameter_signature("standard"),
                        "time-lapse": self._track_map_parameter_signature("time-lapse"),
                    },
                    progress_callback=paired_progress,
                )
                for path in paired_result.get("created_paths", []):
                    image_path = Path(path).resolve(strict=False)
                    if image_path.exists():
                        created_count += 1
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "plotImageCreated:",
                            (str(image_path), f"Maps: {project_name}"),
                            False,
                        )
                completed = int(include_overview) + 2 * len(track_numbers)
                for media_index, item in enumerate(selected_media_items, start=1):
                    if self.plot_cancel_event is not None and self.plot_cancel_event.is_set():
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "plotCreationFinished:",
                            ("cancelled", created_count, str(trackimages_dir)),
                            False,
                        )
                        return
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setStatusFromWorker:",
                        f"Generating media maps {media_index}/{len(selected_media_items)}...",
                        False,
                    )
                    paths = render_media_map_specs(
                        [item],
                        Path(item["control_file"]),
                        media_map_options,
                        output_writer=output_writer,
                    )
                    for image_path in paths:
                        if image_path.exists():
                            created_count += 1
                            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                                "plotImageCreated:",
                                (str(image_path), f"Plots: {project_name}"),
                                False,
                            )
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "setProgressFromWorker:",
                        (float(completed + 2 * media_index), float(max(total_outputs, 1))),
                        False,
                    )
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "plotCreationFinished:",
                    (f"error: {exc}", created_count, str(trackimages_dir)),
                    False,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "plotCreationFinished:",
                ("success", created_count, str(trackimages_dir)),
                False,
            )

        self.plot_creation_thread = threading.Thread(target=run_task, name="make-plots", daemon=True)
        self.geolocations_thread = self.plot_creation_thread
        self.plot_creation_thread.start()
        self.refresh_workflow_assistant()

    @objc.IBAction
    def makePlots_(self, _sender):
        if self.plot_creation_thread is not None and self.plot_creation_thread.is_alive():
            show_alert("Map generation is already running.")
            return
        if self._uses_media_only_maps():
            context = self._media_only_plot_context()
            if context is None:
                show_alert(
                    "No media stages are available yet.",
                    "Import media and prepare its metadata first.",
                )
                return
            defaults = [
                item["selection_number"]
                for item in context["media_map_items"]
                if item.get("needs_update")
            ]
            if context.get("overview_needs_update"):
                defaults.insert(0, 0)
            selected = self.choose_plot_tracks(
                context,
                default_numbers=defaults,
                default_all=False,
                action_title="Generate and Update",
            )
            if selected is None:
                self.set_status("Map generation cancelled.")
                return
            self._start_media_only_plot_creation(context, selected)
            return
        validated = self._validate_plot_request()
        if validated is None:
            return
        project_name, gpx_path, trackimages_dir = validated
        if not self._confirm_shared_asset_change("track_map_base", self.track_map_base, "generated maps"):
            return
        common_options = self._plot_common_options(project_name, trackimages_dir)
        try:
            selection_context = prepare_with_options(str(gpx_path), plot_tracks="all", **common_options)
            execute_map_variants_from_context(
                selection_context,
                selected_track_numbers=[],
                plot_overview=False,
            )
            self._add_media_maps_to_plot_context(selection_context)
        except (OSError, ValueError, FileNotFoundError, RuntimeError) as exc:
            show_alert("Could not inspect GPX tracks.", str(exc))
            self.set_status("Map generation failed.")
            return
        default_numbers = self._track_map_update_numbers(gpx_path, selection_context)
        selected_numbers = self.choose_plot_tracks(
            selection_context,
            default_numbers=default_numbers,
            default_all=False,
            action_title="Generate and Update",
        )
        if selected_numbers is None:
            self.set_status("Map generation cancelled.")
            return
        self._start_plot_creation(
            project_name,
            gpx_path,
            trackimages_dir,
            common_options,
            selection_context,
            selected_numbers,
        )

    @objc.IBAction
    def updatePlots_(self, _sender):
        """Compatibility action for older nib/action references."""
        self.makePlots_(_sender)

    @objc.IBAction
    def cancelMakePlots_(self, _sender):
        if self.plot_cancel_event is not None:
            self.plot_cancel_event.set()
            self.gpx_cancel_plots_button.setEnabled_(False)
            self.set_status("Cancelling plot creation after the current image...")

    def plotImageCreated_(self, payload):
        path_text, title = payload
        image_path = Path(str(path_text)).resolve(strict=False)
        if image_path not in self.plot_creation_image_paths:
            self.plot_creation_image_paths.append(image_path)
        self.show_plot_viewer(self.plot_creation_image_paths, str(title), start_index=len(self.plot_creation_image_paths) - 1)

    def plotCreationFinished_(self, payload):
        status, created_count, trackimages_dir = payload
        self.gpx_cancel_plots_button.setHidden_(True)
        self.gpx_cancel_plots_button.setEnabled_(True)
        self.plot_cancel_event = None
        self.plot_creation_thread = None
        self.refresh_workflow_assistant()
        self.reset_progress()
        self.refresh_current_gpx_summary()
        self.refresh_control_file_display()
        status = str(status)
        if status == "success":
            message = f"Created overview and {created_count} track/media map(s) in {trackimages_dir}."
            self._finish_geolocations_run("Map generation finished.", message)
            return
        if status == "cancelled":
            message = f"Map generation cancelled after {created_count} track/media map(s)."
            self._finish_geolocations_run("Map generation cancelled.", message)
            return
        if status.startswith("error:"):
            show_alert("Could not create plot images.", status[6:].strip())
            self._finish_geolocations_run(status, "Map generation failed.")

    @objc.IBAction
    def viewPlots_(self, _sender):
        def append_map_pair(paths, output_dir, canonical_name):
            canonical = output_dir / Path(str(canonical_name)).name
            for candidate in (
                canonical,
                output_dir / time_lapse_track_map_name(canonical.name),
            ):
                resolved = candidate.resolve(strict=False)
                if resolved.is_file() and resolved not in paths:
                    paths.append(resolved)

        if self._uses_media_only_maps():
            context = self._media_only_plot_context()
            if context is None:
                show_alert("No media-stage map information is available.")
                return
            paths = []
            overview = Path(context["overview_path"])
            if overview.is_file():
                paths.append(overview.resolve(strict=False))
            output_dir = Path(context["output_dir"])
            plan = context.get("map_plan")
            for section in plan.sections if plan is not None else ():
                for _keyword, filename in section.get("maps", ()):
                    append_map_pair(paths, output_dir, filename)
            if not paths:
                show_alert(
                    "No Media Maps are available.",
                    "Use Generate and Update Maps first.",
                )
                return
            self.show_plot_viewer(paths, self._current_project_name() or "Media Maps")
            return
        validated = self._validate_plot_request()
        if validated is None:
            return
        project_name, gpx_path, _trackimages_dir = validated
        try:
            context = self._plot_context_for_gpx(gpx_path)
        except (OSError, ValueError, FileNotFoundError, RuntimeError) as exc:
            show_alert("Could not inspect plot images.", str(exc))
            return
        if context is None:
            show_alert("No plot information available.")
            return
        image_paths = []
        overview_path = Path(context["overview_path"]).resolve(strict=False)
        if overview_path.exists():
            image_paths.append(overview_path)
        output_dir = Path(context["output_dir"])
        plan = context.get("project_map_plan")
        if plan is not None:
            for section in plan.sections:
                for _keyword, filename in section.get("maps", ()):
                    append_map_pair(image_paths, output_dir, filename)
        else:
            for track in context.get("tracks", []):
                canonical_name = track.get("track_plot_image_filename")
                if canonical_name:
                    append_map_pair(image_paths, output_dir, canonical_name)
        if not image_paths:
            show_alert("No track plot images found.", f"Expected images in:\n{context['output_dir']}")
            return
        self.show_plot_viewer(image_paths, f"Track Plots: {project_name}", start_index=0)
        self.set_status(f"Opened {len(image_paths)} plot image(s).")

    @objc.IBAction
    def editPlots_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        trackimages_dir = self._track_images_dir()
        if trackimages_dir is None:
            show_alert("Please choose a project directory first.")
            return
        trackimages_dir.mkdir(parents=True, exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(trackimages_dir)))
        self.set_status(f"Opened plot directory {trackimages_dir}")

    @objc.IBAction
    def editMediaFiles_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        items = self.project_media_items()
        urls = [NSURL.fileURLWithPath_(str(item["path"])) for item in items if Path(item["path"]).exists()]
        if urls:
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(urls)
            self.set_status(f"Opened Finder with {len(urls)} photos/videos selected.")
        else:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(project_dir)))
            self.set_status(f"Opened project directory {project_dir}")

    @objc.IBAction
    def viewMediaFiles_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return
        self.media_browser_mode = "view"
        rows, _items = self.media_browser_table_rows()
        if not rows:
            show_alert("No photos or videos are available.", "Import photos or videos into the project directory first.")
            return
        self.open_media_browser_window()

    def _open_gpx_editor(self, input_paths=None, output_path=None, open_pdf_summary=False, project_name=None):
        from GPXEditor import show_gpx_editor_from_cli_args

        if not open_pdf_summary and self.current_project_file is not None:
            gpx_name = self._gpx_field_basename()
            if not self._confirm_shared_asset_change("gpx_file", gpx_name, "GPX file"):
                return

        existing_controller = self.gpx_editor_controller
        existing_window = getattr(existing_controller, "window", None) if existing_controller is not None else None
        if existing_window is not None and existing_window.isVisible():
            existing_controller.apply_project_parameters(self.parameters)
            existing_window.makeKeyAndOrderFront_(None)
            existing_window.orderFrontRegardless()
            NSApp().activateIgnoringOtherApps_(True)
            if project_name:
                existing_controller.project_name = str(project_name)
                existing_controller.project_field.setStringValue_(str(project_name))
            if open_pdf_summary:
                existing_controller.exportPdf_(None)
            self.set_status("GPXEditor is already open.")
            return existing_controller
        if existing_window is not None and not existing_window.isVisible():
            self.gpx_editor_controller = None

        expected_output_path = Path(output_path).expanduser().resolve(strict=False) if output_path is not None else None

        def gpx_file_identity(path):
            if path is None:
                return None
            candidate = Path(path).expanduser().resolve(strict=False)
            try:
                stat = candidate.stat()
            except OSError:
                return None
            return (str(candidate), int(stat.st_size), int(stat.st_mtime_ns))

        handled_gpx_identities = set()
        opening_path = self._current_single_gpx_path() or expected_output_path
        opening_identity = gpx_file_identity(opening_path)
        if opening_identity is not None:
            handled_gpx_identities.add(opening_identity)

        def handle_editor_output(returned_output_path, source="editor"):
            if returned_output_path is None:
                return
            returned_path = Path(returned_output_path).expanduser().resolve(strict=False)
            current_path = self._current_single_gpx_path()
            if source == "save" and current_path is not None and returned_path != current_path.resolve(strict=False):
                self.set_status(f"GPXEditor saved {returned_path.name}; will confirm active GPX file when the editor closes.")
                return
            if source == "close" and current_path is not None and returned_path != current_path.resolve(strict=False):
                if not confirm_alert(
                    "Use the GPX file saved by the editor?",
                    f"The editor last saved:\n{returned_path}\n\nThe GUI is currently using:\n{current_path}\n\nSwitch to the saved file?",
                    "Use Saved File",
                    "Keep Current",
                ):
                    self.set_status(f"Continuing with {current_path.name}.")
                    return
            if returned_path.exists() and self.current_project_dir is not None and returned_path.parent != self.current_project_dir.resolve(strict=False):
                adopted_path = self._adopt_single_gpx_path(returned_path, show_errors=True, target_basename=returned_path.name)
                if adopted_path is None:
                    return
                returned_path = adopted_path
            returned_identity = gpx_file_identity(returned_path)
            if returned_identity is not None and returned_identity in handled_gpx_identities:
                if source == "close":
                    self.set_status(f"GPXEditor closed without new changes to {returned_path.name}.")
                return
            self._set_gpx_field_value(returned_path.name, manual=not self._is_default_gpx_path(returned_path))
            self.mark_dirty()
            self._refresh_project_file_menus()
            if returned_path.exists():
                try:
                    self.populate_track_summary(returned_path)
                    summary_path = self._regenerate_tracks_summary_json(returned_path)
                    self.refresh_track_maps_summary()
                    self.refresh_control_file_display()
                except (OSError, RuntimeError, ValueError) as exc:
                    show_alert("Could not reload the GPX file after editing.", str(exc))
                    self.set_status("GPX file handling failed.")
                    return
                refreshed_identity = gpx_file_identity(returned_path)
                if refreshed_identity is not None:
                    handled_gpx_identities.add(refreshed_identity)
            else:
                summary_path = None
            if source == "save":
                suffix = f"; updated {summary_path.name}" if summary_path is not None else ""
                self.set_status(f"Updated GPX summary from saved file {returned_path}{suffix}")
            else:
                suffix = f"; updated {summary_path.name}" if summary_path is not None else ""
                self.set_status(f"Continuing with {returned_path}{suffix}")

        def handle_editor_close(returned_output_path):
            self.gpx_editor_controller = None
            handle_editor_output(returned_output_path, source="close")

        def handle_editor_save(returned_output_path):
            handle_editor_output(returned_output_path, source="save")
            if returned_output_path is not None and Path(returned_output_path).is_file():
                self._select_gpx_journey_source()
                self.gpx_ready_cache = True
                self.mark_dirty(immediate=True)
                self.refresh_section_status_indicators()

        cli_args = []
        if output_path is not None:
            cli_args.extend(["--output-file", str(output_path)])
        for input_path in input_paths or []:
            cli_args.append(str(input_path))
        input_count = len(input_paths or [])
        input_names = ", ".join(path.name for path in (input_paths or [])[:4])
        self._pause_project_status_for_editor_startup()

        def handle_initial_editor_load_complete():
            self._resume_project_status_after_editor_startup()

        try:
            self.gpx_editor_controller = show_gpx_editor_from_cli_args(
                cli_args,
                on_close=handle_editor_close,
                on_save=handle_editor_save,
                on_settings_change=self._apply_embedded_editor_parameters,
                on_initial_load_complete=handle_initial_editor_load_complete,
                settings=self.parameters,
            )
        except Exception as exc:
            self._resume_project_status_after_editor_startup()
            show_alert("Could not open GPXEditor.", str(exc))
            self.set_status("GPX editor launch failed.")
            return None
        if project_name:
            self.gpx_editor_controller.project_name = str(project_name)
            self.gpx_editor_controller.project_field.setStringValue_(str(project_name))
        if open_pdf_summary:
            if self.gpx_editor_controller.track_load_timer is not None:
                self.gpx_editor_controller.pending_pdf_export_after_load = True
                self.gpx_editor_controller.set_status("Loading GPX tracks before opening PDF Summary...")
            else:
                self.gpx_editor_controller.exportPdf_(None)
        if expected_output_path is not None:
            suffix = f" with {input_count} input file(s)" if input_count else " with 0 input files"
            if input_names:
                suffix += f": {input_names}"
            self.set_status(f"Opened GPXEditor for {expected_output_path}{suffix}")
        else:
            self.set_status(f"Opened GPXEditor with {input_count} input file(s).")
        return self.gpx_editor_controller

    def handle_gpx_file_selection(self, show_errors, mark_dirty=True):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            return False

        gpx_paths = self._gpx_paths()
        if not gpx_paths:
            if show_errors:
                show_alert("Please provide a GPX file first.")
            return False

        for gpx_path in gpx_paths:
            if gpx_path.suffix.lower() != ".gpx":
                if show_errors:
                    show_alert("Only .gpx files are supported.", str(gpx_path))
                return False
            if not gpx_path.parent.exists() or not gpx_path.parent.is_dir():
                if show_errors:
                    show_alert("Das Directory des GPX-Files existiert nicht.", str(gpx_path.parent))
                self.set_status("GPX file handling failed.")
                return False

        try:
            resolved_paths = [gpx_path.resolve(strict=False) for gpx_path in gpx_paths]
            if len(resolved_paths) == 1 and resolved_paths[0].exists():
                resolved_path = self._adopt_single_gpx_path(resolved_paths[0], show_errors=show_errors)
                if resolved_path is None:
                    return False
                if mark_dirty:
                    self.mark_dirty()
                self.populate_track_summary(resolved_path)
                return True

            existing_inputs = [path for path in resolved_paths if path.exists()]
            if not existing_inputs:
                if show_errors:
                    show_alert(
                        "The selected GPX file does not exist.",
                        "Use Choose to select existing GPX files, or use Add & Edit Tracks to create a new GPX file.",
                    )
                self.clear_gpx_summary()
                return False

            target_name = self._gpx_field_basename()
            if not target_name:
                default_path = self._default_gpx_path()
                target_name = default_path.name if default_path is not None else ""
            if not target_name:
                if show_errors:
                    show_alert("Please choose a project directory first.")
                return False
            default_path = project_dir / Path(target_name).name
            if not default_path.parent.exists() or not default_path.parent.is_dir():
                if show_errors:
                    show_alert("Das Directory des GPX-Files existiert nicht.", str(default_path.parent))
                self.set_status("GPX file handling failed.")
                return False

            self._set_gpx_field_value(default_path.name, manual=True)
            if mark_dirty:
                self.mark_dirty()
            self._open_gpx_editor(input_paths=existing_inputs, output_path=default_path.resolve(strict=False))
            return True
        except (OSError, RuntimeError, ValueError, FileNotFoundError) as exc:
            if show_errors:
                show_alert("Could not open the GPX file.", str(exc))
            self.set_status("GPX file handling failed.")
            return False

    @objc.IBAction
    def addAndEditTracks_(self, _sender):
        project_dir = self._resolve_project_directory(allow_create=False, update_gpx_field=False)
        if project_dir is None:
            show_alert("Please choose a project directory first.")
            return

        selected_paths = self._gpx_paths()
        default_path = self._default_gpx_path()
        if default_path is None:
            show_alert("Please choose a project directory first.")
            return

        if not default_path.parent.exists() or not default_path.parent.is_dir():
            show_alert("Das Directory des GPX-Files existiert nicht.", str(default_path.parent))
            self.set_status("GPX file handling failed.")
            return

        if not selected_paths:
            self._set_gpx_field_value(default_path.name, manual=False)
            self.mark_dirty()
            self._open_gpx_editor(output_path=default_path)
            return

        for selected_path in selected_paths:
            if not selected_path.parent.exists() or not selected_path.parent.is_dir():
                show_alert("Das Directory des GPX-Files existiert nicht.", str(selected_path.parent))
                self.set_status("GPX file handling failed.")
                return

        resolved_paths = [selected_path.resolve(strict=False) for selected_path in selected_paths]
        if len(resolved_paths) == 1:
            resolved_paths[0] = self._adopt_single_gpx_path(resolved_paths[0], show_errors=True) or resolved_paths[0]
            self._set_gpx_field_value(resolved_paths[0].name, manual=not self._is_default_gpx_path(resolved_paths[0]))
        else:
            self._set_gpx_field_value(default_path.name, manual=True)
        existing_inputs = [path for path in resolved_paths if path.exists()]
        output_path = default_path.resolve(strict=False)
        if len(resolved_paths) == 1 and resolved_paths[0].exists():
            output_path = resolved_paths[0]
        self.mark_dirty()
        self._open_gpx_editor(input_paths=existing_inputs, output_path=output_path)

    def controlTextDidEndEditing_(self, notification):
        field = notification.object()
        if field is self.project_dir_field:
            text = str(self.project_dir_field.stringValue()).strip()
            if text and self._resolve_project_directory(allow_create=True) is None:
                return
            if self.skip_next_project_dir_dirty:
                self.skip_next_project_dir_dirty = False
                return
            self.mark_dirty()
        if field is self.gpx_field:
            text = str(self.gpx_field.stringValue()).strip()
            if text:
                self.gpx_field_manually_changed = True
                if self.handle_gpx_file_selection(show_errors=False):
                    self._select_gpx_journey_source()
                    self.gpx_ready_cache = True
                    self.mark_dirty(immediate=True)
            else:
                self.gpx_field_manually_changed = False
                self.clear_gpx_summary()
                self.mark_dirty()
        if field is self.title_field:
            self.adventureNameCommitted_(self.title_field)
        if field is self.control_file_field:
            self.controlFileCommitted_(self.control_file_field)
        if field is self.music_field:
            self._commit_music_playlist(show_errors=True)

    def controlTextDidChange_(self, notification):
        field = notification.object()
        if field is self.control_table_search_field:
            # Do not select a table row while the NSSearchField field editor is
            # handling keystrokes. Changing the table selection can retire that
            # editor and reset the insertion point after every typed character.
            self._refresh_control_table_search(select_first=False)
            return
        if field in self.parameter_controls.values():
            self._capture_parameter_controls()

    @objc.IBAction
    def fieldChanged_(self, _sender):
        if _sender is self.media_place_names_checkbox:
            enabled = self.media_place_names_checkbox.state() == NSControlStateValueOn
            self.parameters["locations.add_place_names"] = enabled
            self.workflow_assistant_state["place_names_requested"] = enabled
        self.mark_dirty()

    def textDidChange_(self, _notification):
        self.mark_dirty()

    def shutdown(self):
        self.cancel_adventure_autosave()
        if self.parameter_editor_controller is not None:
            try:
                self.parameter_editor_controller.close()
            except Exception:
                pass
            self.parameter_editor_controller = None
        if self.parameter_window is not None:
            try:
                self.parameter_window.setDelegate_(None)
                self.parameter_window.orderOut_(None)
                self.parameter_window.close()
            except Exception:
                pass
            self.parameter_window = None
            self.parameter_window_delegate = None
        if self.slideshow_process is not None and self.slideshow_process.poll() is None:
            try:
                self.slideshow_process.terminate()
            except Exception:
                pass
            self.slideshow_process = None
        if getattr(self, "notification_center", None) is not None:
            self.notification_center.removeObserver_(self)
            self.notification_center = None
        if self.geolocations_window is not None:
            try:
                self.geolocations_window.setDelegate_(None)
                self.geolocations_window.orderOut_(None)
                self.geolocations_window.close()
            except Exception:
                pass
            self.geolocations_window = None
            self.geolocations_window_delegate = None
        if self.media_browser_window is not None:
            try:
                self.media_browser_window.orderOut_(None)
                self.media_browser_window.close()
            except Exception:
                pass
            self.media_browser_window = None
        if self.media_update_preview_window is not None:
            try:
                self.media_update_preview_window.orderOut_(None)
                self.media_update_preview_window.close()
            except Exception:
                pass
            self.media_update_preview_window = None
            self.media_update_preview_table = None
            self.media_update_preview_data_source = None
        if self.media_viewer_window is not None:
            try:
                self.stop_media_viewer_video()
                self.media_viewer_window.setDelegate_(None)
                self.media_viewer_window.orderOut_(None)
                self.media_viewer_window.close()
            except Exception:
                pass
            self.media_viewer_window = None
            self.media_viewer_view = None
            self.media_viewer_delegate = None
        if self.control_table_window is not None:
            try:
                if self.control_table_search_field is not None:
                    self.control_table_search_field.setDelegate_(None)
                self.control_table_window.setDelegate_(None)
                self.control_table_window.orderOut_(None)
                self.control_table_window.close()
            except Exception:
                pass
            self.control_table_window = None
            self.control_table_window_delegate = None
            self.control_table_view = None
            self.control_table_data_source = None
            self.control_table_search_field = None
            self.control_table_search_status = None
            self.control_table_search_matches = []
        if self.music_directive_help_window is not None:
            try:
                self.music_directive_help_window.orderOut_(None)
                self.music_directive_help_window.close()
            except Exception:
                pass
            self.music_directive_help_window = None
            self.music_directive_help_text = None
        if self.main_help_window is not None:
            try:
                self.main_help_window.orderOut_(None)
                self.main_help_window.close()
            except Exception:
                pass
            self.main_help_window = None
        if self.album_selection_window is not None:
            try:
                NSApp().stopModal()
            except Exception:
                pass
            self._cleanup_album_selection_window()
        if self.plot_viewer_window is not None:
            self.plot_viewer_window.setDelegate_(None)
            self.plot_viewer_window.orderOut_(None)
            self._release_plot_viewer_references(self.plot_viewer_window)

    def show(self):
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        if self.startup_project_file is not None and self.startup_project_file.exists():
            if self.load_project_configuration(self.startup_project_file.resolve()):
                self.window.makeFirstResponder_(self.title_field)
                return
        if self.startup_project_directory is not None:
            if self._activate_project_directory(self.startup_project_directory, allow_create=True):
                self.window.makeFirstResponder_(self.title_field)
                return
        self.window.makeFirstResponder_(self.project_dir_field)


class GPSTrackShowGUIAppDelegate(NSObject):
    """Application delegate."""

    def initWithProjectDirectory_projectFile_(self, project_directory, project_file):
        self = objc.super(GPSTrackShowGUIAppDelegate, self).init()
        if self is None:
            return None
        self.project_directory = project_directory
        self.project_file = project_file
        return self

    def applicationDidFinishLaunching_(self, _notification):
        self.controller = GPXTrackerController.alloc().initWithProjectDirectory_projectFile_(self.project_directory, self.project_file)
        self.controller.show()
        NSApp().activateIgnoringOtherApps_(True)

    def applicationWillTerminate_(self, _notification):
        if getattr(self, "controller", None) is not None:
            self.controller.shutdown()

    def applicationShouldTerminate_(self, _sender):
        controller = getattr(self, "controller", None)
        if controller is None or controller.confirm_close():
            return NSTerminateNow
        return NSTerminateCancel


def build_argument_parser():
    parser = argparse.ArgumentParser(
        prog="GPSTrackShowGUI.py",
        description="Open the myCamino GPS Track Show GUI.",
    )
    parser.add_argument(
        "--project-directory",
        "-p",
        metavar="PDIR",
        help="Optional project directory to preload when the GUI starts.",
    )
    parser.add_argument(
        "startup_path",
        nargs="?",
        metavar="ADVENTURE_OR_DIRECTORY",
        help=(
            "Optional Adventure file or project directory. A directory loads its "
            "most recently modified valid .adv file when available."
        ),
    )
    return parser


def main():
    parser = build_argument_parser()
    args = parser.parse_args()
    try:
        project_directory, adventure_file = resolve_gui_startup_paths(
            args.project_directory,
            args.startup_path,
        )
    except ValueError as exc:
        parser.error(str(exc))

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = GPSTrackShowGUIAppDelegate.alloc().initWithProjectDirectory_projectFile_(
        project_directory,
        adventure_file,
    )
    app.setDelegate_(delegate)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
