"""App Store distribution boundaries and native metadata tests.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import GetGeoLocations as geo
import map_provider_utils
from apple_media_metadata import (
    NativeMetadata,
    convert_image_to_jpeg,
    imageio_properties_to_metadata,
)
from runtime_distribution import (
    Distribution,
    current_distribution,
    external_runtime_tool_allowed,
    metadata_backend_name,
)
from security_scoped_access import (
    BOOKMARK_DEFAULTS_KEY,
    SecurityScopedAccessManager,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeDefaults:
    def __init__(self):
        self.values = {}

    def dictionaryForKey_(self, key):
        return self.values.get(key)

    def setObject_forKey_(self, value, key):
        self.values[key] = dict(value)


class AppStoreFoundationTests(unittest.TestCase):
    def test_distribution_policy_blocks_only_external_store_tools(self):
        store = {"MYCAMINO_DISTRIBUTION": "app-store"}
        direct = {"MYCAMINO_DISTRIBUTION": "direct"}
        self.assertEqual(current_distribution(store), Distribution.APP_STORE)
        self.assertEqual(current_distribution(direct), Distribution.DIRECT)
        for tool in ("exiftool", "mdls", "sips", "security", "ps"):
            self.assertFalse(external_runtime_tool_allowed(tool, store))
            self.assertTrue(external_runtime_tool_allowed(tool, direct))
        self.assertTrue(external_runtime_tool_allowed("ffmpeg", store))
        self.assertEqual(metadata_backend_name(store), "native")

    def test_imageio_metadata_maps_signed_gps_and_timezone(self):
        result = imageio_properties_to_metadata(
            {
                "{GPS}": {
                    "Latitude": 43.1,
                    "LatitudeRef": "N",
                    "Longitude": 1.25,
                    "LongitudeRef": "W",
                    "DateStamp": "2024:07:15",
                    "TimeStamp": "05:55:36",
                },
                "{Exif}": {
                    "DateTimeOriginal": "2024:07:15 07:55:37",
                    "OffsetTimeOriginal": "+02:00",
                },
            }
        )
        self.assertEqual(result.embedded["GPSLongitude"], -1.25)
        self.assertEqual(result.embedded["GPSDateTime"], "2024:07:15 05:55:36Z")
        self.assertEqual(
            result.embedded["DateTimeOriginal"], "2024:07:15 07:55:37+02:00"
        )

    def test_store_prefetch_never_calls_external_metadata_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = Path(temporary, "photo.jpeg").resolve()
            media.write_bytes(b"media")
            native = NativeMetadata(
                {"DateTimeOriginal": "2024:07:15 07:55:37+02:00"},
                {"kMDItemLatitude": 43.1, "kMDItemLongitude": -1.25},
                "imageio",
            )
            with (
                patch.dict("os.environ", {"MYCAMINO_DISTRIBUTION": "app-store"}),
                patch.object(geo, "read_native_media_metadata", return_value=native),
                patch.object(geo.subprocess, "run", side_effect=AssertionError("external tool")),
            ):
                bundle = geo.prefetch_media_metadata([media])[media]
        self.assertEqual(bundle.provider, "native")
        self.assertFalse(bundle.exiftool_available)
        self.assertEqual(bundle.spotlight["kMDItemLatitude"], 43.1)
        self.assertEqual(
            geo.datetime_from_metadata_bundle(bundle)[0].isoformat(),
            "2024-07-15T07:55:37+02:00",
        )
        self.assertEqual(
            geo.datetime_from_metadata_bundle(bundle)[1]["selected_source"],
            "native:DateTimeOriginal",
        )

    def test_store_single_file_datetime_uses_native_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = Path(temporary, "photo.jpeg")
            media.write_bytes(b"media")
            native = NativeMetadata(
                {"DateTimeOriginal": "2024:07:15 07:55:37+02:00"}, {}, "imageio"
            )
            with (
                patch.dict("os.environ", {"MYCAMINO_DISTRIBUTION": "app-store"}),
                patch.object(geo, "read_native_media_metadata", return_value=native),
                patch.object(geo.subprocess, "run", side_effect=AssertionError("external tool")),
            ):
                selected, details = geo.get_photo_datetime_with_debug(media)
        self.assertEqual(selected.isoformat(), "2024-07-15T07:55:37+02:00")
        self.assertEqual(details["selected_source"], "native:DateTimeOriginal")

    def test_native_image_conversion_replaces_sips(self):
        candidates = sorted((ROOT / "demo").rglob("*.jpeg"))
        if not candidates:
            self.skipTest("bundled demo JPEG is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary, "converted.jpeg")
            convert_image_to_jpeg(candidates[0], output, quality=0.8)
            self.assertTrue(output.read_bytes().startswith(b"\xff\xd8"))

    def test_store_provider_credentials_use_security_framework_wrapper(self):
        store = {"MYCAMINO_DISTRIBUTION": "app-store"}
        with (
            patch.dict("os.environ", store, clear=False),
            patch("macos_keychain.read_generic_password", return_value="secret") as read,
            patch("macos_keychain.store_generic_password") as write,
            patch.object(map_provider_utils.subprocess, "run", side_effect=AssertionError("CLI")),
        ):
            self.assertEqual(
                map_provider_utils.read_provider_credential("geoapify", "maps"),
                "secret",
            )
            map_provider_utils.store_provider_credential("geoapify", "new", "maps")
        read.assert_called_once_with("org.mycamino.maps.geoapify", "maps")
        write.assert_called_once_with("org.mycamino.maps.geoapify", "maps", "new")

    def test_bookmark_manager_balances_active_scope(self):
        defaults = FakeDefaults()
        url = Mock()
        url.path.return_value = "/tmp/Adventure"
        url.bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error_.return_value = (
            b"bookmark",
            None,
        )
        url.startAccessingSecurityScopedResource.return_value = True
        manager = SecurityScopedAccessManager(defaults)
        with patch("security_scoped_access.is_app_store_build", return_value=True):
            self.assertTrue(manager.remember_url(url))
            self.assertTrue(manager.encoded_bookmark("/tmp/Adventure"))
            manager.stop()
        self.assertIn(BOOKMARK_DEFAULTS_KEY, defaults.values)
        url.stopAccessingSecurityScopedResource.assert_called_once_with()

    def test_malformed_bookmark_is_a_safe_permission_miss(self):
        defaults = FakeDefaults()
        defaults.values[BOOKMARK_DEFAULTS_KEY] = {"/tmp/Adventure": "not base64!"}
        manager = SecurityScopedAccessManager(defaults)
        with patch("security_scoped_access.is_app_store_build", return_value=True):
            self.assertFalse(manager.restore("/tmp/Adventure"))

    def test_store_resources_and_specs_are_declared(self):
        with (ROOT / "packaging/app-store/PrivacyInfo.xcprivacy").open("rb") as handle:
            privacy = plistlib.load(handle)
        with (ROOT / "packaging/app-store/myCamino.entitlements").open("rb") as handle:
            entitlements = plistlib.load(handle)
        self.assertFalse(privacy["NSPrivacyTracking"])
        self.assertTrue(entitlements["com.apple.security.app-sandbox"])
        spec = (ROOT / "myCamino GPS Track Show.spec").read_text(encoding="utf-8")
        self.assertIn("PrivacyInfo.xcprivacy", spec)
        self.assertIn("MYCaminoDistribution", spec)
        build = (ROOT / "build_app_store.sh").read_text(encoding="utf-8")
        self.assertIn("check_app_store_compliance.py", build)
        self.assertIn("embedded.provisionprofile", build)
        checker = (ROOT / "scripts/check_app_store_compliance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("com.apple.security.inherit", checker)
        self.assertIn("validate_nested_code", checker)


if __name__ == "__main__":
    unittest.main()
