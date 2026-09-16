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
        page = self.request()[2]
        self.assertIn('myCamino Control', page)
        self.assertNotIn('test-token', page)
        self.assertNotIn('FediOps', page)

    def test_rejects_csrf_cross_origin_host_and_oversized_form(self):
        with patch.object(self.panel, 'run_action') as run:
            self.assertEqual(self.request('/action-run', 'POST', {'action': 'stop_mycamino_staging'})[0], 403)
            self.assertEqual(self.request('/action-run', 'POST', {'csrf': self.panel.csrf}, {'Origin':'https://evil.test'})[0], 403)
            self.assertEqual(self.request(headers={'Host':'evil.test'})[0], 403)
            self.assertEqual(self.request('/action-run', 'POST', {'csrf':self.panel.csrf, 'padding':'a'*33000})[0], 413)
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
