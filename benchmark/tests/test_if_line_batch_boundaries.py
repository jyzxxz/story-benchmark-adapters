import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native_shims.if_line import trace
from native_shims.if_line.evidence import sanitize_native_evidence
from story_benchmark.batch_native.if_line import _image_output_link, _media_receipt, _render
from story_benchmark.recording import Recorder, jsonl


class EvidenceTests(unittest.TestCase):
    def test_signed_url_trace_is_sanitized_without_changing_live_response(self):
        import httpx
        url='https://cdn.example/a.png?Policy=private-policy&X-Amz-Signature=private-signature'
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{
                'BENCH_MEDIA_MODE':'1','BENCH_RUN_ID':'local-redaction','BENCH_TRACE_DIR':tmp}):
            request=httpx.Request('POST','http://127.0.0.1/image/v1/images/generations')
            request.extensions['benchmark_event']={'call_id':'local-image','boundary':'gateway_client'}
            response=httpx.Response(200,request=request,headers={'x-benchmark-call-id':'gateway-image'},
                json={'data':[{'url':url}],'usage':{'output_tokens':7}})
            holder={};token=trace._image_receipt.set(holder)
            try: trace.on_response_sync(response)
            finally: trace._image_receipt.reset(token)
            saved=json.loads((Path(tmp)/'responses/local-image.json').read_text())
            self.assertEqual(saved['data'][0]['url'],'https://cdn.example/a.png')
            self.assertEqual(saved['usage']['output_tokens'],7)
            self.assertEqual(response.json()['data'][0]['url'],url)
            self.assertEqual(holder['call_id'],'gateway-image')
            with patch.dict(os.environ,{'BENCH_MEDIA_MODE':'0'}):
                self.assertEqual(trace.redact({'url':url})['url'],url)

    def test_post_run_logs_json_and_partial_snapshots_skip_frozen_source(self):
        url='https://cdn.example/a.png?Policy=private-policy&X-Amz-Credential=private-credential'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);native=root/'native';native.mkdir()
            frozen=native/'original-source.json';frozen.write_text(json.dumps({'url':url}))
            payload=native/'provider.json';payload.write_text(json.dumps({'url':url,'output_tokens':12}))
            log=native/'worker.log';log.write_text('download failed '+url+'\n')
            partial=native/'partial.jsonl';partial.write_text('{"url":"'+url+'"')
            image=native/'image.bin';image.write_bytes(b'unchanged pixels')
            integrity=native/'source-before.json';hashes={'sha256':'a'*64,'file_count':1,'files':{'api_key_pool.py':'b'*64}}
            integrity.write_text(json.dumps(hashes))
            report=sanitize_native_evidence(root,frozen_files=[frozen])
            self.assertIn('private-policy',frozen.read_text())
            for path in (payload,log,partial):
                self.assertNotIn('private-policy',path.read_text())
                self.assertNotIn('private-credential',path.read_text())
            self.assertEqual(json.loads(payload.read_text())['output_tokens'],12)
            self.assertEqual(image.read_bytes(),b'unchanged pixels')
            self.assertEqual(json.loads(integrity.read_text()),hashes)
            self.assertEqual(report['skipped_frozen_files'],['native/original-source.json'])
            self.assertEqual(len(report['changed_files']),3)

    def test_receipt_survives_native_asyncio_and_thread_hops(self):
        holder={};token=trace._image_receipt.set(holder)
        async def native():
            trace.observe_image_receipt('http://127.0.0.1/image/v1/images/generations',
                {'x-benchmark-call-id':'async-receipt'},200)
            return await asyncio.to_thread(lambda:dict(trace._image_receipt.get()))
        try:
            with patch.dict(os.environ,{'BENCH_MEDIA_MODE':'1'}):
                child=asyncio.run(native())
            self.assertEqual(child,holder)
            self.assertEqual(holder['call_id'],'async-receipt')
        finally: trace._image_receipt.reset(token)

    def test_same_pixels_keep_actual_call_and_candidate_identity(self):
        class Gateway:
            def output_for_call(self,call_id,index=0):
                return {'output_id':f'{call_id}-{index}','file_sha256':'same-bytes'}
            def output_for_hash(self,digest): return None
        gateway=Gateway()
        for call_id in ('call-a','call-b'):
            oid,link=_image_output_link(gateway,'same-bytes',{'call_id':call_id,'candidate_index':0})
            self.assertEqual(oid,call_id+'-0')
            self.assertEqual(link['method'],'exact_native_call_and_index')
        self.assertIsNone(_image_output_link(gateway,'same-bytes')[0])
        self.assertIsNone(_image_output_link(gateway,'other-bytes',{'call_id':'call-a','candidate_index':0})[0])

    def test_native_cache_reuses_same_path_receipt_not_equal_pixels_elsewhere(self):
        rows=[{'native_asset_id':1,'call_id':'cache-source','candidate_index':0,
               'native_result':{'cached':False,'image_path':'/native/a.png'}},
              {'native_asset_id':2,'call_id':'unrelated','candidate_index':0,
               'native_result':{'cached':False,'image_path':'/native/b.png'}},
              {'native_asset_id':3,'native_result':{'cached':True,'image_path':'/native/a.png'}}]
        receipt=_media_receipt(rows,3)
        self.assertEqual(receipt['call_id'],'cache-source')
        self.assertEqual(receipt['cached_from_native_result_line'],1)
        rows[-1]['native_result']['cached']=False
        self.assertNotIn('call_id',_media_receipt(rows,3))


