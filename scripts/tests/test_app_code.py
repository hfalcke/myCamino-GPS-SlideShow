import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ops_control.app_code import action_arguments, commit_and_push, read_metadata, update_metadata, validate_version

ROOT = Path(__file__).resolve().parents[2]


class AppCodeTests(unittest.TestCase):
    def test_version_validation_and_literal_metadata_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(ROOT/'application_metadata.py', root)
            self.assertEqual(update_metadata('0.9.2', '2026-10-03', root), 'Version 0.9.2 · 3 October 2026')
            self.assertEqual(read_metadata(root), {'APP_VERSION': '0.9.2', 'APP_BUNDLE_VERSION': '0.9.2', 'APP_RELEASE_DATE': '2026-10-03'})
            source = (root/'application_metadata.py').read_text()
            self.assertIn('def full_version_label()', source)
            for version, day in [('1.0;bad','2026-10-03'), ('01.2.3','2026-10-03'), ('1.2.3','2026-02-30'), ('1.2.3','20261003')]:
                with self.assertRaises(ValueError):
                    update_metadata(version, day, root)
                self.assertEqual((root/'application_metadata.py').read_text(), source)

    def test_arguments_are_not_shell_interpreted(self):
        message = 'Fix $(touch /tmp/not-executed) <script> "quotes"'
        argv = action_arguments('commit_mycamino_code', {'message': message})
        self.assertEqual(argv[-1], message)
        self.assertEqual(argv[-2], '--message')
        for message in ['', ' ', 'a\nb', 'x'*501, '\x00']:
            with self.assertRaises(ValueError):
                action_arguments('commit_mycamino_code', {'message': message})

    def git(self, root, *args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.PIPE).strip()

    def fixture(self, directory):
        root = Path(directory)/'source'
        remote = Path(directory)/'remote.git'
        root.mkdir()
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        self.git(root, 'init', '-b', 'main')
        self.git(root, 'config', 'user.name', 'Local Fixture')
        self.git(root, 'config', 'user.email', 'fixture@example.invalid')
        self.git(root, 'remote', 'add', 'origin', str(remote))
        (root/'tests').mkdir()
        (root/'tests/test_fixture.py').write_text('import unittest\nclass Test(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        (root/'.gitignore').write_text('dist/\n__pycache__/\nprivate.env\n')
        (root/'private.env').write_text('excluded fixture\n')
        self.git(root, 'add', '.gitignore', 'tests')
        self.git(root, 'commit', '-m', 'Initial fixture')
        self.git(root, 'push', '-u', 'origin', 'main')
        return root, remote

    def test_commit_push_includes_staged_unstaged_and_new_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root, remote = self.fixture(directory)
            (root/'staged.txt').write_text('staged')
            self.git(root, 'add', 'staged.txt')
            (root/'new.txt').write_text('new')
            with patch.dict(os.environ, {'PYTHON':sys.executable, 'REMOTE':'origin'}):
                commit_and_push('Publish fixture', root)
                head = self.git(root, 'rev-parse', 'HEAD')
                self.assertEqual(self.git(remote, 'rev-parse', 'main'), head)
                self.assertEqual(self.git(root, 'status', '--porcelain'), '')
                self.assertNotIn('private.env', self.git(root, 'ls-files'))
                commit_and_push('No changes', root)
                self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), head)

    def test_failed_tests_leave_index_and_remote_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root, remote = self.fixture(directory)
            before = self.git(remote, 'rev-parse', 'main')
            (root/'tests/test_fixture.py').write_text('import unittest\nclass Test(unittest.TestCase):\n def test_bad(self): self.fail("fixture failure")\n')
            with patch.dict(os.environ, {'PYTHON':sys.executable, 'REMOTE':'origin'}):
                with self.assertRaises(subprocess.CalledProcessError):
                    commit_and_push('Must not push', root)
            self.assertEqual(self.git(root, 'diff', '--cached', '--name-only'), '')
            self.assertEqual(self.git(remote, 'rev-parse', 'main'), before)

    def release_fixture(self, directory, *, build_fails=False):
        root, remote = self.fixture(directory)
        shutil.copy(ROOT/'release.sh', root)
        shutil.copy(ROOT/'application_metadata.py', root)
        shutil.copy(ROOT/'tests/test_application_metadata.py', root/'tests')
        (root/'ops_control').mkdir()
        shutil.copy(ROOT/'ops_control/app_code.py', root/'ops_control/app_code.py')
        (root/'scripts/ops').mkdir(parents=True)
        (root/'scripts/ops/mycamino_env.sh').write_text('# Isolated fixture environment\n')
        (root/'build_dmg.sh').write_text('''#!/bin/bash
set -eu
test -z "$(git status --porcelain)"
python3 -c 'from application_metadata import APP_VERSION; assert APP_VERSION == "0.9.2"'
mkdir -p 'dist/myCamino GPS Track Show.app/Contents'
printf 'fixture' > dist/myCamino-GPS-Track-Show.dmg
printf 'fixture' > 'dist/myCamino GPS Track Show.app/Contents/Info.plist'
''' + ('exit 17\n' if build_fails else ''))
        publisher = root/'scripts/publish_website_release.sh'
        publisher.write_text('''#!/bin/bash
set -eu
test "$2" = 'Version 0.9.2 · 3 October 2026'
test "$3" = 2026-10-03
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
printf '%s\\n' "$2" "$3" > dist/published.txt
''')
        for script in (root/'build_dmg.sh', publisher):
            script.chmod(0o755)
        binary = Path(directory)/'bin'
        binary.mkdir()
        (binary/'plutil').write_text('#!/bin/sh\necho 0.9.2\n')
        (binary/'plutil').chmod(0o755)
        env = dict(os.environ, PYTHON=sys.executable, MYCAMINO_LOCK_HELD='1', REMOTE='origin',
                   PATH=str(binary)+os.pathsep+os.environ['PATH'])
        return root, remote, env

    def release(self, root, env, *extra):
        return subprocess.run(['bash','release.sh','--yes','--message','Fixture release',
                               '--version','0.9.2','--date','2026-10-03', *extra],cwd=root,env=env,capture_output=True,text=True)

    def test_release_sets_metadata_before_commit_build_and_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root, remote, env = self.release_fixture(directory)
            result = self.release(root, env)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual((root/'dist/published.txt').read_text(), 'Version 0.9.2 · 3 October 2026\n2026-10-03\n')
            self.assertEqual(self.git(root, 'status', '--porcelain'), '')
            self.assertEqual(self.git(root,'rev-parse','HEAD'), self.git(remote,'rev-parse','main'))

    def test_build_failure_does_not_push_or_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root, remote, env = self.release_fixture(directory, build_fails=True)
            previous = self.git(remote, 'rev-parse', 'main')
            result = self.release(root, env)
            self.assertEqual(result.returncode, 17)
            self.assertEqual(self.git(remote, 'rev-parse', 'main'), previous)
            self.assertFalse((root/'dist/published.txt').exists())
            self.assertNotEqual(self.git(root, 'rev-parse', 'HEAD'), previous)

    def test_invalid_version_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root, remote, env = self.release_fixture(directory)
            before = (root/'application_metadata.py').read_text()
            result = self.release(root, env, '--version', 'invalid')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root/'application_metadata.py').read_text(), before)
            self.assertFalse((root/'dist').exists())


if __name__ == '__main__':
    unittest.main()
