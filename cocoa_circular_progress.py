# SPDX-License-Identifier: GPL-3.0-or-later
"""Small determinate progress view used by the Adventure Map."""

from __future__ import annotations

import objc
from AppKit import NSBezierPath, NSColor, NSMakePoint, NSMenu, NSMenuItem, NSView
from Foundation import NSTimer


def progress_fraction(current, total):
    """Return a clamped fraction, or None when no total is known."""
    try:
        total = float(total)
        current = float(current)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return min(max(current / total, 0.0), 1.0)


class CircularProgressView(NSView):
    def initWithFrame_owner_(self, frame, owner):
        self = objc.super(CircularProgressView, self).initWithFrame_(frame)
        if self is not None:
            self.owner = owner
            self.fraction = None
            self.rotation = 0.0
            self.animation_timer = None
            self.hide_timer = None
            self.setHidden_(True)
        return self

    def setProgress_current_total_(self, active, current, total):
        if not active:
            if self.animation_timer is not None:
                self.animation_timer.invalidate()
                self.animation_timer = None
            if not self.isHidden() and self.fraction is not None:
                self.fraction = 1.0
                self.setNeedsDisplay_(True)
                if self.hide_timer is not None:
                    self.hide_timer.invalidate()
                self.hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.35, self, "hideAfterCompletion:", None, False
                )
            else:
                self.setHidden_(True)
            return
        if self.hide_timer is not None:
            self.hide_timer.invalidate()
            self.hide_timer = None
        self.fraction = progress_fraction(current, total)
        self.setHidden_(False)
        if active and self.fraction is None and self.animation_timer is None:
            self.animation_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.08, self, "advanceAnimation:", None, True
            )
        elif (not active or self.fraction is not None) and self.animation_timer is not None:
            self.animation_timer.invalidate()
            self.animation_timer = None
        self.setNeedsDisplay_(True)

    def hideAfterCompletion_(self, _timer):
        self.hide_timer = None
        self.setHidden_(True)

    def advanceAnimation_(self, _timer):
        self.rotation = (self.rotation + 18.0) % 360.0
        self.setNeedsDisplay_(True)

    def drawRect_(self, _dirty_rect):
        bounds = self.bounds()
        radius = max(2.0, min(bounds.size.width, bounds.size.height) / 2.0 - 1.0)
        center = NSMakePoint(bounds.size.width / 2.0, bounds.size.height / 2.0)
        NSColor.colorWithCalibratedWhite_alpha_(0.88, 0.92).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(bounds).fill()
        fraction = self.fraction
        if fraction is None:
            fraction = 0.25
            start = 90.0 - self.rotation
        else:
            start = 90.0
        if fraction > 0.0:
            sector = NSBezierPath.bezierPath()
            sector.moveToPoint_(center)
            sector.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                center, radius, start, start - 360.0 * fraction, True
            )
            sector.closePath()
            NSColor.colorWithCalibratedWhite_alpha_(0.22, 0.94).setFill()
            sector.fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.05, 0.72).setStroke()
        outline = NSBezierPath.bezierPathWithOvalInRect_(bounds)
        outline.setLineWidth_(1.0)
        outline.stroke()

    def mouseDown_(self, _event):
        self.owner.showProcessingDetails_(None)

    def rightMouseDown_(self, event):
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

    def removeFromSuperview(self):
        if self.animation_timer is not None:
            self.animation_timer.invalidate()
            self.animation_timer = None
        if self.hide_timer is not None:
            self.hide_timer.invalidate()
            self.hide_timer = None
        objc.super(CircularProgressView, self).removeFromSuperview()
