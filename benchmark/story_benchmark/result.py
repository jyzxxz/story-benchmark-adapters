"""One response shape for every system, including native failures.

This is a deterministic export of recorded facts, never a story writer or judge.
"""
import json
from pathlib import Path
from .audit import audit_trace, validate_source_map, is_http_attempt_record
from .io import atomic_json, read_json, sha256, redact, BenchmarkError
from .output_boundary import choice_label, validate_choice_sources

RESULT_KEYS = {'schema_version','system','run_id','case_id','outcome','adapter_status','native_status',
               'input','scope','content','errors','usage','artifacts','provenance'}
OUTCOMES = {'completed','native_error','native_output_incomplete','input_rejected','adapter_error','budget_exhausted','delivery_unknown'}
NATIVE_CODES = {
    'native_graph_node_count_mismatch','native_exit','native_artifact_missing',
    'native_artifact_missing_or_empty','native_json_parse_error','native_schema_error',
    'native_empty_response','native_invalid_artifact','native_task_failed',
    'native_json_error','native_schema_validation_error','native_response_empty',
    'missing_scene','invalid_beat','duplicate_beat_id','beat_cycle','missing_beat',
    'invalid_prose','invalid_next','invalid_choices','invalid_choice','missing_entry',
    'missing_entry_node','automatic_jump_cycle','automatic_jump_target_missing',
    'native_empty_model_response','native_http_error','native_artifact_empty',
    'native_artifact_parse_error','native_artifact_invalid_shape','native_automatic_jump_cycle',
    'native_jump_target_missing','native_previous_stage_failed','invalid_response',
    # IF Line's frozen client uses these names for invalid/missing native
    # artifacts. Source-identity and export-mapping errors remain adapter errors.
    'missing_native_artifact','missing_result_ref','invalid_native_candidate_count',
    'invalid_native_candidate','duplicate_native_candidate_identity',
}


def error_category(code):
    if code in {'delivery_unknown','task_timeout','root_run_timeout'}:
        return 'transport'
    if 'budget' in code:
        return 'budget'
    if code in NATIVE_CODES or code.startswith(('native_generation_','native_output_')):
        return 'native'
    if code in {'input_rejected','preflight_failed','run_directory_exists'}:
        return 'input'
    return 'adapter'


def empty_result(system, run_id, case=None):
    case = case or {}
    return {
        'schema_version':'3.0','system':system,'run_id':run_id,'case_id':case.get('case_id'),
        'outcome':'input_rejected','adapter_status':'not_run','native_status':'not_run',
        'input':{'shared_text':None,'shared_sha256':None,'provided_prefix':None,'opening_sha256':None,
                 'contract':case.get('input_contract'),'received_equal':None,'first_request_occurrences':None},
        'scope':{'contract':case.get('output_contract'),'status':'not_evaluated','selection_executed':None,
                 'body_optional':True,'choice_count':0,'issues':[]},
        'content':{'body':[],'choices':[],'previews':[],'quality_status':'not_evaluated'},
        'errors':[],
        'usage':{'http_calls':0,'complete':False,'prompt_tokens':None,'completion_tokens':None,'total_tokens':None,'actual_cost':None},
        'artifacts':{k:None for k in ('manifest','audit','body','choices','previews','metadata','provided_prefix')},
        'provenance':{'evidence_kind':None,'adapter_inventory_sha256':None,'native_source_unchanged':None,
                      'request_model_verified':None,'request_parameters_verified':None,'context_semantics':'not_evaluated',
                      'native_issue_codes':[]},
    }


def source(value):
    return {'file':value['native_source'],'pointer':value['native_pointer']}


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


