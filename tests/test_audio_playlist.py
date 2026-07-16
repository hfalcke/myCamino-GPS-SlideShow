"""Tests for recursive playlists and explicit #MUSIC directives."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from audio_playlist import (
    MusicTransportState,
    album_directories,
    audio_files_in_directory,
    generated_playlist_text,
    load_audio_playlist,
    updated_playlist_text,
)
from slideshow_control_format import MusicSyntaxError, parse_music_directive
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

    def test_generic_loop_and_bad_volume_are_rejected(self):
        with self.assertRaises(MusicSyntaxError):
            parse_music_directive("#MUSIC: #LOOP")
        with self.assertRaises(MusicSyntaxError):
            parse_music_directive("#MUSIC: #VOLUME 10")

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
