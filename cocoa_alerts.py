"""Foreground presentation for blocking startup dialogs.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from AppKit import NSApp, NSModalPanelWindowLevel


def run_foreground_modal_alert(alert):
    """Run on the main thread; keep the alert above ordinary application windows.

    Startup can enter a modal loop before normal application activation. Also,
    callbacks in that loop can bring the main window forward. Explicitly
    activate and raise the alert at modal-panel level to handle both cases.
    Restore its original level afterwards, including when presentation fails.
    """
    NSApp().activateIgnoringOtherApps_(True)
    alert.layout()
    window = alert.window()
    original_level = window.level()
    try:
        window.setLevel_(max(original_level, NSModalPanelWindowLevel))
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()
        return alert.runModal()
    finally:
        window.orderOut_(None)
        window.setLevel_(original_level)
