#!/usr/bin/env python3
"""Typed project parameter registry shared by the myCamino applications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


PARAMETER_SCHEMA_VERSION = 1


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


def _choice(*items: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return items


PARAMETER_SPECS = (
    ParameterSpec("slideshow.media_duration_seconds", "Slide Show", "Media duration", 3.0, "float", "Minimum display time for a photo in automatic playback.", 0.1, 3600.0, unit="s"),
    ParameterSpec("slideshow.transition", "Slide Show", "Transition", "blend", "choice", "Transition used between standard slide-show media.", choices=_choice(("blend", "Blend"), ("fade", "Fade"), ("switch", "Switch"), ("expand", "Expand"), ("collage", "Collage"), ("quad", "Quad"), ("random", "Random"))),
    ParameterSpec("slideshow.transition_duration_ms", "Slide Show", "Transition duration", 700, "int", "Duration of animated image transitions.", 0, 10000, advanced=True, unit="ms"),
    ParameterSpec("slideshow.background_color", "Slide Show", "Background color", "#000000", "color", "Color behind maps and media."),
    ParameterSpec("slideshow.font_color", "Slide Show", "Font color", "#FFFFFF", "color", "Color used for slide-show text overlays."),
    ParameterSpec("slideshow.font_size", "Slide Show", "Font size", 30, "int", "Base overlay font size.", 8, 200, unit="pt"),
    ParameterSpec("slideshow.marker_color", "Slide Show", "Marker color", "#FF0000", "color", "Color of the moving GPS marker."),
    ParameterSpec("slideshow.marker_radius", "Slide Show", "Marker radius", 6, "int", "Radius of the GPS marker.", 1, 100, unit="px"),
    ParameterSpec("slideshow.arrow_scale", "Slide Show", "Arrow scale", 1.0, "float", "Scale factor for the direction arrow; zero hides it.", 0.0, 10.0),
    ParameterSpec("slideshow.clock", "Slide Show", "Show clock", True, "bool", "Show the analog clock when timing is available."),
    ParameterSpec("slideshow.place_names", "Slide Show", "Show place names", True, "bool", "Show reverse-geocoded place names."),
    ParameterSpec("slideshow.start_mode", "Slide Show", "Default show", "time_lapse", "choice", "Slide-show type selected by default in the main window.", choices=_choice(("time_lapse", "Time-Lapse"), ("standard", "Standard"))),
    ParameterSpec("slideshow.fullscreen", "Slide Show", "Fullscreen", "auto", "choice", "Choose automatic, always-on, or windowed startup.", choices=_choice(("auto", "Auto"), ("on", "On"), ("off", "Off"))),
    ParameterSpec("slideshow.window_mode", "Slide Show", "Window mode", "auto", "choice", "Automatic uses one window on one screen and a separate overview window when multiple screens are available.", choices=_choice(("auto", "Automatic"), ("single", "Single window"), ("multiple", "Separate overview window"))),
    ParameterSpec("slideshow.track_map_before_media", "Slide Show", "Track map before each medium", False, "bool", "In single-window Standard playback, briefly show the marked track map before every photo or video. The stage map is always shown once at the beginning of its stage."),
    ParameterSpec("slideshow.join_windows", "Slide Show", "Join windows", False, "bool", "Place photo and map roles side-by-side in one window."),
    ParameterSpec("slideshow.display_swap", "Slide Show", "Swap displays", False, "bool", "Swap the initial photo and map display assignment."),
    ParameterSpec("slideshow.repeat", "Slide Show", "Repeat", False, "bool", "Restart after the final control-file row."),
    ParameterSpec("slideshow.manual_start", "Slide Show", "Start manually", False, "bool", "Start in manual rather than automatic playback mode."),
    ParameterSpec("slideshow.collage_size_range", "Slide Show", "Collage size range", "33-66", "range", "Minimum and maximum collage image size in percent, for example 33-66.", unit="%"),
    ParameterSpec("slideshow.collage_max_images", "Slide Show", "Maximum collage images", 9, "int", "Clear the collage after this many images.", 1, 100),

    ParameterSpec("timelapse.stage_duration_seconds", "Time-Lapse", "Stage duration", 30.0, "float", "Active arrow-motion duration for one stage.", 1.0, 3600.0, unit="s"),
    ParameterSpec("timelapse.media_min_fraction", "Time-Lapse", "Preferred media minimum", 0.5, "fraction", "Preferred minimum framed-media size; track-free space can allow larger media.", 0.01, 1.0, unit="%"),
    ParameterSpec("timelapse.overview_as_media", "Time-Lapse", "Overview inside track map", True, "bool", "In single-window mode, show the overview as a framed medium over the stage map. Disable this to show it full-screen before the stage."),
    ParameterSpec("timelapse.marker_style", "Time-Lapse", "Moving marker", "pilgrim", "choice", "Show a walking pilgrim or the traditional arrow at the current track position.", choices=_choice(("pilgrim", "Walking pilgrim"), ("arrow", "Arrow"))),

    ParameterSpec("trackmaps.ordering", "Track Maps", "Track ordering", "track_number", "choice", "Order maps by recording date or original track number.", choices=_choice(("date", "Date"), ("track_number", "Track number"))),
    ParameterSpec("trackmaps.variant", "Track Maps", "Map variant", "time_lapse", "choice", "Variant preferred by Create, Update, and View.", choices=_choice(("standard", "Standard"), ("time_lapse", "Time-Lapse"))),
    ParameterSpec("trackmaps.image_size", "Track Maps", "Image size", "1920x1080", "image_size", "Output map dimensions in pixels."),
    ParameterSpec("trackmaps.zoom", "Track Maps", "Map zoom", 15, "int", "Requested basemap zoom level.", 0, 22),
    ParameterSpec("trackmaps.route_width", "Track Maps", "Route width", 4.0, "float", "Width of the plotted route line.", 0.1, 50.0),
    ParameterSpec("trackmaps.route_color", "Track Maps", "Route color", "#0000FF", "color", "Color of the plotted route."),
    ParameterSpec("trackmaps.endpoint_color", "Track Maps", "Endpoint color", "#FFFFFF", "color", "Color of start/end point dots."),
    ParameterSpec("trackmaps.endpoint_size", "Track Maps", "Endpoint size", 0.0, "float", "Size of start/end dots; zero hides them.", 0.0, 500.0),
    ParameterSpec("trackmaps.background_color", "Track Maps", "Background color", "#000000", "color", "Background outside the map axes."),
    ParameterSpec("trackmaps.title_color", "Track Maps", "Title color", "#FFFFFF", "color", "Track map title and subtitle color."),
    ParameterSpec("trackmaps.font_factor", "Track Maps", "Font factor", 2.2, "float", "Multiplier for automatically selected map label sizes.", 0.1, 10.0),
    ParameterSpec("trackmaps.overview_labels", "Track Maps", "Overview labels", "none", "choice", "Labels printed on the overview map.", choices=_choice(("none", "None"), ("default", "Track, date, length"))),
    ParameterSpec("trackmaps.remove_name_prefix", "Track Maps", "Remove name prefix", "", "text", "Remove this prefix from track names when displaying them."),
    ParameterSpec("trackmaps.edge_margin_fraction", "Track Maps", "Time-Lapse edge margin", 0.05, "fraction", "Minimum route distance from the plotting-area edge.", 0.0, 0.49, advanced=True, unit="%"),

    ParameterSpec("gpx.fallback_walking_speed_kmh", "GPX Processing", "Fallback walking speed", 3.5, "float", "Speed used when missing timestamps cannot be anchored.", 0.1, 50.0, unit="km/h"),
    ParameterSpec("gpx.minimum_point_spacing_m", "GPX Processing", "Minimum point spacing", 10.0, "float", "Discard filtered points closer than this distance.", 0.0, 10000.0, unit="m"),
    ParameterSpec("gpx.maximum_accuracy_m", "GPX Processing", "Maximum GPS inaccuracy", 10.0, "float", "Discard points whose reported uncertainty exceeds this value.", 0.1, 10000.0, unit="m"),
    ParameterSpec("gpx.editor_autosave_seconds", "GPX Processing", "Editor autosave interval", 300.0, "float", "Interval between GPX Editor recovery saves.", 10.0, 86400.0, unit="s"),
    ParameterSpec("gpx.map_padding_fraction", "GPX Processing", "Map padding", 0.08, "fraction", "Padding around tracks in interactive maps.", 0.0, 1.0, unit="%"),
    ParameterSpec("gpx.overview_zoom", "GPX Processing", "Overview zoom", 8, "int", "Default GPX Editor overview tile zoom.", 0, 22),
    ParameterSpec("gpx.track_zoom", "GPX Processing", "Track zoom", 14, "int", "Default GPX Editor track tile zoom.", 0, 22),
    ParameterSpec("gpx.elevation_headroom_fraction", "GPX Processing", "Elevation headroom", 0.08, "fraction", "Vertical headroom above and below elevation profiles.", 0.0, 1.0, unit="%"),
    ParameterSpec("gpx.maximum_map_tiles", "GPX Processing", "Maximum interactive tiles", 48, "int", "Lower zoom automatically when an interactive map would exceed this tile count.", 1, 1000, advanced=True),

    ParameterSpec("pdf.document_dpi", "PDF Export", "Document DPI", 200, "int", "Rendering resolution for PDF pages.", 72, 1200),
    ParameterSpec("pdf.map_dpi", "PDF Export", "Embedded map DPI", 600, "int", "Pixel density used for embedded PDF maps.", 72, 2400),
    ParameterSpec("pdf.overview_zoom", "PDF Export", "Overview zoom", 8, "int", "Tile zoom for PDF overview maps.", 0, 22),
    ParameterSpec("pdf.track_zoom", "PDF Export", "Track zoom", 14, "int", "Tile zoom for PDF track maps.", 0, 22),
    ParameterSpec("pdf.maximum_map_tiles", "PDF Export", "Maximum PDF map tiles", 24, "int", "Maximum tile count for one embedded PDF map.", 1, 1000, advanced=True),

    ParameterSpec("locations.reuse_radius_m", "Locations", "Place-name search radius", 150.0, "float", "GPS positions within this radius reuse the same place name. This reduces map lookups and speeds up Add Place Names.", 0.0, 100000.0, unit="m"),
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
    values = raw.get("values", {}) if isinstance(raw, dict) and isinstance(raw.get("values"), dict) else raw
    values = values if isinstance(values, dict) else {}
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
    return frozenset(
        {
            *(spec.key for spec in PARAMETER_SPECS if spec.section == "Track Maps" and spec.key != "trackmaps.variant"),
            "gpx.fallback_walking_speed_kmh",
            "gpx.minimum_point_spacing_m",
            "gpx.maximum_accuracy_m",
            "maps.provider",
            "maps.custom_url",
            "maps.custom_attribution",
            "maps.maximum_zoom",
        }
    )


def parameter_subset(values: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: values[key] for key in keys if key in values}
