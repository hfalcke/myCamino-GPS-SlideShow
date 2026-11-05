# SPDX-License-Identifier: GPL-3.0-or-later
"""Display-only track-name formatting; never use for paths or identities."""


def display_track_name(name: str, prefix: str = "") -> str:
    result = str(name or "").lstrip()
    if prefix and result.startswith(prefix):
        result = result[len(prefix):].lstrip()
    return result


def numbered_track_title(title, metadata, number="stage", format="period"):
    """Add presentation numbering only; do not derive asset identities here."""
    value = metadata.get("stage_number") if number == "stage" else metadata.get("track_id") if number == "track" else None
    if value is None or not str(value).strip():
        return title
    import re
    match = re.fullmatch(r"0*(\d+)([a-z]*)", str(value).strip())
    if not match or int(match[1]) <= 0:
        return title
    label = str(int(match[1])) + match[2]
    return f"#{label} {title}" if format == "hash" else f"{label}. {title}"
