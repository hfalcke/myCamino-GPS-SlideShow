"""Introductory maps use recorded stage time without inventing missing values."""

import tempfile
import unittest
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSMakeRect
import GPSTrackShow as show


class StageStartClockTests(unittest.TestCase):
    def test_complete_track_start_wins_over_media(self):
        for key in ('track_start_time', 'start_time'):
            with self.subTest(key=key):
                self.assertEqual(show.stage_start_clock_values(
                    {key: '04.11.2026 09:12:34'}, {'datetime_iso': '2026-11-05T17:30:00'}),
                    ((9, 12), '04.11.2026'))

    def test_absolute_processed_point_is_used_but_synthetic_points_are_not(self):
        metadata = {'timed_track_points': [
            {'time_iso': '1970-01-01T00:00:00', 'has_absolute_time': False},
            {'time_iso': '2026-11-04T10:20:00', 'has_absolute_time': True}]}
        self.assertEqual(show.stage_start_clock_values(metadata), ((10, 20), '04.11.2026'))
        metadata.update(has_absolute_time=False, track_start_time='1970-01-01T00:00:00')
        self.assertEqual(show.stage_start_clock_values(metadata, stage_date='04.11.2026'),
                         (None, '04.11.2026'))

    def test_date_only_track_falls_back_to_first_media_timestamp(self):
        self.assertEqual(show.stage_start_clock_values({'track_date': '03.11.2026'},
            {'datetime_iso': '2026-11-04T10:20:00'}), ((10, 20), '04.11.2026'))

    def test_date_fallback_order_and_invalid_dates(self):
        for track, media, stage, expected in (
            ({'track_date': '03.11.2026'}, {'date_german': 'Wednesday, 04.11.2026'}, '05.11.2026', '03.11.2026'),
            ({}, {'datetime_iso': '2026-11-04'}, '05.11.2026', '04.11.2026'),
            ({}, {}, 'Wednesday, 04.11.2026', '04.11.2026'),
            ({'track_start_time': 'bad'}, {'date_german': 'bad'}, '31.02.2026', None),
        ):
            with self.subTest(track=track, media=media):
                self.assertEqual(show.stage_start_clock_values(track, media, stage_date=stage), (None, expected))
        self.assertEqual(show.stage_start_clock_values({'track_start_time': '2026-11-04'}), (None, '04.11.2026'))

    def test_time_only_media_and_control_row_time(self):
        entry = show.parse_photo_entry('photo.jpeg | 12:34')
        self.assertEqual(show.stage_start_clock_values({}, {}, entry), ((12, 34), None))
        self.assertEqual(show.stage_start_clock_values({}, {}, entry, '04.11.2026'),
                         ((12, 34), '04.11.2026'))
        self.assertEqual(show.stage_start_clock_values({}, {'time': '00:00'}), ((0, 0), None))

    def test_timezone_uses_existing_local_display_convention(self):
        value = '2026-11-04T00:20:00+09:00'
        expected = datetime.fromisoformat(value).astimezone()
        self.assertEqual(show.stage_start_clock_values({}, {'datetime_iso': value}),
                         ((expected.hour, expected.minute), expected.strftime('%d.%m.%Y')))

    def app(self, root):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.config = SimpleNamespace(photodir=root, clock=True)
        app.current_phase = show.PlaybackPhase.STAGE_MAP
        app.current_stage_index = 0
        app.current_track_metadata = {'track_name': 'Stage'}
        app.playlist_lines = ['#Datum: 04.11.2026', '#Map: stage.png',
                              '#photo-hidden.jpeg', 'first.jpeg | 11:00', 'second.jpeg | 09:00']
        app.stages = show.parse_stage_descriptors(app.playlist_lines)
        cache = show.StagePresentationCacheEntry((), root / 'stage.png', None, app.current_track_metadata)
        app.stage_presentation_cache = OrderedDict([((0, False), cache)])
        return app

    def header(self, app, **kwargs):
        return app._build_header_state(('Stage',), (), app.current_track_metadata, **kwargs)

    def test_control_order_hidden_rows_and_cache_reuse_across_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sidecar = show.media_sidecar_path(root / 'first.jpeg')
            sidecar.write_text('{}')
            app = self.app(root)
            with patch.object(show, 'try_read_photo_metadata', return_value={}) as read:
                for phase in (show.PlaybackPhase.STAGE_MAP, show.PlaybackPhase.STAGE_OVERVIEW):
                    app.current_phase = phase
                    app.time_lapse_active = phase == show.PlaybackPhase.STAGE_OVERVIEW
                    header = self.header(app)
                    self.assertEqual(header.clock_time, (11, 0))
                    self.assertEqual(header.clock_date_text, '04.11.2026')
                read.assert_called_once_with(sidecar.resolve(), (root / 'first.jpeg').resolve())
            self.assertIsNotNone(app.stage_presentation_cache[(0, False)].intro_clock_signature)

    def test_first_medium_without_time_does_not_search_later_media(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            app.playlist_lines[3] = 'first.jpeg'
            header = self.header(app)
            self.assertIsNone(header.clock_time)
            self.assertTrue(header.date_visible)

    def test_cache_refreshes_for_sidecar_control_order_and_track_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = show.media_sidecar_path(root / 'first.jpeg')
            second = show.media_sidecar_path(root / 'second.jpeg')
            first.write_text('{}')
            second.write_text('{}')
            app = self.app(root)
            with patch.object(show, 'try_read_photo_metadata', return_value={}) as read:
                self.header(app)
                first.write_text('{"changed": true}')
                self.header(app)
                self.assertEqual(read.call_count, 2)
                app.playlist_lines[3], app.playlist_lines[4] = app.playlist_lines[4], app.playlist_lines[3]
                self.assertEqual(self.header(app).clock_time, (9, 0))
                self.assertEqual(read.call_count, 3)
                app.current_track_metadata['track_start_time'] = '04.11.2026 08:15:00'
                self.assertEqual(self.header(app).clock_time, (8, 15))
                self.assertEqual(read.call_count, 3)

    def test_date_only_clock_off_and_fixed_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            app.playlist_lines[3] = 'first.jpeg'
            date_only = self.header(app)
            self.assertFalse(date_only.clock_visible)
            self.assertTrue(date_only.date_visible)
            app.current_track_metadata['track_start_time'] = '04.11.2026 09:12:00'
            complete = self.header(app)
            self.assertEqual(date_only.layout_components, complete.layout_components)
            for bounds in ((0, 0, 1024, 768), (0, 0, 1920, 1080)):
                self.assertEqual(show.responsive_header_layout(bounds, **dict(date_only.layout_components)),
                                 show.responsive_header_layout(bounds, **dict(complete.layout_components)))
            app.config.clock = False
            hidden = self.header(app)
            self.assertFalse(hidden.clock_visible)
            self.assertFalse(hidden.date_visible)

    def test_live_motion_media_and_adventure_intro_do_not_use_stage_start(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            app.current_track_metadata['track_start_time'] = '04.11.2026 09:12:00'
            for phase in (show.PlaybackPhase.TIME_LAPSE, show.PlaybackPhase.MEDIA,
                          show.PlaybackPhase.INTRO_INFO, show.PlaybackPhase.INTRO_OVERVIEW):
                app.current_phase = phase
                state = self.header(app, clock_time=(15, 45), clock_date_text='05.11.2026')
                self.assertEqual(state.clock_time, (15, 45))
                self.assertEqual(state.clock_date_text, '05.11.2026')

    def test_native_retained_view_supports_date_only_and_hidden_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            app.playlist_lines[3] = 'first.jpeg'
            view = show.TimeLapseMapView.alloc().initWithFrame_(NSMakeRect(0, 0, 1024, 768))
            with patch.object(show, 'create_clock_overlay_image', return_value=None) as render:
                view.apply_header_state(self.header(app))
                self.assertEqual(render.call_args.args[1:4], (None, None, '04.11.2026'))
                self.assertTrue(render.call_args.kwargs['reserve_date_space'])
                self.assertEqual(view.clock_view.alphaValue(), 1)
                view.apply_header_state(self.header(app), mode='off')
                self.assertEqual(view.clock_view.alphaValue(), 0)
                app.config.clock = False
                view.apply_header_state(self.header(app))
                self.assertEqual(view.clock_view.alphaValue(), 0)

    def test_standard_renderer_supports_date_only(self):
        presenter = show.CocoaImagePresenter.__new__(show.CocoaImagePresenter)
        presenter.clock_view = Mock()
        presenter._header_reference_rect = Mock(return_value=NSMakeRect(0, 0, 1024, 768))
        presenter._responsive_header_layout = Mock(return_value=show.responsive_header_layout((0, 0, 1024, 768)))
        presenter._header_layout_metadata = Mock(return_value=None)
        presenter.header_font_factor = 2.2
        presenter.header_font_color = (1, 1, 1, 1)
        presenter.header_shadow_color = (0, 0, 0, 1)
        presenter.header_background_style = 'black'
        with patch.object(show, 'create_clock_overlay_image') as render:
            presenter.set_clock_time(None, '04.11.2026')
            self.assertEqual(render.call_args.args[1:4], (None, None, '04.11.2026'))
            self.assertTrue(render.call_args.kwargs['reserve_date_space'])
            presenter.clock_view.setAlphaValue_.assert_called_with(1)

    def test_date_only_image_does_not_draw_a_clock_face(self):
        with patch.object(show, 'NSImage') as image, patch.object(show, 'NSGraphicsContext'), \
             patch.object(show, 'NSShadow'), patch.object(show, 'NSBezierPath') as path, \
             patch.object(show, 'draw_shadowed_text') as text:
            show.create_clock_overlay_image(100, None, None, '04.11.2026',
                                            date_on_right=True, reserve_date_space=True)
            path.bezierPathWithOvalInRect_.assert_not_called()
            self.assertEqual(text.call_count, 2)
            self.assertEqual(image.alloc.return_value.initWithSize_.call_args.args[0].width, 290)


if __name__ == '__main__':
    unittest.main()
