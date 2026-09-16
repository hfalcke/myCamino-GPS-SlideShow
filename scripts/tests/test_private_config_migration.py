import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

AVAILABLE = importlib.util.find_spec('ansible') is not None and importlib.util.find_spec('yaml') is not None
SCRIPT = Path(__file__).resolve().parents[2]/'scripts/deploy/migrate_private_config.py'


@unittest.skipUnless(AVAILABLE, 'Run with .venv-ops/bin/python for vault migration tests')
class MigrationTests(unittest.TestCase):
    def setUp(self):
        import yaml
        self.yaml = yaml
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'infra'
        self.dest = self.root/'private'
        group = self.source/'infra/ansible/group_vars'
        group.mkdir(parents=True)
        inventory = self.source/'infra/ansible/inventory'
        inventory.mkdir()
        (self.source/'secrets').mkdir()
        key = self.root/'ssh-key'
        key.write_text('fixture only')
        self.public = group/'staging.yml'
        self.private = group/'staging.vault.yml'
        self.public.write_text(yaml.safe_dump({
            'mycamino_host':'mycamino.example.test', 'mycamino_app_root':'/opt/mycamino/site',
            'mycamino_data_root':'/var/lib/mycamino', 'mycamino_image':'fixture:main',
            'mycamino_operator_name':'Test Operator', 'mycamino_operator_address':'Test address',
            'mycamino_operator_email':'test@example.invalid', 'docker_network_name':'shared',
            'smtp_host':'smtp.example.test','smtp_port':587,'smtp_use_tls':True,'smtp_use_ssl':False,
            'fedipass_host':'unchanged.example.test'}))
        self.private.write_text(yaml.safe_dump({
            'mycamino_django_secret_key':'fixture-django-secret','mycamino_form_ip_salt':'fixture-salt',
            'mycamino_smtp_username':'fixture-user','mycamino_smtp_password':'fixture-smtp-secret',
            'mycamino_admin_username':'fixture-admin','mycamino_admin_email':'admin@example.invalid',
            'mycamino_admin_password':'fixture-admin-secret','ghcr_username':'fixture-user','ghcr_token':'fixture-ghcr-secret'}))
        (inventory/'staging.yml').write_text(yaml.safe_dump({'all':{'children':{'staging':{'hosts':{
            'fixture-server':{'ansible_host':'example.test','ansible_user':'deploy','ansible_ssh_private_key_file':str(key)}}}}}}))
        (self.source/'secrets/fediops-control.local.env').write_text('FEDIOPS_CONTROL_PORT=8787\nMYCAMINO_REPO_ROOT=/old/path\n')

    def tearDown(self):
        self.temp.cleanup()

    def migrate(self, *args):
        return subprocess.run([sys.executable,str(SCRIPT),'--source-infra',str(self.source),
            '--destination',str(self.dest),*args], capture_output=True,text=True)

    def test_prepare_encrypts_and_finalize_keeps_shared_settings(self):
        original = self.private.read_bytes()
        prepared = self.migrate()
        self.assertEqual(prepared.returncode,0,prepared.stderr)
        self.assertEqual(self.private.read_bytes(),original)
        self.assertTrue((self.dest/'secrets.vault.yml').read_bytes().startswith(b'$ANSIBLE_VAULT;'))
        self.assertTrue((self.dest/'migration-backup.vault').read_bytes().startswith(b'$ANSIBLE_VAULT;'))
        self.assertNotIn('fixture-django-secret', prepared.stdout+prepared.stderr)
        self.assertEqual((self.dest/'vault-password.txt').stat().st_mode & 0o777,0o600)
        finalized = self.migrate('--finalize')
        self.assertEqual(finalized.returncode,0,finalized.stderr)
        data = self.yaml.safe_load(self.public.read_text())
        self.assertIn('mycamino_host', data)
        self.assertNotIn('mycamino_operator_address', data)
        self.assertEqual(data['fedipass_host'],'unchanged.example.test')
        remaining = self.yaml.safe_load(self.private.read_text())
        self.assertEqual(remaining['ghcr_token'],'fixture-ghcr-secret')
        self.assertNotIn('mycamino_django_secret_key',remaining)

    def test_changed_destination_prevents_source_cleanup(self):
        self.assertEqual(self.migrate().returncode,0)
        before = self.private.read_bytes()
        data = self.yaml.safe_load((self.dest/'deployment.yml').read_text())
        data['mycamino_host']='changed.example.test'
        (self.dest/'deployment.yml').write_text(self.yaml.safe_dump(data))
        result = self.migrate('--finalize')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.private.read_bytes(),before)
