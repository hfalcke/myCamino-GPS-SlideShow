"""Start, stop or reopen the independent notebook panel."""
import json
import fcntl
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get('MYCAMINO_CONTROL_STATE', str(Path.home()/'.local/state/mycamino-control')))

def session():
    try:
        data = json.loads((STATE/'session.json').read_text())
        pid = int(data['pid'])
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
        if '-m ops_control.server' in command:
            return data
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        pass
    return None

def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'start'
    if action not in {'start','stop','restart','status'}:
        raise SystemExit('Usage: ./control.sh [start|stop|restart|status]')
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = session()
    if action in {'stop','restart'} and data:
        with (STATE/'operation.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('An operation is running. Wait for it to finish before stopping the panel.')
            os.kill(data['pid'], signal.SIGTERM)
        for _ in range(50):
            if not session():
                break
            time.sleep(.1)
        else:
            raise SystemExit('Panel is still stopping. Wait and retry.')
        data = None
    if action == 'stop':
        print('myCamino Control stopped.')
        return
    if action == 'status':
        print(f"myCamino Control is running at http://127.0.0.1:{data['port']}" if data else 'myCamino Control is stopped.')
        raise SystemExit(0 if data else 1)
    if not data:
        with (STATE/'server.log').open('a') as log:
            process = subprocess.Popen([sys.executable, '-m', 'ops_control.server'], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            data = session()
            if data:
                break
            if process.poll() is not None:
                raise SystemExit(f'Panel could not start. See {STATE}/server.log (port may already be in use).')
            time.sleep(.1)
        else:
            raise SystemExit(f'Panel startup timed out. See {STATE}/server.log.')
    print(f"myCamino Control: http://127.0.0.1:{data['port']}")
    if os.environ.get('MYCAMINO_CONTROL_NO_OPEN') != '1':
        webbrowser.open(data['url'])

if __name__ == '__main__':
    main()
