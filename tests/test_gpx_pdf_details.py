"""Checkpoints, pauses, sidecar-only enrichment, and shared PDF settings."""

import copy
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from adventure_parameters import (
    EDITOR_PARAMETER_SECTIONS, default_parameters, normalize_parameters,
    parameter_payload, specs_for_section,
)
from gpx_pdf_details import (
    Checkpoint, PDFMediaIndex, break_candidates, break_duration, build_checkpoints,
    checkpoint_time, seconds_between, coordinate_place, compact_place_name, place_extra_detail,
)
from gpx_processing import ProcessingOptions, RawTrackPoint, process_raw_points
from plot_rendering import new_figure


START = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)


def processed(samples, *, spacing=0, threshold=1.5):
    points = []
    for index, sample in enumerate(samples):
        minutes, latitude, *rest = sample
        segment = rest[0] if rest else 0
        time = START + timedelta(minutes=minutes) if minutes is not None else None
        points.append(RawTrackPoint(index, segment, index, latitude, 7,
                                   elevation_m=100 + index * 10, time=time))
    return process_raw_points(points, ProcessingOptions(horizontal_smoothing_distance_m=0,
        minimum_point_spacing_m=spacing, elevation_smoothing_distance_m=0,
        stationary_speed_threshold_kmh=threshold))