def validate_result(result):
    if set(result) != RESULT_KEYS or result['schema_version'] != '3.0' or result['outcome'] not in OUTCOMES:
        raise BenchmarkError('invalid_result_envelope')
    if result['system'] not in {'if_line','ai4visualnovel','infiplot'} or not isinstance(result['run_id'],str):
        raise BenchmarkError('invalid_result_identity')
    expected = empty_result(result['system'],result['run_id'])
    for name in ('input','scope','content','usage','artifacts','provenance'):
        if not isinstance(result[name],dict) or set(result[name]) != set(expected[name]):
            raise BenchmarkError('invalid_result_fields:'+name)
    if result['scope']['selection_executed'] is not None and type(result['scope']['selection_executed']) is not bool:
        raise BenchmarkError('invalid_selection_observation')
    if result['outcome']=='completed' and (result['scope']['selection_executed'] is not False or result['scope']['status']!='reached'):
        raise BenchmarkError('completed_result_boundary_not_verified')
    if result['adapter_status'] not in {'not_run','passed','failed','unverified'} or result['native_status'] not in {'not_run','completed','failed','unknown'}:
        raise BenchmarkError('invalid_result_status')
    if result['scope']['choice_count'] != len(result['content']['choices']):
        raise BenchmarkError('invalid_choice_count')
    for error in result['errors']:
        if set(error) != {'category','code','message'} or error['category'] not in {'input','adapter','native','budget','transport'} or not all(isinstance(v,str) for v in error.values()):
            raise BenchmarkError('invalid_result_error')
    for name in ('body','choices','previews'):
        if not isinstance(result['content'][name],list):
            raise BenchmarkError('invalid_result_content:'+name)
    ids = set()
    for choice in result['content']['choices']:
        if set(choice) != {'id','label','source','preview_ids'} or not isinstance(choice['label'],str) or not choice['label'].strip():
            raise BenchmarkError('invalid_result_choice')
        if choice['id'] in ids:
            raise BenchmarkError('duplicate_result_choice_id')
        ids.add(choice['id'])
    preview_ids = set()
    for name in ('body','previews'):
        for segment in result['content'][name]:
            fields = {'id','kind','speaker','text','source'} | ({'choice_id'} if name == 'previews' else set())
            if set(segment) != fields or not isinstance(segment['text'],str) or not segment['text'].strip():
                raise BenchmarkError('invalid_result_segment')
            if name == 'previews':
                if segment['choice_id'] not in ids or segment['id'] in preview_ids:
                    raise BenchmarkError('orphan_or_duplicate_result_preview')
                preview_ids.add(segment['id'])
    for choice in result['content']['choices']:
        expected_previews = [p['id'] for p in result['content']['previews'] if p['choice_id'] == choice['id']]
        if choice['preview_ids'] != expected_previews:
            raise BenchmarkError('result_preview_association_mismatch')
    for item in sum((result['content'][name] for name in ('body','choices','previews')),[]):
        src=item['source']
        if not isinstance(item['id'],str) or not item['id'] or not isinstance(src,dict) or set(src)!={'file','pointer'} or not isinstance(src['file'],str) or not src['file'] or Path(src['file']).is_absolute() or '..' in Path(src['file']).parts:
            raise BenchmarkError('invalid_result_source')
        pointer=src['pointer']
        if not ((isinstance(pointer,str) and pointer.startswith('/')) or (isinstance(pointer,dict) and type(pointer.get('line')) is int and pointer['line']>0)):
            raise BenchmarkError('invalid_result_pointer')
    return result


