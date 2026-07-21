# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve project license documents in source and PyInstaller builds."""

from __future__ import annotations

from pathlib import Path
import sys


DOCUMENTS = {
    "license": ("GPL-3.0.txt", "LICENSE"),
    "copyright": ("COPYRIGHT.txt", "COPYRIGHT"),
    "third_party": ("Third-Party Notices.txt", "THIRD_PARTY_NOTICES.md"),
    "source": ("Source Code Information.txt", "SOURCE_CODE.md"),
}


def resource_root(bundle_root: str | Path | None = None) -> Path:
    """Return the PyInstaller resource root or the source checkout root."""
    if bundle_root is not None:
        return Path(bundle_root)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent


def license_document_path(
    kind: str,
    *,
    bundle_root: str | Path | None = None,
    source_root: str | Path | None = None,
) -> Path:
    """Locate a named project document, preferring packaged resources."""
    try:
        packaged_name, source_name = DOCUMENTS[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown license document: {kind}") from exc

    root = resource_root(bundle_root)
    packaged = root / "licenses" / "myCamino" / packaged_name
    if packaged.is_file():
        return packaged

    source = Path(source_root) if source_root is not None else root
    fallback = source / source_name
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"The {kind} document is not available: {packaged}")


def read_license_document(kind: str, **kwargs) -> str:
    """Read one license document as UTF-8 text."""
    return license_document_path(kind, **kwargs).read_text(encoding="utf-8")
