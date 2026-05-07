#!/usr/bin/env python3
"""Display a GPS-aware photo slideshow on macOS using Cocoa only."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import signal
import sys
import time
import traceback
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from plot_metadata_utils import (
    coordinate_to_pixel,
    extract_coordinate_point,
    read_photo_metadata,
    read_plot_metadata,
)

try:
    import objc
    from AppKit import (
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyRegular,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSBitmapImageRep,
        NSColor,
        NSEvent,
        NSEventMaskKeyDown,
        NSEventMaskKeyUp,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSAffineTransform,
        NSCompositingOperationSourceOver,
        NSImageAlignCenter,
        NSImage,
        NSImageScaleProportionallyUpOrDown,
        NSImageView,
        NSLeftArrowFunctionKey,
        NSMakeRect,
        NSMakeSize,
        NSRightArrowFunctionKey,
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


DEFAULT_LIST_NAME = "photos-sorted.lst"
TRANSITION_MS = 700
TRANSITION_STEPS = 14
WIPE_TRANSITION_MS = 1000
WIPE_TRANSITION_STEPS = 100
DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 800
DEFAULT_DOT_COLOR_NAME = "red"
DEFAULT_DOT_SIZE = 6
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
KEY_HELP_LINES = [
    "Keys:",
    "space                 next image / pause auto",
    "right / down          next image",
    "left / up             previous image",
    "Cmd + arrows          jump to previous/next date",
    "m                     toggle auto/manual mode",
    "p                     toggle place-name overlay",
    "+ / -                 change duration in auto mode",
    "c                     toggle analog clock overlay",
    "t                     cycle transition mode",
    "f                     toggle fullscreen/window mode",
    "d                     swap photo/map displays",
    "i                     show photo metadata overlay",
    "h                     show this key help",
    "q or Esc              quit",
]
STARTUP_HINT_TEXT = (
    "Press h to get more help on keyboard controls - "
    "Drücke die h-Taste, um eine Übersicht der Tastaturbefehle zu bekommen, "
    "um die Dia-Show zu steuern."
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


ENABLED_TRANSITIONS = (
    Transition.BLEND,
    Transition.FADE,
    Transition.SWITCH,
    Transition.EXPAND,
    Transition.COLLAGE,
    Transition.QUAD,
    Transition.RANDOM,
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
    transition: Transition
    background_color: tuple[float, float, float, float]
    dot_color: tuple[float, float, float, float]
    dot_size: int
    arrow_length: float
    font_color: tuple[float, float, float, float]
    font_size: int
    mapwindow: bool
    join_windows: bool
    repeat: bool
    photo_geometry: Optional[str]
    map_geometry: Optional[str]
    fullscreen: bool
    window_swap: bool
    clock: bool
    placenames: bool
    debug: bool
    keypressed: bool
    collage_size_min: float
    collage_size_max: float
    collage_max_images: int
    trackdir: Optional[Path] = None


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


@dataclass(frozen=True)
class HistoryTarget:
    """Lightweight semantic snapshot for rebuilding one displayed target."""

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
    dynamic_transition: bool = False


@dataclass
class DisplayState:
    """One currently displayed slideshow state."""

    targets: list[WindowTarget]
    next_callback: Optional[Callable[[], None]]
    auto_delay: Optional[float]
    description: str
    playlist_index: int
    history_targets: Optional[list[HistoryTarget]] = None


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
    parser.add_argument("--duration", "-d", type=float, default=3.0, help="Seconds per main slide.")
    parser.add_argument(
        "--transition",
        "-t",
        choices=[item.value for item in ENABLED_TRANSITIONS],
        default=Transition.BLEND.value,
        help="Transition type between slides.",
    )
    parser.add_argument("--background-color", default="black", help="Background color.")
    parser.add_argument("--dot-color", default=DEFAULT_DOT_COLOR_NAME, help="GPS marker color.")
    parser.add_argument("--dot-size", type=int, default=DEFAULT_DOT_SIZE, help="GPS marker radius in pixels.")
    parser.add_argument(
        "--arrow-length",
        type=float,
        default=1.0,
        help="Scale factor for the track-map arrow above photo markers; 0 disables arrows.",
    )
    parser.add_argument("--font-color", default="white", help="Overview date text color.")
    parser.add_argument("--font-size", type=int, default=30, help="Overview date text font size.")
    parser.add_argument("--mapwindow", "-m", action="store_true", default=None, help="Open a separate map window.")
    parser.add_argument("--join-windows", "-j", action="store_true", help="Show photo and map views side by side.")
    parser.add_argument("--repeat", "-r", action="store_true", help="Repeat until q or Ctrl-C.")
    parser.add_argument("--photo-geometry", default=None, help="Window geometry WIDTHxHEIGHT+X+Y.")
    parser.add_argument("--map-geometry", default=None, help="Window geometry WIDTHxHEIGHT+X+Y.")
    parser.add_argument("--fullscreen", "-f", action="store_true", default=None, help="Start slideshow windows fullscreen.")
    parser.add_argument("--switch-display", "-s", action="store_true", dest="window_swap", help="Switch photo/map display assignment at startup.")
    parser.add_argument("--clock", "-c", choices=["on", "off"], default="on", help="Show analog clock on photos when time is known.")
    parser.add_argument("--placenames", "-p", choices=["on", "off"], default="on", help="Show place names from photo metadata on photos.")
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
    if args.duration <= 0:
        parser.error("--duration must be greater than 0")
    if args.dot_size < 1:
        parser.error("--dot-size must be at least 1")
    if args.arrow_length < 0:
        parser.error("--arrow-length must be 0 or greater")
    if args.font_size < 8:
        parser.error("--font-size must be at least 8")
    collage_size_min, collage_size_max = parse_percentage_range(args.collage_size_range, parser)
    if args.collage_max_images < 1:
        parser.error("--collage-max-images must be at least 1")
    mapwindow_enabled = True if args.mapwindow is None else bool(args.mapwindow)
    auto_two_screen_mode = available_screen_count() >= 2
    fullscreen_enabled = auto_two_screen_mode if args.fullscreen is None else bool(args.fullscreen)
    if args.join_windows and not mapwindow_enabled:
        parser.error("--join-windows requires --mapwindow")

    return Config(
        photodir=photodir,
        inputlist=inputlist,
        start_track=int(args.start),
        duration=float(args.duration),
        transition=Transition(args.transition),
        background_color=parse_color(args.background_color, parser, "--background-color"),
        dot_color=parse_color(args.dot_color, parser, "--dot-color"),
        dot_size=args.dot_size,
        arrow_length=float(args.arrow_length),
        font_color=parse_color(args.font_color, parser, "--font-color"),
        font_size=args.font_size,
        mapwindow=mapwindow_enabled,
        join_windows=bool(args.join_windows),
        repeat=bool(args.repeat),
        photo_geometry=args.photo_geometry,
        map_geometry=args.map_geometry,
        fullscreen=fullscreen_enabled,
        window_swap=bool(args.window_swap),
        clock=args.clock == "on",
        placenames=args.placenames == "on",
        debug=bool(args.debug),
        keypressed=bool(args.keypressed),
        collage_size_min=collage_size_min,
        collage_size_max=collage_size_max,
        collage_max_images=args.collage_max_images,
        trackdir=trackdir,
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
    transition: str | Transition = Transition.BLEND,
    background_color: str | tuple[float, ...] | list[float] = "black",
    dot_color: str | tuple[float, ...] | list[float] = DEFAULT_DOT_COLOR_NAME,
    dot_size: int = DEFAULT_DOT_SIZE,
    arrow_length: float = 1.0,
    font_color: str | tuple[float, ...] | list[float] = "white",
    font_size: int = 30,
    mapwindow: Optional[bool] = None,
    join_windows: bool = False,
    repeat: bool = False,
    photo_geometry: Optional[str] = None,
    map_geometry: Optional[str] = None,
    fullscreen: Optional[bool] = None,
    window_swap: bool = False,
    clock: bool = True,
    placenames: bool = True,
    debug: bool = False,
    keypressed: bool = False,
    collage_size_range: str = "33-66",
    collage_max_images: int = 9,
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
    if duration <= 0:
        raise ValueError("duration must be greater than 0")
    if dot_size < 1:
        raise ValueError("dot_size must be at least 1")
    if arrow_length < 0:
        raise ValueError("arrow_length must be 0 or greater")
    if font_size < 8:
        raise ValueError("font_size must be at least 8")
    if collage_max_images < 1:
        raise ValueError("collage_max_images must be at least 1")

    transition_value = transition if isinstance(transition, Transition) else Transition(str(transition))
    if transition_value not in ENABLED_TRANSITIONS:
        raise ValueError(f"transition is not enabled: {transition_value.value}")
    collage_size_min, collage_size_max = parse_percentage_range_option(collage_size_range)
    mapwindow_enabled = True if mapwindow is None else bool(mapwindow)
    fullscreen_enabled = False if fullscreen is None else bool(fullscreen)
    if join_windows and not mapwindow_enabled:
        raise ValueError("join_windows requires mapwindow")

    return Config(
        photodir=photo_dir_path,
        inputlist=input_list_path,
        start_track=int(start),
        duration=float(duration),
        transition=transition_value,
        background_color=parse_color_option(background_color, "background_color"),
        dot_color=parse_color_option(dot_color, "dot_color"),
        dot_size=int(dot_size),
        arrow_length=float(arrow_length),
        font_color=parse_color_option(font_color, "font_color"),
        font_size=int(font_size),
        mapwindow=mapwindow_enabled,
        join_windows=bool(join_windows),
        repeat=bool(repeat),
        photo_geometry=photo_geometry,
        map_geometry=map_geometry,
        fullscreen=fullscreen_enabled,
        window_swap=bool(window_swap),
        clock=bool(clock),
        placenames=bool(placenames),
        debug=bool(debug),
        keypressed=bool(keypressed),
        collage_size_min=collage_size_min,
        collage_size_max=collage_size_max,
        collage_max_images=int(collage_max_images),
        trackdir=track_dir_path,
    )


def ns_color(color: tuple[float, float, float, float]):
    """Return an NSColor from normalized RGBA floats."""
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*color)


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
    parts = [part.strip() for part in line.split("|")]
    filename = parts[0]
    time_text = parts[1] if len(parts) > 1 and parts[1] else None
    latitude = None
    longitude = None
    if len(parts) > 2 and parts[2]:
        latitude, longitude = parse_coordinate_pair(parts[2])
    place = parts[3] if len(parts) > 3 and parts[3] else None
    return PhotoListEntry(filename, time_text, latitude, longitude, place)


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


def safe_float(value: object) -> Optional[float]:
    """Convert one value to float when possible."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
    tangent_x, tangent_y = tangent
    normal_x, normal_y = -tangent_y, tangent_x
    if normal_y > 0:
        normal_x, normal_y = -normal_x, -normal_y
    normal_length = math.hypot(normal_x, normal_y)
    if normal_length <= 0:
        return
    normal_x /= normal_length
    normal_y /= normal_length
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


