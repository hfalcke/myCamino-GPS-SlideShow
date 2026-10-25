"""Keep active table editors readable independently of row-display styling.

AppKit can overwrite setup colors during edit/select, so restore contrast after
those native calls too, rather than only when preparing the field editor.
"""

import objc
from AppKit import (
    NSBackgroundColorAttributeName,
    NSColor,
    NSComboBoxCell,
    NSForegroundColorAttributeName,
    NSTextFieldCell,
)


def restore_field_editor_contrast(editor):
    if editor is None:
        return None
    foreground = NSColor.textColor()
    editor.setDrawsBackground_(True)
    editor.setBackgroundColor_(NSColor.textBackgroundColor())
    editor.setTextColor_(foreground)
    editor.setInsertionPointColor_(foreground)
    # Setting textColor alone can leave the next typed characters in a row's
    # selected/disabled color inherited from the shared field editor.
    typing = dict(editor.typingAttributes() or {})
    typing[NSForegroundColorAttributeName] = foreground
    typing.pop(NSBackgroundColorAttributeName, None)
    editor.setTypingAttributes_(typing)
    selection = dict(editor.selectedTextAttributes() or {})
    selection[NSForegroundColorAttributeName] = NSColor.selectedTextColor()
    selection[NSBackgroundColorAttributeName] = NSColor.selectedTextBackgroundColor()
    editor.setSelectedTextAttributes_(selection)
    return editor


class MyCaminoEditingTextFieldCell(NSTextFieldCell):
    def setUpFieldEditorAttributes_(self, editor):
        editor = objc.super(MyCaminoEditingTextFieldCell, self).setUpFieldEditorAttributes_(editor)
        return restore_field_editor_contrast(editor)

    def selectWithFrame_inView_editor_delegate_start_length_(self, frame, view, editor, delegate, start, length):
        objc.super(MyCaminoEditingTextFieldCell, self).selectWithFrame_inView_editor_delegate_start_length_(
            frame, view, editor, delegate, start, length)
        restore_field_editor_contrast(editor)

    def editWithFrame_inView_editor_delegate_event_(self, frame, view, editor, delegate, event):
        objc.super(MyCaminoEditingTextFieldCell, self).editWithFrame_inView_editor_delegate_event_(
            frame, view, editor, delegate, event)
        restore_field_editor_contrast(editor)


class MyCaminoEditingComboBoxCell(NSComboBoxCell):
    def setUpFieldEditorAttributes_(self, editor):
        editor = objc.super(MyCaminoEditingComboBoxCell, self).setUpFieldEditorAttributes_(editor)
        return restore_field_editor_contrast(editor)

    def selectWithFrame_inView_editor_delegate_start_length_(self, frame, view, editor, delegate, start, length):
        objc.super(MyCaminoEditingComboBoxCell, self).selectWithFrame_inView_editor_delegate_start_length_(
            frame, view, editor, delegate, start, length)
        restore_field_editor_contrast(editor)

    def editWithFrame_inView_editor_delegate_event_(self, frame, view, editor, delegate, event):
        objc.super(MyCaminoEditingComboBoxCell, self).editWithFrame_inView_editor_delegate_event_(
            frame, view, editor, delegate, event)
        restore_field_editor_contrast(editor)


def _editable_cell(cell):
    # NSTableColumn's default text cell is editable/selectable, unlike a newly
    # constructed NSTextFieldCell. Column/delegate policies still gate editing.
    cell.setEditable_(True)
    cell.setSelectable_(True)
    return cell


def make_table_text_cell():
    return _editable_cell(MyCaminoEditingTextFieldCell.alloc().initTextCell_(""))


def make_table_combo_box_cell():
    return _editable_cell(MyCaminoEditingComboBoxCell.alloc().init())
