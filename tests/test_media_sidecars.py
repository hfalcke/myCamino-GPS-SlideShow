"""Regression coverage for extension-aware media sidecars."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import GetGeoLocations as geo
from plot_metadata_utils import (
    build_photo_metadata_payload,
    legacy_media_sidecar_path,
    media_sidecar_matches_media,
    media_sidecar_path,
)


class MediaSidecarTests(unittest.TestCase):
    def _record_for(self, path: Path) -> geo.PhotoRecord:
        return geo.PhotoRecord(
            source_filename=path.name,
            display_filename=path.name,
            photo_path=path,
            json_path=media_sidecar_path(path),
            photo_datetime=datetime(2022, 7, 7, 14, 48, 36),
            latitude=44.6826,
            longitude=3.1268,
            place=None,
            place_details=None,
            source="photo",
            geocode_requested=False,
            place_updated=False,
        )

    def test_extension_aware_paths_are_distinct(self):
        jpeg = Path("IMG_4104.jpeg")
        movie = Path("IMG_4104.mov")
        self.assertEqual(media_sidecar_path(jpeg).name, "IMG_4104.jpeg.json")
        self.assertEqual(media_sidecar_path(movie).name, "IMG_4104.mov.json")
        self.assertNotEqual(media_sidecar_path(jpeg), media_sidecar_path(movie))
        self.assertEqual(legacy_media_sidecar_path(jpeg), legacy_media_sidecar_path(movie))

    def test_migration_moves_owned_legacy_sidecar_and_regenerates_movie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            jpeg = project / "IMG_4104.jpeg"
            movie = project / "IMG_4104.mov"
            jpeg.touch()
            movie.touch()
            legacy = legacy_media_sidecar_path(jpeg)
            payload = build_photo_metadata_payload(
                jpeg.name,
                jpeg,
                datetime(2020, 5, 23, 15, 23, 10),
                49.755847,
                6.644433,
                "Trier",
                {"locality": "Trier", "name": "Hauptmarkt"},
            )
            legacy.write_text(json.dumps(payload), encoding="utf-8")

            with patch.object(geo, "build_record_from_photo", side_effect=lambda path, *_args: self._record_for(path)):
                report = geo.migrate_media_sidecars(project)

            jpeg_sidecar = media_sidecar_path(jpeg)
            movie_sidecar = media_sidecar_path(movie)
            self.assertTrue(jpeg_sidecar.exists())
            self.assertTrue(movie_sidecar.exists())
            self.assertFalse(legacy.exists())
            self.assertEqual(len(report.migrated), 1)
            self.assertEqual(len(report.regenerated), 1)
            jpeg_payload = json.loads(jpeg_sidecar.read_text(encoding="utf-8"))
            movie_payload = json.loads(movie_sidecar.read_text(encoding="utf-8"))
            self.assertTrue(media_sidecar_matches_media(jpeg_payload, jpeg))
            self.assertTrue(media_sidecar_matches_media(movie_payload, movie))
            self.assertEqual(jpeg_payload["place"], "Trier")
            self.assertEqual(movie_payload["source_filename"], movie.name)

    def test_mismatched_sidecar_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            media = project / "IMG_4104.mov"
            media.touch()
            wrong_payload = build_photo_metadata_payload(
                "IMG_4104.jpeg",
                project / "IMG_4104.jpeg",
                datetime(2020, 5, 23, 15, 23, 10),
                49.755847,
                6.644433,
                "Trier",
            )
            sidecar = media_sidecar_path(media)
            sidecar.write_text(json.dumps(wrong_payload), encoding="utf-8")
            self.assertFalse(media_sidecar_matches_media(wrong_payload, media))
            self.assertIsNone(geo.load_record_from_json(sidecar, media))


if __name__ == "__main__":
    unittest.main()
