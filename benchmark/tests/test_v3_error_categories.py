"""Recorded trace regressions for stop-cause attribution; no provider calls."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from story_benchmark.adapters.if_line import IFLineError
from story_benchmark.compiler import compile_case
from story_benchmark.result import error_category, validate_result
from story_benchmark.runner import execute_run, verify_saved_run
from test_v3 import Fixture


ROOT = Path(__file__).resolve().parents[1]


class StopCauseFixture(Fixture):
    def __init__(self, *, failure=None, budget=True, unknown=False, cleanup=False, blocked=None, mode='normal'):
        super().__init__('if_line', mode)
        self.failure = failure
        self.budget = budget
        self.unknown = unknown
        self.cleanup = cleanup
        self.blocked = blocked

    def generate_first_artifact(self, handle):
        result = super().generate_first_artifact(handle)
        trace = Path(handle['run_dir']) / 'trace/stop-cause.jsonl'
        events = []
        if self.budget:
            # Same boundary and error shape emitted by the IF HTTP budget hook:
            # no call_id, because this rejected request never reached HTTP.
            events.append({'event': 'error', 'boundary': 'budget', 'system': 'if_line',
                           'usage': None, 'error': {'code': 'budget_exhausted',
                                                    'detail': 'benchmark call/output budget exhausted'}})
        if self.unknown:
            events.append({'event': 'started', 'boundary': 'http', 'system': 'if_line',
                           'root_run_id': Path(handle['run_dir']).name,
                           'call_id': 'unknown-delivery', 'stage': 'branch.candidates.generate',
                           'requested_model': 'frozen', 'actual_model': None,
                           'request_messages': [], 'request_schema_and_sampling': {},
                           'usage': None, 'response_file': None,
                           'delivery_status': 'delivery_unknown'})
        if self.blocked:
            events.append({'event': 'completed', 'boundary': 'http', 'system': 'if_line',
                           'call_id': 'never-sent', 'usage': {'prompt_tokens': 999,
                               'completion_tokens': 999, 'total_tokens': 1998}, **self.blocked})
        trace.write_text(''.join(json.dumps(event) + '\n' for event in events))
        if self.failure:
            raise IFLineError(self.failure, 'native recorded failure')
        return result

    def close(self, handle):
        super().close(handle)
        if self.cleanup:
            raise RuntimeError('worker cleanup failed')


class V3ErrorCategoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('cases', 'profiles'):
            shutil.copytree(ROOT / name, self.root / name)
        self.bundle = self.root / 'bundle'
        compile_case(self.root / 'cases/CAMPUS-01-V3.json', self.bundle, True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_fixture(self, name, **kwargs):
        result = execute_run('if_line', self.bundle, {'model': 'frozen'},
                             self.root / name, StopCauseFixture(**kwargs), mock=True)
        validate_result(result)
        verify_saved_run(self.root / name)
        return result

    def test_recorded_budget_precedes_following_native_task_failure(self):
        result = self.run_fixture('budget-native', failure='native_task_failed')
        self.assertEqual(result['outcome'], 'budget_exhausted')
        self.assertEqual(result['native_status'], 'failed')
        self.assertIsNone(result['scope']['selection_executed'])
        self.assertEqual(result['errors'][0]['code'], 'native_task_failed')
        self.assertIn('budget_exhausted', [error['code'] for error in result['errors']])
        self.assertIn('budget_exhausted', result['provenance']['native_issue_codes'])

    def test_plain_native_task_failure_stays_native(self):
        result = self.run_fixture('plain-native', failure='native_task_failed', budget=False)
        self.assertEqual(result['outcome'], 'native_error')
        self.assertEqual(result['errors'][0]['category'], 'native')

    def test_known_if_native_artifact_aliases_are_native(self):
        aliases = ('missing_native_artifact', 'missing_result_ref', 'invalid_native_candidate_count',
                   'invalid_native_candidate', 'duplicate_native_candidate_identity')
        for code in aliases:
            with self.subTest(code=code):
                result = self.run_fixture(code, failure=code, budget=False)
                self.assertEqual(result['outcome'], 'native_error')
                self.assertEqual(result['errors'][0]['category'], 'native')
                self.assertEqual(result['errors'][0]['code'], code)

    def test_budget_does_not_mask_cleanup_failure(self):
        result = self.run_fixture('cleanup', failure='native_task_failed', cleanup=True)
        self.assertEqual(result['outcome'], 'adapter_error')
        self.assertEqual(result['adapter_status'], 'failed')
        self.assertIn('cleanup_failed', [error['code'] for error in result['errors']])

    def test_budget_does_not_mask_source_identity_failure(self):
        result = self.run_fixture('source', failure='candidate_source_input_mismatch')
        self.assertEqual(result['outcome'], 'adapter_error')
        self.assertEqual(result['errors'][0]['category'], 'adapter')
        for code in ('candidate_source_input_mismatch', 'candidate_result_refs_mismatch',
                     'native_snapshot_boundary_not_verified', 'export_source_mapping_invalid'):
            self.assertEqual(error_category(code), 'adapter')

    def test_budget_does_not_mask_invalid_export_mapping(self):
        result = self.run_fixture('mapping', mode='forged')
        self.assertEqual(result['outcome'], 'adapter_error')
        self.assertEqual(result['adapter_status'], 'failed')
        self.assertIn('export_source_mapping_invalid', [error['code'] for error in result['errors']])

    def test_budget_does_not_resolve_unknown_delivery(self):
        result = self.run_fixture('unknown', failure='native_task_failed', unknown=True)
        self.assertEqual(result['outcome'], 'delivery_unknown')
        self.assertEqual(result['native_status'], 'unknown')
        self.assertIsNone(result['scope']['selection_executed'])
        self.assertIn('delivery_unknown', [error['code'] for error in result['errors']])

    def test_unsent_records_never_enter_http_count_or_token_sum(self):
        control = self.run_fixture('usage-control', budget=False)
        for index, blocked in enumerate(({'event': 'blocked'}, {'delivery_status': 'not_sent'}, {'wire_sent': False})):
            with self.subTest(blocked=blocked):
                result = self.run_fixture('usage-blocked-' + str(index), budget=False, blocked=blocked)
                self.assertEqual(result['outcome'], 'completed')
                self.assertEqual(result['usage'], control['usage'])
                self.assertEqual(result['usage']['http_calls'], 1)
                self.assertEqual(result['usage']['total_tokens'], 2)


if __name__ == '__main__':
    unittest.main()
