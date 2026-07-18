"""Tests for persistent video-audio normalization metadata and discovery."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from json_storage import atomic_write_json
from video_audio_normalization import (
    NORMALIZATION_MANIFEST,
    NORMALIZED_VIDEO_DIRECTORY,
    NormalizationSettings,
    discover_project_videos,
    manifest_path,
    normalization_status,
    source_signature,
    valid_normalized_video,
)


class VideoAudioNormalizationTests(unittest.TestCase):
    def test_discovery_excludes_generated_and_music_directories(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            source = project / "Album" / "clip.mov"
            source.parent.mkdir()
            source.write_bytes(b"source")
            for directory in ("audio", "trackimages", NORMALIZED_VIDEO_DIRECTORY):
                generated = project / directory / "copy.mov"
                generated.parent.mkdir()
                generated.write_bytes(b"generated")
            self.assertEqual(discover_project_videos(project), [source.resolve()])

    def test_current_copy_requires_matching_signature_and_parameters(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            source = project / "clip.mov"
            source.write_bytes(b"source")
            output = project / NORMALIZED_VIDEO_DIRECTORY / "clip.mov"
            output.parent.mkdir()
            output.write_bytes(b"normalized")
            settings = NormalizationSettings()
            atomic_write_json(
                manifest_path(project),
                {
                    "version": 1,
                    "entries": {
                        "clip.mov": {
                            "source_signature": source_signature(source),
                            "output": f"{NORMALIZED_VIDEO_DIRECTORY}/clip.mov",
                            "status": "normalized",
                            "parameters": settings.payload(),
                        }
                    },
                },
            )
            self.assertEqual(valid_normalized_video(project, source, settings), output.resolve())
            self.assertIsNone(
                valid_normalized_video(
                    project,
                    source,
                    NormalizationSettings(target_lufs=-18.0),
                )
            )
            source.write_bytes(b"changed source")
            self.assertIsNone(valid_normalized_video(project, source, settings))

    def test_status_distinguishes_current_missing_and_without_audio(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            current = project / "current.mov"
            silent = project / "silent.mov"
            missing = project / "missing.mov"
            for path in (current, silent, missing):
                path.write_bytes(path.name.encode())
            output = project / NORMALIZED_VIDEO_DIRECTORY / "current.mov"
            output.parent.mkdir()
            output.write_bytes(b"normalized")
            settings = NormalizationSettings()
            atomic_write_json(
                manifest_path(project),
                {
                    "version": 1,
                    "entries": {
                        "current.mov": {
                            "source_signature": source_signature(current),
                            "output": f"{NORMALIZED_VIDEO_DIRECTORY}/current.mov",
                            "status": "normalized",
                            "parameters": settings.payload(),
                        },
                        "silent.mov": {
                            "source_signature": source_signature(silent),
                            "status": "without_audio",
                            "parameters": settings.payload(),
                        },
                    },
                },
            )
            self.assertEqual(
                normalization_status(project, settings),
                {"total": 3, "current": 1, "stale": 0, "missing": 1, "without_audio": 1},
            )


if __name__ == "__main__":
    unittest.main()
