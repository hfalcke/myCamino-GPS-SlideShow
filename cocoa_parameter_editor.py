#!/usr/bin/env python3
"""Reusable native Cocoa editor for myCamino parameter registries."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSColorSpace,
    NSColorWell,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSFont,
    NSImage,
    NSImageOnly,
    NSMakeRect,
    NSPopUpButton,
    NSScrollView,
    NSStepper,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject

from adventure_parameters import (
    SPECS_BY_KEY,
    changed_parameter_keys,
    default_parameters,
    normalize_parameter_value,
    normalize_parameters,
    validate_parameters,
    visible_specs_for_section,
)
from cocoa_button_style import apply_liquid_glass_button_style, make_liquid_glass_button


class ParameterEditorWindowDelegate(NSObject):
    def initWithController_(self, controller):
        self = objc.super(ParameterEditorWindowDelegate, self).init()
        if self is not None:
            self.controller = controller
        return self

    def windowDidResize_(self, _notification):
        self.controller.layout_window()

    def windowShouldClose_(self, _sender):
        self.controller.cancel_(None)
        return False


class CocoaParameterEditor(NSObject):
    """Section-filtered settings window backed by adventure_parameters."""

    def init(self):
        self = objc.super(CocoaParameterEditor, self).init()
        if self is None:
            return None
        self.window = None
        self.window_delegate = None
        self.sections = ()
        self.values = default_parameters()
        self.draft = dict(self.values)
        self.apply_callback = None
        self.current_section = ""
        self.show_advanced = False
        self.section_buttons = []
        self.form_scroll = None
        self.form_view = None
        self.controls = {}
        self.steppers = {}
        self.tag_to_key = {}
        self.advanced_checkbox = None
        self.error_label = None
        self.apply_button = None
        return self

    @objc.python_method
    def configure(
        self,
        *,
        title: str,
        sections: Iterable[str],
        values: dict,
        apply_callback: Callable[[dict, set[str]], bool | None],
    ):
        self.title = str(title)
        self.sections = tuple(sections)
        if not self.sections:
            raise ValueError("at least one parameter section is required")
        self.values = normalize_parameters(values)
        self.draft = dict(self.values)
        self.apply_callback = apply_callback
        self.current_section = self.sections[0]
        self._build_window()
        return self

    def _make_button(self, title, action):
        button = make_liquid_glass_button(NSMakeRect(0, 0, 100, 28))
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        return apply_liquid_glass_button_style(button)

    def _make_checkbox(self, title, action):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 180, 24))
        button.setButtonType_(NSButtonTypeSwitch)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    def _make_label(self, text, size=13.0, bold=False):
        label = NSTextField.labelWithString_(str(text))
        font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        label.setFont_(font)
        return label

    def _make_text_field(self, value=""):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 180, 27))
        field.setFont_(NSFont.systemFontOfSize_(13.0))
        field.setStringValue_(str(value))
        return field

    def _make_reset_button(self, tag):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 26, 26))
        image_factory = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        image = image_factory("arrow.counterclockwise", "Reset") if image_factory is not None else None
        if image is not None:
            button.setImage_(image)
            button.setImagePosition_(NSImageOnly)
            button.setTitle_("")
        else:
            button.setTitle_("R")
        button.setBordered_(False)
        button.setTarget_(self)
        button.setAction_("resetParameter:")
        button.setTag_(tag)
        return button

    def _build_window(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(180.0, 100.0, 900.0, 680.0),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_(self.title)
        self.window.setMinSize_((760.0, 540.0))
        self.window_delegate = ParameterEditorWindowDelegate.alloc().initWithController_(self)
        self.window.setDelegate_(self.window_delegate)
        content = self.window.contentView()

        for index, section in enumerate(self.sections):
            button = self._make_button(section, "selectSection:")
            button.setTag_(7000 + index)
            button.setToolTip_(f"Show {section} settings.")
            content.addSubview_(button)
            self.section_buttons.append(button)

        self.form_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.form_scroll.setHasVerticalScroller_(True)
        self.form_scroll.setHasHorizontalScroller_(False)
        self.form_scroll.setBorderType_(1)
        content.addSubview_(self.form_scroll)
        self.advanced_checkbox = self._make_checkbox("Show Advanced Settings", "toggleAdvanced:")
        content.addSubview_(self.advanced_checkbox)
        self.error_label = self._make_label("", 11.0)
        self.error_label.setTextColor_(NSColor.systemRedColor())
        content.addSubview_(self.error_label)
        self.reset_all_button = self._make_button("Reset All", "resetAll:")
        self.cancel_button = self._make_button("Cancel", "cancel:")
        self.apply_button = self._make_button("Apply", "apply:")
        for button in (self.reset_all_button, self.cancel_button, self.apply_button):
            content.addSubview_(button)
        self.layout_window()

    def layout_window(self):
        if self.window is None:
            return
        bounds = self.window.contentView().bounds()
        width, height = float(bounds.size.width), float(bounds.size.height)
        sidebar_width = 178.0
        footer_height = 70.0
        for index, button in enumerate(self.section_buttons):
            button.setFrame_(NSMakeRect(14.0, height - 16.0 - (index + 1) * 34.0, 150.0, 28.0))
        self.form_scroll.setFrame_(
            NSMakeRect(sidebar_width, footer_height, max(420.0, width - sidebar_width - 14.0), max(300.0, height - footer_height - 14.0))
        )
        self.advanced_checkbox.setFrame_(NSMakeRect(18.0, 40.0, 154.0, 24.0))
        self.reset_all_button.setFrame_(NSMakeRect(18.0, 8.0, 110.0, 28.0))
        self.apply_button.setFrame_(NSMakeRect(width - 116.0, 18.0, 100.0, 30.0))
        self.cancel_button.setFrame_(NSMakeRect(width - 226.0, 18.0, 100.0, 30.0))
        self.error_label.setFrame_(NSMakeRect(sidebar_width + 12.0, 20.0, max(220.0, width - sidebar_width - 250.0), 24.0))
        if self.form_view is not None:
            self._capture_controls(update_error=False)
            self._render_section()

    def _parameter_color(self, value):
        text = str(value).strip().lower()
        named = {
            "black": NSColor.blackColor(), "white": NSColor.whiteColor(),
            "red": NSColor.redColor(), "blue": NSColor.blueColor(),
            "green": NSColor.greenColor(), "yellow": NSColor.yellowColor(),
            "gray": NSColor.grayColor(), "grey": NSColor.grayColor(),
            "orange": NSColor.orangeColor(), "cyan": NSColor.cyanColor(),
            "magenta": NSColor.magentaColor(),
        }
        if text in named:
            return named[text]
        if re.fullmatch(r"#[0-9a-f]{6}", text):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(
                int(text[1:3], 16) / 255.0,
                int(text[3:5], 16) / 255.0,
                int(text[5:7], 16) / 255.0,
                1.0,
            )
        return NSColor.blackColor()

    def _parameter_color_text(self, color):
        converted = color.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()) if color is not None else None
        if converted is None:
            return "#000000"
        return "#{:02X}{:02X}{:02X}".format(
            round(float(converted.redComponent()) * 255.0),
            round(float(converted.greenComponent()) * 255.0),
            round(float(converted.blueComponent()) * 255.0),
        )

    @staticmethod
    def _display_value(spec, value):
        return f"{float(value) * 100.0:g}" if spec.value_type == "fraction" else str(value)

    @staticmethod
    def _stepper_values(spec, value):
        scale = 100.0 if spec.value_type == "fraction" else 1.0
        displayed = float(value) * scale
        minimum = float(spec.minimum) * scale if spec.minimum is not None else -1.0e9
        maximum = float(spec.maximum) * scale if spec.maximum is not None else 1.0e9
        increment = 1.0 if spec.value_type in {"int", "fraction"} else (0.1 if max(abs(displayed), 1.0) < 10.0 else 1.0)
        return displayed, minimum, maximum, increment

    def _render_section(self):
        specs = visible_specs_for_section(self.current_section, self.draft, self.show_advanced)
        visible_height = max(300.0, float(self.form_scroll.contentSize().height))
        row_height = 64.0
        document_height = max(visible_height, 24.0 + row_height * len(specs))
        document_width = max(520.0, float(self.form_scroll.contentSize().width))
        form_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, document_width, document_height))
        self.controls = {}
        self.steppers = {}
        self.tag_to_key = {}
        for row, spec in enumerate(specs):
            y = document_height - 18.0 - (row + 1) * row_height
            label = self._make_label(spec.label, 13.0, True)
            label.setFrame_(NSMakeRect(16.0, y + 34.0, 215.0, 20.0))
            label.setToolTip_(spec.help_text)
            form_view.addSubview_(label)
            help_label = self._make_label(spec.help_text, 10.5)
            help_label.setTextColor_(NSColor.secondaryLabelColor())
            help_label.setFrame_(NSMakeRect(16.0, y + 6.0, max(220.0, document_width - 72.0), 28.0))
            form_view.addSubview_(help_label)

            tag = 8000 + row
            value = self.draft.get(spec.key, spec.default)
            control_x = min(245.0, max(220.0, document_width * 0.42))
            is_numeric = spec.value_type in {"int", "float", "fraction"}
            reset_x = document_width - 36.0
            stepper_x = reset_x - 26.0 if is_numeric else reset_x
            unit_width = 42.0 if spec.unit else 0.0
            unit_x = stepper_x - unit_width - (6.0 if spec.unit else 0.0)
            control_right = (unit_x if spec.unit else stepper_x) - 8.0
            control_width = max(110.0, control_right - control_x)
            if spec.value_type == "bool":
                control = self._make_checkbox("", "valueChanged:")
                control.setState_(NSControlStateValueOn if bool(value) else NSControlStateValueOff)
            elif spec.value_type == "choice":
                control = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(0, 0, control_width, 26.0))
                control.addItemsWithTitles_([display for _stored, display in spec.choices])
                selected = next((index for index, item in enumerate(spec.choices) if item[0] == value), 0)
                control.selectItemAtIndex_(selected)
                control.setTarget_(self)
                control.setAction_("valueChanged:")
            elif spec.value_type == "color":
                control = NSColorWell.alloc().initWithFrame_(NSMakeRect(0, 0, min(90.0, control_width), 26.0))
                control.setColor_(self._parameter_color(value))
                control.setTarget_(self)
                control.setAction_("valueChanged:")
            else:
                control = self._make_text_field(self._display_value(spec, value))
                control.setDelegate_(self)
                control.setTarget_(self)
                control.setAction_("valueChanged:")
            control.setTag_(tag)
            control.setFrame_(NSMakeRect(control_x, y + 31.0, control_width, 27.0))
            control.setToolTip_(spec.help_text)
            form_view.addSubview_(control)
            self.controls[spec.key] = control
            self.tag_to_key[tag] = spec.key
            if spec.unit:
                unit = self._make_label(spec.unit, 11.0)
                unit.setFrame_(NSMakeRect(unit_x, y + 35.0, unit_width, 18.0))
                form_view.addSubview_(unit)
            if is_numeric:
                displayed, minimum, maximum, increment = self._stepper_values(spec, value)
                stepper = NSStepper.alloc().initWithFrame_(NSMakeRect(stepper_x, y + 31.0, 20.0, 27.0))
                stepper.setMinValue_(minimum)
                stepper.setMaxValue_(maximum)
                stepper.setIncrement_(increment)
                stepper.setDoubleValue_(displayed)
                stepper.setValueWraps_(False)
                stepper.setAutorepeat_(True)
                stepper.setTarget_(self)
                stepper.setAction_("stepperChanged:")
                stepper.setTag_(tag)
                form_view.addSubview_(stepper)
                self.steppers[spec.key] = stepper
            reset = self._make_reset_button(tag)
            reset.setFrame_(NSMakeRect(reset_x, y + 31.0, 26.0, 26.0))
            reset.setToolTip_(f"Reset {spec.label} to {spec.default}.")
            form_view.addSubview_(reset)

        self.form_view = form_view
        self.form_scroll.setDocumentView_(form_view)
        key_views = list(self.controls.values())
        for current, following in zip(key_views, key_views[1:]):
            current.setNextKeyView_(following)
        if key_views:
            key_views[-1].setNextKeyView_(self.advanced_checkbox)
            self.advanced_checkbox.setNextKeyView_(self.cancel_button)
            self.cancel_button.setNextKeyView_(self.apply_button)
            self.apply_button.setNextKeyView_(key_views[0])
        for index, button in enumerate(self.section_buttons):
            button.setEnabled_(self.sections[index] != self.current_section)
        self._validate_draft()

    def _control_value(self, spec, control):
        if spec.value_type == "bool":
            raw = control.state() == NSControlStateValueOn
        elif spec.value_type == "choice":
            raw = spec.choices[int(control.indexOfSelectedItem())][0]
        elif spec.value_type == "color":
            raw = self._parameter_color_text(control.color())
        else:
            raw = str(control.stringValue())
        return normalize_parameter_value(spec, raw)

    def _capture_controls(self, update_error=True):
        errors = {}
        for key, control in self.controls.items():
            spec = SPECS_BY_KEY[key]
            try:
                self.draft[key] = self._control_value(spec, control)
                if key in self.steppers:
                    displayed, _minimum, _maximum, _increment = self._stepper_values(spec, self.draft[key])
                    self.steppers[key].setDoubleValue_(displayed)
            except (TypeError, ValueError) as exc:
                errors[key] = str(exc)
        if update_error:
            self._validate_draft(errors)
        return errors

    def _validate_draft(self, field_errors=None):
        errors = dict(field_errors or {})
        if not errors:
            errors.update(validate_parameters(self.draft))
        self.apply_button.setEnabled_(not errors)
        if errors:
            key, message = next(iter(errors.items()))
            label = SPECS_BY_KEY[key].label if key in SPECS_BY_KEY else key
            self.error_label.setStringValue_(f"{label}: {message}")
        else:
            self.error_label.setStringValue_("")
        return errors

    def show(self):
        self.draft = dict(self.values)
        self.advanced_checkbox.setState_(NSControlStateValueOn if self.show_advanced else NSControlStateValueOff)
        self._render_section()
        self.window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)

    def update_values(self, values):
        self.values = normalize_parameters(values)
        if self.window is not None and self.window.isVisible():
            self.draft = dict(self.values)
            self._render_section()

    def close(self):
        if self.window is None:
            return
        self.window.setDelegate_(None)
        self.window.orderOut_(None)
        self.window.close()
        self.window = None
        self.window_delegate = None

    @objc.IBAction
    def selectSection_(self, sender):
        self._capture_controls()
        index = int(sender.tag()) - 7000
        if 0 <= index < len(self.sections):
            self.current_section = self.sections[index]
            self._render_section()

    @objc.IBAction
    def toggleAdvanced_(self, sender):
        self._capture_controls()
        self.show_advanced = sender.state() == NSControlStateValueOn
        self._render_section()

    @objc.IBAction
    def valueChanged_(self, sender):
        key = self.tag_to_key.get(int(sender.tag()))
        self._capture_controls()
        if key == "maps.provider":
            self.performSelector_withObject_afterDelay_("refreshSection:", None, 0.0)

    def controlTextDidChange_(self, _notification):
        self._capture_controls()

    def refreshSection_(self, _payload):
        self._render_section()

    @objc.IBAction
    def stepperChanged_(self, sender):
        key = self.tag_to_key.get(int(sender.tag()))
        if key is None or key not in self.controls:
            return
        spec = SPECS_BY_KEY[key]
        value = float(sender.doubleValue())
        text = str(int(round(value))) if spec.value_type == "int" else f"{value:g}"
        self.controls[key].setStringValue_(text)
        self._capture_controls()

    @objc.IBAction
    def resetParameter_(self, sender):
        key = self.tag_to_key.get(int(sender.tag()))
        if key is not None:
            self.draft[key] = SPECS_BY_KEY[key].default
            self._render_section()

    @objc.IBAction
    def resetAll_(self, _sender):
        defaults = default_parameters()
        section_set = set(self.sections)
        for key, spec in SPECS_BY_KEY.items():
            if spec.section in section_set:
                self.draft[key] = defaults[key]
        self._render_section()

    @objc.IBAction
    def cancel_(self, _sender):
        self.draft = dict(self.values)
        self.window.orderOut_(None)

    @objc.IBAction
    def apply_(self, _sender):
        field_errors = self._capture_controls()
        if self._validate_draft(field_errors):
            return
        normalized = normalize_parameters(self.draft)
        changed = changed_parameter_keys(self.values, normalized)
        if self.apply_callback is not None and self.apply_callback(normalized, changed) is False:
            return
        self.values = normalized
        self.draft = dict(normalized)
        self.window.orderOut_(None)
