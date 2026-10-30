"""Retained nonmodal timestamp diagnostics and estimation tools.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import xml.etree.ElementTree as ET

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSButtonTypeSwitch, NSColor,
    NSControlStateValueOn, NSMakeRect, NSMakeSize, NSOpenPanel, NSPopUpButton, NSScrollView,
    NSTableColumn, NSTableView, NSTextField, NSWindow,
    NSWindowStyleMaskClosable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
from Foundation import NSObject, NSTimer, NSURL

from gpx_point_editing import manual_track_start_time
from gpx_processing import process_track_element
from track_time_estimation import (TimingCancelled, check_cancelled, estimate_track_times,
                                  missing_time_ranges, timing_observations, timing_points_from_element)


TIMING_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mycamino-track-times")


def analyze_track_times(snapshots, options, parameters, mode, context, extra_paths, use_media, cancel, progress):
    """All geometry work and optional basic metadata preparation stay off AppKit."""
    prepared_tracks, results = {}, {}
    for number, xml, _label, _hidden in snapshots:
        check_cancelled(cancel)
        progress(f"Analyzing track times ({len(results) + 1}/{len(snapshots)})…")
        element = ET.fromstring(xml)
        points = timing_points_from_element(element, process_track_element(element, options))
        start = manual_track_start_time(element)
        prepared_tracks[number] = points, start
        results[number] = estimate_track_times(points, mode=mode,
            fallback_speed_kmh=parameters["gpx.fallback_walking_speed_kmh"],
            manual_start_time=start, cancel_event=cancel)
    needs_media = [number for number, result in results.items()
                   if result.unresolved or result.count("extrapolation")]
    if use_media and needs_media:
        from GetGeoLocations import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
        from media_metadata_service import media_paths_from_control_file, prepare_media_records
        from project_media_watcher import discover_project_media

        paths = set()
        project = context.get("project_directory")
        known_paths = context.get("_timing_project_media_paths")
        if known_paths is not None:
            paths.update(Path(path) for path in known_paths)
        elif project:
            paths.update(discover_project_media(Path(project), IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
        control = context.get("control_file")
        if control:
            control = Path(control)
            if not control.is_absolute() and project:
                control = Path(project) / control
            if control.is_file():
                paths.update(media_paths_from_control_file(control, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
        for path in extra_paths:
            path = Path(path)
            if path.is_dir():
                paths.update(discover_project_media(path, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
            elif path.suffix.casefold() == ".lst":
                paths.update(media_paths_from_control_file(path, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
            elif path.suffix.casefold() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                paths.add(path)
        check_cancelled(cancel)
        if paths:
            progress(f"Checking existing metadata for {len(paths)} media files…")
            prepared = prepare_media_records(sorted(paths), cancel_event=cancel,
                progress_callback=lambda current, total, name: progress(f"Media metadata {current}/{total}: {name}"))
            observations, ignored = timing_observations(prepared)
            for number in needs_media:
                check_cancelled(cancel)
                points, start = prepared_tracks[number]
                results[number] = estimate_track_times(points, mode=mode, media=observations,
                    maximum_distance_m=parameters["gpx.media_timing_max_distance_m"],
                    fallback_speed_kmh=parameters["gpx.fallback_walking_speed_kmh"],
                    manual_start_time=start, cancel_event=cancel)
            if ignored:
                progress(f"Ignored {len(ignored)} media without independent GPS/time anchors.")
    check_cancelled(cancel)
    return results


class TrackTimingToolWindow(NSWindow):
    def cancelOperation_(self, _sender):
        self.tool.dismiss_(None)


class TrackTimingTool(NSObject):
    def initWithEditor_purpose_(self, editor, purpose):
        self = objc.super(TrackTimingTool, self).init()
        if self is None:
            return None
        self.editor, self.purpose = editor, purpose
        self.rows, self.results, self.snapshots = [], {}, []
        self.extra_paths = []
        self.cancel_event = threading.Event()
        self.future = self.timer = None
        self.progress_text = ""
        self.offered = []
        self.applied_context = None
        self.build()
        return self

    @objc.python_method
    def build(self):
        self.window = TrackTimingToolWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 180, 940, 480), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        self.window.tool = self
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.setTitle_("Missing Timestamps" if self.purpose == "issues" else "Estimate Track Times")
        self.window.setContentMinSize_(NSMakeSize(840, 430))
        self.editor.register_auxiliary_window(self.window)
        root = self.window.contentView()
        self.info = NSTextField.labelWithString_("")
        root.addSubview_(self.info)
        self.scope = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 180, 24), False)
        self.scope.addItemsWithTitles_(["All tracks", "Selected tracks"])
        self.scope.setTarget_(self)
        self.scope.setAction_("optionsChanged:")
        if self.purpose == "estimate" and self.editor.selected_nrs:
            self.scope.selectItemAtIndex_(1)
        root.addSubview_(self.scope)
        self.mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 280, 24), False)
        self.mode.addItemsWithTitles_(["Fill Missing Times", "Refresh Previously Estimated Times"])
        self.mode.setTarget_(self)
        self.mode.setAction_("optionsChanged:")
        self.mode.setHidden_(self.purpose != "estimate")
        root.addSubview_(self.mode)
        self.flag = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 500, 24))
        self.flag.setButtonType_(NSButtonTypeSwitch)
        self.flag.setTitle_("Do not ask again for these tracks" if self.purpose == "issues" else "Use Adventure media and additional media where needed")
        self.flag.setState_(0 if self.purpose == "issues" else 1)
        self.flag.setTarget_(self)
        self.flag.setAction_("optionsChanged:")
        root.addSubview_(self.flag)
        self.scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 110, 900, 230))
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setHasHorizontalScroller_(True)
        self.table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 900, 230))
        self.table.setAllowsMultipleSelection_(True)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setDataSource_(self)
        self.table.setDelegate_(self)
        self.table.setTarget_(self)
        self.table.setDoubleAction_("inspect:")
        for key, title, width in (("track", "Stage / Track — Name", 270), ("segment", "Segment", 65),
                ("range", "Waypoint rows", 110), ("count", "Missing", 65),
                ("before", "Previous time", 180), ("after", "Next time / Result", 270)):
            column = NSTableColumn.alloc().initWithIdentifier_(key)
            column.headerCell().setStringValue_(title)
            column.setWidth_(width)
            column.setEditable_(False)
            self.table.addTableColumn_(column)
        self.scroll.setDocumentView_(self.table)
        root.addSubview_(self.scroll)
        self.status = NSTextField.labelWithString_("")
        root.addSubview_(self.status)
        self.buttons = {}
        actions = (("Inspect", "inspect:"), ("Estimate Times…", "estimate:"),
                   ("Ask Again", "askAgain:"), ("Ignore", "ignore:")) if self.purpose == "issues" else (
                   ("Choose Additional Media…", "chooseMedia:"), ("Set Start Time…", "setStart:"),
                   ("Analyze", "analyze:"), ("Apply", "apply:"),
                   ("Save Tracks", "saveTracks:"), ("Cancel", "dismiss:"))
        for title, action in actions:
            button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 120, 28))
            button.setTitle_(title)
            button.setTarget_(self)
            button.setAction_(action)
            root.addSubview_(button)
            self.buttons[title] = button
        if self.purpose == "estimate":
            self.buttons["Apply"].setEnabled_(False)
            self.buttons["Save Tracks"].setEnabled_(False)
            self.buttons["Save Tracks"].setToolTip_(
                "Save the complete GPX document with the applied timestamps. Untitled documents open Save As. Undo remains available.")
        self.layout()

    @objc.python_method
    def layout(self):
        bounds = self.window.contentView().bounds()
        width, height = bounds.size.width, bounds.size.height
        self.info.setFrame_(NSMakeRect(16, height - 48, width - 32, 32))
        self.scope.setFrame_(NSMakeRect(16, height - 80, 180, 24))
        self.mode.setFrame_(NSMakeRect(210, height - 80, 330, 24))
        self.flag.setFrame_(NSMakeRect(16, height - 112, width - 32, 24))
        self.scroll.setFrame_(NSMakeRect(16, 95, width - 32, height - 219))
        self.status.setFrame_(NSMakeRect(16, 59, width - 32, 30))
        x = 16
        widths = {"Choose Additional Media…": 195, "Set Start Time…": 135,
                  "Analyze": 90, "Apply": 80, "Save Tracks": 115, "Cancel": 85}
        for title, button in self.buttons.items():
            size = widths.get(title, 135)
            button.setFrame_(NSMakeRect(x, 16, size, 28))
            x += size + 8

    def windowDidResize_(self, _notification):
        self.layout()

    def windowShouldClose_(self, _window):
        self.dismiss_(None)
        return False

    @objc.python_method
    def show(self, offered=()):
        self.offered = list(offered)
        if self.purpose == "issues":
            self.flag.setState_(0)
            self.flag.setHidden_(not self.offered)
            self.scope.selectItemAtIndex_(0)
            self.refresh()
        elif self.editor.selected_nrs:
            self.scope.selectItemAtIndex_(1)
        self.window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)
        if self.purpose == "estimate":
            self.analyze_(None)

    @objc.python_method
    def tracks(self):
        selected = set(self.editor.selected_nrs)
        return [track for track in self.editor.tracks
                if self.scope.indexOfSelectedItem() == 0 or track.nr in selected]

    @objc.python_method
    def refresh(self):
        if self.purpose != "issues":
            return
        offered = set(self.offered)
        self.rows = []
        for track in self.tracks():
            if offered and track.nr not in offered:
                continue
            for gap in self.editor.timing_ranges(track):
                self.rows.append(dict(nr=track.nr, hidden=track.hidden, first=gap.first, last=gap.last,
                    track=f"{track.display_label}: {track.name}", segment=str(gap.segment + 1),
                    range=f"{gap.first + 1}–{gap.last + 1}", count=str(gap.count),
                    before=str(gap.preceding or "—"), after=str(gap.following or "—")))
        self.table.reloadData()
        count = sum(int(row["count"]) for row in self.rows)
        tracks = len({row["nr"] for row in self.rows})
        self.info.setStringValue_(f"{count} waypoints lack timestamps in {tracks} track(s). Existing timestamps will be preserved.")
        self.status.setStringValue_("Double-click a gap to inspect its original waypoint rows." if self.rows else "No missing timestamps in this scope.")

    def numberOfRowsInTableView_(self, _table):
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(self, _table, column, row):
        return self.rows[row].get(str(column.identifier()), "") if 0 <= row < len(self.rows) else ""

    def tableView_willDisplayCell_forTableColumn_row_(self, _table, cell, _column, row):
        if 0 <= row < len(self.rows):
            cell.setTextColor_(NSColor.secondaryLabelColor() if self.rows[row].get("hidden") else NSColor.labelColor())

    @objc.python_method
    def selected_rows(self):
        indexes = self.table.selectedRowIndexes()
        return [row for index, row in enumerate(self.rows) if indexes.containsIndex_(index)]

    def inspect_(self, _sender):
        clicked = int(self.table.clickedRow())
        rows = self.selected_rows()
        if not rows and 0 <= clicked < len(self.rows):
            rows = [self.rows[clicked]]
        if rows:
            row = rows[0]
            self.editor.inspect_missing_time_range(row["nr"], row.get("first", 0), row.get("last", 0))

    def estimate_(self, _sender):
        rows = self.selected_rows() or self.rows
        self.editor.selected_nrs = list(dict.fromkeys(row["nr"] for row in rows))
        self.editor.estimateTrackTimes_(None)

    def askAgain_(self, _sender):
        rows = self.selected_rows()
        if not rows:
            self.status.setStringValue_("Select the tracks whose reminders should be enabled.")
            return
        self.editor.update_timing_suppressions([row["nr"] for row in rows], suppress=False)
        self.status.setStringValue_("Timing reminders enabled for the selected tracks.")

    def ignore_(self, _sender):
        if self.flag.state() == NSControlStateValueOn and self.offered:
            self.editor.update_timing_suppressions({row["nr"] for row in self.rows}, suppress=True)
        self.dismiss_(None)

    def dismiss_(self, _sender):
        self.cancel_event.set()
        if self.future is not None:
            self.future.cancel()
        self.future = None
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.window.orderOut_(None)

    def optionsChanged_(self, _sender):
        if self.purpose == "issues":
            self.refresh()
        else:
            self.cancel_event.set()
            if self.future is not None:
                self.future.cancel()
            self.results = {}
            self.buttons["Apply"].setEnabled_(False)
            self.progress_text = "Options changed. Choose Analyze to prepare a new estimate."
            self.status.setStringValue_(self.progress_text)

    def chooseMedia_(self, _sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(True)
        panel.setAllowsMultipleSelection_(True)
        project = self.editor.media_context.get("project_directory")
        if project:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(project)))
        if panel.runModal():
            self.extra_paths = [str(url.path()) for url in panel.URLs()]
            self.optionsChanged_(None)

    def setStart_(self, _sender):
        rows = self.selected_rows()
        tracks = self.tracks()
        number = rows[0]["nr"] if rows else (tracks[0].nr if tracks else None)
        if number is not None:
            self.editor.edit_timing_start(number, rows[0].get("first", 0) if rows else 0)

    def analyze_(self, _sender):
        if self.future is not None and not self.future.done():
            return
        self.cancel_event.set()
        if self.future is not None:
            self.future.cancel()
        self.cancel_event = cancel = threading.Event()
        self.results = {}
        self.rows = []
        self.table.reloadData()
        self.snapshots = [(track.nr, ET.tostring(track.element), f"{track.display_label}: {track.name}", track.hidden)
                          for track in self.tracks()]
        self.analysis_settings = dict(self.editor.project_parameters)
        self.analysis_context = dict(self.editor.media_context)
        media_context = dict(self.analysis_context)
        workspace = getattr(self.editor, "adventure_workspace_delegate", None)
        items = getattr(workspace, "media_items", None)
        if items and not getattr(workspace, "processing_active", False):
            media_context["_timing_project_media_paths"] = tuple(str(item.path) for item in items)
        self.buttons["Apply"].setEnabled_(False)
        self.buttons["Analyze"].setEnabled_(False)
        self.info.setStringValue_("Only timestamp values change. Recorded and manual times remain fixed; route direction and geometry are preserved.")
        self.progress_text = "Preparing track times…"
        self.future = TIMING_EXECUTOR.submit(analyze_track_times, list(self.snapshots), self.editor.processing_options(),
            dict(self.analysis_settings), "fill" if self.mode.indexOfSelectedItem() == 0 else "refresh",
            media_context, list(self.extra_paths), self.flag.state() == NSControlStateValueOn,
            cancel, lambda message: self.set_progress(message, cancel))
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.1, self, "poll:", None, True)

    @objc.python_method
    def set_progress(self, message, cancel):
        if cancel is self.cancel_event and not cancel.is_set():
            self.progress_text = str(message)

    def poll_(self, _sender):
        self.status.setStringValue_(self.progress_text)
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        self.buttons["Analyze"].setEnabled_(True)
        if self.cancel_event.is_set():
            return
        try:
            self.results = future.result()
        except Exception as exc:
            self.status.setStringValue_(str(exc))
            return
        self.rows = []
        totals = dict(interpolation=0, media=0, extrapolation=0, unresolved=0)
        for number, _xml, label, hidden in self.snapshots:
            result = self.results[number]
            counts = {key: result.count(key) for key in ("interpolation", "media", "extrapolation")}
            counts["unresolved"] = len(result.unresolved)
            for key, value in counts.items():
                totals[key] += value
            detail = "; ".join(f"{value} {key}" for key, value in counts.items())
            if result.unresolved:
                detail += "; " + result.unresolved[0][1]
            if result.ignored_media:
                detail += "; " + result.ignored_media[0]
            first = result.unresolved[0][0] if result.unresolved else (result.patches[0].index if result.patches else 0)
            self.rows.append(dict(nr=number, hidden=hidden, track=label, first=first, last=first, after=detail))
        self.table.reloadData()
        self.progress_text = "; ".join(f"{value} {key}" for key, value in totals.items())
        self.status.setStringValue_(self.progress_text)
        self.buttons["Apply"].setEnabled_(any(result.patches for result in self.results.values()))

    def apply_(self, _sender):
        if self.analysis_context != self.editor.media_context:
            self.progress_text = "The Adventure changed. Analyze again before applying timestamps."
            self.status.setStringValue_(self.progress_text)
            return
        if self.editor.apply_timing_results(self.snapshots, self.results, self.analysis_settings):
            self.buttons["Apply"].setEnabled_(False)
            self.buttons["Save Tracks"].setEnabled_(True)
            self.applied_context = dict(self.editor.media_context)
            self.results = {}
            self.progress_text = "Estimated timestamps applied. Choose Save Tracks to save now. Undo remains available."
            self.status.setStringValue_(self.progress_text)

    def saveTracks_(self, _sender):
        if self.applied_context is None:
            return
        if self.applied_context != self.editor.media_context:
            self.progress_text = "The Adventure changed. Save the updated tracks from their original editor."
            self.status.setStringValue_(self.progress_text)
            self.buttons["Save Tracks"].setEnabled_(False)
            return
        try:
            saved = self.editor.save_current()
        except (OSError, ValueError, RuntimeError) as exc:
            self.progress_text = f"Could not save updated tracks: {exc}. Changes remain in the editor; Save Tracks can retry."
        else:
            if saved:
                self.buttons["Save Tracks"].setEnabled_(False)
                path = self.editor.current_output_path()
                self.progress_text = f"Updated tracks saved to {Path(path).name}. Undo remains available." if path else "Updated tracks saved. Undo remains available."
            else:
                self.progress_text = "Save cancelled or not completed. Applied timestamps remain in the editor; Save Tracks can retry."
        self.status.setStringValue_(self.progress_text)
        self.status.setToolTip_(self.progress_text)
