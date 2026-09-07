"""Provider-attempt audit regressions; no native process or network is used."""
import json
from pathlib import Path
import tempfile
import unittest

from story_benchmark.audit import audit_trace, is_http_attempt_record


class HttpAttemptAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v3-http-audit-')
        self.root = Path(self.temp.name)
        (self.root / 'trace/responses').mkdir(parents=True)
        self.shared = '<<<SHARED_TASK_V2_BEGIN>>>\n共同题目与开头\n<<<SHARED_TASK_V2_END>>>'
        self.parameters = {'thinking': {'type': 'disabled'}}
        (self.root / 'config.json').write_text(json.dumps({
            'model': 'same-model', 'model_parameters': self.parameters}))
        (self.root / 'manifest.json').write_text(json.dumps({
            'system': 'infiplot', 'root_run_id': 'fixture'}))
        # This is an actual receiver observation fixture, independent of whether
        # the request is later blocked before contacting a provider.
        (self.root / 'trace/received-writer.json').write_text(json.dumps({
            'boundary': 'native_sdk_task_block', 'received_task': self.shared}))
        (self.root / 'trace/responses/sent.json').write_text('{}')

    def tearDown(self):
        self.temp.cleanup()

    def sent(self, event='completed', **overrides):
        return {
            'root_run_id': 'fixture', 'system': 'infiplot', 'boundary': 'http',
            'call_id': 'sent', 'event': event, 'stage': 'writer',
            'start_time': '2026-09-07T00:00:00Z', 'attempt': 1,
            'requested_model': 'same-model', 'actual_model': 'same-model',
            'request_messages': [{'role': 'system', 'content': self.shared}],
            'request_schema_and_sampling': self.parameters,
            'response_file': 'responses/sent.json',
            'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5},
            'usage_complete': True, 'delivery_status': 'response_received',
            **overrides,
        }

    def blocked(self, **overrides):
        # Frozen InfiPlot Relay.handle_request rejects before its started event.
        return {
            'root_run_id': 'fixture', 'system': 'infiplot', 'boundary': 'http',
            'call_id': 'not-sent', 'event': 'blocked', 'error': 'call_budget_exceeded',
            'generation_issue_code': 'call_budget_exceeded', 'delivery_status': 'not_sent',
            **overrides,
        }

    def audit(self, records, materialize=False):
        (self.root / 'trace/calls-fixture.jsonl').write_text(
            ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records))
        return audit_trace(self.root, self.shared, 'opening', materialize=materialize)

    def assert_one_successful_attempt(self, result):
        self.assertEqual(result['observed_http_calls'], 1)
        self.assertEqual(result['task_entry_shared_occurrences'], 1)
        self.assertTrue(result['configured_model_matches_requests'])
        self.assertTrue(result['configured_model_parameters_match_requests'])
        self.assertTrue(result['usage_coverage_complete'])
        self.assertEqual(result['missing_response_files'], [])
        self.assertEqual(result['delivery_unknown_calls'], [])

    def test_infiplot_sent_plus_blocked_preserves_ingress_and_budget_issue(self):
        result = self.audit([self.sent('started'), self.sent(), self.blocked()], materialize=True)
        self.assert_one_successful_attempt(result)
        self.assertEqual(result['receiver_observations'], 1)
        self.assertTrue(result['external_input_equal'])
        self.assertIn('budget_exhausted', result['native_issue_codes'])
        self.assertIn('call_budget_exceeded', result['native_issue_codes'])
        self.assertEqual([p.name for p in (self.root / 'requests').iterdir()], ['sent.json'])
        self.assertFalse((self.root / 'responses/not-sent.json').exists())

    def test_only_blocked_has_no_attempt_or_model_proof_but_keeps_receiver(self):
        result = self.audit([self.blocked()], materialize=True)
        self.assertEqual(result['observed_http_calls'], 0)
        self.assertIsNone(result['task_entry_shared_occurrences'])
        self.assertIsNone(result['configured_model_matches_requests'])
        self.assertIsNone(result['configured_model_parameters_match_requests'])
        self.assertFalse(result['usage_coverage_complete'])
        self.assertEqual(result['missing_response_files'], [])
        self.assertEqual(result['delivery_unknown_calls'], [])
        self.assertEqual(result['receiver_observations'], 1)
        self.assertTrue(result['external_input_equal'])
        self.assertIn('budget_exhausted', result['native_issue_codes'])

    def test_ifline_budget_boundary_and_false_wire_sent_do_not_count(self):
        blocked = {'event': 'error', 'boundary': 'budget', 'system': 'if_line',
                   'root_run_id': 'fixture', 'call_id': 'ifline-blocked',
                   'requested_model': 'different-rejected-model', 'actual_model': None,
                   'usage': None, 'wire_sent': False,
                   'error': {'code': 'budget_exhausted', 'detail': 'call budget exhausted'}}
        result = self.audit([self.sent(), blocked])
        self.assert_one_successful_attempt(result)
        self.assertTrue(result['call_context_valid'])
        self.assertIn('budget_exhausted', result['native_issue_codes'])

    def test_ai4vn_sdk_budget_error_stays_outside_http_attempts(self):
        blocked = {'boundary': 'sdk', 'call_id': 'ai4vn-sdk', 'event': 'error',
                   'delivery_status': 'not_sent', 'failure_code': 'budget_exhausted',
                   'error': {'type': 'RuntimeError', 'message': 'benchmark_call_budget_exhausted'}}
        result = self.audit([self.sent(), blocked])
        self.assert_one_successful_attempt(result)
        self.assertIn('budget_exhausted', result['native_issue_codes'])

    def test_completed_only_fixture_remains_supported(self):
        for event in ('completed', 'complete', 'response'):
            with self.subTest(event=event):
                record = self.sent(event)
                record.pop('boundary')
                self.assert_one_successful_attempt(self.audit([record]))

    def test_partial_http_error_only_is_still_an_attempt_with_unknown_delivery(self):
        record = self.sent('error', delivery_status='delivery_unknown', usage=None,
                           usage_complete=False, response_complete=False)
        result = self.audit([record])
        self.assertEqual(result['observed_http_calls'], 1)
        self.assertEqual(result['delivery_unknown_calls'], ['sent'])
        self.assertFalse(result['usage_coverage_complete'])

    def test_started_without_response_remains_counted_and_unknown(self):
        record = self.sent('started', response_file=None, usage=None)
        record.pop('delivery_status')
        result = self.audit([record])
        self.assertEqual(result['observed_http_calls'], 1)
        self.assertEqual(result['delivery_unknown_calls'], ['sent'])
        self.assertEqual(result['missing_response_files'], ['sent'])

    def test_explicit_not_sent_wins_over_completed_or_error_event_names(self):
        for overrides in ({'delivery_status': 'not_sent'}, {'wire_sent': False}, {'event': 'blocked'}):
            with self.subTest(overrides=overrides):
                record = self.sent(**overrides)
                self.assertFalse(is_http_attempt_record(record))
                self.assertEqual(self.audit([record])['observed_http_calls'], 0)

    def test_unrelated_boundaries_do_not_overwrite_http_call_evidence(self):
        result = self.audit([self.sent(), self.sent('error', boundary='sdk', requested_model='wrong')])
        self.assert_one_successful_attempt(result)


if __name__ == '__main__':
    unittest.main()
