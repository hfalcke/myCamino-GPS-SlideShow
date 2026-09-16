"""Serialize CLI releases with panel operations; nested release steps inherit the lock."""
import os
import subprocess
import sys
from .server import ControlPanel, CheckResult

def main():
    panel = ControlPanel('cli')
    def run():
        env = dict(os.environ, MYCAMINO_LOCK_HELD='1')
        status = subprocess.call(sys.argv[1:], env=env)
        return CheckResult('CLI release', '', status, '', '')
    result = panel.perform('cli-release', run, exclusive=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode)

if __name__ == '__main__':
    main()
