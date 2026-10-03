"""Map-first Cocoa workspace for myCamino Adventures.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import concurrent.futures
import copy
import json
import math
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from security_scoped_access import SecurityScopedAccessManager
import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSApplication,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSCompositingOperationSourceOver,
    NSEventModifierFlagCommand,
    NSEventModifierFlagOption,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSOpenPanel,
    NSPopUpButton,
    NSScrollView,
    NSShadow,
    NSShadowAttributeName,
    NSTextField,
    NSTextView,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
    NSZeroRect,
)
from Foundation import NSDate, NSObject, NSString, NSTimer, NSURL

from adventure_files import (
    AdventureFormatError,
    create_adventure_from_template,
    discover_adventure_candidates,
    filename_base,
    load_adventure,
    validate_adventure_payload,
)
from adventure_map_workspace import (
    MediaMapItem,
    ProcessingJournal,
    RecoverySnapshot,
    WorkspaceRecoverySession,
    cluster_projected_media,
    delete_recovery_session,
    discover_recovery_sessions,
    media_item_from_sidecar,
    media_cluster_belongs_to_track,
    normalized_screen_rectangle,
    ordered_media_viewer_paths,
    resolved_media_paths,
    screen_rectangles_intersect,
    should_expand_media_thumbnails,
    extent_from_track_summary,
    extent_from_media_items,
    read_map_extent_prefix,
    track_extent_is_prominent,
    temporary_control_rows,
    update_media_selection,
)
from adventure_parameters import default_parameters, normalize_parameters
from adventure_parameters import SECTION_ORDER, parameter_payload
from application_metadata import full_version_label
from cocoa_map_provider_setup import run_map_provider_setup
from cocoa_native_menus import (
    WindowMenuCoordinator,
    add_menu,
    configure_mycamino_branding,
    menu_item,
)
from cocoa_application_news import install_application_news
from cocoa_circular_progress import CircularProgressView
from cocoa_guided_demo import GuidedDemoCoordinator, is_guided_demo_recovery
from cocoa_gpx_recovery import recover_missing_adventure_gpx
from cocoa_help_document import show_help_document
from cocoa_parameter_editor import CocoaParameterEditor
from demo_resources import demo_prepared_render_parameters, install_demo_prepared_assets
from control_media_inventory import (
    build_control_media_inventory_payload,
    classify_project_media,
    control_media_inventory_path,
    load_control_media_inventory,
    mark_imported_media,
    write_control_media_inventory,
)


from GPXEditor import (
    ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD,
    FILE_DRAG_TYPE,
    GPXEditorController,
    bundled_resource_path,
    lonlat_to_web_mercator,
)
from json_storage import atomic_write_json
from interactive_tile_viewport import (
    panned_extent_to_include_point,
    tile_zoom_for_viewport,
)
from gpx_adventure_identity import build_gpx_identity, normalize_gpx_identity
from license_resources import read_license_document
from media_metadata_service import media_paths_from_control_file, prepare_media_records
from media_import_utils import accepted_media_paths, import_media_files
from media_enrichment_queue import (
    AdaptiveGeocodePacing,
    LOOKAHEAD_MEDIA_COUNT,
    SlideshowAwareEnrichmentQueue,
    control_media_priority,
)
from network_availability import network_availability
from plot_metadata_utils import media_sidecar_freshness, media_sidecar_path, validate_media_sidecar
from project_media_watcher import ProjectMediaWatcher
from workspace_mutations import MutationJournal, MutationKind, WorkspaceMutation


_BACKWARD_ARROW_KEY_CODES = {123, 126}
_FORWARD_ARROW_KEY_CODES = {124, 125}


def overview_arrow_direction(key: str, key_code: int) -> int:
    """Map all four cursor keys onto previous/next overview selection."""
    if int(key_code) in _BACKWARD_ARROW_KEY_CODES or key in {"\uf702", "\uf700"}:
        return -1
    if int(key_code) in _FORWARD_ARROW_KEY_CODES or key in {"\uf703", "\uf701"}:
        return 1
    return 0


def adventure_autosave_delays(
    has_saved_adventure: bool,
    autosave_interval_seconds: float = 60.0,
) -> tuple[float, float | None]:
    """Return recovery debounce and fixed project-checkpoint intervals."""
    interval = max(15.0, min(600.0, float(autosave_interval_seconds)))
    return 1.0, interval if has_saved_adventure else None


def suggested_adventure_name_for_gpx_drop(paths) -> str:
    """Read only leading GPX metadata; never traverse the track point payload."""
    candidates = [Path(path).expanduser() for path in paths or []]
    if not candidates:
        return "New Adventure"
    source = candidates[0]
    suggested = ""
    in_metadata = False
    try:
        for event, element in ET.iterparse(source, events=("start", "end")):
            local_name = element.tag.rsplit("}", 1)[-1]
            if event == "start":
                if local_name == "metadata":
                    in_metadata = True
                elif local_name in {"trk", "rte", "wpt"}:
                    break
            elif local_name == "name" and (in_metadata or not suggested):
                value = str(element.text or "").strip()
                if value:
                    suggested = value
                    if in_metadata:
                        break
            elif local_name == "metadata":
                in_metadata = False
    except (ET.ParseError, OSError):
        suggested = ""
    if not suggested or suggested.casefold() in {"untitled", "untitled adventure"}:
        suggested = source.stem
    return filename_base(suggested) or "New Adventure"


def _write_recovery_snapshot(
    recovery,
    *,
    created_at,
    adventure_name,
    gpx_root,
    control_rows,
    source_directory,
    adventure_draft,
    media_paths,
    selected_track_identity,
):
    """Serialize the large GPX recovery payload away from AppKit."""
    snapshot = RecoverySnapshot(
        created_at,
        adventure_name,
        ET.tostring(gpx_root, encoding="unicode"),
        control_rows,
        source_directory,
        adventure_draft,
        {},
        [{"path": path} for path in media_paths],
        selected_track_identity,
    )
    recovery.write(snapshot)


def _write_gpx_checkpoint(path: Path, root: ET.Element):
    """Atomically persist a detached GPX tree and calculate its identity."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            ET.ElementTree(root).write(
                handle, encoding="utf-8", xml_declaration=True
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return build_gpx_identity(path)


MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
    ".gif", ".webp", ".mov", ".mp4", ".m4v", ".avi", ".mts", ".m2ts",
}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".aiff", ".flac", ".ogg"}
GENERATED_DIRECTORIES = {"trackimages", "normalized-videos", "audio", "narration", ".mycamino", "__pycache__"}


def _main_gui_module():
    """Reuse the running GUI module instead of redefining its PyObjC classes."""
    module = sys.modules.get("GPSTrackShowGUI")
    if module is not None:
        return module
    main_module = sys.modules.get("__main__")
    if main_module is not None and hasattr(main_module, "GPXTrackerController"):
        sys.modules["GPSTrackShowGUI"] = main_module
        return main_module
    import GPSTrackShowGUI
    return GPSTrackShowGUI


def _atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _JournalWriter:
    def __init__(self, journal, phase, line_callback=None):
        self.journal = journal
        self.phase = phase
        self.pending = ""
        self.line_callback = line_callback

    def write(self, value):
        self.pending += str(value)
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip():
                self.journal.append(line, phase=self.phase)
                if self.line_callback is not None:
                    self.line_callback(line)
        return len(str(value))

    def flush(self):
        if self.pending.strip():
            self.journal.append(self.pending, phase=self.phase)
            if self.line_callback is not None:
                self.line_callback(self.pending)
        self.pending = ""