class CheckpointTests(unittest.TestCase):
    def test_twenty_minute_default_detects_a_twenty_five_minute_break(self):
        track = processed([(0, 50), (30, 50.02), (55, 50.0201), (100, 50.06)])
        for values in (default_parameters(), {}):
            pauses = [marker for marker in build_checkpoints(track, values) if marker.break_end]
            self.assertEqual(len(pauses), 1)
            self.assertEqual(pauses[0].break_seconds, 25 * 60)
        custom = dict(default_parameters(), **{"pdf.break_duration_minutes": 30})
        self.assertFalse(any(marker.break_end for marker in build_checkpoints(track, custom)))

    def test_exactly_twenty_minutes_remains_the_strict_break_boundary(self):
        track = processed([(0, 50), (30, 50.02), (50, 50.02), (100, 50.06)])
        self.assertFalse(any(marker.break_end for marker in build_checkpoints(track, default_parameters())))

    def test_hourly_interpolation_uses_route_distance_and_keeps_endpoints(self):
        track = processed([(0, 50), (120, 50.1)])
        markers = build_checkpoints(track, default_parameters())
        self.assertEqual([item.kind for item in markers], ['start', 'regular', 'end'])
        self.assertEqual([item.number for item in markers], [1, 2, 3])
        self.assertAlmostEqual(markers[1].distance, track.length_km / 2)
        self.assertAlmostEqual(markers[1].latitude, 50.05)
        self.assertAlmostEqual(markers[1].elevation, 105)
        self.assertIsNone(markers[0].average_speed)

    def test_speeds_are_since_previous_checkpoint_not_since_start(self):
        track = processed([(0, 50), (60, 50.03), (120, 50.12)])
        markers = build_checkpoints(track, default_parameters())
        self.assertAlmostEqual(markers[2].average_speed, markers[1].average_speed * 3)
        self.assertAlmostEqual(markers[2].moving_speed, markers[2].average_speed)

    def test_long_tracks_are_bounded_and_endpoints_never_removed(self):
        track = processed([(index * 60, 50 + index * .02) for index in range(49)])
        for limit in (2, 5, 10):
            values = dict(default_parameters(), **{'pdf.maximum_markers': limit})
            markers = build_checkpoints(track, values)
            self.assertLessEqual(len(markers), limit)
            self.assertEqual(markers[0].position, 0)
            self.assertEqual(markers[-1].position, len(track.points) - 1)

    def test_untimed_and_single_point_tracks_do_not_invent_times(self):
        for samples in ([(None, 50)], [(None, 50), (None, 50.1)]):
            markers = build_checkpoints(processed(samples), default_parameters())
            self.assertEqual(len(markers), len(samples))
            self.assertTrue(all(item.timestamp is None for item in markers))
            self.assertTrue(all(item.average_speed is None for item in markers))

    def test_no_interpolation_or_pause_across_segments_or_missing_times(self):
        track = processed([(0, 50), (20, 50.01), (100, 50.01, 1), (120, 50.02, 1)])
        markers = build_checkpoints(track, default_parameters())
        self.assertEqual(len(markers), 2)
        self.assertFalse(break_candidates(track, 100, 1800))
        track = processed([(0, 50), (None, 50), (60, 50)])
        self.assertFalse(break_candidates(track, 100, 1800))

    def test_break_radius_duration_and_replacement(self):
        track = processed([(0, 50), (55, 50.02), (75, 50.0202), (90, 50.0201), (120, 50.05)])
        markers = build_checkpoints(track, default_parameters())
        pauses = [marker for marker in markers if marker.break_end]
        self.assertEqual(len(markers), 3)
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0].timestamp, START + timedelta(minutes=55))
        self.assertEqual(pauses[0].break_seconds, 35 * 60)
        self.assertFalse(break_candidates(track, 100, 35 * 60))
        self.assertFalse(break_candidates(track, 1, 30 * 60))

    def test_smoothing_does_not_erase_stationary_observations(self):
        track = processed([(0, 50), (55, 50.02), (75, 50.02), (90, 50.02), (120, 50.05)], spacing=100)
        pauses = break_candidates(track, 100, 1800)
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0].break_seconds, 35 * 60)

    def test_regular_markers_inside_an_accepted_break_are_removed(self):
        track = processed([(0, 50), (90, 50.03), (100, 50.03), (140, 50.03), (180, 50.08)])
        markers = build_checkpoints(track, default_parameters())
        pauses = [marker for marker in markers if marker.break_end]
        self.assertTrue(pauses)
        self.assertFalse(any(marker.kind == 'regular' and
            pause.timestamp <= marker.timestamp <= pause.break_end for marker in markers for pause in pauses))

    def test_quality_rejected_observations_cannot_establish_a_break(self):
        track = processed([(0, 50), (20, 50.03), (60, 50.03), (80, 50.05)])
        track.raw_points[2].horizontal_status = 'horizontal accuracy'
        self.assertFalse(break_candidates(track, 100, 1800))

    def test_break_scan_distance_work_scales_linearly(self):
        import gpx_pdf_details
        counts = []
        for count in (1000, 2000):
            track = processed([(index, 50 + index * .001) for index in range(count)])
            with patch('gpx_pdf_details.haversine_km', wraps=gpx_pdf_details.haversine_km) as distance:
                break_candidates(track, 100, 1800)
                counts.append(distance.call_count)
        self.assertLessEqual(counts[1], 2 * counts[0] + 2)

    def test_longest_break_wins_and_breaks_never_exceed_half(self):
        track = processed([(0, 50), (40, 50.02), (80, 50.02), (100, 50.05),
                           (155, 50.05), (180, 50.08), (225, 50.08), (260, 50.12)])
        parameters = dict(default_parameters(), **{'pdf.maximum_markers': 3})
        markers = build_checkpoints(track, parameters)
        pauses = [marker for marker in markers if marker.break_end]
        self.assertLessEqual(len(pauses), len(markers) // 2)
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0].break_seconds, 55 * 60)

    def test_analysis_does_not_modify_processed_data_or_statistics(self):
        track = processed([(0, 50), (60, 50.02), (120, 50.02), (180, 50.05)])
        before = copy.deepcopy(track)
        build_checkpoints(track, default_parameters())
        self.assertEqual(track, before)

    def test_reversed_times_do_not_form_a_break(self):
        track = processed([(60, 50), (30, 50), (0, 50)])
        self.assertFalse(break_candidates(track, 100, 1800))
        self.assertEqual(len(build_checkpoints(track, default_parameters())), 2)

    def test_day_offsets_durations_and_dst_use_elapsed_time(self):
        zone = ZoneInfo('Europe/Berlin')
        before = datetime(2026, 10, 25, 1, tzinfo=zone)
        after = datetime(2026, 10, 25, 4, tzinfo=zone)
        self.assertEqual(seconds_between(before, after), 4 * 3600)
        marker = Checkpoint(0, 50, 7, 100, 0, START + timedelta(days=1))
        self.assertIn('(+1d)', checkpoint_time(marker, START))
        self.assertEqual(break_duration(45 * 60), '45 min')
        self.assertEqual(break_duration(90 * 60), '1h 30m')
        self.assertEqual(break_duration(26 * 3600), '1d 2h')


