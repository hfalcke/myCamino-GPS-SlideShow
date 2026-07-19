"""Tests for explicit #CONTROL slideshow directives."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from GPSTrackShow import GPSTrackShowApp, Transition, parse_stage_descriptors
from slideshow_control_format import parse_control_directive


class SlideshowControlTests(unittest.TestCase):
    def make_app(self):
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(
            duration=3.0,
            transition=Transition.BLEND,
            time_lapse_stages=False,
            debug=False,
        )
        app.playlist_lines = ["#CONTROL: #LABEL $START", "photo.jpeg"]
        app.stages = []
        app.time_lapse_active = False
        app.time_lapse_stage = None
        app.random_transition_mode = False
        app.active_transition = Transition.BLEND
        app.transition_change_armed = False
        app.initial_duration = 3.0
        app.initial_playback_style = "BLEND"
        app.control_directives = {}
        app.control_labels = {"start": 0}
        app.control_flow_steps = 0
        app.control_non_display_limit = 1000
        app.control_pause_active = False
        app.control_pause_resume_callback = None
        app.active_callback = None
        app._show_temporary_status_overlay = Mock()
        app._update_window_titles = Mock()
        app._advance = Mock()
        return app

    def test_duration_and_transition_apply_in_order(self):
        app = self.make_app()
        directive = parse_control_directive(
            "#CONTROL: #DURATION 6.5, #TRANSITION FADE"
        )
        self.assertFalse(app._execute_control_actions(directive, 0))
        self.assertEqual(app.config.duration, 6.5)
        self.assertEqual(app._playback_style_name(), "FADE")

    def test_pause_defers_remaining_actions_and_keeps_music_untouched(self):
        app = self.make_app()
        callbacks = []
        app._schedule_callback = lambda _seconds, callback: callbacks.append(callback) or Mock()
        directive = parse_control_directive(
            "#CONTROL: #PAUSE 2, #DURATION 7"
        )
        self.assertTrue(app._execute_control_actions(directive, 0))
        self.assertTrue(app.control_pause_active)
        self.assertEqual(app.config.duration, 3.0)
        callbacks.pop()()
        self.assertFalse(app.control_pause_active)
        self.assertEqual(app.config.duration, 7.0)
        app._advance.assert_called_once_with()

    def test_positive_pause_resets_non_display_loop_guard(self):
        app = self.make_app()
        app.control_flow_steps = 800
        app._schedule_callback = lambda _seconds, _callback: Mock()
        directive = parse_control_directive("#CONTROL: #PAUSE 2")
        self.assertTrue(app._execute_control_actions(directive, 0))
        self.assertEqual(app.control_flow_steps, 0)

    def test_goto_schedules_first_case_insensitive_label(self):
        app = self.make_app()
        callbacks = []
        app._schedule_callback = lambda _seconds, callback: callbacks.append(callback) or Mock()
        app._jump_to_playlist_row = Mock(return_value=True)
        directive = parse_control_directive("#CONTROL: #GOTO $START")
        self.assertTrue(app._execute_control_actions(directive, 1))
        callbacks.pop()()
        app._jump_to_playlist_row.assert_called_once_with(
            0,
            reconstruct_control=False,
        )

    def test_transition_before_goto_is_applied_before_transfer(self):
        app = self.make_app()
        callbacks = []
        app._schedule_callback = lambda _seconds, callback: callbacks.append(callback) or Mock()
        app._jump_to_playlist_row = Mock(return_value=True)
        directive = parse_control_directive(
            "#CONTROL: #TRANSITION QUAD, #GOTO $START, #DURATION 9"
        )
        self.assertTrue(app._execute_control_actions(directive, 1))
        self.assertEqual(app._playback_style_name(), "QUAD")
        self.assertEqual(app.config.duration, 3.0)
        callbacks.pop()()
        app._jump_to_playlist_row.assert_called_once_with(
            0,
            reconstruct_control=False,
        )

    def test_time_lapse_reenters_stage_after_jump_to_label(self):
        app = self.make_app()
        app.playlist_lines = [
            "#Map: stage.png",
            "#CONTROL: #LABEL $TEST",
            "photo.jpeg | 12:00 | 50.0, 7.0 | Place",
            "#CONTROL: #GOTO $TEST",
        ]
        app.stages = parse_stage_descriptors(app.playlist_lines)
        app.control_directives = {
            1: parse_control_directive(app.playlist_lines[1]),
            3: parse_control_directive(app.playlist_lines[3]),
        }
        app.running = True
        app.playlist_index = 1
        app.time_lapse_active = True
        app.time_lapse_stage = None
        app.resume_start_pending = False
        app.music_controller = SimpleNamespace(synchronize_row=Mock())
        app._prime_context_before_index = Mock()
        app._start_time_lapse_stage = Mock()
        app._handle_photo = Mock()

        GPSTrackShowApp._advance(app)

        app._prime_context_before_index.assert_called_once_with(0)
        call = app._start_time_lapse_stage.call_args
        self.assertEqual(call.args, (0, "stage.png"))
        self.assertEqual(call.kwargs["resume_media"][0], 2)
        self.assertEqual(call.kwargs["resume_media"][1].source_name, "photo.jpeg")
        self.assertIsNone(call.kwargs["relation"])
        app._handle_photo.assert_not_called()
        self.assertEqual(app._playback_style_name(), "TIME_LAPSE")

    def test_end_stops_processing_later_actions(self):
        app = self.make_app()
        app._handle_playlist_end = Mock()
        directive = parse_control_directive(
            "#CONTROL: #END, #DURATION 9"
        )
        self.assertTrue(app._execute_control_actions(directive, 0))
        app._handle_playlist_end.assert_called_once_with()
        self.assertEqual(app.config.duration, 3.0)

    def test_reconstruction_ignores_flow_and_restores_state(self):
        app = self.make_app()
        app.control_directives = {
            0: parse_control_directive("#CONTROL: #DURATION 4, #TRANSITION QUAD"),
            1: parse_control_directive("#CONTROL: #GOTO $START, #PAUSE 20"),
        }
        app._reconstruct_control_state_before(2)
        self.assertEqual(app.config.duration, 4.0)
        self.assertEqual(app._playback_style_name(), "QUAD")
        app._reconstruct_control_state_before(
            2,
            {"version": 1, "duration": 8.0, "transition": "SWITCH"},
        )
        self.assertEqual(app.config.duration, 8.0)
        self.assertEqual(app._playback_style_name(), "SWITCH")


if __name__ == "__main__":
    unittest.main()
