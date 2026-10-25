import unittest
from unittest.mock import Mock, patch

from AppKit import NSBackgroundColorAttributeName, NSColor, NSForegroundColorAttributeName
from cocoa_table_editing import (restore_field_editor_contrast, make_table_text_cell, make_table_combo_box_cell,
                                 MyCaminoEditingTextFieldCell, MyCaminoEditingComboBoxCell)


class TableEditingContrastTests(unittest.TestCase):
    def test_contrast_is_restored_after_native_setup_selection_and_editing(self):
        editor = object()
        for cls in (MyCaminoEditingTextFieldCell, MyCaminoEditingComboBoxCell):
            for name, args in (
                    ("setUpFieldEditorAttributes_", (editor,)),
                    ("selectWithFrame_inView_editor_delegate_start_length_", (object(), object(), editor, object(), 0, 1)),
                    ("editWithFrame_inView_editor_delegate_event_", (object(), object(), editor, object(), object()))):
                with self.subTest(cell=cls.__name__, path=name), \
                        patch("cocoa_table_editing.objc") as bridge, \
                        patch("cocoa_table_editing.restore_field_editor_contrast", return_value=editor) as restore:
                    native = getattr(bridge.super.return_value, name)
                    native.return_value = editor
                    sequence = Mock()
                    sequence.attach_mock(native, "native")
                    sequence.attach_mock(restore, "contrast")
                    method = getattr(cls, name)
                    getattr(method, "callable", method)(object(), *args)
                    native.assert_called_once_with(*args)
                    restore.assert_called_once_with(editor)
                    self.assertEqual([call[0] for call in sequence.mock_calls], ["native", "contrast"])

    def test_factories_preserve_native_table_cell_editability(self):
        for factory, name, initializer in (
                (make_table_text_cell, "MyCaminoEditingTextFieldCell", "initTextCell_"),
                (make_table_combo_box_cell, "MyCaminoEditingComboBoxCell", "init")):
            with self.subTest(cell=name), patch("cocoa_table_editing." + name) as cls:
                cell = getattr(cls.alloc.return_value, initializer).return_value
                self.assertIs(factory(), cell)
                cell.setEditable_.assert_called_once_with(True)
                cell.setSelectable_.assert_called_once_with(True)

    def test_white_row_style_is_reset_for_text_and_future_typing(self):
        editor = Mock()
        font, paragraph = object(), object()
        typing = {NSForegroundColorAttributeName: NSColor.whiteColor(),
                  NSBackgroundColorAttributeName: NSColor.whiteColor(),
                  "NSFont": font, "NSParagraphStyle": paragraph}
        editor.typingAttributes.return_value = typing
        editor.selectedTextAttributes.return_value = {"NSUnderline": 1}
        self.assertIs(restore_field_editor_contrast(editor), editor)
        editor.setDrawsBackground_.assert_called_once_with(True)
        editor.setBackgroundColor_.assert_called_once_with(NSColor.textBackgroundColor())
        editor.setTextColor_.assert_called_once_with(NSColor.textColor())
        editor.setInsertionPointColor_.assert_called_once_with(NSColor.textColor())
        result = editor.setTypingAttributes_.call_args.args[0]
        self.assertEqual(result[NSForegroundColorAttributeName], NSColor.textColor())
        self.assertNotIn(NSBackgroundColorAttributeName, result)
        self.assertIs(result["NSFont"], font)
        self.assertIs(result["NSParagraphStyle"], paragraph)
        self.assertEqual(typing[NSForegroundColorAttributeName], NSColor.whiteColor())
        selected = editor.setSelectedTextAttributes_.call_args.args[0]
        self.assertEqual(selected[NSForegroundColorAttributeName], NSColor.selectedTextColor())
        self.assertEqual(selected[NSBackgroundColorAttributeName], NSColor.selectedTextBackgroundColor())
        self.assertEqual(selected["NSUnderline"], 1)

    def test_shared_editor_is_reset_after_each_reuse(self):
        editor = Mock()
        editor.typingAttributes.side_effect = [
            {NSForegroundColorAttributeName: color} for color in
            (NSColor.whiteColor(), NSColor.disabledControlTextColor(), NSColor.redColor())]
        editor.selectedTextAttributes.return_value = {}
        for _ in range(3):
            restore_field_editor_contrast(editor)
        self.assertEqual(editor.setTypingAttributes_.call_count, 3)
        for call in editor.setTypingAttributes_.call_args_list:
            self.assertEqual(call.args[0][NSForegroundColorAttributeName], NSColor.textColor())

    def test_empty_attributes_and_missing_editor_are_harmless(self):
        self.assertIsNone(restore_field_editor_contrast(None))
        editor = Mock()
        editor.typingAttributes.return_value = None
        editor.selectedTextAttributes.return_value = None
        restore_field_editor_contrast(editor)
        editor.setTypingAttributes_.assert_called_once_with({NSForegroundColorAttributeName: NSColor.textColor()})


if __name__ == "__main__":
    unittest.main()
