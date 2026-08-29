"""Tests for recursive playlists and explicit #MUSIC directives."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import PropertyMock, patch

from audio_playlist import (
    MusicTransportState,
    album_directories,
    audio_files_in_directory,
    generated_playlist_text,
    load_audio_playlist,
    resolve_audio_selection,
    updated_playlist_text,
)
from slideshow_control_format import (
    CONTROL_TRANSITIONS,
    ControlSyntaxError,
    MusicSyntaxError,
    AudioSelectionSyntaxError,
    control_labels,
    parse_control_directive,
    parse_music_directive,
    parse_narrator_directive,
    parse_play_directive,
    serialize_control_parameters,
)
from GPSTrackShow import BackgroundMusicController


class AudioPlaylistTests(unittest.TestCase):
    def _controller_without_players(self, playlist):
        controller = BackgroundMusicController.__new__(BackgroundMusicController)
        controller.playlist = playlist
        controller.transport = MusicTransportState(playlist)
        controller.warned_music_errors = set()
        controller.control_enabled = True
        controller.user_enabled = True
        controller.slideshow_paused = False
        controller.video_paused = False
        controller.volume_level = 9
        controller.current_index = None
        controller.queue_resume_index = None
        controller.queue_resume_seconds = None
        controller.players = [None, None]
        controller.active_slot = 0
        controller.overlay_callback = lambda *_args: None
        return controller

    def test_volume_level_updates_both_crossfade_players(self):
        class Player:
            def __init__(self):
                self.volume = None

            def setVolume_(self, value):
                self.volume = float(value)

        controller = self._controller_without_players(SimpleNamespace(files=[]))
        controller.config = SimpleNamespace(music_volume_percent=60.0)
        controller.fade_envelopes = [0.25, 0.75]
        controller.players = [Player(), Player()]
        controller._set_volume_level(3, False)
        self.assertAlmostEqual(controller.players[0].volume, 0.05)
        self.assertAlmostEqual(controller.players[1].volume, 0.15)

    def test_music_directive_parses_csv_paths_and_ordered_commands(self):
        directive = parse_music_directive(
            '#MUSIC: #ON, #LOOPLINE, $INTRO, "Album/01 Auf,Seele.mp3", #VOLUME 7'
        )
        self.assertEqual(
            [action.kind for action in directive.actions],
            ["on", "loop_line", "target_label", "target_path", "volume"],
        )
        self.assertEqual(directive.actions[3].value, "Album/01 Auf,Seele.mp3")
        self.assertEqual(directive.actions[4].value, 7)

    def test_music_directive_accepts_quoted_path_with_spaces(self):
        directive = parse_music_directive(
            '#MUSIC: "Album Name/Song Number One.mp3"'
        )
        self.assertEqual(len(directive.actions), 1)
        self.assertEqual(directive.actions[0].kind, "target_path")
        self.assertEqual(
            directive.actions[0].value,
            "Album Name/Song Number One.mp3",
        )

    def test_play_and_narrator_share_mixed_selection_grammar(self):
        play = parse_play_directive('#PLAY: $INTRO, $A - $C, "A song, live.mp3"')
        narration = parse_narrator_directive("#NARRATOR: $CHAPTER, $ONE - $THREE")
        self.assertEqual([item.kind for item in play.items], ["label", "range", "path"])
        self.assertEqual(play.items[1].value, ("A", "C"))
        self.assertEqual(play.items[2].value, "A song, live.mp3")
        self.assertEqual([item.kind for item in narration.items], ["label", "range"])
        with self.assertRaises(AudioSelectionSyntaxError):
            parse_play_directive("#PLAY: $A - song.mp3")
        with self.assertRaises(AudioSelectionSyntaxError):
            parse_play_directive("#PLAY: Chapter one.mp3")
        escaped = parse_play_directive(r"#PLAY: Chapter\ one.mp3")
        self.assertEqual(escaped.items[0].value, "Chapter one.mp3")

    def test_audio_selection_resolves_ranges_and_preserves_order(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a.mp3", "b.mp3", "c.mp3"):
                (root / name).touch()
            playlist_path = root / "test.playlist"
            playlist_path.write_text("$A\na.mp3\n$B\nb.mp3\n$C\nc.mp3\n", encoding="utf-8")
            playlist = load_audio_playlist(root, playlist_path)
            indexes, warnings = resolve_audio_selection(
                playlist,
                parse_play_directive("#PLAY: $C, $A - $B, $C"),
            )
            self.assertEqual(indexes, (2, 0, 1, 2))
            self.assertEqual(warnings, ())

    def test_play_directive_uses_temporary_queue(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a.mp3", "b.mp3", "c.mp3"):
                (root / name).touch()
            playlist = load_audio_playlist(root)
            controller = self._controller_without_players(playlist)
            controller.current_index = 1
            controller.transport.set_playlist(1)
            controller._execute_play_directive(
                parse_play_directive("#PLAY: a.mp3, c.mp3"),
                False,
            )
            self.assertEqual(controller.transport.mode, "queue")
            self.assertEqual(controller.transport.sequence, (0, 2))
            self.assertEqual(controller.transport.continuation_index, 1)

    def test_generic_loop_and_bad_volume_are_rejected(self):
        with self.assertRaises(MusicSyntaxError):
            parse_music_directive("#MUSIC: #LOOP")
        with self.assertRaises(MusicSyntaxError):
            parse_music_directive("#MUSIC: #VOLUME 10")

    def test_music_goto_is_an_alias_for_jump(self):
        jump = parse_music_directive("#MUSIC: #JUMP $INTRO")
        goto = parse_music_directive("#MUSIC: #GOTO $INTRO")
        self.assertEqual(goto.actions, jump.actions)

    def test_control_directive_parses_ordered_commands_and_styles(self):
        directive = parse_control_directive(
            "#CONTROL: #LABEL $INTRO, #DURATION 4.5, #TRANSITION time-lapse, #PAUSE 2"
        )
        self.assertEqual(
            [action.kind for action in directive.actions],
            ["label", "duration", "transition", "pause"],
        )
        self.assertEqual(directive.actions[2].value, "TIME_LAPSE")
        self.assertEqual(
            set(CONTROL_TRANSITIONS),
            {"TIME_LAPSE", "BLEND", "FADE", "SWITCH", "EXPAND", "COLLAGE", "QUAD", "RANDOM"},
        )

    def test_control_directive_serializes_canonical_transition(self):
        directive = parse_control_directive(
            "#CONTROL: #jump $Intro, #transition time-lapse, #duration 5.0"
        )
        self.assertEqual(
            serialize_control_parameters(directive),
            "#GOTO $Intro, #TRANSITION TIME_LAPSE, #DURATION 5",
        )

    def test_control_directive_rejects_bad_numbers_and_styles(self):
        for line in (
            "#CONTROL: #DURATION 0",
            "#CONTROL: #PAUSE -1",
            "#CONTROL: #TRANSITION SPIN",
            "#CONTROL: bare text",
        ):
            with self.subTest(line=line), self.assertRaises(ControlSyntaxError):
                parse_control_directive(line)

    def test_control_labels_keep_first_and_report_duplicates(self):
        labels, duplicates = control_labels(
            [
                "#CONTROL: #LABEL $Start",
                "image.jpeg | 12:00 | 1, 2 | Place",
                "#CONTROL: #LABEL $START",
            ]
        )
        self.assertEqual(labels, {"start": 0})
        self.assertEqual(duplicates, (("START", 0, 2),))

    def test_recursive_order_album_membership_and_dollar_labels(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            album = directory / "Album"
            album.mkdir()
            (album / "Zulu.mp3").touch()
            (album / "alpha.m4a").touch()
            (directory / "middle.wav").touch()
            files = audio_files_in_directory(directory)
            self.assertEqual(
                [path.relative_to(directory).as_posix() for path in files],
                ["Album/alpha.m4a", "Album/Zulu.mp3", "middle.wav"],
            )
            self.assertEqual(album_directories(files, directory), (album.resolve(),))

            playlist_path = directory / "Trip.playlist"
            playlist_path.write_text(
                "$ALB_ALBUM\n$START\nAlbum/alpha\n$NEXT\nAlbum/Zulu.mp3\nmiddle.wav\n",
                encoding="utf-8",
            )
            playlist = load_audio_playlist(directory, playlist_path)
            self.assertEqual(playlist.index_for_label("$start"), 0)
            self.assertEqual(playlist.index_for_label("NEXT"), 1)
            self.assertEqual(playlist.album_for_target(0), (0, 1))

    def test_hash_playlist_labels_are_not_accepted_as_legacy_syntax(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "one.mp3").touch()
            source = directory / "Trip.playlist"
            source.write_text("#OLD\none.mp3\n", encoding="utf-8")
            playlist = load_audio_playlist(directory, source)
            self.assertIsNone(playlist.index_for_label("OLD"))
            self.assertIn("must start with $", playlist.warnings[0])

    def test_ambiguous_extensionless_file_is_skipped(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "same.mp3").touch()
            (directory / "same.wav").touch()
            playlist_path = directory / "Trip.playlist"
            playlist_path.write_text("same\n", encoding="utf-8")
            playlist = load_audio_playlist(directory, playlist_path)
            self.assertFalse(playlist.files)
            self.assertIn("Ambiguous", playlist.warnings[0])

    def test_generated_labels_are_unique_and_paths_are_relative(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            album = root / "Album"
            album.mkdir()
            files = [album / "Long identical filename.mp3", album / "Long-identical-filename.wav"]
            for path in files:
                path.touch()
            lines = generated_playlist_text(files, root).splitlines()
            labels = [line[1:] for line in lines if line.startswith("$")]
            self.assertEqual(len(labels), 3)  # album plus two files
            self.assertEqual(len({label.casefold() for label in labels}), 3)
            self.assertTrue(all(len(label) <= 12 for label in labels))
            self.assertIn("Album/Long identical filename.mp3", lines)

    def test_update_preserves_content_and_appends_only_missing_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "one.mp3"
            second = root / "two.mp3"
            first.touch()
            second.touch()
            existing = "$CUSTOM\none.mp3\n# user comment\n"
            updated, missing = updated_playlist_text(existing, [first, second], root)
            self.assertTrue(updated.startswith(existing))
            self.assertEqual(missing, (second.resolve(),))
            self.assertIn("two.mp3", updated)

    def test_transport_queue_returns_to_interrupted_playlist_index(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = []
            for name in ("one.mp3", "two.mp3", "three.mp3"):
                path = root / name
                path.touch()
                files.append(path)
            playlist = load_audio_playlist(root)
            state = MusicTransportState(playlist)
            state.set_playlist(1)
            self.assertEqual(state.set_queue((2, 0)), 2)
            self.assertEqual(state.next_index(), 0)
            self.assertEqual(state.next_index(), 1)
            self.assertEqual(state.next_index(), 2)

    def test_controller_remembers_interrupted_title_time_for_queue(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.mp3").touch()
            controller = self._controller_without_players(load_audio_playlist(root))
            controller.current_index = 0
            player = object()
            controller._player = lambda: player
            controller._current_seconds = lambda _player: 12.5
            controller._effective_crossfade = lambda _player: 2.0
            controller._remember_queue_resume_position(0)
            self.assertEqual(controller.queue_resume_index, 0)
            self.assertEqual(controller.queue_resume_seconds, 14.5)

    def test_audio_resume_snapshot_remaps_paths_after_playlist_reordering(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("one.mp3", "two.mp3", "three.mp3"):
                (root / name).touch()
            original_playlist_path = root / "Original.playlist"
            original_playlist_path.write_text(
                "one.mp3\ntwo.mp3\nthree.mp3\n",
                encoding="utf-8",
            )
            original = load_audio_playlist(root, original_playlist_path)
            controller = self._controller_without_players(original)
            controller.config = SimpleNamespace(
                music_playlist=original_playlist_path,
                music_volume_percent=65.0,
            )
            controller.started = True
            controller.transport.set_loop("loop_range", (0, 2))
            controller.transport.sequence_position = 1
            controller.transport.current_index = 2
            controller.current_index = 2
            controller.queue_resume_index = 1
            controller.queue_resume_seconds = 8.25
            controller.user_enabled = False
            controller.control_enabled = True
            controller.volume_level = 4
            controller._player = lambda: object()
            controller._current_seconds = lambda _player: 37.5
            with patch.object(
                BackgroundMusicController,
                "available",
                new_callable=PropertyMock,
                return_value=True,
            ):
                snapshot = controller.resume_state_snapshot()

            reordered_path = root / "Reordered.playlist"
            reordered_path.write_text(
                "three.mp3\none.mp3\ntwo.mp3\n",
                encoding="utf-8",
            )
            restored = self._controller_without_players(
                load_audio_playlist(root, reordered_path)
            )
            restored.config = SimpleNamespace(music_volume_percent=65.0)
            switched = []
            restored._switch_to = (
                lambda index, immediate=False, resume_seconds=None,
                fade_in_from_silence=False: switched.append(
                    (index, immediate, resume_seconds, fade_in_from_silence)
                )
            )
            restored._apply_all_player_gains = lambda: None

            self.assertTrue(restored.restore_resume_state(snapshot))
            self.assertEqual(restored.transport.mode, "loop_range")
            self.assertEqual(restored.transport.sequence, (1, 0))
            self.assertEqual(restored.transport.sequence_position, 1)
            self.assertEqual(restored.transport.current_index, 0)
            self.assertEqual(restored.queue_resume_index, 2)
            self.assertEqual(restored.queue_resume_seconds, 8.25)
            self.assertFalse(restored.user_enabled)
            self.assertTrue(restored.control_enabled)
            self.assertEqual(restored.volume_level, 4)
            self.assertEqual(switched, [(0, False, 37.5, True)])

    def test_audio_resume_rejects_a_missing_saved_title(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.mp3").touch()
            controller = self._controller_without_players(load_audio_playlist(root))
            controller.config = SimpleNamespace(music_volume_percent=65.0)
            self.assertFalse(
                controller.restore_resume_state(
                    {
                        "version": 1,
                        "current_file": "missing.mp3",
                        "transport": {"mode": "playlist", "sequence": []},
                    }
                )
            )

    def test_transport_loop_range_is_inclusive(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("one.mp3", "two.mp3", "three.mp3"):
                (root / name).touch()
            state = MusicTransportState(load_audio_playlist(root))
            self.assertEqual(state.set_loop("loop_range", (0, 1)), 0)
            self.assertEqual([state.next_index(), state.next_index(), state.next_index()], [1, 0, 1])

    def test_directive_actions_preserve_gate_order(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.mp3").touch()
            playlist_path = root / "Trip.playlist"
            playlist_path.write_text("$A\none.mp3\n", encoding="utf-8")
            playlist = load_audio_playlist(root, playlist_path)

            controller = self._controller_without_players(playlist)
            controller._execute_directive(
                parse_music_directive("#MUSIC: $A, #OFF"),
                switch_player=False,
                show_status=False,
            )
            self.assertFalse(controller.control_enabled)

            controller = self._controller_without_players(playlist)
            controller._execute_directive(
                parse_music_directive("#MUSIC: #OFF, $A"),
                switch_player=False,
                show_status=False,
            )
            self.assertTrue(controller.control_enabled)

    def test_directive_transport_modes_replace_each_other(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            album = root / "Album"
            album.mkdir()
            for path in (album / "one.mp3", album / "two.mp3", root / "three.mp3"):
                path.touch()
            playlist_path = root / "Trip.playlist"
            playlist_path.write_text(
                "$ALBUM\n$A\nAlbum/one.mp3\n$B\nAlbum/two.mp3\n$C\nthree.mp3\n",
                encoding="utf-8",
            )
            controller = self._controller_without_players(load_audio_playlist(root, playlist_path))

            controller._execute_directive(parse_music_directive("#MUSIC: #LOOPRANGE $A $B"), False, False)
            self.assertEqual((controller.transport.mode, controller.transport.sequence), ("loop_range", (0, 1)))
            controller._execute_directive(parse_music_directive("#MUSIC: #LOOPALBUM, $B"), False, False)
            self.assertEqual((controller.transport.mode, controller.transport.sequence), ("loop_album", (0, 1)))
            controller._execute_directive(parse_music_directive("#MUSIC: #LOOPALL"), False, False)
            self.assertEqual(controller.transport.sequence, (0, 1, 2))
            controller._execute_directive(parse_music_directive("#MUSIC: #CONTINUE"), False, False)
            self.assertEqual(controller.transport.mode, "playlist")
            controller._execute_directive(parse_music_directive("#MUSIC: #JUMP $B"), False, False)
            self.assertEqual(controller.transport.current_index, 1)
            self.assertEqual(controller.transport.next_index(), 2)
            controller._execute_directive(
                parse_music_directive("#MUSIC: #LOOPLINE, $A, #JUMP $B, $C"),
                False,
                False,
            )
            self.assertEqual((controller.transport.mode, controller.transport.sequence), ("queue", (2,)))


if __name__ == "__main__":
    unittest.main()
