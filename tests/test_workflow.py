"""Behavioral acceptance tests for the portable task/check/report helper."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/fleet.py'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'repo'
        self.repo.mkdir()
        (self.repo / '.fleet/tasks').mkdir(parents=True)
        (self.repo / '.gitignore').write_text('.fleet/runs/\n__pycache__/\n')
        (self.repo / 'README.md').write_text('Synthetic test repository.\n')
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Workflow tests')
        self.git('config', 'user.email', 'tests@example.invalid')
        self.git('add', '.')
        self.git('commit', '-m', 'Initial fixture')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.project = {'schema_version': 1, 'project_id': 'test', 'context_files': ['README.md'], 'checks': [{'name': 'unit', 'argv': ['{python}', '-c', 'print("checked")'], 'timeout_seconds': 10}], 'secret_names': []}
        self.task = {'schema_version': 1, 'id': 'TEST-001', 'title': 'Test task', 'outcome': 'Verify behavior', 'acceptance': ['Expected behavior is observable.'], 'scope': ['.'], 'dependencies': [], 'base_sha': self.head, 'check_names': ['unit'], 'max_attempts': 2, 'provider': 'manual', 'model': None}
        self.save()

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.repo, text=True, capture_output=True, check=True)
        return result.stdout

    def save(self):
        (self.repo / '.fleet/project.json').write_text(json.dumps(self.project), encoding='utf-8')
        (self.repo / '.fleet/tasks/TEST-001.json').write_text(json.dumps(self.task), encoding='utf-8')

    def run_cli(self, command, *arguments, env=None):
        return subprocess.run([sys.executable, str(CLI), command, '--repo', str(self.repo), *arguments], text=True, capture_output=True, env=env, timeout=20)

    def check(self, env=None):
        return self.run_cli('check', '--task', 'TEST-001', env=env)

    def reports(self):
        return sorted((self.repo / '.fleet/runs/TEST-001').glob('*.json'))

    def latest(self):
        paths = self.reports()
        self.assertTrue(paths, 'A check must produce evidence')
        return json.loads(max(paths, key=lambda path: path.stat().st_mtime_ns).read_text())

    def test_pass_records_exact_revision_and_keeps_owner_acceptance_separate(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        record = self.latest()
        self.assertEqual(record['status'], 'passed')
        self.assertIn(self.head, json.dumps(record))
        self.assertIn('TEST-001', json.dumps(record))
        self.assertIn('checked', json.dumps(record))
        status = self.run_cli('status', '--task', 'TEST-001')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn('verified', status.stdout.lower())
        self.assertNotIn('"accepted": true', status.stdout.lower())

    def test_failed_child_is_not_pass(self):
        self.project['checks'][0]['argv'] = ['{python}', '-c', 'raise SystemExit(7)']
        self.save()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertEqual(self.latest()['status'], 'failed')

    def test_missing_executable_is_unavailable(self):
        self.project['checks'][0]['argv'] = ['missing-executable-for-workflow-test-785523']
        self.save()
        result = self.check()
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn('unavailable', json.dumps(self.latest()).lower())

    def test_empty_checks_never_pass(self):
        self.project['checks'] = []
        self.task['check_names'] = []
        self.save()
        self.assertEqual(self.check().returncode, 2)

    def test_task_id_cannot_escape_repository(self):
        result = self.run_cli('check', '--task', '../outside')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.repo.parent / 'outside.json').exists())

    def test_context_path_cannot_escape_repository(self):
        self.project['context_files'] = ['../outside.md']
        self.save()
        self.assertEqual(self.run_cli('validate').returncode, 2)

    def test_unknown_schema_and_boolean_schema_are_rejected(self):
        for version in (2, True):
            with self.subTest(version=version):
                self.task['schema_version'] = version
                self.save()
                self.assertEqual(self.run_cli('validate').returncode, 2)

    def test_invalid_timeouts_are_rejected(self):
        for timeout in (-1, 0, float('nan'), True, 3601):
            with self.subTest(timeout=timeout):
                self.project['checks'][0]['timeout_seconds'] = timeout
                self.save()
                self.assertEqual(self.run_cli('validate').returncode, 2)

    def test_unknown_required_check_is_rejected(self):
        self.task['check_names'] = ['not-configured']
        self.save()
        self.assertEqual(self.check().returncode, 2)

    def test_secret_redaction_and_subprocess_environment(self):
        secret = 'synthetic-secret-for-test-932817'
        environment = os.environ.copy()
        environment.update(APP_TOKEN=secret, UNRELATED_API_KEY='another-synthetic-secret-912')
        self.project['secret_names'] = ['APP_TOKEN']
        self.project['checks'][0]['argv'] = ['{python}', '-c', 'import os; print(os.getenv("APP_TOKEN")); print(os.getenv("UNRELATED_API_KEY", "absent"))']
        self.save()
        result = self.check(env=environment)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        serialized = json.dumps(self.latest())
        self.assertNotIn(secret, serialized)
        self.assertNotIn('another-synthetic-secret-912', serialized)
        self.assertIn('absent', serialized)

    def test_changed_tracked_and_untracked_content_invalidates_evidence(self):
        self.git('add', '.')
        self.git('commit', '-m', 'Track task configuration')
        self.assertEqual(self.check().returncode, 0)
        (self.repo / 'README.md').write_text('Changed after verification\n')
        self.assertIn('stale', self.run_cli('status', '--task', 'TEST-001').stdout.lower())
        self.git('restore', 'README.md')
        self.assertEqual(self.check().returncode, 0)
        (self.repo / 'new-code.py').write_text('new_behavior = True\n')
        self.assertIn('stale', self.run_cli('status', '--task', 'TEST-001').stdout.lower())

    def test_mutating_check_is_not_passing_evidence(self):
        self.project['checks'][0]['argv'] = ['{python}', '-c', 'from pathlib import Path; Path("README.md").write_text("changed")']
        self.save()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn('stale', json.dumps(self.latest()).lower())

    def test_reports_are_unique_and_old_evidence_is_not_overwritten(self):
        self.assertEqual(self.check().returncode, 0)
        first = self.reports()[0]
        original = first.read_bytes()
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(len(self.reports()), 2)
        self.assertEqual(first.read_bytes(), original)

    def test_timeout_stops_descendants(self):
        child = 'import time; from pathlib import Path; time.sleep(1.2); Path("escaped-child.txt").write_text("bad")'
        parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); print("started",flush=True); time.sleep(10)'
        self.project['checks'][0].update(argv=['{python}', '-c', parent], timeout_seconds=0.3)
        self.save()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        time.sleep(1.3)
        self.assertFalse((self.repo / 'escaped-child.txt').exists())
        self.assertIn('tim', json.dumps(self.latest()).lower())

    def test_dependencies_require_current_evidence(self):
        dependency = dict(self.task, id='DEP-001', dependencies=[])
        (self.repo / '.fleet/tasks/DEP-001.json').write_text(json.dumps(dependency))
        self.task['dependencies'] = ['DEP-001']
        self.save()
        self.assertNotEqual(self.check().returncode, 0)
        passed = self.run_cli('check', '--task', 'DEP-001')
        self.assertEqual(passed.returncode, 0, passed.stderr + passed.stdout)
        self.assertEqual(self.check().returncode, 0)

    def test_init_and_task_creation_refuse_overwrite(self):
        self.assertEqual(self.run_cli('init', '--project-id', 'test').returncode, 2)
        result = self.run_cli('new-task', '--id', 'TEST-001', '--title', 'Replacement', '--outcome', 'Replacement', '--criterion', 'Replacement')
        self.assertEqual(result.returncode, 2)
        saved = json.loads((self.repo / '.fleet/tasks/TEST-001.json').read_text())
        self.assertEqual(saved['title'], 'Test task')

    def test_prompt_references_scope_and_criteria_without_environment(self):
        result = self.run_cli('prompt', '--task', 'TEST-001', '--mode', 'implement')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Verify behavior', result.stdout)
        self.assertIn('Expected behavior is observable.', result.stdout)
        self.assertIn(self.head, result.stdout)
        self.assertIn(str(CLI), result.stdout)

    def test_empty_evidence_cannot_claim_verified(self):
        self.assertEqual(self.check().returncode, 0)
        path = self.reports()[0]
        data = json.loads(path.read_text())
        data['checks'] = []
        path.write_text(json.dumps(data))
        status = json.loads(self.run_cli('status', '--task', 'TEST-001').stdout)
        self.assertNotEqual(status.get('status'), 'verified')

    def test_timeout_stops_child_after_parent_exits(self):
        child = 'import time; from pathlib import Path; time.sleep(1.2); Path("escaped-child.txt").write_text("bad")'
        parent = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{child!r}])'
        self.project['checks'][0].update(argv=['{python}', '-c', parent], timeout_seconds=0.3)
        self.save()
        self.assertEqual(self.check().returncode, 1)
        time.sleep(1.3)
        self.assertFalse((self.repo / 'escaped-child.txt').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX signal cancellation')
    def test_cancellation_records_interrupted_result(self):
        self.project['checks'][0]['argv'] = ['{python}', '-c', 'from pathlib import Path; import time; Path(".fleet/runs/started").touch(); time.sleep(10)']
        self.save()
        proc = subprocess.Popen([sys.executable, str(CLI), 'check', '--repo', str(self.repo), '--task', 'TEST-001'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not (self.repo / '.fleet/runs/started').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('interrupted', json.dumps(self.latest()))

    @unittest.skipIf(os.name == 'nt', 'Symlink creation requires Windows privileges')
    def test_control_directory_symlink_cannot_escape(self):
        outside = self.repo.parent / 'outside'
        outside.mkdir()
        (self.repo / '.fleet/runs').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.check().returncode, 2)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
