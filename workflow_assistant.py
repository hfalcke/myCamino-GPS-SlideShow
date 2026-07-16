"""Readiness and geometry helpers for the first-time workflow assistant."""

from __future__ import annotations

from dataclasses import dataclass
import math
import textwrap


NEW_ASSISTANT_STATE = {
    "enabled": True,
    "journey_source_confirmed": False,
    "media_confirmed": False,
    "metadata_prepared": False,
    "place_names_requested": True,
    "place_names_completed": False,
    "slideshow_started": False,
}

ASSISTANT_TEXT_INPUT_STAGES = frozenset({"project", "adventure", "gpx"})


def assistant_stage_uses_text_input(stage):
    """Return whether advancing to this stage should focus its text field."""
    return str(stage or "") in ASSISTANT_TEXT_INPUT_STAGES


def normalize_assistant_state(value, *, existing_adventure: bool = False):
    """Return a complete assistant state for new or previously saved Adventures."""
    if not isinstance(value, dict):
        return {
            "enabled": True,
            "journey_source_confirmed": bool(existing_adventure),
            "media_confirmed": bool(existing_adventure),
            "metadata_prepared": bool(existing_adventure),
            "place_names_requested": True,
            "place_names_completed": bool(existing_adventure),
            "slideshow_started": bool(existing_adventure),
        }
    return {
        "enabled": bool(value.get("enabled", True)),
        "journey_source_confirmed": bool(
            value.get("journey_source_confirmed", existing_adventure)
        ),
        "media_confirmed": bool(value.get("media_confirmed", existing_adventure)),
        "metadata_prepared": bool(value.get("metadata_prepared", existing_adventure)),
        "place_names_requested": bool(value.get("place_names_requested", True)),
        "place_names_completed": bool(value.get("place_names_completed", False)),
        "slideshow_started": bool(value.get("slideshow_started", False)),
    }


def next_assistant_stage(readiness, state, stage_order=None):
    """Return the first incomplete workflow stage, or ``None`` when complete."""
    if not state.get("enabled", True):
        return None
    order = stage_order or (
        "project",
        "adventure",
        "gpx",
        "media",
        "metadata",
        "track_maps",
        "control",
    )
    for stage in order:
        if not readiness.get(stage, False):
            return stage
    if not state.get("slideshow_started", False):
        return "slideshow"
    return None


def detected_gpx_choices(paths):
    """Return journey-source choices tailored to the detected GPX files."""
    detected = [str(path) for path in paths]
    if len(detected) == 1:
        filename = detected[0].replace("\\", "/").rsplit("/", 1)[-1]
        return (
            ("use_detected_gpx", f"use {filename}"),
            ("choose_other_gpx", "choose other GPX files..."),
            ("no_gpx", "no GPX file - use only photos"),
        )
    if len(detected) > 1:
        return (
            ("join_detected_gpx", f"join the {len(detected)} detected GPX files"),
            ("choose_other_gpx", "choose other GPX files..."),
            ("no_gpx", "no GPX file - use only photos"),
        )
    return (
        ("choose_other_gpx", "choose GPX files..."),
        ("no_gpx", "no GPX file - use only photos"),
    )


@dataclass(frozen=True)
class BubbleGeometry:
    frame: tuple[float, float, float, float]
    pointer_side: str
    pointer_offset: float


