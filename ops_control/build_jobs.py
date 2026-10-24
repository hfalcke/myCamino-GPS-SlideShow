"""Background code operations with bounded live output and a shared lock."""
from __future__ import annotations

import codecs
from datetime import datetime
import fcntl
import os
import secrets
import selectors
import signal
import subprocess
import threading
import time

from .shared import CheckResult

# These are script milestones, not estimates of elapsed build time.
BUILD_STAGES = (
    'Building app icon',
    'Checking Python syntax',
    'Verifying guided demo assets',
    'Fetching and verifying corresponding FFmpeg source',
    'Preparing licenses and corresponding source',
    'Building bundled slide-show player',
    'Building standalone GPX editor',
    'Building myCamino GPS Track Show.app',
    'Preparing DMG root',
    'Creating dist/myCamino-GPS-Track-Show.dmg',
    'Verifying dist/myCamino-GPS-Track-Show.dmg',
    'Mounting DMG and checking distributed license resources',
    'Done:',
)
RELEASE_STAGES = (
    'Updating authoritative application version and release date',
    'Checking patch formatting',
    'Running test suite',
    'Staging all repository changes',
    'No source changes to commit; building the existing revision.',
    'Creating commit',
    'Building and verifying DMG from',
    *BUILD_STAGES,
    'Verified built application version',
    'Pushing ',
    'Publishing verified DMG to mycamino.heinofalcke.de',
    'Release complete',
)
# Compatibility name used by existing build tests and callers.
STAGES = BUILD_STAGES
OUTPUT_LIMIT = 250_000


class BuildJob:
    def __init__(self, *, key='build_mycamino_dmg', label='Build Local DMG',
                 stages=BUILD_STAGES, running_message='Building… live updates every second',
                 success_message='Completed successfully — DMG saved in dist/myCamino-GPS-Track-Show.dmg'):
        self.id = secrets.token_hex(16)
        self.key = key
        self.label = label
        self.stages = tuple(stages)
        self.running_message = running_message
        self.success_message = success_message
        self.started = time.monotonic()
        self.started_at = datetime.now().astimezone().isoformat(timespec='seconds')
        self.finished = None
        self.status = 'running'
        self.returncode = None
        self.stage = 'Starting operation'
        self.completed = 0
        self.output = ''
        self.cursor = 0
        self.pending_line = ''
        self.guard = threading.Lock()
        self.thread = None

    def append(self, text):
        with self.guard:
            self.cursor += len(text)
            self.output = (self.output + text)[-OUTPUT_LIMIT:]
            lines = (self.pending_line + text).replace('\r', '\n').split('\n')
            self.pending_line = lines.pop()[-4096:]
            for line in lines:
                if not line.startswith('==> '):
                    continue
                self.stage = line[4:][:512]
                for index, prefix in enumerate(self.stages):
                    if line[4:].startswith(prefix):
                        self.completed = max(self.completed, index)
                        self.stage = 'Finishing cleanup' if prefix == 'Done:' else line[4:][:512]
                        break

    def finish(self, returncode):
        with self.guard:
            self.returncode = returncode
            self.status = 'succeeded' if returncode == 0 else 'failed'
            self.finished = time.monotonic()
            if returncode == 0:
                self.completed = len(self.stages)
                self.stage = 'Completed successfully'

    def snapshot(self, cursor=0):
        with self.guard:
            start = self.cursor - len(self.output)
            reset = cursor < start or cursor > self.cursor
            text = self.output if reset else self.output[cursor-start:]
            return {'id': self.id, 'key': self.key, 'label': self.label,
                    'status': self.status, 'returncode': self.returncode,
                    'started_at': self.started_at,
                    'stage': self.stage, 'completed': self.completed, 'total': len(self.stages),
                    'running_message': self.running_message,
                    'success_message': self.success_message,
                    'elapsed': int((self.finished or time.monotonic()) - self.started),
                    'output': text, 'cursor': self.cursor, 'reset': reset,
                    'truncated': start > 0}


class BuildJobs:
    def __init__(self, state_root, audit):
        self.state_root = state_root
        self.audit = audit
        self.jobs = {}
        self.guard = threading.Lock()

    def get(self, job_id):
        with self.guard:
            return self.jobs.get(job_id)

    def recent(self):
        with self.guard:
            return [job.snapshot() for job in reversed(list(self.jobs.values()))]

    def start(self, argv, *, cwd, timeout, key='build_mycamino_dmg',
              label='Build Local DMG', stages=BUILD_STAGES,
              running_message='Building… live updates every second',
              success_message='Completed successfully — DMG saved in dist/myCamino-GPS-Track-Show.dmg'):
        # Acquire before returning to the browser, so duplicate submissions cannot
        # queue a second build or race an existing deployment/CLI release.
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = open(self.state_root/'operation.lock', 'a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            job = BuildJob(key=key, label=label, stages=stages,
                           running_message=running_message,
                           success_message=success_message)
            job.thread = threading.Thread(target=self._run, args=(job, lock, argv, cwd, timeout),
                                          name='mycamino-build-'+job.id, daemon=False)
            with self.guard:
                while len(self.jobs) >= 10:
                    oldest = next(iter(self.jobs))
                    if self.jobs[oldest].snapshot()['status'] == 'running':
                        raise RuntimeError('A build is already running.')
                    del self.jobs[oldest]
                self.jobs[job.id] = job
                try:
                    job.thread.start()
                except Exception:
                    del self.jobs[job.id]
                    raise
            return job
        except Exception:
            lock.close()
            raise

    @staticmethod
    def _signal(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def _run(self, job, lock, argv, cwd, timeout):
        process = None
        code = 1
        try:
            env = dict(os.environ, MYCAMINO_LOCK_HELD='1', PYTHONUNBUFFERED='1')
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, bufsize=0)
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            deadline = time.monotonic() + timeout
            timed_out = False
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    now = time.monotonic()
                    if now >= deadline and not timed_out:
                        timed_out = True
                        job.append('\nOperation timed out; stopping its processes.\n')
                        self._signal(process, signal.SIGTERM)
                    if timed_out and now >= deadline + 3:
                        self._signal(process, signal.SIGKILL)
                        break
                    for key, _ in selector.select(timeout=0.2):
                        data = os.read(key.fileobj.fileno(), 8192)
                        if data:
                            job.append(decoder.decode(data))
                        else:
                            selector.unregister(key.fileobj)
            job.append(decoder.decode(b'', final=True))
            code = process.wait(timeout=5)
            if timed_out:
                code = 124
        except Exception as error:
            if process is not None:
                self._signal(process, signal.SIGKILL)
                process.wait()
            job.append(f'\nOperation could not complete: {error}\n')
        finally:
            if process is not None and process.stdout is not None:
                process.stdout.close()
            try:
                # The permanent action log contains outcome only, not raw output.
                self.audit(job.key, CheckResult(job.label, '', code, '', ''))
            except OSError:
                job.append('\nWarning: could not write the action log.\n')
            finally:
                lock.close()
                job.finish(code)
