"""Shared Cocoa button styling for the myCamino tools."""

from __future__ import annotations

from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSCompositingOperationSourceOver,
    NSFont,
    NSFontAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSForegroundColorAttributeName,
    NSInsetRect,
    NSMakePoint,
    NSMakeRect,
    NSShadow,
    NSZeroRect,
)
from Foundation import NSString


class LiquidGlassButton(NSButton):
    """Borderless custom-drawn capsule button with a light glass/accent look."""

    def isFlipped(self):
        return False

    def drawRect_(self, dirty_rect):
        bounds = self.bounds()
        rect = NSInsetRect(bounds, 1.6, 2.4)
        radius = min(13.0, rect.size.height / 2.0)
        enabled = bool(self.isEnabled())
        highlighted = bool(self.isHighlighted())

        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)

        NSGraphicsContext.saveGraphicsState()
        shadow = NSShadow.alloc().init()
        shadow.setShadowOffset_((0.0, -1.5))
        shadow.setShadowBlurRadius_(8.0)
        shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.18 if enabled else 0.06))
        shadow.set()

        if highlighted:
            top = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.38, 0.74, 1.0, 0.72)
            bottom = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.40, 0.86, 0.70)
        elif enabled:
            top = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.78)
            bottom = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.52, 0.78, 1.0, 0.38)
        else:
            top = NSColor.colorWithCalibratedWhite_alpha_(0.90, 0.34)
            bottom = NSColor.colorWithCalibratedWhite_alpha_(0.75, 0.22)

        gradient = NSGradient.alloc().initWithStartingColor_endingColor_(top, bottom)
        gradient.drawInBezierPath_angle_(path, 90.0)
        NSGraphicsContext.restoreGraphicsState()

        stroke = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.86 if enabled else 0.30)
        stroke.setStroke()
        path.setLineWidth_(1.15)
        path.stroke()

        accent_rect = NSInsetRect(rect, 0.9, 0.9)
        accent = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(accent_rect, radius - 1.0, radius - 1.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.56, 1.0, 0.22 if enabled else 0.08).setStroke()
        accent.setLineWidth_(0.9)
        accent.stroke()

        shine_rect = NSMakeRect(rect.origin.x + 3.0, rect.origin.y + rect.size.height * 0.55, rect.size.width - 6.0, rect.size.height * 0.33)
        shine = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(shine_rect, radius * 0.55, radius * 0.55)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.26 if enabled else 0.08).setFill()
        shine.fill()

        self._draw_content_in_rect(rect, enabled)

    def _draw_content_in_rect(self, rect, enabled: bool):
        title = str(self.title() or "")
        image = self.image()
        if image is not None and not title:
            size = image.size()
            image_rect = NSMakeRect(
                rect.origin.x + (rect.size.width - size.width) / 2.0,
                rect.origin.y + (rect.size.height - size.height) / 2.0,
                size.width,
                size.height,
            )
            image.drawInRect_fromRect_operation_fraction_(
                image_rect,
                NSZeroRect,
                NSCompositingOperationSourceOver,
                0.88 if enabled else 0.35,
            )
            return

        font = NSFont.systemFontOfSize_weight_(12.4, 0.22)
        color = (
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.13, 0.22, 0.94)
            if enabled
            else NSColor.colorWithCalibratedWhite_alpha_(0.30, 0.48)
        )
        attributes = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        text = NSString.stringWithString_(title)
        text_size = text.sizeWithAttributes_(attributes)
        text_point = NSMakePoint(
            rect.origin.x + (rect.size.width - text_size.width) / 2.0,
            rect.origin.y + (rect.size.height - text_size.height) / 2.0 + 0.5,
        )
        text.drawAtPoint_withAttributes_(text_point, attributes)


def make_liquid_glass_button(frame):
    button = LiquidGlassButton.alloc().initWithFrame_(frame)
    return apply_liquid_glass_button_style(button)


def apply_liquid_glass_button_style(button, *, compact: bool = False):
    """Configure a button to use the custom liquid-glass drawing path."""
    button.setBordered_(False)
    if hasattr(button, "setWantsLayer_"):
        button.setWantsLayer_(False)
    return button
