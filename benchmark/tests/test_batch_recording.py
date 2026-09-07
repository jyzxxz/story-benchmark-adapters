import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import request, error

from story_benchmark.batch import load_batch_config, prepare_plan, read_plan, execute_plan
from story_benchmark.batch_worker import audit_inputs, constraints_from_sources, run_job
from story_benchmark.batch_compare import compare_batches
from story_benchmark.gateway import ModelGateway, normalize_usage
from story_benchmark.io import BenchmarkError, atomic_json, sha256
from story_benchmark.recording import (Recorder, compute_metrics, duration, jsonl, make_review_packages,
    seal, sentence_prefix, usage_totals, verify_recording, redact_evidence)

BENCH=Path(__file__).resolve().parents[1]
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDDsAAAAASUVORK5CYII=')


@contextmanager
def provider(response,stream=False,status=200,extra_length=0):
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            observed.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(status);self.send_header('Content-Type','text/event-stream' if stream else 'application/json')
            self.send_header('Content-Length',str(len(response)+extra_length));self.end_headers()
            self.wfile.write(response)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)+'/v1',observed
    finally:server.shutdown();server.server_close();thread.join()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'run'
        self.r=Recorder(self.root,'run-id',7)
        self.r.save_bytes('inputs/opening.txt','已发生的共同开头。'.encode())
        self.r.save_bytes('inputs/shared_task.txt','共同任务'.encode())
        self.r.save_json('inputs/constraints.json',{'test':'same'})
    def tearDown(self):self.temp.cleanup()

    def test_real_original_substring_and_observed_window_seal(self):
        self.r.event('run_submitted')
        original='角色走入雨中。另一段不能混进来。'
        s=self.r.story(original,native_source={'file':'native-source.json','pointer':'/text'},revision_id='original-v1')
        self.assertEqual(s['text'],'角色走入雨中。')
        self.assertTrue(self.r.scope_reached)
        self.assertIsNone(self.r.story('超出窗口',native_source={},revision_id='v2'))
        self.r.event('run_stopped')
        self.r.save_json('metrics.json',compute_metrics(self.root))
        make_review_packages(self.root,'anonymous')
        seal(self.root,{'visible_chars':7,'scope_reached':True,'output_contract':{'window_chars':7}})
        self.assertTrue(verify_recording(self.root)['ok'])
        raw=(self.root/'evaluation/reading_blind.json').read_text()
        self.assertNotIn('run-id',raw);self.assertNotIn('native-source',raw)
        (self.root/s['observed_source_file']).write_text('被修改')
        with self.assertRaisesRegex(BenchmarkError,'recording_file_changed'):verify_recording(self.root)

    def test_sentence_rule_preserves_unicode_and_no_boundary_units(self):
        self.assertEqual(sentence_prefix('甲乙丙丁。后文',3),'甲乙丙丁。')
        self.assertEqual(sentence_prefix('无标点原生单位',2),'无标点原生单位')
        self.assertEqual(sentence_prefix('😀说：“走！”尾巴',4),'😀说：“走！”')

    def test_usage_missing_is_not_zero_and_evaluation_separate(self):
        calls=[{'role':'text','purpose':'generation','delivery':'completed','usage_normalized':normalize_usage({'prompt_tokens':4,'completion_tokens':2,'total_tokens':6,'prompt_tokens_details':{'cached_tokens':3}})},
               {'role':'text','purpose':'generation','delivery':'unknown','usage_normalized':normalize_usage(None)},
               {'role':'text','purpose':'evaluation','delivery':'completed','usage_normalized':normalize_usage({'total_tokens':9})}]
        m=usage_totals(calls)
        self.assertIsNone(m['text']['total_tokens']);self.assertEqual(m['text']['known_total_tokens_subtotal'],6)
        self.assertEqual(m['text']['coverage'],.5);self.assertEqual(m['evaluation']['total_tokens'],9)
        self.assertIsNone(m['image']['total_tokens'])
        self.assertIsNone(normalize_usage({'prompt_tokens':4,'completion_tokens':2})['total_tokens'])

    def test_clock_and_backend_not_presented(self):
        events=[{'event_type':'a','monotonic_ns':2,'clock_id':'one','observer':'backend'},
                {'event_type':'b','monotonic_ns':100,'clock_id':'two','observer':'backend'}]
        self.assertIsNone(duration(events,'a','b'))
        self.r.event('run_submitted');self.r.story('文字',native_source={},revision_id='v')
        m=compute_metrics(self.root)['M4']
        self.assertIsNotNone(m['backend_first_text_seconds'])
        self.assertIsNone(m['first_text_presented_seconds']);self.assertEqual(m['presentation_status'],'not_measured')

    def test_empty_body_frame_assets_count_without_invented_text(self):
        image=self.root/'native.png';image.write_bytes(PNG)
        asset=self.r.asset(image,origin='library')
        self.r.frame(segment_ids=[],asset_ids=[asset['asset_id']],clean_path=image,ui_path=None,native_source={'empty_native_body':True})
        m=compute_metrics(self.root)
        self.assertEqual(m['M8']['used_assets'],1);self.assertEqual(m['M8']['total_segments'],0)
        self.assertEqual(m['M6']['evidence_status'],'missing_frames')

    def test_first_text_uses_visited_script_not_chapter_or_empty_menu(self):
        self.r.event('run_submitted')
        self.r.event('native_chapter_available',availability_id='final-script')
        self.r.event('native_story_unit_available',native_id='visited',readable_text=[])
        self.r.event('native_script_available',availability_id='unvisited-script')
        self.r.event('native_script_available',availability_id='final-script')
        self.r.story('实际正文',native_id='visited',native_source={'availability_id':'final-script'},revision_id='v')
        events=jsonl(self.root/'telemetry/events.jsonl')
        expected=duration([e for e in events if e.get('availability_id')!='unvisited-script'],
                          'run_submitted','native_script_available',observer='adapter_backend')
        self.assertEqual(compute_metrics(self.root)['M4']['backend_first_text_seconds'],expected)

    def test_signed_asset_urls_scrubbed_but_native_payload_not_mutated(self):
        original={'data':[{'url':'https://host/img.png?Policy=SIGNED&X-Amz-Credential=PRIVATE&Expires=123'}]}
        scrubbed=redact_evidence(original)
        self.assertEqual(scrubbed['data'][0]['url'],'https://host/img.png')
        self.assertIn('PRIVATE',original['data'][0]['url'])
        self.assertEqual(redact_evidence({'Policy':'signed','X-Amz-Credential':'secret'}),{'Policy':'[REDACTED]','X-Amz-Credential':'[REDACTED]'})
        inventory={'app/services/api_key_pool.py':'a'*64}
        self.assertEqual(redact_evidence(inventory),inventory)

    def test_started_without_terminal_never_disappears_from_denominators(self):
        self.r.append('telemetry/calls.jsonl',{'call_id':'complete','role':'text','delivery':'completed',
            'usage_normalized':normalize_usage({'total_tokens':30})})
        self.r.event('model_request_started',call_id='pending-text',role='text')
        self.r.event('model_request_started',call_id='pending-image',role='image')
        m=compute_metrics(self.root)
        self.assertEqual(m['M7']['roles']['text']['attempts'],2)
        self.assertEqual(m['M7']['roles']['text']['coverage'],.5)
        self.assertIsNone(m['M7']['roles']['text']['total_tokens'])
        self.assertEqual(m['M8']['image_request_attempts'],1)
        self.assertFalse(m['M8']['candidate_count_complete']);self.assertIsNone(m['M8']['returned_candidates'])
        self.assertEqual(len(jsonl(self.root/'telemetry/calls.jsonl')),1)

    def test_menu_and_placeholder_frames_are_not_first_body_image(self):
        image=self.root/'native.png';image.write_bytes(PNG)
        self.r.event('run_submitted')
        self.r.frame(segment_ids=[],asset_ids=[],clean_path=image,ui_path=image)
        s=self.r.story('新文字',native_source={},revision_id='v')
        placeholder=self.r.frame(segment_ids=[s['segment_id']],asset_ids=[],clean_path=image,ui_path=image,placeholder=True)
        self.r.event('frame_presented',observer='offscreen_native_dom',frame_id=placeholder['frame_id'])
        m=compute_metrics(self.root)['M4']
        self.assertIsNone(m['backend_first_frame_seconds']);self.assertIsNone(m['offscreen_dom_first_frame_seconds'])

    def test_same_clock_different_observation_labels_can_be_paired(self):
        events=[{'event_type':'submit','clock_id':'one','observer':'adapter_backend','monotonic_ns':100},
                {'event_type':'show','clock_id':'one','observer':'offscreen_native_dom','monotonic_ns':200}]
        self.assertEqual(duration(events,'submit','show',observer='offscreen_native_dom'),.0000001)

    def test_receipt_and_wire_both_required(self):
        shared='同一份题目和固定开头'
        self.r.save_json('native/received.json',{'received_task':shared,'boundary':'native_reader'})
        self.r.save_json('telemetry/raw/a.json',{'payload':{'messages':[{'content':shared}]},'before_payload':{'messages':[{'content':shared}]}})
        self.r.append('telemetry/calls.jsonl',{'call_id':'a','role':'text','request_file':'telemetry/raw/a.json','start_monotonic_ns':1})
        self.assertEqual(audit_inputs(self.root,shared)['status'],'passed')
        self.r.save_json('telemetry/raw/a.json',{'payload':{'messages':[{'content':shared+shared}]},'before_payload':{'messages':[{'content':shared}]}})
        self.assertEqual(audit_inputs(self.root,shared)['status'],'incomplete')

    def test_constraints_preserve_exact_source_ranges(self):
        bundle=BENCH/'examples/CAMPUS-01-V4';data=constraints_from_sources(bundle)
        self.assertGreater(len(data['source_clauses']),10)
        for row in data['source_clauses']:
            source=(bundle/row['source_file']).read_text()
            self.assertEqual(source[row['char_start']:row['char_end']],row['requirement'])
            self.assertEqual(sha256(source),row['source_sha256'])

    def test_sdk_receiver_is_valid_without_claiming_route_receiver(self):
        shared='同一份任务'
        payload={'messages':[{'content':shared}]}
        self.r.save_json('telemetry/raw/a.json',{'payload':payload,'before_payload':payload})
        self.r.append('telemetry/calls.jsonl',{'call_id':'a','role':'text','request_file':'telemetry/raw/a.json','start_monotonic_ns':1})
        self.r.save_json('native/sender.json',{'received_task':shared,'boundary':'native_browser_fetch_request_body',
            'direct_native_route_receiver_observed':False})
        self.assertEqual(audit_inputs(self.root,shared)['status'],'incomplete')
        self.r.save_json('native/sdk.json',{'received_task':shared,'boundary':'native_sdk_task_block',
            'direct_native_route_receiver_observed':False})
        self.assertEqual(audit_inputs(self.root,shared)['status'],'passed')


