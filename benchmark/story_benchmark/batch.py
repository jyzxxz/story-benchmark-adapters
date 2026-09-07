"""Independent native batch entry points, immutable plans, no generation caps."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import importlib
import json
import math
import os
import platform
from pathlib import Path
import signal
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import uuid

from .compiler import verify_bundle
from .io import BenchmarkError, atomic_json, read_json, redact, sha256, safe_child
from .recording import verify_recording, utc_now

SYSTEMS = {'if_line': 'if_line', 'ai4visualnovel': 'ai4vn', 'infiplot': 'infiplot'}
PATH_FIELDS = {'repo_path','repo_dir','source_lock','python_executable','redis_executable',
               'node_executable','pnpm_executable','runtime_root','rembg_model_dir',
               'playwright_module','dependency_repo_path','node_modules','pg_bin','chromium_executable'}
FORBIDDEN_OVERRIDE = {'model','model_base_url','model_parameters','image_model','vision_model',
    'providers','policy','budget_mode','live','allow_pilot','count','concurrency',
    'max_calls','max_output_tokens','max_input_chars','timeout_seconds','window_chars'}


def driver_for(system):
    return importlib.import_module('.batch_native.'+SYSTEMS[system], __package__)


def load_batch_config(path):
    path = Path(path).resolve()
    raw = read_json(path)
    allowed = {'schema_version','evidence_kind','allow_pilot','providers','systems','bundles',
               'choice_indices','reading_delay_seconds','render_mode'}
    if raw.get('schema_version') != 'batch.1' or set(raw)-allowed:
        raise BenchmarkError('invalid_batch_schema')
    if raw.get('evidence_kind') not in ('live','fixture') or type(raw.get('allow_pilot')) is not bool:
        raise BenchmarkError('explicit_evidence_kind_and_pilot_policy_required')
    if redact(raw) != raw:
        raise BenchmarkError('inline_credentials_forbidden_use_environment_names')
    providers = raw.get('providers', {})
    if set(providers) != {'text','vision','image'}:
        raise BenchmarkError('common_text_vision_image_providers_required')
    for role, provider in providers.items():
        if set(provider)-{'model','base_url','api_key_env','parameters'} or not all(
                isinstance(provider.get(k),str) and provider[k] for k in ('model','base_url','api_key_env')):
            raise BenchmarkError('invalid_provider:'+role)
        parsed = urlsplit(provider['base_url'])
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BenchmarkError('invalid_provider_endpoint:'+role)
        if raw['evidence_kind']=='fixture' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise BenchmarkError('fixture_must_use_local_provider')
        # Require an explicit versioned base; never guess vendor routing.
        if not parsed.path.rstrip('/').endswith('/v1'):
            raise BenchmarkError('provider_base_url_must_end_v1:'+role)
        parameters=provider.get('parameters',{})
        if not isinstance(parameters,dict) or set(parameters) & {'messages','prompt','model','n','stream','tools','response_format','max_tokens','max_completion_tokens'}:
            raise BenchmarkError('parameters_must_not_replace_task_or_native_format_or_set_budget:'+role)
        if role=='image' and parameters:
            raise BenchmarkError('image_parameters_remain_native_and_are_recorded')
        if role!='image':
            from .model_parameters import validate_model_parameters
            validate_model_parameters(parameters)
    if providers['image']['model'] != 'gpt-image-2':
        raise BenchmarkError('this_protocol_requires_common_gpt_image_2')
    if set(raw.get('systems',{})) != set(SYSTEMS):
        raise BenchmarkError('three_system_configurations_required')
    configs={}
    for system,specific in raw['systems'].items():
        if set(specific) & FORBIDDEN_OVERRIDE:
            raise BenchmarkError('per_system_common_condition_override:'+system)
        config=dict(specific)
        for field in PATH_FIELDS:
            if config.get(field):
                # Resolving a venv interpreter symlink selects its base Python
                # and silently loses the native virtual environment.
                config[field]=os.path.abspath(path.parent/Path(config[field]).expanduser())
        config.update(live=raw['evidence_kind']=='live', allow_pilot=raw['allow_pilot'],
            model=providers['text']['model'],model_base_url=providers['text']['base_url'],
            model_parameters=providers['text'].get('parameters',{}),
            image_model=providers['image']['model'],vision_model=providers['vision']['model'],
            budget_mode='unlimited',max_calls=None,max_output_tokens=None,max_input_chars=None,timeout_seconds=None)
        configs[system]=config
    bundles=raw.get('bundles')
    if not isinstance(bundles,list) or not bundles or len(set(bundles))!=len(bundles):
        raise BenchmarkError('nonempty_unique_common_bundle_list_required')
    bundles=[str((path.parent/p).resolve()) for p in bundles]
    indices=raw.get('choice_indices',[0])
    if not isinstance(indices,list) or not indices or any(type(i) is not int or i<0 for i in indices):
        raise BenchmarkError('nonnegative_choice_indices_required')
    delay=raw.get('reading_delay_seconds',0)
    if type(delay) not in (int,float) or not math.isfinite(delay) or delay<0:
        raise BenchmarkError('nonnegative_reader_delay_required')
    if raw.get('render_mode','offscreen_native')!='offscreen_native':
        raise BenchmarkError('only_offscreen_native_capture_supported')
    return {'schema_version':'batch.1','source_config_sha256':sha256(path.read_bytes()),
        'providers':providers,'systems':configs,'bundles':bundles,
        'policy':{'choice_indices':indices,'reading_delay_seconds':delay,'render_mode':'offscreen_native',
                  'evidence_kind':raw['evidence_kind']},'allow_pilot':raw['allow_pilot']}


def check_bundle(bundle, allow_pilot):
    check=verify_bundle(bundle)
    manifest=read_json(Path(bundle)/'manifest.json')
    contract=manifest.get('output_contract',{})
    if manifest.get('profile')!='FULL_VN' or contract.get('version')!='4.0' or contract.get('media')!='images':
        raise BenchmarkError('batch_requires_v4_image_reading_window_bundle')
    if manifest.get('review_status')!='approved' and not allow_pilot:
        raise BenchmarkError('unapproved_bundle_requires_allow_pilot')
    return manifest


def preflight_batch(system,config):
    reports=[]
    missing=[role for role,p in config['providers'].items() if not os.environ.get(p['api_key_env'])]
    for bundle in config['bundles']:
        try:
            manifest=check_bundle(bundle,config['allow_pilot'])
            policy={**config['policy'],'window_chars':manifest['output_contract']['window_chars']}
            report=driver_for(system).preflight(config['systems'][system],Path(bundle),policy)
            reports.append({'bundle':bundle,**report})
        except Exception as exc:
            reports.append({'bundle':bundle,'ok':False,'errors':[str(exc)]})
    return {'ok':not missing and all(r['ok'] for r in reports),'system':system,
            'missing_credential_roles':missing,'bundles':reports,'generation':'not_run'}


def prepare_plan(system,config,out,count):
    from .runner import adapter_source_inventory
    if type(count) is not int or count<=0:
        raise BenchmarkError('positive_story_count_required')
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    frozen=[]
    import shutil
    for i,bundle in enumerate(config['bundles']):
        manifest=check_bundle(bundle,config['allow_pilot'])
        target=out/'bundles'/str(i)
        shutil.copytree(bundle,target)
        frozen.append({'path':str(target),'manifest':manifest})
    batch_id=uuid.uuid4().hex
    jobs=[]
    for index in range(count):
        entry=frozen[index%len(frozen)]
        repeat=index//len(frozen)+1
        run_id=f'{system}-{index+1:05d}-{batch_id[:12]}'
        jobs.append({'run_id':run_id,'index':index,'case_id':entry['manifest']['case_id'],
            'repeat':repeat,'bundle':entry['path'],'shared_sha256':entry['manifest']['shared_sha256']})
    plan={'schema_version':'batch-plan.1','batch_id':batch_id,'system':system,'count':count,
          'configuration':config,'jobs':jobs,'adapter_source_inventory':adapter_source_inventory(),
          'note':'One actual native path per independent root; no automatic failed-job retries.'}
    atomic_json(out/'plan.json',plan)
    atomic_json(out/'plan.sha256.json',{'sha256':sha256((out/'plan.json').read_bytes())})
    return plan


def read_plan(out):
    out=Path(out)
    if sha256((out/'plan.json').read_bytes())!=read_json(out/'plan.sha256.json')['sha256']:
        raise BenchmarkError('frozen_batch_plan_changed')
    plan=read_json(out/'plan.json')
    for job in plan['jobs']:
        m=check_bundle(job['bundle'],plan['configuration']['allow_pilot'])
        if m['shared_sha256']!=job['shared_sha256']:
            raise BenchmarkError('frozen_batch_input_changed')
    return plan


def summarize(out,plan):
    rows=[]
    for job in plan['jobs']:
        root=Path(out)/'runs'/job['run_id']
        state_path=Path(out)/'states'/(job['run_id']+'.json')
        state=read_json(state_path) if state_path.exists() else {'state':'queued'}
        if (root/'manifest.json').exists():
            manifest=read_json(root/'manifest.json')
            verified=verify_recording(root)
            metrics=read_json(root/'metrics.json')
            rows.append({**job,'state':'sealed','evidence_verified':verified['ok'],
                'scope_reached':manifest['scope_reached'],'native_ended':manifest['native_ended'],
                'stop_reason':manifest['stop_reason'],'visible_chars':manifest['visible_chars'],
                'input_audit':manifest['input_audit']['status'],'metrics':metrics})
        else:
            rows.append({**job,**state,'scope_reached':None,'native_ended':None,
                'stop_reason':'delivery_unknown' if state.get('state') in ('running','interrupted','worker_failed') else 'not_started'})
    summary={'schema_version':'batch-results.1','batch_id':plan['batch_id'],'system':plan['system'],
             'all_attempts_retained':True,'quality_scores_computed':False,'runs':rows}
    atomic_json(Path(out)/'results.json',summary)
    columns=('run_id','case_id','repeat','state','scope_reached','native_ended','stop_reason','visible_chars','input_audit')
    with (Path(out)/'results.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
    return summary


def execute_plan(out,concurrency):
    from .runner import adapter_source_inventory
    if type(concurrency) is not int or concurrency<=0:
        raise BenchmarkError('positive_concurrency_required')
    out=Path(out).resolve();plan=read_plan(out)
    if plan.get('adapter_source_inventory')!=adapter_source_inventory():
        raise BenchmarkError('adapter_changed_since_plan_create_a_new_batch_explicitly')
    # Exclusive scheduler lock prevents duplicate billing from simultaneous resumes.
    lock=out/'scheduler.lock'
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise BenchmarkError('scheduler_lock_exists_inspect_process_before_recovery')
    os.write(fd,str(os.getpid()).encode());os.close(fd)
    session={'session_id':uuid.uuid4().hex,'started_at':utc_now(),
        'requested_concurrency':concurrency,'worker_capacity':min(concurrency,plan['count']),
        'host':{'system':platform.system(),'release':platform.release(),'machine':platform.machine(),
                'logical_cpus':os.cpu_count()},
        'other_host_workload':'not_measured','provider_rate_limits':'not_measured'}
    session_path=out/'scheduling'/(session['session_id']+'.json')
    atomic_json(session_path,session)
    processes={};guard=threading.Lock();cancelled=threading.Event()
    old_handlers={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    def cancel(signum,frame):
        cancelled.set()
        with guard:
            for process in processes.values():
                if process.poll() is None:process.send_signal(signal.SIGTERM)
    if threading.current_thread() is threading.main_thread():
        for sig in old_handlers:signal.signal(sig,cancel)
    def one(job):
        state_path=out/'states'/(job['run_id']+'.json')
        root=out/'runs'/job['run_id']
        # Never resend a possibly delivered/paid run. Resume only untouched queue.
        if state_path.exists() or root.exists() or cancelled.is_set():return
        worker_start=utc_now()
        atomic_json(state_path,{'state':'running','scheduler_pid':os.getpid(),'worker_started_at':worker_start,
                               'scheduling_context':session})
        log=out/'launch_logs'/(job['run_id']+'.log');log.parent.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ)
        env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])+os.pathsep+env.get('PYTHONPATH','')
        with log.open('wb') as stream:
            process=subprocess.Popen([sys.executable,'-m','story_benchmark.batch_worker',
                '--plan',str(out/'plan.json'),'--index',str(job['index'])],env=env,
                cwd=str(Path(__file__).resolve().parents[1]),stdout=stream,stderr=subprocess.STDOUT)
            with guard:
                processes[job['run_id']]=process
                if cancelled.is_set():process.send_signal(signal.SIGTERM)
            code=process.wait()
        with guard:processes.pop(job['run_id'],None)
        atomic_json(state_path,{'state':'sealed' if (root/'manifest.json').exists() else 'interrupted' if cancelled.is_set() else 'worker_failed',
                              'exit_code':code,'log':str(log.relative_to(out)),
                              'worker_started_at':worker_start,'worker_finished_at':utc_now(),'scheduling_context':session})
    try:
        with ThreadPoolExecutor(max_workers=min(concurrency,plan['count'])) as pool:
            futures=[pool.submit(one,job) for job in plan['jobs']]
            for future in as_completed(futures):future.result()
        return summarize(out,plan)
    finally:
        session_workers=[]
        for job in plan['jobs']:
            state_path=out/'states'/(job['run_id']+'.json')
            if state_path.exists():
                state=read_json(state_path)
                if state.get('scheduling_context',{}).get('session_id')==session['session_id']:
                    session_workers.append({'run_id':job['run_id'],**state})
        atomic_json(session_path,{**session,'finished_at':utc_now(),'interrupted':cancelled.is_set(),
            'workers':session_workers})
        if threading.current_thread() is threading.main_thread():
            for sig,handler in old_handlers.items():signal.signal(sig,handler)
        lock.unlink(missing_ok=True)


def main(system=None):
    parser=argparse.ArgumentParser(description='原生图文故事批量生成与八项评审证据采集')
    if system is None:parser.add_argument('--system',choices=SYSTEMS,required=True)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--count',type=int,default=1,help='根运行总数；按共同题目顺序循环')
    parser.add_argument('--concurrency',type=int,default=1)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--resume',action='store_true',help='只启动尚未开始的排队任务')
    parser.add_argument('--verify',action='store_true',help='重算已封存证据与指标；不调用模型')
    args=parser.parse_args();system=system or args.system
    try:
        if args.resume or args.verify:
            if not args.out:raise BenchmarkError('--out_required')
            plan=read_plan(args.out)
            if plan['system']!=system:raise BenchmarkError('batch_system_mismatch')
            result=summarize(args.out,plan) if args.verify else execute_plan(args.out,args.concurrency)
        else:
            if not args.config:raise BenchmarkError('--config_required')
            config=load_batch_config(args.config)
            if args.preflight:result=preflight_batch(system,config)
            else:
                if not args.out:raise BenchmarkError('--out_required')
                report=preflight_batch(system,config)
                if not report['ok']:print(json.dumps(report,ensure_ascii=False,indent=2));return 2
                plan=prepare_plan(system,config,args.out,args.count)
                result={'state':'planned','count':len(plan['jobs']),'out':str(args.out)} if args.plan_only else execute_plan(args.out,args.concurrency)
        # Full evidence remains on disk; compact terminal summary only.
        if 'runs' in result:
            result={'batch_id':result['batch_id'],'system':system,'out':str(args.out),
                    'runs':[{k:r.get(k) for k in ('run_id','state','scope_reached','stop_reason','input_audit')} for r in result['runs']]}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result.get('ok',True) else 2
    except (BenchmarkError,OSError,KeyError,ValueError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 2


if __name__=='__main__':
    raise SystemExit(main())
