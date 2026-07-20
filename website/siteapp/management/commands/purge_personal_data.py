from django.core.management.base import BaseCommand
from django.utils import timezone

from siteapp.models import BetaRegistration, ContactMessage


class Command(BaseCommand):
    help = "Delete expired unverified beta registrations and old contact/beta data."

    def add_arguments(self, parser):
        parser.add_argument("--verified-days", type=int, default=455)
        parser.add_argument("--contact-days", type=int, default=365)

    def handle(self, *args, **options):
        now = timezone.now()
        unverified, _ = BetaRegistration.objects.filter(verified_at__isnull=True, created_at__lt=now - timezone.timedelta(days=7)).delete()
        verified, _ = BetaRegistration.objects.filter(verified_at__isnull=False, updated_at__lt=now - timezone.timedelta(days=options["verified_days"])).delete()
        contacts, _ = ContactMessage.objects.filter(created_at__lt=now - timezone.timedelta(days=options["contact_days"])).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {unverified} unverified, {verified} verified, and {contacts} contact records."))
