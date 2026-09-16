"""Geometry-preserving route timing from nearby media.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from media_route_timing import MediaObservation, RoutePoint, propose_route_timing


UTC = timezone.utc


def observation(name, minute, latitude, longitude):
    return MediaObservation(
        Path(name),
        datetime(2026, 8, 3, 10, minute, tzinfo=UTC),
        latitude,
        longitude,
    )


class MediaRouteTimingTests(unittest.TestCase):
    def setUp(self):
        self.route = [
            RoutePoint(50.0, 7.000, 0),
            RoutePoint(50.0, 7.010, 0),
            RoutePoint(50.0, 7.020, 0),
            RoutePoint(50.0, 7.030, 0),
        ]

    def test_interpolates_existing_points_without_changing_geometry(self):
        media = [
            observation("first.jpg", 0, 50.0, 7.005),
            observation("second.jpg", 20, 50.0, 7.025),
        ]
        proposal = propose_route_timing(self.route, media, maximum_distance_m=100)
        self.assertEqual(len(proposal.anchors), 2)
        self.assertEqual(len(proposal.point_times), len(self.route))
        self.assertLess(proposal.point_times[0], media[0].timestamp)
        self.assertGreater(proposal.point_times[-1], media[-1].timestamp)
        self.assertTrue(
            all(later > earlier for earlier, later in zip(proposal.point_times, proposal.point_times[1:]))
        )

    def test_distance_limit_is_inclusive_and_configurable(self):
        # Roughly 89 m north of the equator-aligned route latitude.
        media = [observation("near.jpg", 0, 50.0008, 7.015)]
        accepted = propose_route_timing(self.route, media, maximum_distance_m=100)
        rejected = propose_route_timing(self.route, media, maximum_distance_m=50)
        self.assertEqual(len(accepted.anchors), 1)
        self.assertEqual(len(rejected.anchors), 0)
        self.assertEqual(rejected.ignored[0][1], "farther than configured distance")

    def test_monotonic_matching_ignores_a_photo_that_moves_backwards(self):
        media = [
            observation("first.jpg", 0, 50.0, 7.025),
            observation("back.jpg", 10, 50.0, 7.005),
            observation("last.jpg", 20, 50.0, 7.029),
        ]
        proposal = propose_route_timing(self.route, media, maximum_distance_m=100)
        self.assertEqual([item.observation.path.name for item in proposal.anchors], ["first.jpg", "last.jpg"])
        self.assertEqual(proposal.ignored[0][0].path.name, "back.jpg")
        self.assertEqual(proposal.ignored[0][1], "inconsistent route order")

    def test_media_can_establish_the_reverse_of_the_authored_route_direction(self):
        media = [
            observation("first.jpg", 0, 50.0, 7.025),
            observation("second.jpg", 20, 50.0, 7.005),
        ]

        proposal = propose_route_timing(self.route, media, maximum_distance_m=100)

        self.assertTrue(proposal.route_reversed)
        self.assertEqual(len(proposal.anchors), 2)
        self.assertEqual(
            [item.observation.path.name for item in proposal.anchors],
            ["first.jpg", "second.jpg"],
        )
        self.assertTrue(
            all(
                later > earlier
                for earlier, later in zip(
                    proposal.point_times, proposal.point_times[1:]
                )
            )
        )

    def test_one_anchor_uses_fallback_speed(self):
        proposal = propose_route_timing(
            self.route,
            [observation("only.jpg", 0, 50.0, 7.015)],
            fallback_speed_kmh=4.2,
        )
        self.assertAlmostEqual(proposal.extrapolation_speed_kmh, 4.2)
        self.assertEqual(len(proposal.point_times), len(self.route))

    def test_manual_waypoint_anchor_is_preserved_in_timeline(self):
        manual_time = datetime(2026, 8, 3, 10, 10, tzinfo=UTC)
        proposal = propose_route_timing(
            self.route,
            [
                observation("first.jpg", 0, 50.0, 7.005),
                observation("last.jpg", 20, 50.0, 7.025),
            ],
            fixed_point_times={1: manual_time},
            allow_reverse=False,
        )

        self.assertEqual(proposal.point_times[1], manual_time)

    def test_unclassified_existing_times_do_not_constrain_retiming(self):
        proposal = propose_route_timing(
            self.route,
            [
                observation("first.jpg", 0, 50.0, 7.025),
                observation("second.jpg", 20, 50.0, 7.005),
            ],
            fixed_point_times={},
        )

        self.assertTrue(proposal.route_reversed)

    def test_duplicate_content_identity_counts_once_for_direction(self):
        duplicated = MediaObservation(
            Path("copy.jpeg"),
            datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
            50.0,
            7.025,
            "a" * 64,
        )
        original = MediaObservation(
            Path("original.jpeg"),
            duplicated.timestamp,
            duplicated.latitude,
            duplicated.longitude,
            "a" * 64,
        )
        last = MediaObservation(
            Path("last.jpeg"),
            datetime(2026, 8, 3, 10, 20, tzinfo=UTC),
            50.0,
            7.005,
            "b" * 64,
        )

        proposal = propose_route_timing(self.route, [original, duplicated, last])

        self.assertTrue(proposal.route_reversed)
        self.assertEqual(proposal.duplicate_count, 1)
        self.assertEqual(len(proposal.anchors), 2)

    def test_equal_time_and_coordinate_fallback_counts_once(self):
        first = observation("first.jpeg", 0, 50.0, 7.005)
        duplicate = observation("copy.jpeg", 0, 50.0, 7.005)
        last = observation("last.jpeg", 20, 50.0, 7.025)

        proposal = propose_route_timing(self.route, [first, duplicate, last])

        self.assertEqual(proposal.duplicate_count, 1)
        self.assertEqual(len(proposal.anchors), 2)

    def test_segment_gap_does_not_add_route_distance(self):
        route = [
            RoutePoint(50.0, 7.0, 0),
            RoutePoint(50.0, 7.01, 0),
            RoutePoint(51.0, 8.0, 1),
            RoutePoint(51.0, 8.01, 1),
        ]
        proposal = propose_route_timing(
            route,
            [
                observation("one.jpg", 0, 50.0, 7.005),
                observation("two.jpg", 20, 51.0, 8.005),
            ],
        )
        self.assertEqual(len(proposal.anchors), 2)
        self.assertLess((proposal.point_times[2] - proposal.point_times[1]).total_seconds(), 1.0)


if __name__ == "__main__":
    unittest.main()
