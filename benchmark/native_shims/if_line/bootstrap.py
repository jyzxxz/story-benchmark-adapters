"""Install transparent input, isolation and observation rules outside IF Line."""
from __future__ import annotations
import copy
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys

from . import trace
BASE_COMMIT = '572407fce9b648a4206ac37da6a9f6ed22631da8'
_installed = None


def source_fingerprint(repo):
    root = Path(repo).resolve()
    files = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x != '.git']
        for name in names:
            path = Path(directory)/name
            if path == root/'.git':
                continue
            if path.is_symlink():
                files[str(path.relative_to(root))] = 'symlink:' + os.readlink(path)
            else:
                files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'sha256':digest, 'file_count':len(files), 'files':files}


def protect_source(repo):
    root = Path(repo).resolve()
    def inside(path):
        if not isinstance(path,(str,bytes,os.PathLike)):
            return False
        try:
            Path(os.fsdecode(path)).resolve().relative_to(root)
            return True
        except (ValueError,OSError):
            return False
    def guard(event,args):
        if event == 'open':
            path,mode,flags = args
            write = (isinstance(mode,str) and any(x in mode for x in 'wax+')) or (
                isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if write and inside(path):
                raise PermissionError('benchmark upstream source is read-only')
        elif event in {'os.mkdir','os.remove','os.rmdir','os.chmod','os.truncate','os.utime'}:
            if args and inside(args[0]):
                raise PermissionError('benchmark upstream source is read-only')
        elif event in {'os.rename','os.replace','os.symlink','os.link'}:
            if any(inside(x) for x in args[:2]):
                raise PermissionError('benchmark upstream source is read-only')
    sys.addaudithook(guard)


def install(repo, runtime, config=None):
    """Called in each native process before importing app.main or worker tasks."""
    global _installed
    config = dict(config or {})
    repo,runtime = Path(repo).resolve(),Path(runtime).resolve()
    if _installed:
        if _installed != (repo,runtime):
            raise RuntimeError('one IF Line root run per process')
        return
    if repo == runtime or repo in runtime.parents:
        raise ValueError('runtime directory must be outside immutable source')
    if any(path.exists() for path in (repo/'.env',repo/'backend/.env')):
        raise ValueError('native .env forbidden; use isolated launcher environment')
    if 'app.services.prompt_templates' in sys.modules:
        raise RuntimeError('IF shim must be installed before native prompt modules import')
    runtime.mkdir(parents=True,exist_ok=True)
    before = source_fingerprint(repo)
    (runtime/'source-before.json').write_text(json.dumps(before,indent=2))
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    protect_source(repo)
    sys.path.insert(0,str(repo/'backend'))
    os.chdir(runtime)
    os.environ.update(STATIC_DIR=str(runtime/'static'),AUTH_RATELIMIT_BACKEND='memory',
        TTS_ENABLED='false',TTS_FALLBACK_ENABLED='false',TTS_OUTPUT_DIR=str(runtime/'static/tts_cache'),
        IMAGE_OUTPUT_DIR=str(runtime/'static/assets'))
    # Native defaults derive log/cache paths from __file__. Redirect only paths.
    from app.utils import logging as xlog
    original_setup = xlog.setup_logger
    xlog.setup_logger = lambda root=None: original_setup(runtime/'logs')
    from app.services import voice_clone_storage_service as storage
    storage.STATIC_DIR = runtime/'static'
    storage.VOICE_REFS_DIR = storage.STATIC_DIR/'voice_refs'
    storage.TTS_CACHE_DIR = storage.STATIC_DIR/'tts_cache'
    storage.VOICE_CLONE_CACHE_DIR = storage.TTS_CACHE_DIR/'voice_clone'
    storage.VOICE_CLONE_UPLOAD_TMP_DIR = runtime/'storage/tmp/voice_clone'

    from app.services import api_key_pool as pool
    for cls,sync in ((pool.PooledAsyncOpenAI,False),(pool.PooledOpenAI,True)):
        old_new = cls._new_client
        def new_client(self,api_key,*,base_url_override=None,_old=old_new,_sync=sync):
            endpoint=base_url_override or self._client_kwargs.get('base_url') or os.environ['OPENAI_BASE_URL']
            if str(endpoint).rstrip('/') != os.environ['OPENAI_BASE_URL'].rstrip('/'):
                raise RuntimeError('native text endpoint differs from frozen common endpoint')
            shadow=copy.copy(self)
            shadow._client_kwargs=trace.instrument_client_kwargs(self._client_kwargs,sync=_sync)
            return _old(shadow,api_key,base_url_override=base_url_override)
        cls._new_client = new_client
        old_execute = cls._execute
        if sync:
            def execute(self,path,method,kwargs,_old=old_execute):
                return _old(self,path,method,trace.apply_output_budget(kwargs,path))
        else:
            async def execute(self,path,method,kwargs,_old=old_execute):
                return await _old(self,path,method,trace.apply_output_budget(kwargs,path))
        cls._execute = execute

    from app.services import prompt_templates as prompts
    original_prompt = prompts.get_story_bible_prompt
    signature = inspect.signature(original_prompt)
    @functools.wraps(original_prompt)
    def shared_prompt(*args,**kwargs):
        native = original_prompt(*args,**kwargs)
        if trace._context.get().get('benchmark_input_mode') != 'shared_task':
            return native
        values=signature.bind(*args,**kwargs)
        values.apply_defaults()
        shared=values.arguments['extra_requirements']
        trace.validate_shared_input(values.arguments,None,'shared_task')
        if any(marker in shared for marker in ('【基础输入信息】','【核心创作要求】')):
            raise ValueError('shared text contains reserved native input boundary')
        start=native.index('【基础输入信息】\n')+len('【基础输入信息】\n')
        end=native.index('\n\n【核心创作要求】\n1.',start)
        return native[:start]+shared+native[end:]
    prompts.get_story_bible_prompt = shared_prompt

    from app.application import story_generation_service as generation
    original_execute = generation.execute_generation_task
    @functools.wraps(original_execute)
    def dispatch(task_id,lease):
        task=generation._get_task(task_id,lease)
        params=task.parameters or {}
        mode=params.get('benchmark_input_mode')
        if task.kind=='bible.generate' and mode is not None:
            trace.validate_shared_input((task.source_refs or {}).get('project_snapshot') or {},params.get('instructions'),mode)
        context={'native_task_id':task.id,'native_project_id':task.project_id,
            'stage':task.kind,'native_attempt':task.attempt,'benchmark_input_mode':mode}
        token=trace._context.set(context)
        try:
            return original_execute(task_id,lease)
        finally:
            trace._context.reset(token)
    generation.execute_generation_task = dispatch

    # Candidate generation has its own Celery task and does not pass through
    # story_generation_service. Only add observation context around its existing
    # verified provider call; leave native claim/source/lease/retry logic intact.
    from app.workers import branch_tasks
    original_branch_call = branch_tasks._call_provider_with_heartbeat
    @functools.wraps(original_branch_call)
    async def branch_call(task_id, **kwargs):
        with kwargs['session_factory']() as db:
            task=db.query(branch_tasks.GenerationTask).filter_by(id=task_id).one()
            context={'native_task_id':task.id,'native_project_id':task.project_id,
                     'stage':task.kind,'native_attempt':task.attempt}
        token=trace._context.set(context)
        try:
            return await original_branch_call(task_id,**kwargs)
        finally:
            trace._context.reset(token)
    branch_tasks._call_provider_with_heartbeat = branch_call

    from celery import signals
    def worker_ready(**_): trace.write_worker_receipt()
    signals.worker_ready.connect(worker_ready,weak=False)
    _installed=(repo,runtime)
    receipt={'system':'if_line','runtime_adapter':'external_if_line_v1','source_modified':False,
        'source_before_sha256':before['sha256'],'source_file_count':before['file_count'],
        'pid':os.getpid(),'repo_path':str(repo),'runtime_dir':str(runtime),
        'rules':['bible_base_input_only','task_trace_context','branch_task_trace_context','per_call_output_cap',
                 'http_observation','runtime_directory_isolation','source_write_guard']}
    (runtime/f'shim-receipt-{os.getpid()}.json').write_text(json.dumps(receipt,indent=2))
    return receipt


def verify_source_unchanged(repo,runtime):
    runtime=Path(runtime)
    before=json.loads((runtime/'source-before.json').read_text())
    after=source_fingerprint(repo)
    result={'source_unchanged':before==after,'before_sha256':before['sha256'],
            'after_sha256':after['sha256'],'file_count':after['file_count']}
    (runtime/'source-after.json').write_text(json.dumps(result,indent=2))
    if before != after:
        raise RuntimeError('native source changed during experiment')
    return result
