# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable retained Cocoa viewer for bundled plain-text help documents."""

from __future__ import annotations

from AppKit import (
    NSBackingStoreBuffered,
    NSFont,
    NSMakeRect,
    NSMakeSize,
    NSScrollView,
    NSTextView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)

from help_resources import read_help_document


_WINDOWS = {}


def show_help_document(title: str, filename: str):
    window = _WINDOWS.get(filename)
    if window is None:
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(220, 140, 780, 620),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setReleasedWhenClosed_(False)
        window.setMinSize_(NSMakeSize(520, 340))
        window.setTitle_(title)
        scroll = NSScrollView.alloc().initWithFrame_(window.contentView().bounds())
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        text = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setRichText_(False)
        text.setFont_(NSFont.systemFontOfSize_(14.0))
        text.setTextContainerInset_(NSMakeSize(20.0, 18.0))
        text.setString_(read_help_document(filename))
        scroll.setDocumentView_(text)
        window.setContentView_(scroll)
        _WINDOWS[filename] = window
    window.makeKeyAndOrderFront_(None)
    window.orderFrontRegardless()
    return window
