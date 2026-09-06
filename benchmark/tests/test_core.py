import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from story_benchmark.__main__ import main as cli_main
from story_benchmark.compiler import compile_case, load_case, render, verify_bundle
from story_benchmark.io import BenchmarkError, atomic_json, sha256, redact
from story_benchmark.runner import run_once, resume_export, validate_config, RootRunTimeout, verify_saved_run
from story_benchmark.audit import audit_trace, validate_source_map

ROOT = Path(__file__).resolve().parents[1]


class CaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('cases', 'profiles'):
            shutil.copytree(ROOT / name, self.root / name)
        self.case = self.root / 'cases/CAMPUS-01.json'
        self.out = self.root / 'bundle'

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, fn):
        value = json.loads(self.case.read_text())
        fn(value)
        self.case.write_text(json.dumps(value, ensure_ascii=False))

    def build(self):
        return compile_case(self.case, self.out, True)

    def test_identical_decoded_inputs(self):
        result = self.build()
        self.assertEqual(result['payload_check'], 'passed')
        self.assertEqual(result['native_integration'], 'not_run')
        shared = (self.out / 'shared_task.txt').read_text()
        self.assertEqual(shared, shared.strip())
        self.assertIn((self.root/'cases/CAMPUS-01.opening.txt').read_text(),shared)
        self.assertEqual(shared.count('<<<OPENING_BEGIN>>>'),1)

    def test_one_character_tamper(self):
        self.build()
        path = self.out/'payloads/requirements.txt'
        path.write_text(path.read_text().replace('周遥','周瑶',1))
        with self.assertRaisesRegex(BenchmarkError,'bundle_hash_mismatch'): verify_bundle(self.out)

    def test_payload_comparison_not_only_manifest_hash(self):
        self.build()
        path = self.out/'payloads/requirements.txt'
        path.write_text(path.read_text()+'额外要求')
        manifest = json.loads((self.out/'manifest.json').read_text())
        manifest['files']['payloads/requirements.txt'] = sha256(path.read_bytes())
        atomic_json(self.out/'manifest.json',manifest)
        with self.assertRaisesRegex(BenchmarkError,'payload_mismatch'): verify_bundle(self.out)

    def test_missing_opening(self):
        (self.root/'cases/CAMPUS-01.opening.txt').unlink()
        with self.assertRaises(FileNotFoundError): self.build()

    def test_duplicate_json_key(self):
        self.case.write_text(self.case.read_text().replace('"case_id":','"case_id":"duplicate", "case_id":',1))
        with self.assertRaisesRegex(BenchmarkError,'duplicate_json_key'): self.build()

    def test_player_not_declared(self):
        self.edit(lambda c:c.update(player_name='额外玩家'))
        with self.assertRaisesRegex(BenchmarkError,'player_not_declared'): self.build()

    def test_pilot_cannot_run_as_formal(self):
        with self.assertRaisesRegex(BenchmarkError,'unapproved_case'): compile_case(self.case,self.out)

    def test_pilot_full_vn_cannot_bypass(self):
        self.edit(lambda c:c.update(profile='FULL_VN'))
        with self.assertRaisesRegex(BenchmarkError,'unapproved_case'): self.build()

    def test_approval_cannot_be_self_asserted_without_record(self):
        self.edit(lambda c:c.update(review_status='approved'))
        with self.assertRaisesRegex(BenchmarkError,'approval_record_required'): self.build()

    def test_no_overwrite(self):
        self.build()
        before = (self.out/'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError): self.build()
        self.assertEqual(before,(self.out/'manifest.json').read_bytes())

    def test_reserved_marker_in_source(self):
        path=self.root/'cases/CAMPUS-01.opening.txt'
        path.write_text(path.read_text()+'<<<SHARED_TASK_V2_END>>>')
        self.edit(lambda c:c['provenance']['source_sha256'].update(opening=sha256(path.read_bytes())))
        with self.assertRaisesRegex(BenchmarkError,'forged_shared_boundary'): self.build()

    def test_reserved_marker_in_metadata(self):
        self.edit(lambda c:c.update(title='<<<OPENING_BEGIN>>>'))
        with self.assertRaisesRegex(BenchmarkError,'forged_shared_boundary'): self.build()

    def test_source_hash_required(self):
        (self.root/'cases/CAMPUS-01.opening.txt').write_text('另一个开头')
        with self.assertRaisesRegex(BenchmarkError,'source_hash_mismatch'): self.build()

    def test_path_escape(self):
        self.edit(lambda c:c.update(opening_file='../../secret'))
        with self.assertRaisesRegex(BenchmarkError,'path_outside_root'): self.build()

    def test_dev_does_not_append_full_story_goals(self):
        case,texts,_=load_case(self.case,True)
        rendered=render(case,texts)
        self.assertNotIn('至少 3 条',rendered)
        self.assertNotIn('4,000',rendered)
        self.assertNotIn('每条完整路线',rendered)

    def test_future_choice_not_allowed(self):
        self.edit(lambda c:c.update(trajectory='AB'))
        with self.assertRaisesRegex(BenchmarkError,'unknown_case_fields'): self.build()

    def test_wrong_character_count_caught(self):
        self.build()
        path=self.out/'payloads/ai4vn.json'
        data=json.loads(path.read_text()); data['character_count']=4; atomic_json(path,data)
        manifest=json.loads((self.out/'manifest.json').read_text()); manifest['files']['payloads/ai4vn.json']=sha256(path.read_bytes()); atomic_json(self.out/'manifest.json',manifest)
        with self.assertRaisesRegex(BenchmarkError,'payload_metadata_mismatch'):verify_bundle(self.out)

    def test_manifest_cannot_upgrade_pilot(self):
        self.build()
        manifest=json.loads((self.out/'manifest.json').read_text());manifest['review_status']='approved'
        atomic_json(self.out/'manifest.json',manifest)
        with self.assertRaisesRegex(BenchmarkError,'bundle_case_metadata_mismatch'):verify_bundle(self.out)


class FakeAdapter:
    """Never contacts a native service. Test fixture only."""
    def __init__(self, failure=None): self.failure=failure;self.calls=0;self.closed=False
    def preflight(self,bundle):return {'ok':True,'checks':[],'errors':[]}
    def prepare(self,bundle,run):return {'run_dir':str(run)}
    def generate_first_artifact(self,handle):
        self.calls+=1
        if self.failure=='timeout':raise TimeoutError('mock timeout')
        if self.failure=='interrupt':raise KeyboardInterrupt()
        if self.failure=='error':raise RuntimeError('mock HTTP 500')
        if self.failure!='missing':atomic_json(Path(handle['run_dir'])/'native/prose.json',{'text':'模拟正文' if self.failure!='empty' else ''})
        return {'evidence_kind':'mock'}
    def export_first_artifact(self,handle):
        text=json.loads((Path(handle['run_dir'])/'native/prose.json').read_text())['text']
        return {'segments':[{'segment_id':'p001','kind':'narration','speaker':None,'text':text,'native_source':'native/prose.json','native_pointer':'/text'}] if text else [],'choices':[],'generation_issue_codes':[]}
    def close(self,handle):self.closed=True


class RecordedFixtureAdapter(FakeAdapter):
    """On-disk audit fixture, never a native-service or paid-provider call."""
    def __init__(self, trace_fault=None):
        super().__init__()
        self.trace_fault = trace_fault

    def generate_first_artifact(self, handle):
        result = super().generate_first_artifact(handle)
        run = Path(handle['run_dir'])
        shared = (run / 'shared_task.txt').read_text()
        atomic_json(run / 'trace/received-input.json', {
            'received_task': shared, 'boundary': 'fixture_receiver',
        })
        call = {
            'call_id': 'fixture-http-1', 'boundary': 'http', 'attempt': 1,
            'root_run_id': run.name, 'system': 'if_line',
            'stage': 'chapter.generate', 'start_time': '2026-01-01T00:00:00Z',
            'requested_model': 'other-model' if self.trace_fault == 'wrong_model' else 'frozen',
            'request_messages': [{'role': 'user', 'content': shared}],
            'request_schema_and_sampling': {},
        }
        events = [dict(call, event='started'), dict(
            call, event='completed', actual_model=call['requested_model'],
            usage={'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2},
            response_file='fixture-response.json',
        )]
        trace = '\n'.join(json.dumps(event, ensure_ascii=False) for event in events) + '\n'
        if self.trace_fault == 'malformed_trace':
            trace += '{incomplete\n'
        (run / 'trace/fixture.jsonl').write_text(trace, encoding='utf-8')
        if self.trace_fault != 'missing_response':
            atomic_json(run / 'trace/fixture-response.json', {'fixture_only': True, 'text': '模拟正文'})
        return result


class RunnerTest(unittest.TestCase):
    setUp=CaseTest.setUp
    tearDown=CaseTest.tearDown
    build=CaseTest.build
    # Run-state tests use explicit mock provenance, distinct from generation evidence.
    def test_mock_success_is_not_native_verification(self):
        self.build();adapter=FakeAdapter()
        result=run_once('if_line',self.out,{},self.root/'run',adapter,mock=True)
        self.assertEqual(result['state'],'EXPORTED');self.assertEqual(result['evidence_kind'],'mock')
        self.assertEqual(result['native_integration'],'not_verified');self.assertTrue(adapter.closed)
        self.assertEqual(resume_export(self.root/'run',adapter),result);self.assertEqual(adapter.calls,1)

    def test_interrupted_operation_never_resent(self):
        self.build();adapter=FakeAdapter('interrupt')
        with self.assertRaises(KeyboardInterrupt):run_once('if_line',self.out,{},self.root/'run',adapter,mock=True)
        state=json.loads((self.root/'run/manifest.json').read_text())
        self.assertEqual(state['generation_status'],'delivery_unknown')
        with self.assertRaisesRegex(BenchmarkError,'reconciliation'):resume_export(self.root/'run',adapter)
        self.assertEqual(adapter.calls,1)

    def test_timeout_and_error_keep_evidence(self):
        self.build()
        for failure in ('timeout','error'):
            adapter=FakeAdapter(failure);run=self.root/failure
            with self.assertRaises((RuntimeError,TimeoutError)):run_once('if_line',self.out,{},run,adapter,mock=True)
            self.assertTrue((run/'errors.jsonl').stat().st_size);self.assertTrue(adapter.closed)

    def test_empty_is_not_success(self):
        self.build();result=run_once('if_line',self.out,{},self.root/'empty',FakeAdapter('empty'),mock=True)
        self.assertEqual(result['generation_status'],'empty');self.assertEqual(result['adapter_status'],'failed')

    def test_missing_artifact_keeps_completed_generation_evidence(self):
        self.build()
        with self.assertRaises(FileNotFoundError):run_once('if_line',self.out,{},self.root/'missing',FakeAdapter('missing'),mock=True)
        self.assertTrue((self.root/'missing/operation_result.json').exists())

    def test_missing_limits_prevents_prepare(self):
        self.build();adapter=FakeAdapter()
        with self.assertRaisesRegex(BenchmarkError,'missing_positive_limit'):run_once('if_line',self.out,{'live':True},self.root/'run',adapter)
        self.assertEqual(adapter.calls,0);self.assertFalse((self.root/'run').exists())

    def test_successive_runs_are_isolated(self):
        self.build()
        for name in ('a','b'):run_once('if_line',self.out,{},self.root/name,FakeAdapter(),mock=True)
        (self.root/'a/native/prose.json').write_text('changed')
        self.assertEqual(json.loads((self.root/'b/native/prose.json').read_text())['text'],'模拟正文')

    def test_root_timeout_covers_whole_generation(self):
        import time
        class Slow(FakeAdapter):
            def generate_first_artifact(self,handle):
                try:
                    time.sleep(.25)
                except (TimeoutError, ConnectionError):
                    pass  # Native readiness loops must not swallow the root deadline.
                self.calls+=1
                return {}
        self.build();adapter=Slow()
        with self.assertRaisesRegex(RootRunTimeout,'root_run_timeout'):
            run_once('if_line',self.out,{'timeout_seconds':.04},self.root/'timed',adapter,mock=True)
        self.assertTrue(adapter.closed)
        self.assertEqual(adapter.calls,0)
        self.assertEqual(json.loads((self.root/'timed/manifest.json').read_text())['generation_status'],'delivery_unknown')

    def test_fixture_cannot_be_labelled_live(self):
        errors=validate_config({'live':True,'managed_runtime':'engineering_fixed_response','model':'frozen',
            'model_base_url':'https://example.org/v1','timeout_seconds':60,'max_calls':10,
            'max_output_tokens':100,'max_input_chars':1000})
        self.assertIn('fixture_execution_forbidden_in_live_runner',errors)

    def test_close_source_failure_invalidates_acceptance(self):
        class ChangedSource(FakeAdapter):
            def close(self,handle):raise RuntimeError('source_drift')
        self.build();result=run_once('if_line',self.out,{},self.root/'drift',ChangedSource(),mock=True)
        self.assertEqual(result['adapter_status'],'failed')
        self.assertEqual(result['native_integration'],'not_verified')
        self.assertEqual(result['cleanup_status'],'failed')

    def test_invalid_runtime_evidence_never_verifies_native_integration(self):
        self.build()
        for fault in (None, 'wrong_model', 'missing_response', 'malformed_trace'):
            with self.subTest(fault=fault):
                run = self.root / (fault or 'valid_fixture')
                result = run_once('if_line', self.out, {'model': 'frozen'}, run,
                                  RecordedFixtureAdapter(fault), mock=True)
                audit = json.loads((run / 'audit.json').read_text())
                self.assertEqual(result['evidence_kind'], 'mock')
                if fault is None:
                    self.assertEqual(result['native_integration'], 'verified_with_fixture_provider')
                    self.assertEqual(result['audit_status'], 'passed')
                else:
                    self.assertEqual(result['native_integration'], 'not_verified')
                    self.assertEqual(result['audit_status'], 'incomplete')
                    if fault == 'wrong_model':
                        self.assertFalse(audit['configured_model_matches_requests'])
                    elif fault == 'missing_response':
                        self.assertEqual(audit['missing_response_files'], ['fixture-http-1'])
                    else:
                        self.assertEqual(audit['trace_parse_errors'], ['trace/fixture.jsonl:3'])

    def test_saved_evidence_or_configuration_tamper_blocks_resume_and_cli_audit(self):
        self.build()
        for fault in ('prose', 'config', 'config_with_updated_file_hash'):
            with self.subTest(fault=fault):
                adapter = RecordedFixtureAdapter()
                run = self.root / fault
                run_once('if_line', self.out, {'model': 'frozen'}, run, adapter, mock=True)
                relative = 'native/prose.json' if fault == 'prose' else 'config.json'
                path = run / relative
                document = json.loads(path.read_text())
                document['text' if fault == 'prose' else 'model'] = 'tampered'
                atomic_json(path, document)
                expected_error = 'saved_evidence_changed:' + relative
                if fault == 'config_with_updated_file_hash':
                    manifest = json.loads((run / 'manifest.json').read_text())
                    manifest['evidence_files'][relative] = sha256(path.read_bytes())
                    atomic_json(run / 'manifest.json', manifest)
                    expected_error = 'saved_configuration_changed'
                changed_bytes = path.read_bytes()
                with self.assertRaisesRegex(BenchmarkError, expected_error):
                    resume_export(run, adapter)
                output = StringIO()
                with patch('sys.argv', ['story-benchmark', 'audit-run', '--run-dir', str(run)]), redirect_stdout(output):
                    self.assertEqual(cli_main(), 1)
                self.assertIn(expected_error, json.loads(output.getvalue())['error'])
                self.assertEqual(adapter.calls, 1)
                self.assertEqual(path.read_bytes(), changed_bytes)

    def test_sealed_mock_resume_and_cli_audit_are_read_only_without_dispatch(self):
        self.build()
        run = self.root / 'sealed'
        original_adapter = RecordedFixtureAdapter()
        result = run_once('if_line', self.out, {'model': 'frozen'}, run,
                          original_adapter, mock=True)
        before = {str(path.relative_to(run)): sha256(path.read_bytes())
                  for path in run.rglob('*') if path.is_file()}
        self.assertEqual(verify_saved_run(run), result)
        no_dispatch = unittest.mock.Mock(spec=FakeAdapter)
        for method in ('preflight', 'prepare', 'generate_first_artifact', 'export_first_artifact', 'close'):
            getattr(no_dispatch, method).side_effect = AssertionError('sealed resume must not dispatch')
        self.assertEqual(resume_export(run, no_dispatch), result)
        self.assertEqual(no_dispatch.mock_calls, [])
        output = StringIO()
        with patch('sys.argv', ['story-benchmark', 'audit-run', '--run-dir', str(run)]), redirect_stdout(output):
            self.assertEqual(cli_main(), 0)
        self.assertTrue(json.loads(output.getvalue())['configured_model_matches_requests'])
        after = {str(path.relative_to(run)): sha256(path.read_bytes())
                 for path in run.rglob('*') if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(verify_saved_run(run), result)
        self.assertEqual(original_adapter.calls, 1)


class AuditTest(unittest.TestCase):
    def test_unknown_usage_is_not_zero_and_sdk_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'trace').mkdir()
            events=[{'call_id':'sdk1','boundary':'sdk','event':'completed','usage':{'total_tokens':20}},
                    {'call_id':'http1','boundary':'http','event':'started','stage':'writer','request_messages':[{'role':'system','content':'shared opening'}]},
                    {'call_id':'http1','boundary':'http','event':'completed','usage':None}]
            (root/'trace/1.jsonl').write_text('\n'.join(json.dumps(v) for v in events))
            result=audit_trace(root,'shared','opening')
            self.assertEqual(result['observed_http_calls'],1);self.assertFalse(result['usage_coverage_complete'])
            self.assertIsNone(result['actual_cost']);self.assertIsNone(result['external_input_equal'])

    def test_malformed_trace_prevents_complete_usage_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'trace').mkdir();(root/'trace/1.jsonl').write_text('{incomplete')
            result=audit_trace(root,'shared','opening');self.assertFalse(result['usage_coverage_complete']);self.assertTrue(result['trace_parse_errors'])

    def test_wrong_pointer_fails_even_when_text_elsewhere(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);atomic_json(root/'prose.json',{'correct':'正文','wrong':'另一条路线'})
            valid,issues=validate_source_map(root,[{'segment_id':'p1','kind':'narration','speaker':None,'text':'正文','native_source':'prose.json','native_pointer':'/wrong'}])
            self.assertFalse(valid)

    def test_credential_redaction(self):
        data={'Authorization':'Bearer actualsecret','Cookie':'id=abc','url':'https://api.example/v1?api_key=secret&mode=ok','usage':None}
        encoded=json.dumps(redact(data));self.assertNotIn('actualsecret',encoded);self.assertNotIn('api_key=secret',encoded);self.assertIn('null',encoded)


if __name__=='__main__':unittest.main()
