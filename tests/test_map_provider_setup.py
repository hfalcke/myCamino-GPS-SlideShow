from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from map_provider_setup import (
    PROVIDER_DEFINITIONS,
    known_provider_credentials,
    load_map_provider_preference,
    save_map_provider_preference,
    validate_custom_xyz_access,
    validate_custom_xyz_configuration,
    validate_provider_credential,
)


class FakeResponse:
    status = 200
    headers = {"Content-Type": "image/png"}

    def read(self, _size=-1):
        return b"\x89PNG"


class MapProviderSetupTests(unittest.TestCase):
    def test_catalog_contains_guided_and_advanced_choices(self):
        providers = {item.provider_id: item for item in PROVIDER_DEFINITIONS}
        self.assertTrue(providers["geoapify"].recommended)
        self.assertTrue(providers["thunderforest"].requires_key)
        self.assertTrue(providers["stadia"].requires_key)
        self.assertTrue(providers["esri"].settings_only)
        self.assertTrue(providers["custom"].settings_only)
        self.assertFalse(providers["osm"].requires_key)

    def test_machine_preference_never_contains_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.json"
            save_map_provider_preference("geoapify", "personal", path=path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("api_key", text)
            self.assertEqual(
                load_map_provider_preference(path),
                {
                    "version": 1,
                    "preferred_output_provider": "geoapify",
                    "credential_id": "personal",
                    "credential_verified": True,
                },
            )

    def test_known_credentials_only_reports_keyed_providers(self):
        with mock.patch(
            "map_provider_setup.read_provider_credential",
            side_effect=lambda provider, _credential: "secret" if provider == "stadia" else "",
        ):
            self.assertEqual(known_provider_credentials(), {"stadia"})

    def test_valid_key_uses_one_image_request(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        result = validate_provider_credential(
            "geoapify", "secret", timeout_seconds=3.0, opener=opener
        )
        self.assertTrue(result.valid)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", result.message)
        self.assertIn("myCamino", calls[0][0].headers["User-agent"])

    def test_rejected_key_is_not_described_as_network_failure(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        result = validate_provider_credential("stadia", "wrong", opener=opener)
        self.assertFalse(result.valid)
        self.assertFalse(result.network_error)
        self.assertIn("rejected", result.message)
        self.assertNotIn("wrong", result.message)

    def test_network_failure_can_be_distinguished(self):
        def opener(_request, timeout):
            raise urllib.error.URLError("offline")

        result = validate_provider_credential("thunderforest", "secret", opener=opener)
        self.assertFalse(result.valid)
        self.assertTrue(result.network_error)

    def test_custom_xyz_requires_https_placeholders_and_attribution(self):
        self.assertIsNotNone(
            validate_custom_xyz_configuration("http://tiles/{z}/{x}/{y}.png", "Tiles")
        )
        self.assertIsNotNone(
            validate_custom_xyz_configuration("https://tiles/{z}/{x}.png", "Tiles")
        )
        self.assertIsNotNone(
            validate_custom_xyz_configuration("https://tiles/{z}/{x}/{y}.png", "")
        )
        self.assertIsNone(
            validate_custom_xyz_configuration(
                "https://tiles/{z}/{x}/{y}.png", "Example tiles"
            )
        )

    def test_custom_access_error_does_not_echo_credentialed_url(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        result = validate_custom_xyz_access(
            "https://tiles/{z}/{x}/{y}.png?token=very-secret", opener=opener
        )
        self.assertFalse(result.valid)
        self.assertNotIn("very-secret", result.message)


if __name__ == "__main__":
    unittest.main()
