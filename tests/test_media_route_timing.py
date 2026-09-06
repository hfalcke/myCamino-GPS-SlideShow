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

    def test_one_anchor_uses_fallback_speed(self):
        proposal = propose_route_timing(
            self.route,
            [observation("only.jpg", 0, 50.0, 7.015)],
            fallback_speed_kmh=4.2,
        )
        self.assertAlmostEqual(proposal.extrapolation_speed_kmh, 4.2)
        self.assertEqual(len(proposal.point_times), len(self.route))

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
