"""Regression tests for the retained media-update review workflow."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
    def test_map_preparation_keeps_review_objects_and_presentation_choices(self):
        item = update_item(Path('/project/Others/photo.jpeg'), add=False, metadata=True)
        table = Mock()
        table.selectedRowIndexes.return_value.containsIndex_.return_value = True
        table.visibleRect.return_value.origin = (0, 73)
        checkbox = Mock()
        checkbox.state.return_value = 0
        media_plan = SimpleNamespace(project_dir=Path('/project'), control_file=Path('/project/trip.lst'), items=[item])
        controller = SimpleNamespace(
            control_file_update_plan=SimpleNamespace(media=media_plan), media_update_plan=media_plan,
            media_update_preview_table=table, media_update_preview_visible_items=[item],
            media_update_preview_place_checkbox=checkbox, media_update_preview_window=Mock(),
            startAutomaticMapGeneration_=Mock(), geolocations_thread=None)
        original = controller.control_file_update_plan
        GPXTrackerController._start_review_map_preparation(controller)
        self.assertIs(controller.control_file_update_plan, original)
        self.assertFalse(item.add_to_control)
        self.assertTrue(item.update_metadata)
        self.assertEqual(controller.media_review_map_preparation['selected'], [item.media_path])
        self.assertEqual(controller.media_review_map_preparation['scroll'], (0, 73))
        self.assertFalse(controller.media_review_map_preparation['place'])
        controller.startAutomaticMapGeneration_.assert_called_once_with([])

    def test_rechecking_after_maps_uses_existing_analysis_merge_without_extraction_request(self):
        root = Path('/project')
        item = update_item(root / 'Others/photo.jpeg', add=False)
        review = dict(project=root, control=root / 'trip.lst', phase='waiting_status')
        controller = SimpleNamespace(
            media_review_map_preparation=review, current_project_dir=root,
            _control_file_path=lambda: root / 'trip.lst', media_update_plan=SimpleNamespace(items=[item]),
            _start_control_file_update_analysis=Mock(return_value=True))
        GPXTrackerController.refreshMediaReviewAfterMaps_.callable(controller, None)
        controller._start_control_file_update_analysis.assert_called_once_with(
            paths=[item.media_path], actions={'photo.jpeg': 'use_sidecar'}, append=True)
        self.assertEqual(review['phase'], 'reanalyzing')

    def test_cancelled_provider_or_failed_maps_restore_review_without_changing_choices(self):
        plan = object()
        controller = SimpleNamespace(media_review_map_preparation={'phase': 'maps'},
            pending_slideshow_after_map_generation={'continue_previous': True},
            media_update_plan=plan, _restore_media_update_preview=Mock())
        GPXTrackerController._restore_review_after_map_failure(controller)
        self.assertIs(controller.media_update_plan, plan)
        self.assertIsNone(controller.pending_slideshow_after_map_generation)
        self.assertIsNone(controller.media_review_map_preparation)
        controller._restore_media_update_preview.assert_called_once()

    def test_apply_with_stale_maps_offers_prepare_then_apply_instead_of_partial_commit(self):
        controller = SimpleNamespace(
            media_update_plan=object(), control_file_update_plan=SimpleNamespace(track_maps=SimpleNamespace(warning='Maps stale')),
            _start_review_map_preparation=Mock(), _start_control_file_update_commit=Mock())
        with patch('GPSTrackShowGUI.confirm_alert', return_value=True):
            GPXTrackerController.applyMediaUpdatePlan_.callable(controller, None)
        controller._start_review_map_preparation.assert_called_once_with(apply_after=True)
        controller._start_control_file_update_commit.assert_not_called()

    def test_successful_recheck_applies_only_previously_confirmed_choices(self):
        controller = SimpleNamespace(
            media_review_map_preparation=dict(apply_after=True, place=False),
            control_file_update_plan=SimpleNamespace(track_maps=SimpleNamespace(warning='')),
            _start_control_file_update_commit=Mock(), show_media_update_preview=Mock())
        GPXTrackerController._finish_review_map_preparation(controller)
        controller._start_control_file_update_commit.assert_called_once_with(update_places=False)
        controller.show_media_update_preview.assert_not_called()
        self.assertIsNone(controller.media_review_map_preparation)

    def test_recheck_restores_selection_scroll_and_place_choice_without_resetting_filter(self):
        item = update_item(Path('/project/photo.jpeg'))
        controller = SimpleNamespace(
            media_review_map_preparation=dict(apply_after=False, place=False, selected=[item.media_path], scroll=(0, 73)),
            control_file_update_plan=SimpleNamespace(track_maps=SimpleNamespace(warning='')),
            show_media_update_preview=Mock(), media_update_preview_table=Mock(),
            media_update_preview_place_checkbox=Mock(), media_update_preview_visible_items=[item],
            media_update_plan=SimpleNamespace(items=[item]),
            media_update_preview_filter='excluded', media_update_preview_sort_column='name',
            media_update_preview_sort_ascending=False)
        GPXTrackerController._finish_review_map_preparation(controller)
        controller.show_media_update_preview.assert_called_once()
        controller.media_update_preview_place_checkbox.setState_.assert_called_once_with(0)
        controller.media_update_preview_table.selectRowIndexes_byExtendingSelection_.assert_called_once()
        controller.media_update_preview_table.scrollPoint_.assert_called_once_with((0, 73))
        self.assertEqual(controller.media_update_preview_filter, 'excluded')
        self.assertFalse(controller.media_update_preview_sort_ascending)

    def test_map_only_preparation_with_no_remaining_review_resumes_without_empty_window(self):
        controller = SimpleNamespace(
            media_review_map_preparation=dict(apply_after=False),
            media_update_plan=SimpleNamespace(items=[]),
            control_file_update_plan=SimpleNamespace(track_maps=SimpleNamespace(warning='', change_count=0)),
            set_status=Mock(), pending_slideshow_after_map_generation={'continue_previous': True},
            performSelector_withObject_afterDelay_=Mock(), show_media_update_preview=Mock())
        GPXTrackerController._finish_review_map_preparation(controller)
        controller.show_media_update_preview.assert_not_called()
        controller.performSelector_withObject_afterDelay_.assert_called_once_with('resumeSlideShowAfterMaps:', None, 0.0)

    def test_review_recheck_from_another_project_is_discarded(self):
        controller = SimpleNamespace(
            media_review_map_preparation=dict(project=Path('/old'), control=Path('/old/trip.lst'), phase='waiting_status'),
            current_project_dir=Path('/new'), _control_file_path=lambda: Path('/new/trip.lst'),
            pending_slideshow_after_map_generation={}, _start_control_file_update_analysis=Mock())
        GPXTrackerController.refreshMediaReviewAfterMaps_.callable(controller, None)
        controller._start_control_file_update_analysis.assert_not_called()
        self.assertIsNone(controller.media_review_map_preparation)

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
        automatic_commit = source.index("not control_file_update_requires_review")
        self.assertLess(append_branch, automatic_commit)
        self.assertIn(
            "self.show_media_update_preview(select_paths=appended_paths)",
            source[append_branch:automatic_commit],
        )

    def test_escape_is_handled_by_retained_media_browser_window(self):
        source = inspect.getsource(MediaBrowserWindow)
        self.assertIn('== "\\x1b"', source)
        self.assertIn("controller.closeMediaBrowser_", source)

    def test_automatic_map_review_waits_for_fresh_project_status(self):
        source = inspect.getsource(
            GPXTrackerController.geoLocationsRunFinished_.callable
        )
        automatic_branch = source[source.index('if mode == "automatic_maps":'):]
        pending = automatic_branch.index(
            "self.pending_control_update_offer_after_status = list("
        )
        refresh = automatic_branch.index(
            'self.start_async_project_status_refresh("automatic map generation")',
            pending,
        )
        self.assertLess(pending, refresh)
        self.assertIn(
            "applyAsyncProjectStatus_ opens the confirmation",
            automatic_branch,
        )

    def test_fresh_status_opens_deferred_control_update_confirmation(self):
        label = Mock()
        button = Mock()
        controller = SimpleNamespace(
            status_refresh_generation=7,
            project_status_pending=True,
            media_counts_cache=None,
            control_ready_cache=False,
            track_maps_status_cache=None,
            track_endpoint_place_missing_cache=0,
            gpx_ready_cache=False,
            gpx_summary_label=label,
            track_maps_summary_label=label,
            media_summary_label=label,
            media_metadata_button=button,
            control_file_summary_label=label,
            parameters={},
            pending_control_update_after_status=None,
            pending_control_update_offer_after_status=["new.mov"],
            _format_track_maps_summary_from_status=Mock(return_value="Current"),
            refresh_section_status_indicators=Mock(),
            set_status=Mock(),
            performSelector_withObject_afterDelay_=Mock(),
        )
        result = {
            "generation": 7,
            "media_counts": (1, 2),
            "control_ready": True,
            "track_maps_status": {"summary_exists": True},
            "endpoint_place_missing_count": 0,
            "gpx_ready": True,
            "gpx_summary": "Tracks",
            "media_summary": "Media",
            "control_summary": "Control file up to date",
        }

        GPXTrackerController.applyAsyncProjectStatus_.callable(controller, result)

        self.assertEqual(controller.pending_control_update_after_status, ["new.mov"])
        self.assertIsNone(controller.pending_control_update_offer_after_status)
        controller.performSelector_withObject_afterDelay_.assert_called_once_with(
            "offerControlUpdateAfterMaps:", None, 0.0
        )


if __name__ == "__main__":
    unittest.main()