class GatewayTests(unittest.TestCase):
    def request(self,gateway,role,payload,headers=None):
        req=request.Request(gateway.url(role)+('/images/generations' if role=='image' else '/chat/completions'),
            data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+gateway.api_key,'Content-Type':'application/json',**(headers or {})})
        with request.urlopen(req) as response:
            return response.headers.get('x-benchmark-call-id'),response.read()

    def test_sse_usage_and_operation_capture(self):
        response=b'data: {"model":"frozen","choices":[{"delta":{"content":"story"}}]}\n\ndata: {"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}\n\ndata: [DONE]\n\n'
        with tempfile.TemporaryDirectory() as tmp,provider(response,True) as (url,observed),patch.dict(os.environ,{'FIXTURE_KEY':'only-local-key'}):
            r=Recorder(Path(tmp),'r',10);g=ModelGateway(r,{'text':{'model':'frozen','base_url':url,'api_key_env':'FIXTURE_KEY'}}).start()
            try:call,raw=self.request(g,'text',{'model':'frozen','messages':[{'role':'user','content':'same task'}],'stream':True},{'X-Benchmark-Native-Stage':'writer'})
            finally:g.close()
            self.assertEqual(raw,response);row=jsonl(Path(tmp)/'telemetry/calls.jsonl')[0]
            self.assertEqual(row['usage_normalized']['total_tokens'],11);self.assertEqual(row['native_stage'],'writer')
            self.assertEqual(len(observed),1);self.assertEqual(row['call_id'],call)

    def test_identical_candidates_are_distinct_outputs_and_ready_before_response(self):
        response=json.dumps({'data':[{'b64_json':base64.b64encode(PNG).decode()}]*2}).encode()
        with tempfile.TemporaryDirectory() as tmp,provider(response) as (url,observed),patch.dict(os.environ,{'FIXTURE_KEY':'only-local-key'}):
            r=Recorder(Path(tmp),'r',10);g=ModelGateway(r,{'image':{'model':'gpt-image-2','base_url':url,'api_key_env':'FIXTURE_KEY'}}).start()
            try:
                call,raw=self.request(g,'image',{'model':'gpt-image-2','prompt':'same native prompt'})
                self.assertIsNotNone(g.output_for_call(call,0));self.assertIsNotNone(g.output_for_call(call,1))
            finally:g.close()
            self.assertIsNone(g.output_for_hash(sha256(PNG)))
            self.assertNotEqual(g.output_for_call(call,0)['output_id'],g.output_for_call(call,1)['output_id'])
            m=compute_metrics(Path(tmp))['M8']
            self.assertEqual(m['image_request_attempts'],1);self.assertEqual(m['returned_candidates'],2)
            self.assertEqual(m['saved_candidate_files'],2)

    def test_http_failure_keeps_attempt_without_invented_usage(self):
        with tempfile.TemporaryDirectory() as tmp,provider(b'{"error":{"message":"rate limit"}}',status=429) as (url,_),patch.dict(os.environ,{'FIXTURE_KEY':'only-local-key'}):
            r=Recorder(Path(tmp),'r',10);g=ModelGateway(r,{'text':{'model':'frozen','base_url':url,'api_key_env':'FIXTURE_KEY'}}).start()
            try:
                with self.assertRaises(error.HTTPError):self.request(g,'text',{'model':'frozen','messages':[]})
            finally:g.close()
            row=jsonl(Path(tmp)/'telemetry/calls.jsonl')[0]
            self.assertEqual(row['status_code'],429);self.assertIsNone(row['usage_normalized']['total_tokens'])

    def test_short_content_length_is_unknown_even_when_partial_json_parses(self):
        response=b'{"choices":[],"usage":{"total_tokens":99}}'
        with tempfile.TemporaryDirectory() as tmp,provider(response,extra_length=5) as (url,_),patch.dict(os.environ,{'FIXTURE_KEY':'only-local-key'}):
            r=Recorder(Path(tmp),'r',10);g=ModelGateway(r,{'text':{'model':'frozen','base_url':url,'api_key_env':'FIXTURE_KEY'}}).start()
            try:
                _,raw=self.request(g,'text',{'model':'frozen','messages':[]})
            finally:g.close()
            row=jsonl(Path(tmp)/'telemetry/calls.jsonl')[0]
            self.assertEqual(row['delivery'],'unknown');self.assertIsNone(row['usage_normalized']['total_tokens'])
            partial=json.loads((Path(tmp)/row['response_file']).read_text())
            self.assertEqual(partial['body_utf8'],response.decode());self.assertFalse(partial['transport_complete'])


