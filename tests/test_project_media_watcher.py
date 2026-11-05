from pathlib import Path
import json
import unicodedata
import tempfile
import unittest

from project_media_watcher import (
    MediaDiscoveryState,
    MediaFileSignature,
    discover_project_media,
)


class ProjectMediaWatcherTests(unittest.TestCase):
    def test_generated_root_maps_are_not_photographs_but_ordinary_pngs_are(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "copied.adv").write_text(json.dumps({'track_map_base': 'Old Name'}))
            (root / "trip.lst").write_text('#Overviewmap: Old Name.png\n# #Map: Old Stage.png\n')
            for name in ('Old Name.png', 'Old Stage.png', 'photo.png'):
                (root / name).write_bytes(b'image')
            result = discover_project_media(root, {'.png'})
            self.assertEqual({p.name for p in result}, {'photo.png'})

    def test_legacy_summary_excludes_managed_images_even_before_adventure_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Old-summary.json').write_text(json.dumps({
                'source_gpx': '/old/Old.gpx', 'tracks': [{'track_plot_image_filename': '/old/0001_Stage.png'}]}))
            for name in ('Old.png', '0001_Stage.png', 'photo.png'):
                (root / name).write_bytes(b'image')
            self.assertEqual({p.name for p in discover_project_media(root, {'.png'})}, {'photo.png'})

    def test_corrupt_project_documents_do_not_hide_ordinary_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'broken.adv').write_text('{')
            (root / 'photo.png').touch()
            self.assertEqual({p.name for p in discover_project_media(root, {'.png'})}, {'photo.png'})

    def test_map_exclusion_handles_unicode_without_renaming_real_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'K\u00f6ln'
            (root / 'trip.adv').write_text(json.dumps({'track_map_base': name}))
            (root / (name + '-summary.json')).write_text(json.dumps({'source_gpx': 'track.gpx', 'tracks': []}))
            image = root / (unicodedata.normalize('NFD', name) + '.png')
            image.write_bytes(b'map')
            self.assertEqual(discover_project_media(root, {'.png'}), {})
            self.assertEqual(image.read_bytes(), b'map')

    def test_adventure_name_alone_does_not_exclude_a_real_photograph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Trip.adv').write_text(json.dumps({'project_name': 'Trip', 'track_map_base': 'Trip'}))
            (root / 'Trip.png').write_bytes(b'photo')
            self.assertEqual({p.name for p in discover_project_media(root, {'.png'})}, {'Trip.png'})

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
