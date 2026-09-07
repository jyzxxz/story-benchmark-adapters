"""Source-preserving IF Line authoring-path and native-player batch driver.

No story instructions are added after the first Bible input. A structural
checkpoint selects an existing native revision and preserves native path state.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

from story_benchmark.adapters.if_line import IFLineAdapter, IFLineError, _read, _write, BASE_COMMIT
from story_benchmark.provenance import verify_repository


def preflight(config, bundle, policy):
    errors,checks=[],[]
    try:
        bundle=Path(bundle)
        case=_read(bundle/'case.json')
        output=case['output_contract']
        if output.get('version')!='4.0' or output.get('scope')!='readable_window':
            errors.append('if_line_batch_requires_v4_readable_window')
        if (bundle/'shared_task.txt').read_text() != _read(bundle/'payloads/if_line.json')['extra_requirements']:
            errors.append('shared_payload_mismatch')
        if not 0<len((bundle/'opening.txt').read_text())<=8000:
            errors.append('opening_exceeds_native_tail')
        if config.get('budget_mode')!='unlimited' or any(config.get(k,'missing') is not None
                for k in ('max_calls','max_output_tokens','max_input_chars','timeout_seconds')):
            errors.append('batch_requires_explicit_unlimited')
        if not policy.get('choice_indices') or any(type(i) is not int or i<0 for i in policy['choice_indices']):
            errors.append('invalid_choice_indices')
        if type(policy.get('window_chars')) is not int or policy['window_chars']<=0:
            errors.append('invalid_window_chars')
        if policy.get('evidence_kind')!='fixture' and output.get('window_chars')!=policy.get('window_chars'):
            errors.append('bundle_reading_window_mismatch')
        for field in ('model','image_model','vision_model'):
            if not config.get(field): errors.append('missing_'+field)
        for executable in ('python_executable','node_executable'):
            if not Path(config.get(executable,'')).is_file(): errors.append('missing_'+executable)
        if Path(config.get('python_executable','')).is_file():
            probe=subprocess.run([config['python_executable'],'-c',
                "import importlib.util,json; print(json.dumps([m for m in ['PIL','rembg','onnxruntime','aiohttp','celery','psycopg'] if importlib.util.find_spec(m) is None]))"],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
            if probe.returncode: errors.append('native_python_dependency_probe_failed')
            else: errors.extend('missing_native_dependency:'+m for m in json.loads(probe.stdout))
        if not (Path(config.get('rembg_model_dir',''))/'u2net.onnx').is_file(): errors.append('missing_native_rembg_model')
        deps=Path(config.get('node_modules',''))
        for name in ('vue','@vue/compiler-sfc','esbuild','playwright'):
            if not (deps/name).is_dir(): errors.append('missing_node_dependency:'+name)
        for name in ('initdb','pg_ctl','createdb'):
            if not (Path(config.get('pg_bin','/opt/homebrew/bin'))/name).is_file(): errors.append('missing_'+name)
        if not shutil.which(config.get('redis_executable','redis-server')): errors.append('missing_redis_server')
        report=verify_repository(Path(config['repo_path']),BASE_COMMIT,config)
        errors.extend(report['errors']);checks.extend(report['checks'])
        checks.append({'native_source':'immutable','path_choice_execution':'native_authoring_path_promotion',
            'audio':'disabled_text_and_images_scope','renderer':'original_VNGraphPlayer_headless_chromium'})
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        errors.append('invalid_batch_configuration:'+str(exc))
    return {'ok':not errors,'errors':errors,'checks':checks}


class _BatchAdapter(IFLineAdapter):
    def preflight(self,bundle_dir):
        return preflight(self.config,Path(bundle_dir),self.config['batch_policy'])
    def _wait_task(self,handle,name,task_id):
        try: return super()._wait_task(handle,name,task_id)
        except IFLineError:
            path=Path(handle['run_dir'])/'native'/f'{name}_task.json'
            if path.exists():
                task=_read(path)
                for child in task.get('result_refs',{}).get('child_task_ids',[]):
                    _write(path.parent/f'child_{child}.json',self._request('GET',f'/api/tasks/{child}'))
            raise


class _Postgres:
    """One disposable cluster per run: safe with independent parallel processes."""
    def __init__(self,root,config):
        self.root=Path(root);self.bin=Path(config.get('pg_bin','/opt/homebrew/bin'));self.started=False
    def command(self,*args):
        result=subprocess.run([str(self.bin/args[0]),*map(str,args[1:])],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        with (self.root/'control.log').open('a') as stream: stream.write(result.stdout)
        if result.returncode: raise IFLineError('isolated_postgres_failed',args[0])
    def start(self):
        self.root.mkdir(parents=True,exist_ok=False)
        self.cluster=self.root/'data';self.sockets=self.root/'socket';self.sockets.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.command('initdb','-D',self.cluster,'--no-locale','--encoding=UTF8','--auth=trust','-U','benchmark')
        # TCP only, so the long evidence directory never exceeds UNIX socket limits.
        self.command('pg_ctl','-D',self.cluster,'-l',self.root/'postgres.log','-o',
            f"-h 127.0.0.1 -p {self.port} -k ''",'start','-w')
        self.started=True
        self.command('createdb','-h','127.0.0.1','-p',self.port,'-U','benchmark','if_line_bench_batch')
        return f'postgresql+psycopg://benchmark@127.0.0.1:{self.port}/if_line_bench_batch'
    def close(self):
        if self.started:
            self.command('pg_ctl','-D',self.cluster,'stop','-m','fast','-w');self.started=False


def _native_file(recorder,handle,name,data):
    path=Path(handle['run_dir'])/'native'/name
    _write(path,data)
    return {'file':str(path.relative_to(recorder.root)),'pointer':''}


def _candidate_checkpoint(adapter,handle,recorder,path,chapter,step,initial=False):
    endpoint='prefix-checkpoints' if initial else 'selected-checkpoints'
    receipt=adapter._operation(handle,f'checkpoint_{step}','POST',
        f'/__benchmark__/path-chapters/{path["id"]}/{endpoint}',
        {'chapter_revision_id':chapter['id'],'opening_sha256':chapter['content_hash']},idempotent=True)
    source=_native_file(recorder,handle,f'checkpoint_{step}.json',receipt)
    cid=receipt['checkpoint']['id'];story_path=path['story_path_id']
    # Two options is an explicit interface cardinality, no option prose is injected.
    revision,task=adapter._generate(handle,f'candidate_{step}',
        f'/story-paths/{story_path}/checkpoints/{cid}/candidate-set-generations',
        {'chapter_revision_id':chapter['id'],'state_snapshot_id':receipt['state_snapshot_id'],'candidate_count':2},
        'candidate_set_revision_id')
    provider=task.get('source_refs',{}).get('candidate_set_source',{}).get('provider_input',{})
    if provider.get('instructions') != '' or provider.get('chapter_tail') != chapter['content'][-8000:]:
        raise IFLineError('native_candidate_snapshot_mismatch')
    adapter._activate(handle,f'activate_candidates_{step}',
        f'/story-paths/{story_path}/checkpoints/{cid}/candidate-set-head',revision)
    candidates=adapter._request('GET',f'/api/candidate-set-revisions/{revision}/candidates')
    native_source=_native_file(recorder,handle,f'candidates_{step}.json',candidates)
    recorder.event('choice_available',native_source=native_source,native_id=cid,
        choice_execution='native_authoring_path_promotion',candidate_count=len(candidates))
    return cid,candidates,native_source,source


def _select(adapter,handle,recorder,policy,step,candidates,native_source):
    executed=sum(c.get('selection_executed') is True for c in recorder._choices)
    index=policy['choice_indices'][min(executed,len(policy['choice_indices'])-1)]
    if index>=len(candidates): raise IFLineError('choice_index_out_of_range')
    selected=candidates[index]
    if policy.get('reading_delay_seconds',0):
        recorder.event('simulated_reading_started',duration_seconds=policy['reading_delay_seconds'])
        time.sleep(policy['reading_delay_seconds'])
        recorder.event('simulated_reading_completed')
    promoted=adapter._operation(handle,f'promote_{step}','POST',
        f'/api/branch-candidates/{selected["id"]}/story-paths',{},idempotent=True)
    _native_file(recorder,handle,f'promoted_path_{step}.json',promoted)
    if promoted.get('fork_candidate_id')!=selected['id']: raise IFLineError('promotion_receipt_mismatch')
    recorder.choice([{'id':c['id'],'label':c['option_key'],'raw':c} for c in candidates],
        selected_index=index,native_source=native_source,native_id=selected['candidate_set_revision_id'])
    recorder.event('choice_selected',native_candidate_id=selected['id'],story_path_id=promoted['id'],
        choice_execution='native_authoring_path_promotion')
    return promoted


def _image_output_link(gateway,digest,receipt=None):
    receipt=receipt or {}
    call_id=receipt.get('call_id');index=receipt.get('candidate_index')
    if call_id and type(index) is int:
        linked=gateway.output_for_call(call_id,index=index)
        mode='exact_native_call_and_index'
    else:
        linked=gateway.output_for_hash(digest)
        mode='unique_hash_fallback_without_native_receipt'
    if not isinstance(linked,dict) or linked.get('file_sha256') not in (None,digest):
        return None,{'method':'unresolved','attempted_method':mode}
    return linked.get('output_id'),{'method':mode,'call_id':linked.get('call_id',call_id),
        'candidate_index':linked.get('candidate_index',index),'hash_verified':linked.get('file_sha256')==digest}


def _media_receipt(results,asset_id):
    matching=[(i,r) for i,r in enumerate(results) if r.get('native_asset_id')==asset_id]
    if not matching: return {}
    index,receipt=matching[-1]
    native_result=receipt.get('native_result') or {}
    if native_result.get('cached') is True and not receipt.get('call_id'):
        path=native_result.get('image_path')
        prior=[(i,r) for i,r in enumerate(results[:index]) if path and r.get('call_id')
            and (r.get('native_result') or {}).get('image_path')==path]
        if prior:
            line,original=prior[-1]
            return {**receipt,'call_id':original['call_id'],'candidate_index':original['candidate_index'],
                'cached_from_native_result_line':line+1,'cache_identity_source':'native_cached_result_same_file_path'}
    return receipt


def _assets(adapter,handle,recorder,gateway,script_id,project_id,step):
    snapshot=adapter._request('GET',f'/__benchmark__/chapter-script-revisions/{script_id}/resource-snapshot')
    _native_file(recorder,handle,f'resources_{step}_before.json',snapshot)
    for role in ('portrait','background','keyframe'):
        if not any(s['role']==role for s in snapshot['slots']): continue
        accepted=adapter._operation(handle,f'render_{step}_{role}','POST',
            f'/api/chapter-script-revisions/{script_id}/resource-renders',{'role':role},idempotent=True)
        adapter._wait_task(handle,f'render_{step}_{role}',accepted['task_id'])
    snapshot=adapter._request('GET',f'/__benchmark__/chapter-script-revisions/{script_id}/resource-snapshot')
    source=_native_file(recorder,handle,f'resources_{step}.json',snapshot)
    by_url={}
    versions={}
    runtime=Path(handle['runtime_dir'])
    writes=[json.loads(x) for x in (runtime/'native-image-writes.jsonl').read_text().splitlines()] if (runtime/'native-image-writes.jsonl').exists() else []
    results=[json.loads(x) for x in (runtime/'native-media-results.jsonl').read_text().splitlines()] if (runtime/'native-media-results.jsonl').exists() else []
    provider_bytes=[json.loads(x) for x in (runtime/'native-image-provider-bytes.jsonl').read_text().splitlines()] if (runtime/'native-image-provider-bytes.jsonl').exists() else []
    for slot_no,slot in enumerate(snapshot['slots']):
        if slot['status']!='bound' or not slot['asset_version_id']:
            raise IFLineError('native_resource_unbound',slot['id'])
        vid=slot['asset_version_id']
        if vid in versions: continue
        version=adapter._request('GET',f'/api/projects/{project_id}/asset-versions/{vid}')
        native_source=_native_file(recorder,handle,f'asset_{vid}.json',version)
        url=version.get('media_url')
        if not url: raise IFLineError('native_media_url_missing')
        absolute=urllib.parse.urljoin(adapter.base,url)
        request=urllib.request.Request(absolute,headers={'Cookie':'sid='+adapter._cookie})
        with adapter._opener.open(request,timeout=60) as response: raw=response.read()
        file=Path(handle['run_dir'])/'native'/f'asset_{vid}{Path(urllib.parse.urlsplit(url).path).suffix or ".png"}'
        file.write_bytes(raw)
        final_hash=hashlib.sha256(raw).hexdigest()
        receipt=_media_receipt(results,slot['asset_id'])
        output_id,linkage=_image_output_link(gateway,final_hash,receipt)
        parent_asset=None
        if not output_id:
            native_path=(receipt.get('native_result') or {}).get('image_path')
            originals=[w for w in writes if w['native_path']==native_path]
            exact=[r for r in provider_bytes if receipt.get('call_id') and r.get('call_id')==receipt['call_id']
                and r.get('candidate_index')==receipt.get('candidate_index')]
            original=exact[-1] if exact else originals[-1] if originals else None
            if original:
                oid,parent_linkage=_image_output_link(gateway,original['raw_file_sha256'],original)
                if oid:
                    stream_name='native-image-provider-bytes.jsonl' if exact else 'native-image-writes.jsonl'
                    rows=provider_bytes if exact else writes
                    parent_asset=recorder.asset(Path(original['raw_file']),origin='generated',output_id=oid,
                        role=slot['role'],native_source={'file':str((runtime/stream_name).relative_to(recorder.root)),
                        'pointer':{'line':rows.index(original)+1},'candidate_linkage':parent_linkage})['asset_id']
        asset=recorder.asset(file,origin='generated' if output_id else 'derived',output_id=output_id,
            parent_asset_id=parent_asset,native_source={**native_source,'candidate_linkage':linkage,
                'cached_from_native_result_line':receipt.get('cached_from_native_result_line')},role=slot['role'])
        asset={**asset,'_native_character_ids':[str(c.get('character_id') or c.get('id') or c.get('name'))
            for c in version.get('render_spec',{}).get('extra_parameters',{}).get('characters',[])
            if c.get('character_id') or c.get('id') or c.get('name')]}
        versions[vid]=asset
        by_url[url]=asset;by_url[absolute]=asset
        if slot.get('character_id'):
            recorder.character(str(slot['character_id']),{'asset_version':version,'slot':slot},
                native_source={**source,'pointer':f'/slots/{slot_no}'},reference_asset_ids=[asset['asset_id']])
    return by_url,snapshot


def _render(adapter,handle,recorder,policy,config,graph,step,by_url,*,
            script_revision_id=None,script_generation_task_id=None):
    root=Path(handle['run_dir'])/'native'/f'playback_{step}';root.mkdir()
    input_file=root/'input.json'
    _write(input_file,{'graph':graph['graph_json'],'api_base_url':adapter.base,
        'choice_indices':policy['choice_indices'],
        'choice_offset':sum(c.get('selection_executed') is True for c in recorder._choices),
        'reading_delay_seconds':policy.get('reading_delay_seconds',0),
        'remaining_chars':recorder.window_chars-recorder.visible_chars})
    env={**os.environ,'IFLINE_RENDER_SID':adapter._cookie}
    command=[config['node_executable'],str(Path(__file__).resolve().parents[2]/'native_shims/if_line/render_native.cjs'),
        config['repo_path'],config['node_modules'],str(input_file),str(root)]
    with (root/'renderer.log').open('w') as log:
        completed=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
    if not (root/'playback.json').is_file(): raise IFLineError('native_renderer_failed',str(completed.returncode))
    playback=_read(root/'playback.json')
    playback['native_graph_source']={'file':str((Path(handle['run_dir'])/'native'/f'graph_{step}.json').relative_to(recorder.root)),
        'pointer':'/graph_json','graph_revision_id':graph['id'],'graph_hash':graph.get('graph_hash')}
    _write(root/'playback.json',playback)
    source={'file':str((root/'playback.json').relative_to(recorder.root)),'pointer':''}
    from story_benchmark.recording import jsonl
    source_calls=[row['call_id'] for row in jsonl(recorder.root/'telemetry/calls.jsonl')
        if script_generation_task_id and row.get('native_task_id')==script_generation_task_id
        and row.get('role')=='text' and row.get('call_id')]
    for i,beat in enumerate(playback['beats']):
        if recorder.scope_reached: break
        segment=recorder.story(beat['text'],speaker=beat.get('speaker') or None,
            kind='narration' if not beat.get('speaker') or beat['speaker'] in ('旁白','Narrator') else 'dialogue',
            native_source={**source,'pointer':f'/beats/{i}/text','availability_id':script_revision_id,
                'script_revision_id':script_revision_id,'script_generation_task_id':script_generation_task_id,
                'source_call_association':'all_text_attempts_for_native_script_task_not_winning_revision_attribution'},
            revision_id=graph['id'],native_id=str(beat['nodeIndex']),
            source_call_ids=source_calls or None) if beat.get('text') and not beat.get('options') else None
        stage=beat['stage'];urls=[stage['backgroundUrl'],stage['illustrationUrl']]
        if not stage['illustrationUrl']: urls.extend(p['url'] for p in stage['portraits'])
        unknown=[url for url in urls if url and url not in by_url]
        if unknown: recorder.error('unmapped_native_frame_assets','Native player asset URL lacks frozen asset version',urls=unknown)
        assets=[by_url[url]['asset_id'] for url in urls if url in by_url]
        recorder.frame(segment_ids=[segment['segment_id']] if segment else [],asset_ids=list(dict.fromkeys(assets)),
            clean_path=Path(beat['clean_path']),ui_path=Path(beat['ui_path']),
            character_ids=([p['id'] for p in stage['portraits']] if not stage['illustrationUrl'] else
                by_url.get(stage['illustrationUrl'],{}).get('_native_character_ids',[])),
            native_source={**source,'pointer':f'/beats/{i}'},capture_method='offscreen_native',
            placeholder=bool(beat['missing_images'] or unknown or any(not p['url'] for p in stage['portraits'])))
        timestamp={'clock_id':playback['clock_id'],'timestamp_observer_id':playback['clock_id'],
            'monotonic_ns':int(beat['monotonic_ns']),'utc':beat['observed_utc'],'observer':'offscreen_native'}
        recorder.event('frame_ready',**timestamp,observed_boundary='native_renderer_capture',
            segment_id=segment['segment_id'] if segment else None)
        if segment: recorder.event('story_text_available',**timestamp,segment_id=segment['segment_id'],
            observed_boundary='native_renderer_beat')
        for choice_no,choice in enumerate(playback['choices']):
            if choice['beat_index']!=i: continue
            if recorder.scope_reached: raise IFLineError('native_choice_after_reading_window')
            recorder.append('native/if_line_player_choices.jsonl',choice)
            choice_source={**source,'pointer':f'/choices/{choice_no}',
                'id_semantics':'native_option_array_index','renderer_clock_id':playback['clock_id'],
                'available_monotonic_ns':choice['available_monotonic_ns'],
                'committed_monotonic_ns':choice['committed_monotonic_ns']}
            recorder.choice([{'id':str(index),'label':option['text'],'raw_native':option}
                for index,option in enumerate(choice['options'])],selected_index=choice['selected_index'],
                native_source=choice_source,native_id=str(choice['native_node_index']))
            recorder.event('choice_selected',observer='offscreen_native',
                clock_id=playback['clock_id'],timestamp_observer_id=playback['clock_id'],
                monotonic_ns=int(choice['committed_monotonic_ns']),utc=choice['committed_utc'],
                interaction_id=f"{playback['clock_id']}-choice-{choice_no}",
                observed_boundary='native_renderer_committed_choice',native_source=choice_source)
    if completed.returncode: raise IFLineError('native_renderer_failed',str(completed.returncode))
    return playback


def run(config,bundle,run_dir,recorder,gateway,policy):
    config=dict(config);bundle=Path(bundle).resolve();run_dir=Path(run_dir).resolve()
    report=preflight(config,bundle,policy)
    if not report['ok']: raise IFLineError('preflight_failed',','.join(report['errors']))
    from native_shims.if_line.bootstrap import source_fingerprint
    before=source_fingerprint(config['repo_path'])
    recorder.save_bytes('native/if_line_source_before.json',json.dumps(before,ensure_ascii=False,indent=2).encode())
    db=_Postgres(run_dir/'native/postgres',config)
    adapter=None;handle=None;errors=[];ended=None;stop='native_error';paths=[]
    previous={k:os.environ.get(k) for k in ('IFLINE_BATCH_DB','IFLINE_BATCH_KEY','U2NET_HOME','NUMBA_CACHE_DIR')}
    try:
        os.environ['IFLINE_BATCH_DB']=db.start();os.environ['IFLINE_BATCH_KEY']=gateway.api_key
        os.environ['U2NET_HOME']=config.get('rembg_model_dir',str(run_dir/'native/rembg-models'))
        os.environ['NUMBA_CACHE_DIR']=str(run_dir/'native/numba-cache')
        config.update(root_run_id=recorder.run_id,trace_dir=str(run_dir/'native/trace'),model_base_url=gateway.url('text'),
            image_base_url=gateway.url('image'),vision_base_url=gateway.url('vision'),
            model_api_key_env='IFLINE_BATCH_KEY',database_url_env='IFLINE_BATCH_DB',managed_runtime='native_services',
            isolated_deployment=True,entry_mode='batch_readable_window',batch_media=True,batch_policy=policy,
            redis_executable=config.get('redis_executable',shutil.which('redis-server')),live=True)
        adapter=_BatchAdapter(config)
        handle=adapter.prepare(bundle,run_dir/'native/authoring');adapter._check_worker(handle)
        payload={k:v for k,v in _read(bundle/'payloads/if_line.json').items() if k!='benchmark_input_mode'}
        project=adapter._operation(handle,'project','POST','/api/projects',payload)
        shared=(bundle/'shared_task.txt').read_text()
        if project.get('extra_requirements')!=shared: raise IFLineError('receiver_input_mismatch')
        _native_file(recorder,handle,'project.json',project)
        pid=project['id'];path_id=project['root_story_path_id'];paths.append(path_id)
        bible,task=adapter._generate(handle,'bible',f'/projects/{pid}/bible-generations',
            {'parameters':{'benchmark_input_mode':'shared_task'}},'bible_revision_id')
        if task['source_refs']['project_snapshot']['extra_requirements']!=shared: raise IFLineError('task_snapshot_input_mismatch')
        recorder.save_json('native/received_if_line.json',{'received_task':task['source_refs']['project_snapshot']['extra_requirements'],
            'boundary':'native_task_snapshot','native_task_id':task['id']})
        adapter._get_revision(handle,f'/projects/{pid}/bible-revisions',bible,'bible_revision.json')
        adapter._activate(handle,'activate_bible',f'/projects/{pid}/bible-head',bible)
        outline,_=adapter._generate(handle,'outline_initial',f'/story-paths/{path_id}/outline-generations',
            {'chapter_count':int(config.get('chapter_count',13)),'bible_revision_id':bible},'outline_revision_id')
        adapter._get_revision(handle,f'/story-paths/{path_id}/outline-revisions',outline,'outline_initial.json')
        adapter._activate(handle,'activate_outline_initial',f'/story-paths/{path_id}/outline-head',outline)
        chapters=adapter._request('GET',f'/api/story-paths/{path_id}/chapters')
        path=min(chapters,key=lambda c:c['display_index'])
        opening=(bundle/'opening.txt').read_text()
        chapter=adapter._operation(handle,'manual_prefix','POST',f'/api/path-chapters/{path["id"]}/revisions',
            {'content':opening,'parent_revision_id':None})
        if chapter['content']!=opening: raise IFLineError('provided_prefix_receipt_mismatch')
        _native_file(recorder,handle,'provided_prefix_revision.json',chapter)
        adapter._activate(handle,'activate_prefix',f'/path-chapters/{path["id"]}/head',chapter['id'])
        step=0
        while not recorder.scope_reached:
            _,candidates,source,_=_candidate_checkpoint(adapter,handle,recorder,path,chapter,step,initial=step==0)
            promoted=_select(adapter,handle,recorder,policy,step,candidates,source)
            path_id=promoted['id'];paths.append(path_id)
            outline,_=adapter._generate(handle,f'outline_{step}',f'/story-paths/{path_id}/outline-generations',
                {'chapter_count':int(config.get('chapter_count',13)),'bible_revision_id':bible},'outline_revision_id')
            adapter._get_revision(handle,f'/story-paths/{path_id}/outline-revisions',outline,f'outline_{step}.json')
            adapter._activate(handle,f'activate_outline_{step}',f'/story-paths/{path_id}/outline-head',outline)
            chapters=adapter._request('GET',f'/api/story-paths/{path_id}/chapters')
            _native_file(recorder,handle,f'path_chapters_{step}.json',chapters)
            pending=[c for c in chapters if not c.get('current_revision_id')]
            if not pending: stop='native_end';ended=True;break
            path=min(pending,key=lambda c:c['display_index'])
            revision,_=adapter._generate(handle,f'chapter_{step}',f'/path-chapters/{path["id"]}/generations',
                {'bible_revision_id':bible,'outline_revision_id':outline},'chapter_revision_id')
            chapter=adapter._request('GET',f'/api/chapter-revisions/{revision}')
            _native_file(recorder,handle,f'chapter_{step}.json',chapter)
            adapter._activate(handle,f'activate_chapter_{step}',f'/path-chapters/{path["id"]}/head',revision)
            recorder.event('native_chapter_available',native_revision_id=revision,story_path_id=path_id,availability_id=revision)
            script,script_task=adapter._generate(handle,f'script_{step}',f'/chapter-revisions/{revision}/script-generations',{},'chapter_script_revision_id')
            adapter._get_revision(handle,f'/chapter-revisions/{revision}/script-revisions',script,f'script_{step}.json')
            recorder.event('native_script_available',native_revision_id=script,availability_id=script,
                script_generation_task_id=script_task['id'],chapter_revision_id=revision,
                observed_boundary='native_script_revision_received')
            by_url,_=_assets(adapter,handle,recorder,gateway,script,pid,step)
            graph_id,_=adapter._generate(handle,f'compile_{step}',f'/chapter-script-revisions/{script}/vn-graph-compilations',{},'vngraph_revision_id')
            graph=adapter._request('GET',f'/api/vn-graph-revisions/{graph_id}')
            _native_file(recorder,handle,f'graph_{step}.json',graph)
            adapter._activate(handle,f'activate_graph_{step}',f'/chapter-script-revisions/{script}/vn-graph-head',graph_id)
            _render(adapter,handle,recorder,policy,config,graph,step,by_url,
                script_revision_id=script,script_generation_task_id=script_task['id'])
            step+=1
        if recorder.scope_reached: stop='reading_window';ended=False
    except Exception as exc:
        code=getattr(exc,'code','if_line_driver_error')
        errors.append({'code':code,'message':str(exc)});recorder.error(code,str(exc))
        stop='delivery_unknown' if code=='delivery_unknown' else 'native_error'
    finally:
        try:
            if adapter is not None:
                if handle is not None:
                    try:
                        recorder.save_json('native/if_line_quota_final.json',adapter._request('GET','/__benchmark__/quota-snapshot'))
                    finally: adapter.close(handle)
                else: adapter._stop_managed()
        except Exception as exc:
            errors.append({'code':'native_cleanup_failed','message':str(exc)})
            recorder.error('native_cleanup_failed',str(exc));stop='native_error'
        try: db.close()
        except Exception as exc:
            errors.append({'code':'postgres_cleanup_failed','message':str(exc)});stop='native_error'
        # Native clients need signed URLs while running. Once all services are
        # closed, sanitize retained legacy snapshots/logs before root sealing.
        from native_shims.if_line.evidence import sanitize_native_evidence
        recorder.save_json('native/if_line_evidence_redaction.json',sanitize_native_evidence(run_dir,
            frozen_files=[Path(config['repo_path'])/relative for relative in before['files']]))
        for key,value in previous.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        if handle and handle.get('runtime_dir'):
            topups=Path(handle['runtime_dir'])/'internal-credit-topups.jsonl'
            if topups.exists():
                for number,line in enumerate(topups.read_text().splitlines(),1):
                    item=json.loads(line)
                    recorder.event('internal_credit_topup_observed',native_record=item,
                        native_source={'file':str(topups.relative_to(recorder.root)),'pointer':{'line':number}},
                        provider_currency_amount=None,observed_boundary='post_run_native_quota_trace_collection')
        after=source_fingerprint(config['repo_path'])
        recorder.save_bytes('native/if_line_source_after.json',json.dumps(after,ensure_ascii=False,indent=2).encode())
        if before!=after:
            errors.append({'code':'native_source_changed','message':'source fingerprint differs'});stop='native_error'
        recorder.save_json('trajectories/paths.json',{'trajectory_id':'main','native_story_path_ids':paths,
            'choice_execution':'native_authoring_path_promotion','native_planning_retained':True,
            'semantic_consistency':'not_evaluated','stop_reason':stop})
    return {'stop_reason':stop,'native_ended':ended,'errors':errors,'source_unchanged':before==after,
        'choice_execution':'native_authoring_path_promotion','render_mode':'offscreen_native',
        'audio':'disabled_text_and_images_scope','native_story_path_ids':paths}
