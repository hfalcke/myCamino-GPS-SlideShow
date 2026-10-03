"""Disposable visual-test panel with synthetic data and no remote commands."""
import json
import os
from pathlib import Path
import sys
import tempfile
from http.server import ThreadingHTTPServer
from ops_control.server import ControlPanel, RequestHandler
from ops_control.shared import CheckResult
from scripts.tests.test_mycamino_download_page import download_log


class FixturePanel(ControlPanel):
    def start_build(self, confirmation):
        if confirmation != 'BUILD MYCAMINO DMG':
            return CheckResult('Fixture','',2,'','Missing build confirmation.')
        # Real progress transport with synthetic output, never the real build script.
        program = "import time; print('==> Building app icon',flush=True); print('Synthetic build output — no DMG is created.',flush=True); time.sleep(4); print('==> Building standalone GPX editor',flush=True); print('<script>This is harmless log text.</script>',flush=True); time.sleep(8); print('==> Done: synthetic fixture',flush=True)"
        return self.build_jobs.start([sys.executable, '-u', '-c', program], cwd=Path.cwd(), timeout=30)

    def refresh_mycamino_exports(self):
        return CheckResult('refresh','',0,'','')

    def read_mycamino_export(self, key):
        data = download_log(65) if key == 'downloads' else '\ufeffemail,date,message\r\nwalker@example.invalid,2026-09-16,Synthetic example\r\n'
        return CheckResult('export','',0,data,'')

    def list_mycamino_news(self):
        return CheckResult('news','',0,json.dumps([{'slug':'beta-update','title':'The next Camino beta',
            'summary':'Synthetic example for visual testing.','kind':'update','app_version':'0.9.1',
            'link':'https://example.invalid','is_published':False,'published_at':'2026-09-16T12:00:00Z'}]),'')

    def run_vps_bash(self,*args,**kwargs):
        raise AssertionError('Visual fixture cannot access the server')

    def run_action(self,*args,**kwargs):
        return CheckResult('Fixture','',2,'','Actions are disabled in the visual fixture.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='mycamino-panel-fixture-') as state:
        os.environ['MYCAMINO_CONTROL_STATE'] = state
        server = ThreadingHTTPServer(('127.0.0.1',8790),RequestHandler)
        server.panel = FixturePanel('visual-test')
        server.serve_forever()
