"""Printable track reports, independent of Cocoa tables and window state.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import math
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from collections import Counter
from track_types import stage_number, track_number, element_track_type
from datetime import datetime
from pathlib import Path
from typing import Callable

from plot_rendering import new_figure, serialized_plotting
from map_overlay import map_uses_dynamic_overlays


PDF_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mycamino-pdf")
PDF_COLUMNS = (
    ("row", "Row", ""), ("nr", "Track Number", ""), ("stage", "Stage", ""),
    ("track_id", "Track ID", ""), ("show", "Show", ""),
    ("name", "Name", ""), ("date", "Date & Time", ""),
    ("length", "Length", "km"), ("duration", "Duration", ""),
    ("sum", "Sum", "km"), ("distance", "Distance", "km"),
    ("speed", "avg", "km/h"), ("moving_speed", "mov", "km/h"),
    ("ascent", "Ascent", "m"), ("descent", "Descent", "m"),
    ("npoints", "NPoints", ""),
)
DEFAULT_PDF_COLUMNS = tuple(key for key, _, _ in PDF_COLUMNS if key not in {"row", "npoints", "show", "nr", "track_id"})


class PDFExportCancelled(Exception):
    """Cancellation leaves the previously exported file untouched."""


@dataclass(frozen=True)
class SummaryTrack:
    row: int
    nr: int
    name: str
    hidden: bool
    date: datetime | None
    length: float | None
    duration: float | None
    distance: float | None
    speed: float | None
    moving_speed: float | None
    ascent: float | None
    descent: float | None
    npoints: int | None
    stage: int | None = None
    track_id: str = ""
    track_type: str = "unassigned"
    number_assigned: bool = True
    stage_assigned: bool = True
    display_number: int | None = None

    @property
    def report_number(self):
        return (self.display_number if self.display_number is not None else self.nr) if self.number_assigned else None

    @property
    def report_stage(self):
        return (self.stage if self.stage is not None else self.nr) if self.stage_assigned else None


@dataclass(frozen=True)
class SummaryOptions:
    columns: tuple[str, ...] = DEFAULT_PDF_COLUMNS
    scope: str = "visible"
    orientation: str = "portrait"
    include_overview: bool = False
    include_track_maps: bool = False
    elevation_profile: bool = True
    rotate_maps: bool = False
    name_prefix: str = ""


@dataclass(frozen=True)
class SummarySnapshot:
    title: str
    tracks: tuple[SummaryTrack, ...]
    options: SummaryOptions

    def __post_init__(self):
        counts = Counter(track.report_stage for track in self.tracks if track.report_stage is not None)
        columns = tuple(key for key in self.options.columns if key != "track_id")
        if "stage" in columns and any(count > 1 for count in counts.values()):
            index = columns.index("stage") + 1
            columns = columns[:index] + ("track_id",) + columns[index:]
        object.__setattr__(self, "options", replace(self.options, columns=columns))


@dataclass(frozen=True)
class SummaryRow:
    kind: str
    cells: tuple[str, ...]
    hidden: bool = False


def select_summary_tracks(tracks, scope="visible", selected=()):
    selected = set(selected)
    if scope not in {"visible", "all", "selected"}:
        raise ValueError("Unknown PDF track selection")
    return tuple(track for track in tracks if scope == "all" or (
        track.nr in selected if scope == "selected" else not track.hidden
    ))


def report_name(name: str, prefix: str = "") -> str:
    result = name.lstrip()
    if prefix and result.startswith(prefix):
        result = result[len(prefix):].lstrip()
    return result


def summary_track(track, row: int, ids=None) -> SummaryTrack:
    metrics = track.metrics
    if not metrics or track.metrics_dirty:
        raise ValueError("Track data is still loading")
    duration = metrics.get("duration")
    number = track_number(track.element, track.nr)
    stage = stage_number(track.element, track.nr)
    return SummaryTrack(
        row, track.nr, track.name, track.hidden, metrics.get("time"),
        metrics.get("length_km"), duration.total_seconds() if duration is not None else None,
        metrics.get("distance_km"), metrics.get("speed_kmh"), metrics.get("moving_speed_kmh"),
        metrics.get("ascent_m"), metrics.get("descent_m"), metrics.get("npoints"),
        stage, (ids or {}).get(track.nr, str(stage) if stage is not None else ""), element_track_type(track.element),
        number is not None, stage is not None, number,
    )


def format_report_duration(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h {minutes:02d}m"


def compact_number(value: float | None, *, integer=False) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:.0f}" if integer or abs(value) >= 100 else f"{value:.1f}"


def summary_rows(snapshot: SummarySnapshot) -> tuple[SummaryRow, ...]:
    columns = snapshot.options.columns
    visible = [track for track in snapshot.tracks if not track.hidden]
    totals, averages = {}, {}
    for key in ("length", "duration", "npoints", "ascent", "descent", "speed", "moving_speed"):
        values = [getattr(track, key) for track in visible if getattr(track, key) is not None]
        averages[key] = sum(values) / len(values) if values else None
        if key not in {"speed", "moving_speed"}:
            totals[key] = sum(values) if values else None
    totals["sum"] = totals.get("length")
    integer_elevation = {
        key: any(value is not None and abs(value) >= 100 for value in (
            *[getattr(track, key) for track in snapshot.tracks], totals.get(key), averages.get(key)
        )) for key in ("ascent", "descent")
    }

    def formatted(key, value):
        if key == "duration":
            return format_report_duration(value)
        if key in {"speed", "moving_speed"}:
            return "N/A" if value is None else f"{value:.1f}"
        if key == "npoints":
            return "N/A" if value is None else (f"{value:.0f}" if value == int(value) else f"{value:.1f}")
        return compact_number(value, integer=integer_elevation.get(key, False))

    rows = []
    stage_counts = Counter(track.report_stage for track in snapshot.tracks if track.report_stage is not None)
    cumulative = 0.0
    for track in snapshot.tracks:
        if not track.hidden:
            cumulative += track.length or 0.0
        cells = []
        for key in columns:
            if key == "name":
                value = report_name(track.name, snapshot.options.name_prefix)
            elif key == "date":
                value = track.date.astimezone().strftime("%d.%m.%Y %H:%M") if track.date else "N/A"
            elif key == "show":
                value = "no" if track.hidden else "yes"
            elif key == "stage":
                value = str(track.report_stage) if track.report_stage is not None else ""
            elif key == "track_id":
                value = track.track_id if track.report_stage is not None and stage_counts[track.report_stage] > 1 else ""
            elif key in {"nr", "row"}:
                value = str(track.row) if key == "row" else (str(track.report_number) if track.report_number is not None else "")
            else:
                value = formatted(key, cumulative if key == "sum" else getattr(track, key))
            cells.append(value)
        rows.append(SummaryRow("track", tuple(cells), track.hidden))
    for kind, values in (("Total", totals), ("Average", averages)):
        rows.append(SummaryRow(kind, tuple(formatted(key, values[key]) if key in values else "" for key in columns)))
    return tuple(rows)


def check_cancelled(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise PDFExportCancelled()


def reusable_map_background(candidates, *, mode, extent, pixel_size, fingerprints, provider, minimum_zoom=None):
    """Reuse complete, attributed backgrounds without stretching or upsampling."""
    import numpy as np
    from PIL import Image

    if not fingerprints or any(not value for value in fingerprints):
        return None
    for path in candidates:
        path = Path(path)
        try:
            metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            if not map_uses_dynamic_overlays(metadata):
                continue
            if metadata.get("basemap") != provider or not metadata.get("map_attribution"):
                continue
            if metadata.get("missing_basemap_tiles", 0):
                continue
            if minimum_zoom is not None and int(metadata.get("effective_zoom", -1)) < minimum_zoom:
                continue
            actual = (metadata.get("source_track_geometry_fingerprints") if mode == "overview"
                      else [metadata.get("track_geometry_fingerprint")])
            if actual != list(fingerprints):
                continue
            stored = metadata["extent_mercator"]
            # Cropping to a different extent could discard embedded attribution.
            if any(not math.isclose(float(stored[key]), float(value), rel_tol=1e-9, abs_tol=.01)
                   for key, value in extent.items()):
                continue
            box = metadata["axes_box_fraction"]
            left, bottom, width, height = (float(box[key]) for key in ("left", "bottom", "width", "height"))
            if not (0 <= left < 1 and 0 <= bottom < 1 and width > 0 and height > 0
                    and left + width <= 1.000001 and bottom + height <= 1.000001):
                continue
            with Image.open(path) as image:
                if width * image.width < pixel_size[0] or height * image.height < pixel_size[1]:
                    continue
                span_x = float(stored["max_x"]) - float(stored["min_x"])
                span_y = float(stored["max_y"]) - float(stored["min_y"])
                if span_x <= 0 or span_y <= 0 or not math.isclose(
                    width * image.width / (height * image.height), span_x / span_y, rel_tol=.01
                ):
                    continue
                cropped = image.crop((round(left * image.width), round((1 - bottom - height) * image.height),
                                      round((left + width) * image.width), round((1 - bottom) * image.height)))
                return np.asarray(cropped.convert("RGB")).copy()
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def table_figures(snapshot: SummarySnapshot):
    """Measure real glyph widths; only the name column may be ellipsized."""
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Rectangle

    options = snapshot.options
    columns = options.columns
    rows = summary_rows(snapshot)
    metadata = {key: (title, unit) for key, title, unit in PDF_COLUMNS}
    width, height = (841.68, 595.44) if options.orientation == "landscape" else (595.44, 841.68)
    margin = 22.0
    gutter = 39.0
    available = width - 2 * margin - gutter
    renderer = RendererAgg(int(width), int(height), 72)

    def measure(text, size, bold=False):
        return renderer.get_text_width_height_descent(
            text, FontProperties(family="DejaVu Sans", size=size, weight="bold" if bold else "normal"), False
        )[0]

    font = 8.5
    while True:
        widths = []
        for index, key in enumerate(columns):
            title, unit = metadata[key]
            headers = [title, unit] + (["Speed"] if key in {"speed", "moving_speed"} else [])
            measured = max([measure(text, font, True) for text in headers] + [
                measure(row.cells[index], font, row.kind != "track") for row in rows
            ])
            widths.append(max(42, min(measured + 9, available * .31)) if key == "name" else measured + 9)
        if sum(widths) <= available or font <= 1.0:
            break
        font = round(font - .25, 2)
    if "name" in columns:
        widths[columns.index("name")] += max(0, available - sum(widths))
    table_width = sum(widths)
    row_height = max(14.0, font + 7)
    header_height = max(35.0, font * 3 + 9)
    top = height - 75
    per_page = max(3, int((top - header_height - 48) // row_height))
    pages = [rows[start:start + per_page] for start in range(0, len(rows), per_page)]
    if len(pages) > 1 and len(pages[-1]) < 2:
        pages[-1] = pages[-2][-1:] + pages[-1]
        pages[-2] = pages[-2][:-1]

    for page_number, page_rows in enumerate(pages, 1):
        fig = new_figure(figsize=(width / 72, height / 72), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1], xlim=(0, width), ylim=(0, height))
        ax.axis("off")
        title_size = min(15, 15 * (width - 2 * margin) / max(measure(snapshot.title, 15, True), 1))
        ax.text(margin, height - 23, snapshot.title, fontsize=title_size, weight="bold", va="top", color="#173c58")
        ax.text(margin, height - 45, "Track summary", fontsize=10, color="#52606d", va="top")
        ax.text(width - margin, height - 45, f"Page {page_number} of {len(pages)}", ha="right", va="top", fontsize=8, color="#52606d")
        x = margin + gutter
        for index, key in enumerate(columns):
            cell_width = widths[index]
            ax.add_patch(Rectangle((x, top - header_height), cell_width, header_height, facecolor="#1f4e79", edgecolor="white", linewidth=.4))
            title, unit = metadata[key]
            if key in {"speed", "moving_speed"}:
                ax.text(x + cell_width / 2, top - header_height * .5, title, ha="center", va="center", fontsize=font, color="white", weight="bold")
                ax.text(x + cell_width / 2, top - header_height * .8, unit, ha="center", va="center", fontsize=font, color="white")
            else:
                label = title + (f"\n{unit}" if unit else "")
                ax.text(x + cell_width / 2, top - header_height / 2, label, ha="center", va="center", fontsize=font, color="white", weight="bold", linespacing=1.4)
            x += cell_width
        speed_indexes = [i for i, key in enumerate(columns) if key in {"speed", "moving_speed"}]
        # Only merge contiguous speed columns; unusual programmatic orders still fit.
        groups = []
        for index in speed_indexes:
            if groups and index == groups[-1][-1] + 1:
                groups[-1].append(index)
            else:
                groups.append([index])
        for group in groups:
            x = margin + gutter + sum(widths[:group[0]])
            span = sum(widths[i] for i in group)
            ax.add_patch(Rectangle((x, top - header_height * .35), span, header_height * .35, facecolor="#1f4e79", edgecolor="none"))
            ax.text(x + span / 2, top - header_height * .18, "Speed", ha="center", va="center", fontsize=font, weight="bold", color="white")
        for row_index, row in enumerate(page_rows):
            y = top - header_height - (row_index + 1) * row_height
            summary = row.kind != "track"
            color = "#e8f1f8" if summary else ("#f4f7fa" if row_index % 2 else "white")
            ax.add_patch(Rectangle((margin + gutter, y), table_width, row_height, facecolor=color, edgecolor="#c9d1d9", linewidth=.35))
            if summary:
                ax.text(margin + gutter - 4, y + row_height / 2, row.kind, ha="right", va="center", fontsize=min(font, 8), weight="bold", color="#173c58")
            x = margin + gutter
            for index, key in enumerate(columns):
                value = row.cells[index]
                if key == "name" and measure(value, font, summary) > widths[index] - 9:
                    while value and measure(value + "...", font, summary) > widths[index] - 9:
                        value = value[:-1]
                    value += "..."
                numeric = key not in {"name", "date", "show"}
                ax.text(x + widths[index] - 4 if numeric else x + 4, y + row_height / 2, value,
                        ha="right" if numeric else "left", va="center", fontsize=font,
                        color="#727b85" if row.hidden else "#202c36", weight="bold" if summary else "normal")
                x += widths[index]
        if any(track.hidden for track in snapshot.tracks):
            ax.text(margin, 23, "Hidden tracks: gray rows and dashed gray routes; excluded from totals, averages and cumulative distance.", fontsize=6.5, color="#52606d")
        yield fig


@serialized_plotting
def export_summary_pdf(path: Path, snapshot: SummarySnapshot, *, map_pages=None,
                       cancel=None, progress: Callable[[str], None] = lambda _message: None):
    from matplotlib.backends.backend_pdf import PdfPages

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".pdf", dir=path.parent)
    os.close(descriptor)
    try:
        check_cancelled(cancel)
        with PdfPages(temporary, metadata={"Title": snapshot.title, "Subject": "myCamino track summary"}) as pdf:
            for index, figure in enumerate(table_figures(snapshot), 1):
                check_cancelled(cancel)
                progress(f"Writing summary table page {index}...")
                pdf.savefig(figure)
                figure.clear()
            if map_pages is not None:
                map_pages(pdf)
        check_cancelled(cancel)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