class AdventureMapController(NSObject):
    """Own the map shell and an otherwise hidden GPX editor controller."""

    def initWithProjectDirectory_projectFile_(self, project_directory, project_file):
        self = objc.super(AdventureMapController, self).init()
        if self is None:
            return None
        self.project_directory = Path(str(project_directory)).expanduser().resolve(strict=False) if project_directory else None
        self.adventure_file = Path(str(project_file)).expanduser().resolve(strict=False) if project_file else None
        self.adventure_payload = None
        self.parameters = default_parameters()
        self.weather_consent = "unasked"
        self.untimed_prompt_suppressions: set[str] = set()
        self.adventure_name = "Untitled Adventure"
        self.control_file = None
        self.control_rows = []
        self.temporary_control_model = False
        self.media_items: list[MediaMapItem] = []
        self.media_clusters = []
        self.media_hit_targets = []
        self.show_media_icons = True
        self.expanded_media_group = frozenset()
        self.selected_media_path = None
        self.selected_media_paths: set[Path] = set()
        self.control_file_context_anchor_path = None
        self.media_selection_origin = None
        self.media_selection_current = None
        self.media_selection_base: set[Path] = set()
        self.media_viewer = None
        self.selected_track_identity = ""
        self.track_media_identity_cache = {}
        self.map_state = "overview"
        self.focused_track_nr = None
        self.overview_viewport = None
        self.track_viewports = {}
        self.plot_view = None
        self.window = None
        self.full_gui_controller = None
        self.retired_full_gui_controllers = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="mycamino-workspace")
        self.cancel_event = threading.Event()
        self.processing_generation = 0
        self.media_index_generation = 0
        self.processing_active = False
        self.processing_current = 0
        self.processing_total = 0
        self.processing_phase = ""
        self.background_progress = {}
        self.network_state = network_availability.state
        network_availability.add_listener(self._network_state_changed)
        self.pending_media: set[Path] = set()
        self.enrichment_queue = SlideshowAwareEnrichmentQueue()
        self.enrichment_active = False
        self.enrichment_paused_by_cancel = False
        self.enrichment_generation = 0
        self.enrichment_cancel_event = threading.Event()
        self.enrichment_weather_attempted: set[Path] = set()
        self.enrichment_attempted_paths: set[Path] = set()
        self.control_assets_pending = False
        self.auto_processed_paths: set[Path] = set()
        self.pending_start_after_processing = False
        self.pending_control_media: set[Path] = set()
        self.pending_route_timing_media: set[Path] = set()
        self.pending_route_timing_track_nr: int | None = None
        self.pending_create_after_gpx_load = False
        self.pending_workspace_drop_after_adventure_load: list[Path] = []
        self.media_watch_seen_paths: set[Path] = set()
        self.media_watcher = None
        self.gpx_watcher = None
        self.missing_gpx_reference = ""
        self.pending_gpx_recovery_review = False
        self.parameter_editor_controller = None
        self.properties_window = None
        self.properties_name_field = None
        self.properties_project_field = None
        self.properties_description_text = None
        self.properties_title_image_field = None
        self.properties_name_unlocked = False
        self.window_menu_coordinator = None
        self.project_access = SecurityScopedAccessManager()
        self.recovery = WorkspaceRecoverySession()
        self.recovery_dirty = False
        self.recovery_debounce_timer = None
        self.recovery_periodic_timer = None
        self.recovery_write_future = None
        self.discard_recovery_after_write = False
        self.workspace_autosave_timer = None
        self.workspace_autosave_generation = 0
        self.workspace_autosave_in_progress = False
        self.workspace_autosave_future = None
        self.workspace_autosave_poll_timer = None
        self.workspace_autosave_context = None
        self.workspace_dirty_components: set[str] = set()
        self.mutation_journal = MutationJournal()
        self.derived_track_data_future = None
        self.derived_track_data_pending = False
        self.journal = ProcessingJournal(self.recovery.directory / "logs")
        self.journal_window = None
        self.journal_text = None
        self.journal_refresh_timer = None
        self.startup_in_progress = False
        self.startup_map_generation = 0
        self.startup_extent_applied = False
        self.startup_media_extent_pending = False
        self.pending_startup_gpx_path = None
        self.startup_track_loading = False
        self.startup_media_scan_deferred = False
        self.startup_watcher_deferred = False
        self.startup_post_track_work_pending = False
        self.startup_pending_media_paths: set[Path] = set()
        self.document_windows = {}
        self.recent_adventure_menu = None
        self.help_window = None
        self.demo_mode = False
        self.demo_coordinator = None
        self.gpx_controller = GPXEditorController.alloc().initStandalone_(False)
        self.gpx_controller.adventure_workspace_delegate = self
        self.gpx_controller.on_initial_load_complete_callback = self._tracks_loaded
        return self

    @objc.python_method
    def show(self):
        self.startup_in_progress = True
        try:
            recovered = self._offer_recovery()
            if recovered:
                self._show_empty_world()
                self.show_workspace_overview(remember_current=False)
                if self.project_directory is not None:
                    self._scan_project_media()
            elif self.adventure_file is not None:
                self.load_adventure_path(self.adventure_file)
            elif self.project_directory is not None:
                self.load_project_directory(self.project_directory)
            else:
                self._show_empty_world()
            self.recovery_periodic_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                60.0, self, "periodicRecovery:", None, True
            )
        finally:
            self.startup_in_progress = False

    @objc.python_method
    def _show_empty_world(self):
        self.map_state = "overview"
        self.focused_track_nr = None
        if not self.gpx_controller.tracks:
            self.gpx_controller.create_empty_track(
                name="New Track",
                mark_dirty=False,
                assign_default_project_name=False,
            )
        if self.plot_view is not None:
            plot_info = self.gpx_controller.interactive_plot_info("overview")
            if plot_info is not None:
                self.plot_view.mode = "overview"
                self.plot_view.track_index = 0
                self.plot_view.replace_plot_info(plot_info)
                self._set_title()
            return
        self.gpx_controller.open_plot_window("overview", open_profile=False)
        existing = self.gpx_controller.existing_plot_view("overview")
        if existing:
            self._attach_map_window(existing[0], existing[1])

    @objc.python_method
    def _attach_map_window(self, window, view):
        self.window = window
        self.plot_view = view
        view.adventure_workspace_delegate = self
        view.show_media_markers = self.show_media_icons
        view.registerForDraggedTypes_([FILE_DRAG_TYPE])
        view.controller.adventure_workspace_delegate = self
        self._set_title()
        if getattr(view, "_adventure_controls_installed", False):
            view.setNeedsDisplay_(True)
            return
        old_bounds = window.contentView().bounds()
        container = NSView.alloc().initWithFrame_(old_bounds)
        container.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        view.reparenting_for_adventure_workspace = True
        try:
            view.removeFromSuperview()
            view.setFrame_(old_bounds)
            view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            container.addSubview_(view)
        finally:
            view.reparenting_for_adventure_workspace = False
        self.progress_indicator = CircularProgressView.alloc().initWithFrame_owner_(
            NSMakeRect(old_bounds.size.width - 40, old_bounds.size.height - 40, 30, 30), self
        )
        self.progress_indicator.setAutoresizingMask_(1 << 0 | 1 << 4)
        container.addSubview_(self.progress_indicator)
        self.progress_label = NSTextField.labelWithString_("")
        self.progress_label.setFrame_(NSMakeRect(old_bounds.size.width - 220, old_bounds.size.height - 30, 184, 20))
        self.progress_label.setAlignment_(2)
        self.progress_label.setDrawsBackground_(False)
        self.progress_label.setBezeled_(False)
        self.progress_label.setTextColor_(NSColor.whiteColor())
        progress_shadow = NSShadow.alloc().init()
        progress_shadow.setShadowColor_(NSColor.blackColor().colorWithAlphaComponent_(0.95))
        progress_shadow.setShadowOffset_(NSMakeSize(1.0, -1.0))
        progress_shadow.setShadowBlurRadius_(4.0)
        self.progress_label.setShadow_(progress_shadow)
        self.progress_label.setAutoresizingMask_(1 << 0 | 1 << 4)
        container.addSubview_(self.progress_label)
        self.network_status_label = NSTextField.labelWithString_("")
        self.network_status_label.setFrame_(
            NSMakeRect(old_bounds.size.width - 330, old_bounds.size.height - 52, 294, 20)
        )
        self.network_status_label.setAlignment_(2)
        self.network_status_label.setDrawsBackground_(False)
        self.network_status_label.setBezeled_(False)
        self.network_status_label.setTextColor_(NSColor.whiteColor())
        self.network_status_label.setShadow_(progress_shadow)
        self.network_status_label.setAutoresizingMask_(1 << 0 | 1 << 4)
        container.addSubview_(self.network_status_label)
        window.setContentView_(container)
        window.setMinSize_(NSMakeSize(920, 560))
        window.makeFirstResponder_(view)
        view._adventure_controls_installed = True
        if view.interactive_tiles_active:
            view.schedule_interactive_tiles(immediate=True)
        self._refresh_progress_indicator()
        self._apply_network_state(self.network_state)
        view.setNeedsDisplay_(True)

    @objc.python_method
    def _network_state_changed(self, state):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyNetworkState:", str(state), False
        )

    @objc.IBAction
    def applyNetworkState_(self, state):
        self._apply_network_state(str(state))

    @objc.python_method
    def _apply_network_state(self, state):
        previous = getattr(self, "network_state", "online")
        self.network_state = str(state)
        label = getattr(self, "network_status_label", None)
        if label is not None:
            messages = {
                "offline": "Offline - online work paused",
                "recovering": "Connection found - resuming shortly",
            }
            label.setStringValue_(messages.get(self.network_state, ""))
        if previous != self.network_state and hasattr(self, "journal"):
            if self.network_state == "offline":
                self._journal(
                    "Network",
                    "Offline for at least 5 seconds; weather, place-name, and tile retrieval are paused.",
                )
            elif self.network_state == "online" and previous != "online":
                self._journal("Network", "Connection is stable; paused online work is resuming.")

    @objc.python_method
    def _set_title(self):
        if self.window is not None:
            self.window.setTitle_(f"myCamino — {self.adventure_name}")

    @objc.python_method
    def window_status_for_window(self, window):
        states = []
        if bool(window.isKeyWindow()):
            states.append("Active")
        elif bool(window.isMiniaturized()):
            states.append("Minimized")
        elif not bool(window.isVisible()):
            states.append("Hidden")
        else:
            states.append("Visible")
        if window is self.window and (self.recovery_dirty or self.gpx_controller.dirty):
            states.append("Unsaved")
        if window is self.window and self.processing_active:
            states.append("Processing")
        return ", ".join(states)

    def validateMenuItem_(self, item):
        action = str(item.action() or "")
        if self.full_gui_controller is not None:
            self._synchronize_control_file_from_full_gui()
        has_project = self.project_directory is not None
        has_tracks = bool(self.gpx_controller.tracks)
        has_selection = bool(self.gpx_controller.selected_tracks())
        has_control = bool(self.control_file is not None and self.control_file.is_file())
        if action == "toggleAudioEnabled:":
            item.setState_(
                NSControlStateValueOn
                if bool(self.parameters.get("audio.enabled", False))
                else NSControlStateValueOff
            )
        if action in {
            "revealProjectFolder:", "importMedia:", "revealMediaFolder:",
            "addPlaceNames:", "addHistoricalWeather:", "updateMetadata:",
            "generateMaps:", "viewMaps:", "revealMaps:", "chooseControlFile:",
            "createControlFile:", "toggleAudioEnabled:", "chooseMusicPlaylist:",
            "createMusicPlaylist:", "updateMusicPlaylist:", "editMusicPlaylist:",
            "revealMusicFolder:", "chooseNarrationPlaylist:",
            "createNarrationPlaylist:", "updateNarrationPlaylist:",
            "editNarrationPlaylist:", "revealNarrationFolder:",
            "normalizeVideoAudio:", "startSlideShow:", "continueSlideShow:",
            "exportPDFSummary:",
        }:
            return has_project
        if action in {"openControlFile:", "updateControlFile:", "revealControlFile:", "chooseSlideShowStart:"}:
            return has_control
        if action in {
            "showTrackTable:", "showWaypointTable:", "showElevationProfile:",
            "fitToTrack:", "renumberTracks:", "estimateRouteTimesFromMedia:",
        }:
            return has_tracks
        if action == "saveSelectedTracksAs:":
            return has_selection
        if action == "createTrackFromSelectedMedia:":
            return bool(self.selected_media_paths)
        if action == "cancelMapGeneration:":
            controller = self.full_gui_controller
            return bool(
                controller is not None
                and not controller.gpx_cancel_plots_button.isHidden()
            )
        return True

    def updateWorkspaceWindowTitle_(self, _window):
        self._set_title()

    @objc.python_method
    def _remember_workspace_viewport(self):
        if self.plot_view is None:
            return
        extent = self.plot_view.current_extent()
        if extent is None:
            return
        viewport = {
            "extent": dict(extent),
            "zoom": self.plot_view.current_tile_zoom(),
        }
        if self.map_state == "track" and self.focused_track_nr is not None:
            self.track_viewports[int(self.focused_track_nr)] = viewport
        else:
            self.overview_viewport = viewport

    @objc.python_method
    def _fit_workspace_plot_info(self, plot_info, tracks, saved_viewport=None):
        info = copy.deepcopy(plot_info)
        if saved_viewport is not None:
            extent = dict(saved_viewport["extent"])
            zoom = int(saved_viewport["zoom"])
        else:
            extent = dict((info.get("metadata") or {}).get("extent_mercator") or {})
            has_points = any(track.points() for track in tracks)
            if extent and has_points and self.plot_view is not None:
                bounds = self.plot_view.bounds()
                extent = self.gpx_controller.fit_extent_to_aspect(
                    extent,
                    (max(bounds.size.width, 1.0), max(bounds.size.height, 1.0)),
                )
            zoom = int(info.get("tile_zoom_level", info.get("zoom_level", 0)))
            if extent and self.plot_view is not None:
                bounds = self.plot_view.bounds()
                zoom = max(
                    zoom,
                    tile_zoom_for_viewport(
                        extent,
                        bounds.size.width,
                        bounds.size.height,
                        maximum_zoom=self.gpx_controller.maximum_map_zoom,
                    ),
                )
        if not extent:
            return info
        zoom, _diagnostics = self.gpx_controller.effective_tile_zoom(extent, zoom)

        def update_entry(entry):
            metadata = dict(entry.get("metadata") or {})
            metadata["extent_mercator"] = dict(extent)
            metadata["axes_box_fraction"] = {
                "left": 0.0,
                "bottom": 0.0,
                "width": 1.0,
                "height": 1.0,
            }
            entry["metadata"] = metadata
            entry["base_extent_mercator"] = dict(extent)
            entry["zoom_level"] = zoom
            entry["tile_zoom_level"] = zoom

        update_entry(info)
        current_nr = info.get("current_track_nr")
        if current_nr in (info.get("tracks") or {}):
            update_entry(info["tracks"][current_nr])
        return info

    @objc.python_method
    def focus_workspace_tracks(self, tracks, *, mode="track", reset_viewport=False):
        tracks = [track for track in tracks if track in self.gpx_controller.tracks]
        if self.plot_view is None or not tracks:
            return None
        self._remember_workspace_viewport()
        self.gpx_controller.selected_nrs = [track.nr for track in tracks]
        self.gpx_controller.update_selection_field()
        self.gpx_controller.highlight_selected_rows()
        plot_info = self.gpx_controller.interactive_plot_info(mode, tracks_override=tracks)
        if plot_info is None:
            return None
        saved_viewport = None
        if not reset_viewport and len(tracks) == 1:
            saved_viewport = self.track_viewports.get(tracks[0].nr)
        plot_info = self._fit_workspace_plot_info(
            plot_info,
            tracks,
            saved_viewport,
        )
        self.map_state = "track"
        self.focused_track_nr = tracks[0].nr if len(tracks) == 1 else None
        self.selected_track_identity = str(tracks[0].nr) if len(tracks) == 1 else ""
        self.plot_view.mode = mode
        self.plot_view.track_index = 0
        self.plot_view.replace_plot_info(plot_info)
        self._set_title()
        self.plot_view.setNeedsDisplay_(True)
        if self.window is not None:
            self.window.makeKeyAndOrderFront_(None)
            self.window.makeFirstResponder_(self.plot_view)
        return self.plot_view

    @objc.python_method
    def focus_workspace_track(self, track, point_index=None, reset_viewport=False):
        view = self.focus_workspace_tracks(
            [track],
            mode="track",
            reset_viewport=reset_viewport,
        )
        if view is None:
            return None
        if point_index is not None and track.points():
            view.move_cursor_to_track_point(
                track,
                max(0, min(int(point_index), len(track.points()) - 1)),
                sync_table=False,
                focus_plot=False,
            )
        return view

    @objc.python_method
    def inspect_workspace_track(self, track, point_index=0):
        view = self.focus_workspace_track(
            track,
            point_index,
            reset_viewport=self.focused_track_nr != track.nr,
        )
        if view is None:
            return None
        inspector = self.gpx_controller.open_inspector_for_track(track)
        if inspector is not None:
            view.inspector = inspector
            inspector.plot_view = view
            inspector.select_point_index(
                max(0, min(int(point_index), max(0, len(track.points()) - 1)))
            )
            inspector.window.makeKeyAndOrderFront_(None)
            inspector.window.orderFrontRegardless()
        return inspector

    @objc.python_method
    def show_workspace_overview(self, remember_current=True):
        if self.plot_view is None:
            return None
        if remember_current:
            self._remember_workspace_viewport()
        tracks = self.gpx_controller.visible_tracks()
        plot_info = self.gpx_controller.interactive_plot_info("overview")
        if plot_info is None:
            return None
        plot_info = self._fit_workspace_plot_info(
            plot_info,
            tracks,
            self.overview_viewport,
        )
        self.map_state = "overview"
        self.focused_track_nr = None
        self.plot_view.mode = "overview"
        self.plot_view.track_index = 0
        self.plot_view.replace_plot_info(plot_info)
        self._set_title()
        self.plot_view.setNeedsDisplay_(True)
        return self.plot_view

    @objc.IBAction
    def showAdventureOverview_(self, _sender):
        self.show_workspace_overview()

    @objc.python_method
    def load_project_directory(self, directory: Path):
        directory = Path(directory).expanduser().resolve(strict=False)
        if not self.project_access.activate_directory(directory):
            self._alert(
                "Adventure folder access is unavailable.",
                "Choose the folder again to renew myCamino's permission.",
            )
            return
        self._reset_optional_enrichment()
        self._stop_media_watcher()
        self.startup_track_loading = False
        self.startup_media_scan_deferred = False
        self.startup_watcher_deferred = False
        self.startup_post_track_work_pending = False
        self.startup_pending_media_paths.clear()
        self.startup_media_extent_pending = False
        records, templates, errors = discover_adventure_candidates(directory)
        if records:
            self.load_adventure_path(records[0].path)
            return
        if templates:
            selected = self._choose_copied_template(templates, directory)
            if selected is not None:
                self.load_adventure_path(selected)
            return
        self.project_directory = Path(directory).resolve(strict=False)
        self.adventure_name = filename_base(self.project_directory.name)
        self._prepare_gpx_document_context()
        self.overview_viewport = None
        self.track_viewports.clear()
        self.temporary_control_model = True
        self.journal.move_to(self.project_directory / ".mycamino" / "logs")
        gpx_paths = sorted(self.project_directory.glob("*.gpx"), key=lambda path: path.name.casefold())
        if gpx_paths:
            self.startup_track_loading = True
            self.startup_media_scan_deferred = True
            self.startup_watcher_deferred = True
            self._show_empty_world()
            self.gpx_controller.tracks = []
            self.gpx_controller.next_nr = 1
            self.gpx_controller.load_gpx_paths(gpx_paths, mark_dirty=False)
        else:
            self.startup_media_extent_pending = True
            self._show_empty_world()
        if errors:
            self._journal("Adventure", "; ".join(errors))
        self._scan_project_media()
        self._start_media_watcher()

    @objc.python_method
    def _choose_copied_template(self, templates, directory):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Use the copied Adventure?")
        alert.setInformativeText_(
            f"Select a copied Adventure template. A local Adventure named {directory.name} will share the copied project assets."
        )
        chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 420, 28), False)
        chooser.addItemsWithTitles_([f"{item.path.name} — Needs adaptation" for item in templates])
        alert.setAccessoryView_(chooser)
        alert.addButtonWithTitle_("Create from Selected")
        alert.addButtonWithTitle_("Cancel")
        if int(alert.runModal()) != 1000:
            return None
        try:
            selected = templates[max(0, int(chooser.indexOfSelectedItem()))]
            path, _payload = create_adventure_from_template(selected.path, directory, directory.name)
            return path
        except (AdventureFormatError, FileExistsError, OSError) as exc:
            self._alert("Could not adapt the copied Adventure.", str(exc))
            return None

    @objc.python_method
    def load_adventure_path(self, path: Path):
        path = Path(path).expanduser().resolve(strict=False)
        if not self.project_access.activate_directory(path.parent):
            self._alert(
                "Adventure folder access is unavailable.",
                "Choose the folder again to renew myCamino's permission.",
            )
            return
        if self.adventure_file is not None and (
            self.workspace_dirty_components or self.gpx_controller.dirty
        ):
            if not self._flush_workspace_autosave(show_errors=True):
                return
        self._reset_optional_enrichment()
        self._stop_media_watcher()
        self.startup_track_loading = False
        self.startup_media_scan_deferred = False
        self.startup_watcher_deferred = False
        self.startup_post_track_work_pending = False
        self.startup_pending_media_paths.clear()
        self.startup_media_extent_pending = False
        try:
            record = load_adventure(path)
        except AdventureFormatError as exc:
            self._alert("Could not load Adventure.", str(exc))
            return
        loaded_payload = dict(record.payload)
        self.missing_gpx_reference = ""
        self.pending_gpx_recovery_review = False
        referenced_gpx = str(loaded_payload.get("gpx_file", "")).strip()
        if referenced_gpx and not (record.path.parent / referenced_gpx).is_file():
            recovery = recover_missing_adventure_gpx(record.path, loaded_payload)
            loaded_payload = recovery.payload
            self.pending_gpx_recovery_review = bool(recovery.review_requested)
            if recovery.action == "ignored":
                self.missing_gpx_reference = referenced_gpx
                self._journal(
                    "Tracks",
                    f"GPX file is still missing: {referenced_gpx}",
                )
        if self.workspace_autosave_timer is not None:
            self.workspace_autosave_timer.invalidate()
            self.workspace_autosave_timer = None
        if self.workspace_autosave_poll_timer is not None:
            self.workspace_autosave_poll_timer.invalidate()
            self.workspace_autosave_poll_timer = None
        if self.workspace_autosave_future is not None:
            try:
                self.workspace_autosave_future.result()
            except Exception:
                pass
            self.workspace_autosave_future = None
            self.workspace_autosave_context = None
            self.workspace_autosave_in_progress = False
        self.workspace_dirty_components.clear()
        self.recovery_dirty = False
        self.adventure_file = record.path
        self.overview_viewport = None
        self.track_viewports.clear()
        self.auto_processed_paths.clear()
        self.adventure_payload = loaded_payload
        self.untimed_prompt_suppressions = {
            str(value)
            for value in loaded_payload.get(
                ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD, []
            )
            if str(value).strip()
        }
        self.project_directory = record.path.parent
        self.adventure_name = record.project_name
        self._prepare_gpx_document_context()
        self.demo_mode = bool(record.payload.get("guided_demo", False))
        self._remember_recent_adventure(self.project_directory)
        self.control_file = self.project_directory / str(loaded_payload.get("control_file", ""))
        self.temporary_control_model = not self.control_file.is_file()
        self.parameters = normalize_parameters(loaded_payload.get("parameters") or {})
        self.mutation_journal.clear()
        self._configure_workspace_autosave_timer()
        stored_consent = str(loaded_payload.get("weather_consent", "")).casefold()
        if stored_consent not in {"unasked", "free", "customer", "disabled"}:
            stored_consent = (
                str(self.parameters.get("weather.access", "free")).casefold()
                if bool(self.parameters.get("weather.enabled", False))
                else "unasked"
            )
        self.weather_consent = stored_consent
        self.gpx_controller.apply_project_parameters(self.parameters)
        self.gpx_controller.media_context = {
            "project_directory": str(self.project_directory),
            "control_file": str(self.control_file),
        }
        self.journal.move_to(self.project_directory / ".mycamino" / "logs")
        gpx_name = str(loaded_payload.get("gpx_file", "")).strip()
        gpx = self.project_directory / gpx_name if gpx_name else None
        # Put a usable world canvas on screen before a large GPX finishes loading.
        self.gpx_controller.tracks = []
        self.gpx_controller.next_nr = 1
        self._show_empty_world()
        self.gpx_controller.tracks = []
        self.gpx_controller.next_nr = 1
        if gpx is not None and gpx.is_file():
            self.startup_track_loading = True
            self.startup_media_scan_deferred = True
            self.startup_watcher_deferred = True
            self._set_progress("Loading tracks", 0, 1)
            self._begin_startup_extent_lookup(gpx)
            self.pending_startup_gpx_path = gpx
            # Let AppKit paint the world map before parsing a large GPX file.
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.12, self, "beginStartupGPXLoad:", None, False
            )
        else:
            self.startup_media_extent_pending = True
            self._show_empty_world()
        self._load_control_rows()
        self._scan_project_media()
        self._start_media_watcher()

    @objc.python_method
    def _stored_startup_extent(self):
        if self.project_directory is None or self.adventure_payload is None:
            return None
        base = str(self.adventure_payload.get("track_map_base", "")).strip()
        if not base:
            return None
        map_directory = self.project_directory / "trackimages"
        extent = read_map_extent_prefix(map_directory / f"{base}.json")
        if extent is not None:
            return extent
        summary_path = map_directory / f"{base}-summary.json"
        try:
            return extent_from_track_summary(json.loads(summary_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    @objc.python_method
    def _begin_startup_extent_lookup(self, _gpx_path):
        self.startup_map_generation += 1
        generation = self.startup_map_generation
        self.startup_extent_applied = False
        future = self.executor.submit(self._stored_startup_extent)

        def poll(timer):
            if not future.done():
                return
            timer.invalidate()
            if generation != self.startup_map_generation or self.plot_view is None:
                return
            try:
                extent = future.result()
            except Exception as exc:
                self._journal("Tracks", f"Could not read the saved overview extent: {exc}")
                return
            if extent is None:
                return
            bounds = self.plot_view.bounds()
            extent = self.gpx_controller.fit_extent_to_aspect(
                extent,
                (max(bounds.size.width, 1.0), max(bounds.size.height, 1.0)),
            )
            zoom = tile_zoom_for_viewport(
                extent,
                max(bounds.size.width, 1.0),
                max(bounds.size.height, 1.0),
                maximum_zoom=self.gpx_controller.maximum_map_zoom,
            )
            self.plot_view.set_interactive_viewport(
                extent,
                zoom,
            )
            self.overview_viewport = {"extent": dict(extent), "zoom": int(zoom)}
            self.startup_extent_applied = True

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.02, _BlockTimer.alloc().initWithBlock_(poll), "fire:", None, True
        )

    @objc.python_method
    def _apply_startup_media_extent(self, extent):
        """Fit a media-only Adventure once its indexed GPS extent is available."""
        if (
            not self.startup_media_extent_pending
            or extent is None
            or self.plot_view is None
        ):
            return False
        bounds = self.plot_view.bounds()
        extent = self.gpx_controller.fit_extent_to_aspect(
            extent,
            (max(bounds.size.width, 1.0), max(bounds.size.height, 1.0)),
        )
        zoom = tile_zoom_for_viewport(
            extent,
            max(bounds.size.width, 1.0),
            max(bounds.size.height, 1.0),
            maximum_zoom=self.gpx_controller.maximum_map_zoom,
        )
        zoom, _diagnostics = self.gpx_controller.effective_tile_zoom(extent, zoom)
        self.plot_view.set_interactive_viewport(
            extent,
            zoom,
        )
        self.overview_viewport = {"extent": dict(extent), "zoom": int(zoom)}
        self.startup_media_extent_pending = False
        self._journal(
            "Media index",
            "Fitted the startup map to media with GPS coordinates.",
        )
        return True

    def beginStartupGPXLoad_(self, _timer):
        gpx = self.pending_startup_gpx_path
        self.pending_startup_gpx_path = None
        if gpx is None or not Path(gpx).is_file():
            return
        self.gpx_controller.initial_load_completion_notified = False
        self.gpx_controller.on_initial_load_complete_callback = self._tracks_loaded
        self.gpx_controller.load_gpx_paths([Path(gpx)], mark_dirty=False)

    @objc.python_method
    def workspace_track_loading_progress(self, current, total):
        self._set_progress("Loading tracks", current, total)
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def _tracks_loaded(self):
        self.startup_track_loading = False
        self._clear_progress()
        if not self.gpx_controller.tracks:
            self.gpx_controller.create_empty_track(
                name="New Track",
                mark_dirty=False,
                assign_default_project_name=False,
            )
        if (
            self.pending_create_after_gpx_load
            and not any(track.points() for track in self.gpx_controller.tracks)
        ):
            self.pending_create_after_gpx_load = False
            self._alert(
                "The Adventure was not created.",
                "No usable GPX tracks were loaded. The original GPX file was not changed.",
            )
        if self.plot_view is not None:
            # The saved extent is an immediate startup approximation. Refit
            # once to the fully loaded geometry so newly added tracks are not
            # clipped, while replace_plot_info keeps the existing tiles visible.
            if self.startup_extent_applied:
                self.overview_viewport = None
                self.startup_extent_applied = False
            self.show_workspace_overview(remember_current=False)
        else:
            self.gpx_controller.open_plot_window("overview", open_profile=False)
            existing = self.gpx_controller.existing_plot_view("overview")
            if existing:
                self._attach_map_window(existing[0], existing[1])
        self._journal("Tracks", f"Loaded {len(self.gpx_controller.tracks)} track(s).")
        self._dispatch_pending_workspace_drop()
        if (
            self.gpx_controller.project_name_needs_metadata_repair
            and self.adventure_file is not None
            and self.gpx_controller.tracks
        ):
            self.gpx_controller.project_name_needs_metadata_repair = False
            self.gpx_controller.dirty = True
            self.workspaceMutationDidOccur_(
                WorkspaceMutation.create(
                    "gpx",
                    MutationKind.METADATA,
                    message=(
                        "Replaced missing or placeholder GPX document metadata "
                        f"with the Adventure name '{self.gpx_controller.project_name}'."
                    ),
                )
            )
        if self.startup_media_scan_deferred:
            self.startup_media_scan_deferred = False
            self.startup_post_track_work_pending = True
            self._scan_project_media()
            return
        self._queue_derived_track_data()
        if self.startup_watcher_deferred:
            self.startup_watcher_deferred = False
            self._start_media_watcher()

    @objc.python_method
    def _queue_derived_track_data(self):
        if self.adventure_payload is None or self.project_directory is None:
            return
        if self.derived_track_data_future is not None and not self.derived_track_data_future.done():
            self.derived_track_data_pending = True
            return
        gpx_path = self.project_directory / str(self.adventure_payload.get("gpx_file", ""))
        if not gpx_path.is_file():
            return
        output_dir = self.project_directory / "trackimages"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._journal("Derived Track Data", f"Checking {len(self.gpx_controller.tracks)} track(s).")
        self._set_background_progress(
            "derived", "Updating track data", 0, len(self.gpx_controller.tracks)
        )

        values = dict(self.parameters)
        use_track_order = str(values.get("trackmaps.ordering", "track_number")) == "track_number"
        output_base = str(
            self.adventure_payload.get("track_map_base")
            or filename_base(self.adventure_name)
        )
        processing_keys = (
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
            "gpx.running_speed_window_distance_m",
            "gpx.stationary_speed_threshold_kmh",
        )
        processing_signature = {
            key: values.get(key) for key in processing_keys
        }

        def work():
            from gpx_tracks_table import run_with_options
            return run_with_options(
                gpx_path,
                print_table_output=False,
                plot_overview=False,
                plot_tracks=None,
                output_dir=str(output_dir),
                output_base=output_base,
                nojson=False,
                pdf=False,
                verbose=False,
                sort_original=use_track_order,
                sort_date=not use_track_order,
                remove_prefix=values["trackmaps.remove_name_prefix"],
                gpx_threshold_distance=values["gpx.minimum_point_spacing_m"],
                gpx_threshold_accuracy=values["gpx.maximum_accuracy_m"],
                gpx_horizontal_smoothing_distance=values["gpx.horizontal_smoothing_distance_m"],
                gpx_elevation_smoothing_distance=values["gpx.elevation_smoothing_distance_m"],
                gpx_maximum_vertical_accuracy=values["gpx.maximum_vertical_accuracy_m"],
                gpx_maximum_hdop=values["gpx.maximum_hdop"],
                gpx_maximum_vdop=values["gpx.maximum_vdop"],
                gpx_running_speed_window_distance=values["gpx.running_speed_window_distance_m"],
                gpx_stationary_speed_threshold=values["gpx.stationary_speed_threshold_kmh"],
                fallback_walking_speed_kmh=values["gpx.fallback_walking_speed_kmh"],
                adventure_processing_parameters=processing_signature,
            )
        future = self.executor.submit(work)
        self.derived_track_data_future = future

        def poll(timer):
            if not future.done():
                return
            timer.invalidate()
            if self.derived_track_data_future is future:
                self.derived_track_data_future = None
            self._clear_background_progress("derived")
            try:
                result = future.result()
                self._journal(
                    "Derived Track Data",
                    f"Rebuilt current data for {len(result.get('tracks', []))} track(s); no maps were rendered.",
                )
                controller = self.full_gui_controller
                if controller is not None:
                    controller.start_async_project_status_refresh(
                        "derived track data rebuilt"
                    )
                if self.pending_gpx_recovery_review:
                    self.pending_gpx_recovery_review = False
                    controller = self._ensure_full_gui(show_window=False)
                    if controller is not None:
                        controller.pending_control_update_offer_after_status = []
                        controller.makePlots_(None)
            except Exception as exc:
                self._journal("Derived Track Data", f"Failed: {exc}")
            if self.derived_track_data_pending:
                self.derived_track_data_pending = False
                self._queue_derived_track_data()
            else:
                self._start_optional_enrichment()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, _BlockTimer.alloc().initWithBlock_(poll), "fire:", None, True
        )

    @objc.python_method
    def _load_control_rows(self):
        self.control_rows = []
        if self.control_file is None or not self.control_file.is_file():
            self.temporary_control_model = True
            return
        self.temporary_control_model = False
        parse_slideshow_control_line = _main_gui_module().parse_slideshow_control_line
        try:
            self.control_rows = [parse_slideshow_control_line(line) for line in self.control_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError as exc:
            self._journal("Control File", f"Could not read {self.control_file.name}: {exc}")

    @objc.python_method
    def _project_media_paths(self) -> list[Path]:
        if self.project_directory is None or not self.project_directory.is_dir():
            return []
        return sorted(
            (
                path.resolve(strict=False)
                for path in self.project_directory.rglob("*")
                if path.is_file()
                and path.suffix.casefold() in MEDIA_EXTENSIONS
                and not any(part.casefold() in GENERATED_DIRECTORIES for part in path.relative_to(self.project_directory).parts)
            ),
            key=lambda item: str(item).casefold(),
        )

    @objc.python_method
    def _scan_project_media(self):
        if self.startup_track_loading:
            self.startup_media_scan_deferred = True
            return
        generation = self.media_index_generation = self.media_index_generation + 1
        self._set_background_progress("media_index", "Indexing media", 0, 0)
        def work():
            project_paths = self._project_media_paths()
            parameters = dict(self.parameters)
            result = []
            pending = []
            enrichment = []
            for path in project_paths:
                sidecar = media_sidecar_path(path)
                status, validated, _reason = validate_media_sidecar(path, sidecar)
                result.append(media_item_from_sidecar(path, validated))
                freshness = media_sidecar_freshness(path, validated) if status == "available" else "unknown"
                needs_place = bool(parameters.get("locations.add_place_names", True)) and (
                    not isinstance(validated, dict) or not validated.get("place")
                )
                needs_weather = bool(parameters.get("weather.enabled", False)) and (
                    not isinstance(validated, dict) or not isinstance(validated.get("weather"), dict)
                )
                if path not in self.auto_processed_paths and (
                    status != "available" or freshness == "changed"
                ):
                    pending.append(path)
                elif needs_place or needs_weather:
                    enrichment.append(path)
            return result, pending, enrichment, extent_from_media_items(result)
        future = self.executor.submit(work)
        def poll(_timer):
            if not future.done():
                return
            _timer.invalidate()
            if generation != self.media_index_generation:
                return
            self._clear_background_progress("media_index")
            try:
                self.media_items, pending, enrichment, media_extent = future.result()
            except Exception as exc:
                self._journal("Media index", f"Failed: {exc}")
                return
            available_paths = resolved_media_paths(self.media_items)
            self.selected_media_paths.intersection_update(available_paths)
            if self.selected_media_path not in available_paths:
                self.selected_media_path = None
            if self.plot_view is not None:
                self._apply_startup_media_extent(media_extent)
                self.plot_view.setNeedsDisplay_(True)
            self._queue_optional_enrichment(enrichment, start=False)
            self._journal(
                "Media index",
                f"Indexed {len(self.media_items)} media file(s); "
                f"{sum(item.latitude is not None and item.longitude is not None for item in self.media_items)} have map positions.",
            )
            if self.temporary_control_model:
                enabled_by_path = {
                    str(row.get("name", "")): not bool(row.get("disabled", False))
                    for row in self.control_rows
                    if row.get("type") in {"IMG", "VID"}
                }
                items = [
                    MediaMapItem(
                        item.path, item.latitude, item.longitude, item.exposure_time,
                        item.place, item.track_identity,
                        enabled_by_path.get(str(item.path), item.enabled),
                    )
                    for item in self.media_items
                ]
                self.control_rows = temporary_control_rows(items)
                if self.adventure_file is None and self.control_rows:
                    self.workspaceDocumentDidChange_("Temporary control model updated")
            if self.startup_post_track_work_pending:
                self.startup_post_track_work_pending = False
                self.startup_pending_media_paths.update(pending)
                # Give AppKit one paint interval to install the media markers
                # before lower-priority metadata and derived-data work starts.
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.15, self, "finishPrioritizedStartupWork:", None, False
                )
                return
            if pending:
                self.queue_media_processing(pending)
            elif self.pending_control_media and not self.processing_active:
                self._finish_detected_media_control_followup()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.1, _BlockTimer.alloc().initWithBlock_(poll), "fire:", None, True)

    @objc.IBAction
    def finishPrioritizedStartupWork_(self, _timer):
        self._finish_prioritized_startup_work()

    @objc.python_method
    def _finish_prioritized_startup_work(self):
        pending = sorted(
            self.startup_pending_media_paths,
            key=lambda path: str(path).casefold(),
        )
        self.startup_pending_media_paths.clear()
        self._queue_derived_track_data()
        if pending:
            self.queue_media_processing(pending)
        elif self.pending_control_media and not self.processing_active:
            self._finish_detected_media_control_followup()
        if self.startup_watcher_deferred:
            self.startup_watcher_deferred = False
            self._start_media_watcher()

    @objc.python_method
    def queue_media_processing(self, paths):
        added = {Path(path).expanduser().resolve(strict=False) for path in paths}
        if self._metadata_creation_requires_weather_choice(added):
            if not self._request_initial_weather_choice():
                return
        self.pending_media.update(path for path in added if path.is_file())
        self.enrichment_paused_by_cancel = False
        if self.processing_active:
            return
        self._start_next_media_batch()

    @objc.python_method
    def _start_next_media_batch(self):
        if not self.pending_media:
            self._clear_progress()
            if self.pending_control_media:
                # Refresh the temporary model from newly written sidecars before
                # creating or reviewing the control file.
                self._scan_project_media()
                return
            self._start_optional_enrichment()
            if self.pending_start_after_processing:
                self.pending_start_after_processing = False
                self.performSelector_withObject_afterDelay_("finishPendingStart:", None, 0.0)
            return
        paths = sorted(self.pending_media, key=lambda path: str(path).casefold())
        self.pending_media.clear()
        self.processing_active = True
        self.cancel_event.clear()
        generation = self.processing_generation = self.processing_generation + 1
        self._journal("Metadata", f"Preparing {len(paths)} media file(s).")
        self._set_progress("Metadata", 0, len(paths))
        def phase_progress(phase, current, total, name=""):
            if generation != self.processing_generation:
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyProgress:", (phase, current, total, name), False
            )
        def progress(current, total, name):
            phase_progress("Extracting coordinates", current, total, name)
        parameters = dict(self.parameters)
        def prepare_core_metadata():
            summary_path = None
            if self.project_directory is not None:
                candidate = (
                    self.project_directory
                    / "trackimages"
                    / f"{filename_base(self.adventure_name)}-summary.json"
                )
                if candidate.is_file():
                    summary_path = candidate
            prepared = prepare_media_records(
                paths,
                refresh_changed=True,
                tracks_summary_path=summary_path,
                place_equivalence_m=float(
                    parameters.get("locations.reuse_radius_m", 150.0)
                ),
                progress_callback=progress,
                detail_callback=lambda message: self._journal("Metadata", message),
                cancel_event=self.cancel_event,
            )
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "offerPendingRouteTiming:",
                (paths, prepared),
                False,
            )
            return prepared
        future = self.executor.submit(prepare_core_metadata)
        def poll(_timer):
            if not future.done():
                return
            _timer.invalidate()
            self.processing_active = False
            try:
                prepared = future.result()
            except Exception as exc:
                self._journal("Metadata", f"Failed: {exc}")
                self._clear_progress()
                return
            for item in prepared:
                if item.record is None:
                    self._journal("Metadata", f"Skipped {item.path.name}: {item.error or item.action}")
                else:
                    self._journal("Metadata", f"{item.action.title()} {item.path.name}")
            self._queue_optional_enrichment(
                [item.path for item in prepared if item.record is not None],
                start=False,
            )
            self.auto_processed_paths.update(paths)
            self._scan_project_media()
            self._start_next_media_batch()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.1, _BlockTimer.alloc().initWithBlock_(poll), "fire:", None, True)

    def offerPendingRouteTiming_(self, payload):
        """Offer route timing as soon as core media metadata is available."""
        paths, _prepared = payload
        timing_paths = self.pending_route_timing_media.intersection(paths)
        timing_track = next(
            (
                track
                for track in self.gpx_controller.tracks
                if track.nr == self.pending_route_timing_track_nr
            ),
            None,
        )
        if not timing_paths or timing_track is None:
            return
        self.pending_route_timing_media.difference_update(timing_paths)
        project_media = self._project_media_paths()
        self._journal(
            "Route Timing",
            f"The new media metadata is ready. Checking all {len(project_media)} "
            f"project media file(s) against track #{timing_track.nr}.",
        )
        self._prepare_route_timing_offer(
            timing_track.nr,
            project_media,
            confirmation_required=False,
        )

    @objc.python_method
    def _queue_optional_enrichment(self, paths, *, start=True):
        candidates = [
            Path(path).expanduser().resolve(strict=False)
            for path in paths
            if Path(path).is_file()
            and Path(path).expanduser().resolve(strict=False)
            not in self.enrichment_attempted_paths
        ]
        if not candidates:
            return
        self.enrichment_queue.add(candidates)
        if start:
            self._start_optional_enrichment()

    @objc.python_method
    def _reset_optional_enrichment(self):
        self.enrichment_cancel_event.set()
        self.enrichment_generation += 1
        self.enrichment_active = False
        self.enrichment_paused_by_cancel = False
        self.enrichment_queue.clear()
        self.enrichment_weather_attempted.clear()
        self.enrichment_attempted_paths.clear()
        self.control_assets_pending = False
        self._clear_optional_enrichment_progress()

    @objc.python_method
    def _optional_enrichment_visible_on_map(self):
        return bool(
            self.parameters.get(
                "locations.show_background_enrichment_progress",
                False,
            )
        )

    @objc.python_method
    def _set_optional_enrichment_progress(self, phase, current, total):
        if self._optional_enrichment_visible_on_map():
            self._set_background_progress("enrichment", phase, current, total)
        else:
            self._clear_background_progress("enrichment")
        controller = self.full_gui_controller
        if controller is not None:
            controller.setOptionalEnrichmentProgress_(
                (str(phase), int(current), int(total))
            )

    @objc.IBAction
    def applyOptionalEnrichmentProgress_(self, payload):
        phase, current, total = payload
        self._set_optional_enrichment_progress(phase, current, total)

    @objc.python_method
    def _clear_optional_enrichment_progress(self):
        self._clear_background_progress("enrichment")
        controller = self.full_gui_controller
        if controller is not None:
            controller.clearOptionalEnrichmentProgress_(None)

    @objc.python_method
    def _place_name_is_current(self, path):
        from GetGeoLocations import load_record_from_json, record_place_matches_gps

        resolved = Path(path).expanduser().resolve(strict=False)
        record = load_record_from_json(media_sidecar_path(resolved), resolved)
        return bool(
            record is not None
            and record_place_matches_gps(
                record,
                float(self.parameters.get("locations.reuse_radius_m", 150.0)),
            )
        )

    @objc.python_method
    def _start_optional_enrichment(self):
        if (
            self.enrichment_active
            or self.enrichment_paused_by_cancel
            or not self.enrichment_queue
            or self.processing_active
            or self.pending_media
            or self.control_assets_pending
            or (
                self.derived_track_data_future is not None
                and not self.derived_track_data_future.done()
            )
        ):
            return
        parameters = dict(self.parameters)
        add_places = bool(parameters.get("locations.add_place_names", True))
        add_weather = bool(parameters.get("weather.enabled", False))
        if not add_places and not add_weather:
            self.enrichment_queue.clear()
            return
        self.enrichment_active = True
        self.enrichment_cancel_event.clear()
        generation = self.enrichment_generation = self.enrichment_generation + 1
        initial_total = len(self.enrichment_queue)
        self._journal(
            "Background Enrichment",
            f"Queued {initial_total} media file(s); maps and playback need not wait.",
        )

        def report(phase, current, total):
            if generation != self.enrichment_generation:
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyOptionalEnrichmentProgress_",
                (phase, current, total),
                False,
            )

        def work():
            from GetGeoLocations import DeferredPlaceNameSession
            from historical_weather import enrich_media_weather

            session = DeferredPlaceNameSession(
                self._project_media_paths(),
                float(parameters.get("locations.reuse_radius_m", 150.0)),
            )
            pacing = AdaptiveGeocodePacing(
                float(parameters.get("locations.pacing_min_seconds", 1.0)),
                float(parameters.get("locations.pacing_max_seconds", 5.0)),
            )
            completed = 0
            while self.enrichment_queue and not self.enrichment_cancel_event.is_set():
                ordered = self.enrichment_queue.ordered()
                if not ordered:
                    break
                if add_weather:
                    weather_batch = [
                        path for path in ordered[:100]
                        if path not in self.enrichment_weather_attempted
                    ]
                    if weather_batch:
                        report("Historical weather", completed, initial_total)
                        try:
                            weather_report = enrich_media_weather(
                                weather_batch,
                                options=self._historical_weather_options_from(parameters),
                                detail_callback=lambda message: self._journal(
                                    "Historical Weather", message
                                ),
                                cancel_event=self.enrichment_cancel_event,
                            )
                            self._journal(
                                "Historical Weather",
                                f"Background batch: {weather_report.updated} downloaded, "
                                f"{weather_report.reused} reused, "
                                f"{weather_report.current} already current.",
                            )
                        except Exception as exc:
                            self._journal("Historical Weather", f"Background batch failed: {exc}")
                        finally:
                            self.enrichment_weather_attempted.update(weather_batch)
                path = self.enrichment_queue.next()
                if path is None:
                    break
                if add_places and not self.enrichment_cancel_event.is_set():
                    report(f"Place names: {path.name}", completed, initial_total)
                    try:
                        outcome, requested = session.enrich(
                            path,
                            timeout_seconds=float(
                                parameters.get("locations.timeout_seconds", 10.0)
                            ),
                        )
                        self._journal("Place Names", f"{path.name}: {outcome}.")
                        if requested:
                            ready_ahead = self.enrichment_queue.ready_ahead(
                                self._place_name_is_current,
                                LOOKAHEAD_MEDIA_COUNT,
                            )
                            minimum, maximum = pacing.range_for(
                                playback_active=self.enrichment_queue.playback_active,
                                ready_ahead=ready_ahead,
                            )
                            delay = random.uniform(minimum, maximum)
                            if self.enrichment_cancel_event.wait(delay):
                                break
                    except Exception as exc:
                        self._journal("Place Names", f"{path.name}: failed: {exc}")
                self.enrichment_attempted_paths.add(path)
                self.enrichment_queue.discard(path)
                completed += 1
                report("Background enrichment", completed, max(initial_total, completed))
            return completed

        future = self.executor.submit(work)

        def poll(timer):
            if not future.done():
                return
            timer.invalidate()
            if generation != self.enrichment_generation:
                return
            self.enrichment_active = False
            self._clear_optional_enrichment_progress()
            try:
                completed = future.result()
                self._journal(
                    "Background Enrichment",
                    f"Finished optional enrichment for {completed} media file(s).",
                )
            except Exception as exc:
                self._journal("Background Enrichment", f"Failed: {exc}")
            if self.enrichment_queue:
                self._start_optional_enrichment()

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1,
            _BlockTimer.alloc().initWithBlock_(poll),
            "fire:",
            None,
            True,
        )

    @objc.python_method
    def _historical_weather_options_from(self, parameters):
        from historical_weather import WeatherOptions
        from map_provider_utils import read_provider_credential

        access = str(parameters.get("weather.access", "free")).casefold()
        credential = str(parameters.get("weather.credential_id", "default"))
        return WeatherOptions(
            access="customer" if access == "customer" else "free",
            api_key=(
                read_provider_credential("open-meteo", credential)
                if access == "customer"
                else ""
            ),
            timeout_seconds=float(parameters.get("weather.timeout_seconds", 20.0)),
            minimum_request_interval_seconds=0.2 if access == "customer" else 1.0,
        )

    @objc.python_method
    def untimed_prompt_suppressed_identities(self):
        return set(self.untimed_prompt_suppressions)

    @objc.python_method
    def remember_untimed_prompt_suppressions(self, identities):
        before = set(self.untimed_prompt_suppressions)
        self.untimed_prompt_suppressions.update(
            str(value) for value in identities if str(value).strip()
        )
        if self.untimed_prompt_suppressions != before:
            if isinstance(getattr(self, "adventure_payload", None), dict):
                self.adventure_payload[
                    ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD
                ] = sorted(self.untimed_prompt_suppressions)
            self.adventureComponentDidChange_("adventure")

    @objc.IBAction
    def applyProgress_(self, value):
        phase, current, total, name = value
        self._set_progress(f"{phase}: {Path(str(name)).name}", current, total)

    @objc.python_method
    def _set_progress(self, phase, current, total):
        self.processing_phase = str(phase)
        self.processing_current = int(current)
        self.processing_total = int(total)
        self.background_progress["foreground"] = (
            self.processing_phase, self.processing_current, self.processing_total
        )
        self._refresh_progress_indicator()

    @objc.python_method
    def _clear_progress(self):
        self.processing_active = False
        self.background_progress.pop("foreground", None)
        self._refresh_progress_indicator()

    @objc.python_method
    def _set_background_progress(self, key, phase, current, total):
        self.background_progress[str(key)] = (str(phase), int(current), int(total))
        self._refresh_progress_indicator()

    @objc.python_method
    def _clear_background_progress(self, key):
        self.background_progress.pop(str(key), None)
        self._refresh_progress_indicator()

    @objc.python_method
    def _refresh_progress_indicator(self):
        if hasattr(self, "progress_indicator"):
            if self.background_progress:
                phase, current, total = next(reversed(self.background_progress.values()))
                self.progress_indicator.setProgress_current_total_(True, current, total)
                self.progress_label.setStringValue_(
                    f"{phase} {current}/{total}" if total else phase
                )
            else:
                self.progress_indicator.setProgress_current_total_(False, 0, 0)
                self.progress_label.setStringValue_("")

    @objc.python_method
    def _journal(self, phase, message):
        self.journal.append(message, phase=phase)

    @objc.python_method
    def _guidance_title(self):
        if self.project_directory is None and self.adventure_file is None:
            return "Drop an Adventure, folder, GPX tracks, media, or audio here"
        selected = set(self.gpx_controller.selected_nrs)
        track = next(
            (item for item in self.gpx_controller.tracks if item.nr in selected),
            None,
        )
        return track.name if track is not None else self.adventure_name

    @objc.python_method
    def _selected_track_and_media_identities(self):
        if len(self.gpx_controller.selected_nrs) != 1:
            return None, set()
        track = next(
            (
                candidate
                for candidate in self.gpx_controller.tracks
                if candidate.nr == self.gpx_controller.selected_nrs[0]
            ),
            None,
        )
        if track is None:
            return None, set()
        from gpx_processing import (
            processed_track_data_fingerprint,
            processed_track_geometry_fingerprint,
            semantic_track_fingerprint,
        )

        processed = self.gpx_controller.display_processed_track(track)
        cache_key = (track.nr, id(processed), track.processed_fingerprint)
        identities = self.track_media_identity_cache.get(cache_key)
        if identities is None:
            identities = {
                str(track.nr),
                semantic_track_fingerprint(track.element),
                processed_track_geometry_fingerprint(processed),
                processed_track_data_fingerprint(processed),
            }
            self.track_media_identity_cache = {cache_key: identities}
        return track, identities

    @objc.python_method
    def _viewer_selected_media(self, path):
        self.selected_media_path = Path(path).resolve(strict=False)
        self.selected_media_paths = {self.selected_media_path}
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def _draw_guidance_overlay(self, view, bounds):
        if view.show_help:
            return
        info_visible = bool(
            view.show_info
            and (view.cursor is not None or view.context_overlay_coordinate is not None)
        )
        show_help_hint = bool(
            view.transient_help_until
            and time.monotonic() < view.transient_help_until
        )
        rows = []
        if not info_visible:
            rows.append((self._guidance_title(), 18.0))
        if show_help_hint:
            rows.append(("Press h for help", 13.0))
        if self.missing_gpx_reference:
            rows.append((f"GPX file missing: {self.missing_gpx_reference}", 13.0))
        if not rows:
            return
        available_width = max(
            240.0,
            bounds.size.width - (230.0 if self.background_progress else 32.0),
        )
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.blackColor().colorWithAlphaComponent_(0.9))
        shadow.setShadowOffset_(NSMakeSize(1.0, -1.0))
        shadow.setShadowBlurRadius_(3.0)
        for row, (text, initial_size) in enumerate(rows):
            font_size = initial_size
            while font_size > 11.0:
                attrs = {
                    NSFontAttributeName: NSFont.boldSystemFontOfSize_(font_size),
                    NSForegroundColorAttributeName: NSColor.whiteColor(),
                    NSShadowAttributeName: shadow,
                }
                size = NSString.stringWithString_(text).sizeWithAttributes_(attrs)
                if size.width <= available_width:
                    break
                font_size -= 1.0
            drawing_text = NSString.stringWithString_(text)
            size = drawing_text.sizeWithAttributes_(attrs)
            x = max(16.0, (available_width - size.width) / 2.0)
            y = bounds.size.height - 34.0 - row * 22.0
            drawing_text.drawAtPoint_withAttributes_(NSMakePoint(x, y), attrs)

    def drawWorkspaceOverlay_inView_bounds_(self, _sender, view, bounds):
        self._draw_guidance_overlay(view, bounds)
        if not self.show_media_icons or not self.media_items:
            self.media_clusters = []
            self.media_hit_targets = []
            return
        metadata = view.plot_info.get("metadata") or {}
        extent = metadata.get("extent_mercator") or {}
        if not all(key in extent for key in ("min_x", "max_x", "min_y", "max_y")):
            return
        width = max(float(extent["max_x"]) - float(extent["min_x"]), 1.0)
        height = max(float(extent["max_y"]) - float(extent["min_y"]), 1.0)
        map_rect = view.interactive_map_rect(bounds) if view.interactive_tiles_active else bounds
        def project(item):
            x, y = lonlat_to_web_mercator(item.longitude, item.latitude)
            return (
                map_rect.origin.x + (x - float(extent["min_x"])) / width * map_rect.size.width,
                map_rect.origin.y + (y - float(extent["min_y"])) / height * map_rect.size.height,
            )
        projected = []
        for item in self.media_items:
            if item.latitude is None or item.longitude is None:
                continue
            x, y = project(item)
            if (
                map_rect.origin.x - 48 <= x <= map_rect.origin.x + map_rect.size.width + 48
                and map_rect.origin.y - 48 <= y <= map_rect.origin.y + map_rect.size.height + 48
            ):
                projected.append((item, x, y))
        self.media_clusters = cluster_projected_media(projected, cell_size=36.0)
        self.media_hit_targets = []
        selected_track, selected_identities = self._selected_track_and_media_identities()
        selected_track_prominent = self.map_state == "track"
        if selected_track is not None and not selected_track_prominent:
            track_entries = view.plot_info.get("tracks") or {}
            track_entry = track_entries.get(selected_track.nr) or track_entries.get(str(selected_track.nr)) or {}
            track_extent = (track_entry.get("metadata") or {}).get("extent_mercator") or {}
            if not track_extent:
                track_extent = self.gpx_controller.extent_for_track_records([selected_track]) or {}
            selected_track_prominent = track_extent_is_prominent(track_extent, extent)

        def belongs_to_selected_track(cluster):
            if selected_track is None:
                return False
            return media_cluster_belongs_to_track(
                cluster,
                selected_identities,
                focused_track_view=selected_track_prominent,
            )

        selected_clusters = [
            cluster
            for cluster in self.media_clusters
            if belongs_to_selected_track(cluster)
        ]
        projected_track_length = 0.0
        if selected_track is not None:
            processed = self.gpx_controller.display_processed_track(selected_track)
            pixels_per_meter = min(map_rect.size.width / width, map_rect.size.height / height)
            projected_track_length = processed.length_km * 1000.0 * pixels_per_meter
        show_thumbnails = should_expand_media_thumbnails(
            selected_clusters,
            projected_track_length=projected_track_length,
            thumbnail_size=72.0,
        )
        attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(11.0),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        ordered_clusters = sorted(
            self.media_clusters,
            key=lambda cluster: (
                bool(resolved_media_paths(cluster.items) & self.selected_media_paths),
                any(
                    self.selected_media_path == item.path.resolve(strict=False)
                    for item in cluster.items
                ),
            ),
        )
        for cluster in ordered_clusters:
            cluster_paths = resolved_media_paths(cluster.items)
            cluster_selected = bool(cluster_paths & self.selected_media_paths)
            selected_item = next(
                (
                    item
                    for item in cluster.items
                    if self.selected_media_path == item.path.resolve(strict=False)
                ),
                None,
            )
            belongs_to_selected = belongs_to_selected_track(cluster)
            draw_thumbnail = show_thumbnails and belongs_to_selected
            if draw_thumbnail:
                projected_items = []
                for item in cluster.items:
                    item_x, item_y = project(item)
                    projected_items.append((item, item_x, item_y))
                projected_items.sort(
                    key=lambda value: self.selected_media_path == value[0].path.resolve(strict=False)
                )
                drew_thumbnail = False
                for item, item_x, item_y in projected_items:
                    image = view.request_photo_thumbnail(item.path)
                    if image is None:
                        continue
                    image_size = image.size()
                    scale = min(
                        72.0 / max(image_size.width, 1.0),
                        72.0 / max(image_size.height, 1.0),
                    )
                    image_width = image_size.width * scale
                    image_height = image_size.height * scale
                    left = max(2.0, min(bounds.size.width - image_width - 2.0, item_x - image_width / 2.0))
                    bottom = max(2.0, min(bounds.size.height - image_height - 2.0, item_y + 12.0))
                    frame = NSMakeRect(left - 3.0, bottom - 3.0, image_width + 6.0, image_height + 6.0)
                    item_path = item.path.resolve(strict=False)
                    active = self.selected_media_path == item_path
                    (NSColor.systemBlueColor() if active else NSColor.whiteColor()).setFill()
                    NSBezierPath.fillRect_(frame)
                    image.drawInRect_fromRect_operation_fraction_(
                        NSMakeRect(left, bottom, image_width, image_height),
                        NSZeroRect,
                        NSCompositingOperationSourceOver,
                        1.0,
                    )
                    if item_path in self.selected_media_paths:
                        NSColor.systemRedColor().setStroke()
                        outline = NSBezierPath.bezierPathWithRect_(frame)
                        outline.setLineWidth_(3.0)
                        outline.stroke()
                    self.media_hit_targets.append((frame, cluster, True, item.path))
                    drew_thumbnail = True
                if drew_thumbnail:
                    continue
            dim = bool(selected_identities and not belongs_to_selected)
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.48, 0.95, 0.35 if dim else 0.9)
            color.setFill()
            radius = 15.0 if selected_item is not None else 13.0
            rect = NSMakeRect(cluster.x - radius, cluster.y - radius, radius * 2.0, radius * 2.0)
            NSBezierPath.bezierPathWithOvalInRect_(rect).fill()
            if cluster_selected:
                NSColor.systemRedColor().setStroke()
                outline = NSBezierPath.bezierPathWithOvalInRect_(rect)
                outline.setLineWidth_(3.0)
                outline.stroke()
            text = str(cluster.count)
            drawing_text = NSString.stringWithString_(text)
            size = drawing_text.sizeWithAttributes_(attrs)
            drawing_text.drawAtPoint_withAttributes_(NSMakePoint(cluster.x - size.width / 2, cluster.y - size.height / 2), attrs)
            self.media_hit_targets.append((rect, cluster, False, None))
        self._draw_media_selection_rectangle()

    @objc.python_method
    def _draw_media_selection_rectangle(self):
        if self.media_selection_origin is None or self.media_selection_current is None:
            return
        left, bottom, right, top = normalized_screen_rectangle(
            self.media_selection_origin,
            self.media_selection_current,
        )
        NSColor.blackColor().setStroke()
        outline = NSBezierPath.bezierPathWithRect_(
            NSMakeRect(left, bottom, right - left, top - bottom)
        )
        outline.setLineWidth_(2.0)
        outline.stroke()

    @objc.python_method
    def workspace_media_target_at_location(self, location):
        for rect, cluster, is_thumbnail, clicked_path in reversed(self.media_hit_targets):
            if (
                rect.origin.x <= location.x <= rect.origin.x + rect.size.width
                and rect.origin.y <= location.y <= rect.origin.y + rect.size.height
            ):
                return cluster, is_thumbnail, clicked_path
        return None

    @objc.python_method
    def _paths_for_media_target(self, target):
        cluster, _is_thumbnail, clicked_path = target
        if clicked_path is not None:
            return {Path(clicked_path).resolve(strict=False)}
        return resolved_media_paths(cluster.items)

    @objc.python_method
    def _select_media_target(self, target, *, additive=False):
        paths = self._paths_for_media_target(target)
        self.selected_media_paths = update_media_selection(
            self.selected_media_paths,
            paths,
            additive=additive,
        )
        if paths:
            self.selected_media_path = min(paths, key=lambda path: str(path).casefold())
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def beginWorkspaceMediaSelectionAt_modifierFlags_(self, location, _modifier_flags):
        self.media_selection_origin = (float(location.x), float(location.y))
        self.media_selection_current = self.media_selection_origin
        self.media_selection_base = set(self.selected_media_paths)
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def updateWorkspaceMediaSelectionTo_(self, location):
        self.media_selection_current = (float(location.x), float(location.y))
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def finishWorkspaceMediaSelectionAt_(self, location):
        self.updateWorkspaceMediaSelectionTo_(location)
        selection_rect = normalized_screen_rectangle(
            self.media_selection_origin,
            self.media_selection_current,
        )
        dragged = (selection_rect[2] - selection_rect[0] > 5.0) or (
            selection_rect[3] - selection_rect[1] > 5.0
        )
        selected = set(self.media_selection_base) if dragged else set()
        if dragged:
            for rect, cluster, _is_thumbnail, clicked_path in self.media_hit_targets:
                target_rect = (
                    float(rect.origin.x),
                    float(rect.origin.y),
                    float(rect.origin.x + rect.size.width),
                    float(rect.origin.y + rect.size.height),
                )
                if screen_rectangles_intersect(selection_rect, target_rect):
                    if clicked_path is not None:
                        selected.add(Path(clicked_path).resolve(strict=False))
                    else:
                        selected.update(resolved_media_paths(cluster.items))
        self.selected_media_paths = selected
        self.selected_media_path = (
            min(selected, key=lambda path: str(path).casefold()) if selected else None
        )
        self.media_selection_origin = None
        self.media_selection_current = None
        self.media_selection_base = set()
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def cancelWorkspaceMediaSelection_(self, _sender):
        self.media_selection_origin = None
        self.media_selection_current = None
        self.media_selection_base = set()
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)

    @objc.python_method
    def _show_adventure_media(self, selected_path):
        control_names = [
            str(row.get("name", ""))
            for row in self.control_rows
            if str(row.get("type", "")).upper() in {"IMG", "VID"}
        ]
        paths = ordered_media_viewer_paths(
            self.media_items,
            control_names,
            project_directory=self.project_directory,
        )
        selected = Path(selected_path).resolve(strict=False)
        if selected not in paths:
            paths.append(selected)
        if self.media_viewer is None:
            from cocoa_media_viewer import CocoaMediaViewer
            self.media_viewer = CocoaMediaViewer.alloc().init()
        return self.media_viewer.show_paths(
            paths,
            paths.index(selected),
            selection_callback=self._viewer_selected_media,
        )

    def workspaceMapClicked_clickCount_(self, view, click_count):
        self.workspaceMapClicked_clickCount_modifierFlags_(view, click_count, 0)

    @objc.python_method
    def workspaceMapClicked_clickCount_modifierFlags_(self, view, click_count, modifier_flags):
        location = getattr(view, "last_gesture_location", None)
        media_target = getattr(view, "clicked_workspace_media_target", None)
        if media_target is None and location is not None:
            media_target = self.workspace_media_target_at_location(location)
        if media_target is not None:
            self._select_media_target(
                media_target,
                additive=bool(int(modifier_flags) & NSEventModifierFlagCommand),
            )
            cluster, _is_thumbnail, clicked_path = media_target
            selected_path = Path(clicked_path).resolve(strict=False) if clicked_path is not None else self.selected_media_path
            if click_count >= 2 and selected_path is not None:
                self._show_adventure_media(selected_path)
            return
        linked_target = getattr(view, "clicked_media_target", None)
        if linked_target is not None:
            track, point_index, path = linked_target
            view.active_media_path = path.resolve(strict=False)
            self.selected_media_path = path.resolve(strict=False)
            self.selected_media_paths = {self.selected_media_path}
            view.setNeedsDisplay_(True)
            if click_count >= 2:
                self._show_adventure_media(path)
            return
        self.selected_media_paths.clear()
        self.selected_media_path = None
        view.setNeedsDisplay_(True)
        cursor = view.cursor
        cursor_distance = math.inf
        if cursor is not None and location is not None:
            transformer = view._metadata_transformer([cursor[1]], view.bounds())
            if transformer is not None:
                cursor_x, cursor_y = transformer(cursor[1])
                cursor_distance = math.hypot(cursor_x - location.x, cursor_y - location.y)
        if click_count >= 2 and cursor_distance > 28.0:
            self.show_workspace_overview()
            return
        if cursor is None:
            if click_count >= 2:
                self.show_workspace_overview()
            return
        track = cursor[2]
        self.gpx_controller.selected_nrs = [track.nr]
        self.gpx_controller.update_selection_field()
        self.gpx_controller.highlight_selected_rows()
        self.selected_track_identity = str(track.nr)
        if click_count >= 2:
            self.openTrackEditor_(None)
            return
        view.setNeedsDisplay_(True)

    @objc.python_method
    def workspaceMediaContextMenuForTarget_(self, target):
        target_paths = self._paths_for_media_target(target)
        if not (target_paths & self.selected_media_paths):
            self.selected_media_paths = set(target_paths)
            self.selected_media_path = (
                min(target_paths, key=lambda path: str(path).casefold())
                if target_paths
                else None
            )
            if self.plot_view is not None:
                self.plot_view.setNeedsDisplay_(True)
        _cluster, _is_thumbnail, clicked_path = target
        anchor_candidates = self._paths_for_media_target(target)
        self.control_file_context_anchor_path = (
            Path(clicked_path).resolve(strict=False)
            if clicked_path is not None
            else min(anchor_candidates, key=lambda path: str(path).casefold())
            if anchor_candidates
            else None
        )
        menu = NSMenu.alloc().initWithTitle_("Media")
        for title, action in (
            ("Show in Control File", "showMediaInControlFile:"),
            ("Zoom to Selected Media", "zoomToSelectedMedia:"),
            ("Create Track from Selected Media", "createTrackFromSelectedMedia:"),
        ):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self)
            item.setEnabled_(bool(self.selected_media_paths))
            menu.addItem_(item)
        menu.addItem_(NSMenuItem.separatorItem())
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Hide All Media Icons", "toggleWorkspaceMediaIcons:", ""
        )
        item.setTarget_(self)
        menu.addItem_(item)
        return menu

    @objc.IBAction
    def toggleWorkspaceMediaIcons_(self, _sender):
        self.show_media_icons = not self.show_media_icons
        if self.plot_view is not None:
            self.plot_view.show_media_markers = self.show_media_icons
            if not self.show_media_icons:
                self.media_clusters = []
                self.media_hit_targets = []
                self.plot_view.photo_thumbnail_hit_targets = []
            self.plot_view.setNeedsDisplay_(True)
        self.gpx_controller.set_status(
            "Showing all media icons."
            if self.show_media_icons
            else "All media icons are hidden."
        )

    @objc.IBAction
    def showMediaInControlFile_(self, _sender):
        if self.control_file is None or not self.control_file.is_file():
            self._alert("No control file yet.", "Create or choose a control file first.")
            return
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            return
        controller.editControlFile_(None)
        controller.show_control_file_media_selection(
            self.selected_media_paths,
            self.control_file_context_anchor_path,
        )

    @objc.IBAction
    def zoomToSelectedMedia_(self, _sender):
        items = [
            item
            for item in self.media_items
            if item.path.resolve(strict=False) in self.selected_media_paths
            and item.latitude is not None
            and item.longitude is not None
        ]
        if not items or self.plot_view is None:
            return
        projected = [lonlat_to_web_mercator(item.longitude, item.latitude) for item in items]
        xs = [point[0] for point in projected]
        ys = [point[1] for point in projected]
        if len(projected) == 1:
            span = (2.0 * math.pi * 6378137.0) / (2 ** max(0, self.gpx_controller.track_zoom)) * 3.0
            extent = {
                "min_x": xs[0] - span / 2.0,
                "max_x": xs[0] + span / 2.0,
                "min_y": ys[0] - span / 2.0,
                "max_y": ys[0] + span / 2.0,
            }
        else:
            span_x = max(max(xs) - min(xs), 100.0)
            span_y = max(max(ys) - min(ys), 100.0)
            extent = {
                "min_x": min(xs) - span_x * 0.15,
                "max_x": max(xs) + span_x * 0.15,
                "min_y": min(ys) - span_y * 0.15,
                "max_y": max(ys) + span_y * 0.15,
            }
        extent = self.gpx_controller.fit_extent_to_aspect(extent, (1920, 1080))
        self.plot_view.render_extent(
            extent,
            status=f"Fitted {len(items)} selected media item(s) to the map.",
        )

    @objc.IBAction
    def createTrackFromSelectedMedia_(self, _sender):
        paths = [
            item.path
            for item in self.media_items
            if item.path.resolve(strict=False) in self.selected_media_paths
        ]
        if not paths:
            return
        self.gpx_controller.media_context["_pending_media_paths"] = [str(path) for path in paths]
        self.gpx_controller.media_context["_skip_media_selection_once"] = True
        self.gpx_controller.media_context["_force_new_media_track_once"] = True
        self.gpx_controller.addTracksFromPhotos_(None)

    @objc.python_method
    def workspaceTrackSelectedForEditing_(self, track):
        """Synchronize map-first selection without changing the viewport."""
        self.selected_track_identity = str(track.nr)
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)
            self.gpx_controller.refresh_elevation_profile_for_plot_view(self.plot_view)

    @objc.python_method
    def workspace_tracks_deleted(self, deleted_track_numbers):
        """Discard deleted-track views and refit the remaining Adventure."""
        deleted = {int(value) for value in deleted_track_numbers}
        for number in deleted:
            self.track_viewports.pop(number, None)
        self.track_media_identity_cache.clear()
        self.overview_viewport = None
        self.focused_track_nr = None
        self.selected_track_identity = ""
        if self.plot_view is not None:
            self.show_workspace_overview(remember_current=False)

    @objc.python_method
    def _ordered_workspace_media_icon_paths(self):
        icon_media = [
            item
            for item in self.media_items
            if item.latitude is not None and item.longitude is not None
        ]
        control_names = [
            str(row.get("name", ""))
            for row in self.control_rows
            if str(row.get("type", "")).upper() in {"IMG", "VID"}
        ]
        return ordered_media_viewer_paths(
            icon_media,
            control_names,
            project_directory=self.project_directory,
        )

    @objc.python_method
    def _pan_workspace_coordinate_into_view(self, latitude, longitude):
        view = self.plot_view
        if view is None:
            return False
        extent = view.current_extent()
        if not extent:
            return False
        point_x, point_y = lonlat_to_web_mercator(
            float(longitude), float(latitude)
        )
        next_extent = panned_extent_to_include_point(extent, point_x, point_y)
        if next_extent == extent:
            return False
        view.set_interactive_viewport(next_extent, view.current_tile_zoom())
        return True

    @objc.python_method
    def _navigate_workspace_media(self, direction):
        if not self.show_media_icons or self.selected_media_path is None:
            return False
        paths = self._ordered_workspace_media_icon_paths()
        if not paths:
            return False
        selected = Path(self.selected_media_path).resolve(strict=False)
        if selected in paths:
            index = (paths.index(selected) + int(direction)) % len(paths)
        else:
            index = 0 if int(direction) > 0 else len(paths) - 1
        selected = paths[index]
        self.selected_media_path = selected
        self.selected_media_paths = {selected}
        selected_item = next(
            (
                item
                for item in self.media_items
                if item.path.resolve(strict=False) == selected
            ),
            None,
        )
        if self.plot_view is not None:
            self.plot_view.active_media_path = selected
            self.plot_view.setNeedsDisplay_(True)
        if selected_item is not None:
            self._pan_workspace_coordinate_into_view(
                selected_item.latitude,
                selected_item.longitude,
            )
        self.gpx_controller.set_status(
            f"Selected media {index + 1} of {len(paths)}: {selected.name}."
        )
        return True

    @objc.python_method
    def _navigate_workspace_waypoint(self, direction):
        view = self.plot_view
        if view is None:
            return False
        cursor = view.cursor
        track = cursor[2] if cursor is not None else None
        visible_tracks = self.gpx_controller.visible_tracks()
        if track not in visible_tracks:
            selected = [
                candidate
                for candidate in self.gpx_controller.selected_tracks()
                if candidate in visible_tracks
            ]
            track = selected[0] if len(selected) == 1 else None
            cursor = None
        if track is None:
            return False
        points = track.points()
        if not points:
            return False
        if cursor is not None and cursor[2] is track:
            point_index = max(
                0,
                min(len(points) - 1, int(cursor[0]) + int(direction)),
            )
        else:
            point_index = 0 if int(direction) > 0 else len(points) - 1
        self.selected_media_path = None
        self.selected_media_paths.clear()
        view.active_media_path = None
        self.selected_track_identity = str(track.nr)
        self.gpx_controller.selected_nrs = [track.nr]
        self.gpx_controller.update_selection_field()
        self.gpx_controller.highlight_selected_rows()
        view.cursor = (point_index, points[point_index], track)
        view.context_overlay_coordinate = None
        view.show_info = not view.info_overlay_dismissed
        view.sync_inspector_selection()
        view.update_window_title()
        self.gpx_controller.refresh_elevation_profile_for_plot_view(view)
        view.setNeedsDisplay_(True)
        self._pan_workspace_coordinate_into_view(
            points[point_index].lat,
            points[point_index].lon,
        )
        self.gpx_controller.set_status(
            f"Selected point {point_index + 1} of {len(points)} in track #{track.nr}."
        )
        return True

    @objc.python_method
    def _navigate_workspace_track(self, direction):
        tracks = self.gpx_controller.visible_tracks()
        if not tracks:
            return False
        selected = self.gpx_controller.selected_tracks()
        current = selected[0] if len(selected) == 1 and selected[0] in tracks else None
        if current is None and self.focused_track_nr is not None:
            current = next(
                (track for track in tracks if track.nr == self.focused_track_nr),
                None,
            )
        if current is None:
            index = 0 if int(direction) > 0 else len(tracks) - 1
        else:
            index = (tracks.index(current) + int(direction)) % len(tracks)
        track = tracks[index]
        self.selected_media_path = None
        self.selected_media_paths.clear()
        self.focus_workspace_track(track, reset_viewport=True)
        self.gpx_controller.set_status(
            f"Selected track {index + 1} of {len(tracks)}: {track.name}."
        )
        return True

    @objc.python_method
    def _navigate_workspace_selection(self, direction):
        if self._navigate_workspace_media(direction):
            return True
        return self._navigate_workspace_waypoint(direction)

    def handleWorkspaceKeyEvent_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        flags = int(event.modifierFlags())
        command = bool(flags & NSEventModifierFlagCommand)
        option = bool(flags & NSEventModifierFlagOption)
        direction = overview_arrow_direction(key, int(event.keyCode()))
        if direction and not option:
            if command:
                return self._navigate_workspace_track(direction)
            return self._navigate_workspace_selection(direction)
        if command and key.casefold() == "g":
            self.openAdventureGUI_(None); return True
        if command and key.casefold() == "e":
            self.openTrackEditor_(None); return True
        if command and key.casefold() == "l":
            self.openControlFile_(None); return True
        if command and key.casefold() == "s":
            self.saveWorkspace_(None); return True
        if command and key == "0":
            self.show_workspace_overview(); return True
        if key == " ":
            self.startSlideShow_(None); return True
        if key.casefold() == "h":
            self.showHelp_(None); return True
        return False

    def workspaceMapWindowWillClose_(self, window):
        if window is not self.window:
            return
        other_visible_windows = [
            candidate
            for candidate in NSApp().windows()
            if candidate is not window and candidate.isVisible()
        ]
        if not other_visible_windows:
            NSApp().performSelector_withObject_afterDelay_("terminate:", None, 0.0)

    @objc.python_method
    def _stop_media_watcher(self):
        watcher, self.media_watcher = self.media_watcher, None
        if watcher is not None:
            watcher.stop()
        watcher, self.gpx_watcher = self.gpx_watcher, None
        if watcher is not None:
            watcher.stop()

    @objc.python_method
    def _start_media_watcher(self):
        if self.startup_track_loading:
            self.startup_watcher_deferred = True
            return
        self._stop_media_watcher()
        if self.project_directory is None or not self.project_directory.is_dir():
            return
        self.media_watch_seen_paths.clear()

        def discovered(paths, initial):
            payload = {
                "paths": [str(path) for path in paths],
                "initial": bool(initial),
            }
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "handleWatchedMedia:", payload, False
                )
            except Exception:
                pass

        self.media_watcher = ProjectMediaWatcher(
            self.project_directory,
            discovered,
            extensions=MEDIA_EXTENSIONS,
            excluded_directories=GENERATED_DIRECTORIES,
            stability_seconds=1.0,
            reconciliation_seconds=60.0,
        )
        self.media_watcher.start()

        def discovered_gpx(paths, initial):
            if initial:
                return
            payload = [str(path) for path in paths]
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "handleWatchedGPX:", payload, False
                )
            except Exception:
                pass

        self.gpx_watcher = ProjectMediaWatcher(
            self.project_directory,
            discovered_gpx,
            extensions={".gpx"},
            excluded_directories=GENERATED_DIRECTORIES,
            stability_seconds=1.0,
            reconciliation_seconds=60.0,
        )
        self.gpx_watcher.start()

    def handleWatchedGPX_(self, raw_paths):
        if self.adventure_file is None or not isinstance(self.adventure_payload, dict):
            return
        referenced = str(self.adventure_payload.get("gpx_file", "")).strip()
        if not referenced or (self.project_directory / referenced).is_file():
            return
        paths = [Path(value).resolve(strict=False) for value in raw_paths]
        if not paths:
            return
        recovery = recover_missing_adventure_gpx(
            self.adventure_file,
            self.adventure_payload,
            preferred_candidate=paths[0],
        )
        if recovery.action == "accepted":
            self.load_adventure_path(self.adventure_file)

    @objc.python_method
    def _control_membership_candidates(self, paths):
        paths = [Path(path).resolve(strict=False) for path in paths if Path(path).is_file()]
        if not paths:
            return []
        if self.control_file is None or not self.control_file.is_file():
            return paths
        inventory = load_control_media_inventory(self.control_file)
        included = [
            str(row.get("name", ""))
            for row in self.control_rows
            if str(row.get("type", "")).upper() in {"IMG", "VID"}
        ]
        memberships = classify_project_media(inventory, paths, included)
        return [
            item.media_path
            for item in memberships
            if item.state in {"new", "unclassified"}
        ]

    @objc.python_method
    def _metadata_creation_requires_weather_choice(self, paths):
        if self.weather_consent != "unasked":
            return False
        for path in paths:
            status, _payload, _reason = validate_media_sidecar(path, media_sidecar_path(path))
            if status in {"missing", "invalid"}:
                return True
        return False

    @objc.python_method
    def _save_weather_consent(self):
        if self.adventure_payload is None or self.adventure_file is None:
            return
        self.adventure_payload["weather_consent"] = self.weather_consent
        self.adventure_payload["parameters"] = parameter_payload(self.parameters)
        self.adventureComponentDidChange_("adventure")

    @objc.python_method
    def _request_initial_weather_choice(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Add historical weather to media?")
        alert.setInformativeText_(
            "Weather lookup sends media coordinates and exposure times to Open-Meteo. "
            "Free non-commercial access needs no account or API key. Customer access "
            "requires an Open-Meteo account, a suitable paid plan, and an API key. "
            "Open-Meteo attribution is required for either choice. "
            "This choice is remembered for the Adventure and can be changed in Settings."
        )
        alert.addButtonWithTitle_("Include Weather (Free)")
        alert.addButtonWithTitle_("Customer Account")
        alert.addButtonWithTitle_("No Weather")
        while True:
            response = int(alert.runModal())
            if response == 1000:
                self.weather_consent = "free"
                self.parameters["weather.enabled"] = True
                self.parameters["weather.access"] = "free"
                break
            if response == 1001:
                credential = str(self.parameters.get("weather.credential_id", "default"))
                if not _main_gui_module().request_open_meteo_api_key(credential):
                    continue
                self.weather_consent = "customer"
                self.parameters["weather.enabled"] = True
                self.parameters["weather.access"] = "customer"
                break
            self.weather_consent = "disabled"
            self.parameters["weather.enabled"] = False
            break
        self.gpx_controller.apply_project_parameters(self.parameters)
        self._save_weather_consent()
        return True

    def handleWatchedMedia_(self, payload):
        paths = [
            Path(value).resolve(strict=False)
            for value in payload.get("paths", [])
            if Path(value).is_file()
        ]
        unseen = [path for path in paths if path not in self.media_watch_seen_paths]
        self.media_watch_seen_paths.update(paths)
        if not unseen:
            return
        candidates = self._control_membership_candidates(unseen)
        if not candidates:
            if bool(payload.get("initial")):
                self._scan_project_media()
            return
        self.pending_control_media.update(candidates)
        self._journal(
            "Media Discovery",
            f"Found {len(candidates)} media file(s) not yet classified for the control file.",
        )
        self.queue_media_processing(candidates)

    @objc.python_method
    def _finish_detected_media_control_followup(self):
        paths = sorted(self.pending_control_media, key=lambda path: str(path).casefold())
        self.pending_control_media.clear()
        if not paths or self.project_directory is None:
            return
        if self.control_file is not None and self.control_file.is_file():
            controller = self._ensure_full_gui(show_window=False)
            if controller is not None:
                controller.reviewDiscoveredMediaPaths_(paths)
            self._start_optional_enrichment()
            return

        base = filename_base(self.adventure_name)
        self.control_file = self.project_directory / f"{base}-sorted.lst"
        if isinstance(self.adventure_payload, dict):
            self.adventure_payload["control_file"] = self.control_file.name
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            self.pending_control_media.update(paths)
            return
        controller.pending_imported_media_for_control_update = [str(path) for path in paths]
        controller.assistant_control_skip_maps = bool(self.demo_mode)
        controller.assistant_control_replace_without_confirmation = bool(self.demo_mode)
        self.control_assets_pending = True
        self._journal("Maps", "Preparing all required maps before creating the control file.")
        controller._start_assistant_control_assets_creation()

    @objc.python_method
    def controlAssetsCreationDidFinish_success_(self, success):
        """Receive completion from the shared Maps-to-Control-File pipeline."""
        self.control_assets_pending = False
        controller = self.full_gui_controller
        if not bool(success) or controller is None:
            self._journal(
                "Control File",
                "The control file was not created because required maps are unavailable.",
            )
            self._start_optional_enrichment()
            return
        control_path = controller._control_file_path()
        if control_path is None or not control_path.is_file():
            return
        self.control_file = Path(control_path).resolve(strict=False)
        self.temporary_control_model = False
        self._load_control_rows()
        self.saveWorkspace_(None)
        self._start_optional_enrichment()
        if self.demo_coordinator is not None:
            self.demo_coordinator.controlFileDidOpen()

    @objc.python_method
    def _new_adventure_choice_for_gpx_drop(self, gpx_paths):
        count = len(gpx_paths)
        suggested_name = suggested_adventure_name_for_gpx_drop(gpx_paths)
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Create an Adventure for the dropped GPX file?" if count == 1 else f"Create an Adventure for {count} dropped GPX files?")
        alert.setInformativeText_(
            "No Adventure is open. Give this journey a name, then choose or "
            "create the folder that will contain its tracks, media, maps, audio, "
            "and slide show. myCamino will import the GPX file and save the "
            "Adventure automatically."
        )
        name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 26))
        name_field.setStringValue_(suggested_name)
        name_field.setPlaceholderString_("Adventure name")
        alert.setAccessoryView_(name_field)
        alert.addButtonWithTitle_("Create New Adventure")
        alert.addButtonWithTitle_("Open Existing Adventure…")
        alert.addButtonWithTitle_("Cancel")
        response = int(alert.runModal())
        if response == 1001:
            return "existing", ""
        if response != 1000:
            return "cancel", ""
        name = filename_base(str(name_field.stringValue()).strip())
        if not name:
            self._alert("Enter an Adventure name.")
            return "cancel", ""
        return "create", name

    @objc.python_method
    def _open_existing_adventure_for_pending_drop(self, paths, source):
        selected = self._choose_adventure_or_project_path(source)
        if selected is None:
            return False
        if selected.is_dir() and not any(selected.glob("*.adv")):
            self._alert(
                "No Adventure was found in that folder.",
                "Choose an .adv file or a folder that already contains an Adventure.",
            )
            return False
        self.pending_workspace_drop_after_adventure_load = list(paths)
        if selected.is_dir():
            self.load_project_directory(selected)
        else:
            self.load_adventure_path(selected)
        if not self.startup_track_loading:
            self._dispatch_pending_workspace_drop()
        return True

    @objc.python_method
    def _dispatch_pending_workspace_drop(self):
        pending = list(self.pending_workspace_drop_after_adventure_load)
        self.pending_workspace_drop_after_adventure_load = []
        if pending:
            self.performSelector_withObject_afterDelay_(
                "performWorkspaceDrop:", [str(path) for path in pending], 0.0
            )

    def performWorkspaceDrop_(self, raw_paths):
        paths = [Path(str(path)).expanduser().resolve(strict=False) for path in raw_paths]
        adventures = [path for path in paths if path.suffix.casefold() == ".adv"]
        directories = [path for path in paths if path.is_dir()]
        if adventures:
            self.load_adventure_path(adventures[0]); return
        if directories and any(list(path.glob("*.adv")) for path in directories):
            self.load_project_directory(directories[0]); return
        gpx_paths = [path for path in paths if path.suffix.casefold() == ".gpx"]
        control_paths = [path for path in paths if path.suffix.casefold() == ".lst"]
        audio_paths = [path for path in paths if path.suffix.casefold() in AUDIO_EXTENSIONS]
        media_paths = self._expand_media_paths([path for path in paths if path not in gpx_paths + control_paths + audio_paths])
        if self.project_directory is None and gpx_paths:
            choice, adventure_name = self._new_adventure_choice_for_gpx_drop(gpx_paths)
            if choice == "cancel":
                return
            if choice == "existing":
                self._open_existing_adventure_for_pending_drop(
                    paths, gpx_paths[0]
                )
                return
            if not self._request_project_directory(
                gpx_paths[0],
                adventure_name=adventure_name,
                reject_existing_adventure=True,
            ):
                return
            self.pending_create_after_gpx_load = True
        elif self.project_directory is None and not self._request_project_directory(paths[0] if paths else None):
            return
        if gpx_paths:
            referenced_name = str((self.adventure_payload or {}).get("gpx_file", "")).strip()
            referenced_path = self.project_directory / referenced_name if referenced_name else None
            if (
                self.adventure_file is not None
                and referenced_name
                and referenced_path is not None
                and not referenced_path.is_file()
            ):
                recovery = recover_missing_adventure_gpx(
                    self.adventure_file,
                    self.adventure_payload,
                    preferred_candidate=gpx_paths[0],
                )
                if recovery.action == "accepted":
                    self.load_adventure_path(self.adventure_file)
                return
            if len(self.gpx_controller.tracks) == 1 and not self.gpx_controller.tracks[0].points():
                self.gpx_controller.tracks = []
                self.gpx_controller.next_nr = 1
            self.gpx_controller.load_gpx_paths(
                gpx_paths,
                mark_dirty=True,
                offer_sort_after_drop=True,
            )
            if self.demo_coordinator is not None:
                # Advance the guide immediately.  Demo persistence uses the
                # shared delayed autosave and must not hold the Finder step
                # behind GPX metrics/cache writing.
                self.demo_coordinator.workspaceDidDropGPX(gpx_paths)
        if control_paths:
            self.control_file = control_paths[0]
            self.temporary_control_model = False
            self._load_control_rows()
            try:
                media_paths.extend(media_paths_from_control_file(self.control_file, MEDIA_EXTENSIONS))
            except OSError as exc:
                self._alert("Could not read control file.", str(exc))
        if audio_paths:
            self._import_audio(audio_paths)
        if media_paths:
            self._handle_media_drop(media_paths)

    @objc.python_method
    def _expand_media_paths(self, paths):
        result = []
        for path in paths:
            candidates = path.rglob("*") if path.is_dir() else [path]
            result.extend(
                item.resolve(strict=False)
                for item in candidates
                if item.is_file() and item.suffix.casefold() in MEDIA_EXTENSIONS
                and not any(part.casefold() in GENERATED_DIRECTORIES for part in item.parts)
            )
        return list(dict.fromkeys(result))

    @objc.python_method
    def _request_project_directory(
        self,
        source,
        *,
        adventure_name=None,
        reject_existing_adventure=False,
    ):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setCanCreateDirectories_(True)
        panel.setTitle_("Choose or Create the Adventure Folder")
        panel.setMessage_(
            "This folder will contain the Adventure file, GPX tracks, media, maps, "
            "audio, and control file. Select an existing folder or use New Folder."
        )
        panel.setPrompt_("Use This Folder")
        if source is not None:
            directory = source if source.is_dir() else source.parent
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(directory)))
        if int(panel.runModal()) != 1:
            return False
        selected_directory = Path(str(panel.URL().path())).resolve()
        selected_name = filename_base(
            str(adventure_name or selected_directory.name)
        )
        if reject_existing_adventure and (
            selected_directory / f"{selected_name}.adv"
        ).exists():
            self._alert(
                "That Adventure already exists.",
                "Use Open Existing Adventure or choose another name or folder.",
            )
            return False
        self.project_directory = selected_directory
        self.adventure_name = selected_name
        self._prepare_gpx_document_context(preserve_loaded_document=True)
        self.temporary_control_model = True
        self.journal.move_to(self.project_directory / ".mycamino" / "logs")
        self._set_title()
        return True

    @objc.python_method
    def _prepare_gpx_document_context(self, preserve_loaded_document=False):
        """Keep the temporary world track from becoming GPX document metadata."""
        controller = self.gpx_controller
        controller.project_name_fallback = str(self.adventure_name or "").strip()
        has_real_tracks = any(track.points() for track in controller.tracks)
        if preserve_loaded_document and has_real_tracks:
            if controller.project_name.casefold() in {"", "untitled", "untitled adventure"}:
                controller.project_name = controller.project_name_fallback
                controller.project_field.setStringValue_(controller.project_name)
                controller.project_name_needs_metadata_repair = True
            return
        controller.project_name = ""
        controller.project_name_needs_metadata_repair = False
        controller.project_field.setStringValue_("")

    @objc.python_method
    def _import_media_files(self, paths):
        if self.project_directory is None:
            return []
        results = import_media_files(paths, self.project_directory)
        counts = {action: 0 for action in ("copied", "renamed", "reused", "failed")}
        for item in results:
            counts[item.action] = counts.get(item.action, 0) + 1
            if item.action == "reused":
                self._journal(
                    "Media Import",
                    f"Reused identical Adventure file {item.target.name}.",
                )
            elif item.action == "renamed":
                self._journal(
                    "Media Import",
                    f"Imported different same-name file as {item.target.name}.",
                )
            elif item.action == "copied":
                self._journal("Media Import", f"Copied {item.source.name} to the Adventure.")
            else:
                self._journal("Media Import", f"Could not import {item.source.name}: {item.error}")
        self._journal(
            "Media Import",
            f"Complete: {counts['copied']} copied, {counts['renamed']} renamed because different, "
            f"{counts['reused']} identical reused, {counts['failed']} failed.",
        )
        return accepted_media_paths(results)

    @objc.python_method
    def _handle_media_drop(self, paths):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Use {len(paths)} dropped media file(s)")
        alert.setInformativeText_(
            "Add the media to the slide show, or use their GPS locations to create or extend a track. "
            "For the guided demo, choose Add to Slide Show. Metadata is prepared in the background."
        )
        alert.addButtonWithTitle_("Add to Slide Show")
        alert.addButtonWithTitle_("Create or Extend Track")
        alert.addButtonWithTitle_("Cancel")
        response = int(alert.runModal())
        if response == 1002:
            return
        route_timing_track = None
        attempt_route_timing = False
        if response == 1000:
            selected = self.gpx_controller.selected_tracks()
            timing_candidates = [
                track
                for track in (selected or self.gpx_controller.tracks)
                if self.gpx_controller._media_timing_eligible_route(track)
            ]
            if len(timing_candidates) == 1:
                route_timing_track = timing_candidates[0]
                timing_alert = NSAlert.alloc().init()
                timing_alert.setMessageText_(
                    f"Estimate times for {route_timing_track.name}?"
                )
                timing_alert.setInformativeText_(
                    "The dropped media can provide timing anchors for this route. "
                    "myCamino will first extract their dates and GPS coordinates, then "
                    "add timestamps to existing route points using media within "
                    f"{self.gpx_controller.media_timing_max_distance_m:g} m. "
                    "The route geometry will not change, and other media remain in the "
                    "slide show."
                )
                timing_alert.addButtonWithTitle_("Estimate Route Times")
                timing_alert.addButtonWithTitle_("Not Now")
                attempt_route_timing = int(timing_alert.runModal()) == 1000
                self._journal(
                    "Route Timing",
                    (
                        f"Will estimate timestamps for track #{route_timing_track.nr} "
                        "after media GPS and dates are ready."
                        if attempt_route_timing
                        else f"Route timing was declined for track #{route_timing_track.nr}."
                    ),
                )
        if self.adventure_file is None and not self._create_adventure_for_media_drop():
            return
        # Resolve the first-drop privacy choice before copying anything.
        if self.weather_consent == "unasked":
            if not self._request_initial_weather_choice():
                return
        imported = self._import_media_files(paths)
        if self.demo_mode and self.demo_coordinator is not None:
            try:
                self._set_progress("Loading bundled metadata and maps", 0, 1)
                demo_asset_name = filename_base(
                    self.adventure_file.stem
                    if self.adventure_file is not None
                    else self.adventure_name
                )
                installed = install_demo_prepared_assets(
                    self.demo_coordinator.inputs_directory,
                    self.project_directory,
                    demo_asset_name,
                )
                rendered_with = demo_prepared_render_parameters(
                    self.demo_coordinator.inputs_directory
                )
                self.parameters.update(
                    (key, value) for key, value in rendered_with.items() if key in self.parameters
                )
                self._journal(
                    "Preparing metadata",
                    f"Reused {installed['sidecars']} bundled metadata sidecar(s); no media extraction was needed.",
                )
                self._journal(
                    "Loading place names and weather",
                    "Reused bundled place names and historical weather; no network request was made.",
                )
                self._journal(
                    "Preparing maps",
                    f"Reused {installed['map_assets']} bundled map asset(s); no tiles were requested.",
                )
                self._set_progress("Loading bundled metadata and maps", 1, 1)
            except OSError as exc:
                self._journal("Demo", f"Could not load bundled offline assets: {exc}")
                self._alert("The offline demo assets are incomplete.", str(exc))
                return
        if response == 1000:
            self.pending_control_media.update(imported)
            if attempt_route_timing and route_timing_track is not None:
                self.pending_route_timing_media.update(imported)
                self.pending_route_timing_track_nr = route_timing_track.nr
            if self.control_file is not None and self.control_file.is_file():
                try:
                    mark_imported_media(self.control_file, imported)
                except OSError as exc:
                    self._journal(
                        "Media Import",
                        f"Could not record import timestamps: {exc}",
                    )
        self.queue_media_processing(imported)
        if response == 1001:
            self.gpx_controller.media_context["_pending_media_paths"] = [str(path) for path in imported]
            self.gpx_controller.media_context["_skip_media_selection_once"] = True
            self.gpx_controller.addTracksFromPhotos_(None)
        else:
            self._journal(
                "Media Import",
                f"Copied {len(imported)} media file(s); preparing them before control-file review.",
            )
            if self.demo_coordinator is not None:
                self.demo_coordinator.workspaceDidAcceptSlideShowMedia(imported)

    @objc.python_method
    def _create_adventure_for_media_drop(self):
        if self.project_directory is None and not self._request_project_directory(None):
            return False
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Create an Adventure for these media?")
        alert.setInformativeText_(
            "The accepted files will be copied into this Adventure and prepared in the background."
        )
        name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 26))
        name_field.setStringValue_(self.adventure_name or self.project_directory.name)
        alert.setAccessoryView_(name_field)
        alert.addButtonWithTitle_("Create Adventure")
        alert.addButtonWithTitle_("Cancel")
        if int(alert.runModal()) != 1000:
            return False
        name = filename_base(str(name_field.stringValue()).strip())
        if not name:
            self._alert("Enter an Adventure name.")
            return False
        target = self.project_directory / f"{name}.adv"
        if target.exists():
            self._alert("That Adventure already exists.", target.name)
            return False
        self.adventure_name = name
        self._prepare_gpx_document_context(preserve_loaded_document=True)
        self.saveWorkspace_(None)
        return self.adventure_file is not None and self.adventure_file.is_file()

    @objc.python_method
    def _import_audio(self, paths):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Add audio")
        alert.addButtonWithTitle_("Music")
        alert.addButtonWithTitle_("Narration")
        alert.addButtonWithTitle_("Cancel")
        response = int(alert.runModal())
        if response == 1002:
            return
        destination = self.project_directory / ("audio" if response == 1000 else "narration")
        destination.mkdir(parents=True, exist_ok=True)
        for source in paths:
            shutil.copy2(source, destination / source.name)
        from audio_playlist import audio_files_in_directory, generated_playlist_text, updated_playlist_text
        playlist = destination / f"{filename_base(self.adventure_name)}.playlist"
        files = audio_files_in_directory(destination)
        if playlist.is_file():
            text, _missing = updated_playlist_text(
                playlist.read_text(encoding="utf-8"), files, destination
            )
        else:
            text = generated_playlist_text(files, destination)
        _atomic_write_text(playlist, text)
        if self.adventure_payload is not None:
            key = "music_playlist" if response == 1000 else "narration_playlist"
            self.adventure_payload[key] = str(playlist.relative_to(self.project_directory))
        self.workspaceDocumentDidChange_("Audio added")

    def workspaceDocumentDidChange_(self, _message):
        self.workspaceMutationDidOccur_(
            WorkspaceMutation.create(
                "workspace", MutationKind.ADVENTURE, message=str(_message or "")
            )
        )

    @objc.python_method
    def _workspace_autosave_interval(self):
        return adventure_autosave_delays(
            True,
            self.parameters.get("adventure.autosave_interval_seconds", 60.0),
        )[1]

    @objc.python_method
    def _configure_workspace_autosave_timer(self):
        if self.workspace_autosave_timer is not None:
            self.workspace_autosave_timer.invalidate()
            self.workspace_autosave_timer = None
        if self.adventure_file is None or self.project_directory is None:
            return
        self.workspace_autosave_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self._workspace_autosave_interval(),
            self,
            "performWorkspaceAutosave:",
            None,
            True,
        )

    @objc.python_method
    def workspaceMutationDidOccur_(self, mutation):
        if isinstance(mutation, WorkspaceMutation):
            recorded = self.mutation_journal.record(
                mutation.component,
                mutation.kind,
                track_numbers=mutation.track_numbers,
                message=mutation.message,
            )
            component = recorded.component
        else:
            component = str(getattr(mutation, "component", "workspace"))
            self.mutation_journal.record(component, MutationKind.ADVENTURE)
        self._mark_adventure_component_changed(component)

    def adventureComponentDidChange_(self, component):
        """Compatibility entry point for callers not yet mutation-aware."""
        name = str(component or "workspace")
        kind = {
            "gpx": MutationKind.GEOMETRY,
            "control": MutationKind.CONTROL,
            "audio": MutationKind.AUDIO,
        }.get(name, MutationKind.ADVENTURE)
        self.mutation_journal.record(name, kind)
        self._mark_adventure_component_changed(name)

    @objc.python_method
    def _mark_adventure_component_changed(self, name):
        """Record dirtiness without restarting the fixed checkpoint clock."""
        self.workspace_dirty_components.add(name)
        self.workspace_autosave_generation += 1
        self.recovery_dirty = True
        recovery_delay, _autosave_interval = adventure_autosave_delays(
            self.adventure_file is not None and self.project_directory is not None,
            self.parameters.get("adventure.autosave_interval_seconds", 60.0),
        )
        if self.recovery_debounce_timer is not None:
            self.recovery_debounce_timer.invalidate()
        self.recovery_debounce_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            recovery_delay, self, "writeRecovery:", None, False
        )
        if self.workspace_autosave_timer is None:
            self._configure_workspace_autosave_timer()

    @objc.IBAction
    def performWorkspaceAutosave_(self, _sender):
        if not (self.workspace_dirty_components or self.gpx_controller.dirty):
            return
        if "gpx" in self.workspace_dirty_components or self.gpx_controller.dirty:
            self._begin_background_gpx_checkpoint()
        else:
            self._flush_workspace_autosave(show_errors=False)

    @objc.python_method
    def _begin_background_gpx_checkpoint(self):
        if self.workspace_autosave_future is not None or self.workspace_autosave_in_progress:
            return False
        if self.project_directory is None or self.adventure_file is None:
            return False
        stored_name = str((self.adventure_payload or {}).get("gpx_file", "")).strip()
        gpx_path = self.project_directory / (
            stored_name or f"{filename_base(self.adventure_name)}.gpx"
        )
        try:
            # Detach the mutable editor tree on AppKit, then perform XML output
            # and the expensive identity traversal on the worker.
            saved_root = self.gpx_controller.build_root(gpx_path)
        except Exception as exc:
            self._journal("Save", f"Could not prepare the GPX checkpoint: {exc}")
            return False
        mutation_generation = self.mutation_journal.generation
        self.workspace_autosave_context = {
            "path": gpx_path,
            "root": saved_root,
            "workspace_generation": self.workspace_autosave_generation,
            "mutation_generation": mutation_generation,
            "derived_update_required": self.mutation_journal.needs_derived_data(
                through_generation=mutation_generation
            ),
        }
        self.workspace_autosave_in_progress = True
        self._set_background_progress("workspace-save", "Saving Adventure", 0, 1)
        self.workspace_autosave_future = self.executor.submit(
            _write_gpx_checkpoint, gpx_path, saved_root
        )
        self.workspace_autosave_poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.05, self, "pollWorkspaceAutosave:", None, True
        )
        return True

    @objc.IBAction
    def pollWorkspaceAutosave_(self, _sender):
        if self.workspace_autosave_future is None or not self.workspace_autosave_future.done():
            return
        self._finish_background_gpx_checkpoint()

    @objc.python_method
    def _checkpoint_adventure_payload(self, gpx_path, identity):
        payload = dict(self.adventure_payload or {})
        base = filename_base(self.adventure_name)
        control_path = self.control_file or self.project_directory / f"{base}-sorted.lst"
        payload.update({
            "adventure_format_version": 2,
            "project_name": base,
            "project_directory": str(self.project_directory),
            "gpx_file": Path(gpx_path).name,
            "control_file": control_path.name,
            "track_map_base": str(payload.get("track_map_base", "")).strip() or base,
            "parameters": parameter_payload(self.parameters),
            "weather_consent": self.weather_consent,
            "guided_demo": bool(self.demo_mode),
            "gpx_identity": identity,
            ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD: sorted(
                self.untimed_prompt_suppressions
            ),
        })
        validate_adventure_payload(payload, self.adventure_file)
        atomic_write_json(self.adventure_file, payload)
        self.adventure_payload = payload

    @objc.python_method
    def _finish_background_gpx_checkpoint(self):
        future = self.workspace_autosave_future
        context = self.workspace_autosave_context or {}
        if future is None:
            return True
        if self.workspace_autosave_poll_timer is not None:
            self.workspace_autosave_poll_timer.invalidate()
            self.workspace_autosave_poll_timer = None
        success = False
        try:
            identity = future.result()
            path = Path(context["path"])
            mutation_generation = int(context["mutation_generation"])
            self._checkpoint_adventure_payload(path, identity)
            has_new_gpx_change = self.mutation_journal.has_component_after(
                "gpx", mutation_generation
            )
            self.mutation_journal.acknowledge_component(
                "gpx", mutation_generation
            )
            if not has_new_gpx_change:
                self.workspace_dirty_components.discard("gpx")
                self.gpx_controller.dirty = False
                self.gpx_controller.set_output_path(path)
                self.gpx_controller.schedule_saved_metrics_cache(
                    path, context["root"]
                )
                self.gpx_controller.set_status(
                    f"Auto-saved {len(self.gpx_controller.tracks)} track(s) to {path}."
                )
                if context.get("derived_update_required"):
                    self._queue_derived_track_data()
            self._journal("Save", f"Auto-saved {path.name} in the background.")
            if not self.workspace_dirty_components:
                self.recovery_dirty = False
                self.journal.move_to(self.project_directory / ".mycamino" / "logs")
                self._discard_recovery_after_successful_save()
                self._remember_recent_adventure(self.project_directory)
            success = True
        except Exception as exc:
            self._journal("Save", f"Background GPX autosave failed: {exc}")
            self.recovery_dirty = True
        finally:
            self.workspace_autosave_future = None
            self.workspace_autosave_context = None
            self.workspace_autosave_in_progress = False
            self._clear_background_progress("workspace-save")
        return success

    @objc.python_method
    def _flush_workspace_autosave(self, *, show_errors=True, force=False):
        if self.workspace_autosave_future is not None:
            if not (show_errors or force):
                return False
            try:
                self.workspace_autosave_future.result()
            except Exception:
                pass
            self._finish_background_gpx_checkpoint()
        if self.workspace_autosave_in_progress:
            return False
        if self.project_directory is None or self.adventure_file is None:
            return not self.workspace_dirty_components
        components = set(self.workspace_dirty_components)
        if force:
            components.update({"control", "adventure"})
            if self.gpx_controller.dirty or (
                str((self.adventure_payload or {}).get("gpx_file", "")).strip()
                and not self.missing_gpx_reference
            ):
                components.add("gpx")
        if not components and not self.gpx_controller.dirty:
            return True
        generation = self.workspace_autosave_generation
        mutation_generation = self.mutation_journal.generation
        derived_update_required = self.mutation_journal.needs_derived_data(
            through_generation=mutation_generation
        )
        self.workspace_autosave_in_progress = True
        failures = []
        saved_components = set()
        gpx_saved = False
        try:
            self._set_background_progress("workspace-save", "Saving Adventure", 0, 1)
            controller = self.full_gui_controller
            if controller is not None:
                if controller.current_project_dir is not None:
                    self.project_directory = Path(controller.current_project_dir)
                if controller.current_project_file is not None:
                    self.adventure_file = Path(controller.current_project_file)
                self.adventure_name = controller._current_project_name()
                current_control = controller._control_file_path()
                if current_control is not None:
                    self.control_file = current_control
            if controller is not None and controller.control_table_dirty:
                if controller._save_control_table_changes(show_errors=show_errors):
                    self.control_file = controller.control_table_path
                    self.control_rows = copy.deepcopy(controller.control_table_rows)
                    saved_components.add("control")
                else:
                    failures.append("control file contains invalid or unsavable changes")
            elif controller is not None and "control" in components:
                if controller.control_table_path is not None:
                    self.control_file = controller.control_table_path
                    self.control_rows = copy.deepcopy(controller.control_table_rows)
                saved_components.add("control")
            elif "control" in components and self.temporary_control_model:
                # Temporary rows are a review model, not a playable control
                # file. Only the combined Maps-to-Control pipeline may install
                # the initial .lst after its assets have been validated.
                saved_components.add("control")
            elif "control" in components and self.control_rows:
                try:
                    serialize = _main_gui_module().serialize_slideshow_control_row
                    control_path = self.control_file or self.project_directory / f"{filename_base(self.adventure_name)}-sorted.lst"
                    _atomic_write_text(
                        control_path,
                        "\n".join(serialize(row) for row in self.control_rows) + "\n",
                    )
                    control_text = control_path.read_text(encoding="utf-8")
                    inventory = load_control_media_inventory(control_path)
                    inventory_payload = build_control_media_inventory_payload(
                        inventory,
                        [item.path for item in self.media_items],
                        [
                            str(row.get("name", ""))
                            for row in self.control_rows
                            if str(row.get("type", "")).upper() in {"IMG", "VID"}
                        ],
                        control_text=control_text,
                    )
                    write_control_media_inventory(
                        inventory_payload,
                        control_media_inventory_path(control_path),
                    )
                    self.control_file = control_path
                    saved_components.add("control")
                except OSError as exc:
                    failures.append(f"control file: {exc}")
            elif "control" in components:
                saved_components.add("control")

            if "gpx" in components or self.gpx_controller.dirty:
                try:
                    stored_gpx_name = str(
                        (self.adventure_payload or {}).get("gpx_file", "")
                    ).strip()
                    gpx_path = self.project_directory / (
                        stored_gpx_name or f"{filename_base(self.adventure_name)}.gpx"
                    )
                    if force:
                        self.gpx_controller.save_to_path(gpx_path)
                    else:
                        saved_root = self.gpx_controller.write_gpx_file(gpx_path)
                        self.gpx_controller.schedule_saved_metrics_cache(
                            gpx_path, saved_root
                        )
                        self.gpx_controller.set_output_path(gpx_path)
                        self.gpx_controller.dirty = False
                        self.gpx_controller.set_status(
                            f"Auto-saved {len(self.gpx_controller.tracks)} track(s) to {gpx_path}."
                        )
                    saved_components.add("gpx")
                    gpx_saved = True
                except OSError as exc:
                    failures.append(f"GPX file: {exc}")

            if controller is not None:
                payload = dict(controller._collect_project_payload())
                existing_identity = (self.adventure_payload or {}).get("gpx_identity")
                if existing_identity is not None:
                    payload["gpx_identity"] = existing_identity
                self.parameters = normalize_parameters(controller.parameters)
                self.weather_consent = controller.weather_consent
            else:
                payload = dict(self.adventure_payload or {})
            base = filename_base(self.adventure_name)
            stored_gpx_name = str(payload.get("gpx_file", "")).strip()
            stored_map_base = str(payload.get("track_map_base", "")).strip()
            control_path = self.control_file or self.project_directory / f"{base}-sorted.lst"
            active_gpx_path = self.project_directory / stored_gpx_name if stored_gpx_name else None
            if gpx_saved:
                active_gpx_path = gpx_path
                stored_gpx_name = gpx_path.name
            payload.update({
                "adventure_format_version": 2,
                "project_name": base,
                "project_directory": str(self.project_directory),
                "gpx_file": stored_gpx_name,
                "control_file": control_path.name,
                "track_map_base": stored_map_base or base,
                "parameters": parameter_payload(self.parameters),
                "weather_consent": self.weather_consent,
                "guided_demo": bool(self.demo_mode),
                ADVENTURE_UNTIMED_PROMPT_SUPPRESSIONS_FIELD: sorted(
                    self.untimed_prompt_suppressions
                ),
            })
            if (
                active_gpx_path is not None
                and active_gpx_path.is_file()
                and (gpx_saved or normalize_gpx_identity(payload.get("gpx_identity")) is None)
            ):
                try:
                    payload["gpx_identity"] = build_gpx_identity(active_gpx_path)
                except (OSError, ValueError) as exc:
                    failures.append(f"GPX identity: {exc}")
            elif not stored_gpx_name:
                payload.pop("gpx_identity", None)
            adventure_components = components - {"gpx", "control"}
            adventure_needs_save = (
                force
                or bool(adventure_components)
                or payload != (self.adventure_payload or {})
            )
            if adventure_needs_save and not failures:
                try:
                    validate_adventure_payload(payload, self.adventure_file)
                    atomic_write_json(self.adventure_file, payload)
                    self.adventure_payload = payload
                    saved_components.add("adventure")
                    saved_components.update(adventure_components)
                    if controller is not None:
                        controller.current_project_file = self.adventure_file
                        controller.saved_project_payload = dict(payload)
                        controller.mark_clean()
                except (OSError, AdventureFormatError) as exc:
                    failures.append(f"Adventure file: {exc}")

            if generation == self.workspace_autosave_generation:
                self.workspace_dirty_components.difference_update(saved_components)
            if not failures and not self.workspace_dirty_components:
                self.mutation_journal.acknowledge(mutation_generation)
                self.recovery_dirty = False
                # The journal initially lives inside the recovery session.
                # Move it before discarding that session or the next message
                # would target a deleted processing.log during drag-and-drop.
                self.journal.move_to(self.project_directory / ".mycamino" / "logs")
                self._discard_recovery_after_successful_save()
                self._journal("Save", f"Auto-saved {self.adventure_file.name}.")
                self._remember_recent_adventure(self.project_directory)
                return True
            if failures:
                self._journal("Save", "Auto-save failed: " + "; ".join(failures))
                if show_errors:
                    self._alert("Could not save all Adventure changes.", "\n".join(failures))
            return not failures
        except Exception as exc:
            self._journal("Save", f"Auto-save failed unexpectedly: {exc}")
            if show_errors:
                self._alert("Could not save the Adventure.", str(exc))
            return False
        finally:
            self.workspace_autosave_in_progress = False
            self._clear_background_progress("workspace-save")
            if gpx_saved and derived_update_required:
                self._queue_derived_track_data()
            if self.workspace_dirty_components and generation != self.workspace_autosave_generation:
                self._mark_adventure_component_changed(
                    next(iter(self.workspace_dirty_components))
                )

    @objc.python_method
    def _saved_gpx_contains_points(self):
        if self.project_directory is None or not isinstance(self.adventure_payload, dict):
            return False
        name = str(self.adventure_payload.get("gpx_file", "")).strip()
        path = self.project_directory / name if name else None
        if path is None or not path.is_file():
            return False
        tail = b""
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(64 * 1024), b""):
                    sample = tail + block
                    if b"<trkpt" in sample or b":trkpt" in sample:
                        return True
                    tail = sample[-16:]
        except OSError:
            return False
        return False

    @objc.python_method
    def _has_recoverable_workspace_content(self):
        """Exclude the automatic empty-track placeholder from crash recovery."""
        if getattr(self, "workspace_dirty_components", None):
            return True
        for track in self.gpx_controller.tracks:
            if any(element.tag.rsplit("}", 1)[-1] == "trkpt" for element in track.element.iter()):
                return True
        if self.control_rows or self.pending_media or self.pending_control_media:
            return True
        if self.gpx_controller.dirty and self._saved_gpx_contains_points():
            # Preserve an intentional deletion of all points from a saved track.
            return True
        if self.recovery_dirty and self.control_file is not None:
            try:
                if self.control_file.is_file() and self.control_file.stat().st_size > 0:
                    return True
            except OSError:
                pass
        return False

    @objc.IBAction
    def periodicRecovery_(self, _sender):
        if (
            self.discard_recovery_after_write
            and self.recovery_write_future is not None
            and self.recovery_write_future.done()
        ):
            try:
                self.recovery_write_future.result()
            except Exception as exc:
                self._journal("Recovery", f"Could not finish recovery data: {exc}")
            self.recovery_write_future = None
            self.discard_recovery_after_write = False
            self.recovery.discard()
            self.recovery = WorkspaceRecoverySession()
        if self.recovery_dirty:
            self.writeRecovery_(None)

    @objc.python_method
    def _discard_recovery_after_successful_save(self):
        if self.recovery_write_future is not None:
            if not self.recovery_write_future.done():
                self.discard_recovery_after_write = True
                return
            try:
                self.recovery_write_future.result()
            except Exception:
                pass
            self.recovery_write_future = None
        self.discard_recovery_after_write = False
        self.recovery.discard()
        self.recovery = WorkspaceRecoverySession()

    @objc.IBAction
    def writeRecovery_(self, _sender):
        if self.recovery_write_future is not None:
            if not self.recovery_write_future.done():
                return
            try:
                self.recovery_write_future.result()
            except Exception as exc:
                self.recovery_dirty = True
                self._journal("Recovery", f"Could not write recovery data: {exc}")
            self.recovery_write_future = None
        if not self.recovery_dirty:
            return
        if not self._has_recoverable_workspace_content():
            self.recovery_dirty = False
            self.recovery.discard()
            self.recovery = WorkspaceRecoverySession()
            return
        control_rows = self.control_rows
        adventure_draft = self.adventure_payload or {}
        if self.full_gui_controller is not None:
            if self.full_gui_controller.control_table_dirty:
                control_rows = self.full_gui_controller.control_table_rows
            try:
                adventure_draft = self.full_gui_controller._collect_project_payload()
            except Exception:
                pass
        payload = {
            "created_at": datetime.now().astimezone().isoformat(),
            "adventure_name": self.adventure_name,
            "gpx_root": self.gpx_controller.build_root(),
            "control_rows": copy.deepcopy(control_rows),
            "source_directory": str(self.project_directory or ""),
            "adventure_draft": copy.deepcopy(adventure_draft),
            "media_paths": [str(item.path) for item in self.media_items],
            "selected_track_identity": self.selected_track_identity,
        }
        self.recovery_dirty = False
        if _sender is None:
            _write_recovery_snapshot(self.recovery, **payload)
        elif self.recovery_write_future is None:
            self.recovery_write_future = self.executor.submit(
                _write_recovery_snapshot, self.recovery, **payload
            )

    @objc.python_method
    def _offer_recovery(self):
        recovery_project = (
            self.adventure_file.parent
            if self.adventure_file is not None
            else self.project_directory
        )
        sessions = discover_recovery_sessions(project_directory=recovery_project)
        sessions = [session for session in sessions if not is_guided_demo_recovery(session)]
        if not sessions:
            return False
        newest = sessions[0]
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Recover an unsaved Adventure?")
        alert.setInformativeText_(f"{newest.get('adventure_name', 'Untitled')} — {newest.get('created_at', '')}")
        alert.addButtonWithTitle_("Recover")
        alert.addButtonWithTitle_("Not Now")
        alert.addButtonWithTitle_("Delete")
        response = int(alert.runModal())
        if response == 1002:
            if not delete_recovery_session(newest):
                self._alert(
                    "Could not delete the recovery.",
                    "The recovery files could not be removed.",
                )
            return False
        if response != 1000:
            return False
        try:
            root = ET.fromstring(newest["gpx_xml"])
            self.gpx_controller.tracks = []
            temp = self.recovery.directory / "recovered.gpx"
            ET.ElementTree(root).write(temp, encoding="utf-8", xml_declaration=True)
            self.gpx_controller._load_gpx_paths_synchronously([temp], mark_dirty=False)
            self.control_rows = list(newest.get("control_rows") or [])
            self.adventure_name = str(newest.get("adventure_name") or self.adventure_name)
            self._prepare_gpx_document_context(preserve_loaded_document=True)
            source = str(newest.get("source_directory") or "")
            self.project_directory = Path(source) if source else None
            if self.project_directory is not None:
                self.journal.move_to(self.project_directory / ".mycamino" / "logs")
            return True
        except Exception as exc:
            self._alert("Could not recover the workspace.", str(exc))
            return False

    @objc.IBAction
    def saveWorkspace_(self, _sender):
        if self.project_directory is None and not self._request_project_directory(None):
            return False
        base = filename_base(self.adventure_name)
        if self.adventure_file is None:
            self.adventure_file = self.project_directory / f"{base}.adv"
        self.workspace_dirty_components.update({"gpx", "control", "adventure"})
        self.workspace_autosave_generation += 1
        success = self._flush_workspace_autosave(show_errors=True, force=True)
        if success:
            self.journal.move_to(self.project_directory / ".mycamino" / "logs")
            self._configure_workspace_autosave_timer()
            if self.media_watcher is None:
                self._start_media_watcher()
        return success

    @objc.python_method
    def _manage_map_provider_from_settings(self, draft):
        result = run_map_provider_setup(
            preferred_provider=str(draft.get("maps.output_provider", "geoapify")),
            credential_id=str(draft.get("maps.credential_id", "default")),
            timeout_seconds=float(draft.get("maps.request_timeout_seconds", 12.0)),
        )
        if str(result.get("action", "cancel")) == "cancel":
            return None
        return {"maps.output_provider": str(result.get("provider", "osm"))}

    @objc.python_method
    def _apply_workspace_parameters(self, values, changed):
        old = dict(self.parameters)
        self.parameters = normalize_parameters(values)
        if "weather.enabled" in changed or "weather.access" in changed:
            if not bool(self.parameters.get("weather.enabled", False)):
                self.weather_consent = "disabled"
            else:
                access = str(self.parameters.get("weather.access", "free")).casefold()
                if access == "customer":
                    credential = str(self.parameters.get("weather.credential_id", "default"))
                    if not _main_gui_module().request_open_meteo_api_key(credential):
                        self.parameters = old
                        return False
                    self.weather_consent = "customer"
                else:
                    alert = NSAlert.alloc().init()
                    alert.setMessageText_("Enable historical weather?")
                    alert.setInformativeText_(
                        "Media coordinates and exposure times will be sent to the free, "
                        "non-commercial Open-Meteo service."
                    )
                    alert.addButtonWithTitle_("Enable")
                    alert.addButtonWithTitle_("Cancel")
                    if int(alert.runModal()) != 1000:
                        self.parameters = old
                        return False
                    self.weather_consent = "free"
        self.gpx_controller.apply_project_parameters(self.parameters)
        if self.adventure_payload is not None:
            self.adventure_payload["parameters"] = parameter_payload(self.parameters)
            self.adventure_payload["weather_consent"] = self.weather_consent
        if self.full_gui_controller is not None:
            self.full_gui_controller._apply_parameter_values(
                self.parameters, propagate_to_editor=True
            )
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)
        if "adventure.autosave_interval_seconds" in changed:
            self._configure_workspace_autosave_timer()
        self.adventureComponentDidChange_("adventure")
        return True

    @objc.IBAction
    def showSettings_(self, _sender):
        if self.parameter_editor_controller is None:
            self.parameter_editor_controller = CocoaParameterEditor.alloc().init()
            self.parameter_editor_controller.configure(
                title="Adventure Settings",
                sections=SECTION_ORDER,
                values=self.parameters,
                apply_callback=self._apply_workspace_parameters,
                manage_map_provider_callback=self._manage_map_provider_from_settings,
            )
        else:
            self.parameter_editor_controller.update_values(self.parameters)
        self.parameter_editor_controller.show()

    @objc.python_method
    def _show_settings_section(self, section):
        self.showSettings_(None)
        if section in SECTION_ORDER:
            self.parameter_editor_controller.current_section = section
            self.parameter_editor_controller._render_section()

    @objc.IBAction
    def showMediaSettings_(self, _sender):
        self._show_settings_section("Locations")

    @objc.IBAction
    def showMapSettings_(self, _sender):
        self._show_settings_section("Map Generation")

    @objc.IBAction
    def showAudioSettings_(self, _sender):
        self._show_settings_section("Audio")

    @objc.IBAction
    def showSlideShowSettings_(self, _sender):
        self._show_settings_section("Slide Show")

    @objc.python_method
    def _property_button(self, title, action, frame):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    @objc.python_method
    def _refresh_properties_fields(self):
        if self.properties_window is None:
            return
        payload = self.adventure_payload or {}
        self.properties_name_field.setStringValue_(self.adventure_name or "")
        self.properties_name_field.setEditable_(self.adventure_file is None or self.properties_name_unlocked)
        self.properties_project_field.setStringValue_(str(self.project_directory or ""))
        self.properties_description_text.setString_(str(payload.get("description", "") or ""))
        self.properties_title_image_field.setStringValue_(str(payload.get("title_image", "") or ""))

    @objc.IBAction
    def showAdventureProperties_(self, _sender):
        if self.properties_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(260, 180, 680, 430),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setReleasedWhenClosed_(False)
            window.setTitle_("Adventure Properties")
            content = window.contentView()

            def label(text, y):
                field = NSTextField.labelWithString_(text)
                field.setFrame_(NSMakeRect(20, y, 120, 24))
                content.addSubview_(field)

            label("Adventure name", 378)
            self.properties_name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(145, 378, 405, 26))
            content.addSubview_(self.properties_name_field)
            content.addSubview_(self._property_button("Edit Name", "unlockAdventurePropertyName:", NSMakeRect(560, 378, 100, 26)))
            label("Project directory", 340)
            self.properties_project_field = NSTextField.alloc().initWithFrame_(NSMakeRect(145, 340, 405, 26))
            self.properties_project_field.setEditable_(False)
            content.addSubview_(self.properties_project_field)
            content.addSubview_(self._property_button("Change…", "changeAdventureDirectory:", NSMakeRect(560, 340, 100, 26)))
            label("Description", 300)
            description_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(145, 182, 515, 136))
            description_scroll.setHasVerticalScroller_(True)
            description_scroll.setBorderType_(1)
            self.properties_description_text = NSTextView.alloc().initWithFrame_(description_scroll.contentView().bounds())
            self.properties_description_text.setRichText_(False)
            self.properties_description_text.setVerticallyResizable_(True)
            description_scroll.setDocumentView_(self.properties_description_text)
            content.addSubview_(description_scroll)
            label("Intro title image", 140)
            self.properties_title_image_field = NSTextField.alloc().initWithFrame_(NSMakeRect(145, 140, 315, 26))
            self.properties_title_image_field.setEditable_(False)
            content.addSubview_(self.properties_title_image_field)
            content.addSubview_(self._property_button("Choose…", "chooseAdventureTitleImage:", NSMakeRect(470, 140, 90, 26)))
            content.addSubview_(self._property_button("Use First", "useFirstAdventureTitleImage:", NSMakeRect(570, 140, 90, 26)))
            content.addSubview_(self._property_button("Cancel", "cancelAdventureProperties:", NSMakeRect(460, 28, 90, 30)))
            content.addSubview_(self._property_button("Apply", "applyAdventureProperties:", NSMakeRect(570, 28, 90, 30)))
            self.properties_window = window
        self.properties_name_unlocked = self.adventure_file is None
        self._refresh_properties_fields()
        self.properties_window.makeKeyAndOrderFront_(None)
        self.properties_window.orderFrontRegardless()

    @objc.IBAction
    def saveWorkspaceAs_(self, _sender):
        self.showAdventureProperties_(None)
        self.unlockAdventurePropertyName_(None)

    @objc.IBAction
    def unlockAdventurePropertyName_(self, _sender):
        self.properties_name_unlocked = True
        self.properties_name_field.setEditable_(True)
        self.properties_window.makeFirstResponder_(self.properties_name_field)
        self.properties_name_field.selectText_(None)

    @objc.IBAction
    def chooseAdventureTitleImage_(self, _sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        if self.project_directory is not None:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.project_directory)))
        if panel.runModal() and panel.URL() is not None:
            path = Path(str(panel.URL().path())).expanduser().resolve(strict=False)
            if self.project_directory is not None:
                try:
                    value = str(path.relative_to(self.project_directory))
                except ValueError:
                    value = str(path)
            else:
                value = str(path)
            self.properties_title_image_field.setStringValue_(value)

    @objc.IBAction
    def useFirstAdventureTitleImage_(self, _sender):
        self.properties_title_image_field.setStringValue_("")

    @objc.IBAction
    def cancelAdventureProperties_(self, _sender):
        self.properties_window.orderOut_(None)

    @objc.IBAction
    def changeAdventureDirectory_(self, _sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        if self.project_directory is not None:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.project_directory)))
        if panel.runModal() and panel.URL() is not None:
            directory = Path(str(panel.URL().path())).resolve(strict=False)
            controller = self._ensure_full_gui(show_window=False)
            if controller is not None and not controller._activate_project_directory(
                directory, allow_create=True
            ):
                return
            self.load_project_directory(directory)
            self._refresh_properties_fields()

    @objc.IBAction
    def applyAdventureProperties_(self, _sender):
        name = filename_base(str(self.properties_name_field.stringValue()).strip())
        if not name:
            self._alert("Adventure name is required.", "Enter a name before applying the properties.")
            return
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            return
        if name != controller._current_project_name():
            controller._set_adventure_name_editing(True)
            controller.title_field.setStringValue_(name)
            controller.adventureNameCommitted_(controller.title_field)
            if controller._current_project_name() != name:
                return
        controller.description_text.setString_(str(self.properties_description_text.string()))
        title_value = str(self.properties_title_image_field.stringValue()).strip()
        controller.title_image = controller._resolve_optional_project_path(title_value) if title_value else None
        controller.mark_dirty(immediate=True)
        if not controller.save_project_configuration():
            return
        self.adventure_file = controller.current_project_file
        self.adventure_payload = dict(controller._collect_project_payload())
        self.project_directory = controller.current_project_dir
        self.adventure_name = controller._current_project_name()
        self._prepare_gpx_document_context(preserve_loaded_document=True)
        self.control_file = controller._control_file_path()
        self._set_title()
        self.properties_window.orderOut_(None)

    @objc.python_method
    def adoptAdvancedInterfaceParameters_weatherConsent_(self, values, consent):
        """Keep the map workspace and embedded Advanced Interface in one settings state."""
        self.parameters = normalize_parameters(values)
        normalized_consent = str(consent).casefold()
        if normalized_consent in {"unasked", "free", "customer", "disabled"}:
            self.weather_consent = normalized_consent
        self.gpx_controller.apply_project_parameters(self.parameters)
        if self.adventure_payload is not None:
            self.adventure_payload["parameters"] = parameter_payload(self.parameters)
            self.adventure_payload["weather_consent"] = self.weather_consent
        if self.parameter_editor_controller is not None:
            self.parameter_editor_controller.update_values(self.parameters)
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)
        self.adventureComponentDidChange_("adventure")

    @objc.python_method
    def _ensure_full_gui(self, show_window=True):
        try:
            if self.full_gui_controller is None:
                controller_class = _main_gui_module().GPXTrackerController
                controller = controller_class.alloc().initWithProjectDirectory_projectFile_(
                    self.project_directory, self.adventure_file
                )
                if controller is None:
                    raise RuntimeError("The Advanced Interface could not be initialized.")
                self.full_gui_controller = controller
                controller.hosted_by_adventure_map = True
                controller.adventure_map_host = self
                controller.show()
                if controller.parameters != self.parameters:
                    controller._apply_parameter_values(
                        self.parameters, propagate_to_editor=True
                    )
                controller.weather_consent = self.weather_consent
                if self.demo_mode and self.project_directory is not None:
                    demo_path = str(self.project_directory.resolve(strict=False))
                    controller.recent_adventures = [
                        path for path in controller.recent_adventures
                        if str(path.resolve(strict=False)) != demo_path
                    ]
                    controller._save_recent_adventures()
                    controller._refresh_recent_adventure_menu()
                if not show_window:
                    controller.window.orderOut_(None)
            window = self.full_gui_controller.window
            if show_window:
                window.makeKeyAndOrderFront_(None)
                window.orderFrontRegardless()
            NSApp().activateIgnoringOtherApps_(True)
            return self.full_gui_controller
        except Exception as exc:
            self.full_gui_controller = None
            self._journal("Advanced Interface", f"Could not open: {exc}")
            self._alert("Could not open the Advanced Interface.", str(exc))
            return None

    @objc.python_method
    def _synchronize_control_file_from_full_gui(self):
        """Adopt a control file created or selected by the retained controller."""
        controller = self.full_gui_controller
        if controller is None:
            return self.control_file
        path = controller._control_file_path()
        if path is None or not Path(path).is_file():
            return self.control_file
        resolved = Path(path).resolve(strict=False)
        if resolved != self.control_file:
            self.control_file = resolved
            self.temporary_control_model = False
            self._load_control_rows()
            if isinstance(self.adventure_payload, dict):
                try:
                    stored = str(resolved.relative_to(self.project_directory))
                except ValueError:
                    stored = str(resolved)
                self.adventure_payload["control_file"] = stored
        return self.control_file

    @objc.IBAction
    def openAdventureGUI_(self, _sender):
        if self.gpx_controller.dirty or self.control_rows and self.adventure_file is None:
            self.saveWorkspace_(None)
        self._ensure_full_gui()

    @objc.python_method
    def _invoke_full_gui(self, selector, sender=None):
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            return None
        method = getattr(controller, selector, None)
        if method is None:
            self._alert("Action unavailable.", selector)
            return None
        return method(sender)

    @objc.python_method
    def _choose_adventure_or_project_path(self, source=None):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["adv"])
        if source is not None:
            source_path = Path(source).expanduser().resolve(strict=False)
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(source_path if source_path.is_dir() else source_path.parent)))
        elif self.project_directory is not None:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.project_directory)))
        if not panel.runModal() or panel.URL() is None:
            return None
        return Path(str(panel.URL().path())).expanduser().resolve(strict=False)

    @objc.IBAction
    def openAdventureOrProject_(self, _sender):
        path = self._choose_adventure_or_project_path()
        if path is None:
            return
        if path.is_dir():
            self.load_project_directory(path)
        else:
            self.load_adventure_path(path)

    @objc.python_method
    def recent_adventure_paths(self):
        paths = []
        store = Path.home() / "Library" / "Application Support" / "myCamino GPS Track Show" / "recent_adventures.json"
        try:
            values = json.loads(store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            values = []
        for value in values:
            path = Path(str(value)).expanduser().resolve(strict=False)
            if path.exists() and path not in paths:
                paths.append(path)
        if self.adventure_file is not None and self.adventure_file.exists():
            paths = [self.adventure_file] + [path for path in paths if path != self.adventure_file]
        return paths[:10]

    @objc.python_method
    def _recent_adventures_store_path(self):
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "myCamino GPS Track Show"
            / "recent_adventures.json"
        )

    @objc.python_method
    def _remember_recent_adventure(self, project):
        if project is None:
            return
        path = Path(project).expanduser().resolve(strict=False)
        if path.suffix.casefold() == ".adv":
            path = path.parent
        if not path.is_dir():
            return
        if self.demo_mode and any(part.startswith("mycamino-demo-") for part in path.parts):
            return
        recent = []
        for candidate in [path, *self.recent_adventure_paths()]:
            candidate = Path(candidate)
            if candidate.suffix.casefold() == ".adv":
                candidate = candidate.parent
            candidate = candidate.resolve(strict=False)
            if candidate.is_dir() and candidate not in recent:
                recent.append(candidate)
            if len(recent) >= 10:
                break
        try:
            atomic_write_json(
                self._recent_adventures_store_path(),
                [str(candidate) for candidate in recent],
            )
        except OSError as exc:
            self._journal("Adventure", f"Could not update recent Adventures: {exc}")
            return
        self._refresh_recent_adventure_menu()

    @objc.python_method
    def _refresh_recent_adventure_menu(self):
        menu = self.recent_adventure_menu
        if menu is None:
            return
        menu.removeAllItems()
        paths = self.recent_adventure_paths()
        if not paths:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "No Recent Adventures", None, ""
            )
            item.setEnabled_(False)
            menu.addItem_(item)
            return
        for path in paths:
            item = menu_item(path.name, "openRecentAdventure:", self)
            item.setRepresentedObject_(str(path))
            menu.addItem_(item)

    @objc.IBAction
    def openRecentAdventure_(self, sender):
        value = sender.representedObject()
        if not value:
            return
        path = Path(str(value)).expanduser().resolve(strict=False)
        if path.is_dir():
            self.load_project_directory(path)
        elif path.is_file():
            self.load_adventure_path(path)

    @objc.IBAction
    def revealProjectFolder_(self, _sender):
        if self.project_directory is not None:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(self.project_directory)))

    @objc.IBAction
    def addGPXFiles_(self, _sender):
        self.gpx_controller.addTracks_(None)

    @objc.IBAction
    def showTrackTable_(self, _sender):
        self.gpx_controller.show()

    @objc.IBAction
    def showWaypointTable_(self, _sender):
        self.gpx_controller.inspectTrack_(None)

    @objc.IBAction
    def fitToTrack_(self, _sender):
        self.gpx_controller.plotSelected_(None)

    @objc.IBAction
    def estimateRouteTimesFromMedia_(self, _sender):
        self.gpx_controller.estimateRouteTimesFromMedia_(None)

    @objc.python_method
    def workspace_tracks_did_load(self, loaded_records):
        """Offer geometry-preserving timing after a route is dropped into an Adventure."""
        if self.pending_create_after_gpx_load:
            self.pending_create_after_gpx_load = False
            if self.saveWorkspace_(None):
                self._journal(
                    "Adventure",
                    f"Created {self.adventure_file.name} from the dropped GPX track(s).",
                )
            else:
                self._alert(
                    "Could not create the Adventure.",
                    "The GPX tracks remain open and recoverable. Choose Save Workspace to try again.",
                )
        untimed = [
            track
            for track in loaded_records
            if self.gpx_controller._fully_untimed_route(track)
        ]
        if len(untimed) != 1:
            return
        track = untimed[0]
        self.pending_route_timing_track_nr = track.nr
        self.gpx_controller.selected_nrs = [track.nr]
        self.gpx_controller.update_selection_field()
        self.gpx_controller.highlight_selected_rows()
        if self.pending_route_timing_media and self.processing_active:
            return
        media_paths = list(self.pending_route_timing_media) or self._project_media_paths()
        if not media_paths:
            return
        self.pending_route_timing_media.difference_update(media_paths)
        self._prepare_route_timing_offer(track.nr, media_paths)

    @objc.python_method
    def _prepare_route_timing_offer(
        self,
        track_nr,
        media_paths,
        *,
        confirmation_required=True,
    ):
        """Prepare existing media off the AppKit thread, then show one offer."""
        paths = list(dict.fromkeys(Path(path).resolve(strict=False) for path in media_paths))
        if not paths:
            return
        self._journal(
            "Route Timing",
            f"Checking {len(paths)} media file(s) against untimed track #{track_nr}.",
        )
        self._set_background_progress("route_timing", "Checking route timing", 0, len(paths))

        def progress(current, total, _name):
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyRouteTimingProgress:",
                (current, total),
                False,
            )

        future = self.executor.submit(
            prepare_media_records,
            paths,
            refresh_changed=True,
            progress_callback=progress,
        )

        def poll(timer):
            if not future.done():
                return
            timer.invalidate()
            self._clear_background_progress("route_timing")
            try:
                prepared = future.result()
            except Exception as exc:
                self._journal("Route Timing", f"Could not inspect media: {exc}")
                return
            track = next(
                (item for item in self.gpx_controller.tracks if item.nr == track_nr),
                None,
            )
            if track is not None:
                changed = self.gpx_controller.offer_route_timing_from_prepared(
                    prepared,
                    track,
                    confirmation_required=confirmation_required,
                )
                if changed:
                    # Save the accepted direction before rebuilding derivatives
                    # from the Adventure's on-disk GPX.
                    self._flush_workspace_autosave(show_errors=False)
                self._journal(
                    "Route Timing",
                    "Applied media-derived route timestamps."
                    if changed
                    else "Route timestamps were not changed.",
                )
            if self.pending_route_timing_track_nr == track_nr:
                self.pending_route_timing_track_nr = None

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1,
            _BlockTimer.alloc().initWithBlock_(poll),
            "fire:",
            None,
            True,
        )

    @objc.IBAction
    def applyRouteTimingProgress_(self, value):
        current, total = value
        self._set_background_progress(
            "route_timing",
            "Checking route timing",
            current,
            total,
        )

    @objc.IBAction
    def renumberTracks_(self, _sender):
        self.gpx_controller.renumberTracks_(None)

    @objc.IBAction
    def saveSelectedTracksAs_(self, _sender):
        self.gpx_controller.saveSelectedTracksAs_(None)

    @objc.IBAction
    def importMedia_(self, _sender):
        self._invoke_full_gui("importMediaFiles_", None)

    @objc.IBAction
    def viewMedia_(self, _sender):
        if self.media_items:
            selected = self.selected_media_path or self.media_items[0].path
            self._show_adventure_media(selected)
        else:
            self._invoke_full_gui("viewMediaFiles_", None)

    @objc.IBAction
    def revealMediaFolder_(self, _sender):
        self.revealProjectFolder_(None)

    @objc.IBAction
    def addPlaceNames_(self, _sender):
        self._invoke_full_gui("getPlaceNames_", None)

    @objc.IBAction
    def addHistoricalWeather_(self, _sender):
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            return
        controller.media_weather_checkbox.setState_(NSControlStateValueOn)
        controller.fieldChanged_(controller.media_weather_checkbox)
        controller.updateMediaMetadata_(None)

    @objc.IBAction
    def updateMetadata_(self, _sender):
        self._invoke_full_gui("updateMediaMetadata_", None)

    @objc.IBAction
    def generateMaps_(self, _sender):
        self._invoke_full_gui("makePlots_", None)

    @objc.IBAction
    def viewMaps_(self, _sender):
        self._invoke_full_gui("viewPlots_", None)

    @objc.IBAction
    def revealMaps_(self, _sender):
        self._invoke_full_gui("editPlots_", None)

    @objc.IBAction
    def cancelMapGeneration_(self, _sender):
        self._invoke_full_gui("cancelMakePlots_", None)

    @objc.IBAction
    def chooseControlFile_(self, _sender):
        if self.project_directory is None:
            self._alert("No Adventure directory.", "Create or open an Adventure first.")
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["lst"])
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.project_directory)))
        if not panel.runModal() or panel.URL() is None:
            return
        self.control_file = Path(str(panel.URL().path())).resolve(strict=False)
        if self.adventure_payload is not None:
            try:
                self.adventure_payload["control_file"] = str(self.control_file.relative_to(self.project_directory))
            except ValueError:
                self.adventure_payload["control_file"] = str(self.control_file)
        controller = self._ensure_full_gui(show_window=False)
        if controller is not None:
            controller.current_control_file = self.control_file
            controller.control_file_field.setStringValue_(self.control_file.name)
            controller.mark_dirty(immediate=True)
        self._load_control_rows()

    @objc.IBAction
    def createControlFile_(self, _sender):
        self._invoke_full_gui("createControlFile_", None)

    @objc.IBAction
    def updateControlFile_(self, _sender):
        self._invoke_full_gui("updateControlFile_", None)

    @objc.IBAction
    def revealControlFile_(self, _sender):
        if self.control_file is not None and self.control_file.exists():
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([
                NSURL.fileURLWithPath_(str(self.control_file))
            ])

    @objc.IBAction
    def toggleAudioEnabled_(self, _sender):
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            return
        enabled = not bool(controller.parameters.get("audio.enabled", False))
        controller.no_music_checkbox.setState_(NSControlStateValueOff if enabled else NSControlStateValueOn)
        controller.noMusicChanged_(controller.no_music_checkbox)
        self.parameters = normalize_parameters(controller.parameters)

    @objc.IBAction
    def chooseMusicPlaylist_(self, _sender):
        self._invoke_full_gui("chooseMusicPlaylist_", None)

    @objc.IBAction
    def createMusicPlaylist_(self, _sender):
        self._invoke_full_gui("createMusicPlaylist_", None)

    @objc.IBAction
    def updateMusicPlaylist_(self, _sender):
        self._invoke_full_gui("updateMusicPlaylist_", None)

    @objc.IBAction
    def editMusicPlaylist_(self, _sender):
        self._invoke_full_gui("editMusicPlaylist_", None)

    @objc.IBAction
    def revealMusicFolder_(self, _sender):
        self._invoke_full_gui("openMusicFolder_", None)

    @objc.IBAction
    def chooseNarrationPlaylist_(self, _sender):
        self._invoke_full_gui("chooseNarrationPlaylist_", None)

    @objc.IBAction
    def createNarrationPlaylist_(self, _sender):
        self._invoke_full_gui("createNarrationPlaylist_", None)

    @objc.IBAction
    def updateNarrationPlaylist_(self, _sender):
        self._invoke_full_gui("updateNarrationPlaylist_", None)

    @objc.IBAction
    def editNarrationPlaylist_(self, _sender):
        self._invoke_full_gui("editNarrationPlaylist_", None)

    @objc.IBAction
    def revealNarrationFolder_(self, _sender):
        self._invoke_full_gui("openNarrationFolder_", None)

    @objc.IBAction
    def normalizeVideoAudio_(self, _sender):
        self._invoke_full_gui("normalizeVideoAudio_", None)

    @objc.IBAction
    def showAudioDirectiveHelp_(self, _sender):
        self._invoke_full_gui("showMusicDirectiveHelp_", None)

    @objc.IBAction
    def continueSlideShow_(self, _sender):
        self._invoke_full_gui("continueSelectedSlideShow_", None)

    @objc.IBAction
    def chooseSlideShowStart_(self, _sender):
        self.openControlFile_(None)

    @objc.IBAction
    def exportPDFSummary_(self, _sender):
        self._invoke_full_gui("exportPdfSummary_", None)

    @objc.IBAction
    def showControlDirectiveHelp_(self, _sender):
        self._invoke_full_gui("showControlDirectiveHelp_", None)

    @objc.IBAction
    def showGPXEditorHelp_(self, _sender):
        self.gpx_controller.help_(None)

    @objc.IBAction
    def openTrackEditor_(self, _sender):
        self.gpx_controller.show()

    @objc.IBAction
    def openControlFile_(self, _sender):
        self._synchronize_control_file_from_full_gui()
        if self.control_file is None or not self.control_file.is_file():
            if self.control_rows:
                self.saveWorkspace_(None)
            else:
                self._alert("No control file yet.", "Drop media or a control file first.")
                return
        controller = self._ensure_full_gui(show_window=False)
        if controller is not None:
            controller.editControlFile_(None)

    @objc.python_method
    def slideshowLiveStateDidChange_(self, state):
        """Move pending enrichment to the current and upcoming control rows."""
        if not isinstance(state, dict) or self.control_file is None:
            return
        try:
            control_order, row_active_path = control_media_priority(
                self.control_file,
                MEDIA_EXTENSIONS,
                state.get("active_row"),
            )
        except OSError:
            return
        active_path = row_active_path
        display = state.get("display")
        if isinstance(display, dict):
            candidate = Path(str(display.get("asset_path") or ""))
            if candidate.suffix.casefold() in MEDIA_EXTENSIONS:
                active_path = candidate
        if active_path is None:
            active_row = state.get("active_media")
            if isinstance(active_row, int):
                try:
                    lines = self.control_file.read_text(encoding="utf-8").splitlines()
                    raw = lines[active_row].strip()
                    if raw and not raw.startswith("#"):
                        value = raw.split("|", 1)[0].strip().strip('"')
                        candidate = Path(value).expanduser()
                        if not candidate.is_absolute():
                            candidate = self.control_file.parent / candidate
                        if candidate.suffix.casefold() in MEDIA_EXTENSIONS:
                            active_path = candidate
                except (IndexError, OSError):
                    pass
        self.enrichment_queue.set_playback_position(control_order, active_path)

    @objc.python_method
    def slideshowDidFinishEnrichmentPriority_(self, _state=None):
        self.enrichment_queue.clear_playback_position()

    @objc.IBAction
    def startSlideShow_(self, _sender):
        if self.adventure_file is None or self.gpx_controller.dirty:
            self.saveWorkspace_(None)
        if self.processing_active or self.pending_media:
            self.pending_start_after_processing = True
            self._journal("Start", "Waiting for current metadata preparation before starting.")
            self.showProcessingDetails_(None)
            return
        self.finishPendingStart_(None)

    @objc.IBAction
    def finishPendingStart_(self, _sender):
        controller = self._ensure_full_gui(show_window=False)
        if controller is None:
            return
        self._synchronize_control_file_from_full_gui()
        control_path = controller._control_file_path()
        if control_path is not None and control_path.is_file():
            controller.startSlideShow_(None)
        else:
            self._journal("Start", "Preparing maps and the initial control file before playback.")
            controller.makePlots_(None)

    @objc.IBAction
    def showHelp_(self, _sender):
        if self.help_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(220, 150, 760, 560),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered,
                False,
            )
            window.setReleasedWhenClosed_(False)
            window.setMinSize_(NSMakeSize(520, 320))
            window.setTitle_("Adventure Map Help")
            scroll = NSScrollView.alloc().initWithFrame_(window.contentView().bounds())
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(False)
            scroll.setAutohidesScrollers_(True)
            text_view = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
            text_view.setMinSize_(NSMakeSize(0, 0))
            text_view.setMaxSize_(NSMakeSize(1.0e7, 1.0e7))
            text_view.setVerticallyResizable_(True)
            text_view.setHorizontallyResizable_(False)
            text_view.setAutoresizingMask_(NSViewWidthSizable)
            text_view.setEditable_(False)
            text_view.setSelectable_(True)
            text_view.setFont_(NSFont.systemFontOfSize_(14.0))
            text_view.setTextContainerInset_(NSMakeSize(18.0, 16.0))
            text_view.textContainer().setContainerSize_(
                NSMakeSize(scroll.contentView().bounds().size.width, 1.0e7)
            )
            text_view.textContainer().setWidthTracksTextView_(True)
            text_view.setString_(
                "GETTING STARTED\n\n"
                "Drop an Adventure, project folder, GPX tracks, photos, videos, or audio onto the map.\n\n"
                "TRACKS\n\n"
                "Click a track to select it. The selected track is dark blue; other tracks are light blue, and marked ranges are red.\n\n"
                "Double-click a track to open the Track Table. Double-click it there to open its Waypoint Table. Right-click a track on the map to open either table, fit the view, or show the synchronized Elevation Profile.\n\n"
                "Double-click empty map space, or press Command-0, to return to the complete Adventure overview. Drag the map or use two-finger scrolling to pan.\n\n"
                "MEDIA\n\n"
                "Crowded media are grouped and expand into thumbnails when enough room is available. Click a group to select it; Command-click adds or removes groups. Command-drag from empty map space draws a selection rectangle. Selected media have a red outline.\n\n"
                "Right-click selected media to fit them to the map or create a track from their GPS positions. Double-click a thumbnail or group to open the media viewer. Previous and Next move through all media in the Adventure.\n\n"
                "KEYBOARD SHORTCUTS\n\n"
                "Command-O    Open an Adventure or project\n"
                "Command-G    Advanced Interface\n"
                "Command-E    Track Editor\n"
                "Command-L    Control File\n"
                "Command-S    Save\n"
                "Command-,    Adventure Settings\n"
                "Command-0    Adventure overview\n"
                "Space        Start Slide Show\n"
                "h            Show this Help\n"
                "q            Close the map; quit if it is the last window\n"
                "Command-Q    Quit myCamino\n\n"
                "MAP KEYS\n\n"
                "i            Show or hide point information\n"
                "e            Open the synchronized Elevation Profile\n"
                "a            Set the current point as anchorpoint\n"
                "+ / -        Zoom around the selected point or map center\n"
                "Command-+/-  Zoom by a larger step\n"
                "c            Center on the current point\n"
                "z            Zoom to the current selection\n"
                "Shift-Z      Fit the complete map or track\n"
                "Arrows       Previous / next waypoint or media; pan if needed\n"
                "Command-Arrows Previous / next visible track and fit it\n"
                "Option-Arrows Pan the map by 20 percent\n"
                "u            Clear the current map selection\n"
                "d            Show or hide track start/end markers\n"
                "n            Show or hide track numbers in the overview\n"
                "m            Start a marked range at the current point\n"
                "Delete       Delete the marked range or, on a track map, the current waypoint\n"
                "x            Cut the track connection after the cursor on a track map\n"
                "p            Change photo-thumbnail display on a track map\n"
                "s            Save the current map as PNG\n"
                "Command-Z    Undo the last track edit\n"
                "Shift-Command-Z Redo the last track edit\n"
                "Command-X    Cut the track after the current point on a track map\n"
                "Escape       Cancel a drag or close point information\n\n"
                "Mouse wheel and trackpad pinch zoom. Dragging pans the map; "
                "Option-drag starts panning immediately. Shift-click also sets "
                "the range marker. Hold a waypoint or segment for one second to "
                "move or insert a point.\n"
            )
            scroll.setDocumentView_(text_view)
            window.setContentView_(scroll)
            self.help_window = window
        self.help_window.makeKeyAndOrderFront_(None)
        self.help_window.orderFrontRegardless()

    @objc.IBAction
    def showQuickStart_(self, _sender):
        show_help_document("myCamino Quick Start", "MYCAMINO_QUICK_START.md")

    @objc.IBAction
    def startGuidedDemo_(self, _sender):
        if self.demo_coordinator is None:
            self.demo_coordinator = GuidedDemoCoordinator.alloc().initWithWorkspaceController_(self)
        self.demo_coordinator.start()

    @objc.python_method
    def discardGuidedDemoWorkspace(self):
        """Return to an empty map without retaining references to demo files."""
        self._stop_media_watcher()
        self._reset_optional_enrichment()
        self.cancel_event.set()
        self.processing_generation += 1
        self.media_index_generation += 1
        self.processing_active = False
        self.background_progress.clear()
        if self.workspace_autosave_timer is not None:
            self.workspace_autosave_timer.invalidate()
            self.workspace_autosave_timer = None
        if self.workspace_autosave_poll_timer is not None:
            self.workspace_autosave_poll_timer.invalidate()
            self.workspace_autosave_poll_timer = None
        if self.workspace_autosave_future is not None:
            try:
                self.workspace_autosave_future.result()
            except Exception:
                pass
            self.workspace_autosave_future = None
            self.workspace_autosave_context = None
        if self.full_gui_controller is not None:
            retired = self.full_gui_controller
            retired.adventure_map_host = None
            try:
                retired._stop_project_media_watcher()
                retired.cancel_adventure_autosave()
                if retired.geolocations_cancel_event is not None:
                    retired.geolocations_cancel_event.set()
                retired.window.orderOut_(None)
            except Exception:
                pass
            # Do not release a PyObjC window graph from inside the Stop Demo
            # button callback. AppKit and worker callbacks may still have it on
            # their stacks; retaining it until process exit avoids native use-
            # after-free crashes.
            self.retired_full_gui_controllers.append(retired)
            self.full_gui_controller = None
        self.workspace_dirty_components.clear()
        self.mutation_journal.clear()
        self.workspace_autosave_in_progress = False
        self.recovery_dirty = False
        self.pending_media.clear()
        self.pending_control_media.clear()
        self.media_items = []
        self.selected_media_paths.clear()
        self.selected_media_path = None
        self.control_rows = []
        self.control_file = None
        self.temporary_control_model = True
        self.adventure_payload = None
        self.adventure_file = None
        self.project_directory = None
        self.adventure_name = "Untitled"
        self.parameters = default_parameters()
        self.weather_consent = "unasked"
        self.untimed_prompt_suppressions.clear()
        if self.gpx_controller.track_load_timer is not None:
            self.gpx_controller.on_initial_load_complete_callback = None
            self.gpx_controller.cancel_staged_track_loading()
        self.gpx_controller.tracks = []
        self.gpx_controller.next_nr = 1
        self.gpx_controller.dirty = False
        self.gpx_controller.project_name = ""
        self.gpx_controller.project_name_fallback = ""
        self.gpx_controller.project_name_needs_metadata_repair = False
        self.gpx_controller.project_field.setStringValue_("")
        self.gpx_controller.apply_project_parameters(self.parameters)
        self.journal.move_to(self.recovery.directory / "logs")
        self._show_empty_world()
        self._set_title()

    @objc.IBAction
    def showElevationProfile_(self, _sender):
        if self.plot_view is not None:
            self.gpx_controller.open_elevation_profile_for_plot_view(self.plot_view)

    @objc.IBAction
    def showAbout_(self, _sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("myCamino")
        alert.setInformativeText_(
            f"{full_version_label()}\n\n"
            "Create and present map-based Adventures from GPS tracks, photos, and videos.\n\n"
            "Copyright (C) 2026 Heino Falcke\nLicensed under GPL-3.0-or-later."
        )
        alert.runModal()

    @objc.python_method
    def _show_project_document(self, kind, title):
        window = self.document_windows.get(kind)
        if window is not None:
            window.makeKeyAndOrderFront_(None)
            return
        try:
            document = read_license_document(kind)
        except (OSError, ValueError) as exc:
            self._alert("Document unavailable", str(exc))
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
        text_view = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setRichText_(False)
        text_view.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
        text_view.setString_(document)
        scroll.setDocumentView_(text_view)
        window.setContentView_(scroll)
        self.document_windows[kind] = window
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
    def showThirdPartyNotices_(self, _sender):
        self._show_project_document("third_party", "Third-Party Notices")

    @objc.IBAction
    def quitApplication_(self, _sender):
        NSApp().terminate_(self)

    def showProcessingDetails_(self, _sender=None):
        if self.journal_window is None:
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(180, 160, 760, 440),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
                NSBackingStoreBuffered, False,
            )
            window.setReleasedWhenClosed_(False)
            window.setTitle_("Adventure Processing")
            scroll = NSScrollView.alloc().initWithFrame_(window.contentView().bounds())
            scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            scroll.setHasVerticalScroller_(True)
            self.journal_text = NSTextView.alloc().initWithFrame_(scroll.bounds())
            self.journal_text.setEditable_(False)
            scroll.setDocumentView_(self.journal_text)
            window.setContentView_(scroll)
            self.journal_window = window
        self.journal_text.setString_(self.journal.read())
        self.journal_window.makeKeyAndOrderFront_(None)
        if self.journal_refresh_timer is None:
            self.journal_refresh_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.5, self, "refreshJournal:", None, True
            )

    @objc.IBAction
    def refreshJournal_(self, _sender):
        if self.journal_window is None or not self.journal_window.isVisible():
            return
        next_text = self.journal.read()
        if str(self.journal_text.string()) == next_text:
            return
        scroll = self.journal_text.enclosingScrollView()
        clip = scroll.contentView() if scroll is not None else None
        origin = clip.bounds().origin if clip is not None else None
        document_height = self.journal_text.bounds().size.height
        visible_height = clip.bounds().size.height if clip is not None else 0
        following = origin is None or origin.y + visible_height >= document_height - 24
        selection = self.journal_text.selectedRange()
        self.journal_text.setString_(next_text)
        if following:
            self.journal_text.scrollRangeToVisible_((len(next_text), 0))
        elif clip is not None:
            clip.scrollToPoint_(origin)
            self.journal_text.setSelectedRange_(selection)

    def cancelCurrentOperation_(self, _sender=None):
        self.cancel_event.set()
        self.enrichment_paused_by_cancel = True
        self.enrichment_cancel_event.set()
        self._journal("Cancel", "Cancellation requested.")

    def revealLogFile_(self, _sender=None):
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(self.journal.path))])

    @objc.python_method
    def _alert(self, title, detail=""):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        if detail:
            alert.setInformativeText_(detail)
        alert.runModal()

    @objc.python_method
    def shutdown(self):
        network_availability.remove_listener(self._network_state_changed)
        self._reset_optional_enrichment()
        self._stop_media_watcher()
        was_demo = bool(self.demo_mode)
        if self.workspace_autosave_timer is not None:
            self.workspace_autosave_timer.invalidate()
            self.workspace_autosave_timer = None
        if self.workspace_autosave_future is not None:
            try:
                self.workspace_autosave_future.result()
            except Exception:
                pass
            self._finish_background_gpx_checkpoint()
        save_failed = False
        if self.adventure_file is not None and (
            self.workspace_dirty_components
            or self.gpx_controller.dirty
            or (
                self.full_gui_controller is not None
                and (
                    self.full_gui_controller.project_dirty
                    or self.full_gui_controller.control_table_dirty
                )
            )
        ):
            saved = self._flush_workspace_autosave(show_errors=False)
            if not saved:
                saved = self._flush_workspace_autosave(show_errors=False)
            save_failed = not saved
            if save_failed:
                self.recovery_dirty = True
                self.writeRecovery_(None)
                self._alert(
                    "Adventure autosave failed before quitting.",
                    f"myCamino retried the save and retained recovery data in:\n{self.recovery.directory}",
                )
        if self.recovery_write_future is not None:
            try:
                self.recovery_write_future.result()
            except Exception:
                pass
            self.recovery_write_future = None
        if self.demo_coordinator is not None and was_demo:
            self.demo_coordinator.suspendForShutdown()
        if not save_failed and was_demo:
            self.recovery.discard()
        elif not save_failed and (self.recovery_dirty or self.gpx_controller.dirty) and self._has_recoverable_workspace_content():
            self.recovery_dirty = True
            self.writeRecovery_(None)
        elif not save_failed:
            self.recovery.discard()
        self.cancel_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.project_access.stop()
        if self.parameter_editor_controller is not None:
            self.parameter_editor_controller.close()
            self.parameter_editor_controller = None
        if self.recovery_periodic_timer is not None:
            self.recovery_periodic_timer.invalidate()
        if self.journal_refresh_timer is not None:
            self.journal_refresh_timer.invalidate()
        for window in self.document_windows.values():
            try:
                window.orderOut_(None)
                window.close()
            except Exception:
                pass
        self.document_windows.clear()
        if self.help_window is not None:
            try:
                self.help_window.orderOut_(None)
                self.help_window.close()
            except Exception:
                pass
            self.help_window = None
        try:
            self.gpx_controller.close_main_editor_window(delete_recovery=False, force=True)
        except Exception:
            pass


