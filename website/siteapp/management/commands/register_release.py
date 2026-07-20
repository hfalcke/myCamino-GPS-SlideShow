import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from siteapp.models import Release


class Command(BaseCommand):
    help = "Verify a release file and atomically make its metadata active."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--label", required=True)
        parser.add_argument("--date", required=True)

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"Release file does not exist: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        with transaction.atomic():
            Release.objects.filter(is_active=True).update(is_active=False)
            release = Release.objects.create(label=options["label"], file_name=path.name, release_date=options["date"], file_size=path.stat().st_size, sha256=digest.hexdigest(), is_active=True)
        self.stdout.write(self.style.SUCCESS(f"Activated {release.label}: {release.sha256}"))