@unittest.skipUnless(os.environ.get('IFLINE_RENDER_E2E')=='1','requires external native Vue/Chromium dependencies')
class NativeRendererTests(unittest.TestCase):
    def test_native_choice_revisit_policy_and_frame_without_story(self):
        package=Path(__file__).resolve().parents[2]
        def string(value):return {'Kind':'String','StringValue':value}
        graph={'Version':'1.0','StartNodeIndex':0,'Nodes':[
            {'Index':0,'NodeType':1,'SubType':6,'Data':{},'Outputs':{'Next':[1]}},
            {'Index':1,'NodeType':1,'SubType':1,'Data':{'TextData':string('走到门前。')},'Outputs':{'Next':[2]}},
            {'Index':2,'NodeType':1,'SubType':5,'Data':{'Options':{'Kind':'List','Items':[
                {'Kind':'Object','ObjectValue':{'Text':string('再看一次')}},
                {'Kind':'Object','ObjectValue':{'Text':string('进入阅览室')}}]}},
             'Outputs':{'Options[0].Next':[1],'Options[1].Next':[3]}},
            {'Index':3,'NodeType':1,'SubType':1,'Data':{'TextData':string('进入阅览室。')},'Outputs':{'Next':[4]}},
            {'Index':4,'NodeType':1,'SubType':11,'Data':{},'Outputs':{}}]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);recorder=Recorder(root/'run','local-render',14)
            recorder.choice([{'id':'initial','label':'initial native promotion'}],selected_index=0)
            native=recorder.root/'native';(native/'native').mkdir(parents=True)
            revision={'id':'fixture-graph','graph_json':graph,'graph_hash':hashlib.sha256(json.dumps(graph).encode()).hexdigest()}
            (native/'native/graph_0.json').write_text(json.dumps(revision))
            config={'repo_path':str(package/'systems/if_line'),'node_executable':os.environ['IFLINE_NODE'],
                'node_modules':os.environ['IFLINE_NODE_MODULES']}
            adapter=type('Adapter',(),{'base':'http://127.0.0.1:1','_cookie':''})()
            handle={'run_dir':str(native),'chapter_0_revision_id':'chapter'}
            for call_id,task,role in [('draft','script-task','text'),('repair','script-task','text'),
                                      ('other-task','chapter-task','text'),('vision','script-task','vision')]:
                recorder.append('telemetry/calls.jsonl',{'call_id':call_id,'native_task_id':task,'role':role})
            result=_render(adapter,handle,recorder,{'choice_indices':[1,0,1],'reading_delay_seconds':0},config,revision,0,{},
                script_revision_id='script-revision',script_generation_task_id='script-task')
            self.assertEqual([c['selected_index'] for c in result['choices']],[0,1])
            self.assertEqual([c['policy_choice_ordinal'] for c in result['choices']],[1,2])
            story=jsonl(recorder.root/'trajectories/main/story.jsonl')
            self.assertEqual([s['text'] for s in story],['走到门前。','走到门前。','进入阅览室。'])
            self.assertTrue(all(s['source_call_ids']==['draft','repair'] for s in story))
            self.assertTrue(all(s['native_source']['script_generation_task_id']=='script-task' and
                s['native_source']['availability_id']=='script-revision' for s in story))
            self.assertEqual(len(jsonl(recorder.root/'visuals/frame_map.jsonl')),5)
            self.assertEqual(sum(not f['segment_ids'] for f in jsonl(recorder.root/'visuals/frame_map.jsonl')),2)
            choices=jsonl(recorder.root/'trajectories/main/choices.jsonl')
            self.assertEqual([c['selected_index'] for c in choices],[0,0,1])
            self.assertEqual([c['after_segment_id'] for c in choices[1:]],['s000001','s000002'])
            selected=[e for e in jsonl(recorder.root/'telemetry/events.jsonl') if e['event_type']=='choice_selected']
            self.assertTrue(all(e['clock_id']==result['clock_id'] for e in selected))
            self.assertTrue(all(int(c['committed_monotonic_ns'])>=int(c['clicked_monotonic_ns']) for c in result['choices']))


if __name__=='__main__':unittest.main()
