"""Tests for atomic Adventure and standalone settings storage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adventure_parameters import EDITOR_PARAMETER_KEYS, default_parameters
from json_storage import atomic_write_json, load_parameter_subset, parameter_subset_payload


class JsonStorageTests(unittest.TestCase):
    def test_atomic_write_replaces_complete_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adventure.adv"
            atomic_write_json(path, {"project_name": "First"})
            atomic_write_json(path, {"project_name": "Second", "description": "Complete"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"project_name": "Second", "description": "Complete"},
            )
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_editor_payload_contains_only_editor_settings(self):
        values = default_parameters()
        payload = parameter_subset_payload(values, EDITOR_PARAMETER_KEYS)
        self.assertEqual(set(payload["values"]), set(EDITOR_PARAMETER_KEYS))
        self.assertNotIn("slideshow.transition", payload["values"])

    def test_subset_loader_preserves_valid_values_and_defaults_invalid_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "values": {
                            "gpx.editor_autosave_seconds": 45,
                            "pdf.document_dpi": "invalid",
                        },
                    }
                ),
                encoding="utf-8",
            )
            values, warnings = load_parameter_subset(path, EDITOR_PARAMETER_KEYS)
            self.assertEqual(values["gpx.editor_autosave_seconds"], 45.0)
            self.assertEqual(values["pdf.document_dpi"], default_parameters()["pdf.document_dpi"])
            self.assertTrue(any("Document DPI" in warning for warning in warnings))

    def test_malformed_settings_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            values, warnings = load_parameter_subset(path, EDITOR_PARAMETER_KEYS)
            self.assertEqual(values, default_parameters())
            self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
