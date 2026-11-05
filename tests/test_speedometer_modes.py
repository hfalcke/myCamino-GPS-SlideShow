import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adventure_parameters import default_parameters, normalize_parameters, normalize_speedometer_mode
from GPSTrackShow import GPSTrackShowApp, config_from_options, parse_args


class SpeedometerModeTests(unittest.TestCase):
    def test_legacy_settings_and_new_modes_roundtrip(self):
        self.assertEqual(default_parameters()['slideshow.speedometer'], 'timelapse')
        for value, expected in ((True, 'on'), (False, 'off'), ('on', 'on'), ('off', 'off'),
                                ('timelapse', 'timelapse'), ('time_lapse', 'timelapse')):
            settings = normalize_parameters({'slideshow.speedometer': value})
            self.assertEqual(settings['slideshow.speedometer'], expected)
            self.assertEqual(normalize_parameters(settings)['slideshow.speedometer'], expected)
        with self.assertRaises(ValueError):
            normalize_speedometer_mode('invalid')

    def test_shared_header_respects_actual_playback_mode_and_available_data(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace()
        for mode in ('on', 'off', 'timelapse', True, False):
            app.config.speedometer = mode
            for time_lapse in (False, True):
                for tracking in (False, True):
                    app.time_lapse_active = time_lapse
                    app.standard_route_tracking_active = tracking
                    for speed in (None, 0., 4.8):
                        with self.subTest(mode=mode, time_lapse=time_lapse, tracking=tracking, speed=speed):
                            state = app._build_header_state(('Stage',), (), {}, running_speed_kmh=speed)
                            expected = speed is not None and (mode in ('on', True) or (mode == 'timelapse' and time_lapse))
                            self.assertEqual(state.speedometer_visible, expected)

    def test_python_and_cli_accept_all_three_policies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            playlist = root / 'trip.lst'
            playlist.write_text('photo.jpeg\n')
            self.assertEqual(config_from_options(root, inputlist=playlist).speedometer, 'timelapse')
            with patch('GPSTrackShow.available_screen_count', return_value=1):
                self.assertEqual(parse_args([str(root), '--inputlist', str(playlist)]).speedometer, 'timelapse')
            for mode in ('on', 'off', 'timelapse'):
                self.assertEqual(config_from_options(root, inputlist=playlist, speedometer=mode).speedometer, mode)
                with patch('GPSTrackShow.available_screen_count', return_value=1):
                    self.assertEqual(parse_args([str(root), '--inputlist', str(playlist), '--speedometer', mode]).speedometer, mode)
            self.assertEqual(config_from_options(root, inputlist=playlist, speedometer=False).speedometer, 'off')

    def test_live_settings_keep_policy_instead_of_casting_strings_to_true(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace()
        app.photo_presenter = app.map_presenter = None
        app.time_photo_view = app.time_map_view = None
        app._refresh_photo_overlays = Mock()
        app._show_temporary_status_overlay = Mock()
        for mode in ('off', 'timelapse', 'on'):
            app._apply_runtime_settings({'slideshow.speedometer': mode})
            self.assertEqual(app.config.speedometer, mode)


if __name__ == '__main__':
    unittest.main()
