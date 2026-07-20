from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_http_methods

from .forms import BetaRegistrationForm, ContactForm
from .models import BetaRegistration, ContactMessage, Release
from .services import count_download, deliver_contact, digest_ip, digest_token, issue_verification, refresh_operator_exports


def latest_release():
    return Release.objects.filter(is_active=True).first()


def page(request, template, **context):
    context.setdefault("release", latest_release())
    return render(request, template, context)


def home(request):
    return page(request, "siteapp/home.html")


def faq(request):
    return page(request, "siteapp/faq.html")


def privacy(request):
    return page(request, "siteapp/privacy.html")


def imprint(request):
    return page(request, "siteapp/imprint.html", operator_name=settings.OPERATOR_NAME, operator_address=settings.OPERATOR_ADDRESS)


def documentation_index(request):
    return page(request, "siteapp/documentation_index.html")


@require_GET
def documentation_detail(request, guide):
    guides = {
        "slideshow": ("myCamino GPS Track Show", "MYCAMINO_GPS_TRACK_SHOW_USER_GUIDE.md"),
        "gpx-editor": ("myCamino GPX Editor", "GPXEDITOR_USER_GUIDE.md"),
    }
    if guide not in guides:
        return HttpResponse(status=404)
    title, filename = guides[guide]
    source = (settings.DOCS_ROOT / filename).resolve()
    if source.parent != settings.DOCS_ROOT.resolve() or not source.is_file():
        return page(request, "siteapp/documentation_detail.html", title=title, guide_html="<p>Guide unavailable.</p>")
    rendered = markdown.markdown(source.read_text(encoding="utf-8"), extensions=["fenced_code", "tables", "toc"])
    return page(request, "siteapp/documentation_detail.html", title=title, guide_html=mark_safe(rendered))


def _throttled(request, purpose, limit=6, window=600):
    key = f"throttle:{purpose}:{digest_ip(request)}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit


@require_http_methods(["GET", "POST"])
def beta_download(request):
    form = BetaRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["website"] or _throttled(request, "beta"):
            messages.success(request, "If the address can receive beta mail, a download link will arrive shortly.")
            return redirect("beta-download")
        now = timezone.now()
        registration, _ = BetaRegistration.objects.get_or_create(
            email=form.cleaned_data["email"],
            defaults={"consent_at": now, "ip_digest": digest_ip(request)},
        )
        registration.consent_at = now
        registration.ip_digest = digest_ip(request)
        registration.is_active = True
        registration.save(update_fields=["consent_at", "ip_digest", "is_active", "updated_at"])
        cooldown = registration.verification_sent_at and now - registration.verification_sent_at < timezone.timedelta(seconds=settings.VERIFY_RESEND_SECONDS)
        if not cooldown:
            try:
                issue_verification(registration, request)
            except Exception:
                pass
        refresh_operator_exports()
        messages.success(request, "If the address can receive beta mail, a download link will arrive shortly.")
        return redirect("beta-download")
    return page(request, "siteapp/download.html", form=form)


@require_GET
def verify_beta(request, token):
    now = timezone.now()
    registration = get_object_or_404(BetaRegistration, token_digest=digest_token(token), is_active=True)
    if not registration.token_expires_at or registration.token_expires_at <= now:
        messages.error(request, "This beta link has expired. Request a new one below.")
        return redirect("beta-download")
    with transaction.atomic():
        if registration.verified_at is None:
            registration.verified_at = now
            registration.save(update_fields=["verified_at", "updated_at"])
    request.session["beta_registration_id"] = registration.pk
    request.session.set_expiry(settings.DOWNLOAD_SESSION_SECONDS)
    return redirect("protected-download")


@require_GET
def authorize_download(request):
    registration_id = request.session.get("beta_registration_id")
    registration = BetaRegistration.objects.filter(pk=registration_id, is_active=True, verified_at__isnull=False).first()
    release = latest_release()
    if not registration or not release:
        return HttpResponse("Beta access required", status=401)
    count_download(registration, request, release)
    response = HttpResponse(status=204)
    response.headers["Cache-Control"] = "no-store"
    return response


@require_GET
def protected_download_fallback(request):
    """Caddy serves this route in production after forward authorization."""
    messages.info(request, "Verify your email to download the beta.")
    return redirect("beta-download")


@require_http_methods(["GET", "POST"])
def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["website"] or _throttled(request, "contact", limit=4):
            messages.success(request, "Thank you. Your message has been received.")
            return redirect("contact")
        contact_message = ContactMessage.objects.create(
            name=form.cleaned_data["name"], email=form.cleaned_data["email"],
            subject=form.cleaned_data["subject"], message=form.cleaned_data["message"],
            consent_at=timezone.now(), ip_digest=digest_ip(request),
        )
        deliver_contact(contact_message)
        refresh_operator_exports()
        messages.success(request, "Thank you. Your message has been received.")
        return redirect("contact")
    return page(request, "siteapp/contact.html", form=form)


@require_GET
def health(request):
    return HttpResponse("ok", content_type="text/plain")
