from __future__ import annotations
import html
import json
from .shared import CheckResult, MYCAMINO_DOWNLOAD_PAGE_SIZE, parse_mycamino_download_log, parse_mycamino_news_json

class Views:
    def _json_payload(self, stdout: str) -> dict[str, object]:
        for line in reversed(stdout.splitlines()):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def render_mycamino_news_page(
        self,
        result: CheckResult | None = None,
        form: dict[str, list[str]] | None = None,
        edit_slug: str = "",
    ) -> str:
        listing = self.panel.list_mycamino_news()
        rows = []
        list_error = ""
        if listing.returncode == 0:
            try:
                rows = parse_mycamino_news_json(listing.stdout)
            except ValueError as exc:
                list_error = str(exc)
        else:
            list_error = listing.stderr or listing.stdout or "Could not list application news."

        selected = next((row for row in rows if row["slug"] == edit_slug), None)
        values = {
            "slug": "",
            "title": "",
            "summary": "",
            "kind": "news",
            "app_version": "",
            "link": "https://mycamino.heinofalcke.de/",
            "published_at": "",
        }
        if selected:
            values.update({key: str(selected.get(key, "")) for key in values})
        if form:
            for key in values:
                values[key] = form.get(key, [values[key]])[0]

        notice = ""
        if result is not None:
            detail = result.stdout or result.stderr or "Operation completed."
            css = "status-ok" if result.returncode == 0 else "status-bad"
            notice = f'<p class="{css}">{html.escape(detail.strip())}</p>'
        if list_error:
            notice += f'<p class="status-bad">{html.escape(list_error)}</p>'

        query = html.escape(self.panel.token_query())
        save_action = f"/mycamino-news-save?{query}"
        editor = f"""
<section>
  <h2>{'Edit message' if selected else 'Create message'}</h2>
  <p>Save a draft first, review it below, and publish only when it is ready for application users.</p>
  <form method="post" action="{save_action}">
    <label>Slug <input name="slug" required maxlength="120" pattern="[a-z0-9]+(-[a-z0-9]+)*" value="{html.escape(values['slug'])}"></label>
    <label>Title <input name="title" required maxlength="240" value="{html.escape(values['title'])}"></label>
    <label>Kind <select name="kind"><option value="news"{' selected' if values['kind'] == 'news' else ''}>News</option><option value="update"{' selected' if values['kind'] == 'update' else ''}>Update</option></select></label>
    <label>Application version <input name="app_version" maxlength="40" value="{html.escape(values['app_version'])}"></label>
    <label>Link <input name="link" type="url" value="{html.escape(values['link'])}"></label>
    <label>Publication time <input name="published_at" type="datetime-local" value="{html.escape(values['published_at'][:16])}"></label>
    <label>Message <textarea name="summary" required maxlength="4000" rows="8">{html.escape(values['summary'])}</textarea></label>
    <p><button name="operation" value="save">Save Draft</button> <button name="operation" value="publish">Save and Publish</button></p>
  </form>
</section>
"""
        table_rows = []
        for row in rows:
            slug = str(row["slug"])
            state = "Published" if row["is_published"] else "Draft"
            state_operation = "withdraw" if row["is_published"] else "publish"
            state_label = "Withdraw" if row["is_published"] else "Publish"
            edit_href = "/mycamino-news?" + html.escape(self.panel.token_query(edit=slug))
            state_action = "/mycamino-news-state?" + query
            delete_action = "/mycamino-news-delete?" + query
            controls = (
                f'<a class="button secondary" href="{edit_href}">Edit</a> '
                f'<form class="inline" method="post" action="{state_action}"><input type="hidden" name="slug" value="{html.escape(slug)}"><button name="operation" value="{state_operation}">{state_label}</button></form> '
                f'<form class="inline" method="post" action="{delete_action}"><input type="hidden" name="slug" value="{html.escape(slug)}"><input name="confirmation" size="12" placeholder="type slug"><button>Delete</button></form>'
            )
            table_rows.append(
                "<tr>"
                f"<td>{html.escape(slug)}</td><td>{html.escape(state)}</td>"
                f"<td>{html.escape(str(row['kind']).title())}</td>"
                f"<td>{html.escape(str(row['published_at'])[:16])}</td>"
                f"<td>{html.escape(str(row['title']))}</td><td>{controls}</td></tr>"
            )
        listing_html = (
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable data table"><table><thead><tr><th>Slug</th><th>Status</th><th>Kind</th><th>Publication</th><th>Title</th><th>Actions</th></tr></thead><tbody>'
            + "".join(table_rows)
            + "</tbody></table></div>"
            if table_rows else '<p class="muted">No messages have been created.</p>'
        )
        content = (
            self.back_link()
            + "<h1>myCamino Application News</h1>"
            + "<p>Messages are delivered through the public application feed. The stable slug is the users' local read/unread identity.</p>"
            + notice + editor + "<section><h2>Messages</h2>" + listing_html + "</section>"
        )
        return self.page("myCamino Application News", content)

    def render_mycamino_downloads_page(self, requested_page: str) -> str:
        refresh = self.panel.refresh_mycamino_exports()
        if refresh.returncode != 0:
            detail = html.escape(refresh.stderr or refresh.stdout or "Export refresh failed.")
            return self.page(
                "myCamino Downloads",
                self.back_link() + f'<h1>myCamino Downloads</h1><p class="status-bad">Could not refresh download data.</p><pre>{detail}</pre>',
            )
        result = self.panel.read_mycamino_export("downloads")
        if result.returncode != 0:
            detail = html.escape(result.stderr or result.stdout or "Download export unavailable.")
            return self.page(
                "myCamino Downloads",
                self.back_link() + f'<h1>myCamino Downloads</h1><p class="status-bad">Could not read download data.</p><pre>{detail}</pre>',
            )
        try:
            downloads = parse_mycamino_download_log(result.stdout)
        except ValueError as exc:
            return self.page(
                "myCamino Downloads",
                self.back_link() + f'<h1>myCamino Downloads</h1><p class="status-bad">{html.escape(str(exc))}</p>',
            )

        try:
            page_number = int(requested_page)
        except (TypeError, ValueError):
            page_number = 1
        page_count = max(1, (len(downloads) + MYCAMINO_DOWNLOAD_PAGE_SIZE - 1) // MYCAMINO_DOWNLOAD_PAGE_SIZE)
        page_number = min(max(1, page_number), page_count)
        start = (page_number - 1) * MYCAMINO_DOWNLOAD_PAGE_SIZE
        page_rows = downloads[start : start + MYCAMINO_DOWNLOAD_PAGE_SIZE]

        rows = [
            (
                row["request_date"],
                row["email"],
                row["release"],
                row["file_name"],
                row["method"],
                row["range"],
                row["uri"],
                row["ip_hash"],
                row["user_agent"],
            )
            for row in page_rows
        ]
        table = (
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable data table">'
            + self.table(
                ["Requested", "Email", "Release", "File", "Method", "Range", "URI", "IP hash", "User agent"],
                rows,
            )
            + "</div>"
            if rows
            else '<p class="muted">No authorized download requests have been recorded.</p>'
        )

        navigation = []
        if page_number > 1:
            first_href = "/mycamino-downloads?" + self.panel.token_query(page="1")
            previous_href = "/mycamino-downloads?" + self.panel.token_query(page=str(page_number - 1))
            navigation.extend(
                [
                    f'<a class="button secondary" href="{html.escape(first_href)}">First</a>',
                    f'<a class="button secondary" href="{html.escape(previous_href)}">Previous</a>',
                ]
            )
        if page_number < page_count:
            next_href = "/mycamino-downloads?" + self.panel.token_query(page=str(page_number + 1))
            last_href = "/mycamino-downloads?" + self.panel.token_query(page=str(page_count))
            navigation.extend(
                [
                    f'<a class="button secondary" href="{html.escape(next_href)}">Next</a>',
                    f'<a class="button secondary" href="{html.escape(last_href)}">Last</a>',
                ]
            )
        navigation_html = " ".join(navigation)
        export_href = "/mycamino-export?" + self.panel.token_query(file="downloads")
        content = (
            self.back_link()
            + "<h1>myCamino Downloads</h1>"
            + "<p>Authorized download requests are listed newest first. Raw IP addresses are not stored; IP hash values are pseudonymous.</p>"
            + f'<p><strong>{len(downloads)}</strong> requests &middot; Page {page_number} of {page_count} &middot; 30 per page '
            + f'<a class="button secondary" href="{html.escape(export_href)}">Download complete log</a></p>'
            + table
            + (f"<p>{navigation_html}</p>" if navigation_html else "")
        )
        return self.page("myCamino Downloads", content)

    def render_mycamino_data_page(self) -> str:
        refresh = self.panel.refresh_mycamino_exports()
        sections = [
            "<p><a href=\"/?" + html.escape(self.panel.token_query()) + "\">Back to control panel</a></p>",
            "<h1>myCamino Beta Data &amp; Download Log</h1>",
            "<p>These files are rebuilt from the website database on every refresh. The CSV downloads use UTF-8 with an Excel-compatible byte-order marker. Times include their UTC offset.</p>",
            f'<p><a class="button" href="/mycamino-downloads?{html.escape(self.panel.token_query())}">List Downloads in Pages</a></p>',
        ]
        if refresh.returncode != 0:
            sections.append(f'<p class="status-bad">Could not refresh exports.</p><pre>{html.escape(refresh.stderr or refresh.stdout)}</pre>')
        for key, title, description in (
            ("downloads", "Download requests", "Count and details for every authorized protected-file request."),
            ("registrations", "Beta registrations", "Email addresses, request/consent/verification dates, and download dates."),
            ("contacts", "Contact messages", "All submitted contact fields and mail delivery status."),
        ):
            result = self.panel.read_mycamino_export(key)
            href = "/mycamino-export?" + self.panel.token_query(file=key)
            content = result.stdout.lstrip("\ufeff") if result.returncode == 0 else (result.stderr or "File unavailable.")
            sections.append(
                f'<section><h2>{html.escape(title)}</h2><p>{html.escape(description)} '
                f'<a class="button secondary" href="{html.escape(href)}">Download file</a></p>'
                f'<pre class="mycamino-data">{html.escape(content)}</pre></section>'
            )
        return self.page("myCamino Beta Data", "\n".join(sections))

    def render_mycamino_issue_result(self, action_key: str, result: CheckResult) -> str:
        if result.returncode != 0:
            return self.render_check_result(result)
        payload = self._json_payload(result.stdout)
        issues = payload.get("issues") if isinstance(payload, dict) else None
        counts = payload.get("counts") if isinstance(payload, dict) else None
        if not isinstance(issues, list) or not isinstance(counts, dict):
            invalid = CheckResult(
                result.title,
                result.command,
                1,
                result.stdout,
                "The GitHub issue helper returned an invalid response.",
            )
            return self.render_check_result(invalid)

        total = int(counts.get("total", len(issues)))
        bugs = int(counts.get("bugs", 0))
        features = int(counts.get("feature_requests", 0))
        other = int(counts.get("other", 0))
        if action_key == "mycamino_new_reports":
            message = (
                '<p class="status-ok">There are no new myCamino reports or requests awaiting triage.</p>'
                if total == 0
                else f'<p class="status-warn">{total} myCamino report(s) or request(s) await triage.</p>'
            )
        else:
            message = (
                '<p class="status-ok">There are no open myCamino reports or requests.</p>'
                if total == 0
                else f'<p class="status-warn">There are {total} open myCamino report(s) or request(s).</p>'
            )

        summary = (
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable data table">'
            + self.html_table(
                ["Total", "Bugs", "Feature requests", "Other", "Retrieved (UTC)"],
                [
                    (
                        html.escape(str(total)),
                        html.escape(str(bugs)),
                        html.escape(str(features)),
                        html.escape(str(other)),
                        html.escape(str(payload.get("retrieved_at", ""))),
                    )
                ],
            )
            + "</div>"
        )
        rows = []
        expected_prefix = "https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/"
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            try:
                number = int(issue.get("number", 0))
            except (TypeError, ValueError):
                continue
            if number < 1:
                continue
            issue_url = f"{expected_prefix}{number}"
            issue_type = str(issue.get("type", "Other"))
            if issue_type not in {"Bug", "Feature request", "Other"}:
                issue_type = "Other"
            labels = issue.get("labels", [])
            label_text = ", ".join(str(label) for label in labels) if isinstance(labels, list) else ""
            rows.append(
                (
                    html.escape(issue_type),
                    f'<a href="{issue_url}" target="_blank" rel="noopener">#{number}</a>',
                    f'<a class="issue-title" href="{issue_url}" target="_blank" rel="noopener">{html.escape(str(issue.get("title", "(untitled)")))}</a>',
                    html.escape(str(issue.get("reporter", "unknown"))),
                    html.escape(str(issue.get("created_at", ""))),
                    html.escape(str(issue.get("updated_at", ""))),
                    html.escape(str(issue.get("comments", 0))),
                    html.escape(label_text),
                )
            )
        issue_table = (
            '<div class="table-scroll issue-table" tabindex="0" role="region" aria-label="Scrollable GitHub issues">'
            + self.html_table(
                ["Type", "Issue", "Title", "Reporter", "Created", "Updated", "Comments", "Labels"],
                rows,
            )
            + "</div>"
            if rows
            else ""
        )
        rate = payload.get("rate_limit", {})
        rate_text = ""
        if isinstance(rate, dict) and rate.get("remaining") is not None:
            rate_text = (
                '<p class="muted">GitHub API requests remaining: '
                f'{html.escape(str(rate.get("remaining")))} of {html.escape(str(rate.get("limit", "?")))}. '
                f'Reset: {html.escape(str(rate.get("reset", "unknown")))}.</p>'
            )
        return message + summary + issue_table + rate_text

    def table(self, headers: list[str], rows: list[tuple[str, ...]]) -> str:
        header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
        body_rows = []
        for row in rows:
            cells = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
            body_rows.append(f"<tr>{cells}</tr>")
        return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"

    def html_table(self, headers: list[str], rows: list[tuple[str, ...]]) -> str:
        header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
        body_rows = []
        for row in rows:
            cells = "".join(f"<td>{cell}</td>" for cell in row)
            body_rows.append(f"<tr>{cells}</tr>")
        return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
