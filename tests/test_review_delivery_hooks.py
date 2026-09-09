"""Delivery orchestration tests with fake workers/publishers; no native/API calls."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmark'))
sys.path.insert(0, str(ROOT / 'tools'))
from story_benchmark import batch

spec = importlib.util.spec_from_file_location('review_delivery_experiment_test', ROOT / 'tools/experiment.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def sealed_fixture(root, *, stopped='reading_window'):
    manifest = {'scope_reached': stopped == 'reading_window', 'native_ended': None,
                'stop_reason': stopped, 'visible_chars': 4001,
                'input_audit': {'status': 'passed'}}
    metrics = {f'M{i}': {'original_value': i, 'unknown': None} for i in range(1, 9)}
    write_json(root / 'manifest.json', manifest)
    write_json(root / 'metrics.json', metrics)
    return manifest, metrics


class BatchDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'batch'
        self.out.mkdir()
        self.inventory = {'fixture.py': 'fixed'}
        self.plan = {'batch_id': 'fixture-batch', 'system': 'if_line', 'count': 2,
                     'adapter_source_inventory': self.inventory,
                     'jobs': [{'run_id': f'run-{i}', 'index': i, 'case_id': 'fixture', 'repeat': 1}
                              for i in range(2)]}
        self.delivery = {'status': 'ready', 'entry_file': str(self.out / 'review-deliveries/v1/review/index.html'),
                         'zip_file': str(self.out / 'review-deliveries/v1/review.zip')}

    def execute(self, publisher, popen):
        with patch.object(batch, 'read_plan', return_value=self.plan), \
             patch('story_benchmark.runner.adapter_source_inventory', return_value=self.inventory), \
             patch.object(batch, 'verify_recording', return_value={'ok': True}), \
             patch.object(batch, 'publish_review_delivery', side_effect=publisher) as published, \
             patch.object(batch.subprocess, 'Popen', side_effect=popen), redirect_stdout(io.StringIO()):
            result = batch.execute_plan(self.out, 2)
        return result, published

    def test_after_all_workers_finish_one_delivery_contains_unchanged_results(self):
        finished = []
        out = self.out
        class Worker:
            def __init__(self, argv, **kwargs):
                self.index = int(argv[argv.index('--index') + 1])
                self.returncode = None
            def wait(self):
                sealed_fixture(out / 'runs' / f'run-{self.index}')
                finished.append(self.index)
                self.returncode = 0
                return 0
            def poll(self):
                return self.returncode
            def send_signal(self, signum):
                raise AssertionError('No cancellation expected')
        def publish(source):
            self.assertEqual(sorted(finished), [0, 1])
            self.assertTrue((source / 'results.json').is_file())
            return self.delivery
        result, published = self.execute(publish, Worker)
        published.assert_called_once_with(self.out.resolve())
        self.assertEqual(result['review_delivery'], self.delivery)
        self.assertEqual([r['stop_reason'] for r in result['runs']], ['reading_window'] * 2)
        self.assertEqual(json.loads((self.out / 'results.json').read_text()), result)
        for row in result['runs']:
            self.assertEqual(row['metrics'], json.loads((self.out / 'runs' / row['run_id'] / 'metrics.json').read_text()))
        self.assertFalse((self.out / 'scheduler.lock').exists())

    def test_resume_never_resends_sealed_runs_but_packages_complete_collection(self):
        for job in self.plan['jobs']:
            sealed_fixture(self.out / 'runs' / job['run_id'])
        originals = {p: p.read_bytes() for p in (self.out / 'runs').rglob('*') if p.is_file()}
        result, published = self.execute(lambda source: self.delivery,
                                         lambda *a, **k: self.fail('No worker should be resent'))
        published.assert_called_once()
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})

    def test_failed_delivery_does_not_change_native_failure_or_metrics(self):
        for job in self.plan['jobs']:
            sealed_fixture(self.out / 'runs' / job['run_id'], stopped='native_error')
        failure = {'status': 'failed', 'message': 'fixture ZIP error'}
        result, published = self.execute(lambda source: failure,
                                         lambda *a, **k: self.fail('No paid retry'))
        published.assert_called_once()
        self.assertEqual(result['review_delivery'], failure)
        self.assertEqual([r['stop_reason'] for r in result['runs']], ['native_error'] * 2)
        self.assertTrue(all(r['metrics']['M7']['unknown'] is None for r in result['runs']))

    def test_verify_summary_does_not_publish(self):
        for job in self.plan['jobs']:
            sealed_fixture(self.out / 'runs' / job['run_id'])
        with patch.object(batch, 'verify_recording', return_value={'ok': True}), \
             patch.object(batch, 'publish_review_delivery', side_effect=AssertionError('verify must not publish')):
            result = batch.summarize(self.out, self.plan)
        self.assertNotIn('review_delivery', result)

    def test_publisher_exception_is_a_delivery_failure_not_a_generation_exception(self):
        module = types.ModuleType('story_benchmark.review_delivery')
        module.publish_delivery = lambda source: (_ for _ in ()).throw(OSError('fixture full disk'))
        with patch.dict(sys.modules, {'story_benchmark.review_delivery': module}), redirect_stdout(io.StringIO()) as output:
            result = batch.publish_review_delivery(self.out)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['generation_retry_requested'])
        self.assertIn('fixture full disk', output.getvalue())

    def test_publisher_reports_the_actual_zip_to_terminal(self):
        module = types.ModuleType('story_benchmark.review_delivery')
        module.publish_delivery = lambda source: self.delivery
        with patch.dict(sys.modules, {'story_benchmark.review_delivery': module}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(batch.publish_review_delivery(self.out), self.delivery)
        self.assertIn('Review ZIP to send: ' + self.delivery['zip_file'], output.getvalue())


class ExperimentDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'experiment'
        self.out.mkdir()
        self.plan = {'systems': ['if_line'], 'attempts_per_system': 1}
        self.args = argparse.Namespace(concurrency=1, secrets=self.out / 'unused.env',
                                       verify=False, preflight=False, run=True, resume=False)
        self.calls = []
        self.delivery = {'status': 'ready', 'zip_file': str(self.out / 'review-deliveries/v1/review.zip'),
                         'entry_file': str(self.out / 'review-deliveries/v1/review/index.html')}

    def native_call(self, argv, out):
        mode = next(m for m in ('--preflight', '--plan-only', '--resume', '--verify') if m in argv)
        self.calls.append(mode)
        if mode == '--plan-only':
            write_json(out / 'if_line/plan.json', {})
        if mode == '--resume':
            self.write_completed(out)
        return 0

    def write_completed(self, out, stopped='reading_window'):
        sealed_fixture(out / 'if_line/runs/one', stopped=stopped)
        write_json(out / 'if_line/results.json', {'runs': [{'scope_reached': stopped == 'reading_window',
                   'evidence_verified': True, 'stop_reason': stopped}]})

    def execute(self, native=None, delivery=None):
        with patch.object(experiment, 'call', side_effect=native or self.native_call), \
             patch.object(experiment, 'publish_review_delivery', return_value=delivery or self.delivery) as publisher, \
             redirect_stdout(io.StringIO()):
            code = experiment.execute(self.out, self.plan, self.args)
        return code, publisher

    def test_success_publishes_once_after_generation_and_exposes_zip_in_summary(self):
        code, published = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, ['--preflight', '--plan-only', '--resume', '--verify'])
        published.assert_called_once_with(self.out)
        summary = json.loads((self.out / 'experiment_summary.json').read_text())
        self.assertEqual(summary['review_delivery'], self.delivery)
        self.assertTrue(summary['all_scopes_reached'])
        self.assertIn(self.delivery['zip_file'], (self.out / 'EXPERIMENT_REPORT.md').read_text())

    def test_partial_native_failure_publishes_without_changing_exit_status(self):
        def native(argv, out):
            code = self.native_call(argv, out)
            if '--resume' in argv:
                self.write_completed(out, stopped='native_error')
                return 2
            return code
        partial = {**self.delivery, 'status': 'partial'}
        code, published = self.execute(native, partial)
        self.assertEqual(code, 2)
        published.assert_called_once_with(self.out)
        summary = json.loads((self.out / 'experiment_summary.json').read_text())
        self.assertEqual(summary['review_delivery']['status'], 'partial')
        self.assertEqual(summary['systems']['if_line']['stop_reasons'], {'native_error': 1})

    def test_interrupt_after_native_cleanup_publishes_sealed_results_and_returns_130(self):
        def native(argv, out):
            code = self.native_call(argv, out)
            if '--resume' in argv:
                raise KeyboardInterrupt()
            return code
        code, published = self.execute(native, {**self.delivery, 'status': 'partial'})
        self.assertEqual(code, 130)
        published.assert_called_once()
        summary = json.loads((self.out / 'experiment_summary.json').read_text())
        self.assertEqual(summary['stages'][-1]['mode'], 'interrupted')
        self.assertIn('review_delivery', summary)

    def test_verify_only_does_not_publish_even_when_runs_exist(self):
        self.args.verify = True
        write_json(self.out / 'if_line/plan.json', {})
        self.write_completed(self.out)
        code, published = self.execute()
        self.assertEqual(code, 0)
        published.assert_not_called()
        self.assertEqual(self.calls, ['--verify'])

    def test_preflight_only_does_not_publish_even_when_runs_exist(self):
        self.args.preflight = True
        self.write_completed(self.out)
        code, published = self.execute()
        self.assertEqual(code, 0)
        published.assert_not_called()
        self.assertEqual(self.calls, ['--preflight'])

    def test_failed_initial_preflight_without_sealed_runs_does_not_publish(self):
        code, published = self.execute(lambda argv, out: 2)
        self.assertEqual(code, 2)
        published.assert_not_called()

    def test_resume_preflight_failure_still_exposes_prior_sealed_stories(self):
        self.args.run = False
        self.args.resume = True
        self.write_completed(self.out)
        code, published = self.execute(lambda argv, out: 2, {**self.delivery, 'status': 'partial'})
        self.assertEqual(code, 2)
        published.assert_called_once_with(self.out)

    def test_delivery_error_does_not_turn_success_into_generation_retry(self):
        code, published = self.execute(delivery={'status': 'failed', 'message': 'fixture export error'})
        self.assertEqual(code, 0)
        published.assert_called_once()
        self.assertEqual(self.calls.count('--resume'), 1)
        summary = json.loads((self.out / 'experiment_summary.json').read_text())
        self.assertTrue(summary['all_scopes_reached'])
        self.assertEqual(summary['review_delivery']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
