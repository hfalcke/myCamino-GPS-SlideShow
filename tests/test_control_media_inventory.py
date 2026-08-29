"""Tests for safe control-file media membership tracking."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import GetGeoLocations as geo
from control_media_inventory import (
    build_control_media_inventory_payload,
    classify_project_media,
    control_media_inventory_path,
    load_control_media_inventory,
    mark_imported_media,
    write_control_media_inventory,
)
from plot_metadata_utils import (
    build_photo_metadata_payload,
    media_file_signature,
    media_sidecar_path,
)


class ControlMediaInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.control = self.project / "Trip-sorted.lst"
        self.control.write_text("#Datum: Montag, 15.07.2024\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def media(self, name, hour=12):
        path = self.project / name
        path.write_bytes(name.encode("utf-8"))
        payload = build_photo_metadata_payload(
            name,
            path,
            datetime(2024, 7, 15, hour, 0).astimezone(),
            50.0,
            8.0,
            "Place",
            source_file_signature=media_file_signature(path),
        )
        media_sidecar_path(path).write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_bootstrap_keeps_absent_media_unclassified(self):
        included = self.media("included.jpeg")
        absent = self.media("absent.jpeg")
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            "included.jpeg | 12:00 | 50.000000, 8.000000 | Place\n",
            encoding="utf-8",
        )
        inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )
        states = {item.media_path.name: item.state for item in memberships}
        self.assertFalse(inventory.bootstrap_complete)
        self.assertEqual(states[included.name], "included")
        self.assertEqual(states[absent.name], "unclassified")

    def test_disabled_control_row_remains_included(self):
        hidden = self.media("hidden.jpeg")
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            "# hidden.jpeg | 12:00 | 50.000000, 8.000000 | Place\n",
            encoding="utf-8",
        )
        _inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )
        self.assertEqual(memberships[0].media_path.name, hidden.name)
        self.assertEqual(memberships[0].state, "included")

    def test_import_is_durable_and_recommended_after_restart(self):
        imported = self.media("imported.jpeg")
        mark_imported_media(self.control, [imported])
        inventory = load_control_media_inventory(self.control)
        membership = classify_project_media(inventory, [imported], [])[0]
        self.assertEqual(membership.state, "new")
        self.assertEqual(membership.reason, "Imported with myCamino")
        self.assertTrue(membership.imported_at)

    def test_applied_inventory_remembers_unchecked_media_as_excluded(self):
        included = self.media("included.jpeg")
        excluded = self.media("excluded.jpeg")
        inventory = load_control_media_inventory(self.control)
        payload = build_control_media_inventory_payload(
            inventory,
            [included, excluded],
            [included.name],
            control_text=self.control.read_text(encoding="utf-8"),
        )
        write_control_media_inventory(payload, control_media_inventory_path(self.control))
        loaded = load_control_media_inventory(self.control)
        states = {
            item.media_path.name: item.state
            for item in classify_project_media(loaded, [included, excluded], [included.name])
        }
        self.assertEqual(states, {"included.jpeg": "included", "excluded.jpeg": "excluded"})

    def test_external_row_deletion_requires_fresh_confirmation(self):
        media = self.media("externally-removed.jpeg")
        original = (
            "#Datum: Montag, 15.07.2024\n"
            "externally-removed.jpeg | 12:00 | 50.000000, 8.000000 | Place\n"
        )
        self.control.write_text(original, encoding="utf-8")
        inventory = load_control_media_inventory(self.control)
        payload = build_control_media_inventory_payload(
            inventory,
            [media],
            [media.name],
            control_text=original,
        )
        write_control_media_inventory(payload, control_media_inventory_path(self.control))
        self.control.write_text("#Datum: Montag, 15.07.2024\n", encoding="utf-8")

        inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )

        self.assertTrue(inventory.bootstrap_complete)
        self.assertEqual(memberships[0].state, "unclassified")
        self.assertIn("external", memberships[0].reason.lower())

    def test_missing_control_media_is_reported_and_retained(self):
        missing_name = "missing-on-disk.jpeg"
        original = (
            "#Datum: Montag, 15.07.2024\n"
            f"{missing_name} | 12:00 | 50.000000, 8.000000 | Place\n"
        )
        self.control.write_text(original, encoding="utf-8")

        plan = geo.analyze_control_file_updates(
            self.project,
            [],
            control_file=self.control,
            tracks_summary_path=None,
            media_only=True,
            media_inventory=load_control_media_inventory(self.control),
        )
        self.assertTrue(any(missing_name in warning for warning in plan.media.warnings))

        geo.commit_control_file_update_plan(plan, update_place_names=False)
        self.assertEqual(self.control.read_text(encoding="utf-8"), original)

    def test_current_unclassified_sidecar_is_analyzed_without_extraction(self):
        media = self.media("candidate.jpeg")
        inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )
        membership_map = {item.media_path.name: item for item in memberships}
        with patch.object(geo, "build_record_from_photo") as extractor:
            plan = geo.analyze_control_file_updates(
                self.project,
                [media],
                control_file=self.control,
                tracks_summary_path=None,
                media_only=True,
                media_memberships=membership_map,
                media_inventory=inventory,
            )
        extractor.assert_not_called()
        item = plan.media.items[0]
        self.assertEqual(item.membership_status, "unclassified")
        self.assertFalse(item.add_to_control)
        self.assertFalse(item.update_metadata)

    def test_apply_without_add_remembers_unclassified_media_as_excluded(self):
        media = self.media("leave-out.jpeg")
        original = self.control.read_text(encoding="utf-8")
        inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )
        plan = geo.analyze_control_file_updates(
            self.project,
            [media],
            control_file=self.control,
            tracks_summary_path=None,
            media_only=True,
            media_memberships={media.name: memberships[0]},
            media_inventory=inventory,
        )

        result = geo.commit_control_file_update_plan(
            plan,
            update_place_names=False,
        )

        self.assertEqual(self.control.read_text(encoding="utf-8"), original)
        stored = load_control_media_inventory(self.control)
        self.assertEqual(stored.entry(media.name)["state"], "excluded")
        self.assertEqual(result.media.inventory_included, 0)
        self.assertEqual(result.media.inventory_excluded, 1)

    def test_unclassified_media_is_recommended_for_empty_matching_stage(self):
        media = self.media("stage-photo.jpeg")
        track_dir = self.project / "trackimages"
        track_dir.mkdir()
        map_name = "0001_Stage.png"
        (track_dir / map_name).write_bytes(b"map")
        summary = track_dir / "Trip-summary.json"
        summary.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "nr": 1,
                            "original_sequence_number": 1,
                            "start_time": "15.07.2024 08:00:00",
                            "end_time": "15.07.2024 18:00:00",
                            "track_plot_image_filename": map_name,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.control.write_text(
            f"#Datum: Montag, 15.07.2024\n#Map: {map_name}\n",
            encoding="utf-8",
        )
        inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )
        with patch.object(geo, "build_record_from_photo") as extractor:
            plan = geo.analyze_control_file_updates(
                self.project,
                [media],
                control_file=self.control,
                tracks_summary_path=summary,
                summary_current=True,
                media_memberships={memberships[0].media_path.name: memberships[0]},
                media_inventory=inventory,
            )
        extractor.assert_not_called()
        item = plan.media.items[0]
        self.assertEqual(item.membership_status, "recommended")
        self.assertTrue(item.add_to_control)

    def test_metadata_repair_does_not_add_excluded_media(self):
        media = self.media("excluded.jpeg")
        inventory = load_control_media_inventory(self.control)
        payload = build_control_media_inventory_payload(
            inventory,
            [media],
            [],
            control_text=self.control.read_text(encoding="utf-8"),
        )
        write_control_media_inventory(payload, control_media_inventory_path(self.control))
        inventory, memberships = geo.discover_control_media_membership(
            self.project, self.control
        )
        membership_map = {item.media_path.name: item for item in memberships}
        replacement = geo.record_from_sidecar_payload(
            json.loads(media_sidecar_path(media).read_text(encoding="utf-8")),
            media_sidecar_path(media),
            media,
        )
        with patch.object(geo, "build_record_from_photo", return_value=replacement):
            plan = geo.analyze_control_file_updates(
                self.project,
                [media],
                control_file=self.control,
                tracks_summary_path=None,
                actions={media.name: "refresh"},
                media_only=True,
                media_memberships=membership_map,
                media_inventory=inventory,
            )
        item = plan.media.items[0]
        self.assertFalse(item.add_to_control)
        self.assertFalse(item.update_metadata)
        item.update_metadata = True
        item.apply_update = True
        geo.commit_control_file_update_plan(plan, update_place_names=False)
        self.assertNotIn(media.name, self.control.read_text(encoding="utf-8"))
        self.assertTrue(media_sidecar_path(media).is_file())


if __name__ == "__main__":
    unittest.main()