class _BlockTimer(NSObject):
    def initWithBlock_(self, block):
        self = objc.super(_BlockTimer, self).init()
        if self is not None:
            self.block = block
        return self

    def fire_(self, timer):
        self.block(timer)


def _menu_item(title, action, target, key="", modifiers=NSEventModifierFlagCommand):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    item.setTarget_(target)
    if key:
        item.setKeyEquivalentModifierMask_(modifiers)
    return item


def build_adventure_map_menu(controller):
    """Build the complete native workflow menu for the map-first workspace."""
    main_menu = NSMenu.alloc().initWithTitle_("Main Menu")

    application_menu = add_menu(main_menu, "myCamino")
    application_menu.addItem_(menu_item("About myCamino", "showAbout:", controller))
    application_menu.addItem_(menu_item("Settings…", "showSettings:", controller, ",", NSEventModifierFlagCommand))
    application_menu.addItem_(NSMenuItem.separatorItem())
    application_menu.addItem_(menu_item("License", "showProjectLicense:", controller))
    application_menu.addItem_(menu_item("App Store Distribution Exception", "showAppStoreException:", controller))
    application_menu.addItem_(menu_item("App Store EULA Draft", "showAppStoreEULA:", controller))
    application_menu.addItem_(menu_item("Contributor Agreement", "showContributorAgreement:", controller))
    application_menu.addItem_(menu_item("Third-Party Notices", "showThirdPartyNotices:", controller))
    install_application_news(application_menu)
    application_menu.addItem_(NSMenuItem.separatorItem())
    application_menu.addItem_(menu_item("Quit myCamino", "terminate:", NSApp(), "q", NSEventModifierFlagCommand))

    file_menu = add_menu(main_menu, "File")
    file_menu.addItem_(menu_item("Open Adventure or Project…", "openAdventureOrProject:", controller, "o", NSEventModifierFlagCommand))
    recent_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Open Recent", None, "")
    recent_menu = NSMenu.alloc().initWithTitle_("Open Recent")
    recent_root.setSubmenu_(recent_menu)
    controller.recent_adventure_menu = recent_menu
    controller._refresh_recent_adventure_menu()
    file_menu.addItem_(recent_root)
    file_menu.addItem_(menu_item("Save Workspace", "saveWorkspace:", controller, "s", NSEventModifierFlagCommand))
    file_menu.addItem_(menu_item("Save Workspace As…", "saveWorkspaceAs:", controller))

    adventure_menu = add_menu(main_menu, "Adventure")
    adventure_menu.addItem_(menu_item("Adventure Properties…", "showAdventureProperties:", controller))
    adventure_menu.addItem_(menu_item("Advanced Interface", "openAdventureGUI:", controller, "g", NSEventModifierFlagCommand))
    adventure_menu.addItem_(NSMenuItem.separatorItem())
    adventure_menu.addItem_(menu_item("Show Adventure Overview", "showAdventureOverview:", controller, "0", NSEventModifierFlagCommand))
    adventure_menu.addItem_(menu_item("Reveal Project Folder", "revealProjectFolder:", controller))

    track_menu = add_menu(main_menu, "Track")
    for title, action in (
        ("Add GPX Files…", "addGPXFiles:"),
        ("Track Table", "showTrackTable:"),
        ("Waypoint Table", "showWaypointTable:"),
        ("Elevation Profile", "showElevationProfile:"),
        ("Fit to Track", "fitToTrack:"),
        ("Estimate Route Times from Media…", "estimateRouteTimesFromMedia:"),
        ("Renumber Tracks", "renumberTracks:"),
        ("Save Selected Tracks As…", "saveSelectedTracksAs:"),
    ):
        track_menu.addItem_(menu_item(title, action, controller))

    media_menu = add_menu(main_menu, "Media")
    for title, action in (
        ("Import Media…", "importMedia:"),
        ("View Media", "viewMedia:"),
        ("Reveal Media Folder", "revealMediaFolder:"),
        ("Create Track from Selected Media", "createTrackFromSelectedMedia:"),
        ("Add Place Names", "addPlaceNames:"),
        ("Add Historical Weather", "addHistoricalWeather:"),
        ("Update Metadata", "updateMetadata:"),
    ):
        media_menu.addItem_(menu_item(title, action, controller))
    media_menu.addItem_(NSMenuItem.separatorItem())
    media_menu.addItem_(menu_item("Media Settings…", "showMediaSettings:", controller))

    maps_menu = add_menu(main_menu, "Maps")
    for title, action in (
        ("Generate and Update Maps…", "generateMaps:"),
        ("View Maps", "viewMaps:"),
        ("Reveal Maps", "revealMaps:"),
        ("Cancel Generation", "cancelMapGeneration:"),
    ):
        maps_menu.addItem_(menu_item(title, action, controller))
    maps_menu.addItem_(NSMenuItem.separatorItem())
    maps_menu.addItem_(menu_item("Map Settings…", "showMapSettings:", controller))

    control_menu = add_menu(main_menu, "Control File")
    for title, action in (
        ("Choose Control File…", "chooseControlFile:"),
        ("Create Control File", "createControlFile:"),
        ("Edit Control File", "openControlFile:"),
        ("Update Control File", "updateControlFile:"),
        ("Reveal Control File", "revealControlFile:"),
    ):
        control_menu.addItem_(menu_item(title, action, controller))

    audio_menu = add_menu(main_menu, "Audio")
    audio_menu.addItem_(menu_item("Enable Audio", "toggleAudioEnabled:", controller))
    audio_menu.addItem_(NSMenuItem.separatorItem())
    for title, action in (
        ("Choose Music Playlist…", "chooseMusicPlaylist:"),
        ("Create Music Playlist", "createMusicPlaylist:"),
        ("Update Music Playlist", "updateMusicPlaylist:"),
        ("Edit Music Playlist", "editMusicPlaylist:"),
        ("Reveal Music Folder", "revealMusicFolder:"),
        ("Choose Narration Playlist…", "chooseNarrationPlaylist:"),
        ("Create Narration Playlist", "createNarrationPlaylist:"),
        ("Update Narration Playlist", "updateNarrationPlaylist:"),
        ("Edit Narration Playlist", "editNarrationPlaylist:"),
        ("Reveal Narration Folder", "revealNarrationFolder:"),
        ("Normalize Video Audio", "normalizeVideoAudio:"),
        ("Audio Directive Help", "showAudioDirectiveHelp:"),
    ):
        audio_menu.addItem_(menu_item(title, action, controller))
    audio_menu.addItem_(NSMenuItem.separatorItem())
    audio_menu.addItem_(menu_item("Audio Settings…", "showAudioSettings:", controller))

    slideshow_menu = add_menu(main_menu, "Slide Show")
    slideshow_menu.addItem_(menu_item("Start", "startSlideShow:", controller))
    slideshow_menu.addItem_(menu_item("Continue…", "continueSlideShow:", controller))
    slideshow_menu.addItem_(menu_item("Choose Start Position…", "chooseSlideShowStart:", controller))
    slideshow_menu.addItem_(menu_item("PDF Summary…", "exportPDFSummary:", controller))
    slideshow_menu.addItem_(NSMenuItem.separatorItem())
    slideshow_menu.addItem_(menu_item("Slide Show Settings…", "showSlideShowSettings:", controller))

    window_menu = add_menu(main_menu, "Window")
    coordinator = WindowMenuCoordinator.alloc().init()
    coordinator.status_provider = controller.window_status_for_window
    coordinator.attach(window_menu)
    controller.window_menu_coordinator = coordinator

    help_menu = add_menu(main_menu, "Help")
    help_menu.addItem_(menu_item("Quick Start", "showQuickStart:", controller))
    help_menu.addItem_(menu_item("Start Guided Demo", "startGuidedDemo:", controller))
    help_menu.addItem_(NSMenuItem.separatorItem())
    help_menu.addItem_(menu_item("Adventure Map Help", "showHelp:", controller))
    help_menu.addItem_(menu_item("GPX Editor Help", "showGPXEditorHelp:", controller))
    help_menu.addItem_(menu_item("Control Directive Help", "showControlDirectiveHelp:", controller))
    help_menu.addItem_(menu_item("Music and Narration Help", "showAudioDirectiveHelp:", controller))
    return main_menu


