"""Small font-independent vector pictograms shared by PDF and Cocoa playback.

Coordinates occupy a unit square, with the origin at the lower left.
"""

from math import cos, sin, tau
from functools import lru_cache


def circle(x, y, radius):
    return tuple((x + radius * cos(tau * i / 24), y + radius * sin(tau * i / 24))
                 for i in range(25))


@lru_cache(maxsize=16)
def symbol_paths(kind):
    head = circle(.52, .82, .09)
    person = (head, ((.49, .72), (.45, .45), (.3, .15)),
              ((.45, .45), (.67, .13)), ((.48, .67), (.73, .5)))
    if kind in {"hiking", "pilgrimage", "pilgrim", "sightseeing"}:
        paths = list(person)
        if kind == "hiking":
            paths += [((.4, .69), (.25, .69), (.21, .47), (.4, .44), (.4, .69))]
        elif kind in {"pilgrimage", "pilgrim"}:
            paths += [((.33, .9), (.69, .9)), ((.4, .9), (.45, 1), (.58, 1), (.63, .9)),
                      ((.76, .75), (.76, .08))]
        else:
            paths += [((.6, .62), (.85, .62), (.85, .45), (.6, .45), (.6, .62)),
                      circle(.73, .535, .045)]
        return tuple(paths)
    if kind == "bike":
        return (circle(.21, .25, .18), circle(.8, .25, .18),
                ((.21, .25), (.42, .6), (.62, .25), (.21, .25), (.5, .25), (.7, .66), (.8, .25)),
                ((.32, .65), (.5, .65)), ((.7, .66), (.64, .82), (.8, .82)))
    if kind == "car":
        return (((.08, .2), (.08, .5), (.25, .58), (.36, .78), (.68, .78),
                 (.82, .56), (.94, .49), (.94, .2), (.08, .2)),
                ((.31, .56), (.76, .56)), circle(.27, .2, .12), circle(.76, .2, .12))
    if kind == "bus":
        return (((.06, .25), (.06, .79), (.85, .79), (.94, .64),
                 (.94, .25), (.06, .25)),
                ((.17, .49), (.17, .68), (.35, .68), (.35, .49), (.17, .49)),
                ((.43, .49), (.43, .68), (.61, .68), (.61, .49), (.43, .49)),
                ((.71, .26), (.71, .68), (.84, .68), (.87, .6), (.87, .26)),
                circle(.25, .23, .11), circle(.77, .23, .11))
    if kind == "train":
        return (((.2, .24), (.2, .78), (.3, .88), (.7, .88), (.8, .78),
                 (.8, .24), (.2, .24)),
                ((.29, .53), (.29, .73), (.71, .73), (.71, .53), (.29, .53)),
                ((.5, .53), (.5, .73)), circle(.32, .36, .045), circle(.68, .36, .045),
                ((.32, .24), (.16, .06)), ((.68, .24), (.84, .06)),
                ((.25, .16), (.75, .16)), ((.2, .1), (.8, .1)))
    if kind == "metro":
        return (((.18, .22), (.18, .84), (.26, .94), (.74, .94),
                 (.82, .84), (.82, .22), (.18, .22)),
                ((.36, .72), (.36, .86), (.5, .76), (.64, .86), (.64, .72)),
                ((.28, .44), (.28, .62), (.72, .62), (.72, .44), (.28, .44)),
                circle(.32, .32, .04), circle(.68, .32, .04),
                ((.32, .22), (.22, .06)), ((.68, .22), (.78, .06)),
                ((.26, .12), (.74, .12)))
    if kind in {"airplane", "plane"}:
        points = ((.5, .98), (.56, .64), (.97, .38), (.97, .3), (.57, .44),
                 (.56, .17), (.7, .06), (.7, .02), (.5, .08), (.3, .02),
                 (.3, .06), (.44, .17), (.43, .44), (.03, .3), (.03, .38),
                 (.44, .64), (.5, .98))
        return (tuple((y, 1 - x) for x, y in points),)
    if kind == "horse":
        return (circle(.5, .88, .07), ((.49, .8), (.45, .59), (.66, .59), (.62, .37)),
                ((.12, .56), (.67, .56), (.8, .78), (.92, .69), (.85, .48), (.7, .46),
                 (.66, .1)), ((.55, .44), (.48, .1)), ((.24, .44), (.18, .1)),
                ((.15, .55), (.06, .35)), ((.29, .44), (.34, .1)))
    if kind == "rest":
        return (((.08, .12), (.08, .77)), ((.09, .28), (.93, .28), (.93, .12)),
                ((.12, .45), (.12, .6), (.38, .6), (.38, .45)),
                ((.38, .45), (.38, .66), (.84, .66), (.93, .45), (.08, .45)))
    return (((.5, .98), (.88, .12), (.5, .32), (.12, .12), (.5, .98)),)


def draw_pdf_symbol(ax, kind, *, color="#2463B4"):
    for path in symbol_paths(kind):
        ax.plot([p[0] for p in path], [p[1] for p in path], color=color,
                linewidth=1.7, solid_capstyle="round", solid_joinstyle="round")
    ax.set(xlim=(-.08, 1.08), ylim=(-.08, 1.08), aspect="equal")
    ax.axis("off")


def draw_cocoa_symbol(kind, center, size, color):
    from AppKit import NSBezierPath

    color.setStroke()
    for points in symbol_paths(kind):
        path = NSBezierPath.bezierPath()
        for index, (x, y) in enumerate(points):
            point = (center[0] + (x - .5) * size, center[1] + (y - .5) * size)
            (path.moveToPoint_ if index == 0 else path.lineToPoint_)(point)
        path.setLineWidth_(max(1.3, size * .055))
        path.stroke()
