#!/usr/bin/env python3
"""Shared Cocoa workflow for recovering a missing Adventure GPX.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from AppKit import NSAlert, NSMakeRect, NSModalResponseOK, NSOpenPanel, NSPopUpButton
from Foundation import NSURL

from gpx_adventure_identity import (
    GpxRecoveryCandidate,
    build_gpx_identity,
    classify_gpx_identity,
    discover_gpx_recovery_candidates,
    matching_track_map_base,
    normalize_gpx_identity,
    patch_gpx_source_references,
)
from json_storage import atomic_write_json


@dataclass(frozen=True)
class MissingGpxRecoveryResult:
    payload: dict
    path: Path | None
    action: str
    impact: str = "unknown"
    review_requested: bool = False


def _candidate_title(candidate: GpxRecoveryCandidate, has_identity: bool) -> str:
    notes = []
    if has_identity and candidate.score >= 1000:
        notes.append("exact identity match")
    if candidate.summary_source_match:
        notes.append("used by existing map summary")
    if not has_identity:
        notes.append("identity not previously recorded")
    suffix = f" — {', '.join(notes)}" if notes else ""
    return f"{candidate.path.name}{suffix}"


def _choose_external_gpx(project_directory: Path) -> Path | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setAllowedFileTypes_(["gpx"])
    panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(project_directory)))
    if int(panel.runModal()) != int(NSModalResponseOK):
        return None
    url = panel.URL()
    return Path(str(url.path())).expanduser().resolve(strict=False) if url is not None else None


def _copy_external_candidate(source: Path, project_directory: Path, identity: dict) -> tuple[Path, bool]:
    if source.parent.resolve(strict=False) == project_directory.resolve(strict=False):
        return source, False
    target = project_directory / source.name
    suffix = 2
    while target.exists():
        try:
            existing = build_gpx_identity(target)
        except (OSError, ValueError):
            existing = None
        if existing is not None and classify_gpx_identity(existing, identity) == "exact":
            return target, False
        target = project_directory / f"{source.stem}-{suffix}{source.suffix}"
        suffix += 1
    temporary = target.with_name(f".{target.name}.importing")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target, True


def _confirm_changed_candidate(candidate: Path, impact: str) -> tuple[bool, bool]:
    alert = NSAlert.alloc().init()
    alert.setMessageText_("This GPX differs from the saved Adventure identity")
    descriptions = {
        "data": "The route geometry matches, but timing, elevation, or track names changed.",
        "geometry": "The route geometry, segments, track order, or track membership changed.",
        "unknown": "myCamino cannot prove that this is the previously referenced GPX.",
    }
    alert.setInformativeText_(
        f"{candidate.name}\n\n{descriptions.get(impact, descriptions['unknown'])}\n\n"
        "The existing control file will not be rewritten automatically."
    )
    alert.addButtonWithTitle_("Use GPX and Review Changes")
    alert.addButtonWithTitle_("Use GPX, Review Later")
    alert.addButtonWithTitle_("Cancel")
    response = int(alert.runModal())
    return response in {1000, 1001}, response == 1000


def recover_missing_adventure_gpx(
    adventure_path: str | Path,
    payload: dict,
    preferred_candidate: str | Path | None = None,
) -> MissingGpxRecoveryResult:
    """Run the shared missing-GPX chooser and persist accepted decisions."""
    adventure = Path(adventure_path).expanduser().resolve(strict=False)
    project = adventure.parent
    result_payload = dict(payload)
    missing_reference = str(result_payload.get("gpx_file", "")).strip()
    if not missing_reference:
        return MissingGpxRecoveryResult(result_payload, None, "none")
    expected = project / missing_reference
    if expected.is_file():
        return MissingGpxRecoveryResult(result_payload, expected, "present")

    stored_identity = result_payload.get("gpx_identity")
    candidates = discover_gpx_recovery_candidates(
        project,
        missing_reference,
        stored_identity=stored_identity,
    )
    if preferred_candidate is not None:
        preferred = Path(preferred_candidate).expanduser().resolve(strict=False)
        candidates = [
            GpxRecoveryCandidate(preferred, 900),
            *[item for item in candidates if item.path != preferred],
        ]
    has_identity = normalize_gpx_identity(stored_identity) is not None
    alert = NSAlert.alloc().init()
    alert.setMessageText_("The Adventure's GPX file is missing")
    explanation = (
        f"Referenced file: {missing_reference}\n\n"
        "Choose a likely replacement, find another GPX, keep the missing reference for now, "
        "or delete only the reference. Existing maps and the control file will not be deleted."
    )
    if not has_identity:
        explanation += (
            "\n\nThis Adventure has no stored GPX identity. A suggested file therefore requires "
            "your explicit acceptance; myCamino will store its identity afterward."
        )
    if candidates and candidates[0].evidence:
        explanation += f"\n\nSuggested candidate evidence:\n{candidates[0].evidence}"
    alert.setInformativeText_(explanation)
    popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 520, 28), False)
    if candidates:
        popup.addItemsWithTitles_([_candidate_title(item, has_identity) for item in candidates])
    else:
        popup.addItemWithTitle_("No GPX candidate found in this project")
        popup.setEnabled_(False)
    alert.setAccessoryView_(popup)
    alert.addButtonWithTitle_("Use Selected GPX")
    alert.addButtonWithTitle_("Find Other GPX…")
    alert.addButtonWithTitle_("Ignore for Now")
    alert.addButtonWithTitle_("Delete Reference")
    if not candidates:
        alert.buttons()[0].setEnabled_(False)
    response = int(alert.runModal())
    if response == 1002 or response not in {1000, 1001, 1003}:
        return MissingGpxRecoveryResult(result_payload, None, "ignored")
    if response == 1003:
        result_payload["gpx_file"] = ""
        result_payload.pop("gpx_identity", None)
        atomic_write_json(adventure, result_payload)
        return MissingGpxRecoveryResult(result_payload, None, "deleted")

    selected_record = None
    if response == 1000:
        selected_record = candidates[max(0, int(popup.indexOfSelectedItem()))]
        selected = selected_record.path
    else:
        selected = _choose_external_gpx(project)
        if selected is None:
            return MissingGpxRecoveryResult(result_payload, None, "ignored")
        selected_record = next(
            (item for item in candidates if item.path == selected),
            GpxRecoveryCandidate(selected, 0),
        )

    try:
        candidate_identity = build_gpx_identity(selected)
    except (OSError, ValueError) as exc:
        failure = NSAlert.alloc().init()
        failure.setMessageText_("The selected GPX cannot be used")
        failure.setInformativeText_(str(exc))
        failure.runModal()
        return MissingGpxRecoveryResult(result_payload, None, "ignored")
    impact = classify_gpx_identity(stored_identity, candidate_identity)
    review_requested = False
    # Clicking Use Selected is the explicit legacy acceptance. A candidate that
    # conflicts with an existing identity receives the stronger second warning.
    if has_identity and impact not in {"exact", "equivalent"}:
        accepted, review_requested = _confirm_changed_candidate(selected, impact)
        if not accepted:
            return MissingGpxRecoveryResult(result_payload, None, "ignored", impact)
    try:
        adopted, copied = _copy_external_candidate(selected, project, candidate_identity)
    except OSError as exc:
        failure = NSAlert.alloc().init()
        failure.setMessageText_("The selected GPX could not be copied")
        failure.setInformativeText_(str(exc))
        failure.runModal()
        return MissingGpxRecoveryResult(result_payload, None, "ignored", impact)
    if adopted != selected:
        try:
            candidate_identity = build_gpx_identity(adopted)
        except (OSError, ValueError) as exc:
            if copied:
                adopted.unlink(missing_ok=True)
            failure = NSAlert.alloc().init()
            failure.setMessageText_("The copied GPX could not be verified")
            failure.setInformativeText_(str(exc))
            failure.runModal()
            return MissingGpxRecoveryResult(result_payload, None, "ignored", impact)
    result_payload["gpx_file"] = adopted.name
    result_payload["gpx_identity"] = candidate_identity
    map_base = matching_track_map_base(project, selected_record)
    if map_base:
        result_payload["track_map_base"] = map_base
    try:
        atomic_write_json(adventure, result_payload)
    except OSError as exc:
        if copied:
            adopted.unlink(missing_ok=True)
        failure = NSAlert.alloc().init()
        failure.setMessageText_("The Adventure could not be updated")
        failure.setInformativeText_(str(exc))
        failure.runModal()
        return MissingGpxRecoveryResult(dict(payload), None, "ignored", impact)
    try:
        patch_gpx_source_references(project, missing_reference, adopted)
        if selected.name != Path(missing_reference).name:
            patch_gpx_source_references(project, selected.name, adopted)
    except OSError:
        # Source paths are advisory metadata. The accepted Adventure and GPX
        # remain valid even when a read-only legacy sidecar cannot be modernized.
        pass
    return MissingGpxRecoveryResult(
        result_payload,
        adopted,
        "accepted",
        impact,
        review_requested,
    )
