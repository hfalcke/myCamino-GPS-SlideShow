from pathlib import Path
import tempfile
import unittest

from project_media_watcher import (
    MediaDiscoveryState,
    MediaFileSignature,
    discover_project_media,
)


class ProjectMediaWatcherTests(unittest.TestCase):
    def test_discovery_excludes_generated_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "photo.jpg").write_bytes(b"photo")
            (root / "album").mkdir()
            (root / "album" / "clip.mov").write_bytes(b"video")
            (root / "trackimages").mkdir()
            (root / "trackimages" / "map.jpg").write_bytes(b"generated")
            result = discover_project_media(root, {".jpg", ".mov"}, {"trackimages"})
            self.assertEqual({path.name for path in result}, {"photo.jpg", "clip.mov"})

    def test_initial_files_are_reported_separately(self):
        state = MediaDiscoveryState(1.0)
        path = Path("photo.jpg")
        initial, ready = state.update({path: MediaFileSignature(10, 1)}, 0.0)
        self.assertEqual(initial, [path])
        self.assertEqual(ready, [])

    def test_new_file_must_remain_stable(self):
        state = MediaDiscoveryState(1.0)
        state.update({}, 0.0)
        path = Path("video.mov")
        self.assertEqual(state.update({path: MediaFileSignature(10, 1)}, 0.2), ([], []))
        self.assertEqual(state.update({path: MediaFileSignature(20, 2)}, 0.8), ([], []))
        self.assertEqual(state.update({path: MediaFileSignature(20, 2)}, 1.7), ([], []))
        self.assertEqual(state.update({path: MediaFileSignature(20, 2)}, 1.81), ([], [path]))

    def test_removed_path_can_be_discovered_again(self):
        state = MediaDiscoveryState(0.0)
        path = Path("photo.jpg")
        state.update({path: MediaFileSignature(10, 1)}, 0.0)
        state.update({}, 1.0)
        self.assertEqual(
            state.update({path: MediaFileSignature(10, 1)}, 2.0)[1], [path]
        )


if __name__ == "__main__":
    unittest.main()
