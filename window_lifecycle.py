"""Logical window ownership without retaining obsolete controller graphs.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class WindowRecord:
    reference: Callable
    owner_id: int | None
    role: str
    document: str = ""
    listed: bool = True
    presented: bool = False
    retired: bool = False
    dismissed: bool = False

    @property
    def window(self):
        return self.reference()


class WindowRegistry:
    """Separate reusable hidden windows from never-opened and retired windows."""

    def __init__(self):
        self.records = {}

    def register(self, window, *, owner=None, role="window", document="", listed=None):
        key = id(window)
        record = self.records.get(key)
        if record is not None and record.window is window:
            if owner is not None:
                record.owner_id = id(owner)
                record.role = role
                record.document = str(document or "")
            if listed is not None:
                record.listed = bool(listed)
            return record
        try:
            reference = weakref.ref(window)
        except TypeError:
            # PyObjC windows require Objective-C zeroing weak references, not
            # Python weakref support. Import lazily for non-Cocoa tests.
            import objc

            reference = objc.WeakRef(window)
        record = WindowRecord(reference, id(owner) if owner is not None else None, role, str(document or ""))
        if listed is not None:
            record.listed = bool(listed)
        self.records[key] = record
        return record

    def presented(self, window):
        record = self.register(window)
        if record.retired:
            return False
        record.presented = True
        record.dismissed = False
        return True

    def dismiss(self, window):
        record = self.records.get(id(window))
        if record is not None:
            record.dismissed = True

    def retire(self, window):
        self.register(window).retired = True

    def retire_owner(self, owner):
        for record in self.records.values():
            if record.owner_id == id(owner):
                record.retired = True

    def is_active(self, window):
        record = self.records.get(id(window))
        return bool(record is not None and record.window is window and record.presented
                    and record.listed and not record.retired and not record.dismissed)

    def entries(self):
        result = []
        expired = []
        for key, record in self.records.items():
            window = record.window
            if window is None:
                expired.append(key)
                continue
            if record.retired or not record.listed:
                continue
            if window.isVisible() or window.isMiniaturized():
                record.presented = True
                record.dismissed = False
            if record.presented and not record.dismissed and str(window.title() or "").strip():
                result.append(record)
        for key in expired:
            del self.records[key]
        return result


WINDOW_REGISTRY = WindowRegistry()
