"""Renderer-scale overview density and printable checkpoint-panel layout."""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adventure_parameters import default_parameters
from gpx_pdf_details import build_checkpoints, checkpoint_time, compact_place_name, compact_weather_text, coordinate_place
from gpx_pdf_layout import (
    continuation_figures, draw_checkpoint_markers, draw_panel, measure_panel, track_statistics,
)
from map_label_layout import automatic_labels_visible
from plot_rendering import new_figure
from test_gpx_pdf_details import START, processed
from test_plot_rendering import fake_tiles, map_renderer


class LabelDensityTests(unittest.TestCase):
    def test_threshold_uses_actual_text_width_in_display_pixels(self):
        figure = new_figure(figsize=(8, 4), dpi=100)
        ax = figure.add_axes([.1, .1, .8, .8], xlim=(0, 1), ylim=(0, 1))
        text = 'Stage with a real label'
        renderer = figure.canvas.get_renderer()
        from matplotlib.font_manager import FontProperties
        label_width = renderer.get_text_width_height_descent(text, FontProperties(size=10), False)[0] + 8 * figure.dpi / 72
        required = label_width / .7 / ax.get_window_extent().width
        self.assertFalse(automatic_labels_visible(ax, [text], [[(0, .5), (required * .99, .5)]], 10))
        self.assertTrue(automatic_labels_visible(ax, [text], [[(0, .5), (required * 1.01, .5)]], 10))

    def test_closed_routes_have_length_and_segment_gaps_do_not(self):
        figure = new_figure(figsize=(6, 4))
        ax = figure.add_axes([.1, .1, .8, .8], xlim=(0, 1), ylim=(0, 1))
        self.assertTrue(automatic_labels_visible(ax, ['Closed route'], [[(.1, .1), (.9, .1), (.9, .9), (.1, .1)]], 10))
        self.assertFalse(automatic_labels_visible(ax, ['Must not bridge segments'], [[(.1, .1)], [(.9, .9)]], 10))
        self.assertFalse(automatic_labels_visible(ax, ['Outside'], [[(2, 2), (10, 10)]], 10))

    def test_pdf_labels_follow_saved_choice_not_interactive_number_state(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.overview_numbers = True
            figure = new_figure(figsize=(8, 4))
            ax = figure.add_axes([.1, .1, .8, .8])
            renderer.project_parameters['trackmaps.overview_labels'] = 'none'
            renderer.draw_pdf_overview_labels(ax, renderer.tracks)
            self.assertFalse(ax.texts)
            renderer.project_parameters['trackmaps.overview_labels'] = 'default'
            renderer.draw_pdf_overview_labels(ax, renderer.tracks)
            self.assertTrue(any('Attribution regression' in text.get_text() for text in ax.texts))

    def test_generated_overview_auto_controls_both_png_and_dynamic_metadata(self):
        from gpx_tracks_table import parse_label_text, render_track_plot
        self.assertEqual(parse_label_text('automatic'), [['AUTOMATIC']])
        with tempfile.TemporaryDirectory() as directory:
            for name, expected in [('Short', True), ('Very long place name ' * 200, False)]:
                output = Path(directory) / 'overview.png'
                track = dict(name=name, table_number=1, time=START, duration=timedelta(hours=1),
                             length_km=10, points=[(50, 7), (50.1, 7.1)])
                with patch('contextily.plotting.bounds2img', side_effect=fake_tiles):
                    metadata = render_track_plot([track], 10, (600, 400), 1, False, output,
                        'blue', 3, 'white', 0, 'black', 'white', [['AUTOMATIC']], 'Overview', True,
                        map_provider='esri')
                self.assertEqual(bool(metadata[2].get('overview_dynamic_labels')), expected)


class TrackPanelTests(unittest.TestCase):
    def markers(self):
        track = processed([(index * 60, 50 + index * .03) for index in range(10)])
        return tuple(replace(marker, place='Saint-Jean-Pied-de-Port, Province, Country', weather_code=2,
            weather='18.4 °C · Wind 12 km/h · Rain 0.8 mm · Cloud 65% · RH 72% · 1014 hPa')
            for marker in build_checkpoints(track, default_parameters()))

    def test_panel_compacts_rows_and_overflow_continues_without_losing_rows(self):
        markers = self.markers()
        for size in ((8.27, 11.69), (11.69, 8.27)):
            with self.subTest(size=size):
                figure = new_figure(figsize=size)
                statistics = tuple((f'Metric {index}', '100 km') for index in range(8))
                layout = measure_panel(figure, size[0] * .88, statistics, markers, default_parameters())
                self.assertEqual(len(layout.rows), 10)
                self.assertEqual(layout.summary_height, 56)
                self.assertTrue(all('\n' not in value for _, values in layout.rows for value in values))
                self.assertTrue(all(values[5] == 'Saint-Jean-Pied-de-Port' for _, values in layout.rows))
                first = draw_panel(figure, [.06, .7, .88, .13], layout)
                self.assertLess(first, 10)
                text = ' '.join(item.get_text() for item in figure.axes[-1].texts)
                for removed in ('CHECKPOINTS', 'since previous checkpoint', 'Start', 'End'):
                    self.assertNotIn(removed, text)
                figures = list(continuation_figures(size, 'Track 1', layout, first))
                self.assertTrue(figures)
                for extra in figures:
                    self.assertTrue(any('continued' in text.get_text() for text in extra.texts))
                    actual_width = extra.axes[0].get_position().width * size[0] * 72
                    self.assertAlmostEqual(actual_width, sum(layout.widths))
                    extra.clear()

    def test_profile_and_map_use_clock_times_without_checkpoint_numbers(self):
        markers = self.markers()
        figure = new_figure()
        map_ax, profile_ax = figure.subplots(2)
        profile_ax.set_xlim(0, markers[-1].distance)
        draw_checkpoint_markers(map_ax, profile_ax, markers)
        times = [checkpoint_time(marker, markers[0].timestamp) for marker in markers]
        self.assertEqual([text.get_text() for text in profile_ax.texts], times)
        self.assertEqual([text.get_text() for text in map_ax.texts], times)
        self.assertEqual(profile_ax.child_axes, [])
        figure.canvas.draw()
        box = profile_ax.get_window_extent()
        for label in profile_ax.texts:
            self.assertEqual(label.get_gid(), 'checkpoint-time')
            bounds = label.get_window_extent()
            self.assertGreaterEqual(bounds.x0, box.x0)
            self.assertLessEqual(bounds.x1, box.x1)
            self.assertGreaterEqual(bounds.y0, box.y0)
            self.assertLess(bounds.y1, box.y1)
        self.assertEqual(len(profile_ax.collections), len(markers))
        self.assertEqual([float(item.get_offsets()[0][0]) for item in profile_ax.collections], [item.distance for item in markers])

    def test_missing_statistics_are_omitted_and_zero_values_remain(self):
        track = SimpleNamespace(nr=2, metrics={'length_km': 0, 'speed_kmh': None, 'npoints': 0})
        values = dict(track_statistics(track, 0, 0))
        self.assertEqual(values['Length'], '0.0 km')
        for omitted in ('Points', 'Track', 'Recorded'):
            self.assertNotIn(omitted, values)
        self.assertNotIn('Average speed', values)

    def test_break_uses_one_time_range_and_shared_weather_icon(self):
        marker = replace(self.markers()[0], break_end=START + timedelta(minutes=45), kind='break')
        figure = new_figure(figsize=(8.27, 11.69))
        layout = measure_panel(figure, 7.27, (), (marker,), default_parameters())
        expected = checkpoint_time(marker, START) + '-' + checkpoint_time(replace(marker, timestamp=marker.break_end), START)
        self.assertEqual(layout.rows[0][1][0], expected)
        self.assertLess(layout.row_heights[0], 14)
        with patch('gpx_pdf_layout.draw_weather_symbol') as symbol:
            self.assertEqual(draw_panel(figure, [.06, .7, .88, .1], layout), 1)
            self.assertEqual(symbol.call_args.args[-1], 2)

    def test_locality_and_weather_keep_values_without_administrative_names(self):
        self.assertEqual(compact_place_name({'place': 'Dijon, Burgundy, France'}), 'Dijon')
        self.assertEqual(compact_place_name({'place': 'Dijon (Burgundy), France'}), 'Dijon')
        self.assertEqual(compact_place_name({'place': 'District, Province', 'place_details': {'locality': 'Dijon'}}), 'Dijon')
        self.assertEqual(compact_place_name({'place_details': {'locality': 'Frankfurt (Oder)'}}), 'Frankfurt (Oder)')
        self.assertEqual(compact_weather_text(self.markers()[0].weather),
                         '18.4° 12km/h 0.8mm')
        self.assertEqual(compact_weather_text('18.4°C 12km/h 0.8mm C65% RH72% 1014hPa'),
                         '18.4° 12km/h 0.8mm')

    def test_street_is_added_only_if_it_fits_without_wrapping(self):
        marker = replace(self.markers()[0], place='Dijon', place_detail='Rue de la Chouette')
        layout = measure_panel(new_figure(), 7, (), (marker,), default_parameters())
        self.assertEqual(layout.rows[0][1][5], 'Dijon · Rue de la Chouette')
        marker = replace(marker, place_detail='An extremely long street name ' * 20)
        layout = measure_panel(new_figure(), 7, (), (marker,), default_parameters())
        self.assertEqual(layout.rows[0][1][5], 'Dijon')

    def test_coordinate_fallback_preserves_both_coordinates(self):
        marker = self.markers()[0]
        marker = replace(marker, place=coordinate_place(marker))
        layout = measure_panel(new_figure(), 7, (), (marker,), default_parameters())
        self.assertEqual(layout.rows[0][1][5], coordinate_place(marker))

    def test_weather_disabled_omits_values_icons_and_attribution(self):
        figure = new_figure()
        parameters = dict(default_parameters(), **{'pdf.checkpoint_weather': False})
        layout = measure_panel(figure, 7, (), self.markers(), parameters)
        self.assertNotIn('Weather', layout.columns)
        with patch('gpx_pdf_layout.draw_weather_symbol') as symbol:
            draw_panel(figure, [.06, .2, .88, .7], layout)
            symbol.assert_not_called()
        self.assertFalse(any('Open-Meteo' in text.get_text() for text in figure.axes[-1].texts))

    def test_every_shared_weather_category_has_a_vector_symbol(self):
        from weather_symbols import draw_weather_symbol, weather_condition_kind
        for code, expected in ((0, 'sun'), (2, 'cloud'), (61, 'rain'), (75, 'snow'), (95, 'storm'), (None, 'cloud')):
            with self.subTest(code=code):
                self.assertEqual(weather_condition_kind(code), expected)
                figure = new_figure()
                ax = figure.add_axes([0, 0, 1, 1])
                draw_weather_symbol(ax, 10, 10, 10, code)
                self.assertGreater(len(ax.patches) + len(ax.lines), 0)

    def test_renderer_prepares_one_enrichment_index_and_caches_checkpoint_results(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.media_directories = (Path(directory),)
            with patch('GPXEditor.PDFMediaIndex', wraps=__import__('gpx_pdf_details').PDFMediaIndex) as index:
                first = renderer.checkpoints_for_track(renderer.tracks[0])
                second = renderer.checkpoints_for_track(renderer.tracks[0])
                self.assertIs(first, second)
                index.assert_called_once()

    def test_disabled_checkpoints_do_not_read_sidecars_or_run_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.project_parameters['pdf.checkpoints'] = False
            with patch('GPXEditor.PDFMediaIndex', side_effect=AssertionError('sidecars')):
                self.assertEqual(renderer.checkpoints_for_track(renderer.tracks[0]), ())

    def test_hidden_information_box_keeps_markers_without_loading_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.project_parameters['pdf.track_info'] = False
            with patch('GPXEditor.PDFMediaIndex', side_effect=AssertionError('sidecars')):
                self.assertTrue(renderer.checkpoints_for_track(renderer.tracks[0]))
                self.assertIsNone(renderer.track_panel(renderer.tracks[0], new_figure(), 7))
