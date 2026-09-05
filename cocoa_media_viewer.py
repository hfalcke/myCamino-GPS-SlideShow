"""Small reusable Cocoa viewer for local image and video references.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSImage,
    NSImageAlignCenter,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSTextField,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSObject


class MediaViewerKeyView(NSView):
    def initWithController_(self, controller):
        self = objc.super(MediaViewerKeyView, self).initWithFrame_(NSMakeRect(0, 0, 900, 620))
        if self is not None:
            self.controller = controller
        return self

    def acceptsFirstResponder(self):
        return True

    def keyDown_(self, event):
        code = int(event.keyCode())
        key = str(event.charactersIgnoringModifiers() or "")
        if code in {123, 126}:
            self.controller.previous_(None)
            return
        if code in {124, 125}:
            self.controller.next_(None)
            return
        if key in {"q", "Q", "\x1b"}:
            self.controller.close_(None)
            return
        objc.super(MediaViewerKeyView, self).keyDown_(event)


class CocoaMediaViewer(NSObject):
    def init(self):
        self = objc.super(CocoaMediaViewer, self).init()
        if self is None:
            return None
        self.paths = []
        self.index = 0
        self.selection_callback = None
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(220, 140, 900, 620), style, NSBackingStoreBuffered, False
        )
        self.window.setReleasedWhenClosed_(False)
        root = MediaViewerKeyView.alloc().initWithController_(self)
        root.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.window.setContentView_(root)
        self.image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(12, 52, 876, 556))
        self.image_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.image_view.setImageAlignment_(NSImageAlignCenter)
        self.image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        root.addSubview_(self.image_view)
        self.message = NSTextField.labelWithString_("")
        self.message.setFrame_(NSMakeRect(110, 16, 680, 24))
        self.message.setAutoresizingMask_(NSViewWidthSizable)
        self.message.setAlignment_(1)
        root.addSubview_(self.message)
        for title, action, x in (("Previous", "previous:", 12), ("Open", "openExternally:", 112), ("Next", "next:", 792)):
            button = NSButton.alloc().initWithFrame_(NSMakeRect(x, 12, 96, 30))
            button.setTitle_(title)
            button.setTarget_(self)
            button.setAction_(action)
            if title == "Next":
                button.setAutoresizingMask_(1)
            root.addSubview_(button)
        return self

    @objc.python_method
    def show_paths(self, paths, index=0, selection_callback=None):
        self.paths = [Path(path).expanduser().resolve(strict=False) for path in paths if Path(path).is_file()]
        if not self.paths:
            return False
        self.index = max(0, min(int(index), len(self.paths) - 1))
        self.selection_callback = selection_callback
        self.refresh()
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.window.contentView())
        return True

    def refresh(self):
        path = self.paths[self.index]
        image = NSImage.alloc().initWithContentsOfFile_(str(path))
        self.image_view.setImage_(image)
        self.window.setTitle_(f"Media {self.index + 1}/{len(self.paths)} - {path.name}")
        if image is None:
            self.message.setStringValue_(f"{path.name} - open with the default application")
        else:
            self.message.setStringValue_(path.name)
        if self.selection_callback is not None:
            self.selection_callback(path)

    @objc.IBAction
    def previous_(self, _sender):
        if self.paths:
            self.index = (self.index - 1) % len(self.paths)
            self.refresh()

    @objc.IBAction
    def next_(self, _sender):
        if self.paths:
            self.index = (self.index + 1) % len(self.paths)
            self.refresh()

    @objc.IBAction
    def openExternally_(self, _sender):
        if self.paths:
            NSWorkspace.sharedWorkspace().openFile_(str(self.paths[self.index]))

    @objc.IBAction
    def close_(self, _sender):
        self.window.orderOut_(None)
