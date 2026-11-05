"""Control-file help is discoverable without stealing text-editing shortcuts."""

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GPSTrackShowGUI as gui


class ControlFileHelpTests(unittest.TestCase):
    def event(self, key="h", modifiers=0, event_type=gui.NSEventTypeKeyDown):
        return SimpleNamespace(
            type=lambda: event_type, modifierFlags=lambda: modifiers,
            charactersIgnoringModifiers=lambda: key)

    def window(self, *, editor=None, responder=None):
        table = Mock()
        table.currentEditor.return_value = editor
        controller = SimpleNamespace(control_table_view=table, showControlDirectiveHelp_=Mock())
        return SimpleNamespace(controller=controller, firstResponder=lambda: responder)

    def send_event(self, window, event):
        with patch.object(gui, "objc") as bridge:
            gui.SlideShowControlTableWindow.sendEvent_.callable(window, event)
            return bridge.super.return_value.sendEvent_

    def test_h_opens_help_without_requiring_table_focus_or_selection(self):
        for key in ("h", "H"):
            with self.subTest(key=key):
                window = self.window(responder=object())
                forwarded = self.send_event(window, self.event(key))
                window.controller.showControlDirectiveHelp_.assert_called_once_with(window)
                forwarded.assert_not_called()

    def test_h_is_ordinary_text_during_cell_and_search_editing(self):
        for editor, responder in ((object(), object()), (None, Mock(spec=gui.NSTextView)),
                                  (None, Mock(spec=gui.NSSearchField)), (None, Mock(spec=gui.NSTextField))):
            with self.subTest(editor=editor, responder=responder):
                window = self.window(editor=editor, responder=responder)
                event = self.event()
                forwarded = self.send_event(window, event)
                window.controller.showControlDirectiveHelp_.assert_not_called()
                forwarded.assert_called_once_with(event)

    def test_system_shortcuts_other_keys_and_non_keyboard_events_are_preserved(self):
        for event in (self.event(modifiers=int(gui.NSEventModifierFlagCommand)),
                      self.event(modifiers=int(gui.NSEventModifierFlagControl)),
                      self.event(modifiers=int(gui.NSEventModifierFlagOption)),
                      self.event(key="c"), self.event(event_type=0)):
            with self.subTest(event=event):
                window = self.window()
                forwarded = self.send_event(window, event)
                window.controller.showControlDirectiveHelp_.assert_not_called()
                forwarded.assert_called_once_with(event)

    def test_existing_cell_clipboard_routing_stays_intact(self):
        editor = Mock()
        window = self.window(editor=editor)
        forwarded = self.send_event(window, self.event("c", int(gui.NSEventModifierFlagCommand)))
        editor.copy_.assert_called_once_with(None)
        forwarded.assert_not_called()

    def test_help_explains_context_stable_identity_and_all_row_types(self):
        text = gui.GPXTrackerController._control_directive_help_content(SimpleNamespace())
        for code, description in gui.CONTROL_ROW_TYPE_DESCRIPTIONS.items():
            self.assertIn(f"{code}: {description}", text)
        for phrase in ("CTX means context", "#Track: internal_id=9", "permanent identifier",
                       "GPX extensions", "not the table row", "Track Number, Stage number",
                       "displayed Track ID", "not reused", "Press h", "question-mark button",
                       "#SET_TIME", "#REPEAT_UNTIL", "Cmd-Z / Shift-Cmd-Z"):
            self.assertIn(phrase, text)

    def test_help_icon_is_connected_accessible_and_fits_beside_close(self):
        buttons = {}
        controller = SimpleNamespace(
            control_table_window=None, control_table_preview_size="off",
            _make_icon_button=Mock(side_effect=lambda *_args: Mock()),
            _make_button=Mock(side_effect=lambda title, *_args: buttons.setdefault(title, Mock())),
            _make_label=Mock(side_effect=lambda *_args, **_kwargs: Mock()),
            _sync_control_table_filter_popup=Mock())
        with ExitStack() as stack:
            for name in ("SlideShowControlTableWindow", "GPSTrackShowGUIControlTableWindowDelegate",
                         "NSPopUpButton", "NSSearchField", "NSScrollView", "SlideShowControlTableView",
                         "NSTableColumn", "NSImageCell", "NSFont", "SlideShowControlTableDataSource",
                         "make_table_combo_box_cell", "make_table_text_cell", "disable_field_editor_text_checking"):
                stack.enter_context(patch.object(gui, name))
            gui.GPXTrackerController._ensure_control_table_window(controller)
            help_button = controller.control_table_help_button
            controller._make_icon_button.assert_any_call("sf:questionmark.circle", "?", "showControlDirectiveHelp:")
            help_button.setAccessibilityLabel_.assert_called_once_with("Control File Help")
            self.assertIn("(h)", help_button.setToolTip_.call_args.args[0])
            frame = help_button.setFrame_.call_args.args[0]
            self.assertLessEqual(frame.origin.x + frame.size.width, 980.)
            close_frame = buttons["Close"].setFrame_.call_args.args[0]
            self.assertGreater(frame.origin.x, close_frame.origin.x + close_frame.size.width)
            # Reopening the retained editor does not add another button/window.
            count = controller._make_icon_button.call_count
            gui.GPXTrackerController._ensure_control_table_window(controller)
            self.assertEqual(controller._make_icon_button.call_count, count)
            self.assertIs(controller.control_table_help_button, help_button)

    def test_explicit_help_reuses_window_and_focuses_it_but_row_selection_does_not(self):
        window, button, table = object(), object(), object()
        help_window = Mock()
        controller = SimpleNamespace(
            control_table_window=window, control_table_help_button=button,
            control_directive_help_window=help_window, control_directive_help_text=Mock(),
            _control_directive_help_content=lambda: "Help text", _position_control_directive_help_window=Mock())
        with patch.object(gui, "NSWindow") as constructor:
            for sender in (window, button):
                gui.GPXTrackerController.showControlDirectiveHelp_.callable(controller, sender)
            self.assertEqual(help_window.makeKeyAndOrderFront_.call_count, 2)
            help_window.orderFront_.assert_not_called()
            gui.GPXTrackerController.showControlDirectiveHelp_.callable(controller, table)
            help_window.orderFront_.assert_called_once_with(None)
            constructor.alloc.assert_not_called()
        self.assertIs(controller.control_directive_help_window, help_window)

    def test_new_help_wraps_and_scrolls_and_is_retained(self):
        button = object()
        controller = SimpleNamespace(
            control_table_window=object(), control_table_help_button=button,
            control_directive_help_window=None, _control_directive_help_content=lambda: "Help text",
            _position_control_directive_help_window=Mock())
        with patch.object(gui, "NSWindow") as windows, patch.object(gui, "NSScrollView") as scrolls, \
             patch.object(gui, "NSTextView") as texts, patch.object(gui, "NSFont"), \
             patch.object(gui, "disable_field_editor_text_checking"):
            scroll = scrolls.alloc.return_value.initWithFrame_.return_value
            scroll.bounds.return_value = gui.NSMakeRect(0, 0, 504, 524)
            text = texts.alloc.return_value.initWithFrame_.return_value
            gui.GPXTrackerController.showControlDirectiveHelp_.callable(controller, button)
            gui.GPXTrackerController.showControlDirectiveHelp_.callable(controller, button)
            windows.alloc.assert_called_once()
            scroll.setHasVerticalScroller_.assert_called_once_with(True)
            text.setEditable_.assert_called_once_with(False)
            text.setVerticallyResizable_.assert_called_once_with(True)
            text.setHorizontallyResizable_.assert_called_once_with(False)
            text.textContainer().setWidthTracksTextView_.assert_called_once_with(True)
            controller.control_directive_help_window.setReleasedWhenClosed_.assert_called_once_with(False)

    def test_help_placement_handles_no_selection_and_the_new_button(self):
        owner, table, help_window = Mock(), Mock(), Mock()
        owner.frame.return_value = gui.NSMakeRect(200, 100, 980, 600)
        owner.screen.return_value.visibleFrame.return_value = gui.NSMakeRect(0, 0, 1280, 800)
        table.window.return_value = owner
        table.editedRow.return_value = table.selectedRow.return_value = -1
        table.editedColumn.return_value = table.columnWithIdentifier_.return_value = -1
        help_window.frame.return_value = gui.NSMakeRect(0, 0, 520, 540)
        controller = SimpleNamespace(music_directive_help_window=help_window, control_table_view=table)
        button = Mock(spec=gui.NSView)
        button.window.return_value = owner
        owner.convertRectToScreen_.return_value = gui.NSMakeRect(1138, 128, 28, 28)
        for anchor in (None, table, button):
            with self.subTest(anchor=anchor):
                gui.GPXTrackerController._position_music_directive_help_window(controller, anchor)
                origin = help_window.setFrameOrigin_.call_args.args[0]
                self.assertGreaterEqual(origin.x, 0)
                self.assertGreaterEqual(origin.y, 0)
                self.assertLessEqual(origin.x + 520, 1280)
                self.assertLessEqual(origin.y + 540, 800)
        table.frameOfCellAtColumn_row_.assert_not_called()
        button.convertRect_toView_.assert_called_once()


if __name__ == "__main__":
    unittest.main()
