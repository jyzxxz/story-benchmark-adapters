"""Run with IFLINE_PRISTINE_REPO and IFLINE_PYTHON to verify immutable native E2E."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from story_benchmark.adapters.if_line import IFLineAdapter, IFLineError
from native_shims.if_line.bootstrap import source_fingerprint


@unittest.skipUnless(os.environ.get('IFLINE_PRISTINE_REPO') and os.environ.get('IFLINE_PYTHON'),
                     'requires explicit isolated native source and dependency Python')
class ExternalIFLineTests(unittest.TestCase):
    def test_native_http_worker_chain_and_source_unchanged(self):
        out=None
        if os.environ.get('IFLINE_ENGINEERING_EVIDENCE_DIR'):
            out=Path(os.environ['IFLINE_ENGINEERING_EVIDENCE_DIR']).resolve()
            out.mkdir(parents=True,exist_ok=False)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            bundle=root/'bundle'
            (bundle/'payloads').mkdir(parents=True)
            shared='【共同任务】\n在门廊等雨停。\n【固定开头】\n门已经关上。'
            (bundle/'shared_task.txt').write_text(shared)
            (bundle/'opening.txt').write_text('门已经关上。')
            (bundle/'payloads/if_line.json').write_text(json.dumps({'title':'工程测试',
                'characters':[],'story_start':'','story_end':'','extra_requirements':shared},ensure_ascii=False))
            if os.environ.get('BENCH_SHARED_BUNDLE'):
                bundle=Path(os.environ['BENCH_SHARED_BUNDLE']).resolve()
                shared=(bundle/'shared_task.txt').read_text()
            repo=Path(os.environ['IFLINE_PRISTINE_REPO']).resolve()
            before=source_fingerprint(repo)
            config={'repo_path':str(repo),'root_run_id':'external-fixture','trace_dir':str(root/'run/trace'),
                'max_calls':8,'max_output_tokens':32768,'max_input_chars':100000,'model':'fixed-test-model',
                'model_base_url':'http://127.0.0.1:9/v1','live':True,'timeout_seconds':30,
                'python_executable':os.environ['IFLINE_PYTHON'],'managed_runtime':'engineering_fixed_response','chapter_count':1}
            if os.environ.get('IFLINE_SOURCE_LOCK'):
                config['source_lock']=os.environ['IFLINE_SOURCE_LOCK']
            adapter=IFLineAdapter(config)
            handle=adapter.prepare(bundle,root/'run')
            try:
                result=adapter.generate_first_artifact(handle)
                exported=adapter.export_first_artifact(handle)
                self.assertEqual(result['execution_environment'],'engineering_fixed_response')
                self.assertEqual(exported['segments'][0]['text'],'雨继续下着。')
                with self.assertRaisesRegex(IFLineError,'native_http_error: 409'):
                    adapter._request('PUT',f"/api/projects/{result['native_project_id']}/bible-head",
                        {'revision_id':handle['bible_revision_id']},{'If-Match':'"1"'})
                adapter.generate_first_artifact(handle)
            finally:
                adapter.close(handle)
                if out is not None:
                    import shutil
                    shutil.copytree(root/'run',out/'run')
            self.assertEqual(before,source_fingerprint(repo))
            runtime=root/'run/native-runtime'
            self.assertTrue(json.loads((runtime/'source-after.json').read_text())['source_unchanged'])
            requests=json.loads((runtime/'fixed-provider-requests.json').read_text())
            self.assertEqual(len(requests),5)  # Includes native visual-style text classification.
            self.assertEqual(sum(m.get('content','').count(shared) for m in requests[0]['messages']),1)
            self.assertNotIn('请自行设计主角',requests[0]['messages'][1]['content'])
            self.assertTrue(requests[2]['stream'])
            events=[json.loads(line) for file in (root/'run/trace').glob('if_line-*.jsonl') for line in file.read_text().splitlines()]
            completed=[r for r in events if r['event']=='completed']
            self.assertEqual(len(completed),5)
            self.assertEqual({r['stage'] for r in completed},{'bible.generate','outline.generate','chapter.generate','chapter_script.generate'})
            self.assertIsNone(next(r for r in completed if r['stage']=='chapter.generate')['usage'])
            self.assertEqual(json.loads((root/'run/trace/received_if_line.json').read_text())['received_task'],shared)
            if out is not None:
                (out/'result.json').write_text(json.dumps({'evidence_kind':'engineering_fixed_response',
                    'source_unchanged':True,'source_sha256':before['sha256'],'source_file_count':before['file_count'],
                    'api':'actual_loopback_http_native_routes','auth':'native_guest_sid','worker':'native_task_function_serial_thread',
                    'broker_delivery':'not_exercised','storage':'isolated_sqlite','paid_model_calls':0,
                    'fixed_http_calls':5,'result':result,'export':exported},ensure_ascii=False,indent=2))

if __name__=='__main__': unittest.main()
