"""Authoritative user-visible application version metadata."""

from __future__ import annotations

from datetime import date


APP_VERSION = "0.9.1"
APP_BUNDLE_VERSION = "0.9.1"
APP_RELEASE_DATE = "2026-08-30"


def release_date() -> date:
    """Return the declared release date after validating its ISO format."""
    return date.fromisoformat(APP_RELEASE_DATE)


def compact_version_label() -> str:
    """Return the compact label used in the main-window header."""
    released = release_date()
    return f"v{APP_VERSION} · {released:%d %b %Y}"


def full_version_label() -> str:
    """Return a readable version label for tooltips and release metadata."""
    released = release_date()
    return f"Version {APP_VERSION} · {released.day} {released:%B %Y}"


def bundle_build_number() -> str:
    """Return a monotonically sortable numeric macOS bundle build number."""
    return APP_RELEASE_DATE.replace("-", "")