class BatchConfigTests(unittest.TestCase):
    def test_virtualenv_interpreter_symlink_is_not_resolved_to_base_python(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            link=Path(tmp)/'venv/bin/python';link.parent.mkdir(parents=True);link.symlink_to(sys.executable)
            raw=json.loads((BENCH/'configs/batch.example.json').read_text())
            raw['systems']['if_line']['python_executable']='venv/bin/python'
            file=Path(tmp)/'config.json';atomic_json(file,raw)
            config=load_batch_config(file)
            resolved=config['systems']['if_line']['python_executable']
            self.assertEqual(resolved,str(file.resolve().parent/'venv/bin/python'))
            self.assertTrue(Path(resolved).is_symlink())

    def test_common_config_disallows_per_system_budget_model_or_prompt_override(self):
        raw=json.loads((BENCH/'configs/batch.example.json').read_text())
        for field,value in [('model','different'),('max_calls',10),('window_chars',200)]:
            with tempfile.TemporaryDirectory() as tmp:
                changed=json.loads(json.dumps(raw));changed['systems']['infiplot'][field]=value
                path=Path(tmp)/'config.json';atomic_json(path,changed)
                with self.assertRaisesRegex(BenchmarkError,'per_system_common_condition_override'):load_batch_config(path)

    def test_plan_freezes_common_input_order_count_and_detects_tampering(self):
        config=load_batch_config(BENCH/'configs/batch.example.json')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'batch';plan=prepare_plan('if_line',config,out,3)
            self.assertEqual([j['repeat'] for j in plan['jobs']],[1,2,3])
            self.assertEqual(len({j['run_id'] for j in plan['jobs']}),3)
            self.assertEqual(len({j['shared_sha256'] for j in plan['jobs']}),1)
            self.assertEqual(read_plan(out),plan)
            (out/'plan.json').write_text('{}')
            with self.assertRaisesRegex(BenchmarkError,'frozen_batch_plan_changed'):read_plan(out)

    def test_changed_adapter_cannot_resume_queue(self):
        config=load_batch_config(BENCH/'configs/batch.example.json')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'batch';prepare_plan('if_line',config,out,1)
            with patch('story_benchmark.runner.adapter_source_inventory',return_value={'changed':'hash'}):
                with self.assertRaisesRegex(BenchmarkError,'adapter_changed_since_plan'):execute_plan(out,1)
            self.assertFalse((out/'runs').exists())

    def test_duplicate_direct_workers_have_only_one_root_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        config=load_batch_config(BENCH/'configs/batch.example.json')
        config['policy']['evidence_kind']='fixture'
        entered=threading.Event();release=threading.Event();executions=[]
        class Fixture:
            @staticmethod
            def preflight(*args):return {'ok':True,'errors':[]}
            @staticmethod
            def run(*args):
                executions.append(1);entered.set()
                if not release.wait(15):raise ValueError('test coordination timed out')
                return {'stop_reason':'native_end','native_ended':True}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'batch';plan=prepare_plan('if_line',config,out,1);job=plan['jobs'][0];root=out/'runs'/job['run_id']
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(run_job,plan,job,root,Fixture)
                self.assertTrue(entered.wait(15))
                second=pool.submit(run_job,plan,job,root,Fixture)
                try:
                    with self.assertRaisesRegex(BenchmarkError,'root_run_already_exists'):second.result(15)
                finally:release.set()
                self.assertEqual(first.result(30)['evidence_kind'],'fixture')
            self.assertEqual(len(executions),1)
            self.assertTrue(verify_recording(root)['ok'])

    def test_comparison_binds_sealed_root_to_plan_and_keeps_scheduling_separate(self):
        config=load_batch_config(BENCH/'configs/batch.example.json');config['policy']['evidence_kind']='fixture'
        class Fixture:
            @staticmethod
            def preflight(*args):return {'ok':True,'errors':[]}
            @staticmethod
            def run(specific,bundle,root,recorder,gateway,policy):
                text=(bundle/'shared_task.txt').read_text()
                recorder.save_json('native/received.json',{'received_task':text,'boundary':'fixture_receiver'})
                body={'messages':[{'role':'user','content':text}]}
                recorder.save_json('telemetry/raw/fixture.json',{'payload':body,'before_payload':body})
                recorder.append('telemetry/calls.jsonl',{'call_id':'fixture','role':'text','start_monotonic_ns':1,
                    'request_file':'telemetry/raw/fixture.json','usage_normalized':{},'delivery':'completed'})
                return {'stop_reason':'native_end','native_ended':True}
        with tempfile.TemporaryDirectory() as tmp:
            folders=[];roots=[]
            for system in ('if_line','ai4visualnovel','infiplot'):
                out=Path(tmp)/system;folders.append(out)
                plan=prepare_plan(system,config,out,1);job=plan['jobs'][0]
                root=out/'runs'/job['run_id'];roots.append(root)
                run_job(plan,job,root,Fixture)
            report=compare_batches(folders)
            self.assertTrue(report['comparison_ready']);self.assertIsNone(report['latency_scheduling_conditions_identical'])
            original=json.loads((roots[0]/'manifest.json').read_text())
            for field,value in [('root_run_id','another-run'),('system','infiplot'),('repeat',99),
                                ('evidence_kind','live'),('model_configuration',{})]:
                atomic_json(roots[0]/'manifest.json',{**original,field:value})
                report=compare_batches(folders)
                self.assertFalse(report['comparison_ready']);self.assertFalse(report['integrity_valid'])
                self.assertTrue(any('sealed_run_plan_mismatch' in e and field in e for e in report['condition_errors']))
            atomic_json(roots[0]/'manifest.json',original)


if __name__=='__main__':unittest.main()