def assistant_bubble_size(content):
    """Estimate a compact bubble size for its wrapped text and visible controls."""
    content = content if isinstance(content, dict) else {"message": str(content or "")}
    message = str(content.get("message", "")).strip()
    actions = list(content.get("actions") or [])[:3]
    choices = list(content.get("choices") or [])
    longest_line = max((len(line) for line in message.splitlines()), default=0)
    if longest_line <= 38 and len(message) <= 70:
        width = 300.0
    elif longest_line <= 62 and len(message) <= 150:
        width = 360.0
    else:
        width = 430.0
    if choices:
        width = max(width, 390.0)
        longest_choice = max((len(str(item[1])) for item in choices), default=0)
        width = max(width, min(470.0, 54.0 + longest_choice * 7.0))
    if len(actions) >= 3:
        width = max(width, 420.0)
    elif len(actions) == 2:
        width = max(width, 350.0)

    characters_per_line = max(28, int((width - 54.0) / 7.0))
    wrapped_lines = 0
    for paragraph in message.splitlines() or [""]:
        wrapped_lines += max(
            1,
            len(textwrap.wrap(paragraph, width=characters_per_line))
            or int(math.ceil(len(paragraph) / characters_per_line)),
        )
    text_height = max(38.0, wrapped_lines * 17.0 + 5.0)
    controls_height = (24.0 * len(choices) + 5.0 if choices else 0.0) + (
        34.0 if actions else 0.0
    )
    return width, text_height + controls_height + 37.0


def relocated_bubble_geometry(container, target, size, origin, margin=10.0):
    """Keep a user-positioned bubble inside its container and aim at its target."""
    cx, cy, cw, ch = (float(value) for value in container)
    tx, ty, tw, th = (float(value) for value in target)
    bw = min(float(size[0]), max(120.0, cw - 2.0 * margin))
    bh = min(float(size[1]), max(72.0, ch - 2.0 * margin))
    min_x, max_x = cx + margin, cx + cw - margin - bw
    min_y, max_y = cy + margin, cy + ch - margin - bh
    x = min(max(float(origin[0]), min_x), max_x)
    y = min(max(float(origin[1]), min_y), max_y)
    target_mid_x = tx + tw / 2.0
    target_mid_y = ty + th / 2.0

    if target_mid_y >= y + bh:
        side = "top"
    elif target_mid_y <= y:
        side = "bottom"
    elif target_mid_x <= x:
        side = "left"
    elif target_mid_x >= x + bw:
        side = "right"
    else:
        distances = (
            (abs(target_mid_y - (y + bh)), "top"),
            (abs(target_mid_y - y), "bottom"),
            (abs(target_mid_x - x), "left"),
            (abs(target_mid_x - (x + bw)), "right"),
        )
        side = min(distances)[1]

    if side in {"top", "bottom"}:
        pointer = target_mid_x - x
        limit = bw
    else:
        pointer = target_mid_y - y
        limit = bh
    pointer = min(max(pointer, 22.0), max(22.0, limit - 22.0))
    return BubbleGeometry((x, y, bw, bh), side, pointer)


def bubble_geometry(container, target, size=(350.0, 112.0), margin=10.0, gap=8.0):
    """Place a bubble near a target without covering it or leaving its container."""
    cx, cy, cw, ch = (float(value) for value in container)
    tx, ty, tw, th = (float(value) for value in target)
    bw = min(float(size[0]), max(120.0, cw - 2.0 * margin))
    bh = min(float(size[1]), max(72.0, ch - 2.0 * margin))
    target_mid_x = tx + tw / 2.0
    target_mid_y = ty + th / 2.0

    candidates = (
        (target_mid_x - bw / 2.0, ty + th + gap, "bottom"),
        (target_mid_x - bw / 2.0, ty - bh - gap, "top"),
        (tx + tw + gap, target_mid_y - bh / 2.0, "left"),
        (tx - bw - gap, target_mid_y - bh / 2.0, "right"),
    )
    min_x, max_x = cx + margin, cx + cw - margin - bw
    min_y, max_y = cy + margin, cy + ch - margin - bh
    for x, y, side in candidates:
        if min_x <= x <= max_x and min_y <= y <= max_y:
            pointer = target_mid_x - x if side in {"top", "bottom"} else target_mid_y - y
            limit = bw if side in {"top", "bottom"} else bh
            return BubbleGeometry((x, y, bw, bh), side, min(max(pointer, 22.0), limit - 22.0))

    x = min(max(candidates[0][0], min_x), max_x)
    y = min(max(candidates[0][1], min_y), max_y)
    side = "bottom" if y >= ty + th else "top"
    pointer = min(max(target_mid_x - x, 22.0), bw - 22.0)
    return BubbleGeometry((x, y, bw, bh), side, pointer)
