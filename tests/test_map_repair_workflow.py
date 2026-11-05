"""Map maintenance must converge without removing authored dependencies."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPSTrackShowGUI import (
    GPXTrackerController, missing_control_map_assets, protected_control_map_paths,
    select_map_repair_numbers,
)


class MapRepairWorkflowTests(unittest.TestCase):
    def context(self, root):
        maps = root / 'trackimages'
        maps.mkdir(exist_ok=True)
        return dict(output_dir=str(maps), output_base='New', args=SimpleNamespace(),
                    overview_path=str(maps / 'New.png'), table_json_path=str(maps / 'New-summary.json'),
                    tracks=[dict(table_number=1, track_geometry_fingerprint='geometry',
                                 track_data_fingerprint='data', track_data_sidecar='New-trackdata/0001.json',
                                 track_plot_image_filename='0001_Stage_New.png',
                                 track_plot_time_lapse_image_filename='0001_Stage_New-timelapse.png')])

    def test_known_required_names_narrow_repairs_without_case_or_variant_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            self.assertEqual(select_map_repair_numbers(context, [0, 1, 2],
                             {'0001_stage_new-timelapse.png'}), ([1], set()))
            self.assertEqual(select_map_repair_numbers(context, [0, 1], {'new.png'}), ([0], set()))
            self.assertEqual(select_map_repair_numbers(context, [0, 1], None), ([0, 1], set()))

    def test_unknown_authored_names_never_filter_out_missing_current_maps(self):
        context = {'overview_path': 'New.png', 'tracks': []}
        selected, unknown = select_map_repair_numbers(context, [0, 1, 2], {'old.png'})
        self.assertEqual(selected, [0, 1, 2])
        self.assertEqual(unknown, {'old.png'})

    def write_pair(self, directory, name):
        path = directory / name
        path.write_bytes(b'unchanged-map')
        path.with_suffix('.json').write_text('{}')
        return path

    def test_cleanup_keeps_authored_hidden_and_paired_maps_and_other_families(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            maps = Path(context['output_dir'])
            preserved = [self.write_pair(maps, name) for name in (
                '0001_Stage_New.png', '0099_Previous_New.png', '0099_Previous_New-timelapse.png',
                '0002_Other_Adventure.png')]
            obsolete = self.write_pair(maps, '0003_Unused_New.png')
            (root / 'second.lst').write_text('# #Map: 0099_Previous_New.png\n')
            controller = SimpleNamespace(control_table_dirty=False)
            removed = GPXTrackerController._cleanup_obsolete_track_map_files(controller, context)
            self.assertEqual({p.name for p in removed}, {obsolete.name, obsolete.with_suffix('.json').name})
            self.assertTrue(all(p.exists() and p.with_suffix('.json').exists() for p in preserved))
            protected = protected_control_map_paths(maps)
            self.assertIn(preserved[2].resolve(), protected)
            self.assertEqual(missing_control_map_assets((root / 'second.lst').read_text(), maps), [])

    def test_cleanup_defers_when_control_edits_or_unreadable_control_prevent_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            obsolete = self.write_pair(Path(context['output_dir']), '0003_Unused_New.png')
            controller = SimpleNamespace(control_table_dirty=True)
            self.assertEqual(GPXTrackerController._cleanup_obsolete_track_map_files(controller, context), [])
            controller.control_table_dirty = False
            with patch('GPSTrackShowGUI.protected_control_map_paths', side_effect=OSError('unreadable')):
                self.assertEqual(GPXTrackerController._cleanup_obsolete_track_map_files(controller, context), [])
            self.assertTrue(obsolete.exists())

    def test_media_cleanup_also_preserves_authored_map_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            maps = root / 'trackimages'
            maps.mkdir()
            old = self.write_pair(maps, 'New-media-2026-01-01.png')
            self.write_pair(maps, 'New-media-2026-01-01-timelapse.png')
            unused = self.write_pair(maps, 'New-media-2026-01-02.png')
            (root / 'trip.lst').write_text('#MediaMap: New-media-2026-01-01.png\n'
                                           '#MediaMap: New-media-2026-01-01-timelapse.png\n')
            controller = SimpleNamespace(control_table_dirty=False, _control_file_base_name=lambda: 'New')
            removed = GPXTrackerController._cleanup_obsolete_media_map_files(
                controller, SimpleNamespace(media_map_specs=[]), maps)
            self.assertTrue(old.exists())
            self.assertEqual({p.name for p in removed}, {unused.name, unused.with_suffix('.json').name})

    def run_worker(self, root, *, old_reference=False, unresolved=False, renderer_error=False, external_reference=False):
        context = self.context(root)
        maps = Path(context['output_dir'])
        control = root / 'trip.lst'
        name = '0001_Stage_Old.png' if old_reference else '0001_Stage_New.png'
        if external_reference:
            name = str(root.parent / 'old-project' / name)
        control.write_text(f'#Map: {name}\n')
        controller = SimpleNamespace(
            current_project_dir=root, control_table_dirty=False,
            parameters={'maps.output_provider': 'geoapify', 'locations.reuse_radius_m': 100},
            _current_project_name=lambda: 'New', _uses_media_only_maps=lambda: False,
            _current_single_gpx_path=lambda: root / 'tracks.gpx',
            _plot_common_options=lambda *args: {}, _track_map_parameter_signature=lambda *args: {},
            _cleanup_obsolete_track_map_files=Mock(return_value=[]),
            _cleanup_obsolete_media_map_files=Mock(return_value=[]),
            _track_map_update_numbers=Mock(return_value=[1]), _media_map_options=lambda: {},
            _use_track_order=lambda: False)
        (root / 'tracks.gpx').write_text('<gpx/>')
        reconcile = SimpleNamespace(reused=[], repaired=[], substantive_parameter_changes=[], ambiguous=[])
        plan = SimpleNamespace(media_map_specs=[], overview_name='New.png')

        def render(_context, **kwargs):
            if renderer_error:
                raise RuntimeError('provider unavailable')
            if unresolved:
                return {'created_paths': []}
            from map_overlay import MAP_CONTENT_VERSION
            paths = []
            for key in ('track_plot_image_filename', 'track_plot_time_lapse_image_filename'):
                image = maps / context['tracks'][0][key]
                image.write_bytes(b'new-map')
                image.with_suffix('.json').write_text(json.dumps(dict(
                    background_only=True, map_content_version=MAP_CONTENT_VERSION,
                    track_number=1, track_geometry_fingerprint='geometry')))
                paths.append(str(image))
            return {'created_paths': paths}

        with patch('GPSTrackShowGUI.prepare_with_options', return_value=context), \
             patch('GPSTrackShowGUI.reconcile_track_sidecars', return_value=reconcile), \
             patch('GPSTrackShowGUI.relink_numbered_track_assets', return_value=SimpleNamespace(relinked=[], skipped=[])), \
             patch('GPSTrackShowGUI.execute_map_variants_from_context', side_effect=render) as execute, \
             patch('GPSTrackShowGUI.project_map_plan_from_sidecars', return_value=plan), \
             patch('GPSTrackShowGUI.render_project_map_plan', return_value=[]):
            result = GPXTrackerController._generate_missing_project_maps_worker(
                controller, project_dir=root, control_path=control, cancel_event=threading.Event(),
                required_map_names=[name, name.replace('.png', '-timelapse.png')])
        self.assertEqual(execute.call_args.kwargs['selected_track_numbers'], [1])
        self.assertEqual(control.read_text(), f'#Map: {name}\n')
        return result

    def test_old_names_prepare_current_maps_then_require_review_not_another_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_worker(Path(directory), old_reference=True)
            self.assertEqual(result['created_count'], 2)
            self.assertTrue(result['control_review_required'])
            self.assertEqual(result['failures'], [])

    def test_current_names_repair_and_validate_successfully(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_worker(Path(directory))
            self.assertEqual(result['created_count'], 2)
            self.assertFalse(result['control_review_required'] or result['failures'])

    def test_absolute_old_project_reference_requires_review_after_local_map_is_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_worker(Path(directory), external_reference=True)
            self.assertEqual(result['created_count'], 2)
            self.assertTrue(result['control_review_required'])
            self.assertEqual(result['failures'], [])

    def test_zero_output_for_required_current_maps_is_failure_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_worker(Path(directory), unresolved=True)
            self.assertEqual(len(result['failures']), 2)
            self.assertFalse(result['control_review_required'])

    def test_provider_failure_does_not_become_a_control_reference_review(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_worker(Path(directory), old_reference=True, renderer_error=True)
            self.assertEqual(result['failures'][0]['error'], 'provider unavailable')
            self.assertFalse(result['control_review_required'])

    def test_completion_retains_launch_request_until_reference_review_is_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'trip.lst'
            control.write_text('#Map: old.png\n')
            controller = SimpleNamespace(
                geolocations_mode='automatic_maps',
                automatic_map_generation_result=dict(created_count=2, failures=[], control_review_required=True),
                pending_slideshow_after_map_generation={'time_lapse': True},
                automatic_map_pending_imported_paths=[], automatic_map_review_control=False,
                _control_file_path=lambda: control, set_status=Mock(), _continue_adventure_processing=Mock(),
                start_async_project_status_refresh=Mock(), performSelector_withObject_afterDelay_=Mock(),
                _finish_geolocations_run=Mock(), _show_scrollable_list=Mock())
            GPXTrackerController.geoLocationsRunFinished_.callable(controller, 'done')
            self.assertTrue(controller.pending_slideshow_after_map_generation['awaiting_review'])
            self.assertEqual(controller.pending_control_update_offer_after_status, [])
            controller.performSelector_withObject_afterDelay_.assert_not_called()
            controller.start_async_project_status_refresh.assert_called_once()
            self.assertIn('Review Control File Updates', controller.set_status.call_args.args[0])

    def test_successful_map_preparation_automatically_resumes_original_launch(self):
        request = dict(time_lapse=False, continue_previous=True, transient_resume_position={'row': 12})
        controller = SimpleNamespace(
            geolocations_mode='automatic_maps',
            automatic_map_generation_result=dict(created_count=3, failures=[]),
            pending_slideshow_after_map_generation=request,
            automatic_map_pending_imported_paths=[], automatic_map_review_control=False,
            _control_file_path=lambda: None, set_status=Mock(), _continue_adventure_processing=Mock(),
            start_async_project_status_refresh=Mock(), performSelector_withObject_afterDelay_=Mock(),
            _finish_geolocations_run=Mock(), _start_slide_show=Mock())
        GPXTrackerController.geoLocationsRunFinished_.callable(controller, 'success')
        controller.performSelector_withObject_afterDelay_.assert_called_once_with('resumeSlideShowAfterMaps:', None, 0.0)
        GPXTrackerController.resumeSlideShowAfterMaps_.callable(controller, None)
        controller._start_slide_show.assert_called_once_with(**request)
        self.assertIsNone(controller.pending_slideshow_after_map_generation)

    def test_applying_reference_review_resumes_waiting_slideshow(self):
        controller = SimpleNamespace(
            geolocations_mode='media_update_commit', control_file_update_result=None,
            _control_file_path=lambda: None, refresh_media_summary=Mock(), refresh_control_file_display=Mock(),
            _finish_geolocations_run=Mock(), start_async_project_status_refresh=Mock(),
            pending_slideshow_after_map_generation={'awaiting_review': True},
            performSelector_withObject_afterDelay_=Mock())
        GPXTrackerController.geoLocationsRunFinished_.callable(controller, 'success')
        controller.performSelector_withObject_afterDelay_.assert_called_once_with('resumeSlideShowAfterMaps:', None, 0.0)

    def test_partial_map_failure_retains_work_but_never_launches_or_retries_automatically(self):
        controller = SimpleNamespace(
            geolocations_mode='automatic_maps',
            automatic_map_generation_result=dict(created_count=2, failures=[{'filename': 'stage.png', 'error': 'offline'}]),
            pending_slideshow_after_map_generation={'time_lapse': True},
            automatic_map_pending_imported_paths=[], automatic_map_review_control=False,
            _control_file_path=lambda: None, set_status=Mock(), _continue_adventure_processing=Mock(),
            start_async_project_status_refresh=Mock(), performSelector_withObject_afterDelay_=Mock(),
            _finish_geolocations_run=Mock(), _show_scrollable_list=Mock())
        GPXTrackerController.geoLocationsRunFinished_.callable(controller, 'success')
        self.assertIsNone(controller.pending_slideshow_after_map_generation)
        controller.performSelector_withObject_afterDelay_.assert_not_called()
        self.assertIn('completed maps were retained', controller.set_status.call_args.args[0])

    def test_preparation_waits_for_fresh_status_before_refreshing_retained_review(self):
        review = {'phase': 'maps'}
        controller = SimpleNamespace(
            geolocations_mode='automatic_maps',
            automatic_map_generation_result=dict(created_count=3, failures=[]),
            pending_slideshow_after_map_generation={'continue_previous': True},
            media_review_map_preparation=review,
            automatic_map_pending_imported_paths=[], automatic_map_review_control=False,
            _control_file_path=lambda: None, set_status=Mock(), _continue_adventure_processing=Mock(),
            start_async_project_status_refresh=Mock(), performSelector_withObject_afterDelay_=Mock(),
            _finish_geolocations_run=Mock())
        GPXTrackerController.geoLocationsRunFinished_.callable(controller, 'success')
        self.assertEqual(review['phase'], 'waiting_status')
        self.assertTrue(controller.pending_slideshow_after_map_generation['awaiting_review'])
        controller.start_async_project_status_refresh.assert_called_once()
        controller.performSelector_withObject_afterDelay_.assert_not_called()

    def test_osm_preparation_opens_provider_setup_instead_of_manual_track_selection(self):
        controller = SimpleNamespace(
            parameters={'maps.output_provider': 'osm'},
            pending_slideshow_after_map_generation={'continue_previous': True},
            pending_map_provider_action=None, set_status=Mock())
        with patch('GPSTrackShowGUI.load_map_provider_preference', return_value={'preferred_output_provider': 'osm'}), \
             patch('GPSTrackShowGUI.run_map_provider_setup', return_value={'action': 'cancel'}) as setup:
            self.assertFalse(GPXTrackerController._ensure_automatic_map_provider(controller, ('automatic_maps', [])))
        self.assertEqual(setup.call_args.kwargs['preferred_provider'], 'geoapify')
        self.assertIsNone(controller.pending_slideshow_after_map_generation)


if __name__ == '__main__':
    unittest.main()
