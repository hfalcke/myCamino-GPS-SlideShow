"""Directional pictograms share the pilgrim's stable per-track heading."""

from datetime import datetime, timedelta, timezone
import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GPSTrackShow as show


class DirectionalSlideshowMarkerTests(unittest.TestCase):
    def noisy_timeline(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return show.timeline_points_with_distances([
            {"lat": lat, "lon": lon, "time": start + timedelta(minutes=index), "segment_index": 0}
            for index, (lat, lon) in enumerate([
                (50, 6), (50.0001, 6.01), (49.9999, 6.0099), (50.0001, 6.02),
                (49.9999, 6.0199), (50, 6.03), (50, 6.03)])])

    def marker_view(self, points):
        view = SimpleNamespace(
            imagePixelForLat_lon_=lambda lat, lon: (lon * 100., -lat * 100.),
            map_image=object(), map_metadata={}, route_points=points,
            _scale_components=lambda _scale: (1., 1.),
            bounds=lambda: SimpleNamespace(size=SimpleNamespace(width=1600., height=1000.)),
            pilgrim_frames=[], marker_radius=5., arrow_factor=1., marker_color=(1., 0., 0., 1.))
        view.stage_tangent = show.TimeLapseMapView.fixedStageTangent.callable(view)
        return view

    def test_symbols_native_forward_axis_aligns_with_reference_direction(self):
        for kind in show.DIRECTIONAL_VECTOR_MARKERS:
            for tangent in ((1, 0), (-1, 0), (0, -1), (0, 1), (.6, -.8), (-.6, .8)):
                with self.subTest(kind=kind, tangent=tangent):
                    rotation, mirrored = show.transport_marker_orientation(kind, tangent)
                    forward = (0, -1) if kind in {"train", "metro"} else (1, 0)
                    x, y = forward
                    if mirrored:
                        x = -x
                    angle = math.radians(rotation)
                    actual = (x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle))
                    self.assertAlmostEqual(actual[0], tangent[0])
                    self.assertAlmostEqual(actual[1], -tangent[1])

    def test_all_directional_symbols_keep_heading_through_noise_stops_and_backward_navigation(self):
        points = self.noisy_timeline()
        view = self.marker_view(points)
        rect = SimpleNamespace(origin=SimpleNamespace(x=0., y=0.))
        fractions = [index / 30. for index in range(31)]
        fractions += list(reversed(fractions))
        for kind in show.DIRECTIONAL_VECTOR_MARKERS:
            view.marker_style = kind
            with self.subTest(kind=kind), patch.object(show, "image_size_tuple", return_value=(100., 100.)), \
                 patch.object(show, "draw_transport_marker_at") as vehicle, patch.object(show, "draw_marker_at") as dot:
                for fraction in fractions:
                    point = show.interpolate_timeline_state(points, fraction)
                    show.TimeLapseMapView.drawMarkerForLat_lon_imageRect_scale_.callable(
                        view, point["lat"], point["lon"], rect, 1.)
                self.assertEqual(vehicle.call_count, len(fractions))
                self.assertTrue(all(call.args[4] == (1., 0.) for call in vehicle.call_args_list))
                self.assertEqual(dot.call_count, len(fractions))

    def test_pilgrim_keeps_facing_and_animation_while_position_changes(self):
        points = self.noisy_timeline()
        view = self.marker_view(points)
        view.marker_style = "pilgrim"
        view.pilgrim_frames = [object() for _ in range(9)]
        view.pilgrim_walk_state = Mock()
        view.pilgrim_walk_state.update.side_effect = range(len(points))
        rect = SimpleNamespace(origin=SimpleNamespace(x=0., y=0.))
        with patch.object(show, "image_size_tuple", return_value=(100., 100.)), \
             patch.object(show, "draw_pilgrim_at_marker") as pilgrim, patch.object(show, "draw_marker_at"):
            for point in points:
                show.TimeLapseMapView.drawMarkerForLat_lon_imageRect_scale_.callable(
                    view, point["lat"], point["lon"], rect, 1.)
        self.assertEqual(view.pilgrim_walk_state.update.call_count, len(points))
        for index, call in enumerate(pilgrim.call_args_list):
            self.assertIs(call.args[3], view.pilgrim_frames[index])
            self.assertEqual(call.args[6:], (0., False))

    def test_active_member_uses_its_own_cached_endpoints_not_the_block_chord(self):
        east = [{"lat": 50., "lon": 6.}, {"lat": 50., "lon": 6.01}]
        west = [{"lat": 50., "lon": 8.}, {"lat": 50., "lon": 7.99}]
        combined = east + west
        metadata = {"active_member_id": 7, "block_member_spans": {
            7: (0., .5, east, east), 9: (.5, 1., west, west)}}
        view = self.marker_view(combined)
        view.map_metadata = metadata
        self.assertEqual(show.TimeLapseMapView.fixedStageTangent.callable(view), (1., 0.))
        view.map_metadata = {**metadata, "active_member_id": 9}
        self.assertEqual(show.TimeLapseMapView.fixedStageTangent.callable(view), (-1., 0.))
        for active, expected in ((7, east), (9, west)):
            with self.subTest(active=active), patch.object(show, "timed_points_from_metadata", side_effect=AssertionError):
                reference = show.marker_reference_points({**metadata, "active_member_id": active}, combined)
                self.assertIs(reference, expected)
                # The overview has its own projection metadata but shares this reference.
                view.map_metadata = {}
                view.route_points = reference
                self.assertEqual(show.TimeLapseMapView.fixedStageTangent.callable(view),
                                 (1., 0.) if active == 7 else (-1., 0.))

    def test_reference_is_calculated_only_when_track_or_map_changes(self):
        points = self.noisy_timeline()
        view = self.marker_view(points)
        view.media_image = None
        view.pilgrim_walk_state = Mock()
        view.fixedStageTangent = Mock(side_effect=lambda: show.TimeLapseMapView.fixedStageTangent.callable(view))
        view._reset_media_layout_animation = Mock()
        view.setNeedsDisplay_ = Mock()
        image = view.map_image
        metadata = {"active_member_id": 7, "block_member_spans": {
            7: (0., .5, points, points), 9: (.5, 1., list(reversed(points)), [])}}
        configure = show.TimeLapseMapView.configureWithImage_metadata_routePoints_arrowLatLon_mediaImage_highlightRoute_.callable
        for identity in (7, 9):
            active = {**metadata, "active_member_id": identity}
            for point in points:
                configure(view, image, active, points, (point["lat"], point["lon"]), None, False)
                self.assertEqual(view.stage_tangent, (1., 0.) if identity == 7 else (-1., 0.))
        self.assertEqual(view.fixedStageTangent.call_count, 2)
        self.assertEqual(view.pilgrim_walk_state.reset.call_count, 2)
        # A media change does not reset heading or gait.
        configure(view, image, active, points, (50., 6.), object(), False)
        self.assertEqual(view.fixedStageTangent.call_count, 2)
        self.assertEqual(view.pilgrim_walk_state.reset.call_count, 2)

    def test_legacy_and_degenerate_tracks_keep_previous_reference_policy(self):
        points = self.noisy_timeline()
        self.assertIs(show.marker_reference_points(None, points), points)
        self.assertIs(show.marker_reference_points({}, points), points)
        for route in ([], points[:1], [points[0], points[1], points[0]]):
            view = self.marker_view(route)
            self.assertIsNone(view.stage_tangent)
            self.assertEqual(show.pilgrim_orientation_for_tangent(view.stage_tangent), (0., False))

    def test_reference_only_projects_endpoints(self):
        view = self.marker_view(self.noisy_timeline())
        view.imagePixelForLat_lon_ = Mock(wraps=view.imagePixelForLat_lon_)
        self.assertEqual(show.TimeLapseMapView.fixedStageTangent.callable(view), (1., 0.))
        self.assertEqual(view.imagePixelForLat_lon_.call_count, 2)
        view.imagePixelForLat_lon_.return_value = None
        view.imagePixelForLat_lon_.side_effect = None
        self.assertIsNone(show.TimeLapseMapView.fixedStageTangent.callable(view))

    def test_directional_icons_stay_clear_of_position_dot(self):
        for kind in ("bus", "plane", "horse", "train"):
            with self.subTest(kind=kind), patch.object(show, "NSGraphicsContext"), \
                 patch.object(show, "NSAffineTransform") as transforms, patch.object(show, "draw_cocoa_symbol") as draw, \
                 patch.object(show, "ns_color", return_value="color"):
                show.draw_transport_marker_at(400., 300., 900., kind, (-1., 0.), (1, 0, 0, 1), 5, 1.)
                center = transforms.transform.return_value.translateXBy_yBy_.call_args.args
                self.assertGreater(center[1] - 600., draw.call_args.args[2] * .5 + 5)
                self.assertEqual(draw.call_args.args[:2], (kind, (0, 0)))
