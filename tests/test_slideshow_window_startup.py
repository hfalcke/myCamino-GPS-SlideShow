"""Constructor and restart invariants without opening native player windows."""

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import GPSTrackShow as show


class SlideshowWindowStartupTests(unittest.TestCase):
    def make_app(self, root, *, mapwindow=False, joined=False, fullscreen=False, resume=False):
        playlist = root / 'trip.lst'
        playlist.write_text('#Map: stage.png\nphoto.jpeg\n')
        config = show.config_from_options(root, inputlist=playlist, transition='BLEND',
            mapwindow=mapwindow, join_windows=joined, fullscreen=fullscreen,
            resume_index=1 if resume else None)
        snapshots = []
        original_build = show.GPSTrackShowApp._build_windows

        def build(app):
            snapshots.append((app.photo_window, app.map_window, app.photo_presenter, app.map_presenter))
            original_build(app)

        def separate(app, **_kwargs):
            app.map_window = Mock()
            app.map_presenter = Mock()

        with ExitStack() as stack:
            for name in ('NSApplication', 'NSView', 'NSSplitView', 'CocoaImagePresenter',
                         'BackgroundMusicController', 'NarrationController'):
                stack.enter_context(patch.object(show, name))
            stack.enter_context(patch.object(show.signal, 'signal'))
            for name in ('_apply_start_track', '_attach_time_lapse_view', '_update_window_titles',
                         '_apply_screen_layout', '_install_key_monitor', 'schedule_callback'):
                stack.enter_context(patch.object(show.GPSTrackShowApp, name))
            stack.enter_context(patch.object(show.GPSTrackShowApp, '_create_window', side_effect=lambda *args: Mock()))
            stack.enter_context(patch.object(show.GPSTrackShowApp, '_create_separate_map_window', autospec=True,
                                            side_effect=separate))
            stack.enter_context(patch.object(show.GPSTrackShowApp, '_build_windows', new=build))
            app = show.GPSTrackShowApp(config)
        self.assertEqual(snapshots, [(None, None, None, None)])
        app.schedule_callback = Mock()
        return app

    def test_window_handles_exist_for_every_launch_layout_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            for mapwindow, joined in ((False, False), (True, False), (True, True)):
                for fullscreen in (False, True):
                    for resume in (False, True):
                        with self.subTest(mapwindow=mapwindow, joined=joined, fullscreen=fullscreen, resume=resume):
                            app = self.make_app(Path(directory), mapwindow=mapwindow, joined=joined,
                                                fullscreen=fullscreen, resume=resume)
                            self.assertIsNotNone(app.photo_window)
                            self.assertEqual(app.map_window is not None, mapwindow and not joined)
                            app._available_screens = lambda: [object(), object()]
                            expected = ({'photo', 'map'} if mapwindow and not joined else {'photo'}) if fullscreen else set()
                            self.assertEqual(app._startup_fullscreen_roles(), expected)
                            app._restore_display_after_settings()

    def test_single_window_start_and_restart_command_use_initialized_handles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.make_app(root, fullscreen=True, resume=True)
            command_file = root / 'commands.json'
            command_file.write_text(json.dumps({'command': 'restart', 'sequence': 1}))
            object.__setattr__(app.config, 'command_file', command_file)
            app.intro_available = False
            app._run_memory_watchdog = Mock()
            app._prime_standard_resume_map_context = Mock()
            app._show_initial_photo_preview = Mock()
            app._show_startup_overview_preview = Mock()
            app._show_temporary_status_overlay = Mock()
            app._resume_state_payload = Mock(return_value={'playlist_index': 1})
            app._jump_to_playlist_row = Mock()
            app._refresh_header_layouts = Mock()
            app._available_screens = Mock(return_value=[object()])
            app.start(run_loop=False)
            app._jump_to_playlist_row.assert_called_once_with(1)
            self.assertIsNone(app.map_window)
            self.assertIsNotNone(app.startup_playback_callback)
            app._poll_external_commands()
            app._jump_to_playlist_row.assert_called_once_with(1)


class StartupProfileMapTests(unittest.TestCase):
    def app(self, root, *, matching_stage=True):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.current_track_path = root / 'stage.png'
        app.current_track_image = object()
        app.current_track_metadata = {'track_type': 'sightseeing'}
        app.current_stage_index = None
        app.stages = show.parse_stage_descriptors(['#Map: stage.png'] if matching_stage else [])
        app.config = SimpleNamespace(track_type_colors=())
        app._elevation_profile_for_map = Mock(return_value=None)
        app._load_stage_metadata = Mock(return_value={'track_type': 'hiking'})
        return app

    def test_pre_stage_profile_map_uses_matching_stage_cache_without_changing_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.app(root)
            entry = SimpleNamespace(image=object(), metadata={'track_type': 'hiking'},
                                    profile_map_image=None, map_path=root / 'stage-timelapse.png')
            app._load_stage_entry = Mock(return_value=entry)
            overlay = object()
            with patch.object(show, 'resolve_track_map_variant', return_value=entry.map_path), \
                 patch.object(show, 'draw_dynamic_map_overlay', return_value=overlay) as draw, \
                 patch.object(show, 'warn_message') as warning:
                self.assertIs(app._stage_map_with_elevation_profile(), overlay)
                self.assertIs(app._stage_map_with_elevation_profile(), overlay)
            warning.assert_not_called()
            app._load_stage_entry.assert_called_with(0, True)
            draw.assert_called_once()
            self.assertIsNone(app.current_stage_index)

    def test_profile_map_outside_stage_descriptors_loads_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.app(root, matching_stage=False)
            app._load_stage_entry = Mock()
            preferred = root / 'stage-timelapse.png'
            with patch.object(show, 'resolve_track_map_variant', return_value=preferred), \
                 patch.object(show, 'load_nsimage', return_value=object()) as load, \
                 patch.object(show, 'draw_dynamic_map_overlay', return_value='overlay'), \
                 patch.object(show, 'warn_message') as warning:
                self.assertEqual(app._stage_map_with_elevation_profile(), 'overlay')
            load.assert_called_once_with(preferred)
            app._load_stage_entry.assert_not_called()
            app._load_stage_metadata.assert_called_once_with(preferred)
            warning.assert_not_called()

    def test_unreadable_optional_profile_map_still_falls_back_to_current_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.app(root)
            app._load_stage_entry = Mock(return_value=None)
            with patch.object(show, 'resolve_track_map_variant', return_value=root / 'stage-timelapse.png'), \
                 patch.object(show, 'warn_message') as warning:
                self.assertIs(app._stage_map_with_elevation_profile(), app.current_track_image)
            warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
