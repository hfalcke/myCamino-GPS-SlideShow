from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from GetGeoLocations import PhotoRecord
from media_metadata_service import media_paths_from_control_file, prepare_media_records
from plot_metadata_utils import media_sidecar_path, write_photo_metadata


class MediaMetadataServiceTests(unittest.TestCase):
    def test_control_file_returns_only_enabled_existing_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one.jpg"
            second = root / "two.mov"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            control = root / "tour.lst"
            control.write_text(
                "one.jpg | 10:00\n# two.mov | 10:05\n#MUSIC: #ON\nmissing.jpg\n",
                encoding="utf-8",
            )
            self.assertEqual(
                media_paths_from_control_file(control, {".jpg", ".mov"}),
                [first.resolve()],
            )

    def test_current_sidecar_is_reused_without_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "photo.jpg"
            media.write_bytes(b"photo")
            stat = media.stat()
            write_photo_metadata(
                {
                    "source_filename": media.name,
                    "datetime_iso": "2026-07-01T10:00:00+00:00",
                    "latitude": 50.0,
                    "longitude": 7.0,
                    "source_file_signature": {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    },
                },
                media_sidecar_path(media),
            )
            with patch("GetGeoLocations.prefetch_media_metadata") as prefetch:
                result = prepare_media_records([media])
            prefetch.assert_not_called()
            self.assertEqual(result[0].action, "reused")
            self.assertEqual(result[0].record.latitude, 50.0)

    def test_missing_sidecar_uses_canonical_batched_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "photo.jpg"
            media.write_bytes(b"photo")
            record = PhotoRecord(
                source_filename=media.name,
                display_filename=media_sidecar_path(media).name,
                photo_path=media,
                json_path=media_sidecar_path(media),
                photo_datetime=datetime(2026, 7, 1, 10, tzinfo=UTC),
                latitude=50.0,
                longitude=7.0,
                place=None,
                place_details=None,
                source="embedded",
                geocode_requested=False,
                place_updated=False,
            )
            with (
                patch("GetGeoLocations.prefetch_media_metadata", return_value={media.resolve(): object()}) as prefetch,
                patch("GetGeoLocations.build_record_from_photo", return_value=record) as build,
                patch("GetGeoLocations.write_record_json") as write,
            ):
                result = prepare_media_records([media])
            prefetch.assert_called_once()
            self.assertFalse(build.call_args.kwargs["getclearnames"])
            write.assert_called_once()
            self.assertEqual(result[0].action, "extracted")

    def test_track_inference_is_applied_only_when_summary_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "photo.jpg"
            media.write_bytes(b"photo")
            stat = media.stat()
            write_photo_metadata(
                {
                    "source_filename": media.name,
                    "datetime_iso": "2026-07-01T10:00:00+00:00",
                    "latitude": None,
                    "longitude": None,
                    "source_file_signature": {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    },
                },
                media_sidecar_path(media),
            )
            summary = root / "tour-summary.json"
            summary.write_text("{}", encoding="utf-8")
            resolver = Mock()
            resolver.apply.return_value = False
            with (
                patch("GetGeoLocations.load_tracks_summary", return_value=object()),
                patch("GetGeoLocations.LazyTrackGpsResolver", return_value=resolver),
            ):
                prepare_media_records([media], tracks_summary_path=summary)
            resolver.apply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
