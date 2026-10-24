"""Measured, printable information panels and checkpoint overlays.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

from gpx_pdf_details import checkpoint_time, compact_place_name, compact_weather_text, coordinate_place
from gpx_pdf_summary import compact_number, format_report_duration
from plot_rendering import new_figure
from plot_metadata_utils import lonlat_to_web_mercator
from map_label_layout import measurement_renderer
from weather_symbols import draw_weather_symbol


BLUE = "#174f8a"
AMBER = "#ad6500"


def _column_heading(column):
    return {"Track km": "Track\nkm", "Avg km/h": "Avg\nkm/h",
            "Mov km/h": "Mov\nkm/h", "Height m": "Height\nm"}.get(column, column)


def track_statistics(track, journey_distance=None, previous_gap=None):
    metrics = track.metrics
    items = []
    specs = (("length_km", "Length", "km"), ("speed_kmh", "Average speed", "km/h"),
             ("moving_speed_kmh", "Moving speed", "km/h"), ("ascent_m", "Ascent", "m"),
             ("descent_m", "Descent", "m"),
             ("distance_km", "Distance to anchor", "km"))
    duration = metrics.get("duration")
    if duration is not None:
        items.append(("Duration", format_report_duration(duration.total_seconds())))
    for key, label, unit in specs:
        value = metrics.get(key)
        if value is not None:
            number = compact_number(value, integer=key == "npoints")
            items.append((label, f"{number} {unit}".strip()))
    if journey_distance is not None:
        items.append(("Journey distance", f"{compact_number(journey_distance)} km"))
    order = ("Length", "Duration", "Ascent", "Descent", "Average speed", "Moving speed", "Journey distance", "Distance to anchor")
    values = dict(items)
    return tuple((label, values[label]) for label in order if label in values)


def _wrap(text, width, renderer, font):
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            trial = (line + " " + word).strip()
            if line and renderer.get_text_width_height_descent(trial, font, False)[0] > width:
                lines.append(line)
                line = ""
            # Split long uninterrupted names rather than allowing overflow.
            while renderer.get_text_width_height_descent(word, font, False)[0] > width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and renderer.get_text_width_height_descent(word[:cut], font, False)[0] > width:
                    cut -= 1
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:cut])
                word = word[cut:]
            line = (line + " " + word).strip()
        lines.append(line)
    return "\n".join(lines)


def report_title(figure, title, width_inches, font_size=15):
    renderer = measurement_renderer(figure)
    font = FontProperties(size=font_size, weight="bold")
    width = width_inches * figure.dpi
    lines = _wrap(title, width, renderer, font).splitlines()
    if len(lines) > 2:
        last = lines[1]
        while last and renderer.get_text_width_height_descent(last + "...", font, False)[0] > width:
            last = last[:-1]
        lines = [lines[0], last + "..."]
    return "\n".join(lines)


@dataclass(frozen=True)
class PanelLayout:
    statistics: tuple
    rows: tuple
    columns: tuple
    widths: tuple
    row_heights: tuple
    summary_height: float
    height: float
    font_size: float = 9.0
    header_height: float = 25.0


def measure_panel(figure, width_inches, statistics, checkpoints, parameters):
    renderer = measurement_renderer(figure)
    width_points = width_inches * 72
    summary_height = 6 + 25 * ((len(statistics) + 3) // 4) if statistics else 0
    columns = ["Time", "Track km", "Avg km/h", "Mov km/h", "Height m"]
    show_place = parameters.get("pdf.checkpoint_places", True) and any(marker.place for marker in checkpoints)
    show_weather = parameters.get("pdf.checkpoint_weather", True) and any(marker.weather for marker in checkpoints)
    if show_place:
        columns.append("Place")
    if show_weather:
        columns.extend(("Weather", ""))
    origin = next((marker.timestamp for marker in checkpoints if marker.timestamp), None)
    values_by_row = []
    for marker in checkpoints:
        time = checkpoint_time(marker, origin)
        if marker.break_end:
            end = checkpoint_time(replace(marker, timestamp=marker.break_end), origin)
            time += f"-{end}"
        values = [time, compact_number(marker.distance),
                  f"{marker.average_speed:.1f}" if marker.average_speed is not None else "",
                  f"{marker.moving_speed:.1f}" if marker.moving_speed is not None else "",
                  f"{marker.elevation:.0f}" if marker.elevation is not None else ""]
        if show_place:
            values.append(marker.place if marker.place == coordinate_place(marker)
                          else compact_place_name({"place": marker.place}))
        if show_weather:
            values.extend((compact_weather_text(marker.weather), ""))
        values_by_row.append(values)
    for font_size in (9.0, 8.5, 8.0, 7.5):
        font = FontProperties(size=font_size)
        header_font = FontProperties(size=min(8, font_size), weight="bold")
        widths = [max([*(renderer.get_text_width_height_descent(line, header_font, False)[0]
                        for line in _column_heading(label).splitlines() or [""]),
                      *(renderer.get_text_width_height_descent(row[index], font, False)[0]
                        for row in values_by_row)]) * 72 / figure.dpi + 7
                  for index, label in enumerate(columns)]
        if show_weather:
            widths[-1] = 16
        if sum(widths) <= width_points:
            break
    if sum(widths) > width_points:
        flexible = [index for index, column in enumerate(columns) if column in {"Place", "Weather"}]
        fixed_width = sum(width for index, width in enumerate(widths) if index not in flexible)
        available = width_points - fixed_width
        natural = sum(widths[index] for index in flexible)
        for index in flexible:
            widths[index] = max(10, widths[index] * available / natural)
    else:
        # Use any spare width for the locality, not extra gaps in numeric cells.
        index = columns.index("Place") if show_place else columns.index("Weather") if show_weather else 0
        widths[index] += width_points - sum(widths)
    rows, heights = [], []
    for marker, values in zip(checkpoints, values_by_row):
        if show_place and marker.place_detail:
            index = columns.index("Place")
            detailed = values[index] + " · " + marker.place_detail
            available_pixels = (widths[index] - 6) * figure.dpi / 72
            if renderer.get_text_width_height_descent(detailed, font, False)[0] <= available_pixels:
                values[index] = detailed
        wrapped = tuple(_wrap(value, max(8, width - 6) * figure.dpi / 72, renderer, font)
                        for value, width in zip(values, widths))
        rows.append((marker, wrapped))
        heights.append(max(value.count("\n") + 1 for value in wrapped) * (font_size * 1.18) + 2)
    table_height = 25 + sum(heights) + (13 if show_weather else 2) if rows else 0
    return PanelLayout(tuple(statistics), tuple(rows), tuple(columns), tuple(widths), tuple(heights),
                       summary_height, summary_height + table_height, font_size)


def draw_panel(figure, rect, layout, row_start=0, row_end=None, show_statistics=True):
    """Draw in physical points; return the first row that did not fit."""
    ax = figure.add_axes(rect)
    width, height = rect[2] * figure.get_figwidth() * 72, rect[3] * figure.get_figheight() * 72
    ax.set(xlim=(0, width), ylim=(height, 0))
    ax.axis("off")
    y = 0
    if show_statistics and layout.statistics:
        ax.add_patch(Rectangle((0, 0), width, layout.summary_height, facecolor="#edf3f8", edgecolor="none"))
        for index, (label, value) in enumerate(layout.statistics):
            x, top = 7 + index % 4 * width / 4, 4 + index // 4 * 25
            ax.text(x, top, label.upper(), fontsize=7.2, color="#52667a", va="top")
            ax.text(x, top + 9, value, fontsize=10.5, fontweight="bold", color="#173b5e", va="top")
        y += layout.summary_height
    if not layout.rows or row_start >= len(layout.rows):
        return row_start
    footer = 13 if "Weather" in layout.columns else 2
    if y + layout.header_height + layout.row_heights[row_start] + footer > height:
        return row_start  # Do not leave an empty checkpoint header on a map page.
    ax.add_patch(Rectangle((0, y), width, layout.header_height, facecolor="#e5edf5", edgecolor="none"))
    x = 0
    for label, cell_width in zip(layout.columns, layout.widths):
        ax.text(x + 3, y + 3, _column_heading(label), fontsize=min(8, layout.font_size), fontweight="bold", color=BLUE, va="top", linespacing=1.05)
        x += cell_width
    y += layout.header_height
    stop = len(layout.rows) if row_end is None else min(row_end, len(layout.rows))
    index = row_start
    while index < stop:
        row_height = layout.row_heights[index]
        if y + row_height + footer > height + .01:
            break
        marker, values = layout.rows[index]
        if index % 2 == 0:
            ax.add_patch(Rectangle((0, y), width, row_height, facecolor="#f5f8fb", edgecolor="none"))
        x = 0
        for value, cell_width in zip(values[:len(layout.columns)], layout.widths):
            ax.text(x + 3, y + 1, value, fontsize=layout.font_size,
                    color=AMBER if marker.break_end else "#253b50", va="top", linespacing=1.25)
            x += cell_width
        if marker.weather and "Weather" in layout.columns:
            draw_weather_symbol(ax, width - layout.widths[-1] / 2, y + row_height / 2,
                                min(11, row_height - 1), marker.weather_code)
        y += row_height
        index += 1
    if "Weather" in layout.columns and any(marker.weather for marker, _ in layout.rows[row_start:index]):
        ax.text(0, y + 5, "Historical estimates: Open-Meteo.com · CC BY 4.0 (creativecommons.org/licenses/by/4.0)",
                fontsize=6.2, color="#52667a", va="top")
    return index


def draw_checkpoint_markers(map_ax, profile_ax, checkpoints, profile_enabled=True):
    origin = next((marker.timestamp for marker in checkpoints if marker.timestamp), None)
    renderer = measurement_renderer(map_ax.figure)
    font = FontProperties(size=7.5)
    occupied = []
    for marker in checkpoints:
        color = AMBER if marker.break_end else BLUE
        x, y = lonlat_to_web_mercator(marker.longitude, marker.latitude)
        targets = [(map_ax, x, y)]
        if profile_ax is not None and profile_enabled and marker.elevation is not None:
            targets.append((profile_ax, marker.distance, marker.elevation))
        for ax, px, py in targets:
            ax.scatter([px], [py], s=38, facecolors="white", edgecolors=color, linewidths=1.4, zorder=8)
            if marker.break_end:
                ax.scatter([px], [py], s=76, facecolors="none", edgecolors=color, linewidths=.9, zorder=8)
            label = checkpoint_time(marker, origin)
            if ax is map_ax:
                if marker.break_end:
                    end = checkpoint_time(replace(marker, timestamp=marker.break_end), origin)
                    label += f"-{end}"
            else:
                continue  # Profile clock labels are placed together inside its top edge.
            if not label:
                continue
            offset, align = (5, 7), "left"
            if ax is map_ax:
                box = ax.get_window_extent()
                anchor_x, anchor_y = ax.transData.transform((px, py))
                width = max(renderer.get_text_width_height_descent(line, font, False)[0] for line in label.splitlines()) + 8
                height = len(label.splitlines()) * 10 * ax.figure.dpi / 72 + 6
                candidates = []
                scale = ax.figure.dpi / 72
                for dx, dy, ha in ((6, 8, "left"), (-6, 8, "right"), (6, -20, "left"),
                                   (-6, -20, "right"), (6, 25, "left"), (-6, 25, "right")):
                    left = anchor_x + dx * scale - (width if ha == "right" else 0)
                    bottom = anchor_y + dy * scale
                    # Keep labels within the map, clear of its attribution strip.
                    left = max(box.x0 + 2, min(box.x1 - width - 2, left))
                    bottom = max(box.y0 + 18 * scale, min(box.y1 - height - 2, bottom))
                    bounds = (left, bottom, left + width, bottom + height)
                    overlap = sum(max(0, min(bounds[2], old[2]) - max(bounds[0], old[0])) *
                                  max(0, min(bounds[3], old[3]) - max(bounds[1], old[1])) for old in occupied)
                    candidates.append((overlap, bounds))
                _, bounds = min(candidates, key=lambda candidate: candidate[0])
                occupied.append(bounds)
                offset = ((bounds[0] - anchor_x) / scale, (bounds[1] - anchor_y) / scale)
            ax.annotate(label, (px, py), xytext=offset, textcoords="offset points", ha=align,
                        fontsize=7.5, color=color, zorder=9, clip_on=False,
                        bbox=dict(boxstyle="round,pad=.16", facecolor="white", edgecolor="none", alpha=.9))
    if profile_ax is not None and profile_enabled:
        timed = []
        for marker in checkpoints:
            if marker.timestamp is None:
                continue
            label = checkpoint_time(marker, origin)
            if marker.break_end:
                label += "-" + checkpoint_time(replace(marker, timestamp=marker.break_end), origin)
            if timed and abs(timed[-1][0] - marker.distance) < 1e-8:
                timed[-1] = (marker.distance, timed[-1][1] + "/" + label)
            else:
                timed.append((marker.distance, label))
        if timed:
            box = profile_ax.get_window_extent()
            scale = profile_ax.figure.dpi / 72
            lane_ends = []
            for distance, label in timed:
                width = renderer.get_text_width_height_descent(label, font, False)[0] + 4 * scale
                x = profile_ax.transData.transform((distance, 0))[0]
                left = max(box.x0 + 2 * scale, min(box.x1 - width - 2 * scale, x - width / 2))
                lane = next((index for index, end in enumerate(lane_ends) if left > end + 3 * scale), len(lane_ends))
                if lane == len(lane_ends):
                    lane_ends.append(0)
                lane_ends[lane] = left + width
                annotation = profile_ax.annotate(label, (distance, 1), xycoords=profile_ax.get_xaxis_transform(),
                    xytext=((left - x) / scale + 2, -4 - lane * 11), textcoords="offset points",
                    ha="left", va="top", fontsize=7.5, color=BLUE, zorder=10, clip_on=True,
                    bbox=dict(boxstyle="round,pad=.12", facecolor="white", edgecolor="none", alpha=.85))
                annotation.set_gid("checkpoint-time")


def continuation_figures(size, title, layout, first_row):
    # Keep the measured physical column widths, including the final icon cell.
    # A different continuation-page margin must not squeeze text into that cell.
    width = sum(layout.widths) / (72 * size[0])
    left = (1 - width) / 2
    while first_row < len(layout.rows):
        figure = new_figure(figsize=size)
        heading = report_title(figure, title + " - continued", size[0] * width, 14)
        figure.text(left, .955, heading, fontsize=14, fontweight="bold", color=BLUE, va="top")
        top = .955 - (18 * (heading.count('\n') + 1) + 20) / (size[1] * 72)
        end = draw_panel(figure, [left, .05, width, top - .05], layout, first_row, show_statistics=False)
        if end == first_row:
            raise ValueError("A checkpoint row is too large for a PDF page; shorten its place information.")
        yield figure
        first_row = end
