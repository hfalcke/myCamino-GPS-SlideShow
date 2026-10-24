"""Shared renderer-scale label density checks for PNG and PDF overviews.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import numpy as np
from matplotlib.font_manager import FontProperties


def measurement_renderer(figure):
    if not hasattr(figure.canvas, "get_renderer"):
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        FigureCanvasAgg(figure)
    return figure.canvas.get_renderer()


def automatic_labels_visible(ax, labels, segments, font_size, fraction=.7):
    """Compare padded text widths and clipped route length in display pixels."""
    renderer = measurement_renderer(ax.figure)
    font = FontProperties(size=font_size)
    padding = 8 * ax.figure.dpi / 72
    label_width = sum(max((renderer.get_text_width_height_descent(line, font, False)[0]
                           for line in label.splitlines()), default=0) + padding for label in labels)
    route_length = 0.0
    box = ax.get_window_extent()
    for segment in segments:
        if len(segment) < 2:
            continue
        points = ax.transData.transform(np.asarray(segment, dtype=float))
        start, delta = points[:-1], np.diff(points, axis=0)
        low, high = np.zeros(len(delta)), np.ones(len(delta))
        valid = np.isfinite(start).all(axis=1) & np.isfinite(delta).all(axis=1)
        for dimension, minimum, maximum in ((0, box.x0, box.x1), (1, box.y0, box.y1)):
            step, origin = delta[:, dimension], start[:, dimension]
            nonzero = step != 0
            valid &= nonzero | ((origin >= minimum) & (origin <= maximum))
            a = np.divide(minimum - origin, step, out=np.full(len(delta), -np.inf), where=nonzero)
            b = np.divide(maximum - origin, step, out=np.full(len(delta), np.inf), where=nonzero)
            low = np.maximum(low, np.minimum(a, b))
            high = np.minimum(high, np.maximum(a, b))
        valid &= high > low
        route_length += float(np.sum(np.linalg.norm(delta[valid], axis=1) * (high[valid] - low[valid])))
    return bool(labels) and route_length > 0 and label_width <= fraction * route_length
