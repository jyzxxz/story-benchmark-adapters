"""Synthetic localhost provider: real native CLI, no external model or media API."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_ai4vn import ADAPTER, BENCH, NATIVE_PYTHON, REPO, REPO_CONFIG
from story_benchmark.audit import audit_trace, validate_source_map
from story_benchmark.compiler import compile_case
from story_benchmark.runner import run_once, resume_export, verify_saved_run


def fixture_reply(request, fixture):
    system = request['messages'][0]['content']
    user = request['messages'][-1]['content']
    if '资深的 Visual Novel' in system and '制作人' not in system:
        if 'Step1 设计文档' in user:
            return json.dumps({'title': fixture['marker'], 'background': 'Synthetic engineering fixture only.',
                'art_style': 'fixture', 'story_outline': {'groups': [{'group_id': 'group1', 'group_outline': 'synthetic 12-node graph'}]},
                'characters': [{'id': name, 'name': name, 'gender': '', 'is_protagonist': index == 0,
                    'personality': 'fixture', 'appearance': 'fixture', 'background': 'fixture'} for index, name in enumerate(['林', '周', '顾'])],
                'scenes': [{'id': 'classroom', 'name': '教室', 'description': 'fixture'}]}, ensure_ascii=False)
        nodes = ['root'] + ['node' + str(n) for n in range(1, 12)]
        edges = [('root', 'node1', '追查'), ('root', 'node2', '等待'), ('node1', 'node3', None), ('node2', 'node3', None)]
        edges += [('node' + str(n), 'node' + str(n+1), None) for n in range(3, 11)]
        return json.dumps({'nodes': {n: {'id': n, 'summary': fixture['marker'] + n, 'type': 'merge' if n == 'node3' else 'normal'} for n in nodes},
                           'edges': [{'from': a, 'to': b, 'choice_text': c} for a, b, c in edges]}, ensure_ascii=False)
    if 'Visual Novel 游戏制作人' in system:
        if request.get('response_format'):
            return json.dumps({'thought': 'Synthetic validator response', 'action': 'finalize', 'action_input': {}, 'final_decision': 'PASS'})
        return 'PASS'
    if '剧情结构分析助手' in system:
        return json.dumps([{'id': n, 'summary': fixture['marker'] + '片段', 'characters': ['林'], 'location': '教室'} for n in (1, 2, 3)], ensure_ascii=False)
    if '导演助手' in system:
        with fixture['lock']:
            if not fixture['actor_requested']:
                fixture['actor_requested'] = True
                return '<character>林</character><advice>固定工程夹具表演</advice>'
        return '<character>STOP</character>'
    if '擅长总结故事' in system:
        return fixture['marker'] + '摘要，仅用于工程测试。'
    if '游戏中的角色' in system:
        fixture['actor_calls'] += 1
        return '<content id="林">固定工程夹具表演。</content>'
    if '编剧' in system:
        return ('<scene>教室</scene>\n<content id="旁白">' + fixture['marker'] + '生成正文。</content>\n'
                '<content id="林">请作选择。</content>\n[CHOICE]\n'
                '<choice target="node1">追查</choice>\n<choice target="node2">等待</choice>')
    raise AssertionError('Unexpected native stage system prompt: ' + system[:100])


def start_fixture_server(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            fixture['calls'].append(request)
            try:
                content = fixture_reply(request, fixture)
                payload = {'id': 'synthetic-response', 'model': 'fixture-model', 'choices': [{'index': 0,
                    'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
                self.send_response(200)
            except Exception as error:
                fixture['errors'].append(str(error))
                payload = {'error': {'message': str(error), 'type': 'fixture_error'}}
                self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('x-request-id', 'synthetic-request-' + str(len(fixture['calls'])))
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class NativeFlowTests(unittest.TestCase):
    def test_real_cli_runs_with_local_fixed_sdk_responses(self):
        fixture = {'marker': 'MOCK_A', 'lock': threading.Lock(), 'actor_requested': False, 'actor_calls': 0, 'calls': [], 'errors': []}
        server = start_fixture_server(fixture)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(os.getenv('AI4VN_TEST_ARTIFACT_DIR', temporary.name)).resolve()
        root.mkdir(parents=True, exist_ok=True)
        all_results = []
        shared_bundle = os.getenv('BENCH_SHARED_BUNDLE')
        markers = ('CAMPUS_01_NATIVE_MOCK',) if shared_bundle else ('MOCK_A', 'MOCK_B')
        for marker in markers:
            fixture.update(marker=marker, actor_requested=False)
            if shared_bundle:
                bundle = Path(shared_bundle).resolve()
                shared = (bundle / 'shared_task.txt').read_text(encoding='utf-8')
                opening = (bundle / 'opening.txt').read_text(encoding='utf-8')
            else:
                bundle = root / (marker + '-bundle')
                (bundle / 'payloads').mkdir(parents=True)
                shared = marker + '\n公共任务：固定开头已发生。'
                opening = '固定开头已发生。'
                (bundle / 'shared_task.txt').write_text(shared)
                (bundle / 'payloads/requirements.txt').write_text(shared)
                (bundle / 'payloads/ai4vn.json').write_text('{"character_count":3}')
                (bundle / 'case.json').write_text('{"evidence_kind":"mock"}')
                (bundle / 'opening.txt').write_text(opening)
            config = {**REPO_CONFIG, 'python_executable': NATIVE_PYTHON, 'live': True,
                      'model': 'fixture-model', 'text_provider': 'openai', 'api_key_env': 'AI4VN_FIXTURE_API_KEY',
                      'model_base_url': f'http://127.0.0.1:{server.server_port}/v1', 'max_calls': 150,
                      'max_output_tokens': 8192, 'max_input_chars': 200000, 'timeout_seconds': 45}
            adapter = ADAPTER.AI4VNAdapter(config)
            with patch.dict(os.environ, {'AI4VN_FIXTURE_API_KEY': 'synthetic-local-only-key'}):
                handle = adapter.prepare(bundle, root / marker)
                (root / marker / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
                (root / marker / 'manifest.json').write_text(json.dumps({'evidence_kind': 'mock', 'system': 'ai4visualnovel', 'root_run_id': marker, 'source_mode': handle['source_mode']}, indent=2))
                (root / marker / 'shared_task.txt').write_bytes((bundle / 'shared_task.txt').read_bytes())
                result = adapter.generate_first_artifact(handle)
                exported = adapter.export_first_artifact(handle)
                adapter.close(handle)
            export_dir = root / marker / 'export'
            export_dir.mkdir()
            (export_dir / 'provided_prefix.txt').write_bytes((bundle / 'opening.txt').read_bytes())
            (export_dir / 'generated.jsonl').write_text(''.join(json.dumps(segment, ensure_ascii=False) + '\n' for segment in exported['segments']))
            (export_dir / 'choices.json').write_text(json.dumps(exported['choices'], ensure_ascii=False, indent=2))
            self.assertEqual(result['native_integration'], 'ran')
            self.assertEqual(handle['source_mode'], 'pristine_copy_external_launcher')
            self.assertTrue(all(json.loads(path.read_text())['unchanged'] for path in Path(handle['trace_dir']).glob('source-integrity-ai4vn-*.json')))
            self.assertFalse((Path(handle['source_dir']) / 'agents/benchmark_trace.py').exists())
            self.assertFalse((Path(handle['source_dir']) / 'sitecustomize.py').exists())
            self.assertEqual(exported['segments'][0]['text'], marker + '生成正文。')
            self.assertEqual(len(exported['choices']), 2)
            trace_dir = Path(handle['trace_dir'])
            receipt_files = list(trace_dir.glob('received-ai4vn-*.json'))
            self.assertEqual(len(receipt_files), 1)
            self.assertEqual(json.loads(receipt_files[0].read_text())['received_task'], shared)
            self.assertEqual(json.loads(receipt_files[0].read_text())['boundary'], 'native_requirements_reader')
            records = [json.loads(line) for path in trace_dir.glob('*.jsonl') for line in path.read_text().splitlines()]
            starts = sorted([r for r in records if r['event'] == 'started' and r['boundary'] == 'http'], key=lambda r: r['start_time'])
            completed = [r for r in records if r['event'] == 'completed' and r['boundary'] == 'http']
            self.assertEqual(len(starts), len(completed))
            self.assertTrue(any('actor_agent.perform_plot' == r['stage'] for r in starts))
            self.assertTrue(any('writer_agent.synthesize_script' == r['stage'] for r in starts))
            self.assertTrue(all(r['usage'] == {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30} for r in completed))
            self.assertEqual(starts[0]['request_messages'][1]['content'].count(shared), 1)
            self.assertEqual(int((trace_dir / 'ai4vn-call-count').read_text()), len(starts))
            self.assertTrue(all(r['request_schema_and_sampling']['max_tokens'] == 8192 for r in starts))
            self.assertFalse(fixture['errors'], fixture['errors'])
            # Raw source text is retained and source pointers identify it exactly.
            for segment in exported['segments']:
                text = (root / marker / segment['native_source']).read_text().splitlines()[segment['native_pointer']['line'] - 1]
                self.assertIn(segment['text'], text)
            audit = audit_trace(root / marker, shared, opening)
            self.assertTrue(audit['external_input_equal'])
            self.assertTrue(audit['task_entry_request_contains_shared'])
            self.assertTrue(audit['first_prose_request_observed'])
            self.assertEqual(audit['task_entry_shared_occurrences'], 1)
            self.assertTrue(audit['usage_coverage_complete'])
            self.assertTrue(audit['configured_model_matches_requests'])
            self.assertTrue(validate_source_map(root / marker, exported['segments'])[0])
            (root / marker / 'audit.json').write_text(json.dumps({**audit, 'evidence_kind': 'mock'}, indent=2))
            all_results.append({'evidence_kind': 'mock', 'implementation': 'external_launcher_pristine_source', 'run': marker, 'sdk_http_calls': len(starts),
                                'native_stages': sorted(set(r['stage'] for r in starts)),
                                'segments': len(exported['segments']), 'choices': len(exported['choices']),
                                'shared_task_file': str(bundle / 'shared_task.txt'),
                                'shared_sha256': hashlib.sha256(shared.encode()).hexdigest(),
                                'opening_sha256': hashlib.sha256(opening.encode()).hexdigest(),
                                'external_paid_generation': False})
        self.assertEqual(fixture['actor_calls'], len(markers))
        (root / 'native-flow-report.json').write_text(json.dumps(all_results, ensure_ascii=False, indent=2))

    def test_root_runner_seals_native_cli_and_resumes_without_dispatch(self):
        marker = 'ROOT_RUNNER_CAMPUS_01_MOCK'
        fixture = {'marker': marker, 'lock': threading.Lock(), 'actor_requested': False,
                   'actor_calls': 0, 'calls': [], 'errors': []}
        server = start_fixture_server(fixture)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(os.getenv('AI4VN_TEST_ARTIFACT_DIR', temporary.name)).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if os.getenv('BENCH_SHARED_BUNDLE'):
            bundle = Path(os.environ['BENCH_SHARED_BUNDLE']).resolve()
        else:
            bundle = root / 'root-runner-bundle'
            compile_case(BENCH / 'cases/CAMPUS-01.json', bundle, allow_pilot=True)
        run = root / marker
        shared = (bundle / 'shared_task.txt').read_text(encoding='utf-8')
        opening = (bundle / 'opening.txt').read_text(encoding='utf-8')
        common_config = {
            **REPO_CONFIG, 'python_executable': NATIVE_PYTHON,
            'model': 'fixture-model', 'text_provider': 'openai',
            'api_key_env': 'AI4VN_FIXTURE_API_KEY',
            'model_base_url': f'http://127.0.0.1:{server.server_port}/v1',
            'root_run_id': marker, 'trace_dir': str(run / 'trace'),
            'max_calls': 150, 'max_output_tokens': 8192,
            'max_input_chars': 200000, 'timeout_seconds': 45,
        }
        # The injected adapter enables native subprocesses only against loopback;
        # the public runner and every persisted root verdict remain explicitly mock.
        adapter = ADAPTER.AI4VNAdapter({**common_config, 'live': True})
        before_source = {relative: hashlib.sha256((REPO / relative).read_bytes()).hexdigest()
                         for relative in adapter._source_files()}
        with patch.dict(os.environ, {'AI4VN_FIXTURE_API_KEY': 'synthetic-local-only-key'}):
            manifest = run_once('ai4visualnovel', bundle, {**common_config, 'live': False},
                                run, adapter=adapter, mock=True)
        self.assertEqual(manifest['state'], 'EXPORTED')
        self.assertEqual(manifest['evidence_kind'], 'mock')
        self.assertEqual(manifest['native_integration'], 'verified_with_fixture_provider')
        self.assertEqual(manifest['adapter_status'], 'completed')
        self.assertEqual(manifest['audit_status'], 'passed')
        self.assertEqual(manifest['cleanup_status'], 'completed')
        self.assertFalse(json.loads((run / 'config.json').read_text())['live'])
        self.assertTrue(manifest['evidence_files'])
        self.assertEqual(verify_saved_run(run), manifest)
        state = json.loads((run / 'native/ai4vn/adapter-state.json').read_text())
        self.assertEqual(state['stages'], {'design': 'completed', 'script': 'completed'})
        self.assertEqual(state['source_mode'], 'pristine_copy_external_launcher')
        if REPO_CONFIG.get('source_lock'):
            self.assertFalse((Path(state['source_dir']) / '.git').exists())
        for phase in ('prepared', 'before_generation', 'after_generation', 'closed'):
            integrity = json.loads((run / 'trace' / f'source-integrity-ai4vn-{phase}.json').read_text())
            self.assertTrue(integrity['unchanged'])
            self.assertEqual(integrity['source_files_verified'], len(before_source))
        self.assertEqual(before_source, {relative: hashlib.sha256((REPO / relative).read_bytes()).hexdigest()
                                         for relative in before_source})
        self.assertEqual(before_source, {relative: hashlib.sha256((Path(state['source_dir']) / relative).read_bytes()).hexdigest()
                                         for relative in before_source})
        receipt_files = list((run / 'trace').glob('received-ai4vn-*.json'))
        self.assertEqual(len(receipt_files), 1)
        receipt = json.loads(receipt_files[0].read_text())
        self.assertEqual(receipt['received_task'], shared)
        self.assertEqual(receipt['boundary'], 'native_requirements_reader')
        self.assertEqual((run / 'shared_task.txt').read_bytes(), (bundle / 'shared_task.txt').read_bytes())
        self.assertEqual((run / 'export/provided_prefix.txt').read_bytes(), (bundle / 'opening.txt').read_bytes())
        segments = [json.loads(line) for line in (run / 'export/generated.jsonl').read_text().splitlines()]
        choices = json.loads((run / 'export/choices.json').read_text())
        self.assertEqual(segments[0]['text'], marker + '生成正文。')
        self.assertEqual(len(choices), 2)
        self.assertTrue(validate_source_map(run, segments)[0])
        audit = json.loads((run / 'audit.json').read_text())
        for field in ('external_input_equal', 'task_entry_request_contains_shared', 'first_prose_request_observed',
                      'call_context_valid', 'usage_coverage_complete', 'configured_model_matches_requests', 'source_mapping_valid'):
            self.assertTrue(audit[field], field)
        self.assertEqual(audit['task_entry_shared_occurrences'], 1)
        self.assertFalse(audit['missing_response_files'])
        self.assertFalse(audit['trace_parse_errors'])
        self.assertFalse(fixture['errors'])
        self.assertEqual(fixture['actor_calls'], 1)
        self.assertEqual(audit['observed_http_calls'], len(fixture['calls']))
        self.assertEqual(int((run / 'trace/ai4vn-call-count').read_text()), len(fixture['calls']))
        self.assertLessEqual(len(fixture['calls']), common_config['max_calls'])
        self.assertTrue(all(request['model'] == common_config['model'] for request in fixture['calls']))
        self.assertTrue(all(request['max_tokens'] <= common_config['max_output_tokens'] for request in fixture['calls']))
        self.assertTrue(all(len(json.dumps(request, ensure_ascii=False, separators=(',', ':'))) <= common_config['max_input_chars']
                            for request in fixture['calls']))
        before_resume = {str(path.relative_to(run)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in run.rglob('*') if path.is_file()}
        calls_before_resume = len(fixture['calls'])
        with patch.object(adapter, 'prepare', side_effect=AssertionError('unexpected prepare')), \
             patch.object(adapter, 'generate_first_artifact', side_effect=AssertionError('unexpected generation')), \
             patch.object(adapter, 'export_first_artifact', side_effect=AssertionError('unexpected export')), \
             patch.object(adapter, 'close', side_effect=AssertionError('unexpected close')):
            self.assertEqual(resume_export(run, adapter), manifest)
        self.assertEqual(len(fixture['calls']), calls_before_resume)
        self.assertEqual(before_resume, {str(path.relative_to(run)): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in run.rglob('*') if path.is_file()})
        self.assertEqual(verify_saved_run(run), manifest)
        report = {'evidence_kind': 'mock', 'implementation': 'external_launcher_pristine_source',
                  'entrypoint': 'root_run_once_injected_adapter', 'run': marker,
                  'native_cli_stages': state['stages'], 'observed_http_calls': len(fixture['calls']),
                  'native_source_files_unchanged': len(before_source), 'source_lock': REPO_CONFIG.get('source_lock'),
                  'shared_task_file': str(bundle / 'shared_task.txt'),
                  'shared_sha256': hashlib.sha256(shared.encode()).hexdigest(),
                  'opening_sha256': hashlib.sha256(opening.encode()).hexdigest(),
                  'audit_status': manifest['audit_status'], 'native_integration': manifest['native_integration'],
                  'cleanup_status': manifest['cleanup_status'], 'sealed_resume_no_dispatch': True,
                  'sealed_evidence_unchanged_after_resume': True, 'external_paid_generation': False}
        (root / 'root-runner-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    unittest.main()
