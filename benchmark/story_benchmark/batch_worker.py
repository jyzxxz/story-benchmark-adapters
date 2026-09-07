"""One OS process per root run: isolated native state and sealed evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import signal
import sys
import traceback
import uuid

from .batch import check_bundle, driver_for, read_plan
from .gateway import ModelGateway
from .io import BenchmarkError, read_json, sha256
from .provenance import verify_repository, source_identity
from .recording import Recorder, compute_metrics, jsonl, make_review_packages, seal, verify_recording, utc_now, audit_native_sources
from .runner import adapter_source_inventory


class StopRequested(BaseException):
    pass


def source_check(config):
    lock=read_json(config['source_lock'])
    repo=Path(config['repo_path'])
    aliases={'ai4visualnovel':'AI4VisualNovel','ai4vn':'AI4VisualNovel'}
    key=aliases.get(repo.name.lower(),repo.name)
    if key not in lock['systems']:
        raise BenchmarkError('unknown_native_source_directory')
    item=lock['systems'][key]
    return verify_repository(repo,item['base_commit'],config)


def preserve_source_patch(recorder, report):
    """Keep the verified patch portable alongside the run's source provenance."""
    if not report.get('ok') or not report.get('source_modified'):
        return
    source = report['checks'][0]
    manifest_data = Path(source['source_patch_manifest']).read_bytes()
    if sha256(manifest_data) != source['source_patch_manifest_sha256']:
        raise BenchmarkError('source_patch_changed_before_run')
    patch_relative = json.loads(manifest_data)['patch_file']
    for path_key, hash_key, target in (
        ('source_patch_manifest', 'source_patch_manifest_sha256', 'manifest.json'),
        ('source_patch_file', 'source_patch_sha256', patch_relative),
    ):
        data = Path(source[path_key]).read_bytes()
        if sha256(data) != source[hash_key]:
            raise BenchmarkError('source_patch_changed_before_run')
        recorder.save_bytes('native/source_patch/' + target, data)


def verify_planned_source(plan, config, report):
    """Refuse a different valid patch at the same path before opening a gateway."""
    if not report.get('ok'):
        raise BenchmarkError('native_source_verification_failed_before')
    if plan.get('source_identity') is not None:
        if source_identity(report) != plan['source_identity']:
            raise BenchmarkError('native_source_changed_since_plan')
    elif config.get('source_patch'):
        raise BenchmarkError('source_patch_requires_new_frozen_plan')
    return True


def constraints_from_sources(bundle):
    """Lossless source clauses; applicability/semantic scoring remain review work."""
    bundle=Path(bundle);case=read_json(bundle/'case.json')
    rows=[]
    for file in ('sources/brief.txt','sources/prefix.txt'):
        value=(bundle/file).read_text(encoding='utf-8')
        for match in re.finditer(r'[^\n；]+(?:；|$)',value,re.M):
            quote=match.group()
            if not quote.strip():continue
            rows.append({'constraint_id':f'R{len(rows)+1:03d}','source_file':file,
                'source_sha256':sha256(value),'char_start':match.start(),'char_end':match.end(),
                'requirement':quote,'source_quote':quote,'type':'source_clause','must_be_explicit':None,
                'applicable_period':None,'applicability_status':'requires_content_review',
                'decomposition_status':'verbatim_clause_not_validated_atomic_assertion'})
    # These types describe explicit public wording only. They add no story facts.
    structured=[]
    brief=(bundle/'sources/brief.txt').read_text()
    definitions=[
        ('红色雨伞第一次出现时在实验楼门口','initial_state','first_appearance',False,True),
        ('电报码不是随机噪声','permanent_rule','whenever_signal_is_explained',False,False),
        ('周遥第一场明确说“我不记得昨晚来过这里”','required_presentation','first_substantive_scene',True,True),
        ('地下室有仍在运行的备用电源','permanent_rule','when_basement_power_is_relevant',False,False),
        ('沈教授在第二场之前不知道周遥失忆的细节','knowledge_boundary','before_second_substantive_scene',False,False)]
    for quote,kind,period,explicit,opening_satisfies in definitions:
        if quote not in brief:continue
        start=brief.index(quote)
        structured.append({'constraint_id':f'C{len(structured)+1:03d}','requirement':quote,'source_quote':quote,
            'source_file':'sources/brief.txt','source_sha256':sha256(brief),'char_start':start,'char_end':start+len(quote),
            'type':kind,'applicable_period':period,'must_be_explicit':explicit,
            'already_satisfied_by_public_opening':None,'check_public_opening_first':opening_satisfies,
            'score_generated_credit_for_opening':False,
            'status':'not_evaluated','applicability_requires_content_evidence':True})
    for key,value in case.get('scope_map',{}).items():
        structured.append({'constraint_id':f'S{len(structured)+1:03d}','requirement':value,'source_quote':value,
            'source_file':'case.json','source_pointer':'/scope_map/'+key.replace('~','~0').replace('/','~1'),
            'type':'conditional_or_temporal_rule','status':'not_evaluated','must_be_explicit':False})
    for decision in case.get('decisions',[]):
        structured.append({'constraint_id':decision['id'],'type':'required_choice','source_file':'case.json',
            'source_pointer':'/decisions/'+str(case['decisions'].index(decision)),
            'source_quote':decision,'requirement':decision,'must_be_explicit':True,
            'applicable_period':'common_reading_window','status':'not_evaluated',
            'native_menu_mapping':'requires_semantic_review_not_native_menu_count'})
    return {'version':'constraints.1','source_clauses':rows,'structured_constraints':structured,'scope_map':case.get('scope_map',{}),
        'prescribed_decisions':case.get('decisions',[]),'public_characters':case.get('character_names',[]),
        'no_full_ending_requirement':True,'audio_visual_scope':'images_only_audio_requirements_not_scored',
        'judging_rule':'Determine applicability in the observed window before scoring. Do not automatically pass missing events or equate arbitrary native menus with prescribed decisions.',
        'human_validation_status':'not_reviewed'}


