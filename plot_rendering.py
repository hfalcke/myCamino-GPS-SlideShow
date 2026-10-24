"""Headless, serialized plotting for Cocoa applications and command-line tools.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import os
import threading
from functools import wraps


# Set before importing Contextily/pyplot. Switching a running GUI backend from
# a worker can destroy figure windows, so renderers never switch backends.
os.environ["MPLBACKEND"] = "Agg"
PLOT_RENDER_LOCK = threading.RLock()


def serialized_plotting(function):
    @wraps(function)
    def render(*args, **kwargs):
        with PLOT_RENDER_LOCK:
            return function(*args, **kwargs)
    return render


def new_figure(*args, **kwargs):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(*args, **kwargs)
    FigureCanvasAgg(figure)
    return figure


def add_headless_basemap(contextily, ax, *, source, **kwargs):
    """Avoid Contextily's attribution helper, which calls global pyplot.draw()."""
    from matplotlib import patheffects

    attribution = kwargs.pop("attribution", None)
    font_size = kwargs.pop("attribution_size", 8)
    if attribution is None:
        attribution = source.get("attribution", "") if hasattr(source, "get") else ""
    contextily.add_basemap(ax, source=source, attribution=False, **kwargs)
    if attribution:
        ax.figure.canvas.draw()
        text = ax.text(
            0.005, 0.005, attribution, transform=ax.transAxes, size=font_size,
            path_effects=[patheffects.withStroke(linewidth=2, foreground="w")],
            wrap=True,
        )
        text._get_wrap_line_width = lambda: ax.get_window_extent().width * 0.99
