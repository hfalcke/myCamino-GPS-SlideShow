#!/usr/bin/env python3
"""Independent, loopback-only myCamino operator panel."""
from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from .remote import RemoteOperations
from .shared import (CheckResult, ControlAction, MYCAMINO_DOWNLOAD_PAGE_SIZE,
                     parse_mycamino_download_log, parse_mycamino_news_json)
from .views import Views
from .app_code import CODE_ACTIONS, action_arguments, read_metadata
from .build_jobs import BuildJobs

REPO_ROOT = Path(__file__).resolve().parents[1]
COOKIE = 'mycamino_control'
EXPORTS = {'downloads': ('download-requests.log', 'text/plain'),
           'registrations': ('beta-registrations.csv', 'text/csv'),
           'contacts': ('contact-messages.csv', 'text/csv')}


def run_command(argv, *, cwd=REPO_ROOT, timeout=45, env=None):
    """Bound execution time, including descendants, and expose failures."""
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                   text=True, start_new_session=True, env=env)
        try:
            out, err = process.communicate(timeout=timeout)
            return CheckResult('Command', shlex.join(argv), process.returncode, out, err)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                out, err = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                out, err = process.communicate()
            return CheckResult('Command', shlex.join(argv), 124, out, err + '\nCommand timed out.')
    except OSError as exc:
        return CheckResult('Command', shlex.join(argv), 1, '', str(exc))