def draw_marker_at(
    pixel_x: float,
    pixel_y: float,
    image_height: float,
    color: tuple[float, float, float, float],
    radius: int,
) -> None:
    """Draw one filled marker with black outline into the current focus."""
    outline_width = max(1.0, radius / 5.0)
    oval_rect = NSMakeRect(pixel_x - radius, image_height - pixel_y - radius, radius * 2, radius * 2)
    ns_color(color).setFill()
    fill_path = NSBezierPath.bezierPathWithOvalInRect_(oval_rect)
    fill_path.fill()
    ns_color(COLOR_NAMES["black"]).setStroke()
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


def create_place_overlay_image(width: float, height: float, place_text: str, font_size: int, font_color):
    """Create a centered place-name overlay near the top of the photo view."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    font = NSFont.fontWithName_size_("Arial Bold", float(font_size)) or NSFont.boldSystemFontOfSize_(float(font_size))
    outline_width = max(1.0, font_size / 12.0)
    lines = [line.strip() for line in str(place_text).splitlines() if line.strip()] or [str(place_text)]
    line_sizes = [
        NSString.stringWithString_(line).sizeWithAttributes_(
            {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: ns_color(font_color),
            }
        )
        for line in lines
    ]
    line_height = max((size.height for size in line_sizes), default=float(font_size)) + max(4.0, font_size * 0.12)
    top_y = max(10.0, height - line_height * len(lines) - 18.0)
    for index, line in enumerate(lines):
        draw_outlined_text(
            line,
            width / 2.0,
            top_y + (len(lines) - 1 - index) * line_height,
            font,
            font_color,
            COLOR_NAMES["black"],
            outline_width,
        )
    image.unlockFocus()
    return image


def create_startup_hint_overlay_image(width: float, height: float):
    """Create the temporary startup hint shown on the photo screen."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()

    panel_width = min(width * 0.78, 1180.0)
    panel_height = min(height * 0.18, 140.0)
    panel_x = (width - panel_width) / 2.0
    panel_y = height - panel_height - 34.0
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
    lines = [
        "Press h to get more help on keyboard controls",
        "Drücke die h-Taste, um eine Übersicht der Tastaturbefehle zu bekommen,",
        "um die Dia-Show zu steuern.",
    ]
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
    """Return one dd.mm.yyyy date string for the clock overlay."""
    datetime_iso = photo_metadata.get("datetime_iso")
    if isinstance(datetime_iso, str) and len(datetime_iso) >= 10:
        iso_date = datetime_iso[:10]
        parts = iso_date.split("-")
        if len(parts) == 3:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
    date_german = photo_metadata.get("date_german")
    if isinstance(date_german, str) and "," in date_german:
        return date_german.partition(",")[2].strip() or None
    if isinstance(fallback_date_text, str) and "," in fallback_date_text:
        return fallback_date_text.partition(",")[2].strip() or None
    return None


