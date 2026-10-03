import fcntl
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from ops_control.build_jobs import BuildJob, BuildJobs, OUTPUT_LIMIT, STAGES


class BuildJobsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.audits = []
        self.jobs = BuildJobs(self.root, lambda key, result: self.audits.append((key, result.returncode)))

    def tearDown(self):
        for job in self.jobs.jobs.values():
            if job.thread:
                job.thread.join(10)
                self.assertFalse(job.thread.is_alive())
        self.directory.cleanup()

    def start(self, code, timeout=5):
        return self.jobs.start([sys.executable, '-c', code], cwd=self.root, timeout=timeout)

    def wait_output(self, job, text):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            data = job.snapshot()
            if text in data['output']:
                return data
            time.sleep(.01)
        self.fail('Expected live output not received: '+repr(job.snapshot()))

    def test_live_output_stages_completion_and_shared_lock(self):
        job = self.start("import os,sys,time; assert os.environ['MYCAMINO_LOCK_HELD']=='1'; print('==> Building app icon', flush=True); sys.stderr.write('stderr line\\n'); print('partial',end='',flush=True); time.sleep(.7); print('\\n==> Checking Python syntax',flush=True)")
        first = self.wait_output(job, 'partial')
        self.assertEqual(first['status'], 'running')
        self.assertEqual(first['stage'], 'Building app icon')
        self.assertIn('stderr line', first['output'])
        with self.assertRaises(BlockingIOError):
            self.start("print('must not run')")
        with open(self.root/'operation.lock', 'a') as lock:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job.thread.join(5)
        result = job.snapshot(first['cursor'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['completed'], len(STAGES))
        self.assertNotIn('partial', result['output'])
        self.assertIn('Checking Python syntax', result['output'])
        self.assertEqual(self.audits, [('build_mycamino_dmg', 0)])
        with open(self.root/'operation.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_failure_never_shows_complete_progress(self):
        job = self.start("import sys; print('==> Building standalone GPX editor'); print('build error',file=sys.stderr); sys.exit(7)")
        job.thread.join(5)
        data = job.snapshot()
        self.assertEqual(data['returncode'], 7)
        self.assertEqual(data['status'], 'failed')
        self.assertLess(data['completed'], data['total'])
        self.assertIn('build error', data['output'])

    def test_timeout_and_failed_spawn_release_lock(self):
        job = self.start("import time; time.sleep(30)", timeout=.1)
        job.thread.join(6)
        self.assertEqual(job.snapshot()['returncode'], 124)
        self.assertIn('timed out', job.snapshot()['output'])
        missing = self.jobs.start(['/does/not/exist/mycamino'], cwd=self.root, timeout=1)
        missing.thread.join(2)
        self.assertEqual(missing.snapshot()['status'], 'failed')
        with open(self.root/'operation.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_external_operation_lock_prevents_start(self):
        with open(self.root/'operation.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaises(BlockingIOError):
                self.start("print('must not run')")
        self.assertEqual(self.jobs.recent(), [])

    def test_bounded_output_partial_lines_and_cursor_recovery(self):
        job = BuildJob()
        job.append('==> Building standalone GPX')
        job.append(' editor\n')
        self.assertEqual(job.snapshot()['stage'], 'Building standalone GPX editor')
        job.append('x'*(OUTPUT_LIMIT+100))
        data = job.snapshot(0)
        self.assertTrue(data['reset'])
        self.assertTrue(data['truncated'])
        self.assertEqual(len(data['output']), OUTPUT_LIMIT)
        self.assertEqual(job.snapshot(data['cursor'])['output'], '')
        self.assertTrue(job.snapshot(data['cursor']+1)['reset'])

    def test_stage_definitions_match_build_script(self):
        source = (Path(__file__).resolve().parents[2]/'build_dmg.sh').read_text()
        for stage in STAGES:
            if stage.startswith(('Building myCamino', 'Creating dist/', 'Verifying dist/', 'Done:')):
                continue  # These messages interpolate shell variables.
            self.assertIn('==> '+stage, source)


if __name__ == '__main__':
    unittest.main()
