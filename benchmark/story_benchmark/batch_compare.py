"""Compare independently launched program batches before any quality judging."""
import argparse
from pathlib import Path
import json
from .batch import SYSTEMS, read_plan
from .batch_worker import audit_inputs
from .io import BenchmarkError, atomic_json, read_json, sha256
from .recording import verify_recording


def compare_batches(folders):
    plans={};errors=[]
    for folder in map(Path,folders):
        plan=read_plan(folder)
        if plan['system'] in plans:raise BenchmarkError('duplicate_system_batch')
        plans[plan['system']]=(folder,plan)
    if set(plans)!=set(SYSTEMS):raise BenchmarkError('exactly_three_system_batches_required')
    controls=[];signatures=[]
    for folder,plan in plans.values():
        controls.append({'providers':plan['configuration']['providers'],'policy':plan['configuration']['policy'],
                         'allow_pilot':plan['configuration']['allow_pilot'],'budget':'unlimited'})
        signatures.append([(j['case_id'],j['repeat'],j['shared_sha256']) for j in plan['jobs']])
    if any(c!=controls[0] for c in controls):errors.append('common_generation_conditions_differ')
    if any(s!=signatures[0] for s in signatures):errors.append('task_order_repeats_or_input_differ')
    rows=[];adapter_versions=[];schedules={}
    for system,(folder,plan) in plans.items():
        for job in plan['jobs']:
            root=folder/'runs'/job['run_id'];row={'system':system,'case_id':job['case_id'],'repeat':job['repeat'],'run_id':job['run_id']}
            if not (root/'manifest.json').exists():
                rows.append({**row,'input_verified':False,'reason':'no_sealed_run'});continue
            verify_recording(root);manifest=read_json(root/'manifest.json')
            bundle_manifest=read_json(Path(job['bundle'])/'manifest.json')
            expected={'root_run_id':job['run_id'],'batch_id':plan['batch_id'],'system':system,
                'case_id':job['case_id'],'repeat':job['repeat'],'shared_task_sha256':job['shared_sha256'],
                'case_version':read_json(Path(job['bundle'])/'case.json')['case_version'],
                'opening_sha256':sha256((Path(job['bundle'])/'opening.txt').read_bytes()),
                'model_configuration':plan['configuration']['providers'],
                'output_contract':bundle_manifest['output_contract'],
                'evidence_kind':plan['configuration']['policy']['evidence_kind'],
                'adapter_source_before':plan['adapter_source_inventory']}
            mismatches=[key for key,value in expected.items() if manifest.get(key)!=value]
            if mismatches:errors.append('sealed_run_plan_mismatch:'+job['run_id']+':'+','.join(mismatches))
            context=manifest.get('scheduling_context')
            if not (isinstance(context,dict) and all(type(context.get(k)) is int and context[k]>0
                    for k in ('requested_concurrency','worker_capacity')) and isinstance(context.get('host'),dict) and context['host']):
                context=None
            schedules.setdefault(system,[]).append({k:context.get(k) for k in ('requested_concurrency','worker_capacity','host')} if context else None)
            audit=audit_inputs(root,(Path(job['bundle'])/'shared_task.txt').read_text())
            row.update(input_verified=audit['status']=='passed',scope_reached=manifest['scope_reached'],
                       native_ended=manifest['native_ended'],stop_reason=manifest['stop_reason'],
                       source_unchanged=all(manifest['source_verification'].values()),
                       adapter_unchanged=manifest['adapter_unchanged_during_run'],
                       native_text_source_audit=manifest['native_text_source_audit'],plan_binding_valid=not mismatches)
            row['native_source_variant'] = manifest.get('native_source_variant')
            adapter_versions.append(manifest['adapter_source_before'])
            rows.append(row)
    if adapter_versions and any(v!=adapter_versions[0] for v in adapter_versions):errors.append('adapter_revisions_differ')
    inputs=all(r['input_verified'] for r in rows)
    integrity=all(r.get('source_unchanged') is True and r.get('adapter_unchanged') is True
                  and r.get('native_text_source_audit')!='failed' and r.get('plan_binding_valid') is True for r in rows)
    schedule_lists=[schedules.get(system,[]) for system in plans]
    scheduling_known=all(len(items)==len(plans[system][1]['jobs']) and all(x is not None for x in items)
                         for system,items in zip(plans,schedule_lists))
    scheduling_equal=all(items==schedule_lists[0] for items in schedule_lists) if scheduling_known else None
    return {'schema_version':'batch-comparison.1','common_conditions_identical':not errors,
        'generation_conditions_identical':not errors,
        'latency_scheduling_conditions_identical':scheduling_equal,
        'latency_scheduling_contexts':schedules,
        'latency_comparison_requires':'Review offscreen modes, host workload and provider limits separately; identical declared concurrency does not measure these.',
        'actual_received_inputs_identical':inputs and not errors,'condition_errors':errors,'runs':rows,
        'integrity_valid':integrity,'comparison_ready':not errors and inputs and integrity,
        'all_attempts_included':True,'quality_ranking':None,
        'note':'Missing body or image evidence remains missing; native end is descriptive and carries no scoring bonus or penalty.'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('batches',nargs=3,type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=compare_batches(args.batches);atomic_json(args.out,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['comparison_ready'] else 2


if __name__=='__main__':raise SystemExit(main())
