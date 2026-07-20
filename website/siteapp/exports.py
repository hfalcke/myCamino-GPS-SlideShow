import csv
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import BetaRegistration, ContactMessage, DownloadEvent


def _excel_safe(value):
    """Prevent spreadsheet software from interpreting submitted text as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _iso(value):
    return timezone.localtime(value).isoformat(timespec="seconds") if value else ""


def _atomic_write(path, writer):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
            writer(stream)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def export_operator_data(root=None):
    root = Path(root or settings.MYCAMINO_EXPORT_ROOT)
    registrations = list(BetaRegistration.objects.prefetch_related("download_events").order_by("created_at", "email"))
    contacts = list(ContactMessage.objects.order_by("created_at", "id"))
    events = list(DownloadEvent.objects.select_related("registration", "release").order_by("requested_at", "id"))

    def write_registrations(stream):
        writer = csv.writer(stream)
        writer.writerow([
            "email", "request_date", "consent_date", "verification_sent_date", "verified_date",
            "download_request_count", "first_download_request_date", "last_download_request_date",
            "active", "registration_ip_hash",
        ])
        for registration in registrations:
            event_dates = [event.requested_at for event in registration.download_events.all()]
            writer.writerow([
                _excel_safe(registration.email), _iso(registration.created_at), _iso(registration.consent_at),
                _iso(registration.verification_sent_at), _iso(registration.verified_at), registration.download_count,
                _iso(min(event_dates)) if event_dates else "", _iso(registration.last_download_at),
                registration.is_active, registration.ip_digest,
            ])

    def write_contacts(stream):
        writer = csv.writer(stream)
        writer.writerow([
            "received_date", "name", "email", "subject", "message", "consent_date",
            "delivered_date", "delivery_error", "admin_notes", "ip_hash",
        ])
        for contact in contacts:
            writer.writerow([
                _iso(contact.created_at), _excel_safe(contact.name), _excel_safe(contact.email),
                _excel_safe(contact.subject), _excel_safe(contact.message), _iso(contact.consent_at),
                _iso(contact.delivered_at), _excel_safe(contact.delivery_error),
                _excel_safe(contact.admin_notes), contact.ip_digest,
            ])

    def write_download_log(stream):
        stream.write(f"Total authorized download requests: {len(events)}\n")
        stream.write(f"Generated: {_iso(timezone.now())}\n\n")
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow([
            "request_date", "email", "release", "file_name", "sha256", "ip_hash",
            "method", "uri", "range", "user_agent",
        ])
        for event in events:
            release = event.release
            writer.writerow([
                _iso(event.requested_at), _excel_safe(event.registration.email),
                _excel_safe(release.label if release else ""), _excel_safe(release.file_name if release else ""),
                release.sha256 if release else "", event.ip_digest, event.request_method,
                _excel_safe(event.request_uri), _excel_safe(event.range_header), _excel_safe(event.user_agent),
            ])

    paths = {
        "registrations": root / "beta-registrations.csv",
        "contacts": root / "contact-messages.csv",
        "downloads": root / "download-requests.log",
    }
    _atomic_write(paths["registrations"], write_registrations)
    _atomic_write(paths["contacts"], write_contacts)
    _atomic_write(paths["downloads"], write_download_log)
    return paths
