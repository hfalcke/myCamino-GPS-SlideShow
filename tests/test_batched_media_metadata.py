import json
import plistlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import GetGeoLocations as geo


class BatchedMediaMetadataTests(unittest.TestCase):
    def test_exiftool_uses_one_process_per_bounded_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp, f"image-{index}.jpg").resolve() for index in range(7)]
            calls = []

            def run(command, **_kwargs):
                calls.append(command)
                files = [item for item in command if str(item).endswith(".jpg")]
                payload = [{"SourceFile": item, "GPSLatitude": 50.0} for item in files]
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

            with (
                patch.object(geo, "is_exiftool_available", return_value=True),
                patch.object(geo.subprocess, "run", side_effect=run),
            ):
                result = geo.read_exiftool_json_many(paths, batch_size=3)

            self.assertEqual(len(calls), 3)
            self.assertEqual(set(result), set(paths))
            self.assertTrue(all(result[path]["GPSLatitude"] == 50.0 for path in paths))

    def test_prefetch_reads_spotlight_once_and_reuses_exiftool_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp, "photo with spaces.jpg").resolve()
            media.write_bytes(b"media")
            exif_payload = {
                media: {
                    "SourceFile": str(media),
                    "GPSLatitude": 40.0,
                    "GPSLongitude": 7.0,
                    "DateTimeOriginal": "2024:07:15 12:30:00",
                }
            }
            spotlight_payload = {
                "kMDItemLatitude": 50.0,
                "kMDItemLongitude": 8.0,
            }

            with (
                patch.object(geo, "is_exiftool_available", return_value=True),
                patch.object(geo, "read_exiftool_json_many", return_value=exif_payload) as exif_many,
                patch.object(geo, "read_mdls_metadata", return_value=spotlight_payload) as mdls,
            ):
                bundles = geo.prefetch_media_metadata([media])

            with (
                patch.object(geo, "read_mdls_gps_pair", side_effect=AssertionError("legacy mdls path used")),
                patch.object(geo, "read_exiftool_json", side_effect=AssertionError("ExifTool reread")),
            ):
                record = geo.build_record_from_photo(
                    media,
                    False,
                    {},
                    [],
                    0.0,
                    True,
                    metadata_bundle=bundles[media],
                )

            exif_many.assert_called_once()
            mdls.assert_called_once_with(media)
            self.assertEqual((record.latitude, record.longitude), (50.0, 8.0))
            self.assertEqual(record.photo_datetime, datetime(2024, 7, 15, 12, 30, tzinfo=geo.LOCAL_TIMEZONE))
            self.assertEqual(record.datetime_source, "exiftool:DateTimeOriginal")

    def test_spotlight_plist_reader_uses_one_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp, "photo.jpg")
            media.write_bytes(b"media")
            payload = {
                "kMDItemLatitude": 50.0,
                "kMDItemLongitude": 8.0,
                "kMDItemContentCreationDate": datetime(2024, 7, 15, 12, 30),
            }
            completed = type(
                "Result",
                (),
                {"returncode": 0, "stdout": plistlib.dumps(payload)},
            )()
            with patch.object(geo.subprocess, "run", return_value=completed) as run:
                result = geo.read_mdls_metadata(media)

            run.assert_called_once()
            self.assertEqual(result["kMDItemLatitude"], 50.0)


if __name__ == "__main__":
    unittest.main()
