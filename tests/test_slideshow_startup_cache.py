"""Startup reuse retains the same freshness rules across application restarts."""

import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

from GPSTrackShowGUI import slideshow_dependency_issues
from gpx_tracks_table import prepare_with_options, execute_run_context, refresh_track_presentation_metadata
from map_overlay import MAP_CONTENT_VERSION
from plot_metadata_utils import read_plot_metadata
import slideshow_startup as startup


class PersistentStartupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.maps = self.root / 'trackimages'
        self.maps.mkdir()
        self.gpx = self.root / 'trip.gpx'
        self.gpx.write_text('<gpx version="1.1"><trk><name>Walk</name><trkseg>'
            '<trkpt lat="50.0" lon="7.0"><ele>100</ele><time>2026-11-04T10:00:00Z</time></trkpt>'
            '<trkpt lat="50.01" lon="7.01"><ele>120</ele><time>2026-11-04T11:00:00Z</time></trkpt>'
            '</trkseg></trk></gpx>')
        self.options = dict(output_dir=str(self.maps), output_base='trip', plot_overview=False,
            plot_tracks=None, nojson=False, pdf=False, gpx_running_speed_window_distance=100,
            gpx_stationary_speed_threshold=1, adventure_processing_parameters={'distance': 10})
        self.context = prepare_with_options(str(self.gpx), **self.options)
        execute_run_context(self.context, print_table_output=False)
        track = self.context['tracks'][0]
        count = int(track.get('segment_count', len(track.get('segments', []))))
        values = {key: track.get(key) for key in startup.PRESENTATION_KEYS}
        background = dict(background_only=True, map_content_version=MAP_CONTENT_VERSION,
            track_geometry_fingerprint=track['track_geometry_fingerprint'], track_number=1,
            overlay_track_types=[track.get('track_type', 'unassigned')] * count,
            overlay_hidden_tracks=[bool(track.get('hidden'))] * count,
            unrelated={'keep': [1, 2, 3]}, **values)
        self.standard = self.maps / track['track_plot_image_filename']
        self.timelapse = self.maps / track['track_plot_time_lapse_image_filename']
        for path in (self.standard, self.timelapse):
            path.write_bytes(b'modern map image')
            path.with_suffix('.json').write_text(json.dumps(background))
        self.overview = Path(self.context['overview_path'])
        self.overview.write_bytes(b'overview image')
        self.overview.with_suffix('.json').write_text(json.dumps(dict(
            background_only=True, map_content_version=MAP_CONTENT_VERSION,
            source_track_geometry_fingerprints=[track['track_geometry_fingerprint']],
            overlay_track_types=[track.get('track_type', 'unassigned')] * count,
            overlay_hidden_tracks=[bool(track.get('hidden'))] * count)))
        self.control = self.root / 'trip.lst'
        self.control.write_text(f'#Overviewmap: {self.overview.name}\n#Map: {self.standard.name}\nphoto.jpeg\n')
        self.hint = startup.prepared_track_hint(self.context, startup.file_signature(self.gpx))
        self.prepare = Mock(wraps=prepare_with_options)
        self.execute = Mock(wraps=execute_run_context)
        self.read = Mock(side_effect=read_plot_metadata)

    def validate(self, **kwargs):
        return startup.validate_startup(self.gpx, self.control, self.maps, self.options,
            previous_key=kwargs.pop('previous_key', None), extra_assets=(), prepare=self.prepare,
            execute=self.execute, check=slideshow_dependency_issues, read_metadata=self.read,
            upgrade=Mock(), progress=Mock(), **kwargs)

    def test_restart_reuses_successful_validation_without_metadata_reads_or_writes(self):
        first = self.validate(prepared_hint=self.hint)
        self.assertEqual(first['issues'], {'maps': [], 'derived': []})
        self.assertTrue(first['reused_loaded_tracks'])
        paths = [call.args[0] for call in self.read.call_args_list]
        self.assertEqual(len(paths), len(set(paths)))
        self.read.reset_mock()
        files = {p: startup.file_signature(p) for p in self.maps.rglob('*') if p.is_file()}
        second = self.validate()  # No memory key or Adventure hint after restart.
        self.assertTrue(second['reused'])
        self.read.assert_not_called()
        self.prepare.assert_not_called()
        self.execute.assert_not_called()
        self.assertEqual(files, {p: startup.file_signature(p) for p in files})
        self.assertIsNotNone(startup.reuse_prepared_tracks(self.gpx, self.maps, self.options))

    def test_old_successful_cache_repairs_stale_segment_styles_once_without_reprocessing(self):
        self.gpx.write_text(self.gpx.read_text().replace('<name>Walk</name>', '<name>Walk</name><type>pilgrimage</type>'))
        context = prepare_with_options(str(self.gpx), **self.options)
        execute_run_context(context, print_table_output=False)
        hint = startup.prepared_track_hint(context, startup.file_signature(self.gpx))
        for image in (self.standard, self.timelapse):
            path = image.with_suffix('.json')
            data = read_plot_metadata(path)
            data.update(track_type='pilgrimage', hidden=False,
                        overlay_track_types=['unassigned'], overlay_hidden_tracks=[True])
            path.write_text(json.dumps(data))
        images = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in (self.standard, self.timelapse, self.overview)}
        before = startup.validation_signature(self.gpx, self.control, self.maps, self.options, context=hint['context'])
        legacy_key = (*before[:-1], tuple(startup.ALGORITHM_VERSIONS))
        startup.save_prepared_track_hint(self.gpx, self.maps, self.options, hint, cache_key=legacy_key)
        first = self.validate(previous_key=legacy_key)
        self.assertFalse(first['reused'])
        self.assertTrue(first['reused_loaded_tracks'])
        self.assertEqual(first['issues'], {'maps': [], 'derived': []})
        self.prepare.assert_not_called()
        self.execute.assert_not_called()
        for image in (self.standard, self.timelapse):
            metadata = read_plot_metadata(image.with_suffix('.json'))
            self.assertEqual(metadata['overlay_track_types'], ['pilgrimage'])
            self.assertEqual(metadata['overlay_hidden_tracks'], [False])
        self.assertEqual(images, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in images})
        self.read.reset_mock()
        second = self.validate()
        self.assertTrue(second['reused'])
        self.read.assert_not_called()

    def test_deleted_implicit_timelapse_map_blocks_cached_launch(self):
        self.validate(prepared_hint=self.hint)
        self.timelapse.unlink()
        result = self.validate()
        self.assertIn(self.timelapse.name, result['issues']['maps'])
        self.assertIsNone(result['cache_key'])
        self.prepare.assert_not_called()

    def test_replaced_geometry_or_baked_background_is_rejected(self):
        self.validate(prepared_hint=self.hint)
        path = self.standard.with_suffix('.json')
        original = read_plot_metadata(path)
        for update in ({'track_geometry_fingerprint': 'older geometry'}, {'background_only': False},
                       {'map_content_version': MAP_CONTENT_VERSION - 1}):
            path.write_text(json.dumps(dict(original, **update)))
            result = self.validate()
            self.assertIn(self.standard.name, result['issues']['maps'])
            self.assertIsNone(result['cache_key'])
        self.prepare.assert_not_called()

    def test_stale_canonical_data_is_repaired_not_hidden_by_current_map_data(self):
        self.validate(prepared_hint=self.hint)
        path = self.maps / self.context['tracks'][0]['track_data_sidecar']
        data = read_plot_metadata(path)
        data['track_data_fingerprint'] = 'old track'
        path.write_text(json.dumps(data))
        result = self.validate()
        self.prepare.assert_called_once()
        self.execute.assert_called_once()
        self.assertEqual(result['issues'], {'maps': [], 'derived': []})
        self.assertEqual(read_plot_metadata(path)['track_data_fingerprint'],
                         self.context['tracks'][0]['track_data_fingerprint'])

    def test_stale_speed_settings_rebuild_canonical_data(self):
        self.validate(prepared_hint=self.hint)
        path = self.maps / self.context['tracks'][0]['track_data_sidecar']
        data = read_plot_metadata(path)
        data['running_speed']['window_distance_m'] = 999
        path.write_text(json.dumps(data))
        result = self.validate()
        self.assertIsNotNone(result['cache_key'])
        self.prepare.assert_called_once()
        self.assertEqual(read_plot_metadata(path)['running_speed']['window_distance_m'], 100)

    def test_gpx_geometry_change_preserving_mtime_invalidates_hint_and_old_maps(self):
        self.validate(prepared_hint=self.hint)
        stat = self.gpx.stat()
        self.gpx.write_text(self.gpx.read_text().replace('lat="50.01"', 'lat="50.02"'))
        os.utime(self.gpx, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        result = self.validate()
        self.prepare.assert_called_once()
        self.assertIn(self.standard.name, result['issues']['maps'])
        self.assertIn(self.timelapse.name, result['issues']['maps'])
        self.assertIn(self.overview.name, result['issues']['maps'])
        self.assertIsNone(result['cache_key'])

    def test_timing_only_edit_refreshes_data_but_preserves_map_backgrounds(self):
        self.validate(prepared_hint=self.hint)
        maps = [self.standard, self.timelapse, self.overview]
        before = [startup.file_signature(path) for path in maps]
        self.gpx.write_text(self.gpx.read_text().replace('T10:00:00Z', 'T10:05:00Z'))
        result = self.validate()
        self.assertEqual(result['issues'], {'maps': [], 'derived': []})
        self.prepare.assert_called_once()
        self.assertEqual(before, [startup.file_signature(path) for path in maps])

    def test_control_edits_do_not_reprocess_tracks(self):
        self.validate(prepared_hint=self.hint)
        self.control.write_text(self.control.read_text() + 'another.jpeg\n')
        result = self.validate()
        self.assertFalse(result['reused'])
        self.assertTrue(result['reused_loaded_tracks'])
        self.prepare.assert_not_called()

    def test_unrelated_artifacts_and_render_preferences_do_not_invalidate(self):
        first = self.validate(prepared_hint=self.hint)
        (self.maps / 'profile-cache.json').write_text('{}')
        (self.maps / 'unused.png').write_bytes(b'export')
        self.options.update(map_provider='geoapify', title_color='green', zoom=15)
        self.assertTrue(self.validate(previous_key=first['cache_key'])['reused'])
        self.prepare.assert_not_called()

    def test_processing_settings_change_requires_preparation(self):
        self.validate(prepared_hint=self.hint)
        self.options['gpx_running_speed_window_distance'] = 200
        self.options['adventure_processing_parameters'] = {'distance': 20}
        self.validate()
        self.prepare.assert_called_once()

    def test_direct_processing_option_change_cannot_reuse_an_adventure_hint(self):
        self.validate(prepared_hint=self.hint)
        self.options['gpx_horizontal_smoothing_distance'] = 50
        self.validate(prepared_hint=self.hint)
        self.prepare.assert_called_once()

    def test_deletion_after_initial_validation_is_caught_before_success(self):
        original = refresh_track_presentation_metadata
        def delete_map(*args, **kwargs):
            original(*args, **kwargs)
            self.timelapse.unlink()
        with patch('gpx_tracks_table.refresh_track_presentation_metadata', side_effect=delete_map):
            result = self.validate(prepared_hint=self.hint)
        self.assertIn(self.timelapse.name, result['issues']['maps'])
        self.assertIsNone(result['cache_key'])

    def test_changed_external_map_is_a_required_dependency(self):
        external = self.root / 'elsewhere'
        external.mkdir()
        path = external / self.standard.name
        for name in (self.standard, self.timelapse):
            (external / name.name).write_bytes(name.read_bytes())
            (external / name.with_suffix('.json').name).write_bytes(name.with_suffix('.json').read_bytes())
        self.control.write_text(f'#Overviewmap: {self.overview.name}\n#Map: {path}\n')
        self.validate(prepared_hint=self.hint)
        (external / self.timelapse.name).unlink()
        result = self.validate()
        self.assertIn(self.timelapse.name, result['issues']['maps'])
        self.assertIsNone(result['cache_key'])

    def test_corrupt_old_or_unwritable_cache_falls_back_safely(self):
        self.validate(prepared_hint=self.hint)
        cache = startup._cache_path(self.gpx, self.maps, self.options)
        for text in ('broken JSON', 'null', '[]', '{"version": -1}',
                     '{"version": 2, "algorithms": [], "prepared_hint": []}'):
            cache.write_text(text)
            result = self.validate(prepared_hint=self.hint)
            self.assertFalse(result['reused'])
            self.assertEqual(result['issues'], {'maps': [], 'derived': []})
        cache.unlink()
        with patch.object(startup, 'atomic_write_json', side_effect=PermissionError('read only')):
            result = self.validate(prepared_hint=self.hint)
        self.assertIsNotNone(result['cache_key'])
        self.assertFalse(cache.exists())

    def test_existing_background_preparation_is_joined(self):
        future = Future()
        future.set_result({'slideshow_prepared_hint': self.hint})
        result = self.validate(prepared_future=future)
        self.assertTrue(result['reused_loaded_tracks'])
        self.assertIn('Shared track preparation', result['timings'])
        self.prepare.assert_not_called()

    def test_failed_background_preparation_falls_back_to_required_checks(self):
        future = Future()
        future.set_exception(RuntimeError('background failed'))
        self.validate(prepared_future=future)
        self.prepare.assert_called_once()

    def test_stale_background_hint_is_not_adopted(self):
        future = Future()
        future.set_result({'slideshow_prepared_hint': self.hint})
        self.gpx.write_text(self.gpx.read_text().replace('lat="50.01"', 'lat="50.02"'))
        result = self.validate(prepared_future=future)
        self.prepare.assert_called_once()
        self.assertIn(self.standard.name, result['issues']['maps'])
        self.assertIsNone(result['cache_key'])

    def test_control_dependency_parser_keeps_spacing_and_hidden_semantics(self):
        paths = startup.referenced_control_map_assets(
            '#Map : visible.png\n# #Map: hidden.png\n#MediaMap: media.png\n', self.maps)
        self.assertEqual([path.name for path in paths], ['visible.png', 'visible-timelapse.png', 'media.png'])

    def test_metadata_changed_while_decoding_cannot_be_cached(self):
        def read(path):
            data = read_plot_metadata(path)
            Path(path).write_text(json.dumps(dict(data, changed=True)))
            return data
        self.read.side_effect = read
        with self.assertRaisesRegex(RuntimeError, 'changed while being read'):
            self.validate(prepared_hint=self.hint)
        self.assertFalse(startup._cache_path(self.gpx, self.maps, self.options).exists())

    def test_projection_is_bounded_and_presentation_patch_preserves_full_payload(self):
        path = self.standard.with_suffix('.json')
        data = read_plot_metadata(path)
        data.update(track_type='unassigned', track_points=[[1, 2], [3, 4]],
                    timed_track_points=[{'extra': 'keep'}])
        path.write_text(json.dumps(data))
        reader = startup.StartupMetadataReader(read_plot_metadata)
        projected = reader(path)
        self.assertNotIn('track_points', projected)
        self.assertEqual(projected['timed_track_points'], [])
        changed_track = dict(self.context['tracks'][0], track_type='bike')
        refresh_track_presentation_metadata(dict(self.context, tracks=[changed_track]), read_metadata=reader)
        updated = read_plot_metadata(path)
        self.assertEqual(updated['track_points'], data['track_points'])
        self.assertEqual(updated['unrelated'], data['unrelated'])
        self.assertEqual(updated['timed_track_points'], data['timed_track_points'])
        self.assertEqual(updated['track_type'], 'bike')

    def test_unchanged_execute_does_not_rewrite_derived_or_summary(self):
        files = [Path(self.context['table_json_path']),
                 self.maps / self.context['tracks'][0]['track_data_sidecar']]
        before = [startup.file_signature(path) for path in files]
        execute_run_context(self.context, print_table_output=False)
        self.assertEqual(before, [startup.file_signature(path) for path in files])

    def test_editing_one_track_does_not_replace_unchanged_derived_sidecars(self):
        extra = ('<trk><name>Other</name><trkseg>'
            '<trkpt lat="51.0" lon="8.0"><time>2026-11-05T10:00:00Z</time></trkpt>'
            '<trkpt lat="51.01" lon="8.01"><time>2026-11-05T11:00:00Z</time></trkpt>'
            '</trkseg></trk>')
        self.gpx.write_text(self.gpx.read_text().replace('</gpx>', extra + '</gpx>'))
        context = prepare_with_options(str(self.gpx), **self.options)
        execute_run_context(context, print_table_output=False)
        first, second = [self.maps / track['track_data_sidecar'] for track in context['tracks']]
        before_first, before_second = startup.file_signature(first), startup.file_signature(second)
        self.gpx.write_text(self.gpx.read_text().replace('2026-11-04T10:00:00Z', '2026-11-04T10:05:00Z'))
        changed = prepare_with_options(str(self.gpx), **self.options)
        execute_run_context(changed, print_table_output=False)
        self.assertNotEqual(before_first, startup.file_signature(first))
        self.assertEqual(before_second, startup.file_signature(second))


if __name__ == '__main__':
    unittest.main()
