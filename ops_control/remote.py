from __future__ import annotations
import shlex
from pathlib import Path
from .shared import CheckResult, MYCAMINO_NEWS_SLUG_RE

class RemoteOperations:
    def refresh_mycamino_exports(self) -> CheckResult:
        return self.run_vps_bash(
            "Refresh myCamino operator exports",
            "docker compose exec -T web python manage.py export_operator_data",
            cwd=Path("/opt/mycamino/site"),
            timeout=45,
        )

    def read_mycamino_export(self, key: str) -> CheckResult:
        paths = {
            "downloads": "/exports/download-requests.log",
            "registrations": "/exports/beta-registrations.csv",
            "contacts": "/exports/contact-messages.csv",
        }
        path = paths.get(key)
        if not path:
            return CheckResult("Read myCamino export", key, 2, "", "Unknown export file.")
        return self.run_vps_bash(
            f"Read {path}",
            f"docker compose exec -T web cat {shlex.quote(path)}",
            cwd=Path("/opt/mycamino/site"),
            timeout=30,
        )

    def run_mycamino_news(self, arguments: list[str], *, timeout: int = 45) -> CheckResult:
        command = "docker compose exec -T web python manage.py app_news " + " ".join(
            shlex.quote(str(value)) for value in arguments
        )
        return self.run_vps_bash(
            "Manage myCamino application news",
            command,
            cwd=Path("/opt/mycamino/site"),
            timeout=timeout,
        )

    def list_mycamino_news(self) -> CheckResult:
        return self.run_mycamino_news(["list", "--all", "--json"])

    @staticmethod
    def _news_validation_error(message: str) -> CheckResult:
        return CheckResult("Manage myCamino application news", "validation", 2, "", message)

    def save_mycamino_news(self, form: dict[str, list[str]]) -> CheckResult:
        slug = form.get("slug", [""])[0].strip()
        title = form.get("title", [""])[0].strip()
        summary = form.get("summary", [""])[0].strip()
        kind = form.get("kind", ["news"])[0].strip()
        app_version = form.get("app_version", [""])[0].strip()
        link = form.get("link", [""])[0].strip()
        published_at = form.get("published_at", [""])[0].strip()
        operation = form.get("operation", ["save"])[0]
        if not MYCAMINO_NEWS_SLUG_RE.fullmatch(slug):
            return self._news_validation_error("Slug must contain lowercase letters, numbers, and single hyphens.")
        if not title or len(title) > 240:
            return self._news_validation_error("Title is required and may contain at most 240 characters.")
        if not summary or len(summary) > 4000:
            return self._news_validation_error("Summary is required and may contain at most 4000 characters.")
        if kind not in {"news", "update"}:
            return self._news_validation_error("Kind must be news or update.")
        if len(app_version) > 40:
            return self._news_validation_error("App version may contain at most 40 characters.")
        if link and not link.startswith("https://"):
            return self._news_validation_error("Link must be blank or use HTTPS.")
        arguments = [
            "put", slug, "--title", title, "--summary", summary,
            "--kind", kind, "--app-version", app_version, "--link", link,
        ]
        if published_at:
            arguments.extend(["--published-at", published_at])
        if operation == "publish":
            arguments.append("--publish")
        return self.run_mycamino_news(arguments)

    def change_mycamino_news_state(self, form: dict[str, list[str]]) -> CheckResult:
        slug = form.get("slug", [""])[0].strip()
        operation = form.get("operation", [""])[0]
        if not MYCAMINO_NEWS_SLUG_RE.fullmatch(slug) or operation not in {"publish", "withdraw"}:
            return self._news_validation_error("Invalid news state request.")
        return self.run_mycamino_news([operation, slug])

    def delete_mycamino_news(self, form: dict[str, list[str]]) -> CheckResult:
        slug = form.get("slug", [""])[0].strip()
        confirmation = form.get("confirmation", [""])[0].strip()
        if not MYCAMINO_NEWS_SLUG_RE.fullmatch(slug) or confirmation != slug:
            return self._news_validation_error("Type the exact slug to confirm deletion.")
        return self.run_mycamino_news(["delete", slug, "--confirm", slug])