class AdventureMapAppDelegate(NSObject):
    def initWithProjectDirectory_projectFile_(self, project_directory, project_file):
        self = objc.super(AdventureMapAppDelegate, self).init()
        if self is not None:
            self.project_directory = project_directory
            self.project_file = project_file
        return self

    def applicationDidFinishLaunching_(self, _notification):
        logo = bundled_resource_path("MyCaminoLogo-ohneText.png")
        configure_mycamino_branding(logo)
        self.controller = AdventureMapController.alloc().initWithProjectDirectory_projectFile_(
            self.project_directory, self.project_file
        )
        self.main_menu = build_adventure_map_menu(self.controller)
        NSApp().setMainMenu_(self.main_menu)
        self.controller.show()
        NSApp().activateIgnoringOtherApps_(True)
        self.demo_coordinator = GuidedDemoCoordinator.alloc().initWithWorkspaceController_(self.controller)
        self.controller.demo_coordinator = self.demo_coordinator
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.35, self.demo_coordinator, "offerAtStartup:", None, False
        )

    def applicationWillTerminate_(self, _notification):
        if getattr(self, "controller", None) is not None:
            self.controller.shutdown()

    def applicationShouldTerminateAfterLastWindowClosed_(self, _application):
        controller = getattr(self, "controller", None)
        return not bool(controller is not None and controller.startup_in_progress)
