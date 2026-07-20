from django.core.management.base import BaseCommand

from siteapp.exports import export_operator_data


class Command(BaseCommand):
    help = "Export beta registrations, contact messages, and the download audit log."

    def handle(self, *args, **options):
        paths = export_operator_data()
        for label, path in paths.items():
            self.stdout.write(f"{label}: {path}")