def create_clock_overlay_image(clock_size: float, hour: int, minute: int, date_text: Optional[str] = None):
    """Create a transparent analog clock image, optionally with a date below it."""
    import math

    date_height = max(16.0, clock_size * 0.22) if date_text else 0.0
    total_height = clock_size + date_height
    image = NSImage.alloc().initWithSize_(NSMakeSize(clock_size, total_height))
    image.lockFocus()

    stroke_width = max(1.5, clock_size / 30.0)
    padding = stroke_width / 2.0 + 1.0
    usable_size = max(8.0, clock_size - 2.0 * padding)
    radius = usable_size / 2.0
    center_x = clock_size / 2.0
    center_y = date_height + clock_size / 2.0

    face_rect = NSMakeRect(center_x - radius, center_y - radius, usable_size, usable_size)
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.45).setFill()
    face = NSBezierPath.bezierPathWithOvalInRect_(face_rect)
    face.fill()
    ns_color(COLOR_NAMES["white"]).setStroke()
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
    NSBezierPath.bezierPathWithOvalInRect_(hub_rect).fill()

    if date_text:
        font_size = max(10.0, clock_size * 0.14)
        font = NSFont.boldSystemFontOfSize_(font_size)
        outline_width = max(1.0, font_size / 10.0)
        baseline_y = max(1.0, date_height * 0.08)
        draw_outlined_text(
            date_text,
            clock_size / 2.0,
            baseline_y,
            font,
            COLOR_NAMES["white"],
            COLOR_NAMES["black"],
            outline_width,
        )

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


def draw_overview_overlay(overview_image, overview_metadata: dict, track_metadata: dict, date_text: Optional[str], config: Config):
    """Return the overview map with endpoints and date drawn on top."""
    debug_print(config, "Drawing overview overlay with track endpoints and date")
    result = copy_image(overview_image)
    width, height = image_size_tuple(result)
    result.lockFocus()

    ns_color(config.dot_color).setFill()
    start_point = track_metadata.get("start_point", track_metadata.get("first_point"))
    end_point = track_metadata.get("end_point", track_metadata.get("last_point"))
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

    if point_pixels[0] is not None and point_pixels[1] is not None:
        draw_arrow_line(
            point_pixels[0][0],
            point_pixels[0][1],
            point_pixels[1][0],
            point_pixels[1][1],
            height,
            config.dot_color,
            max(1.0, config.dot_size / 2.0),
        )

    for point_pixel in point_pixels:
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
    track_name = track_metadata.get("track_name")
    track_duration = track_metadata.get("track_duration")
    if isinstance(track_name, str) and track_name.strip():
        caption = track_name.strip()
        formatted_duration = format_overview_duration(track_duration)
        if formatted_duration:
            caption = f"{caption} ({formatted_duration})"
        caption_lines.append(caption)
    if date_text:
        caption_lines.append(date_text)

    if caption_lines:
        debug_print(config, f"Drawing overview caption lines: {caption_lines}")
        font = NSFont.fontWithName_size_("Arial Bold", float(config.font_size)) or NSFont.boldSystemFontOfSize_(float(config.font_size))
        outline_width = max(1.0, config.font_size / 12.0)
        line_height = font.ascender() - font.descender() + 4.0
        top_margin = max(10.0, line_height * 2.0)
        for index, line in enumerate(caption_lines):
            draw_outlined_text(
                line,
                width / 2.0,
                height - top_margin - (index + 1) * line_height,
                font,
                config.font_color,
                COLOR_NAMES["black"],
                outline_width,
            )

    result.unlockFocus()
    debug_print(config, "Finished overview overlay drawing")
    return result


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


def try_read_photo_metadata(path: Path) -> Optional[dict]:
    """Read photo metadata if present, otherwise warn and continue."""
    if not path.is_file():
        warn_message(f"photo metadata file not found: {path}")
        return None
    try:
        return read_photo_metadata(path)
    except Exception as exc:
        warn_message(f"failed to read photo metadata {path}: {exc}")
        return None


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

        def initWithCallback_(self, callback: Callable[[], None]):  # type: ignore[override]
            self = objc.super(TimerTarget, self).init()
            if self is None:
                return None
            self.callback = callback
            self.debug_config = None
            self.debug_context = getattr(callback, "__name__", repr(callback))
            return self

        def fire_(self, _timer) -> None:
            try:
                self.callback()
            except BaseException as exc:  # pragma: no cover - GUI callback path
                debug_exception(self.debug_config, self.debug_context, exc)
                raise


    class GPSTrackShowWindowDelegate(NSObject):
        """Window delegate for resize and close events."""

        def initWithController_(self, controller):  # type: ignore[override]
            self = objc.super(GPSTrackShowWindowDelegate, self).init()
            if self is None:
                return None
            self.controller = controller
            return self

        def windowWillClose_(self, _notification) -> None:
            self.controller.quit()


    class ScheduledCallback:
        """Small wrapper around NSTimer so callbacks can be cancelled."""

        def __init__(self, timer, target):
            self.timer = timer
            self.target = target

        def cancel(self) -> None:
            self.timer.invalidate()


