"""Local real HTTP/stream/Chromium cache failure regression; zero paid calls."""
from datetime import datetime, timezone
import hashlib
import gzip
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib import request

from native_shims.infiplot.response_capture import ServerResponseReader
from story_benchmark.batch_native.infiplot import Browser, finalize_lineage, writer_lineage
from story_benchmark.recording import Recorder, jsonl, seal
from story_benchmark.io import BenchmarkError

BENCH=Path(__file__).resolve().parents[1]
NODE=os.environ.get('INFIPLOT_TEST_NODE') or shutil.which('node')


@unittest.skipUnless(NODE, 'Node runtime required')
class NativeResponseCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(os.environ.get('INFIPLOT_RESPONSE_EVIDENCE',cls.temp.name))
        cls.root.mkdir(parents=True,exist_ok=True)
        cls.wire=cls.root/'native/server-wire'
        cls.log=(cls.root/'server.log').open('w')
        cls.process=subprocess.Popen([NODE,str(BENCH/'tests/fixtures/infiplot_response_capture_server.cjs'),str(cls.wire)],stdout=subprocess.PIPE,stderr=cls.log,text=True)
        cls.port=json.loads(cls.process.stdout.readline())['port']
        cls.base=f'http://127.0.0.1:{cls.port}'
        cls.reader=ServerResponseReader(cls.root,NODE)

    @classmethod
    def tearDownClass(cls):
        try:
            request.urlopen(cls.base+'/shutdown',timeout=10).read()
            cls.process.wait(timeout=10)
        finally:
            if cls.process.poll() is None:cls.process.kill();cls.process.wait()
            cls.process.stdout.close();cls.log.close();cls.temp.cleanup()

    def get(self,kind):
        with request.urlopen(self.base+'/api/scene?kind='+kind,timeout=30) as response:return response.read()

    def wait_meta(self,kind):
        path=self.wire/('op-'+kind+'.json')
        for _ in range(300):
            if path.exists():
                data=json.loads(path.read_text())
                if data['response_state']!='recording':return data
            time.sleep(.01)
        self.fail('response capture did not settle')

    def test_large_actual_http_preserves_stream_bytes_callbacks_and_backpressure(self):
        started=datetime.now(timezone.utc).isoformat()
        with request.urlopen(self.base+'/api/scene?kind=large',timeout=60) as response:
            first=response.read(1)
            stats=json.load(request.urlopen(self.base+'/stats'))
            self.assertFalse(stats['large']['finished'],'original first byte must be delivered before full body is captured')
            h=hashlib.sha256(first);total=len(first)
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                h.update(chunk);total+=len(chunk)
        meta=self.wait_meta('large')
        self.assertGreater(total,100*1024*1024)
        self.assertEqual(meta['response_state'],'complete')
        self.assertEqual((meta['captured_bytes'],meta['response_sha256']),(total,h.hexdigest()))
        self.assertEqual(hashlib.sha256((self.wire/meta['response_file']).read_bytes()).hexdigest(),h.hexdigest())
        stats=json.load(request.urlopen(self.base+'/stats'))['large']
        self.assertEqual(stats['callbacks'],112)
        self.assertGreater(stats['backpressure_false'],0)
        responses=self.reader.refresh()
        self.assertEqual(responses['op-large']['data']['scene']['id'],'large')
        (self.root/'large-summary.json').write_text(json.dumps({'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),
            'http_body_bytes':total,'sha256':h.hexdigest(),'native_streaming_first_byte_verified':True,'stats':stats,'paid_calls':0},indent=2))

    def test_native_request_stream_is_not_consumed(self):
        payload=b'{"native-request":"unchanged"}'
        req=request.Request(self.base+'/api/scene?kind=post',data=payload,headers={'Content-Type':'application/json'})
        data=json.load(request.urlopen(req))
        self.assertEqual(data['received_body'],payload.decode())
        meta=self.wait_meta('post')
        self.assertEqual(meta['request_content_length'],str(len(payload)))
        self.assertFalse(meta['request_body_consumed_by_observer'])

    def test_original_encoded_response_bytes_and_sse_are_deterministically_read(self):
        for kind in ('gzip','deflate','br','sse'):
            with self.subTest(kind=kind):
                raw=self.get(kind);meta=self.wait_meta(kind)
                self.assertEqual(hashlib.sha256(raw).hexdigest(),meta['response_sha256'])
                responses=self.reader.refresh()
                self.assertEqual(responses['op-'+kind]['data']['scene']['id'],kind)

    def test_capture_storage_error_does_not_change_original_success(self):
        payload=json.loads(self.get('capture-error'))
        self.assertEqual(payload['scene']['id'],'capture-error')
        meta=self.wait_meta('capture-error')
        self.assertEqual(meta['response_state'],'capture_failed')
        self.assertTrue(meta['errors'])
        self.reader.refresh()
        self.assertNotIn('op-capture-error',self.reader.responses)
        self.assertIn('op-capture-error.json',self.reader.errors)

    def test_interrupted_response_is_never_promoted_to_complete_json(self):
        with self.assertRaises((http.client.IncompleteRead,ConnectionError)):
            self.get('interrupted')
        meta=self.wait_meta('interrupted')
        self.assertNotEqual(meta['response_state'],'complete')
        self.reader.refresh(final=True)
        self.assertNotIn('op-interrupted',self.reader.responses)

    @unittest.skipUnless(os.environ.get('INFIPLOT_PLAYWRIGHT_MODULE'), 'opt-in real Chromium inspector eviction')
    def test_real_cdp_eviction_preserves_headers_and_server_writer_lineage(self):
        output=self.root/'cdp-eviction.json'
        subprocess.run([NODE,str(BENCH/'tests/fixtures/infiplot_cdp_eviction.cjs'),os.environ['INFIPLOT_PLAYWRIGHT_MODULE'],self.base,str(output)],check=True,timeout=30)
        observed=json.loads(output.read_text());rows=observed['rows']
        self.assertGreater(observed['bytes'],2*1024*1024)
        headers=[r for r in rows if r['kind']=='native_response_headers']
        errors=[r for r in rows if r['kind']=='native_response_error']
        self.assertEqual(headers[0]['status'],200)
        self.assertEqual(headers[0]['native_operation_id'],'op-cache')
        self.assertTrue(errors,[{k:r.get(k) for k in ('kind','request_id','status','message')} for r in rows])
        self.assertIn('evict',errors[0]['message'].lower())
        self.wait_meta('cache')
        rec=Recorder(self.root/'recorder','cache-fixture',100)
        observer=Browser.__new__(Browser)
        observer.recorder=rec;observer.response_headers={};observer.responses={};observer.requests={};observer.failures=[]
        for row in rows:observer.observe(row)
        self.assertTrue(any(r.get('kind')=='native_response_error' for r in jsonl(rec.root/'native/browser-wire/index.jsonl')))
        calls=[{'call_id':'writer-captured','native_stage':'writer','native_operation_id':'op-cache'}]
        lineage=writer_lineage('cache',observer.responses,calls,self.reader.refresh())
        self.assertEqual(lineage['source_call_ids'],['writer-captured'])
        self.assertEqual(lineage['server_observation']['origin'],'original_native_http_response_bytes')
        # A late-flushing disk observer resolves only provenance before seal.
        state={'session':{'history':[{'scene':{'id':'cache'}}]},'beat':{'narration':'Original native response.'}}
        file=rec.save_json('native/observed.json',state)
        segment=rec.story(state['beat']['narration'],speaker=None,kind='narration',native_source={'file':file,'pointer':'/beat/narration'},revision_id='original-revision',native_id='b1',source_call_ids=None)
        rec.append('telemetry/calls.jsonl',calls[0])
        before=jsonl(rec.root/'trajectories/main/story.jsonl')[0]
        audit=finalize_lineage(rec,observer.responses,self.reader.responses)
        after=jsonl(rec.root/'trajectories/main/story.jsonl')[0]
        self.assertEqual(audit['status'],'complete');self.assertEqual(audit['late_resolved_segments'],1)
        self.assertEqual(after['source_call_ids'],['writer-captured'])
        self.assertEqual({k:v for k,v in before.items() if k!='source_call_ids'},{k:v for k,v in after.items() if k!='source_call_ids'})

    def test_unresolvable_lineage_produces_explicit_error(self):
        rec=Recorder(self.root/'missing-lineage','missing-fixture',100)
        file=rec.save_json('native/observed.json',{'session':{'history':[{'scene':{'id':'missing'}}]},'beat':{'narration':'Unchanged text.'}})
        rec.story('Unchanged text.',speaker=None,kind='narration',native_source={'file':file,'pointer':'/beat/narration'},revision_id='r1',native_id='b1',source_call_ids=None)
        audit=finalize_lineage(rec,{}, {})
        self.assertEqual(audit['status'],'incomplete')
        self.assertTrue(any(e['code']=='text_call_lineage_incomplete' for e in jsonl(rec.root/'errors.jsonl')))

    def test_safe_retention_separates_original_hash_and_removes_signed_url_bytes(self):
        raw=self.get('signed')
        self.assertIn(b'fixture-secret-signature',raw,'Original client response must not change')
        original=self.wait_meta('signed')
        transitions=self.reader.retain_safe_evidence()
        meta=json.loads((self.wire/'op-signed.json').read_text())
        self.assertEqual(meta['original_response_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(meta['original_response_bytes'],len(raw))
        safe=(self.wire/meta['response_file']).read_bytes()
        self.assertEqual(meta['response_sha256'],hashlib.sha256(safe).hexdigest())
        self.assertNotEqual(meta['response_sha256'],meta['original_response_sha256'])
        self.assertNotIn(b'fixture-secret',safe)
        self.assertFalse((self.wire/original['response_file']).exists())
        self.assertEqual(json.loads(safe)['imageUrl'],'https://images.example/scene.png')
        self.assertEqual(self.reader.refresh()['op-signed']['data']['scene']['id'],'signed')


class ReaderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.wire=self.root/'native/server-wire';self.wire.mkdir(parents=True)
        self.reader=ServerResponseReader(self.root,NODE or 'node')

    def response(self,name,raw,encoding='identity',content_type='application/json'):
        file=self.wire/(name+'.response.raw');file.write_bytes(raw)
        meta={'native_operation_id':name,'response_state':'complete','response_file':file.name,'captured_bytes':len(raw),
            'response_sha256':hashlib.sha256(raw).hexdigest(),'response_status':200,'content_encoding':encoding,'content_type':content_type,'errors':[]}
        path=self.wire/(name+'.json');path.write_text(json.dumps(meta));return path

    def test_corrupt_compression_and_nonobject_sse_are_recorded_as_capture_errors(self):
        self.response('gzip',gzip.compress(b'{"scene":{"id":"x"}}')[:-4],'gzip')
        self.response('deflate',b'not-deflate','deflate')
        self.response('sse',b'data: 5\n\n','identity','text/event-stream')
        self.reader.refresh(final=True)
        self.assertEqual(set(self.reader.errors),{'gzip.json','deflate.json','sse.json'})
        self.assertFalse(self.reader.responses)

    def test_invalidated_or_disappeared_metadata_cannot_leave_old_success_cached(self):
        path=self.response('op',b'{"scene":{"id":"x"}}')
        self.assertIn('op',self.reader.refresh())
        data=json.loads(path.read_text());data['response_state']='capture_failed';data['errors']=['disk_failure'];path.write_text(json.dumps(data))
        self.assertNotIn('op',self.reader.refresh())
        self.response('op',b'{"scene":{"id":"x"}}');self.assertIn('op',self.reader.refresh())
        path.unlink();self.assertNotIn('op',self.reader.refresh())

    def test_final_refresh_rehashes_bytes_and_catches_metadata_read_error(self):
        path=self.response('op',b'{"scene":{"id":"x"}}');self.assertIn('op',self.reader.refresh())
        (self.wire/'op.response.raw').write_bytes(b'changed')
        self.assertNotIn('op',self.reader.refresh(final=True))
        self.assertIn('hash_or_length',self.reader.errors['op.json'])
        with patch.object(Path,'read_bytes',side_effect=OSError('metadata unreadable')):
            self.reader.refresh(final=True)
        self.assertIn('metadata unreadable',self.reader.errors['op.json'])

    def test_late_conflicting_operation_invalidates_initial_nonempty_lineage(self):
        rec=Recorder(self.root/'run','conflict',100)
        file=rec.save_json('native/observed.json',{'session':{'history':[{'scene':{'id':'same'}}]},'beat':{'narration':'Original text.'}})
        rec.story('Original text.',speaker=None,kind='narration',native_source={'file':file,'pointer':'/beat/narration'},revision_id='r1',native_id='b1',source_call_ids=['a'])
        for ident in ('a','b'):rec.append('telemetry/calls.jsonl',{'call_id':ident,'native_stage':'writer','native_operation_id':'op-'+ident})
        responses={i:{'data':{'scene':{'id':'same'}},'native_operation_id':'op-'+i,'request_id':i} for i in ('a','b')}
        audit=finalize_lineage(rec,responses,{})
        self.assertEqual(audit['status'],'incomplete');self.assertEqual(len(audit['conflicting_lineages']),1)
        story=jsonl(rec.root/'trajectories/main/story.jsonl')[0]
        self.assertIsNone(story['source_call_ids']);self.assertEqual(story['text'],'Original text.')

    def test_sealed_recording_guards_refuse_all_provenance_or_retention_changes(self):
        rec=Recorder(self.root,'sealed',100);(self.root/'manifest.json').write_text('{"sealed":true}')
        before={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError,'sealed_recording'):finalize_lineage(rec,{}, {})
        with self.assertRaisesRegex(ValueError,'sealed_recording'):self.reader.retain_safe_evidence()
        after={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_orphan_unindexed_raw_is_discarded_and_missing_capture_is_reported(self):
        raw=self.wire/'orphan.response.raw';raw.write_bytes(b'private unindexed response')
        transitions=self.reader.retain_safe_evidence()
        self.assertFalse(raw.exists())
        self.assertEqual(transitions[0]['retained_format'],'unavailable_orphan_raw_discarded')
        self.assertEqual(self.reader.audit({})['status'],'incomplete')

    def test_failed_retention_cannot_seal_remaining_private_raw(self):
        (self.wire/'private.response.raw').write_bytes(b'private original response')
        with self.assertRaisesRegex(BenchmarkError,'unredacted_native_response'):
            seal(self.root,{'root_run_id':'must-not-seal'})
        self.assertFalse((self.root/'manifest.json').exists())


if __name__=='__main__':unittest.main()
