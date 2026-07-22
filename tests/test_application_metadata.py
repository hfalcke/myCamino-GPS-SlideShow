import unittest

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
        self.assertEqual(bundle_build_number(), "20260722")

    def test_user_visible_labels_include_version_and_release_date(self):
        self.assertEqual(
            compact_version_label(),
            "v0.9.0 · 22 Jul 2026",
        )
        self.assertEqual(
            full_version_label(),
            "Version 0.9.0 · 22 July 2026",
        )


if __name__ == "__main__":
    unittest.main()