class CocoaImagePresenter:
    """Layered NSImageView presenter with transitions."""

    def __init__(self, host_view, background_color, schedule_callback, collage_size_min: float = 0.33, collage_size_max: float = 0.66, collage_max_images: int = 9):
        self.host_view = host_view
        self.background_color = background_color
        self.schedule_callback = schedule_callback
        self.collage_size_min = collage_size_min
        self.collage_size_max = collage_size_max
        self.collage_max_images = collage_max_images
        self.pending_handles = []
        self.current_image = None
        self.help_visible = False
        self.clock_time: Optional[tuple[int, int]] = None
        self.clock_date_text: Optional[str] = None
        self.place_visible = False
        self.place_text: Optional[str] = None
        self.info_visible = False
        self.info_text: Optional[str] = None
        self.status_visible = False
        self.status_text: Optional[str] = None
        self.quad_index = 0
        self.collage_slot_index = 0
        self.collage_count = 0
        self.layout_canvas = None
        self.layout_mode: Optional[Transition] = None
        self.last_media_rect = None
        self.video_view = None
        self.video_player = None

        host_view.setWantsLayer_(True)
        host_view.layer().setBackgroundColor_(background_cgcolor(background_color))

        self.primary_view = self._make_image_view(host_view.bounds())
        self.overlay_view = self._make_image_view(host_view.bounds())
        self.clock_view = self._make_image_view(host_view.bounds())
        self.place_view = self._make_image_view(host_view.bounds())
        self.info_view = self._make_image_view(host_view.bounds())
        self.status_view = self._make_image_view(host_view.bounds())
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
        host_view.addSubview_(self.clock_view)
        host_view.addSubview_(self.place_view)
        host_view.addSubview_(self.info_view)
        host_view.addSubview_(self.status_view)
        host_view.addSubview_(self.startup_hint_view)
        host_view.addSubview_(self.help_view)
        self.overlay_view.setAlphaValue_(0.0)
        self.clock_view.setAlphaValue_(0.0)
        self.place_view.setAlphaValue_(0.0)
        self.info_view.setAlphaValue_(0.0)
        self.status_view.setAlphaValue_(0.0)
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

    def dispose(self) -> None:
        """Stop activity without tearing down AppKit views during window close."""
        self.cancel_pending(detach_video=False)
        for view_name in (
            "primary_view",
            "overlay_view",
            "clock_view",
            "place_view",
            "info_view",
            "status_view",
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
            self.clock_view,
            self.place_view,
            self.info_view,
            self.status_view,
            self.startup_hint_view,
            self.help_view,
        ):
            view.removeFromSuperview()
            self.host_view.addSubview_(view)

    def play_video(self, video_path: Path, frame_rect=None) -> None:
        """Play a video in the presenter after its still-frame transition."""
        self.stop_video()
        if not AVKIT_VIDEO_AVAILABLE or AVPlayer is None or AVPlayerView is None:
            warn_message(f"video playback is unavailable because AVKit bindings are missing: {video_path}")
            return
        rect = frame_rect if frame_rect is not None else self.host_view.bounds()
        player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(video_path)))
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
        self._raise_overlay_views()
        player.play()

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
        bounds = self.host_view.bounds()
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

    def set_startup_hint_visible(self, visible: bool) -> None:
        """Show or hide the temporary startup hint."""
        if not visible:
            self.startup_hint_view.setAlphaValue_(0.0)
            self.startup_hint_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        self.startup_hint_view.setFrame_(bounds)
        self.startup_hint_view.setImage_(create_startup_hint_overlay_image(bounds.size.width, bounds.size.height))
        self.startup_hint_view.setAlphaValue_(1.0)

    def set_clock_time(self, clock_time: Optional[tuple[int, int]], clock_date_text: Optional[str] = None) -> None:
        """Show or hide the clock overlay in the top-left corner of the presenter."""
        self.clock_time = clock_time
        self.clock_date_text = clock_date_text
        if clock_time is None:
            self.clock_view.setAlphaValue_(0.0)
            self.clock_view.setImage_(None)
            return
        bounds = self.host_view.bounds()
        clock_size = max(40.0, bounds.size.height / 10.0)
        margin = max(10.0, clock_size / 6.0)
        date_height = max(16.0, clock_size * 0.22) if clock_date_text else 0.0
        self.clock_view.setFrame_(
            NSMakeRect(
                margin,
                bounds.size.height - margin - clock_size - date_height,
                clock_size,
                clock_size + date_height,
            )
        )
        self.clock_view.setImage_(create_clock_overlay_image(clock_size, clock_time[0], clock_time[1], clock_date_text))
        self.clock_view.setAlphaValue_(1.0)

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
        self.place_view.setImage_(create_place_overlay_image(bounds.size.width, bounds.size.height, place_text, font_size, font_color))
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
        self.overlay_view.setFrame_(self.host_view.bounds())
        self.fade_view.setAlphaValue_(0.0)
        if on_complete is not None:
            on_complete()

    def _transition_blend(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        self.overlay_view.setImage_(image)
        self.overlay_view.setFrame_(self.host_view.bounds())
        self.overlay_view.setAlphaValue_(0.0)

        def step(index: int) -> None:
            alpha = index / TRANSITION_STEPS
            self.overlay_view.setAlphaValue_(alpha)
            if index < TRANSITION_STEPS:
                self.pending_handles.append(self.schedule_callback(TRANSITION_MS / 1000.0 / TRANSITION_STEPS, lambda: step(index + 1)))
            else:
                self._finish_transition(image, on_complete)

        step(1)

    def _transition_fade(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        half_steps = max(1, TRANSITION_STEPS // 2)

        def fade_out(index: int) -> None:
            self.fade_view.setAlphaValue_(index / half_steps)
            if index < half_steps:
                self.pending_handles.append(self.schedule_callback(TRANSITION_MS / 2000.0 / half_steps, lambda: fade_out(index + 1)))
            else:
                self.primary_view.setImage_(image)
                fade_in(half_steps)

        def fade_in(index: int) -> None:
            self.fade_view.setAlphaValue_(max(0.0, (index - 1) / half_steps))
            if index > 0:
                self.pending_handles.append(self.schedule_callback(TRANSITION_MS / 2000.0 / half_steps, lambda: fade_in(index - 1)))
            else:
                self._finish_transition(image, on_complete)

        fade_out(1)

    def _transition_expand(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        bounds = self.host_view.bounds()
        full_width = bounds.size.width
        full_height = bounds.size.height
        self.overlay_view.setImage_(image)

        def step(index: int) -> None:
            scale = max(0.02, index / TRANSITION_STEPS)
            width = full_width * scale
            height = full_height * scale
            x_pos = (full_width - width) / 2.0
            y_pos = (full_height - height) / 2.0
            self.overlay_view.setFrame_(NSMakeRect(x_pos, y_pos, width, height))
            self.overlay_view.setAlphaValue_(1.0)
            if index < TRANSITION_STEPS:
                self.pending_handles.append(self.schedule_callback(TRANSITION_MS / 1000.0 / TRANSITION_STEPS, lambda: step(index + 1)))
            else:
                self._finish_transition(image, on_complete)

        step(1)

    def _transition_wipe(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        bounds = self.host_view.bounds()
        current_image = self.current_image if self.current_image is not None else make_blank_canvas(bounds.size.width, bounds.size.height, self.background_color)

        def step(index: int) -> None:
            progress = index / WIPE_TRANSITION_STEPS
            wipe_frame = create_wipe_frame(current_image, image, progress, bounds.size.width, bounds.size.height, self.background_color)
            self.overlay_view.setFrame_(bounds)
            self.overlay_view.setImage_(wipe_frame)
            self.overlay_view.setAlphaValue_(1.0)
            if index < WIPE_TRANSITION_STEPS:
                self.pending_handles.append(self.schedule_callback(WIPE_TRANSITION_MS / 1000.0 / WIPE_TRANSITION_STEPS, lambda: step(index + 1)))
            else:
                self._finish_transition(image, on_complete)

        step(1)

    def _transition_collage(self, image, on_complete: Optional[Callable[[], None]], media_is_video: bool = False) -> None:
        bounds = self.host_view.bounds()
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
        self.last_media_rect = media_rect
        self.collage_count += 1
        self.collage_slot_index = (self.collage_slot_index + 1) % 5
        display_image = copy_image(collage_image)
        self.current_image = display_image
        self.primary_view.setImage_(display_image)
        self.overlay_view.setAlphaValue_(0.0)
        self.fade_view.setAlphaValue_(0.0)
        if on_complete is not None:
            self.pending_handles.append(self.schedule_callback(0.0, on_complete))

    def _transition_quad(self, image, on_complete: Optional[Callable[[], None]]) -> None:
        bounds = self.host_view.bounds()
        canvas = self._base_canvas(Transition.QUAD)
        quad_image, media_rect = create_quad_canvas(
            image,
            canvas,
            bounds.size.width,
            bounds.size.height,
            self.quad_index,
            self.background_color,
        )
        self.last_media_rect = media_rect
        self.quad_index = (self.quad_index + 1) % 4
        display_image = copy_image(quad_image)
        self.current_image = display_image
        self.primary_view.setImage_(display_image)
        self.overlay_view.setAlphaValue_(0.0)
        self.fade_view.setAlphaValue_(0.0)
        if on_complete is not None:
            self.pending_handles.append(self.schedule_callback(0.0, on_complete))


class GPSTrackShowApp:
    """Cocoa application controller for the slideshow."""

    def __init__(self, config: Config):
        self.config = config
        self.playlist_lines = self._load_playlist_lines()
        self.playlist_index = 0
        self.current_date: Optional[str] = None
        self.current_overview_image = None
        self.current_overview_metadata: Optional[dict] = None
        self.current_track_image = None
        self.current_track_metadata: Optional[dict] = None
        self._apply_start_track()
        self.start_playlist_index = self.playlist_index
        self.running = True
        self.manual_mode = config.keypressed
        self.active_callback = None
        self.timer_handles = []
        self.window_delegates = []
        self.photo_presenter: Optional[CocoaImagePresenter] = None
        self.map_presenter: Optional[CocoaImagePresenter] = None
        self.current_state: Optional[DisplayState] = None
        self.history: list[DisplayState] = []
        self.role_targets: dict[str, WindowTarget] = {}
        self.screen_swap = config.window_swap
        self.help_key_down = False
        self.info_key_down = False
        self.transition_key_down = False
        self.fullscreen_active = config.fullscreen
        self.active_photo_presenter: Optional[CocoaImagePresenter] = None
        self.transition_overlay_deadline = 0.0
        self.transition_overlay_hide_handle = None
        self.help_overlay_deadline = 0.0
        self.help_overlay_hide_handle = None
        self.duration_overlay_hide_handle = None
        self.quad_bootstrap_remaining = 0
        self.random_transition_mode = config.transition == Transition.RANDOM
        self.active_transition = random.choice(RANDOM_TRANSITIONS) if self.random_transition_mode else config.transition
        self.transition_change_armed = False
        self.paused = False
        self.startup_preview_identity: Optional[str] = None
        self.owns_run_loop = True
        self.on_quit = None
        self._quit_notified = False

        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        debug_print(self.config, f"Application created. photodir={self.config.photodir}")
        debug_print(self.config, f"Input list={self.config.inputlist}")
        debug_print(self.config, f"Loaded {len(self.playlist_lines)} non-empty playlist lines")
        debug_print(self.config, f"Starting at track {self.config.start_track}; playlist index={self.playlist_index}")
        debug_print(self.config, f"Initial mode={'manual' if self.manual_mode else 'automatic'} duration={self.config.duration:.1f}s")
        self._build_windows()
        self._install_key_monitor()
        signal.signal(signal.SIGINT, self._handle_sigint)

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

    def _apply_start_track(self) -> None:
        """Move the initial playlist position to the requested Nth track map."""
        start_index = self._find_track_start_index(self.config.start_track)
        if start_index is None:
            track_count = sum(1 for line in self.playlist_lines if line.startswith("#Map:"))
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
            if line.startswith("#Map:"):
                seen_tracks += 1
                if seen_tracks == track_number:
                    return index
        return None

    def _prime_context_before_index(self, start_index: int) -> None:
        """Load overview/date context that would have been seen before start_index."""
        overview_filename = None
        date_text = None
        for line in self.playlist_lines[:start_index]:
            if line.startswith("#Overviewmap:"):
                overview_filename = line.partition(":")[2].strip()
            elif line.startswith("#Datum:"):
                date_text = line.partition(":")[2].strip()
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
            if chars in {"p", "P"} or raw_chars in {"p", "P"}:
                self._toggle_placenames()
                return None
            if chars in {"t", "T"} or raw_chars in {"t", "T"}:
                if self.transition_key_down:
                    return None
                self.transition_key_down = True
                if self.transition_overlay_hide_handle is not None:
                    self.transition_overlay_hide_handle.cancel()
                    self.transition_overlay_hide_handle = None
                self._cycle_transition()
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
            if chars in {"d", "D"} or raw_chars in {"d", "D"}:
                self._swap_window_screens()
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
                remaining = self.help_overlay_deadline - time.monotonic()
                if remaining <= 0:
                    self._set_help_overlay_visible(False)
                else:
                    if self.help_overlay_hide_handle is not None:
                        self.help_overlay_hide_handle.cancel()
                    self.help_overlay_hide_handle = self.schedule_callback(remaining, lambda: self._set_help_overlay_visible(False))
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
                self.help_overlay_deadline = time.monotonic() + 3.0
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
            self.active_photo_presenter.set_status_text("RANDOM" if self.random_transition_mode else self.active_transition.value)
            self.active_photo_presenter.set_status_visible(True)

    def _jump_to_date_section(self, forward: bool) -> None:
        """Jump to the next or previous #Datum section."""
        direction = "next" if forward else "previous"
        current_index = max(0, min(self.playlist_index - 1, len(self.playlist_lines) - 1))
        if forward:
            target_index = next(
                (index for index in range(self.playlist_index, len(self.playlist_lines)) if self.playlist_lines[index].startswith("#Datum:")),
                None,
            )
        else:
            current_section_index = next(
                (index for index in range(current_index, -1, -1) if self.playlist_lines[index].startswith("#Datum:")),
                None,
            )
            search_from = current_section_index - 1 if current_section_index is not None else current_index
            target_index = next(
                (index for index in range(search_from, -1, -1) if self.playlist_lines[index].startswith("#Datum:")),
                None,
            )
        if target_index is None:
            debug_print(self.config, f"No {direction} date section available")
            return
        debug_print(self.config, f"Jumping to {direction} date section at playlist index {target_index}")
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        self._prime_context_before_index(target_index)
        self.playlist_index = target_index
        self._advance()

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

    def _toggle_mode(self) -> None:
        """Switch between automatic and manual stepping."""
        self.manual_mode = not self.manual_mode
        self.paused = False
        mode_name = "manual" if self.manual_mode else "automatic"
        debug_print(self.config, f"Switched to {mode_name} mode")
        self._update_window_titles(f"Mode {mode_name}")
        self._show_temporary_status_overlay(mode_name, 2.0)
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if not self.manual_mode and self.current_state is not None and self.current_state.next_callback is not None and self.current_state.auto_delay is not None:
            self._schedule_callback(self.current_state.auto_delay, self.current_state.next_callback)

    def _toggle_pause(self) -> None:
        """Pause or resume automatic playback."""
        if self.manual_mode:
            return
        self.paused = not self.paused
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.paused:
            debug_print(self.config, "Paused automatic playback")
            self._update_window_titles("Paused")
            self._show_temporary_status_overlay("Pause", 2.0)
            return
        debug_print(self.config, "Resumed automatic playback")
        self._update_window_titles("Running")
        self._show_temporary_status_overlay("Auto", 2.0)
        if self.current_state is not None and self.current_state.next_callback is not None and self.current_state.auto_delay is not None:
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
        if not self.manual_mode and self.current_state is not None and self.current_state.next_callback is not None and self.current_state.auto_delay is not None:
            self._schedule_callback(new_duration, self.current_state.next_callback)

    def _toggle_clock(self) -> None:
        """Toggle the analog clock overlay on photos."""
        object.__setattr__(self.config, "clock", not self.config.clock)
        state_text = "on" if self.config.clock else "off"
        debug_print(self.config, f"Clock overlay toggled {state_text}")
        self._update_window_titles(f"Clock {state_text}")
        self._refresh_photo_overlays()

    def _toggle_placenames(self) -> None:
        """Toggle place-name overlays on photos."""
        object.__setattr__(self.config, "placenames", not self.config.placenames)
        state_text = "on" if self.config.placenames else "off"
        debug_print(self.config, f"Place-name overlay toggled {state_text}")
        self._update_window_titles(f"Place names {state_text}")
        self._refresh_photo_overlays()

    def _cycle_transition(self) -> None:
        """Arm the next available transition for the next photo only."""
        transitions = list(ENABLED_TRANSITIONS)
        base_transition = Transition.RANDOM if self.random_transition_mode else self.active_transition
        if base_transition not in transitions:
            base_transition = Transition.FADE
        current_index = transitions.index(base_transition)
        next_transition = transitions[(current_index + 1) % len(transitions)]
        self.random_transition_mode = next_transition == Transition.RANDOM
        if self.random_transition_mode:
            self.active_transition = random.choice(RANDOM_TRANSITIONS)
        else:
            self.active_transition = next_transition
        self.transition_change_armed = True
        transition_label = "RANDOM" if self.random_transition_mode else self.active_transition.value
        debug_print(self.config, f"Transition armed for next photo: {transition_label}")
        self._update_window_titles(f"Next transition {transition_label}")

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
        """Refresh clock and place overlays without redrawing the current image."""
        photo_target = self.role_targets.get("photo")
        active_presenter = self._presenter_for_role("photo") if photo_target is not None else None
        if active_presenter is not None and photo_target is not None:
            active_presenter.set_clock_time(
                photo_target.clock_time if self.config.clock else None,
                photo_target.clock_date_text if self.config.clock else None,
            )
            active_presenter.set_place_text(
                photo_target.place_text,
                bool(self.config.placenames),
                self.config.font_size,
                self.config.font_color,
            )
        if self.photo_presenter is not None and self.photo_presenter is not active_presenter:
            self.photo_presenter.set_clock_time(None)
            self.photo_presenter.set_place_text(None, False, self.config.font_size, self.config.font_color)
        if self.map_presenter is not None and self.map_presenter is not active_presenter:
            self.map_presenter.set_clock_time(None)
            self.map_presenter.set_place_text(None, False, self.config.font_size, self.config.font_color)

    def _build_history_targets(self, state: DisplayState) -> list[HistoryTarget]:
        """Capture lightweight semantic target state for backward navigation."""
        dynamic_photo = state.description.startswith("Photo ")
        return [
            HistoryTarget(
                presenter_name=target.presenter_name,
                image=target.image,
                transition=target.transition,
                clock_time=target.clock_time,
                clock_date_text=target.clock_date_text,
                place_text=target.place_text,
                info_text=target.info_text,
                photo_identity=target.photo_identity,
                video_path=target.video_path,
                video_duration=target.video_duration,
                dynamic_transition=bool(target.presenter_name == "photo" and dynamic_photo),
            )
            for target in state.targets
        ]

    def _rebuild_targets_from_history(self, history_targets: list[HistoryTarget]) -> list[WindowTarget]:
        """Rebuild display targets from semantic history using the current transition mode."""
        current_transition = self._effective_photo_transition()
        return [
            WindowTarget(
                presenter_name=target.presenter_name,
                image=target.image,
                transition=current_transition if target.dynamic_transition else target.transition,
                clock_time=target.clock_time,
                clock_date_text=target.clock_date_text,
                place_text=target.place_text,
                info_text=target.info_text,
                photo_identity=target.photo_identity,
                video_path=target.video_path,
                video_duration=target.video_duration,
            )
            for target in history_targets
        ]

    def _step_forward(self) -> None:
        """Advance immediately to the next state."""
        debug_print(self.config, "Manual forward step requested")
        if not self.manual_mode:
            self.paused = False
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if self.current_state is not None and self.current_state.next_callback is not None:
            self.current_state.next_callback()

    def _step_backward(self) -> None:
        """Restore the previous displayed state."""
        debug_print(self.config, "Manual backward step requested")
        if not self.manual_mode:
            self.paused = False
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if not self.history:
            debug_print(self.config, "No previous state available")
            return
        previous_state = self.history.pop()
        if previous_state.history_targets is not None:
            previous_state.targets = self._rebuild_targets_from_history(previous_state.history_targets)
        self._display_state(previous_state, push_history=False)

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
            swap_targets.append(
                WindowTarget(
                    "photo",
                    photo_image,
                    Transition.SWITCH,
                    old_photo_target.clock_time,
                    old_photo_target.clock_date_text,
                    old_photo_target.place_text,
                    old_photo_target.info_text,
                    old_photo_target.photo_identity,
                )
            )
        if old_map_target is not None and map_image is not None:
            swap_targets.append(WindowTarget("map", map_image, Transition.SWITCH))
        if swap_targets:
            self._show_targets(swap_targets, on_complete=None)
            self.role_targets = {target.presenter_name: target for target in swap_targets}
            self._reset_photo_layouts(None, preserve_current_image=True)

    def _toggle_fullscreen(self) -> None:
        """Toggle fullscreen/window mode."""
        self.fullscreen_active = not self.fullscreen_active
        debug_print(self.config, f"Toggling fullscreen: active={self.fullscreen_active}")
        if self.config.join_windows or not self.config.mapwindow or self.map_window is None:
            if self.photo_window is not None:
                self.photo_window.toggleFullScreen_(None)
            return

        screens = self._available_screens()
        if self.photo_window is not None:
            self.photo_window.toggleFullScreen_(None)
        if len(screens) > 1 and self.map_window is not None:
            self.schedule_callback(0.15, lambda: self.map_window.toggleFullScreen_(None))
        elif self.map_window is not None:
            self.map_window.setLevel_(NSFloatingWindowLevel if self.fullscreen_active else 0)
            if screens:
                self._apply_window_to_screen(self.map_window, screens[0], self.fullscreen_active, half_size=True)
            self.map_window.orderFrontRegardless()
        if not self.fullscreen_active:
            self.schedule_callback(0.3, self._apply_screen_layout)

    def schedule_callback(self, delay_seconds: float, callback: Callable[[], None]):
        debug_print(self.config, f"Scheduling callback in {delay_seconds:.3f}s for {getattr(callback, '__name__', repr(callback))}")
        target = TimerTarget.alloc().initWithCallback_(callback)
        target.debug_config = self.config
        target.debug_context = getattr(callback, "__name__", repr(callback))
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.0, delay_seconds),
            target,
            "fire:",
            None,
            False,
        )
        handle = ScheduledCallback(timer, target)
        self.timer_handles.append(handle)
        return handle

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

            self.photo_presenter = CocoaImagePresenter(
                photo_host,
                self.config.background_color,
                self.schedule_callback,
                self.config.collage_size_min,
                self.config.collage_size_max,
                self.config.collage_max_images,
            )
            self.map_presenter = CocoaImagePresenter(map_host, self.config.background_color, self.schedule_callback)
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
        self.photo_presenter = CocoaImagePresenter(
            photo_host,
            self.config.background_color,
            self.schedule_callback,
            self.config.collage_size_min,
            self.config.collage_size_max,
            self.config.collage_max_images,
        )
        debug_print(self.config, "Created photo window")
        self.photo_window.makeKeyAndOrderFront_(None)
        self._update_window_titles()

        if self.config.mapwindow:
            self.map_window = self._create_window("GPSTrackShow - Maps", self.config.map_geometry, False)
            map_host = NSView.alloc().initWithFrame_(self.map_window.contentView().bounds())
            map_host.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            self.map_window.contentView().addSubview_(map_host)
            self.map_presenter = CocoaImagePresenter(map_host, self.config.background_color, self.schedule_callback)
            debug_print(self.config, "Created map window")
            self.map_window.makeKeyAndOrderFront_(None)
            self._update_window_titles()

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
        window.setTitle_(title)
        behavior = NSWindowCollectionBehaviorFullScreenPrimary
        window.setCollectionBehavior_(behavior)
        delegate = GPSTrackShowWindowDelegate.alloc().initWithController_(self)
        window.setDelegate_(delegate)
        self.window_delegates.append(delegate)
        return window

    def start(self, run_loop: bool = True) -> None:
        self.owns_run_loop = bool(run_loop)
        debug_print(self.config, "Starting application run loop" if self.owns_run_loop else "Starting embedded slideshow")
        self._show_initial_photo_preview()
        self._show_startup_hint()
        if self.config.debug:
            self._show_startup_test_image()
        else:
            self.schedule_callback(0.0, self._advance)
        self.app.activateIgnoringOtherApps_(True)
        if self.owns_run_loop:
            self.app.run()

    def _show_startup_hint(self) -> None:
        """Show the temporary startup help hint on the photo screen."""
        if self.photo_presenter is None:
            return
        self.photo_presenter.set_startup_hint_visible(True)
        self.schedule_callback(5.0, lambda: self.photo_presenter.set_startup_hint_visible(False))

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

    def _display_state(self, state: DisplayState, push_history: bool = True) -> None:
        """Display one slideshow state and optionally schedule its continuation."""
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        if state.history_targets is None:
            state.history_targets = self._build_history_targets(state)
        if push_history and self.current_state is not None:
            self.history.append(self.current_state)
        merged_targets = dict(self.role_targets)
        for target in state.targets:
            merged_targets[target.presenter_name] = target
        self.role_targets = merged_targets
        state.targets = self._ordered_targets(merged_targets)
        self.current_state = state
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
            ),
            push_history=False,
        )

    def _advance(self) -> None:
        debug_print(self.config, f"Advance called at playlist index {self.playlist_index}")
        if not self.running:
            return
        if self.playlist_index >= len(self.playlist_lines):
            if self.config.repeat:
                self._prime_context_before_index(self.start_playlist_index)
                self.playlist_index = self.start_playlist_index
            else:
                self.quit()
                return

        line = self.playlist_lines[self.playlist_index]
        self.playlist_index += 1
        debug_print(self.config, f"Processing line: {line}")
        if line.startswith("#Overviewmap:"):
            self._handle_overview(line.partition(":")[2].strip())
            self._advance()
            return
        if line.startswith("#Datum:"):
            self.current_date = line.partition(":")[2].strip()
            if self.active_transition == Transition.QUAD:
                self._reset_photo_layouts(Transition.QUAD)
            elif self.active_transition == Transition.COLLAGE:
                self._reset_photo_layouts(Transition.COLLAGE)
            self._advance()
            return
        if line.startswith("#Map:"):
            self._handle_map(line.partition(":")[2].strip())
            return
        self._handle_photo(parse_photo_entry(line))

    def _handle_overview(self, filename: str) -> None:
        image_path = resolve_path(self._track_asset_dir(), filename)
        debug_print(self.config, f"Loading overview image {image_path}")
        self.current_overview_image = load_nsimage(image_path)
        metadata_path = image_path.with_suffix(".json")
        self.current_overview_metadata = try_read_plot_metadata(metadata_path)
        if self.current_overview_metadata is not None:
            debug_print(self.config, f"Overview metadata loaded from {metadata_path}")
        else:
            debug_print(self.config, "Overview metadata missing; continuing without overview markers")

    def _handle_map(self, filename: str) -> None:
        if self.current_overview_image is None:
            raise RuntimeError("encountered #Map before #Overviewmap")
        self._choose_random_transition_for_track()
        if self.active_transition == Transition.QUAD:
            self._reset_photo_layouts(Transition.QUAD)
        elif self.active_transition == Transition.COLLAGE:
            self._reset_photo_layouts(Transition.COLLAGE)

        track_path = resolve_path(self._track_asset_dir(), filename)
        debug_print(self.config, f"Loading track map {track_path}")
        self.current_track_image = load_nsimage(track_path)
        metadata_path = track_path.with_suffix(".json")
        self.current_track_metadata = try_read_plot_metadata(metadata_path)
        if self.current_track_metadata is not None:
            debug_print(self.config, f"Track map metadata loaded from {metadata_path}")
        else:
            debug_print(self.config, "Track map metadata missing; continuing without track markers")
        debug_print(self.config, "Creating rendered overview image for current track")
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
            warn_message("overview or track metadata missing; skipping overview endpoint markers")
        debug_print(self.config, "Overview image ready; preparing to show overview then track")
        track_image = self.current_track_image

        if self.photo_presenter is not None:
            # Always show the overview as a full-screen single image on the photo side,
            # even when the active photo transition is QUAD or COLLAGE.
            self.photo_presenter.reset_photo_layout(None)

        def show_track() -> None:
            debug_print(self.config, "Switching from overview image to track image")
            if self.map_presenter is not None:
                self._advance()
                return
            targets = [WindowTarget("photo", track_image, self.config.transition)]
            self._display_state(
                DisplayState(
                    targets=targets,
                    next_callback=self._advance,
                    auto_delay=self.config.duration,
                    description="Track map",
                    playlist_index=self.playlist_index,
                )
            )

        if self.map_presenter is not None:
            overview_targets = [
                WindowTarget("photo", overview_image, Transition.FADE),
                WindowTarget("map", track_image, self.config.transition),
            ]
        else:
            overview_targets = [WindowTarget("photo", overview_image, self.config.transition)]
        self._display_state(
            DisplayState(
                targets=overview_targets,
                next_callback=show_track,
                auto_delay=self.config.duration,
                description="Overview map",
                playlist_index=self.playlist_index,
            )
        )

    def _handle_photo(self, entry: PhotoListEntry) -> None:
        photo_path = resolve_path(self.config.photodir, entry.source_name)
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
            metadata_path = photo_path.with_suffix(".json")
            photo_metadata = try_read_photo_metadata(metadata_path) or {}
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

        def make_photo_target(transition: Transition) -> WindowTarget:
            return WindowTarget(
                "photo",
                photo_image,
                transition,
                clock_time,
                clock_date_text,
                place_text,
                info_text,
                photo_identity,
                video_path,
                video_delay if video_path is not None else None,
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
        if self.current_track_image is not None:
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

        if marked_track is not None:
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
            if on_complete is not None:
                on_complete()

        active_photo_presenter = None
        active_info_presenter = None
        active_place_presenter = None
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
                active_place_presenter = presenter
            debug_print(self.config, f"Sending image to presenter '{target.presenter_name}' with transition {target.transition}")
            presenter.set_clock_time(
                target.clock_time if target.presenter_name == "photo" and self.config.clock else None,
                target.clock_date_text if target.presenter_name == "photo" and self.config.clock else None,
            )
            presenter.set_place_text(
                target.place_text if target.presenter_name == "photo" else None,
                bool(target.presenter_name == "photo" and self.config.placenames),
                self.config.font_size,
                self.config.font_color,
            )
            presenter.set_info_text(target.info_text if target.presenter_name == "photo" else None)
            effective_transition = Transition.SWITCH if target.presenter_name == "map" else target.transition
            if target.presenter_name == "photo":
                def photo_complete(current_presenter=presenter, current_target=target, transition=effective_transition) -> None:
                    if current_target.video_path is not None:
                        frame_rect = current_presenter.last_media_rect if transition in {Transition.COLLAGE, Transition.QUAD} else None
                        current_presenter.play_video(current_target.video_path, frame_rect)
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
        if self.map_presenter is not None and self.map_presenter is not active_photo_presenter:
            self.map_presenter.set_clock_time(None)
        if self.photo_presenter is not None and self.photo_presenter is not active_info_presenter:
            self.photo_presenter.set_info_text(None)
        if self.map_presenter is not None and self.map_presenter is not active_info_presenter:
            self.map_presenter.set_info_text(None)
        if self.photo_presenter is not None and self.photo_presenter is not active_place_presenter:
            self.photo_presenter.set_place_text(None, False, self.config.font_size, self.config.font_color)
        if self.map_presenter is not None and self.map_presenter is not active_place_presenter:
            self.map_presenter.set_place_text(None, False, self.config.font_size, self.config.font_color)
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

    def quit(self) -> None:
        if not self.running:
            return
        debug_print(self.config, "Quitting application")
        self.running = False
        if self.active_callback is not None:
            self.active_callback.cancel()
            self.active_callback = None
        for handle in list(self.timer_handles):
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
        self.history.clear()
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
