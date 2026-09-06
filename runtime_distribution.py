"""Distribution-specific runtime policy for myCamino builds.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import os
import plistlib
import sys
from enum import Enum
from pathlib import Path


class Distribution(str, Enum):
    DIRECT = "direct"
    APP_STORE = "app-store"


_EXTERNAL_RUNTIME_TOOLS = frozenset({"exiftool", "mdls", "sips", "security", "ps"})
_EMBEDDED_HELPERS = frozenset({"gpstrackshow", "ffmpeg"})


def current_distribution(environment: dict[str, str] | None = None) -> Distribution:
    source = os.environ if environment is None else environment
    value = source.get("MYCAMINO_DISTRIBUTION", "")
    if not value and environment is None and getattr(sys, "frozen", False):
        info_path = Path(sys.executable).resolve(strict=False).parents[1] / "Info.plist"
        try:
            with info_path.open("rb") as handle:
                value = str(plistlib.load(handle).get("MYCaminoDistribution", ""))
        except (OSError, ValueError, IndexError, plistlib.InvalidFileException):
            value = ""
    normalized = str(value).strip().casefold().replace("_", "-")
    return Distribution.APP_STORE if normalized in {"app-store", "appstore", "store"} else Distribution.DIRECT


def is_app_store_build(environment: dict[str, str] | None = None) -> bool:
    return current_distribution(environment) is Distribution.APP_STORE


def external_runtime_tool_allowed(
    tool: str, environment: dict[str, str] | None = None
) -> bool:
    normalized = str(tool).strip().casefold()
    if normalized in _EMBEDDED_HELPERS:
        return True
    return not (is_app_store_build(environment) and normalized in _EXTERNAL_RUNTIME_TOOLS)


def metadata_backend_name(environment: dict[str, str] | None = None) -> str:
    return "native" if is_app_store_build(environment) else "native-with-direct-fallback"
