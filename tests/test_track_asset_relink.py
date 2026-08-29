import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gpx_processing import geometry_fingerprint_from_segments
from track_asset_relink import (
    reconcile_legacy_track_sidecars,
    relink_numbered_track_assets,
)


class TrackAssetRelinkTests(unittest.TestCase):
    def _write_asset(self, root, stem, fingerprint, layout, content):
        image = root / f"{stem}.png"
        metadata = root / f"{stem}.json"
        image.write_bytes(content)
        metadata.write_text(
            json.dumps(
                {
                    "track_fingerprint": fingerprint,
                    "track_number": int(stem[:4]),
                    "track_name": "Stage",
                    "map_layout": layout,
                    "output_image": str(image),
                    "output_metadata": str(metadata),
                }
            ),
            encoding="utf-8",
        )

    def test_relinks_number_swap_and_updates_control_and_overview(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            output = project / "trackimages"
            output.mkdir()
            self._write_asset(output, "0001_Stage_trip", "A", "standard", b"A-standard")
            self._write_asset(
                output,
                "0001_Stage_trip-timelapse",
                "A",
                "time-lapse",
                b"A-timelapse",
            )
            self._write_asset(output, "0002_Stage_trip", "B", "standard", b"B-standard")
            self._write_asset(
                output,
                "0002_Stage_trip-timelapse",
                "B",
                "time-lapse",
                b"B-timelapse",
            )
            overview = output / "trip.json"
            overview.write_text(
                json.dumps(
                    {
                        "source_track_fingerprints": ["A", "B"],
                        "tracks": [
                            {"track_fingerprint": "A", "track_number": 1},
                            {"track_fingerprint": "B", "track_number": 2},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            control = project / "trip-sorted.lst"
            control.write_text(
                "#Map: 0001_Stage_trip.png\n#Map: 0002_Stage_trip.png\n",
                encoding="utf-8",
            )
            tracks = [
                {
                    "table_number": 1,
                    "name": "Stage",
                    "track_fingerprint": "B",
                    "track_plot_image_filename": "0001_Stage_trip.png",
                    "track_plot_time_lapse_image_filename": "0001_Stage_trip-timelapse.png",
                },
                {
                    "table_number": 2,
                    "name": "Stage",
                    "track_fingerprint": "A",
                    "track_plot_image_filename": "0002_Stage_trip.png",
                    "track_plot_time_lapse_image_filename": "0002_Stage_trip-timelapse.png",
                },
            ]
            context = {
                "output_dir": str(output),
                "overview_metadata_path": str(overview),
                "tracks": tracks,
            }

            report = relink_numbered_track_assets(context, project_dir=project)

            self.assertEqual(len(report.relinked), 4)
            self.assertEqual((output / "0001_Stage_trip.png").read_bytes(), b"B-standard")
            self.assertEqual((output / "0002_Stage_trip.png").read_bytes(), b"A-standard")
            first_metadata = json.loads(
                (output / "0001_Stage_trip.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_metadata["track_fingerprint"], "B")
            self.assertEqual(first_metadata["track_number"], 1)
            self.assertEqual(
                control.read_text(encoding="utf-8"),
                "#Map: 0002_Stage_trip.png\n#Map: 0001_Stage_trip.png\n",
            )
            overview_payload = json.loads(overview.read_text(encoding="utf-8"))
            self.assertEqual(overview_payload["source_track_fingerprints"], ["B", "A"])
            self.assertEqual(
                [item["track_number"] for item in overview_payload["tracks"]],
                [1, 2],
            )
            self.assertTrue(list((project / ".mycamino-control-backups").rglob("*.lst")))

    def test_unrelated_destination_collision_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            self._write_asset(output, "0009_Stage_trip", "A", "standard", b"source")
            self._write_asset(output, "0001_Stage_trip", "OTHER", "standard", b"other")
            context = {
                "output_dir": str(output),
                "tracks": [
                    {
                        "table_number": 1,
                        "name": "Stage",
                        "track_fingerprint": "A",
                        "track_plot_image_filename": "0001_Stage_trip.png",
                        "track_plot_time_lapse_image_filename": "",
                    }
                ],
            }

            report = relink_numbered_track_assets(context)

            self.assertTrue(report.skipped)
            self.assertEqual((output / "0001_Stage_trip.png").read_bytes(), b"other")

    def test_reconciles_legacy_geometry_and_provider_alias_without_rendering(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            output = project / "trackimages"
            output.mkdir()
            gpx = project / "copied.gpx"
            gpx.write_text("<gpx/>", encoding="utf-8")
            image = output / "0001_Old_trip.png"
            image.write_bytes(b"unchanged-map")
            segments = [[
                {"lat": 50.0, "lon": 7.0},
                {"lat": 50.1, "lon": 7.1},
            ]]
            metadata = image.with_suffix(".json")
            metadata.write_text(
                json.dumps(
                    {
                        "track_fingerprint": "legacy-semantic-hash",
                        "track_number": 1,
                        "track_name": "Old",
                        "processed_track_segments": segments,
                        "map_layout": "standard",
                        "source_gpx": "/old/project/trip.gpx",
                        "adventure_render_parameters": {
                            "maps.provider": "osm",
                            "trackmaps.rendered_layout": "standard",
                        },
                    }
                ),
                encoding="utf-8",
            )
            geometry = geometry_fingerprint_from_segments(segments)
            context = {
                "output_dir": str(output),
                "output_base": "trip",
                "args": SimpleNamespace(gpx_file=str(gpx)),
                "tracks": [
                    {
                        "table_number": 1,
                        "name": "Current",
                        "track_fingerprint_version": 2,
                        "track_geometry_fingerprint": geometry,
                        "track_data_fingerprint": "current-data",
                    }
                ],
            }

            report = reconcile_legacy_track_sidecars(
                context,
                render_parameters_by_layout={
                    "standard": {
                        "maps.output_provider": "osm",
                        "trackmaps.rendered_layout": "standard",
                    }
                },
            )

            self.assertEqual(report.repaired, [metadata.name])
            self.assertEqual(image.read_bytes(), b"unchanged-map")
            repaired = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(repaired["track_geometry_fingerprint"], geometry)
            self.assertTrue(repaired["track_data_fingerprint"])
            self.assertNotEqual(
                repaired["track_data_fingerprint"], "legacy-semantic-hash"
            )
            self.assertEqual(repaired["source_gpx"], str(gpx.resolve()))
            self.assertEqual(
                repaired["adventure_render_parameters"]["maps.output_provider"],
                "osm",
            )
            self.assertNotIn(
                "maps.provider", repaired["adventure_render_parameters"]
            )

    def test_reconciliation_rejects_ambiguous_duplicate_geometry(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            gpx = output / "trip.gpx"
            gpx.write_text("<gpx/>", encoding="utf-8")
            image = output / "0001_Stage_trip.png"
            image.write_bytes(b"map")
            segments = [[{"lat": 50.0, "lon": 7.0}]]
            metadata = image.with_suffix(".json")
            metadata.write_text(
                json.dumps({"processed_track_segments": segments}),
                encoding="utf-8",
            )
            geometry = geometry_fingerprint_from_segments(segments)
            context = {
                "output_dir": str(output),
                "output_base": "trip",
                "args": SimpleNamespace(gpx_file=str(gpx)),
                "tracks": [
                    {"table_number": number, "track_geometry_fingerprint": geometry}
                    for number in (1, 2)
                ],
            }
            report = reconcile_legacy_track_sidecars(context)
            self.assertEqual(report.ambiguous, [metadata.name])
            self.assertNotIn(
                "track_geometry_fingerprint",
                json.loads(metadata.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
