"""Automatic import offers must not retime recorded tracks, including the demo."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import GPXEditorController, TrackRecord
from cocoa_adventure_map import AdventureMapController


GPX_NS = "http://www.topografix.com/GPX/1/1"


def route_with_times(times, nr=1):
    track = ET.Element(f"{{{GPX_NS}}}trk")
    ET.SubElement(track, f"{{{GPX_NS}}}name").text = "Test route"
    segment = ET.SubElement(track, f"{{{GPX_NS}}}trkseg")
    for index, timestamp in enumerate(times):
        point = ET.SubElement(
            segment, f"{{{GPX_NS}}}trkpt", lat=str(43 + index / 1000), lon="-1.2"
        )
        if timestamp is not None:
            ET.SubElement(point, f"{{{GPX_NS}}}time").text = timestamp
    return TrackRecord(nr=nr, element=track)


class RouteTimingOfferTests(unittest.TestCase):
    def make_controller(self, tracks, selected=None):
        gpx = SimpleNamespace(
            tracks=tracks,
            selected_tracks=Mock(return_value=tracks if selected is None else selected),
            _fully_untimed_route=lambda track: GPXEditorController._fully_untimed_route(None, track),
            _media_timing_eligible_route=lambda track: GPXEditorController._media_timing_eligible_route(None, track),
            media_timing_max_distance_m=100.0,
        )
        return SimpleNamespace(
            gpx_controller=gpx,
            adventure_file=Path("/tmp/route-timing-test.adv"),
            weather_consent="disabled",
            _import_media_files=Mock(side_effect=lambda paths: paths),
            demo_mode=False,
            demo_coordinator=None,
            pending_control_media=set(),
            pending_route_timing_media=set(),
            pending_route_timing_track_nr=None,
            control_file=None,
            queue_media_processing=Mock(),
            _journal=Mock(),
        )

    def drop_media(self, controller, timing_response=1000):
        paths = [Path("/tmp/photo.jpg"), Path("/tmp/video.mov")]
        import_alert = Mock()
        import_alert.runModal.return_value = 1000
        timing_alert = Mock()
        timing_alert.runModal.return_value = timing_response
        before = [ET.tostring(track.element) for track in controller.gpx_controller.tracks]
        with patch("cocoa_adventure_map.NSAlert") as alerts:
            alerts.alloc.return_value.init.side_effect = [import_alert, timing_alert]
            AdventureMapController._handle_media_drop(controller, paths)
            alert_count = alerts.alloc.return_value.init.call_count
        controller._import_media_files.assert_called_once_with(paths)
        controller.queue_media_processing.assert_called_once_with(paths)
        self.assertEqual(controller.pending_control_media, set(paths))
        self.assertEqual(
            [ET.tostring(track.element) for track in controller.gpx_controller.tracks], before
        )
        return alert_count, timing_alert, paths

    def assert_no_timing_offer(self, controller):
        count, alert, _ = self.drop_media(controller)
        self.assertEqual(count, 1)
        alert.runModal.assert_not_called()
        self.assertEqual(controller.pending_route_timing_media, set())
        self.assertIsNone(controller.pending_route_timing_track_nr)

    def test_recorded_and_partly_timed_tracks_are_not_offered(self):
        timestamp = "2024-07-15T04:41:58Z"
        for times in ([timestamp, timestamp], [timestamp, None], [None, timestamp], []):
            with self.subTest(times=times):
                self.assert_no_timing_offer(self.make_controller([route_with_times(times)]))

    def test_bundled_demo_track_is_not_offered(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "demo/St-Jean-Pied-de-Port-to-Roncesvalles/1 - GPX Track"
            / "St-Jean-Pied-de-Port-to-Roncesvalles.gpx"
        )
        element = ET.parse(path).getroot().find(f"{{{GPX_NS}}}trk")
        self.assertIsNotNone(element)
        track = TrackRecord(nr=1, element=element)
        self.assertTrue(track.points())
        self.assertTrue(all(point.time is not None for point in track.points()))
        # Use the ordinary import path: no demo-specific exemption is needed.
        self.assert_no_timing_offer(self.make_controller([track]))

    def test_untimed_route_offers_and_queues_estimation(self):
        controller = self.make_controller([route_with_times([None, None])])
        count, alert, paths = self.drop_media(controller)
        self.assertEqual(count, 2)
        text = alert.setInformativeText_.call_args.args[0]
        self.assertIn("This route has no recorded times.", text)
        self.assertIn("Choose Not Now", text)
        self.assertEqual(controller.pending_route_timing_media, set(paths))
        self.assertEqual(controller.pending_route_timing_track_nr, 1)

    def test_not_now_imports_media_without_queuing_timing(self):
        controller = self.make_controller([route_with_times([None, None])])
        count, _, _ = self.drop_media(controller, timing_response=1001)
        self.assertEqual(count, 2)
        self.assertEqual(controller.pending_route_timing_media, set())
        self.assertIsNone(controller.pending_route_timing_track_nr)

    def test_unselected_timed_track_is_not_offered(self):
        controller = self.make_controller(
            [route_with_times(["2024-07-15T04:41:58Z"])], selected=[]
        )
        self.assert_no_timing_offer(controller)

    def test_unselected_untimed_route_is_offered(self):
        controller = self.make_controller([route_with_times([None])], selected=[])
        count, _, _ = self.drop_media(controller)
        self.assertEqual(count, 2)
        self.assertEqual(controller.pending_route_timing_track_nr, 1)

    def test_multiple_untimed_candidates_are_not_automatically_chosen(self):
        controller = self.make_controller(
            [route_with_times([None], nr=1), route_with_times([None], nr=2)]
        )
        self.assert_no_timing_offer(controller)

    def test_timed_selection_does_not_offer_unselected_untimed_route(self):
        timed = route_with_times(["2024-07-15T04:41:58Z"], nr=1)
        untimed = route_with_times([None], nr=2)
        self.assert_no_timing_offer(self.make_controller([timed, untimed], selected=[timed]))

    def test_manual_retiming_remains_available_for_recorded_tracks(self):
        timestamp = "2024-07-15T04:41:58Z"
        for times in ([timestamp, timestamp], [timestamp, None], [None, None]):
            with self.subTest(times=times):
                controller = self.make_controller([route_with_times(times)])
                menu_item = SimpleNamespace(action=lambda: "estimateRouteTimesFromMedia:")
                self.assertTrue(
                    GPXEditorController.validateMenuItem_.callable(
                        controller.gpx_controller, menu_item
                    )
                )


if __name__ == "__main__":
    unittest.main()
