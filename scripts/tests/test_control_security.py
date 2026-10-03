import fcntl
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from http.server import ThreadingHTTPServer
from ops_control.server import COOKIE, ControlPanel, RequestHandler, run_command
from ops_control.shared import CheckResult
from ops_control.app_code import CODE_ACTIONS


class PanelSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'MYCAMINO_CONTROL_STATE': self.directory.name})
        self.env.start()
        self.panel = ControlPanel('test-token')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), RequestHandler)
        self.server.panel = self.panel
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.env.stop()
        self.directory.cleanup()

    def request(self, path='/', method='GET', data=None, headers=None, authenticated=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        h = {'Cookie': COOKIE+'=test-token'} if authenticated else {}
        if data is not None:
            h['Content-Type'] = 'application/x-www-form-urlencoded'
            h['Origin'] = f'http://127.0.0.1:{self.server.server_port}'
        h.update(headers or {})
        conn.request(method, path, body=urlencode(data) if data is not None else None, headers=h)
        res = conn.getresponse()
        result = res.status, dict(res.getheaders()), res.read().decode()
        conn.close()
        return result

    def test_authentication_bootstrap_and_cookie(self):
        self.assertEqual(self.request(authenticated=False)[0], 401)
        status, headers, _ = self.request('/?token=test-token', authenticated=False)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        _, headers, page = self.request()
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn('myCamino Control', page)
        self.assertNotIn('test-token', page)
        self.assertNotIn('FediOps', page)

    def test_rejects_csrf_cross_origin_host_and_oversized_form(self):
        with patch.object(self.panel, 'run_action') as run:
            self.assertEqual(self.request('/action-run', 'POST', {'action': 'stop_mycamino_staging'})[0], 403)
            self.assertEqual(self.request('/action-run', 'POST', {'csrf': self.panel.csrf}, {'Origin':'https://evil.test'})[0], 403)
            self.assertEqual(self.request('/action-run', 'POST', {'csrf': self.panel.csrf}, {'Origin':'null'})[0], 403)
            self.assertEqual(self.request('/action-run', 'POST', {'csrf': self.panel.csrf}, {'Origin':'http://127.0.0.1:1'})[0], 403)
            self.assertEqual(self.request(headers={'Host':'evil.test'})[0], 403)
            self.assertEqual(self.request('/action-run', 'POST', {'csrf':self.panel.csrf, 'padding':'a'*33000})[0], 413)
            run.assert_not_called()

    def test_all_operations_accept_same_origin_forms_and_explain_the_action(self):
        home = self.request()[2]
        for action in self.panel.actions().values():
            with self.subTest(action=action.key):
                self.assertIn(action.description, home)
                _, headers, page = self.request('/action-confirm?action='+action.key)
                # Native form submissions must retain a non-null same-origin Origin.
                self.assertEqual(headers['Referrer-Policy'], 'same-origin')
                self.assertIn(action.description, page)
                self.assertIn('name="csrf"', page)
                with patch.object(self.panel, 'run_action', return_value=CheckResult('test', '', 1, '', 'Synthetic command failure')) as run:
                    status, _, content = self.request('/action-run', 'POST', {
                        'csrf': self.panel.csrf, 'action': action.key,
                        'confirmation': action.confirmation})
                    self.assertEqual(status, 200)
                    self.assertIn(action.description, content)
                    self.assertIn('Synthetic command failure', content)
                    if action.key in CODE_ACTIONS:
                        run.assert_called_once_with(action.key, action.confirmation, parameters={
                            'message': '', 'version': '', 'release_date': ''})
                    else:
                        run.assert_called_once_with(action.key, action.confirmation)

    def test_code_release_form_and_validated_dispatch(self):
        page = self.request('/action-confirm?action=release_mycamino_app')[2]
        for field in ('csrf', 'version', 'release_date', 'message'):
            self.assertIn(f'name="{field}"', page)
        self.assertIn('<h2>Code</h2>', self.request()[2])
        data = {'action': 'release_mycamino_app', 'csrf': self.panel.csrf,
                'confirmation': 'RELEASE MYCAMINO APP', 'message': 'Release "safe" <text>',
                'version': '0.9.2', 'release_date': '2026-10-03'}
        with patch('ops_control.server.run_command', return_value=CheckResult('test','',0,'done','')) as command:
            response = self.request('/action-run', 'POST', data)
            self.assertEqual(response[0], 200)
            argv = command.call_args.args[0]
            self.assertEqual(argv[1:4], ['-m', 'ops_control.app_code', 'release'])
            self.assertIn(data['message'], argv)
            self.assertIn('0.9.2', argv)
            self.assertEqual(command.call_args.kwargs['env']['MYCAMINO_LOCK_HELD'], '1')
            command.reset_mock()
            data['version'] = '0.9.2; touch /tmp/unsafe'
            response = self.request('/action-run', 'POST', data)
            self.assertIn('Version must contain', response[2])
            command.assert_not_called()
            data['version'] = '0.9.2'
            data['confirmation'] = ''
            self.assertIn('missing confirmation', self.request('/action-run', 'POST', data)[2])
            command.assert_not_called()

    def test_unknown_action_is_never_executed(self):
        with patch.object(self.panel, 'run_action') as run:
            self.assertEqual(self.request('/action-run', 'POST', {
                'csrf': self.panel.csrf, 'action': 'arbitrary-command'})[0], 404)
            run.assert_not_called()

    def test_confirmation_and_safe_results(self):
        with patch('ops_control.server.run_command') as command:
            result = self.panel.run_action('deploy_mycamino_site', '')
            self.assertNotEqual(result.returncode, 0)
            command.assert_not_called()
        with patch.object(self.panel, 'run_action', return_value=CheckResult('test', '', 1, '<script>bad()</script>', 'SSH failed')):
            status, _, content = self.request('/action-run', 'POST', {'csrf':self.panel.csrf, 'action':'mycamino_staging_status'})
            self.assertEqual(status, 200)
            self.assertIn('&lt;script&gt;', content)
            self.assertIn('SSH failed', content)
        self.assertEqual(self.request('/mycamino-export?file=../../secret')[0], 404)

    def test_news_form_has_csrf_and_bad_input_never_reaches_server(self):
        with patch.object(self.panel, 'list_mycamino_news', return_value=CheckResult('list','',0,'[]','')):
            page = self.request('/mycamino-news')[2]
            self.assertIn('name="csrf"', page)
            with patch.object(self.panel, 'run_mycamino_news') as remote:
                _, _, content = self.request('/mycamino-news-save','POST',{
                    'csrf':self.panel.csrf,'slug':'bad slug','title':'Title','summary':'Text'})
                self.assertIn('Slug must contain', content)
                remote.assert_not_called()

    def test_operation_lock_and_log_do_not_record_private_data(self):
        with open(Path(self.directory.name)/'operation.lock','a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            called=[]
            result=self.panel.perform('deploy', lambda: called.append(True), exclusive=True)
            self.assertEqual(result.returncode, 75)
            self.assertEqual(called, [])
            with patch('ops_control.server.run_command') as command:
                result = self.panel.run_action('build_mycamino_dmg', 'BUILD MYCAMINO DMG')
                self.assertEqual(result.returncode, 75)
                command.assert_not_called()
        self.panel.perform('news', lambda: CheckResult('private','secret command',0,'private@example.org','secret'))
        log=self.panel.action_log_path.read_text()
        self.assertNotIn('private@example.org',log)
        self.assertNotIn('secret',log)
        self.assertEqual(json.loads(log.splitlines()[-1])['action'], 'news')

    def test_command_failure_and_timeout(self):
        result=run_command(['/nonexistent/mycamino-command'])
        self.assertNotEqual(result.returncode,0)
        result=run_command(['python3','-c','import time; time.sleep(10)'], timeout=.05)
        self.assertEqual(result.returncode,124)

if __name__ == '__main__':
    unittest.main()
