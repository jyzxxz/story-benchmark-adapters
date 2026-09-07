import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

BENCH = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get('AI4VN_TEST_REPO', str(BENCH.parent.parent / 'pristine/AI4VisualNovel' if (BENCH.parent.parent / 'pristine/AI4VisualNovel').is_dir() else BENCH.parent / 'systems/AI4VisualNovel'))).resolve()
SHIM = BENCH / 'native_shims/ai4vn'
sys.path.insert(0, str(BENCH))
REPO_CONFIG = {'repo_path': str(REPO)}
if os.getenv('AI4VN_TEST_SOURCE_LOCK'):
    REPO_CONFIG['source_lock'] = os.environ['AI4VN_TEST_SOURCE_LOCK']
elif (REPO.parent.parent / 'baseline-lock.json').is_file():
    REPO_CONFIG['source_lock'] = str(REPO.parent.parent / 'baseline-lock.json')
elif (BENCH.parent / 'baseline-lock.json').is_file():
    REPO_CONFIG['source_lock'] = str(BENCH.parent / 'baseline-lock.json')
else:
    REPO_CONFIG['expected_commit'] = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
SPEC = importlib.util.spec_from_file_location('ai4vn_under_test', BENCH / 'story_benchmark/adapters/ai4vn.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)
NATIVE_PYTHON = os.environ.get('AI4VN_TEST_PYTHON', sys.executable)


class ExportTests(unittest.TestCase):
    def extract(self, text, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            story = Path(directory) / 'story.txt'
            story.write_text(text, encoding='utf-8')
            return ADAPTER.extract_first_visible(REPO, story, {'characters': [{'id': 'lin', 'name': '林'}]}, **kwargs)

    def test_v3_choice_only_is_valid_without_inventing_prose(self):
        text = ('=== Node: root ===\n<jump target="decision"/>\n=== Node: decision ===\n'
                '[CHOICE]\n<choice target="a">进入实验楼</choice>\n'
                '<choice target="b">先保护周遥并检查收音机</choice>')
        export = self.extract(text, allow_empty_body=True)
        self.assertEqual(export['segments'], [])
        self.assertEqual(export['generation_issue_codes'], [])
        self.assertEqual(export['stop_reason'], 'first_choice')
        self.assertEqual(export['export_scope'], 'first_unselected_choice')
        self.assertEqual(export['native_capability_status'], 'first_unselected_choice_available')
        self.assertEqual(export['native_step']['native_pointer']['node_id'], 'decision')
        self.assertEqual(export['prechoice_body_status'], 'empty_native')
        self.assertIs(export['selection_executed'], False)
        self.assertIn('empty_prose', self.extract(text)['generation_issue_codes'])

    def test_missing_choice_is_explicit_even_when_body_is_allowed_empty(self):
        for text in ('', '=== Node: root ===\n<content id="旁白">故事停在节点结尾。</content>'):
            with self.subTest(text=text):
                export = self.extract(text, allow_empty_body=True)
                self.assertIn('native_choice_boundary_missing', export['generation_issue_codes'])
                self.assertEqual(export['native_capability_status'], 'boundary_not_reached')
                self.assertFalse(export['first_choice_reached'])

    def test_first_choice_no_other_branch(self):
        result = self.extract('''=== Node: root ===
<scene>教室</scene>
<content id="旁白">原始正文。</content>
<content id="lin">别走。</content>
[CHOICE]
<choice target="node1">追查</choice>
<choice target="node2">等待</choice>
=== Node: node1 ===
<content id="旁白">另一分支不导出。</content>''')
        self.assertEqual([s['text'] for s in result['segments']], ['原始正文。', '别走。'])
        self.assertEqual(result['segments'][1]['speaker'], '林')
        self.assertEqual(result['segments'][0]['native_pointer']['line'], 3)
        self.assertEqual(len(result['choices']), 2)
        self.assertEqual(result['stop_reason'], 'first_choice')

    def test_conditions_remain_explicit_unsupported_boundaries(self):
        for control in ('[IF: 林 >= 1]', '[ELSE]', '[ENDIF]'):
            result = self.extract(f'=== Node: root ===\n<content id="旁白">之前。</content>\n{control}\n<content id="旁白">之后。</content>')
            self.assertEqual(len(result['segments']), 1)
            self.assertIn('control_flow_not_executed', result['generation_issue_codes'])
            self.assertIn('unsupported_native_condition_boundary', result['generation_issue_codes'])
            self.assertEqual(result['stop_reason'], 'unsupported_condition_boundary')

    def test_automatic_jumps_reach_first_choice_with_original_node_pointers(self):
        result = self.extract('''=== Node: root ===
<content id="旁白">门外等待。</content>
<jump target="intro"/>
<content id="旁白">跳转后不可达。</content>
=== Node: intro ===
<scene>门廊</scene>
<content id="lin">我还没有决定。</content>
<jump target="decision"/>
=== Node: decision ===
<content id="旁白">两条路就在眼前。</content>
[CHOICE]
<choice target="left">进入实验楼</choice>
<choice target="right">保护周遥并检查收音机</choice>
=== Node: left ===
<content id="旁白">尚未选择的分支。</content>
=== Node: orphan ===
<content id="旁白">文件顺序不是实际路径。</content>''')
        self.assertEqual([segment['text'] for segment in result['segments']],
                         ['门外等待。', '我还没有决定。', '两条路就在眼前。'])
        self.assertEqual([segment['native_pointer']['line'] for segment in result['segments']], [2, 7, 10])
        self.assertEqual([segment['native_pointer']['node_id'] for segment in result['segments']], ['root', 'intro', 'decision'])
        self.assertEqual(result['traversed_node_ids'], ['root', 'intro', 'decision'])
        self.assertEqual([(transition['from'], transition['to']) for transition in result['automatic_transitions']],
                         [('root', 'intro'), ('intro', 'decision')])
        self.assertEqual([choice['native_pointer']['line'] for choice in result['choices']], [12, 13])
        self.assertTrue(result['first_choice_reached'])
        self.assertIs(result['selection_executed'], False)
        self.assertEqual(result['stop_reason'], 'first_choice')

    def test_missing_and_cyclic_automatic_jumps_fail_without_guessing_route(self):
        for text, code in (
            ('=== Node: root ===\n<jump target="missing"/>', 'native_jump_target_missing'),
            ('=== Node: root ===\n<jump target="root"/>', 'native_automatic_jump_cycle'),
            ('=== Node: root ===\n<jump target="next"/>\n=== Node: next ===\n<jump target="root"/>', 'native_automatic_jump_cycle'),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, code):
                self.extract(text)

    def test_jump_failures_carry_known_native_codes(self):
        for text, code in (('=== Node: root ===\n<jump target="root"/>', 'native_automatic_jump_cycle'),
                           ('=== Node: root ===\n<jump target="missing"/>', 'native_jump_target_missing')):
            with self.subTest(code=code), self.assertRaises(ADAPTER.AI4VNError) as raised:
                self.extract(text)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.category, 'native_error')

    def test_node_end_does_not_infer_transition_from_file_order(self):
        result = self.extract('''=== Node: root ===
<content id="旁白">本节点结束。</content>
=== Node: next ===
<choice target="a">A</choice>
<choice target="b">B</choice>''')
        self.assertEqual(result['traversed_node_ids'], ['root'])
        self.assertFalse(result['first_choice_reached'])
        self.assertEqual(result['choices'], [])
        self.assertEqual(result['stop_reason'], 'entry_node_end')

    def test_empty_and_missing_root_do_not_use_summary(self):
        self.assertEqual(self.extract('=== Node: node1 ===\n<content id="旁白">x</content>')['segments'], [])
        self.assertIn('empty_prose', self.extract('=== Node: root ===\n<scene>summary</scene>')['generation_issue_codes'])

    def test_native_ignored_choices_plural(self):
        result = self.extract('=== Node: root ===\n<content id="旁白">正文</content>\n[CHOICES]\n<choice target="a">A</choice>\n<choice target="b">B</choice>')
        self.assertEqual(len(result['choices']), 2)

    def test_duplicate_node_follows_native_last_node(self):
        result = self.extract('=== Node: root ===\n<content id="旁白">old</content>\n=== Node: root ===\n<content id="旁白">new</content>')
        self.assertEqual([s['text'] for s in result['segments']], ['new'])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / 'bundle'
        (self.bundle / 'payloads').mkdir(parents=True)
        (self.bundle / 'shared_task.txt').write_text('共同任务\n固定开头')
        (self.bundle / 'opening.txt').write_text('固定开头')
        (self.bundle / 'case.json').write_text('{}')
        (self.bundle / 'payloads/requirements.txt').write_text('共同任务\n固定开头')
        (self.bundle / 'payloads/ai4vn.json').write_text('{"character_count":3}')
        self.adapter = ADAPTER.AI4VNAdapter({**REPO_CONFIG, 'python_executable': NATIVE_PYTHON})

    def test_missing_input_and_mismatch_fail(self):
        self.assertTrue(self.adapter.preflight(self.bundle)['ok'])
        (self.bundle / 'payloads/requirements.txt').write_text('修改')
        self.assertFalse(self.adapter.preflight(self.bundle)['ok'])
        (self.bundle / 'shared_task.txt').unlink()
        self.assertFalse(self.adapter.preflight(self.bundle)['ok'])

    def test_two_runs_isolated_and_overwrite_refused(self):
        a = self.adapter.prepare(self.bundle, self.root / 'a')
        b = self.adapter.prepare(self.bundle, self.root / 'b')
        self.assertNotEqual(a['native_dir'], b['native_dir'])
        with self.assertRaises(FileExistsError):
            self.adapter.prepare(self.bundle, self.root / 'a')
        with self.assertRaisesRegex(RuntimeError, 'live_generation_not_enabled'):
            self.adapter.generate_first_artifact(a)

    def test_resume_running_does_not_send(self):
        handle = self.adapter.prepare(self.bundle, self.root / 'a')
        handle['stages']['design'] = 'running'
        with patch.object(ADAPTER.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'delivery_unknown'):
                self.adapter._run_stage(handle, 'design', ['irrelevant'])
            spawn.assert_not_called()

    def test_successful_exit_still_requires_artifact(self):
        handle = self.adapter.prepare(self.bundle, self.root / 'a')
        self.adapter.config['live'] = True
        with patch.object(self.adapter, '_run_stage'):
            with self.assertRaisesRegex(RuntimeError, 'native_artifact_missing'):
                self.adapter.generate_first_artifact(handle)

    def test_source_lock_without_git_and_source_drift_detection(self):
        snapshot = self.root / 'without-git'
        snapshot.mkdir()
        hashes = {}
        for relative in self.adapter._source_files():
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
            hashes[relative] = ADAPTER.hashlib.sha256(target.read_bytes()).hexdigest()
        lock = self.root / 'baseline-lock.json'
        lock.write_text(json.dumps({'systems': {'ai4vn': {'base_commit': ADAPTER.BASELINE,
            'adapted_commit': ADAPTER.BASELINE, 'files': hashes}}}))
        adapter = ADAPTER.AI4VNAdapter({'repo_path': str(snapshot), 'source_lock': str(lock), 'python_executable': NATIVE_PYTHON})
        self.assertTrue(adapter.preflight(self.bundle)['ok'])
        handle = adapter.prepare(self.bundle, self.root / 'locked-run')
        self.assertFalse((Path(handle['source_dir']) / '.git').exists())
        (Path(handle['source_dir']) / 'main.py').write_text('changed fixture')
        with self.assertRaisesRegex(RuntimeError, 'native_source_changed'):
            adapter._verify_sources(handle, 'test_changed')
        self.assertEqual(ADAPTER.hashlib.sha256((snapshot / 'main.py').read_bytes()).hexdigest(), hashes['main.py'])

    def test_timeout_and_nonzero_keep_evidence(self):
        for command, expected in (([sys.executable, '-c', 'import time;time.sleep(5)'], 'delivery_unknown'),
                                  ([sys.executable, '-c', 'raise SystemExit(3)'], 'failed')):
            handle = self.adapter.prepare(self.bundle, self.root / expected)
            self.adapter.config['timeout_seconds'] = 0.15
            with patch.object(self.adapter, '_env', return_value=dict(os.environ)):
                with self.assertRaises(RuntimeError):
                    self.adapter._run_stage(handle, 'design', command)
            self.assertEqual(handle['stages']['design'], expected)
            self.assertTrue((Path(handle['native_dir']) / 'design.stdout.log').exists())

    def test_live_preflight_requires_budget_model_and_auth(self):
        self.adapter.config.update(live=True, api_key_env='TEST_MISSING_BENCHMARK_KEY')
        report = self.adapter.preflight(self.bundle)
        self.assertFalse(report['ok'])
        self.assertIn('model_not_frozen', report['errors'])
        self.assertIn('missing_positive_budget:max_calls', report['errors'])

    def test_native_node_count_failure_retains_exit_and_never_retries(self):
        handle = self.adapter.prepare(self.bundle, self.root / 'node-count-failure')
        self.adapter.config['timeout_seconds'] = 5
        command = [NATIVE_PYTHON, '-c', 'raise ValueError("story_graph 节点数不匹配，期望 12，实际 13")']
        with patch.object(self.adapter, '_env', return_value=dict(os.environ)):
            with self.assertRaisesRegex(RuntimeError, 'native_graph_node_count_mismatch:expected=12:actual=13:native_exit=1'):
                self.adapter._run_stage(handle, 'design', command)
        diagnostic = json.loads((Path(handle['native_dir']) / 'native_failure.json').read_text())
        self.assertEqual(diagnostic['native_exit_code'], 1)
        self.assertEqual(diagnostic['expected_nodes'], 12)
        self.assertEqual(diagnostic['actual_nodes'], 13)
        self.assertFalse(diagnostic['automatic_retry'])
        self.assertTrue(diagnostic['failing_candidate_rejected_before_producer_review'])
        self.assertFalse(diagnostic['prior_graph_review_observed_in_log'])
        self.assertFalse(diagnostic['native_script_started'])
        self.assertEqual(handle['stages'], {'design': 'failed'})
        self.assertIn('ValueError: story_graph 节点数不匹配，期望 12，实际 13',
                      (Path(handle['native_dir']) / 'design.stdout.log').read_text())
        with patch.object(ADAPTER.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ADAPTER.NativeGraphNodeCountError, 'previous_native_failure_no_resend'):
                self.adapter._run_stage(handle, 'design', command)
            spawn.assert_not_called()

    def test_known_parse_schema_and_exit_errors_are_not_delivery_unknown(self):
        cases = [('import json;json.loads("invalid")', 'native_json_parse_error'),
                 ('raise ValueError("game_outline Schema 校验失败: fixture")', 'native_schema_validation_error'),
                 ('raise SystemExit(7)', 'native_exit')]
        for code, expected in cases:
            handle = self.adapter.prepare(self.bundle, self.root / expected)
            self.adapter.config['timeout_seconds'] = 5
            with patch.object(self.adapter, '_env', return_value=dict(os.environ)), self.assertRaises(ADAPTER.AI4VNError) as raised:
                self.adapter._run_stage(handle, 'design', [NATIVE_PYTHON, '-c', code])
            self.assertEqual(raised.exception.code, expected)
            self.assertEqual(handle['stages']['design'], 'failed')
            diagnostic = json.loads((Path(handle['native_dir']) / 'native_failure.json').read_text())
            self.assertEqual(diagnostic['failure_code'], expected)
            self.assertFalse(diagnostic['automatic_retry'])

    def test_incomplete_http_still_reports_unknown_despite_process_exit(self):
        handle = self.adapter.prepare(self.bundle, self.root / 'incomplete-send')
        trace = Path(handle['trace_dir']) / 'ai4vn-fixture.jsonl'
        trace.write_text(json.dumps({'boundary':'http','call_id':'synthetic','event':'started',
                                    'operation_id':'ai4vn.design','start_time':'2026-01-01'}) + '\n')
        self.adapter.config['timeout_seconds'] = 5
        with patch.object(self.adapter, '_env', return_value=dict(os.environ)), self.assertRaises(ADAPTER.AI4VNError) as raised:
            self.adapter._run_stage(handle, 'design', [NATIVE_PYTHON, '-c', 'raise SystemExit(3)'])
        self.assertEqual(raised.exception.code, 'delivery_unknown')
        self.assertEqual(handle['stages']['design'], 'delivery_unknown')

    def test_native_artifact_errors_and_v3_handle_export(self):
        handle = self.adapter.prepare(self.bundle, self.root / 'artifact-errors')
        data = Path(handle['source_dir']) / 'data'
        data.mkdir()
        design = data / 'game_design.json'
        for content, expected in [('', 'native_artifact_empty'), ('{bad', 'native_artifact_parse_error'),
                                  ('[]', 'native_artifact_invalid_shape')]:
            design.write_text(content)
            with self.subTest(expected=expected), self.assertRaises(ADAPTER.AI4VNError) as raised:
                self.adapter.export_first_artifact(handle)
            self.assertEqual(raised.exception.code, expected)
        design.write_text('{"characters":[]}')
        (data / 'story.txt').write_text('=== Node: root ===\n<choice target="a">A</choice>\n<choice target="b">B</choice>')
        handle['output_contract'] = {'version':'3.0','scope':'first_unselected_choice','allow_empty_body':True}
        exported = self.adapter.export_first_artifact(handle)
        self.assertEqual(exported['generation_issue_codes'], [])
        self.assertEqual(exported['segments'], [])
        self.assertEqual(len(exported['choices']), 2)

    def test_root_classifies_native_node_count_failure_as_known_failed_exit(self):
        from story_benchmark.compiler import compile_case
        from story_benchmark.runner import run_once
        bundle = self.root / 'compiled-bundle'
        compile_case(BENCH / 'cases/CAMPUS-01.json', bundle, allow_pilot=True)
        class NativeFailureAdapter(ADAPTER.AI4VNAdapter):
            def generate_first_artifact(self, handle):
                with patch.object(self, '_env', return_value=dict(os.environ)):
                    self._run_stage(handle, 'design', [NATIVE_PYTHON, '-c',
                        'raise ValueError("story_graph 节点数不匹配，期望 12，实际 13")'])
        adapter = NativeFailureAdapter({**REPO_CONFIG, 'python_executable': NATIVE_PYTHON, 'timeout_seconds': 5})
        run = self.root / 'known-native-failure'
        with self.assertRaises(ADAPTER.NativeGraphNodeCountError):
            run_once('ai4visualnovel', bundle, {}, run, adapter=adapter, mock=True)
        manifest = json.loads((run / 'manifest.json').read_text())
        self.assertEqual(manifest['state'], 'FAILED')
        self.assertEqual(manifest['failure_code'], 'native_graph_node_count_mismatch')
        self.assertEqual(manifest['generation_status'], 'failed')
        self.assertEqual(manifest['cleanup_status'], 'completed')
        self.assertFalse((run / 'operation_result.json').exists())
        self.assertFalse(list((run / 'trace').glob('response-*.json')))


@unittest.skipUnless(Path(NATIVE_PYTHON).exists(), 'native environment unavailable')
class NativeTests(unittest.TestCase):
    def run_native(self, code, env):
        with tempfile.TemporaryDirectory() as source_directory:
            native_root = Path(source_directory)
            adapter = ADAPTER.AI4VNAdapter({**REPO_CONFIG, 'python_executable': NATIVE_PYTHON})
            original_hashes = {}
            for relative in adapter._source_files():
                source = REPO / relative
                target = native_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                original_hashes[relative] = ADAPTER.hashlib.sha256(source.read_bytes()).hexdigest()
            base_env = {**os.environ, 'PYTHONPATH': str(native_root) + os.pathsep + str(SHIM),
                        'PYTHONDONTWRITEBYTECODE': '1', **env, 'BENCH_NATIVE_ROOT': str(native_root)}
            for key in ('OPENAI_API_KEY', 'GOOGLE_API_KEY'):
                base_env[key] = 'offline-test-key'
            boot = 'from launcher import install_sdk_observers\ninstall_sdk_observers()\n'
            result = subprocess.run([NATIVE_PYTHON, '-c', boot + code], cwd=native_root, env=base_env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for relative, digest in original_hashes.items():
                self.assertEqual(ADAPTER.hashlib.sha256((REPO / relative).read_bytes()).hexdigest(), digest)
                self.assertEqual(ADAPTER.hashlib.sha256((native_root / relative).read_bytes()).hexdigest(), digest)
            return result.stdout

    def test_disabled_hook_preserves_sdk_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_native("""
import ai4vn_observer as benchmark_trace
seen={}
def fake(**kwargs):seen.update(kwargs);return 'unchanged'
arguments={'model':'native','messages':[{'role':'user','content':'original'}],'temperature':0.7,'response_format':None}
assert benchmark_trace.sdk_call('openai',fake,arguments)=='unchanged'
assert seen==arguments
assert benchmark_trace.output_cap()=={}
assert benchmark_trace.openai_client_kwargs()=={}
""", {'AI4VN_RUN_DIR': directory, 'BENCH_TRACE_DIR': ''})

    def test_http_budget_uses_compact_unicode_and_keeps_lower_native_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_native("""
import os,json
from pathlib import Path
import httpx
import ai4vn_observer as trace
arguments={'model':'fixture','messages':[{'role':'user','content':'汉字🙂'}],'max_tokens':64}
capped=trace.execution_arguments(arguments)
assert capped['max_tokens']==64 and arguments['max_tokens']==64
assert trace.execution_arguments({'model':'fixture'})['max_tokens']==128
assert trace.execution_arguments({'max_completion_tokens':32})['max_completion_tokens']==32
size=len(json.dumps(capped,ensure_ascii=False,separators=(',',':')))
os.environ['BENCH_MAX_INPUT_CHARS']=str(size)
def request():return httpx.Request('POST','http://127.0.0.1/fixture',content=json.dumps(capped,ensure_ascii=False,indent=4).encode())
trace.http_request(request())
os.environ['BENCH_MAX_INPUT_CHARS']=str(size-1)
try:trace.http_request(request())
except RuntimeError as error:assert str(error)=='benchmark_input_budget_exhausted'
else:raise AssertionError('input cap missing')
assert (Path(os.environ['BENCH_TRACE_DIR'])/'ai4vn-call-count').read_text()=='1'
""", {'AI4VN_RUN_DIR': directory, 'BENCH_TRACE_DIR': str(Path(directory)/'trace'),
             'BENCH_MAX_CALLS': '2', 'BENCH_MAX_OUTPUT_TOKENS': '128', 'BENCH_ALLOW_LIVE': '1'})

    def test_both_paths_failed_json_and_input_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_native('''
from pathlib import Path
import os,json,argparse
from agents.config import PathConfig
from game_engine.config import DataPaths
from agents.utils import JSONParser
assert Path(PathConfig.DATA_DIR)==DataPaths.DATA_DIR
assert Path(PathConfig.LOG_DIR).resolve()==Path(os.environ['BENCH_NATIVE_ROOT']).resolve()/'logs'
JSONParser._save_failed_response('synthetic', ValueError('fixture'))
assert list(Path(PathConfig.LOG_DIR).glob('failed_json_*.txt'))
import main
from launcher import attach_receipt_observer
attach_receipt_observer(main)
p=Path(os.environ['BENCH_TRACE_DIR']).parent/'shared-task-fixture.txt';p.write_text('共同任务\\n固定开头')
a=argparse.Namespace(input_file=None,requirements_file=str(p))
assert main.resolve_design_inputs(a)==('共同任务\\n固定开头',[])
assert json.loads(next(Path(os.environ['BENCH_TRACE_DIR']).glob('received-ai4vn-*.json')).read_text())['received_task']=='共同任务\\n固定开头'
''', {'BENCH_TRACE_DIR': str(Path(directory)/'trace'), 'TEXT_PROVIDER': 'openai'})

    def test_http_retry_budget_unknown_usage_and_message_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_native('''
import os,json,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
requests=[]
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
  if len(requests)==1:
   self.send_response(429);payload={'error':{'message':'synthetic retry','type':'rate_limit','code':'rate_limit'}}
  else:
   self.send_response(200);payload={'id':'fixture','model':'fake-model','choices':[{'index':0,'message':{'role':'assistant','content':'正文'},'finish_reason':'stop'}]}
  self.send_header('Content-Type','application/json');self.send_header('x-request-id','fixture-request');self.end_headers();self.wfile.write(json.dumps(payload).encode())
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
from agents.llm_client import LLMClient
client=LLMClient(api_key='offline-test-key',base_url=f'http://127.0.0.1:{server.server_port}/v1')
messages=[{'role':'user','content':'共同任务固定开头'}]
assert client._chat_openai(messages,0.7,False)=='正文'
assert len(requests)==2
assert all(r['messages']==messages and r['max_tokens']==128 for r in requests)
try:client._chat_openai(messages,0.7,False)
except Exception:pass
else:raise AssertionError('budget not enforced')
assert len(requests)==2
records=[json.loads(line) for p in Path(os.environ['BENCH_TRACE_DIR']).glob('*.jsonl') for line in p.read_text().splitlines()]
http=[r for r in records if r['boundary']=='http' and r['event']=='started']
assert len(http)==2 and [r['attempt'] for r in http]==[1,2]
assert all(r['usage'] is None for r in records)
assert all('offline-test-key' not in json.dumps(r) for r in records)
sdk_errors=[r for r in records if r['boundary']=='sdk' and r['event']=='error']
assert sdk_errors and all(r['failure_code']=='budget_exhausted' for r in sdk_errors)
assert all(r['delivery_status']=='not_sent' for r in sdk_errors)
server.shutdown()
''', {'AI4VN_RUN_DIR': directory, 'TEXT_PROVIDER': 'openai', 'BENCH_TRACE_DIR': str(Path(directory)/'trace'),
                   'BENCH_RUN_ID': 'synthetic', 'BENCH_MAX_CALLS': '2', 'BENCH_MAX_OUTPUT_TOKENS': '128',
                   'BENCH_MAX_INPUT_CHARS': '10000', 'BENCH_ALLOW_LIVE': '1', 'BENCH_OPERATION_ID': 'fixture'})

    def test_shared_model_parameters_reach_wire_and_count_toward_input_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_native('''
import os,json,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
requests=[]
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  body=self.rfile.read(int(self.headers['Content-Length']))
  requests.append(json.loads(body))
  self.send_response(200)
  self.send_header('Content-Type','application/json')
  self.end_headers()
  self.wfile.write(json.dumps({'id':'fixture','model':'fixture-model','choices':[{'index':0,'message':{'role':'assistant','content':'中文正文'},'finish_reason':'stop'}]}).encode())
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
from agents.llm_client import LLMClient
client=LLMClient(api_key='offline-test-key',base_url=f'http://127.0.0.1:{server.server_port}/v1')
messages=[{'role':'user','content':'公共输入汉字🙂保持原样'}]
os.environ['BENCH_MODEL_PARAMETERS']='{}'
assert client._chat_openai(messages,0.7,True)=='中文正文'
os.environ['BENCH_MODEL_PARAMETERS']=json.dumps({'thinking':{'type':'disabled'}})
assert client._chat_openai(messages,0.7,True)=='中文正文'
assert len(requests)==2
assert 'thinking' not in requests[0]
assert requests[1]=={**requests[0],'thinking':{'type':'disabled'}}
assert all(r['messages']==messages and r['response_format']=={'type':'json_object'} for r in requests)
records=[json.loads(line) for p in Path(os.environ['BENCH_TRACE_DIR']).glob('*.jsonl') for line in p.read_text().splitlines()]
starts=[r for r in records if r['boundary']=='http' and r['event']=='started']
assert len(starts)==2
assert 'thinking' not in starts[0]['request_schema_and_sampling']
assert starts[1]['request_schema_and_sampling']['thinking']=={'type':'disabled'}
# The common provider parameter is counted in the full actual request JSON cap.
size=len(json.dumps(requests[1],ensure_ascii=False,separators=(',',':')))
os.environ['BENCH_MAX_INPUT_CHARS']=str(size-1)
try:client._chat_openai(messages,0.7,True)
except Exception:pass
else:raise AssertionError('added model parameters escaped the input budget')
assert len(requests)==2
assert (Path(os.environ['BENCH_TRACE_DIR'])/'ai4vn-call-count').read_text()=='2'
server.shutdown()
''', {'TEXT_PROVIDER': 'openai', 'BENCH_TRACE_DIR': str(Path(directory)/'trace'),
      'BENCH_RUN_ID': 'synthetic-parameters', 'BENCH_MAX_CALLS': '3', 'BENCH_MAX_OUTPUT_TOKENS': '128',
      'BENCH_MAX_INPUT_CHARS': '10000', 'BENCH_ALLOW_LIVE': '1', 'BENCH_OPERATION_ID': 'fixture'})


if __name__ == '__main__':
    unittest.main()
