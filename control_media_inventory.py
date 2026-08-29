# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent membership state for media referenced by a slide-show control file."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


CONTROL_MEDIA_INVENTORY_VERSION = 1


def normalize_media_name(value: str | Path) -> str:
    return unicodedata.normalize("NFC", Path(str(value).strip()).name)


def control_media_inventory_path(control_file: Path | str) -> Path:
    control = Path(control_file).expanduser().resolve(strict=False)
    return control.with_name(f"{control.name}.mycamino-state.json")


def control_file_identity(control_file: Path | str) -> dict[str, object]:
    control = Path(control_file).expanduser().resolve(strict=False)
    try:
        data = control.read_bytes()
        stat_result = control.stat()
    except OSError:
        return {"name": control.name, "size": 0, "sha256": ""}
    return {
        "name": control.name,
        "size": int(stat_result.st_size),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def _folder_added_text(path: Path) -> str:
    try:
        stat_result = path.stat()
    except OSError:
        return ""
    timestamp = getattr(stat_result, "st_birthtime", None)
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp)).astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


@dataclass
class ControlMediaInventory:
    control_file: Path
    entries: dict[str, dict[str, object]] = field(default_factory=dict)
    bootstrap_complete: bool = False
    loaded: bool = False
    updated_at: str = ""
    stored_control_identity: dict[str, object] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return control_media_inventory_path(self.control_file)

    def entry(self, media_name: str | Path) -> Optional[dict[str, object]]:
        return self.entries.get(normalize_media_name(media_name))


@dataclass(frozen=True)
class MediaMembership:
    media_path: Path
    state: str
    reason: str
    first_seen_at: str
    imported_at: str
    folder_added_at: str


def load_control_media_inventory(control_file: Path | str) -> ControlMediaInventory:
    control = Path(control_file).expanduser().resolve(strict=False)
    result = ControlMediaInventory(control_file=control)
    path = control_media_inventory_path(control)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict) or payload.get("format_version") != CONTROL_MEDIA_INVENTORY_VERSION:
        return result
    if str(payload.get("control_file", "")) != control.name:
        return result
    raw_entries = payload.get("media")
    if not isinstance(raw_entries, dict):
        return result
    for raw_name, raw_entry in raw_entries.items():
        if not isinstance(raw_entry, dict):
            continue
        state = str(raw_entry.get("state", ""))
        if state not in {"included", "excluded", "pending"}:
            continue
        name = normalize_media_name(str(raw_entry.get("name") or raw_name))
        result.entries[name] = {
            "name": name,
            "state": state,
            "first_seen_at": str(raw_entry.get("first_seen_at", "")),
            "imported_at": str(raw_entry.get("imported_at", "")),
            "folder_added_at": str(raw_entry.get("folder_added_at", "")),
        }
    result.bootstrap_complete = bool(payload.get("bootstrap_complete"))
    result.loaded = True
    result.updated_at = str(payload.get("updated_at", ""))
    stored_identity = payload.get("control_identity")
    result.stored_control_identity = dict(stored_identity) if isinstance(stored_identity, dict) else {}
    return result


def classify_project_media(
    inventory: ControlMediaInventory,
    media_paths: Iterable[Path | str],
    included_names: Iterable[str],
    imported_paths: Iterable[Path | str] = (),
) -> list[MediaMembership]:
    included = {normalize_media_name(name) for name in included_names}
    imported = {
        Path(path).expanduser().resolve(strict=False)
        for path in imported_paths
    }
    current_identity = control_file_identity(inventory.control_file)
    inventory_matches_control = bool(
        inventory.loaded
        and inventory.stored_control_identity.get("sha256")
        and inventory.stored_control_identity.get("sha256")
        == current_identity.get("sha256")
    )
    now = _iso_now()
    memberships = []
    for value in media_paths:
        media_path = Path(value).expanduser().resolve(strict=False)
        name = normalize_media_name(media_path.name)
        old = inventory.entries.get(name, {})
        old_state = str(old.get("state", ""))
        if name in included:
            state, reason = "included", "Already included"
        elif media_path in imported or old_state == "pending":
            state, reason = "new", "Imported with myCamino"
        elif not inventory.bootstrap_complete:
            state, reason = "unclassified", "Not yet classified"
        elif not old:
            state, reason = "new", "New since the last control-file inventory"
        elif old_state == "included" and not inventory_matches_control:
            state, reason = (
                "unclassified",
                "Removed by an external control-file edit; confirm whether to exclude",
            )
        else:
            state, reason = "excluded", "Previously excluded"
        memberships.append(
            MediaMembership(
                media_path=media_path,
                state=state,
                reason=reason,
                first_seen_at=str(old.get("first_seen_at") or now),
                imported_at=str(old.get("imported_at") or (now if media_path in imported else "")),
                folder_added_at=str(old.get("folder_added_at") or _folder_added_text(media_path)),
            )
        )
    return memberships


def build_control_media_inventory_payload(
    inventory: ControlMediaInventory,
    media_paths: Iterable[Path | str],
    included_names: Iterable[str],
    *,
    control_text: Optional[str] = None,
) -> dict[str, object]:
    included = {normalize_media_name(name) for name in included_names}
    now = _iso_now()
    media = {}
    for value in media_paths:
        path = Path(value).expanduser().resolve(strict=False)
        name = normalize_media_name(path.name)
        old = inventory.entries.get(name, {})
        media[name] = {
            "name": name,
            "state": "included" if name in included else "excluded",
            "first_seen_at": str(old.get("first_seen_at") or now),
            "imported_at": str(old.get("imported_at", "")),
            "folder_added_at": str(old.get("folder_added_at") or _folder_added_text(path)),
        }
    identity = control_file_identity(inventory.control_file)
    if control_text is not None:
        encoded = control_text.encode("utf-8")
        identity = {
            "name": inventory.control_file.name,
            "size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return {
        "format_version": CONTROL_MEDIA_INVENTORY_VERSION,
        "control_file": inventory.control_file.name,
        "control_identity": identity,
        "bootstrap_complete": True,
        "updated_at": now,
        "media": media,
    }


def mark_imported_media(
    control_file: Path | str,
    media_paths: Iterable[Path | str],
) -> None:
    control = Path(control_file).expanduser().resolve(strict=False)
    inventory = load_control_media_inventory(control)
    now = _iso_now()
    for value in media_paths:
        path = Path(value).expanduser().resolve(strict=False)
        name = normalize_media_name(path.name)
        old = inventory.entries.get(name, {})
        inventory.entries[name] = {
            "name": name,
            "state": "pending",
            "first_seen_at": str(old.get("first_seen_at") or now),
            "imported_at": str(old.get("imported_at") or now),
            "folder_added_at": str(old.get("folder_added_at") or _folder_added_text(path)),
        }
    payload = {
        "format_version": CONTROL_MEDIA_INVENTORY_VERSION,
        "control_file": control.name,
        "control_identity": control_file_identity(control),
        "bootstrap_complete": inventory.bootstrap_complete,
        "updated_at": now,
        "media": inventory.entries,
    }
    write_control_media_inventory(payload, control_media_inventory_path(control))


def write_control_media_inventory(payload: dict[str, object], destination: Path | str) -> None:
    path = Path(destination).expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