def _strings(value):
    if isinstance(value,str):return [value]
    if isinstance(value,list):return [s for v in value for s in _strings(v)]
    if isinstance(value,dict):return [s for v in value.values() for s in _strings(v)]
    return []


def audit_inputs(root,expected):
    root=Path(root);receipts=[]
    # Only receiver observations, never hashes copied by the compiler/launcher.
    for file in root.rglob('*'):
        if not file.is_file() or file.suffix not in ('.json','.jsonl') or not ('trace' in file.parts or 'native' in file.parts):continue
        try:
            records=jsonl(file) if file.suffix=='.jsonl' else [read_json(file)]
        except (ValueError,UnicodeError):continue
        for row in records:
            if not isinstance(row,dict):continue
            if 'received_task' in row and (row.get('boundary') or row.get('kind')=='received_input' or 'received' in file.name):
                receipts.append({'file':str(file.relative_to(root)),'boundary':row.get('boundary'),
                    'received_sha256':sha256(row['received_task']) if isinstance(row['received_task'],str) else None,
                    'valid_string':isinstance(row['received_task'],str), 'matches':row['received_task']==expected,
                    'receiver_observed':row.get('boundary')=='native_sdk_task_block' or
                        (row.get('boundary')!='native_browser_fetch_request_body' and row.get('direct_native_route_receiver_observed') is not False)})
    calls=sorted((c for c in jsonl(root/'telemetry/calls.jsonl') if c['role']=='text'),key=lambda c:c['start_monotonic_ns'])
    first=None
    if calls:
        call=calls[0];request=read_json(root/call['request_file']);payload=request['payload']
        texts=_strings(payload.get('messages',[]))
        occurrences=sum(t.count(expected) for t in texts)
        before_occurrences=sum(t.count(expected) for t in _strings(request.get('before_payload',{}).get('messages',[])))
        first={'call_id':call['call_id'],'request_file':call['request_file'],
            'shared_occurrences':occurrences,'before_gateway_shared_occurrences':before_occurrences,
            'exact_shared_present':occurrences==1,'gateway_did_not_modify_messages':payload.get('messages')==request.get('before_payload',{}).get('messages')}
    passed=any(r['receiver_observed'] for r in receipts) and all(r['matches'] for r in receipts) and first is not None and first['exact_shared_present'] and first['gateway_did_not_modify_messages']
    return {'status':'passed' if passed else 'incomplete','receiver_observations':receipts,
            'first_creative_text_request':first,'shared_sha256':sha256(expected),
            'scope':'external_task_equal_native_internal_prompts_preserved'}


