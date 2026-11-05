"""Session master volume updates active audio without changing authored settings."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import GPSTrackShow as show


class SlideshowVolumeTests(unittest.TestCase):
    def app(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.config = SimpleNamespace(music_volume_percent=60, narration_volume_percent=80,
            video_volume_percent=90, session_volume_percent=100)
        app._show_temporary_status_overlay = Mock()
        music = show.BackgroundMusicController.__new__(show.BackgroundMusicController)
        music.config = app.config
        music.players = [Mock(), Mock()]
        music.fade_envelopes = [.25, .75]
        music.volume_level = 6
        music.narration_gain_envelope = .5
        narration = show.NarrationController.__new__(show.NarrationController)
        narration.config = app.config
        narration.players = [Mock(), Mock()]
        narration.fade_envelopes = [.4, .6]
        narration.paused = False
        narration.user_enabled = True
        app.music_controller = music
        app.narration_controller = narration
        app.narration_video_envelope = .25
        app.photo_presenter = SimpleNamespace(video_player=Mock())
        app.map_presenter = SimpleNamespace(video_player=Mock())
        app.active_photo_presenter = app.photo_presenter
        app.time_lapse_video_player = Mock()
        return app

    def test_master_gain_preserves_channel_settings_fades_and_ducking(self):
        app = self.app()
        with patch.object(show.BackgroundMusicController, 'effective_playing', new=property(lambda self: True)):
            app._change_audio_volume(-50)
        self.assertEqual(app.config.session_volume_percent, 50)
        self.assertEqual((app.config.music_volume_percent, app.config.narration_volume_percent,
                          app.config.video_volume_percent), (60, 80, 90))
        for player, expected in zip(app.music_controller.players + app.narration_controller.players,
                                    (.025, .075, .16, .24)):
            self.assertAlmostEqual(player.setVolume_.call_args.args[0], expected)
        for player in (app.photo_presenter.video_player, app.map_presenter.video_player,
                       app.time_lapse_video_player):
            player.setVolume_.assert_called_once_with(.1125)
            player.play.assert_not_called()
            player.pause.assert_not_called()
        self.assertEqual(app.music_controller.volume_level, 6)

    def test_zero_restore_clamping_and_muted_narration(self):
        app = self.app()
        app.narration_controller.user_enabled = False
        with patch.object(show.BackgroundMusicController, 'effective_playing', new=property(lambda self: False)):
            app._change_audio_volume(-200)
            self.assertEqual(app.config.session_volume_percent, 0)
            app._change_audio_volume(200)
            self.assertEqual(app.config.session_volume_percent, 100)
        for player in app.narration_controller.players + app.music_controller.players:
            player.setVolume_.assert_called_with(0)
            player.play.assert_not_called()
        app.narration_controller.user_enabled = True
        app.narration_controller.paused = True
        app.narration_controller.refresh_volume()
        for player in app.narration_controller.players:
            player.setVolume_.assert_called_with(0)
        app.narration_controller.paused = False
        app.narration_controller.refresh_volume()
        self.assertAlmostEqual(app.narration_controller.players[0].setVolume_.call_args.args[0], .32)

    def test_narration_crossfade_uses_new_gain_for_pending_callbacks(self):
        app = self.app()
        narration = app.narration_controller
        narration.fade_handles = []
        narration.disposed = False
        callbacks = []
        narration.schedule_callback = lambda _delay, callback: callbacks.append(callback) or Mock()
        outgoing, incoming = narration.players
        narration._fade_players(outgoing, incoming, .1)
        callbacks[0]()
        before = list(narration.fade_envelopes)
        app.config.session_volume_percent = 0
        narration.refresh_volume()
        callbacks[-1]()
        self.assertEqual(narration.fade_envelopes, [0, 1])
        self.assertNotEqual(before, narration.fade_envelopes)
        app.config.session_volume_percent = 50
        narration.refresh_volume()
        outgoing.setVolume_.assert_called_with(0)
        incoming.setVolume_.assert_called_with(.4)
        outgoing.pause.assert_called_once()
        incoming.play.assert_called_once()

    def test_key_bindings_keep_command_duration_and_control_audio_distinct(self):
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app._change_audio_volume = Mock()
        app._change_time_lapse_duration = Mock()
        app._change_duration = Mock()
        handlers = {}
        def monitor(mask, handler):
            handlers[mask] = handler
            return object()
        with patch.object(show, 'NSEvent') as event_class:
            event_class.addLocalMonitorForEventsMatchingMask_handler_.side_effect = monitor
            app._install_key_monitor()
        key = handlers[show.NSEventMaskKeyDown]
        def press(chars, modifiers, raw=None):
            event = SimpleNamespace(charactersIgnoringModifiers=lambda: chars,
                characters=lambda: chars if raw is None else raw, modifierFlags=lambda: modifiers)
            self.assertIsNone(key(event))
        press('+', show.NSEventModifierFlagCommand)
        press('-', show.NSEventModifierFlagCommand)
        self.assertEqual(app._change_time_lapse_duration.call_args_list, [((5.,),), ((-5.,),)])
        app._change_audio_volume.assert_not_called()
        press('+', show.NSEventModifierFlagControl, raw='\x1d')
        press('-', show.NSEventModifierFlagControl, raw='\x1f')
        press('=', show.NSEventModifierFlagControl)
        self.assertEqual(app._change_audio_volume.call_args_list, [((5.,),), ((-5.,),), ((5.,),)])
        press('+', 0)
        press('-', 0)
        self.assertEqual(app._change_duration.call_args_list, [((1.,),), ((-1.,),)])

    def test_help_lists_both_shortcuts(self):
        help_text = '\n'.join(show.KEY_HELP_LINES)
        self.assertIn('Cmd-+ / Cmd--', help_text)
        self.assertIn('Ctrl-+ / Ctrl--', help_text)

    def test_new_player_session_resets_master_without_changing_channel_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / 'trip.lst'
            control.write_text('photo.jpeg\n')
            config = show.config_from_options(root, inputlist=control, music_volume_percent=45)
            object.__setattr__(config, 'session_volume_percent', 25.)
            with ExitStack() as stack:
                for name in ('NSApplication', 'BackgroundMusicController', 'NarrationController'):
                    stack.enter_context(patch.object(show, name))
                stack.enter_context(patch.object(show.signal, 'signal'))
                for name in ('_apply_start_track', '_build_windows', '_install_key_monitor'):
                    stack.enter_context(patch.object(show.GPSTrackShowApp, name))
                show.GPSTrackShowApp(config)
            self.assertEqual(config.session_volume_percent, 100.)
            self.assertEqual(config.music_volume_percent, 45.)


if __name__ == '__main__':
    unittest.main()
