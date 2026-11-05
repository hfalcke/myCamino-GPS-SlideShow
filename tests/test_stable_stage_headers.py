import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import GPSTrackShow as s
from adventure_parameters import default_parameters, normalize_parameters
from map_overlay import MAP_CONTENT_VERSION


class StableStageHeaderTests(unittest.TestCase):
    def app(self):
        app = s.GPSTrackShowApp.__new__(s.GPSTrackShowApp)
        app.config = SimpleNamespace(weather=True, speedometer='timelapse', font_size=30,
                                     font_family='System', track_title_mode='track_name')
        app.current_stage_index = 0
        app.current_track_metadata = dict(map_kind='track', track_name='A long stage name',
            track_length_km=20.4, track_duration='05:34')
        return app

    def test_map_overview_and_media_reserve_identical_centered_header(self):
        app = self.app()
        before = app._build_header_state(('Wrong overview title',), (), app.current_track_metadata)
        app.active_caption_font = s.CaptionFontState(90, 'italic', 'Courier')
        during = app._build_header_state(('Other title',), ('Total: 123 km', 'Stage: 5 km', 'Height: 200 m'),
            app.current_track_metadata, place='Town', clock_time=(12, 30), running_speed_kmh=4.8,
            weather={'values': {'temperature_2m': 18}})
        self.assertEqual(before.lines[:2], during.lines[:2])
        self.assertEqual(before.lines[2], '')
        self.assertEqual(during.lines[2], 'Town')
        self.assertEqual(before.font_size, during.font_size)
        self.assertEqual(before.font_family, during.font_family)
        self.assertEqual(before.font_style, during.font_style)
        for width, height in ((1024, 768), (1600, 1200), (1440, 900), (1920, 1080)):
            layouts = [s.responsive_header_layout((0, 0, width, height), metadata,
                metrics_lines=state.layout_metrics, **dict(state.layout_components))
                for state, metadata in ((before, {'axes_box_fraction': {'bottom': 0, 'height': .85}}),
                                        (before, {}), (during, None))]
            self.assertEqual(layouts[0], layouts[1])
            self.assertEqual(layouts[1], layouts[2])
            self.assertAlmostEqual(layouts[0]['center'][0] + layouts[0]['center'][2] / 2, width / 2)
            with self.assertRaises(TypeError):
                layouts[0]['center'] = ()

    def test_empty_rows_keep_baselines_and_semantic_styles(self):
        layout = s.responsive_header_layout((0, 0, 1024, 768))
        def draw(lines):
            with patch.object(s, 'draw_shadowed_text') as render:
                s.draw_runtime_header((0, 0, 1024, 768), None, lines, (), font_color=(1,1,1,1),
                    font_factor=2.2, base_font_size=30, background_style='off',
                    background_color=(0,0,0,1), shadow_color=(0,0,0,1), layout=layout)
            return render.call_args_list
        full = draw(('Stage', 'Statistics', 'Town'))
        sparse = draw(('', 'Statistics', ''))
        self.assertEqual(sparse[0].args[1:3], full[1].args[1:3])
        for call in full:
            self.assertEqual(call.args[1], 512)
        self.assertEqual(sparse[0].args[3].fontName(), full[1].args[3].fontName())

    def test_settings_and_python_cli_preserve_styles(self):
        defaults = default_parameters()
        for suffix, style in (('stage_name', 'bold'), ('stage_statistics', 'italic'), ('location', 'regular')):
            self.assertEqual(defaults[f'slideshow.header_{suffix}_style'], style)
            self.assertEqual(normalize_parameters({f'slideshow.header_{suffix}_style': 'bold-italic'})[
                f'slideshow.header_{suffix}_style'], 'bold-italic')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trip.lst'
            path.write_text('photo.jpg\n')
            config = s.config_from_options(directory, inputlist=path, header_location_style='italic')
            self.assertEqual(config.header_location_style, 'italic')
            with patch.object(s, 'available_screen_count', return_value=1):
                config = s.parse_args([directory, '--inputlist', str(path), '--header-location-style', 'bold-italic'])
            self.assertEqual(config.header_location_style, 'bold-italic')
            with self.assertRaises(ValueError):
                s.config_from_options(directory, inputlist=path, header_location_style='invalid')

    def test_live_style_changes_update_shared_state(self):
        app = self.app()
        app.photo_presenter = app.map_presenter = None
        app._refresh_photo_overlays = Mock()
        app._show_temporary_status_overlay = Mock()
        app._apply_runtime_settings({'slideshow.header_stage_name_style': 'regular',
            'slideshow.header_stage_statistics_style': 'bold', 'slideshow.header_location_style': 'italic'})
        state = app._build_header_state((), (), app.current_track_metadata)
        self.assertEqual(state.central_styles, ('regular', 'bold', 'italic'))

    def test_declared_empty_map_margin_is_excluded_without_losing_geography(self):
        meta = dict(background_only=True, map_content_version=MAP_CONTENT_VERSION,
                    axes_box_fraction=dict(bottom=.002, height=.90, left=.002, width=.996))
        for width, height in ((1024,768), (1920,1080), (1440,900)):
            outer = (0,0,width,height)
            image, scale = s.map_image_rect_and_scale(outer, (1920,1080), meta, 'black')
            content = s.header_content_rect(outer, None, 'black')
            self.assertAlmostEqual(scale[0], scale[1])
            self.assertAlmostEqual(image[1] + image[3] * .902, content[1] + content[3])
            self.assertGreaterEqual(image[1], 0)
            self.assertLessEqual(image[0] + image[2], width)


if __name__ == '__main__':
    unittest.main()
