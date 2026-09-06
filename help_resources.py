# SPDX-License-Identifier: GPL-3.0-or-later
"""Locate user-facing help documents in source and frozen builds."""

from __future__ import annotations

from pathlib import Path
import sys


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def help_document_path(filename: str) -> Path:
    path = resource_root() / "docs" / filename
    if not path.is_file():
        raise FileNotFoundError(f"Help document is unavailable: {path}")
    return path


def read_help_document(filename: str) -> str:
    return help_document_path(filename).read_text(encoding="utf-8")
