"""Tour Overview highlighting remains distinct from stage transportation colors."""

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GPSTrackShow as show


class SlideshowOverviewHighlightTests(unittest.TestCase):
    def render(self, metadata):
        image = Mock()
        config = SimpleNamespace(debug=False, dot_color=show.COLOR_NAMES["white"], dot_size=4,
                                 track_type_colors=(), track_title_mode="track_name", track_name_prefix="")
        strokes, active_color = [], [None]

        def color(value):
            result = Mock()
            result.setStroke.side_effect = lambda: active_color.__setitem__(0, value)
            return result

        def path():
            result = Mock()
            points, dash = [], [None]
            result.moveToPoint_.side_effect = points.append
            result.lineToPoint_.side_effect = points.append
            result.setLineDash_count_phase_.side_effect = lambda values, _count, _phase: dash.__setitem__(0, values)
            result.stroke.side_effect = lambda: strokes.append((active_color[0], tuple(points), dash[0]))
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(show, "copy_image", return_value=image))
            stack.enter_context(patch.object(show, "image_size_tuple", return_value=(1000, 500)))
            stack.enter_context(patch.object(show, "coordinate_to_pixel", side_effect=lambda lat, lon, _data: (lon, lat)))
            stack.enter_context(patch.object(show, "scale_metadata_pixel_to_image", side_effect=lambda x, y, *_args: (x, y)))
            stack.enter_context(patch.object(show, "NSBezierPath", SimpleNamespace(bezierPath=path)))
            stack.enter_context(patch.object(show, "ns_color", side_effect=color))
            stack.enter_context(patch.object(show, "draw_marker_at"))
            arrow = stack.enter_context(patch.object(show, "draw_arrow_line"))
            stack.enter_context(patch.object(show, "type_route_color", side_effect=AssertionError("Overview highlights are not type-colored")))
            show.draw_overview_overlay(image, {}, metadata, None, config, show_caption=False)
        image.lockFocus.assert_called_once()
        image.unlockFocus.assert_called_once()
        return strokes, arrow

    def test_all_current_block_routes_are_red_and_direction_avoids_gray_members(self):
        metadata = {
            "stage_plan_version": 1, "stage_kind": "gpx_track",
            "overlay_geometry": {"segments": [
                [[20, 0], [20, 80]], [[80, 100], [80, 500]],
                [[40, 10], [40, 30]], [[200, 600], [200, 990]],
            ]},
            "overlay_track_types": ["pilgrimage", "bike", "bus", "train"],
            # The second member belongs to a later block; the fourth is hidden.
            "overlay_hidden_tracks": [False, True, False, True],
        }
        strokes, arrow = self.render(metadata)
        red = [points for color, points, _dash in strokes if color == show.COLOR_NAMES["red"]]
        gray = [(points, dash) for color, points, dash in strokes if color == (.67, .67, .67, 1.)]
        self.assertEqual(red, [((0, 480), (80, 480)), ((10, 460), (30, 460))])
        self.assertEqual(len(gray), 2)
        self.assertTrue(all(dash == [5., 4.] for _points, dash in gray))
        # Later inactive members must not obscure overlapping active routes.
        colors = [color for color, _points, _dash in strokes if color != show.COLOR_NAMES["black"]]
        self.assertEqual(colors[-2:], [show.COLOR_NAMES["red"]] * 2)
        arrow.assert_called_once()
        start_x, start_y, end_x, end_y = arrow.call_args.args[:4]
        self.assertAlmostEqual((start_x + end_x) / 2, 60)
        self.assertEqual((start_y, end_y), (20, 20))

    def test_legacy_visible_track_remains_red(self):
        strokes, arrow = self.render({"track_segments": [[[20, 0], [20, 80]]]})
        self.assertEqual(strokes[-1][0], show.COLOR_NAMES["red"])
        arrow.assert_called_once()

    def test_hidden_track_has_no_red_route_or_direction_cue(self):
        strokes, arrow = self.render({"hidden": True, "track_segments": [[[20, 0], [20, 80]]]})
        self.assertNotIn(show.COLOR_NAMES["red"], [color for color, _points, _dash in strokes])
        arrow.assert_not_called()
