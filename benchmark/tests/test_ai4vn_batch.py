"""Actual native design/script/render/pygame with local multimodal providers.

rembg's heavyweight segmentation dependency is a declared deterministic test
double. Original Artist._remove_background runs, and native renderer is real.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
from story_benchmark.batch_native.ai4vn import run, preflight, _output_id
from story_benchmark.recording import Recorder, jsonl, verify_recording
from story_benchmark.gateway import ModelGateway, parse_payload
from story_benchmark.batch import load_batch_config, prepare_plan, execute_plan
from test_ai4vn_native_flow import fixture_reply

PYTHON = os.environ.get('AI4VN_TEST_PYTHON', '')


def provider(fixture):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), (90, 140, 190)).save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            payload, parts = parse_payload(raw, self.headers.get('Content-Type', 'application/json'))
            fixture['calls'].append({'path': self.path, 'payload': payload, 'multipart': parts is not None,
                'benchmark_headers': [name for name in self.headers if name.lower().startswith('x-benchmark-')]})
            if '/images/' in self.path:
                # Two independent identical candidates, deliberately testing
                # that local asset reuse cannot erase model output accounting.
                response = {'data': [{'b64_json': encoded}, {'b64_json': encoded}], 'usage': {'total_tokens': 6}}
                if fixture.get('image_failure'):
                    response = {'error': {'type': 'fixture_image_failure', 'message': 'retained native image failure'}}
            else:
                if payload['model'] == 'gpt-5.4-mini':
                    content = json.dumps({'decision': 'REVISE' if fixture.get('reject_images') else 'PASS',
                                          'feedback': 'fixture image rejected' if fixture.get('reject_images') else ''})
                elif '编剧' in payload['messages'][0]['content']:
                    user = payload['messages'][-1]['content']
                    selected = json.loads(user.split('【后续分支选项】\n')[1].split('\n\n【可用角色详情】')[0])
                    if fixture.get('loop_choice') and selected:
                        selected = [{**c, 'target': 'root'} for c in selected]
                    content = '<scene>教室</scene>\n<image id="林">neutral</image>\n<content id="林">本地夹具里的雨声逐渐变轻。</content>\n'
                    content += '<content id="旁白">这段文字只检验原生渲染与真实路径记录。</content>\n'
                    if fixture.get('menu_first') and selected:
                        content = '<scene>教室</scene>\n<image id="林">neutral</image>\n'
                    if selected:
                        content += '[CHOICE]\n' + '\n'.join('<choice target="' + c['target'] + '">' + c['text'] + '</choice>' for c in selected)
                else:
                    content = fixture_reply(payload, fixture)
                    if fixture.get('two_choices') and 'Step2 的 story_graph' in str(payload['messages'][-1]['content']):
                        graph = json.loads(content)
                        graph['edges'] = [e for e in graph['edges'] if e['from'] not in ('node3', 'node4')]
                        graph['edges'] += [{'from': 'node3', 'to': 'node4', 'choice_text': '继续左路'},
                                           {'from': 'node3', 'to': 'node5', 'choice_text': '继续右路'},
                                           {'from': 'node4', 'to': 'node6', 'choice_text': None}]
                        content = json.dumps(graph, ensure_ascii=False)
                response = {'id': 'local-response', 'model': payload['model'], 'choices': [{'index': 0,
                    'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
            raw = json.dumps(response, ensure_ascii=False).encode()
            self.send_response(400 if '/images/' in self.path and fixture.get('image_failure') else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('x-request-id', 'local-' + str(len(fixture['calls'])))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(bool(PYTHON) and Path(PYTHON).is_file(), 'set AI4VN_TEST_PYTHON to opt in to native CLI fixtures')
class BatchAI4Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.environ.get('AI4VN_BATCH_EVIDENCE_DIR', self.temporary.name)).resolve() / self._testMethodName
        self.root.mkdir(parents=True)
        self.fixture = {'marker': 'MULTIMODAL_FIXTURE', 'lock': threading.Lock(), 'actor_requested': False,
                        'actor_calls': 0, 'calls': [], 'errors': []}
        self.server = provider(self.fixture)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.wrapper = self.root / 'native-python-fixture'
        self.wrapper.write_text('#!' + PYTHON + '\n' + '''import sys,types,runpy,importlib.machinery
stub=types.ModuleType('rembg')
stub.__spec__=importlib.machinery.ModuleSpec('rembg',loader=None)
stub.new_session=lambda *a,**k: object()
stub.remove=lambda image,*a,**k: image.convert('RGBA')
sys.modules['rembg']=stub
if sys.argv[1]=='-c': exec(sys.argv[2])
else:
 path=sys.argv.pop(1)
 runpy.run_path(path,run_name='__main__')
''')
        self.wrapper.chmod(0o755)
        self.config = {'repo_path': str(BENCH.parent / 'systems/AI4VisualNovel'),
                       'source_lock': str(BENCH.parent / 'baseline-lock.json'), 'python_executable': str(self.wrapper),
                       'model': 'fixture-model', 'image_model': 'gpt-image-2', 'vision_model': 'gpt-5.4-mini',
                       'budget_mode': 'unlimited', 'timeout_seconds': None, 'max_calls': None,
                       'max_output_tokens': None, 'max_input_chars': None}
        self.bundle = Path(os.environ.get('BENCH_SHARED_BUNDLE', str(BENCH / 'examples/CAMPUS-01-V4')))
        self.policy = {'window_chars': 90, 'choice_indices': [1], 'reading_delay_seconds': 0,
                       'evidence_kind': 'fixture', 'render_mode': 'offscreen_native'}

    def execute(self):
        root = self.root / 'run'
        recorder = Recorder(root, self._testMethodName, self.policy['window_chars'])
        providers = {role: {'model': model, 'base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
                            'api_key_env': 'AI4_BATCH_LOCAL_KEY'} for role, model in
                     [('text', 'fixture-model'), ('vision', 'gpt-5.4-mini'), ('image', 'gpt-image-2')]}
        with patch.dict(os.environ, {'AI4_BATCH_LOCAL_KEY': 'local-fixture-provider-key'}):
            gateway = ModelGateway(recorder, providers).start()
            try:
                result = run(self.config, self.bundle, root, recorder, gateway, self.policy)
            finally:
                gateway.close()
        (self.root / 'fixture-result.json').write_text(json.dumps({'result': result, 'evidence_kind': 'fixture',
            'native_source_modified': False, 'rembg_dependency': 'deterministic_test_double', 'paid_calls': 0}, ensure_ascii=False, indent=2))
        return root, result

    def test_full_native_multimodal_choice_and_renderer(self):
        checked = preflight(self.config, self.bundle, self.policy)
        self.assertTrue(checked['ok'], checked)
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'reading_window', result)
        story = jsonl(root / 'trajectories/main/story.jsonl')
        choices = jsonl(root / 'trajectories/main/choices.jsonl')
        self.assertTrue(story)
        self.assertEqual(len(choices), 1)
        self.assertTrue(any(s['native_id'] == 'node2' for s in story))
        self.assertFalse(any(s['native_id'] == 'node1' for s in story))
        frames = jsonl(root / 'visuals/frame_map.jsonl')
        self.assertEqual(len(frames), len(story) + len(choices))
        self.assertEqual(sum(bool(f['segment_ids']) for f in frames), len(story))
        self.assertTrue(all(f['clean_file'] and f['ui_file'] for f in frames))
        self.assertTrue(all(f['asset_ids'] for f in frames))
        self.assertTrue(any(f['candidate_character_ids'] for f in frames))
        calls = jsonl(root / 'telemetry/calls.jsonl')
        self.assertEqual({c['role'] for c in calls}, {'text', 'vision', 'image'})
        self.assertTrue(all(c['native_stage'] in ('design', 'script', 'render') for c in calls))
        self.assertTrue(any(c['native_task_id'] == 'root' and c['native_operation_id'].startswith('WriterAgent.synthesize_script:') for c in calls))
        self.assertFalse(any(c['benchmark_headers'] for c in self.fixture['calls']))
        images = jsonl(root / 'images/requests.jsonl')
        outputs = jsonl(root / 'images/outputs.jsonl')
        self.assertGreaterEqual(len(images), 5)
        self.assertEqual(len(outputs), 2 * len(images))
        self.assertEqual(len({o['output_id'] for o in outputs}), len(outputs))
        self.assertTrue(any(c['multipart'] for c in self.fixture['calls']))
        assets = jsonl(root / 'images/assets.jsonl')
        self.assertTrue(any(a['origin'] == 'derived' and a['parent_asset_id'] for a in assets))
        self.assertTrue(any(a['output_id'] for a in assets), assets)
        self.assertTrue(all(a['output_id'] for a in assets if a['origin'] == 'generated'), assets)
        self.assertTrue(jsonl(root / 'characters/versions.jsonl'))
        self.assertTrue(all(s['source_call_ids'] for s in story))
        events = jsonl(root / 'telemetry/events.jsonl')
        early = [e for e in events if e['event_type'] == 'native_story_unit_available']
        self.assertTrue(any(e['native_id'] == 'root' and e['readable_text'] for e in early))
        self.assertTrue(all(e['observer'] == 'adapter_backend' for e in early))
        for segment in story:
            source = segment['native_source']
            original = (root / source['original_file']).read_text().splitlines()[source['line'] - 1]
            self.assertIn(segment['text'], original)
        self.assertFalse(any(e['event_type'] in ('story_text_presented', 'frame_presented') for e in events))
        self.assertTrue(all(e['frame_id'] for e in events if e['event_type'] == 'offscreen_frame_observed'))
        receipts = [e for e in jsonl(root / 'native/ai4vn/observations.jsonl') if e['kind'] == 'received_input']
        self.assertEqual(receipts[0]['received_task'], (self.bundle / 'shared_task.txt').read_text())
        first = self.fixture['calls'][0]['payload']
        self.assertEqual(sum(m['content'].count(receipts[0]['received_task']) for m in first['messages']), 1)
        source = json.loads((root / 'trace/source-integrity-ai4vn-closed.json').read_text())
        self.assertTrue(source['unchanged'])
        self.assertEqual(source['source_files_verified'], 35)

    def test_candidate_identity_survives_pending_download_and_rejects_wrong_bytes(self):
        class Gateway:
            candidate = {'output_id': 'call-a-candidate-0', 'file_sha256': None}
            def output_for_call(self, call_id, index=0):
                return self.candidate if call_id == 'call-a' and index == 0 else None
        gateway = Gateway()
        self.assertEqual(_output_id(gateway, 'native-bytes', 'call-a', 0), 'call-a-candidate-0')
        self.assertIsNone(_output_id(gateway, 'native-bytes', 'call-b', 0))
        self.assertIsNone(_output_id(gateway, 'native-bytes', 'call-a', 1))
        gateway.candidate['file_sha256'] = 'different-bytes'
        self.assertIsNone(_output_id(gateway, 'native-bytes', 'call-a', 0))

    def test_two_os_workers_seal_independent_paths_and_resume_without_calls(self):
        specific = {key: self.config[key] for key in ('repo_path', 'source_lock', 'python_executable')}
        providers = {role: {'model': model, 'base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
                            'api_key_env': 'AI4_BATCH_LOCAL_KEY'} for role, model in
                     [('text', 'fixture-model'), ('vision', 'gpt-5.4-mini'), ('image', 'gpt-image-2')]}
        config_path = self.root / 'batch-fixture.json'
        config_path.write_text(json.dumps({'schema_version': 'batch.1', 'evidence_kind': 'fixture',
            'allow_pilot': True, 'providers': providers,
            'systems': {'ai4visualnovel': specific, 'if_line': {}, 'infiplot': {}},
            'bundles': [str(self.bundle.resolve())], 'choice_indices': [1],
            'reading_delay_seconds': 0, 'render_mode': 'offscreen_native'}))
        config = load_batch_config(config_path)
        out = self.root / 'batch'
        plan = prepare_plan('ai4visualnovel', config, out, count=2)
        with patch.dict(os.environ, {'AI4_BATCH_LOCAL_KEY': 'local-fixture-provider-key'}):
            result = execute_plan(out, concurrency=2)
            self.assertEqual(len(result['runs']), 2)
            self.assertTrue(all(row['state'] == 'sealed' for row in result['runs']), result)
            sessions = [json.loads(path.read_text()) for path in (out / 'scheduling').glob('*.json')]
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session['requested_concurrency'], 2)
            self.assertEqual(session['worker_capacity'], 2)
            self.assertEqual({worker['run_id'] for worker in session['workers']}, {job['run_id'] for job in plan['jobs']})
            self.assertTrue(all(worker['worker_started_at'] < worker['worker_finished_at'] for worker in session['workers']))
            self.assertLess(max(worker['worker_started_at'] for worker in session['workers']),
                            min(worker['worker_finished_at'] for worker in session['workers']))
            pids, clocks, call_sets, manifests = [], [], [], []
            for job in plan['jobs']:
                root = out / 'runs' / job['run_id']
                self.assertTrue(verify_recording(root)['ok'])
                manifest = json.loads((root / 'manifest.json').read_text())
                self.assertEqual(manifest['evidence_kind'], 'fixture')
                self.assertEqual(manifest['input_audit']['status'], 'passed', manifest)
                self.assertEqual(manifest['native_text_source_audit'], 'passed', manifest)
                self.assertEqual(manifest['stop_reason'], 'native_end', manifest)
                self.assertTrue(manifest['adapter_unchanged_during_run'])
                self.assertTrue(all(manifest['source_verification'].values()))
                context = manifest['scheduling_context']
                self.assertEqual(context['session_id'], session['session_id'])
                self.assertEqual(context['requested_concurrency'], 2)
                self.assertEqual(context['worker_capacity'], 2)
                self.assertEqual(context, json.loads((root / 'scheduling_context.json').read_text()))
                events = jsonl(root / 'native/ai4vn/observations.jsonl')
                pids.append({e['native_pid'] for e in events})
                clocks.append({e['clock_id'] for e in jsonl(root / 'telemetry/events.jsonl')})
                call_sets.append({c['call_id'] for c in jsonl(root / 'telemetry/calls.jsonl')})
                story = jsonl(root / 'trajectories/main/story.jsonl')
                nodes = {s['native_id'] for s in story}
                self.assertIn('node2', nodes)
                self.assertNotIn('node1', nodes)
                for segment in story:
                    source = segment['native_source']
                    original = (root / source['original_file']).read_text().splitlines()[source['line'] - 1]
                    self.assertIn(segment['text'], original)
                integrity = json.loads((root / 'trace/source-integrity-ai4vn-closed.json').read_text())
                self.assertTrue(integrity['unchanged'])
                self.assertEqual(integrity['source_files_verified'], 35)
                manifests.append(hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest())
            self.assertTrue(pids[0] and pids[1] and pids[0].isdisjoint(pids[1]))
            self.assertTrue(clocks[0].isdisjoint(clocks[1]))
            self.assertTrue(call_sets[0] and call_sets[1] and call_sets[0].isdisjoint(call_sets[1]))
            calls_before = len(self.fixture['calls'])
            resumed = execute_plan(out, concurrency=2)
            self.assertEqual(len(self.fixture['calls']), calls_before)
            self.assertEqual(result, resumed)
            sessions_after = [json.loads(path.read_text()) for path in (out / 'scheduling').glob('*.json')]
            self.assertEqual(len(sessions_after), 2)
            resume_session = next(s for s in sessions_after if s['session_id'] != session['session_id'])
            self.assertEqual(resume_session['workers'], [])
            self.assertEqual(resume_session['requested_concurrency'], 2)
            for job, expected in zip(plan['jobs'], manifests):
                root = out / 'runs' / job['run_id']
                self.assertEqual(hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(), expected)
        (self.root / 'fixture-result.json').write_text(json.dumps({'evidence_kind': 'fixture',
            'native_worker_pids': [sorted(p) for p in pids], 'roots': [j['run_id'] for j in plan['jobs']],
            'http_attempts': len(self.fixture['calls']), 'resume_additional_calls': 0, 'paid_calls': 0,
            'rembg_dependency': 'deterministic_test_double', 'source_files_verified_per_root': 35}, indent=2))

    def test_original_design_failure_stops_before_images(self):
        self.fixture['outline_response'] = ''
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'native_error')
        self.assertEqual(len(self.fixture['calls']), 1)
        self.assertEqual(jsonl(root / 'images/outputs.jsonl'), [])
        self.assertEqual(jsonl(root / 'trajectories/main/story.jsonl'), [])
        self.assertTrue(jsonl(root / 'errors.jsonl'))
        self.assertTrue(json.loads((root / 'trace/source-integrity-ai4vn-closed.json').read_text())['unchanged'])

    def test_native_end_repeats_last_choice_policy_without_combining_routes(self):
        self.fixture['two_choices'] = True
        self.policy['window_chars'] = 10000
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'native_end', result)
        choices = jsonl(root / 'trajectories/main/choices.jsonl')
        self.assertEqual(len(choices), 2)
        self.assertTrue(all(c['selected_index'] == 1 for c in choices))
        nodes = {s['native_id'] for s in jsonl(root / 'trajectories/main/story.jsonl')}
        self.assertIn('node2', nodes)
        self.assertIn('node5', nodes)
        self.assertNotIn('node1', nodes)
        self.assertNotIn('node4', nodes)

    def test_native_body_loop_uses_common_window_without_adapter_visit_limit(self):
        self.fixture['loop_choice'] = True
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'reading_window', result)
        self.assertFalse(result['errors'])
        story = jsonl(root / 'trajectories/main/story.jsonl')
        self.assertEqual({s['native_id'] for s in story}, {'root'})
        self.assertGreaterEqual(len(jsonl(root / 'trajectories/main/choices.jsonl')), 2)
        self.assertGreaterEqual(sum(len(s['text']) for s in story), self.policy['window_chars'])

    def test_first_native_menu_is_drawn_without_fabricating_body(self):
        self.fixture['menu_first'] = True
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'reading_window', result)
        self.assertFalse(result['errors'])
        story = jsonl(root / 'trajectories/main/story.jsonl')
        choices = jsonl(root / 'trajectories/main/choices.jsonl')
        frames = jsonl(root / 'visuals/frame_map.jsonl')
        self.assertEqual(story[0]['native_id'], 'node2')
        self.assertNotIn('root', {s['native_id'] for s in story})
        self.assertIsNone(choices[0]['after_segment_id'])
        self.assertTrue(choices[0]['selection_executed'])
        self.assertEqual(choices[0]['selected_index'], 1)
        first = frames[0]
        self.assertEqual(first['segment_ids'], [])
        self.assertTrue(first['observed_empty_body'])
        self.assertEqual(first['native_source']['native_ui_kind'], 'choice_menu')
        self.assertTrue(first['asset_ids'])
        self.assertEqual(first['status'], 'ready')
        from PIL import Image, ImageChops
        with Image.open(root / first['ui_file']) as ui, Image.open(root / first['clean_file']) as clean:
            self.assertIsNotNone(ImageChops.difference(ui.convert('RGB'), clean.convert('RGB')).getbbox())
        events = jsonl(root / 'telemetry/events.jsonl')
        observed = next(e for e in events if e['event_type'] == 'offscreen_frame_observed')
        self.assertEqual(observed['frame_id'], first['frame_id'])
        self.assertEqual(observed['segment_ids'], [])
        self.assertFalse(observed['desktop_presented'])
        available = next(e for e in events if e['event_type'] == 'choice_available')
        selected = next(e for e in events if e['event_type'] == 'choice_selected')
        self.assertEqual(available['frame_id'], first['frame_id'])
        self.assertLess(observed['monotonic_ns'], available['monotonic_ns'])
        self.assertLess(available['monotonic_ns'], selected['monotonic_ns'])

    def test_native_image_failure_keeps_attempts_and_missing_frames(self):
        self.fixture['image_failure'] = True
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'reading_window', result)
        self.assertIn('native_image_generation_failed', {e['code'] for e in result['errors']})
        self.assertEqual(jsonl(root / 'images/outputs.jsonl'), [])
        requests = jsonl(root / 'images/requests.jsonl')
        self.assertTrue(requests)
        self.assertTrue(all(r['status_code'] == 400 and r['returned_count'] == 0 for r in requests))
        self.assertTrue(all(f['status'] == 'placeholder' for f in jsonl(root / 'visuals/frame_map.jsonl')))

    def test_last_native_rejected_image_is_retained_as_rejected_but_used(self):
        self.fixture['reject_images'] = True
        root, result = self.execute()
        self.assertEqual(result['stop_reason'], 'reading_window', result)
        self.assertIn('native_image_review_exhausted_used_last', {e['code'] for e in result['errors']})
        selections = jsonl(root / 'images/native_selections.jsonl')
        self.assertEqual(len(selections), 3)
        self.assertTrue(all(s['rejected_but_used'] and s['asset_id'] for s in selections))
        reviews = jsonl(root / 'images/native_reviews.jsonl')
        self.assertEqual(len(reviews), 9)
        self.assertTrue(all(r['native_decision'] == 'REVISE' for r in reviews))
        assets = {a['asset_id']: a for a in jsonl(root / 'images/assets.jsonl')}
        self.assertTrue(all(assets[r['asset_id']]['native_source']['file'] == r['native_path'] for r in reviews))


if __name__ == '__main__':
    unittest.main()
