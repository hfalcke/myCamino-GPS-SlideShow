"""Map-first Cocoa workspace for myCamino Adventures.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import concurrent.futures
import copy
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

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
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
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
from cocoa_parameter_editor import CocoaParameterEditor
from control_media_inventory import (
    build_control_media_inventory_payload,
    classify_project_media,
    load_control_media_inventory,
    mark_imported_media,
    write_control_media_inventory,
)
from GPXEditor import (
    FILE_DRAG_TYPE,
    GPXEditorController,
    bundled_resource_path,
    lonlat_to_web_mercator,
)
from json_storage import atomic_write_json
from interactive_tile_viewport import tile_zoom_for_viewport
from license_resources import read_license_document
from media_metadata_service import media_paths_from_control_file, prepare_media_records
from plot_metadata_utils import media_sidecar_freshness, media_sidecar_path, validate_media_sidecar
from project_media_watcher import ProjectMediaWatcher


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
    def __init__(self, journal, phase):
        self.journal = journal
        self.phase = phase
        self.pending = ""

    def write(self, value):
        self.pending += str(value)
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip():
                self.journal.append(line, phase=self.phase)
        return len(str(value))

    def flush(self):
        if self.pending.strip():
            self.journal.append(self.pending, phase=self.phase)
        self.pending = ""


class WorkspaceProgressIndicator(NSProgressIndicator):
    def initWithFrame_owner_(self, frame, owner):
        self = objc.super(WorkspaceProgressIndicator, self).initWithFrame_(frame)
        if self is not None:
            self.owner = owner
        return self

    def mouseDown_(self, event):
        self.owner.showProcessingDetails_(None)

    def rightMouseDown_(self, event):
        from AppKit import NSMenu, NSMenuItem
        menu = NSMenu.alloc().initWithTitle_("Processing")
        for title, action in (
            ("Show Processing Details", "showProcessingDetails:"),
            ("Cancel Current Operation", "cancelCurrentOperation:"),
            ("Reveal Log File", "revealLogFile:"),
        ):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self.owner)
            menu.addItem_(item)
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)


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
        self.adventure_name = "Untitled Adventure"
        self.control_file = None
        self.control_rows = []
        self.temporary_control_model = False
        self.media_items: list[MediaMapItem] = []
        self.media_clusters = []
        self.media_hit_targets = []
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
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="mycamino-workspace")
        self.cancel_event = threading.Event()
        self.processing_generation = 0
        self.media_index_generation = 0
        self.processing_active = False
        self.processing_current = 0
        self.processing_total = 0
        self.processing_phase = ""
        self.background_progress = {}
        self.pending_media: set[Path] = set()
        self.auto_processed_paths: set[Path] = set()
        self.pending_start_after_processing = False
        self.pending_control_media: set[Path] = set()
        self.media_watch_seen_paths: set[Path] = set()
        self.media_watcher = None
        self.parameter_editor_controller = None
        self.properties_window = None
        self.properties_name_field = None
        self.properties_project_field = None
        self.properties_description_text = None
        self.properties_title_image_field = None
        self.properties_name_unlocked = False
        self.window_menu_coordinator = None
        self.recovery = WorkspaceRecoverySession()
        self.recovery_dirty = False
        self.recovery_debounce_timer = None
        self.recovery_periodic_timer = None
        self.journal = ProcessingJournal(self.recovery.directory / "logs")
        self.journal_window = None
        self.journal_text = None
        self.journal_refresh_timer = None
        self.startup_in_progress = False
        self.startup_map_generation = 0
        self.startup_extent_applied = False
        self.pending_startup_gpx_path = None
        self.startup_track_loading = False
        self.startup_media_scan_deferred = False
        self.startup_watcher_deferred = False
        self.startup_post_track_work_pending = False
        self.startup_pending_media_paths: set[Path] = set()
        self.document_windows = {}
        self.help_window = None
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
            self.gpx_controller.create_empty_track(name="New Track", mark_dirty=False)
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
        self.progress_indicator = WorkspaceProgressIndicator.alloc().initWithFrame_owner_(
            NSMakeRect(old_bounds.size.width - 30, old_bounds.size.height - 30, 20, 20), self
        )
        self.progress_indicator.setStyle_(NSProgressIndicatorStyleSpinning)
        self.progress_indicator.setDisplayedWhenStopped_(False)
        self.progress_indicator.setAutoresizingMask_(1 << 0 | 1 << 4)
        container.addSubview_(self.progress_indicator)
        self.progress_label = NSTextField.labelWithString_("")
        self.progress_label.setFrame_(NSMakeRect(old_bounds.size.width - 220, old_bounds.size.height - 30, 184, 20))
        self.progress_label.setAlignment_(2)
        self.progress_label.setDrawsBackground_(False)
        self.progress_label.setBezeled_(False)
        self.progress_label.setTextColor_(NSColor.whiteColor())
        self.progress_label.setAutoresizingMask_(1 << 0 | 1 << 4)
        container.addSubview_(self.progress_label)
        window.setContentView_(container)
        window.setMinSize_(NSMakeSize(920, 560))
        window.makeFirstResponder_(view)
        view._adventure_controls_installed = True
        if view.interactive_tiles_active:
            view.schedule_interactive_tiles(immediate=True)
        self._refresh_progress_indicator()
        view.setNeedsDisplay_(True)

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
        if action in {"showTrackTable:", "showWaypointTable:", "showElevationProfile:", "fitToTrack:", "renumberTracks:"}:
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
        self.plot_view.replace_plot_info(plot_info, preserve_tiles=True)
        self._set_title()
        self.plot_view.setNeedsDisplay_(True)
        return self.plot_view

    @objc.IBAction
    def showAdventureOverview_(self, _sender):
        self.show_workspace_overview()

    @objc.python_method
    def load_project_directory(self, directory: Path):
        self._stop_media_watcher()
        self.startup_track_loading = False
        self.startup_media_scan_deferred = False
        self.startup_watcher_deferred = False
        self.startup_post_track_work_pending = False
        self.startup_pending_media_paths.clear()
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
        self._stop_media_watcher()
        self.startup_track_loading = False
        self.startup_media_scan_deferred = False
        self.startup_watcher_deferred = False
        self.startup_post_track_work_pending = False
        self.startup_pending_media_paths.clear()
        try:
            record = load_adventure(path)
        except AdventureFormatError as exc:
            self._alert("Could not load Adventure.", str(exc))
            return
        self.adventure_file = record.path
        self.overview_viewport = None
        self.track_viewports.clear()
        self.auto_processed_paths.clear()
        self.adventure_payload = dict(record.payload)
        self.project_directory = record.path.parent
        self.adventure_name = record.project_name
        self.control_file = self.project_directory / str(record.payload.get("control_file", ""))
        self.temporary_control_model = not self.control_file.is_file()
        self.parameters = normalize_parameters(record.payload.get("parameters") or {})
        stored_consent = str(record.payload.get("weather_consent", "")).casefold()
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
        gpx = self.project_directory / str(record.payload.get("gpx_file", ""))
        # Put a usable world canvas on screen before a large GPX finishes loading.
        self.gpx_controller.tracks = []
        self.gpx_controller.next_nr = 1
        self._show_empty_world()
        self.gpx_controller.tracks = []
        self.gpx_controller.next_nr = 1
        if gpx.is_file():
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
            self.plot_view.set_interactive_viewport(extent, zoom)
            self.overview_viewport = {"extent": dict(extent), "zoom": int(zoom)}
            self.startup_extent_applied = True

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.02, _BlockTimer.alloc().initWithBlock_(poll), "fire:", None, True
        )

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
            self.gpx_controller.create_empty_track(name="New Track", mark_dirty=False)
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
        gpx_path = self.project_directory / str(self.adventure_payload.get("gpx_file", ""))
        if not gpx_path.is_file():
            return
        output_dir = self.project_directory / "trackimages"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._journal("Derived Track Data", f"Checking {len(self.gpx_controller.tracks)} track(s).")
        self._set_background_progress(
            "derived", "Updating track data", 0, len(self.gpx_controller.tracks)
        )
        def work():
            from gpx_tracks_table import upgrade_timed_track_sidecars
            return upgrade_timed_track_sidecars(gpx_path, output_dir)
        future = self.executor.submit(work)
        def poll(timer):
            if not future.done():
                return
            timer.invalidate()
            self._clear_background_progress("derived")
            try:
                report = future.result()
                self._journal(
                    "Derived Track Data",
                    f"Updated {len(report.get('updated', []))}; current {len(report.get('current', []))}; skipped {len(report.get('skipped', []))}.",
                )
            except Exception as exc:
                self._journal("Derived Track Data", f"Failed: {exc}")
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
                    status != "available" or freshness == "changed" or needs_place or needs_weather
                ):
                    pending.append(path)
            return result, pending
        future = self.executor.submit(work)
        def poll(_timer):
            if not future.done():
                return
            _timer.invalidate()
            if generation != self.media_index_generation:
                return
            self._clear_background_progress("media_index")
            try:
                self.media_items, pending = future.result()
            except Exception as exc:
                self._journal("Media index", f"Failed: {exc}")
                return
            available_paths = resolved_media_paths(self.media_items)
            self.selected_media_paths.intersection_update(available_paths)
            if self.selected_media_path not in available_paths:
                self.selected_media_path = None
            if self.plot_view is not None:
                self.plot_view.setNeedsDisplay_(True)
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
        def progress(current, total, name):
            if generation != self.processing_generation:
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_("applyProgress:", ("Metadata", current, total, name), False)
        parameters = dict(self.parameters)
        def prepare_and_enrich():
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
                cancel_event=self.cancel_event,
            )
            usable_paths = [item.path for item in prepared if item.record is not None]
            if usable_paths and bool(parameters.get("locations.add_place_names", True)):
                from GetGeoLocations import run_with_options
                self._journal("Place Names", f"Checking {len(usable_paths)} new media file(s).")
                run_with_options(
                    self.project_directory,
                    photolist=self.control_file or self.project_directory / "photos.lst",
                    redo_reverse_geolocation=True,
                    infer_gps_from_tracks=False,
                    photonames=",".join(path.name for path in usable_paths),
                    distance=float(parameters.get("locations.reuse_radius_m", 150.0)),
                    cancel_event=self.cancel_event,
                    stdout=_JournalWriter(self.journal, "Place Names"),
                    stderr=_JournalWriter(self.journal, "Place Names"),
                )
            if usable_paths and bool(parameters.get("weather.enabled", False)):
                from historical_weather import WeatherOptions, enrich_media_weather
                from map_provider_utils import read_provider_credential
                access = str(parameters.get("weather.access", "free")).casefold()
                credential = str(parameters.get("weather.credential_id", "default"))
                self._journal("Historical Weather", f"Checking {len(usable_paths)} new media file(s).")
                enrich_media_weather(
                    usable_paths,
                    options=WeatherOptions(
                        access="customer" if access == "customer" else "free",
                        api_key=read_provider_credential("open-meteo", credential) if access == "customer" else "",
                        timeout_seconds=float(parameters.get("weather.timeout_seconds", 20.0)),
                        minimum_request_interval_seconds=0.2 if access == "customer" else 1.0,
                    ),
                    detail_callback=lambda message: self._journal("Historical Weather", message),
                    cancel_event=self.cancel_event,
                )
            return prepared
        future = self.executor.submit(prepare_and_enrich)
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
            self.auto_processed_paths.update(paths)
            self._scan_project_media()
            self._start_next_media_batch()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.1, _BlockTimer.alloc().initWithBlock_(poll), "fire:", None, True)

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
                self.progress_indicator.startAnimation_(None)
                self.progress_label.setStringValue_(
                    f"{phase} {current}/{total}" if total else phase
                )
            else:
                self.progress_indicator.stopAnimation_(None)
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
        if not self.media_items:
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
        return menu

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

    def handleWorkspaceKeyEvent_(self, event):
        key = str(event.charactersIgnoringModifiers() or event.characters() or "")
        flags = int(event.modifierFlags())
        command = bool(flags & NSEventModifierFlagCommand)
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
        try:
            atomic_write_json(self.adventure_file, self.adventure_payload)
        except OSError as exc:
            self._journal("Weather", f"Could not save the weather choice: {exc}")

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
            return

        # Build the same stage plan used by map generation, but serialize it
        # directly so this automatic path performs no rendering or tile access.
        from GetGeoLocations import project_control_plan_text, project_map_plan_from_sidecars

        base = filename_base(self.adventure_name)
        self.control_file = self.project_directory / f"{base}-sorted.lst"
        summary_candidate = (
            self.project_directory / "trackimages" / f"{base}-summary.json"
        )
        plan = project_map_plan_from_sidecars(
            self.project_directory,
            self.control_file,
            tracks_summary_path=summary_candidate if summary_candidate.is_file() else None,
            sort_date_sections_by_tracks=(
                str(self.parameters.get("trackmaps.ordering", "track_number"))
                == "track_number"
            ),
            media_map_options={
                "output_dir": str(self.project_directory / "trackimages"),
                "filename_base": base,
            },
            infer_gps_from_tracks=summary_candidate.is_file(),
            place_equivalence_m=float(
                self.parameters.get("locations.reuse_radius_m", 150.0)
            ),
        )
        _atomic_write_text(self.control_file, project_control_plan_text(plan))
        self._load_control_rows()
        self.saveWorkspace_(None)
        if self.control_file is None or not self.control_file.is_file():
            self.pending_control_media.update(paths)
            return
        inventory = load_control_media_inventory(self.control_file)
        included = [
            str(row.get("name", ""))
            for row in self.control_rows
            if str(row.get("type", "")).upper() in {"IMG", "VID"}
            and not bool(row.get("disabled", False))
        ]
        payload = build_control_media_inventory_payload(
            inventory,
            self._project_media_paths(),
            included,
            control_text=self.control_file.read_text(encoding="utf-8"),
        )
        write_control_media_inventory(payload, inventory.path)
        self.openControlFile_(None)

    def performWorkspaceDrop_(self, raw_paths):
        paths = [Path(str(path)).expanduser().resolve(strict=False) for path in raw_paths]
        adventures = [path for path in paths if path.suffix.casefold() == ".adv"]
        directories = [path for path in paths if path.is_dir()]
        if adventures:
            self.load_adventure_path(adventures[0]); return
        if directories and any(list(path.glob("*.adv")) for path in directories):
            self.load_project_directory(directories[0]); return
        if self.project_directory is None and not self._request_project_directory(paths[0] if paths else None):
            return
        gpx_paths = [path for path in paths if path.suffix.casefold() == ".gpx"]
        control_paths = [path for path in paths if path.suffix.casefold() == ".lst"]
        audio_paths = [path for path in paths if path.suffix.casefold() in AUDIO_EXTENSIONS]
        media_paths = self._expand_media_paths([path for path in paths if path not in gpx_paths + control_paths + audio_paths])
        if gpx_paths:
            self.gpx_controller.load_gpx_paths(
                gpx_paths,
                mark_dirty=True,
                offer_sort_after_drop=True,
            )
            self.workspaceDocumentDidChange_("Tracks added")
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
    def _request_project_directory(self, source):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setPrompt_("Use Adventure Folder")
        if source is not None:
            directory = source if source.is_dir() else source.parent
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(directory)))
        if int(panel.runModal()) != 1:
            return False
        self.project_directory = Path(str(panel.URL().path())).resolve()
        self.adventure_name = filename_base(self.project_directory.name)
        self.temporary_control_model = True
        self.journal.move_to(self.project_directory / ".mycamino" / "logs")
        self._set_title()
        return True

    @objc.python_method
    def _import_media_files(self, paths):
        result = []
        for source in paths:
            if self.project_directory is None or source.parent == self.project_directory:
                result.append(source); continue
            target = self.project_directory / source.name
            counter = 2
            while target.exists() and not os.path.samefile(source, target):
                target = self.project_directory / f"{source.stem}-{counter}{source.suffix}"
                counter += 1
            if not target.exists():
                shutil.copy2(source, target)
            result.append(target)
        return result

    @objc.python_method
    def _handle_media_drop(self, paths):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Use {len(paths)} dropped media file(s)")
        alert.setInformativeText_("Create or extend a GPS track from their locations, or add them to the slide show. Metadata is prepared in the background.")
        alert.addButtonWithTitle_("Create or Extend Track")
        alert.addButtonWithTitle_("Add to Slide Show")
        alert.addButtonWithTitle_("Cancel")
        response = int(alert.runModal())
        if response == 1002:
            return
        if self.adventure_file is None and not self._create_adventure_for_media_drop():
            return
        # Resolve the first-drop privacy choice before copying anything.
        if self.weather_consent == "unasked":
            if not self._request_initial_weather_choice():
                return
        imported = self._import_media_files(paths)
        if response == 1001:
            self.pending_control_media.update(imported)
            if self.control_file is not None and self.control_file.is_file():
                try:
                    mark_imported_media(self.control_file, imported)
                except OSError as exc:
                    self._journal(
                        "Media Import",
                        f"Could not record import timestamps: {exc}",
                    )
        self.queue_media_processing(imported)
        if response == 1000:
            self.gpx_controller.media_context["_pending_media_paths"] = [str(path) for path in imported]
            self.gpx_controller.media_context["_skip_media_selection_once"] = True
            self.gpx_controller.addTracksFromPhotos_(None)
        else:
            self._journal(
                "Media Import",
                f"Copied {len(imported)} media file(s); preparing them before control-file review.",
            )

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
        self.recovery_dirty = True
        if self.recovery_debounce_timer is not None:
            self.recovery_debounce_timer.invalidate()
        self.recovery_debounce_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self, "writeRecovery:", None, False
        )

    @objc.IBAction
    def periodicRecovery_(self, _sender):
        if self.recovery_dirty:
            self.writeRecovery_(None)

    @objc.IBAction
    def writeRecovery_(self, _sender):
        if not self.recovery_dirty:
            return
        root = self.gpx_controller.build_root()
        snapshot = RecoverySnapshot(
            datetime.now().astimezone().isoformat(), self.adventure_name,
            ET.tostring(root, encoding="unicode"), copy.deepcopy(self.control_rows),
            str(self.project_directory or ""), copy.deepcopy(self.adventure_payload or {}),
            {}, [{"path": str(item.path)} for item in self.media_items], self.selected_track_identity,
        )
        self.recovery.write(snapshot)
        self.recovery_dirty = False

    @objc.python_method
    def _offer_recovery(self):
        recovery_project = (
            self.adventure_file.parent
            if self.adventure_file is not None
            else self.project_directory
        )
        sessions = discover_recovery_sessions(project_directory=recovery_project)
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
            return
        base = filename_base(self.adventure_name)
        gpx_path = self.project_directory / f"{base}.gpx"
        control_path = self.control_file or self.project_directory / f"{base}-sorted.lst"
        self.gpx_controller.save_to_path(gpx_path)
        if self.control_rows:
            serialize_slideshow_control_row = _main_gui_module().serialize_slideshow_control_row
            _atomic_write_text(
                control_path,
                "\n".join(serialize_slideshow_control_row(row) for row in self.control_rows) + "\n",
            )
        payload = dict(self.adventure_payload or {})
        payload.update({
            "adventure_format_version": 2, "project_name": base,
            "project_directory": str(self.project_directory), "gpx_file": gpx_path.name,
            "control_file": control_path.name, "track_map_base": base,
            "parameters": parameter_payload(self.parameters),
            "weather_consent": self.weather_consent,
        })
        self.adventure_file = self.project_directory / f"{base}.adv"
        atomic_write_json(self.adventure_file, payload)
        self.adventure_payload = payload
        self.control_file = control_path
        self.recovery.discard()
        self.recovery = WorkspaceRecoverySession()
        self.journal.move_to(self.project_directory / ".mycamino" / "logs")
        self._journal("Save", f"Saved {self.adventure_file.name}.")
        if self.media_watcher is None:
            self._start_media_watcher()

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
            if self.adventure_file is not None:
                try:
                    atomic_write_json(self.adventure_file, self.adventure_payload)
                except OSError as exc:
                    self._alert("Could not save Adventure Settings.", str(exc))
                    self.parameters = old
                    self.gpx_controller.apply_project_parameters(old)
                    return False
        if self.full_gui_controller is not None:
            self.full_gui_controller._apply_parameter_values(
                self.parameters, propagate_to_editor=True
            )
        if self.plot_view is not None:
            self.plot_view.setNeedsDisplay_(True)
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

    @objc.IBAction
    def openAdventureOrProject_(self, _sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["adv"])
        if self.project_directory is not None:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.project_directory)))
        if not panel.runModal() or panel.URL() is None:
            return
        path = Path(str(panel.URL().path())).expanduser().resolve(strict=False)
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
        if self.control_file is None or not self.control_file.is_file():
            if self.control_rows:
                self.saveWorkspace_(None)
            else:
                self._alert("No control file yet.", "Drop media or a control file first.")
                return
        controller = self._ensure_full_gui(show_window=False)
        if controller is not None:
            controller.editControlFile_(None)

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
                "Click a track to select it. The selected track is dark blue; other tracks are gray, and marked ranges are red.\n\n"
                "Double-click a track to open the Track Table. Double-click it there to open its Waypoint Table. Right-click a track on the map to open either table, fit the view, or show the synchronized Elevation Profile.\n\n"
                "Double-click empty map space, or press Command-0, to return to the complete Adventure overview. Drag the map or use two-finger scrolling to pan.\n\n"
                "MEDIA\n\n"
                "Crowded media are grouped and expand into thumbnails when enough room is available. Click a group to select it; Command-click adds or removes groups. Command-drag from empty map space draws a selection rectangle. Selected media have a red outline.\n\n"
                "Right-click selected media to fit them to the map or create a track from their GPS positions. Double-click a thumbnail or group to open the media viewer. Previous and Next move through all media in the Adventure.\n\n"
                "KEYBOARD SHORTCUTS\n\n"
                "Command-G    Advanced Interface\n"
                "Command-E    Track Editor\n"
                "Command-L    Control File\n"
                "Command-S    Save\n"
                "Command-,    Adventure Settings\n"
                "Command-0    Adventure overview\n"
                "e            Elevation Profile while the map is active\n"
                "Space        Start Slide Show\n"
                "h            Show this Help\n"
                "Command-Q    Quit myCamino\n"
            )
            scroll.setDocumentView_(text_view)
            window.setContentView_(scroll)
            self.help_window = window
        self.help_window.makeKeyAndOrderFront_(None)
        self.help_window.orderFrontRegardless()

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
        self._stop_media_watcher()
        if self.recovery_dirty or self.gpx_controller.dirty:
            self.recovery_dirty = True
            self.writeRecovery_(None)
        else:
            self.recovery.discard()
        self.cancel_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
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
    application_menu.addItem_(menu_item("Third-Party Notices", "showThirdPartyNotices:", controller))
    install_application_news(application_menu)
    application_menu.addItem_(NSMenuItem.separatorItem())
    application_menu.addItem_(menu_item("Quit myCamino", "terminate:", NSApp(), "q", NSEventModifierFlagCommand))

    file_menu = add_menu(main_menu, "File")
    file_menu.addItem_(menu_item("Open Adventure or Project…", "openAdventureOrProject:", controller, "o", NSEventModifierFlagCommand))
    recent_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Open Recent", None, "")
    recent_menu = NSMenu.alloc().initWithTitle_("Open Recent")
    recent_root.setSubmenu_(recent_menu)
    recent_paths = controller.recent_adventure_paths()
    if recent_paths:
        for recent_path in recent_paths:
            recent_item = menu_item(recent_path.name, "openRecentAdventure:", controller)
            recent_item.setRepresentedObject_(str(recent_path))
            recent_menu.addItem_(recent_item)
    else:
        empty_recent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("No Recent Adventures", None, "")
        empty_recent.setEnabled_(False)
        recent_menu.addItem_(empty_recent)
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

    def applicationWillTerminate_(self, _notification):
        if getattr(self, "controller", None) is not None:
            self.controller.shutdown()

    def applicationShouldTerminateAfterLastWindowClosed_(self, _application):
        controller = getattr(self, "controller", None)
        return not bool(controller is not None and controller.startup_in_progress)
