"""Shared offline-state behavior.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import urllib.error
import unittest

from network_availability import NetworkAvailabilityCoordinator, is_connectivity_error


class NetworkAvailabilityTests(unittest.TestCase):
    def test_http_policy_errors_are_not_treated_as_offline(self):
        error = urllib.error.HTTPError("https://example.test", 429, "limited", {}, None)
        try:
            self.assertFalse(is_connectivity_error(error))
        finally:
            error.close()

    def test_connection_errors_enter_offline_only_after_grace_period(self):
        now = [10.0]
        coordinator = NetworkAvailabilityCoordinator(
            grace_seconds=5.0,
            probe=lambda: False,
            clock=lambda: now[0],
        )
        coordinator.report_failure(ConnectionError("offline"))
        self.assertEqual(coordinator.state, "online")

        now[0] = 14.9
        coordinator.report_failure(ConnectionError("offline"))
        self.assertEqual(coordinator.state, "online")

        now[0] = 15.1
        coordinator.report_failure(ConnectionError("offline"))
        self.assertEqual(coordinator.state, "offline")


if __name__ == "__main__":
    unittest.main()
