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
    def extract(self, text):
        with tempfile.TemporaryDirectory() as directory:
            story = Path(directory) / 'story.txt'
            story.write_text(text, encoding='utf-8')
            return ADAPTER.extract_first_visible(REPO, story, {'characters': [{'id': 'lin', 'name': '林'}]})

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

    def test_condition_and_jump_are_boundaries(self):
        for control in ('[IF: 林 >= 1]', '<jump target="node1"/>', '[ELSE]', '[ENDIF]'):
            result = self.extract(f'=== Node: root ===\n<content id="旁白">之前。</content>\n{control}\n<content id="旁白">之后。</content>')
            self.assertEqual(len(result['segments']), 1)
            self.assertIn('control_flow_not_executed', result['generation_issue_codes'])

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
server.shutdown()
''', {'AI4VN_RUN_DIR': directory, 'TEXT_PROVIDER': 'openai', 'BENCH_TRACE_DIR': str(Path(directory)/'trace'),
                   'BENCH_RUN_ID': 'synthetic', 'BENCH_MAX_CALLS': '2', 'BENCH_MAX_OUTPUT_TOKENS': '128',
                   'BENCH_MAX_INPUT_CHARS': '10000', 'BENCH_ALLOW_LIVE': '1', 'BENCH_OPERATION_ID': 'fixture'})


if __name__ == '__main__':
    unittest.main()
