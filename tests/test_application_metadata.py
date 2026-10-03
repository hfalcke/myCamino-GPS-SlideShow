import unittest
from unittest.mock import patch

from application_metadata import (
    APP_BUNDLE_VERSION,
    APP_RELEASE_DATE,
    APP_VERSION,
    bundle_build_number,
    compact_version_label,
    full_version_label,
    release_date,
)


class ApplicationMetadataTests(unittest.TestCase):
    def test_declared_version_and_date_are_valid(self):
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertRegex(APP_BUNDLE_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(release_date().isoformat(), APP_RELEASE_DATE)
        self.assertEqual(APP_VERSION, APP_BUNDLE_VERSION)
        self.assertEqual(bundle_build_number(), APP_RELEASE_DATE.replace('-', ''))

    def test_user_visible_labels_include_version_and_release_date(self):
        self.assertTrue(compact_version_label().startswith(f'v{APP_VERSION} · '))
        self.assertTrue(full_version_label().startswith(f'Version {APP_VERSION} · '))
        with patch('application_metadata.APP_VERSION', '1.2.3'), patch('application_metadata.APP_RELEASE_DATE', '2026-10-03'):
            self.assertEqual(compact_version_label(), 'v1.2.3 · 03 Oct 2026')
            self.assertEqual(full_version_label(), 'Version 1.2.3 · 3 October 2026')
            self.assertEqual(bundle_build_number(), '20261003')


if __name__ == "__main__":
    unittest.main()
