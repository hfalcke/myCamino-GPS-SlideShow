"""Shared selection of an existing Adventure; never creates project files."""

from AppKit import NSAlert, NSMakeRect, NSPopUpButton

from cocoa_alerts import run_foreground_modal_alert


def choose_adventure(records):
    if not records:
        return None
    if len(records) == 1:
        return records[0].path
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Open Adventure")
    alert.setInformativeText_("This folder contains several Adventures. Choose the one to open. No new Adventure will be created.")
    chooser = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 520, 28), False)
    chooser.addItemsWithTitles_([f"{record.project_name} ({record.path.name})" for record in records])
    alert.setAccessoryView_(chooser)
    alert.addButtonWithTitle_("Open Selected")
    alert.addButtonWithTitle_("Cancel")
    if int(run_foreground_modal_alert(alert)) != 1000:
        return None
    index = int(chooser.indexOfSelectedItem())
    return records[index].path if 0 <= index < len(records) else None
