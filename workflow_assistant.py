"""Readiness and geometry helpers for the first-time workflow assistant."""

from __future__ import annotations

from dataclasses import dataclass


NEW_ASSISTANT_STATE = {
    "enabled": True,
    "place_names_completed": False,
    "slideshow_started": False,
}


def normalize_assistant_state(value, *, existing_adventure: bool = False):
    """Return a complete assistant state for new or previously saved Adventures."""
    if not isinstance(value, dict):
        return {
            "enabled": True,
            "place_names_completed": bool(existing_adventure),
            "slideshow_started": bool(existing_adventure),
        }
    return {
        "enabled": bool(value.get("enabled", True)),
        "place_names_completed": bool(value.get("place_names_completed", False)),
        "slideshow_started": bool(value.get("slideshow_started", False)),
    }


def next_assistant_stage(readiness, state):
    """Return the first incomplete workflow stage, or ``None`` when complete."""
    if not state.get("enabled", True):
        return None
    for stage in ("project", "adventure", "gpx", "track_maps", "media", "control"):
        if not readiness.get(stage, False):
            return stage
    if not state.get("place_names_completed", False):
        return "place_names"
    if not state.get("slideshow_started", False):
        return "slideshow"
    return None


@dataclass(frozen=True)
class BubbleGeometry:
    frame: tuple[float, float, float, float]
    pointer_side: str
    pointer_offset: float


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
