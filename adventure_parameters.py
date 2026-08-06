#!/usr/bin/env python3
"""Typed project parameter registry shared by the myCamino applications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from gpx_processing import (
    DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M,
    DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH,
)


PARAMETER_SCHEMA_VERSION = 18


@dataclass(frozen=True)
class ParameterSpec:
    key: str
    section: str
    label: str
    default: Any
    value_type: str
    help_text: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[tuple[str, str], ...] = ()
    advanced: bool = False
    unit: str = ""
    subsection: str = ""


def _choice(*items: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return items


PARAMETER_SPECS = (
    ParameterSpec("slideshow.media_duration_seconds", "Slide Show", "Media duration", 3.0, "float", "Minimum display time for a photo in automatic playback.", 0.1, 3600.0, unit="s"),
    ParameterSpec("slideshow.transition", "Slide Show", "Initial style", "time_lapse", "choice", "Initial playback style. Use t/T during playback to cycle through all styles.", choices=_choice(("time_lapse", "Time-Lapse"), ("blend", "Blend"), ("fade", "Fade"), ("switch", "Switch"), ("expand", "Expand"), ("collage", "Collage"), ("quad", "Quad"), ("random", "Random"))),
    ParameterSpec("slideshow.transition_duration_ms", "Slide Show", "Transition duration", 700, "int", "Duration of animated image transitions.", 0, 10000, advanced=True, unit="ms"),
    ParameterSpec("slideshow.background_color", "Slide Show", "Background color", "#000000", "color", "Color behind maps and media."),
    ParameterSpec("slideshow.marker_color", "Slide Show", "Marker color", "#FF0000", "color", "Color of the moving GPS marker."),
    ParameterSpec("slideshow.marker_radius", "Slide Show", "Marker radius", 6, "int", "Radius of the GPS marker.", 1, 100, unit="px"),
    ParameterSpec("slideshow.arrow_scale", "Slide Show", "Arrow scale", 1.0, "float", "Scale factor for the direction arrow; zero hides it.", 0.0, 10.0),
    ParameterSpec("slideshow.font_color", "Slide Show", "Font color", "#FFFFFF", "color", "Color used for header text, clock marks and hands, time, and date.", subsection="Header"),
    ParameterSpec("slideshow.header_shadow_color", "Slide Show", "Shadow color", "#000000", "color", "Color used for header text, clock, time, and date shadows.", subsection="Header"),
    ParameterSpec("slideshow.font_size", "Slide Show", "Font size", 30, "int", "Base header font size.", 8, 200, unit="pt", subsection="Header"),
    ParameterSpec("slideshow.font_family", "Slide Show", "Font family", "System", "str", "Font family used for automatic headers and #CAPTION text.", subsection="Header"),
    ParameterSpec("slideshow.font_style", "Slide Show", "Font style", "bold", "choice", "Default style used for automatic headers and #CAPTION text.", choices=_choice(("regular", "Regular"), ("bold", "Bold"), ("italic", "Italic"), ("bold-italic", "Bold Italic")), subsection="Header"),
    ParameterSpec("slideshow.clock", "Slide Show", "Clock", True, "bool", "Show the analog clock at the left of the slide-show header when timing is available.", subsection="Header"),
    ParameterSpec("slideshow.header_stage_name", "Slide Show", "Stage name", True, "bool", "Show the stage name as the first available line in the middle of the header.", subsection="Header"),
    ParameterSpec("slideshow.header_track_details", "Slide Show", "Track length & duration", True, "bool", "Show the stage track length and duration as the next available header line.", subsection="Header"),
    ParameterSpec("slideshow.header_place_name", "Slide Show", "Place name", True, "bool", "Show the current reverse-geocoded place as the next available header line.", subsection="Header"),
    ParameterSpec("slideshow.header_track_stats", "Slide Show", "Track statistics", True, "bool", "Show total distance, stage distance, and elevation at the right of the header when available.", subsection="Header"),
    ParameterSpec("slideshow.header_background", "Slide Show", "Header layout", "black", "choice", "Use one layout for photos, Time-Lapse maps, and overview maps. No box and Semi-transparent remain full-frame; Header area fits content below an opaque band in the selected background color.", choices=_choice(("off", "No box"), ("transparent", "Semi-transparent overlay"), ("black", "Header area (background color)")), subsection="Header"),
    ParameterSpec("slideshow.elevation_profile", "Slide Show", "Show elevation profile", True, "bool", "Show the cached min/max elevation profile in the Stage Map at the beginning of every GPX track."),
    ParameterSpec("slideshow.fullscreen", "Slide Show", "Fullscreen", "auto", "choice", "Choose automatic, always-on, or windowed startup.", choices=_choice(("auto", "Auto"), ("on", "On"), ("off", "Off"))),
    ParameterSpec("slideshow.window_mode", "Slide Show", "Window mode", "auto", "choice", "Automatic uses one window on one screen and a separate overview window when multiple screens are available.", choices=_choice(("auto", "Automatic"), ("single", "Single window"), ("multiple", "Separate overview window"))),
    ParameterSpec("slideshow.track_map_before_media", "Slide Show", "Track map before each medium", False, "bool", "In single-window Standard playback, briefly show the marked track map before every photo or video. The stage map is always shown once at the beginning of its stage."),
    ParameterSpec("slideshow.join_windows", "Slide Show", "Join windows", False, "bool", "Place photo and map roles side-by-side in one window."),
    ParameterSpec("slideshow.display_swap", "Slide Show", "Swap displays", False, "bool", "Swap the initial photo and map display assignment."),
    ParameterSpec("slideshow.end_behavior", "Slide Show", "At end", "loop_forever", "choice", "Show a black final slide, replay the complete show once, or loop forever. Every replay starts with the title slide.", choices=_choice(("black", "Black final slide"), ("loop_once", "Loop once"), ("loop_forever", "Loop forever"))),
    ParameterSpec("slideshow.manual_start", "Slide Show", "Start manually", False, "bool", "Start in manual rather than automatic playback mode."),
    ParameterSpec("slideshow.collage_size_range", "Slide Show", "Collage size range", "33-66", "range", "Minimum and maximum collage image size in percent, for example 33-66.", unit="%"),
    ParameterSpec("slideshow.collage_max_images", "Slide Show", "Maximum collage images", 9, "int", "Clear the collage after this many images.", 1, 100),

    ParameterSpec("audio.enabled", "Audio", "Background music", False, "bool", "Enable the optional background-music source for this Adventure."),
    ParameterSpec("audio.crossfade_seconds", "Audio", "Crossfade duration", 2.0, "float", "Fade duration for normal music changes, marker jumps, pause, and resume.", 0.0, 30.0, unit="s"),
    ParameterSpec("audio.music_volume_percent", "Audio", "Music volume", 65.0, "float", "Maximum internal background-music level. Device volume remains authoritative.", 0.0, 100.0, unit="%"),
    ParameterSpec("audio.video_volume_percent", "Audio", "Video volume", 100.0, "float", "Playback level for the sound contained in videos.", 0.0, 100.0, unit="%"),
    ParameterSpec("audio.use_normalized_videos", "Audio", "Use normalized video audio", True, "bool", "Prefer current prepared video copies with normalized audio during playback."),
    ParameterSpec("audio.video_normalization_target_lufs", "Audio", "Video normalization target", -16.0, "float", "Target integrated loudness for prepared video audio.", -40.0, -5.0, unit="LUFS"),
    ParameterSpec("audio.video_normalization_max_boost_db", "Audio", "Maximum video boost", 12.0, "float", "Maximum gain applied to a quiet video during normalization.", 0.0, 30.0, unit="dB"),
    ParameterSpec("audio.video_normalization_true_peak_db", "Audio", "Maximum true peak", -1.5, "float", "True-peak ceiling used while preparing normalized video audio.", -10.0, 0.0, unit="dBTP"),

    ParameterSpec("timelapse.stage_duration_seconds", "Slide Show", "Stage duration", 30.0, "float", "Active arrow-motion duration for one stage.", 1.0, 3600.0, unit="s", subsection="Time-Lapse"),
    ParameterSpec("timelapse.media_min_fraction", "Slide Show", "Preferred media minimum", 0.5, "fraction", "Preferred minimum framed-media size; track-free space can allow larger media.", 0.01, 1.0, unit="%", subsection="Time-Lapse"),
    ParameterSpec("timelapse.overview_as_media", "Slide Show", "Overview inside track map", True, "bool", "In single-window mode, show the overview as a framed medium over the stage map. Disable this to show it full-screen before the stage.", subsection="Time-Lapse"),
    ParameterSpec("timelapse.overview_on_stage_map_dual", "Slide Show", "Overview inset with second display", True, "bool", "Also show the overview as the first Stage Map inset when a separate overview display is active.", subsection="Time-Lapse"),
    ParameterSpec("timelapse.marker_style", "Slide Show", "Moving marker", "pilgrim", "choice", "Choose the moving symbol shown at the current track position.", choices=_choice(("pilgrim", "Walking pilgrim"), ("bike", "Bicycle"), ("car", "Car"), ("plane", "Airplane"), ("arrow", "Arrow")), subsection="Time-Lapse"),
    ParameterSpec("slideshow.speedometer", "Slide Show", "Show speedometer", True, "bool", "Show distance-smoothed moving speed in Time-Lapse when recorded timing is available.", subsection="Time-Lapse"),

    ParameterSpec("trackmaps.ordering", "Map Generation", "Track ordering", "track_number", "choice", "Order maps by recording date or original track number.", choices=_choice(("date", "Date"), ("track_number", "Track number"))),
    ParameterSpec("trackmaps.route_source", "Map Generation", "Journey source", "automatic", "choice", "Use GPX tracks when available, require GPX tracks, or build date stages from media locations.", choices=_choice(("automatic", "Automatic"), ("gpx", "GPX tracks"), ("media", "Media locations"))),
    ParameterSpec("trackmaps.gpx_overlay", "Map Generation", "GPX route display", "line", "choice", "Draw the measured GPX route dynamically or hide it.", choices=_choice(("line", "Line"), ("hidden", "Hidden"))),
    ParameterSpec("trackmaps.media_overlay", "Map Generation", "Media route display", "dots", "choice", "Show measured photo positions as dots, connect them by an estimated line, or hide them.", choices=_choice(("dots", "Photo dots"), ("interpolated", "Interpolated line"), ("hidden", "Hidden"))),
    ParameterSpec("trackmaps.dynamic_header", "Map Generation", "Show map header", True, "bool", "Draw map titles and dates dynamically so their appearance can be changed without rebuilding the basemap."),
    ParameterSpec("trackmaps.track_title", "Map Generation", "Track title", "endpoint_places", "choice", "Use reverse-geocoded start and end places for GPX-stage titles, with the GPX track name as fallback, or always use the GPX track name.", choices=_choice(("endpoint_places", "Start - destination"), ("track_name", "GPX track name"))),
    ParameterSpec("trackmaps.image_size", "Map Generation", "Image size", "1920x1080", "image_size", "Output map dimensions in pixels."),
    ParameterSpec("trackmaps.zoom", "Map Generation", "Map zoom", 16, "int", "Requested basemap zoom level.", 0, 22),
    ParameterSpec("trackmaps.route_width", "Map Generation", "Route width", 4.0, "float", "Width of the plotted route line.", 0.1, 50.0),
    ParameterSpec("trackmaps.route_color", "Map Generation", "Route color", "#0000FF", "color", "Color of the plotted route."),
    ParameterSpec("trackmaps.endpoint_color", "Map Generation", "Endpoint color", "#FFFFFF", "color", "Color of start/end point dots."),
    ParameterSpec("trackmaps.endpoint_size", "Map Generation", "Endpoint size", 0.0, "float", "Size of start/end dots; zero hides them.", 0.0, 500.0),
    ParameterSpec("trackmaps.media_point_color", "Map Generation", "Media point color", "#0066FF", "color", "Color used for media-derived location dots and estimated routes."),
    ParameterSpec("trackmaps.background_color", "Map Generation", "Background color", "#000000", "color", "Background outside the map axes."),
    ParameterSpec("trackmaps.title_color", "Map Generation", "Title color", "#FFFFFF", "color", "Track map title and subtitle color."),
    ParameterSpec("trackmaps.font_factor", "Map Generation", "Font factor", 2.2, "float", "Multiplier for automatically selected map label sizes.", 0.1, 10.0),
    ParameterSpec("trackmaps.overview_labels", "Map Generation", "Overview labels", "none", "choice", "Labels printed on the overview map.", choices=_choice(("none", "None"), ("default", "Track, date, length"))),
    ParameterSpec("trackmaps.remove_name_prefix", "Map Generation", "Remove name prefix", "", "text", "Remove this prefix from track names when displaying them."),
    ParameterSpec("trackmaps.edge_margin_fraction", "Map Generation", "Time-Lapse edge margin", 0.05, "fraction", "Minimum route distance from the plotting-area edge.", 0.0, 0.49, advanced=True, unit="%"),

    ParameterSpec("gpx.fallback_walking_speed_kmh", "GPX Processing", "Fallback walking speed", 3.5, "float", "Speed used when missing timestamps cannot be anchored.", 0.1, 50.0, unit="km/h"),
    ParameterSpec("gpx.horizontal_smoothing_distance_m", "GPX Processing", "Horizontal smoothing", 10.0, "float", "Smooth horizontal GPS coordinates over this route distance before spacing and length calculations. Set to zero to disable.", 0.0, 10000.0, unit="m"),
    ParameterSpec("gpx.minimum_point_spacing_m", "GPX Processing", "Minimum point spacing", 10.0, "float", "Retain processed points at least this far apart. Set to zero to retain every quality-accepted point.", 0.0, 10000.0, unit="m"),
    ParameterSpec("gpx.maximum_accuracy_m", "GPX Processing", "Maximum horizontal error", 10.0, "float", "Reject coordinates whose explicit horizontal uncertainty exceeds this value. Set to zero to disable.", 0.0, 10000.0, unit="m"),
    ParameterSpec("gpx.maximum_vertical_accuracy_m", "GPX Processing", "Maximum vertical error", 20.0, "float", "Ignore elevations whose explicit vertical uncertainty exceeds this value. Set to zero to disable.", 0.0, 10000.0, unit="m"),
    ParameterSpec("gpx.maximum_hdop", "GPX Processing", "Maximum HDOP", 20.0, "float", "Reject coordinates above this horizontal dilution of precision. Set to zero to disable.", 0.0, 10000.0),
    ParameterSpec("gpx.maximum_vdop", "GPX Processing", "Maximum VDOP", 20.0, "float", "Ignore elevations above this vertical dilution of precision. Set to zero to disable.", 0.0, 10000.0),
    ParameterSpec("gpx.running_speed_window_distance_m", "GPX Processing", "Running-speed window", DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M, "float", "Calculate each displayed speed over this centered route distance.", 0.0, 10000.0, unit="m"),
    ParameterSpec("gpx.stationary_speed_threshold_kmh", "GPX Processing", "Stationary speed threshold", DEFAULT_STATIONARY_SPEED_THRESHOLD_KMH, "float", "Intervals slower than this are excluded from moving-average speed. Set to zero to include them.", 0.0, 100.0, unit="km/h"),
    ParameterSpec("gpx.editor_autosave_seconds", "GPX Processing", "Editor autosave interval", 300.0, "float", "Interval between GPX Editor recovery saves.", 10.0, 86400.0, unit="s"),
    ParameterSpec("gpx.map_padding_fraction", "GPX Processing", "Map padding", 0.08, "fraction", "Padding around tracks in interactive maps.", 0.0, 1.0, unit="%"),
    ParameterSpec("gpx.overview_zoom", "GPX Processing", "Overview zoom", 8, "int", "Default GPX Editor overview tile zoom.", 0, 22),
    ParameterSpec("gpx.track_zoom", "GPX Processing", "Track zoom", 14, "int", "Default GPX Editor track tile zoom.", 0, 22),
    ParameterSpec("gpx.elevation_smoothing_distance_m", "GPX Processing", "Elevation smoothing", 50.0, "float", "Smooth GPS elevations over this route distance before calculating ascent and descent. Set to zero to use raw point-to-point elevations.", 0.0, 1000.0, unit="m"),
    ParameterSpec("gpx.elevation_headroom_fraction", "GPX Processing", "Elevation headroom", 0.08, "fraction", "Vertical headroom above and below elevation profiles.", 0.0, 1.0, unit="%"),
    ParameterSpec("gpx.maximum_map_tiles", "GPX Processing", "Maximum interactive tiles", 48, "int", "Lower zoom automatically when an interactive map would exceed this tile count.", 1, 1000, advanced=True),

    ParameterSpec("pdf.document_dpi", "PDF Export", "Document DPI", 200, "int", "Rendering resolution for PDF pages.", 72, 1200),
    ParameterSpec("pdf.map_dpi", "PDF Export", "Embedded map DPI", 600, "int", "Pixel density used for embedded PDF maps.", 72, 2400),
    ParameterSpec("pdf.overview_zoom", "PDF Export", "Overview zoom", 8, "int", "Tile zoom for PDF overview maps.", 0, 22),
    ParameterSpec("pdf.track_zoom", "PDF Export", "Track zoom", 14, "int", "Tile zoom for PDF track maps.", 0, 22),
    ParameterSpec("pdf.maximum_map_tiles", "PDF Export", "Maximum PDF map tiles", 24, "int", "Maximum tile count for one embedded PDF map.", 1, 1000, advanced=True),

    ParameterSpec("locations.add_place_names", "Locations", "Add place names", True, "bool", "Add readable place names while media metadata is prepared or updated."),
    ParameterSpec("locations.reuse_radius_m", "Locations", "Place-name search radius", 150.0, "float", "GPS positions within this radius reuse the same place name. This reduces map lookups and speeds up place-name extraction.", 0.0, 100000.0, unit="m"),
    ParameterSpec("locations.timeout_seconds", "Locations", "Request timeout", 10.0, "float", "Maximum wait for one Apple reverse-geocoding request.", 1.0, 300.0, advanced=True, unit="s"),
    ParameterSpec("locations.pacing_min_seconds", "Locations", "Minimum request spacing", 1.0, "float", "Minimum delay between reverse-geocoding requests.", 0.0, 60.0, advanced=True, unit="s"),
    ParameterSpec("locations.pacing_max_seconds", "Locations", "Maximum request spacing", 5.0, "float", "Maximum delay between reverse-geocoding requests.", 0.0, 60.0, advanced=True, unit="s"),

    ParameterSpec("maps.provider", "Map Service", "Map provider", "osm", "choice", "Basemap provider shared by Track Maps and GPX Editor.", choices=_choice(("osm", "OpenStreetMap"), ("esri", "Esri World Street Map"), ("custom", "Custom"))),
    ParameterSpec("maps.custom_url", "Map Service", "Custom URL template", "", "text", "HTTP(S) tile URL containing {z}, {x}, and {y}.", advanced=True),
    ParameterSpec("maps.custom_attribution", "Map Service", "Custom attribution", "", "text", "Required map attribution for a custom provider.", advanced=True),
    ParameterSpec("maps.maximum_zoom", "Map Service", "Maximum provider zoom", 19, "int", "Maximum supported tile zoom.", 0, 30),
    ParameterSpec("maps.request_timeout_seconds", "Map Service", "Tile request timeout", 12.0, "float", "Maximum wait for a map tile request.", 1.0, 300.0, advanced=True, unit="s"),
    ParameterSpec("maps.cache_retention_hours", "Map Service", "Tile cache retention", 24.0, "float", "Delete cached tiles older than this age.", 0.0, 8760.0, advanced=True, unit="h"),
)


SPECS_BY_KEY = {spec.key: spec for spec in PARAMETER_SPECS}
SECTION_ORDER = tuple(dict.fromkeys(spec.section for spec in PARAMETER_SPECS))
EDITOR_PARAMETER_SECTIONS = ("GPX Processing", "PDF Export", "Map Service")
EDITOR_PARAMETER_KEYS = tuple(
    spec.key for spec in PARAMETER_SPECS if spec.section in EDITOR_PARAMETER_SECTIONS
)
COLOR_NAMES = {"black", "white", "red", "blue", "green", "yellow", "gray", "grey", "orange", "cyan", "magenta"}


def default_parameters() -> dict[str, Any]:
    return {spec.key: spec.default for spec in PARAMETER_SPECS}


def specs_for_section(section: str, include_advanced: bool = False) -> tuple[ParameterSpec, ...]:
    return tuple(
        spec
        for spec in PARAMETER_SPECS
        if spec.section == section and (include_advanced or not spec.advanced)
    )


def visible_specs_for_section(
    section: str,
    values: dict[str, Any] | None = None,
    include_advanced: bool = False,
) -> tuple[ParameterSpec, ...]:
    """Return settings visible for a section and its current dependent choices."""
    values = values or {}
    custom_provider = values.get("maps.provider") == "custom"
    custom_keys = {"maps.custom_url", "maps.custom_attribution"}
    return tuple(
        spec
        for spec in PARAMETER_SPECS
        if spec.section == section
        and (
            (spec.key in custom_keys and custom_provider)
            or (spec.key not in custom_keys and (include_advanced or not spec.advanced))
        )
    )


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
    raise ValueError("must be on or off")


def normalize_parameter_value(spec: ParameterSpec, value: Any) -> Any:
    if spec.value_type == "bool":
        return _normalize_bool(value)
    if spec.value_type == "int":
        if isinstance(value, bool):
            raise ValueError("must be a whole number")
        try:
            numeric = float(str(value).strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError("must be a whole number") from exc
        if not numeric.is_integer():
            raise ValueError("must be a whole number")
        number = int(numeric)
        normalized: Any = number
    elif spec.value_type in {"float", "fraction"}:
        if isinstance(value, bool):
            raise ValueError("must be a number")
        normalized = float(str(value).strip().replace(",", "."))
        if spec.value_type == "fraction" and normalized > 1.0:
            normalized /= 100.0
    elif spec.value_type == "choice":
        normalized = str(value).strip()
        allowed = {item[0] for item in spec.choices}
        if normalized not in allowed:
            raise ValueError(f"must be one of: {', '.join(sorted(allowed))}")
        return normalized
    elif spec.value_type == "color":
        normalized = str(value).strip()
        if normalized.lower() not in COLOR_NAMES and not re.fullmatch(r"#[0-9A-Fa-f]{6}", normalized):
            raise ValueError("must be a named color or #RRGGBB")
        return normalized.upper() if normalized.startswith("#") else normalized.lower()
    elif spec.value_type == "image_size":
        normalized = str(value).lower().replace(" ", "")
        match = re.fullmatch(r"(\d+)x(\d+)", normalized)
        if not match or int(match.group(1)) < 100 or int(match.group(2)) < 100:
            raise ValueError("must look like WIDTHxHEIGHT with both dimensions at least 100")
        return normalized
    elif spec.value_type == "range":
        normalized = str(value).strip().replace("%", "")
        match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*[-,]\s*(\d+(?:[.,]\d+)?)\s*", normalized)
        if not match:
            raise ValueError("must look like MIN-MAX")
        minimum, maximum = (float(item.replace(",", ".")) for item in match.groups())
        if minimum <= 0.0 or maximum > 100.0 or minimum > maximum:
            raise ValueError("must satisfy 0 < MIN <= MAX <= 100")
        return f"{minimum:g}-{maximum:g}"
    else:
        return str(value)

    if spec.minimum is not None and normalized < spec.minimum:
        raise ValueError(f"must be at least {spec.minimum:g}")
    if spec.maximum is not None and normalized > spec.maximum:
        raise ValueError(f"must be at most {spec.maximum:g}")
    return normalized


def validate_parameters(values: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    normalized = default_parameters()
    for spec in PARAMETER_SPECS:
        try:
            normalized[spec.key] = normalize_parameter_value(spec, values.get(spec.key, spec.default))
        except (TypeError, ValueError) as exc:
            errors[spec.key] = str(exc)
    if not errors:
        if normalized["locations.pacing_min_seconds"] > normalized["locations.pacing_max_seconds"]:
            message = "minimum spacing must not exceed maximum spacing"
            errors["locations.pacing_min_seconds"] = message
            errors["locations.pacing_max_seconds"] = message
        if normalized["maps.provider"] == "custom":
            template = normalized["maps.custom_url"]
            if not template.startswith(("http://", "https://")) or not all(token in template for token in ("{z}", "{x}", "{y}")):
                errors["maps.custom_url"] = "custom URL must use HTTP(S) and contain {z}, {x}, and {y}"
            if not normalized["maps.custom_attribution"].strip():
                errors["maps.custom_attribution"] = "attribution is required for a custom provider"
    return errors


def normalize_parameters(raw: Any) -> dict[str, Any]:
    source_version = raw.get("version", 1) if isinstance(raw, dict) else 1
    values = raw.get("values", {}) if isinstance(raw, dict) and isinstance(raw.get("values"), dict) else raw
    values = values if isinstance(values, dict) else {}
    values = dict(values)
    if "slideshow.header_background" not in values and "timelapse.header_background" in values:
        values["slideshow.header_background"] = values["timelapse.header_background"]
    values.pop("timelapse.header_background", None)
    if str(values.get("slideshow.header_background", "")).strip().casefold() == "reserved":
        values["slideshow.header_background"] = "black"
    if "slideshow.header_title" in values:
        legacy_title = values.pop("slideshow.header_title")
        values.setdefault("slideshow.header_stage_name", legacy_title)
        values.setdefault("slideshow.header_track_details", legacy_title)
    if "slideshow.place_names" in values:
        values.setdefault(
            "slideshow.header_place_name",
            values.pop("slideshow.place_names"),
        )
    if "slideshow.start_mode" in values:
        start_mode = str(values.get("slideshow.start_mode") or "").strip().casefold()
        if start_mode == "time_lapse":
            values["slideshow.transition"] = "time_lapse"
        elif "slideshow.transition" not in values:
            values["slideshow.transition"] = "blend"
        values.pop("slideshow.start_mode", None)
    if "slideshow.end_behavior" not in values and "slideshow.repeat" in values:
        # The former bool was normally stored even when the user never chose it.
        # Adopt the new requested default for all pre-schema-12 Adventures.
        values["slideshow.end_behavior"] = "loop_forever"
    values.pop("slideshow.repeat", None)
    try:
        legacy_schema = int(source_version or 1) < 2
    except (TypeError, ValueError):
        legacy_schema = True
    if legacy_schema and str(values.get("trackmaps.media_point_color", "")).upper() == "#FF8C00":
        values["trackmaps.media_point_color"] = "#0066FF"
    try:
        pre_zoom_16_schema = int(source_version or 1) < 8
    except (TypeError, ValueError):
        pre_zoom_16_schema = True
    if pre_zoom_16_schema and values.get("trackmaps.zoom") == 15:
        values["trackmaps.zoom"] = 16
    try:
        pre_speed_window_500_schema = int(source_version or 1) < 17
    except (TypeError, ValueError):
        pre_speed_window_500_schema = True
    if (
        pre_speed_window_500_schema
        and values.get("gpx.running_speed_window_distance_m") == 100.0
    ):
        values["gpx.running_speed_window_distance_m"] = (
            DEFAULT_RUNNING_SPEED_WINDOW_DISTANCE_M
        )
    normalized = default_parameters()
    for spec in PARAMETER_SPECS:
        if spec.key not in values:
            continue
        try:
            normalized[spec.key] = normalize_parameter_value(spec, values[spec.key])
        except (TypeError, ValueError):
            normalized[spec.key] = spec.default
    return normalized


def parameter_payload(values: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_parameters(values)
    return {"version": PARAMETER_SCHEMA_VERSION, "values": normalized}


def changed_parameter_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {key for key in SPECS_BY_KEY if before.get(key) != after.get(key)}


def map_affecting_parameter_keys() -> frozenset[str]:
    overlay_only = {
        "trackmaps.gpx_overlay",
        "trackmaps.media_overlay",
        "trackmaps.dynamic_header",
        "trackmaps.track_title",
        "trackmaps.route_width",
        "trackmaps.route_color",
        "trackmaps.endpoint_color",
        "trackmaps.endpoint_size",
        "trackmaps.media_point_color",
        "trackmaps.title_color",
        "trackmaps.font_factor",
        "trackmaps.overview_labels",
    }
    return frozenset(
        {
            *(
                spec.key
                for spec in PARAMETER_SPECS
                if spec.section == "Map Generation"
                and spec.key not in overlay_only
                and spec.key != "trackmaps.route_source"
            ),
            "gpx.fallback_walking_speed_kmh",
            "gpx.horizontal_smoothing_distance_m",
            "gpx.minimum_point_spacing_m",
            "gpx.maximum_accuracy_m",
            "gpx.maximum_vertical_accuracy_m",
            "gpx.maximum_hdop",
            "gpx.maximum_vdop",
            "gpx.elevation_smoothing_distance_m",
            "maps.provider",
            "maps.custom_url",
            "maps.custom_attribution",
            "maps.maximum_zoom",
        }
    )


def parameter_subset(values: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: values[key] for key in keys if key in values}
