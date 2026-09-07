"""Synthetic regressions for the recorded AI4 SDK budget shape; no requests.

These are test fixtures, never amendments to the three unresolved live attempts.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
from story_benchmark.audit import audit_trace
from story_benchmark.result import finalize_result, validate_result


class SDKBudgetAuditTests(unittest.TestCase):
    def make_run(self, unknown_ids=(), completed_calls=0):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name in ('trace', 'requests', 'responses', 'export'):
            (root / name).mkdir()
        shared, opening = '固定工程测试：共同输入。', '已有开头。'
        (root / 'shared_task.txt').write_text(shared)
        (root / 'export/provided_prefix.txt').write_text(opening)
        case = json.loads((BENCH / 'examples/CAMPUS-01-V3/case.json').read_text())
        (root / 'case.json').write_text(json.dumps(case, ensure_ascii=False))
        (root / 'config.json').write_text(json.dumps({'model':'fixture-model', 'model_parameters':{'thinking':{'type':'disabled'}}}))
        manifest = {'system':'ai4visualnovel', 'root_run_id':'sdk-budget-fixture', 'state':'EXPORTED',
                    'evidence_kind':'mock', 'cleanup_status':'completed', 'adapter_source_sha256':{}}
        (root / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'errors.jsonl').write_text('')
        (root / 'export/generated.jsonl').write_text('')
        (root / 'export/choices.json').write_text('[]')
        (root / 'export/metadata.json').write_text(json.dumps({'stop_reason':'entry_node_end',
             'selection_executed':False, 'generation_issue_codes':['empty_prose','native_choice_boundary_missing']}))
        (root / 'trace/received-ai4vn-fixture.json').write_text(json.dumps({'boundary':'native_requirements_reader','received_task':shared}))
        records = []
        ids = list(unknown_ids) + [f'http-completed-{i:03d}' for i in range(completed_calls)]
        for index, call_id in enumerate(ids):
            event = {'root_run_id':manifest['root_run_id'], 'system':'ai4vn', 'operation_id':'ai4vn.script',
                     'boundary':'http', 'call_id':call_id, 'parent_call_id':f'parent-{index:03d}',
                     'stage':'actor_agent.perform_plot', 'event':'started', 'attempt':1,
                     'start_time':(datetime(2026,9,7,2,16,tzinfo=timezone.utc)+timedelta(seconds=index)).isoformat(), 'end_time':None,
                     'requested_model':'fixture-model', 'actual_model':None,
                     'request_messages':[{'role':'user','content':shared}],
                     'request_schema_and_sampling':{'max_tokens':16384,'thinking':{'type':'disabled'}},
                     'usage':None, 'usage_coverage_complete':False, 'response_file':None, 'error':None}
            records.append(event)
            if call_id not in unknown_ids:
                response = f'response-{call_id}.json'
                (root / 'trace' / response).write_text(json.dumps({'model':'fixture-model','choices':[{
                    'message':{'content':'synthetic fixture'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}))
                records.append({**event, 'event':'completed','actual_model':'fixture-model','status_code':200,
                                'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30},
                                'usage_coverage_complete':True,'response_file':response,'finish_reason':'stop'})
        for index in range(144):
            # SDK wraps the local budget exception; its message omits "budget".
            records.append({'root_run_id':manifest['root_run_id'], 'system':'ai4vn',
                'operation_id':'ai4vn.script', 'stage':'writer_agent.decide_next_speaker',
                'boundary':'sdk', 'call_id':f'sdk-budget-{index:03d}', 'event':'error',
                'error':{'type':'APIConnectionError','message':'Connection error.'},
                'failure_code':'budget_exhausted', 'delivery_status':'not_sent',
                'usage':None, 'response_file':None})
        (root / 'trace/ai4vn-fixture.jsonl').write_text(''.join(json.dumps(event, ensure_ascii=False)+'\n' for event in records))
        return root, manifest, shared, opening

    def test_sdk_failure_code_budget_preserves_three_unknown_http_attempts(self):
        unknown = ['2a7e700f4f844a42ace3de44f78e0352', '12d33a757c0a4185851e56f5eda39ec3', '8bbcfe451c7b45e68499fde9f1ba757e']
        root, manifest, shared, opening = self.make_run(unknown, 157)
        original_trace = (root / 'trace/ai4vn-fixture.jsonl').read_bytes()
        audit = audit_trace(root, shared, opening)
        self.assertIn('budget_exhausted', audit['native_issue_codes'])
        self.assertEqual(audit['observed_http_calls'], 160)  # SDK blocks are not sends.
        self.assertEqual(set(audit['delivery_unknown_calls']), set(unknown))
        self.assertEqual(set(audit['missing_response_files']), set(unknown))
        self.assertFalse(audit['usage_coverage_complete'])
        result = finalize_result(root, manifest)
        self.assertEqual(validate_result(result), result)
        self.assertEqual(result['outcome'], 'delivery_unknown')
        self.assertEqual(result['adapter_status'], 'unverified')
        self.assertEqual(result['native_status'], 'unknown')
        self.assertEqual({e['code'] for e in result['errors']}, {'budget_exhausted','delivery_unknown'})
        self.assertEqual(result['usage']['http_calls'], 160)
        self.assertFalse(result['usage']['complete'])
        self.assertIsNone(result['usage']['total_tokens'])
        self.assertEqual((root / 'trace/ai4vn-fixture.jsonl').read_bytes(), original_trace)
        for call_id in unknown:
            self.assertFalse((root / 'trace' / f'response-{call_id}.json').exists())
            self.assertFalse((root / 'responses' / f'{call_id}.json').exists())

    def test_sdk_only_budget_blocks_are_not_unknown_provider_sends(self):
        root, manifest, shared, opening = self.make_run()
        audit = audit_trace(root, shared, opening)
        self.assertEqual(audit['observed_http_calls'], 0)
        self.assertEqual(audit['delivery_unknown_calls'], [])
        self.assertEqual(audit['missing_response_files'], [])
        self.assertEqual(audit['native_issue_codes'], ['budget_exhausted'])
        result = finalize_result(root, manifest)
        self.assertEqual(result['outcome'], 'budget_exhausted')
        self.assertEqual(result['adapter_status'], 'unverified')
        self.assertEqual([e['code'] for e in result['errors']], ['budget_exhausted'])
        self.assertEqual(result['usage']['http_calls'], 0)
        self.assertEqual(list((root / 'requests').iterdir()), [])
        self.assertEqual(list((root / 'responses').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
