import json
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from siteapp.models import ApplicationNews


def _publication_time(value):
    if not value:
        return timezone.now()
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError("--published-at must be an ISO date and time")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


class Command(BaseCommand):
    help = "List, create, update, publish, withdraw, or delete myCamino application news."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("list", "put", "publish", "withdraw", "delete"))
        parser.add_argument("slug", nargs="?")
        parser.add_argument("--title")
        parser.add_argument("--summary")
        parser.add_argument("--summary-file")
        parser.add_argument("--kind", choices=("news", "update"))
        parser.add_argument("--app-version")
        parser.add_argument("--link")
        parser.add_argument("--published-at")
        parser.add_argument("--publish", action="store_true")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--confirm")

    def _require_slug(self, options):
        slug = str(options.get("slug") or "").strip()
        if not slug:
            raise CommandError(f"{options['action']} requires a slug")
        return slug

    def _summary(self, options):
        if options.get("summary") is not None and options.get("summary_file"):
            raise CommandError("use either --summary or --summary-file, not both")
        source = options.get("summary_file")
        if source == "-":
            return sys.stdin.read()
        if source:
            try:
                with open(source, encoding="utf-8") as stream:
                    return stream.read()
            except OSError as exc:
                raise CommandError(f"could not read summary file: {exc}") from exc
        return options.get("summary")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "list":
            return self._list(options)
        slug = self._require_slug(options)
        if action == "put":
            return self._put(slug, options)
        if action == "publish":
            item = self._get(slug)
            item.published_at = _publication_time(options.get("published_at"))
            item.is_published = True
            item.save(update_fields=("published_at", "is_published", "updated_at"))
            self.stdout.write(self.style.SUCCESS(f"Published {slug}"))
            return
        if action == "withdraw":
            item = self._get(slug)
            item.is_published = False
            item.save(update_fields=("is_published", "updated_at"))
            self.stdout.write(self.style.SUCCESS(f"Withdrew {slug}"))
            return
        if options.get("confirm") != slug:
            raise CommandError(f"deleting {slug} requires --confirm {slug}")
        self._get(slug).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {slug}"))

    def _get(self, slug):
        try:
            return ApplicationNews.objects.get(slug=slug)
        except ApplicationNews.DoesNotExist as exc:
            raise CommandError(f"unknown application-news slug: {slug}") from exc

    def _put(self, slug, options):
        summary = self._summary(options)
        with transaction.atomic():
            item = ApplicationNews.objects.filter(slug=slug).first()
            created = item is None
            if created:
                if not options.get("title") or summary is None:
                    raise CommandError("new messages require --title and --summary or --summary-file")
                item = ApplicationNews(slug=slug, published_at=_publication_time(options.get("published_at")))
            for option, attribute in (
                ("title", "title"),
                ("kind", "kind"),
                ("app_version", "app_version"),
                ("link", "link"),
            ):
                value = options.get(option)
                if value is not None:
                    setattr(item, attribute, value.strip())
            if summary is not None:
                item.summary = summary.strip()
            if options.get("published_at"):
                item.published_at = _publication_time(options["published_at"])
            if options.get("publish"):
                item.is_published = True
            item.full_clean()
            item.save()
        state = "published" if item.is_published else "draft"
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {slug} ({state})"))

    def _list(self, options):
        queryset = ApplicationNews.objects.all()
        if not options.get("all"):
            queryset = queryset.filter(is_published=True, published_at__lte=timezone.now())
        rows = [
            {
                "slug": item.slug,
                "title": item.title,
                "summary": item.summary,
                "kind": item.kind,
                "app_version": item.app_version,
                "link": item.link,
                "published_at": item.published_at.isoformat(),
                "is_published": item.is_published,
            }
            for item in queryset
        ]
        if options.get("json"):
            self.stdout.write(json.dumps(rows, ensure_ascii=False))
            return
        if not rows:
            self.stdout.write("No application news items.")
            return
        for row in rows:
            state = "published" if row["is_published"] else "draft"
            self.stdout.write(
                f"{row['slug']}\t{state}\t{row['kind']}\t{row['published_at']}\t{row['title']}"
            )