class ControlPanel(RemoteOperations):
    def __init__(self, token):
        self.token = token
        self.csrf = secrets.token_urlsafe(32)
        self.state_root = Path(os.environ.get('MYCAMINO_CONTROL_STATE',
                                            str(Path.home()/'.local/state/mycamino-control')))
        self.action_log_path = self.state_root/'actions.log'
        self.build_jobs = BuildJobs(self.state_root, self.audit)
        self.ssh_host = os.environ.get('MYCAMINO_SSH_HOST', 'staging.fedipass.org')
        self.ssh_user = os.environ.get('MYCAMINO_SSH_USER', 'deploy')
        self.ssh_key = os.path.expanduser(os.environ.get('MYCAMINO_SSH_KEY',
                                    '~/.ssh/fedipass_staging_ed25519'))

    def token_query(self, **extra):
        # Authentication stays in a host-only cookie, never in outgoing links.
        return urlencode(extra)

    def run_vps_bash(self, label, command, *, cwd=None, timeout=45):
        if cwd is not None:
            command = 'cd ' + shlex.quote(str(cwd)) + ' && ' + command
        result = run_command(['ssh', '-i', self.ssh_key, '-o', 'BatchMode=yes',
                              '-o', 'ConnectTimeout=10', '-o', 'ForwardAgent=no',
                              self.ssh_user+'@'+self.ssh_host, command], timeout=timeout)
        return CheckResult(label, result.command, result.returncode, result.stdout, result.stderr)

    def actions(self):
        specs = [
            ('commit_mycamino_code', 'Commit and Push myCamino Code', 'python3 -m ops_control.app_code commit', True, 1800, 'COMMIT AND PUSH MYCAMINO CODE'),
            ('build_mycamino_dmg', 'Build Local DMG', 'python3 -m ops_control.app_code build', True, 7200, 'BUILD MYCAMINO DMG'),
            ('release_mycamino_app', 'Release App DMG to Website', 'python3 -m ops_control.app_code release', True, 10800, 'RELEASE MYCAMINO APP'),
            ('release_mycamino_website', 'Release myCamino Website (All Steps)', 'scripts/ops/release_mycamino_website.sh', True, 2400, 'RELEASE MYCAMINO WEBSITE'),
            ('publish_mycamino_site', 'Commit And Push myCamino Website', 'scripts/ops/publish_mycamino_site.sh', True, 900, 'PUBLISH MYCAMINO WEBSITE'),
            ('mycamino_build_status', 'Check myCamino GitHub Build', "gh run list --repo hfalcke/myCamino-GPS-SlideShow --workflow 'myCamino website' --limit 5", False, 60, ''),
            ('deploy_mycamino_site', 'Pull And Deploy myCamino Website', 'scripts/deploy/deploy_mycamino_staging.sh', True, 900, 'DEPLOY MYCAMINO WEBSITE'),
            ('mycamino_staging_status', 'Check myCamino Server Status', 'remote:status', False, 45, ''),
            ('start_mycamino_staging', 'Start myCamino Website On Server', 'remote:start', True, 90, 'START MYCAMINO WEBSITE'),
            ('stop_mycamino_staging', 'Stop myCamino Website On Server', 'remote:stop', True, 90, 'STOP MYCAMINO WEBSITE'),
            ('start_mycamino_local_preview', 'Start Local myCamino Preview', 'scripts/ops/mycamino_local_preview.sh start', True, 300, 'START MYCAMINO PREVIEW'),
            ('stop_mycamino_local_preview', 'Stop Local myCamino Preview', 'scripts/ops/mycamino_local_preview.sh stop', True, 45, 'STOP MYCAMINO PREVIEW'),
            ('mycamino_local_preview_status', 'Check Local myCamino Preview', 'scripts/ops/mycamino_local_preview.sh status', False, 45, ''),
            ('mycamino_new_reports', 'Check New myCamino Reports & Requests', 'python3 scripts/ops/mycamino_github_issues.py --mode new', False, 90, ''),
            ('mycamino_open_reports', 'List All Open myCamino Reports & Requests', 'python3 scripts/ops/mycamino_github_issues.py --mode open', False, 90, ''),
        ]
        descriptions = {
            'commit_mycamino_code': 'Run application tests, commit all non-ignored changes in this repository, and push the current branch and any pending commits to GitHub. Includes staged files, documentation, and website changes. Does not build a DMG or deploy the website.',
            'build_mycamino_dmg': 'Build and verify a local DMG from the current notebook files, including uncommitted edits. Replaces the local build products in dist/. Does not commit, push, change the version, or upload anything.',
            'release_mycamino_app': 'Choose the version and release date, run tests, commit all non-ignored repository changes, build and verify the DMG, push the current branch, and upload it as the active website download. Updates the displayed release metadata; retains the previous DMG. Does not deploy website code.',
            'release_mycamino_website': 'Publish website changes to GitHub, wait for its website image to build, then deploy and check the live website. Desktop app DMGs are packaged separately with release.sh.',
            'publish_mycamino_site': 'Commit and push the website and control-panel files to GitHub. This starts the website image build; deploying it to the server is a separate step. Desktop app files are excluded.',
            'mycamino_build_status': 'Show the five latest website image builds on GitHub and whether they are running, succeeded, or failed. This read-only check does not start a build or update the website.',
            'deploy_mycamino_site': 'Pull the published website image onto the server, apply database updates, restart the website, and check its health. This updates the live website, not the downloadable app.',
            'mycamino_staging_status': 'Check whether the website service is running on the server and whether its public health endpoint responds. No settings are changed.',
            'start_mycamino_staging': 'Start the existing website service on the server so visitors can access it. This does not publish a new release.',
            'stop_mycamino_staging': 'Stop the website service on the server. Visitors will lose access until you start it again; stored data and releases are kept.',
            'start_mycamino_local_preview': 'Start a website preview on this notebook at port 8765, using a separate preview database. The public website is unchanged.',
            'stop_mycamino_local_preview': 'Stop the website preview on this notebook. The public website is unchanged.',
            'mycamino_local_preview_status': 'Check whether the notebook website preview is running and show its address. This does not start or stop it.',
            'mycamino_new_reports': 'List open GitHub bug reports and feature requests still marked needs-triage (awaiting review). This is read-only. Remove that label on GitHub after reviewing an issue to clear it from this list.',
            'mycamino_open_reports': 'List all open GitHub issues, including bug reports and feature requests already reviewed, with the most recently updated first. Show counts and links to each issue. This is read-only; private website contact messages are not included.',
        }
        return {k: ControlAction(k, label, 'mycamino', 'notebook', descriptions[k],
                command, REPO_ROOT, timeout, mutating, confirmation, 'notebook')
                for k, label, command, mutating, timeout, confirmation in specs}

    def audit(self, key, result):
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never log news bodies, credentials, emails, export contents, or commands.
        event = {'time': datetime.now(timezone.utc).isoformat(), 'action': key,
                 'returncode': result.returncode}
        fd = os.open(self.action_log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(event) + '\n')

    def perform(self, key, operation, *, exclusive=False):
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = None
        try:
            if exclusive:
                lock = open(self.state_root/'operation.lock', 'a')
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    result = CheckResult(key, '', 75, '', 'Another modifying operation is running. Try again after it finishes.')
                    self.audit(key, result)
                    return result
            result = operation()
            self.audit(key, result)
            return result
        finally:
            if lock:
                lock.close()

    def run_action(self, key, confirmation='', parameters=None):
        action = self.actions().get(key)
        if not action or (action.mutating and confirmation != action.confirmation):
            return CheckResult('Action', '', 2, '', 'Unknown action or missing confirmation.')
        try:
            argv = action_arguments(key, parameters or {}) if key in CODE_ACTIONS else shlex.split(action.command)
        except ValueError as error:
            return CheckResult(action.label, '', 2, '', str(error))
        def execute():
            if action.command.startswith('remote:'):
                commands = {'status': 'docker compose ps web && curl -fsS --max-time 10 https://mycamino.heinofalcke.de/health/',
                            'start': 'docker compose up -d web', 'stop': 'docker compose stop web'}
                return self.run_vps_bash(action.label, commands[action.command.split(':')[1]],
                                         cwd=Path('/opt/mycamino/site'), timeout=action.timeout)
            env = dict(os.environ)
            if action.mutating:
                env['MYCAMINO_LOCK_HELD'] = '1'
            return run_command(argv, timeout=action.timeout, env=env)
        return self.perform(key, execute, exclusive=action.mutating)

    def start_build(self, confirmation):
        action = self.actions()['build_mycamino_dmg']
        if confirmation != action.confirmation:
            return CheckResult(action.label, '', 2, '', 'Missing build confirmation.')
        try:
            return self.build_jobs.start(action_arguments(action.key, {}), cwd=REPO_ROOT,
                                         timeout=action.timeout)
        except BlockingIOError:
            return CheckResult(action.label, '', 75, '', 'Another modifying operation is running. Wait for it to finish; no new build was started.')
        except (OSError, RuntimeError) as error:
            return CheckResult(action.label, '', 1, '', f'Could not start build: {error}')


