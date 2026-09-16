import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2]/'scripts/ops/publish_mycamino_site.sh'


class PublishScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base/'repo'
        self.repo.mkdir()
        self.remote = self.base/'remote.git'
        subprocess.run(['git','init','--bare',str(self.remote)], check=True, capture_output=True)
        self.git('init','-b','main')
        self.git('config','user.name','Test')
        self.git('config','user.email','test@example.invalid')
        self.git('remote','add','origin',str(self.remote))
        for name in ['.gitignore','website/manage.py','.github/workflows/mycamino-site.yml',
                     'scripts/publish_website_release.sh','ops_control/file.py','control.sh',
                     'scripts/ops/file.sh','scripts/deploy/file.sh','scripts/tests/file.py','infra/file.yml','docs/CONTROL_PANEL.md','app.py']:
            p = self.repo/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('initial\n')
        self.git('add','.')
        self.git('commit','-m','Initial')
        self.git('push','-u','origin','main')
        fake = self.repo/'.venv-web/bin/python'
        fake.parent.mkdir(parents=True)
        fake.write_text('#!/bin/sh\nexit 0\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, MYCAMINO_CONFIG_DIR=str(self.base/'config'),
                        MYCAMINO_CONTROL_STATE=str(self.base/'state'))

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git','-C',str(self.repo),*args], stderr=subprocess.STDOUT, text=True).strip()

    def publish(self):
        return subprocess.run(['bash',str(SCRIPT),str(self.repo)], env=self.env, capture_output=True,text=True)

    def test_website_commit_leaves_application_edits_untouched(self):
        (self.repo/'website/manage.py').write_text('website edit\n')
        (self.repo/'app.py').write_text('application edit\n')
        result = self.publish()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.git('diff','--name-only'), 'app.py')
        self.assertEqual(self.git('show','--format=','--name-only','HEAD'), 'website/manage.py')

    def test_preexisting_staged_change_is_preserved_and_refused(self):
        (self.repo/'app.py').write_text('application edit\n')
        self.git('add','app.py')
        result = self.publish()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('already-staged',result.stderr)
        self.assertEqual(self.git('diff','--cached','--name-only'), 'app.py')

    def test_existing_application_commit_is_not_pushed(self):
        remote_before = self.git('rev-parse','origin/main')
        (self.repo/'app.py').write_text('application edit\n')
        self.git('add','app.py')
        self.git('commit','-m','Unpublished application work')
        result = self.publish()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Existing unpushed commits',result.stderr)
        self.assertEqual(self.git('rev-parse','origin/main'),remote_before)
