# SPDX-License-Identifier: GPL-3.0-or-later

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from demo_resources import (
    create_demo_session,
    demo_prepared_render_parameters,
    install_demo_prepared_assets,
    verify_demo_manifest,
)
from cocoa_guided_demo import (
    DEMO_ADVENTURE_NAME,
    controller_demo_adventure,
    is_guided_demo_recovery,
    saved_demo_adventure,
)
from help_resources import help_document_path
from plot_metadata_utils import media_sidecar_freshness


class DemoResourceTests(unittest.TestCase):
    def test_loaded_adventure_requires_explicit_demo_marker_for_continuation(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "Demo.adv"
            path.write_text("{}", encoding="utf-8")
            marked = SimpleNamespace(
                adventure_file=path,
                adventure_payload={"guided_demo": True},
            )
            ordinary = SimpleNamespace(
                adventure_file=path,
                adventure_payload={"guided_demo": False},
            )
            self.assertEqual(controller_demo_adventure(marked), path)
            self.assertIsNone(controller_demo_adventure(ordinary))

    def test_demo_recovery_is_not_offered_as_unsaved_adventure(self):
        self.assertTrue(is_guided_demo_recovery({"adventure_name": DEMO_ADVENTURE_NAME}))
        self.assertTrue(
            is_guided_demo_recovery({"source_directory": "/tmp/mycamino-demo-123/Demo Adventure"})
        )
        self.assertFalse(is_guided_demo_recovery({"adventure_name": "My Journey"}))

    def test_saved_demo_adventure_uses_remembered_workspace(self):
        class Defaults:
            def __init__(self, value):
                self.value = value

            def stringForKey_(self, _key):
                return self.value

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            workspace = root / "Demo Adventure"
            inputs = root / "Demo Inputs"
            workspace.mkdir()
            inputs.mkdir()
            (inputs / "manifest.json").write_text("{}", encoding="utf-8")
            older = workspace / "Older.adv"
            newer = workspace / "Newer.adv"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            older.touch()
            newer.touch()
            self.assertIn(
                saved_demo_adventure(Defaults(str(workspace))),
                {older, newer},
            )
            self.assertIsNone(saved_demo_adventure(Defaults(str(workspace / "missing"))))

    def test_bundled_demo_manifest_and_scope(self):
        root = Path(__file__).resolve().parents[1]
        demo = root / "demo" / "St-Jean-Pied-de-Port-to-Roncesvalles"
        self.assertEqual(verify_demo_manifest(demo), [])
        manifest = json.loads((demo / "manifest.json").read_text(encoding="utf-8"))
        photos = [item for item in manifest["files"] if item["path"].startswith("2 - Photographs/")]
        self.assertGreater(len(photos), 0)
        self.assertEqual(
            len(photos),
            len(list((demo / "2 - Photographs").glob("*.jpeg"))),
        )
        self.assertTrue(all(max(item["width"], item["height"]) <= 1920 for item in photos))
        self.assertFalse(any(Path(item["path"]).suffix.casefold() in {".mov", ".mp4"} for item in manifest["files"]))
        prepared = demo / "3 - Prepared Adventure"
        self.assertEqual(len(list((prepared / "metadata").glob("*.jpeg.json"))), len(photos))
        self.assertTrue((prepared / "trackimages" / "St Jean to Roncesvalles Demo-summary.json").is_file())

    def test_prepared_assets_install_into_workspace(self):
        root = Path(__file__).resolve().parents[1]
        demo = root / "demo" / "St-Jean-Pied-de-Port-to-Roncesvalles"
        with tempfile.TemporaryDirectory() as name:
            result = install_demo_prepared_assets(
                demo,
                Path(name),
                "St Jean to Roncesvalles Demo",
            )
            self.assertGreater(result["sidecars"], 0)
            self.assertGreater(result["map_assets"], 0)
            self.assertTrue((Path(name) / "trackimages" / "St Jean to Roncesvalles Demo-summary.json").is_file())
        parameters = demo_prepared_render_parameters(demo)
        self.assertEqual(parameters["trackmaps.image_size"], "1920x1080")
        self.assertIn("maps.output_provider", parameters)

    def test_prepared_assets_are_relinked_to_demo_adventure_name(self):
        root = Path(__file__).resolve().parents[1]
        demo = root / "demo" / "St-Jean-Pied-de-Port-to-Roncesvalles"
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            install_demo_prepared_assets(demo, workspace, "TestDemo")
            maps = workspace / "trackimages"
            summary_path = maps / "TestDemo-summary.json"
            self.assertTrue((maps / "TestDemo.png").is_file())
            self.assertTrue(summary_path.is_file())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["source_gpx"], "TestDemo.gpx")
            self.assertEqual(summary["output_image"], "TestDemo.png")
            track = summary["tracks"][0]
            self.assertIn("TestDemo", track["track_plot_image_filename"])
            self.assertTrue((maps / track["track_plot_image_filename"]).is_file())
            self.assertTrue((maps / track["track_data_sidecar"]).is_file())

    def test_prepared_asset_install_requires_actual_adventure_name(self):
        root = Path(__file__).resolve().parents[1]
        demo = root / "demo" / "St-Jean-Pied-de-Port-to-Roncesvalles"
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                install_demo_prepared_assets(demo, Path(name), "")

    def test_prepared_sidecars_are_current_after_demo_import(self):
        root = Path(__file__).resolve().parents[1]
        demo = root / "demo" / "St-Jean-Pied-de-Port-to-Roncesvalles"
        photo = next((demo / "2 - Photographs").glob("*.jpeg"))
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            imported = workspace / photo.name
            shutil.copy2(photo, imported)
            install_demo_prepared_assets(
                demo,
                workspace,
                "St Jean to Roncesvalles Demo",
            )
            payload = json.loads((workspace / f"{photo.name}.json").read_text(encoding="utf-8"))
            self.assertEqual(media_sidecar_freshness(imported, payload), "current")
            self.assertTrue(payload["place"])
            self.assertEqual(payload["weather"]["provider"], "open-meteo")

    def test_demo_manifest_detects_changed_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "asset.txt").write_text("original", encoding="utf-8")
            digest = hashlib.sha256(b"original").hexdigest()
            (root / "manifest.json").write_text(
                json.dumps({"format_version": 2, "files": [{"path": "asset.txt", "sha256": digest}]}),
                encoding="utf-8",
            )
            self.assertEqual(verify_demo_manifest(root), [])
            (root / "asset.txt").write_text("changed", encoding="utf-8")
            self.assertEqual(verify_demo_manifest(root), ["Checksum mismatch for asset.txt"])

    def test_demo_session_is_a_disposable_copy(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_text("{}", encoding="utf-8")
            session, inputs, workspace = create_demo_session(source, root)
            self.assertEqual(session.parent, root)
            self.assertTrue((inputs / "manifest.json").is_file())
            self.assertTrue(workspace.is_dir())
            self.assertEqual(list(workspace.iterdir()), [])

    def test_quick_start_document_is_available_in_source_tree(self):
        self.assertTrue(help_document_path("MYCAMINO_QUICK_START.md").is_file())

    def test_distribution_includes_demo_and_verifies_it(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "myCamino GPS Track Show.spec").read_text(encoding="utf-8")
        build = (root / "build_dmg.sh").read_text(encoding="utf-8")
        self.assertIn("('demo', 'demo')", spec)
        self.assertIn("scripts/prepare_demo_assets.py --verify", build)
        self.assertIn('$TMP_ROOT/myCamino Demo', build)


if __name__ == "__main__":
    unittest.main()
