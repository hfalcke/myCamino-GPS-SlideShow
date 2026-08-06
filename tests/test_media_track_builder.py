from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import media_track_builder as builder


def media(name, latitude, longitude, hour, place=None):
    return builder.MediaTrackPoint(
        Path(name),
        latitude,
        longitude,
        datetime(2026, 7, 1, hour, tzinfo=UTC) if hour is not None else None,
        place,
    )


class MediaTrackBuilderTests(unittest.TestCase):
    def test_spacing_preserves_stage_endpoints(self):
        points = [
            media("a.jpg", 50.0, 7.0, 9),
            media("b.jpg", 50.0, 7.00001, 10),
            media("c.jpg", 50.0, 7.00002, 11),
        ]
        reduced, merged = builder.reduce_media_points(points, 10.0)
        self.assertEqual(
            [point.media_path.name for point in reduced],
            ["a.jpg", "c.jpg"],
        )
        self.assertEqual(merged, 1)

    def test_control_file_groups_selected_media_by_stage(self):
        points = [
            media("a.jpg", 50.0, 7.0, 9, "Start"),
            media("b.jpg", 50.1, 7.1, 10, "End"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "tour.lst"
            control.write_text(
                "#Datum: 01.07.2026\n"
                "#Map: stage-one.png\n"
                "a.jpg | 09:00 | 50, 7 | Start\n"
                "#Map: stage-two.png\n"
                "b.jpg | 10:00 | 50.1, 7.1 | End\n",
                encoding="utf-8",
            )
            with patch.object(
                builder,
                "load_media_track_points",
                return_value=(points, 0, 2, []),
            ):
                result = builder.build_media_tracks(
                    [Path("a.jpg"), Path("b.jpg")],
                    control_path=control,
                )
        self.assertEqual(len(result.tracks), 2)
        self.assertEqual(
            [track.findtext(builder.qname("name")) for track in result.tracks],
            ["Start", "End"],
        )
        self.assertTrue(
            all(
                next(
                    element
                    for element in track.iter()
                    if element.tag == builder.mycamino_name("trackOrigin")
                ).attrib["kind"]
                == "media-derived"
                for track in result.tracks
            )
        )

    def test_fallback_groups_media_by_local_date(self):
        points = [
            media("a.jpg", 50.0, 7.0, 9),
            builder.MediaTrackPoint(
                Path("b.jpg"),
                50.1,
                7.1,
                datetime(2026, 7, 2, 10, tzinfo=UTC),
            ),
        ]
        with patch.object(
            builder,
            "load_media_track_points",
            return_value=(points, 0, 2, []),
        ):
            result = builder.build_media_tracks([Path("a.jpg"), Path("b.jpg")])
        self.assertEqual(len(result.tracks), 2)
