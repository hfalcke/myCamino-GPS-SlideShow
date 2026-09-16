"""Disposable visual-test panel with synthetic data and no remote commands."""
import json
from http.server import ThreadingHTTPServer
from ops_control.server import ControlPanel, RequestHandler
from ops_control.shared import CheckResult
from scripts.tests.test_mycamino_download_page import download_log


class FixturePanel(ControlPanel):
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
    server = ThreadingHTTPServer(('127.0.0.1',8790),RequestHandler)
    server.panel = FixturePanel('visual-test')
    server.serve_forever()
