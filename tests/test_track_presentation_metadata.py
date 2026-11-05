"""Presentation refresh repairs route styles without regenerating backgrounds."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gpx_tracks_table import refresh_track_presentation_metadata
from map_overlay import MAP_CONTENT_VERSION, overlay_track_presentation
from plot_metadata_utils import read_plot_metadata
from slideshow_startup import StartupMetadataReader


class TrackPresentationMetadataTests(unittest.TestCase):
    def test_stale_arrays_are_repaired_even_when_scalar_fields_already_match(self):
        for count, hidden in ((1, False), (3, False), (2, True)):
            for compact in (False, True):
                with self.subTest(count=count, hidden=hidden, compact=compact), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    images = [root / 'stage.png', root / 'stage-timelapse.png']
                    payload = dict(background_only=True, map_content_version=MAP_CONTENT_VERSION,
                        track_geometry_fingerprint='geometry', track_type='pilgrimage', hidden=hidden,
                        overlay_track_types=['unassigned'] * count, overlay_hidden_tracks=[not hidden] * count,
                        overlay_geometry={'segments': [[[50, 7], [51, 8]]] * count},
                        track_points=[[50, 7], [51, 8]], timed_track_points=[{'private_field': 'keep'}],
                        unrelated={'keep': [1, 2, 3]})
                    for image in images:
                        image.write_bytes(b'unchanged PNG')
                        image.with_suffix('.json').write_text(json.dumps(payload))
                    fingerprints = {image: (image.read_bytes(), image.stat().st_mtime_ns) for image in images}
                    context = dict(output_dir=root, tracks=[dict(track_type='pilgrimage', hidden=hidden,
                        segment_count=count, track_geometry_fingerprint='geometry',
                        track_plot_image_filename=images[0].name, track_plot_time_lapse_image_filename=images[1].name)])
                    original = copy.deepcopy(context)
                    reader = StartupMetadataReader(read_plot_metadata) if compact else None
                    with patch('gpx_tracks_table.render_track_plot', side_effect=AssertionError('must not render')):
                        refresh_track_presentation_metadata(context, read_metadata=reader)
                    expected = dict(payload, overlay_track_types=['pilgrimage'] * count,
                                    overlay_hidden_tracks=[hidden] * count)
                    for image in images:
                        self.assertEqual(read_plot_metadata(image.with_suffix('.json')), expected)
                        self.assertEqual((image.read_bytes(), image.stat().st_mtime_ns), fingerprints[image])
                    self.assertEqual(context, original)
                    with patch('json_storage.atomic_write_json') as write:
                        refresh_track_presentation_metadata(context, read_metadata=reader)
                    write.assert_not_called()

    def test_type_and_visibility_changes_update_every_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'stage.json'
            path.write_text(json.dumps(dict(background_only=True, map_content_version=MAP_CONTENT_VERSION,
                track_geometry_fingerprint='geometry', track_type='pilgrimage', hidden=False,
                overlay_track_types=['pilgrimage'] * 3, overlay_hidden_tracks=[False] * 3)))
            context = dict(output_dir=root, tracks=[dict(track_type='bike', hidden=True, segments=[[], [], []],
                track_geometry_fingerprint='geometry', track_plot_image_filename='stage.png')])
            refresh_track_presentation_metadata(context)
            updated = read_plot_metadata(path)
            self.assertEqual(updated['track_type'], 'bike')
            self.assertTrue(updated['hidden'])
            self.assertEqual(updated['overlay_track_types'], ['bike'] * 3)
            self.assertEqual(updated['overlay_hidden_tracks'], [True] * 3)

    def test_geometry_mismatch_and_legacy_format_are_not_made_current(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'stage.json'
            for fingerprint, modern in (('wrong', True), ('geometry', False)):
                path.write_text(json.dumps(dict(background_only=modern, map_content_version=MAP_CONTENT_VERSION,
                    track_geometry_fingerprint=fingerprint, track_type='unassigned', overlay_track_types=['unassigned'])))
                original = path.read_bytes()
                refresh_track_presentation_metadata(dict(output_dir=root, tracks=[dict(track_type='pilgrimage',
                    hidden=False, segment_count=1, track_geometry_fingerprint='geometry',
                    track_plot_image_filename='stage.png')]))
                self.assertEqual(path.read_bytes(), original)

    def test_stage_renderer_prefers_scalar_type_and_visibility_over_stale_arrays(self):
        metadata = dict(track_type='pilgrimage', hidden=False,
                        overlay_track_types=['unassigned', 'bike'], overlay_hidden_tracks=[True, True])
        original = copy.deepcopy(metadata)
        for index in (0, 1):
            self.assertEqual(overlay_track_presentation(metadata, index, 'gpx_track'), ('pilgrimage', False))
        self.assertEqual(metadata, original)

    def test_overview_renderer_keeps_individual_segment_types_and_visibility(self):
        metadata = dict(track_type='pilgrimage', hidden=False,
                        overlay_track_types=['unassigned', 'bike'], overlay_hidden_tracks=[False, True])
        self.assertEqual(overlay_track_presentation(metadata, 0, 'overview'), ('unassigned', False))
        self.assertEqual(overlay_track_presentation(metadata, 1, 'overview'), ('bike', True))


if __name__ == '__main__':
    unittest.main()
