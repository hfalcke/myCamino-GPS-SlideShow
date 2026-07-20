import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import F
from django.utils import timezone

from .models import BetaRegistration, ContactMessage


def digest_token(token):
    return hmac.new(settings.SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def digest_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
    ip = forwarded or request.META.get("REMOTE_ADDR", "")
    return hmac.new(settings.FORM_IP_SALT.encode(), ip.encode(), hashlib.sha256).hexdigest() if ip else ""


def issue_verification(registration, request):
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    registration.token_digest = digest_token(token)
    registration.token_expires_at = now + timezone.timedelta(seconds=settings.VERIFY_TOKEN_SECONDS)
    registration.verification_sent_at = now
    registration.save(update_fields=["token_digest", "token_expires_at", "verification_sent_at", "updated_at"])
    url = f"{settings.PUBLIC_BASE_URL}/beta/verify/{token}/"
    EmailMessage(
        subject="Your myCamino beta download",
        body=("Thank you for joining the myCamino beta.\n\n"
              f"Verify your email and download the current macOS beta here:\n{url}\n\n"
              "This link expires in 24 hours. myCamino is GPL-3.0-or-later software."),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[registration.email],
    ).send(fail_silently=False)


def deliver_contact(contact):
    try:
        EmailMessage(
            subject=f"[myCamino contact] {contact.subject}",
            body=f"From: {contact.name} <{contact.email}>\n\n{contact.message}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[settings.CONTACT_RECIPIENT],
            reply_to=[contact.email],
        ).send(fail_silently=False)
    except Exception as exc:
        contact.delivery_error = str(exc)[:2000]
        contact.save(update_fields=["delivery_error"])
        return False
    contact.delivered_at = timezone.now()
    contact.delivery_error = ""
    contact.save(update_fields=["delivered_at", "delivery_error"])
    return True


def count_download(registration):
    BetaRegistration.objects.filter(pk=registration.pk).update(download_count=F("download_count") + 1, last_download_at=timezone.now())
