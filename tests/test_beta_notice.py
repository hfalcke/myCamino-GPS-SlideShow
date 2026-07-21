import unittest
from types import SimpleNamespace
from unittest import mock

import GPSTrackShowGUI as gui

from beta_notice import (
    BETA_NOTICE_VERSION,
    BUG_REPORT_URL,
    FEATURE_REQUEST_URL,
    beta_notice_should_be_shown,
)


class BetaNoticeTests(unittest.TestCase):
    def test_notice_is_shown_until_current_version_is_acknowledged(self):
        self.assertTrue(beta_notice_should_be_shown(0))
        self.assertTrue(beta_notice_should_be_shown(None))
        self.assertFalse(beta_notice_should_be_shown(BETA_NOTICE_VERSION))
        self.assertFalse(beta_notice_should_be_shown(BETA_NOTICE_VERSION + 1))

    def test_public_report_links_use_structured_github_forms(self):
        self.assertEqual(
            BUG_REPORT_URL,
            "https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/new?template=01-bug.yml",
        )
        self.assertEqual(
            FEATURE_REQUEST_URL,
            "https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/new?template=02-feature-request.yml",
        )

    def test_first_launch_acknowledges_notice_and_opens_report_link_on_request(self):
        class Defaults:
            stored = 0

            def integerForKey_(self, _key):
                return self.stored

            def setInteger_forKey_(self, value, _key):
                self.stored = value

            def synchronize(self):
                return True

        class DefaultsFactory:
            @staticmethod
            def standardUserDefaults():
                return defaults

        class Alert:
            @classmethod
            def alloc(cls):
                return cls()

            def init(self):
                return self

            def setMessageText_(self, text):
                self.message = text

            def setInformativeText_(self, text):
                self.informative = text

            def addButtonWithTitle_(self, title):
                return title

            def runModal(self):
                return 1001

        defaults = Defaults()
        opened = []
        controller = SimpleNamespace(openBugReport_=lambda sender: opened.append(sender))
        with mock.patch.object(gui, "NSUserDefaults", DefaultsFactory), mock.patch.object(gui, "NSAlert", Alert):
            gui.GPXTrackerController.show_first_launch_beta_notice(controller)

        self.assertEqual(defaults.stored, BETA_NOTICE_VERSION)
        self.assertEqual(opened, [None])


if __name__ == "__main__":
    unittest.main()
