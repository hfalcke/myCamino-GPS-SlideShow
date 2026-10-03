"""Validated application-code operations used by the notebook panel and release CLI."""
from __future__ import annotations

import argparse
import ast
from datetime import date
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CODE_ACTIONS = {
    'commit_mycamino_code': 'commit',
    'build_mycamino_dmg': 'build',
    'release_mycamino_app': 'release',
}


def validate_version(version, released):
    if not re.fullmatch(r'(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})', version):
        raise ValueError('Version must contain three numbers from 0 to 999, for example 0.9.2.')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', released):
        raise ValueError('Release date must use YYYY-MM-DD.')
    try:
        day = date.fromisoformat(released)
    except ValueError:
        raise ValueError('Release date must be a valid calendar date.') from None
    return f'Version {version} · {day.day} {day:%B %Y}'


def read_metadata(root=ROOT):
    # Read literals on every request, without executing code or retaining stale imports.
    tree = ast.parse((root/'application_metadata.py').read_text())
    fields = {'APP_VERSION', 'APP_BUNDLE_VERSION', 'APP_RELEASE_DATE'}
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in fields:
                    values[target.id] = ast.literal_eval(node.value)
    if values.keys() != fields or not all(isinstance(v, str) for v in values.values()):
        raise ValueError('Application version metadata is incomplete.')
    validate_version(values['APP_VERSION'], values['APP_RELEASE_DATE'])
    return values


def update_metadata(version, released, root=ROOT):
    label = validate_version(version, released)
    read_metadata(root)
    path = root/'application_metadata.py'
    source = path.read_text()
    for field, value in {'APP_VERSION': version, 'APP_BUNDLE_VERSION': version,
                         'APP_RELEASE_DATE': released}.items():
        source, count = re.subn(r'^'+field+r'\s*=.*$', f'{field} = "{value}"', source, flags=re.M)
        if count != 1:
            raise ValueError(f'Expected exactly one {field} assignment; metadata unchanged.')
    compile(source, str(path), 'exec')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.version-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(source)
    try:
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return label


def action_arguments(key, parameters):
    """Build argv, never a shell command, from narrowly validated form fields."""
    operation = CODE_ACTIONS[key]
    argv = [sys.executable, '-m', 'ops_control.app_code', operation]
    if operation in {'commit', 'release'}:
        message = parameters.get('message', '').strip()
        if not message or len(message) > 500 or any(ord(c) < 32 for c in message):
            raise ValueError('Enter a one-line commit message (1–500 characters).')
        argv += ['--message', message]
    if operation == 'release':
        version = parameters.get('version', '').strip()
        released = parameters.get('release_date', '').strip()
        validate_version(version, released)
        argv += ['--version', version, '--date', released]
    return argv


def run(argv, root=ROOT, **kwargs):
    return subprocess.run(argv, cwd=root, check=True, **kwargs)


def commit_and_push(message, root=ROOT):
    branch = run(['git', 'branch', '--show-current'], root, capture_output=True, text=True).stdout.strip()
    if not branch:
        raise ValueError('Cannot commit and push from a detached HEAD.')
    for state in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
        path = run(['git', 'rev-parse', '--git-path', state], root, capture_output=True, text=True).stdout.strip()
        if (root/path).exists():
            raise ValueError('Finish the active Git merge/rebase operation first.')
    remote = os.environ.get('REMOTE', 'origin')
    run(['git', 'remote', 'get-url', remote], root, stdout=subprocess.DEVNULL)
    print('==> Checking source changes and running application tests', flush=True)
    run(['git', '--no-pager', 'diff', '--check'], root)
    run(['git', '--no-pager', 'diff', '--cached', '--check'], root)
    run([os.environ.get('PYTHON', str(root/'.venv/bin/python')), '-m', 'unittest', 'discover', '-s', 'tests'], root)
    print('==> Staging all non-ignored repository changes', flush=True)
    run(['git', 'add', '--all'], root)
    run(['git', '--no-pager', 'diff', '--cached', '--check'], root)
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=root).returncode
    if changed == 1:
        run(['git', 'commit', '-m', message], root)
    elif changed:
        raise ValueError('Could not inspect staged changes.')
    else:
        print('==> No new changes to commit; pushing existing commits.', flush=True)
    print(f'==> Pushing {branch} to {remote}', flush=True)
    run(['git', 'push', '--set-upstream', remote, branch], root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for operation in ('commit', 'build', 'release', 'version'):
        command = sub.add_parser(operation)
        if operation in {'commit', 'release'}:
            command.add_argument('--message', required=True)
        if operation in {'release', 'version'}:
            command.add_argument('--version', required=True)
            command.add_argument('--date', required=True)
        if operation == 'version':
            command.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        if args.operation == 'version':
            if args.check:
                print(validate_version(args.version, args.date))
                return
            update = lambda: print(update_metadata(args.version, args.date))
        else:
            key = next(k for k, v in CODE_ACTIONS.items() if v == args.operation)
            action_arguments(key, {'message': getattr(args, 'message', ''),
                                  'version': getattr(args, 'version', ''),
                                  'release_date': getattr(args, 'date', '')})
            if args.operation == 'commit':
                update = lambda: commit_and_push(args.message)
            elif args.operation == 'build':
                update = lambda: run(['bash', './build_dmg.sh'])
            else:
                update = lambda: run(['bash', './release.sh', '--yes', '--message', args.message,
                                      '--version', args.version, '--date', args.date])
        if os.environ.get('MYCAMINO_LOCK_HELD') == '1':
            update()
        else:
            # Direct CLI invocation uses the same lock as panel operations.
            from .server import ControlPanel, CheckResult
            def locked():
                env = dict(os.environ, MYCAMINO_LOCK_HELD='1')
                result = subprocess.run([sys.executable, '-m', 'ops_control.app_code', *sys.argv[1:]], cwd=ROOT, env=env)
                return CheckResult('Application code', '', result.returncode, '', '')
            result = ControlPanel('cli').perform('application-code', locked, exclusive=True)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            raise SystemExit(result.returncode)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Operation stopped: {error}. Any completed local commit is preserved.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
