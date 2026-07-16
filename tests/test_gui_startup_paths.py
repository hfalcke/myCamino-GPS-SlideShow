import tempfile
import unittest
from pathlib import Path

from GPSTrackShowGUI import (
    GPXTrackerController,
    build_argument_parser,
    ensure_default_project_playlist,
    ensure_project_audio_directory,
    playlist_belongs_to_audio_directory,
    resolve_gui_startup_paths,
)


class GUIStartupPathTests(unittest.TestCase):
    def test_project_audio_directory_is_created_and_recreated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project = Path(temporary_directory)
            audio_dir = ensure_project_audio_directory(project)
            self.assertEqual(audio_dir, (project / "audio").resolve())
            self.assertTrue(audio_dir.is_dir())
            audio_dir.rmdir()
            self.assertTrue(ensure_project_audio_directory(project).is_dir())

    def test_playlist_must_be_directly_inside_project_audio_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            audio_dir = ensure_project_audio_directory(temporary_directory)
            playlist = audio_dir / "Morning.playlist"
            nested = audio_dir / "Album" / "Nested.playlist"
            wrong_type = audio_dir / "Morning.txt"
            self.assertTrue(
                playlist_belongs_to_audio_directory(playlist, audio_dir)
            )
            self.assertFalse(
                playlist_belongs_to_audio_directory(nested, audio_dir)
            )
            self.assertFalse(
                playlist_belongs_to_audio_directory(wrong_type, audio_dir)
            )

    def test_default_empty_playlist_uses_adventure_name_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            playlist = ensure_default_project_playlist(
                temporary_directory,
                "Test Reise",
            )
            self.assertEqual(playlist.name, "Test Reise.playlist")
            self.assertEqual(playlist.read_text(encoding="utf-8"), "")

            playlist.write_text("$START\nsong.mp3\n", encoding="utf-8")
            self.assertEqual(
                ensure_default_project_playlist(
                    temporary_directory,
                    "Test Reise",
                ).read_text(encoding="utf-8"),
                "$START\nsong.mp3\n",
            )

    def test_status_update_does_not_reenter_the_appkit_event_loop(self):
        class StatusLabel:
            def __init__(self):
                self.value = None
                self.needs_display = False

            def setStringValue_(self, value):
                self.value = value

            def setNeedsDisplay_(self, value):
                self.needs_display = bool(value)

        controller = type("ControllerStub", (), {})()
        controller.status_label = StatusLabel()

        GPXTrackerController.set_status(controller, "Starting")

        self.assertEqual(controller.status_label.value, "Starting")
        self.assertTrue(controller.status_label.needs_display)

    def test_positional_directory_becomes_project_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            args = build_argument_parser().parse_args([str(directory)])
            project_dir, adventure_file = resolve_gui_startup_paths(
                args.project_directory,
                args.startup_path,
            )

            self.assertEqual(project_dir, directory.resolve())
            self.assertIsNone(adventure_file)

    def test_positional_adventure_remains_an_adventure_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            adventure = Path(temporary_directory) / "Trip.adv"
            project_dir, adventure_file = resolve_gui_startup_paths(None, adventure)

            self.assertIsNone(project_dir)
            self.assertEqual(adventure_file, adventure.resolve())

    def test_conflicting_directory_arguments_are_rejected(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with self.assertRaises(ValueError):
                resolve_gui_startup_paths(first, second)


if __name__ == "__main__":
    unittest.main()
