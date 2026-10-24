"""Shared native-menu and application-branding helpers for myCamino.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import objc
from AppKit import (
    NSApp, NSImage, NSMenu, NSMenuItem, NSPanel, NSWindow,
    NSWindowDidBecomeKeyNotification, NSWindowWillCloseNotification,
)
from Foundation import NSNotificationCenter, NSObject, NSProcessInfo
from window_lifecycle import WINDOW_REGISTRY


def register_owned_windows(owner, seen=None):
    """Discover windows from active owners, never from AppKit's hidden history."""
    if owner is None or getattr(owner, "lifecycle_retired", False):
        return
    seen = set() if seen is None else seen
    if id(owner) in seen:
        return
    seen.add(id(owner))
    fields = vars(owner)
    document = fields.get("adventure_file") or fields.get("current_project_file") or fields.get("last_save_path") or ""

    def register(window, role):
        WINDOW_REGISTRY.register(
            window, owner=owner, role=role, document=document,
            listed=not isinstance(window, NSPanel) and not window.isExcludedFromWindowsMenu(),
        )

    for name, value in fields.items():
        if isinstance(value, NSWindow):
            register(value, name)
        elif name in {"document_windows", "license_document_windows"} and isinstance(value, dict):
            for kind, window in value.items():
                if isinstance(window, NSWindow):
                    register(window, f"document:{kind}")
        elif name in {"plot_windows", "auxiliary_windows"} and isinstance(value, list):
            for item in value:
                window = item[0] if isinstance(item, tuple) else item
                if isinstance(window, NSWindow):
                    register(window, f"auxiliary:{id(window)}")
    for name in ("gpx_controller", "gpx_editor_controller", "full_gui_controller", "settings_controller",
                 "parameter_editor_controller", "media_viewer", "demo_coordinator"):
        child = fields.get(name)
        if child is not None:
            register_owned_windows(child, seen)


def retire_owned_windows(owner):
    """Hide obsolete tools without releasing PyObjC graphs on a close callback."""
    register_owned_windows(owner)
    owners = [owner]
    for name in ("parameter_editor_controller", "settings_controller", "media_viewer"):
        child = getattr(owner, name, None)
        if child is not None:
            owners.append(child)
    windows = []
    for item in owners:
        for record in list(WINDOW_REGISTRY.records.values()):
            window = record.window
            if record.owner_id == id(item) and window is not None:
                window.setExcludedFromWindowsMenu_(True)
                window.orderOut_(None)
                windows.append(window)
        WINDOW_REGISTRY.retire_owner(item)
    return windows


class ReusableToolWindowDelegate(NSObject):
    """Hide a reusable tool on close; keep native delegate ownership stable."""

    def windowShouldClose_(self, window):
        window.orderOut_(None)
        return False


class WindowLifecycleObserver(NSObject):
    def windowPresented_(self, notification):
        window = notification.object()
        if not isinstance(window, NSPanel) and not window.isExcludedFromWindowsMenu():
            WINDOW_REGISTRY.presented(window)

    def windowClosed_(self, notification):
        WINDOW_REGISTRY.dismiss(notification.object())


_window_lifecycle_observer = None


def observe_window_lifecycle():
    global _window_lifecycle_observer
    if _window_lifecycle_observer is None:
        _window_lifecycle_observer = WindowLifecycleObserver.alloc().init()
        center = NSNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(_window_lifecycle_observer, "windowPresented:", NSWindowDidBecomeKeyNotification, None)
        center.addObserver_selector_name_object_(_window_lifecycle_observer, "windowClosed:", NSWindowWillCloseNotification, None)


MENU_HELP = {
    "PDF Summary…": "Create a printable track summary, with optional maps and elevation profiles.",
    "Adventure Properties…": "Change the Adventure name, description and title image.",
    "Advanced Interface": "Open the detailed workflow controls and project status.",
    "Track Table": "Select, order and edit the Adventure's tracks.",
    "Waypoint Table": "Inspect and edit individual points in the selected track.",
    "Import Media…": "Add photos and videos to this Adventure.",
    "Update Metadata": "Prepare media dates, GPS coordinates and enabled optional information.",
    "Generate and Update Maps…": "Choose missing or stale project maps to prepare.",
    "Edit Control File": "Edit the slideshow sequence, media and control commands.",
    "Start": "Prepare required assets and play the Adventure slideshow.",
    "Continue…": "Resume the slideshow from a saved position.",
    "Settings…": "Change settings for this workspace and Adventure.",
}


def menu_item(title, action, target, key="", modifiers=None, tooltip=None):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    item.setTarget_(target)
    help_text = tooltip or MENU_HELP.get(title)
    if help_text:
        item.setToolTip_(help_text)
    if key and modifiers is not None:
        item.setKeyEquivalentModifierMask_(modifiers)
    return item


def add_menu(main_menu, title):
    root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    menu = NSMenu.alloc().initWithTitle_(title)
    root.setSubmenu_(menu)
    main_menu.addItem_(root)
    return menu


def configure_mycamino_branding(logo_path: str | Path | None = None):
    """Apply user-facing branding before any application windows are created."""
    process = NSProcessInfo.processInfo()
    setter = getattr(process, "setProcessName_", None)
    if setter is not None:
        setter("myCamino")
    if logo_path is None:
        return
    path = Path(logo_path)
    if path.is_file():
        image = NSImage.alloc().initWithContentsOfFile_(str(path))
        if image is not None:
            NSApp().setApplicationIconImage_(image)


class WindowMenuCoordinator(NSObject):
    """List logical tools, not AppKit's retained native-window history."""

    def init(self):
        self = objc.super(WindowMenuCoordinator, self).init()
        if self is not None:
            self.menu = None
            self.status_provider = None
            self.owner = None
        return self

    @objc.python_method
    def attach(self, menu):
        observe_window_lifecycle()
        self.menu = menu
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(self)
        self.rebuild()

    @objc.python_method
    def _window_status(self, window):
        if callable(self.status_provider):
            custom = self.status_provider(window)
            if custom:
                return str(custom)
        if bool(window.isMiniaturized()):
            return "Minimized"
        if not bool(window.isVisible()):
            return "Hidden"
        if bool(window.isKeyWindow()):
            return "Active"
        return "Visible"

    @objc.python_method
    def rebuild(self):
        if self.menu is None:
            return
        self.menu.removeAllItems()
        register_owned_windows(self.owner)
        windows = [record.window for record in WINDOW_REGISTRY.entries()]
        windows.sort(key=lambda window: (not bool(window.isKeyWindow()), str(window.title()).casefold()))
        if not windows:
            empty = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("No Open Windows", None, "")
            empty.setEnabled_(False)
            self.menu.addItem_(empty)
            return
        for window in windows:
            title = f"{window.title()} - {self._window_status(window)}"
            item = menu_item(title, "activateWindow:", self)
            item.setRepresentedObject_(window)
            item.setEnabled_(True)
            self.menu.addItem_(item)

    def menuWillOpen_(self, _menu):
        self.rebuild()

    @objc.IBAction
    def activateWindow_(self, sender):
        window = sender.representedObject()
        if window is None or not WINDOW_REGISTRY.is_active(window):
            return
        if bool(window.isMiniaturized()):
            window.deminiaturize_(None)
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        NSApp().activateIgnoringOtherApps_(True)
