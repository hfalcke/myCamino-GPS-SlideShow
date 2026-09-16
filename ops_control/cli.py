"""Fixed remote operations for command-line use."""
import sys
from .server import ControlPanel

def main():
    if len(sys.argv) < 3 or sys.argv[1] != 'news' or sys.argv[2] not in {'list','put','publish','withdraw','delete'}:
        raise SystemExit('Usage: python -m ops_control.cli news {list|put|publish|withdraw|delete} ...')
    panel = ControlPanel('cli')
    result = panel.perform('news:'+sys.argv[2], lambda: panel.run_mycamino_news(sys.argv[2:]), exclusive=sys.argv[2]!='list')
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    raise SystemExit(result.returncode)

if __name__ == '__main__':
    main()
