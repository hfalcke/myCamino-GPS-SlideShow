"""Regression tests for the retained media-update review workflow."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from GPSTrackShowGUI import (
    GPXTrackerController,
    MediaBrowserWindow,
    merge_media_update_review_items,
)


def update_item(path: Path, *, add=False, metadata=False, move=False):
    return SimpleNamespace(
        media_path=path,
        add_to_control=add,
        update_metadata=metadata,
        reposition=move,
        apply_update=bool(add or metadata or move),
    )


class MediaUpdateReviewWindowTests(unittest.TestCase):
    def test_rechecked_rows_replace_duplicates_and_preserve_choices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = update_item(root / "first.jpeg", add=True, metadata=False)
            second = update_item(root / "second.jpeg", metadata=True)
            refreshed_first = update_item(
                root / "first.jpeg", add=False, metadata=True, move=True
            )
            added = update_item(root / "third.jpeg", add=True, metadata=True)

            merged = merge_media_update_review_items(
                [first, second],
                [refreshed_first, added],
            )

            self.assertEqual(
                [item.media_path.name for item in merged],
                ["first.jpeg", "second.jpeg", "third.jpeg"],
            )
            self.assertIs(merged[0], refreshed_first)
            self.assertTrue(merged[0].add_to_control)
            self.assertFalse(merged[0].update_metadata)
            self.assertFalse(merged[0].reposition)
            self.assertTrue(merged[0].apply_update)
            self.assertIs(merged[1], second)
            self.assertIs(merged[2], added)

    def test_closing_recheck_browser_restores_retained_review(self):
        controller = SimpleNamespace(
            media_browser_window=Mock(),
            media_browser_mode="recheck",
            _restore_media_update_preview=Mock(return_value=True),
        )

        GPXTrackerController._close_media_browser(controller)

        controller.media_browser_window.orderOut_.assert_called_once_with(None)
        controller._restore_media_update_preview.assert_called_once_with()
        self.assertEqual(controller.media_browser_mode, "view")

    def test_empty_recheck_selection_returns_without_analysis(self):
        controller = SimpleNamespace(
            media_browser_mode="recheck",
            selected_media_browser_items=Mock(return_value=[]),
            _close_media_browser=Mock(),
            _start_control_file_update_analysis=Mock(),
        )

        GPXTrackerController._analyze_selected_media_updates(controller)

        controller._close_media_browser.assert_called_once_with()
        controller._start_control_file_update_analysis.assert_not_called()

    def test_review_footer_is_bottom_anchored_and_window_has_minimum_size(self):
        source = inspect.getsource(GPXTrackerController.show_media_update_preview)
        self.assertIn("setContentMinSize_(NSMakeSize(980.0, 500.0))", source)
        self.assertIn(
            "apply_button.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)",
            source,
        )
        self.assertIn(
            "cancel_button.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)",
            source,
        )

    def test_manual_append_always_returns_to_review_before_auto_commit(self):
        module_source = (
            Path(__file__).parent.parent / "GPSTrackShowGUI.py"
        ).read_text(encoding="utf-8")
        start = module_source.index("    def geoLocationsRunFinished_(")
        end = module_source.index("    def cancelGeoLocationsRun_(", start)
        source = module_source[start:end]
        append_branch = source.index("if appended_analysis:")
        automatic_commit = source.index("if not control_file_update_requires_review")
        self.assertLess(append_branch, automatic_commit)
        self.assertIn(
            "self.show_media_update_preview(select_paths=appended_paths)",
            source[append_branch:automatic_commit],
        )

    def test_escape_is_handled_by_retained_media_browser_window(self):
        source = inspect.getsource(MediaBrowserWindow)
        self.assertIn('== "\\x1b"', source)
        self.assertIn("controller.closeMediaBrowser_", source)


if __name__ == "__main__":
    unittest.main()
