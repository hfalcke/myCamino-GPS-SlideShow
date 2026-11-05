"""The complete map and all header columns fit narrow and wide screens."""

import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSMakeRect, NSImageAlignTop
import GPSTrackShow as show


STANDARD = {'axes_box_fraction': {'left': .001, 'bottom': .002, 'width': .998, 'height': .90},
            'runtime_header_fraction': .098}
TIME_LAPSE = {'axes_box_fraction': {'left': .001, 'bottom': .002, 'width': .998, 'height': .996},
              'runtime_header_fraction': .098}
METRICS = ('Total: 12345 km', 'Stage: 123.4 km', 'Height: 1234 m')


class ResponsiveSlideshowLayoutTests(unittest.TestCase):
    def test_full_maps_fit_and_top_align_at_every_supported_aspect(self):
        for width, height in ((640, 480), (1024, 768), (1600, 1200), (1440, 900), (1920, 1080), (3840, 2160), (768, 1024)):
            for metadata in (None, STANDARD, TIME_LAPSE):
                for style in ('black', 'off', 'transparent'):
                    for visible in (True, False):
                        with self.subTest(screen=(width, height), metadata=metadata, style=style, visible=visible):
                            outer = (0., 0., float(width), float(height))
                            frame = show.header_content_rect(outer, metadata, style, visible)
                            rect, scale = show.map_image_rect_and_scale(outer, (1920., 1080.), metadata, style, visible)
                            self.assertAlmostEqual(scale[0], scale[1])
                            self.assertAlmostEqual(rect[2] / rect[3], 16 / 9)
                            self.assertGreaterEqual(rect[0], frame[0] - 1e-6)
                            self.assertGreaterEqual(rect[1], frame[1] - 1e-6)
                            self.assertLessEqual(rect[0] + rect[2], frame[0] + frame[2] + 1e-6)
                            self.assertAlmostEqual(rect[1] + rect[3], frame[1] + frame[3])

    def test_header_components_and_center_never_overlap(self):
        for width, height in ((640, 480), (1024, 768), (1600, 1200), (1920, 1080), (3840, 2160), (768, 1024)):
            for clock, speed, weather in itertools.product((False, True), repeat=3):
                for metrics in ((), METRICS, ('Total: 123456789 km', 'Stage: 999.9 km', 'Height: 99999 m')):
                    with self.subTest(screen=(width, height), clock=clock, speed=speed, weather=weather, metrics=metrics):
                        layout = show.responsive_header_layout((0, 0, width, height), TIME_LAPSE,
                            clock_visible=clock, speedometer_visible=speed, weather_visible=weather, metrics_lines=metrics)
                        x, y, w, h = layout['band']
                        frames = [layout['center']]
                        frames += [layout[key] for key, enabled in (('clock', clock), ('speedometer', speed), ('weather', weather)) if enabled]
                        for fx, fy, fw, fh in frames:
                            self.assertGreaterEqual(fx, x)
                            self.assertGreaterEqual(fy, y)
                            self.assertLessEqual(fx + fw, x + w + 1e-6)
                            self.assertLessEqual(fy + fh, y + h + 1e-6)
                        frames.sort()
                        for first, second in zip(frames, frames[1:]):
                            self.assertLessEqual(first[0] + first[2], second[0] + 1e-6)
                        if metrics:
                            font = layout['statistics_font_size']
                            parsed = [tuple(p.strip() for p in line.partition(':')[::2]) for line in metrics]
                            stats_width = (max(show.header_text_size(label + ':', font)[0] for label, _ in parsed)
                                           + max(5., font * .45)
                                           + max(show.header_text_size(value, font)[0] for _, value in parsed))
                            self.assertLessEqual(max(f[0] + f[2] for f in frames), layout['statistics_right'] - stats_width + 1e-6)

    def test_narrow_headers_shrink_widgets_and_omit_only_secondary_weather(self):
        narrow = show.responsive_header_layout((0, 0, 1024, 768), metrics_lines=METRICS)
        wide = show.responsive_header_layout((0, 0, 1920, 1080), metrics_lines=METRICS)
        self.assertLess(narrow['clock_size'], wide['clock_size'])
        self.assertFalse(narrow['secondary_weather'])
        self.assertTrue(wide['secondary_weather'])

    def test_long_location_uses_town_before_shrinking_and_keeps_source_untouched(self):
        source = {'place_details': {'locality': 'Santiago de Compostela', 'sub_locality': 'Old Town',
                                   'thoroughfare': 'An extremely long street name'}}
        full = 'Old Town, Galicia, Spain\nAn extremely long street name with house number 12345'
        variants = show.place_header_variants(full, source)
        width = show.header_text_size('Santiago de Compostela', 20)[0] + 1
        result, size = show.fitted_header_text(variants[0], width, 20, variants=variants)
        self.assertEqual(result, 'Santiago de Compostela')
        self.assertEqual(size, 20)
        self.assertEqual(source['place_details']['thoroughfare'], 'An extremely long street name')
        self.assertIn('street name', variants[0])

    def test_very_long_titles_and_places_stay_inside_center_column(self):
        for width in (20., 80., 180., 400.):
            for text in ('An extremely long title ' * 20, 'Santiago de Compostela ' * 40):
                fitted, size = show.fitted_header_text(text, width, 32)
                self.assertLessEqual(show.header_text_size(fitted, size)[0], width + 1e-6)

    def presenter(self):
        p = show.CocoaImagePresenter.__new__(show.CocoaImagePresenter)
        p.host_view = SimpleNamespace(bounds=lambda: NSMakeRect(0, 0, 1024, 768))
        p.header_visible = True
        p.header_metadata = STANDARD  # Shared content deliberately has different geometry.
        p.header_background_style = 'black'
        p.header_reference_is_map = True
        p.header_reference_fills_frame = False
        p.header_reference_image = object()
        p.header_reference_metadata = TIME_LAPSE
        return p

    def test_presenter_uses_displayed_map_metadata_not_shared_header_metadata(self):
        p = self.presenter()
        self.assertIsNone(p._header_layout_metadata())
        with patch.object(show, 'image_size_tuple', return_value=(1920, 1080)):
            frame = p._content_frame()
        self.assertLess(frame.size.height, 768)
        self.assertEqual(p._header_reference_rect(), p.host_view.bounds())
        p.header_reference_metadata = STANDARD
        with patch.object(show, 'image_size_tuple', return_value=(1920, 1080)):
            frame = p._header_reference_rect()
        self.assertEqual(frame.size.width, 1024)
        self.assertEqual(frame.size.height, 768)
        self.assertEqual(frame.origin.y, 0)

    def test_map_views_align_images_at_top_without_changing_media_alignment_policy(self):
        p = self.presenter()
        p.primary_view, p.overlay_view, p.fade_view = Mock(), Mock(), Mock()
        p.video_view = None
        with patch.object(show, 'image_size_tuple', return_value=(1920, 1080)):
            p._apply_header_layout()
        p.primary_view.setImageAlignment_.assert_called_once_with(NSImageAlignTop)
        with patch.object(show, 'image_size_tuple', return_value=(1920, 1080)):
            self.assertEqual(p.primary_view.setFrame_.call_args.args[0], p._content_frame())

    def test_profile_variant_and_overview_keep_their_own_geometry(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.current_phase = show.PlaybackPhase.STAGE_MAP
        app.current_track_metadata = STANDARD
        app.current_overview_metadata = {'overview': True}
        app.profile_map_display_image = object()
        app.profile_map_display_metadata = TIME_LAPSE
        photo = show.WindowTarget('photo', app.profile_map_display_image, show.Transition.SWITCH)
        overview = show.WindowTarget('map', object(), show.Transition.SWITCH)
        self.assertIs(app._map_metadata_for_target(photo), TIME_LAPSE)
        self.assertIs(app._map_metadata_for_target(overview), app.current_overview_metadata)

    def test_layout_reuses_measurements_and_does_no_map_or_network_work(self):
        show._responsive_header_layout.cache_clear()
        with patch.object(show, 'load_nsimage', side_effect=AssertionError('decode')), \
             patch.object(show, 'draw_dynamic_map_overlay', side_effect=AssertionError('render')):
            first = show.responsive_header_layout((0, 0, 1024, 768), TIME_LAPSE, metrics_lines=METRICS)
            second = show.responsive_header_layout((0, 0, 1024, 768), TIME_LAPSE, metrics_lines=METRICS)
        self.assertIs(first, second)
        self.assertEqual(show._responsive_header_layout.cache_info().hits, 1)

    def test_disabled_place_component_never_replaces_the_last_title_row(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.config = SimpleNamespace()
        state = app._build_header_state(('Stage', 'Track details'), (), STANDARD,
            place='Town, a long street', place_variants=('Town, a long street', 'Town'))
        self.assertEqual(state.lines, ('Stage', 'Track details', ''))
        self.assertEqual(state.place_variants, ())

    def test_new_map_reference_refreshes_widget_geometry_even_with_same_clock_and_weather(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.config = SimpleNamespace()
        state = app._build_header_state(('Stage',), METRICS, STANDARD,
            clock_time=(12, 30), clock_date_text='03.11.2026', running_speed_kmh=4.8)
        p = self.presenter()
        p._shared_header_render_signature = None
        p._shared_clock_render_signature = None
        p._shared_weather_render_signature = None
        p.set_header, p.set_clock_time, p.set_speedometer, p.set_weather = Mock(), Mock(), Mock(), Mock()
        p.apply_header_state(state)
        p.apply_header_state(state)
        self.assertEqual(p.set_clock_time.call_count, 1)
        p.header_reference_image = object()
        p.apply_header_state(state)
        self.assertEqual(p.set_clock_time.call_count, 2)
        self.assertEqual(p.set_weather.call_count, 2)

    def test_secondary_weather_is_hidden_locally_without_changing_selected_field(self):
        p = self.presenter()
        p.header_metrics, p.header_font_size = METRICS, 30.
        p.header_font_family, p.header_font_style = 'System', 'bold'
        p.weather_view = Mock()
        p.set_weather({'values': {'temperature_2m': 18.4}}, True, secondary='wind')
        self.assertEqual(p.weather_secondary, 'wind')
        self.assertEqual(p.weather_view.setWeather_primary_secondary_icon_.call_args.args[2], 'none')

    def test_recreated_secondary_window_gets_overview_geometry_before_presentation(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        image = object()
        app.map_window, app.time_map_view = Mock(), None
        app.time_lapse_active, app.time_lapse_stage = False, None
        app.current_stage_index, app.stages = None, []
        app.current_overview_metadata = {'overview': True}
        app.current_header_state = object()
        app._current_marked_overview_image = lambda: image
        app._header_mode_for_target = lambda target: 'full'
        app._raise_global_overlay_layers = Mock()
        app.map_presenter = SimpleNamespace(set_content_visible=Mock(), set_header_reference_image=Mock(),
            transition_to=Mock(), apply_header_state=Mock())
        app._refresh_separate_map_window_content()
        app.map_presenter.set_header_reference_image.assert_called_once_with(
            image, False, True, app.current_overview_metadata)


if __name__ == '__main__':
    unittest.main()
