# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided, disposable drag-and-drop demonstration for myCamino."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

import objc
from AppKit import (
    NSAlert,
    NSBackingStoreBuffered,
    NSButton,
    NSFont,
    NSMakeRect,
    NSMakeSize,
    NSOpenPanel,
    NSTextField,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSObject, NSUserDefaults, NSURL

from adventure_files import filename_base
from demo_resources import create_demo_session, demo_resource_directory, verify_demo_manifest


DEMO_PREFERENCE_KEY = "myCaminoGuidedDemoOfferVersion"
DEMO_WORKSPACE_KEY = "myCaminoGuidedDemoLastWorkspace"
DEMO_INPUTS_KEY = "myCaminoGuidedDemoInputsDirectory"
DEMO_OFFER_VERSION = 1
DEMO_ADVENTURE_NAME = "St Jean to Roncesvalles Demo"


def saved_demo_adventure(defaults=None) -> Path | None:
    """Return the last saved demo Adventure, including normal project folders."""
    defaults = defaults or NSUserDefaults.standardUserDefaults()
    workspace_text = str(defaults.stringForKey_(DEMO_WORKSPACE_KEY) or "").strip()
    if not workspace_text:
        return None
    workspace = Path(workspace_text)
    if not workspace.is_dir():
        return None
    candidates = sorted(workspace.glob("*.adv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def controller_demo_adventure(controller) -> Path | None:
    """Return a loaded Adventure explicitly saved as a guided demo."""
    payload = getattr(controller, "adventure_payload", None)
    path = getattr(controller, "adventure_file", None)
    if isinstance(payload, dict) and payload.get("guided_demo") is True and path is not None:
        candidate = Path(path)
        if candidate.is_file():
            return candidate
    return None


def is_guided_demo_recovery(session) -> bool:
    """Keep guided-demo drafts out of the normal unsaved-Adventure recovery dialog."""
    name = str(session.get("adventure_name", ""))
    source = str(session.get("source_directory", ""))
    return name == DEMO_ADVENTURE_NAME or "mycamino-demo-" in source


def should_offer_demo(*, frozen=None, defaults=None, resource_directory=None):
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return False
    resource = Path(resource_directory) if resource_directory else demo_resource_directory()
    if not (resource / "manifest.json").is_file():
        return False
    defaults = defaults or NSUserDefaults.standardUserDefaults()
    return int(defaults.integerForKey_(DEMO_PREFERENCE_KEY)) < DEMO_OFFER_VERSION


class GuidedDemoCoordinator(NSObject):
    def initWithWorkspaceController_(self, controller):
        self = objc.super(GuidedDemoCoordinator, self).init()
        if self is not None:
            self.controller = controller
            self.session_root = None
            self.inputs_directory = None
            self.workspace_directory = None
            self.window = None
            self.message_field = None
            self.open_button = None
            self.stage = "idle"
        return self

    def offerAtStartup_(self, _timer):
        if saved_demo_adventure() is not None or controller_demo_adventure(self.controller) is not None:
            self.start()
            return
        if not should_offer_demo():
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Try the guided myCamino demo?")
        alert.setInformativeText_(
            "Build a small Adventure in a folder you choose by dragging a supplied GPS track and photographs onto the map."
        )
        alert.addButtonWithTitle_("Start Demo")
        alert.addButtonWithTitle_("Not Now")
        alert.addButtonWithTitle_("Never Show Again")
        response = int(alert.runModal())
        if response == 1000:
            self.start()
        elif response == 1002:
            NSUserDefaults.standardUserDefaults().setInteger_forKey_(
                DEMO_OFFER_VERSION, DEMO_PREFERENCE_KEY
            )

    @objc.python_method
    def start(self):
        current_demo = controller_demo_adventure(self.controller)
        if (
            self.controller.adventure_file is not None
            and not self.controller.demo_mode
            and current_demo is None
        ):
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Start the guided demo?")
            alert.setInformativeText_(
                "The current Adventure will be saved before myCamino asks for a new Demo Adventure folder."
            )
            alert.addButtonWithTitle_("Save and Start Demo")
            alert.addButtonWithTitle_("Cancel")
            if int(alert.runModal()) != 1000:
                return
            self.controller.saveWorkspace_(None)
        previous = saved_demo_adventure() or current_demo
        if previous is not None:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Continue the previous guided demo?")
            alert.setInformativeText_(
                "Continue with the saved demo Adventure, or start again with a clean map and a newly opened Demo Inputs folder."
            )
            alert.addButtonWithTitle_("Continue Previous Demo")
            alert.addButtonWithTitle_("Start New Demo")
            alert.addButtonWithTitle_("Cancel")
            response = int(alert.runModal())
            if response == 1000:
                self._continue(previous)
                return
            if response == 1002:
                return
            if self.controller.adventure_file is not None:
                self.controller.discardGuidedDemoWorkspace()
            self._discard_previous_demo(previous)
        elif (
            self.controller.adventure_file is not None
            or self.controller.full_gui_controller is not None
        ):
            self.controller.discardGuidedDemoWorkspace()
        resource = demo_resource_directory()
        try:
            failures = verify_demo_manifest(resource)
            required_failures = [
                item for item in failures
                if "2 - Photographs/" not in item or not item.startswith("Missing ")
            ]
            if required_failures:
                raise ValueError("; ".join(required_failures[:3]))
            self.session_root, self.inputs_directory, temporary_workspace = create_demo_session(resource)
            shutil.rmtree(temporary_workspace, ignore_errors=True)
        except (OSError, ValueError) as exc:
            self.controller._alert("The guided demo is unavailable.", str(exc))
            return

        self.workspace_directory = self._choose_new_demo_workspace()
        if self.workspace_directory is None:
            shutil.rmtree(self.session_root, ignore_errors=True)
            self.session_root = None
            self.inputs_directory = None
            return

        self.controller.demo_mode = True
        self.controller.demo_coordinator = self
        self.controller._stop_media_watcher()
        self.controller.project_directory = self.workspace_directory
        self.controller.adventure_name = filename_base(self.workspace_directory.name)
        self.controller.adventure_file = None
        self.controller.adventure_payload = None
        self.controller.control_file = None
        self.controller.control_rows = []
        self.controller.temporary_control_model = True
        self.controller.weather_consent = "disabled"
        self.controller.parameters["weather.enabled"] = False
        self.controller.parameters["locations.add_place_names"] = False
        self.controller.gpx_controller.tracks = []
        self.controller.gpx_controller.next_nr = 1
        self.controller._show_empty_world()
        # Establish the destination now, but let the shared delayed autosave write
        # the initial files.  A forced save here used to block the guide and Finder
        # behind GPX/cache work, making a fresh demo look as though it had stopped.
        base = filename_base(self.controller.adventure_name)
        self.controller.adventure_file = self.workspace_directory / f"{base}.adv"
        # No control file exists yet. Temporary media rows must not be written
        # until the Maps-to-Control pipeline installs the complete file.
        self.controller.workspace_dirty_components.update({"gpx", "adventure"})
        self.controller.adventureComponentDidChange_("adventure")
        NSUserDefaults.standardUserDefaults().setObject_forKey_(
            str(self.workspace_directory), DEMO_WORKSPACE_KEY
        )
        NSUserDefaults.standardUserDefaults().setObject_forKey_(
            str(self.inputs_directory), DEMO_INPUTS_KEY
        )
        omitted_photos = [
            item for item in failures
            if item.startswith("Missing ") and "2 - Photographs/" in item
        ]
        if omitted_photos:
            self.controller._journal(
                "Demo",
                f"{len(omitted_photos)} optional demo photograph(s) were removed and will be skipped.",
            )
        self.stage = "gpx"
        self._show_instruction(
            "1. Add the GPS track",
            "The demo folder is open in Finder. Drag the GPX file from ‘1 - GPX Track’ onto this map.\n\n"
            "You can edit tracks directly on the map by inserting, moving, splitting, or joining points. "
            "A detailed Track Table and Waypoint Table are also available from the Track menu, but are not opened during this demo.\n\n"
            "You can also create a GPS track from photos only, or paste points copied from Google Maps and other sources.",
            self.inputs_directory / "1 - GPX Track",
        )

    @objc.python_method
    def _choose_new_demo_workspace(self):
        """Choose a normal, new Adventure folder for the guided demo."""
        while True:
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseDirectories_(True)
            panel.setCanChooseFiles_(False)
            panel.setCanCreateDirectories_(True)
            panel.setPrompt_("Use Demo Adventure Folder")
            pictures = Path.home() / "Pictures"
            if pictures.is_dir():
                panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(pictures)))
            if int(panel.runModal()) != 1 or panel.URL() is None:
                return None
            directory = Path(str(panel.URL().path())).resolve(strict=False)
            conflicts = sorted(directory.glob("*.adv")) + sorted(directory.glob("*.lst"))
            if not conflicts:
                return directory
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Choose a new Demo Adventure folder")
            alert.setInformativeText_(
                "That folder already contains an Adventure or control file. "
                "Use New Folder in the chooser so the demo cannot replace existing work."
            )
            alert.addButtonWithTitle_("Choose Another Folder")
            alert.addButtonWithTitle_("Cancel")
            if int(alert.runModal()) != 1000:
                return None

    @objc.python_method
    def _continue(self, adventure_path):
        self.workspace_directory = Path(adventure_path).parent
        defaults = NSUserDefaults.standardUserDefaults()
        stored_inputs = str(defaults.stringForKey_(DEMO_INPUTS_KEY) or "").strip()
        self.inputs_directory = Path(stored_inputs) if stored_inputs else demo_resource_directory()
        if not (self.inputs_directory / "manifest.json").is_file():
            self.inputs_directory = demo_resource_directory()
        self.session_root = (
            self.inputs_directory.parent
            if self.inputs_directory.parent.name.startswith("mycamino-demo-")
            else None
        )
        self.controller.demo_mode = True
        self.controller.demo_coordinator = self
        self.controller.load_adventure_path(Path(adventure_path))
        control_file = self.controller.control_file
        media = list(self.workspace_directory.glob("*.jpeg"))
        gpx = list(self.workspace_directory.glob("*.gpx"))
        if control_file is not None and control_file.is_file():
            self.stage = "complete"
            self._show_complete_instruction()
        elif media:
            self.stage = "processing"
            self._show_instruction(
                "3. Preparing the Adventure",
                "The previous demo Adventure has been restored. myCamino will continue preparing metadata, maps, and its control file. Click the circular progress indicator for details.",
                None,
            )
        elif gpx:
            self.stage = "media"
            self._show_instruction(
                "2. Add the photographs",
                "Continue the previous demo by dragging the photographs from ‘2 - Photographs’ onto the map and choosing Add to Slide Show.",
                self.inputs_directory / "2 - Photographs",
            )
        else:
            self.stage = "gpx"
            self._show_instruction(
                "1. Add the GPS track",
                "Continue the previous demo by dragging the supplied GPX track onto the map.",
                self.inputs_directory / "1 - GPX Track",
            )

    @objc.python_method
    def _discard_previous_demo(self, adventure_path):
        workspace = Path(adventure_path).parent
        root = workspace.parent
        if root.name.startswith("mycamino-demo-"):
            shutil.rmtree(root, ignore_errors=True)
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.removeObjectForKey_(DEMO_WORKSPACE_KEY)
        defaults.removeObjectForKey_(DEMO_INPUTS_KEY)

    @objc.python_method
    def workspaceDidDropGPX(self, paths):
        if self.stage != "gpx" or not paths:
            return
        self.stage = "media"
        self._show_instruction(
            "2. Add the photographs",
            "Select all photographs in ‘2 - Photographs’ and drag them onto the map. "
            "When asked, choose the first option, Add to Slide Show.\n\n"
            "myCamino will copy them into the Demo Adventure, extract their metadata in the background, "
            "and create the initial control file automatically.",
            self.inputs_directory / "2 - Photographs",
        )

    @objc.python_method
    def workspaceDidAcceptSlideShowMedia(self, _paths):
        if self.stage != "media":
            return
        self.stage = "processing"
        self._show_instruction(
            "3. Preparing the Adventure",
            "Metadata and the control file are now being prepared. The circular indicator on the map shows progress. "
            "Click it to see detailed processing messages.\n\n"
            "The control-file editor will open automatically when this work is complete.",
            None,
        )

    @objc.python_method
    def controlFileDidOpen(self):
        if self.stage not in {"media", "processing"}:
            return
        self.stage = "complete"
        NSUserDefaults.standardUserDefaults().setInteger_forKey_(
            DEMO_OFFER_VERSION, DEMO_PREFERENCE_KEY
        )
        self._show_complete_instruction()

    @objc.python_method
    def _show_complete_instruction(self):
        self._show_instruction(
            "4. Start the slide show",
            "The control file lists the order of maps and photographs. Start the slide show from the Slide Show menu "
            "or from the control-file editor.\n\n"
            "During playback, t and Shift-T cycle through transition styles, and h shows all key commands. "
            "Music and narration files can also be added from the Audio menu; they are not part of this short demo.",
            None,
            button_title="Close Guide",
        )

    @objc.python_method
    def _show_instruction(self, title, message, reveal_path, button_title="Open Demo Folder"):
        if self.window is None:
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(240, 180, 620, 310),
                NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
                NSBackingStoreBuffered,
                False,
            )
            self.window.setReleasedWhenClosed_(False)
            content = self.window.contentView()
            self.message_field = NSTextField.alloc().initWithFrame_(NSMakeRect(24, 70, 572, 205))
            self.message_field.setEditable_(False)
            self.message_field.setSelectable_(True)
            self.message_field.setBordered_(False)
            self.message_field.setDrawsBackground_(False)
            self.message_field.setFont_(NSFont.systemFontOfSize_(14.0))
            self.message_field.setUsesSingleLineMode_(False)
            self.message_field.setAutoresizingMask_(NSViewWidthSizable)
            content.addSubview_(self.message_field)
            self.open_button = NSButton.alloc().initWithFrame_(NSMakeRect(24, 22, 180, 32))
            self.open_button.setTarget_(self)
            self.open_button.setAction_("openDemoFolder:")
            content.addSubview_(self.open_button)
            stop = NSButton.alloc().initWithFrame_(NSMakeRect(466, 22, 130, 32))
            stop.setTitle_("Stop Demo")
            stop.setTarget_(self)
            stop.setAction_("stopDemo:")
            content.addSubview_(stop)
        self.window.setTitle_(title)
        self.message_field.setStringValue_(message)
        self.reveal_path = reveal_path
        self.open_button.setTitle_(button_title)
        self.open_button.setEnabled_(reveal_path is not None or self.stage == "complete")
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        if reveal_path is not None:
            self._reveal(reveal_path)

    @objc.python_method
    def _reveal(self, path):
        path = Path(path)
        workspace = NSWorkspace.sharedWorkspace()
        if path.is_dir():
            # Revealing one contained item makes Finder open the requested
            # directory reliably.  Opening a temporary directory URL while a
            # Finder drag is completing can otherwise fall back to the system
            # temporary root.
            item = next((candidate for candidate in sorted(path.iterdir()) if not candidate.name.startswith(".")), None)
            if item is not None:
                workspace.activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(item))])
                return
        workspace.openURL_(NSURL.fileURLWithPath_(str(path)))

    def openDemoFolder_(self, _sender):
        if self.stage == "complete":
            self.window.orderOut_(None)
        elif self.reveal_path is not None:
            self._reveal(self.reveal_path)

    def stopDemo_(self, _sender):
        if self.window is not None:
            self.window.orderOut_(None)
        if self.stage in {"stopping", "stopped"}:
            return
        self.stage = "stopping"
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.removeObjectForKey_(DEMO_WORKSPACE_KEY)
        defaults.removeObjectForKey_(DEMO_INPUTS_KEY)
        # Return from the NSButton action before changing retained Cocoa window
        # graphs. Destroying them on this native event stack can segfault AppKit.
        self.performSelector_withObject_afterDelay_("finishStoppingDemo:", None, 0.0)

    def finishStoppingDemo_(self, _sender):
        workspace = self.workspace_directory
        session_root = self.session_root
        external_workspace = bool(
            workspace is not None
            and (session_root is None or session_root not in workspace.parents)
        )
        if external_workspace:
            self.controller.demo_mode = False
            if isinstance(self.controller.adventure_payload, dict):
                self.controller.adventure_payload["guided_demo"] = False
            self.controller.adventureComponentDidChange_("adventure")
        else:
            self.controller.discardGuidedDemoWorkspace()
            self.controller.demo_mode = False
        self.controller.demo_coordinator = None
        # Finder windows are still displaying the supplied GPX and photograph
        # folders. Keep those read-only inputs in temporary storage so Finder
        # does not fall back to /var/.../T when their parent disappears. The
        # generated Adventure is left in disposable system storage until macOS
        # cleans it, because retired worker callbacks may still hold its paths.
        if session_root is not None:
            try:
                (session_root / ".stopped").write_text("Stopped demo\n", encoding="utf-8")
            except OSError:
                pass
        self.stage = "stopped"

    @objc.python_method
    def suspendForShutdown(self):
        """Leave the saved demo session available for an explicit continuation."""
        if self.window is not None:
            self.window.orderOut_(None)
        self.stage = "suspended"
