# SPDX-License-Identifier: GPL-3.0-or-later

from cocoa_circular_progress import progress_fraction
import unittest


class CircularProgressTests(unittest.TestCase):
    def test_progress_fraction_is_clamped(self):
        self.assertEqual(progress_fraction(0, 10), 0.0)
        self.assertEqual(progress_fraction(5, 10), 0.5)
        self.assertEqual(progress_fraction(12, 10), 1.0)
        self.assertEqual(progress_fraction(-1, 10), 0.0)

    def test_progress_fraction_unknown_total_is_indeterminate(self):
        self.assertIsNone(progress_fraction(1, 0))
        self.assertIsNone(progress_fraction("bad", 10))


if __name__ == "__main__":
    unittest.main()
