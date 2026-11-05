"""Track-name prefixes are presentation-only, never asset-naming inputs."""

import copy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GPSTrackShow as show
from gpx_pdf_summary import report_name
from track_name_utils import display_track_name


class TrackNamePrefixTests(unittest.TestCase):
    def test_exact_prefix_is_removed_once_not_from_middle_or_different_case(self):
        for formatter in (display_track_name, report_name):
            self.assertEqual(formatter('  JW JW Santiago', 'JW '), 'JW Santiago')
            self.assertEqual(formatter('JWM Santiago', 'JW '), 'JWM Santiago')
            self.assertEqual(formatter('Before JW Santiago', 'JW '), 'Before JW Santiago')
            self.assertEqual(formatter('jw Santiago', 'JW '), 'jw Santiago')
            self.assertEqual(formatter('  Santiago', ''), 'Santiago')

    def test_shared_stage_headers_and_endpoint_fallback_use_prefix_without_mutating_metadata(self):
        metadata = {'track_name': '  JW Santiago', 'track_length_km': 12.4, 'track_duration': '3:00'}
        original = copy.deepcopy(metadata)
        config = SimpleNamespace(track_title_mode='track_name', track_name_prefix='JW ')
        introductory = show.dynamic_stage_header_lines(metadata, config)
        media = show.selected_stage_header_lines(metadata, config)
        self.assertEqual(introductory[0], 'Santiago')
        self.assertEqual(media[0], 'Santiago')
        self.assertEqual(show.track_display_title(metadata, 'endpoint_places', 'JW '), 'Santiago')
        self.assertEqual(metadata, original)
        self.assertEqual(show.track_display_title({'track_name': 'JW'}, 'track_name', 'JW'), 'Unnamed')

    def test_real_endpoint_places_are_not_prefix_stripped(self):
        metadata = {'track_name': 'JW Santiago',
                    'track_endpoint_places': {'start': {'place': 'JW Town'}, 'end': {'place': 'Santiago'}}}
        self.assertEqual(show.track_display_title(metadata, 'endpoint_places', 'JW '), 'JW Town - Santiago')

    def test_python_and_cli_options(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'trip.lst'
            control.write_text('photo.jpeg\n')
            config = show.config_from_options(directory, inputlist=control, track_name_prefix='JW ')
            self.assertEqual(config.track_name_prefix, 'JW ')
            with patch.object(show, 'available_screen_count', return_value=1):
                config = show.parse_args([directory, '--inputlist', str(control), '--remove-track-name-prefix', 'JW '])
            self.assertEqual(config.track_name_prefix, 'JW ')

    def test_live_setting_updates_title_without_changing_metadata(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.config = SimpleNamespace(track_title_mode='track_name', track_name_prefix='')
        app.photo_presenter = app.map_presenter = None
        app._refresh_photo_overlays = Mock()
        app._show_temporary_status_overlay = Mock()
        app._apply_runtime_settings({'slideshow.remove_name_prefix': 'JW '})
        self.assertEqual(app.config.track_name_prefix, 'JW ')

    def test_display_prefix_changes_preserve_map_and_processing_signatures(self):
        from adventure_parameters import default_parameters
        from GPSTrackShowGUI import GPXTrackerController

        before = default_parameters()
        before['trackmaps.remove_name_prefix'] = 'JW'
        after = dict(before, **{'slideshow.remove_name_prefix': 'JWM ', 'pdf.remove_name_prefix': 'Other '})
        first = GPXTrackerController._track_summary_parameter_signature(SimpleNamespace(parameters=before))
        second = GPXTrackerController._track_summary_parameter_signature(SimpleNamespace(parameters=after))
        self.assertEqual(first, second)
        self.assertEqual(before['trackmaps.remove_name_prefix'], after['trackmaps.remove_name_prefix'])

    def test_pdf_detached_names_and_overview_labels_strip_only_once(self):
        from GPXEditor import PDFMapRenderer, qname
        from test_plot_rendering import map_renderer
        from plot_rendering import new_figure
        import threading

        with tempfile.TemporaryDirectory() as directory:
            source, _ = map_renderer(directory)
            source.tracks[0].element.find(qname('name')).text = 'JW JW Santiago'
            source.project_parameters['trackmaps.remove_name_prefix'] = 'JW'
            source.project_parameters['pdf.remove_name_prefix'] = 'unused global default'
            options = replace(source.options, name_prefix='JW ')
            renderer = PDFMapRenderer(source, source.tracks, options, threading.Event(), Mock())
            self.assertEqual(renderer.tracks[0].name, 'JW Santiago')
            self.assertEqual(source.tracks[0].name, 'JW JW Santiago')
            renderer.project_parameters['trackmaps.overview_labels'] = 'default'
            figure = new_figure(figsize=(6, 4))
            axis = figure.add_subplot()
            renderer.draw_pdf_overview_labels(axis, renderer.tracks)
            self.assertTrue(any(text.get_text().startswith('JW Santiago\n') for text in axis.texts))
            figure.clear()


if __name__ == '__main__':
    unittest.main()
