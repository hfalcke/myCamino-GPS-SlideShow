"""Report-specific enrichment must not mutate media or bypass consent."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adventure_parameters import default_parameters
from gpx_pdf_details import Checkpoint, PDFMediaIndex, coordinate_place
from gpx_pdf_enrichment import CheckpointSidecar, checkpoint_key, enrich_checkpoints
from gpx_pdf_summary import PDFExportCancelled
from historical_weather import weather_is_current
import test_gpx_pdf_details as detail_tests
from test_gpx_pdf_details import START
from test_historical_weather import _response


class CheckpointEnrichmentTests(unittest.TestCase):
    def markers(self):
        return (Checkpoint(0, 50, 7, 100, 0, START), Checkpoint(1, 50.1, 7, 200, 10, START))

    def parameters(self, **updates):
        result = dict(default_parameters(), **{'weather.enabled': True})
        result.update(updates)
        return result

    def resolver(self, *args, **kwargs):
        return 'Dijon', {'locality': 'Dijon', 'name': 'Rue de la Chouette'}, {'requested': True}

    def request(self, url, timeout):
        count = len(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['latitude'][0].split(','))
        return _response(START.timestamp(), count)

    def test_one_km_reuse_does_not_change_media_freshness_or_time_window(self):
        payload = detail_tests.ExistingEnrichmentTests().payload()
        index = PDFMediaIndex(checkpoint_payloads=[payload])
        near = replace(self.markers()[0], latitude=50.008)
        far = replace(near, latitude=50.0091)
        late = replace(near, timestamp=START + timedelta(minutes=30, seconds=1))
        first, second, third = index.enrich((near, far, late), self.parameters())
        self.assertEqual(first.place, 'Dijon')
        self.assertTrue(first.weather)
        self.assertEqual(second.place, coordinate_place(second))
        self.assertFalse(second.weather or third.weather)
        self.assertFalse(weather_is_current(dict(payload, latitude=near.latitude)))
        for minutes in (15, 30):
            timed = replace(near, timestamp=START + timedelta(minutes=minutes))
            self.assertTrue(index.enrich((timed,), self.parameters())[0].weather)
            self.assertFalse(weather_is_current(dict(payload, datetime_iso=timed.timestamp.isoformat())))

    def test_thirty_minute_group_samples_each_checkpoint_time(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.markers()[0]
            second = replace(first, timestamp=START + timedelta(minutes=30))
            request = Mock(side_effect=self.request)
            result = enrich_checkpoints((first, second), PDFMediaIndex(), self.parameters(),
                Path(directory) / 'cache.json', resolve_place=self.resolver, request_json=request)
            request.assert_called_once()
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
            self.assertEqual(len(query['latitude'][0].split(',')), 1)
            self.assertIn('10.0 °C', result[0].weather)
            self.assertIn('12.0 °C', result[1].weather)

    def test_reuse_does_not_chain_past_original_lookup_coordinate(self):
        payload = detail_tests.ExistingEnrichmentTests().payload()
        payload.update(latitude=50.008, place_coordinate={'latitude': 50, 'longitude': 7})
        payload['weather']['media_source']['latitude'] = 50.008
        payload['weather']['query'] = {'latitude': 50, 'longitude': 7}
        marker = replace(self.markers()[0], latitude=50.016)
        result = PDFMediaIndex(checkpoint_payloads=[payload]).enrich([marker], self.parameters())[0]
        self.assertEqual(result.place, coordinate_place(marker))
        self.assertFalse(result.weather)

    def test_fill_batches_weather_and_writes_only_new_sidecar_then_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {'route.gpx': b'<gpx/>', 'photo.jpeg': b'photo', 'photo.jpeg.json': b'{"personal":"unchanged"}'}
            for name, content in original.items():
                (root / name).write_bytes(content)
            path = root / 'Report-checkpoints.json'
            resolver, request = Mock(side_effect=self.resolver), Mock(side_effect=self.request)
            result = enrich_checkpoints(self.markers(), PDFMediaIndex(), self.parameters(), path,
                                        resolve_place=resolver, request_json=request)
            self.assertEqual(resolver.call_count, 2)
            request.assert_called_once()
            for marker in result:
                self.assertEqual(marker.place, 'Dijon')
                self.assertEqual(marker.place_detail, 'Rue de la Chouette')
                self.assertTrue(marker.weather)
            kwargs = resolver.call_args.kwargs
            self.assertEqual((kwargs['geocode_pacing_min_seconds'], kwargs['geocode_pacing_max_seconds']), (1, 5))
            self.assertFalse(kwargs['wait_for_network'])
            stored = json.loads(path.read_text())
            self.assertEqual(len(stored['records']), 2)
            for record in stored['records'].values():
                self.assertEqual(record['weather']['license'], 'CC BY 4.0')
                self.assertTrue(weather_is_current(record))
            unexpected = Mock(side_effect=AssertionError('unnecessary request'))
            again = enrich_checkpoints(self.markers(), PDFMediaIndex(), self.parameters(), path,
                                       resolve_place=unexpected, request_json=unexpected, weather_key=unexpected)
            self.assertEqual(result, again)
            unexpected.assert_not_called()
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            self.assertEqual(set(item.name for item in root.iterdir()), set(original) | {path.name})

    def test_warm_cache_never_accesses_customer_keychain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            marker = self.markers()[0]
            enrich_checkpoints([marker], PDFMediaIndex(), self.parameters(), path,
                              resolve_place=self.resolver, request_json=self.request)
            key = Mock(side_effect=AssertionError('credentials not needed for cached weather'))
            result = enrich_checkpoints([marker], PDFMediaIndex(), self.parameters(**{'weather.access': 'customer'}), path,
                                        weather_key=key)
            self.assertTrue(result[0].weather)
            key.assert_not_called()

    def test_changed_time_gets_new_weather_but_retains_place_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report-checkpoints.json'
            marker = self.markers()[0]
            enrich_checkpoints([marker], PDFMediaIndex(), self.parameters(), path,
                              resolve_place=self.resolver, request_json=self.request)
            stored = json.loads(path.read_text())
            stored['custom'] = 'preserve'
            stored['records'][checkpoint_key(marker)]['extra'] = {'also': 'preserve'}
            path.write_text(json.dumps(stored))
            changed = replace(marker, timestamp=START + timedelta(hours=1))
            resolver = Mock(side_effect=AssertionError('place should be reused'))
            request = Mock(side_effect=self.request)
            result = enrich_checkpoints([changed], PDFMediaIndex(), self.parameters(), path,
                                        resolve_place=resolver, request_json=request)
            request.assert_called_once()
            resolver.assert_not_called()
            self.assertTrue(result[0].weather)
            stored = json.loads(path.read_text())
            self.assertEqual(stored['custom'], 'preserve')
            self.assertEqual(stored['records'][checkpoint_key(marker)]['extra'], {'also': 'preserve'})
            self.assertEqual(len(stored['records']), 2)

    def test_disabled_weather_does_not_retrieve_or_request_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Mock(side_effect=AssertionError('weather disabled'))
            result = enrich_checkpoints([self.markers()[0]], PDFMediaIndex(), self.parameters(**{'weather.enabled': False}),
                Path(directory) / 'cache.json', resolve_place=self.resolver, request_json=request)
            self.assertEqual(result[0].place, 'Dijon')
            self.assertFalse(result[0].weather)
            request.assert_not_called()

    def test_cache_only_and_offline_make_no_requests(self):
        for offline, retrieve in ((False, False), (True, True)):
            with self.subTest(offline=offline), tempfile.TemporaryDirectory() as directory:
                request = Mock(side_effect=AssertionError('no requests allowed'))
                path = Path(directory) / 'cache.json'
                with patch('gpx_pdf_enrichment.network_availability', SimpleNamespace(offline=offline)):
                    result = enrich_checkpoints(self.markers(), PDFMediaIndex(),
                        self.parameters(**{'pdf.retrieve_checkpoint_data': retrieve}), path,
                        resolve_place=request, request_json=request)
                request.assert_not_called()
                self.assertFalse(path.exists())
                self.assertTrue(all(item.place == coordinate_place(item) for item in result))

    def test_cancellation_preserves_completed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            cancel = threading.Event()
            path = Path(directory) / 'cache.json'

            def progress(message):
                if 'place 2 of' in message:
                    cancel.set()

            with self.assertRaises(PDFExportCancelled):
                enrich_checkpoints(self.markers(), PDFMediaIndex(), self.parameters(), path,
                    resolve_place=self.resolver, request_json=self.request, cancel=cancel, progress=progress)
            records = json.loads(path.read_text())['records']
            self.assertEqual(list(records), [checkpoint_key(self.markers()[0])])
            self.assertEqual(next(iter(records.values()))['place'], 'Dijon')

    def test_future_sidecar_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            original = '{"schema_version":999,"kind":"future","records":{}}'
            path.write_text(original)
            request = Mock(side_effect=AssertionError('unknown cache'))
            enrich_checkpoints(self.markers(), PDFMediaIndex(), self.parameters(), path,
                               resolve_place=request, request_json=request)
            request.assert_not_called()
            self.assertEqual(path.read_text(), original)

    def test_customer_without_key_keeps_completed_places_and_does_not_request(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Mock(side_effect=AssertionError('missing credentials'))
            result = enrich_checkpoints([self.markers()[0]], PDFMediaIndex(),
                self.parameters(**{'weather.access': 'customer'}), Path(directory) / 'cache.json',
                resolve_place=self.resolver, request_json=request)
            request.assert_not_called()
            self.assertEqual(result[0].place, 'Dijon')
            self.assertFalse(result[0].weather)

    def test_customer_secret_is_not_stored_in_checkpoint_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            request = Mock(side_effect=self.request)
            secret = 'private-test-credential'
            result = enrich_checkpoints([self.markers()[0]], PDFMediaIndex(),
                self.parameters(**{'weather.access': 'customer'}), path, resolve_place=self.resolver,
                request_json=request, weather_key=lambda: secret)
            self.assertTrue(result[0].weather)
            self.assertNotIn(secret, path.read_text())
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
            self.assertEqual(query['apikey'], [secret])

    def test_failed_weather_does_not_lose_places(self):
        with tempfile.TemporaryDirectory() as directory:
            error = urllib.error.HTTPError('https://test.invalid', 404, 'unavailable', {}, None)
            result = enrich_checkpoints([self.markers()[0]], PDFMediaIndex(), self.parameters(),
                Path(directory) / 'cache.json', resolve_place=self.resolver, request_json=Mock(side_effect=error))
            self.assertEqual(result[0].place, 'Dijon')
            self.assertFalse(result[0].weather)

    def test_rate_limit_preserves_places_and_explains_pdf_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            def limited(url, timeout):
                raise urllib.error.HTTPError(url, 429, 'rate limited', {}, None)

            request = Mock(side_effect=limited)
            messages = []
            with patch('historical_weather._cancelable_wait'):
                result = enrich_checkpoints([self.markers()[0]], PDFMediaIndex(), self.parameters(),
                    Path(directory) / 'cache.json', resolve_place=self.resolver, request_json=request, progress=messages.append)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(result[0].place, 'Dijon')
            self.assertFalse(result[0].weather)
            self.assertTrue(any('export the PDF again' in message for message in messages))

    def test_preparation_happens_before_plotting_lock(self):
        from GPXEditor import export_prepared_summary_pdf
        from plot_rendering import PLOT_RENDER_LOCK
        renderer = Mock()
        renderer.prepare_checkpoint_data.side_effect = lambda: self.assertFalse(PLOT_RENDER_LOCK._is_owned())
        with patch('GPXEditor.export_summary_pdf', return_value='done') as export:
            result = export_prepared_summary_pdf('report.pdf', object(), renderer, threading.Event(), Mock())
        self.assertEqual(result, 'done')
        renderer.prepare_checkpoint_data.assert_called_once()
        export.assert_called_once()

    def test_renderer_installs_enriched_checkpoint_cache(self):
        from test_plot_rendering import map_renderer
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.checkpoint_sidecar_path = Path(directory) / 'cache.json'
            with patch('GPXEditor.enrich_checkpoints', side_effect=lambda markers, *args, **kwargs:
                       tuple(replace(marker, place='Updated') for marker in markers)) as enrich:
                renderer.prepare_checkpoint_data()
            enrich.assert_called_once()
            self.assertTrue(all(marker.place == 'Updated' for marker in renderer.checkpoints_for_track(renderer.tracks[0])))

    def test_atomic_failure_keeps_previous_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            marker = self.markers()[0]
            store = CheckpointSidecar(path)
            store.update(marker, {'place': 'Original'})
            original = path.read_bytes()
            with patch('json_storage.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.update(marker, {'place': 'Changed'})
                    store.flush()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


class CheckpointGeocoderCancellationTests(unittest.TestCase):
    def test_explicit_cancel_event_is_independent_of_media_job(self):
        import GetGeoLocations as geo
        other_job = threading.Event()
        other_job.set()
        with patch.object(geo, 'RUNTIME_CANCEL_EVENT', other_job):
            geo.check_cancelled(threading.Event())
            with self.assertRaises(geo.ProcessingCancelled):
                geo.check_cancelled()

    def test_inflight_geocoder_is_cancelled_without_waiting_for_timeout(self):
        import GetGeoLocations as geo
        cancel = threading.Event()
        geocoder = Mock()
        run_loop = Mock()
        run_loop.currentRunLoop.return_value.runUntilDate_.side_effect = lambda _date: cancel.set()
        network = SimpleNamespace(offline=False, wait_until_available=lambda event: True)
        with patch.object(geo, 'CORELOCATION_AVAILABLE', True), \
             patch.object(geo, 'CLLocation', Mock()), \
             patch.object(geo, 'CLGeocoder', Mock(alloc=Mock(return_value=Mock(init=Mock(return_value=geocoder))))), \
             patch.object(geo, 'NSRunLoop', run_loop), \
             patch.object(geo, 'NSDate', Mock()), \
             patch('network_availability.network_availability', network):
            with self.assertRaises(geo.ProcessingCancelled):
                geo.reverse_geocode_location_details_with_debug(50, 7, cancel_event=cancel)
        geocoder.cancelGeocode.assert_called_once()

    def test_coordinate_service_preserves_configured_random_delay(self):
        import GetGeoLocations as geo
        cancel = threading.Event()
        with patch.object(geo, 'reverse_geocode_location_details_with_debug', return_value=('Dijon', {}, {})) as request, \
             patch.object(geo.random, 'uniform', return_value=3) as jitter, \
             patch.object(geo, 'sleep_with_cancel') as sleep:
            result = geo.resolve_place_for_coordinate(50, 7, {}, [], 1000, True,
                geocode_pacing_min_seconds=1, geocode_pacing_max_seconds=5,
                cancel_event=cancel, wait_for_network=False)
        self.assertEqual(result[0], 'Dijon')
        jitter.assert_called_once_with(1.0, 5.0)
        sleep.assert_called_once_with(3, cancel)
        self.assertFalse(request.call_args.kwargs['wait_for_network'])