def run_job(plan,job,root,driver=None):
    root=Path(root).resolve()
    if plan.get('adapter_source_inventory')!=adapter_source_inventory():
        raise BenchmarkError('adapter_changed_since_plan_no_dispatch')
    config=plan['configuration'];specific=config['systems'][plan['system']]
    bundle=Path(job['bundle']);bundle_manifest=check_bundle(bundle,config['allow_pilot'])
    window=bundle_manifest['output_contract']['window_chars']
    policy={**config['policy'],'window_chars':window}
    if driver is not None and policy['evidence_kind']!='fixture':
        raise BenchmarkError('injected_driver_requires_explicit_fixture_evidence')
    # Atomic ownership also protects direct duplicate worker invocations.
    try:root.mkdir(parents=True,exist_ok=False)
    except FileExistsError:raise BenchmarkError('root_run_already_exists_no_automatic_resend')
    recorder=Recorder(root,job['run_id'],window)
    recorder.event('run_submitted')
    started=utc_now();inventory_before=adapter_source_inventory()
    for src,target in (('shared_task.txt','shared_task.txt'),('sources/brief.txt','brief.txt'),
                       ('opening.txt','opening.txt'),('case.json','case.json')):
        recorder.save_bytes('inputs/'+target,(bundle/src).read_bytes())
    for file in (bundle/'sources').iterdir():
        if file.is_file():recorder.save_bytes('inputs/sources/'+file.name,file.read_bytes())
    recorder.save_json('inputs/constraints.json',constraints_from_sources(bundle))
    recorder.save_json('inputs/bundle_manifest.json',bundle_manifest)
    recorder.save_json('configuration.json',{'providers':config['providers'],'system':specific,'policy':policy})
    state_path=root.parent.parent/'states'/(job['run_id']+'.json')
    scheduling=read_json(state_path).get('scheduling_context') if state_path.exists() else None
    recorder.save_json('scheduling_context.json',scheduling)
    gateway=None;result={};before=after=None;source_matched_plan=False
    try:
        before=source_check(specific)
        source_matched_plan = verify_planned_source(plan, specific, before)
        preserve_source_patch(recorder, before)
        native=driver or driver_for(plan['system'])
        preflight=native.preflight(specific,bundle,policy)
        recorder.save_json('preflight.json',preflight)
        if not preflight['ok']:raise BenchmarkError('native_preflight_failed')
        gateway=ModelGateway(recorder,config['providers']).start()
        result=native.run(specific,bundle,root,recorder,gateway,policy)
        if not isinstance(result,dict):raise BenchmarkError('native_result_not_object')
    except StopRequested:
        result={'stop_reason':'delivery_unknown','native_ended':None,'interrupted':True}
        recorder.error('operator_interrupted','Generation interrupted; delivered requests are drained and never automatically resent.')
    except Exception as exc:
        result={'stop_reason':'adapter_error','native_ended':None,'exception_type':type(exc).__name__,
                'failure_origin':'adapter_or_dependency_exception_requires_diagnosis'}
        recorder.error('run_exception',str(exc),exception_type=type(exc).__name__)
        recorder.save_json('native/exception.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
    finally:
        # Driver must close native clients first; drain sent work, then seal.
        if gateway:gateway.close()
        try:after=source_check(specific)
        except Exception as exc:after={'ok':False,'errors':[str(exc)]}
    recorder.event('run_stopped',stop_reason=result.get('stop_reason'))
    input_audit=audit_inputs(root,(bundle/'shared_task.txt').read_text())
    recorder.save_json('input_audit.json',input_audit)
    recorder.save_json('native/result.json',result)
    recorder.save_json('native/source_verification.json',{'before':before,'after':after})
    if not after['ok']:recorder.error('native_source_changed','Native source integrity check failed',check=after)
    source_identity_unchanged = bool(source_matched_plan and before and before.get('ok') and after.get('ok')
        and source_identity(before) == source_identity(after))
    if not source_identity_unchanged:
        recorder.error('native_source_identity_changed', 'Native source did not retain the frozen plan identity.')
    inventory_after=adapter_source_inventory()
    if inventory_before!=inventory_after:recorder.error('adapter_changed_during_run','Adapter source changed during generation; run is unsuitable for a formal batch.')
    source_audit=audit_native_sources(root)
    recorder.save_json('native_source_audit.json',source_audit)
    if source_audit['status']=='failed':recorder.error('native_text_mapping_failed','Visible text cannot be verified at its original native anchor.')
    native_paths=read_json(root/'trajectories/paths.json') if (root/'trajectories/paths.json').exists() else None
    paths={'version':'paths.1','native_path_metadata':native_paths,'trajectories':[{'trajectory_id':'main','parent_trajectory_id':None,
        'story_file':'trajectories/main/story.jsonl','choices_file':'trajectories/main/choices.jsonl',
        'native_ended':result.get('native_ended'),'scope_reached':recorder.scope_reached,
        'stop_reason':result.get('stop_reason','delivery_unknown'),'choice_policy':policy['choice_indices'],
        'path_semantic_comparability':'not_evaluated','shared_cost_counting':'one_root_once'}]}
    recorder.save_json('trajectories/paths.json',paths)
    metrics=compute_metrics(root);recorder.save_json('metrics.json',metrics)
    make_review_packages(root,'sample-'+uuid.uuid4().hex[:16])
    errors=jsonl(root/'errors.jsonl')
    verified_source = before if before and before.get('ok') else after
    verified_identity = source_identity(verified_source) if verified_source and verified_source.get('ok') else {}
    source_variant = {key: verified_identity.get(key) for key in (
        'source_modified', 'native_variant', 'source_patch_manifest_sha256',
        'source_patch_sha256', 'source_tree_sha256')}
    manifest={'schema_version':'recording.1','root_run_id':job['run_id'],'batch_id':plan['batch_id'],
        'system':plan['system'],'case_id':job['case_id'],'case_version':read_json(bundle/'case.json')['case_version'],
        'repeat':job['repeat'],'evidence_kind':policy['evidence_kind'],'started_at':started,'sealed_at':utc_now(),
        'shared_task_sha256':bundle_manifest['shared_sha256'],'opening_sha256':sha256((bundle/'opening.txt').read_bytes()),
        'base_commit':(after.get('checks') or [{}])[0].get('base_commit'),
        'source_verification':{'before_ok':bool(before and before['ok']),'after_ok':after['ok'],
                               'frozen_identity_unchanged':source_identity_unchanged},
        'native_source_variant': source_variant,
        'adapter_source_before':inventory_before,'adapter_source_after':inventory_after,
        'adapter_unchanged_during_run':inventory_before==inventory_after,
        'model_configuration':config['providers'],'budget_policy':'unlimited_adapter_native_limits_preserved',
        'scheduling_context':scheduling,
        'output_contract':bundle_manifest['output_contract'],'scope_reached':recorder.scope_reached,
        'native_ended':result.get('native_ended'),'stop_reason':result.get('stop_reason','delivery_unknown'),
        'visible_chars':recorder.visible_chars,'input_audit':input_audit,
        'native_text_source_audit':source_audit['status'],
        'quality_evaluation':'not_run','error_count':len(errors),
        'recording_completeness':{
            'content_evidence':{k:metrics[k]['evidence_status'] for k in ('M1','M2','M3','M5','M6')},
            'native_text_mapping':source_audit['status'],
            'desktop_presentation':metrics['M4']['presentation_status'],
            'usage_coverage_by_role':{k:v['coverage'] for k,v in metrics['M7']['roles'].items()},
            'calls_without_terminal_record':metrics['M7']['started_without_terminal_record'],
            'image_candidate_count_complete':metrics['M8']['candidate_count_complete'],
            'candidate_files_saved':metrics['M8']['saved_candidate_files'],
            'generated_asset_candidate_link_coverage':metrics['M8']['generated_asset_candidate_link_coverage'],
            'actual_cost':'unavailable_provider_billing_not_supplied',
            'semantic_choice_mapping':'not_evaluated','constraint_decomposition':'requires_review'} ,
        'formal_eligibility':'requires_frozen_pilot_validation_and_content_review'}
    seal(root,manifest)
    verify_recording(root)
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True,type=Path);parser.add_argument('--index',required=True,type=int)
    args=parser.parse_args();plan=read_plan(args.plan.parent);job=plan['jobs'][args.index]
    def stop(signum,frame):
        # Repeated interrupts do not prevent cleanup/usage draining.
        signal.signal(signal.SIGTERM,signal.SIG_IGN);signal.signal(signal.SIGINT,signal.SIG_IGN)
        raise StopRequested()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    manifest=run_job(plan,job,args.plan.parent/'runs'/job['run_id'])
    print(json.dumps({'root_run_id':job['run_id'],'stop_reason':manifest['stop_reason'],'scope_reached':manifest['scope_reached']}))


if __name__=='__main__':main()
