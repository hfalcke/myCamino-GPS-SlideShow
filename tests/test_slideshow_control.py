"""Tests for explicit #CONTROL slideshow directives."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from GPSTrackShow import (
    CaptionFontState,
    GPSTrackShowApp,
    Transition,
    build_caption_plan,
    parse_stage_descriptors,
)
from slideshow_control_format import compile_repeat_ranges, parse_control_directive


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
        app.repeat_ranges = ()
        app.default_caption_font = CaptionFontState(30.0, "regular", "System")
        app.font_state_by_row = {}
        app.music_controller = SimpleNamespace(
            repeat_row=Mock(),
            synchronize_row=Mock(),
        )
        app.narration_controller = SimpleNamespace(
            repeat_row=Mock(),
            synchronize_row=Mock(),
        )
        app._show_temporary_status_overlay = Mock()
        app._update_window_titles = Mock()
        app._advance = Mock()
        return app

    def test_repeat_dispatch_runs_in_source_order_before_boundary(self):
        app = self.make_app()
        app.playlist_lines = [
            "#MUSIC: #VOLUME 4, #REPEAT_UNTIL $DONE",
            "#NARRATOR: $VOICE, #REPEAT_UNTIL $DONE",
            "photo.jpeg",
            "#CONTROL: #LABEL $DONE",
        ]
        app.repeat_ranges = compile_repeat_ranges(app.playlist_lines)
        calls = []
        app.music_controller.repeat_row = lambda row: calls.append(("music", row))
        app.narration_controller.repeat_row = lambda row: calls.append(("narrator", row))

        self.assertTrue(
            app._run_repeated_directives(2, "media", lambda: calls.append(("display", 2)))
        )
        self.assertEqual(calls, [("music", 0), ("narrator", 1), ("display", 2)])

    def test_repeated_pause_defers_the_display_continuation(self):
        app = self.make_app()
        app.playlist_lines = [
            "#CONTROL: #PAUSE 2, #REPEAT_UNTIL $DONE",
            "photo.jpeg",
            "#CONTROL: #LABEL $DONE",
        ]
        app.repeat_ranges = compile_repeat_ranges(app.playlist_lines)
        callbacks = []
        app._schedule_callback = (
            lambda _seconds, callback: callbacks.append(callback) or Mock()
        )
        displayed = []

        self.assertTrue(
            app._run_repeated_directives(
                1,
                "media",
                lambda: displayed.append(True),
            )
        )
        self.assertEqual(displayed, [])
        callbacks.pop()()
        self.assertEqual(displayed, [True])

    def test_reconstruction_reapplies_active_duration_and_transition_ranges(self):
        app = self.make_app()
        app.playlist_lines = [
            "#CONTROL: #DURATION 4, #TRANSITION TIME_LAPSE, #REPEAT_UNTIL $DONE",
            "#CONTROL: #DURATION 8, #TRANSITION FADE",
            "photo.jpeg",
            "#CONTROL: #LABEL $DONE",
        ]
        app.control_directives = {
            index: directive
            for index, line in enumerate(app.playlist_lines)
            if (directive := parse_control_directive(line)) is not None
        }
        app.repeat_ranges = compile_repeat_ranges(app.playlist_lines)

        app._reconstruct_control_state_before(2)

        self.assertEqual(app.config.duration, 4.0)
        self.assertEqual(app._playback_style_name(), "TIME_LAPSE")

    def test_repeated_font_reapplies_after_a_later_normal_font(self):
        app = self.make_app()
        app.playlist_lines = [
            "#FONT: #SIZE 42, #REPEAT_UNTIL $DONE",
            "#FONT: #SIZE 18",
            "photo.jpeg",
            "#CONTROL: #LABEL $DONE",
        ]
        _captions, app.font_state_by_row = build_caption_plan(
            app.playlist_lines,
            app.default_caption_font,
        )
        app.repeat_ranges = compile_repeat_ranges(app.playlist_lines)

        self.assertEqual(app.font_state_by_row[2].size, 18.0)
        self.assertEqual(app._font_state_for_boundary(2).size, 42.0)

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

    def manual_style_app(self):
        app = self.make_app()
        app.initial_playback_style = 'TIME_LAPSE'
        app.time_lapse_active = True
        app.current_display_index = 3
        app.playlist_index = 4
        app._toggle_time_lapse_mode = Mock(side_effect=lambda: setattr(
            app, 'time_lapse_active', not app.time_lapse_active))
        return app

    def test_t_choice_survives_forward_backward_and_repeated_reconstruction(self):
        app = self.manual_style_app()
        app._cycle_playback_style(1)
        self.assertEqual(app._playback_style_name(), 'BLEND')
        for row in (6, 8, 2, 6, 2):
            app._reconstruct_control_state_before(row)
            self.assertEqual(app._playback_style_name(), 'BLEND')
        self.assertEqual(app.initial_playback_style, 'TIME_LAPSE')

    def test_every_manual_style_survives_navigation_without_changing_initial_settings(self):
        for style in ('FADE', 'SWITCH', 'EXPAND', 'COLLAGE', 'QUAD', 'RANDOM', 'TIME_LAPSE'):
            with self.subTest(style=style):
                app = self.manual_style_app()
                app._select_playback_style('BLEND', show_status=False)
                app._select_playback_style(style, show_status=False)
                app._reconstruct_control_state_before(10)
                self.assertEqual(app._playback_style_name(), style)
                self.assertEqual(app.config.transition, Transition.BLEND)

    def test_shift_t_choice_survives_reconstruction(self):
        app = self.manual_style_app()
        app._cycle_playback_style(-1)
        app._reconstruct_control_state_before(10)
        self.assertEqual(app._playback_style_name(), 'RANDOM')

    def test_manual_choice_supersedes_prior_commands_but_not_new_forward_commands(self):
        app = self.manual_style_app()
        app.control_directives = {
            0: parse_control_directive('#CONTROL: #TRANSITION QUAD, #DURATION 4'),
            6: parse_control_directive('#CONTROL: #TRANSITION FADE, #DURATION 7'),
        }
        app._cycle_playback_style(1)
        app._reconstruct_control_state_before(5)
        self.assertEqual(app._playback_style_name(), 'BLEND')
        self.assertEqual(app.config.duration, 4)
        app._reconstruct_control_state_before(8)
        self.assertEqual(app._playback_style_name(), 'FADE')
        self.assertEqual(app.config.duration, 7)
        self.assertIsNone(app.playback_style_override)

    def test_executed_idempotent_transition_also_ends_manual_override(self):
        app = self.manual_style_app()
        app._cycle_playback_style(1)
        app._execute_control_actions(parse_control_directive('#CONTROL: #TRANSITION BLEND'), 5)
        self.assertIsNone(app.playback_style_override)

    def test_active_repeat_transition_takes_precedence_at_navigation_boundary(self):
        app = self.manual_style_app()
        app.playlist_lines = [
            '#CONTROL: #TRANSITION QUAD, #REPEAT_UNTIL $DONE',
            'one.jpeg', 'two.jpeg', 'three.jpeg', 'four.jpeg',
            '#CONTROL: #LABEL $DONE',
        ]
        app.control_directives = {0: parse_control_directive(app.playlist_lines[0])}
        app.repeat_ranges = compile_repeat_ranges(app.playlist_lines)
        app._cycle_playback_style(1)
        app._reconstruct_control_state_before(4)
        self.assertEqual(app._playback_style_name(), 'QUAD')
        self.assertIsNone(app.playback_style_override)

    def test_resume_retains_manual_override_even_when_it_matches_a_prior_directive(self):
        app = self.manual_style_app()
        app.control_directives = {0: parse_control_directive('#CONTROL: #TRANSITION BLEND')}
        app._cycle_playback_style(1)
        state = app._control_state_snapshot()
        app = self.manual_style_app()
        app.control_directives = {0: parse_control_directive('#CONTROL: #TRANSITION BLEND')}
        app._reconstruct_control_state_before(4, state)
        app._reconstruct_control_state_before(0)
        self.assertEqual(app._playback_style_name(), 'BLEND')
        self.assertEqual(app.playback_style_override['row'], 3)

    def test_old_resume_style_is_preserved_on_subsequent_stage_navigation(self):
        app = self.manual_style_app()
        app._reconstruct_control_state_before(4, {'version': 1, 'duration': 8, 'transition': 'BLEND'})
        app._reconstruct_control_state_before(8)
        self.assertEqual(app._playback_style_name(), 'BLEND')

    def test_settled_command_arrow_commit_uses_manual_style(self):
        app = self.manual_style_app()
        app.current_display_index = 1
        app._cycle_playback_style(1)
        app.stages = parse_stage_descriptors(['#Map: first.png', 'one.jpeg', '#Map: next.png', 'two.jpeg'])
        app.pending_stage_index = 1
        app.stage_navigation_generation = 4
        app._cancel_time_lapse_stage = Mock()
        app._prime_context_before_index = Mock()
        app._sync_audio_row = Mock()
        app._start_time_lapse_stage = Mock()
        app._show_standard_stage_map_only = Mock()
        app._restore_committed_stage_header = Mock()
        app.schedule_callback = Mock()
        app._commit_pending_stage_navigation(4)
        app._show_standard_stage_map_only.assert_called_once_with(1, immediate=True)
        app._start_time_lapse_stage.assert_not_called()
        self.assertEqual(app._playback_style_name(), 'BLEND')


if __name__ == "__main__":
    unittest.main()
