import hashlib
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.messages import get_messages
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from siteapp.models import BetaRegistration, ContactMessage, DownloadEvent, Release
from siteapp.services import digest_token


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="https://test.example", VERIFY_RESEND_SECONDS=600)
class PublicSiteTests(TestCase):
    def test_public_pages_render(self):
        for name in ("home", "documentation", "faq", "contact", "privacy", "imprint", "beta-download", "health"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertContains(self.client.get(reverse("documentation-detail", args=["slideshow"])), "Basic Workflow")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "vivid, animated slide show")
        self.assertContains(response, "Walking the Camino over 2,300 km")
        self.assertContains(response, "Heino Falcke")
        self.assertContains(response, "data-gallery")
        self.assertContains(response, "timelapse-photo-mountains.webp")
        self.assertContains(response, "timelapse-photo-horses.webp")
        self.assertContains(response, "fullscreen-picture.webp")
        self.assertContains(response, "Read the required installation steps")
        self.assertContains(response, "Download (free beta-test)")
        self.assertNotContains(response, "Join the macOS beta")
        html = response.content.decode()
        gallery_order = (
            "timelapse-sunrise.webp",
            "timelapse-photo-mountains.webp",
            "timelapse-photo-horses.webp",
            "timelapse-overview.webp",
            "timelapse-elevation.webp",
            "collage.webp",
            "fullscreen-picture.webp",
        )
        positions = [html.index(image) for image in gallery_order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

        faq = self.client.get(reverse("faq"))
        contact = self.client.get(reverse("contact"))
        for report_page in (faq, contact):
            self.assertContains(report_page, "template=01-bug.yml")
            self.assertContains(report_page, "template=02-feature-request.yml")
        self.assertContains(contact, "private form")

    def test_workflow_guides_prefer_app_icons_and_omit_migration_notes(self):
        for guide in ("slideshow", "gpx-editor"):
            response = self.client.get(reverse("documentation-detail", args=[guide]))
            self.assertContains(response, "icon in Applications")
            self.assertContains(response, "Experts working directly from the source code")
            self.assertNotContains(response, "migrat", status_code=200)

    def test_beta_registration_normalizes_email_and_hashes_token(self):
        response = self.client.post(reverse("beta-download"), {"email": "  Walker@Example.ORG ", "consent": "on", "website": ""})
        self.assertIn(
            "If the address can receive an e-mail, a download link for the program will arrive shortly.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )
        self.assertRedirects(response, reverse("beta-download"))
        registration = BetaRegistration.objects.get()
        self.assertEqual(registration.email, "walker@example.org")
        self.assertEqual(len(registration.token_digest), 64)
        self.assertNotIn("walker@example.org", registration.token_digest)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("System Settings > Privacy & Security", mail.outbox[0].body)
        self.assertIn("Open Anyway", mail.outbox[0].body)

    def test_unsigned_beta_installation_steps_are_available(self):
        response = self.client.get(reverse("beta-download"))
        self.assertContains(response, "Request Download Link")
        self.assertContains(
            response,
            "This is an unsigned .DMG file that is not verified by Apple.",
        )
        self.assertContains(response, 'href="#install-beta">instructions</a>')
        self.assertContains(response, 'id="install-beta"')
        self.assertContains(response, "Read the installation steps before downloading")
        self.assertContains(response, "Apple cannot check the app for malicious software")
        self.assertContains(response, "Privacy &amp; Security")
        self.assertContains(response, "Open Anyway")
        self.assertContains(response, "Dennoch öffnen")
        self.assertContains(response, "Apple’s official instructions")

    def test_duplicate_registration_respects_cooldown(self):
        data = {"email": "walker@example.org", "consent": "on", "website": ""}
        self.client.post(reverse("beta-download"), data)
        self.client.post(reverse("beta-download"), data)
        self.assertEqual(BetaRegistration.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_verification_establishes_download_session_and_remains_usable_until_expiry(self):
        token = "known-verification-token"
        Release.objects.create(
            label="Test beta", file_name="test.dmg", release_date="2026-07-20",
            file_size=123, sha256="a" * 64, is_active=True,
        )
        registration = BetaRegistration.objects.create(email="walker@example.org", consent_at=timezone.now(), token_digest=digest_token(token), token_expires_at=timezone.now()+timezone.timedelta(hours=1))
        response = self.client.get(reverse("verify-beta", args=[token]))
        self.assertRedirects(response, reverse("protected-download"), fetch_redirect_response=False)
        registration.refresh_from_db()
        self.assertIsNotNone(registration.verified_at)
        self.assertEqual(registration.token_digest, digest_token(token))
        self.assertGreater(registration.token_expires_at, timezone.now())
        self.assertEqual(registration.download_count, 0)
        self.assertEqual(self.client.get(
            reverse("authorize-download"),
            HTTP_USER_AGENT="Trail Browser", HTTP_X_FORWARDED_METHOD="GET",
            HTTP_X_FORWARDED_URI="/downloads/myCamino-GPS-Track-Show.dmg", HTTP_RANGE="bytes=0-1023",
        ).status_code, 204)
        registration.refresh_from_db()
        self.assertEqual(registration.download_count, 1)
        event = DownloadEvent.objects.get()
        self.assertEqual(event.range_header, "bytes=0-1023")
        self.assertEqual(event.request_uri, "/downloads/myCamino-GPS-Track-Show.dmg")
        second_browser = self.client_class()
        self.assertRedirects(
            second_browser.get(reverse("verify-beta", args=[token])),
            reverse("protected-download"),
            fetch_redirect_response=False,
        )
        self.assertEqual(second_browser.get(reverse("authorize-download")).status_code, 204)
        registration.refresh_from_db()
        self.assertEqual(registration.download_count, 2)

    def test_expired_token_does_not_authorize(self):
        token = "expired"
        BetaRegistration.objects.create(email="walker@example.org", consent_at=timezone.now(), token_digest=digest_token(token), token_expires_at=timezone.now()-timezone.timedelta(seconds=1))
        self.assertRedirects(self.client.get(reverse("verify-beta", args=[token])), reverse("beta-download"))
        self.assertEqual(self.client.get(reverse("authorize-download")).status_code, 401)

    def test_contact_is_stored_and_delivered(self):
        response = self.client.post(reverse("contact"), {"name":"Ada Walker","email":"ada@example.org","subject":"A trail note","message":"A detailed and useful beta message.","consent":"on","website":""})
        self.assertRedirects(response, reverse("contact"))
        contact = ContactMessage.objects.get()
        self.assertIsNotNone(contact.delivered_at)
        self.assertEqual(mail.outbox[0].reply_to, ["ada@example.org"])

    @mock.patch("siteapp.services.EmailMessage.send", side_effect=RuntimeError("SMTP unavailable"))
    def test_contact_failure_is_preserved_for_admin_retry(self, send):
        self.client.post(reverse("contact"), {"name":"Ada Walker","email":"ada@example.org","subject":"A trail note","message":"Please preserve this message.","consent":"on","website":""})
        contact = ContactMessage.objects.get()
        self.assertIsNone(contact.delivered_at)
        self.assertIn("SMTP unavailable", contact.delivery_error)

    def test_consent_is_required(self):
        self.client.post(reverse("beta-download"), {"email":"ada@example.org"})
        self.assertEqual(BetaRegistration.objects.count(), 0)


class MaintenanceTests(TestCase):
    def test_purge_removes_expired_unverified_registration(self):
        item = BetaRegistration.objects.create(email="old@example.org", consent_at=timezone.now())
        BetaRegistration.objects.filter(pk=item.pk).update(created_at=timezone.now()-timezone.timedelta(days=8))
        call_command("purge_personal_data")
        self.assertFalse(BetaRegistration.objects.filter(pk=item.pk).exists())

    def test_register_release_computes_metadata_and_switches_active_release(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "beta.dmg"
            artifact.write_bytes(b"release-content")
            call_command("register_release", str(artifact), label="Test beta", date="2026-07-21")
        release = Release.objects.get(label="Test beta")
        self.assertTrue(release.is_active)
        self.assertEqual(release.sha256, hashlib.sha256(b"release-content").hexdigest())
        self.assertEqual(Release.objects.filter(is_active=True).count(), 1)

    def test_operator_exports_are_excel_compatible_and_include_audit_data(self):
        registration = BetaRegistration.objects.create(
            email="walker@example.org", consent_at=timezone.now(), verified_at=timezone.now(),
            download_count=1, last_download_at=timezone.now(),
        )
        release = Release.objects.create(
            label="Test beta", file_name="test.dmg", release_date="2026-07-20",
            file_size=123, sha256="b" * 64, is_active=True,
        )
        DownloadEvent.objects.create(
            registration=registration, release=release, ip_digest="hash", user_agent="Browser",
            request_method="GET", request_uri="/downloads/test.dmg", range_header="bytes=0-10",
        )
        ContactMessage.objects.create(
            name="=unsafe", email="walker@example.org", subject="Question", message="Hello",
            consent_at=timezone.now(),
        )
        with tempfile.TemporaryDirectory() as directory, override_settings(MYCAMINO_EXPORT_ROOT=directory):
            call_command("export_operator_data")
            registrations = (Path(directory) / "beta-registrations.csv").read_text(encoding="utf-8-sig")
            contacts = (Path(directory) / "contact-messages.csv").read_text(encoding="utf-8-sig")
            log = (Path(directory) / "download-requests.log").read_text(encoding="utf-8-sig")
        self.assertIn("walker@example.org", registrations)
        self.assertIn("'=unsafe", contacts)
        self.assertIn("Total authorized download requests: 1", log)
        self.assertIn("bytes=0-10", log)
