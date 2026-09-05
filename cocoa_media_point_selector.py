"""Cocoa review table for choosing media-derived GPX waypoints.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSButtonCell,
    NSButtonTypeSwitch,
    NSControlStateValueOn,
    NSMakeRect,
    NSScrollView,
    NSSortDescriptor,
    NSTableColumn,
    NSTableView,
)
from Foundation import NSObject

from GetGeoLocations import record_from_sidecar_payload
from plot_metadata_utils import media_sidecar_path, validate_media_sidecar


class MediaPointSelectorDataSource(NSObject):
    def initWithRows_(self, rows):
        self = objc.super(MediaPointSelectorDataSource, self).init()
        if self is not None:
            self.rows = rows
        return self

    def numberOfRowsInTableView_(self, _table):
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(self, _table, column, row):
        return self.rows[row].get(str(column.identifier()), "")

    def tableView_setObjectValue_forTableColumn_row_(self, _table, value, column, row):
        if str(column.identifier()) == "include":
            self.rows[row]["include"] = bool(value)

    def tableView_sortDescriptorsDidChange_(self, table, _old):
        descriptors = list(table.sortDescriptors() or [])
        if not descriptors:
            return
        descriptor = descriptors[0]
        key = str(descriptor.key())
        ascending = bool(descriptor.ascending())
        self.rows.sort(
            key=lambda row: (row.get(key) in {None, ""}, str(row.get(key) or "").casefold()),
            reverse=not ascending,
        )
        table.reloadData()


def _row_for_media(path: Path) -> dict:
    status, payload, reason = validate_media_sidecar(path, media_sidecar_path(path))
    date = gps = place = ""
    if status == "available":
        try:
            record = record_from_sidecar_payload(payload, media_sidecar_path(path), path)
        except Exception as exc:
            status, reason = "invalid", str(exc)
        else:
            date = record.photo_datetime.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if record.latitude is not None and record.longitude is not None:
                gps = f"{record.latitude:.6f}, {record.longitude:.6f}"
            place = record.place or ""
    return {
        "include": True,
        "filename": path.name,
        "date": date,
        "gps": gps,
        "place": place,
        "status": "Available" if status == "available" else f"{status.title()}: {reason or ''}",
        "path": path,
    }


def choose_media_points(paths) -> list[Path] | None:
    """Review candidate media without extracting or modifying metadata."""
    rows = [_row_for_media(Path(path)) for path in paths]
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Choose Media Points")
    alert.setInformativeText_(
        "Only checked files will be prepared and added. Existing sidecar values are shown; "
        "missing metadata remains blank until you continue. Click a column heading to sort."
    )
    alert.addButtonWithTitle_("Prepare Selected")
    alert.addButtonWithTitle_("Cancel")
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 820, 360))
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(True)
    table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 820, 360))
    specs = [
        ("include", "Use", 42),
        ("filename", "Filename", 190),
        ("date", "Exposure Time", 150),
        ("gps", "GPS", 170),
        ("place", "Place", 130),
        ("status", "Sidecar", 170),
    ]
    for key, title, width in specs:
        column = NSTableColumn.alloc().initWithIdentifier_(key)
        column.headerCell().setStringValue_(title)
        column.setWidth_(width)
        column.setSortDescriptorPrototype_(
            NSSortDescriptor.sortDescriptorWithKey_ascending_(key, True)
        )
        if key == "include":
            cell = NSButtonCell.alloc().init()
            cell.setButtonType_(NSButtonTypeSwitch)
            cell.setTitle_("")
            cell.setState_(NSControlStateValueOn)
            column.setDataCell_(cell)
            column.setEditable_(True)
        table.addTableColumn_(column)
    data_source = MediaPointSelectorDataSource.alloc().initWithRows_(rows)
    table.setDataSource_(data_source)
    table.setDelegate_(data_source)
    table.setAllowsMultipleSelection_(True)
    table.setUsesAlternatingRowBackgroundColors_(True)
    scroll.setDocumentView_(table)
    alert.setAccessoryView_(scroll)
    if int(alert.runModal()) != 1000:
        return None
    return [row["path"] for row in rows if row.get("include")]
