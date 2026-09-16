import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from GetGeoLocations import DeferredPlaceNameSession

from media_enrichment_queue import (
    AdaptiveGeocodePacing,
    SlideshowAwareEnrichmentQueue,
    control_media_priority,
)


class MediaEnrichmentQueueTests(unittest.TestCase):
    def test_active_and_following_media_are_prioritized(self):
        paths = [Path(f"/tmp/{name}.jpg").resolve() for name in ("a", "b", "c", "d")]
        queue = SlideshowAwareEnrichmentQueue()
        queue.add(paths)
        queue.set_playback_position(paths, paths[2])

        self.assertEqual(queue.ordered(), [paths[2], paths[3], paths[0], paths[1]])

    def test_stage_row_starts_with_following_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = root / "show.lst"
            control.write_text(
                "first.jpg\n#MAP: stage.png\n# hidden.jpg\nsecond.mov\nthird.jpg\n",
                encoding="utf-8",
            )

            order, active = control_media_priority(
                control, {".jpg", ".mov"}, active_row=1
            )

            self.assertIsNone(active)
            self.assertEqual(
                order,
                [root / "second.mov", root / "third.jpg", root / "first.jpg"],
            )

    def test_ready_ahead_counts_only_consecutive_media(self):
        paths = [Path(f"/tmp/{index}.jpg").resolve() for index in range(12)]
        queue = SlideshowAwareEnrichmentQueue()
        queue.set_playback_position(paths, paths[0])

        ready = queue.ready_ahead(lambda path: path in set(paths[1:7]))

        self.assertEqual(ready, 6)

    def test_catch_up_caps_default_delay_at_two_seconds(self):
        pacing = AdaptiveGeocodePacing(1.0, 5.0)

        self.assertEqual(
            pacing.range_for(playback_active=True, ready_ahead=3),
            (1.0, 2.0),
        )
        self.assertEqual(
            pacing.range_for(playback_active=True, ready_ahead=10),
            (1.0, 2.5),
        )
        self.assertEqual(
            pacing.range_for(playback_active=True, ready_ahead=10),
            (1.0, 3.0),
        )

    def test_configured_minimum_is_never_violated(self):
        pacing = AdaptiveGeocodePacing(3.0, 5.0)

        self.assertEqual(
            pacing.range_for(playback_active=True, ready_ahead=0),
            (3.0, 3.0),
        )

    def test_incremental_place_session_patches_only_place_fields(self):
        media = Path("/tmp/photo.jpg").resolve()
        record = SimpleNamespace(
            photo_path=media,
            place=None,
            place_details=None,
            place_updated=False,
            latitude=50.0,
            longitude=7.0,
            debug_info=None,
        )

        def resolve(value, *_args, **_kwargs):
            value.place = "Bonn"
            value.place_updated = True
            value.debug_info = {"geocode": {"requested": True}}
            return value

        session = DeferredPlaceNameSession([], 150.0)
        with (
            patch("GetGeoLocations.load_record_from_json", return_value=record),
            patch("GetGeoLocations.record_place_matches_gps", return_value=False),
            patch("GetGeoLocations.resolve_place_for_record", side_effect=resolve),
            patch("GetGeoLocations.write_record_place_fields") as write,
        ):
            outcome, requested = session.enrich(media)

        self.assertEqual(outcome, "Bonn")
        self.assertTrue(requested)
        write.assert_called_once_with(record)


if __name__ == "__main__":
    unittest.main()
