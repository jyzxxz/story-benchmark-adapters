"""Opt-in real PostgreSQL/Redis/Celery path with a loopback fixture provider."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from story_benchmark.adapters.if_line import IFLineAdapter, IFLineError
from native_shims.if_line.bootstrap import source_fingerprint
from native_shims.if_line.fixtures import FixedProvider


@unittest.skipUnless(os.environ.get('IFLINE_NATIVE_SERVICES_TEST')=='1',
                     'requires explicit disposable PostgreSQL/Redis engineering run')
class NativeServicesTests(unittest.TestCase):
    def test_real_postgres_redis_worker_beat(self):
        # Reserve a fresh evidence directory before starting any native services.
        # Re-running into an existing directory must never merge process traces.
        out=Path(os.environ['IFLINE_ENGINEERING_EVIDENCE_DIR']).resolve()
        out.mkdir(parents=True,exist_ok=False)
        repo=Path(os.environ['IFLINE_PRISTINE_REPO']).resolve()
        before=source_fingerprint(repo)
        python=os.environ['IFLINE_PYTHON']
        pg_bin=Path(os.environ.get('IFLINE_PG_BIN','/opt/homebrew/bin'))
        bundle=Path(os.environ['BENCH_SHARED_BUNDLE']).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cluster=root/'pgdata'
            sockets=root/'pgsocket'
            sockets.mkdir()
            pglog=root/'postgres.log'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            subprocess.run([str(pg_bin/'initdb'),'-D',str(cluster),'--no-locale','--encoding=UTF8','--auth=trust','-U','benchmark'],
                           check=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            subprocess.run([str(pg_bin/'pg_ctl'),'-D',str(cluster),'-l',str(pglog),'-o',
                f'-h 127.0.0.1 -p {port} -k {sockets}','start','-w'],check=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            provider=None;thread=None;adapter=None;handle=None
            previous={k:os.environ.get(k) for k in ('IFLINE_NATIVE_TEST_DATABASE_URL','IFLINE_NATIVE_TEST_KEY')}
            try:
                subprocess.run([str(pg_bin/'createdb'),'-h','127.0.0.1','-p',str(port),'-U','benchmark',
                    'if_line_bench_native_e2e'],check=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                FixedProvider.requests=[]
                FixedProvider.chapter_count=13
                FixedProvider.model='fixed-test-model'
                provider=ThreadingHTTPServer(('127.0.0.1',0),FixedProvider)
                thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
                os.environ['IFLINE_NATIVE_TEST_DATABASE_URL']=f'postgresql+psycopg://benchmark@127.0.0.1:{port}/if_line_bench_native_e2e'
                os.environ['IFLINE_NATIVE_TEST_KEY']='fixture-native-services-key'
                config={'repo_path':str(repo),'root_run_id':'if-line-native-e2e','trace_dir':str(root/'run/trace'),
                    'source_lock':os.environ['IFLINE_SOURCE_LOCK'],'max_calls':12,'max_output_tokens':32768,
                    'max_input_chars':100000,'model':'fixed-test-model','model_base_url':f'http://127.0.0.1:{provider.server_port}/v1',
                    'live':True,'timeout_seconds':60,'python_executable':python,'managed_runtime':'native_services',
                    'model_parameters':{'thinking':{'type':'disabled'}},
                    'isolated_deployment':True,'database_url_env':'IFLINE_NATIVE_TEST_DATABASE_URL',
                    'model_api_key_env':'IFLINE_NATIVE_TEST_KEY','redis_executable':shutil.which('redis-server')}
                adapter=IFLineAdapter(config)
                handle=adapter.prepare(bundle,root/'run')
                result=adapter.generate_first_artifact(handle)
                exported=adapter.export_first_artifact(handle)
                count=len(FixedProvider.requests)
                adapter.generate_first_artifact(handle)
                self.assertEqual(len(FixedProvider.requests),count)
                self.assertEqual(exported['segments'][0]['text'],'雨继续下着。')
                self.assertEqual(result['execution_environment'],'native_services')
                self.assertEqual(count,5)
                outline=json.loads((root/'run/native/outline_revision.json').read_text())
                self.assertEqual(len(outline['chapters']),13)
                chapter_requests=[r for r in FixedProvider.requests if '小说作家' in r['messages'][0]['content']]
                self.assertEqual(len(chapter_requests),1)
                self.assertIn('第 1/13 章（非最后一章）',chapter_requests[0]['messages'][1]['content'])
                self.assertTrue(all(r['thinking']=={'type':'disabled'} for r in FixedProvider.requests))
                with self.assertRaisesRegex(IFLineError,'native_http_error: 409'):
                    adapter._request('PUT',f"/api/projects/{result['native_project_id']}/bible-head",
                        {'revision_id':handle['bible_revision_id']},{'If-Match':'"1"'})
                adapter.close(handle);handle=None
                self.assertEqual(source_fingerprint(repo),before)
                events=[json.loads(line) for file in (root/'run/trace').glob('if_line-*.jsonl')
                    for line in file.read_text().splitlines()]
                completed=[event for event in events if event.get('boundary')=='http' and event['event']=='completed']
                started=[event for event in events if event.get('boundary')=='http' and event['event']=='started']
                self.assertEqual(len(completed),count)
                self.assertEqual(len(started),count)
                self.assertEqual(len({event['call_id'] for event in completed}),count)
                (out/'result.json').write_text(json.dumps({'evidence_kind':'engineering_fixed_response',
                    'api':'actual_native_http','auth':'native_guest_sid','worker':'native_celery_solo_process',
                    'broker_delivery':'actual_isolated_redis_with_celery_beat_outbox',
                    'storage':'actual_disposable_postgresql','migrations':'native_alembic_upgrade_head',
                    'paid_model_calls':0,'fixed_http_calls':count,'source_unchanged':True,
                    'source_sha256':before['sha256'],'result':result,'export':exported},ensure_ascii=False,indent=2))
            finally:
                if adapter is not None:
                    try:
                        if handle is not None: adapter.close(handle)
                        else: adapter._stop_managed()
                    except Exception: pass
                if provider: provider.shutdown();provider.server_close()
                if thread: thread.join(timeout=5)
                subprocess.run([str(pg_bin/'pg_ctl'),'-D',str(cluster),'stop','-m','fast','-w'],
                               check=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                for key,value in previous.items():
                    if value is None: os.environ.pop(key,None)
                    else: os.environ[key]=value
                if (root/'run').exists():
                    shutil.copytree(root/'run',out/'run')

if __name__=='__main__': unittest.main()
