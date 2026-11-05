# SPDX-License-Identifier: GPL-3.0-or-later
"""Display-only track-name formatting; never use for paths or identities."""


def display_track_name(name: str, prefix: str = "") -> str:
    result = str(name or "").lstrip()
    if prefix and result.startswith(prefix):
        result = result[len(prefix):].lstrip()
    return result