def finalize_result(run_dir, manifest):
    root = Path(run_dir)
    case = read_json(root/'case.json')
    result = empty_result(manifest['system'],manifest['root_run_id'],case)
    shared = (root/'shared_task.txt').read_text(encoding='utf-8')
    opening = (root/'export/provided_prefix.txt').read_text(encoding='utf-8')
    audit = audit_trace(root,shared,opening,materialize=True)
    result['input'].update(shared_text=shared,shared_sha256=sha256(shared),provided_prefix=opening,
                           opening_sha256=sha256(opening),received_equal=audit['external_input_equal'],
                           first_request_occurrences=audit['task_entry_shared_occurrences'])
    body = read_lines(root/'export/generated.jsonl')
    previews = read_lines(root/'export/unselected_previews.jsonl')
    choices = read_json(root/'export/choices.json') if (root/'export/choices.json').is_file() else []
    metadata = read_json(root/'export/metadata.json') if (root/'export/metadata.json').is_file() else {}
    body_valid, body_issues = validate_source_map(root,body) if body else (True,[])
    preview_valid, preview_issues = validate_source_map(root,previews) if previews else (True,[])
    choice_valid, choice_issues = validate_choice_sources(root,choices)
    native_ids=[str(c.get('choice_id') or c.get('id') or f'choice-{i+1}') for i,c in enumerate(choices)]
    duplicate_ids=len(set(native_ids))!=len(native_ids)
    for segment in body:
        result['content']['body'].append({'id':segment['segment_id'],'kind':segment['kind'],
            'speaker':segment.get('speaker'),'text':segment['text'],'source':source(segment)})
    for index, choice in enumerate(choices):
        result['content']['choices'].append({'id':f'choice-{index+1}' if duplicate_ids else native_ids[index],
            'label':choice_label(choice),'source':source(choice),'preview_ids':[]})
    for preview in previews:
        result['content']['previews'].append({'id':preview['segment_id'],'choice_id':str(preview['choice_id']),
            'kind':'branch_preview','speaker':preview.get('speaker'),'text':preview['text'],'source':source(preview)})
    for choice in result['content']['choices']:
        choice['preview_ids'] = [p['id'] for p in result['content']['previews'] if p['choice_id'] == choice['id']]
    reached = metadata.get('stop_reason') == 'first_choice' or metadata.get('export_scope') in {'entry_to_first_choice','first_unselected_choice'} and bool(choices)
    issues = list(body_issues)+list(preview_issues)+list(choice_issues)
    if duplicate_ids:
        issues.append('native_duplicate_choice_id')
    if manifest['state']=='EXPORTED' and len(choices) != case['output_contract']['choice_count']:
        issues.append('native_choice_count_mismatch')
    if manifest['state'] == 'EXPORTED' and not reached:
        issues.append('native_first_choice_not_reached')
    if metadata and metadata.get('selection_executed') is not False:
        issues.append('selection_state_not_verified')
    result['scope'].update(status='reached' if reached and not issues else 'not_reached' if metadata else 'not_evaluated',
                           selection_executed=metadata.get('selection_executed'),
                           choice_count=len(choices),issues=issues)
    for error in read_lines(root/'errors.jsonl'):
        code = error.get('code','adapter_error')
        result['errors'].append({'category':error_category(code),'code':code,'message':error.get('detail','')})
    transport_ok = (audit['external_input_equal'] is True and audit['task_entry_shared_occurrences']==1
        and audit['configured_model_matches_requests'] is True and audit['configured_model_parameters_match_requests'] is True
        and audit['call_context_valid'] and not audit['trace_parse_errors'] and not audit['missing_response_files']
        and not audit['delivery_unknown_calls'])
    mapping_ok = body_valid and preview_valid and (choice_valid or not choices)
    native_issues=sorted(set(metadata.get('generation_issue_codes',[])+audit.get('native_issue_codes',[])))
    result['provenance']['native_issue_codes']=native_issues
    if 'budget_exhausted' in native_issues:
        result['errors'].append({'category':'budget','code':'budget_exhausted','message':'Native trace or export reported budget exhaustion.'})
    elif any('fallback' in code or 'degraded' in code for code in native_issues):
        result['errors'].append({'category':'native','code':'native_output_degraded','message':str(native_issues)})
    result['outcome'] = 'completed' if manifest['state']=='EXPORTED' and not issues else 'native_output_incomplete'
    result['native_status'] = 'completed' if manifest['state']=='EXPORTED' else 'failed'
    result['adapter_status'] = 'passed' if transport_ok and mapping_ok else 'unverified'
    if result['errors']:
        category = result['errors'][0]['category']
        result['outcome'] = {'native':'native_error','input':'input_rejected','transport':'delivery_unknown','budget':'budget_exhausted','adapter':'adapter_error'}[category]
        if category=='adapter':result['adapter_status']='failed'
        if category=='transport':result['native_status']='unknown'
    if not mapping_ok:
        result['outcome']='adapter_error';result['adapter_status']='failed'
        result['errors'].append({'category':'adapter','code':'export_source_mapping_invalid','message':str(issues)})
    if manifest.get('cleanup_status')=='failed':
        result['outcome']='adapter_error';result['adapter_status']='failed'
        result['errors'].append({'category':'adapter','code':'cleanup_failed','message':'See close_error.json'})
    if result['outcome']=='completed' and not transport_ok:
        result['outcome']='adapter_error'
        result['adapter_status']='failed'
        result['errors'].append({'category':'adapter','code':'request_evidence_incomplete','message':'See audit.json'})
    if result['outcome']=='native_output_incomplete':
        result['errors'].append({'category':'native','code':'native_output_contract_unmet','message':str(issues)})
    if metadata and metadata.get('selection_executed') is not False:
        result.update(outcome='adapter_error',adapter_status='failed')
        result['errors'].append({'category':'adapter','code':'selection_state_not_verified','message':'Export did not attest an unselected boundary.'})
    if audit['delivery_unknown_calls']:
        result.update(outcome='delivery_unknown',native_status='unknown')
        if not any(e['code']=='delivery_unknown' for e in result['errors']):
            result['errors'].append({'category':'transport','code':'delivery_unknown','message':str(audit['delivery_unknown_calls'])})
    if manifest['system']=='if_line' and metadata:
        observations=audit['structured_opening_observations']
        # Fixtures that do not exercise native snapshots remain explicitly mock.
        if manifest['evidence_kind']=='live' and (not observations or not all(o['opening_equal'] is True and o['instructions_empty'] is True for o in observations)):
            result.update(outcome='adapter_error',adapter_status='failed')
            result['errors'].append({'category':'adapter','code':'native_snapshot_boundary_not_verified','message':'See structured_opening_observations in audit.json'})
    # A budget hook can stop the provider before the native worker reports its
    # generic task failure. Preserve the actual stop cause, but never mask a
    # source/mapping/cleanup failure or an uncertain delivery with that cause.
    if (any(error['code']=='budget_exhausted' for error in result['errors'])
            and result['outcome'] in {'completed','native_error','native_output_incomplete','budget_exhausted'}):
        result['outcome']='budget_exhausted'
    calls = {}
    for path in (root/'trace').rglob('*.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:record=json.loads(line)
            except ValueError:continue
            if is_http_attempt_record(record):calls.setdefault(record['call_id'],{}).update(record)
    result['usage'].update(http_calls=audit['observed_http_calls'],complete=audit['usage_coverage_complete'])
    if audit['usage_coverage_complete']:
        for field in ('prompt_tokens','completion_tokens','total_tokens'):
            values=[(record.get('usage') or {}).get(field) for record in calls.values()]
            result['usage'][field]=sum(values) if all(type(v) is int for v in values) else None
    result['provenance'].update(evidence_kind=manifest['evidence_kind'],
        adapter_inventory_sha256=sha256(json.dumps(manifest['adapter_source_sha256'],sort_keys=True)),
        native_source_unchanged=(False if manifest.get('native_source_modified') is True else
            True if manifest.get('native_source_modified') is False and manifest.get('cleanup_status')=='completed'
            and manifest.get('evidence_kind')=='live' else None),
        request_model_verified=audit['configured_model_matches_requests'],request_parameters_verified=audit['configured_model_parameters_match_requests'])
    paths={'manifest':'manifest.json','audit':'audit.json','body':'export/generated.jsonl','choices':'export/choices.json',
           'previews':'export/unselected_previews.jsonl','metadata':'export/metadata.json','provided_prefix':'export/provided_prefix.txt'}
    audit.update(v3_output_scope=result['scope'],source_mapping_valid=mapping_ok)
    atomic_json(root/'audit.json',audit)
    for key,path in paths.items():
        result['artifacts'][key]=path if (root/path).exists() else None
    validate_result(result)
    atomic_json(root/'result.json',redact(result))
    manifest.update(result_schema_version='3.0',result_file='result.json',result_outcome=result['outcome'])
    return result