class RequestHandler(Views, BaseHTTPRequestHandler):
    server_version = 'myCaminoControl/1'

    @property
    def panel(self):
        return getattr(self, '_panel', self.server.panel if hasattr(self, 'server') else None)

    @panel.setter
    def panel(self, value):
        self._panel = value

    def log_message(self, *_):
        pass  # Base class logs query strings, including the bootstrap token.

    def reply(self, status, content, *, content_type='text/html; charset=utf-8', headers=None,
              referrer_policy='same-origin'):
        body = content.encode('utf-8') if isinstance(content, str) else content
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        # no-referrer turns native form POST origins into "null" in browsers.
        # Preserve our own origin without leaking referrers to external sites.
        self.send_header('Referrer-Policy', referrer_policy)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def authorized(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            value = cookie[COOKIE].value if COOKIE in cookie else ''
        except Exception:
            return False
        return secrets.compare_digest(value, self.panel.token)

    def do_GET(self):
        if not self.host_ok():
            return self.reply(403, 'Invalid host')
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == '/' and 'token' in query:
            if secrets.compare_digest(query['token'][0], self.panel.token):
                # The bootstrap URL contains a secret: never forward its referrer,
                # including on the redirect to the authenticated home page.
                return self.reply(303, '', referrer_policy='no-referrer', headers={'Location': '/', 'Set-Cookie':
                    f'{COOKIE}={self.panel.token}; HttpOnly; SameSite=Strict; Path=/'})
            return self.reply(403, 'Invalid token')
        if not self.authorized():
            return self.reply(401, 'Open the control.sh launcher to sign in.')
        if url.path == '/style.css':
            return self.reply(200, (REPO_ROOT/'ops_control/style.css').read_text(), content_type='text/css')
        if url.path == '/build-progress.js':
            return self.reply(200, (REPO_ROOT/'ops_control/build-progress.js').read_text(), content_type='text/javascript; charset=utf-8')
        if url.path in ('/build-progress', '/build-status'):
            job_id = query.get('id', [''])[0]
            job = self.panel.build_jobs.get(job_id) if re.fullmatch(r'[a-f0-9]{32}', job_id) else None
            if job is None:
                return self.reply(404, 'Build progress is unavailable (the panel may have restarted). Check the current build status before starting another build.')
            if url.path == '/build-status':
                try:
                    cursor = int(query.get('cursor', ['0'])[0])
                    if cursor < 0:
                        raise ValueError
                except ValueError:
                    return self.reply(400, 'Invalid output cursor')
                return self.reply(200, json.dumps(job.snapshot(cursor)), content_type='application/json; charset=utf-8')
            return self.reply(200, self.build_progress_page(job.snapshot()))
        if url.path in ('/', '/actions'):
            return self.reply(200, self.home())
        if url.path == '/action-confirm':
            action = self.panel.actions().get(query.get('action', [''])[0])
            if not action:
                return self.reply(404, 'Unknown action')
            content = f'<h1>{html.escape(action.label)}</h1><p>{html.escape(action.description)}</p>'
            fields = self.code_fields(action.key) if action.key in CODE_ACTIONS else ''
            content += f'<form method="post" action="/action-run"><input type="hidden" name="action" value="{action.key}"><input type="hidden" name="confirmation" value="{action.confirmation}">{fields}<button>{"Confirm and run" if action.mutating else "Run check"}</button></form>'
            if action.key in CODE_ACTIONS:
                status = run_command(['git', 'status', '--short', '--branch'])
                outgoing = run_command(['git', 'log', '--oneline', '@{upstream}..HEAD'])
                content += '<h2>Current checkout</h2><pre>'+html.escape(status.stdout or status.stderr)+'</pre>'
                content += '<h2>Pending commits</h2><pre>'+html.escape(outgoing.stdout or ('No pending commits.' if not outgoing.returncode else 'No upstream configured; check the current branch before pushing.'))+'</pre>'
                content += '<p>Review these files and commits before confirming. Local DMG builds open a live progress page; you can close it and return from the panel home page. Other operations return their result when finished. A failed build or upload does not undo a completed commit or version update.</p>'
            return self.reply(200, self.page(action.label, content))
        if url.path == '/mycamino-downloads':
            return self.reply(200, self.render_mycamino_downloads_page(query.get('page', ['1'])[0]))
        if url.path == '/mycamino-data':
            return self.reply(200, self.render_mycamino_data_page())
        if url.path == '/mycamino-news':
            return self.reply(200, self.render_mycamino_news_page(edit_slug=query.get('edit', [''])[0]))
        if url.path == '/mycamino-export':
            key = query.get('file', [''])[0]
            if key not in EXPORTS:
                return self.reply(404, 'Unknown export')
            refreshed = self.panel.refresh_mycamino_exports()
            result = self.panel.read_mycamino_export(key) if refreshed.returncode == 0 else refreshed
            if result.returncode:
                return self.reply(502, self.page('Export failed', self.render_check_result(result)))
            name, mime = EXPORTS[key]
            return self.reply(200, result.stdout, content_type=mime+'; charset=utf-8',
                              headers={'Content-Disposition': f'attachment; filename="{name}"'})
        if url.path == '/action-log':
            log = self.panel.action_log_path
            lines = log.read_text().splitlines()[-200:] if log.exists() else ['No operations recorded.']
            return self.reply(200, self.page('Action log', '<h1>Action log</h1><pre>'+html.escape('\n'.join(reversed(lines)))+'</pre>'))
        return self.reply(404, 'Not found')

    def do_POST(self):
        if not self.host_ok() or not self.authorized():
            return self.reply(403, 'Authentication required')
        origin = self.headers.get('Origin')
        if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
            return self.reply(403, 'Invalid origin')
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            return self.reply(415, 'Expected form data')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                return self.reply(413, 'Form is too large or empty')
            form = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, 'Invalid form')
        if not secrets.compare_digest(form.get('csrf', [''])[0], self.panel.csrf):
            return self.reply(403, 'Invalid CSRF token')
        path = urlsplit(self.path).path
        if path == '/action-run':
            key = form.get('action', [''])[0]
            action = self.panel.actions().get(key)
            if action is None:
                return self.reply(404, 'Unknown action')
            if key == 'build_mycamino_dmg':
                result = self.panel.start_build(form.get('confirmation', [''])[0])
                if not isinstance(result, CheckResult):
                    return self.reply(303, '', headers={'Location': '/build-progress?id='+result.id})
                return self.reply(200, self.page(action.label, '<h1>Build Local DMG</h1><p>'+html.escape(action.description)+'</p>'+self.render_check_result(result)+self.back_link()))
            if key in CODE_ACTIONS:
                parameters = {name: form.get(name, [''])[0] for name in ('message', 'version', 'release_date')}
                result = self.panel.run_action(key, form.get('confirmation', [''])[0], parameters=parameters)
            else:
                result = self.panel.run_action(key, form.get('confirmation', [''])[0])
            content = self.render_mycamino_issue_result(key, result) if key in ('mycamino_new_reports', 'mycamino_open_reports') else self.render_check_result(result)
            heading = f'<h1>{html.escape(action.label)}</h1><p>{html.escape(action.description)}</p>'
            return self.reply(200, self.page(action.label, heading+content+self.back_link()))
        operations = {'/mycamino-news-save': self.panel.save_mycamino_news,
                      '/mycamino-news-state': self.panel.change_mycamino_news_state,
                      '/mycamino-news-delete': self.panel.delete_mycamino_news}
        if path in operations:
            result = self.panel.perform(path, lambda: operations[path](form), exclusive=True)
            return self.reply(200, self.render_mycamino_news_page(result=result, form=form))
        return self.reply(404, 'Unknown operation')

    def page(self, title, content):
        csrf = html.escape(getattr(self.panel, 'csrf', ''))
        content = re.sub(r'(<form\b[^>]*>)', lambda m: m[1]+f'<input type="hidden" name="csrf" value="{csrf}">', content)
        return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+' · myCamino Control</title><link rel="stylesheet" href="/style.css"></head><body><header><a href="/">myCamino Control</a><nav><a href="/mycamino-downloads">Downloads</a><a href="/mycamino-data">Beta data</a><a href="/mycamino-news">Application news</a><a href="/action-log">Action log</a></nav></header><main>'+content+'</main></body></html>'

    def home(self):
        content = '<h1>myCamino Control</h1><p>Manage application code, DMG releases, the website, downloads, and beta feedback.</p><p><a class="button" href="http://127.0.0.1:8765/" target="_blank" rel="noopener">Open local preview</a> <a class="button" href="https://mycamino.heinofalcke.de/" target="_blank" rel="noopener">Open website</a></p>'
        builds = self.panel.build_jobs.recent()
        if builds:
            content += '<section><h2>Recent local builds</h2><ul>'
            for job in builds:
                content += '<li><a href="/build-progress?id='+job['id']+'">Build Local DMG — '+html.escape(job['status'])+'</a> · '+html.escape(job['started_at'].replace('T', ' '))+' · '+html.escape(job['stage'])+'</li>'
            content += '</ul></section>'
        for group, code in (('Code', True), ('Website and feedback', False)):
            content += f'<section><h2>{group}</h2><div class="operations">'
            for action in self.panel.actions().values():
                if (action.key in CODE_ACTIONS) != code:
                    continue
                kind = 'Confirmation required' if action.mutating else 'Read-only check'
                content += f'<article class="operation-card"><a class="button" href="/action-confirm?action={action.key}">{html.escape(action.label)}</a><p class="operation-kind">{kind}</p><p>{html.escape(action.description)}</p></article>'
            content += '</div></section>'
        return self.page('Operations', content)

    def build_progress_page(self, job):
        status = 'Building…' if job['status'] == 'running' else ('Completed successfully' if job['status'] == 'succeeded' else f"Failed (exit {job['returncode']})")
        content = '<h1>Build Local DMG</h1><section id="build-job" data-job="'+job['id']+'" data-cursor="'+str(job['cursor'])+'">'
        content += '<p id="build-status" role="status">'+status+'</p><p id="build-stage">'+html.escape(job['stage'])+'</p>'
        content += '<progress id="build-progress" aria-labelledby="build-stage" max="'+str(job['total'])+'" value="'+str(job['completed'])+'"></progress>'
        content += '<p id="build-count">'+str(job['completed'])+' of '+str(job['total'])+' stages completed</p><p class="muted">Stage-based progress, not an estimate of time remaining. Packaging steps can take several minutes.</p>'
        content += '<p id="build-elapsed">Elapsed: '+str(job['elapsed'])+' seconds</p><p id="build-connection" role="status"></p>'
        content += '<label class="follow-output"><input id="build-follow" type="checkbox" checked> Follow new output</label>'
        content += '<pre id="build-output" tabindex="0" aria-label="Live build output">'+html.escape(job['output'])+'</pre>'
        content += '<p id="build-truncated" class="muted">'+('Showing the latest output; older lines have been trimmed.' if job['truncated'] else '')+'</p>'
        content += '<p>You can leave this page and return using Recent local builds. Closing the tab does not stop the build. Recent output is kept for this panel session only.</p>'
        content += '<p><a href="/build-progress?id='+job['id']+'">Refresh progress</a></p><noscript><p>Enable JavaScript for live updates, or use Refresh progress. Refreshing does not restart the build.</p></noscript></section>'
        content += '<script src="/build-progress.js" defer></script>'+self.back_link()
        return self.page('Build progress', content)

    def code_fields(self, key):
        fields = ''
        if key == 'release_mycamino_app':
            try:
                metadata = read_metadata(REPO_ROOT)
                version = metadata['APP_VERSION']
                fields += '<p>Current source version: '+html.escape(version)+' · '+html.escape(metadata['APP_RELEASE_DATE'])+'</p>'
            except (OSError, ValueError, SyntaxError):
                version = ''
                fields += '<p class="status-bad">Could not read current version metadata. Repair application_metadata.py before releasing.</p>'
            fields += '<label>Release version<input name="version" required maxlength="11" pattern="[0-9]+[.][0-9]+[.][0-9]+" placeholder="0.9.2" value="'+html.escape(version)+'"></label>'
            fields += '<label>Release date<input type="date" name="release_date" required value="'+date.today().isoformat()+'"></label>'
            fields += '<p>The version and date will be saved in application_metadata.py and used by both the built app and the website download listing. Change the version for a new numbered release.</p>'
        if key in ('commit_mycamino_code', 'release_mycamino_app'):
            fields += '<label>Commit message<input name="message" required maxlength="500" placeholder="Describe the changes in this release"></label>'
        return fields

    def back_link(self):
        return '<p><a href="/">Back to control panel</a></p>'

    def render_check_result(self, result):
        status = 'Completed successfully' if result.returncode == 0 else f'Failed (exit {result.returncode})'
        return f'<p class="{"status-ok" if result.returncode == 0 else "status-bad"}">{status}</p><pre>{html.escape(result.stdout)}</pre><pre>{html.escape(result.stderr)}</pre>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=int(os.environ.get('MYCAMINO_CONTROL_PORT', 8788)))
    args = parser.parse_args()
    os.umask(0o077)
    panel = ControlPanel(os.environ.get('MYCAMINO_CONTROL_TOKEN') or secrets.token_urlsafe(32))
    panel.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), RequestHandler)
    server.panel = panel
    # Only the local launcher reads this private bootstrap file.
    info = panel.state_root/'session.json'
    info.write_text(json.dumps({'pid': os.getpid(), 'port': server.server_port,
                    'url': f'http://127.0.0.1:{server.server_port}/?token={panel.token}'}))
    info.chmod(0o600)
    server.timeout = 1
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f'myCamino Control listening on http://127.0.0.1:{server.server_port}', flush=True)
    try:
        while not stopped:
            server.handle_request()
    finally:
        server.server_close()
        info.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
