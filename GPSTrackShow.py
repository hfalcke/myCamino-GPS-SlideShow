#!/usr/bin/env python3
"""Display a GPS-aware photo slideshow on macOS using Cocoa only."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import resource
import signal
import subprocess
import sys
import time
import traceback
import warnings
import textwrap
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from plot_metadata_utils import (
    coordinate_to_pixel,
    extract_coordinate_point,
    media_sidecar_matches_media,
    media_sidecar_path,
    read_photo_metadata,
    read_plot_metadata,
)
from track_timing_utils import haversine_km, timed_points_payload
from track_map_layout_utils import (
    CORNER_ORDER,
    RectTuple,
    best_media_corner_layout,
    best_unframed_media_layout,
    cached_clear_box_options,
    clear_corner_rect_options,
    canonical_track_map_name,
    inset_rect,
    largest_clear_corner_rects,
    map_plot_rect,
    resolve_track_map_variant,
)
from elevation_profile_cache import (
    ELEVATION_PROFILE_HEIGHT,
    ELEVATION_PROFILE_WIDTH,
    elevation_profile_cache_is_current,
    elevation_profile_cache_paths,
    elevation_profile_manifest,
    elevation_profile_ranges,
    elevation_profile_segments,
)
from map_overlay import map_uses_dynamic_overlays, placement_obstacle_points, scene_from_metadata
from audio_playlist import AUDIO_EXTENSIONS, MusicTransportState, load_audio_playlist
from video_audio_normalization import (
    NormalizationSettings,
    load_manifest as load_video_normalization_manifest,
    valid_normalized_video,
)
from slideshow_control_format import (
    ControlDirective,
    ControlSyntaxError,
    MusicAction,
    MusicDirective,
    MusicSyntaxError,
    control_label_key,
    is_control_directive,
    is_music_directive,
    normalize_control_transition,
    parse_control_directive,
    parse_music_directive,
)

try:
    import objc
    from AppKit import (
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyRegular,
        NSAlert,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSBitmapImageFileTypePNG,
        NSBitmapImageRep,
        NSColor,
        NSEvent,
        NSEventMaskKeyDown,
        NSEventMaskKeyUp,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSGraphicsContext,
        NSAffineTransform,
        NSCompositingOperationSourceOver,
        NSImageAlignCenter,
        NSImage,
        NSImageScaleProportionallyUpOrDown,
        NSImageView,
        NSShadow,
        NSLeftArrowFunctionKey,
        NSMakeRect,
        NSMakeSize,
        NSRightArrowFunctionKey,
        NSRoundLineCapStyle,
        NSRoundLineJoinStyle,
        NSScreen,
        NSSplitView,
        NSDownArrowFunctionKey,
        NSEventModifierFlagCommand,
        NSUpArrowFunctionKey,
        NSView,
        NSViewHeightSizable,
        NSViewWidthSizable,
        NSFloatingWindowLevel,
        NSWindow,
        NSWindowAbove,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorFullScreenPrimary,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSObject, NSString, NSTimer, NSURL

    try:
        from AVFoundation import AVAssetImageGenerator, AVPlayer, AVURLAsset
        from AVKit import AVPlayerView, AVPlayerViewControlsStyleNone
        from CoreMedia import CMTimeGetSeconds, CMTimeMake

        AVKIT_VIDEO_AVAILABLE = True
    except (ImportError, ModuleNotFoundError):  # pragma: no cover - depends on local environment
        AVKIT_VIDEO_AVAILABLE = False
        AVAssetImageGenerator = None  # type: ignore[assignment]
        AVPlayer = None  # type: ignore[assignment]
        AVPlayerView = None  # type: ignore[assignment]
        AVPlayerViewControlsStyleNone = 0  # type: ignore[assignment]
        AVURLAsset = None  # type: ignore[assignment]
        CMTimeGetSeconds = None  # type: ignore[assignment]
        CMTimeMake = None  # type: ignore[assignment]

    APPKIT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover - depends on local environment
    APPKIT_AVAILABLE = False
    AVKIT_VIDEO_AVAILABLE = False
    objc = None  # type: ignore[assignment]

if objc is not None and hasattr(objc, "ObjCPointerWarning"):
    warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)


def available_screen_count() -> int:
    """Return the current number of macOS screens when AppKit is available."""
    if not APPKIT_AVAILABLE:
        return 1
    try:
        return len(list(NSScreen.screens()))
    except Exception:
        return 1


def resolve_map_window(requested: Optional[bool], screen_count: int) -> bool:
    """Resolve automatic window mode without requiring AppKit in tests."""
    if requested is not None:
        return bool(requested)
    return int(screen_count) >= 2


def set_runtime_map_window(config: "Config", enabled: bool) -> None:
    """Update the frozen runtime configuration during a live `w` toggle."""
    object.__setattr__(config, "mapwindow", bool(enabled))


def external_jump_command_row(
    payload: object,
    control_file: Path,
    last_sequence: int,
    row_count: int,
) -> Optional[tuple[int, int]]:
    """Validate one editor-to-player jump command."""
    if not isinstance(payload, dict) or payload.get("command") != "jump":
        return None
    try:
        sequence = int(payload.get("sequence", -1))
        command_control = Path(str(payload.get("control_file", ""))).resolve(strict=False)
    except (TypeError, ValueError):
        return None
    row = payload.get("row")
    if (
        sequence <= int(last_sequence)
        or command_control != Path(control_file).resolve(strict=False)
        or not isinstance(row, int)
        or not 0 <= row < int(row_count)
    ):
        return None
    return sequence, row


def external_settings_command(
    payload: object,
    last_sequence: int,
) -> Optional[tuple[int, dict, bool]]:
    """Validate one GUI-to-player live Settings update."""
    if not isinstance(payload, dict) or payload.get("command") != "settings":
        return None
    try:
        sequence = int(payload.get("sequence", -1))
    except (TypeError, ValueError):
        return None
    values = payload.get("values")
    if sequence <= int(last_sequence) or not isinstance(values, dict):
        return None
    return sequence, dict(values), bool(payload.get("restore_display", False))


def external_restart_command(
    payload: object,
    last_sequence: int,
) -> Optional[int]:
    """Validate one GUI request to rebuild the currently active slide."""
    if not isinstance(payload, dict) or payload.get("command") != "restart":
        return None
    try:
        sequence = int(payload.get("sequence", -1))
    except (TypeError, ValueError):
        return None
    return sequence if sequence > int(last_sequence) else None


def should_show_stage_overview_preview(
    has_map_presenter: bool,
    show_with_map_presenter: bool,
    start_fraction: float,
    has_resume_media: bool,
    relation: Optional[str] = None,
) -> bool:
    """Return whether a fresh stage needs its timed overview inset."""
    return (
        (not has_map_presenter or show_with_map_presenter)
        and start_fraction <= 0.0
        and not has_resume_media
        and relation in {None, ""}
    )


def should_restart_time_lapse_stage_at_overview(
    progress: float,
    has_current_media: bool,
    media_cursor: int,
    overview_preview_active: bool,
    relation: Optional[str],
) -> bool:
    """Return whether Back should first restore the current stage overview."""
    return (
        not overview_preview_active
        and relation in {None, ""}
        and (
            float(progress) > 0.0
            or bool(has_current_media)
            or int(media_cursor) > 0
        )
    )


def slideshow_transition_completion_allowed(
    time_lapse_running: bool,
    overview_preview_active: bool,
) -> bool:
    """Allow only the single-window overview to finish during Time-Lapse."""
    return not time_lapse_running or overview_preview_active


DEFAULT_LIST_NAME = "photos-sorted.lst"
TRANSITION_MS = 700
TRANSITION_STEPS = 14
WIPE_TRANSITION_MS = 1000
WIPE_TRANSITION_STEPS = 100
FIRST_STAGE_OVERVIEW_STARTUP_GRACE_SECONDS = 1.0
INTRO_AUTO_ADVANCE_SECONDS = 30.0
SLIDESHOW_CHECKPOINT_VERSION = 4
MUSIC_RESUME_STATE_VERSION = 1
DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 800
MEMORY_WATCHDOG_INTERVAL_SECONDS = 5.0
HELP_OVERLAY_PERSISTENCE_SECONDS = 5.0
GIBIBYTE = 1024**3
DEFAULT_DOT_COLOR_NAME = "red"
DEFAULT_DOT_SIZE = 6
PILGRIM_FRAME_INTERVAL_SECONDS = 0.1
PILGRIM_MOTION_TOLERANCE_FRACTION = 0.0015
PILGRIM_VISIBLE_SOURCE_RECT = (143.0, 34.0, 230.0, 447.0)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
KEY_HELP_LINES = [
    "Keys:",
    "space                 start show / next image / pause auto",
    "right / down          next image",
    "left / up             previous image",
    "Cmd + arrows          jump to previous/next stage",
    "m                     toggle auto/manual mode",
    "e                     toggle elevation profiles",
    "a                     toggle background audio",
    "n                     toggle normalized/original video audio",
    "+ / -                 change duration in auto mode",
    "c                     toggle the complete clock/title/statistics header",
    "s                     open Slide Show settings in the myCamino window",
    "t / T                 next / previous playback style",
    "f                     toggle fullscreen/window mode",
    "w                     toggle single/separate overview windows",
    "d                     swap photo/map displays",
    "D                     toggle memory debug display",
    "i                     show photo metadata overlay",
    "h                     show this key help",
    "Settings              customize header lines, statistics, and background layout",
    "q or Esc              quit",
]
STARTUP_HINT_TEXT = (
    "Press Space or Right Arrow to continue. Press h for keyboard help."
)

COLOR_NAMES = {
    "black": (0.0, 0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0, 1.0),
    "green": (0.0, 1.0, 0.0, 1.0),
    "blue": (0.0, 0.0, 1.0, 1.0),
    "yellow": (1.0, 1.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0, 1.0),
    "gray": (0.5, 0.5, 0.5, 1.0),
    "grey": (0.5, 0.5, 0.5, 1.0),
    "orange": (1.0, 0.647, 0.0, 1.0),
}


def bundled_resource_path(filename: str) -> Path:
    """Resolve a development or PyInstaller-bundled data file."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / filename


def pilgrim_motion_threshold(width: float, height: float) -> float:
    """Return a resolution-scaled view-space tolerance for visible motion."""
    return max(1.0, math.hypot(max(0.0, width), max(0.0, height)) * PILGRIM_MOTION_TOLERANCE_FRACTION)


def time_lapse_marker_style(configured_style: str, *, overview: bool) -> str:
    """Use transport symbols on both maps while keeping pilgrims stage-local."""
    normalized = str(configured_style or "pilgrim").strip().casefold()
    if overview and normalized == "pilgrim":
        return "arrow"
    return normalized if normalized in {"pilgrim", "bike", "car", "plane", "arrow"} else "arrow"


@dataclass
class PilgrimWalkState:
    """Select retained walk-cycle frames from marker movement over time."""

    frame_index: int = 0
    motion_anchor: Optional[tuple[float, float]] = None
    last_motion_time: Optional[float] = None
    last_frame_time: Optional[float] = None
    stationary: bool = True

    def reset(self) -> None:
        self.frame_index = 0
        self.motion_anchor = None
        self.last_motion_time = None
        self.last_frame_time = None
        self.stationary = True

    def update(
        self,
        position: tuple[float, float],
        now: float,
        motion_threshold: float,
        frame_interval: float = PILGRIM_FRAME_INTERVAL_SECONDS,
    ) -> int:
        if self.motion_anchor is None:
            self.motion_anchor = position
            self.last_motion_time = now
            self.last_frame_time = now
            self.frame_index = 0
            self.stationary = True
            return self.frame_index

        moved = math.hypot(
            position[0] - self.motion_anchor[0],
            position[1] - self.motion_anchor[1],
        ) >= max(0.0, motion_threshold)
        if moved:
            self.motion_anchor = position
            self.last_motion_time = now
            if self.stationary:
                self.stationary = False
                self.frame_index = 3
                self.last_frame_time = now
                return self.frame_index
            elapsed = max(0.0, now - (self.last_frame_time if self.last_frame_time is not None else now))
            interval = max(frame_interval, 1.0e-6)
            # Treat an interval reached within floating-point precision as due.
            steps = math.floor((elapsed + interval * 1.0e-9) / interval)
            if steps:
                self.frame_index = ((max(1, self.frame_index) - 1 + steps) % 8) + 1
                self.last_frame_time = (self.last_frame_time or now) + steps * interval
            return self.frame_index

        if self.last_motion_time is not None and now - self.last_motion_time >= frame_interval:
            self.stationary = True
            self.frame_index = 0
        return self.frame_index


class Transition(str, Enum):
    """Supported slide transition names."""

    BLEND = "BLEND"
    FADE = "FADE"
    SWITCH = "SWITCH"
    EXPAND = "EXPAND"
    WIPE = "WIPE"
    COLLAGE = "COLLAGE"
    QUAD = "QUAD"
    RANDOM = "RANDOM"


def normalize_transition(value: object) -> str:
    """Return the case-insensitive CLI/settings form of a transition name."""
    return str(value).strip().upper()


ENABLED_TRANSITIONS = (
    Transition.BLEND,
    Transition.FADE,
    Transition.SWITCH,
    Transition.EXPAND,
    Transition.COLLAGE,
    Transition.QUAD,
    Transition.RANDOM,
)
PLAYBACK_STYLE_VALUES = (
    "TIME_LAPSE",
    *(transition.value for transition in ENABLED_TRANSITIONS),
)

RANDOM_TRANSITIONS = (
    Transition.BLEND,
    Transition.FADE,
    Transition.SWITCH,
    Transition.EXPAND,
    Transition.COLLAGE,
    Transition.QUAD,
)


@dataclass(frozen=True)
class Config:
    """Parsed command-line configuration."""

    photodir: Path
    inputlist: Path
    start_track: int
    duration: float
    transition_duration_ms: int
    transition: Transition
    initial_style: str
    background_color: tuple[float, float, float, float]
    dot_color: tuple[float, float, float, float]
    dot_size: int
    arrow_length: float
    font_color: tuple[float, float, float, float]
    font_size: int
    mapwindow: bool
    join_windows: bool
    end_behavior: str
    photo_geometry: Optional[str]
    map_geometry: Optional[str]
    fullscreen: bool
    window_swap: bool
    clock: bool
    header_stage_name: bool
    header_track_details: bool
    header_place_name: bool
    header_track_stats: bool
    header_background: str
    header_shadow_color: tuple[float, float, float, float]
    elevation_profile: bool
    debug: bool
    keypressed: bool
    collage_size_min: float
    collage_size_max: float
    collage_max_images: int
    trackdir: Optional[Path] = None
    time_lapse_stages: bool = False
    time_lapse_duration: float = 30.0
    time_lapse_media_min_fraction: float = 0.5
    time_lapse_marker: str = "pilgrim"
    time_lapse_overview_as_media: bool = True
    time_lapse_overview_on_stage_map_dual: bool = True
    track_map_before_media: bool = False
    resume_index: Optional[int] = None
    resume_progress: Optional[float] = None
    resume_media_index: Optional[int] = None
    resume_phase: Optional[str] = None
    resume_audio_state: Optional[dict] = None
    resume_control_state: Optional[dict] = None
    state_file: Optional[Path] = None
    command_file: Optional[Path] = None
    music_source: Optional[Path] = None
    music_playlist: Optional[Path] = None
    audio_crossfade_seconds: float = 2.0
    music_volume_percent: float = 65.0
    video_volume_percent: float = 100.0
    use_normalized_videos: bool = True
    video_normalization_target_lufs: float = -16.0
    video_normalization_max_boost_db: float = 12.0
    video_normalization_true_peak_db: float = -1.5
    gpx_overlay_mode: str = "line"
    media_overlay_mode: str = "dots"
    route_color: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    route_width: float = 4.0
    endpoint_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    endpoint_size: float = 0.0
    media_point_color: tuple[float, float, float, float] = (0.0, 0.4, 1.0, 1.0)
    media_point_size: float = 12.0
    dynamic_map_header: bool = True
    map_header_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    map_header_font_factor: float = 2.2
    track_title_mode: str = "endpoint_places"
    adventure_title: str = ""
    adventure_description: str = ""
    adventure_title_image: Optional[Path] = None

    @property
    def time_lapse_media_max_fraction(self) -> float:
        """Compatibility alias for adventures and callers using the old name."""
        return self.time_lapse_media_min_fraction


@dataclass(frozen=True)
class PhotoListEntry:
    """One parsed photo line from the input list."""

    source_name: str
    time_text: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    place: Optional[str]


@dataclass(frozen=True)
class WindowTarget:
    """One rendered image update for a presenter."""

    presenter_name: str
    image: object
    transition: Transition
    clock_time: Optional[tuple[int, int]] = None
    clock_date_text: Optional[str] = None
    place_text: Optional[str] = None
    info_text: Optional[str] = None
    photo_identity: Optional[str] = None
    video_path: Optional[Path] = None
    video_duration: Optional[float] = None
    header_lines: tuple[str, ...] = ()
    header_metrics: tuple[str, ...] = ()
    header_metadata: Optional[dict] = None


@dataclass
class DisplayState:
    """One currently displayed slideshow state."""

    targets: list[WindowTarget]
    next_callback: Optional[Callable[[], None]]
    auto_delay: Optional[float]
    description: str
    playlist_index: int


@dataclass
class TimeLapseStage:
    """A #Map block and its media rows prepared for animated playback."""

    map_index: int
    next_index: int
    map_filename: str
    date_text: Optional[str]
    media_indexes: list[int]
    media_entries: list[PhotoListEntry]
    media_date_texts: list[Optional[str]]
    relation: Optional[str] = None
    next_date_text: Optional[str] = None


@dataclass(frozen=True)
class StageDescriptor:
    """One map-backed display stage with stable control-file indexes."""

    map_index: int
    next_index: int
    directive: MapDirective
    date_text: Optional[str]
    media_indexes: tuple[int, ...]


class PlaybackPhase(str, Enum):
    """Logical state used for deterministic cursor navigation and resume."""

    INTRO_INFO = "intro_info"
    INTRO_OVERVIEW = "intro_overview"
    STAGE_MAP = "stage_map"
    STAGE_OVERVIEW = "stage_overview"
    ELEVATION_PROFILE = "elevation_profile"
    MEDIA = "media"
    TIME_LAPSE = "time_lapse"


def normalize_time_lapse_resume_phase(value: Optional[str]) -> Optional[str]:
    """Map the former Time-Lapse Stage Map state to the new overview phase."""
    if value == PlaybackPhase.STAGE_MAP.value:
        return PlaybackPhase.STAGE_OVERVIEW.value
    return value


def should_restart_time_lapse_stage_intro(
    resume_phase: Optional[str],
    progress: float,
    relation: Optional[str],
) -> bool:
    """Treat a saved Time-Lapse position at 0% as the stage boundary."""
    return (
        resume_phase == PlaybackPhase.TIME_LAPSE.value
        and relation is None
        and float(progress) <= 0.0
    )


def should_restart_slideshow(end_behavior: str, completed_replays: int) -> bool:
    """Return whether the completed show should restart at its title slide."""
    behavior = str(end_behavior).strip().casefold()
    if behavior == "loop_forever":
        return True
    return behavior == "loop_once" and int(completed_replays) < 1


def time_lapse_overview_display_seconds(
    media_duration: float,
    first_overview: bool,
) -> float:
    """Keep the first overview visible after native windows finish settling."""
    return max(0.0, float(media_duration)) + (
        FIRST_STAGE_OVERVIEW_STARTUP_GRACE_SECONDS if first_overview else 0.0
    )


@dataclass(frozen=True)
class MapDirective:
    """One normal or adjacent-day map directive from a control file."""

    keyword: str
    filename: str
    relation: Optional[str] = None

    @property
    def is_special(self) -> bool:
        return self.relation is not None


def parse_map_directive(line: str) -> Optional[MapDirective]:
    """Parse map directives while keeping adjacent-day maps distinct."""
    content = str(line).strip()
    for keyword, relation in (
        ("#MapBefore:", "Day before"),
        ("#MapAfter:", "Day after"),
        ("#MediaMap:", ""),
        ("#Map:", None),
    ):
        if content.startswith(keyword):
            filename = content[len(keyword) :].strip()
            return MapDirective(keyword[:-1], filename, relation) if filename else None
    return None


def is_normal_map_directive(line: str) -> bool:
    """Return whether a line advances the journey by one regular stage."""
    directive = parse_map_directive(line)
    return directive is not None and not directive.is_special


def adjacent_stage_map_index(
    lines: list[str],
    current_index: int,
    forward: bool,
) -> Optional[int]:
    """Return the next or previous map row relative to the active stage."""
    map_indexes = [
        index
        for index, line in enumerate(lines)
        if parse_map_directive(line) is not None
    ]
    if not map_indexes:
        return None
    bounded_index = max(-1, min(int(current_index), len(lines) - 1))
    current_stage = next(
        (index for index in reversed(map_indexes) if index <= bounded_index),
        None,
    )
    if forward:
        threshold = current_stage if current_stage is not None else bounded_index
        return next((index for index in map_indexes if index > threshold), None)
    if current_stage is None:
        return None
    return next(
        (index for index in reversed(map_indexes) if index < current_stage),
        None,
    )


def previous_displayable_playlist_index(lines: list[str], before_index: int) -> Optional[int]:
    """Find the previous map or media row without retaining rendered images."""
    for index in range(min(before_index - 1, len(lines) - 1), -1, -1):
        line = lines[index]
        if parse_map_directive(line) is not None or not line.startswith("#"):
            return index
    return None


def parse_stage_descriptors(lines: list[str]) -> list[StageDescriptor]:
    """Preparse map blocks so navigation never has to infer stage boundaries."""
    stages: list[StageDescriptor] = []
    current_date = None
    map_rows: list[tuple[int, MapDirective, Optional[str]]] = []
    for index, line in enumerate(lines):
        content = line.strip()
        if content.startswith("#Datum:"):
            current_date = content.partition(":")[2].strip()
        directive = parse_map_directive(line)
        if directive is not None:
            map_rows.append((index, directive, current_date))
    for position, (map_index, directive, date_text) in enumerate(map_rows):
        next_index = map_rows[position + 1][0] if position + 1 < len(map_rows) else len(lines)
        media_indexes = tuple(
            index
            for index in range(map_index + 1, next_index)
            if not lines[index].startswith("#")
        )
        stages.append(
            StageDescriptor(
                map_index,
                next_index,
                directive,
                date_text,
                media_indexes,
            )
        )
    return stages


def stage_index_for_playlist_row(stages: list[StageDescriptor], row_index: int) -> Optional[int]:
    """Return the stage containing one control-file row."""
    for index, stage in enumerate(stages):
        if stage.map_index <= row_index < stage.next_index:
            return index
    return None


def parse_args(argv: list[str]) -> Config:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="GPSTrackShow.py",
        description="Show a GPS/photo slideshow with optional map window on macOS.",
        epilog="\n".join(KEY_HELP_LINES),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("photodir", type=Path, help="Directory containing images, JSON files, and list file.")
    parser.add_argument(
        "--inputlist",
        "-i",
        type=Path,
        default=None,
        help=f"Input list file (default: photodir/{DEFAULT_LIST_NAME}).",
    )
    parser.add_argument(
        "--trackdir",
        type=Path,
        default=None,
        help="Directory containing overview/track map images and their JSON sidecars (default: photodir).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        help="Start with the Nth track map in the input sequence (1 = first track).",
    )
    parser.add_argument("--resume-index", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--resume-progress", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--resume-media-index", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--resume-phase",
        choices=[phase.value for phase in PlaybackPhase],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--resume-audio-state", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--resume-control-state", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--state-file", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--command-file", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--adventure-title", default="", help="Adventure title shown on the introductory slide.")
    parser.add_argument("--adventure-description", default="", help="Adventure description shown on the introductory slide.")
    parser.add_argument(
        "--adventure-title-image",
        type=Path,
        default=None,
        help="Optional title image shown below the introductory description.",
    )
    parser.add_argument("--music", type=Path, default=None, help="One audio file or a directory of background music.")
    parser.add_argument("--music-playlist", type=Path, default=None, help="Optional $label playlist for a music directory.")
    parser.add_argument("--audio-crossfade-seconds", type=float, default=2.0, help="Seconds used to crossfade music titles and #MUSIC transport changes (default: 2).")
    parser.add_argument("--music-volume-percent", type=float, default=65.0, help="Maximum background-music level in percent (default: 65).")
    parser.add_argument("--video-volume-percent", type=float, default=100.0, help="Video playback level in percent (default: 100).")
    parser.add_argument("--use-normalized-videos", action="store_true", default=True, help="Prefer current generated videos with normalized audio (default).")
    parser.add_argument("--no-normalized-videos", action="store_false", dest="use_normalized_videos", help="Use original videos at startup.")
    parser.add_argument("--video-normalization-target-lufs", type=float, default=-16.0, help=argparse.SUPPRESS)
    parser.add_argument("--video-normalization-max-boost-db", type=float, default=12.0, help=argparse.SUPPRESS)
    parser.add_argument("--video-normalization-true-peak-db", type=float, default=-1.5, help=argparse.SUPPRESS)
    parser.add_argument("--duration", "-d", type=float, default=3.0, help="Seconds per main slide.")
    parser.add_argument("--transition-duration-ms", type=int, default=TRANSITION_MS, help="Animated transition duration in milliseconds.")
    parser.add_argument(
        "--time-lapse-stages",
        action="store_true",
        help="Play regular map stages as GPS time-lapses and adjacent-day stages as static maps.",
    )
    parser.add_argument("--time-lapse-duration", type=float, default=30.0, help="Seconds of active arrow motion per time-lapse stage (default: 30).")
    parser.add_argument(
        "--time-lapse-media-min-fraction",
        "--time-lapse-media-max-fraction",
        dest="time_lapse_media_min_fraction",
        type=float,
        default=0.5,
        help="Preferred minimum framed-media size relative to the window; media grows to the largest track-free corner (default: 0.5).",
    )
    parser.add_argument(
        "--time-lapse-marker",
        choices=("pilgrim", "bike", "car", "plane", "arrow"),
        default="pilgrim",
        help="Moving Time-Lapse marker: pilgrim, bicycle, car, airplane, or arrow (default: pilgrim).",
    )
    parser.add_argument(
        "--time-lapse-overview-fullscreen",
        action="store_false",
        dest="time_lapse_overview_as_media",
        default=True,
        help="In single-window Time-Lapse mode, show the stage overview full-screen instead of framed over the track map.",
    )
    parser.add_argument(
        "--time-lapse-overview-on-stage-map-dual",
        action="store_true",
        default=True,
        help="Also show the stage overview inset when a separate overview display is active (default).",
    )
    parser.add_argument(
        "--no-time-lapse-overview-on-stage-map-dual",
        action="store_false",
        dest="time_lapse_overview_on_stage_map_dual",
        help="In dual-display mode, keep the overview only on the overview display.",
    )
    parser.add_argument(
        "--track-map-before-media",
        action="store_true",
        help="In single-window Standard mode, show the marked track map before each photo or video.",
    )
    parser.add_argument(
        "--transition",
        "-t",
        type=normalize_transition,
        choices=PLAYBACK_STYLE_VALUES,
        default="TIME_LAPSE",
        help="Initial playback style (case-insensitive).",
    )
    parser.add_argument("--background-color", default="black", help="Background color.")
    parser.add_argument("--header-stage-name", choices=("on", "off"), default="on", help="Show the stage name in the centered slide-show header.")
    parser.add_argument("--header-track-details", choices=("on", "off"), default="on", help="Show track length and duration in the centered slide-show header.")
    parser.add_argument("--header-place-name", choices=("on", "off"), default="on", help="Show the current place in the centered slide-show header.")
    parser.add_argument("--header-track-stats", choices=("on", "off"), default="on", help="Show track statistics at the right of the slide-show header.")
    parser.add_argument("--header-background", choices=("black", "transparent", "off"), default="black", help="Header layout for all media and maps; black uses the selected background color and fits content below it, while transparent and off remain full-frame.")
    parser.add_argument("--header-shadow-color", default="black", help="Shadow color used for header text, clock, time, and date.")
    parser.add_argument("--dot-color", default=DEFAULT_DOT_COLOR_NAME, help="GPS marker color.")
    parser.add_argument("--dot-size", type=int, default=DEFAULT_DOT_SIZE, help="GPS marker radius in pixels.")
    parser.add_argument("--gpx-overlay", choices=("line", "hidden"), default="line", help="Dynamic GPX route overlay mode.")
    parser.add_argument("--media-overlay", choices=("dots", "interpolated", "hidden"), default="dots", help="Dynamic media-stage overlay mode.")
    parser.add_argument("--route-color", default="#0000FF", help="Dynamic route-line color.")
    parser.add_argument("--route-width", type=float, default=4.0, help="Dynamic route-line width in image pixels.")
    parser.add_argument("--endpoint-color", default="#FFFFFF", help="Dynamic endpoint marker color.")
    parser.add_argument("--endpoint-size", type=float, default=0.0, help="Dynamic endpoint marker diameter; zero hides endpoints.")
    parser.add_argument("--media-point-color", default="#0066FF", help="Dynamic media-location point color.")
    parser.add_argument("--media-point-size", type=float, default=12.0, help="Dynamic media-location point diameter.")
    parser.add_argument("--dynamic-map-header", choices=("on", "off"), default="on", help="Draw map headers from sidecar metadata.")
    parser.add_argument("--map-header-color", default="#FFFFFF", help="Dynamic map-header text color.")
    parser.add_argument("--map-header-font-factor", type=float, default=2.2, help="Scale dynamic map-header and map-label text (default: 2.2).")
    parser.add_argument(
        "--track-title",
        choices=("endpoint_places", "track_name"),
        default="endpoint_places",
        help="GPX-stage title source: reverse-geocoded endpoints or the GPX track name.",
    )
    parser.add_argument(
        "--arrow-length",
        type=float,
        default=1.0,
        help="Scale factor for the track-map arrow above photo markers; 0 disables arrows.",
    )
    parser.add_argument("--font-color", default="white", help="Overview date text color.")
    parser.add_argument("--font-size", type=int, default=30, help="Overview date text font size.")
    parser.add_argument("--mapwindow", "-m", action="store_true", default=None, help="Open a separate map window.")
    parser.add_argument("--no-mapwindow", action="store_false", dest="mapwindow", default=None, help="Do not open a separate map window.")
    parser.add_argument("--join-windows", "-j", action="store_true", help="Show photo and map views side by side.")
    parser.add_argument(
        "--end-behavior",
        choices=("black", "loop_once", "loop_forever"),
        default="loop_forever",
        help="Behavior after the final slide (default: loop_forever).",
    )
    parser.add_argument(
        "--repeat",
        "-r",
        action="store_true",
        help="Compatibility alias for --end-behavior loop_forever.",
    )
    parser.add_argument("--photo-geometry", default=None, help="Window geometry WIDTHxHEIGHT+X+Y.")
    parser.add_argument("--map-geometry", default=None, help="Window geometry WIDTHxHEIGHT+X+Y.")
    parser.add_argument("--fullscreen", "-f", action="store_true", default=None, help="Start slideshow windows fullscreen.")
    parser.add_argument("--no-fullscreen", action="store_false", dest="fullscreen", default=None, help="Start in windowed mode.")
    parser.add_argument("--switch-display", "-s", action="store_true", dest="window_swap", help="Switch photo/map display assignment at startup.")
    parser.add_argument("--clock", "-c", choices=["on", "off"], default="on", help="Include the analog clock in the header when time is known.")
    parser.add_argument("--elevation-profile", action="store_true", default=True, help="Show a cached elevation profile at the beginning of each GPX stage (default).")
    parser.add_argument("--no-elevation-profile", action="store_false", dest="elevation_profile", help="Do not show elevation profiles at stage starts.")
    parser.add_argument(
        "--collage-size-range",
        default="33-66",
        help="Collage photo size range as percentages of screen size, MIN-MAX (default: 33-66).",
    )
    parser.add_argument(
        "--collage-max-images",
        type=int,
        default=9,
        help="Maximum number of collage images before the photo canvas is cleared (default: 9).",
    )
    parser.add_argument("--debug", action="store_true", help="Print verbose slideshow debug output.")
    parser.add_argument(
        "--keypressed",
        "-k",
        action="store_true",
        help="Manual stepping mode: next with space/right/down, previous with left/up.",
    )

    args = parser.parse_args(argv)
    photodir = args.photodir.expanduser().resolve()
    if not photodir.is_dir():
        parser.error(f"photodir is not a directory: {photodir}")

    inputlist = args.inputlist.expanduser().resolve() if args.inputlist else (photodir / DEFAULT_LIST_NAME)
    if not inputlist.is_file():
        parser.error(f"input list file not found: {inputlist}")
    trackdir = args.trackdir.expanduser().resolve() if args.trackdir else photodir
    if not trackdir.is_dir():
        parser.error(f"trackdir is not a directory: {trackdir}")
    if args.start < 1:
        parser.error("--start must be at least 1")
    if args.resume_index is not None and args.resume_index < 0:
        parser.error("--resume-index must be at least 0")
    if args.resume_progress is not None and not 0.0 <= args.resume_progress <= 1.0:
        parser.error("--resume-progress must be between 0 and 1")
    if args.resume_media_index is not None and args.resume_media_index < 0:
        parser.error("--resume-media-index must be at least 0")
    if args.duration <= 0:
        parser.error("--duration must be greater than 0")
    if args.transition_duration_ms < 0:
        parser.error("--transition-duration-ms must be 0 or greater")
    if not 0.0 <= args.audio_crossfade_seconds <= 30.0:
        parser.error("--audio-crossfade-seconds must be between 0 and 30")
    if not 0.0 <= args.music_volume_percent <= 100.0:
        parser.error("--music-volume-percent must be between 0 and 100")
    if not 0.0 <= args.video_volume_percent <= 100.0:
        parser.error("--video-volume-percent must be between 0 and 100")
    if args.video_normalization_max_boost_db < 0.0:
        parser.error("--video-normalization-max-boost-db must be non-negative")
    if args.time_lapse_duration <= 0:
        parser.error("--time-lapse-duration must be greater than 0")
    if not 0 < args.time_lapse_media_min_fraction <= 1:
        parser.error("--time-lapse-media-min-fraction must be greater than 0 and at most 1")
    if args.dot_size < 1:
        parser.error("--dot-size must be at least 1")
    if args.route_width <= 0:
        parser.error("--route-width must be greater than 0")
    if args.endpoint_size < 0 or args.media_point_size <= 0:
        parser.error("--endpoint-size must be non-negative and --media-point-size must be positive")
    if args.map_header_font_factor <= 0:
        parser.error("--map-header-font-factor must be greater than 0")
    if args.arrow_length < 0:
        parser.error("--arrow-length must be 0 or greater")
    if args.font_size < 8:
        parser.error("--font-size must be at least 8")
    collage_size_min, collage_size_max = parse_percentage_range(args.collage_size_range, parser)
    if args.collage_max_images < 1:
        parser.error("--collage-max-images must be at least 1")
    auto_two_screen_mode = available_screen_count() >= 2
    mapwindow_enabled = resolve_map_window(args.mapwindow, 2 if auto_two_screen_mode else 1)
    fullscreen_enabled = auto_two_screen_mode if args.fullscreen is None else bool(args.fullscreen)
    if args.join_windows and not mapwindow_enabled:
        parser.error("--join-windows requires --mapwindow")
    music_source = args.music.expanduser().resolve() if args.music else None
    if music_source is not None and not music_source.exists():
        parser.error(f"music source not found: {music_source}")
    if music_source is not None and music_source.is_file() and music_source.suffix.casefold() not in AUDIO_EXTENSIONS:
        parser.error(f"unsupported music file: {music_source}")
    music_playlist = args.music_playlist.expanduser().resolve() if args.music_playlist else None
    if music_playlist is not None and not music_playlist.is_file():
        parser.error(f"music playlist not found: {music_playlist}")

    initial_style = "TIME_LAPSE" if args.time_lapse_stages else args.transition
    standard_transition = (
        Transition.BLEND
        if initial_style == "TIME_LAPSE"
        else Transition(initial_style)
    )
    resume_audio_state = None
    if args.resume_audio_state:
        try:
            resume_audio_state = json.loads(args.resume_audio_state)
        except (TypeError, json.JSONDecodeError) as exc:
            parser.error(f"invalid --resume-audio-state: {exc}")
        if not isinstance(resume_audio_state, dict):
            parser.error("--resume-audio-state must contain a JSON object")
    resume_control_state = None
    resume_initial_style = None
    if args.resume_control_state:
        try:
            resume_control_state = json.loads(args.resume_control_state)
        except (TypeError, json.JSONDecodeError) as exc:
            parser.error(f"invalid --resume-control-state: {exc}")
        if not isinstance(resume_control_state, dict):
            parser.error("--resume-control-state must contain a JSON object")
        if resume_control_state.get("version") == 1:
            try:
                resume_initial_style = normalize_control_transition(
                    resume_control_state.get("transition")
                )
            except ControlSyntaxError as exc:
                parser.error(f"invalid resume control transition: {exc}")

    return Config(
        photodir=photodir,
        inputlist=inputlist,
        start_track=int(args.start),
        duration=float(args.duration),
        transition_duration_ms=int(args.transition_duration_ms),
        transition=standard_transition,
        initial_style=initial_style,
        background_color=parse_color(args.background_color, parser, "--background-color"),
        dot_color=parse_color(args.dot_color, parser, "--dot-color"),
        dot_size=args.dot_size,
        arrow_length=float(args.arrow_length),
        font_color=parse_color(args.font_color, parser, "--font-color"),
        font_size=args.font_size,
        mapwindow=mapwindow_enabled,
        join_windows=bool(args.join_windows),
        end_behavior=("loop_forever" if args.repeat else str(args.end_behavior)),
        photo_geometry=args.photo_geometry,
        map_geometry=args.map_geometry,
        fullscreen=fullscreen_enabled,
        window_swap=bool(args.window_swap),
        clock=args.clock == "on",
        header_stage_name=args.header_stage_name == "on",
        header_track_details=args.header_track_details == "on",
        header_place_name=args.header_place_name == "on",
        header_track_stats=args.header_track_stats == "on",
        header_background=str(args.header_background),
        header_shadow_color=parse_color(args.header_shadow_color, parser, "--header-shadow-color"),
        elevation_profile=bool(args.elevation_profile),
        debug=bool(args.debug),
        keypressed=bool(args.keypressed),
        collage_size_min=collage_size_min,
        collage_size_max=collage_size_max,
        collage_max_images=args.collage_max_images,
        trackdir=trackdir,
        time_lapse_stages=(resume_initial_style or initial_style) == "TIME_LAPSE",
        time_lapse_duration=float(args.time_lapse_duration),
        time_lapse_media_min_fraction=float(args.time_lapse_media_min_fraction),
        time_lapse_marker=str(args.time_lapse_marker),
        time_lapse_overview_as_media=bool(args.time_lapse_overview_as_media),
        time_lapse_overview_on_stage_map_dual=bool(
            args.time_lapse_overview_on_stage_map_dual
        ),
        track_map_before_media=bool(args.track_map_before_media),
        resume_index=args.resume_index,
        resume_progress=args.resume_progress,
        resume_media_index=args.resume_media_index,
        resume_phase=args.resume_phase,
        resume_audio_state=resume_audio_state,
        resume_control_state=resume_control_state,
        state_file=args.state_file.expanduser().resolve() if args.state_file else None,
        command_file=args.command_file.expanduser().resolve() if args.command_file else None,
        music_source=music_source,
        music_playlist=music_playlist,
        audio_crossfade_seconds=float(args.audio_crossfade_seconds),
        music_volume_percent=float(args.music_volume_percent),
        video_volume_percent=float(args.video_volume_percent),
        use_normalized_videos=bool(args.use_normalized_videos),
        video_normalization_target_lufs=float(args.video_normalization_target_lufs),
        video_normalization_max_boost_db=float(args.video_normalization_max_boost_db),
        video_normalization_true_peak_db=float(args.video_normalization_true_peak_db),
        gpx_overlay_mode=str(args.gpx_overlay),
        media_overlay_mode=str(args.media_overlay),
        route_color=parse_color(args.route_color, parser, "--route-color"),
        route_width=float(args.route_width),
        endpoint_color=parse_color(args.endpoint_color, parser, "--endpoint-color"),
        endpoint_size=float(args.endpoint_size),
        media_point_color=parse_color(args.media_point_color, parser, "--media-point-color"),
        media_point_size=float(args.media_point_size),
        dynamic_map_header=args.dynamic_map_header == "on",
        map_header_color=parse_color(args.map_header_color, parser, "--map-header-color"),
        map_header_font_factor=float(args.map_header_font_factor),
        track_title_mode=str(args.track_title),
        adventure_title=str(args.adventure_title or "").strip(),
        adventure_description=str(args.adventure_description or "").strip(),
        adventure_title_image=(
            args.adventure_title_image.expanduser().resolve()
            if args.adventure_title_image
            else None
        ),
    )


def parse_percentage_range(value: str, parser: argparse.ArgumentParser) -> tuple[float, float]:
    """Parse one MIN-MAX percentage range into normalized floats."""
    cleaned = value.strip().replace("%", "")
    separator = "-" if "-" in cleaned else ","
    parts = [part.strip() for part in cleaned.split(separator)]
    if len(parts) != 2:
        parser.error("--collage-size-range must look like MIN-MAX")
    try:
        minimum = float(parts[0]) / 100.0
        maximum = float(parts[1]) / 100.0
    except ValueError:
        parser.error("--collage-size-range must contain numeric percentages")
    if minimum <= 0 or maximum <= 0 or minimum > maximum:
        parser.error("--collage-size-range must satisfy 0 < MIN <= MAX")
    return minimum, maximum


def parse_color(value: str, parser: argparse.ArgumentParser, label: str) -> tuple[float, float, float, float]:
    """Parse a color string into normalized RGBA floats."""
    lowered = value.strip().lower()
    if lowered in COLOR_NAMES:
        return COLOR_NAMES[lowered]
    if lowered.startswith("#"):
        hex_value = lowered[1:]
        if len(hex_value) == 6:
            try:
                return (
                    int(hex_value[0:2], 16) / 255.0,
                    int(hex_value[2:4], 16) / 255.0,
                    int(hex_value[4:6], 16) / 255.0,
                    1.0,
                )
            except ValueError:
                pass
    parser.error(f"invalid {label}: {value}")
    return COLOR_NAMES["black"]


def parse_percentage_range_option(value: str) -> tuple[float, float]:
    """Parse one MIN-MAX percentage range for direct Python callers."""
    cleaned = value.strip().replace("%", "")
    separator = "-" if "-" in cleaned else ","
    parts = [part.strip() for part in cleaned.split(separator)]
    if len(parts) != 2:
        raise ValueError("collage_size_range must look like MIN-MAX")
    try:
        minimum = float(parts[0]) / 100.0
        maximum = float(parts[1]) / 100.0
    except ValueError as exc:
        raise ValueError("collage_size_range must contain numeric percentages") from exc
    if minimum <= 0 or maximum <= 0 or minimum > maximum:
        raise ValueError("collage_size_range must satisfy 0 < MIN <= MAX")
    return minimum, maximum


def parse_color_option(value: str | tuple[float, ...] | list[float], label: str) -> tuple[float, float, float, float]:
    """Parse one color option for direct Python callers without argparse exits."""
    if isinstance(value, (tuple, list)):
        if len(value) == 3:
            red, green, blue = value
            alpha = 1.0
        elif len(value) == 4:
            red, green, blue, alpha = value
        else:
            raise ValueError(f"{label} must contain RGB or RGBA values")
        return (float(red), float(green), float(blue), float(alpha))
    lowered = str(value).strip().lower()
    if lowered in COLOR_NAMES:
        return COLOR_NAMES[lowered]
    if lowered.startswith("#"):
        hex_value = lowered[1:]
        if len(hex_value) == 6:
            try:
                return (
                    int(hex_value[0:2], 16) / 255.0,
                    int(hex_value[2:4], 16) / 255.0,
                    int(hex_value[4:6], 16) / 255.0,
                    1.0,
                )
            except ValueError as exc:
                raise ValueError(f"invalid {label}: {value}") from exc
    raise ValueError(f"invalid {label}: {value}")


def config_from_options(
    photodir: str | Path,
    *,
    inputlist: str | Path | None = None,
    trackdir: str | Path | None = None,
    start: int = 1,
    duration: float = 3.0,
    transition_duration_ms: int = TRANSITION_MS,
    transition: str | Transition = "TIME_LAPSE",
    background_color: str | tuple[float, ...] | list[float] = "black",
    dot_color: str | tuple[float, ...] | list[float] = DEFAULT_DOT_COLOR_NAME,
    dot_size: int = DEFAULT_DOT_SIZE,
    arrow_length: float = 1.0,
    font_color: str | tuple[float, ...] | list[float] = "white",
    font_size: int = 30,
    mapwindow: Optional[bool] = None,
    join_windows: bool = False,
    end_behavior: str = "loop_forever",
    repeat: Optional[bool] = None,
    photo_geometry: Optional[str] = None,
    map_geometry: Optional[str] = None,
    fullscreen: Optional[bool] = None,
    window_swap: bool = False,
    clock: bool = True,
    header_stage_name: bool = True,
    header_track_details: bool = True,
    header_place_name: bool = True,
    header_track_stats: bool = True,
    header_background: str = "black",
    header_shadow_color: str | tuple[float, ...] | list[float] = "black",
    elevation_profile: bool = True,
    debug: bool = False,
    keypressed: bool = False,
    collage_size_range: str = "33-66",
    collage_max_images: int = 9,
    time_lapse_stages: bool = False,
    time_lapse_duration: float = 30.0,
    time_lapse_media_min_fraction: float = 0.5,
    time_lapse_media_max_fraction: Optional[float] = None,
    time_lapse_marker: str = "pilgrim",
    time_lapse_overview_as_media: bool = True,
    time_lapse_overview_on_stage_map_dual: bool = True,
    track_map_before_media: bool = False,
    resume_index: Optional[int] = None,
    resume_progress: Optional[float] = None,
    resume_media_index: Optional[int] = None,
    resume_phase: Optional[str] = None,
    resume_audio_state: Optional[dict] = None,
    resume_control_state: Optional[dict] = None,
    state_file: str | Path | None = None,
    command_file: str | Path | None = None,
    music_source: str | Path | None = None,
    music_playlist: str | Path | None = None,
    audio_crossfade_seconds: float = 2.0,
    music_volume_percent: float = 65.0,
    video_volume_percent: float = 100.0,
    use_normalized_videos: bool = True,
    video_normalization_target_lufs: float = -16.0,
    video_normalization_max_boost_db: float = 12.0,
    video_normalization_true_peak_db: float = -1.5,
    gpx_overlay_mode: str = "line",
    media_overlay_mode: str = "dots",
    route_color: str | tuple[float, ...] | list[float] = "#0000FF",
    route_width: float = 4.0,
    endpoint_color: str | tuple[float, ...] | list[float] = "#FFFFFF",
    endpoint_size: float = 0.0,
    media_point_color: str | tuple[float, ...] | list[float] = "#0066FF",
    media_point_size: float = 12.0,
    dynamic_map_header: bool = True,
    map_header_color: str | tuple[float, ...] | list[float] = "#FFFFFF",
    map_header_font_factor: float = 2.2,
    track_title_mode: str = "endpoint_places",
    adventure_title: str = "",
    adventure_description: str = "",
    adventure_title_image: str | Path | None = None,
) -> Config:
    """Build a slideshow configuration for direct Python callers."""
    photo_dir_path = Path(photodir).expanduser().resolve()
    if not photo_dir_path.is_dir():
        raise ValueError(f"photodir is not a directory: {photo_dir_path}")

    input_list_path = Path(inputlist).expanduser().resolve() if inputlist else (photo_dir_path / DEFAULT_LIST_NAME)
    if not input_list_path.is_file():
        raise ValueError(f"input list file not found: {input_list_path}")
    track_dir_path = Path(trackdir).expanduser().resolve() if trackdir else photo_dir_path
    if not track_dir_path.is_dir():
        raise ValueError(f"trackdir is not a directory: {track_dir_path}")
    if start < 1:
        raise ValueError("start must be at least 1")
    if resume_index is not None and resume_index < 0:
        raise ValueError("resume_index must be at least 0")
    if resume_progress is not None and not 0.0 <= resume_progress <= 1.0:
        raise ValueError("resume_progress must be between 0 and 1")
    if resume_media_index is not None and resume_media_index < 0:
        raise ValueError("resume_media_index must be at least 0")
    if resume_phase is not None and resume_phase not in {phase.value for phase in PlaybackPhase}:
        raise ValueError(f"unsupported resume phase: {resume_phase}")
    if duration <= 0:
        raise ValueError("duration must be greater than 0")
    if transition_duration_ms < 0:
        raise ValueError("transition_duration_ms must be 0 or greater")
    if not 0.0 <= audio_crossfade_seconds <= 30.0:
        raise ValueError("audio_crossfade_seconds must be between 0 and 30")
    if not 0.0 <= music_volume_percent <= 100.0:
        raise ValueError("music_volume_percent must be between 0 and 100")
    if not 0.0 <= video_volume_percent <= 100.0:
        raise ValueError("video_volume_percent must be between 0 and 100")
    if video_normalization_max_boost_db < 0.0:
        raise ValueError("video_normalization_max_boost_db must be non-negative")
    if time_lapse_duration <= 0:
        raise ValueError("time_lapse_duration must be greater than 0")
    if time_lapse_media_max_fraction is not None:
        time_lapse_media_min_fraction = time_lapse_media_max_fraction
    if not 0 < time_lapse_media_min_fraction <= 1:
        raise ValueError("time_lapse_media_min_fraction must be greater than 0 and at most 1")
    if time_lapse_marker not in {"pilgrim", "bike", "car", "plane", "arrow"}:
        raise ValueError("time_lapse_marker must be pilgrim, bike, car, plane, or arrow")
    if dot_size < 1:
        raise ValueError("dot_size must be at least 1")
    if gpx_overlay_mode not in {"line", "hidden"}:
        raise ValueError("gpx_overlay_mode must be 'line' or 'hidden'")
    if media_overlay_mode not in {"dots", "interpolated", "hidden"}:
        raise ValueError("media_overlay_mode must be 'dots', 'interpolated', or 'hidden'")
    if route_width <= 0 or endpoint_size < 0 or media_point_size <= 0:
        raise ValueError("route_width and media_point_size must be positive and endpoint_size non-negative")
    if map_header_font_factor <= 0:
        raise ValueError("map_header_font_factor must be greater than 0")
    if header_background not in {"black", "transparent", "off"}:
        raise ValueError("header_background must be black, transparent, or off")
    if track_title_mode not in {"endpoint_places", "track_name"}:
        raise ValueError("track_title_mode must be endpoint_places or track_name")
    if arrow_length < 0:
        raise ValueError("arrow_length must be 0 or greater")
    if font_size < 8:
        raise ValueError("font_size must be at least 8")
    if collage_max_images < 1:
        raise ValueError("collage_max_images must be at least 1")

    transition_name = transition.value if isinstance(transition, Transition) else normalize_transition(transition)
    if transition_name not in PLAYBACK_STYLE_VALUES:
        raise ValueError(f"transition is not enabled: {transition_name}")
    initial_style = "TIME_LAPSE" if time_lapse_stages else transition_name
    resume_initial_style = None
    if isinstance(resume_control_state, dict) and resume_control_state.get("version") == 1:
        try:
            resume_initial_style = normalize_control_transition(
                resume_control_state.get("transition")
            )
        except ControlSyntaxError as exc:
            raise ValueError(f"invalid resume control transition: {exc}") from exc
    transition_value = (
        Transition.BLEND
        if initial_style == "TIME_LAPSE"
        else Transition(initial_style)
    )
    collage_size_min, collage_size_max = parse_percentage_range_option(collage_size_range)
    mapwindow_enabled = resolve_map_window(mapwindow, 1)
    fullscreen_enabled = False if fullscreen is None else bool(fullscreen)
    if join_windows and not mapwindow_enabled:
        raise ValueError("join_windows requires mapwindow")
    music_source_path = Path(music_source).expanduser().resolve() if music_source is not None else None
    if music_source_path is not None and not music_source_path.exists():
        raise ValueError(f"music source not found: {music_source_path}")
    if music_source_path is not None and music_source_path.is_file() and music_source_path.suffix.casefold() not in AUDIO_EXTENSIONS:
        raise ValueError(f"unsupported music file: {music_source_path}")
    music_playlist_path = Path(music_playlist).expanduser().resolve() if music_playlist is not None else None
    if music_playlist_path is not None and not music_playlist_path.is_file():
        raise ValueError(f"music playlist not found: {music_playlist_path}")

    normalized_end_behavior = str(end_behavior).strip().casefold()
    if repeat is not None:
        normalized_end_behavior = "loop_forever" if repeat else "black"
    if normalized_end_behavior not in {"black", "loop_once", "loop_forever"}:
        raise ValueError(f"unsupported end behavior: {end_behavior}")

    return Config(
        photodir=photo_dir_path,
        inputlist=input_list_path,
        start_track=int(start),
        duration=float(duration),
        transition_duration_ms=int(transition_duration_ms),
        transition=transition_value,
        initial_style=initial_style,
        background_color=parse_color_option(background_color, "background_color"),
        dot_color=parse_color_option(dot_color, "dot_color"),
        dot_size=int(dot_size),
        arrow_length=float(arrow_length),
        font_color=parse_color_option(font_color, "font_color"),
        font_size=int(font_size),
        mapwindow=mapwindow_enabled,
        join_windows=bool(join_windows),
        end_behavior=normalized_end_behavior,
        photo_geometry=photo_geometry,
        map_geometry=map_geometry,
        fullscreen=fullscreen_enabled,
        window_swap=bool(window_swap),
        clock=bool(clock),
        header_stage_name=bool(header_stage_name),
        header_track_details=bool(header_track_details),
        header_place_name=bool(header_place_name),
        header_track_stats=bool(header_track_stats),
        header_background=str(header_background),
        header_shadow_color=parse_color_option(header_shadow_color, "header_shadow_color"),
        elevation_profile=bool(elevation_profile),
        debug=bool(debug),
        keypressed=bool(keypressed),
        collage_size_min=collage_size_min,
        collage_size_max=collage_size_max,
        collage_max_images=int(collage_max_images),
        trackdir=track_dir_path,
        time_lapse_stages=(resume_initial_style or initial_style) == "TIME_LAPSE",
        time_lapse_duration=float(time_lapse_duration),
        time_lapse_media_min_fraction=float(time_lapse_media_min_fraction),
        time_lapse_marker=str(time_lapse_marker),
        time_lapse_overview_as_media=bool(time_lapse_overview_as_media),
        time_lapse_overview_on_stage_map_dual=bool(
            time_lapse_overview_on_stage_map_dual
        ),
        track_map_before_media=bool(track_map_before_media),
        resume_index=resume_index,
        resume_progress=resume_progress,
        resume_media_index=resume_media_index,
        resume_phase=resume_phase,
        resume_audio_state=(
            dict(resume_audio_state)
            if isinstance(resume_audio_state, dict)
            else None
        ),
        resume_control_state=(
            dict(resume_control_state)
            if isinstance(resume_control_state, dict)
            else None
        ),
        state_file=Path(state_file).expanduser().resolve() if state_file is not None else None,
        command_file=Path(command_file).expanduser().resolve() if command_file is not None else None,
        music_source=music_source_path,
        music_playlist=music_playlist_path,
        audio_crossfade_seconds=float(audio_crossfade_seconds),
        music_volume_percent=float(music_volume_percent),
        video_volume_percent=float(video_volume_percent),
        use_normalized_videos=bool(use_normalized_videos),
        video_normalization_target_lufs=float(video_normalization_target_lufs),
        video_normalization_max_boost_db=float(video_normalization_max_boost_db),
        video_normalization_true_peak_db=float(video_normalization_true_peak_db),
        gpx_overlay_mode=str(gpx_overlay_mode),
        media_overlay_mode=str(media_overlay_mode),
        route_color=parse_color_option(route_color, "route_color"),
        route_width=float(route_width),
        endpoint_color=parse_color_option(endpoint_color, "endpoint_color"),
        endpoint_size=float(endpoint_size),
        media_point_color=parse_color_option(media_point_color, "media_point_color"),
        media_point_size=float(media_point_size),
        dynamic_map_header=bool(dynamic_map_header),
        map_header_color=parse_color_option(map_header_color, "map_header_color"),
        map_header_font_factor=float(map_header_font_factor),
        track_title_mode=str(track_title_mode),
        adventure_title=str(adventure_title or "").strip(),
        adventure_description=str(adventure_description or "").strip(),
        adventure_title_image=(
            Path(adventure_title_image).expanduser().resolve()
            if adventure_title_image is not None
            else None
        ),
    )


def ns_color(color: tuple[float, float, float, float]):
    """Return an NSColor from normalized RGBA floats."""
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*color)


def configure_round_stroke(path) -> None:
    """Use the proven rounded route style that avoids sharp miter spikes."""
    path.setLineJoinStyle_(NSRoundLineJoinStyle)
    path.setLineCapStyle_(NSRoundLineCapStyle)


def simplify_display_path(
    points: list[tuple[float, float]],
    tolerance_pixels: float = 0.75,
) -> list[tuple[float, float]]:
    """Drop sub-pixel route vertices while preserving endpoints."""
    if len(points) <= 2 or tolerance_pixels <= 0.0:
        return points
    tolerance_squared = float(tolerance_pixels) ** 2
    simplified = [points[0]]
    last_x, last_y = points[0]
    for point in points[1:-1]:
        delta_x = point[0] - last_x
        delta_y = point[1] - last_y
        if delta_x * delta_x + delta_y * delta_y >= tolerance_squared:
            simplified.append(point)
            last_x, last_y = point
    if points[-1] != simplified[-1]:
        simplified.append(points[-1])
    return simplified


def background_cgcolor(color: tuple[float, float, float, float]):
    """Return a CGColor for CALayer backgrounds."""
    return ns_color(color).CGColor()


def resolve_path(base_dir: Path, filename: str) -> Path:
    """Resolve a file path relative to the photo directory when needed."""
    candidate = Path(filename).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base_dir / candidate).resolve()


def parse_coordinate_pair(text: str) -> tuple[Optional[float], Optional[float]]:
    """Parse a 'lat, lon' pair."""
    parts = [item.strip() for item in text.split(",")]
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def parse_photo_entry(line: str) -> PhotoListEntry:
    """Parse one standard photo list line."""
    parts = [part.strip() for part in str(line).split("|")]
    filename = parts[0]
    time_text = parts[1] if len(parts) > 1 and parts[1] else None
    latitude = None
    longitude = None
    if len(parts) > 2 and parts[2]:
        latitude, longitude = parse_coordinate_pair(parts[2])
    place = parts[3] if len(parts) > 3 and parts[3] else None
    return PhotoListEntry(filename, time_text, latitude, longitude, place)


def parse_iso_datetime(value: object) -> Optional[datetime]:
    """Parse a sidecar ISO timestamp without depending on local formatting."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_control_datetime(date_text: Optional[str], time_text: Optional[str], reference_time: Optional[datetime] = None) -> Optional[datetime]:
    """Combine a '#Datum:' value and media HH:MM value for time-lapse use."""
    clock = parse_clock_time(time_text)
    if clock is None or not isinstance(date_text, str):
        return None
    date_part = date_text.partition(",")[2].strip() if "," in date_text else date_text.strip()
    try:
        result = datetime.strptime(f"{date_part} {clock[0]:02d}:{clock[1]:02d}", "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    if reference_time is not None and reference_time.tzinfo is not None:
        return result.replace(tzinfo=reference_time.tzinfo)
    return result


def align_datetime_timezone(value: datetime, reference_time: datetime) -> datetime:
    """Make a media timestamp comparable to the timed GPX point sequence."""
    if reference_time.tzinfo is not None and value.tzinfo is None:
        return value.replace(tzinfo=reference_time.tzinfo)
    if reference_time.tzinfo is None and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def time_lapse_clock_datetime(
    marker_time: Optional[datetime],
    progress: float,
    media_time: Optional[datetime],
) -> Optional[datetime]:
    """Advance the clock to late media after the marker reaches track end."""
    if progress < 1.0 or media_time is None:
        return marker_time
    if marker_time is None:
        return media_time
    comparable_media_time = align_datetime_timezone(media_time, marker_time)
    return comparable_media_time if comparable_media_time > marker_time else marker_time


def time_lapse_clock_layout(
    image_rect: tuple[float, float, float, float],
    metadata: Optional[dict],
    has_date: bool,
) -> tuple[tuple[float, float, float, float], float]:
    """Fit the complete clock and date inside the map's top header when possible."""
    image_x, image_y, image_width, image_height = image_rect
    _header_x, header_bottom, _header_width, header_height = runtime_header_band(
        image_rect,
        metadata,
    )
    if header_height >= 24.0:
        stroke_width = max(1.5, header_height / 30.0)
        margin = stroke_width * 1.5 + 1.0
        available_height = max(1.0, header_height - 2.0 * margin)
        width_factor = 2.9 if has_date else 1.0
        available_width = max(1.0, image_width - 2.0 * margin)
        clock_size = max(
            4.0,
            min(available_height, available_width / width_factor),
        )
        date_width = clock_size * 1.9 if has_date else 0.0
        frame_y = header_bottom + max(margin, (header_height - clock_size) / 2.0)
        return (
            image_x + margin,
            frame_y,
            clock_size + date_width,
            clock_size,
        ), clock_size

    clock_size = max(40.0, image_height / 10.0)
    stroke_width = max(1.5, clock_size / 30.0)
    margin = stroke_width * 1.5 + 1.0
    date_width = clock_size * 1.9 if has_date else 0.0
    return (
        image_x + margin,
        image_y + image_height - margin - clock_size,
        clock_size + date_width,
        clock_size,
    ), clock_size


def runtime_header_band(
    image_rect: tuple[float, float, float, float],
    metadata: Optional[dict],
) -> tuple[float, float, float, float]:
    """Return the common header band for maps and full-window media."""
    image_x, image_y, image_width, image_height = image_rect
    axes = metadata.get("axes_box_fraction") if isinstance(metadata, dict) else None
    if isinstance(axes, dict):
        try:
            axes_top = float(axes["bottom"]) + float(axes["height"])
        except (KeyError, TypeError, ValueError):
            axes_top = 1.0
        reserved_height = image_height * max(0.0, 1.0 - axes_top)
        if reserved_height >= 24.0:
            return image_x, image_y + image_height - reserved_height, image_width, reserved_height
    try:
        fraction = float(metadata.get("runtime_header_fraction", 0.12)) if isinstance(metadata, dict) else 0.12
    except (TypeError, ValueError):
        fraction = 0.12
    header_height = image_height * max(0.08, min(0.20, fraction))
    return image_x, image_y + image_height - header_height, image_width, header_height


def header_content_rect(
    outer_rect: tuple[float, float, float, float],
    metadata: Optional[dict],
    background_style: str,
    visible: bool = True,
) -> tuple[float, float, float, float]:
    """Return the full frame, or the area below an opaque black header."""
    if not visible or str(background_style or "off").strip().casefold() != "black":
        return outer_rect
    outer_x, outer_y, outer_width, _outer_height = outer_rect
    _header_x, header_y, _header_width, _header_height = runtime_header_band(
        outer_rect,
        metadata,
    )
    return (
        outer_x,
        outer_y,
        outer_width,
        max(1.0, header_y - outer_y),
    )


def map_image_rect_and_scale(
    outer_rect: tuple[float, float, float, float],
    image_size: tuple[float, float],
    metadata: Optional[dict],
    background_style: str,
    visible: bool = True,
) -> tuple[tuple[float, float, float, float], tuple[float, float]]:
    """Fit a map for a header while retaining full width in black mode."""
    available = header_content_rect(
        outer_rect,
        metadata,
        background_style,
        visible,
    )
    available_x, available_y, available_width, available_height = available
    image_width, image_height = image_size
    if visible and str(background_style or "off").strip().casefold() == "black":
        return available, (
            available_width / max(image_width, 1.0),
            available_height / max(image_height, 1.0),
        )
    scale = min(
        available_width / max(image_width, 1.0),
        available_height / max(image_height, 1.0),
    )
    width, height = image_width * scale, image_height * scale
    return (
        available_x + (available_width - width) / 2.0,
        available_y + (available_height - height) / 2.0,
        width,
        height,
    ), (scale, scale)


def time_lapse_header_title_font_size(
    image_rect: tuple[float, float, float, float],
    metadata: Optional[dict],
    font_factor: float,
    row_count: int,
) -> float:
    """Return the stage-title font size for the displayed map header."""
    image_x, image_y, image_width, image_height = image_rect
    _header_x, _header_y, _header_width, header_height = runtime_header_band(
        (image_x, image_y, image_width, image_height),
        metadata,
    )
    display_scale = max(
        0.65,
        min(max(1.0, image_width) / 1920.0, max(1.0, image_height) / 1080.0),
    )
    requested = max(9.0, 14.0 * float(font_factor)) * display_scale
    padding = max(2.0, header_height * 0.055)
    usable_height = max(1.0, header_height - 2.0 * padding)
    return max(
        8.0,
        min(requested, usable_height / (max(1, int(row_count)) + 0.35)),
    )


def runtime_header_text_shadow_color(
    background_style: str,
    shadow_color: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Return the common title/clock shadow for the selected header layout."""
    alpha = 0.5 if str(background_style or "off").strip().casefold() == "off" else 0.0
    return (
        float(shadow_color[0]),
        float(shadow_color[1]),
        float(shadow_color[2]),
        alpha,
    )


def draw_runtime_header(
    image_rect: tuple[float, float, float, float],
    metadata: Optional[dict],
    header_lines: tuple[str, ...],
    metrics_lines: tuple[str, ...],
    *,
    font_color,
    font_factor: float,
    base_font_size: float,
    background_style: str,
    background_color: tuple[float, float, float, float],
    shadow_color: tuple[float, float, float, float],
) -> None:
    """Draw the shared clock/title/statistics header background and text."""
    header_x, header_y, header_width, header_height = runtime_header_band(image_rect, metadata)
    style = str(background_style or "off").strip().casefold()
    if style in {"black", "transparent"}:
        alpha = 0.48 if style == "transparent" else 1.0
        NSColor.colorWithSRGBRed_green_blue_alpha_(
            float(background_color[0]),
            float(background_color[1]),
            float(background_color[2]),
            alpha,
        ).setFill()
        NSBezierPath.fillRect_(NSMakeRect(header_x, header_y, header_width, header_height))
    text_shadow_color = runtime_header_text_shadow_color(
        style,
        shadow_color,
    )
    padding = max(2.0, header_height * 0.055)
    usable_height = max(1.0, header_height - 2.0 * padding)

    display_scale = max(
        0.65,
        min(max(1.0, header_width) / 1920.0, max(1.0, image_rect[3]) / 1080.0),
    )
    title_lines = tuple(str(line).strip() for line in header_lines[:3] if str(line).strip())
    if title_lines:
        # Always reserve the same three rows. Selected non-empty values are
        # packed from the top, so disabling one option never leaves a gap.
        row_height = usable_height / 3.0
        requested = max(9.0, 14.0 * float(font_factor)) * display_scale
        first_size = max(8.0, min(requested * 1.12, row_height * 0.88))
        title_top = header_y + header_height - padding
        for index, line in enumerate(title_lines):
            font_size = first_size if index == 0 else max(8.0, min(requested * 0.86, row_height * 0.78))
            font = NSFont.boldSystemFontOfSize_(font_size)
            text_height = NSString.stringWithString_(line).sizeWithAttributes_({NSFontAttributeName: font}).height
            baseline = title_top - (index + 1) * row_height + max(0.0, (row_height - text_height) / 2.0)
            draw_shadowed_text(
                line,
                header_x + header_width / 2.0,
                baseline,
                font,
                font_color,
                "center",
                text_shadow_color,
            )

    if metrics_lines:
        font_size = max(8.0, min(float(base_font_size) * 0.62 * display_scale, usable_height / 3.35))
        font = NSFont.boldSystemFontOfSize_(font_size)
        line_height = max(font_size + 1.0, usable_height / 3.0)
        right_x = header_x + header_width - max(5.0, header_width * 0.012)
        parsed = [tuple(part.strip() for part in str(line).partition(":")[::2]) for line in metrics_lines[:3]]
        values = [value for _label, value in parsed] or ["8000 m"]
        value_width = max(
            NSString.stringWithString_(value).sizeWithAttributes_({NSFontAttributeName: font}).width
            for value in values
        )
        colon_x = right_x - value_width - max(5.0, font_size * 0.45)
        top = header_y + header_height - padding
        for index, (label, value) in enumerate(parsed):
            baseline = top - (index + 1) * line_height
            draw_shadowed_text(f"{label}:", colon_x, baseline, font, font_color, "right", text_shadow_color)
            draw_shadowed_text(value, right_x, baseline, font, font_color, "right", text_shadow_color)


def create_runtime_header_overlay_image(
    width: float,
    height: float,
    metadata: Optional[dict],
    header_lines: tuple[str, ...],
    metrics_lines: tuple[str, ...],
    font_color,
    font_factor: float,
    base_font_size: float,
    background_style: str,
    background_color: tuple[float, float, float, float],
    shadow_color: tuple[float, float, float, float],
    reference_rect: Optional[tuple[float, float, float, float]] = None,
):
    """Create the header layer used over full-window photos and videos."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    draw_runtime_header(
        reference_rect or (0.0, 0.0, width, height),
        metadata,
        header_lines,
        metrics_lines,
        font_color=font_color,
        font_factor=font_factor,
        base_font_size=base_font_size,
        background_style=background_style,
        background_color=background_color,
        shadow_color=shadow_color,
    )
    image.unlockFocus()
    return image


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write a small JSON state file atomically so the GUI never reads a partial update."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def timed_points_from_metadata(metadata: Optional[dict]) -> list[dict]:
    """Load sidecar timing, falling back safely to distance-based estimates."""
    payload = metadata.get("timed_track_points") if isinstance(metadata, dict) else None
    points = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            point_time = parse_iso_datetime(item.get("time_iso"))
            lat, lon = safe_float(item.get("lat")), safe_float(item.get("lon"))
            if point_time is not None and lat is not None and lon is not None:
                points.append(
                    {
                        "lat": lat,
                        "lon": lon,
                        "time": point_time,
                        "estimated": bool(item.get("estimated")),
                        "elevation_m": safe_float(item.get("elevation_m")),
                        "cumulative_distance_km": safe_float(item.get("cumulative_distance_km")),
                    }
                )
    if len(points) >= 2:
        return timeline_points_with_distances(points)
    raw_points = metadata_track_points(metadata)
    repaired = timed_points_payload([{"lat": lat, "lon": lon} for lat, lon in raw_points])
    return timeline_points_with_distances([
        {"lat": item["lat"], "lon": item["lon"], "time": parse_iso_datetime(item["time_iso"]), "estimated": True}
        for item in repaired
    ])


def timeline_points_with_distances(points: list[dict]) -> list[dict]:
    """Ensure every timeline point has a monotonic cumulative route distance."""
    running_distance = 0.0
    previous = None
    normalized = []
    for item in points:
        point = dict(item)
        if previous is not None:
            running_distance += haversine_km(previous["lat"], previous["lon"], point["lat"], point["lon"])
        stored_distance = safe_float(point.get("cumulative_distance_km"))
        if stored_distance is None or stored_distance < 0.0 or (normalized and stored_distance < normalized[-1]["cumulative_distance_km"]):
            stored_distance = running_distance
        point["cumulative_distance_km"] = stored_distance
        normalized.append(point)
        previous = point
    return normalized


def timeline_sample_count(duration_seconds: float) -> int:
    """Return the approximately 50 Hz sample count for one stage."""
    return max(1, math.ceil(float(duration_seconds) / 0.020))


def build_time_lapse_media_queue(events: list[tuple[Optional[float], int, PhotoListEntry]]) -> list[tuple[float, int, PhotoListEntry]]:
    """Order timed media first and retain every untimed row at stage end."""
    timed = [(fraction, row_index, entry) for fraction, row_index, entry in events if fraction is not None]
    untimed = [(1.0, row_index, entry) for fraction, row_index, entry in events if fraction is None]
    timed.sort(key=lambda item: (item[0], item[1]))
    return [*timed, *untimed]


def advance_time_lapse_progress(
    progress: float,
    elapsed_seconds: float,
    stage_duration: float,
    next_media_fraction: Optional[float],
    media_blocks_next_event: bool,
) -> tuple[float, bool, bool]:
    """Advance motion, stopping only for media still inside its minimum time."""
    target = min(1.0, progress + max(0.0, elapsed_seconds) / stage_duration)
    if next_media_fraction is not None and target >= next_media_fraction:
        event_progress = max(progress, min(1.0, next_media_fraction))
        return event_progress, not media_blocks_next_event, media_blocks_next_event
    return target, False, False


def time_lapse_media_minimum_pending(
    media_active: bool,
    deadline: Optional[float],
    now: float,
) -> bool:
    """Return whether current media must delay replacement by the next item."""
    return bool(media_active and (deadline is None or now < deadline))


def interpolate_timeline_state(points: list[dict], fraction: float) -> Optional[dict]:
    """Interpolate position, time, distance, and height along one stage."""
    if not points:
        return None
    if len(points) == 1:
        point = points[0]
        return {
            "lat": point["lat"],
            "lon": point["lon"],
            "time": point.get("time"),
            "stage_distance_km": safe_float(point.get("cumulative_distance_km")) or 0.0,
            "elevation_m": safe_float(point.get("elevation_m")),
        }
    fraction = max(0.0, min(1.0, fraction))
    first_time, last_time = points[0].get("time"), points[-1].get("time")
    target_time = None
    if not isinstance(first_time, datetime) or not isinstance(last_time, datetime) or any(
        not isinstance(point.get("time"), datetime) for point in points
    ):
        position = fraction * (len(points) - 1)
        lower = min(int(position), len(points) - 2)
        local = position - lower
    else:
        total = (last_time - first_time).total_seconds()
        if total <= 0:
            position = fraction * (len(points) - 1)
            lower = min(int(position), len(points) - 2)
            local = position - lower
        else:
            target_time = first_time + timedelta(seconds=total * fraction)
            lower = next(
                (index for index in range(len(points) - 1) if points[index + 1]["time"] >= target_time),
                len(points) - 2,
            )
            segment_seconds = (points[lower + 1]["time"] - points[lower]["time"]).total_seconds()
            local = 0.0 if segment_seconds <= 0 else (target_time - points[lower]["time"]).total_seconds() / segment_seconds
    start, end = points[lower], points[lower + 1]
    start_distance = safe_float(start.get("cumulative_distance_km")) or 0.0
    end_distance = safe_float(end.get("cumulative_distance_km"))
    if end_distance is None:
        end_distance = start_distance + haversine_km(start["lat"], start["lon"], end["lat"], end["lon"])
    start_elevation = safe_float(start.get("elevation_m"))
    end_elevation = safe_float(end.get("elevation_m"))
    if start_elevation is None:
        elevation_m = end_elevation
    elif end_elevation is None:
        elevation_m = start_elevation
    else:
        elevation_m = start_elevation + (end_elevation - start_elevation) * local
    return {
        "lat": start["lat"] + (end["lat"] - start["lat"]) * local,
        "lon": start["lon"] + (end["lon"] - start["lon"]) * local,
        "time": target_time,
        "stage_distance_km": start_distance + (end_distance - start_distance) * local,
        "elevation_m": elevation_m,
    }


def interpolate_timeline_point(points: list[dict], fraction: float) -> Optional[tuple[float, float]]:
    """Compatibility wrapper returning only the interpolated coordinates."""
    state = interpolate_timeline_state(points, fraction)
    return None if state is None else (state["lat"], state["lon"])


def format_time_lapse_metrics(total_km: float, stage_km: float, elevation_m: Optional[float]) -> tuple[str, str, str]:
    """Format the three compact lines shown in a track-map header."""
    elevation_text = "--" if elevation_m is None else f"{elevation_m:.0f}"
    stage_text = f"{max(0.0, stage_km):.1f}".replace(".", ",")
    return (
        f"Total traveled: {max(0.0, total_km):.0f} km",
        f"Stage traveled: {stage_text} km",
        f"Height: {elevation_text} m",
    )


def photo_track_metrics(
    metadata: Optional[dict],
    latitude: Optional[float],
    longitude: Optional[float],
    distance_before_stage_km: float,
    media_datetime: Optional[datetime] = None,
) -> tuple[str, ...]:
    """Return Time-Lapse-equivalent statistics at a photograph position."""
    if not isinstance(metadata, dict):
        return ()
    points = metadata.get("timed_track_points")
    candidates: list[dict] = []
    for point in points if isinstance(points, list) else ():
        if not isinstance(point, dict):
            continue
        point_lat = safe_float(point.get("lat", point.get("latitude")))
        point_lon = safe_float(point.get("lon", point.get("longitude")))
        stage_km = safe_float(point.get("cumulative_distance_km"))
        if point_lat is None or point_lon is None or stage_km is None:
            continue
        candidates.append({
            "lat": point_lat,
            "lon": point_lon,
            "stage_km": max(0.0, stage_km),
            "elevation_m": safe_float(point.get("elevation_m", point.get("elevation"))),
            "time": parse_iso_datetime(point.get("time_iso", point.get("time"))),
        })
    selected = None
    if candidates and isinstance(media_datetime, datetime):
        timed = [candidate for candidate in candidates if isinstance(candidate["time"], datetime)]
        if timed:
            selected = min(
                timed,
                key=lambda candidate: abs(
                    (
                        align_datetime_timezone(media_datetime, candidate["time"])
                        - candidate["time"]
                    ).total_seconds()
                ),
            )
    if selected is None and candidates and latitude is not None and longitude is not None:
        selected = min(
            candidates,
            key=lambda candidate: haversine_km(
                float(latitude),
                float(longitude),
                candidate["lat"],
                candidate["lon"],
            ),
        )
    if selected is None and candidates:
        selected = candidates[-1]
    if selected is None:
        stage_km = track_length_from_metadata(metadata)
        if stage_km <= 0.0:
            return ()
        elevation_m = None
    else:
        stage_km = selected["stage_km"]
        elevation_m = selected["elevation_m"]
    return format_time_lapse_metrics(
        max(0.0, float(distance_before_stage_km)) + stage_km,
        stage_km,
        elevation_m,
    )


def track_length_from_metadata(metadata: Optional[dict]) -> float:
    """Return a stage length from modern or legacy plot metadata."""
    if not isinstance(metadata, dict):
        return 0.0
    stored_length = safe_float(metadata.get("track_length_km"))
    if stored_length is not None and stored_length >= 0.0:
        return stored_length
    timed_points = metadata.get("timed_track_points")
    if isinstance(timed_points, list) and timed_points:
        last_distance = safe_float(timed_points[-1].get("cumulative_distance_km")) if isinstance(timed_points[-1], dict) else None
        if last_distance is not None and last_distance >= 0.0:
            return last_distance
    points = metadata_track_points(metadata)
    return sum(
        haversine_km(start[0], start[1], end[0], end[1])
        for start, end in zip(points, points[1:])
    )


def normalized_track_duration(value: object) -> Optional[str]:
    """Return a zero-padded HH:MM duration stored in modern or legacy metadata."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+):(\d{1,2})(?::\d{1,2}(?:\.\d+)?)?", text)
    if match is None:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    if minutes >= 60:
        return None
    return f"{hours:02d}:{minutes:02d}"


def track_endpoint_place(metadata: object, endpoint: str) -> Optional[str]:
    """Return one concise stored start/end place from a Track Map sidecar."""
    if not isinstance(metadata, dict):
        return None
    places = metadata.get("track_endpoint_places")
    if not isinstance(places, dict):
        return None
    value = places.get(endpoint)
    if not isinstance(value, dict):
        return None
    details = value.get("place_details")
    if isinstance(details, dict):
        locality = str(details.get("locality") or "").strip()
        if locality:
            return locality
        sublocality = str(details.get("subLocality") or "").strip()
        if sublocality:
            return sublocality
        areas = details.get("areasOfInterest")
        if isinstance(areas, list):
            for area in areas:
                area_text = str(area or "").strip()
                if area_text:
                    return area_text
        elif areas:
            area_text = str(areas).strip()
            if area_text:
                return area_text
        name = str(details.get("name") or "").strip()
        if name:
            return name
    place = str(value.get("place") or "").strip()
    if not place:
        return None
    concise = place.splitlines()[0].split(",", 1)[0].strip()
    concise = re.sub(r"\s+\([^)]*\)\s*$", "", concise).strip()
    return concise or place


def track_display_title(metadata: object, title_mode: str = "endpoint_places") -> str:
    """Choose endpoint places by default and fall back to the GPX track name."""
    payload = metadata if isinstance(metadata, dict) else {}
    track_name = str(payload.get("track_name") or "").strip()
    if str(title_mode).strip().casefold() != "track_name":
        start_place = track_endpoint_place(payload, "start")
        end_place = track_endpoint_place(payload, "end")
        if start_place and end_place:
            return start_place if start_place == end_place else f"{start_place} - {end_place}"
        if start_place or end_place:
            return start_place or end_place or track_name
    return track_name


def track_header_lines(
    metadata: object,
    title_mode: str = "endpoint_places",
    *,
    omit_date: bool = False,
    details_override: Optional[str] = None,
) -> tuple[str, ...]:
    """Build the dynamic GPX-stage title, optional date, and length/duration."""
    payload = metadata if isinstance(metadata, dict) else {}
    lines: list[str] = []
    title = track_display_title(payload, title_mode)
    if title:
        lines.append(title)
    date_text = str(payload.get("track_date") or "").strip()
    length = safe_float(payload.get("track_length_km"))
    duration = normalized_track_duration(payload.get("track_duration"))
    details = str(details_override or "").strip()
    if not details:
        if length is not None and duration is not None:
            details = f"{max(0.0, length):.1f} km - {duration} h"
        elif length is not None:
            details = f"{max(0.0, length):.1f} km"
        elif duration is not None:
            details = f"{duration} h"
    if date_text and not omit_date:
        lines.append(f"{date_text} · {details}" if details else date_text)
    elif details:
        lines.append(details)
    return tuple(lines)


def dynamic_stage_header_lines(
    metadata: object,
    config: Config,
    *,
    relation_title: Optional[str] = None,
    clock_visible: bool = False,
) -> tuple[str, ...]:
    """Build the runtime header for GPX, adjacent-day, and media-only stages."""
    payload = metadata if isinstance(metadata, dict) else {}
    scene = scene_from_metadata(payload, show_header=True)
    if scene.stage_kind == "gpx_track":
        return track_header_lines(
            payload,
            getattr(config, "track_title_mode", "endpoint_places"),
            omit_date=clock_visible,
            details_override=relation_title,
        )
    if scene.stage_kind == "media_stage" and clock_visible:
        stage_title = str(
            payload.get("media_stage_name")
            or payload.get("track_name")
            or ""
        ).strip()
        return (stage_title,) if stage_title else ()
    return scene.header_lines


def selected_stage_header_lines(
    metadata: object,
    config: Config,
    *,
    place_text: Optional[str] = None,
    relation_title: Optional[str] = None,
) -> tuple[str, ...]:
    """Return the selected title fields, compacted into at most three rows."""
    payload = metadata if isinstance(metadata, dict) else {}
    scene = scene_from_metadata(payload, show_header=True)
    if scene.stage_kind == "gpx_track":
        available = track_header_lines(
            payload,
            getattr(config, "track_title_mode", "endpoint_places"),
            omit_date=True,
            details_override=relation_title,
        )
        stage_name = available[0] if available else ""
        track_details = available[1] if len(available) > 1 else ""
    else:
        stage_name = str(
            payload.get("media_stage_name")
            or payload.get("track_name")
            or (scene.header_lines[0] if scene.header_lines else "")
        ).strip()
        track_details = str(relation_title or "").strip()
    concise_place = " · ".join(
        part.strip() for part in str(place_text or "").splitlines() if part.strip()
    )
    selected = (
        stage_name if getattr(config, "header_stage_name", True) else "",
        track_details if getattr(config, "header_track_details", True) else "",
        concise_place if getattr(config, "header_place_name", True) else "",
    )
    return tuple(value for value in selected if value)[:3]


def track_metadata_supports_clock(metadata: object) -> bool:
    """Return whether a GPX or media stage can display a meaningful clock."""
    if not isinstance(metadata, dict):
        return False
    timed_points = metadata.get("timed_track_points")
    if isinstance(timed_points, list) and any(
        isinstance(point, dict)
        and str(point.get("time_iso") or point.get("time") or "").strip()
        for point in timed_points
    ):
        return True
    media_points = metadata.get("media_points")
    return isinstance(media_points, list) and any(
        isinstance(point, dict)
        and str(point.get("time_iso") or point.get("time") or "").strip()
        for point in media_points
    )


if APPKIT_AVAILABLE:
    _PILGRIM_FRAME_IMAGES = None

    def load_pilgrim_frame_images():
        """Load the standing frame and eight walking frames once per process."""
        global _PILGRIM_FRAME_IMAGES
        if _PILGRIM_FRAME_IMAGES is not None:
            return _PILGRIM_FRAME_IMAGES
        frames = []
        for index in range(9):
            path = bundled_resource_path(f"pilgrim-frame{index:02d}-rigged-512.png")
            image = NSImage.alloc().initWithContentsOfFile_(str(path)) if path.exists() else None
            if image is None:
                frames = []
                break
            frames.append(image)
        _PILGRIM_FRAME_IMAGES = tuple(frames)
        return _PILGRIM_FRAME_IMAGES


    class TimeLapseMapView(NSView):
        """Draw retained maps plus a changing arrow/media layer without image churn."""

        def initWithFrame_(self, frame):
            self = objc.super(TimeLapseMapView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.map_image = None
            self.map_metadata = None
            self.route_points = []
            self.arrow_latlon = None
            self.media_marker_latlon = None
            self.media_marker_fixed_arrow = False
            self.media_image = None
            self.media_draw_frame = True
            self.highlight_route = False
            self.route_visible = True
            self.route_color = COLOR_NAMES["blue"]
            self.route_width = 4.0
            self.marker_color = COLOR_NAMES[DEFAULT_DOT_COLOR_NAME]
            self.marker_radius = DEFAULT_DOT_SIZE
            self.arrow_factor = 1.0
            self.marker_style = "pilgrim"
            self.pilgrim_frames = load_pilgrim_frame_images()
            self.pilgrim_walk_state = PilgrimWalkState()
            self.stage_tangent = None
            self.pilgrim_rotation_degrees = 0.0
            self.pilgrim_mirrored = False
            self.media_min_fraction = 0.5
            self.media_placement_cache_key = None
            self.media_clear_rects = None
            self.current_media_corner = None
            self.media_video_view = None
            self.place_text = None
            self.metrics_lines = ()
            self.metrics_total_distance_km = 0.0
            self.relation_title = None
            self.header_lines = ()
            self.header_background_style = "black"
            self.unified_header_enabled = True
            self.overlay_font_size = 30.0
            self.overlay_font_color = COLOR_NAMES["white"]
            self.overlay_background_color = COLOR_NAMES["black"]
            self.overlay_shadow_color = COLOR_NAMES["black"]
            self.map_header_font_factor = 2.2
            self.header_row_count = 3
            self.clock_overlay_key = None
            self.clock_overlay_image = None
            self.clock_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 1.0, 1.0))
            self.clock_view.setImageAlignment_(NSImageAlignCenter)
            self.clock_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            self.clock_view.setAlphaValue_(0.0)
            self.addSubview_(self.clock_view)
            return self

        def configureWithImage_metadata_routePoints_arrowLatLon_mediaImage_highlightRoute_(self, image, metadata, route_points, arrow_latlon, media_image, highlight_route):
            stage_changed = image is not self.map_image or metadata is not self.map_metadata or route_points is not self.route_points
            self.map_image = image
            self.map_metadata = metadata
            self.route_points = route_points or []
            self.arrow_latlon = arrow_latlon
            self.media_image = media_image
            self.highlight_route = highlight_route
            if stage_changed:
                self.media_placement_cache_key = None
                self.media_clear_rects = None
                self.current_media_corner = None
                self.pilgrim_walk_state.reset()
                self.stage_tangent = self.fixedStageTangent()
                self.pilgrim_rotation_degrees, self.pilgrim_mirrored = pilgrim_orientation_for_tangent(
                    self.stage_tangent
                )
            self.setNeedsDisplay_(True)

        def _update_clock_overlay(self, clock_time, date_text, enabled):
            """Update the retained clock overlay only when its content or size changes."""
            if not enabled or clock_time is None:
                if self.clock_overlay_key is None and self.clock_overlay_image is None:
                    return
                self.clock_view.setAlphaValue_(0.0)
                self.clock_view.setImage_(None)
                self.clock_overlay_key = None
                self.clock_overlay_image = None
                return
            bounds = self.bounds()
            image_rect = self.headerReferenceRect()
            frame, clock_size = time_lapse_clock_layout(
                (
                    float(image_rect.origin.x),
                    float(image_rect.origin.y),
                    float(image_rect.size.width),
                    float(image_rect.size.height),
                ),
                self.map_metadata,
                bool(date_text),
            )
            date_font_size = time_lapse_header_title_font_size(
                (
                    float(image_rect.origin.x),
                    float(image_rect.origin.y),
                    float(image_rect.size.width),
                    float(image_rect.size.height),
                ),
                self.map_metadata,
                self.map_header_font_factor,
                self.header_row_count,
            )
            key = (
                int(clock_time[0]),
                int(clock_time[1]),
                str(date_text or ""),
                round(clock_size, 2),
                round(date_font_size, 2),
                tuple(round(float(value), 4) for value in self.overlay_font_color),
                tuple(round(float(value), 4) for value in self.overlay_shadow_color),
                str(self.header_background_style),
            )
            if key != self.clock_overlay_key:
                self.clock_overlay_image = create_clock_overlay_image(
                    clock_size,
                    int(clock_time[0]),
                    int(clock_time[1]),
                    date_text,
                    date_on_right=True,
                    date_font_size=date_font_size,
                    font_color=self.overlay_font_color,
                    shadow_color=self.overlay_shadow_color,
                    background_style=self.header_background_style,
                )
                self.clock_view.setImage_(self.clock_overlay_image)
                self.clock_overlay_key = key
            self.clock_view.setFrame_(
                NSMakeRect(
                    frame[0],
                    frame[1],
                    frame[2],
                    frame[3],
                )
            )
            self.clock_view.setAlphaValue_(1.0)

        def _raise_clock_overlay(self):
            """Keep the clock above an optional AVPlayer child view."""
            self.clock_view.removeFromSuperview()
            self.addSubview_(self.clock_view)

        def _retire_content(self):
            """Drop heavyweight content while a closed Cocoa window stays retained."""
            self._update_clock_overlay(None, None, False)
            self.map_image = None
            self.map_metadata = None
            self.route_points = []
            self.arrow_latlon = None
            self.media_image = None
            self.media_draw_frame = True
            self.media_marker_latlon = None
            self.media_marker_fixed_arrow = False
            self.place_text = None
            self.metrics_lines = ()
            self.relation_title = None
            self.header_lines = ()
            self.media_clear_rects = None
            self.media_placement_cache_key = None
            self.pilgrim_frames = []
            self.setHidden_(True)

        def imageRectAndScale(self):
            bounds = self.bounds()
            if self.map_image is None:
                return bounds, (1.0, 1.0)
            image_w, image_h = image_size_tuple(self.map_image)
            image_tuple, scale = map_image_rect_and_scale(
                (0.0, 0.0, float(bounds.size.width), float(bounds.size.height)),
                (image_w, image_h),
                self.map_metadata,
                self.header_background_style,
                True,
            )
            return NSMakeRect(*image_tuple), scale

        def headerReferenceRect(self):
            """Align overlays to the image top unless a separate band is used."""
            if str(self.header_background_style).strip().casefold() == "black":
                return self.bounds()
            image_rect, _scale = self.imageRectAndScale()
            return image_rect

        @staticmethod
        def _scale_components(scale):
            if isinstance(scale, (tuple, list)) and len(scale) == 2:
                return float(scale[0]), float(scale[1])
            value = float(scale)
            return value, value

        def imagePixelForLat_lon_(self, lat, lon):
            if self.map_metadata is None:
                return None
            try:
                x, y = coordinate_to_pixel(lat, lon, self.map_metadata)
                return scale_metadata_pixel_to_image(x, y, self.map_metadata, self.map_image)
            except Exception:
                return None

        def viewPointForLat_lon_imageRect_scale_(self, lat, lon, image_rect, scale):
            pixel = self.imagePixelForLat_lon_(lat, lon)
            if pixel is None:
                return None
            _image_w, image_h = image_size_tuple(self.map_image)
            scale_x, scale_y = self._scale_components(scale)
            return (
                image_rect.origin.x + pixel[0] * scale_x,
                image_rect.origin.y + (image_h - pixel[1]) * scale_y,
            )

        def fixedStageTangent(self):
            """Keep the arrow perpendicular to the stage start/end line."""
            if len(self.route_points) < 2:
                return None
            endpoints = [
                self.imagePixelForLat_lon_(self.route_points[0]["lat"], self.route_points[0]["lon"]),
                self.imagePixelForLat_lon_(self.route_points[-1]["lat"], self.route_points[-1]["lon"]),
            ]
            if any(point is None for point in endpoints):
                return None
            return endpoint_tangent(endpoints)

        def drawMarkerForLat_lon_imageRect_scale_(self, lat, lon, image_rect, scale):
            pixel = self.imagePixelForLat_lon_(lat, lon)
            if pixel is None:
                return None
            _image_w, image_h = image_size_tuple(self.map_image)
            scale_x, scale_y = self._scale_components(scale)
            view_point = (
                image_rect.origin.x + pixel[0] * scale_x,
                image_rect.origin.y + (image_h - pixel[1]) * scale_y,
            )
            view_height = float(self.bounds().size.height)
            view_pixel_y = view_height - view_point[1]
            tangent = self.stage_tangent
            display_tangent = None
            if tangent is not None:
                tangent_x = float(tangent[0]) * scale_x
                tangent_y = float(tangent[1]) * scale_y
                tangent_length = math.hypot(tangent_x, tangent_y)
                if tangent_length > 0.0:
                    display_tangent = (
                        tangent_x / tangent_length,
                        tangent_y / tangent_length,
                    )
            use_pilgrim = self.marker_style == "pilgrim" and len(self.pilgrim_frames) == 9
            if use_pilgrim:
                frame_index = self.pilgrim_walk_state.update(
                    view_point,
                    time.monotonic(),
                    pilgrim_motion_threshold(self.bounds().size.width, self.bounds().size.height),
                )
                if self.arrow_factor > 0:
                    rotation, mirrored = pilgrim_orientation_for_tangent(display_tangent)
                    draw_pilgrim_at_marker(
                        view_point[0],
                        view_pixel_y,
                        view_height,
                        self.pilgrim_frames[frame_index],
                        self.marker_radius,
                        self.arrow_factor,
                        rotation,
                        mirrored,
                    )
                draw_marker_at(view_point[0], view_pixel_y, view_height, self.marker_color, self.marker_radius)
            else:
                if self.marker_style in {"bike", "car", "plane"}:
                    draw_transport_marker_at(
                        view_point[0],
                        view_pixel_y,
                        view_height,
                        self.marker_style,
                        display_tangent,
                        self.marker_color,
                        self.marker_radius,
                        self.arrow_factor,
                    )
                elif display_tangent is not None and self.arrow_factor > 0:
                    draw_open_arrow_at_marker(
                        view_point[0], view_pixel_y, view_height, display_tangent, self.marker_color, self.marker_radius, self.arrow_factor
                    )
                draw_marker_at(view_point[0], view_pixel_y, view_height, self.marker_color, self.marker_radius)
            return view_point

        def mediaPlacementGeometry(self):
            """Return cached corner envelopes within the actual map plotting area."""
            bounds = self.bounds()
            image_rect, scale = self.imageRectAndScale()
            image_tuple = (
                float(image_rect.origin.x),
                float(image_rect.origin.y),
                float(image_rect.size.width),
                float(image_rect.size.height),
            )
            plot_rect = map_plot_rect(image_tuple, self.map_metadata)
            cache_metadata = self.map_metadata.get("media_clear_boxes") if isinstance(self.map_metadata, dict) else None
            try:
                margin_fraction = float(cache_metadata.get("margin_fraction", 0.05))
            except (AttributeError, TypeError, ValueError):
                margin_fraction = 0.05
            margin_fraction = max(0.0, min(0.49, margin_fraction))
            placement_rect = inset_rect(plot_rect, margin_fraction)
            cache_key = (
                round(bounds.size.width, 3),
                round(bounds.size.height, 3),
                tuple(round(value, 3) for value in placement_rect),
                round(float(self.media_min_fraction), 6),
            )
            if self.media_placement_cache_key != cache_key or self.media_clear_rects is None:
                image_size = image_size_tuple(self.map_image) if self.map_image is not None else None
                self.media_clear_rects = cached_clear_box_options(self.map_metadata, image_tuple, image_size)
                if self.media_clear_rects is None:
                    obstacles = placement_obstacle_points(self.map_metadata, self.route_points)
                    projected_route = [
                        self.viewPointForLat_lon_imageRect_scale_(point.latitude, point.longitude, image_rect, scale)
                        for point in obstacles
                    ]
                    projected_route = [point for point in projected_route if point is not None]
                    self.media_clear_rects = clear_corner_rect_options(
                        placement_rect,
                        projected_route,
                        connect_points=(
                            scene_from_metadata(
                                self.map_metadata,
                                show_header=False,
                            ).stage_kind
                            != "media_stage"
                        ),
                    )
                self.media_placement_cache_key = cache_key
            return self.media_clear_rects

        def mediaRectsForImage_(self, image):
            """Return outer frame and media content rectangles for the cached corner."""
            clear_rects = self.mediaPlacementGeometry()
            if image is None:
                rect_tuple = clear_rects["top_right"][0]
                rect = NSMakeRect(*rect_tuple)
                return rect, rect
            media_width, media_height = image_size_tuple(image)
            if not self.media_draw_frame:
                corner, content = best_unframed_media_layout(
                    clear_rects,
                    (media_width, media_height),
                )
                self.current_media_corner = corner
                content_rect = NSMakeRect(*content)
                return content_rect, content_rect
            corner, outer, content = best_media_corner_layout(
                clear_rects,
                (float(self.bounds().size.width), float(self.bounds().size.height)),
                float(self.media_min_fraction),
                (media_width, media_height),
            )
            self.current_media_corner = corner
            outer_rect = NSMakeRect(*outer)
            content_rect = NSMakeRect(*content)
            return outer_rect, content_rect

        def mediaRectForArrowPoint_(self, _arrow_point):
            """Compatibility wrapper returning the actual media content rectangle."""
            _outer_rect, content_rect = self.mediaRectsForImage_(self.media_image)
            return content_rect

        def drawTextOverlays(self):
            """Draw stage metrics in the header and one place line at the bottom."""
            image_rect, _scale = self.imageRectAndScale()
            image_width = float(image_rect.size.width)
            image_height = float(image_rect.size.height)
            if image_width <= 0.0 or image_height <= 0.0:
                return

            if self.unified_header_enabled:
                header_rect = self.headerReferenceRect()
                draw_runtime_header(
                    (
                        float(header_rect.origin.x),
                        float(header_rect.origin.y),
                        float(header_rect.size.width),
                        float(header_rect.size.height),
                    ),
                    self.map_metadata,
                    tuple(self.header_lines),
                    tuple(self.metrics_lines),
                    font_color=self.overlay_font_color,
                    font_factor=self.map_header_font_factor,
                    base_font_size=self.overlay_font_size,
                    background_style=self.header_background_style,
                    background_color=self.overlay_background_color,
                    shadow_color=self.overlay_shadow_color,
                )

            if self.relation_title and not self.unified_header_enabled:
                title_x, title_y, title_width, title_height = relation_title_band(
                    (
                        float(image_rect.origin.x),
                        float(image_rect.origin.y),
                        image_width,
                        image_height,
                    ),
                    self.map_metadata,
                )
                title_font_size = max(
                    6.0,
                    min(float(self.overlay_font_size) * 1.05, title_height * 0.62),
                )
                title_font = NSFont.boldSystemFontOfSize_(title_font_size)
                title_size = NSString.stringWithString_(self.relation_title).sizeWithAttributes_(
                    {NSFontAttributeName: title_font}
                )
                draw_shadowed_text(
                    self.relation_title,
                    title_x + title_width / 2.0,
                    title_y + max(0.0, (title_height - title_size.height) / 2.0),
                    title_font,
                    self.overlay_font_color,
                    "center",
                )

            if self.metrics_lines and not self.unified_header_enabled:
                axes = self.map_metadata.get("axes_box_fraction") if isinstance(self.map_metadata, dict) else None
                if isinstance(axes, dict):
                    try:
                        axes_top = float(axes["bottom"]) + float(axes["height"])
                    except (KeyError, TypeError, ValueError):
                        axes_top = 0.88
                else:
                    axes_top = 0.88
                axes_top = max(0.0, min(0.96, axes_top))
                header_bottom = float(image_rect.origin.y) + image_height * axes_top
                header_top = float(image_rect.origin.y + image_rect.size.height)
                header_height = max(1.0, header_top - header_bottom)
                padding = max(2.0, header_height * 0.04)
                font_size = max(8.0, min(float(self.overlay_font_size) * 0.62, (header_height - 2.0 * padding) / 3.35))
                font = NSFont.boldSystemFontOfSize_(font_size)
                line_height = max(font_size + 1.0, (header_height - 2.0 * padding) / 3.0)
                right_x = float(image_rect.origin.x + image_rect.size.width) - max(5.0, image_width * 0.012)
                parsed_lines = [
                    tuple(part.strip() for part in str(line).partition(":")[::2])
                    for line in self.metrics_lines[:3]
                ]
                reserve_values = [
                    f"{max(0.0, float(self.metrics_total_distance_km)):.0f} km",
                    f"{max(0.0, track_length_from_metadata(self.map_metadata)):.1f} km".replace(".", ","),
                    "8000 m",
                    *[value for _label, value in parsed_lines],
                ]
                value_width = max(
                    NSString.stringWithString_(value).sizeWithAttributes_(
                        {NSFontAttributeName: font}
                    ).width
                    for value in reserve_values
                )
                gap = max(5.0, font_size * 0.45)
                colon_x = right_x - value_width - gap
                for index, line in enumerate(self.metrics_lines[:3]):
                    label, _separator, value = str(line).partition(":")
                    baseline_y = header_top - padding - (index + 1) * line_height
                    draw_shadowed_text(
                        f"{label.strip()}:",
                        colon_x,
                        baseline_y,
                        font,
                        self.overlay_font_color,
                        "right",
                    )
                    draw_shadowed_text(
                        value.strip(),
                        right_x,
                        baseline_y,
                        font,
                        self.overlay_font_color,
                        "right",
                    )

            if self.place_text:
                image_tuple = (
                    float(image_rect.origin.x),
                    float(image_rect.origin.y),
                    image_width,
                    image_height,
                )
                plot_rect = map_plot_rect(image_tuple, self.map_metadata)
                header_bottom = plot_rect[1] + plot_rect[3]
                header_height = float(image_rect.origin.y + image_rect.size.height) - header_bottom
                has_header = (
                    isinstance(self.map_metadata, dict)
                    and isinstance(self.map_metadata.get("axes_box_fraction"), dict)
                    and header_height >= 24.0
                )
                if has_header:
                    row_count = max(1, int(self.header_row_count))
                    row_height = header_height / row_count
                    text_band_y = header_bottom
                    max_width = image_width * 0.56
                    requested_font_size = max(
                        9.0,
                        14.0 * float(self.map_header_font_factor),
                    )
                    base_font_size = max(
                        8.0,
                        min(
                            requested_font_size,
                            header_height / (row_count + 0.35),
                        ),
                    )
                    font_size = max(
                        8.0,
                        min(base_font_size * 0.82, row_height * 0.82),
                    )
                else:
                    row_height = max(1.0, image_height * 0.05)
                    text_band_y = float(image_rect.origin.y)
                    max_width = image_width * 0.94
                    font_size = max(
                        8.0,
                        min(float(self.overlay_font_size), row_height * 0.60),
                    )
                font = NSFont.boldSystemFontOfSize_(font_size)
                text = str(self.place_text).replace("\n", " - ").strip()
                while font_size > 8.0:
                    text_size = NSString.stringWithString_(text).sizeWithAttributes_({NSFontAttributeName: font})
                    if text_size.width <= max_width and text_size.height <= row_height * 0.82:
                        break
                    font_size -= 1.0
                    font = NSFont.boldSystemFontOfSize_(font_size)
                text_size = NSString.stringWithString_(text).sizeWithAttributes_({NSFontAttributeName: font})
                baseline_y = text_band_y + max(1.0, (row_height - text_size.height) / 2.0)
                draw_shadowed_text(
                    text,
                    float(image_rect.origin.x + image_rect.size.width / 2.0),
                    baseline_y,
                    font,
                    self.overlay_font_color,
                    "center",
                    None if self.header_background_style == "off" else (0.0, 0.0, 0.0, 0.0),
                )

        def drawRect_(self, _dirty_rect):
            bounds = self.bounds()
            ns_color(COLOR_NAMES["black"]).setFill()
            NSBezierPath.fillRect_(bounds)
            if self.map_image is None:
                return
            image_rect, scale = self.imageRectAndScale()
            image_w, image_h = image_size_tuple(self.map_image)
            self.map_image.drawInRect_fromRect_operation_fraction_(image_rect, NSMakeRect(0, 0, image_w, image_h), NSCompositingOperationSourceOver, 1.0)
            route = [self.viewPointForLat_lon_imageRect_scale_(item["lat"], item["lon"], image_rect, scale) for item in self.route_points]
            route = [point for point in route if point is not None]
            if self.highlight_route and self.route_visible and len(route) >= 2:
                path = NSBezierPath.bezierPath()
                configure_round_stroke(path)
                path.moveToPoint_(route[0])
                for point in route[1:]:
                    path.lineToPoint_(point)
                ns_color(self.route_color).setStroke()
                path.setLineWidth_(max(float(self.route_width), bounds.size.width * 0.002))
                path.stroke()
            if self.media_marker_latlon is not None:
                marker_pixel = self.imagePixelForLat_lon_(self.media_marker_latlon[0], self.media_marker_latlon[1])
                if marker_pixel is not None:
                    scale_x, scale_y = self._scale_components(scale)
                    view_x = image_rect.origin.x + marker_pixel[0] * scale_x
                    view_y = image_rect.origin.y + (image_h - marker_pixel[1]) * scale_y
                    view_height = float(bounds.size.height)
                    view_pixel_y = view_height - view_y
                    if self.media_marker_fixed_arrow and self.arrow_factor > 0:
                        draw_open_arrow_at_marker(
                            view_x,
                            view_pixel_y,
                            view_height,
                            (math.sqrt(0.5), -math.sqrt(0.5)),
                            COLOR_NAMES["red"],
                            self.marker_radius,
                            self.arrow_factor,
                        )
                    draw_marker_at(
                        view_x,
                        view_pixel_y,
                        view_height,
                        COLOR_NAMES["red"],
                        self.marker_radius,
                        COLOR_NAMES["white"],
                    )
            # Draw the moving marker last, so it remains visible when it passes
            # directly over the fixed media-location marker.
            if self.arrow_latlon is not None:
                self.drawMarkerForLat_lon_imageRect_scale_(self.arrow_latlon[0], self.arrow_latlon[1], image_rect, scale)
            if self.media_image is not None:
                frame_rect, media_rect = self.mediaRectsForImage_(self.media_image)
                media_w, media_h = image_size_tuple(self.media_image)
                if self.media_draw_frame:
                    ns_color(COLOR_NAMES["white"]).setFill()
                    NSBezierPath.fillRect_(frame_rect)
                else:
                    NSGraphicsContext.saveGraphicsState()
                    shadow = NSShadow.alloc().init()
                    shadow.setShadowOffset_(NSMakeSize(8.0, -8.0))
                    shadow.setShadowBlurRadius_(14.0)
                    shadow.setShadowColor_(
                        NSColor.colorWithSRGBRed_green_blue_alpha_(
                            0.0,
                            0.0,
                            0.0,
                            0.58,
                        )
                    )
                    shadow.set()
                try:
                    self.media_image.drawInRect_fromRect_operation_fraction_(
                        media_rect,
                        NSMakeRect(0, 0, media_w, media_h),
                        NSCompositingOperationSourceOver,
                        1.0,
                    )
                finally:
                    if not self.media_draw_frame:
                        NSGraphicsContext.restoreGraphicsState()
                if self.media_video_view is not None:
                    self.media_video_view.setFrame_(media_rect)
            self.drawTextOverlays()


def format_place_for_slideshow(raw_place: str) -> str:
    """Show detailed POI/place names on a second overlay line."""
    place = str(raw_place or "").strip()
    if "," not in place:
        return place
    primary, secondary = place.split(",", 1)
    primary = primary.strip()
    secondary = secondary.strip()
    if primary and secondary:
        return f"{primary}\n{secondary}"
    return primary or secondary


def format_place_for_time_lapse(raw_place: object) -> Optional[str]:
    """Return one compact place-name row for the bottom of a track map."""
    if not isinstance(raw_place, str):
        return None
    cleaned = raw_place.strip()
    if not cleaned or cleaned.lower() in {"-", "kein ort", "unknown", "place failed"}:
        return None
    return " - ".join(line.strip() for line in format_place_for_slideshow(cleaned).splitlines() if line.strip())


def safe_float(value: object) -> Optional[float]:
    """Convert one value to float when possible."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def relation_title_band(
    image_rect: RectTuple,
    metadata: Optional[dict],
) -> RectTuple:
    """Return the top map-margin strip reserved above framed media."""
    plot_x, plot_y, plot_width, plot_height = map_plot_rect(image_rect, metadata)
    cache = metadata.get("media_clear_boxes") if isinstance(metadata, dict) else None
    try:
        margin_fraction = float(cache.get("margin_fraction", 0.05))
    except (AttributeError, TypeError, ValueError):
        margin_fraction = 0.05
    margin_fraction = max(0.01, min(0.20, margin_fraction))
    band_height = max(1.0, plot_height * margin_fraction)
    return plot_x, plot_y + plot_height - band_height, plot_width, band_height


def parse_geometry(geometry: Optional[str], default_width: int, default_height: int) -> tuple[int, int, int, int]:
    """Parse Cocoa-style geometry hints."""
    width = default_width
    height = default_height
    x_pos = 100
    y_pos = 100
    if not geometry:
        return width, height, x_pos, y_pos

    size_text, _, position_text = geometry.partition("+")
    if "x" in size_text:
        width_text, _, height_text = size_text.partition("x")
        if width_text:
            width = int(width_text)
        if height_text:
            height = int(height_text)
    if position_text:
        parts = position_text.split("+")
        if len(parts) >= 1 and parts[0]:
            x_pos = int(parts[0])
        if len(parts) >= 2 and parts[1]:
            y_pos = int(parts[1])
    return width, height, x_pos, y_pos


def load_nsimage(path: Path):
    """Load one image file as NSImage."""
    image = NSImage.alloc().initWithContentsOfFile_(str(path))
    if image is None:
        raise ValueError(f"unable to load image: {path}")
    return image


def is_video_path(path: Path) -> bool:
    """Return whether a path looks like a supported video file."""
    return path.suffix.lower() in VIDEO_EXTENSIONS


def make_av_asset(path: Path):
    """Create an AVURLAsset for one local video path."""
    if not AVKIT_VIDEO_AVAILABLE or AVURLAsset is None:
        raise RuntimeError("AVKit/AVFoundation video bindings are not available")
    url = NSURL.fileURLWithPath_(str(path))
    if hasattr(AVURLAsset, "URLAssetWithURL_options_"):
        return AVURLAsset.URLAssetWithURL_options_(url, None)
    return AVURLAsset.alloc().initWithURL_options_(url, None)


def video_duration_seconds(path: Path) -> float:
    """Return the playable duration of one video file in seconds."""
    asset = make_av_asset(path)
    duration = asset.duration()
    if CMTimeGetSeconds is not None:
        seconds = float(CMTimeGetSeconds(duration))
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    value = getattr(duration, "value", None)
    timescale = getattr(duration, "timescale", None)
    if value is not None and timescale:
        seconds = float(value) / float(timescale)
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    raise ValueError(f"unable to determine video duration: {path}")


def load_video_first_frame(path: Path):
    """Load the first frame of a video as an NSImage for slide transitions."""
    asset = make_av_asset(path)
    if AVAssetImageGenerator is None or CMTimeMake is None:
        raise RuntimeError("AVFoundation image-generator bindings are not available")
    if hasattr(AVAssetImageGenerator, "assetImageGeneratorWithAsset_"):
        generator = AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
    else:
        generator = AVAssetImageGenerator.alloc().initWithAsset_(asset)
    if hasattr(generator, "setAppliesPreferredTrackTransform_"):
        generator.setAppliesPreferredTrackTransform_(True)
    result = generator.copyCGImageAtTime_actualTime_error_(CMTimeMake(0, 1), None, None)
    if isinstance(result, tuple):
        cg_image = result[0]
        error = result[-1] if len(result) >= 3 else None
    else:
        cg_image = result
        error = None
    if cg_image is None:
        raise ValueError(f"unable to extract first video frame from {path}: {error}")
    rep = NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
    image = NSImage.alloc().initWithSize_(rep.size())
    if image is None:
        raise ValueError(f"unable to create first-frame image for video: {path}")
    image.addRepresentation_(rep)
    return image


def load_media_preview(path: Path):
    """Load either an image or the first frame of a video as an NSImage."""
    if is_video_path(path):
        return load_video_first_frame(path)
    return load_nsimage(path)


def image_size_tuple(image) -> tuple[float, float]:
    """Return NSImage size as a Python tuple."""
    size = image.size()
    return float(size.width), float(size.height)


def metadata_image_size(metadata: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    """Return metadata image size as width/height when available."""
    if not isinstance(metadata, dict):
        return None, None
    image_size = metadata.get("image_size_px")
    if not isinstance(image_size, dict):
        return None, None
    return safe_float(image_size.get("width")), safe_float(image_size.get("height"))


def scale_metadata_pixel_to_image(pixel_x: float, pixel_y: float, metadata: Optional[dict], image) -> tuple[float, float]:
    """Scale metadata pixel coordinates into the actual NSImage drawing size."""
    actual_width, actual_height = image_size_tuple(image)
    meta_width, meta_height = metadata_image_size(metadata)
    if meta_width is None or meta_height is None or meta_width == 0 or meta_height == 0:
        return pixel_x, pixel_y
    return pixel_x * (actual_width / meta_width), pixel_y * (actual_height / meta_height)


def copy_image(image):
    """Return a copied NSImage."""
    copied = image.copy()
    copied.setSize_(image.size())
    return copied


def draw_dot_on_image(image, pixel_x: float, pixel_y: float, color: tuple[float, float, float, float], radius: int):
    """Draw a styled marker dot into a copy of an NSImage."""
    result = copy_image(image)
    _, height = image_size_tuple(result)
    result.lockFocus()
    draw_marker_at(pixel_x, pixel_y, height, color, radius)
    result.unlockFocus()
    return result


def draw_track_photo_marker(
    image,
    pixel_x: float,
    pixel_y: float,
    metadata: Optional[dict],
    color: tuple[float, float, float, float],
    radius: int,
    arrow_factor: float,
    config: Optional[Config] = None,
):
    """Draw the photo marker dot plus an optional track-normal arrow."""
    result = copy_image(image)
    _, height = image_size_tuple(result)
    result.lockFocus()
    if arrow_factor > 0 and metadata is not None:
        tangent = nearest_track_tangent(pixel_x, pixel_y, metadata, result)
        if tangent is None:
            debug_print(config, "Skipping track marker arrow because no usable track direction metadata is available")
        else:
            draw_open_arrow_at_marker(pixel_x, pixel_y, height, tangent, color, radius, arrow_factor)
    draw_marker_at(pixel_x, pixel_y, height, color, radius)
    result.unlockFocus()
    return result


def draw_media_location_marker(
    image,
    pixel_x: float,
    pixel_y: float,
    color: tuple[float, float, float, float],
    radius: int,
    arrow_factor: float,
):
    """Draw a media-map marker with a fixed 45-degree arrow pointing at it."""
    result = copy_image(image)
    _, height = image_size_tuple(result)
    result.lockFocus()
    if arrow_factor > 0:
        diagonal_tangent = (math.sqrt(0.5), -math.sqrt(0.5))
        draw_open_arrow_at_marker(
            pixel_x,
            pixel_y,
            height,
            diagonal_tangent,
            color,
            radius,
            arrow_factor,
        )
    draw_marker_at(pixel_x, pixel_y, height, color, radius)
    result.unlockFocus()
    return result


def metadata_track_points(metadata: Optional[dict]) -> list[tuple[float, float]]:
    """Extract ordered lat/lon track points from supported metadata keys."""
    if not isinstance(metadata, dict):
        return []
    for key in ("track_points", "points", "route_points", "path_points", "coordinates"):
        points = metadata.get(key)
        extracted = extract_latlon_sequence(points)
        if len(extracted) >= 2:
            return extracted
    return []


def extract_latlon_sequence(value: object) -> list[tuple[float, float]]:
    """Return a lat/lon sequence from a JSON-friendly point list."""
    if not isinstance(value, list):
        return []
    points = []
    for point in value:
        lat, lon = extract_coordinate_point(point)
        if lat is None or lon is None:
            continue
        points.append((lat, lon))
    return points


def nearest_track_tangent(pixel_x: float, pixel_y: float, metadata: dict, image) -> Optional[tuple[float, float]]:
    """Return the local track tangent near a marker in image-pixel coordinates."""
    latlon_points = metadata_track_points(metadata)
    if len(latlon_points) < 2:
        return endpoint_track_tangent(metadata, image)
    pixel_points = []
    for lat, lon in latlon_points:
        try:
            track_x, track_y = coordinate_to_pixel(lat, lon, metadata)
        except Exception:
            return None
        pixel_points.append(scale_metadata_pixel_to_image(track_x, track_y, metadata, image))
    closest_index = min(
        range(len(pixel_points)),
        key=lambda index: (pixel_points[index][0] - pixel_x) ** 2 + (pixel_points[index][1] - pixel_y) ** 2,
    )
    if closest_index == 0:
        start, end = pixel_points[0], pixel_points[1]
    elif closest_index == len(pixel_points) - 1:
        start, end = pixel_points[-2], pixel_points[-1]
    else:
        start, end = pixel_points[closest_index - 1], pixel_points[closest_index + 1]
    tangent_x = end[0] - start[0]
    tangent_y = end[1] - start[1]
    length = math.hypot(tangent_x, tangent_y)
    if length <= 0:
        return None
    return tangent_x / length, tangent_y / length


def endpoint_track_tangent(metadata: dict, image) -> Optional[tuple[float, float]]:
    """Return a track tangent from start/end metadata when full points are absent."""
    start_point = metadata.get("start_point", metadata.get("first_point"))
    end_point = metadata.get("end_point", metadata.get("last_point"))
    start_lat, start_lon = extract_coordinate_point(start_point)
    end_lat, end_lon = extract_coordinate_point(end_point)
    if start_lat is None or start_lon is None or end_lat is None or end_lon is None:
        return None
    try:
        start_x, start_y = coordinate_to_pixel(start_lat, start_lon, metadata)
        end_x, end_y = coordinate_to_pixel(end_lat, end_lon, metadata)
    except Exception:
        return None
    start_x, start_y = scale_metadata_pixel_to_image(start_x, start_y, metadata, image)
    end_x, end_y = scale_metadata_pixel_to_image(end_x, end_y, metadata, image)
    tangent_x = end_x - start_x
    tangent_y = end_y - start_y
    length = math.hypot(tangent_x, tangent_y)
    if length <= 0:
        return None
    return tangent_x / length, tangent_y / length


def endpoint_tangent(points: list[tuple[float, float]]) -> Optional[tuple[float, float]]:
    """Return one fixed tangent along the line joining route start and end."""
    if len(points) < 2:
        return None
    tangent_x = points[-1][0] - points[0][0]
    tangent_y = points[-1][1] - points[0][1]
    length = math.hypot(tangent_x, tangent_y)
    if length <= 0:
        return None
    return tangent_x / length, tangent_y / length


def fixed_arrow_normal(tangent: Optional[tuple[float, float]]) -> Optional[tuple[float, float]]:
    """Return the fixed map-space normal used by the stage arrow."""
    if tangent is None:
        return None
    tangent_x, tangent_y = tangent
    normal_x, normal_y = -tangent_y, tangent_x
    if normal_y > 0:
        normal_x, normal_y = -normal_x, -normal_y
    normal_length = math.hypot(normal_x, normal_y)
    if normal_length <= 0:
        return None
    return normal_x / normal_length, normal_y / normal_length


def pilgrim_orientation_for_tangent(tangent: Optional[tuple[float, float]]) -> tuple[float, bool]:
    """Align a right-facing upright pilgrim with the fixed stage arrow and route."""
    normal = fixed_arrow_normal(tangent)
    if normal is None or tangent is None:
        return 0.0, False

    # Map pixels use a top-left origin, while AppKit drawing uses bottom-left.
    body_x, body_y = normal[0], -normal[1]
    rotation_degrees = math.degrees(math.atan2(-body_x, body_y))
    native_facing_x, native_facing_y = body_y, -body_x
    track_x, track_y = tangent[0], -tangent[1]
    mirrored = native_facing_x * track_x + native_facing_y * track_y < 0.0
    return rotation_degrees, mirrored


def draw_open_arrow_at_marker(
    pixel_x: float,
    pixel_y: float,
    image_height: float,
    tangent: tuple[float, float],
    color: tuple[float, float, float, float],
    radius: int,
    arrow_factor: float,
) -> None:
    """Draw one outlined arrow normal to the local track tangent."""
    normal = fixed_arrow_normal(tangent)
    if normal is None:
        return
    normal_x, normal_y = normal
    arrow_length = image_height * 0.07 * arrow_factor
    head_width = arrow_length / 3.0
    head_length = head_width
    shaft_width = head_width / 2.0
    shaft_length = arrow_length - head_length
    if arrow_length <= 0 or head_width <= 0 or shaft_length <= 0:
        return
    tip_x = pixel_x + normal_x * radius * 2.0
    tip_y = pixel_y + normal_y * radius * 2.0
    head_base_x = tip_x + normal_x * head_length
    head_base_y = tip_y + normal_y * head_length
    shaft_base_x = tip_x + normal_x * arrow_length
    shaft_base_y = tip_y + normal_y * arrow_length
    side_x, side_y = -normal_y, normal_x
    half_head_width = head_width / 2.0
    half_shaft_width = shaft_width / 2.0
    head_left = (head_base_x + side_x * half_head_width, image_height - (head_base_y + side_y * half_head_width))
    head_right = (head_base_x - side_x * half_head_width, image_height - (head_base_y - side_y * half_head_width))
    shaft_left_front = (
        head_base_x + side_x * half_shaft_width,
        image_height - (head_base_y + side_y * half_shaft_width),
    )
    shaft_right_front = (
        head_base_x - side_x * half_shaft_width,
        image_height - (head_base_y - side_y * half_shaft_width),
    )
    shaft_left_back = (
        shaft_base_x + side_x * half_shaft_width,
        image_height - (shaft_base_y + side_y * half_shaft_width),
    )
    shaft_right_back = (
        shaft_base_x - side_x * half_shaft_width,
        image_height - (shaft_base_y - side_y * half_shaft_width),
    )
    tip = (tip_x, image_height - tip_y)
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(tip)
    path.lineToPoint_(head_left)
    path.lineToPoint_(shaft_left_front)
    path.lineToPoint_(shaft_left_back)
    path.lineToPoint_(shaft_right_back)
    path.lineToPoint_(shaft_right_front)
    path.lineToPoint_(head_right)
    path.closePath()
    ns_color(color).setStroke()
    path.setLineWidth_(max(2.0, head_width * 0.12))
    path.stroke()


def draw_pilgrim_at_marker(
    pixel_x: float,
    pixel_y: float,
    image_height: float,
    frame_image,
    radius: int,
    size_factor: float,
    rotation_degrees: float,
    mirrored: bool,
) -> None:
    """Draw one stage-oriented pilgrim with its feet at the GPS marker."""
    if frame_image is None or size_factor <= 0:
        return
    source_x, source_y, source_width, source_height = PILGRIM_VISIBLE_SOURCE_RECT
    visible_height = image_height * 0.07 * size_factor
    visible_width = visible_height * source_width / source_height
    destination = NSMakeRect(-visible_width / 2.0, radius * 1.5, visible_width, visible_height)
    source = NSMakeRect(source_x, source_y, source_width, source_height)
    NSGraphicsContext.saveGraphicsState()
    try:
        transform = NSAffineTransform.transform()
        transform.translateXBy_yBy_(pixel_x, image_height - pixel_y)
        transform.rotateByDegrees_(rotation_degrees)
        if mirrored:
            transform.scaleXBy_yBy_(-1.0, 1.0)
        transform.concat()
        frame_image.drawInRect_fromRect_operation_fraction_(
            destination,
            source,
            NSCompositingOperationSourceOver,
            1.0,
        )
    finally:
        NSGraphicsContext.restoreGraphicsState()


def transport_marker_orientation(
    style: str,
    tangent: Optional[tuple[float, float]],
) -> tuple[float, bool]:
    """Use the pilgrim convention so vehicles follow the route without inversion."""
    if style in {"bike", "car"}:
        return pilgrim_orientation_for_tangent(tangent)
    direction = tangent or (1.0, 0.0)
    return (
        math.degrees(math.atan2(-float(direction[1]), float(direction[0]))),
        False,
    )


def draw_transport_marker_at(
    pixel_x: float,
    pixel_y: float,
    image_height: float,
    style: str,
    tangent: Optional[tuple[float, float]],
    color: tuple[float, float, float, float],
    radius: int,
    size_factor: float,
) -> None:
    """Draw a compact bicycle, car, or airplane aligned with the route."""
    if style not in {"bike", "car", "plane"} or size_factor <= 0.0:
        return
    size = max(float(radius) * 5.0, float(image_height) * 0.055 * float(size_factor))
    rotation, mirrored = transport_marker_orientation(style, tangent)
    outline_width = max(1.5, size * 0.055)
    local_offset = float(radius) * 1.5 + size * ({"car": 0.32, "bike": 0.24}.get(style, 0.0))

    def marker_point(point: tuple[float, float]) -> tuple[float, float]:
        return point[0], point[1] + local_offset

    def marker_rect(x: float, y: float, width: float, height: float):
        return NSMakeRect(x, y + local_offset, width, height)

    NSGraphicsContext.saveGraphicsState()
    try:
        transform = NSAffineTransform.transform()
        transform.translateXBy_yBy_(float(pixel_x), float(image_height) - float(pixel_y))
        transform.rotateByDegrees_(rotation)
        if mirrored:
            transform.scaleXBy_yBy_(-1.0, 1.0)
        transform.concat()
        if style == "car":
            # A top-view silhouette stays readable at every route angle. Its
            # nose points along +X, the same native facing direction as the
            # pilgrim artwork.
            for wheel_x in (-size * 0.31, size * 0.31):
                for wheel_y in (-size * 0.27, size * 0.27):
                    wheel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                        marker_rect(
                            wheel_x - size * 0.10,
                            wheel_y - size * 0.055,
                            size * 0.20,
                            size * 0.11,
                        ),
                        size * 0.035,
                        size * 0.035,
                    )
                    ns_color(COLOR_NAMES["black"]).setFill()
                    wheel.fill()
            body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                marker_rect(-size * 0.50, -size * 0.23, size, size * 0.46),
                size * 0.16,
                size * 0.16,
            )
            ns_color(color).setFill()
            body.fill()
            ns_color(COLOR_NAMES["black"]).setStroke()
            body.setLineWidth_(outline_width)
            body.stroke()

            windows = NSBezierPath.bezierPath()
            windows.moveToPoint_(marker_point((-size * 0.22, -size * 0.17)))
            windows.lineToPoint_(marker_point((size * 0.19, -size * 0.15)))
            windows.lineToPoint_(marker_point((size * 0.28, 0.0)))
            windows.lineToPoint_(marker_point((size * 0.19, size * 0.15)))
            windows.lineToPoint_(marker_point((-size * 0.22, size * 0.17)))
            windows.lineToPoint_(marker_point((-size * 0.29, 0.0)))
            windows.closePath()
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.62).setFill()
            windows.fill()
            for light_y in (-size * 0.13, size * 0.13):
                headlight = NSBezierPath.bezierPathWithOvalInRect_(
                    marker_rect(size * 0.43, light_y - size * 0.035, size * 0.07, size * 0.07)
                )
                ns_color(COLOR_NAMES["yellow"]).setFill()
                headlight.fill()
                tail_light = NSBezierPath.bezierPathWithOvalInRect_(
                    marker_rect(-size * 0.50, light_y - size * 0.035, size * 0.07, size * 0.07)
                )
                ns_color(COLOR_NAMES["red"]).setFill()
                tail_light.fill()
        elif style == "bike":
            wheel_radius = size * 0.22
            wheel_centers = (-size * 0.29, size * 0.29)
            for wheel_x in wheel_centers:
                wheel = NSBezierPath.bezierPathWithOvalInRect_(
                    marker_rect(wheel_x - wheel_radius, -wheel_radius, wheel_radius * 2.0, wheel_radius * 2.0)
                )
                ns_color(COLOR_NAMES["black"]).setStroke()
                wheel.setLineWidth_(outline_width * 2.0)
                wheel.stroke()
                ns_color(color).setStroke()
                wheel.setLineWidth_(outline_width)
                wheel.stroke()
            frame = NSBezierPath.bezierPath()
            for start, end in (
                ((-size * 0.29, 0.0), (0.0, size * 0.05)),
                ((0.0, size * 0.05), (size * 0.29, 0.0)),
                ((0.0, size * 0.05), (-size * 0.09, size * 0.30)),
                ((-size * 0.09, size * 0.30), (-size * 0.29, 0.0)),
                ((-size * 0.09, size * 0.30), (size * 0.17, size * 0.30)),
                ((size * 0.17, size * 0.30), (size * 0.29, 0.0)),
                ((size * 0.17, size * 0.30), (size * 0.25, size * 0.40)),
                ((size * 0.25, size * 0.40), (size * 0.34, size * 0.40)),
            ):
                frame.moveToPoint_(marker_point(start))
                frame.lineToPoint_(marker_point(end))
            configure_round_stroke(frame)
            ns_color(COLOR_NAMES["black"]).setStroke()
            frame.setLineWidth_(outline_width * 2.2)
            frame.stroke()
            ns_color(color).setStroke()
            frame.setLineWidth_(outline_width)
            frame.stroke()
            seat = NSBezierPath.bezierPath()
            seat.moveToPoint_(marker_point((-size * 0.16, size * 0.33)))
            seat.lineToPoint_(marker_point((-size * 0.03, size * 0.33)))
            configure_round_stroke(seat)
            ns_color(COLOR_NAMES["black"]).setStroke()
            seat.setLineWidth_(outline_width * 1.8)
            seat.stroke()
            crank = NSBezierPath.bezierPathWithOvalInRect_(
                marker_rect(-size * 0.045, size * 0.005, size * 0.09, size * 0.09)
            )
            ns_color(COLOR_NAMES["black"]).setStroke()
            crank.setLineWidth_(outline_width)
            crank.stroke()
        else:
            plane = NSBezierPath.bezierPath()
            plane_points = (
                (size * 0.52, 0.0),
                (size * 0.06, size * 0.11),
                (-size * 0.16, size * 0.44),
                (-size * 0.28, size * 0.44),
                (-size * 0.20, size * 0.08),
                (-size * 0.50, size * 0.14),
                (-size * 0.52, 0.0),
                (-size * 0.50, -size * 0.14),
                (-size * 0.20, -size * 0.08),
                (-size * 0.28, -size * 0.44),
                (-size * 0.16, -size * 0.44),
                (size * 0.06, -size * 0.11),
            )
            for index, point in enumerate(plane_points):
                if index == 0:
                    plane.moveToPoint_(marker_point(point))
                else:
                    plane.lineToPoint_(marker_point(point))
            plane.closePath()
            ns_color(color).setFill()
            plane.fill()
            ns_color(COLOR_NAMES["black"]).setStroke()
            plane.setLineWidth_(outline_width)
            plane.stroke()
    finally:
        NSGraphicsContext.restoreGraphicsState()


def draw_marker_at(
    pixel_x: float,
    pixel_y: float,
    image_height: float,
    color: tuple[float, float, float, float],
    radius: int,
    outline_color: Optional[tuple[float, float, float, float]] = None,
) -> None:
    """Draw one filled marker with a configurable outline into current focus."""
    outline_width = max(1.0, radius / 5.0)
    oval_rect = NSMakeRect(pixel_x - radius, image_height - pixel_y - radius, radius * 2, radius * 2)
    ns_color(color).setFill()
    fill_path = NSBezierPath.bezierPathWithOvalInRect_(oval_rect)
    fill_path.fill()
    ns_color(outline_color or COLOR_NAMES["black"]).setStroke()
    fill_path.setLineWidth_(outline_width)
    fill_path.stroke()


def draw_arrow_line(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    image_height: float,
    color: tuple[float, float, float, float],
    line_width: float,
) -> None:
    """Draw a straight line with arrow head into the current focus."""
    import math

    start_y_flipped = image_height - start_y
    end_y_flipped = image_height - end_y

    ns_color(COLOR_NAMES["black"]).setStroke()
    outline_path = NSBezierPath.bezierPath()
    outline_path.moveToPoint_((start_x, start_y_flipped))
    outline_path.lineToPoint_((end_x, end_y_flipped))
    outline_path.setLineWidth_(line_width + max(1.0, line_width / 2.0))
    outline_path.stroke()

    ns_color(color).setStroke()
    path = NSBezierPath.bezierPath()
    path.moveToPoint_((start_x, start_y_flipped))
    path.lineToPoint_((end_x, end_y_flipped))
    path.setLineWidth_(line_width)
    path.stroke()

    angle = math.atan2(end_y_flipped - start_y_flipped, end_x - start_x)
    arrow_length = max(line_width * 4.0, 10.0)
    arrow_spread = math.pi / 7.0
    left_x = end_x - arrow_length * math.cos(angle - arrow_spread)
    left_y = end_y_flipped - arrow_length * math.sin(angle - arrow_spread)
    right_x = end_x - arrow_length * math.cos(angle + arrow_spread)
    right_y = end_y_flipped - arrow_length * math.sin(angle + arrow_spread)

    ns_color(COLOR_NAMES["black"]).setStroke()
    arrow_outline = NSBezierPath.bezierPath()
    arrow_outline.moveToPoint_((end_x, end_y_flipped))
    arrow_outline.lineToPoint_((left_x, left_y))
    arrow_outline.moveToPoint_((end_x, end_y_flipped))
    arrow_outline.lineToPoint_((right_x, right_y))
    arrow_outline.setLineWidth_(line_width + max(1.0, line_width / 2.0))
    arrow_outline.stroke()

    ns_color(color).setStroke()
    arrow = NSBezierPath.bezierPath()
    arrow.moveToPoint_((end_x, end_y_flipped))
    arrow.lineToPoint_((left_x, left_y))
    arrow.moveToPoint_((end_x, end_y_flipped))
    arrow.lineToPoint_((right_x, right_y))
    arrow.setLineWidth_(line_width)
    arrow.stroke()


def draw_outlined_text(
    text: str,
    center_x: float,
    baseline_y: float,
    font,
    fill_color: tuple[float, float, float, float],
    outline_color: tuple[float, float, float, float],
    outline_width: float,
) -> None:
    """Draw centered text with a simple black contour."""
    attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(fill_color),
    }
    outline_attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(outline_color),
    }
    ns_text = NSString.stringWithString_(text)
    text_size = ns_text.sizeWithAttributes_(attributes)
    origin_x = max(0.0, center_x - text_size.width / 2.0)
    offsets = [
        (-outline_width, 0.0),
        (outline_width, 0.0),
        (0.0, -outline_width),
        (0.0, outline_width),
        (-outline_width, -outline_width),
        (-outline_width, outline_width),
        (outline_width, -outline_width),
        (outline_width, outline_width),
    ]
    for offset_x, offset_y in offsets:
        ns_text.drawInRect_withAttributes_(
            NSMakeRect(origin_x + offset_x, baseline_y + offset_y, text_size.width, text_size.height),
            outline_attributes,
        )
    ns_text.drawInRect_withAttributes_(
        NSMakeRect(origin_x, baseline_y, text_size.width, text_size.height),
        attributes,
    )


def draw_outlined_text_left(
    text: str,
    left_x: float,
    baseline_y: float,
    font,
    fill_color: tuple[float, float, float, float],
    outline_color: tuple[float, float, float, float],
    outline_width: float,
) -> None:
    """Draw left-aligned text with a simple black contour."""
    attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(fill_color),
    }
    outline_attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(outline_color),
    }
    ns_text = NSString.stringWithString_(text)
    text_size = ns_text.sizeWithAttributes_(attributes)
    offsets = [
        (-outline_width, 0.0),
        (outline_width, 0.0),
        (0.0, -outline_width),
        (0.0, outline_width),
        (-outline_width, -outline_width),
        (-outline_width, outline_width),
        (outline_width, -outline_width),
        (outline_width, outline_width),
    ]
    for offset_x, offset_y in offsets:
        ns_text.drawInRect_withAttributes_(
            NSMakeRect(left_x + offset_x, baseline_y + offset_y, text_size.width, text_size.height),
            outline_attributes,
        )
    ns_text.drawInRect_withAttributes_(
        NSMakeRect(left_x, baseline_y, text_size.width, text_size.height),
        attributes,
    )


def draw_shadowed_text(
    text: str,
    anchor_x: float,
    baseline_y: float,
    font,
    fill_color: tuple[float, float, float, float],
    alignment: str = "center",
    shadow_color: Optional[tuple[float, float, float, float]] = None,
) -> None:
    """Draw one text line over a half-transparent down-right shadow."""
    ns_text = NSString.stringWithString_(str(text))
    fill_attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(fill_color),
    }
    shadow_attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(
            shadow_color
            if shadow_color is not None
            else (0.0, 0.0, 0.0, 0.5)
        ),
    }
    text_size = ns_text.sizeWithAttributes_(fill_attributes)
    if alignment == "right":
        origin_x = anchor_x - text_size.width
    elif alignment == "left":
        origin_x = anchor_x
    else:
        origin_x = anchor_x - text_size.width / 2.0
    shadow_offset = max(1.0, float(font.pointSize()) / 12.0)
    ns_text.drawInRect_withAttributes_(
        NSMakeRect(origin_x + shadow_offset, baseline_y - shadow_offset, text_size.width, text_size.height),
        shadow_attributes,
    )
    ns_text.drawInRect_withAttributes_(
        NSMakeRect(origin_x, baseline_y, text_size.width, text_size.height),
        fill_attributes,
    )


def _draw_profile_label(text: str, x: float, y: float, font, *, centered=False) -> None:
    attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: NSColor.colorWithSRGBRed_green_blue_alpha_(
            0.12, 0.12, 0.14, 1.0
        ),
    }
    value = NSString.stringWithString_(str(text))
    size = value.sizeWithAttributes_(attributes)
    left = x - size.width / 2.0 if centered else x
    value.drawInRect_withAttributes_(
        NSMakeRect(left, y, size.width + 2.0, size.height + 2.0),
        attributes,
    )


def render_elevation_profile_image(metadata: object):
    """Render the GPX Editor min/max elevation profile as an NSImage."""
    segments = elevation_profile_segments(metadata)
    ranges = elevation_profile_ranges(segments)
    if ranges is None:
        return None
    (x_min, x_max), (y_min, y_max) = ranges
    width = float(ELEVATION_PROFILE_WIDTH)
    height = float(ELEVATION_PROFILE_HEIGHT)
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    if image is None:
        return None
    image.lockFocus()
    try:
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.97, 0.97, 0.98, 1.0).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0.0, 0.0, width, height))
        plot = NSMakeRect(88.0, 54.0, width - 118.0, height - 112.0)
        grid_color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.68, 0.68, 0.72, 0.65)
        axis_color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.12, 0.12, 0.14, 1.0)
        label_font = NSFont.systemFontOfSize_(20.0)
        title_font = NSFont.boldSystemFontOfSize_(27.0)

        for tick in range(5):
            fraction = tick / 4.0
            y = plot.origin.y + fraction * plot.size.height
            grid = NSBezierPath.bezierPath()
            grid.moveToPoint_((plot.origin.x, y))
            grid.lineToPoint_((plot.origin.x + plot.size.width, y))
            grid.setLineWidth_(1.0)
            grid_color.setStroke()
            grid.stroke()
            elevation = y_min + fraction * (y_max - y_min)
            _draw_profile_label(f"{elevation:.0f} m", 8.0, y - 10.0, label_font)

        for tick in range(5):
            fraction = tick / 4.0
            x = plot.origin.x + fraction * plot.size.width
            distance = x_min + fraction * (x_max - x_min)
            _draw_profile_label(
                f"{distance:.1f} km",
                x,
                20.0,
                label_font,
                centered=True,
            )

        axes = NSBezierPath.bezierPath()
        axes.moveToPoint_((plot.origin.x, plot.origin.y + plot.size.height))
        axes.lineToPoint_((plot.origin.x, plot.origin.y))
        axes.lineToPoint_((plot.origin.x + plot.size.width, plot.origin.y))
        axes.setLineWidth_(2.0)
        axis_color.setStroke()
        axes.stroke()

        x_span = max(0.001, x_max - x_min)
        y_span = max(1.0, y_max - y_min)
        route_color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.05, 0.27, 0.82, 1.0)
        for segment in segments:
            if len(segment) < 2:
                continue
            path = NSBezierPath.bezierPath()
            configure_round_stroke(path)
            for index, (distance, elevation) in enumerate(segment):
                point = (
                    plot.origin.x + ((distance - x_min) / x_span) * plot.size.width,
                    plot.origin.y + ((elevation - y_min) / y_span) * plot.size.height,
                )
                if index == 0:
                    path.moveToPoint_(point)
                else:
                    path.lineToPoint_(point)
            path.setLineWidth_(4.0)
            route_color.setStroke()
            path.stroke()

        title = "Elevation Profile"
        if isinstance(metadata, dict) and str(metadata.get("track_name") or "").strip():
            title = f"Elevation Profile - {str(metadata['track_name']).strip()}"
        _draw_profile_label(title, width / 2.0, height - 43.0, title_font, centered=True)
    finally:
        image.unlockFocus()
    return image


def _png_bytes_for_image(image) -> Optional[bytes]:
    tiff_data = image.TIFFRepresentation() if image is not None else None
    if tiff_data is None:
        return None
    representation = NSBitmapImageRep.imageRepWithData_(tiff_data)
    if representation is None:
        return None
    data = representation.representationUsingType_properties_(
        NSBitmapImageFileTypePNG,
        {},
    )
    return bytes(data) if data is not None else None


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_or_create_elevation_profile(track_map_path: Path, metadata: object):
    """Load a current cached profile, or render and atomically store it once."""
    if not elevation_profile_segments(metadata):
        return None
    image_path, manifest_path = elevation_profile_cache_paths(track_map_path)
    manifest = None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if image_path.is_file() and elevation_profile_cache_is_current(manifest, metadata):
        image = NSImage.alloc().initWithContentsOfFile_(str(image_path))
        if image is not None:
            return image
    try:
        image = render_elevation_profile_image(metadata)
        png_data = _png_bytes_for_image(image)
    except Exception as exc:
        warn_message(f"could not render elevation profile for {track_map_path.name}: {exc}")
        return None
    if image is None or png_data is None:
        return image
    try:
        _write_bytes_atomic(image_path, png_data)
        write_json_atomic(manifest_path, elevation_profile_manifest(metadata))
        cached = NSImage.alloc().initWithContentsOfFile_(str(image_path))
        return cached if cached is not None else image
    except OSError as exc:
        warn_message(f"could not cache elevation profile {image_path}: {exc}")
        return image


def unframed_media_rect_for_map(map_image, metadata: object, media_image) -> tuple[float, float, float, float]:
    """Return the largest route-free map rectangle for unframed content."""
    map_width, map_height = image_size_tuple(map_image)
    image_rect = (0.0, 0.0, map_width, map_height)
    clear_rects = cached_clear_box_options(
        metadata,
        image_rect,
        (map_width, map_height),
    )
    if clear_rects is None:
        plot_rect = map_plot_rect(image_rect, metadata)
        cache_metadata = metadata.get("media_clear_boxes") if isinstance(metadata, dict) else None
        try:
            margin = float(cache_metadata.get("margin_fraction", 0.05))
        except (AttributeError, TypeError, ValueError):
            margin = 0.05
        placement_rect = inset_rect(plot_rect, max(0.0, min(0.49, margin)))
        projected = []
        for point in placement_obstacle_points(metadata, []):
            try:
                x, y = coordinate_to_pixel(point.latitude, point.longitude, metadata)
                x, y = scale_metadata_pixel_to_image(x, y, metadata, map_image)
            except Exception:
                continue
            projected.append((x, map_height - y))
        stage_kind = scene_from_metadata(metadata, show_header=False).stage_kind
        clear_rects = clear_corner_rect_options(
            placement_rect,
            projected,
            connect_points=stage_kind != "media_stage",
        )
    _position, rect = best_unframed_media_layout(
        clear_rects,
        image_size_tuple(media_image),
    )
    return rect


def draw_unframed_media_on_map(map_image, metadata: object, media_image):
    """Composite unframed media into the largest route-free map region."""
    if map_image is None or media_image is None:
        return map_image
    result = copy_image(map_image)
    rect = unframed_media_rect_for_map(result, metadata, media_image)
    media_width, media_height = image_size_tuple(media_image)
    result.lockFocus()
    try:
        NSGraphicsContext.saveGraphicsState()
        try:
            shadow = NSShadow.alloc().init()
            shadow.setShadowOffset_(NSMakeSize(8.0, -8.0))
            shadow.setShadowBlurRadius_(14.0)
            shadow.setShadowColor_(
                NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.58)
            )
            shadow.set()
            media_image.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(*rect),
                NSMakeRect(0.0, 0.0, media_width, media_height),
                NSCompositingOperationSourceOver,
                1.0,
            )
        finally:
            NSGraphicsContext.restoreGraphicsState()
    finally:
        result.unlockFocus()
    return result


def format_overview_duration(value: object) -> Optional[str]:
    """Format one duration string for overview captions as H:mm h."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith(" h"):
        return cleaned
    if cleaned.endswith("h"):
        return cleaned[:-1].rstrip() + " h"
    return f"{cleaned} h"


def create_help_overlay_image(width: float, height: float):
    """Create an overlay image describing keyboard controls."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.65).setFill()
    line_height = 30.0
    panel_width = min(width * 0.86, 980.0)
    panel_height = min(height * 0.82, max(430.0, 108.0 + line_height * len(KEY_HELP_LINES[1:])))
    panel_x = (width - panel_width) / 2.0
    panel_y = (height - panel_height) / 2.0
    panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(panel_x, panel_y, panel_width, panel_height),
        18.0,
        18.0,
    )
    panel.fill()
    ns_color(COLOR_NAMES["white"]).setStroke()
    panel.setLineWidth_(2.0)
    panel.stroke()

    title_font = NSFont.boldSystemFontOfSize_(28.0)
    line_font = NSFont.systemFontOfSize_(22.0)
    top_y = panel_y + panel_height - 52.0
    draw_outlined_text("GPSTrackShow Keys", width / 2.0, top_y, title_font, COLOR_NAMES["white"], COLOR_NAMES["black"], 2.0)
    left_x = panel_x + 34.0
    for index, line in enumerate(KEY_HELP_LINES[1:], start=0):
        draw_outlined_text_left(
            line,
            left_x,
            top_y - 50.0 - index * line_height,
            line_font,
            COLOR_NAMES["white"],
            COLOR_NAMES["black"],
            1.5,
        )

    image.unlockFocus()
    return image


def create_info_overlay_image(width: float, height: float, info_text: str):
    """Create a left-aligned metadata overlay panel."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.72).setFill()
    panel_width = min(width * 0.6, 760.0)
    panel_height = min(height * 0.72, 560.0)
    panel_x = 26.0
    panel_y = max(26.0, height - panel_height - 130.0)
    panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(panel_x, panel_y, panel_width, panel_height),
        18.0,
        18.0,
    )
    panel.fill()
    ns_color(COLOR_NAMES["white"]).setStroke()
    panel.setLineWidth_(2.0)
    panel.stroke()

    title_font = NSFont.boldSystemFontOfSize_(26.0)
    line_font = NSFont.userFixedPitchFontOfSize_(16.0) or NSFont.systemFontOfSize_(16.0)
    top_y = panel_y + panel_height - 44.0
    draw_outlined_text_left(
        "Photo Info",
        panel_x + 24.0,
        top_y,
        title_font,
        COLOR_NAMES["white"],
        COLOR_NAMES["black"],
        2.0,
    )

    line_height = 20.0
    max_lines = max(6, int((panel_height - 80.0) / line_height))
    for index, line in enumerate(info_text.splitlines()[:max_lines]):
        draw_outlined_text_left(
            line[:160],
            panel_x + 24.0,
            top_y - 42.0 - index * line_height,
            line_font,
            COLOR_NAMES["white"],
            COLOR_NAMES["black"],
            1.2,
        )

    image.unlockFocus()
    return image


def create_status_overlay_image(width: float, height: float, status_text: str):
    """Create a centered status overlay panel."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    panel_width = min(width * 0.46, 520.0)
    panel_height = 72.0
    panel_x = (width - panel_width) / 2.0
    panel_y = height - panel_height - 28.0
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.66).setFill()
    panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(panel_x, panel_y, panel_width, panel_height),
        18.0,
        18.0,
    )
    panel.fill()
    ns_color(COLOR_NAMES["white"]).setStroke()
    panel.setLineWidth_(2.0)
    panel.stroke()

    font = NSFont.boldSystemFontOfSize_(30.0)
    draw_outlined_text(
        status_text,
        width / 2.0,
        panel_y + 20.0,
        font,
        COLOR_NAMES["white"],
        COLOR_NAMES["black"],
        2.0,
    )
    image.unlockFocus()
    return image


def create_memory_overlay_image(width: float, height: float, memory_text: str, warning: bool = False):
    """Create a compact top-right resident-memory monitor."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    font = NSFont.boldSystemFontOfSize_(18.0)
    attributes = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: ns_color(COLOR_NAMES["white"]),
    }
    text_size = NSString.stringWithString_(memory_text).sizeWithAttributes_(attributes)
    panel_width = min(width - 36.0, max(300.0, text_size.width + 42.0))
    panel_height = 42.0
    panel_x = max(18.0, width - panel_width - 18.0)
    panel_y = max(18.0, height - panel_height - 18.0)
    panel_color = (0.52, 0.05, 0.05, 0.84) if warning else (0.0, 0.0, 0.0, 0.68)
    NSColor.colorWithSRGBRed_green_blue_alpha_(*panel_color).setFill()
    panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(panel_x, panel_y, panel_width, panel_height),
        12.0,
        12.0,
    )
    panel.fill()
    ns_color(COLOR_NAMES["yellow"] if warning else COLOR_NAMES["white"]).setStroke()
    panel.setLineWidth_(1.5)
    panel.stroke()
    draw_outlined_text_left(
        memory_text,
        panel_x + 16.0,
        panel_y + 12.0,
        font,
        COLOR_NAMES["white"],
        COLOR_NAMES["black"],
        1.0,
    )
    image.unlockFocus()
    return image


def create_place_overlay_image(
    width: float,
    height: float,
    place_text: str,
    font_size: int,
    font_color,
    metadata: Optional[dict] = None,
    font_factor: float = 2.2,
    background_style: str = "off",
):
    """Create a place-name row aligned with the common three-row header."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    _header_x, header_y, _header_width, header_height = runtime_header_band(
        (0.0, 0.0, width, height),
        metadata,
    )
    row_height = header_height / 3.0
    requested = max(9.0, 14.0 * float(font_factor))
    base_size = max(8.0, min(requested, header_height / 3.35))
    place_font_size = max(8.0, min(base_size * 0.82, row_height * 0.82))
    font = NSFont.boldSystemFontOfSize_(place_font_size)
    text = " - ".join(line.strip() for line in str(place_text).splitlines() if line.strip())
    text_size = NSString.stringWithString_(text).sizeWithAttributes_({NSFontAttributeName: font})
    baseline = header_y + max(1.0, (row_height - text_size.height) / 2.0)
    shadow_color = None if str(background_style).casefold() == "off" else (0.0, 0.0, 0.0, 0.0)
    draw_shadowed_text(text, width / 2.0, baseline, font, font_color, "center", shadow_color)
    image.unlockFocus()
    return image


def create_startup_hint_overlay_image(
    width: float,
    height: float,
    *,
    bottom: bool = False,
    wait_for_start: bool = False,
):
    """Create the temporary startup hint shown on the photo screen."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    panel_width = min(width * 0.78, 1180.0)
    lines = (
        [
            "Press Space or Right Arrow to continue",
            "The title advances automatically after 30 seconds",
            "Press h for help with keyboard controls",
        ]
        if wait_for_start
        else ["Press h for help with keyboard controls"]
    )
    panel_height = min(height * 0.18, 52.0 + len(lines) * 30.0)
    panel_x = (width - panel_width) / 2.0
    panel_y = 34.0 if bottom else height - panel_height - 34.0
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.66).setFill()
    panel = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(panel_x, panel_y, panel_width, panel_height),
        18.0,
        18.0,
    )
    panel.fill()
    ns_color(COLOR_NAMES["white"]).setStroke()
    panel.setLineWidth_(2.0)
    panel.stroke()

    font = NSFont.boldSystemFontOfSize_(24.0)
    line_height = 30.0
    start_y = panel_y + panel_height - 42.0
    for index, line in enumerate(lines):
        draw_outlined_text(
            line,
            width / 2.0,
            start_y - index * line_height,
            font,
            COLOR_NAMES["white"],
            COLOR_NAMES["black"],
            2.0,
        )

    image.unlockFocus()
    return image


def parse_clock_time(value: object) -> Optional[tuple[int, int]]:
    """Parse a HH:MM time string."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if len(cleaned) != 5 or cleaned[2] != ":":
        return None
    try:
        hour = int(cleaned[:2])
        minute = int(cleaned[3:])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def derive_clock_date_text(photo_metadata: dict, fallback_date_text: Optional[str]) -> Optional[str]:
    """Return the best available date context for the clock overlay."""
    datetime_iso = photo_metadata.get("datetime_iso")
    if isinstance(datetime_iso, str) and len(datetime_iso) >= 10:
        iso_date = datetime_iso[:10]
        parts = iso_date.split("-")
        if len(parts) == 3:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
    date_german = photo_metadata.get("date_german")
    if isinstance(date_german, str) and "," in date_german:
        return date_german.strip() or None
    if isinstance(fallback_date_text, str):
        fallback = fallback_date_text.strip()
        if fallback:
            return fallback
    return None


def clock_date_lines(date_text: Optional[str]) -> tuple[str, str]:
    """Return weekday and date lines, preserving an explicitly supplied weekday."""
    text = str(date_text or "").strip()
    if not text:
        return "", ""
    if "\n" in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
        text = lines[0] if lines else ""
    if "," in text:
        weekday, _separator, date_value = text.partition(",")
        if weekday.strip() and date_value.strip():
            return weekday.strip(), date_value.strip()
    for date_format in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, date_format)
        except ValueError:
            continue
        return parsed.strftime("%A"), parsed.strftime("%d.%m.%Y")
    return "", text


def create_clock_overlay_image(
    clock_size: float,
    hour: int,
    minute: int,
    date_text: Optional[str] = None,
    *,
    date_on_right: bool = False,
    date_font_size: Optional[float] = None,
    font_color: tuple[float, float, float, float] = COLOR_NAMES["white"],
    shadow_color: tuple[float, float, float, float] = COLOR_NAMES["black"],
    background_style: str = "off",
):
    """Create an analog clock whose drop shadow matches the title header."""
    import math

    weekday_text, calendar_date_text = clock_date_lines(date_text)
    date_lines = [
        line
        for line in (weekday_text, calendar_date_text)
        if line
    ]
    date_height = (
        0.0
        if date_on_right
        else (max(16.0, clock_size * 0.22 * len(date_lines)) if date_lines else 0.0)
    )
    date_width = clock_size * 1.9 if date_lines and date_on_right else 0.0
    total_height = clock_size + date_height
    total_width = clock_size + date_width
    image = NSImage.alloc().initWithSize_(NSMakeSize(total_width, total_height))
    image.lockFocus()

    stroke_width = max(1.5, clock_size / 30.0)
    effective_shadow_color = runtime_header_text_shadow_color(
        background_style,
        shadow_color,
    )
    NSGraphicsContext.saveGraphicsState()
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(
            *effective_shadow_color,
        )
    )
    shadow_offset = max(1.0, clock_size / 48.0)
    shadow.setShadowOffset_(NSMakeSize(shadow_offset, -shadow_offset))
    shadow.setShadowBlurRadius_(0.0)
    shadow.set()
    # Reserve the one-line-width bottom-right offset so the shadow is not clipped.
    padding = stroke_width * 1.5 + 1.0
    usable_size = max(8.0, clock_size - 2.0 * padding)
    radius = usable_size / 2.0
    center_x = clock_size / 2.0
    center_y = date_height + clock_size / 2.0

    face_rect = NSMakeRect(center_x - radius, center_y - radius, usable_size, usable_size)
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.45).setFill()
    face = NSBezierPath.bezierPathWithOvalInRect_(face_rect)
    face.fill()
    ns_color(font_color).setStroke()
    face.setLineWidth_(stroke_width)
    face.stroke()

    for tick in range(12):
        angle = math.pi / 2.0 - tick * (2.0 * math.pi / 12.0)
        outer_r = radius * 0.88
        inner_r = radius * (0.62 if tick % 3 == 0 else 0.72)
        line_width = max(1.2, clock_size / 20.0) if tick % 3 == 0 else max(1.0, clock_size / 36.0)
        x1 = center_x + inner_r * math.cos(angle)
        y1 = center_y + inner_r * math.sin(angle)
        x2 = center_x + outer_r * math.cos(angle)
        y2 = center_y + outer_r * math.sin(angle)
        tick_path = NSBezierPath.bezierPath()
        tick_path.moveToPoint_((x1, y1))
        tick_path.lineToPoint_((x2, y2))
        tick_path.setLineWidth_(line_width)
        tick_path.stroke()

    minute_angle = math.pi / 2.0 - minute * (2.0 * math.pi / 60.0)
    hour_angle = math.pi / 2.0 - ((hour % 12) + minute / 60.0) * (2.0 * math.pi / 12.0)

    for angle, length_factor, line_width in (
        (hour_angle, 0.46, max(2.0, clock_size / 18.0)),
        (minute_angle, 0.72, max(1.6, clock_size / 28.0)),
    ):
        hand = NSBezierPath.bezierPath()
        hand.moveToPoint_((center_x, center_y))
        hand.lineToPoint_(
            (
                center_x + radius * length_factor * math.cos(angle),
                center_y + radius * length_factor * math.sin(angle),
            )
        )
        hand.setLineWidth_(line_width)
        hand.stroke()

    hub_rect = NSMakeRect(center_x - 2.5, center_y - 2.5, 5.0, 5.0)
    ns_color(font_color).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(hub_rect).fill()
    NSGraphicsContext.restoreGraphicsState()

    if date_lines:
        requested_font_size = max(
            12.0,
            float(date_font_size)
            if date_font_size is not None
            else clock_size * (0.32 if date_on_right else 0.22),
        )
        if date_on_right and len(date_lines) > 1:
            requested_font_size = min(
                requested_font_size,
                max(8.0, (clock_size - 2.0 * padding) / 2.25),
            )
        font_size = requested_font_size
        font = NSFont.boldSystemFontOfSize_(font_size)
        attributes = {NSFontAttributeName: font}
        line_heights = [
            float(NSString.stringWithString_(line).sizeWithAttributes_(attributes).height)
            for line in date_lines
        ]
        line_gap = max(1.0, font_size * 0.08) if len(date_lines) > 1 else 0.0
        block_height = sum(line_heights) + line_gap * (len(date_lines) - 1)
        if date_on_right:
            baseline_y = max(
                stroke_width + 1.0,
                (clock_size - block_height) / 2.0,
            )
        else:
            baseline_y = max(stroke_width + 1.0, (date_height - block_height) / 2.0)
        center_x = (
            clock_size + date_width / 2.0
            if date_on_right
            else clock_size / 2.0
        )
        # AppKit's origin is bottom-left, so draw the date first and weekday above it.
        for line, line_height in reversed(list(zip(date_lines, line_heights))):
            draw_shadowed_text(
                line,
                center_x,
                baseline_y,
                font,
                font_color,
                "center",
                effective_shadow_color,
            )
            baseline_y += line_height + line_gap

    image.unlockFocus()
    return image


def make_blank_canvas(width: float, height: float, background_color: tuple[float, float, float, float]):
    """Create one solid background canvas."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    ns_color(background_color).setFill()
    NSBezierPath.fillRect_(NSMakeRect(0.0, 0.0, width, height))
    image.unlockFocus()
    return image


def aspect_fit_rect(src_width: float, src_height: float, max_width: float, max_height: float) -> tuple[float, float]:
    """Return one aspect-fit size."""
    if src_width <= 0 or src_height <= 0 or max_width <= 0 or max_height <= 0:
        return max(1.0, max_width), max(1.0, max_height)
    scale = min(max_width / src_width, max_height / src_height)
    return max(1.0, src_width * scale), max(1.0, src_height * scale)


def render_image_to_canvas(image, canvas_width: float, canvas_height: float, background_color: tuple[float, float, float, float]):
    """Render one image aspect-fit onto a background canvas."""
    canvas = make_blank_canvas(canvas_width, canvas_height, background_color)
    image_width, image_height = image_size_tuple(image)
    draw_width, draw_height = aspect_fit_rect(image_width, image_height, canvas_width, canvas_height)
    draw_x = (canvas_width - draw_width) / 2.0
    draw_y = (canvas_height - draw_height) / 2.0
    canvas.lockFocus()
    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(draw_x, draw_y, draw_width, draw_height),
        NSMakeRect(0.0, 0.0, image_width, image_height),
        NSCompositingOperationSourceOver,
        1.0,
    )
    canvas.unlockFocus()
    return canvas


def create_wipe_frame(previous_image, next_image, progress: float, canvas_width: float, canvas_height: float, background_color):
    """Create one left-to-right wipe frame."""
    base = render_image_to_canvas(previous_image, canvas_width, canvas_height, background_color)
    overlay = render_image_to_canvas(next_image, canvas_width, canvas_height, background_color)
    reveal_width = max(1.0, min(canvas_width, canvas_width * progress))
    base.lockFocus()
    overlay.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0.0, 0.0, reveal_width, canvas_height),
        NSMakeRect(0.0, 0.0, reveal_width, canvas_height),
        NSCompositingOperationSourceOver,
        1.0,
    )
    base.unlockFocus()
    return base


def draw_quad_dividers(canvas, canvas_width: float, canvas_height: float) -> None:
    """Draw fine white divider lines for the quad layout."""
    canvas.lockFocus()
    ns_color(COLOR_NAMES["white"]).setStroke()
    vertical = NSBezierPath.bezierPath()
    vertical.moveToPoint_((canvas_width / 2.0, 0.0))
    vertical.lineToPoint_((canvas_width / 2.0, canvas_height))
    vertical.setLineWidth_(1.0)
    vertical.stroke()
    horizontal = NSBezierPath.bezierPath()
    horizontal.moveToPoint_((0.0, canvas_height / 2.0))
    horizontal.lineToPoint_((canvas_width, canvas_height / 2.0))
    horizontal.setLineWidth_(1.0)
    horizontal.stroke()
    canvas.unlockFocus()


def create_quad_canvas(image, canvas, canvas_width: float, canvas_height: float, quad_index: int, background_color):
    """Render one photo into one screen quadrant on an existing canvas."""
    half_width = canvas_width / 2.0
    half_height = canvas_height / 2.0
    margin = 2.0
    quadrant_rects = [
        (0.0, half_height, half_width, half_height),
        (half_width, half_height, half_width, half_height),
        (half_width, 0.0, half_width, half_height),
        (0.0, 0.0, half_width, half_height),
    ]
    rect_x, rect_y, rect_width, rect_height = quadrant_rects[quad_index % 4]
    image_width, image_height = image_size_tuple(image)
    draw_width, draw_height = aspect_fit_rect(
        image_width,
        image_height,
        max(1.0, rect_width - 2.0 * margin),
        max(1.0, rect_height - 2.0 * margin),
    )
    draw_x = rect_x + (rect_width - draw_width) / 2.0
    draw_y = rect_y + (rect_height - draw_height) / 2.0
    canvas.lockFocus()
    ns_color(background_color).setFill()
    NSBezierPath.fillRect_(NSMakeRect(rect_x, rect_y, rect_width, rect_height))
    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(draw_x, draw_y, draw_width, draw_height),
        NSMakeRect(0.0, 0.0, image_width, image_height),
        NSCompositingOperationSourceOver,
        1.0,
    )
    canvas.unlockFocus()
    draw_quad_dividers(canvas, canvas_width, canvas_height)
    return canvas, NSMakeRect(draw_x, draw_y, draw_width, draw_height)


def create_collage_canvas(
    image,
    canvas,
    canvas_width: float,
    canvas_height: float,
    size_min: float,
    size_max: float,
    slot_index: int,
    rotate_item: bool = True,
):
    """Render one photo as a framed, rotated collage item on an existing canvas."""
    image_width, image_height = image_size_tuple(image)
    screen_scale = random.uniform(size_min, size_max)
    max_width = canvas_width * screen_scale
    max_height = canvas_height * screen_scale
    draw_width, draw_height = aspect_fit_rect(image_width, image_height, max_width, max_height)
    frame_size = min(draw_width, draw_height) * 0.03
    framed_width = draw_width + frame_size * 2.0
    framed_height = draw_height + frame_size * 2.0
    angle_deg = random.uniform(-12.0, 12.0) if rotate_item else 0.0
    angle_rad = math.radians(angle_deg)
    bbox_width = abs(framed_width * math.cos(angle_rad)) + abs(framed_height * math.sin(angle_rad))
    bbox_height = abs(framed_width * math.sin(angle_rad)) + abs(framed_height * math.cos(angle_rad))
    max_bbox_width = max(1.0, canvas_width - 4.0)
    max_bbox_height = max(1.0, canvas_height - 4.0)
    if bbox_width > max_bbox_width or bbox_height > max_bbox_height:
        fit_scale = min(max_bbox_width / bbox_width, max_bbox_height / bbox_height)
        draw_width *= fit_scale
        draw_height *= fit_scale
        frame_size *= fit_scale
        framed_width = draw_width + frame_size * 2.0
        framed_height = draw_height + frame_size * 2.0
        bbox_width = abs(framed_width * math.cos(angle_rad)) + abs(framed_height * math.sin(angle_rad))
        bbox_height = abs(framed_width * math.sin(angle_rad)) + abs(framed_height * math.cos(angle_rad))
    boxes = [
        (0.0, canvas_height / 2.0, canvas_width / 2.0, canvas_height / 2.0),
        (canvas_width / 2.0, canvas_height / 2.0, canvas_width / 2.0, canvas_height / 2.0),
        (canvas_width / 2.0, 0.0, canvas_width / 2.0, canvas_height / 2.0),
        (0.0, 0.0, canvas_width / 2.0, canvas_height / 2.0),
        (canvas_width / 4.0, canvas_height / 4.0, canvas_width / 2.0, canvas_height / 2.0),
    ]
    box_x, box_y, box_width, box_height = boxes[slot_index % len(boxes)]
    desired_center_x = random.uniform(box_x, box_x + box_width)
    desired_center_y = random.uniform(box_y, box_y + box_height)
    min_center_x = bbox_width / 2.0
    max_center_x = canvas_width - bbox_width / 2.0
    min_center_y = bbox_height / 2.0
    max_center_y = canvas_height - bbox_height / 2.0
    center_x = min(max(desired_center_x, min_center_x), max_center_x)
    center_y = min(max(desired_center_y, min_center_y), max_center_y)

    canvas.lockFocus()
    transform = NSAffineTransform.transform()
    transform.translateXBy_yBy_(center_x, center_y)
    transform.rotateByDegrees_(angle_deg)
    transform.concat()

    ns_color(COLOR_NAMES["white"]).setFill()
    frame_rect = NSMakeRect(-framed_width / 2.0, -framed_height / 2.0, framed_width, framed_height)
    shadow_width = max(3.0, frame_size * 0.45)
    shadow_offset = max(2.0, frame_size * 0.30)
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.15, 0.15, 0.15, 0.28).setFill()
    NSBezierPath.fillRect_(
        NSMakeRect(framed_width / 2.0 - shadow_width + shadow_offset, -framed_height / 2.0 - shadow_offset, shadow_width, framed_height)
    )
    NSBezierPath.fillRect_(
        NSMakeRect(-framed_width / 2.0 + shadow_offset, -framed_height / 2.0 - shadow_offset, framed_width, shadow_width)
    )
    ns_color(COLOR_NAMES["white"]).setFill()
    NSBezierPath.fillRect_(frame_rect)

    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(-draw_width / 2.0, -draw_height / 2.0, draw_width, draw_height),
        NSMakeRect(0.0, 0.0, image_width, image_height),
        NSCompositingOperationSourceOver,
        1.0,
    )
    canvas.unlockFocus()
    media_rect = NSMakeRect(center_x - draw_width / 2.0, center_y - draw_height / 2.0, draw_width, draw_height)
    return canvas, media_rect


def resolve_preview_photo_from_line(base_dir: Path, line: str) -> Optional[Path]:
    """Resolve the first previewable photo path from one playlist line."""
    if line.startswith("#"):
        return None
    entry = parse_photo_entry(line)
    candidate = resolve_path(base_dir, entry.source_name)
    if candidate.suffix.lower() == ".json":
        try:
            candidate, _metadata = resolve_photo_from_json(base_dir, candidate)
        except Exception:
            return None
    return candidate if candidate.is_file() else None


def resolve_intro_title_image(
    configured_path: Optional[Path],
    media_dir: Path,
    playlist_lines: list[str],
) -> Optional[Path]:
    """Return the configured title image or the first available still image."""
    if configured_path is not None:
        candidate = Path(configured_path).expanduser().resolve(strict=False)
        if candidate.is_file() and not is_video_path(candidate):
            return candidate
    fallback_media = None
    for line in playlist_lines:
        candidate = resolve_preview_photo_from_line(media_dir, line)
        if candidate is None:
            continue
        if fallback_media is None:
            fallback_media = candidate
        if not is_video_path(candidate):
            return candidate
    return fallback_media


def intro_metadata_from_playlist(lines: list[str]) -> dict[str, Optional[str]]:
    """Collect compact date/place information without reading media again."""
    parsed_dates: list[datetime] = []
    places: list[str] = []
    for line in lines:
        content = line.strip()
        if content.startswith("#Datum:"):
            value = content.partition(":")[2].strip()
            date_part = value.partition(",")[2].strip() if "," in value else value
            try:
                parsed_dates.append(datetime.strptime(date_part, "%d.%m.%Y"))
            except ValueError:
                pass
        elif content and not content.startswith("#"):
            place = parse_photo_entry(content).place
            if isinstance(place, str):
                cleaned = place.strip()
                if cleaned and cleaned.casefold() not in {"kein ort", "unknown"}:
                    places.append(cleaned.split(",", 1)[0].strip())
    date_range = None
    if parsed_dates:
        first_date, last_date = min(parsed_dates), max(parsed_dates)
        date_range = first_date.strftime("%d.%m.%Y")
        if last_date.date() != first_date.date():
            date_range += f" - {last_date.strftime('%d.%m.%Y')}"
    return {
        "date_range": date_range,
        "first_place": places[0] if places else None,
        "last_place": places[-1] if places else None,
    }


def stage_name_endpoints(value: object) -> tuple[Optional[str], Optional[str]]:
    """Extract journey endpoints from a generated stage name."""
    name = str(value or "").strip()
    if " - " not in name:
        return None, None
    start, destination = (part.strip() for part in name.split(" - ", 1))
    if start.startswith("JW "):
        start = start[3:].strip()
    return start or None, destination or None


def wrap_text_for_width(text: str, font, maximum_width: float) -> list[str]:
    """Wrap text by measured Cocoa width while retaining explicit paragraphs."""
    lines: list[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            width = NSString.stringWithString_(candidate).sizeWithAttributes_(
                {NSFontAttributeName: font}
            ).width
            if width <= maximum_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_intro_information_panel(
    background_image,
    title: str,
    description: str,
    summary_lines: list[str],
    config: Config,
    title_image=None,
):
    """Draw the readable first Intro phase over the Tour Overview."""
    result = copy_image(background_image)
    width, height = image_size_tuple(result)
    panel_width = width * 0.80
    padding = max(18.0, min(width, height) * 0.025)
    usable_width = panel_width - 2.0 * padding
    maximum_panel_height = height * 0.80
    title_image_width = 0.0
    title_image_height = 0.0
    if title_image is not None:
        source_width, source_height = image_size_tuple(title_image)
        title_image_width, title_image_height = aspect_fit_rect(
            source_width,
            source_height,
            width * 0.35,
            height * 0.35,
        )
    title_font = NSFont.boldSystemFontOfSize_(max(28.0, float(config.font_size) * 1.55))
    summary_font = NSFont.boldSystemFontOfSize_(max(17.0, float(config.font_size) * 0.78))
    title_height = max(
        title_font.pointSize() * 1.20,
        NSString.stringWithString_(title or "Adventure").sizeWithAttributes_(
            {NSFontAttributeName: title_font}
        ).height,
    )
    summary_line_height = summary_font.pointSize() * 1.35
    section_gap = max(10.0, padding * 0.55)
    description_font_size = max(14.0, float(config.font_size) * 0.68)
    description_lines: list[str] = []
    description_font = NSFont.systemFontOfSize_(description_font_size)
    while description_font_size >= 11.0:
        description_font = NSFont.systemFontOfSize_(description_font_size)
        description_lines = (
            wrap_text_for_width(
                description,
                description_font,
                usable_width,
            )
            if str(description or "").strip()
            else []
        )
        description_line_height = description_font_size * 1.28
        desired_height = (
            2.0 * padding
            + title_height
            + len(summary_lines) * summary_line_height
            + (section_gap if summary_lines else 0.0)
            + len(description_lines) * description_line_height
            + (section_gap if description_lines else 0.0)
            + title_image_height
            + (section_gap if title_image_height > 0.0 else 0.0)
        )
        if desired_height <= maximum_panel_height:
            break
        description_font_size -= 1.0
    description_line_height = description_font_size * 1.28
    fixed_height = (
        2.0 * padding
        + title_height
        + len(summary_lines) * summary_line_height
        + (section_gap if summary_lines else 0.0)
        + (section_gap if description_lines else 0.0)
        + title_image_height
        + (section_gap if title_image_height > 0.0 else 0.0)
    )
    maximum_description_lines = max(
        0,
        int(max(0.0, maximum_panel_height - fixed_height) // description_line_height),
    )
    if len(description_lines) > maximum_description_lines:
        description_lines = description_lines[:maximum_description_lines]
        if description_lines:
            description_lines[-1] = description_lines[-1].rstrip(" .") + "..."
    panel_height = min(
        maximum_panel_height,
        fixed_height + len(description_lines) * description_line_height,
    )
    panel = NSMakeRect(
        (width - panel_width) / 2.0,
        max(0.0, height * 0.90 - panel_height),
        panel_width,
        panel_height,
    )
    result.lockFocus()
    try:
        title_y = height * 0.90 - title_height
        intro_text_color = COLOR_NAMES["black"]
        intro_shadow_color = (1.0, 1.0, 1.0, 0.72)
        draw_shadowed_text(
            title or "Adventure",
            panel.origin.x + panel.size.width / 2.0,
            title_y,
            title_font,
            intro_text_color,
            shadow_color=intro_shadow_color,
        )
        cursor_y = title_y
        if summary_lines:
            cursor_y -= section_gap
        for line in summary_lines:
            cursor_y -= summary_line_height
            draw_shadowed_text(
                line,
                panel.origin.x + panel.size.width / 2.0,
                cursor_y,
                summary_font,
                intro_text_color,
                shadow_color=intro_shadow_color,
            )

        if description_lines:
            cursor_y -= section_gap
        centered_description = len(description_lines) < 5
        for line in description_lines:
            cursor_y -= description_line_height
            if centered_description:
                draw_shadowed_text(
                    line,
                    panel.origin.x + panel.size.width / 2.0,
                    cursor_y,
                    description_font,
                    intro_text_color,
                    shadow_color=intro_shadow_color,
                )
            else:
                draw_shadowed_text(
                    line,
                    panel.origin.x + padding,
                    cursor_y,
                    description_font,
                    intro_text_color,
                    alignment="left",
                    shadow_color=intro_shadow_color,
                )
        if title_image is not None and title_image_height > 0.0:
            cursor_y -= section_gap + title_image_height
            context = NSGraphicsContext.currentContext()
            context.saveGraphicsState()
            try:
                shadow = NSShadow.alloc().init()
                shadow.setShadowColor_(
                    NSColor.colorWithSRGBRed_green_blue_alpha_(
                        0.0,
                        0.0,
                        0.0,
                        0.62,
                    )
                )
                shadow.setShadowOffset_(
                    NSMakeSize(
                        max(4.0, width * 0.006),
                        -max(4.0, width * 0.006),
                    )
                )
                shadow.setShadowBlurRadius_(max(8.0, width * 0.012))
                shadow.set()
                title_image.drawInRect_fromRect_operation_fraction_(
                    NSMakeRect(
                        panel.origin.x + (panel.size.width - title_image_width) / 2.0,
                        cursor_y,
                        title_image_width,
                        title_image_height,
                    ),
                    NSMakeRect(
                        0.0,
                        0.0,
                        image_size_tuple(title_image)[0],
                        image_size_tuple(title_image)[1],
                    ),
                    NSCompositingOperationSourceOver,
                    1.0,
                )
            finally:
                context.restoreGraphicsState()
    finally:
        result.unlockFocus()
    return result


def draw_overview_overlay(
    overview_image,
    overview_metadata: dict,
    track_metadata: dict,
    date_text: Optional[str],
    config: Config,
    *,
    header_background: bool = True,
    show_caption: bool = True,
):
    """Return the overview map with endpoints and date drawn on top."""
    debug_print(config, "Drawing overview overlay with track endpoints and date")
    result = copy_image(overview_image)
    width, height = image_size_tuple(result)
    result.lockFocus()

    scene = scene_from_metadata(track_metadata, show_header=False)
    projected_segments: list[list[tuple[float, float]]] = []
    for segment in scene.segments:
        projected = []
        for point in segment:
            try:
                pixel_x, pixel_y = coordinate_to_pixel(
                    point.latitude,
                    point.longitude,
                    overview_metadata,
                )
                projected.append(
                    scale_metadata_pixel_to_image(
                        pixel_x,
                        pixel_y,
                        overview_metadata,
                        result,
                    )
                )
            except Exception:
                continue
        if projected:
            projected_segments.append(projected)

    if scene.stage_kind == "media_stage":
        for segment in projected_segments:
            for pixel_x, pixel_y in segment:
                draw_marker_at(
                    pixel_x,
                    pixel_y,
                    height,
                    config.dot_color,
                    max(1, int(round(config.media_point_size / 2.0))),
                    COLOR_NAMES["white"],
                )
    else:
        for segment in projected_segments:
            if len(segment) < 2:
                continue
            path = NSBezierPath.bezierPath()
            configure_round_stroke(path)
            path.moveToPoint_((segment[0][0], height - segment[0][1]))
            for pixel_x, pixel_y in segment[1:]:
                path.lineToPoint_((pixel_x, height - pixel_y))
            ns_color(COLOR_NAMES["black"]).setStroke()
            path.setLineWidth_(max(5.0, width * 0.0055))
            path.stroke()
            ns_color(COLOR_NAMES["yellow"]).setStroke()
            path.setLineWidth_(max(3.0, width * 0.0035))
            path.stroke()

    start_point = track_metadata.get("start_point", track_metadata.get("first_point"))
    end_point = track_metadata.get("end_point", track_metadata.get("last_point"))
    if start_point is None and end_point is None:
        first_media_point = first_media_coordinate(track_metadata)
        if first_media_point is not None:
            start_point = {
                "latitude": first_media_point[0],
                "longitude": first_media_point[1],
            }
    point_pixels = []
    for point in (start_point or {}, end_point or {}):
        lat, lon = extract_coordinate_point(point)
        if lat is None or lon is None:
            debug_print(config, f"Skipping endpoint marker with unsupported point metadata: {point!r}")
            point_pixels.append(None)
            continue
        pixel_x, pixel_y = coordinate_to_pixel(lat, lon, overview_metadata)
        pixel_x, pixel_y = scale_metadata_pixel_to_image(pixel_x, pixel_y, overview_metadata, result)
        point_pixels.append((pixel_x, pixel_y))

    if scene.stage_kind == "media_stage" and point_pixels[0] is not None:
        diagonal_tangent = (math.sqrt(0.5), -math.sqrt(0.5))
        draw_open_arrow_at_marker(
            point_pixels[0][0],
            point_pixels[0][1],
            height,
            diagonal_tangent,
            config.dot_color,
            config.dot_size,
            config.arrow_length,
        )
    elif point_pixels[0] is not None and point_pixels[1] is not None:
        draw_arrow_line(
            point_pixels[0][0],
            point_pixels[0][1],
            point_pixels[1][0],
            point_pixels[1][1],
            height,
            config.dot_color,
            max(1.0, config.dot_size / 2.0),
        )

    marker_pixels = point_pixels[:1] if scene.stage_kind == "media_stage" else point_pixels
    for point_pixel in marker_pixels:
        if point_pixel is None:
            continue
        draw_marker_at(
            point_pixel[0],
            point_pixel[1],
            height,
            config.dot_color,
            config.dot_size,
        )

    caption_lines = []
    track_name = track_display_title(
        track_metadata,
        getattr(config, "track_title_mode", "endpoint_places"),
    )
    track_duration = track_metadata.get("track_duration")
    if isinstance(track_name, str) and track_name.strip():
        caption = track_name.strip()
        formatted_duration = format_overview_duration(track_duration)
        if formatted_duration:
            caption = f"{caption} ({formatted_duration})"
        caption_lines.append(caption)
    if date_text:
        caption_lines.append(date_text)

    if show_caption and caption_lines:
        debug_print(config, f"Drawing overview caption lines: {caption_lines}")
        stage_header = overview_metadata.get("stage_header_box_fraction")
        if isinstance(stage_header, dict):
            try:
                header_bottom = height * float(stage_header["bottom"])
                header_height = height * float(stage_header["height"])
            except (KeyError, TypeError, ValueError):
                header_bottom = height * 0.88
                header_height = height * 0.12
        else:
            header_bottom = height * 0.88
            header_height = height * 0.12
        requested_font_size = float(config.font_size)
        provisional_outline = max(1.0, requested_font_size / 12.0)
        padding = max(provisional_outline + 2.0, header_height * 0.06)
        usable_height = max(1.0, header_height - 2.0 * padding)
        font_size = max(
            8.0,
            min(
                requested_font_size,
                usable_height / (len(caption_lines) + 0.25),
            ),
        )
        font = NSFont.fontWithName_size_("Arial Bold", font_size) or NSFont.boldSystemFontOfSize_(font_size)
        outline_width = max(1.0, font_size / 12.0)
        padding = max(outline_width + 2.0, header_height * 0.06)
        usable_height = max(1.0, header_height - 2.0 * padding)
        line_height = usable_height / len(caption_lines)
        if header_background:
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.62).setFill()
            NSBezierPath.fillRect_(
                NSMakeRect(0.0, header_bottom, width, header_height)
            )
        for index, line in enumerate(caption_lines):
            text_size = NSString.stringWithString_(line).sizeWithAttributes_(
                {NSFontAttributeName: font}
            )
            row_bottom = (
                header_bottom
                + padding
                + (len(caption_lines) - index - 1) * line_height
            )
            text_y = row_bottom + max(0.0, (line_height - text_size.height) / 2.0)
            draw_outlined_text(
                line,
                width / 2.0,
                text_y,
                font,
                config.font_color,
                COLOR_NAMES["black"],
                outline_width,
            )

    result.unlockFocus()
    debug_print(config, "Finished overview overlay drawing")
    return result


def draw_dynamic_map_overlay(
    image,
    metadata: Optional[dict],
    config: Config,
    *,
    show_header: Optional[bool] = None,
    reserve_place_row: bool = False,
    omit_track_date: bool = False,
    relation_title: Optional[str] = None,
    omit_media_date: bool = False,
):
    """Compose route/media geometry and headers for a background-only map."""
    if image is None or not map_uses_dynamic_overlays(metadata):
        return image
    scene = scene_from_metadata(
        metadata,
        gpx_mode=config.gpx_overlay_mode,
        media_mode=config.media_overlay_mode,
        show_header=(
            config.dynamic_map_header
            if show_header is None
            else bool(show_header)
        ),
    )
    result = copy_image(image)
    width, height = image_size_tuple(result)
    projected_segments = []
    for segment in scene.segments:
        projected = []
        for point in segment:
            try:
                pixel_x, pixel_y = coordinate_to_pixel(point.latitude, point.longitude, metadata)
                pixel_x, pixel_y = scale_metadata_pixel_to_image(pixel_x, pixel_y, metadata, result)
            except Exception:
                continue
            projected.append((pixel_x, pixel_y))
        if projected:
            if scene.stage_kind == "overview":
                projected = simplify_display_path(projected)
            projected_segments.append(projected)

    result.lockFocus()
    try:
        if scene.mode in {"line", "interpolated"}:
            route_color = config.media_point_color if scene.stage_kind == "media_stage" else config.route_color
            for segment in projected_segments:
                if len(segment) < 2:
                    continue
                path = NSBezierPath.bezierPath()
                configure_round_stroke(path)
                path.moveToPoint_((segment[0][0], height - segment[0][1]))
                for pixel_x, pixel_y in segment[1:]:
                    path.lineToPoint_((pixel_x, height - pixel_y))
                ns_color(COLOR_NAMES["black"]).setStroke()
                path.setLineWidth_(max(1.0, config.route_width + 2.0))
                path.stroke()
                ns_color(route_color).setStroke()
                path.setLineWidth_(max(0.5, config.route_width))
                path.stroke()

        if scene.stage_kind == "media_stage" and scene.mode in {"dots", "interpolated"}:
            for segment in projected_segments:
                for pixel_x, pixel_y in segment:
                    draw_marker_at(
                        pixel_x,
                        pixel_y,
                        height,
                        config.media_point_color,
                        max(1.0, config.media_point_size / 2.0),
                        COLOR_NAMES["white"],
                    )
        elif scene.mode == "line" and config.endpoint_size > 0 and scene.stage_kind == "gpx_track":
            points = [point for segment in projected_segments for point in segment]
            if points:
                for pixel_x, pixel_y in (points[0], points[-1]):
                    draw_marker_at(
                        pixel_x,
                        pixel_y,
                        height,
                        config.endpoint_color,
                        max(1.0, config.endpoint_size / 2.0),
                        config.route_color,
                    )

        if scene.stage_kind == "overview" and scene.mode == "line" and isinstance(metadata, dict):
            map_font_size = max(8.0, 14.0 * float(config.map_header_font_factor))
            label_font = NSFont.boldSystemFontOfSize_(max(8.0, map_font_size * 0.55))
            for item in metadata.get("overview_dynamic_labels", []):
                if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                    continue
                try:
                    pixel_x, pixel_y = coordinate_to_pixel(float(item["lat"]), float(item["lon"]), metadata)
                    pixel_x, pixel_y = scale_metadata_pixel_to_image(pixel_x, pixel_y, metadata, result)
                except (KeyError, TypeError, ValueError):
                    continue
                draw_shadowed_text(
                    str(item["text"]),
                    pixel_x,
                    height - pixel_y,
                    label_font,
                    config.map_header_color,
                    "center",
                )

        header_lines = (
            dynamic_stage_header_lines(
                metadata,
                config,
                relation_title=relation_title,
                clock_visible=(
                    omit_track_date
                    if scene.stage_kind == "gpx_track"
                    else omit_media_date
                ),
            )
            if scene.header_lines
            else ()
        )
        if header_lines:
            title_box = (
                metadata.get("overview_title_box_fraction")
                if scene.stage_kind == "overview" and isinstance(metadata, dict)
                else None
            )
            if isinstance(title_box, dict):
                try:
                    header_bottom = height * float(title_box["bottom"])
                    header_height = height * float(title_box["height"])
                except (KeyError, TypeError, ValueError):
                    header_bottom, header_height = height * 0.88, height * 0.12
            else:
                axes = metadata.get("axes_box_fraction") if isinstance(metadata, dict) else None
                try:
                    header_bottom = height * (float(axes["bottom"]) + float(axes["height"]))
                except (KeyError, TypeError, ValueError):
                    header_bottom = height * 0.88
                header_height = max(1.0, height - header_bottom)
            line_count = max(1, len(header_lines))
            row_count = (
                line_count + 1
                if reserve_place_row and scene.stage_kind != "overview"
                else line_count
            )
            requested_font_size = max(9.0, 14.0 * float(config.map_header_font_factor))
            shadow_margin = max(2.0, requested_font_size / 10.0)
            header_padding = max(shadow_margin, header_height * 0.055)
            usable_height = max(1.0, header_height - 2.0 * header_padding)
            font_size = max(
                8.0,
                min(requested_font_size, usable_height / (row_count + 0.35)),
            )
            line_height = usable_height / row_count
            header_top = header_bottom + header_height - header_padding
            for index, line in enumerate(header_lines):
                font = NSFont.boldSystemFontOfSize_(font_size if index == 0 else max(8.0, font_size * 0.82))
                text_size = NSString.stringWithString_(line).sizeWithAttributes_({NSFontAttributeName: font})
                baseline = (
                    header_top
                    - (index + 1) * line_height
                    + max(0.0, (line_height - text_size.height) / 2.0)
                )
                draw_shadowed_text(
                    line,
                    width / 2.0,
                    baseline,
                    font,
                    config.map_header_color,
                    "center",
                )
    finally:
        result.unlockFocus()
    return result


def first_media_coordinate(metadata: object) -> Optional[tuple[float, float]]:
    """Return the first stored media coordinate for a media-only stage."""
    scene = scene_from_metadata(metadata, show_header=False)
    if scene.stage_kind != "media_stage":
        return None
    for point in scene.points:
        return point.latitude, point.longitude
    return None


def draw_relation_title_on_image(
    image,
    metadata: Optional[dict],
    relation_title: Optional[str],
    config: Config,
):
    """Draw the fixed adjacent-day title over a track map copy."""
    if not relation_title:
        return image
    result = copy_image(image)
    width, height = image_size_tuple(result)
    result.lockFocus()
    try:
        title_x, title_y, title_width, title_height = relation_title_band(
            (0.0, 0.0, width, height),
            metadata,
        )
        font_size = max(6.0, min(float(config.font_size) * 1.05, title_height * 0.62))
        font = NSFont.boldSystemFontOfSize_(font_size)
        title_size = NSString.stringWithString_(relation_title).sizeWithAttributes_(
            {NSFontAttributeName: font}
        )
        draw_shadowed_text(
            relation_title,
            title_x + title_width / 2.0,
            title_y + max(0.0, (title_height - title_size.height) / 2.0),
            font,
            config.font_color,
            "center",
        )
    finally:
        result.unlockFocus()
    return result


def draw_time_lapse_overview_media(
    overview_image,
    overview_metadata: dict,
    track_metadata: dict,
    route_points: list[dict],
    date_text: Optional[str],
    config: Config,
):
    """Render the current stage route into an overview used as framed media."""
    del route_points
    return draw_overview_overlay(
        overview_image,
        overview_metadata,
        track_metadata,
        date_text,
        config,
        header_background=False,
        show_caption=False,
    )


def resolve_photo_from_json(base_dir: Path, json_path: Path) -> tuple[Path, dict]:
    """Resolve a photo sidecar JSON file to its image path and metadata."""
    metadata = read_photo_metadata(json_path)
    photo_path_value = metadata.get("photo_path")
    if isinstance(photo_path_value, str) and photo_path_value.strip():
        photo_path = resolve_path(base_dir, photo_path_value.strip())
    elif isinstance(metadata.get("source_filename"), str) and metadata["source_filename"].strip():
        photo_path = resolve_path(base_dir, metadata["source_filename"].strip())
    else:
        raise ValueError(f"photo sidecar JSON does not contain photo_path/source_filename: {json_path}")
    return photo_path, metadata


def build_photo_info_text(photo_path: Path, metadata_path: Optional[Path], metadata: dict, entry: PhotoListEntry) -> str:
    """Build one inspection overlay text block for a photo."""
    lines = [
        f"Media file: {photo_path.name}",
        f"Media path: {photo_path}",
    ]
    if metadata_path is not None:
        lines.append(f"JSON file: {metadata_path}")
    if entry.time_text:
        lines.append(f"Playlist time: {entry.time_text}")
    if entry.latitude is not None and entry.longitude is not None:
        lines.append(f"Playlist GPS: {entry.latitude:.6f}, {entry.longitude:.6f}")
    if entry.place:
        lines.append(f"Playlist place: {entry.place}")
    lines.append("")
    if metadata:
        lines.append("JSON metadata:")
        lines.extend(json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False).splitlines())
    else:
        lines.append("JSON metadata: unavailable")
    return "\n".join(lines)


def try_read_plot_metadata(path: Path) -> Optional[dict]:
    """Read plot metadata if present, otherwise warn and continue."""
    if not path.is_file():
        warn_message(f"plot metadata file not found: {path}")
        return None
    try:
        return read_plot_metadata(path)
    except Exception as exc:
        warn_message(f"failed to read plot metadata {path}: {exc}")
        return None


def try_read_photo_metadata(path: Path, media_path: Optional[Path] = None) -> Optional[dict]:
    """Read photo metadata if present, otherwise warn and continue."""
    if not path.is_file():
        warn_message(f"photo metadata file not found: {path}")
        return None
    try:
        metadata = read_photo_metadata(path)
    except Exception as exc:
        warn_message(f"failed to read photo metadata {path}: {exc}")
        return None
    if media_path is not None and not media_sidecar_matches_media(metadata, media_path):
        warn_message(f"ignoring sidecar with a different media owner: {path}")
        return None
    return metadata


def debug_print(config: Config, message: str) -> None:
    """Print one debug line when debugging is enabled."""
    if config.debug:
        print(f"[GPSTrackShow] {message}", flush=True)


def debug_exception(config: Optional[Config], context: str, exc: BaseException) -> None:
    """Print one exception with traceback."""
    prefix = "[GPSTrackShow]"
    print(f"{prefix} ERROR in {context}: {exc}", file=sys.stderr, flush=True)
    traceback.print_exc()
    if config is not None and config.debug:
        print(f"{prefix} Traceback above came from {context}", file=sys.stderr, flush=True)


def warn_message(message: str) -> None:
    """Print one warning line."""
    print(f"[GPSTrackShow] WARNING: {message}", file=sys.stderr, flush=True)


def format_memory_size(byte_count: Optional[int]) -> str:
    """Format a byte count for the on-screen memory monitor."""
    if byte_count is None or byte_count < 0:
        return "unavailable"
    return f"{byte_count / GIBIBYTE:.2f} GB"


def physical_memory_bytes() -> Optional[int]:
    """Return installed physical memory when the platform exposes it."""
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
    total = page_size * page_count
    return total if total > 0 else None


def memory_watchdog_limits() -> tuple[int, int]:
    """Return warning and stop thresholds, scaled to the current Mac."""
    total = physical_memory_bytes() or 16 * GIBIBYTE
    warning = int(min(3.0 * GIBIBYTE, max(1.5 * GIBIBYTE, total * 0.18)))
    critical = int(min(4.0 * GIBIBYTE, max(2.5 * GIBIBYTE, total * 0.25)))
    return min(warning, critical - 256 * 1024**2), critical


def current_process_resident_bytes() -> Optional[int]:
    """Return current RSS, with a conservative peak-RSS fallback."""
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
        text = result.stdout.strip()
        kibibytes = int(text.split()[0])
        return kibibytes * 1024 if kibibytes >= 0 else None
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        # Sandboxed or managed launches can deny ps.  Darwin's ru_maxrss is in
        # bytes and provides a safe high-water mark for the emergency cutoff.
        try:
            peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except (AttributeError, OSError, ValueError):
            return None
        if peak_rss < 0:
            return None
        return peak_rss if sys.platform == "darwin" else peak_rss * 1024


def create_debug_test_image(width: float = 1200.0, height: float = 800.0):
    """Create a generated startup test image."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    ns_color(COLOR_NAMES["blue"]).setFill()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, width, height))

    ns_color(COLOR_NAMES["yellow"]).setFill()
    NSBezierPath.fillRect_(NSMakeRect(0, height * 0.65, width, height * 0.35))

    ns_color(COLOR_NAMES["red"]).setStroke()
    diagonal_a = NSBezierPath.bezierPath()
    diagonal_a.moveToPoint_((0.0, 0.0))
    diagonal_a.lineToPoint_((width, height))
    diagonal_a.setLineWidth_(8.0)
    diagonal_a.stroke()

    ns_color(COLOR_NAMES["white"]).setStroke()
    diagonal_b = NSBezierPath.bezierPath()
    diagonal_b.moveToPoint_((0.0, height))
    diagonal_b.lineToPoint_((width, 0.0))
    diagonal_b.setLineWidth_(8.0)
    diagonal_b.stroke()

    outer = NSBezierPath.bezierPathWithRect_(NSMakeRect(30.0, 30.0, width - 60.0, height - 60.0))
    ns_color(COLOR_NAMES["black"]).setStroke()
    outer.setLineWidth_(10.0)
    outer.stroke()

    font_big = NSFont.boldSystemFontOfSize_(48.0)
    font_small = NSFont.systemFontOfSize_(28.0)
    big_attrs = {
        NSFontAttributeName: font_big,
        NSForegroundColorAttributeName: ns_color(COLOR_NAMES["black"]),
    }
    small_attrs = {
        NSFontAttributeName: font_small,
        NSForegroundColorAttributeName: ns_color(COLOR_NAMES["white"]),
    }

    title = NSString.stringWithString_("GPSTrackShow Debug")
    subtitle = NSString.stringWithString_("If you can read this, Cocoa image rendering works.")
    title_size = title.sizeWithAttributes_(big_attrs)
    subtitle_size = subtitle.sizeWithAttributes_(small_attrs)
    title.drawInRect_withAttributes_(
        NSMakeRect((width - title_size.width) / 2.0, height * 0.52, title_size.width, title_size.height),
        big_attrs,
    )
    subtitle.drawInRect_withAttributes_(
        NSMakeRect((width - subtitle_size.width) / 2.0, height * 0.42, subtitle_size.width, subtitle_size.height),
        small_attrs,
    )

    image.unlockFocus()
    return image


if APPKIT_AVAILABLE:

    class TimerTarget(NSObject):
        """Objective-C bridge target for one-shot NSTimer callbacks."""

        def initWithCallback_owner_token_(self, callback: Callable[[], None], owner, token: int):  # type: ignore[override]
            self = objc.super(TimerTarget, self).init()
            if self is None:
                return None
            self.callback = callback
            self.owner = owner
            self.token = token
            self.debug_config = None
            self.debug_context = getattr(callback, "__name__", repr(callback))
            return self

        def fire_(self, _timer) -> None:
            callback = self.callback
            self.callback = None
            try:
                if callback is not None:
                    callback()
            except BaseException as exc:  # pragma: no cover - GUI callback path
                debug_exception(self.debug_config, self.debug_context, exc)
                raise
            finally:
                owner = self.owner
                self.owner = None
                if owner is not None:
                    owner._release_timer_handle(self.token)


    class GPSTrackShowWindowDelegate(NSObject):
        """Window delegate for resize and close events."""

        def initWithController_role_(self, controller, role):  # type: ignore[override]
            self = objc.super(GPSTrackShowWindowDelegate, self).init()
            if self is None:
                return None
            self.controller = controller
            self.role = str(role)
            return self

        def windowWillClose_(self, notification) -> None:
            self.controller.window_will_close(notification.object(), self.role)

        def windowDidEnterFullScreen_(self, _notification) -> None:
            self.controller.window_did_enter_fullscreen(self.role)

        def windowDidExitFullScreen_(self, _notification) -> None:
            self.controller.window_did_exit_fullscreen(self.role)

        def windowDidResize_(self, _notification) -> None:
            self.controller.window_did_resize(self.role)


    class ScheduledCallback:
        """Small wrapper around NSTimer so callbacks can be cancelled."""

        def __init__(self, timer, target, owner, token: int):
            self.timer = timer
            self.target = target
            self.owner = owner
            self.token = token

        def cancel(self) -> None:
            if self.timer is not None:
                self.timer.invalidate()
            if self.owner is not None:
                self.owner._release_timer_handle(self.token)
            else:
                self.dispose()

        def dispose(self) -> None:
            """Drop Objective-C and Python callback references after a timer ends."""
            self.timer = None
            self.target = None
            self.owner = None


class CocoaImagePresenter:
    """Layered NSImageView presenter with transitions."""

    def __init__(self, host_view, background_color, schedule_callback, collage_size_min: float = 0.33, collage_size_max: float = 0.66, collage_max_images: int = 9, transition_duration_ms: int = TRANSITION_MS):
        self.host_view = host_view
        self.background_color = background_color
        self.schedule_callback = schedule_callback
        self.collage_size_min = collage_size_min
        self.collage_size_max = collage_size_max
        self.collage_max_images = collage_max_images
        self.transition_duration_ms = max(0, int(transition_duration_ms))
        self.pending_handles = []
        self.current_image = None
        self.help_visible = False
        self.clock_time: Optional[tuple[int, int]] = None
        self.clock_date_text: Optional[str] = None
        self.header_metadata: Optional[dict] = None
        self.header_lines: tuple[str, ...] = ()
        self.header_metrics: tuple[str, ...] = ()
        self.header_visible = False
        self.header_font_size = 30.0
        self.header_font_color = COLOR_NAMES["white"]
        self.header_shadow_color = COLOR_NAMES["black"]
        self.header_font_factor = 2.2
        self.header_background_style = "black"
        self.header_reference_image = None
        self.header_reference_fills_frame = False
        self.place_visible = False
        self.place_text: Optional[str] = None
        self.info_visible = False
        self.info_text: Optional[str] = None
        self.status_visible = False
        self.status_text: Optional[str] = None
        self.memory_visible = False
        self.memory_text: Optional[str] = None
        self.memory_warning = False
        self.quad_index = 0
        self.collage_slot_index = 0
        self.collage_count = 0
        self.layout_canvas = None
        self.layout_mode: Optional[Transition] = None
        self.last_media_rect = None
        self.video_view = None
        self.video_player = None
        self.video_uses_full_frame = False

        host_view.setWantsLayer_(True)
        host_view.layer().setBackgroundColor_(background_cgcolor(background_color))

        self.primary_view = self._make_image_view(host_view.bounds())
        self.overlay_view = self._make_image_view(host_view.bounds())
        self.header_view = self._make_image_view(host_view.bounds())
        self.clock_view = self._make_image_view(host_view.bounds())
        self.place_view = self._make_image_view(host_view.bounds())
        self.info_view = self._make_image_view(host_view.bounds())
        self.status_view = self._make_image_view(host_view.bounds())
        self.memory_view = self._make_image_view(host_view.bounds())
        self.startup_hint_view = self._make_image_view(host_view.bounds())
        self.help_view = self._make_image_view(host_view.bounds())
        self.fade_view = NSView.alloc().initWithFrame_(host_view.bounds())
        self.fade_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.fade_view.setWantsLayer_(True)
        self.fade_view.layer().setBackgroundColor_(background_cgcolor(background_color))
        self.fade_view.setAlphaValue_(0.0)

        host_view.addSubview_(self.primary_view)
        host_view.addSubview_(self.fade_view)
        host_view.addSubview_(self.overlay_view)
        host_view.addSubview_(self.header_view)
        host_view.addSubview_(self.clock_view)
        host_view.addSubview_(self.place_view)
        host_view.addSubview_(self.info_view)
        host_view.addSubview_(self.status_view)
        host_view.addSubview_(self.memory_view)
        host_view.addSubview_(self.startup_hint_view)
        host_view.addSubview_(self.help_view)
        self.overlay_view.setAlphaValue_(0.0)
        self.header_view.setAlphaValue_(0.0)
        self.clock_view.setAlphaValue_(0.0)
        self.place_view.setAlphaValue_(0.0)
        self.info_view.setAlphaValue_(0.0)
        self.status_view.setAlphaValue_(0.0)
        self.memory_view.setAlphaValue_(0.0)
        self.startup_hint_view.setAlphaValue_(0.0)
        self.help_view.setAlphaValue_(0.0)

    def _make_image_view(self, frame_rect):
        view = NSImageView.alloc().initWithFrame_(frame_rect)
        view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        view.setImageAlignment_(NSImageAlignCenter)
        view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        return view

    def cancel_pending(self, detach_video: bool = True) -> None:
        """Cancel pending animation callbacks."""
        while self.pending_handles:
            self.pending_handles.pop().cancel()
        self.stop_video(detach_view=detach_video)

    def set_content_visible(self, visible: bool) -> None:
        """Atomically show or hide every standard slide-show content layer."""
        hidden = not visible
        for view in (
            self.primary_view,
            self.overlay_view,
            self.header_view,
            self.clock_view,
            self.place_view,
            self.info_view,
            self.fade_view,
        ):
            view.setHidden_(hidden)
        if self.video_view is not None:
            self.video_view.setHidden_(hidden)

    def dispose(self) -> None:
        """Stop activity without tearing down AppKit views during window close."""
        self.cancel_pending(detach_video=False)
        for view_name in (
            "primary_view",
            "overlay_view",
            "header_view",
            "clock_view",
            "place_view",
            "info_view",
            "status_view",
            "memory_view",
            "startup_hint_view",
            "help_view",
            "fade_view",
        ):
            view = getattr(self, view_name, None)
            if view is None:
                continue
            try:
                if hasattr(view, "setImage_"):
                    view.setImage_(None)
            except Exception:
                pass
        self.current_image = None
        self.layout_canvas = None

    def stop_video(self, detach_view: bool = True) -> None:
        """Stop and remove any active AVPlayer view."""
        if self.video_player is not None:
            try:
                self.video_player.pause()
            except Exception:
                pass
            self.video_player = None
        self.video_original_path = None
        self.video_playback_path = None
        self.video_uses_full_frame = False
        if self.video_view is not None:
            try:
                if hasattr(self.video_view, "setPlayer_"):
                    self.video_view.setPlayer_(None)
                if detach_view:
                    self.video_view.removeFromSuperview()
            except Exception:
                pass
            if detach_view:
                self.video_view = None

    def _raise_overlay_views(self) -> None:
        """Keep controls and text overlays above an optional video layer."""
        for view in (
            self.overlay_view,
            self.header_view,
            self.clock_view,
            self.place_view,
            self.info_view,
            self.status_view,
            self.memory_view,
            self.startup_hint_view,
            self.help_view,
        ):
            view.removeFromSuperview()
            self.host_view.addSubview_(view)

    def play_video(
        self,
        video_path: Path,
        frame_rect=None,
        *,
        playback_path: Optional[Path] = None,
        volume: float = 1.0,
    ) -> None:
        """Play a video in the presenter after its still-frame transition."""
        self.stop_video()
        if not AVKIT_VIDEO_AVAILABLE or AVPlayer is None or AVPlayerView is None:
            warn_message(f"video playback is unavailable because AVKit bindings are missing: {video_path}")
            return
        self.video_uses_full_frame = frame_rect is None
        rect = frame_rect if frame_rect is not None else self._content_frame()
        actual_path = playback_path or video_path
        player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(actual_path)))
        player.setVolume_(max(0.0, min(1.0, float(volume))))
        view = AVPlayerView.alloc().initWithFrame_(rect)
        if frame_rect is None:
            view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        if hasattr(view, "setControlsStyle_"):
            view.setControlsStyle_(AVPlayerViewControlsStyleNone)
        if hasattr(view, "setShowsFullScreenToggleButton_"):
            view.setShowsFullScreenToggleButton_(False)
        if hasattr(view, "setAllowsPictureInPicturePlayback_"):
            view.setAllowsPictureInPicturePlayback_(False)
        view.setPlayer_(player)
        self.host_view.addSubview_(view)
        self.video_player = player
        self.video_view = view
        self.video_original_path = Path(video_path)
        self.video_playback_path = Path(actual_path)
        self._raise_overlay_views()
        player.play()

    def replace_video_source(self, playback_path: Path, volume: float = 1.0) -> bool:
        """Replace an active video while retaining position and play/pause state."""
        if self.video_player is None or self.video_view is None or not playback_path.is_file():
            return False
        old_player = self.video_player
        try:
            seconds = float(CMTimeGetSeconds(old_player.currentTime())) if CMTimeGetSeconds is not None else 0.0
            was_playing = float(old_player.rate()) > 0.0
        except Exception:
            seconds = 0.0
            was_playing = True
        old_player.pause()
        player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(playback_path)))
        player.setVolume_(max(0.0, min(1.0, float(volume))))
        if CMTimeMake is not None and math.isfinite(seconds) and seconds > 0.0:
            player.seekToTime_(CMTimeMake(int(round(seconds * 1000.0)), 1000))
        self.video_view.setPlayer_(player)
        self.video_player = player
        self.video_playback_path = Path(playback_path)
        if was_playing:
            player.play()
        return True

    def reset_photo_layout(self, mode: Optional[Transition] = None, preserve_current_image: bool = False) -> None:
        """Clear cumulative photo-layout state for collage/quad transitions."""
        self.layout_canvas = None
        self.last_media_rect = None
        if not preserve_current_image:
            self.current_image = None
        self.layout_mode = mode
        if mode == Transition.QUAD:
            self.quad_index = 0
        if mode == Transition.COLLAGE:
            self.collage_count = 0
        self.fade_view.setAlphaValue_(0.0)
        self.overlay_view.setAlphaValue_(0.0)

    def _base_canvas(self, mode: Transition):
        bounds = self._content_frame()
        needs_reset = (
            self.layout_canvas is None
            or self.layout_mode != mode
            or image_size_tuple(self.layout_canvas) != (bounds.size.width, bounds.size.height)
        )
        if needs_reset:
            self.layout_mode = mode
            self.layout_canvas = make_blank_canvas(bounds.size.width, bounds.size.height, self.background_color)
            if mode == Transition.QUAD:
                draw_quad_dividers(self.layout_canvas, bounds.size.width, bounds.size.height)
        return self.layout_canvas

    def _content_frame(self):
        """Return the current host coordinates available to media."""
        bounds = self.host_view.bounds()
        rect = header_content_rect(
            (0.0, 0.0, float(bounds.size.width), float(bounds.size.height)),
            self.header_metadata,
            self.header_background_style,
            self.header_visible,
        )
        return NSMakeRect(*rect)

    def _apply_header_layout(self) -> None:
        """Apply the selected full-frame or black-header media geometry."""
        frame = self._content_frame()
        for view in (self.primary_view, self.overlay_view, self.fade_view):
            view.setFrame_(frame)
        if self.video_view is not None and self.video_uses_full_frame:
            self.video_view.setFrame_(frame)

    def set_help_visible(self, visible: bool) -> None:
        """Show or hide the key-help overlay."""
        self.help_visible = visible
        if visible:
            bounds = self.host_view.bounds()
            overlay = create_help_overlay_image(bounds.size.width, bounds.size.height)
            self.help_view.setFrame_(bounds)
            self.help_view.setImage_(overlay)
            self.help_view.setAlphaValue_(1.0)
        else:
            self.help_view.setAlphaValue_(0.0)

    def set_startup_hint_visible(
        self,
        visible: bool,
        *,
        bottom: bool = False,
        wait_for_start: bool = False,
    ) -> None:
        """Show or hide the temporary startup hint."""
        if not visible:
            self.startup_hint_view.setAlphaValue_(0.0)
            self.startup_hint_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        self.startup_hint_view.setFrame_(bounds)
        self.startup_hint_view.setImage_(
            create_startup_hint_overlay_image(
                bounds.size.width,
                bounds.size.height,
                bottom=bottom,
                wait_for_start=wait_for_start,
            )
        )
        self.startup_hint_view.setAlphaValue_(1.0)

    def set_clock_time(self, clock_time: Optional[tuple[int, int]], clock_date_text: Optional[str] = None) -> None:
        """Show or hide the clock overlay in the top-left corner of the presenter."""
        self.clock_time = clock_time
        self.clock_date_text = clock_date_text
        if clock_time is None:
            self.clock_view.setAlphaValue_(0.0)
            self.clock_view.setImage_(None)
            return
        bounds = self._header_reference_rect()
        frame, clock_size = time_lapse_clock_layout(
            (
                float(bounds.origin.x),
                float(bounds.origin.y),
                float(bounds.size.width),
                float(bounds.size.height),
            ),
            self.header_metadata,
            bool(clock_date_text),
        )
        self.clock_view.setFrame_(
            NSMakeRect(
                frame[0],
                frame[1],
                frame[2],
                frame[3],
            )
        )
        self.clock_view.setImage_(
            create_clock_overlay_image(
                clock_size,
                clock_time[0],
                clock_time[1],
                clock_date_text,
                date_on_right=True,
                date_font_size=time_lapse_header_title_font_size(
                    (
                        float(bounds.origin.x),
                        float(bounds.origin.y),
                        float(bounds.size.width),
                        float(bounds.size.height),
                    ),
                    self.header_metadata,
                    self.header_font_factor,
                    3,
                ),
                font_color=self.header_font_color,
                shadow_color=self.header_shadow_color,
                background_style=self.header_background_style,
            )
        )
        self.clock_view.setAlphaValue_(1.0)

    def set_header_reference_image(self, image, fills_frame: bool = False) -> None:
        """Select the image whose fitted top edge anchors overlay headers."""
        self.header_reference_image = image
        self.header_reference_fills_frame = bool(fills_frame)

    def _header_reference_rect(self):
        bounds = self.host_view.bounds()
        if self.header_background_style == "black":
            return bounds
        frame = self._content_frame()
        if self.header_reference_fills_frame or self.header_reference_image is None:
            return frame
        image_width, image_height = image_size_tuple(self.header_reference_image)
        draw_width, draw_height = aspect_fit_rect(
            image_width,
            image_height,
            float(frame.size.width),
            float(frame.size.height),
        )
        return NSMakeRect(
            float(frame.origin.x) + (float(frame.size.width) - draw_width) / 2.0,
            float(frame.origin.y) + (float(frame.size.height) - draw_height) / 2.0,
            draw_width,
            draw_height,
        )

    def set_header(
        self,
        header_lines: tuple[str, ...],
        metrics_lines: tuple[str, ...],
        metadata: Optional[dict],
        visible: bool,
        font_size: float,
        font_color,
        font_factor: float,
        background_style: str,
        shadow_color,
    ) -> None:
        """Show the shared stage header over full-window media."""
        self.header_lines = tuple(header_lines)
        self.header_metrics = tuple(metrics_lines)
        self.header_metadata = metadata
        self.header_visible = bool(visible)
        self.header_font_size = float(font_size)
        self.header_font_color = font_color
        self.header_shadow_color = shadow_color
        self.header_font_factor = float(font_factor)
        self.header_background_style = str(background_style)
        self._apply_header_layout()
        if not visible or (not header_lines and not metrics_lines and background_style == "off"):
            self.header_view.setAlphaValue_(0.0)
            self.header_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        reference = self._header_reference_rect()
        self.header_view.setFrame_(bounds)
        self.header_view.setImage_(
            create_runtime_header_overlay_image(
                float(bounds.size.width),
                float(bounds.size.height),
                metadata,
                tuple(header_lines),
                tuple(metrics_lines),
                font_color,
                font_factor,
                font_size,
                background_style,
                self.background_color,
                shadow_color,
                (
                    float(reference.origin.x),
                    float(reference.origin.y),
                    float(reference.size.width),
                    float(reference.size.height),
                ),
            )
        )
        self.header_view.setAlphaValue_(1.0)

    def refresh_header_layout(self) -> None:
        """Rebuild retained header layers for the present host-view size."""
        self.set_header(
            self.header_lines,
            self.header_metrics,
            self.header_metadata,
            self.header_visible,
            self.header_font_size,
            self.header_font_color,
            self.header_font_factor,
            self.header_background_style,
            self.header_shadow_color,
        )
        self.set_clock_time(self.clock_time, self.clock_date_text)
        if self.help_visible:
            self.set_help_visible(True)

    def set_place_text(self, place_text: Optional[str], visible: bool, font_size: int, font_color) -> None:
        """Show or hide the place-name overlay near the top of the presenter."""
        self.place_text = place_text
        self.place_visible = visible
        if not visible or not place_text:
            self.place_view.setAlphaValue_(0.0)
            self.place_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        self.place_view.setFrame_(bounds)
        self.place_view.setImage_(
            create_place_overlay_image(
                bounds.size.width,
                bounds.size.height,
                place_text,
                font_size,
                font_color,
                self.header_metadata,
                self.header_font_factor,
                getattr(self, "header_background_style", "black"),
            )
        )
        self.place_view.setAlphaValue_(1.0)

    def set_info_text(self, info_text: Optional[str]) -> None:
        """Store info text and redraw the overlay if it is visible."""
        self.info_text = info_text
        if self.info_visible:
            self.set_info_visible(True)

    def set_info_visible(self, visible: bool) -> None:
        """Show or hide the metadata overlay."""
        self.info_visible = visible
        if not visible or not self.info_text:
            self.info_view.setAlphaValue_(0.0)
            self.info_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        self.info_view.setFrame_(bounds)
        self.info_view.setImage_(create_info_overlay_image(bounds.size.width, bounds.size.height, self.info_text))
        self.info_view.setAlphaValue_(1.0)

    def set_status_text(self, status_text: Optional[str]) -> None:
        """Store status text and redraw when visible."""
        self.status_text = status_text
        if self.status_visible:
            self.set_status_visible(True)

    def set_status_visible(self, visible: bool) -> None:
        """Show or hide the transition status overlay."""
        self.status_visible = visible
        if not visible or not self.status_text:
            self.status_view.setAlphaValue_(0.0)
            self.status_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        self.status_view.setFrame_(bounds)
        self.status_view.setImage_(create_status_overlay_image(bounds.size.width, bounds.size.height, self.status_text))
        self.status_view.setAlphaValue_(1.0)

    def set_memory_text(self, memory_text: Optional[str], visible: bool, warning: bool = False) -> None:
        """Show or hide the persistent resident-memory monitor at the top-right."""
        self.memory_text = memory_text
        self.memory_visible = visible
        self.memory_warning = warning
        if not visible or not memory_text:
            self.memory_view.setAlphaValue_(0.0)
            self.memory_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        self.memory_view.setFrame_(bounds)
        self.memory_view.setImage_(create_memory_overlay_image(bounds.size.width, bounds.size.height, memory_text, warning))
        self.memory_view.setAlphaValue_(1.0)

    def transition_to(
        self,
        image,
        transition: Transition,
        on_complete: Optional[Callable[[], None]] = None,
        media_is_video: bool = False,
    ) -> None:
        """Animate from the current image to a new image."""
        self.cancel_pending()
        self.stop_video()
        self.last_media_rect = None
        if transition == Transition.COLLAGE:
            self._transition_collage(image, on_complete, media_is_video)
            return
        if transition == Transition.QUAD:
            self._transition_quad(image, on_complete)
            return
        if self.layout_mode in {Transition.COLLAGE, Transition.QUAD}:
            self.layout_mode = None
            self.layout_canvas = None
        if self.current_image is None or transition == Transition.SWITCH:
            self.current_image = image
            self.primary_view.setImage_(image)
            self.overlay_view.setAlphaValue_(0.0)
            self.fade_view.setAlphaValue_(0.0)
            if on_complete is not None:
                self.pending_handles.append(self.schedule_callback(0.0, on_complete))
            return

        if transition == Transition.BLEND:
            self._transition_blend(image, on_complete)
        elif transition == Transition.FADE:
            self._transition_fade(image, on_complete)
        elif transition == Transition.EXPAND:
            self._transition_expand(image, on_complete)
        elif transition == Transition.WIPE:
            self._transition_wipe(image, on_complete)
        else:
            self.current_image = image
            self.primary_view.setImage_(image)
            if on_complete is not None:
                self.pending_handles.append(self.schedule_callback(0.0, on_complete))

    def _finish_transition(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        self.current_image = image
        self.primary_view.setImage_(image)
        self.overlay_view.setAlphaValue_(0.0)
        self.overlay_view.setFrame_(self._content_frame())
        self.fade_view.setAlphaValue_(0.0)
        if on_complete is not None:
            on_complete()

    def _transition_blend(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        self.overlay_view.setImage_(image)
        self.overlay_view.setFrame_(self._content_frame())
        self.overlay_view.setAlphaValue_(0.0)

        def step(index: int) -> None:
            alpha = index / TRANSITION_STEPS
            self.overlay_view.setAlphaValue_(alpha)
            if index < TRANSITION_STEPS:
                self.pending_handles.append(self.schedule_callback(self.transition_duration_ms / 1000.0 / TRANSITION_STEPS, lambda: step(index + 1)))
            else:
                self._finish_transition(image, on_complete)

        step(1)

    def _transition_fade(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        half_steps = max(1, TRANSITION_STEPS // 2)

        def fade_out(index: int) -> None:
            self.fade_view.setAlphaValue_(index / half_steps)
            if index < half_steps:
                self.pending_handles.append(self.schedule_callback(self.transition_duration_ms / 2000.0 / half_steps, lambda: fade_out(index + 1)))
            else:
                self.primary_view.setImage_(image)
                fade_in(half_steps)

        def fade_in(index: int) -> None:
            self.fade_view.setAlphaValue_(max(0.0, (index - 1) / half_steps))
            if index > 0:
                self.pending_handles.append(self.schedule_callback(self.transition_duration_ms / 2000.0 / half_steps, lambda: fade_in(index - 1)))
            else:
                self._finish_transition(image, on_complete)

        fade_out(1)

    def _transition_expand(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        bounds = self._content_frame()
        full_width = bounds.size.width
        full_height = bounds.size.height
        self.overlay_view.setImage_(image)

        def step(index: int) -> None:
            scale = max(0.02, index / TRANSITION_STEPS)
            width = full_width * scale
            height = full_height * scale
            x_pos = bounds.origin.x + (full_width - width) / 2.0
            y_pos = bounds.origin.y + (full_height - height) / 2.0
            self.overlay_view.setFrame_(NSMakeRect(x_pos, y_pos, width, height))
            self.overlay_view.setAlphaValue_(1.0)
            if index < TRANSITION_STEPS:
                self.pending_handles.append(self.schedule_callback(self.transition_duration_ms / 1000.0 / TRANSITION_STEPS, lambda: step(index + 1)))
            else:
                self._finish_transition(image, on_complete)

        step(1)

    def _transition_wipe(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        bounds = self._content_frame()
        current_image = self.current_image if self.current_image is not None else make_blank_canvas(bounds.size.width, bounds.size.height, self.background_color)

        def step(index: int) -> None:
            progress = index / WIPE_TRANSITION_STEPS
            wipe_frame = create_wipe_frame(current_image, image, progress, bounds.size.width, bounds.size.height, self.background_color)
            self.overlay_view.setFrame_(bounds)
            self.overlay_view.setImage_(wipe_frame)
            self.overlay_view.setAlphaValue_(1.0)
            if index < WIPE_TRANSITION_STEPS:
                self.pending_handles.append(self.schedule_callback(self.transition_duration_ms / 1000.0 / WIPE_TRANSITION_STEPS, lambda: step(index + 1)))
            else:
                self._finish_transition(image, on_complete)

        step(1)

    def _transition_collage(self, image, on_complete: Optional[Callable[[], None]], media_is_video: bool = False) -> None:
        bounds = self._content_frame()
        if self.collage_count >= self.collage_max_images:
            self.layout_canvas = None
            self.collage_count = 0
        canvas = self._base_canvas(Transition.COLLAGE)
        collage_image, media_rect = create_collage_canvas(
            image,
            canvas,
            bounds.size.width,
            bounds.size.height,
            self.collage_size_min,
            self.collage_size_max,
            self.collage_slot_index,
            rotate_item=not media_is_video,
        )
        self.last_media_rect = NSMakeRect(
            bounds.origin.x + media_rect.origin.x,
            bounds.origin.y + media_rect.origin.y,
            media_rect.size.width,
            media_rect.size.height,
        )
        self.collage_count += 1
        self.collage_slot_index = (self.collage_slot_index + 1) % 5
        display_image = copy_image(collage_image)
        self.current_image = display_image
        self.primary_view.setImage_(display_image)
        self.overlay_view.setAlphaValue_(0.0)
        self.fade_view.setAlphaValue_(0.0)
        self._raise_overlay_views()
        if on_complete is not None:
            self.pending_handles.append(self.schedule_callback(0.0, on_complete))

    def _transition_quad(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        bounds = self._content_frame()
        canvas = self._base_canvas(Transition.QUAD)
        quad_image, media_rect = create_quad_canvas(
            image,
            canvas,
            bounds.size.width,
            bounds.size.height,
            self.quad_index,
            self.background_color,
        )
        self.last_media_rect = NSMakeRect(
            bounds.origin.x + media_rect.origin.x,
            bounds.origin.y + media_rect.origin.y,
            media_rect.size.width,
            media_rect.size.height,
        )
        self.quad_index = (self.quad_index + 1) % 4
        display_image = copy_image(quad_image)
        self.current_image = display_image
        self.primary_view.setImage_(display_image)
        self.overlay_view.setAlphaValue_(0.0)
        self.fade_view.setAlphaValue_(0.0)
        self._raise_overlay_views()
        if on_complete is not None:
            self.pending_handles.append(self.schedule_callback(0.0, on_complete))


class BackgroundMusicController:
    """Retained two-player engine controlled by explicit ``#MUSIC:`` rows."""

    def __init__(self, config: Config, control_lines: list[str], schedule_callback, overlay_callback):
        self.config = config
        self.schedule_callback = schedule_callback
        self.overlay_callback = overlay_callback
        playlist_path = config.music_playlist
        if playlist_path is None and config.music_source is not None and config.music_source.is_dir():
            adventure_name = config.inputlist.stem
            if adventure_name.casefold().endswith("-sorted"):
                adventure_name = adventure_name[:-7]
            candidate = config.music_source / f"{adventure_name}.playlist"
            playlist_path = candidate if candidate.is_file() else None
        self.playlist = load_audio_playlist(config.music_source, playlist_path) if config.music_source else None
        self.directives: dict[int, MusicDirective] = {}
        for index, line in enumerate(control_lines):
            if not is_music_directive(line):
                continue
            try:
                directive = parse_music_directive(line)
            except MusicSyntaxError as exc:
                warn_message(f"control line {index + 1}: {exc}")
                continue
            if directive is not None:
                self.directives[index] = directive
        self.transport = MusicTransportState(self.playlist) if self.playlist is not None else None
        self.warned_music_errors: set[str] = set()
        self.players = [None, None]
        self.fade_envelopes = [0.0, 0.0]
        self.active_slot = 0
        self.current_index: Optional[int] = None
        self.user_enabled = True
        self.control_enabled = True
        self.volume_level = 9
        self.queue_resume_index: Optional[int] = None
        self.queue_resume_seconds: Optional[float] = None
        self.slideshow_paused = False
        self.video_paused = False
        self.poll_handle = None
        self.fade_handles = []
        self.last_control_row = -1
        self.disposed = False
        self.started = False
        if self.playlist is not None:
            for warning in self.playlist.warnings:
                warn_message(warning)

    @property
    def available(self) -> bool:
        return bool(AVKIT_VIDEO_AVAILABLE and AVPlayer is not None and self.playlist and self.playlist.files)

    @property
    def effective_playing(self) -> bool:
        return self.user_enabled and self.control_enabled and not self.slideshow_paused and not self.video_paused

    def _player(self):
        return self.players[self.active_slot]

    def start(self, row_index: int) -> None:
        if self.started or not self.available:
            return
        self.started = True
        target = self._reconstruct_through(int(row_index) - 1)
        self.last_control_row = int(row_index) - 1
        restored = self.restore_resume_state(self.config.resume_audio_state)
        if not restored:
            self._switch_to(target, immediate=True)
            if isinstance(self.config.resume_audio_state, dict):
                self.overlay_callback(
                    "Saved music position unavailable; using control-file music state",
                    3.0,
                )
        self._schedule_poll()

    def _path_for_index(self, index: object) -> Optional[str]:
        if self.playlist is None or not isinstance(index, int):
            return None
        if not 0 <= index < len(self.playlist.files):
            return None
        path = self.playlist.files[index]
        try:
            return path.relative_to(self.playlist.root).as_posix()
        except ValueError:
            return str(path)

    def _index_for_path(self, value: object) -> Optional[int]:
        if self.playlist is None or not isinstance(value, str) or not value.strip():
            return None
        return self.playlist.index_for_path(value)

    def resume_state_snapshot(self) -> Optional[dict]:
        """Return a path-based snapshot before retained AVPlayer objects go away."""
        if not self.started or not self.available or self.current_index is None:
            return None
        current_file = self._path_for_index(self.current_index)
        if current_file is None:
            return None
        player = self._player()
        elapsed = self._current_seconds(player) if player is not None else None
        transport = self.transport
        sequence = (
            [path for path in (self._path_for_index(index) for index in transport.sequence) if path]
            if transport is not None
            else []
        )
        return {
            "version": MUSIC_RESUME_STATE_VERSION,
            "playlist": str(self.config.music_playlist or ""),
            "current_file": current_file,
            "elapsed_seconds": elapsed,
            "transport": {
                "mode": transport.mode if transport is not None else "playlist",
                "sequence": sequence,
                "sequence_position": transport.sequence_position if transport is not None else 0,
                "continuation_file": self._path_for_index(
                    transport.continuation_index if transport is not None else None
                ),
            },
            "queue_resume_file": self._path_for_index(self.queue_resume_index),
            "queue_resume_seconds": self.queue_resume_seconds,
            "control_enabled": bool(self.control_enabled),
            "user_enabled": bool(self.user_enabled),
            "volume_level": int(self.volume_level),
        }

    def restore_resume_state(self, payload: object) -> bool:
        """Restore one exact path-based audio state into the current playlist."""
        if (
            not isinstance(payload, dict)
            or payload.get("version") != MUSIC_RESUME_STATE_VERSION
            or self.transport is None
            or self.playlist is None
        ):
            return False
        current_index = self._index_for_path(payload.get("current_file"))
        if current_index is None:
            self._warn_once("saved music title is no longer present; using control-file music state")
            return False
        transport_payload = payload.get("transport")
        if not isinstance(transport_payload, dict):
            return False
        mode = str(transport_payload.get("mode") or "playlist")
        sequence_paths = transport_payload.get("sequence")
        if not isinstance(sequence_paths, list):
            sequence_paths = []
        sequence = [self._index_for_path(value) for value in sequence_paths]
        if any(index is None for index in sequence):
            self._warn_once("saved music loop or queue changed; using control-file music state")
            return False
        resolved_sequence = tuple(int(index) for index in sequence if index is not None)
        continuation_index = self._index_for_path(
            transport_payload.get("continuation_file")
        )
        if mode == "playlist":
            self.transport.set_playlist(current_index)
        elif mode == "queue" and resolved_sequence:
            self.transport.set_queue(resolved_sequence, return_index=continuation_index)
        elif mode in {"loop_one", "loop_all", "loop_range", "loop_line", "loop_album"} and resolved_sequence:
            self.transport.set_loop(mode, resolved_sequence)
        else:
            self._warn_once("saved music transport is no longer valid; using control-file music state")
            return False
        if resolved_sequence and mode != "playlist":
            try:
                sequence_position = max(
                    0,
                    min(
                        int(transport_payload.get("sequence_position", 0)),
                        len(resolved_sequence) - 1,
                    ),
                )
            except (TypeError, ValueError):
                sequence_position = 0
            self.transport.sequence_position = sequence_position
            self.transport.current_index = current_index
        self.queue_resume_index = self._index_for_path(payload.get("queue_resume_file"))
        self.queue_resume_seconds = safe_float(payload.get("queue_resume_seconds"))
        self.control_enabled = bool(payload.get("control_enabled", True))
        self.user_enabled = bool(payload.get("user_enabled", True))
        try:
            self.volume_level = max(0, min(9, int(payload.get("volume_level", 9))))
        except (TypeError, ValueError):
            self.volume_level = 9
        elapsed = safe_float(payload.get("elapsed_seconds"))
        self._switch_to(
            current_index,
            resume_seconds=elapsed,
            fade_in_from_silence=True,
        )
        self._apply_all_player_gains()
        return True

    def synchronize_row(self, row_index: int) -> None:
        if not self.available:
            return
        if not self.started:
            self.start(row_index)
        row_index = int(row_index)
        if row_index < self.last_control_row:
            target = self._reconstruct_through(row_index)
            self._switch_to(target)
            self.last_control_row = row_index
            return
        for index in range(self.last_control_row + 1, row_index + 1):
            directive = self.directives.get(index)
            if directive is not None:
                self._execute_directive(directive, switch_player=True, show_status=True)
        self.last_control_row = row_index

    def _warn_once(self, message: str) -> None:
        key = str(message).casefold()
        if key not in self.warned_music_errors:
            self.warned_music_errors.add(key)
            warn_message(message)

    def _resolve_target(self, action: MusicAction) -> Optional[int]:
        if self.playlist is None:
            return None
        if action.kind == "target_label":
            index = self.playlist.index_for_label(action.value)
            if index is None:
                self._warn_once(f"music label ${action.value} is not present in the playlist")
            return index
        if action.kind == "target_path":
            index = self.playlist.index_for_path(action.value)
            if index is None:
                self._warn_once(f"music file is not present in the playlist: {action.value}")
            return index
        return None

    def _activate_transport(self, target: Optional[int], switch_player: bool) -> None:
        if target is None:
            return
        if switch_player:
            self._set_control_enabled(True)
        else:
            self.control_enabled = True
        if switch_player:
            self._switch_to(target)

    def _set_volume_level(self, level: int, show_status: bool) -> None:
        self.volume_level = max(0, min(9, int(level)))
        self._apply_all_player_gains()
        if show_status:
            self.overlay_callback(f"Music Volume {self.volume_level}/9", 1.5)

    def _target_gain(self) -> float:
        configured = max(0.0, min(100.0, float(getattr(self.config, "music_volume_percent", 65.0)))) / 100.0
        return configured * max(0.0, min(1.0, float(self.volume_level) / 9.0))

    def _slot_for_player(self, player) -> Optional[int]:
        for index, candidate in enumerate(self.players):
            if candidate is player:
                return index
        return None

    def _set_player_envelope(self, player, envelope: float) -> None:
        slot = self._slot_for_player(player)
        if slot is None:
            return
        self.fade_envelopes[slot] = max(0.0, min(1.0, float(envelope)))
        self._apply_player_gain(slot)

    def _apply_player_gain(self, slot: int) -> None:
        player = self.players[slot]
        if player is None:
            return
        gain = self._target_gain() * self.fade_envelopes[slot] if self.effective_playing else 0.0
        player.setVolume_(max(0.0, min(1.0, gain)))

    def _apply_all_player_gains(self) -> None:
        for slot in range(len(self.players)):
            self._apply_player_gain(slot)

    def _reconstruct_through(self, row_index: int) -> int:
        self.control_enabled = True
        self.volume_level = 9
        self.queue_resume_index = None
        self.queue_resume_seconds = None
        if self.transport is not None:
            self.transport.reset()
        for index in sorted(key for key in self.directives if key <= int(row_index)):
            self._execute_directive(self.directives[index], switch_player=False, show_status=False)
        if self.transport is not None and self.transport.current_index is not None:
            return self.transport.current_index
        return 0

    def _execute_directive(self, directive: MusicDirective, switch_player: bool, show_status: bool) -> None:
        if self.transport is None or self.playlist is None:
            return
        actions = list(directive.actions)
        consumed: set[int] = set()
        pending_targets: list[int] = []
        transport_commands = {
            "jump", "continue", "loop_line", "loop_one", "loop_range", "loop_album", "loop_all"
        }

        def flush_targets() -> None:
            if not pending_targets:
                return
            return_index = None
            if self.transport.mode == "queue":
                return_index = self.transport.continuation_index
            elif switch_player:
                return_index = self.current_index
                self._remember_queue_resume_position(return_index)
            else:
                return_index = self.transport.current_index
            target = self.transport.set_queue(tuple(pending_targets), return_index=return_index)
            pending_targets.clear()
            self._activate_transport(target, switch_player)

        for position, action in enumerate(actions):
            if position in consumed:
                continue
            kind = action.kind
            if kind in {"target_label", "target_path"}:
                target = self._resolve_target(action)
                if target is not None:
                    pending_targets.append(target)
                continue
            # Bare targets form a queue, but that queue takes effect before
            # the next command so directive order remains observable.
            flush_targets()
            if kind == "on":
                if switch_player:
                    self._set_control_enabled(True)
                else:
                    self.control_enabled = True
                continue
            if kind == "off":
                if switch_player:
                    self._set_control_enabled(False)
                else:
                    self.control_enabled = False
                continue
            if kind == "volume_up":
                self._set_volume_level(self.volume_level + 1, show_status)
                continue
            if kind == "volume_down":
                self._set_volume_level(self.volume_level - 1, show_status)
                continue
            if kind == "volume":
                self._set_volume_level(int(action.value), show_status)
                continue

            self._clear_queue_resume_position()
            target = None
            if kind == "jump":
                target = self.playlist.index_for_label(action.value)
                if target is None:
                    self._warn_once(f"music label ${action.value} is not present in the playlist")
                else:
                    target = self.transport.set_playlist(target)
            elif kind == "continue":
                self.transport.continue_normally()
            elif kind == "loop_one":
                current = self.transport.current_index
                if current is None:
                    current = self.current_index
                if current is None:
                    self._warn_once("#LOOPONE has no previously played title")
                else:
                    target = self.transport.set_loop("loop_one", (current,))
            elif kind == "loop_all":
                target = self.transport.set_loop("loop_all", tuple(range(len(self.playlist.files))))
            elif kind == "loop_range":
                first_label, last_label = action.value
                first = self.playlist.index_for_label(first_label)
                last = self.playlist.index_for_label(last_label)
                if first is None or last is None:
                    self._warn_once(f"#LOOPRANGE labels not found: ${first_label} ${last_label}")
                elif first > last:
                    self._warn_once(f"#LOOPRANGE starts after its end: ${first_label} ${last_label}")
                else:
                    target = self.transport.set_loop("loop_range", tuple(range(first, last + 1)))
            elif kind == "loop_line":
                indexes = []
                for later, candidate in enumerate(actions[position + 1 :], start=position + 1):
                    if candidate.kind in transport_commands:
                        break
                    if candidate.kind not in {"target_label", "target_path"}:
                        continue
                    consumed.add(later)
                    resolved = self._resolve_target(candidate)
                    if resolved is not None:
                        indexes.append(resolved)
                if not indexes:
                    self._warn_once("#LOOPLINE requires at least one later label or pathname")
                else:
                    target = self.transport.set_loop("loop_line", indexes)
            elif kind == "loop_album":
                explicit = None
                for later, candidate in enumerate(actions[position + 1 :], start=position + 1):
                    if candidate.kind in transport_commands:
                        break
                    if candidate.kind in {"target_label", "target_path"}:
                        consumed.add(later)
                        explicit = self._resolve_target(candidate)
                        break
                indexes = (
                    self.playlist.album_for_target(explicit)
                    if explicit is not None
                    else self.playlist.next_album(self.transport.current_index)
                )
                if not indexes:
                    self._warn_once("#LOOPALBUM could not resolve a non-root album")
                else:
                    target = self.transport.set_loop("loop_album", indexes)
            self._activate_transport(target, switch_player)
        flush_targets()

    def _remember_queue_resume_position(self, return_index: Optional[int]) -> None:
        """Remember where normal playlist playback was interrupted by a queue."""
        if self.transport is not None and self.transport.mode == "queue":
            return
        self.queue_resume_index = return_index
        self.queue_resume_seconds = None
        player = self._player()
        if player is None or return_index is None:
            return
        seconds = self._current_seconds(player)
        if seconds is not None and self.effective_playing:
            seconds += self._effective_crossfade(player)
        self.queue_resume_seconds = seconds

    def _clear_queue_resume_position(self) -> None:
        self.queue_resume_index = None
        self.queue_resume_seconds = None

    def _apply_gate_transition(self, was_playing: bool) -> None:
        """Fade the retained title when the effective audio gate changes."""
        now_playing = self.effective_playing
        player = self._player()
        if player is None or was_playing == now_playing:
            return
        if now_playing:
            self._fade(None, player, self.config.audio_crossfade_seconds)
        else:
            self._fade(player, None, self.config.audio_crossfade_seconds)

    def _set_control_enabled(self, enabled: bool) -> None:
        was_playing = self.effective_playing
        self.control_enabled = bool(enabled)
        self._apply_gate_transition(was_playing)

    def _new_player(self, index: int):
        path = self.playlist.files[index]
        player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(path)))
        player.setVolume_(0.0)
        return player

    def _duration_seconds(self, player) -> Optional[float]:
        try:
            item = player.currentItem()
            seconds = float(CMTimeGetSeconds(item.duration())) if item is not None and CMTimeGetSeconds is not None else math.nan
            return seconds if math.isfinite(seconds) and seconds > 0 else None
        except Exception:
            return None

    def _current_seconds(self, player) -> Optional[float]:
        try:
            seconds = float(CMTimeGetSeconds(player.currentTime())) if CMTimeGetSeconds is not None else math.nan
            return seconds if math.isfinite(seconds) and seconds >= 0 else None
        except Exception:
            return None

    def _cancel_fades(self, preserve=()) -> None:
        for handle in self.fade_handles:
            handle.cancel()
        self.fade_handles.clear()
        retained = {id(player) for player in preserve if player is not None}
        for player in self.players:
            if player is None or id(player) in retained:
                continue
            try:
                player.setVolume_(0.0)
                player.pause()
            except Exception:
                pass

    def _fade(self, outgoing, incoming, seconds: float, pause_outgoing=True) -> None:
        self._cancel_fades((outgoing, incoming))
        duration = max(0.0, float(seconds))
        outgoing_start = 1.0
        outgoing_slot = self._slot_for_player(outgoing)
        if outgoing_slot is not None:
            outgoing_start = self.fade_envelopes[outgoing_slot]
        incoming_start = 0.0
        incoming_slot = self._slot_for_player(incoming)
        if incoming_slot is not None:
            incoming_start = self.fade_envelopes[incoming_slot]
        if duration <= 0.0:
            if outgoing is not None:
                self._set_player_envelope(outgoing, 0.0)
                if pause_outgoing:
                    outgoing.pause()
            if incoming is not None:
                self._set_player_envelope(incoming, 1.0)
                if self.effective_playing:
                    incoming.play()
            return
        steps = max(2, min(40, int(math.ceil(duration / 0.05))))
        if incoming is not None and self.effective_playing:
            incoming.play()
        for step in range(1, steps + 1):
            fraction = step / steps

            def apply_fade(value=fraction, final=step == steps):
                if self.disposed:
                    return
                if outgoing is not None:
                    self._set_player_envelope(outgoing, outgoing_start * max(0.0, 1.0 - value))
                    if final and pause_outgoing:
                        outgoing.pause()
                if incoming is not None:
                    self._set_player_envelope(
                        incoming,
                        incoming_start + (1.0 - incoming_start) * value,
                    )

            self.fade_handles.append(self.schedule_callback(duration * fraction, apply_fade))

    def _effective_crossfade(self, outgoing) -> float:
        fade = max(0.0, float(self.config.audio_crossfade_seconds))
        duration = self._duration_seconds(outgoing) if outgoing is not None else None
        return min(fade, duration / 2.0) if duration is not None else fade

    def _switch_to(
        self,
        index: int,
        immediate: bool = False,
        resume_seconds: Optional[float] = None,
        fade_in_from_silence: bool = False,
    ) -> None:
        if not self.available or not 0 <= index < len(self.playlist.files):
            return
        outgoing = self._player()
        new_slot = 1 - self.active_slot
        incoming = self._new_player(index)
        if resume_seconds is not None and CMTimeMake is not None:
            try:
                milliseconds = max(0, int(round(float(resume_seconds) * 1000.0)))
                incoming.seekToTime_(CMTimeMake(milliseconds, 1000))
            except Exception as exc:
                self._warn_once(f"could not restore music playback position: {exc}")
        self.players[new_slot] = incoming
        self.fade_envelopes[new_slot] = 0.0
        self.active_slot = new_slot
        self.current_index = index
        if fade_in_from_silence:
            fade = max(0.0, float(self.config.audio_crossfade_seconds))
        else:
            fade = 0.0 if immediate or outgoing is None else self._effective_crossfade(outgoing)
        self._fade(outgoing, incoming, fade)
        old_slot = 1 - self.active_slot

        def release_outgoing():
            old = self.players[old_slot]
            if old is outgoing:
                try:
                    old.pause()
                except Exception:
                    pass
                self.players[old_slot] = None
                self.fade_envelopes[old_slot] = 0.0

        self.fade_handles.append(self.schedule_callback(fade + 0.05, release_outgoing))

    def _schedule_poll(self) -> None:
        if self.disposed:
            return
        self.poll_handle = self.schedule_callback(0.25, self._poll)

    def _poll(self) -> None:
        self.poll_handle = None
        if self.disposed:
            return
        player = self._player()
        if self.effective_playing and player is not None and self.current_index is not None:
            duration = self._duration_seconds(player)
            current = self._current_seconds(player)
            fade = self._effective_crossfade(player)
            if duration is not None and current is not None and current >= max(0.0, duration - fade - 0.05):
                was_queue = self.transport is not None and self.transport.mode == "queue"
                target = self.transport.next_index() if self.transport is not None else None
                if target is not None:
                    queue_completed = was_queue and self.transport.mode == "playlist"
                    resume_seconds = (
                        self.queue_resume_seconds
                        if queue_completed and target == self.queue_resume_index
                        else None
                    )
                    self._switch_to(target, resume_seconds=resume_seconds)
                    if queue_completed:
                        self._clear_queue_resume_position()
        self._schedule_poll()

    def toggle(self) -> None:
        if not self.available:
            self.overlay_callback("No audio available", 2.0)
            return
        self.user_enabled = not self.user_enabled
        player = self._player()
        if self.user_enabled:
            if self.effective_playing and player is not None:
                self._fade(None, player, self.config.audio_crossfade_seconds)
            self.overlay_callback("Audio On" if self.control_enabled else "Audio Off (#OFF)", 2.0)
        else:
            self._fade(player, None, self.config.audio_crossfade_seconds)
            self.overlay_callback("Audio Off", 2.0)

    def set_slideshow_paused(self, paused: bool) -> None:
        self._set_temporary_pause("slideshow_paused", paused)

    def set_video_active(self, active: bool) -> None:
        self._set_temporary_pause("video_paused", active)

    def _set_temporary_pause(self, attribute: str, value: bool) -> None:
        was_playing = self.effective_playing
        setattr(self, attribute, bool(value))
        now_playing = self.effective_playing
        player = self._player()
        if player is None or was_playing == now_playing:
            return
        if now_playing:
            self._fade(None, player, self.config.audio_crossfade_seconds)
        else:
            self._fade(player, None, self.config.audio_crossfade_seconds)

    def dispose(self) -> None:
        self.disposed = True
        if self.poll_handle is not None:
            self.poll_handle.cancel()
            self.poll_handle = None
        self._cancel_fades()
        for player in self.players:
            if player is not None:
                try:
                    player.pause()
                    player.setVolume_(0.0)
                except Exception:
                    pass
        self.players = [None, None]


class GPSTrackShowApp:
    """Cocoa application controller for the slideshow."""

    def __init__(self, config: Config):
        self.config = config
        self.playlist_lines = self._load_playlist_lines()
        self.stages = parse_stage_descriptors(self.playlist_lines)
        self.control_directives: dict[int, ControlDirective] = {}
        for row_index, line in enumerate(self.playlist_lines):
            if not is_control_directive(line):
                continue
            try:
                directive = parse_control_directive(line)
            except ControlSyntaxError as exc:
                warn_message(f"control line {row_index + 1}: {exc}")
                continue
            if directive is not None:
                self.control_directives[row_index] = directive
        self.control_labels = {}
        duplicate_labels = []
        for row_index, directive in self.control_directives.items():
            for action in directive.actions:
                if action.kind != "label":
                    continue
                key = control_label_key(action.value)
                if key in self.control_labels:
                    duplicate_labels.append(
                        (str(action.value), self.control_labels[key], row_index)
                    )
                else:
                    self.control_labels[key] = row_index
        for label, first_row, duplicate_row in duplicate_labels:
            warn_message(
                f"duplicate slide-show label ${label} on lines {first_row + 1} and "
                f"{duplicate_row + 1}; the first definition is used"
            )
        self.playlist_index = 0
        self.current_date: Optional[str] = None
        self.current_overview_image = None
        self.current_overview_metadata: Optional[dict] = None
        self.current_overview_path: Optional[Path] = None
        self.compact_track_summary_loaded = False
        self.compact_track_summary: Optional[dict] = None
        self.summary_tracks_by_map_cache: Optional[dict[str, dict]] = None
        self.stage_start_distance_cache: dict[int, float] = {}
        self.stage_length_cache: dict[str, float] = {}
        self.tour_total_distance_cache: Optional[float] = None
        self.current_stage_overview_image = None
        self.current_track_image = None
        self.current_track_path: Optional[Path] = None
        self.current_track_metadata: Optional[dict] = None
        self.current_elevation_profile_image = None
        self.elevation_profiles_enabled = bool(config.elevation_profile)
        self.header_visible = True
        self.time_lapse_active = bool(config.time_lapse_stages)
        self.time_lapse_stage: Optional[TimeLapseStage] = None
        self.time_lapse_points: list[dict] = []
        self.time_lapse_progress = 0.0
        self.time_lapse_media_queue: list[tuple[float, int, PhotoListEntry]] = []
        self.time_lapse_media_datetimes: dict[int, datetime] = {}
        self.time_lapse_media_cursor = 0
        self.time_lapse_audio_row_cursor = -1
        self.time_lapse_control_row_cursor = -1
        self.time_lapse_control_deferred = False
        self.time_lapse_handle = None
        self.time_lapse_last_tick = None
        self.time_lapse_current_media: Optional[tuple[int, PhotoListEntry]] = None
        self.time_lapse_media_image = None
        self.time_lapse_media_draw_frame = True
        self.time_lapse_media_marker_latlon: Optional[tuple[float, float]] = None
        self.time_lapse_stage_start_marker_latlon: Optional[tuple[float, float]] = None
        self.time_lapse_clock_time: Optional[tuple[int, int]] = None
        self.time_lapse_clock_date_text: Optional[str] = None
        self.time_lapse_place_text: Optional[str] = None
        self.time_lapse_stage_start_distance_km = 0.0
        self.time_lapse_media_deadline: Optional[float] = None
        self.time_lapse_media_remaining: Optional[float] = None
        self.time_lapse_video_player = None
        self.time_lapse_video_view = None
        self.time_lapse_video_original_path: Optional[Path] = None
        self.time_lapse_video_playback_path: Optional[Path] = None
        self.time_photo_view = None
        self.time_map_view = None
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_stage_map_preview_active = False
        self.time_lapse_overview_has_been_displayed = False
        self.fullscreen_window_roles: set[str] = set()
        self.first_overview_waiting_for_fullscreen_role: Optional[str] = None
        self.resume_progress_pending = config.resume_progress
        self.resume_media_index_pending = config.resume_media_index
        self.resume_phase_pending = config.resume_phase
        self.resume_standard_map_index_pending = None
        self.resume_start_pending = config.resume_index is not None
        self.completed_naturally = False
        self.completed_replays = 0
        self.current_phase: Optional[PlaybackPhase] = None
        self.current_stage_index: Optional[int] = None
        self.current_stage_media_position: Optional[int] = None
        self.intro_available = config.resume_index is None and config.start_track == 1
        self.intro_was_shown = False
        self.awaiting_intro_start = False
        self.intro_information_image = None
        self.intro_overview_image = None
        self._apply_start_track()
        self.start_playlist_index = self.playlist_index
        self.running = True
        self.manual_mode = config.keypressed
        self.active_callback = None
        self.timer_handles: dict[int, ScheduledCallback] = {}
        self._next_timer_token = 0
        self.window_delegates = []
        self.parked_map_resource = None
        self.retired_map_resources = []
        self.photo_presenter: Optional[CocoaImagePresenter] = None
        self.map_presenter: Optional[CocoaImagePresenter] = None
        self.current_state: Optional[DisplayState] = None
        self.current_display_index: Optional[int] = None
        self.pending_display_index: Optional[int] = None
        self.role_targets: dict[str, WindowTarget] = {}
        self.screen_swap = config.window_swap
        self.help_key_down = False
        self.info_key_down = False
        self.transition_key_down = False
        self.fullscreen_active = config.fullscreen
        self.active_photo_presenter: Optional[CocoaImagePresenter] = None
        self.transition_overlay_deadline = 0.0
        self.transition_overlay_hide_handle = None
        self.help_overlay_hide_handle = None
        self.duration_overlay_hide_handle = None
        self.startup_hint_hide_handle = None
        self.quad_bootstrap_remaining = 0
        self.random_transition_mode = config.transition == Transition.RANDOM
        self.active_transition = random.choice(RANDOM_TRANSITIONS) if self.random_transition_mode else config.transition
        self.initial_duration = float(config.duration)
        self.initial_playback_style = str(config.initial_style)
        self.control_pause_active = False
        self.control_pause_resume_callback = None
        self.control_non_display_limit = max(1000, len(self.playlist_lines) * 2)
        self.control_flow_steps = 0
        self.transition_change_armed = False
        self.paused = False
        self.startup_preview_identity: Optional[str] = None
        self.memory_debug_visible = False
        self.memory_warning_emitted = False
        self.memory_stop_requested = False
        self.memory_current_bytes: Optional[int] = None
        self.memory_peak_bytes = 0
        self.memory_warning_bytes, self.memory_critical_bytes = memory_watchdog_limits()
        self.memory_watchdog_handle = None
        self.owns_run_loop = True
        self.on_quit = None
        self._quit_notified = False
        self.use_normalized_videos = bool(config.use_normalized_videos)
        self.video_normalization_settings = NormalizationSettings(
            target_lufs=config.video_normalization_target_lufs,
            maximum_boost_db=config.video_normalization_max_boost_db,
            true_peak_db=config.video_normalization_true_peak_db,
        )
        self.video_normalization_manifest = load_video_normalization_manifest(config.photodir)
        self.live_state_sequence = 0
        self.settings_request_sequence = 0
        self.settings_display_snapshot = None
        self.live_state_signature = None
        self.command_poll_handle = None
        self.last_command_sequence = -1

        self._reconstruct_control_state_before(
            self.playlist_index,
            config.resume_control_state,
        )

        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        debug_print(self.config, f"Application created. photodir={self.config.photodir}")
        debug_print(self.config, f"Input list={self.config.inputlist}")
        debug_print(self.config, f"Loaded {len(self.playlist_lines)} non-empty playlist lines")
        debug_print(self.config, f"Starting at track {self.config.start_track}; playlist index={self.playlist_index}")
        debug_print(self.config, f"Initial mode={'manual' if self.manual_mode else 'automatic'} duration={self.config.duration:.1f}s")
        self._build_windows()
        self.music_controller = BackgroundMusicController(
            self.config,
            self.playlist_lines,
            self.schedule_callback,
            self._show_temporary_status_overlay,
        )
        self._install_key_monitor()
        signal.signal(signal.SIGINT, self._handle_sigint)
        signal.signal(signal.SIGTERM, self._handle_sigint)

    def _load_playlist_lines(self) -> list[str]:
        lines = []
        for raw_line in self.config.inputlist.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line:
                lines.append(line)
        return lines

    def _track_asset_dir(self) -> Path:
        """Return the directory used for overview and track-map assets."""
        return self.config.trackdir or self.config.photodir

    def _elevation_profile_for_map(self, track_path: Path, metadata: object):
        """Return a profile, falling back to the other map variant's metadata."""
        if not self.elevation_profiles_enabled:
            return None
        if (
            isinstance(metadata, dict)
            and scene_from_metadata(metadata, show_header=False).stage_kind != "gpx_track"
        ):
            return None
        profile_metadata = metadata if isinstance(metadata, dict) else {}
        if not elevation_profile_segments(profile_metadata):
            canonical = track_path.with_name(canonical_track_map_name(track_path.name))
            for prefer_time_lapse in (False, True):
                alternative = resolve_track_map_variant(
                    canonical,
                    prefer_time_lapse=prefer_time_lapse,
                )
                if alternative is None or alternative == track_path:
                    continue
                alternative_metadata = try_read_plot_metadata(
                    alternative.with_suffix(".json")
                )
                if elevation_profile_segments(alternative_metadata):
                    profile_metadata = alternative_metadata
                    break
        if (
            not isinstance(profile_metadata, dict)
            or scene_from_metadata(
                profile_metadata,
                show_header=False,
            ).stage_kind != "gpx_track"
            or not elevation_profile_segments(profile_metadata)
        ):
            return None
        return load_or_create_elevation_profile(track_path, profile_metadata)

    def _current_elevation_profile(self):
        """Return the current GPX-stage profile, generating its cache if needed."""
        if getattr(self, "current_track_path", None) is None:
            return None
        if getattr(self, "current_elevation_profile_image", None) is None:
            self.current_elevation_profile_image = self._elevation_profile_for_map(
                self.current_track_path,
                getattr(self, "current_track_metadata", None),
            )
        return self.current_elevation_profile_image

    def _stage_map_with_elevation_profile(self, relation: Optional[str] = None):
        """Show profiles on the roomier Time-Lapse map without changing map context."""
        if relation is not None or self.current_track_path is None:
            return self.current_track_image
        canonical = self.current_track_path.with_name(
            canonical_track_map_name(self.current_track_path.name)
        )
        profile_path = (
            resolve_track_map_variant(canonical, prefer_time_lapse=True)
            or self.current_track_path
        )
        profile_map = self.current_track_image
        profile_metadata = self.current_track_metadata
        if profile_path != self.current_track_path:
            try:
                profile_map = load_nsimage(profile_path)
                profile_metadata = try_read_plot_metadata(
                    profile_path.with_suffix(".json")
                )
                profile_map = draw_dynamic_map_overlay(
                    profile_map,
                    profile_metadata,
                    self.config,
                )
            except Exception as exc:
                warn_message(
                    f"could not load preferred profile map {profile_path.name}: {exc}"
                )
                profile_path = self.current_track_path
                profile_map = self.current_track_image
                profile_metadata = self.current_track_metadata
        profile = self._elevation_profile_for_map(profile_path, profile_metadata)
        if profile is None:
            return profile_map
        return draw_unframed_media_on_map(
            profile_map,
            profile_metadata,
            profile,
        )

    def _normalized_video_path(self, original_path: Path) -> Optional[Path]:
        return valid_normalized_video(
            self.config.photodir,
            original_path,
            self.video_normalization_settings,
            manifest=self.video_normalization_manifest,
        )

    def _video_playback_path(self, original_path: Path) -> Path:
        if self.use_normalized_videos:
            normalized = self._normalized_video_path(original_path)
            if normalized is not None:
                return normalized
        return original_path

    def _video_gain(self) -> float:
        return max(0.0, min(100.0, float(self.config.video_volume_percent))) / 100.0

    def _toggle_normalized_videos(self) -> None:
        desired = not self.use_normalized_videos
        active_original = None
        presenter = self.active_photo_presenter
        if presenter is not None and getattr(presenter, "video_player", None) is not None:
            active_original = getattr(presenter, "video_original_path", None)
        if active_original is None and self.time_lapse_video_player is not None:
            active_original = self.time_lapse_video_original_path
        replacement = None
        if active_original is not None:
            replacement = self._normalized_video_path(Path(active_original)) if desired else Path(active_original)
            if desired and replacement is None:
                self._show_temporary_status_overlay("No normalized version available", 2.0)
                return
        self.use_normalized_videos = desired
        if active_original is not None and replacement is not None:
            if presenter is not None and getattr(presenter, "video_player", None) is not None:
                presenter.replace_video_source(replacement, self._video_gain())
            elif self.time_lapse_video_player is not None:
                self._replace_time_lapse_video_source(replacement)
        self._show_temporary_status_overlay(
            "Normalized Video Audio On" if desired else "Normalized Video Audio Off",
            2.0,
        )

    def _load_compact_track_summary(self) -> Optional[dict]:
        """Load one compact summary instead of opening every stage sidecar."""
        if getattr(self, "compact_track_summary_loaded", False):
            return getattr(self, "compact_track_summary", None)
        self.compact_track_summary_loaded = True
        self.compact_track_summary = None
        candidates = []
        current_overview_path = getattr(self, "current_overview_path", None)
        if current_overview_path is not None:
            candidates.append(
                current_overview_path.with_name(
                    f"{current_overview_path.stem}-summary.json"
                )
            )
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                warn_message(f"could not read compact track summary {candidate}: {exc}")
                continue
            if isinstance(payload, dict) and isinstance(payload.get("tracks"), list):
                self.compact_track_summary = payload
                return payload
        if (
            isinstance(getattr(self, "current_overview_metadata", None), dict)
            and isinstance(self.current_overview_metadata.get("tracks"), list)
        ):
            self.compact_track_summary = {
                "tracks": self.current_overview_metadata["tracks"]
            }
        return self.compact_track_summary

    def _summary_tracks_by_map_filename(self) -> dict[str, dict]:
        """Index compact track rows by their canonical Standard map filename."""
        cached = getattr(self, "summary_tracks_by_map_cache", None)
        if cached is not None:
            return cached
        summary = self._load_compact_track_summary()
        rows = summary.get("tracks") if isinstance(summary, dict) else None
        if not isinstance(rows, list):
            self.summary_tracks_by_map_cache = {}
            return {}
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in (
                "track_plot_image_filename",
                "track_plot_time_lapse_image_filename",
            ):
                filename = row.get(key)
                if isinstance(filename, str) and filename.strip():
                    result[canonical_track_map_name(Path(filename).name)] = row
        self.summary_tracks_by_map_cache = result
        return result

    @staticmethod
    def _summary_track_length(row: object) -> float:
        """Read a distance from compact modern or localized summary fields."""
        if not isinstance(row, dict):
            return 0.0
        for key in ("track_length_km", "length_km", "laenge_km"):
            value = safe_float(row.get(key))
            if value is not None and value >= 0.0:
                return value
        return 0.0

    def _attach_time_lapse_view(self, host_view, role: str):
        view = TimeLapseMapView.alloc().initWithFrame_(host_view.bounds())
        view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        view.setHidden_(True)
        host_view.addSubview_(view)
        if role == "photo":
            self.time_photo_view = view
        else:
            self.time_map_view = view
        return view

    def _apply_start_track(self) -> None:
        """Move to an exact resume row or the requested Nth track map."""
        resume_index = self.config.resume_index
        if resume_index is not None and 0 <= resume_index < len(self.playlist_lines):
            if self.time_lapse_active:
                resume_line = self.playlist_lines[resume_index]
                if parse_map_directive(resume_line) is None:
                    if not resume_line.startswith("#"):
                        self.resume_media_index_pending = resume_index
                        resume_index = next(
                            (
                                index
                                for index in range(resume_index, -1, -1)
                                if parse_map_directive(self.playlist_lines[index]) is not None
                            ),
                            None,
                        )
            elif (
                self.resume_media_index_pending is not None
                and 0 <= self.resume_media_index_pending < len(self.playlist_lines)
                and not self.playlist_lines[self.resume_media_index_pending].startswith("#")
            ):
                resume_index = self.resume_media_index_pending
            if not self.time_lapse_active and not self.playlist_lines[resume_index].startswith("#"):
                self.resume_standard_map_index_pending = next(
                    (
                        index
                        for index in range(resume_index, -1, -1)
                        if parse_map_directive(self.playlist_lines[index]) is not None
                    ),
                    None,
                )
            if resume_index is not None:
                self._prime_context_before_index(resume_index)
                self.playlist_index = resume_index
                return
        self.resume_start_pending = False
        self.resume_progress_pending = None
        self.resume_media_index_pending = None
        self.resume_standard_map_index_pending = None
        start_index = self._find_track_start_index(self.config.start_track)
        if start_index is None:
            track_count = sum(1 for line in self.playlist_lines if is_normal_map_directive(line))
            warn_message(
                f"--start {self.config.start_track} requested, but only {track_count} track maps were found; starting at the beginning"
            )
            return
        self._prime_context_before_index(start_index)
        self.playlist_index = start_index

    def _find_track_start_index(self, track_number: int) -> Optional[int]:
        """Return the playlist index of the requested 1-based #Map entry."""
        seen_tracks = 0
        for index, line in enumerate(self.playlist_lines):
            if is_normal_map_directive(line):
                seen_tracks += 1
                if seen_tracks == track_number:
                    return index
        return None

    def _prime_context_before_index(self, start_index: int) -> None:
        """Load overview/date context that would have been seen before start_index."""
        overview_filename = None
        date_text = None
        for line in self.playlist_lines[:start_index]:
            content = line.strip()
            if content.startswith("#Overviewmap:"):
                overview_filename = content.partition(":")[2].strip()
            elif content.startswith("#Datum:"):
                date_text = content.partition(":")[2].strip()
        self.current_date = date_text
        if overview_filename is not None:
            self._handle_overview(overview_filename)

    def _install_key_monitor(self) -> None:
        def handler(event):
            chars = event.charactersIgnoringModifiers()
            raw_chars = event.characters()
            modifier_flags = event.modifierFlags() if hasattr(event, "modifierFlags") else 0
            command_pressed = bool(modifier_flags & NSEventModifierFlagCommand)
            if chars in {"q", "Q", "\x1b"} or raw_chars in {"q", "Q", "\x1b"}:
                self.schedule_callback(0.0, self.quit)
                return None
            if chars in {"m", "M"} or raw_chars in {"m", "M"}:
                self._toggle_mode()
                return None
            if chars in {"c", "C"} or raw_chars in {"c", "C"}:
                self._toggle_clock()
                return None
            if chars in {"s", "S"} or raw_chars in {"s", "S"}:
                self._publish_settings_request()
                return None
            if chars in {"e", "E"} or raw_chars in {"e", "E"}:
                self._toggle_elevation_profiles()
                return None
            if chars in {"a", "A"} or raw_chars in {"a", "A"}:
                self.music_controller.toggle()
                return None
            if chars in {"n", "N"} or raw_chars in {"n", "N"}:
                self._toggle_normalized_videos()
                return None
            if raw_chars in {"t", "T"}:
                if self.transition_key_down:
                    return None
                self.transition_key_down = True
                if self.transition_overlay_hide_handle is not None:
                    self.transition_overlay_hide_handle.cancel()
                    self.transition_overlay_hide_handle = None
                self._cycle_playback_style(-1 if raw_chars == "T" else 1)
                self._set_transition_overlay_visible(True)
                self.transition_overlay_deadline = time.monotonic() + 1.0
                return None
            if chars in {"i", "I"} or raw_chars in {"i", "I"}:
                self.info_key_down = True
                self._set_info_overlay_visible(True)
                return None
            if command_pressed and (chars == NSRightArrowFunctionKey or chars == NSDownArrowFunctionKey):
                self._jump_to_date_section(True)
                return None
            if command_pressed and (chars == NSLeftArrowFunctionKey or chars == NSUpArrowFunctionKey):
                self._jump_to_date_section(False)
                return None
            if chars == " ":
                if self.awaiting_intro_start:
                    self._begin_intro_playback()
                    return None
                if self.manual_mode:
                    self._step_forward()
                else:
                    self._toggle_pause()
                return None
            if chars == NSRightArrowFunctionKey or chars == NSDownArrowFunctionKey:
                self._step_forward()
                return None
            if chars == NSLeftArrowFunctionKey or chars == NSUpArrowFunctionKey:
                self._step_backward()
                return None
            if chars in {"f", "F"} or raw_chars in {"f", "F"}:
                self._toggle_fullscreen()
                return None
            if chars in {"w", "W"} or raw_chars in {"w", "W"}:
                self._toggle_window_mode()
                return None
            if raw_chars == "D":
                self._toggle_memory_debug()
                return None
            if chars == "d" or raw_chars == "d":
                self._swap_window_screens()
                return None
            if command_pressed and raw_chars == "+":
                self._change_time_lapse_duration(5.0)
                return None
            if command_pressed and raw_chars == "-":
                self._change_time_lapse_duration(-5.0)
                return None
            if raw_chars == "+":
                self._change_duration(1.0)
                return None
            if raw_chars == "-":
                self._change_duration(-1.0)
                return None
            return event

        def keyup_handler(event):
            chars = event.charactersIgnoringModifiers()
            raw_chars = event.characters()
            if chars in {"h", "H"} or raw_chars in {"h", "H"}:
                self.help_key_down = False
                if self.help_overlay_hide_handle is not None:
                    self.help_overlay_hide_handle.cancel()
                self.help_overlay_hide_handle = self.schedule_callback(
                    HELP_OVERLAY_PERSISTENCE_SECONDS,
                    lambda: self._set_help_overlay_visible(False),
                )
                return None
            if chars in {"i", "I"} or raw_chars in {"i", "I"}:
                self.info_key_down = False
                self._set_info_overlay_visible(False)
                return None
            if chars in {"t", "T"} or raw_chars in {"t", "T"}:
                self.transition_key_down = False
                remaining = self.transition_overlay_deadline - time.monotonic()
                if remaining <= 0:
                    self._set_transition_overlay_visible(False)
                else:
                    if self.transition_overlay_hide_handle is not None:
                        self.transition_overlay_hide_handle.cancel()
                    self.transition_overlay_hide_handle = self.schedule_callback(remaining, lambda: self._set_transition_overlay_visible(False))
                return None
            return event

        def keydown_handler(event):
            chars = event.charactersIgnoringModifiers()
            raw_chars = event.characters()
            if chars in {"h", "H"} or raw_chars in {"h", "H"}:
                self.help_key_down = True
                if self.help_overlay_hide_handle is not None:
                    self.help_overlay_hide_handle.cancel()
                    self.help_overlay_hide_handle = None
                self._set_help_overlay_visible(True)
                return None
            return handler(event)

        self.event_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, keydown_handler)
        self.event_monitor_up = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyUp, keyup_handler)

    def _set_help_overlay_visible(self, visible: bool) -> None:
        """Show or hide the help overlay on all active presenters."""
        if not visible and self.help_overlay_hide_handle is not None:
            self.help_overlay_hide_handle.cancel()
            self.help_overlay_hide_handle = None
        if self.photo_presenter is not None:
            self.photo_presenter.set_help_visible(visible)
        if self.map_presenter is not None:
            self.map_presenter.set_help_visible(visible)

    def _set_info_overlay_visible(self, visible: bool) -> None:
        """Show or hide the metadata overlay on all active presenters."""
        if self.photo_presenter is not None:
            self.photo_presenter.set_info_visible(visible)
        if self.map_presenter is not None:
            self.map_presenter.set_info_visible(visible)

    def _set_transition_overlay_visible(self, visible: bool) -> None:
        """Show or hide the transition-name overlay on the active photo presenter."""
        if not visible and self.transition_overlay_hide_handle is not None:
            self.transition_overlay_hide_handle.cancel()
            self.transition_overlay_hide_handle = None
        if visible and self.duration_overlay_hide_handle is not None:
            self.duration_overlay_hide_handle.cancel()
            self.duration_overlay_hide_handle = None
        if self.photo_presenter is not None:
            self.photo_presenter.set_status_visible(False)
        if self.map_presenter is not None:
            self.map_presenter.set_status_visible(False)
        if visible and self.active_photo_presenter is not None:
            label = (
                "TIME-LAPSE"
                if self.time_lapse_active
                else ("RANDOM" if self.random_transition_mode else self.active_transition.value)
            )
            self.active_photo_presenter.set_status_text(label)
            self.active_photo_presenter.set_status_visible(True)

    def _jump_to_date_section(self, forward: bool) -> None:
        """Jump to the next or previous map-backed stage."""
        direction = "next" if forward else "previous"
        if self.time_lapse_stage is not None:
            current_index = self.time_lapse_stage.map_index
        elif self.current_display_index is not None:
            current_index = self.current_display_index
        else:
            current_index = self.playlist_index - 1
        target_index = adjacent_stage_map_index(
            self.playlist_lines,
            current_index,
            forward,
        )
        if target_index is None:
            if (
                not forward
                and self.intro_was_shown
                and self.stages
                and self.current_stage_index in {None, 0}
            ):
                if self.active_callback is not None:
                    self.active_callback.cancel()
                    self.active_callback = None
                if self.time_lapse_stage is not None or self.time_lapse_handle is not None:
                    self._cancel_time_lapse_stage()
                self._show_intro_phase(PlaybackPhase.INTRO_INFO)
                return
            debug_print(self.config, f"No {direction} stage available")
            return
        debug_print(self.config, f"Jumping to {direction} stage at playlist index {target_index}")
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.time_lapse_stage is not None or self.time_lapse_handle is not None:
            self._cancel_time_lapse_stage()
        self._prime_context_before_index(target_index)
        stage_index = next(
            (
                index
                for index, stage in enumerate(self.stages)
                if stage.map_index == target_index
            ),
            None,
        )
        if stage_index is None:
            self.playlist_index = target_index
            self._advance()
            return
        stage = self.stages[stage_index]
        if self.time_lapse_active:
            self.playlist_index = target_index
            self.pending_display_index = target_index
            self.current_display_index = target_index
            self._start_time_lapse_stage(
                target_index,
                stage.directive.filename,
                relation=stage.directive.relation,
            )
            return
        self._show_standard_stage_map_only(stage_index, immediate=True)

    def _show_temporary_status_overlay(self, status_text: str, seconds: float) -> None:
        """Show one temporary status overlay on the routed photo presenter."""
        if self.duration_overlay_hide_handle is not None:
            self.duration_overlay_hide_handle.cancel()
            self.duration_overlay_hide_handle = None
        if self.transition_overlay_hide_handle is not None:
            self.transition_overlay_hide_handle.cancel()
            self.transition_overlay_hide_handle = None
        presenter = self.active_photo_presenter or self._presenter_for_role("photo")
        if presenter is None:
            return
        presenter.set_status_text(status_text)
        presenter.set_status_visible(True)

        def hide_if_still_current(current_presenter=presenter, current_text=status_text) -> None:
            if current_presenter.status_text == current_text:
                current_presenter.set_status_visible(False)
            self.duration_overlay_hide_handle = None

        self.duration_overlay_hide_handle = self.schedule_callback(seconds, hide_if_still_current)

    def _reset_photo_layouts(self, mode: Optional[Transition] = None, preserve_current_image: bool = False) -> None:
        """Reset cumulative photo layouts on all presenters."""
        if self.photo_presenter is not None:
            self.photo_presenter.reset_photo_layout(mode, preserve_current_image=preserve_current_image)
        if self.map_presenter is not None:
            self.map_presenter.reset_photo_layout(mode, preserve_current_image=preserve_current_image)

    def _update_window_titles(self, message: Optional[str] = None) -> None:
        """Show current mode and timing in window titles."""
        mode_text = "MANUAL" if self.manual_mode else "AUTO"
        if self.paused and not self.manual_mode:
            mode_text = "PAUSED"
        suffix = f"[{mode_text} | {self.config.duration:.1f}s]"
        if message:
            suffix = f"{suffix} {message}"
        if hasattr(self, "photo_window") and self.photo_window is not None:
            if self.config.join_windows:
                base = "GPSTrackShow"
            elif self.config.mapwindow and self.screen_swap:
                base = "GPSTrackShow - Maps"
            else:
                base = "GPSTrackShow - Photos"
            self.photo_window.setTitle_(f"{base} {suffix}")
        if getattr(self, "map_window", None) is not None:
            map_base = "GPSTrackShow - Photos" if self.screen_swap else "GPSTrackShow - Maps"
            self.map_window.setTitle_(f"{map_base} {suffix}")

    def _freeze_time_lapse_media(self) -> None:
        if self.time_lapse_current_media is None:
            return
        if self.time_lapse_media_deadline is not None:
            self.time_lapse_media_remaining = max(0.0, self.time_lapse_media_deadline - time.monotonic())
            self.time_lapse_media_deadline = None
        if self.time_lapse_video_player is not None:
            try:
                self.time_lapse_video_player.pause()
            except Exception:
                pass

    def _resume_time_lapse_media(self) -> None:
        if self.time_lapse_current_media is None or self.manual_mode or self.paused:
            return
        if self.time_lapse_media_deadline is None:
            remaining = self.time_lapse_media_remaining
            if remaining is None:
                remaining = self.config.duration
            self.time_lapse_media_deadline = time.monotonic() + max(0.0, remaining)
        if self.time_lapse_video_player is not None:
            try:
                self.time_lapse_video_player.play()
            except Exception:
                pass

    def _toggle_mode(self) -> None:
        """Switch between automatic and manual stepping."""
        self.manual_mode = not self.manual_mode
        self.paused = False
        self.music_controller.set_slideshow_paused(False)
        if self.manual_mode:
            self._freeze_time_lapse_media()
        else:
            self._resume_time_lapse_media()
        mode_name = "manual" if self.manual_mode else "automatic"
        debug_print(self.config, f"Switched to {mode_name} mode")
        self._update_window_titles(f"Mode {mode_name}")
        self._show_temporary_status_overlay(mode_name, 2.0)
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.time_lapse_handle is not None:
            self.time_lapse_handle.cancel()
            self.time_lapse_handle = None
        if not self.manual_mode and self.time_lapse_stage_map_preview_active:
            self._schedule_callback(
                self.config.duration,
                self._continue_after_time_lapse_stage_map,
            )
        elif not self.manual_mode and self.time_lapse_overview_preview_active:
            callback = (
                self._continue_after_time_lapse_overview
                if self.time_lapse_overview_inset_active
                else (
                    self.current_state.next_callback
                    if self.current_state is not None
                    else None
                )
            )
            if callback is not None:
                self._schedule_callback(self.config.duration, callback)
        elif (
            not self.manual_mode
            and self.time_lapse_stage is not None
            and self.time_lapse_stage.relation is not None
        ):
            self._schedule_special_time_lapse_advance(
                self.time_lapse_media_remaining or self.config.duration
            )
        elif not self.manual_mode and self.time_lapse_stage is not None and not self.time_lapse_overview_preview_active:
            self.time_lapse_last_tick = time.monotonic()
            self._time_lapse_tick()
        elif not self.manual_mode and self.current_state is not None and self.current_state.next_callback is not None and self.current_state.auto_delay is not None:
            self._schedule_callback(self.current_state.auto_delay, self.current_state.next_callback)

    def _toggle_pause(self) -> None:
        """Pause or resume automatic playback."""
        if self.manual_mode:
            return
        self.paused = not self.paused
        self.music_controller.set_slideshow_paused(self.paused)
        if self.paused:
            self._freeze_time_lapse_media()
        else:
            self._resume_time_lapse_media()
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.time_lapse_handle is not None:
            self.time_lapse_handle.cancel()
            self.time_lapse_handle = None
        if self.paused:
            debug_print(self.config, "Paused automatic playback")
            self._update_window_titles("Paused")
            self._show_temporary_status_overlay("Pause", 2.0)
            return
        debug_print(self.config, "Resumed automatic playback")
        self._update_window_titles("Running")
        self._show_temporary_status_overlay("Auto", 2.0)
        if self.time_lapse_stage_map_preview_active:
            self._schedule_callback(
                self.config.duration,
                self._continue_after_time_lapse_stage_map,
            )
        elif self.time_lapse_overview_preview_active:
            callback = (
                self._continue_after_time_lapse_overview
                if self.time_lapse_overview_inset_active
                else (
                    self.current_state.next_callback
                    if self.current_state is not None
                    else None
                )
            )
            if callback is not None:
                self._schedule_callback(self.config.duration, callback)
        elif self.time_lapse_stage is not None and self.time_lapse_stage.relation is not None:
            self._schedule_special_time_lapse_advance(
                self.time_lapse_media_remaining or self.config.duration
            )
        elif self.time_lapse_stage is not None and not self.time_lapse_overview_preview_active:
            self.time_lapse_last_tick = time.monotonic()
            self._time_lapse_tick()
        elif self.current_state is not None and self.current_state.next_callback is not None and self.current_state.auto_delay is not None:
            self._schedule_callback(self.current_state.auto_delay, self.current_state.next_callback)

    def _change_duration(self, delta_seconds: float) -> None:
        """Change automatic playback duration."""
        if self.manual_mode:
            debug_print(self.config, "Ignoring duration change in manual mode")
            return
        new_duration = max(1.0, self.config.duration + delta_seconds)
        object.__setattr__(self.config, "duration", new_duration)
        debug_print(self.config, f"Duration changed to {new_duration:.1f}s")
        self._update_window_titles(f"Duration {new_duration:.1f}s")
        self._show_temporary_status_overlay(f"Duration {new_duration:.1f}s", 2.0)
        print(f"[GPSTrackShow] Duration: {new_duration:.1f}s", flush=True)
        if self.time_lapse_overview_preview_active:
            if not self.manual_mode and not self.paused:
                callback = (
                    self._continue_after_time_lapse_overview
                    if self.time_lapse_overview_inset_active
                    else (self.current_state.next_callback if self.current_state is not None else None)
                )
                if callback is not None:
                    self._schedule_callback(new_duration, callback)
            return
        if self.time_lapse_stage is not None:
            if self.time_lapse_current_media is not None:
                _row_index, entry = self.time_lapse_current_media
                if not is_video_path(resolve_path(self.config.photodir, entry.source_name)):
                    self.time_lapse_media_remaining = new_duration
                    self.time_lapse_media_deadline = (
                        None if self.paused or self.manual_mode else time.monotonic() + new_duration
                    )
            return
        if not self.manual_mode and self.current_state is not None and self.current_state.next_callback is not None and self.current_state.auto_delay is not None:
            self._schedule_callback(new_duration, self.current_state.next_callback)

    def _change_time_lapse_duration(self, delta_seconds: float) -> None:
        new_duration = max(5.0, self.config.time_lapse_duration + delta_seconds)
        object.__setattr__(self.config, "time_lapse_duration", new_duration)
        self._update_window_titles(f"Time-Lapse {new_duration:.0f}s")
        self._show_temporary_status_overlay(f"Time-Lapse {new_duration:.0f}s", 2.0)
        if self.time_lapse_stage is not None and not self.paused and not self.manual_mode:
            self.time_lapse_last_tick = time.monotonic()

    def _toggle_time_lapse_mode(self) -> None:
        self.time_lapse_active = not self.time_lapse_active
        if self.time_lapse_handle is not None:
            self.time_lapse_handle.cancel()
            self.time_lapse_handle = None
        if self.current_phase in {
            PlaybackPhase.INTRO_INFO,
            PlaybackPhase.INTRO_OVERVIEW,
        }:
            self._show_temporary_status_overlay(
                "Time-Lapse" if self.time_lapse_active else "Standard Slide Show",
                2.0,
            )
            return
        if self.time_lapse_active:
            current_index = max(0, self.playlist_index - 1)
            map_index = next(
                (
                    index
                    for index in range(current_index, -1, -1)
                    if parse_map_directive(self.playlist_lines[index]) is not None
                ),
                None,
            )
            if map_index is not None:
                directive = parse_map_directive(self.playlist_lines[map_index])
                if directive is None:
                    return
                current_media = None
                if current_index > map_index and not self.playlist_lines[current_index].startswith("#"):
                    current_media = (current_index, parse_photo_entry(self.playlist_lines[current_index]))
                self._start_time_lapse_stage(
                    map_index,
                    directive.filename,
                    resume_media=current_media,
                    relation=directive.relation,
                )
            else:
                self._show_temporary_status_overlay("Time-Lapse armed for next stage", 2.0)
        else:
            self.time_lapse_overview_preview_active = False
            self._clear_time_lapse_views()
            self.time_lapse_stage_start_marker_latlon = None
            if self.time_lapse_current_media is not None:
                row_index, entry = self.time_lapse_current_media
                self.time_lapse_current_media = None
                self.time_lapse_media_image = None
                self.time_lapse_media_draw_frame = True
                self.time_lapse_media_marker_latlon = None
                self.time_lapse_clock_time = None
                self.time_lapse_clock_date_text = None
                self.time_lapse_place_text = None
                self.time_lapse_stage_start_distance_km = 0.0
                self.time_lapse_media_deadline = None
                self.time_lapse_media_remaining = None
                self.playlist_index = row_index + 1
                self.time_lapse_stage = None
                self._handle_photo(entry)
            elif self.time_lapse_stage is not None:
                self.playlist_index = self.time_lapse_stage.map_index
                self.time_lapse_stage = None
                self._advance()
            else:
                self._advance()
            self._show_temporary_status_overlay("Standard Slide Show", 2.0)

    def _playback_style_name(self) -> str:
        if self.time_lapse_active:
            return "TIME_LAPSE"
        return "RANDOM" if self.random_transition_mode else self.active_transition.value

    def _assign_playback_style(self, style: str) -> None:
        """Assign one style without moving the playlist or rendering a frame."""
        normalized = normalize_control_transition(style)
        self.time_lapse_active = normalized == "TIME_LAPSE"
        if self.time_lapse_active:
            return
        transition = Transition(normalized)
        self.random_transition_mode = transition == Transition.RANDOM
        self.active_transition = (
            random.choice(RANDOM_TRANSITIONS)
            if self.random_transition_mode
            else transition
        )
        self.transition_change_armed = True

    def _select_playback_style(self, style: str, show_status: bool = True) -> None:
        """Select a style through the same live-switch path used by t/T."""
        normalized = normalize_control_transition(style)
        current = self._playback_style_name()
        if normalized == current:
            return
        if normalized == "TIME_LAPSE":
            self._toggle_time_lapse_mode()
        else:
            transition = Transition(normalized)
            self.random_transition_mode = transition == Transition.RANDOM
            self.active_transition = (
                random.choice(RANDOM_TRANSITIONS)
                if self.random_transition_mode
                else transition
            )
            self.transition_change_armed = True
            if self.time_lapse_active:
                self._toggle_time_lapse_mode()
        if show_status:
            label = normalized.replace("_", "-")
            self._show_temporary_status_overlay(f"Style {label}", 2.0)
            self._update_window_titles(f"Style {label}")

    def _reconstruct_control_state_before(
        self,
        row_index: int,
        resume_state: object = None,
    ) -> None:
        """Restore persistent control state without executing flow commands."""
        duration = self.initial_duration
        style = self.initial_playback_style
        for index in sorted(key for key in self.control_directives if key < int(row_index)):
            for action in self.control_directives[index].actions:
                if action.kind == "duration":
                    duration = float(action.value)
                elif action.kind == "transition":
                    style = str(action.value)
        if isinstance(resume_state, dict) and resume_state.get("version") == 1:
            try:
                saved_duration = float(resume_state.get("duration"))
            except (TypeError, ValueError):
                saved_duration = 0.0
            if math.isfinite(saved_duration) and saved_duration > 0.0:
                duration = saved_duration
            try:
                style = normalize_control_transition(resume_state.get("transition"))
            except ControlSyntaxError:
                pass
        object.__setattr__(self.config, "duration", duration)
        self._assign_playback_style(style)

    def _control_state_snapshot(self) -> dict:
        config = getattr(self, "config", None)
        duration = float(getattr(config, "duration", 3.0))
        transition = (
            self._playback_style_name()
            if hasattr(self, "time_lapse_active")
            else (
                "TIME_LAPSE"
                if getattr(config, "time_lapse_stages", False)
                else str(getattr(getattr(config, "transition", Transition.BLEND), "value", "BLEND"))
            )
        )
        return {
            "version": 1,
            "duration": duration,
            "transition": transition,
        }

    def _toggle_clock(self) -> None:
        """Toggle the complete configured header for this playback session."""
        self.header_visible = not self.header_visible
        state_text = "on" if self.header_visible else "off"
        debug_print(self.config, f"Header toggled {state_text}")
        self._update_window_titles(f"Header {state_text}")
        self._refresh_photo_overlays()

    def _toggle_elevation_profiles(self) -> None:
        """Toggle cached stage-start elevation profiles for this session."""
        self.elevation_profiles_enabled = not self.elevation_profiles_enabled
        if (
            self.time_lapse_active
            and self.time_lapse_stage is not None
            and self.time_lapse_stage_map_preview_active
        ):
            if self.elevation_profiles_enabled:
                self.time_lapse_media_image = self._current_elevation_profile()
                self.time_lapse_media_draw_frame = False
                self._set_time_lapse_views()
            else:
                if self.active_callback is not None:
                    self.active_callback.cancel()
                    self.active_callback = None
                self._continue_after_time_lapse_stage_map()
        self._show_temporary_status_overlay(
            "Elevation Profiles On"
            if self.elevation_profiles_enabled
            else "Elevation Profiles Off",
            2.0,
        )

    def _cycle_playback_style(self, direction: int) -> None:
        """Cycle Time-Lapse and all standard transitions without losing position."""
        current_style = (
            "TIME_LAPSE"
            if self.time_lapse_active
            else ("RANDOM" if self.random_transition_mode else self.active_transition.value)
        )
        try:
            current_index = PLAYBACK_STYLE_VALUES.index(current_style)
        except ValueError:
            current_index = 0
        next_style = PLAYBACK_STYLE_VALUES[
            (current_index + (1 if direction >= 0 else -1)) % len(PLAYBACK_STYLE_VALUES)
        ]
        self._select_playback_style(next_style, show_status=False)
        label = next_style.replace("_", "-")
        debug_print(self.config, f"Playback style selected: {label}")
        self._update_window_titles(f"Style {label}")

    def _choose_random_transition_for_track(self) -> None:
        """Pick a fresh concrete photo transition for RANDOM mode at a new track."""
        if not self.random_transition_mode:
            return
        self.active_transition = random.choice(RANDOM_TRANSITIONS)
        self.transition_change_armed = True
        debug_print(self.config, f"RANDOM selected transition for new track: {self.active_transition.value}")

    def _presenter_for_role(self, presenter_name: str):
        """Return the currently routed presenter for one logical role."""
        if (
            self.config.mapwindow
            and not self.config.join_windows
            and self.map_presenter is not None
            and self.screen_swap
        ):
            return self.map_presenter if presenter_name == "photo" else self.photo_presenter
        return self.photo_presenter if presenter_name == "photo" else self.map_presenter

    def _refresh_photo_overlays(self) -> None:
        """Refresh the unified header without redrawing the current image."""
        if self.time_lapse_active and self.time_lapse_stage is not None:
            self._set_time_lapse_views()
            return
        photo_target = self.role_targets.get("photo")
        active_presenter = self._presenter_for_role("photo") if photo_target is not None else None
        if active_presenter is not None and photo_target is not None:
            header_lines = selected_stage_header_lines(
                photo_target.header_metadata,
                self.config,
                place_text=photo_target.place_text,
            )
            active_presenter.set_header(
                header_lines,
                (
                    photo_target.header_metrics
                    if getattr(self.config, "header_track_stats", True)
                    else ()
                ),
                photo_target.header_metadata,
                bool(self.header_visible),
                self.config.font_size,
                self.config.font_color,
                self.config.map_header_font_factor,
                getattr(self.config, "header_background", "black"),
                getattr(self.config, "header_shadow_color", COLOR_NAMES["black"]),
            )
            active_presenter.set_clock_time(
                photo_target.clock_time if self.header_visible and self.config.clock else None,
                photo_target.clock_date_text if self.header_visible and self.config.clock else None,
            )
        if self.photo_presenter is not None and self.photo_presenter is not active_presenter:
            self.photo_presenter.set_clock_time(None)
            self.photo_presenter.set_header((), (), None, False, self.config.font_size, self.config.font_color, self.config.map_header_font_factor, "off", self.config.header_shadow_color)
        if self.map_presenter is not None and self.map_presenter is not active_presenter:
            self.map_presenter.set_clock_time(None)
            self.map_presenter.set_header((), (), None, False, self.config.font_size, self.config.font_color, self.config.map_header_font_factor, "off", self.config.header_shadow_color)

    def _continue_time_lapse_after_navigation(self) -> None:
        """Resume animation after one arrow-key step without changing playback mode."""
        if self.manual_mode or self.paused or self.time_lapse_stage is None:
            return
        if getattr(self.time_lapse_stage, "relation", None) is not None:
            self._schedule_special_time_lapse_advance(
                self.time_lapse_media_remaining or self.config.duration
            )
            return
        self.time_lapse_last_tick = time.monotonic()
        self._time_lapse_tick()

    def _prepare_standard_stage_assets(self, stage_index: int) -> bool:
        """Load one stage's map context without displaying intermediate rows."""
        if not 0 <= stage_index < len(self.stages):
            return False
        stage = self.stages[stage_index]
        self._prime_context_before_index(stage.map_index)
        self.current_date = stage.date_text
        canonical = resolve_path(self._track_asset_dir(), stage.directive.filename)
        selected = resolve_track_map_variant(canonical, prefer_time_lapse=False) or canonical
        try:
            image = load_nsimage(selected)
        except Exception as exc:
            warn_message(f"could not load stage map {selected}: {exc}")
            return False
        metadata = try_read_plot_metadata(selected.with_suffix(".json"))
        self.current_track_path = selected
        self.current_elevation_profile_image = None
        self.current_track_metadata = metadata
        self.current_track_image = draw_dynamic_map_overlay(
            image,
            metadata,
            self.config,
            relation_title=stage.directive.relation,
        )
        self.current_stage_index = stage_index
        return True

    def _show_standard_stage_map_only(self, stage_index: int, *, immediate: bool = False) -> None:
        """Enter a stage at its Stage Map, including during backward navigation."""
        if not self._prepare_standard_stage_assets(stage_index):
            return
        stage = self.stages[stage_index]
        self.playlist_index = stage.map_index + 1
        self.current_display_index = stage.map_index
        self.pending_display_index = stage.map_index
        self.current_phase = PlaybackPhase.STAGE_MAP
        self.current_stage_media_position = None
        if self.map_presenter is not None:
            overview = (
                draw_overview_overlay(
                    self.current_overview_image,
                    self.current_overview_metadata,
                    self.current_track_metadata,
                    self.current_date,
                    self.config,
                )
                if self.current_overview_image is not None
                and self.current_overview_metadata is not None
                and self.current_track_metadata is not None
                else self.current_overview_image
            )
            targets = [
                WindowTarget(
                    "photo",
                    self._stage_map_with_elevation_profile(stage.directive.relation),
                    Transition.SWITCH,
                )
            ]
            if overview is not None:
                targets.append(
                    WindowTarget(
                        "map",
                        overview,
                        Transition.SWITCH if immediate else Transition.FADE,
                    )
                )
            next_callback = self._advance
        else:
            targets = [
                WindowTarget(
                    "photo",
                    self._stage_map_with_elevation_profile(stage.directive.relation),
                    Transition.SWITCH,
                )
            ]
            next_callback = self._show_standard_stage_overview
        self._display_state(
            DisplayState(
                targets=targets,
                next_callback=next_callback,
                auto_delay=self.config.duration,
                description="Stage map",
                playlist_index=self.playlist_index,
            )
        )

    def _show_standard_stage_media(self, stage_index: int, media_position: int) -> None:
        """Restore one exact medium with the correct map/date context."""
        if not self._prepare_standard_stage_assets(stage_index):
            return
        stage = self.stages[stage_index]
        if not 0 <= media_position < len(stage.media_indexes):
            self._show_standard_stage_map_only(stage_index)
            return
        row_index = stage.media_indexes[media_position]
        self._prime_context_before_index(row_index)
        self.playlist_index = row_index
        self.pending_display_index = None
        self._advance()

    def _start_time_lapse_at_media_row(
        self,
        stage_index: int,
        row_index: int,
        entry: PhotoListEntry,
    ) -> None:
        """Re-enter Time-Lapse at a medium reached without its map row."""
        stage = self.stages[stage_index]
        self._prime_context_before_index(stage.map_index)
        self.playlist_index = stage.map_index + 1
        self.pending_display_index = stage.map_index
        self.current_display_index = stage.map_index
        self._start_time_lapse_stage(
            stage.map_index,
            stage.directive.filename,
            resume_media=(row_index, entry),
            relation=stage.directive.relation,
        )

    def _jump_to_playlist_row(
        self,
        row_index: int,
        *,
        reconstruct_control: bool = True,
    ) -> bool:
        """Jump a running show to one exact control-file row."""
        row_index = int(row_index)
        if not 0 <= row_index < len(self.playlist_lines):
            return False
        if reconstruct_control and hasattr(self, "control_directives"):
            self._reconstruct_control_state_before(row_index)
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.time_lapse_stage is not None or self.time_lapse_handle is not None:
            self._cancel_time_lapse_stage()
        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is not None:
                presenter.cancel_pending()

        stage_index = stage_index_for_playlist_row(self.stages, row_index)
        stage = self.stages[stage_index] if stage_index is not None else None
        if stage is not None and row_index in stage.media_indexes:
            media_position = stage.media_indexes.index(row_index)
            self.music_controller.synchronize_row(row_index)
            if self.time_lapse_active:
                entry = parse_photo_entry(self.playlist_lines[row_index])
                self._start_time_lapse_at_media_row(
                    stage_index,
                    row_index,
                    entry,
                )
            else:
                self._show_standard_stage_media(stage_index, media_position)
            return True

        self.music_controller.synchronize_row(row_index - 1)
        self._prime_context_before_index(row_index)
        self.playlist_index = row_index
        self.pending_display_index = None
        self.current_display_index = None
        self._advance()
        return True

    def _step_forward(self) -> None:
        """Advance once while retaining the current automatic/manual mode."""
        debug_print(self.config, "Forward navigation requested")
        if self._cancel_control_pause(continue_after=True):
            return
        if self.awaiting_intro_start:
            self._begin_intro_playback()
            return
        if self.time_lapse_active and self.time_lapse_stage is not None:
            if getattr(self, "time_lapse_stage_map_preview_active", False):
                if self.active_callback is not None:
                    self.active_callback.cancel()
                    self.active_callback = None
                self._continue_after_time_lapse_stage_map()
                return
            if self.time_lapse_overview_preview_active:
                if self.active_callback is not None:
                    self.active_callback.cancel()
                    self.active_callback = None
                self._continue_after_time_lapse_overview()
                return
            if self.time_lapse_stage.relation is not None:
                if self.time_lapse_handle is not None:
                    self.time_lapse_handle.cancel()
                    self.time_lapse_handle = None
                self._advance_special_time_lapse()
                return
            if self.time_lapse_handle is not None:
                self.time_lapse_handle.cancel()
                self.time_lapse_handle = None
            if self.time_lapse_current_media is not None:
                self._end_time_lapse_media()
                self._continue_time_lapse_after_navigation()
                return
            if self.time_lapse_media_cursor < len(self.time_lapse_media_queue):
                fraction, row_index, entry = self.time_lapse_media_queue[self.time_lapse_media_cursor]
                self.time_lapse_media_cursor += 1
                self.time_lapse_progress = min(1.0, fraction)
                if not self._start_time_lapse_media(row_index, entry) and self.time_lapse_control_deferred:
                    return
                self._continue_time_lapse_after_navigation()
                return
            self.time_lapse_progress = 1.0
            self._finish_time_lapse_stage()
            return
        if not self.manual_mode:
            self.paused = False
            self.music_controller.set_slideshow_paused(False)
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.current_state is not None and self.current_state.next_callback is not None:
            self.current_state.next_callback()

    def _show_previous_time_lapse_stage_or_intro(self) -> None:
        """Show the final meaningful state of the previous stage or the Intro."""
        current_stage_index = self.current_stage_index
        if current_stage_index is not None and current_stage_index > 0:
            previous_stage = self.stages[current_stage_index - 1]
            self._cancel_time_lapse_stage()
            resume_media = (
                (
                    previous_stage.media_indexes[-1],
                    parse_photo_entry(
                        self.playlist_lines[previous_stage.media_indexes[-1]]
                    ),
                )
                if previous_stage.media_indexes
                else None
            )
            if resume_media is None:
                self.resume_phase_pending = PlaybackPhase.ELEVATION_PROFILE.value
            self._prime_context_before_index(previous_stage.map_index)
            self._start_time_lapse_stage(
                previous_stage.map_index,
                previous_stage.directive.filename,
                start_fraction=1.0 if resume_media is None else 0.0,
                resume_media=resume_media,
                relation=previous_stage.directive.relation,
            )
            return
        if self.intro_was_shown:
            self._cancel_time_lapse_stage()
            self._show_intro_phase(PlaybackPhase.INTRO_INFO)

    def _show_time_lapse_phase_before_motion(self) -> None:
        """Restore the last static stage phase before route motion/media."""
        self.time_lapse_progress = 0.0
        if self.time_lapse_current_media is not None:
            self._end_time_lapse_media(redraw=False)
        self.time_lapse_media_cursor = 0
        if self._current_elevation_profile() is not None:
            self._begin_time_lapse_stage_map_preview()
            return
        if should_show_stage_overview_preview(
            getattr(self, "map_presenter", None) is not None,
            getattr(self.config, "time_lapse_overview_on_stage_map_dual", True),
            0.0,
            False,
            self.time_lapse_stage.relation if self.time_lapse_stage is not None else None,
        ):
            self._show_time_lapse_overview_or_begin(
                0.0,
                None,
                self.time_lapse_stage.relation if self.time_lapse_stage is not None else None,
            )
            return
        self._show_previous_time_lapse_stage_or_intro()

    def _step_backward(self) -> None:
        """Restore the previous displayed state without changing playback mode."""
        debug_print(self.config, "Backward navigation requested")
        self._cancel_control_pause(continue_after=False)
        if self.time_lapse_active and self.time_lapse_stage is not None:
            if self.time_lapse_handle is not None:
                self.time_lapse_handle.cancel()
                self.time_lapse_handle = None
            if getattr(self, "time_lapse_stage_map_preview_active", False):
                if should_show_stage_overview_preview(
                    self.map_presenter is not None,
                    getattr(self.config, "time_lapse_overview_on_stage_map_dual", True),
                    0.0,
                    False,
                    self.time_lapse_stage.relation,
                ):
                    self.time_lapse_stage_map_preview_active = False
                    self.time_lapse_media_image = None
                    self.time_lapse_media_draw_frame = True
                    self._show_time_lapse_overview_or_begin(
                        0.0,
                        None,
                        self.time_lapse_stage.relation,
                    )
                else:
                    self._show_previous_time_lapse_stage_or_intro()
                return
            if self.time_lapse_overview_preview_active:
                if self.active_callback is not None:
                    self.active_callback.cancel()
                    self.active_callback = None
                self.time_lapse_overview_preview_active = False
                self.time_lapse_overview_inset_active = False
                self.time_lapse_media_image = None
                self._show_previous_time_lapse_stage_or_intro()
                return
            target_media_index = (
                self.time_lapse_media_cursor - 2
                if self.time_lapse_current_media is not None
                else self.time_lapse_media_cursor - 1
            )
            if target_media_index >= 0:
                self._end_time_lapse_media(redraw=False)
                fraction, row_index, entry = self.time_lapse_media_queue[target_media_index]
                self.time_lapse_progress = min(1.0, fraction)
                self.time_lapse_media_cursor = target_media_index + 1
                if not self._start_time_lapse_media(row_index, entry) and self.time_lapse_control_deferred:
                    return
                self._continue_time_lapse_after_navigation()
                return
            if self.time_lapse_stage.relation in {None, ""}:
                self._show_time_lapse_phase_before_motion()
                return
            if (
                self.time_lapse_stage.relation is not None
                and self.time_lapse_current_media is not None
            ):
                self._end_time_lapse_media(redraw=True)
                self.time_lapse_media_cursor = 0
                self._continue_time_lapse_after_navigation()
                return
            current_map_index = (
                self.time_lapse_stage.map_index
                if self.time_lapse_stage is not None
                else next(
                    (
                        index
                        for index in range(max(0, self.playlist_index - 1), -1, -1)
                        if parse_map_directive(self.playlist_lines[index]) is not None
                    ),
                    None,
                )
            )
            previous_map_index = (
                next(
                    (
                        index
                        for index in range(current_map_index - 1, -1, -1)
                        if parse_map_directive(self.playlist_lines[index]) is not None
                    ),
                    None,
                )
                if current_map_index is not None
                else None
            )
            if previous_map_index is None:
                debug_print(self.config, "No previous time-lapse stage available")
                return
            self._cancel_time_lapse_stage()
            self._prime_context_before_index(previous_map_index)
            previous_directive = parse_map_directive(self.playlist_lines[previous_map_index])
            if previous_directive is None:
                return
            previous_filename = previous_directive.filename
            previous_stage = self._collect_time_lapse_stage(
                previous_map_index,
                previous_filename,
                previous_directive.relation,
            )
            resume_media = (
                (previous_stage.media_indexes[-1], previous_stage.media_entries[-1])
                if previous_stage.media_entries
                else None
            )
            self._start_time_lapse_stage(
                previous_map_index,
                previous_filename,
                start_fraction=1.0 if resume_media is None else 0.0,
                resume_media=resume_media,
                relation=previous_directive.relation,
            )
            return
        if not self.manual_mode:
            self.paused = False
            self.music_controller.set_slideshow_paused(False)
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.current_phase == PlaybackPhase.INTRO_OVERVIEW and self.intro_was_shown:
            self._show_intro_phase(PlaybackPhase.INTRO_INFO)
            return
        if self.current_phase == PlaybackPhase.INTRO_INFO:
            return
        if self.current_stage_index is not None:
            stage = self.stages[self.current_stage_index]
            if self.current_phase == PlaybackPhase.MEDIA:
                media_position = self.current_stage_media_position
                if media_position is not None and media_position > 0:
                    self._show_standard_stage_media(
                        self.current_stage_index,
                        media_position - 1,
                    )
                    return
                self.playlist_index = stage.map_index + 1
                self._show_standard_stage_overview()
                return
            if self.current_phase == PlaybackPhase.STAGE_OVERVIEW:
                self._show_standard_stage_map_only(self.current_stage_index)
                return
            if self.current_phase == PlaybackPhase.STAGE_MAP:
                previous_stage_index = self.current_stage_index - 1
                if previous_stage_index >= 0:
                    previous_stage = self.stages[previous_stage_index]
                    if previous_stage.media_indexes:
                        self._show_standard_stage_media(
                            previous_stage_index,
                            len(previous_stage.media_indexes) - 1,
                        )
                    else:
                        self._show_standard_stage_map_only(previous_stage_index)
                    return
                if self.intro_was_shown:
                    self._show_intro_phase(PlaybackPhase.INTRO_INFO)
                    return
        current_index = self.current_display_index
        if current_index is None:
            current_index = self.playlist_index
        previous_index = previous_displayable_playlist_index(self.playlist_lines, current_index)
        if previous_index is None:
            debug_print(self.config, "No previous playlist entry available")
            return
        self._prime_context_before_index(previous_index)
        self.playlist_index = previous_index
        self.pending_display_index = None
        self._advance()

    def _available_screens(self) -> list[object]:
        """Return currently available macOS screens."""
        try:
            return list(NSScreen.screens())
        except Exception:
            return []

    def _apply_window_to_screen(self, window, screen, floating: bool, half_size: bool = False) -> None:
        """Place one window onto one screen."""
        if window is None or screen is None:
            return
        frame = screen.visibleFrame()
        if half_size:
            width = frame.size.width / 2.0
            height = frame.size.height / 2.0
            x_pos = frame.origin.x + frame.size.width - width
            y_pos = frame.origin.y
            window.setFrame_display_(NSMakeRect(x_pos, y_pos, width, height), True)
        else:
            window.setFrame_display_(frame, True)
        window.setLevel_(NSFloatingWindowLevel if floating else 0)
        window.orderFrontRegardless()

    def _configure_map_window_for_screens(self, screens: list[object]) -> None:
        """Keep a one-screen map visible while preserving dual-screen fullscreen."""
        if self.map_window is None or not screens:
            return
        parent_window = self.map_window.parentWindow()
        if len(screens) == 1:
            self.map_window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenAuxiliary)
            if self.photo_window is not None and parent_window is None:
                self.photo_window.addChildWindow_ordered_(self.map_window, NSWindowAbove)
        else:
            self.map_window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenPrimary)
            if parent_window is not None:
                parent_window.removeChildWindow_(self.map_window)

    def _apply_screen_layout(self) -> None:
        """Place slideshow windows onto screens according to current mode."""
        if self.config.join_windows or not self.config.mapwindow or self.map_window is None:
            if self.fullscreen_active and self.photo_window is not None:
                self.schedule_callback(0.1, lambda: self.photo_window.toggleFullScreen_(None))
            return

        screens = self._available_screens()
        if not screens:
            return

        primary_screen = screens[1] if len(screens) > 1 else screens[0]
        secondary_screen = screens[0]

        self._configure_map_window_for_screens(screens)

        self._apply_window_to_screen(self.photo_window, primary_screen, False)
        self._apply_window_to_screen(
            self.map_window,
            secondary_screen,
            len(screens) == 1,
            half_size=bool(len(screens) == 1),
        )

        if self.fullscreen_active:
            self.photo_window.makeKeyAndOrderFront_(None)
            self.schedule_callback(0.1, lambda: self.photo_window.toggleFullScreen_(None))
            if len(screens) > 1:
                self.map_window.orderFrontRegardless()
                self.schedule_callback(0.35, lambda: self.map_window.toggleFullScreen_(None))
            else:
                self.schedule_callback(
                    0.45,
                    lambda: self._apply_window_to_screen(self.map_window, screens[0], True, half_size=True),
                )

    def _swap_window_screens(self) -> None:
        """Swap routed content between the fixed photo/map windows."""
        if self.config.join_windows or not self.config.mapwindow or self.map_window is None:
            debug_print(self.config, "Screen swap ignored because no separate map window is active")
            return
        old_photo_target = self.role_targets.get("photo")
        old_map_target = self.role_targets.get("map")
        old_photo_presenter = self._presenter_for_role("photo")
        old_map_presenter = self._presenter_for_role("map")
        photo_image = getattr(old_photo_presenter, "current_image", None) or (old_photo_target.image if old_photo_target is not None else None)
        map_image = getattr(old_map_presenter, "current_image", None) or (old_map_target.image if old_map_target is not None else None)
        self.screen_swap = not self.screen_swap
        debug_print(self.config, f"Swapping routed window content: swapped={self.screen_swap}")
        self._update_window_titles()
        swap_targets = []
        if old_photo_target is not None and photo_image is not None:
            swap_targets.append(replace(old_photo_target, image=photo_image, transition=Transition.SWITCH))
        if old_map_target is not None and map_image is not None:
            swap_targets.append(replace(old_map_target, image=map_image, transition=Transition.SWITCH))
        if swap_targets:
            self._show_targets(swap_targets, on_complete=None)
            self.role_targets = {target.presenter_name: target for target in swap_targets}
            self._reset_photo_layouts(None, preserve_current_image=True)
        if self.time_lapse_stage is not None:
            self._set_time_lapse_views()

    def _toggle_fullscreen(self) -> None:
        """Toggle fullscreen/window mode."""
        self.fullscreen_active = not self.fullscreen_active
        debug_print(self.config, f"Toggling fullscreen: active={self.fullscreen_active}")
        self._refresh_header_layouts()
        if self.config.join_windows or not self.config.mapwindow or self.map_window is None:
            if self.photo_window is not None:
                self.photo_window.toggleFullScreen_(None)
                self.schedule_callback(0.05, self._refresh_header_layouts)
            return

        screens = self._available_screens()
        if self.photo_window is not None:
            self.photo_window.toggleFullScreen_(None)
            self.schedule_callback(0.05, self._refresh_header_layouts)
        if len(screens) > 1 and self.map_window is not None:
            self.schedule_callback(0.15, lambda: self.map_window.toggleFullScreen_(None))
        elif self.map_window is not None:
            self.map_window.setLevel_(NSFloatingWindowLevel if self.fullscreen_active else 0)
            if screens:
                self._apply_window_to_screen(self.map_window, screens[0], self.fullscreen_active, half_size=True)
            self.map_window.orderFrontRegardless()
        if not self.fullscreen_active:
            self.schedule_callback(0.3, self._apply_screen_layout)

    def _refresh_header_layouts(self) -> None:
        """Regenerate size-dependent header layers after window geometry changes."""
        if getattr(self, "time_lapse_active", False) and getattr(self, "time_lapse_stage", None) is not None:
            self._set_time_lapse_views()
            return
        for presenter in (
            getattr(self, "photo_presenter", None),
            getattr(self, "map_presenter", None),
        ):
            if presenter is not None:
                presenter.refresh_header_layout()

    def schedule_callback(self, delay_seconds: float, callback: Callable[[], None]):
        debug_print(self.config, f"Scheduling callback in {delay_seconds:.3f}s for {getattr(callback, '__name__', repr(callback))}")
        token = self._next_timer_token
        self._next_timer_token += 1
        target = TimerTarget.alloc().initWithCallback_owner_token_(callback, self, token)
        target.debug_config = self.config
        target.debug_context = getattr(callback, "__name__", repr(callback))
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.0, delay_seconds),
            target,
            "fire:",
            None,
            False,
        )
        handle = ScheduledCallback(timer, target, self, token)
        self.timer_handles[token] = handle
        return handle

    def _release_timer_handle(self, token: int) -> None:
        """Remove a completed/cancelled one-shot timer and its captured callback."""
        handle = self.timer_handles.pop(token, None)
        if handle is not None:
            handle.dispose()

    def window_will_close(self, window, role: str) -> None:
        """Close only the requested secondary view; the primary window quits."""
        if role == "map":
            self._deactivate_separate_map_window(window_already_closing=True)
            return
        if self.running:
            self.quit()

    def window_did_enter_fullscreen(self, role: str) -> None:
        """Arm a waiting first overview only after its native window is visible."""
        role = str(role)
        self.fullscreen_window_roles.add(role)
        self._refresh_header_layouts()
        self._finish_first_overview_fullscreen_wait(role)

    def window_did_exit_fullscreen(self, role: str) -> None:
        """Forget native fullscreen readiness after a window leaves that mode."""
        self.fullscreen_window_roles.discard(str(role))
        self._refresh_header_layouts()

    def window_did_resize(self, _role: str) -> None:
        """Immediately rebuild overlays for the window's new backing size."""
        self._refresh_header_layouts()

    def _first_overview_fullscreen_role(self, first_overview: bool) -> Optional[str]:
        """Return the stage-window role whose fullscreen transition is pending."""
        if not first_overview or not self.fullscreen_active:
            return None
        role = "map" if self.screen_swap and self.map_presenter is not None else "photo"
        return None if role in self.fullscreen_window_roles else role

    def _finish_first_overview_fullscreen_wait(self, role: str) -> None:
        """Redraw the first overview and start its timer after fullscreen settles."""
        if self.first_overview_waiting_for_fullscreen_role != str(role):
            return
        self.first_overview_waiting_for_fullscreen_role = None
        if (
            not self.time_lapse_active
            or not self.time_lapse_overview_preview_active
            or self.current_phase != PlaybackPhase.STAGE_OVERVIEW
        ):
            return
        self._set_time_lapse_views()
        stage_window = self.map_window if role == "map" else self.photo_window
        if stage_window is not None:
            stage_window.displayIfNeeded()
        if not self.manual_mode and not self.paused:
            self._schedule_callback(
                self.config.duration,
                self._continue_after_time_lapse_overview,
            )

    def _current_marked_overview_image(self):
        """Return the best retained overview for a newly shown map window."""
        if self.current_stage_overview_image is not None:
            return self.current_stage_overview_image
        if (
            self.current_overview_image is not None
            and self.current_overview_metadata is not None
            and self.current_track_metadata is not None
        ):
            return draw_overview_overlay(
                self.current_overview_image,
                self.current_overview_metadata,
                self.current_track_metadata,
                self.current_date,
                self.config,
            )
        return self.current_overview_image

    def _raise_time_lapse_map_view(self) -> None:
        """Reattach a parked Time-Lapse view above the presenter's image layers."""
        view = self.time_map_view
        if view is None:
            return
        host = view.superview()
        if host is None:
            return
        view.removeFromSuperview()
        view.setFrame_(host.bounds())
        host.addSubview_(view)
        view.setHidden_(False)
        view.setNeedsDisplay_(True)

    def _refresh_separate_map_window_content(self) -> None:
        """Populate a newly created or restored secondary window immediately."""
        if self.map_window is None:
            return
        if self.time_lapse_active and self.time_lapse_stage is not None:
            self._raise_time_lapse_map_view()
            self._set_time_lapse_views()
        else:
            if self.time_map_view is not None:
                self.time_map_view.setHidden_(True)
            overview_image = self._current_marked_overview_image()
            if self.map_presenter is not None:
                self.map_presenter.set_content_visible(True)
                if overview_image is not None:
                    self.map_presenter.transition_to(overview_image, Transition.SWITCH)
        content = self.map_window.contentView()
        content.setNeedsDisplay_(True)
        self.map_window.displayIfNeeded()

    def _create_separate_map_window(self, apply_layout: bool = True) -> None:
        """Create the optional overview/map window while playback continues."""
        if self.config.join_windows or getattr(self, "map_window", None) is not None:
            return
        set_runtime_map_window(self.config, True)
        restored = self.parked_map_resource is not None
        if restored:
            resource = self.parked_map_resource
            self.parked_map_resource = None
            self.map_window = resource["window"]
            self.map_presenter = resource["presenter"]
            self.time_map_view = resource["view"]
            if self.time_map_view is not None:
                self.time_map_view.setHidden_(False)
        else:
            self.map_window = self._create_window("GPSTrackShow - Maps", self.config.map_geometry, False)
            map_host = NSView.alloc().initWithFrame_(self.map_window.contentView().bounds())
            map_host.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            self.map_window.contentView().addSubview_(map_host)
            self._attach_time_lapse_view(map_host, "map")
            self.map_presenter = CocoaImagePresenter(
                map_host,
                self.config.background_color,
                self.schedule_callback,
                transition_duration_ms=self.config.transition_duration_ms,
            )
        self.map_window.makeKeyAndOrderFront_(None)
        self._update_window_titles()
        screens = self._available_screens()
        self._configure_map_window_for_screens(screens)
        if apply_layout and self.fullscreen_active and screens:
            target_screen = screens[0]
            self._apply_window_to_screen(
                self.map_window,
                target_screen,
                len(screens) == 1,
                half_size=len(screens) == 1,
            )
            if len(screens) > 1 and not restored:
                self.schedule_callback(0.15, lambda: self.map_window and self.map_window.toggleFullScreen_(None))
        elif apply_layout and not self.fullscreen_active:
            self._apply_screen_layout()

        self._refresh_separate_map_window_content()
        if self.map_window is not None:
            self.map_window.orderFrontRegardless()
            self.map_window.makeKeyWindow()
            self.app.activateIgnoringOtherApps_(True)
            # Fullscreen/window restoration is asynchronous in AppKit. Redraw
            # once more after it has settled so a parked window cannot remain black.
            self.schedule_callback(0.35, self._refresh_separate_map_window_content)

    def _dispose_retired_map_windows(self) -> None:
        """Clear heavyweight content without releasing bridged Cocoa wrappers."""
        for resource in self.retired_map_resources:
            if resource.get("disposed"):
                continue
            presenter = resource.get("presenter")
            retired_view = resource.get("view")
            delegate = resource.get("delegate")
            if delegate in self.window_delegates:
                self.window_delegates.remove(delegate)
            if presenter is not None:
                try:
                    presenter.dispose()
                except Exception:
                    pass
            if retired_view is not None:
                try:
                    retired_view._retire_content()
                except Exception:
                    pass
            resource["disposed"] = True

    def _close_retired_map_windows(self) -> None:
        """Close hidden map windows outside the keyboard event callback."""
        closed_any = False
        for resource in self.retired_map_resources:
            if resource.get("state") != "pending_close":
                continue
            window = resource.get("window")
            try:
                if window is not None:
                    window.close()
            except Exception:
                pass
            resource["state"] = "closed"
            closed_any = True
        if closed_any:
            self.schedule_callback(0.05, self._dispose_retired_map_windows)

    def _deactivate_separate_map_window(self, window_already_closing: bool = False) -> None:
        """Hide or retire the optional map window and continue in one window."""
        if self.config.join_windows:
            return
        getattr(self, "fullscreen_window_roles", set()).discard("map")
        window = getattr(self, "map_window", None)
        delegate = window.delegate() if window is not None else None
        presenter = self.map_presenter
        retired_view = self.time_map_view
        resource = {
            "window": window,
            "presenter": presenter,
            "delegate": delegate,
            "view": retired_view,
            "state": "closed" if window_already_closing else "pending_close",
            "disposed": False,
        }
        if window_already_closing and any(value is not None for value in (window, presenter, delegate, retired_view)):
            self.retired_map_resources.append(resource)
        set_runtime_map_window(self.config, False)
        self.screen_swap = False
        self.map_window = None
        self.map_presenter = None
        self.time_map_view = None
        if self.time_lapse_active and self.time_lapse_stage is not None:
            self._set_time_lapse_views()
        elif self.role_targets:
            self._show_targets(self._ordered_targets(self.role_targets), on_complete=None)
        self._update_window_titles("Single window")
        if window is not None and not window_already_closing:
            try:
                parent_window = window.parentWindow()
                if parent_window is not None:
                    parent_window.removeChildWindow_(window)
                window.orderOut_(None)
            except Exception:
                pass
            self.parked_map_resource = {
                "window": window,
                "presenter": presenter,
                "delegate": delegate,
                "view": retired_view,
            }
            return
        if window is None and presenter is None and delegate is None and retired_view is None:
            return
        if window_already_closing:
            self.schedule_callback(0.05, self._dispose_retired_map_windows)
            return
        # A programmatic `w` switch parks rather than closes the native window.
        # Only a window already being closed by AppKit reaches this point.

    def _toggle_window_mode(self) -> None:
        """Switch live between one window and a separate overview window."""
        if self.config.join_windows:
            self._show_temporary_status_overlay("Window switch unavailable in joined layout", 2.0)
            return
        if getattr(self, "map_window", None) is None:
            self._create_separate_map_window()
            self._show_temporary_status_overlay("Separate overview window", 2.0)
        else:
            self._deactivate_separate_map_window()
            self._show_temporary_status_overlay("Single window", 2.0)

    def _build_windows(self) -> None:
        debug_print(self.config, "Building Cocoa windows")
        if self.config.join_windows and self.config.mapwindow:
            self.photo_window = self._create_window("GPSTrackShow", self.config.photo_geometry, True)
            content_view = self.photo_window.contentView()
            split_view = NSSplitView.alloc().initWithFrame_(content_view.bounds())
            split_view.setVertical_(True)
            split_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            content_view.addSubview_(split_view)

            photo_host = NSView.alloc().initWithFrame_(content_view.bounds())
            photo_host.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            map_host = NSView.alloc().initWithFrame_(content_view.bounds())
            map_host.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            split_view.addSubview_(photo_host)
            split_view.addSubview_(map_host)

            self._attach_time_lapse_view(photo_host, "photo")
            self._attach_time_lapse_view(map_host, "map")
            self.photo_presenter = CocoaImagePresenter(
                photo_host,
                self.config.background_color,
                self.schedule_callback,
                self.config.collage_size_min,
                self.config.collage_size_max,
                self.config.collage_max_images,
                self.config.transition_duration_ms,
            )
            self.map_presenter = CocoaImagePresenter(
                map_host,
                self.config.background_color,
                self.schedule_callback,
                transition_duration_ms=self.config.transition_duration_ms,
            )
            debug_print(self.config, "Created joined photo/map window")
            self.photo_window.makeKeyAndOrderFront_(None)
            self._update_window_titles()
            if self.config.fullscreen:
                self.schedule_callback(0.1, lambda: self.photo_window.toggleFullScreen_(None))
            return

        self.photo_window = self._create_window("GPSTrackShow - Photos", self.config.photo_geometry, True)
        photo_host = NSView.alloc().initWithFrame_(self.photo_window.contentView().bounds())
        photo_host.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.photo_window.contentView().addSubview_(photo_host)
        self._attach_time_lapse_view(photo_host, "photo")
        self.photo_presenter = CocoaImagePresenter(
            photo_host,
            self.config.background_color,
            self.schedule_callback,
            self.config.collage_size_min,
            self.config.collage_size_max,
            self.config.collage_max_images,
            self.config.transition_duration_ms,
        )
        debug_print(self.config, "Created photo window")
        self.photo_window.makeKeyAndOrderFront_(None)
        self._update_window_titles()

        if self.config.mapwindow:
            self._create_separate_map_window(apply_layout=False)
            debug_print(self.config, "Created map window")

        self._apply_screen_layout()

    def _create_window(self, title: str, geometry: Optional[str], primary: bool):
        width, height, x_pos, y_pos = parse_geometry(geometry, DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        debug_print(self.config, f"Creating window '{title}' geometry={width}x{height}+{x_pos}+{y_pos}")
        rect = NSMakeRect(x_pos, y_pos, width, height)
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            style,
            NSBackingStoreBuffered,
            False,
        )
        # PyObjC wrappers can otherwise outlive AppKit's native object after
        # close(), producing a zombie lookup when a window is created again.
        window.setReleasedWhenClosed_(False)
        window.setTitle_(title)
        behavior = (
            NSWindowCollectionBehaviorFullScreenPrimary
            if primary
            else NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        window.setCollectionBehavior_(behavior)
        delegate = GPSTrackShowWindowDelegate.alloc().initWithController_role_(
            self,
            "photo" if primary else "map",
        )
        window.setDelegate_(delegate)
        self.window_delegates.append(delegate)
        return window

    def start(self, run_loop: bool = True) -> None:
        self.owns_run_loop = bool(run_loop)
        debug_print(self.config, "Starting application run loop" if self.owns_run_loop else "Starting embedded slideshow")
        if not self.time_lapse_active:
            self._prime_standard_resume_map_context()
            self._show_initial_photo_preview()
        self._run_memory_watchdog()
        self._poll_external_commands()
        if self.config.debug:
            self.music_controller.start(self.playlist_index)
            self._show_startup_hint()
            self._show_startup_test_image()
        elif self.intro_available:
            self.schedule_callback(0.0, self._start_intro_sequence)
        else:
            self.music_controller.start(self.playlist_index)
            self._show_startup_hint()
            self.schedule_callback(0.0, self._advance)
        self.app.activateIgnoringOtherApps_(True)
        if self.owns_run_loop:
            self.app.run()

    def _intro_summary_lines(self) -> list[str]:
        """Build the short facts shown between the title and description."""
        metadata = intro_metadata_from_playlist(self.playlist_lines)
        lines = []
        if metadata.get("date_range"):
            lines.append(str(metadata["date_range"]))
        total_distance = 0.0
        has_distance = False
        first_stage_place = None
        last_stage_place = None
        endpoint_stages = [
            stage for stage in self.stages if not stage.directive.is_special
        ] or list(self.stages)
        summary_by_map = self._summary_tracks_by_map_filename()
        for stage in endpoint_stages:
            row = summary_by_map.get(
                canonical_track_map_name(Path(stage.directive.filename).name)
            )
            distance = self._summary_track_length(row)
            if distance > 0.0:
                total_distance += distance
                has_distance = True
            start_place, destination = stage_name_endpoints(
                row.get("track_name") if isinstance(row, dict) else None
            )
            if first_stage_place is None and start_place:
                first_stage_place = start_place
            if destination:
                last_stage_place = destination
        if not summary_by_map and endpoint_stages:
            endpoint_metadata = []
            for stage in (endpoint_stages[0], endpoint_stages[-1]):
                canonical = resolve_path(self._track_asset_dir(), stage.directive.filename)
                selected = resolve_track_map_variant(canonical, prefer_time_lapse=False) or canonical
                endpoint_metadata.append(
                    try_read_plot_metadata(selected.with_suffix(".json"))
                )
            first_start, _first_destination = stage_name_endpoints(
                endpoint_metadata[0].get("track_name")
                if isinstance(endpoint_metadata[0], dict)
                else None
            )
            _last_start, final_destination = stage_name_endpoints(
                endpoint_metadata[-1].get("track_name")
                if isinstance(endpoint_metadata[-1], dict)
                else None
            )
            first_stage_place = first_start
            last_stage_place = final_destination
        first_place = first_stage_place or metadata.get("first_place")
        last_place = last_stage_place or metadata.get("last_place")
        if first_place and last_place:
            lines.append(
                str(first_place)
                if first_place == last_place
                else f"{first_place} - {last_place}"
            )
        if has_distance:
            lines.append(f"Total traveled: {total_distance:.1f} km")
        return lines

    def _start_intro_sequence(self) -> None:
        """Show the information panel, then the clean overview, on a fresh Start."""
        if not self.running:
            return
        startup_started = time.perf_counter()
        if self.current_overview_image is None:
            overview_line = next(
                (
                    line
                    for line in self.playlist_lines
                    if line.strip().startswith("#Overviewmap:")
                ),
                None,
            )
            if overview_line is not None:
                self._handle_overview(overview_line.partition(":")[2].strip())
        debug_print(
            self.config,
            f"Startup timing: overview ready after {time.perf_counter() - startup_started:.3f}s",
        )
        if self.current_overview_image is None:
            width = (
                self.photo_presenter.host_view.bounds().size.width
                if self.photo_presenter is not None
                else DEFAULT_WINDOW_WIDTH
            )
            height = (
                self.photo_presenter.host_view.bounds().size.height
                if self.photo_presenter is not None
                else DEFAULT_WINDOW_HEIGHT
            )
            clean_overview = make_blank_canvas(
                width,
                height,
                self.config.background_color,
            )
        else:
            clean_overview = self.current_overview_image
        self.intro_overview_image = clean_overview
        title = self.config.adventure_title or self.config.inputlist.stem
        title_image = None
        title_image_path = resolve_intro_title_image(
            self.config.adventure_title_image,
            self.config.photodir,
            self.playlist_lines,
        )
        if title_image_path is not None:
            try:
                title_image = load_media_preview(title_image_path)
            except Exception as exc:
                warn_message(f"could not load Intro title image {title_image_path}: {exc}")
        debug_print(
            self.config,
            f"Startup timing: title image ready after {time.perf_counter() - startup_started:.3f}s",
        )
        self.intro_information_image = draw_intro_information_panel(
            clean_overview,
            title,
            self.config.adventure_description,
            self._intro_summary_lines(),
            self.config,
            title_image=title_image,
        )
        debug_print(
            self.config,
            f"Startup timing: Intro composed after {time.perf_counter() - startup_started:.3f}s",
        )
        self.intro_was_shown = True
        self.awaiting_intro_start = True
        self._show_intro_phase(PlaybackPhase.INTRO_INFO)

    def _begin_intro_playback(self) -> None:
        """Advance the waiting title slide and begin normal timed playback."""
        if not self.awaiting_intro_start:
            return
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        self.awaiting_intro_start = False
        self._hide_startup_hint()
        self.music_controller.start(self.playlist_index)
        self.music_controller.set_slideshow_paused(False)
        self._show_intro_phase(PlaybackPhase.INTRO_OVERVIEW)

    def _show_intro_phase(self, phase: PlaybackPhase) -> None:
        """Display either Intro phase and retain deterministic navigation."""
        if phase == PlaybackPhase.INTRO_OVERVIEW:
            self._hide_startup_hint()
        if self.stages:
            self.playlist_index = self.stages[0].map_index
        if phase == PlaybackPhase.INTRO_INFO:
            image = self.intro_information_image or self.intro_overview_image
            next_callback = (
                self._begin_intro_playback
                if self.awaiting_intro_start
                else lambda: self._show_intro_phase(PlaybackPhase.INTRO_OVERVIEW)
            )
            description = "Adventure introduction"
        else:
            image = self.intro_overview_image
            next_callback = self._advance
            description = "Tour overview"
        if image is None:
            next_callback()
            return
        self.current_phase = phase
        self.current_stage_index = None
        self.current_stage_media_position = None
        self.pending_display_index = None
        targets = [WindowTarget("photo", image, Transition.SWITCH)]
        if self.map_presenter is not None and self.intro_overview_image is not None:
            targets.append(
                WindowTarget(
                    "map",
                    self.intro_overview_image,
                    Transition.SWITCH,
                )
            )
        self._display_state(
            DisplayState(
                targets=targets,
                next_callback=next_callback,
                auto_delay=(
                    None
                    if self.awaiting_intro_start
                    else self.config.duration
                ),
                description=description,
                playlist_index=self.playlist_index,
            )
        )
        if phase == PlaybackPhase.INTRO_INFO and self.awaiting_intro_start:
            self._show_startup_hint(
                bottom=True,
                wait_for_start=True,
            )
            self._schedule_callback(
                INTRO_AUTO_ADVANCE_SECONDS,
                self._begin_intro_playback,
            )

    def _restart_slideshow_from_title(self) -> None:
        """Reset transient playback state and replay the complete title sequence."""
        self.completed_replays += 1
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        self._cancel_time_lapse_stage()
        self.control_pause_active = False
        self.control_pause_resume_callback = None
        object.__setattr__(self.config, "duration", self.initial_duration)
        self._assign_playback_style(self.initial_playback_style)
        self.music_controller.set_slideshow_paused(True)
        self.music_controller.synchronize_row(-1)
        self.current_state = None
        self.current_display_index = None
        self.pending_display_index = None
        self.current_phase = None
        self.current_stage_index = None
        self.current_stage_media_position = None
        self.current_track_image = None
        self.current_track_path = None
        self.current_track_metadata = None
        self.current_elevation_profile_image = None
        self.playlist_index = self.start_playlist_index
        self._prime_context_before_index(self.start_playlist_index)
        self.completed_naturally = False
        self.intro_available = True
        self.intro_was_shown = False
        self.awaiting_intro_start = False
        self._start_intro_sequence()

    def _show_black_end_slide(self) -> None:
        """Finish naturally on a retained black slide until the user quits."""
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        self._cancel_time_lapse_stage()
        self.awaiting_intro_start = False
        self._hide_startup_hint()
        self.music_controller.set_slideshow_paused(True)
        self.completed_naturally = True
        self.current_phase = None
        self.current_stage_index = None
        self.current_stage_media_position = None
        self.current_display_index = None
        targets = []
        for role, presenter in (
            ("photo", self.photo_presenter),
            ("map", self.map_presenter),
        ):
            if presenter is None:
                continue
            bounds = presenter.host_view.bounds()
            targets.append(
                WindowTarget(
                    role,
                    make_blank_canvas(
                        bounds.size.width,
                        bounds.size.height,
                        COLOR_NAMES["black"],
                    ),
                    Transition.FADE,
                )
            )
        self._display_state(
            DisplayState(
                targets=targets,
                next_callback=None,
                auto_delay=None,
                description="End",
                playlist_index=len(self.playlist_lines),
            )
        )

    def _prime_standard_resume_map_context(self) -> None:
        """Restore the track-map role before resuming directly on a standard medium."""
        map_index = self.resume_standard_map_index_pending
        if map_index is None or self.map_presenter is None:
            return
        line = self.playlist_lines[map_index]
        directive = parse_map_directive(line)
        if directive is None:
            return
        canonical_path = resolve_path(self._track_asset_dir(), directive.filename)
        track_path = resolve_track_map_variant(canonical_path, prefer_time_lapse=False) or canonical_path
        try:
            self.current_track_image = load_nsimage(track_path)
            self.current_track_path = track_path
            self.current_elevation_profile_image = None
            self.current_track_metadata = try_read_plot_metadata(track_path.with_suffix(".json"))
        except Exception as exc:
            warn_message(f"could not restore track map for resume: {exc}")
            return
        self.role_targets["map"] = WindowTarget("map", self.current_track_image, Transition.SWITCH)

    def _memory_debug_text(self) -> str:
        """Build the compact top-of-window memory report."""
        return (
            f"Memory {format_memory_size(self.memory_current_bytes)} "
            f"(peak {format_memory_size(self.memory_peak_bytes)}) "
            f"| playlist {self.current_display_index if self.current_display_index is not None else '-'} "
            f"| timers {len(self.timer_handles)}"
        )

    def _set_memory_overlay_visible(self, visible: bool, warning: bool = False) -> None:
        """Update the memory monitor on each active slideshow presenter."""
        text = self._memory_debug_text() if visible else None
        if self.photo_presenter is not None:
            self.photo_presenter.set_memory_text(text, visible, warning)
        if self.map_presenter is not None:
            self.map_presenter.set_memory_text(text, visible, warning)

    def _toggle_memory_debug(self) -> None:
        """Toggle the persistent memory monitor with uppercase D."""
        self.memory_debug_visible = not self.memory_debug_visible
        self._set_memory_overlay_visible(self.memory_debug_visible, self.memory_current_bytes is not None and self.memory_current_bytes >= self.memory_warning_bytes)

    def _run_memory_watchdog(self) -> None:
        """Poll current resident memory and stop before it threatens the Mac."""
        if not self.running or self.memory_stop_requested:
            return
        current_bytes = current_process_resident_bytes()
        if current_bytes is not None:
            self.memory_current_bytes = current_bytes
            self.memory_peak_bytes = max(self.memory_peak_bytes, current_bytes)
            warning_active = current_bytes >= self.memory_warning_bytes
            if self.memory_debug_visible or warning_active:
                self._set_memory_overlay_visible(True, warning_active)
            if warning_active and not self.memory_warning_emitted:
                self.memory_warning_emitted = True
                warn_message(
                    f"slideshow memory is {format_memory_size(current_bytes)}; "
                    f"it will stop at {format_memory_size(self.memory_critical_bytes)}"
                )
            if current_bytes >= self.memory_critical_bytes:
                self.memory_stop_requested = True
                message = (
                    f"The slideshow reached {format_memory_size(current_bytes)} of resident memory.\n\n"
                    f"It will now close to protect this Mac. The safety limit is "
                    f"{format_memory_size(self.memory_critical_bytes)}."
                )
                warn_message(message.replace("\n", " "))
                alert = NSAlert.alloc().init()
                alert.setMessageText_("Slide show stopped for memory safety")
                alert.setInformativeText_(message)
                alert.addButtonWithTitle_("Close Slide Show")
                alert.runModal()
                self.quit()
                return
        self.memory_watchdog_handle = self.schedule_callback(
            MEMORY_WATCHDOG_INTERVAL_SECONDS,
            self._run_memory_watchdog,
        )

    def _hide_startup_hint(self) -> None:
        """Hide the startup hint and cancel its pending timeout."""
        if self.startup_hint_hide_handle is not None:
            self.startup_hint_hide_handle.cancel()
            self.startup_hint_hide_handle = None
        if self.photo_presenter is not None:
            self.photo_presenter.set_startup_hint_visible(False)

    def _show_startup_hint(
        self,
        *,
        bottom: bool = False,
        wait_for_start: bool = False,
    ) -> None:
        """Show the temporary startup help hint on the photo screen."""
        if self.photo_presenter is None:
            return
        if self.startup_hint_hide_handle is not None:
            self.startup_hint_hide_handle.cancel()
        self.photo_presenter.set_startup_hint_visible(
            True,
            bottom=bottom,
            wait_for_start=wait_for_start,
        )
        self.startup_hint_hide_handle = self.schedule_callback(
            HELP_OVERLAY_PERSISTENCE_SECONDS,
            self._hide_startup_hint,
        )

    def _show_initial_photo_preview(self) -> None:
        """If a separate map window exists, preload the first photo in the photo window."""
        if self.photo_presenter is None or self.map_presenter is None or self.config.join_windows:
            return
        for line in self.playlist_lines[self.playlist_index :]:
            photo_path = resolve_preview_photo_from_line(self.config.photodir, line)
            if photo_path is None:
                continue
            try:
                preview_image = load_media_preview(photo_path)
            except Exception as exc:
                warn_message(f"could not preload preview media {photo_path}: {exc}")
                continue
            debug_print(self.config, f"Preloading first media preview into photo window: {photo_path}")
            self.role_targets["photo"] = WindowTarget(
                "photo",
                preview_image,
                Transition.SWITCH,
                photo_identity=str(photo_path.resolve()),
            )
            self.startup_preview_identity = str(photo_path.resolve())
            self._show_targets(self._ordered_targets(self.role_targets), on_complete=None)
            return

    def _display_state(self, state: DisplayState) -> None:
        """Display one slideshow state without retaining older rendered images."""
        self.control_flow_steps = 0
        self.music_controller.set_video_active(
            any(target.presenter_name == "photo" and target.video_path is not None for target in state.targets)
        )
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        merged_targets = dict(self.role_targets)
        for target in state.targets:
            merged_targets[target.presenter_name] = target
        self.role_targets = merged_targets
        state.targets = self._ordered_targets(merged_targets)
        self.current_state = state
        if self.pending_display_index is not None:
            self.current_display_index = self.pending_display_index
        self.playlist_index = state.playlist_index
        self._update_window_titles(state.description)
        self._show_targets(
            state.targets,
            on_complete=(
                (lambda: self._schedule_callback(state.auto_delay, state.next_callback))
                if (
                    not self.manual_mode
                    and not self.paused
                    and state.next_callback is not None
                    and state.auto_delay is not None
                )
                else None
            ),
        )
        self._publish_live_state()

    def _ordered_targets(self, target_map: dict[str, WindowTarget]) -> list[WindowTarget]:
        """Return role targets in photo/map order."""
        ordered = []
        for role_name in ("photo", "map"):
            target = target_map.get(role_name)
            if target is not None:
                ordered.append(target)
        return ordered

    def _effective_photo_transition(self) -> Transition:
        """Return the transition currently active for newly shown photos."""
        return self.active_transition

    def _current_photo_identity(self) -> Optional[str]:
        """Return the identity of the photo currently routed to the photo role."""
        target = self.role_targets.get("photo")
        if target is None:
            return None
        return target.photo_identity

    def _blank_photo_image(self):
        """Return one blank image sized to the photo presenter."""
        if self.photo_presenter is not None:
            bounds = self.photo_presenter.host_view.bounds()
            return make_blank_canvas(bounds.size.width, bounds.size.height, self.config.background_color)
        return make_blank_canvas(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT, self.config.background_color)

    def _consume_pending_transition(self) -> Optional[Transition]:
        """Apply any deferred layout reset for a newly selected transition."""
        if not self.transition_change_armed:
            return None
        next_transition = self.active_transition
        self.transition_change_armed = False
        if next_transition == Transition.QUAD:
            self._reset_photo_layouts(Transition.QUAD)
        elif next_transition == Transition.COLLAGE:
            self._reset_photo_layouts(Transition.COLLAGE)
        else:
            self._reset_photo_layouts(None, preserve_current_image=True)
        self._update_window_titles(f"Transition {next_transition.value}")
        return next_transition

    def _display_photo_state_with_transition_change(
        self,
        final_state: DisplayState,
        map_image=None,
        info_text: Optional[str] = None,
    ) -> None:
        """Apply any armed transition and then display the next photo state directly."""
        self._consume_pending_transition()
        self._display_state(final_state)

    def _show_startup_test_image(self) -> None:
        """Display a generated test image before playlist playback."""
        debug_print(self.config, "Displaying generated startup test image")
        startup_image = create_debug_test_image()
        if self.map_presenter is not None and not self.config.join_windows:
            targets = [WindowTarget("map", startup_image, Transition.SWITCH)]
        else:
            targets = [WindowTarget("photo", startup_image, Transition.SWITCH)]
            if self.map_presenter is not None:
                targets.append(WindowTarget("map", startup_image, Transition.SWITCH))
        self._display_state(
            DisplayState(
                targets=targets,
                next_callback=self._advance,
                auto_delay=2.0,
                description="Startup test",
                playlist_index=self.playlist_index,
            )
        )

    def _collect_time_lapse_stage(
        self,
        map_index: int,
        filename: str,
        relation: Optional[str] = None,
    ) -> TimeLapseStage:
        stage_date_text = self.current_date
        date_text = stage_date_text
        media_indexes, media_entries, media_date_texts = [], [], []
        next_index = len(self.playlist_lines)
        for index in range(map_index + 1, len(self.playlist_lines)):
            line = self.playlist_lines[index]
            content = line.strip()
            if parse_map_directive(line) is not None:
                next_index = index
                break
            if content.startswith("#Datum:"):
                date_text = content.partition(":")[2].strip()
            elif not content.startswith("#"):
                media_indexes.append(index)
                media_entries.append(parse_photo_entry(line))
                media_date_texts.append(date_text)
        return TimeLapseStage(
            map_index,
            next_index,
            filename,
            stage_date_text,
            media_indexes,
            media_entries,
            media_date_texts,
            relation,
            date_text,
        )

    def _time_lapse_distance_before_stage(self, map_index: int) -> float:
        """Sum the route lengths of preceding control-file stages."""
        if map_index in self.stage_start_distance_cache:
            return self.stage_start_distance_cache[map_index]
        summary_by_map = self._summary_tracks_by_map_filename()
        total_km = 0.0
        for stage in self.stages:
            self.stage_start_distance_cache.setdefault(stage.map_index, total_km)
            if stage.map_index >= map_index:
                break
            directive = stage.directive
            if directive.is_special:
                continue
            canonical_name = canonical_track_map_name(Path(directive.filename).name)
            length = self._summary_track_length(summary_by_map.get(canonical_name))
            if length <= 0.0:
                if canonical_name not in self.stage_length_cache:
                    canonical_path = resolve_path(
                        self._track_asset_dir(),
                        directive.filename,
                    )
                    map_path = (
                        resolve_track_map_variant(
                            canonical_path,
                            prefer_time_lapse=True,
                        )
                        or canonical_path
                    )
                    metadata_path = map_path.with_suffix(".json")
                    try:
                        metadata = (
                            read_plot_metadata(metadata_path)
                            if metadata_path.is_file()
                            else None
                        )
                    except Exception:
                        metadata = None
                    self.stage_length_cache[canonical_name] = (
                        track_length_from_metadata(metadata)
                    )
                length = self.stage_length_cache[canonical_name]
            total_km += length
        self.stage_start_distance_cache[map_index] = total_km
        return total_km

    def _time_lapse_total_distance(self) -> float:
        """Return the known complete route length without opening per-stage sidecars."""
        if self.tour_total_distance_cache is not None:
            return self.tour_total_distance_cache
        summary_by_map = self._summary_tracks_by_map_filename()
        total_km = 0.0
        for stage in self.stages:
            if stage.directive.is_special:
                continue
            canonical_name = canonical_track_map_name(
                Path(stage.directive.filename).name
            )
            total_km += self._summary_track_length(
                summary_by_map.get(canonical_name)
            )
        self.tour_total_distance_cache = total_km
        return total_km

    def _time_lapse_media_datetime(
        self,
        entry: PhotoListEntry,
        points: list[dict],
        date_text: Optional[str],
    ) -> Optional[datetime]:
        """Resolve one media timestamp while retaining values beyond the track end."""
        reference_time = points[0].get("time") if points else None
        path = resolve_path(self.config.photodir, entry.source_name)
        metadata = try_read_photo_metadata(media_sidecar_path(path), path) or {}
        photo_time = parse_iso_datetime(metadata.get("datetime_iso"))
        if photo_time is None:
            photo_time = parse_control_datetime(date_text, entry.time_text, reference_time)
        if photo_time is None:
            return None
        if isinstance(reference_time, datetime):
            photo_time = align_datetime_timezone(photo_time, reference_time)
        return photo_time

    def _time_lapse_media_fraction(
        self,
        entry: PhotoListEntry,
        points: list[dict],
        date_text: Optional[str],
        photo_time: Optional[datetime] = None,
    ) -> Optional[float]:
        # Synthetic track times establish motion only; they cannot safely align
        # a real camera timestamp, so keep those media entries at stage end.
        if len(points) < 2 or all(point.get("estimated") for point in points):
            return None
        if photo_time is None:
            photo_time = self._time_lapse_media_datetime(entry, points, date_text)
        if photo_time is None:
            return None
        start, end = points[0]["time"], points[-1]["time"]
        total = (end - start).total_seconds()
        return 0.0 if total <= 0 else max(0.0, min(1.0, (photo_time - start).total_seconds() / total))

    def _set_time_lapse_views(self):
        special_stage = self.time_lapse_stage is not None and self.time_lapse_stage.relation is not None
        media_map_stage = (
            special_stage
            and isinstance(self.current_track_metadata, dict)
            and (
                self.current_track_metadata.get("map_kind") == "media"
                or scene_from_metadata(
                    self.current_track_metadata,
                    show_header=False,
                ).stage_kind == "media_stage"
            )
        )
        marker_state = None if special_stage else interpolate_timeline_state(
            self.time_lapse_points,
            self.time_lapse_progress,
        )
        arrow = None if marker_state is None else (marker_state["lat"], marker_state["lon"])
        stage_distance_km = 0.0 if marker_state is None else float(marker_state.get("stage_distance_km") or 0.0)
        if marker_state is not None and stage_distance_km <= 0.0 and self.time_lapse_progress > 0.0:
            stage_distance_km = track_length_from_metadata(self.current_track_metadata) * self.time_lapse_progress
        elevation_m = None if marker_state is None else safe_float(marker_state.get("elevation_m"))
        metrics_lines = format_time_lapse_metrics(
            self.time_lapse_stage_start_distance_km + stage_distance_km,
            stage_distance_km,
            elevation_m,
        ) if not special_stage else ()
        marker_time = None if marker_state is None else marker_state.get("time")
        media_time = None
        if self.time_lapse_current_media is not None:
            media_time = self.time_lapse_media_datetimes.get(self.time_lapse_current_media[0])
        display_time = time_lapse_clock_datetime(marker_time, self.time_lapse_progress, media_time)
        uses_late_media_time = isinstance(display_time, datetime) and display_time != marker_time
        if isinstance(display_time, datetime) and (
            uses_late_media_time or not all(point.get("estimated") for point in self.time_lapse_points)
        ):
            local_marker_time = display_time.astimezone() if display_time.tzinfo is not None else display_time
            self.time_lapse_clock_time = (local_marker_time.hour, local_marker_time.minute)
            self.time_lapse_clock_date_text = local_marker_time.strftime("%d.%m.%Y")
        else:
            self.time_lapse_clock_time = None
            self.time_lapse_clock_date_text = None
        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is not None:
                presenter.set_content_visible(False)
        stage_view, overview_view = self.time_photo_view, self.time_map_view
        if self.screen_swap and self.map_presenter is not None:
            stage_view, overview_view = overview_view, stage_view
        if stage_view is not None:
            stage_view.setHidden_(False)
            stage_view.marker_color = self.config.dot_color
            stage_view.marker_radius = self.config.dot_size
            stage_view.arrow_factor = self.config.arrow_length
            stage_view.marker_style = time_lapse_marker_style(
                self.config.time_lapse_marker,
                overview=False,
            )
            stage_view.route_visible = self.config.gpx_overlay_mode == "line"
            stage_view.route_color = self.config.route_color
            stage_view.route_width = self.config.route_width
            stage_view.media_min_fraction = self.config.time_lapse_media_min_fraction
            stage_view.media_draw_frame = self.time_lapse_media_draw_frame
            stage_view.media_marker_latlon = self.time_lapse_media_marker_latlon
            stage_view.media_marker_fixed_arrow = media_map_stage
            stage_view.place_text = None
            stage_view.header_lines = (
                selected_stage_header_lines(
                    self.current_track_metadata,
                    self.config,
                    place_text=self.time_lapse_place_text,
                    relation_title=(self.time_lapse_stage.relation if special_stage else None),
                )
                if self.header_visible
                else ()
            )
            stage_view.metrics_lines = (
                metrics_lines
                if self.header_visible and getattr(self.config, "header_track_stats", True)
                else ()
            )
            stage_view.header_background_style = (
                getattr(self.config, "header_background", "black")
                if self.header_visible
                else "off"
            )
            stage_view.metrics_total_distance_km = max(
                self._time_lapse_total_distance(),
                self.time_lapse_stage_start_distance_km + stage_distance_km,
            )
            stage_view.relation_title = None
            stage_view.overlay_font_size = self.config.font_size
            stage_view.overlay_font_color = self.config.font_color
            stage_view.overlay_background_color = self.config.background_color
            stage_view.overlay_shadow_color = self.config.header_shadow_color
            stage_view.map_header_font_factor = self.config.map_header_font_factor
            stage_view.header_row_count = 3
            stage_view.configureWithImage_metadata_routePoints_arrowLatLon_mediaImage_highlightRoute_(self.current_track_image, self.current_track_metadata, self.time_lapse_points, arrow, self.time_lapse_media_image, False)
            stage_view._update_clock_overlay(
                self.time_lapse_clock_time,
                self.time_lapse_clock_date_text,
                bool(self.header_visible and self.config.clock),
            )
            if self.time_lapse_video_view is not None and self.time_lapse_media_image is not None:
                old_superview = self.time_lapse_video_view.superview()
                if old_superview is not stage_view:
                    if old_superview is not None and hasattr(old_superview, "media_video_view"):
                        old_superview.media_video_view = None
                    self.time_lapse_video_view.removeFromSuperview()
                    stage_view.addSubview_(self.time_lapse_video_view)
                    stage_view.media_video_view = self.time_lapse_video_view
                    stage_view._raise_clock_overlay()
                _outer_rect, content_rect = stage_view.mediaRectsForImage_(self.time_lapse_media_image)
                self.time_lapse_video_view.setFrame_(content_rect)
        if overview_view is not None:
            overview_view.setHidden_(False)
            overview_view.marker_color = self.config.dot_color
            overview_view.marker_radius = self.config.dot_size
            overview_view.arrow_factor = self.config.arrow_length
            # The overview remains a conventional navigation map even when
            # the stage map uses the animated pilgrim.
            overview_view.marker_style = time_lapse_marker_style(
                self.config.time_lapse_marker,
                overview=True,
            )
            # The shared renderer already draws the complete highlighted stage;
            # this retained view adds only the moving/current marker.
            overview_view.route_visible = False
            overview_view.route_color = self.config.route_color
            overview_view.route_width = self.config.route_width
            overview_view.media_marker_latlon = (
                self.time_lapse_media_marker_latlon
                or self.time_lapse_stage_start_marker_latlon
            )
            overview_view.media_marker_fixed_arrow = media_map_stage
            overview_view.place_text = None
            overview_view.metrics_lines = (
                metrics_lines
                if self.header_visible and getattr(self.config, "header_track_stats", True)
                else ()
            )
            overview_view.header_lines = (
                selected_stage_header_lines(
                    self.current_track_metadata,
                    self.config,
                    place_text=self.time_lapse_place_text,
                    relation_title=(self.time_lapse_stage.relation if special_stage else None),
                )
                if self.header_visible
                else ()
            )
            overview_view.header_background_style = (
                getattr(self.config, "header_background", "black")
                if self.header_visible
                else "off"
            )
            overview_view.relation_title = None
            overview_view.overlay_font_size = self.config.font_size
            overview_view.overlay_font_color = self.config.font_color
            overview_view.overlay_background_color = self.config.background_color
            overview_view.overlay_shadow_color = self.config.header_shadow_color
            overview_view.map_header_font_factor = self.config.map_header_font_factor
            overview_view.header_row_count = 3
            overview_view.configureWithImage_metadata_routePoints_arrowLatLon_mediaImage_highlightRoute_(
                self.current_stage_overview_image or self.current_overview_image,
                self.current_overview_metadata,
                self.time_lapse_points,
                arrow,
                None,
                False,
            )
            overview_view._update_clock_overlay(
                self.time_lapse_clock_time,
                self.time_lapse_clock_date_text,
                bool(self.header_visible and self.config.clock),
            )
        self._publish_live_state()

    def _clear_time_lapse_views(self):
        self._stop_time_lapse_video()
        for view in (self.time_photo_view, self.time_map_view):
            if view is not None:
                view._update_clock_overlay(None, None, False)
                view.setHidden_(True)
        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is not None:
                presenter.set_content_visible(True)

    def _suspend_standard_playback_for_time_lapse(self):
        """Stop every standard timer/transition before exposing time-lapse views."""
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is not None:
                presenter.cancel_pending()
                presenter.set_content_visible(False)

    def _cancel_time_lapse_stage(self):
        """Cancel all callbacks and transient state owned by the active stage."""
        self.first_overview_waiting_for_fullscreen_role = None
        if self.time_lapse_handle is not None:
            self.time_lapse_handle.cancel()
            self.time_lapse_handle = None
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_stage_map_preview_active = False
        self._stop_time_lapse_video()
        self.time_lapse_current_media = None
        self.time_lapse_media_image = None
        self.time_lapse_media_draw_frame = True
        self.time_lapse_media_marker_latlon = None
        self.time_lapse_stage_start_marker_latlon = None
        self.time_lapse_clock_time = None
        self.time_lapse_clock_date_text = None
        self.time_lapse_place_text = None
        self.time_lapse_stage_start_distance_km = 0.0
        self.current_stage_overview_image = None
        self.time_lapse_media_deadline = None
        self.time_lapse_media_remaining = None
        self.time_lapse_stage = None
        self.time_lapse_points = []
        self.time_lapse_media_queue = []
        self.time_lapse_media_datetimes = {}
        self.time_lapse_media_cursor = 0
        self._clear_time_lapse_views()

    def _start_time_lapse_stage(
        self,
        map_index: int,
        filename: str,
        start_fraction: float = 0.0,
        resume_media: Optional[tuple[int, PhotoListEntry]] = None,
        relation: Optional[str] = None,
    ):
        if self.current_overview_image is None:
            raise RuntimeError("encountered #Map before #Overviewmap")
        self._suspend_standard_playback_for_time_lapse()
        self.time_lapse_stage = self._collect_time_lapse_stage(map_index, filename, relation)
        self.current_stage_index = stage_index_for_playlist_row(self.stages, map_index)
        self.current_stage_media_position = None
        self.time_lapse_audio_row_cursor = map_index
        self.time_lapse_control_row_cursor = map_index
        self._stop_time_lapse_video()
        self.time_lapse_current_media = None
        self.time_lapse_media_image = None
        self.time_lapse_media_draw_frame = True
        self.time_lapse_media_marker_latlon = None
        self.time_lapse_clock_time = None
        self.time_lapse_clock_date_text = None
        self.time_lapse_place_text = None
        self.time_lapse_media_deadline = None
        self.time_lapse_media_remaining = None
        self.current_date = self.time_lapse_stage.date_text
        canonical_track_path = resolve_path(self._track_asset_dir(), filename)
        track_path = resolve_track_map_variant(canonical_track_path, prefer_time_lapse=True) or canonical_track_path
        debug_print(self.config, f"Loading preferred time-lapse track map {track_path}")
        self.current_track_image = load_nsimage(track_path)
        self.current_track_path = track_path
        self.current_elevation_profile_image = None
        self.current_track_metadata = try_read_plot_metadata(track_path.with_suffix(".json"))
        self.current_track_image = draw_dynamic_map_overlay(
            self.current_track_image,
            self.current_track_metadata,
            self.config,
            show_header=False,
            reserve_place_row=True,
            omit_track_date=(
                self.config.clock
                and track_metadata_supports_clock(self.current_track_metadata)
            ),
            relation_title=relation,
            omit_media_date=(
                self.config.clock
                and track_metadata_supports_clock(self.current_track_metadata)
            ),
        )
        self.current_stage_overview_image = (
            draw_overview_overlay(
                self.current_overview_image,
                self.current_overview_metadata,
                self.current_track_metadata,
                self.current_date,
                self.config,
                header_background=False,
                show_caption=False,
            )
            if self.current_overview_metadata is not None
            and self.current_track_metadata is not None
            else self.current_overview_image
        )
        self.time_lapse_points = timed_points_from_metadata(self.current_track_metadata)
        self.time_lapse_stage_start_marker_latlon = first_media_coordinate(
            self.current_track_metadata
        )
        self.time_lapse_stage_start_distance_km = self._time_lapse_distance_before_stage(map_index)
        if relation is None and (
            len(self.time_lapse_points) < 2
            or all(point.get("estimated") for point in self.time_lapse_points)
        ):
            warn_message("Track map has no stored timing; using distance-based motion and showing untimed media at stage end.")
        self.time_lapse_progress = max(0.0, min(1.0, start_fraction))
        media_events = []
        self.time_lapse_media_datetimes = {}
        for row_index, entry, date_text in zip(
            self.time_lapse_stage.media_indexes,
            self.time_lapse_stage.media_entries,
            self.time_lapse_stage.media_date_texts,
        ):
            media_datetime = self._time_lapse_media_datetime(entry, self.time_lapse_points, date_text)
            if media_datetime is not None:
                self.time_lapse_media_datetimes[row_index] = media_datetime
            fraction = self._time_lapse_media_fraction(
                entry,
                self.time_lapse_points,
                date_text,
                photo_time=media_datetime,
            )
            media_events.append((fraction, row_index, entry))
        queue = (
            [(1.0, row_index, entry) for row_index, entry in zip(
                self.time_lapse_stage.media_indexes,
                self.time_lapse_stage.media_entries,
            )]
            if relation is not None
            else build_time_lapse_media_queue(media_events)
        )
        self.time_lapse_media_queue = queue
        self.time_lapse_media_cursor = next((index for index, item in enumerate(queue) if item[0] >= self.time_lapse_progress), len(queue))
        stored_resume_phase = self.resume_phase_pending
        if should_restart_time_lapse_stage_intro(
            stored_resume_phase,
            self.time_lapse_progress,
            relation,
        ):
            # A saved first medium at exactly 0% is still the stage boundary.
            # Restart its visible overview/profile sequence and let the queue
            # present that medium normally afterward.
            resume_media = None
        if resume_media is not None:
            for index, (fraction, row_index, entry) in enumerate(queue):
                if row_index == resume_media[0]:
                    self.time_lapse_progress = min(1.0, fraction)
                    self.time_lapse_media_cursor = index + 1
                    if not self._start_time_lapse_media(row_index, entry) and self.time_lapse_control_deferred:
                        return
                    break
        if relation is not None:
            self.time_lapse_progress = 1.0
        resume_phase = normalize_time_lapse_resume_phase(stored_resume_phase)
        self.resume_phase_pending = None
        if resume_phase == PlaybackPhase.TIME_LAPSE.value:
            if relation is not None:
                self._begin_special_time_lapse_stage(skip_map_delay=True)
            elif self.time_lapse_progress <= 0.0 and resume_media is None:
                self._show_time_lapse_overview_or_begin(
                    0.0,
                    None,
                    relation,
                )
            else:
                self._begin_time_lapse_motion()
            return
        if resume_phase == PlaybackPhase.STAGE_OVERVIEW.value:
            legacy_stage_map = (
                stored_resume_phase == PlaybackPhase.STAGE_MAP.value
            )
            self._show_time_lapse_overview_or_begin(
                0.0 if legacy_stage_map else start_fraction,
                None if legacy_stage_map else resume_media,
                relation,
            )
            return
        if resume_phase == PlaybackPhase.ELEVATION_PROFILE.value:
            self._begin_time_lapse_stage_map_preview()
            return
        self._show_time_lapse_overview_or_begin(
            start_fraction,
            resume_media,
            relation,
        )

    def _begin_time_lapse_stage_map_preview(self) -> None:
        """Show the elevation profile inset after the stage overview."""
        if self.time_lapse_stage is None:
            return
        profile = self._current_elevation_profile()
        if profile is None:
            self._continue_after_time_lapse_stage_map()
            return
        self.time_lapse_stage_map_preview_active = True
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_media_image = profile
        self.time_lapse_media_draw_frame = False
        self.current_phase = PlaybackPhase.ELEVATION_PROFILE
        self._set_time_lapse_views()
        self._update_window_titles("Elevation profile")
        if not self.manual_mode and not self.paused:
            self._schedule_callback(
                self.config.duration,
                self._continue_after_time_lapse_stage_map,
            )

    def _continue_after_time_lapse_stage_map(self) -> None:
        if self.time_lapse_stage is None or not self.time_lapse_active:
            return
        self.time_lapse_stage_map_preview_active = False
        self.time_lapse_media_image = None
        self.time_lapse_media_draw_frame = True
        if self.time_lapse_stage.relation is not None:
            self._begin_special_time_lapse_stage(
                skip_map_delay=self.time_lapse_current_media is not None
            )
            return
        self._begin_time_lapse_motion()

    def _show_time_lapse_overview_or_begin(
        self,
        start_fraction: float,
        resume_media: Optional[tuple[int, PhotoListEntry]],
        relation: Optional[str],
    ) -> None:
        show_stage_overview = should_show_stage_overview_preview(
            self.map_presenter is not None,
            getattr(self.config, "time_lapse_overview_on_stage_map_dual", True),
            start_fraction,
            resume_media is not None,
            relation,
        )
        if relation is not None and not show_stage_overview:
            self._begin_special_time_lapse_stage(skip_map_delay=resume_media is not None)
            return
        if show_stage_overview:
            first_overview = not self.time_lapse_overview_has_been_displayed
            self.time_lapse_overview_has_been_displayed = True
            self.time_lapse_overview_preview_active = True
            self.time_lapse_media_draw_frame = True
            self.current_phase = PlaybackPhase.STAGE_OVERVIEW
            if self.config.time_lapse_overview_as_media:
                self.time_lapse_overview_inset_active = True
                if self.current_overview_metadata is not None and self.current_track_metadata is not None:
                    self.time_lapse_media_image = draw_time_lapse_overview_media(
                        self.current_overview_image,
                        self.current_overview_metadata,
                        self.current_track_metadata,
                        self.time_lapse_points,
                        self.current_date,
                        self.config,
                    )
                else:
                    self.time_lapse_media_image = self.current_overview_image
                self.time_lapse_media_marker_latlon = None
                self.time_lapse_place_text = None
                self.time_lapse_last_tick = time.monotonic()
                self._set_time_lapse_views()
                if self.photo_window is not None:
                    self.photo_window.displayIfNeeded()
                self._update_window_titles("Stage overview")
                if not self.manual_mode and not self.paused:
                    waiting_role = self._first_overview_fullscreen_role(
                        first_overview
                    )
                    if waiting_role is not None:
                        self.first_overview_waiting_for_fullscreen_role = (
                            waiting_role
                        )
                        self._schedule_callback(
                            FIRST_STAGE_OVERVIEW_STARTUP_GRACE_SECONDS,
                            lambda role=waiting_role: (
                                self._finish_first_overview_fullscreen_wait(role)
                            ),
                        )
                    else:
                        self._schedule_callback(
                            time_lapse_overview_display_seconds(
                                self.config.duration,
                                first_overview,
                            ),
                            self._continue_after_time_lapse_overview,
                        )
                return
            if self.current_overview_metadata is not None and self.current_track_metadata is not None:
                overview_image = draw_overview_overlay(
                    self.current_overview_image,
                    self.current_overview_metadata,
                    self.current_track_metadata,
                    self.current_date,
                    self.config,
                )
            else:
                overview_image = self.current_overview_image
            self._clear_time_lapse_views()
            self._display_state(
                DisplayState(
                    targets=[WindowTarget("photo", overview_image, Transition.FADE)],
                    next_callback=self._continue_after_time_lapse_overview,
                    auto_delay=self.config.duration,
                    description="Stage overview",
                    playlist_index=self.playlist_index,
                )
            )
            return
        if relation is not None:
            self._begin_special_time_lapse_stage(skip_map_delay=resume_media is not None)
            return
        if (
            start_fraction <= 0.0
            and resume_media is None
            and self._current_elevation_profile() is not None
        ):
            self._begin_time_lapse_stage_map_preview()
        else:
            self._begin_time_lapse_motion()

    def _continue_after_time_lapse_overview(self) -> None:
        """Continue into animated or static playback after a stage overview."""
        self.first_overview_waiting_for_fullscreen_role = None
        if self.time_lapse_stage is None or not self.time_lapse_active:
            return
        if self.time_lapse_stage.relation is None:
            self.time_lapse_media_draw_frame = True
            if self._current_elevation_profile() is not None:
                self._begin_time_lapse_stage_map_preview()
            else:
                self._begin_time_lapse_motion()
            return
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        self.time_lapse_media_image = None
        self.time_lapse_media_marker_latlon = None
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_media_draw_frame = True
        self.current_state = None
        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is not None:
                presenter.cancel_pending()
                presenter.set_content_visible(False)
        self._begin_special_time_lapse_stage()

    def _begin_special_time_lapse_stage(self, skip_map_delay: bool = False) -> None:
        """Show an adjacent-day map statically before its sequential media."""
        if self.time_lapse_stage is None or self.time_lapse_stage.relation is None:
            return
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_stage_map_preview_active = False
        self.time_lapse_media_draw_frame = True
        self.current_phase = PlaybackPhase.TIME_LAPSE
        self.current_state = None
        self._set_time_lapse_views()
        self._update_window_titles(self.time_lapse_stage.relation)
        if self.manual_mode or self.paused:
            return
        if self.time_lapse_current_media is not None:
            delay = self.time_lapse_media_remaining or self.config.duration
        else:
            delay = 0.0 if skip_map_delay else self.config.duration
        self._schedule_special_time_lapse_advance(delay)

    def _schedule_special_time_lapse_advance(self, delay: float) -> None:
        if self.time_lapse_handle is not None:
            self.time_lapse_handle.cancel()
        self.time_lapse_handle = self.schedule_callback(
            max(0.0, delay),
            self._advance_special_time_lapse,
        )

    def _advance_special_time_lapse(self) -> None:
        """Advance a static adjacent-day stage by one medium."""
        self.time_lapse_handle = None
        if self.time_lapse_stage is None or self.time_lapse_stage.relation is None:
            return
        if self.time_lapse_current_media is not None:
            self._end_time_lapse_media(redraw=False)
        if self.time_lapse_media_cursor >= len(self.time_lapse_media_queue):
            self._finish_time_lapse_stage()
            return
        _fraction, row_index, entry = self.time_lapse_media_queue[self.time_lapse_media_cursor]
        self.time_lapse_media_cursor += 1
        if not self._start_time_lapse_media(row_index, entry):
            if self.time_lapse_control_deferred:
                return
            self._advance_special_time_lapse()
            return
        if not self.manual_mode and not self.paused:
            self._schedule_special_time_lapse_advance(
                self.time_lapse_media_remaining or self.config.duration
            )

    def _begin_time_lapse_motion(self) -> None:
        """Leave the optional single-window overview and animate the stage."""
        if self.time_lapse_stage is None or not self.time_lapse_active:
            return
        if self.time_lapse_overview_inset_active:
            self.time_lapse_media_image = None
            self.time_lapse_media_marker_latlon = None
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_stage_map_preview_active = False
        self.time_lapse_media_draw_frame = True
        self.current_phase = PlaybackPhase.TIME_LAPSE
        self.active_callback = None
        self.current_state = None
        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is not None:
                presenter.cancel_pending()
                presenter.set_content_visible(False)
        self.time_lapse_last_tick = time.monotonic()
        self._set_time_lapse_views()
        self._update_window_titles("Time-Lapse")
        if not self.paused and not self.manual_mode:
            self._time_lapse_tick()

    def _time_lapse_tick(self):
        if not self.running or not self.time_lapse_active or self.paused or self.manual_mode:
            return
        now = time.monotonic()
        elapsed = max(0.0, now - (self.time_lapse_last_tick or now))
        self.time_lapse_last_tick = now
        next_fraction = None
        if self.time_lapse_media_cursor < len(self.time_lapse_media_queue):
            next_fraction = self.time_lapse_media_queue[self.time_lapse_media_cursor][0]
        minimum_pending = time_lapse_media_minimum_pending(
            self.time_lapse_current_media is not None,
            self.time_lapse_media_deadline,
            now,
        )
        self.time_lapse_progress, event_due, _blocked = advance_time_lapse_progress(
            self.time_lapse_progress,
            elapsed,
            self.config.time_lapse_duration,
            next_fraction,
            minimum_pending,
        )
        if event_due:
            _fraction, row_index, entry = self.time_lapse_media_queue[self.time_lapse_media_cursor]
            self.time_lapse_media_cursor += 1
            if not self._start_time_lapse_media(row_index, entry) and self.time_lapse_control_deferred:
                return
            minimum_pending = time_lapse_media_minimum_pending(
                self.time_lapse_current_media is not None,
                self.time_lapse_media_deadline,
                time.monotonic(),
            )
        self._set_time_lapse_views()
        if (
            self.time_lapse_progress >= 1.0
            and self.time_lapse_media_cursor >= len(self.time_lapse_media_queue)
            and not minimum_pending
        ):
            if self.time_lapse_current_media is not None:
                self._end_time_lapse_media(redraw=False)
            self._finish_time_lapse_stage()
            return
        self.time_lapse_handle = self.schedule_callback(0.020, self._time_lapse_tick)

    def _start_time_lapse_media(
        self,
        row_index: int,
        entry: PhotoListEntry,
        *,
        controls_synced: bool = False,
    ) -> bool:
        if not controls_synced:
            state = {"asynchronous": False, "result": False}

            def continue_start() -> None:
                state["result"] = self._start_time_lapse_media(
                    row_index,
                    entry,
                    controls_synced=True,
                )
                if not state["asynchronous"] or not state["result"]:
                    return
                self.time_lapse_control_deferred = False
                if self.time_lapse_stage is None or not self.time_lapse_active:
                    return
                if self.time_lapse_stage.relation is not None:
                    if not self.manual_mode and not self.paused:
                        self._schedule_special_time_lapse_advance(
                            self.time_lapse_media_remaining or self.config.duration
                        )
                elif not self.manual_mode and not self.paused:
                    self.time_lapse_handle = self.schedule_callback(
                        0.020,
                        self._time_lapse_tick,
                    )

            deferred = self._sync_time_lapse_controls_through(
                row_index,
                continue_start,
            )
            state["asynchronous"] = deferred
            self.time_lapse_control_deferred = deferred
            return False if deferred else bool(state["result"])
        self.control_flow_steps = 0
        path = resolve_path(self.config.photodir, entry.source_name)
        try:
            image = load_media_preview(path)
            delay = video_duration_seconds(path) if is_video_path(path) else self.config.duration
        except Exception as exc:
            warn_message(f"could not load time-lapse media {path}: {exc}")
            return False
        self._stop_time_lapse_video()
        self._sync_time_lapse_audio_through(row_index)
        self.playlist_index = row_index + 1
        self.time_lapse_current_media = (row_index, entry)
        self.current_phase = PlaybackPhase.TIME_LAPSE
        if self.current_stage_index is not None:
            stage = self.stages[self.current_stage_index]
            try:
                self.current_stage_media_position = stage.media_indexes.index(row_index)
            except ValueError:
                self.current_stage_media_position = None
        self.time_lapse_media_image = image
        self.time_lapse_media_draw_frame = True
        metadata = try_read_photo_metadata(media_sidecar_path(path), path) or {}
        raw_place = metadata.get("place")
        if not isinstance(raw_place, str) or not raw_place.strip():
            raw_place = entry.place
        self.time_lapse_place_text = format_place_for_time_lapse(raw_place)
        if self.time_lapse_stage is not None and self.time_lapse_stage.relation is not None:
            latitude = safe_float(metadata.get("latitude"))
            longitude = safe_float(metadata.get("longitude"))
            if latitude is None or longitude is None:
                latitude, longitude = entry.latitude, entry.longitude
            self.time_lapse_media_marker_latlon = (
                (latitude, longitude)
                if latitude is not None and longitude is not None
                else None
            )
        else:
            self.time_lapse_media_marker_latlon = interpolate_timeline_point(
                self.time_lapse_points,
                self.time_lapse_progress,
            )
        self.time_lapse_media_remaining = max(0.1, delay)
        display_started = time.monotonic()
        self.time_lapse_media_deadline = None if self.manual_mode else display_started + self.time_lapse_media_remaining
        self.time_lapse_last_tick = display_started
        self._set_time_lapse_views()
        if is_video_path(path):
            self._play_time_lapse_video(path)
            if self.manual_mode and self.time_lapse_video_player is not None:
                self.time_lapse_video_player.pause()
        return True

    def _end_time_lapse_media(self, redraw: bool = True):
        self._stop_time_lapse_video()
        self.time_lapse_current_media = None
        self.time_lapse_media_image = None
        self.time_lapse_media_draw_frame = True
        self.time_lapse_media_marker_latlon = None
        self.time_lapse_clock_time = None
        self.time_lapse_clock_date_text = None
        self.time_lapse_place_text = None
        self.time_lapse_media_deadline = None
        self.time_lapse_media_remaining = None
        if redraw:
            self._set_time_lapse_views()

    def _finish_time_lapse_stage(self):
        if self.time_lapse_stage is None:
            return
        target_row = self.time_lapse_stage.next_index - 1
        if any(
            self.time_lapse_control_row_cursor < index <= target_row
            for index in self.control_directives
        ):
            self._sync_time_lapse_controls_through(
                target_row,
                self._finish_time_lapse_stage,
            )
            return
        self._sync_time_lapse_audio_through(self.time_lapse_stage.next_index - 1)
        next_date_text = self.time_lapse_stage.next_date_text
        self.time_lapse_overview_preview_active = False
        self.time_lapse_overview_inset_active = False
        self.time_lapse_stage_map_preview_active = False
        self.playlist_index = self.time_lapse_stage.next_index
        self.time_lapse_stage = None
        self.time_lapse_current_media = None
        self.time_lapse_media_image = None
        self.time_lapse_media_marker_latlon = None
        self.time_lapse_stage_start_marker_latlon = None
        self.time_lapse_clock_time = None
        self.time_lapse_clock_date_text = None
        self.time_lapse_place_text = None
        self.time_lapse_stage_start_distance_km = 0.0
        self.current_stage_overview_image = None
        self.time_lapse_media_deadline = None
        self.time_lapse_media_remaining = None
        self.time_lapse_media_datetimes = {}
        if next_date_text is not None:
            self.current_date = next_date_text
        self._clear_time_lapse_views()
        self._advance()

    def _sync_time_lapse_audio_through(self, row_index: int) -> None:
        """Execute every music directive consumed internally by a Time-Lapse stage."""
        target = max(0, min(int(row_index), len(self.playlist_lines) - 1))
        if target < self.time_lapse_audio_row_cursor:
            self.music_controller.synchronize_row(target)
            self.time_lapse_audio_row_cursor = target
            return
        for index in range(self.time_lapse_audio_row_cursor + 1, target + 1):
            if is_music_directive(self.playlist_lines[index]) or index == target:
                self.music_controller.synchronize_row(index)
        self.time_lapse_audio_row_cursor = target

    def _sync_time_lapse_controls_through(
        self,
        row_index: int,
        continuation: Callable[[], None],
    ) -> bool:
        """Execute CONTROL rows crossed internally by a Time-Lapse stage."""
        target = max(0, min(int(row_index), len(self.playlist_lines) - 1))
        indexes = [
            index
            for index in sorted(self.control_directives)
            if self.time_lapse_control_row_cursor < index <= target
        ]
        deferred = {"value": False}

        def process(position: int) -> None:
            if position >= len(indexes):
                self.time_lapse_control_row_cursor = max(
                    self.time_lapse_control_row_cursor,
                    target,
                )
                continuation()
                return
            index = indexes[position]
            self.time_lapse_control_row_cursor = index
            if self._execute_control_actions(
                self.control_directives[index],
                index,
                on_complete=lambda: process(position + 1),
            ):
                deferred["value"] = True

        process(0)
        return deferred["value"]

    def _stop_time_lapse_video(self):
        music_controller = getattr(self, "music_controller", None)
        if music_controller is not None:
            music_controller.set_video_active(False)
        if self.time_lapse_video_player is not None:
            try:
                self.time_lapse_video_player.pause()
            except Exception:
                pass
        self.time_lapse_video_player = None
        if self.time_lapse_video_view is not None:
            try:
                superview = self.time_lapse_video_view.superview()
                if superview is not None and hasattr(superview, "media_video_view"):
                    superview.media_video_view = None
                self.time_lapse_video_view.setPlayer_(None)
                self.time_lapse_video_view.removeFromSuperview()
            except Exception:
                pass
        self.time_lapse_video_view = None
        self.time_lapse_video_original_path = None
        self.time_lapse_video_playback_path = None

    def _play_time_lapse_video(self, path: Path):
        """Play a video inside the safe white-framed media region."""
        self._stop_time_lapse_video()
        if not AVKIT_VIDEO_AVAILABLE or AVPlayer is None or AVPlayerView is None:
            return
        stage_view = self.time_map_view if self.screen_swap and self.map_presenter is not None else self.time_photo_view
        if stage_view is None:
            return
        arrow = interpolate_timeline_point(self.time_lapse_points, self.time_lapse_progress)
        try:
            image_rect, scale = stage_view.imageRectAndScale()
            arrow_point = (
                stage_view.viewPointForLat_lon_imageRect_scale_(arrow[0], arrow[1], image_rect, scale)
                if arrow is not None
                else None
            )
            frame = stage_view.mediaRectForArrowPoint_(arrow_point)
            playback_path = self._video_playback_path(path)
            player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(playback_path)))
            player.setVolume_(self._video_gain())
            view = AVPlayerView.alloc().initWithFrame_(frame)
            if hasattr(view, "setControlsStyle_"):
                view.setControlsStyle_(AVPlayerViewControlsStyleNone)
            if hasattr(view, "setShowsFullScreenToggleButton_"):
                view.setShowsFullScreenToggleButton_(False)
            view.setPlayer_(player)
            stage_view.addSubview_(view)
            stage_view.media_video_view = view
            stage_view._raise_clock_overlay()
            self.time_lapse_video_player = player
            self.time_lapse_video_view = view
            self.time_lapse_video_original_path = Path(path)
            self.time_lapse_video_playback_path = Path(playback_path)
            self.music_controller.set_video_active(True)
            player.play()
        except Exception as exc:
            warn_message(f"could not play time-lapse video {path}: {exc}")

    def _replace_time_lapse_video_source(self, playback_path: Path) -> bool:
        if self.time_lapse_video_player is None or self.time_lapse_video_view is None:
            return False
        old_player = self.time_lapse_video_player
        try:
            seconds = float(CMTimeGetSeconds(old_player.currentTime())) if CMTimeGetSeconds is not None else 0.0
            was_playing = float(old_player.rate()) > 0.0
        except Exception:
            seconds = 0.0
            was_playing = True
        old_player.pause()
        player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(playback_path)))
        player.setVolume_(self._video_gain())
        if CMTimeMake is not None and math.isfinite(seconds) and seconds > 0.0:
            player.seekToTime_(CMTimeMake(int(round(seconds * 1000.0)), 1000))
        self.time_lapse_video_view.setPlayer_(player)
        self.time_lapse_video_player = player
        self.time_lapse_video_playback_path = Path(playback_path)
        if was_playing:
            player.play()
        return True

    def _handle_playlist_end(self) -> None:
        """Apply the configured natural or explicit end behavior."""
        if should_restart_slideshow(
            self.config.end_behavior,
            self.completed_replays,
        ):
            self._restart_slideshow_from_title()
        else:
            self._show_black_end_slide()

    def _cancel_control_pause(self, continue_after: bool = False) -> bool:
        """Cancel a #PAUSE timer and optionally run its continuation now."""
        if not getattr(self, "control_pause_active", False):
            return False
        callback = self.control_pause_resume_callback
        self.control_pause_active = False
        self.control_pause_resume_callback = None
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if continue_after and callback is not None:
            callback()
        return True

    def _schedule_control_pause(self, seconds: float, callback: Callable[[], None]) -> None:
        """Hold slideshow progression while intentionally leaving music running."""
        self._cancel_control_pause(False)
        self.control_pause_active = True
        self.control_pause_resume_callback = callback

        def finish_pause():
            if not self.control_pause_active:
                return
            continuation = self.control_pause_resume_callback
            self.control_pause_active = False
            self.control_pause_resume_callback = None
            self.active_callback = None
            if continuation is not None:
                continuation()

        self._schedule_callback(max(0.0, float(seconds)), finish_pause)
        self._show_temporary_status_overlay(f"Pause {float(seconds):g}s", 2.0)

    def _guard_control_flow(self) -> bool:
        self.control_flow_steps += 1
        if self.control_flow_steps <= self.control_non_display_limit:
            return True
        warn_message("slide-show control loop contains no displayable row or timed pause")
        self.manual_mode = True
        self._show_temporary_status_overlay("Control loop stopped", 3.0)
        return False

    def _goto_control_label(self, label: object) -> bool:
        target = self.control_labels.get(control_label_key(label))
        if target is None:
            warn_message(f"slide-show label ${label} is not defined")
            self._show_temporary_status_overlay(f"Label ${label} not found", 3.0)
            return False
        if not self._guard_control_flow():
            return True
        self._schedule_callback(
            0.0,
            lambda: self._jump_to_playlist_row(target, reconstruct_control=False),
        )
        return True

    def _execute_control_actions(
        self,
        directive: ControlDirective,
        row_index: int,
        action_index: int = 0,
        on_complete: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Execute ordered CONTROL actions; return True when playback was deferred."""
        actions = directive.actions
        position = int(action_index)
        while position < len(actions):
            action = actions[position]
            kind = action.kind
            if kind == "label":
                position += 1
                continue
            if kind == "duration":
                object.__setattr__(self.config, "duration", float(action.value))
                self._show_temporary_status_overlay(
                    f"Duration {float(action.value):g}s",
                    2.0,
                )
                position += 1
                continue
            if kind == "transition":
                target_style = normalize_control_transition(action.value)
                current_style = self._playback_style_name()
                next_row = row_index + 1
                if target_style != current_style:
                    if self.time_lapse_stage is not None and target_style != "TIME_LAPSE":
                        self._assign_playback_style(target_style)
                        def leave_time_lapse() -> None:
                            self._cancel_time_lapse_stage()
                            self.playlist_index = next_row
                            self._schedule_callback(0.0, self._advance)

                        self._execute_control_actions(
                            directive,
                            row_index,
                            position + 1,
                            leave_time_lapse,
                        )
                        return True
                    if self.time_lapse_stage is None and target_style == "TIME_LAPSE":
                        next_display_row = next(
                            (
                                index
                                for index in range(next_row, len(self.playlist_lines))
                                if parse_map_directive(self.playlist_lines[index]) is not None
                                or not self.playlist_lines[index].startswith("#")
                            ),
                            None,
                        )
                        if next_display_row is not None:
                            stage_index = stage_index_for_playlist_row(self.stages, next_display_row)
                            stage = self.stages[stage_index] if stage_index is not None else None
                            if stage is not None and next_display_row in stage.media_indexes:
                                self._assign_playback_style(target_style)
                                def enter_time_lapse() -> None:
                                    self._schedule_callback(
                                        0.0,
                                        lambda row=next_display_row: self._jump_to_playlist_row(
                                            row,
                                            reconstruct_control=False,
                                        ),
                                    )

                                self._execute_control_actions(
                                    directive,
                                    row_index,
                                    position + 1,
                                    enter_time_lapse,
                                )
                                return True
                        self._assign_playback_style(target_style)
                    else:
                        self._assign_playback_style(target_style)
                self._show_temporary_status_overlay(
                    f"Style {target_style.replace('_', '-')}",
                    2.0,
                )
                position += 1
                continue
            if kind == "pause":
                if not self._guard_control_flow():
                    return True
                if float(action.value) > 0.0:
                    self.control_flow_steps = 0
                completion = on_complete or self._advance
                continuation = lambda pos=position + 1: self._execute_control_actions(
                    directive,
                    row_index,
                    pos,
                    completion,
                )
                self._schedule_control_pause(float(action.value), continuation)
                return True
            if kind == "goto":
                return self._goto_control_label(action.value)
            if kind == "end":
                self._handle_playlist_end()
                return True
            position += 1
        if on_complete is not None:
            on_complete()
        return False

    def _advance(self) -> None:
        debug_print(self.config, f"Advance called at playlist index {self.playlist_index}")
        if not self.running:
            return
        self._cancel_control_pause(False)
        while self.running:
            if self.playlist_index >= len(self.playlist_lines):
                self._handle_playlist_end()
                return

            row_index = self.playlist_index
            line = self.playlist_lines[row_index]
            content = line.strip()
            self.music_controller.synchronize_row(row_index)
            self.playlist_index += 1
            debug_print(self.config, f"Processing line: {line}")
            if is_music_directive(content):
                if not self._guard_control_flow():
                    return
                continue
            if is_control_directive(content):
                directive = self.control_directives.get(row_index)
                if directive is None:
                    continue
                if not self._guard_control_flow():
                    return
                if self._execute_control_actions(directive, row_index):
                    return
                continue
            if content.startswith("#Overviewmap:"):
                self._handle_overview(content.partition(":")[2].strip())
                if not self._guard_control_flow():
                    return
                continue
            if content.startswith("#Datum:"):
                self.current_date = content.partition(":")[2].strip()
                if self.active_transition == Transition.QUAD:
                    self._reset_photo_layouts(Transition.QUAD)
                elif self.active_transition == Transition.COLLAGE:
                    self._reset_photo_layouts(Transition.COLLAGE)
                if not self._guard_control_flow():
                    return
                continue
            map_directive = parse_map_directive(line)
            if map_directive is not None:
                self.pending_display_index = row_index
                if self.time_lapse_active:
                    self.current_display_index = row_index
                    start_fraction = 0.0
                    resume_media = None
                    if self.resume_start_pending:
                        start_fraction = self.resume_progress_pending or 0.0
                        media_index = self.resume_media_index_pending
                        if (
                            media_index is not None
                            and 0 <= media_index < len(self.playlist_lines)
                            and not self.playlist_lines[media_index].startswith("#")
                        ):
                            resume_media = (media_index, parse_photo_entry(self.playlist_lines[media_index]))
                        self.resume_start_pending = False
                        self.resume_progress_pending = None
                        self.resume_media_index_pending = None
                    self.control_flow_steps = 0
                    self._start_time_lapse_stage(
                        row_index,
                        map_directive.filename,
                        start_fraction=start_fraction,
                        resume_media=resume_media,
                        relation=map_directive.relation,
                    )
                    return
                self.control_flow_steps = 0
                self._handle_map(map_directive.filename, map_directive.relation)
                self.resume_start_pending = False
                return
            entry = parse_photo_entry(line)
            if self.time_lapse_active and self.time_lapse_stage is None:
                stage_index = stage_index_for_playlist_row(self.stages, row_index)
                stage = self.stages[stage_index] if stage_index is not None else None
                if stage is not None and row_index in stage.media_indexes:
                    self.resume_start_pending = False
                    self.control_flow_steps = 0
                    self._start_time_lapse_at_media_row(
                        stage_index,
                        row_index,
                        entry,
                    )
                    return
            self.pending_display_index = row_index
            self.resume_start_pending = False
            self.control_flow_steps = 0
            self._handle_photo(entry)
            return

    def _handle_overview(self, filename: str) -> None:
        image_path = resolve_path(self._track_asset_dir(), filename)
        if self.current_overview_path == image_path and self.current_overview_image is not None:
            debug_print(self.config, f"Reusing overview image {image_path}")
            return
        self.compact_track_summary_loaded = False
        self.compact_track_summary = None
        self.summary_tracks_by_map_cache = None
        self.stage_start_distance_cache.clear()
        self.stage_length_cache.clear()
        self.tour_total_distance_cache = None
        debug_print(self.config, f"Loading overview image {image_path}")
        self.current_overview_image = load_nsimage(image_path)
        self.current_overview_path = image_path
        metadata_path = image_path.with_suffix(".json")
        self.current_overview_metadata = try_read_plot_metadata(metadata_path)
        if self.current_overview_metadata is not None:
            debug_print(self.config, f"Overview metadata loaded from {metadata_path}")
            self.current_overview_image = draw_dynamic_map_overlay(
                self.current_overview_image,
                self.current_overview_metadata,
                self.config,
                show_header=False,
            )
        else:
            debug_print(self.config, "Overview metadata missing; continuing without overview markers")

    def _handle_map(self, filename: str, relation_title: Optional[str] = None) -> None:
        if self.current_overview_image is None:
            raise RuntimeError("encountered #Map before #Overviewmap")
        self._choose_random_transition_for_track()
        if self.active_transition == Transition.QUAD:
            self._reset_photo_layouts(Transition.QUAD)
        elif self.active_transition == Transition.COLLAGE:
            self._reset_photo_layouts(Transition.COLLAGE)

        canonical_track_path = resolve_path(self._track_asset_dir(), filename)
        track_path = resolve_track_map_variant(canonical_track_path, prefer_time_lapse=False) or canonical_track_path
        debug_print(self.config, f"Loading track map {track_path}")
        self.current_track_image = load_nsimage(track_path)
        self.current_track_path = track_path
        self.current_elevation_profile_image = None
        metadata_path = track_path.with_suffix(".json")
        self.current_track_metadata = try_read_plot_metadata(metadata_path)
        if self.current_track_metadata is not None:
            debug_print(self.config, f"Track map metadata loaded from {metadata_path}")
            self.current_track_image = draw_dynamic_map_overlay(
                self.current_track_image,
                self.current_track_metadata,
                self.config,
                relation_title=relation_title,
            )
        else:
            debug_print(self.config, "Track map metadata missing; continuing without track markers")
        debug_print(self.config, "Creating rendered overview image for current stage")
        if self.current_overview_metadata is not None and self.current_track_metadata is not None:
            overview_image = draw_overview_overlay(
                self.current_overview_image,
                self.current_overview_metadata,
                self.current_track_metadata,
                self.current_date,
                self.config,
            )
        else:
            overview_image = self.current_overview_image
            warn_message("overview or stage metadata missing; skipping stage highlighting")
        debug_print(self.config, "Overview image ready; preparing stage display sequence")
        track_image = self._stage_map_with_elevation_profile(relation_title)

        if self.photo_presenter is not None:
            # Always show the overview as a full-screen single image on the photo side,
            # even when the active photo transition is QUAD or COLLAGE.
            self.photo_presenter.reset_photo_layout(None)

        stage_index = stage_index_for_playlist_row(
            self.stages,
            self.pending_display_index if self.pending_display_index is not None else self.playlist_index - 1,
        )
        self.current_stage_index = stage_index
        self.current_stage_media_position = None
        if self.resume_phase_pending == PlaybackPhase.STAGE_OVERVIEW.value:
            self.resume_phase_pending = None
            self._show_standard_stage_overview(overview_image)
            return
        self.resume_phase_pending = None
        if self.map_presenter is not None:
            self.current_phase = PlaybackPhase.STAGE_MAP
            targets = [
                WindowTarget("photo", track_image, self.active_transition),
                WindowTarget("map", overview_image, Transition.FADE),
            ]
            next_callback = self._advance
            description = "Stage map and Tour overview"
        else:
            self.current_phase = PlaybackPhase.STAGE_MAP
            targets = [WindowTarget("photo", track_image, self.active_transition)]
            next_callback = lambda: self._show_standard_stage_overview(overview_image)
            description = "Stage map"
        self._display_state(
            DisplayState(
                targets=targets,
                next_callback=next_callback,
                auto_delay=self.config.duration,
                description=description,
                playlist_index=self.playlist_index,
            )
        )

    def _show_standard_stage_overview(self, overview_image=None) -> None:
        """Show the marked Tour Overview after the Stage Map in one-window mode."""
        if overview_image is None:
            if self.current_overview_image is None:
                self._advance()
                return
            if self.current_overview_metadata is not None and self.current_track_metadata is not None:
                overview_image = draw_overview_overlay(
                    self.current_overview_image,
                    self.current_overview_metadata,
                    self.current_track_metadata,
                    self.current_date,
                    self.config,
                )
            else:
                overview_image = self.current_overview_image
        self.current_phase = PlaybackPhase.STAGE_OVERVIEW
        self.current_stage_media_position = None
        self._display_state(
            DisplayState(
                targets=[WindowTarget("photo", overview_image, Transition.FADE)],
                next_callback=self._advance,
                auto_delay=self.config.duration,
                description="Tour overview",
                playlist_index=self.playlist_index,
            )
        )

    def _handle_photo(self, entry: PhotoListEntry) -> None:
        photo_path = resolve_path(self.config.photodir, entry.source_name)
        row_index = (
            self.pending_display_index
            if self.pending_display_index is not None
            else max(0, self.playlist_index - 1)
        )
        self.current_stage_index = stage_index_for_playlist_row(self.stages, row_index)
        self.current_phase = PlaybackPhase.MEDIA
        self.current_stage_media_position = None
        if self.current_stage_index is not None:
            stage = self.stages[self.current_stage_index]
            try:
                self.current_stage_media_position = stage.media_indexes.index(row_index)
            except ValueError:
                pass
        debug_print(self.config, f"Handling photo entry source={entry.source_name} resolved={photo_path}")
        metadata_path: Optional[Path] = None
        if photo_path.suffix.lower() == ".json":
            try:
                metadata_path = photo_path
                photo_path, photo_metadata = resolve_photo_from_json(self.config.photodir, photo_path)
                debug_print(self.config, f"Resolved JSON-controlled photo path to {photo_path}")
            except Exception as exc:
                warn_message(f"could not use JSON playlist entry {photo_path}: {exc}")
                self._schedule_next(self.config.duration)
                return
        else:
            metadata_path = media_sidecar_path(photo_path)
            photo_metadata = try_read_photo_metadata(metadata_path, photo_path) or {}
            if photo_metadata:
                debug_print(self.config, f"Loaded photo metadata from {metadata_path}")
            else:
                debug_print(self.config, "Photo metadata missing; falling back to list coordinates only")

        video_path = photo_path if is_video_path(photo_path) else None
        video_delay = self.config.duration
        try:
            if video_path is not None:
                photo_image = load_video_first_frame(video_path)
                try:
                    video_delay = video_duration_seconds(video_path)
                except Exception as exc:
                    warn_message(f"could not determine video duration for {video_path}; using configured duration: {exc}")
                    video_delay = self.config.duration
                debug_print(self.config, f"Loaded video first frame {video_path}; duration={video_delay:.2f}s")
            else:
                photo_image = load_nsimage(photo_path)
                debug_print(self.config, f"Loaded photo image {photo_path}")
        except Exception as exc:
            warn_message(f"could not load media {photo_path}: {exc}")
            self._schedule_next(self.config.duration)
            return
        info_text = build_photo_info_text(photo_path, metadata_path, photo_metadata, entry)
        latitude = safe_float(photo_metadata.get("latitude"))
        longitude = safe_float(photo_metadata.get("longitude"))
        if latitude is None or longitude is None:
            latitude = entry.latitude
            longitude = entry.longitude
        debug_print(self.config, f"Photo coordinates lat={latitude} lon={longitude}")

        clock_time = parse_clock_time(entry.time_text)
        if clock_time is None:
            clock_time = parse_clock_time(photo_metadata.get("time"))
        clock_date_text = derive_clock_date_text(photo_metadata, self.current_date)
        if self.config.clock and clock_time is not None:
            debug_print(
                self.config,
                f"Showing clock overlay for time {clock_time[0]:02d}:{clock_time[1]:02d} "
                f"(source={'playlist' if parse_clock_time(entry.time_text) is not None else 'metadata'})",
            )
        transition_change_pending = self.transition_change_armed
        place_text = None
        raw_place = photo_metadata.get("place")
        if not isinstance(raw_place, str) or not raw_place.strip():
            raw_place = entry.place
        if isinstance(raw_place, str):
            cleaned_place = raw_place.strip()
            if cleaned_place and cleaned_place.lower() not in {"kein ort", "unknown"}:
                place_text = format_place_for_slideshow(cleaned_place)
        photo_transition = self._effective_photo_transition()
        photo_identity = str(photo_path.resolve())

        header_lines = selected_stage_header_lines(
            self.current_track_metadata,
            self.config,
            place_text=place_text,
        )
        distance_before_stage = 0.0
        if self.current_stage_index is not None and 0 <= self.current_stage_index < len(self.stages):
            distance_before_stage = self._time_lapse_distance_before_stage(
                self.stages[self.current_stage_index].map_index
            )
        # Retain available statistics even when currently hidden so a live
        # Settings Apply can reveal them without advancing to another medium.
        header_metrics = photo_track_metrics(
            self.current_track_metadata,
            latitude,
            longitude,
            distance_before_stage,
            parse_iso_datetime(photo_metadata.get("datetime_iso")),
        )

        def make_photo_target(transition: Transition) -> WindowTarget:
            return WindowTarget(
                presenter_name="photo",
                image=photo_image,
                transition=transition,
                clock_time=clock_time,
                clock_date_text=clock_date_text,
                place_text=place_text,
                info_text=info_text,
                photo_identity=photo_identity,
                video_path=video_path,
                video_duration=video_delay if video_path is not None else None,
                header_lines=header_lines,
                header_metrics=header_metrics,
                header_metadata=self.current_track_metadata,
            )

        preview_duplicate = (
            not self.transition_change_armed
            and self.startup_preview_identity == photo_identity
            and self._current_photo_identity() == photo_identity
        )
        if preview_duplicate:
            debug_print(self.config, f"Photo {photo_path.name} already visible; suppressing duplicate transition")
            photo_transition = Transition.SWITCH
            self.startup_preview_identity = None

        marked_track = None
        needs_marked_track = self.map_presenter is not None or self.config.track_map_before_media
        if self.current_track_image is not None and needs_marked_track:
            marked_track = self.current_track_image
            if self.current_track_metadata is not None and latitude is not None and longitude is not None:
                pixel_x, pixel_y = coordinate_to_pixel(latitude, longitude, self.current_track_metadata)
                scaled_x, scaled_y = scale_metadata_pixel_to_image(
                    pixel_x,
                    pixel_y,
                    self.current_track_metadata,
                    self.current_track_image,
                )
                debug_print(
                    self.config,
                    f"Drawing track marker at metadata pixel x={pixel_x:.1f} y={pixel_y:.1f} scaled x={scaled_x:.1f} y={scaled_y:.1f}",
                )
                track_width, track_height = image_size_tuple(self.current_track_image)
                debug_print(
                    self.config,
                    f"Track image size width={track_width:.1f} height={track_height:.1f} marker_in_bounds={0 <= scaled_x <= track_width and 0 <= scaled_y <= track_height}",
                )
                if not (0 <= scaled_x <= track_width and 0 <= scaled_y <= track_height):
                    warn_message(
                        f"photo marker for {photo_path.name} lies outside track image bounds: "
                        f"x={scaled_x:.1f}, y={scaled_y:.1f}, width={track_width:.1f}, height={track_height:.1f}"
                    )
                if self.current_track_metadata.get("map_kind") == "media":
                    marked_track = draw_media_location_marker(
                        marked_track,
                        scaled_x,
                        scaled_y,
                        self.config.dot_color,
                        self.config.dot_size,
                        self.config.arrow_length,
                    )
                else:
                    marked_track = draw_track_photo_marker(
                        marked_track,
                        scaled_x,
                        scaled_y,
                        self.current_track_metadata,
                        self.config.dot_color,
                        self.config.dot_size,
                        self.config.arrow_length,
                        self.config,
                    )
            elif self.current_track_metadata is None:
                warn_message("track metadata missing; skipping photo position marker on track map")

        if self.map_presenter is not None and marked_track is not None:
            final_state = DisplayState(
                targets=[
                    make_photo_target(photo_transition),
                    WindowTarget("map", marked_track, self.config.transition),
                ],
                next_callback=self._advance,
                auto_delay=video_delay if video_path is not None else self.config.duration,
                description=f"Photo {photo_path.name}",
                playlist_index=self.playlist_index,
            )
            self._display_photo_state_with_transition_change(final_state, map_image=marked_track, info_text=info_text)
            return

        if marked_track is not None and self.config.track_map_before_media:
            if transition_change_pending:
                final_state = DisplayState(
                    targets=[make_photo_target(photo_transition)],
                    next_callback=self._advance,
                    auto_delay=video_delay if video_path is not None else self.config.duration,
                    description=f"Photo {photo_path.name}",
                    playlist_index=self.playlist_index,
                )
                self._display_photo_state_with_transition_change(final_state, info_text=info_text)
                return

            preview_seconds = min(self.config.duration, max(1.0, self.config.duration / 2.0))

            def show_photo() -> None:
                final_state = DisplayState(
                    targets=[make_photo_target(photo_transition)],
                    next_callback=self._advance,
                    auto_delay=video_delay if video_path is not None else self.config.duration,
                    description=f"Photo {photo_path.name}",
                    playlist_index=self.playlist_index,
                )
                self._display_photo_state_with_transition_change(final_state, info_text=info_text)

            self._display_state(
                DisplayState(
                    targets=[WindowTarget("photo", marked_track, Transition.SWITCH)],
                    next_callback=show_photo,
                    auto_delay=preview_seconds,
                    description="Track marker preview",
                    playlist_index=self.playlist_index,
                )
            )
            return

        final_state = DisplayState(
            targets=[make_photo_target(photo_transition)],
            next_callback=self._advance,
            auto_delay=video_delay if video_path is not None else self.config.duration,
            description=f"Photo {photo_path.name}",
            playlist_index=self.playlist_index,
        )
        self._display_photo_state_with_transition_change(final_state, info_text=info_text)

    def _show_targets(self, targets: list[WindowTarget], on_complete: Optional[Callable[[], None]] = None) -> None:
        debug_print(
            self.config,
            "Displaying targets: " + ", ".join(f"{target.presenter_name}:{target.transition}" for target in targets),
        )

        def done() -> None:
            # A transition completion queued just before T must never restart
            # standard playlist advancement underneath time-lapse playback.
            time_lapse_running = self.time_lapse_active and self.time_lapse_stage is not None
            if on_complete is not None and slideshow_transition_completion_allowed(
                time_lapse_running,
                self.time_lapse_overview_preview_active,
            ):
                on_complete()

        active_photo_presenter = None
        active_info_presenter = None
        for target in targets:
            if (
                self.config.mapwindow
                and not self.config.join_windows
                and self.map_presenter is not None
                and self.screen_swap
            ):
                presenter = self.map_presenter if target.presenter_name == "photo" else self.photo_presenter
            else:
                presenter = self.photo_presenter if target.presenter_name == "photo" else self.map_presenter
            if presenter is None:
                continue
            if target.presenter_name == "photo":
                active_photo_presenter = presenter
                active_info_presenter = presenter
            debug_print(self.config, f"Sending image to presenter '{target.presenter_name}' with transition {target.transition}")
            effective_transition = Transition.SWITCH if target.presenter_name == "map" else target.transition
            full_window_header = target.presenter_name == "photo"
            presenter.set_header_reference_image(
                target.image,
                effective_transition in {Transition.COLLAGE, Transition.QUAD},
            )
            presenter.set_header(
                target.header_lines,
                (
                    target.header_metrics
                    if getattr(self.config, "header_track_stats", True)
                    else ()
                ),
                target.header_metadata,
                bool(full_window_header and self.header_visible),
                self.config.font_size,
                self.config.font_color,
                self.config.map_header_font_factor,
                getattr(self.config, "header_background", "black"),
                getattr(self.config, "header_shadow_color", COLOR_NAMES["black"]),
            )
            presenter.set_clock_time(
                target.clock_time if full_window_header and self.header_visible and self.config.clock else None,
                target.clock_date_text if full_window_header and self.header_visible and self.config.clock else None,
            )
            presenter.set_place_text(None, False, self.config.font_size, self.config.font_color)
            presenter.set_info_text(target.info_text if target.presenter_name == "photo" else None)
            if target.presenter_name == "photo":
                def photo_complete(current_presenter=presenter, current_target=target, transition=effective_transition) -> None:
                    time_lapse_running = self.time_lapse_active and self.time_lapse_stage is not None
                    if not slideshow_transition_completion_allowed(
                        time_lapse_running,
                        self.time_lapse_overview_preview_active,
                    ):
                        return
                    if current_target.video_path is not None:
                        frame_rect = current_presenter.last_media_rect if transition in {Transition.COLLAGE, Transition.QUAD} else None
                        current_presenter.play_video(
                            current_target.video_path,
                            frame_rect,
                            playback_path=self._video_playback_path(current_target.video_path),
                            volume=self._video_gain(),
                        )
                    done()

                presenter.transition_to(
                    target.image,
                    effective_transition,
                    photo_complete if (on_complete is not None or target.video_path is not None) else None,
                    media_is_video=target.video_path is not None,
                )
            else:
                presenter.transition_to(target.image, effective_transition)
        self.active_photo_presenter = active_photo_presenter
        if self.transition_key_down:
            self._set_transition_overlay_visible(True)
        if self.photo_presenter is not None and self.photo_presenter is not active_photo_presenter:
            self.photo_presenter.set_clock_time(None)
            self.photo_presenter.set_header((), (), None, False, self.config.font_size, self.config.font_color, self.config.map_header_font_factor, "off", self.config.header_shadow_color)
        if self.map_presenter is not None and self.map_presenter is not active_photo_presenter:
            self.map_presenter.set_clock_time(None)
            self.map_presenter.set_header((), (), None, False, self.config.font_size, self.config.font_color, self.config.map_header_font_factor, "off", self.config.header_shadow_color)
        if self.photo_presenter is not None and self.photo_presenter is not active_info_presenter:
            self.photo_presenter.set_info_text(None)
        if self.map_presenter is not None and self.map_presenter is not active_info_presenter:
            self.map_presenter.set_info_text(None)
        if self.photo_presenter is not None and self.photo_presenter is not active_photo_presenter:
            self.photo_presenter.set_status_visible(False)
        if self.map_presenter is not None and self.map_presenter is not active_photo_presenter:
            self.map_presenter.set_status_visible(False)

    def _schedule_next(self, seconds: float) -> None:
        self._schedule_callback(seconds, self._advance)

    def _schedule_callback(self, seconds: float, callback: Callable[[], None]) -> None:
        if self.active_callback is not None:
            self.active_callback.cancel()
        self.active_callback = self.schedule_callback(seconds, callback)

    def _handle_sigint(self, _signum: int, _frame: object) -> None:
        self.schedule_callback(0.0, self.quit)

    def _checkpoint_display_snapshot(
        self,
        playlist_index: int,
        media_index: Optional[int],
    ) -> dict:
        """Capture human-readable media details without extracting metadata."""
        row_index = media_index if isinstance(media_index, int) else playlist_index
        if 0 <= row_index < len(self.playlist_lines):
            line = self.playlist_lines[row_index]
            if not line.startswith("#"):
                entry = parse_photo_entry(line)
                media_path = resolve_path(
                    getattr(self.config, "photodir", self.config.inputlist.parent),
                    entry.source_name,
                )
                metadata = try_read_photo_metadata(
                    media_sidecar_path(media_path),
                    media_path,
                ) or {}
                raw_place = metadata.get("place")
                if not isinstance(raw_place, str) or not raw_place.strip():
                    raw_place = entry.place or ""
                media_date = metadata.get("datetime_iso")
                if not isinstance(media_date, str) or not media_date.strip():
                    media_date = " ".join(
                        value
                        for value in (getattr(self, "current_date", None), entry.time_text)
                        if isinstance(value, str) and value.strip()
                    )
                return {
                    "media_name": Path(entry.source_name).name,
                    "place": str(raw_place or "").strip(),
                    "media_date": str(media_date or "").strip(),
                    "asset_path": str(media_path),
                }
        directive = (
            parse_map_directive(self.playlist_lines[playlist_index])
            if 0 <= playlist_index < len(self.playlist_lines)
            else None
        )
        stage_name = ""
        current_track_metadata = getattr(self, "current_track_metadata", None)
        if isinstance(current_track_metadata, dict):
            stage_name = track_display_title(
                current_track_metadata,
                getattr(self.config, "track_title_mode", "endpoint_places"),
            ) or ""
        if not stage_name and directive is not None:
            stage_name = Path(directive.filename).stem
        return {
            "media_name": stage_name,
            "place": "",
            "media_date": str(getattr(self, "current_date", None) or "").strip(),
            "asset_path": (
                str(resolve_path(self._track_asset_dir(), directive.filename))
                if directive is not None
                else ""
            ),
        }

    def _resume_state_payload(self) -> dict:
        """Capture a stable control-list position before Cocoa objects are released."""
        control_file = self.config.inputlist.resolve(strict=False)
        control_identity = {"path": str(control_file)}
        try:
            control_stat = control_file.stat()
            control_identity.update(
                {
                    "size": int(control_stat.st_size),
                    "mtime_ns": int(control_stat.st_mtime_ns),
                }
            )
        except OSError:
            pass
        if self.completed_naturally:
            return {
                "version": SLIDESHOW_CHECKPOINT_VERSION,
                "completed": True,
                "control_file": str(control_file),
                "control_file_identity": control_identity,
                "stopped_at": datetime.now().astimezone().isoformat(),
            }

        media_index = None
        progress = None
        if self.time_lapse_active and self.time_lapse_stage is not None:
            playlist_index = self.time_lapse_stage.map_index
            progress = max(0.0, min(1.0, float(self.time_lapse_progress)))
            if self.time_lapse_current_media is not None:
                media_index = self.time_lapse_current_media[0]
        else:
            phase = getattr(self, "current_phase", None)
            stage_index = getattr(self, "current_stage_index", None)
            if (
                phase in {PlaybackPhase.STAGE_MAP, PlaybackPhase.STAGE_OVERVIEW}
                and isinstance(stage_index, int)
                and 0 <= stage_index < len(self.stages)
            ):
                playlist_index = self.stages[stage_index].map_index
            elif phase in {PlaybackPhase.INTRO_INFO, PlaybackPhase.INTRO_OVERVIEW} and self.stages:
                playlist_index = self.stages[0].map_index
            else:
                playlist_index = self.current_display_index
            if playlist_index is None:
                playlist_index = max(0, min(self.playlist_index - 1, len(self.playlist_lines) - 1))
            if 0 <= playlist_index < len(self.playlist_lines) and not self.playlist_lines[playlist_index].startswith("#"):
                media_index = playlist_index

        line_text = self.playlist_lines[playlist_index] if 0 <= playlist_index < len(self.playlist_lines) else None
        display_snapshot = self._checkpoint_display_snapshot(
            playlist_index,
            media_index,
        )
        return {
            "version": SLIDESHOW_CHECKPOINT_VERSION,
            "completed": False,
            "control_file": str(control_file),
            "control_file_identity": control_identity,
            "playlist_index": playlist_index,
            "line_text": line_text,
            "mode": "time-lapse" if self.time_lapse_active else "standard",
            "style": (
                "TIME_LAPSE"
                if self.time_lapse_active
                else ("RANDOM" if self.random_transition_mode else self.active_transition.value)
            ),
            "stage_index": getattr(self, "current_stage_index", None),
            "phase": (
                PlaybackPhase.STAGE_MAP.value
                if getattr(self, "current_phase", None)
                in {PlaybackPhase.INTRO_INFO, PlaybackPhase.INTRO_OVERVIEW}
                else (
                    self.current_phase.value
                    if getattr(self, "current_phase", None) is not None
                    else None
                )
            ),
            "stage_media_position": getattr(
                self,
                "current_stage_media_position",
                None,
            ),
            "time_lapse_progress": progress,
            "media_index": media_index,
            "stopped_at": datetime.now().astimezone().isoformat(),
            "display": display_snapshot,
            "audio": (
                self.music_controller.resume_state_snapshot()
                if hasattr(
                    getattr(self, "music_controller", None),
                    "resume_state_snapshot",
                )
                else None
            ),
            "control": self._control_state_snapshot(),
        }

    def _publish_live_state(self) -> None:
        """Publish row/phase changes for a synchronized open control editor."""
        if self.config.state_file is None or not self.running:
            return
        payload = self._resume_state_payload()
        row_index = payload.get("media_index")
        if not isinstance(row_index, int):
            row_index = payload.get("playlist_index")
        phase = payload.get("phase")
        signature = (
            row_index,
            phase,
            payload.get("stage_index"),
            payload.get("style"),
        )
        if signature == self.live_state_signature:
            return
        self.live_state_signature = signature
        self.live_state_sequence += 1
        payload.update(
            {
                "live": True,
                "sequence": self.live_state_sequence,
                "active_row": row_index,
                "active_media": payload.get("media_index"),
                "control_file": str(self.config.inputlist.resolve(strict=False)),
            }
        )
        if self.settings_request_sequence:
            payload.update(
                {
                    "request": "open_settings",
                    "settings_section": "Slide Show",
                    "request_sequence": self.settings_request_sequence,
                }
            )
        try:
            stat = self.config.inputlist.stat()
            payload["control_file_signature"] = {
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            write_json_atomic(self.config.state_file, payload)
        except Exception as exc:
            debug_exception(self.config, "write live slideshow state", exc)

    def _publish_settings_request(self) -> None:
        """Ask the parent myCamino GUI to open its Slide Show settings."""
        if self.config.state_file is None:
            self._show_temporary_status_overlay(
                "Slide Show settings are available in the myCamino window",
                3.0,
            )
            return
        self.settings_display_snapshot = {
            "map_window": getattr(self, "map_window", None) is not None,
            "fullscreen": bool(getattr(self, "fullscreen_active", False)),
            "fullscreen_roles": set(
                getattr(self, "fullscreen_window_roles", set())
            ),
            "screen_swap": bool(getattr(self, "screen_swap", False)),
        }
        self.settings_request_sequence += 1
        self.live_state_sequence += 1
        payload = self._resume_state_payload()
        payload.update(
            {
                "live": True,
                "sequence": self.live_state_sequence,
                "request": "open_settings",
                "settings_section": "Slide Show",
                "request_sequence": self.settings_request_sequence,
                "control_file": str(self.config.inputlist.resolve(strict=False)),
            }
        )
        try:
            write_json_atomic(self.config.state_file, payload)
            self._show_temporary_status_overlay("Opening Slide Show settings", 2.0)
        except Exception as exc:
            debug_exception(self.config, "request Slide Show settings", exc)

    def _restore_display_after_settings(self) -> None:
        """Return both slideshow windows to their pre-Settings presentation."""
        snapshot = self.settings_display_snapshot or {}
        self.settings_display_snapshot = None
        wants_map_window = bool(snapshot.get("map_window", self.map_window is not None))
        if wants_map_window and self.map_window is None and not self.config.join_windows:
            self._create_separate_map_window()
        self.screen_swap = bool(snapshot.get("screen_swap", self.screen_swap))

        windows = (
            ("photo", self.photo_window),
            ("map", self.map_window),
        )
        for _role, window in windows:
            if window is not None:
                window.orderFrontRegardless()
        if self.photo_window is not None:
            self.photo_window.makeKeyAndOrderFront_(None)
        self.app.activateIgnoringOtherApps_(True)

        if bool(snapshot.get("fullscreen", self.fullscreen_active)):
            expected_roles = set(snapshot.get("fullscreen_roles") or ())
            if not expected_roles:
                expected_roles.add("photo")
                if self.map_window is not None and len(self._available_screens()) > 1:
                    expected_roles.add("map")
            for index, (role, window) in enumerate(windows):
                if window is None or role not in expected_roles:
                    continue
                if role not in self.fullscreen_window_roles:
                    self.schedule_callback(
                        0.10 + 0.20 * index,
                        lambda current_window=window: current_window.toggleFullScreen_(None),
                    )

        self.schedule_callback(0.12, self._refresh_header_layouts)
        if self.map_window is not None:
            self.schedule_callback(0.35, self._refresh_separate_map_window_content)

    def _apply_runtime_settings(self, values: dict, *, restore_display: bool = False) -> None:
        """Apply supported Adventure settings without restarting playback."""
        simple_values = {
            "slideshow.clock": ("clock", bool),
            "slideshow.header_stage_name": ("header_stage_name", bool),
            "slideshow.header_track_details": ("header_track_details", bool),
            "slideshow.header_place_name": ("header_place_name", bool),
            "slideshow.header_track_stats": ("header_track_stats", bool),
            "slideshow.header_background": ("header_background", str),
            "slideshow.font_size": ("font_size", int),
            "slideshow.marker_radius": ("dot_size", int),
            "slideshow.arrow_scale": ("arrow_length", float),
            "slideshow.transition_duration_ms": ("transition_duration_ms", int),
            "slideshow.collage_max_images": ("collage_max_images", int),
            "timelapse.stage_duration_seconds": ("time_lapse_duration", float),
            "timelapse.media_min_fraction": ("time_lapse_media_min_fraction", float),
            "timelapse.marker_style": ("time_lapse_marker", str),
            "timelapse.overview_as_media": ("time_lapse_overview_as_media", bool),
            "timelapse.overview_on_stage_map_dual": ("time_lapse_overview_on_stage_map_dual", bool),
            "trackmaps.gpx_overlay": ("gpx_overlay_mode", str),
            "trackmaps.route_width": ("route_width", float),
            "trackmaps.font_factor": ("map_header_font_factor", float),
        }
        for key, (attribute, converter) in simple_values.items():
            if key in values:
                converted = converter(values[key])
                if attribute == "header_background":
                    converted = "black" if converted == "reserved" else converted
                    if converted not in {"off", "transparent", "black"}:
                        continue
                object.__setattr__(self.config, attribute, converted)

        color_values = {
            "slideshow.background_color": "background_color",
            "slideshow.font_color": "font_color",
            "slideshow.header_shadow_color": "header_shadow_color",
            "slideshow.marker_color": "dot_color",
            "trackmaps.route_color": "route_color",
        }
        for key, attribute in color_values.items():
            if key in values:
                try:
                    object.__setattr__(
                        self.config,
                        attribute,
                        parse_color_option(values[key], key),
                    )
                except ValueError:
                    continue

        if "slideshow.media_duration_seconds" in values:
            object.__setattr__(
                self.config,
                "duration",
                max(0.1, float(values["slideshow.media_duration_seconds"])),
            )
        if "slideshow.collage_size_range" in values:
            try:
                collage_min, collage_max = parse_percentage_range_option(
                    str(values["slideshow.collage_size_range"])
                )
                object.__setattr__(self.config, "collage_size_min", collage_min)
                object.__setattr__(self.config, "collage_size_max", collage_max)
            except ValueError:
                pass
        if "slideshow.transition" in values:
            try:
                self._select_playback_style(
                    str(values["slideshow.transition"]).upper(),
                    show_status=False,
                )
            except (ControlSyntaxError, ValueError):
                pass

        for presenter in (self.photo_presenter, self.map_presenter):
            if presenter is None:
                continue
            presenter.background_color = self.config.background_color
            presenter.transition_duration_ms = self.config.transition_duration_ms
            presenter.collage_size_min = self.config.collage_size_min
            presenter.collage_size_max = self.config.collage_size_max
            presenter.collage_max_images = self.config.collage_max_images
            presenter.host_view.layer().setBackgroundColor_(
                background_cgcolor(self.config.background_color)
            )
        self._refresh_photo_overlays()
        self._show_temporary_status_overlay("Settings applied", 2.0)
        if restore_display:
            self._restore_display_after_settings()

    def _restart_current_display(self) -> None:
        """Rebuild the active control row without starting a second player."""
        payload = self._resume_state_payload()
        row_index = payload.get("media_index")
        if not isinstance(row_index, int):
            row_index = payload.get("playlist_index")
        if isinstance(row_index, int) and 0 <= row_index < len(self.playlist_lines):
            self._jump_to_playlist_row(row_index)
            self._show_temporary_status_overlay("Restarted at current position", 2.0)
        else:
            self._show_temporary_status_overlay("Current position is not available", 2.0)
        self._restore_display_after_settings()

    def _poll_external_commands(self) -> None:
        """Consume editor-to-player commands without sharing the live-state file."""
        self.command_poll_handle = None
        if not self.running or self.config.command_file is None:
            return
        try:
            with self.config.command_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            command = external_jump_command_row(
                payload,
                self.config.inputlist,
                self.last_command_sequence,
                len(self.playlist_lines),
            )
            if command is not None:
                self.last_command_sequence, row_index = command
                self._jump_to_playlist_row(row_index)
            else:
                settings_command = external_settings_command(
                    payload,
                    self.last_command_sequence,
                )
                if settings_command is not None:
                    self.last_command_sequence, values, restore_display = settings_command
                    self._apply_runtime_settings(
                        values,
                        restore_display=restore_display,
                    )
                else:
                    restart_sequence = external_restart_command(
                        payload,
                        self.last_command_sequence,
                    )
                    if restart_sequence is not None:
                        self.last_command_sequence = restart_sequence
                        self._restart_current_display()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        if self.running:
            self.command_poll_handle = self.schedule_callback(
                0.15,
                self._poll_external_commands,
            )

    def _write_resume_state(self) -> None:
        if self.config.state_file is None:
            return
        try:
            write_json_atomic(self.config.state_file, self._resume_state_payload())
        except Exception as exc:
            debug_exception(self.config, "write resume state", exc)

    def quit(self) -> None:
        if not self.running:
            return
        debug_print(self.config, "Quitting application")
        self.running = False
        self._write_resume_state()
        self.music_controller.dispose()
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.memory_watchdog_handle is not None:
            self.memory_watchdog_handle.cancel()
            self.memory_watchdog_handle = None
        if self.command_poll_handle is not None:
            self.command_poll_handle.cancel()
            self.command_poll_handle = None
        for handle in list(self.timer_handles.values()):
            handle.cancel()
        self.timer_handles.clear()
        if self.photo_presenter is not None:
            self.photo_presenter.cancel_pending(detach_video=False)
        if self.map_presenter is not None:
            self.map_presenter.cancel_pending(detach_video=False)
        if getattr(self, "event_monitor", None) is not None:
            NSEvent.removeMonitor_(self.event_monitor)
            self.event_monitor = None
        if getattr(self, "event_monitor_up", None) is not None:
            NSEvent.removeMonitor_(self.event_monitor_up)
            self.event_monitor_up = None
        if self.photo_presenter is not None:
            self.photo_presenter.dispose()
        if self.map_presenter is not None:
            self.map_presenter.dispose()
        parked = self.parked_map_resource
        if parked is not None:
            parked_presenter = parked.get("presenter")
            if parked_presenter is not None:
                parked_presenter.dispose()
            parked_window = parked.get("window")
            if parked_window is not None:
                try:
                    parked_window.setDelegate_(None)
                    parked_window.orderOut_(None)
                    parked_window.close()
                except Exception:
                    pass
            self.parked_map_resource = None
        for window_name in ("photo_window", "map_window"):
            window = getattr(self, window_name, None)
            if window is not None:
                try:
                    window.setDelegate_(None)
                    window.orderOut_(None)
                    window.close()
                except Exception:
                    pass
                setattr(self, window_name, None)
        self.photo_presenter = None
        self.map_presenter = None
        self.window_delegates.clear()
        self.role_targets.clear()
        self.current_state = None
        self.current_display_index = None
        self.pending_display_index = None
        self.active_photo_presenter = None
        if self.on_quit is not None and not self._quit_notified:
            self._quit_notified = True
            try:
                self.on_quit(self)
            except Exception as exc:
                debug_exception(self.config, "on_quit", exc)
        if self.owns_run_loop:
            os._exit(0)


def run_with_options(
    photodir: str | Path,
    *,
    inputlist: str | Path | None = None,
    run_loop: bool = True,
    **options,
) -> GPSTrackShowApp:
    """Start the slideshow from Python and return the retained app controller."""
    if not APPKIT_AVAILABLE:
        raise RuntimeError(
            "PyObjC Cocoa bindings are required. Install them with: python3 -m pip install pyobjc-framework-Cocoa"
        )
    on_quit = options.pop("on_quit", None)
    config = config_from_options(photodir, inputlist=inputlist, **options)
    app = GPSTrackShowApp(config)
    app.on_quit = on_quit
    app.start(run_loop=run_loop)
    return app


def main(argv: Optional[list[str]] = None) -> int:
    """Run the slideshow CLI."""
    args = sys.argv[1:] if argv is None else argv
    config = parse_args(args)
    if not APPKIT_AVAILABLE:
        print(
            "Error: PyObjC Cocoa bindings are required. "
            "Install them with: python3 -m pip install pyobjc-framework-Cocoa",
            file=sys.stderr,
        )
        return 2
    app = GPSTrackShowApp(config)
    app.start(run_loop=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
