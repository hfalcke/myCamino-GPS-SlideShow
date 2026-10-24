"""Shared offline WMO condition categories and slideshow symbols.

SPDX-License-Identifier: GPL-3.0-or-later
"""


def weather_condition_kind(weather_code):
    try:
        code = int(float(weather_code))
    except (TypeError, ValueError, OverflowError):
        return "cloud"
    if code == 0:
        return "sun"
    if code in {1, 2, 3, 45, 48}:
        return "cloud"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "storm"
    return "rain"


def weather_condition_symbol(weather_code):
    return {"sun": "\u2600\ufe0e", "cloud": "\u2601\ufe0e", "snow": "\u2744\ufe0e",
            "storm": "\u26a1\ufe0e", "rain": "\u2614\ufe0e"}[weather_condition_kind(weather_code)]


def draw_weather_symbol(ax, x, y, size, code, color="#174f8a"):
    """Draw font-independent vector equivalents of the shared slideshow symbols."""
    import math
    from matplotlib.patches import Circle, Polygon

    kind = weather_condition_kind(code)
    if kind == "sun":
        ax.add_patch(Circle((x, y), size * .22, fill=False, edgecolor=color, linewidth=1))
        for index in range(8):
            angle = index * math.pi / 4
            ax.plot([x + math.cos(angle) * size * .32, x + math.cos(angle) * size * .46],
                    [y + math.sin(angle) * size * .32, y + math.sin(angle) * size * .46], color=color, linewidth=.8)
    elif kind == "snow":
        for index in range(3):
            angle = index * math.pi / 3
            ax.plot([x - math.cos(angle) * size * .42, x + math.cos(angle) * size * .42],
                    [y - math.sin(angle) * size * .42, y + math.sin(angle) * size * .42], color=color, linewidth=1)
    elif kind == "storm":
        ax.add_patch(Polygon([(x + size * .1, y - size * .45), (x - size * .28, y + size * .05),
                             (x, y + size * .05), (x - size * .1, y + size * .45),
                             (x + size * .3, y - size * .1), (x + size * .05, y - size * .1)], color=color))
    else:
        for dx, dy, radius in ((-.23, .02, .19), (0, -.10, .25), (.24, .02, .19)):
            ax.add_patch(Circle((x + dx * size, y + dy * size), radius * size, facecolor=color, edgecolor="none"))
        if kind == "rain":
            for dx in (-.22, 0, .22):
                ax.plot([x + dx * size, x + (dx - .08) * size], [y + .28 * size, y + .44 * size], color=color, linewidth=1)
