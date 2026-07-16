"""Tests for authoritative media timestamp selection."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import GetGeoLocations as geo


class MediaDatetimePriorityTests(unittest.TestCase):
    def setUp(self):
        self.media = Path("photo.jpeg")

    def test_embedded_timestamp_keys_follow_authoritative_order(self):
        values = {
            "DateTimeOriginal": "2024:01:01 01:00:00",
            "CreationDate": "2024:02:02 02:00:00",
            "MediaCreateDate": "2024:03:03 03:00:00",
            "CreateDate": "2024:04:04 04:00:00",
        }
        expected = (
            ("DateTimeOriginal", datetime(2024, 1, 1, 1, 0)),
            ("CreationDate", datetime(2024, 2, 2, 2, 0)),
            ("MediaCreateDate", datetime(2024, 3, 3, 3, 0)),
            ("CreateDate", datetime(2024, 4, 4, 4, 0)),
        )
        remaining = dict(values)
        for key, expected_datetime in expected:
            with self.subTest(key=key), patch.object(geo, "read_exiftool_json", return_value=dict(remaining)):
                self.assertEqual(geo.read_exiftool_datetime(self.media), expected_datetime.replace(tzinfo=geo.LOCAL_TIMEZONE))
            remaining.pop(key)

    def test_exif_timestamp_wins_before_spotlight(self):
        embedded = datetime(2024, 5, 1, 12, 0, tzinfo=geo.LOCAL_TIMEZONE)
        with (
            patch.object(geo, "read_exiftool_datetime", return_value=embedded),
            patch.object(geo, "read_mdls_raw", side_effect=AssertionError("Spotlight must not be consulted")),
        ):
            self.assertEqual(geo.get_photo_datetime(self.media), embedded)

    def test_gps_datetime_follows_creation_fields_and_precedes_spotlight(self):
        gps_utc = datetime(2024, 5, 1, 10, 15, 30, tzinfo=timezone.utc)
        expected = gps_utc.astimezone(geo.LOCAL_TIMEZONE)
        metadata = {
            "GPSDateTime": "2024:05:01 10:15:30Z",
            "GPSLatitude": 50.0,
            "GPSLongitude": 8.0,
        }
        with (
            patch.object(geo, "read_exiftool_json", return_value=metadata),
            patch.object(geo, "read_mdls_raw", side_effect=AssertionError("Spotlight must not be consulted")),
        ):
            self.assertEqual(geo.get_photo_datetime(self.media), expected)

        metadata["CreationDate"] = "2024:05:02 14:00:00"
        with patch.object(geo, "read_exiftool_json", return_value=metadata):
            self.assertEqual(
                geo.read_exiftool_datetime(self.media),
                datetime(2024, 5, 2, 14, 0, tzinfo=geo.LOCAL_TIMEZONE),
            )

        metadata.pop("CreationDate")
        metadata["MediaCreateDate"] = "2026:07:12 15:20:37"
        with patch.object(geo, "read_exiftool_json", return_value=metadata):
            self.assertEqual(geo.read_exiftool_datetime(self.media), expected)

    def test_separate_gps_date_and_time_are_combined_as_utc(self):
        metadata = {
            "GPSDateStamp": "2024:05:01",
            "GPSTimeStamp": "10:15:30.5",
        }
        expected = datetime(2024, 5, 1, 10, 15, 30, 500000, tzinfo=timezone.utc).astimezone(
            geo.LOCAL_TIMEZONE
        )
        with patch.object(geo, "read_exiftool_json", return_value=metadata):
            self.assertEqual(geo.read_exiftool_datetime(self.media), expected)

    def test_debug_reports_gps_timestamp_source(self):
        metadata = {"GPSDateStamp": "2024:05:01", "GPSTimeStamp": "10:15:30"}
        with (
            patch.object(geo, "is_exiftool_available", return_value=True),
            patch.object(geo, "read_exiftool_json", return_value=metadata),
        ):
            selected, debug_info = geo.read_exiftool_datetime_with_debug(self.media)
        self.assertIsNotNone(selected)
        self.assertEqual(debug_info["selected_source"], "GPSDateStamp+GPSTimeStamp")

    def test_spotlight_content_then_filesystem_creation_fallback(self):
        content = "2024-05-01 12:00:00 +0200"
        filesystem = "2024-05-02 13:00:00 +0200"
        with (
            patch.object(geo, "read_exiftool_datetime", return_value=None),
            patch.object(
                geo,
                "read_mdls_raw",
                side_effect=lambda _path, key: {
                    "kMDItemContentCreationDate": content,
                    "kMDItemFSCreationDate": filesystem,
                }[key],
            ),
        ):
            selected = geo.get_photo_datetime(self.media)
        self.assertEqual(selected, geo.parse_mdls_datetime(content))

        with (
            patch.object(geo, "read_exiftool_datetime", return_value=None),
            patch.object(
                geo,
                "read_mdls_raw",
                side_effect=lambda _path, key: filesystem if key == "kMDItemFSCreationDate" else None,
            ),
        ):
            selected = geo.get_photo_datetime(self.media)
        self.assertEqual(selected, geo.parse_mdls_datetime(filesystem))

    def test_filesystem_birthtime_then_modification_time_are_final_fallbacks(self):
        birthtime = 1_700_000_000.0
        modification_time = 1_600_000_000.0
        with (
            patch.object(geo, "read_exiftool_datetime", return_value=None),
            patch.object(geo, "read_mdls_raw", return_value=None),
            patch.object(geo, "is_exiftool_available", return_value=True),
            patch.object(Path, "stat", return_value=SimpleNamespace(
                st_birthtime=birthtime,
                st_mtime=modification_time,
            )),
        ):
            selected = geo.get_photo_datetime(self.media)
        self.assertEqual(selected.timestamp(), birthtime)

        with (
            patch.object(geo, "read_exiftool_datetime", return_value=None),
            patch.object(geo, "read_mdls_raw", return_value=None),
            patch.object(geo, "is_exiftool_available", return_value=True),
            patch.object(Path, "stat", return_value=SimpleNamespace(st_mtime=modification_time)),
        ):
            selected = geo.get_photo_datetime(self.media)
        self.assertEqual(selected.timestamp(), modification_time)

    def test_debug_mode_uses_same_exif_first_priority_and_reports_source(self):
        embedded = datetime(2024, 5, 1, 12, 0, tzinfo=geo.LOCAL_TIMEZONE)
        exif_debug = {"selected_source": "DateTimeOriginal", "candidates": []}
        with (
            patch.object(geo, "read_exiftool_datetime_with_debug", return_value=(embedded, exif_debug)),
            patch.object(geo, "read_mdls_raw", side_effect=AssertionError("Spotlight must not be consulted")),
        ):
            selected, debug_info = geo.get_photo_datetime_with_debug(self.media)
        self.assertEqual(selected, embedded)
        self.assertEqual(debug_info["selected_source"], "exiftool:DateTimeOriginal")

    def test_gps_source_does_not_change_timestamp_selection(self):
        selected = datetime(2024, 5, 1, 12, 0, tzinfo=geo.LOCAL_TIMEZONE)
        for mdls_gps, exif_gps in (((50.0, 8.0), (None, None)), ((None, None), (50.0, 8.0))):
            with (
                self.subTest(mdls_gps=mdls_gps),
                patch.object(geo, "read_mdls_gps_pair", return_value=mdls_gps),
                patch.object(geo, "read_exiftool_gps_pair", return_value=exif_gps),
                patch.object(
                    geo,
                    "get_photo_datetime_with_debug",
                    return_value=(selected, {"selected_source": "DateTimeOriginal"}),
                ) as timestamp_reader,
            ):
                record = geo.build_record_from_photo(self.media, False, {}, [], 0.0, False)
            self.assertEqual(record.photo_datetime, selected)
            timestamp_reader.assert_called_once_with(self.media)


if __name__ == "__main__":
    unittest.main()
