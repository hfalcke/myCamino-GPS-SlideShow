"""Shared native-menu and application-branding helpers for myCamino.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import objc
from AppKit import NSApp, NSImage, NSMenu, NSMenuItem
from Foundation import NSObject, NSProcessInfo


def menu_item(title, action, target, key="", modifiers=None):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    item.setTarget_(target)
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
    """Populate a Window menu from the windows currently owned by NSApp."""

    def init(self):
        self = objc.super(WindowMenuCoordinator, self).init()
        if self is not None:
            self.menu = None
            self.status_provider = None
        return self

    @objc.python_method
    def attach(self, menu):
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
        windows = [
            window
            for window in NSApp().windows()
            if str(window.title() or "").strip()
        ]
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
        if window is None:
            return
        if bool(window.isMiniaturized()):
            window.deminiaturize_(None)
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        NSApp().activateIgnoringOtherApps_(True)
