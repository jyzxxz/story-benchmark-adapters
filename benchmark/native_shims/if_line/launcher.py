"""Launch immutable IF Line through the external runtime adapter.

engineering-server is explicitly SQLite + one direct worker thread + local fixed
provider. api/worker are separate native processes configured for an isolated
PostgreSQL/Redis deployment; they never default to a daily project's database.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import runpy
import signal
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from .bootstrap import install, verify_source_unchanged, BASE_COMMIT
from . import trace


def configure(config,mode):
    repo=Path(config.get('repo_path',config.get('repo_dir',''))).resolve()
    runtime=Path(config['runtime_dir']).resolve()
    if not str(config.get('repo_path',config.get('repo_dir',''))):
        raise ValueError('repo_path is required')
    from story_benchmark.provenance import verify_repository
    report=verify_repository(repo,BASE_COMMIT,config)
    if not report['ok']:
        raise ValueError('; '.join(report['errors']))
    runtime.mkdir(parents=True,exist_ok=True)
    if repo in runtime.parents or repo == runtime:
        raise ValueError('runtime must be outside source')
    required=('root_run_id','trace_dir','max_calls','max_output_tokens','max_input_chars','model','model_base_url')
    for key in required:
        if not config.get(key): raise ValueError('missing configuration: '+key)
    provider=urlsplit(config['model_base_url'])
    if provider.scheme not in ('http','https') or not provider.hostname or provider.username or provider.password or provider.query:
        raise ValueError('model_base_url must be a credential-free http(s) endpoint')
    os.environ.update(BENCH_RUN_ID=config['root_run_id'],BENCH_TRACE_DIR=str(Path(config['trace_dir']).resolve()),
        BENCH_MAX_CALLS=str(config['max_calls']),BENCH_MAX_OUTPUT_TOKENS=str(config['max_output_tokens']),
        BENCH_MAX_INPUT_CHARS=str(config['max_input_chars']),LLM_MODEL=config['model'],
        OPENAI_BASE_URL=config['model_base_url'],APP_ENV='test' if mode.startswith('engineering') else 'development',
        CHECK_DEPENDENCIES_ON_STARTUP='false',DATABASE_URL='sqlite:///'+str(runtime/'native.sqlite3'))
    os.environ['BENCH_MODEL_PARAMETERS']=json.dumps(config.get('model_parameters',{}),ensure_ascii=False,separators=(',',':'))
    # Both are native text settings used by the Script IR resource planner.
    os.environ.update(PROMPT_REWRITER_MODEL=config['model'],STYLE_CLASSIFIER_MODEL=config['model'],
        STYLE_CLASSIFIER_BASE_URL=config['model_base_url'])
    if not mode.startswith('engineering') and mode!='entry-check':
        database=os.environ.get(config.get('database_url_env','')) or config.get('database_url')
        broker=os.environ.get(config.get('redis_url_env','')) or config.get('redis_url')
        if not database or not broker or not config.get('isolated_deployment'):
            raise ValueError('native api/worker require explicit isolated database_url, redis_url and isolated_deployment')
        os.environ['DATABASE_URL']=database
        os.environ['REDIS_URL']=broker
        os.environ['CELERY_BROKER_URL']=broker
        # Keep authentication values only in the child environment.
        key=os.environ.get(config.get('model_api_key_env','OPENAI_API_KEY'))
        if not key:
            raise ValueError('missing explicitly configured text model key')
        os.environ['OPENAI_API_KEY']=key
        os.environ['OPENAI_API_KEYS']=''
        os.environ['STYLE_CLASSIFIER_API_KEY']=key
        os.environ['STYLE_CLASSIFIER_API_KEYS']=''
    return repo,runtime


def native_services(config):
    """Actual PostgreSQL/API/Celery/beat; no provider call during initialization."""
    runtime=Path(config['runtime_dir']).resolve()
    runtime.mkdir(parents=True,exist_ok=True)
    processes=[]
    logs=[]
    redis_server=None
    config=dict(config)
    if config.get('redis_executable'):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            redis_port=sock.getsockname()[1]
        redis_dir=runtime/'redis'
        redis_dir.mkdir(exist_ok=True)
        log=(runtime/'redis.log').open('a')
        logs.append(log)
        redis_server=subprocess.Popen([config['redis_executable'],'--bind','127.0.0.1','--port',str(redis_port),
            '--dir',str(redis_dir),'--save','','--appendonly','no'],stdout=log,stderr=subprocess.STDOUT)
        processes.append(redis_server)
        config['redis_url']=f'redis://127.0.0.1:{redis_port}/0'
        config.pop('redis_url_env',None)
    repo=None
    try:
        repo,runtime=configure(config,'native-services')
        from sqlalchemy.engine import make_url
        url=make_url(os.environ['DATABASE_URL'])
        if url.get_backend_name()!='postgresql' or not (url.database or '').startswith('if_line_bench_'):
            raise ValueError('managed native services require a dedicated PostgreSQL database named if_line_bench_*')
        install(repo,runtime,config)
        from app.main import create_app
        from app.database import engine,check_database_schema
        from sqlalchemy import inspect
        tables=inspect(engine).get_table_names()
        if not tables:
            from alembic import command
            from alembic.config import Config
            command.upgrade(Config(str(repo/'backend/alembic.ini')),'head')
        else:
            check_database_schema()
        child_config=runtime/'native-child-config.json'
        child_config.write_text(json.dumps(config,indent=2))
        for role in ('worker','beat'):
            log=(runtime/f'{role}.log').open('a')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable,'-m','native_shims.if_line.launcher',role,
                '--config',str(child_config)],stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ)))
        app=create_app()
        @app.get('/__benchmark__/health')
        def health():
            receipt=Path(config['trace_dir'])/'if_line_worker_receipt.json'
            ready=receipt.exists() and all(p.poll() is None for p in processes)
            if not ready:
                from fastapi import HTTPException
                raise HTTPException(503,'isolated worker is starting')
            return {'root_run_id':config['root_run_id'],'system':'if_line','execution_environment':'native_services'}
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=int(config['port']),log_level='warning')
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs: log.close()
        trace.finalize_pending()
        if repo is not None and (runtime/'source-before.json').exists():
            verify_source_unchanged(repo,runtime)


def engineering_server(config):
    from http.server import ThreadingHTTPServer
    from .fixtures import FixedProvider
    # No shell or developer credentials reach this fixture deployment.
    for key in list(os.environ):
        if any(part in key for part in ('API_KEY','API_KEYS')):
            os.environ[key]=''
    FixedProvider.requests=[]
    FixedProvider.chapter_count=int(config.get('chapter_count',13))
    FixedProvider.model=config['model']
    provider=ThreadingHTTPServer(('127.0.0.1',0),FixedProvider)
    provider_thread=threading.Thread(target=provider.serve_forever,daemon=True)
    provider_thread.start()
    requested=dict(config)
    config={**config,'model_base_url':f'http://127.0.0.1:{provider.server_port}/v1'}
    repo,runtime=configure(config,'engineering-server')
    os.environ['OPENAI_API_KEY']='fixture-key-not-a-real-credential'
    install(repo,runtime,config)
    from app.main import create_app
    from app.database import Base,engine,SessionLocal
    from app.models_v2 import GenerationTask
    from app.workers.tasks import run_generation_task
    Base.metadata.create_all(engine)
    # Engineering fixture schema only; this is not a PostgreSQL migration test.
    from alembic import command
    from alembic.config import Config
    command.stamp(Config(str(repo/'backend/alembic.ini')), 'head')
    app=create_app()
    stop=threading.Event()
    def work():
        while not stop.wait(0.1):
            with SessionLocal() as db:
                row=db.query(GenerationTask).filter(GenerationTask.status=='queued').order_by(GenerationTask.created_at).first()
                task_id=row.id if row else None
            if task_id:
                run_generation_task.run(task_id)
    worker=threading.Thread(target=work,daemon=True)
    worker.start()
    trace.write_worker_receipt()
    receipt_path=Path(config['trace_dir'])/'if_line_worker_receipt.json'
    receipt=json.loads(receipt_path.read_text())
    receipt.update(execution_environment='engineering_fixed_response',broker_delivery='not_exercised',
        model_base_url=config['model_base_url'],requested_model_base_url=requested['model_base_url'])
    receipt_path.write_text(json.dumps(receipt,indent=2))
    @app.get('/__benchmark__/health')
    def health():
        return {'root_run_id':config['root_run_id'],'system':'if_line','execution_environment':'engineering_fixed_response',
                'model':config['model'],'provider_http_calls':len(FixedProvider.requests)}
    import uvicorn
    try:
        uvicorn.run(app,host='127.0.0.1',port=int(config['port']),log_level='warning')
    finally:
        stop.set()
        worker.join(timeout=10)
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=5)
        trace.finalize_pending()
        (runtime/'fixed-provider-requests.json').write_text(json.dumps(trace.redact(FixedProvider.requests),ensure_ascii=False,indent=2))
        (runtime/'engineering-environment.json').write_text(json.dumps({'execution_environment':'engineering_fixed_response',
            'native_auth':'sid_cookie','worker':'native_task_function_on_serial_thread','broker_delivery':'not_exercised',
            'database':'isolated_sqlite','schema_initialization':'native_orm_metadata_then_alembic_stamp_not_migration_test',
            'paid_model_calls':0,'fixed_http_calls':len(FixedProvider.requests)},indent=2))
        verify_source_unchanged(repo,runtime)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('entry-check','engineering-server','native-services','api','worker','beat'))
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).resolve().read_text())
    if args.mode=='engineering-server':
        return engineering_server(config)
    if args.mode=='native-services':
        return native_services(config)
    repo,runtime=configure(config,args.mode)
    install(repo,runtime,config)
    try:
        if args.mode=='entry-check':
            from app.main import create_app
            app=create_app()
            schema=app.openapi()
            (runtime/'openapi.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2))
            from app.services.prompt_templates import get_story_bible_prompt
            for characters in ([], [{'name':'林'}], ['陈']):
                values=('标题',characters,'开头','结局','风格','medium','完整要求')
                assert get_story_bible_prompt(*values)==get_story_bible_prompt.__wrapped__(*values)
            token=trace._context.set({'benchmark_input_mode':'shared_task'})
            try:
                shared='共同任务\n开头已经发生。'
                prompt=get_story_bible_prompt('标题',[],'','','','medium',shared)
                assert prompt.count(shared)==1 and '请自行设计主角' not in prompt
            finally:
                trace._context.reset(token)
            try:
                (repo/'__benchmark_write_probe__').write_text('must be denied')
            except PermissionError:
                pass
            else:
                raise AssertionError('source write guard did not enforce isolation')
            print(json.dumps({'entry_import':'passed','path_count':len(schema['paths']),'source_modified':False,
                'default_prompt_unchanged':True,'shared_input_single_occurrence':True,'source_write_guard':'passed'}))
        elif args.mode=='api':
            from app.main import create_app
            import uvicorn
            uvicorn.run(create_app(),host='127.0.0.1',port=int(config['port']),log_level='warning')
        elif args.mode=='worker':
            sys.argv=['celery','-A','app.workers.celery_app:celery_app','worker','--pool=solo','--concurrency=1',
                      '--queues=text,maintenance','--loglevel=INFO']
            runpy.run_module('celery',run_name='__main__')
        else:
            sys.argv=['celery','-A','app.workers.celery_app:celery_app','beat','--loglevel=INFO',
                      '--schedule',str(runtime/'celerybeat-schedule')]
            runpy.run_module('celery',run_name='__main__')
    finally:
        trace.finalize_pending()
        verify_source_unchanged(repo,runtime)

if __name__=='__main__': main()
