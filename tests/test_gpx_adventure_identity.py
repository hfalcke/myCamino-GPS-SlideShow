"""Tests for Adventure-level GPX identity and recovery discovery."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gpx_adventure_identity import (
    build_gpx_identity,
    classify_gpx_identity,
    discover_gpx_recovery_candidates,
    matching_track_map_base,
    patch_gpx_source_references,
)


def write_gpx(path: Path, *, name="Track", second_lon=7.01, elevation=100, timestamp="2026-01-01T10:00:00Z"):
    path.write_text(
        f"""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1" creator="test">
  <trk><name>{name}</name><trkseg>
    <trkpt lat="50.0" lon="7.0"><ele>90</ele><time>2026-01-01T09:00:00Z</time></trkpt>
    <trkpt lat="50.01" lon="{second_lon}"><ele>{elevation}</ele><time>{timestamp}</time></trkpt>
  </trkseg></trk>
</gpx>\n""",
        encoding="utf-8",
    )


class GpxAdventureIdentityTests(unittest.TestCase):
    def test_identity_classifies_exact_equivalent_data_and_geometry(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / "original.gpx"
            equivalent = directory / "equivalent.gpx"
            data_change = directory / "data.gpx"
            geometry_change = directory / "geometry.gpx"
            write_gpx(original)
            write_gpx(equivalent)
            # Keep valid XML while changing only formatting.
            equivalent.write_text(original.read_text(encoding="utf-8").replace("<trk>", "\n  <trk>"), encoding="utf-8")
            write_gpx(data_change, elevation=120)
            write_gpx(geometry_change, second_lon=7.02)

            first = build_gpx_identity(original)
            self.assertEqual(classify_gpx_identity(first, build_gpx_identity(original)), "exact")
            self.assertEqual(classify_gpx_identity(first, build_gpx_identity(equivalent)), "equivalent")
            self.assertEqual(classify_gpx_identity(first, build_gpx_identity(data_change)), "data")
            self.assertEqual(classify_gpx_identity(first, build_gpx_identity(geometry_change)), "geometry")
            self.assertEqual(classify_gpx_identity(None, first), "unknown")

    def test_summary_source_ranks_legacy_candidate_and_finds_map_base(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trackimages = directory / "trackimages"
            trackimages.mkdir()
            preferred = directory / "Old Name.gpx"
            other = directory / "Other.gpx"
            write_gpx(preferred)
            write_gpx(other, second_lon=7.02)
            (trackimages / "Old Name-summary.json").write_text(
                json.dumps({"source_gpx": str(preferred), "tracks": []}),
                encoding="utf-8",
            )
            (trackimages / "Old Name.png").write_bytes(b"png")

            candidates = discover_gpx_recovery_candidates(directory, "New Name.gpx")

            self.assertEqual(candidates[0].path, preferred.resolve())
            self.assertTrue(candidates[0].summary_source_match)
            self.assertEqual(matching_track_map_base(directory, candidates[0]), "Old Name")

    def test_stored_sha_ranks_exact_candidate_above_filename_similarity(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected_name = directory / "Expected Name.gpx"
            exact = directory / "Renamed Original.gpx"
            write_gpx(expected_name, second_lon=7.02)
            write_gpx(exact)
            stored = build_gpx_identity(exact)

            candidates = discover_gpx_recovery_candidates(
                directory,
                "Expected Name.gpx",
                stored_identity=stored,
            )

            self.assertEqual(candidates[0].path, exact.resolve())
            self.assertGreaterEqual(candidates[0].score, 1000)

    def test_source_reference_patch_changes_json_but_not_images(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trackimages = directory / "trackimages"
            trackimages.mkdir()
            metadata = trackimages / "Trip-summary.json"
            metadata.write_text(
                json.dumps({"source_gpx": "/old/Old.gpx", "nested": {"source_gpx": "Old.gpx"}}),
                encoding="utf-8",
            )
            image = trackimages / "Trip.png"
            image.write_bytes(b"unchanged")
            replacement = directory / "Old.gpx"

            changed = patch_gpx_source_references(directory, "Old.gpx", replacement)

            self.assertEqual(changed, 1)
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_gpx"], str(replacement.resolve(strict=False)))
            self.assertEqual(payload["nested"]["source_gpx"], str(replacement.resolve(strict=False)))
            self.assertEqual(image.read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