class ExistingEnrichmentTests(unittest.TestCase):
    def payload(self):
        return dict(source_filename='image.jpeg', latitude=50, longitude=7, datetime_iso=START.isoformat(), place='Dijon',
                    weather=dict(schema_version=1, media_source=dict(latitude=50, longitude=7,
                    datetime_iso=START.isoformat()), values=dict(temperature_2m=18.4,
                    wind_speed_10m=12, precipitation=.8, relative_humidity_2m=72, pressure_msl=1014, cloud_cover=65, weather_code=3)))

    def test_existing_sidecars_are_read_once_and_enrichment_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.jpeg.json'
            path.write_text(json.dumps(self.payload()))
            original = path.read_bytes()
            with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
                index = PDFMediaIndex([directory])
                markers = [Checkpoint(0, 50, 7, 100, 0, START)]
                for _ in range(2):
                    enriched = index.enrich(markers, default_parameters())[0]
                    self.assertEqual(enriched.place, 'Dijon')
                    self.assertIn('18.4 °C', enriched.weather)
                    self.assertIn('1014 hPa', enriched.weather)
                    self.assertEqual(enriched.weather_code, 3)
                self.assertEqual(path.read_bytes(), original)

    def test_weather_does_not_leak_to_distant_or_different_times(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'image.jpeg.json').write_text(json.dumps(self.payload()))
            (Path(directory) / 'broken.jpeg.json').write_text('broken')
            index = PDFMediaIndex([directory])
            for marker in (Checkpoint(0, 50.02, 7, 100, 0, START),
                           Checkpoint(0, 50, 7, 100, 0, START + timedelta(minutes=31))):
                self.assertFalse(index.enrich([marker], default_parameters())[0].weather)

    def test_enrichment_options_and_cancellation(self):
        from gpx_pdf_summary import PDFExportCancelled
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'image.jpeg.json').write_text(json.dumps(self.payload()))
            index = PDFMediaIndex([directory])
            values = dict(default_parameters(), **{'pdf.checkpoint_weather': False, 'pdf.checkpoint_places': False})
            marker = index.enrich([Checkpoint(0, 50, 7, 100, 0, START)], values)[0]
            self.assertFalse(marker.weather or marker.place)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(PDFExportCancelled):
                PDFMediaIndex([directory], cancel=cancel)

    def test_control_and_directory_duplicates_read_one_valid_sidecar(self):
        import plot_metadata_utils
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'image.jpeg.json'
            path.write_text(json.dumps(self.payload()))
            control = root / 'trip.lst'
            control.write_text('image.jpeg\n./image.jpeg\n')
            with patch('plot_metadata_utils.validate_media_sidecar', wraps=plot_metadata_utils.validate_media_sidecar) as validate:
                PDFMediaIndex([root], control)
                self.assertEqual(validate.call_count, 1)

    def test_wrong_source_identity_is_not_used_for_place_or_weather(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = dict(self.payload(), source_filename='another.jpeg')
            (Path(directory) / 'image.jpeg.json').write_text(json.dumps(payload))
            marker = PDFMediaIndex([directory]).enrich([Checkpoint(0, 50, 7, 100, 0, START)], default_parameters())[0]
            self.assertEqual(marker.place, coordinate_place(marker))
            self.assertFalse(marker.weather)

    def test_missing_enrichment_has_coordinates_not_invented_weather(self):
        with tempfile.TemporaryDirectory() as directory:
            markers = build_checkpoints(processed([(0, 50), (180, 50.1)]), default_parameters())
            result = PDFMediaIndex([directory]).enrich(markers, default_parameters())
            self.assertEqual(len(result), len(markers))
            for marker in result:
                self.assertEqual(marker.place, coordinate_place(marker))
                self.assertFalse(marker.weather)

    def test_location_fallbacks_and_optional_street_detail(self):
        payload = {'place_details': {'locality': 'Dijon', 'name': 'Rue de la Chouette'}}
        self.assertEqual(compact_place_name(payload), 'Dijon')
        self.assertEqual(place_extra_detail(payload, 'Dijon'), 'Rue de la Chouette')
        for key, value in (('subLocality', 'Old Town'), ('thoroughfare', 'Chemin des Vignes'),
                           ('name', 'Chapelle Saint-Michel'), ('areasOfInterest', ['River crossing'])):
            self.assertEqual(compact_place_name({'place_details': {key: value}}), value[0] if isinstance(value, list) else value)


class SharedPDFSettingsTests(unittest.TestCase):
    def test_pdf_provider_setup_does_not_change_machine_slideshow_preference(self):
        import cocoa_map_provider_setup as setup
        with patch.object(setup, '_choose_provider', return_value='osm'), \
             patch.object(setup, 'save_map_provider_preference') as save:
            result = setup.run_map_provider_setup(remember_output_provider=False)
            self.assertEqual(result['provider'], 'osm')
            save.assert_not_called()
        with patch.object(setup, '_choose_provider', return_value='geoapify'), \
             patch.object(setup, 'read_provider_credential', return_value='test-key'), \
             patch.object(setup, 'configure_hosted_provider', return_value={'action': 'configured'}) as configure:
            setup.run_map_provider_setup(remember_output_provider=False)
            self.assertFalse(configure.call_args.kwargs['remember_output_provider'])

    def test_guided_provider_setup_changes_only_the_requested_choice(self):
        import GPXEditor
        from cocoa_adventure_map import AdventureMapController
        for key in ('maps.interactive_provider', 'maps.output_provider', 'pdf.map_provider'):
            values = dict(default_parameters(), **{'_provider_setting': key})
            with patch('GPXEditor.run_map_provider_setup', return_value={'action': 'configured', 'provider': 'geoapify'}) as setup:
                result = GPXEditor.GPXEditorController._manage_map_provider_from_settings(SimpleNamespace(), values)
                self.assertEqual(result, {key: 'geoapify'})
                self.assertEqual(setup.call_args.kwargs['remember_output_provider'], key == 'maps.output_provider')
            with patch('cocoa_adventure_map.run_map_provider_setup', return_value={'action': 'configured', 'provider': 'geoapify'}):
                result = AdventureMapController._manage_map_provider_from_settings(SimpleNamespace(), values)
                self.assertEqual(result, {key: 'geoapify'})

    def test_providers_live_only_in_map_service_and_round_trip(self):
        pdf = {spec.key for spec in specs_for_section('PDF Export', True)}
        service = {spec.key for spec in specs_for_section('Map Service', True)}
        self.assertNotIn('pdf.map_provider', pdf)
        self.assertNotIn('pdf.geoapify_style', pdf)
        self.assertIn('pdf.map_provider', service)
        self.assertIn('Map Generation', EDITOR_PARAMETER_SECTIONS)
        values = dict(default_parameters(), **{'pdf.map_provider': 'geoapify', 'pdf.geoapify_style': 'osm-carto'})
        self.assertEqual(normalize_parameters(parameter_payload(values)), values)

    def test_default_auto_preserves_explicit_legacy_label_choices(self):
        self.assertEqual(default_parameters()['trackmaps.overview_labels'], 'automatic')
        for old in ('none', 'default'):
            self.assertEqual(normalize_parameters({'trackmaps.overview_labels': old})['trackmaps.overview_labels'], old)

    def test_pdf_links_capture_unapplied_edits_before_changing_section(self):
        from cocoa_parameter_editor import CocoaParameterEditor
        editor = SimpleNamespace(_capture_controls=Mock(), show=Mock())
        for method, section in ((CocoaParameterEditor.pdfMapService_, 'Map Service'),
                                (CocoaParameterEditor.pdfMapFormatting_, 'Map Generation')):
            getattr(method, 'callable', method)(editor, None)
            self.assertEqual(editor.current_section, section)
        self.assertEqual(editor._capture_controls.call_count, 2)
