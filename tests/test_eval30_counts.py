"""Count/genre extensions on top of the existing experiment workflow. No models."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import eval30
import experiment as exp


class CountTests(unittest.TestCase):
    def test_arbitrary_count_matches_native_round_robin(self):
        ids = ['CAMPUS-01', 'SCI-FI-01', 'MYSTERY-01']
        for count in range(1, 36):
            actual = exp.attempt_schedule(ids, 3, count)
            expected = Counter(ids[i % len(ids)] for i in range(count))
            self.assertEqual(actual['attempts_by_case'], {i: expected[i] for i in ids})
            self.assertEqual(actual['attempts_per_system'], count)
            self.assertIsNone(actual['repeat'])
            self.assertEqual(actual['count_mode'], 'total_per_system')

    def test_repeat_semantics_unchanged(self):
        actual = exp.attempt_schedule(['A', 'B'], 3)
        self.assertEqual(actual['attempts_per_system'], 6)
        self.assertEqual(actual['attempts_by_case'], {'A': 3, 'B': 3})
        self.assertEqual(actual['repeat'], 3)

    def test_invalid_quantities_and_selections(self):
        for count in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                exp.attempt_schedule(['A'], 1, count)
        for repeat in (None, 0, -1, False, 1.2):
            with self.assertRaises(ValueError):
                exp.attempt_schedule(['A'], repeat)
        for ids in ([], ['A', 'A']):
            with self.assertRaises(ValueError):
                exp.attempt_schedule(ids, 1)

    def test_genre_filter_preserves_catalog_order(self):
        data, _, _ = eval30.catalog_sources()
        ids = exp.requested_genres(data['cases'], ['SCI-FI', 'CAMPUS'])
        self.assertEqual(ids, [f'CAMPUS-0{i}' for i in range(1, 6)] + [f'SCI-FI-0{i}' for i in range(1, 6)])
        for genres in ([], ['wrong'], ['SCI-FI', 'SCI-FI']):
            with self.assertRaises(ValueError):
                exp.requested_genres(data['cases'], genres)

    def test_parser_rejects_conflicting_or_invalid_options(self):
        cases = [ ['--count', '7', '--repeat', '3'], ['--count', '0'],
                  ['--case-ids', 'CAMPUS-01', '--genres', 'SCI-FI'] ]
        for argv in cases:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                exp.main(argv)

    def test_frozen_count_and_genres_cannot_change_on_resume(self):
        for flag in ('--count=7', '--genres=SCI-FI'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                exp.main(['--resume', '--out', '/not-read', flag])

    def test_root_alias_and_existing_shell_expose_count(self):
        for script in ('experiment.sh', 'tools/experiment.sh'):
            result = subprocess.run(['bash', str(ROOT / script), 'run', '--help'],
                                    cwd='/tmp', capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--count', result.stdout)
            self.assertIn('--genres', result.stdout)


class CountIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.config = self.work / 'config.json'
        config = {'schema_version': 'batch.1', 'evidence_kind': 'live', 'allow_pilot': True,
                  'providers': {role: {'model': 'test-fixture', 'base_url': 'https://example.invalid/v1',
                                      'api_key_env': 'TEST_' + role.upper(), 'parameters': {}}
                                for role in ('text', 'image', 'vision')},
                  'systems': {s: {'repo_path': 'systems/' + s} for s in exp.SYSTEMS},
                  'bundles': ['unused'], 'choice_indices': [0, 1],
                  'reading_delay_seconds': 0, 'render_mode': 'offscreen_native'}
        self.config.write_text(json.dumps(config), encoding='utf-8')
        self.args = argparse.Namespace(preset='pilot', case_ids=['CAMPUS-01', 'SCI-FI-01'],
              genres=None, repeat=None, count=7, systems=list(exp.SYSTEMS), choices=[0, 1],
              concurrency=2, config=self.config, secrets=self.work/'secrets.env', suite_root=None,
              out=self.work/'run', allow_pilot=True, yes=False, run=False, resume=False,
              verify=False, preflight=False)

    def test_freeze_count_and_actual_native_command(self):
        out, plan = exp.prepare(self.args)
        self.assertEqual(plan['total_attempts'], 21)
        self.assertEqual(plan['attempts_by_case'], {'CAMPUS-01': 4, 'SCI-FI-01': 3})
        self.assertEqual(plan['experimental_principle'], 'same_task_common_rubric_independent_narratives')
        for system in exp.SYSTEMS:
            cmd = exp.batch_command(out, system, '--resume', 2, self.args.secrets, plan['attempts_per_system'])
            self.assertEqual(cmd[cmd.index('--count') + 1], '7')
            self.assertEqual(cmd[cmd.index('--concurrency') + 1], '2')
            self.assertIn('--resume', cmd)
        self.assertEqual(exp.read_plan(out)['attempts_per_system'], 7)

    def test_count_below_case_total_records_unvisited(self):
        self.args.case_ids = None
        self.args.count = 2
        _, plan = exp.prepare(self.args)
        self.assertEqual(len(plan['attempts_by_case']), 30)
        self.assertEqual(sum(n == 0 for n in plan['attempts_by_case'].values()), 28)
        self.assertEqual(plan['total_attempts'], 6)

    def test_single_system_selected_genre_count(self):
        self.args.case_ids = None
        self.args.genres = ['SCI-FI']
        self.args.systems = ['if_line']
        _, plan = exp.prepare(self.args)
        self.assertEqual(plan['total_attempts'], 7)
        self.assertEqual(plan['case_ids'], [f'SCI-FI-0{i}' for i in range(1, 6)])
        self.assertEqual(list(plan['attempts_by_case'].values()), [2, 2, 1, 1, 1])

    def test_count_alone_does_not_authorize_paid_calls(self):
        with patch.object(exp, 'call', side_effect=AssertionError('No native calls allowed')):
            with redirect_stdout(io.StringIO()):
                code = exp.main(['--config', str(self.config), '--out', str(self.args.out),
                                 '--preset', 'pilot', '--count', '7', '--allow-pilot', '--yes'])
        self.assertEqual(code, 0)
        self.assertFalse((self.args.out / 'if_line').exists())

    def test_root_prepare_runs_real_offline_compiler(self):
        result = subprocess.run(['bash', str(ROOT/'experiment.sh'), 'prepare', '--config', str(self.config),
                                 '--out', str(self.args.out), '--preset', 'pilot', '--count', '3',
                                 '--genres', 'EMOTION', '--allow-pilot'],
                                cwd='/tmp', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = exp.read_plan(self.args.out)
        self.assertEqual(plan['attempts_per_system'], 3)
        self.assertEqual(plan['total_attempts'], 9)
        self.assertEqual(sum(plan['attempts_by_case'].values()), 3)
        self.assertIn('Preparation only: zero model calls', result.stdout)
        self.assertFalse((self.args.out/'if_line').exists())


if __name__ == '__main__':
    unittest.main()
