"""A shared configuration prevents per-system model and budget overrides."""
from pathlib import Path
import os
from urllib.parse import urlsplit
from .io import BenchmarkError, read_json
from .runner import ADAPTERS, preflight

COMMON_FIELDS = {'live','model','model_base_url','timeout_seconds','max_calls','max_output_tokens','max_input_chars','allow_pilot','model_parameters','budget_mode'}
PATH_FIELDS = {'repo_path','repo_dir','source_lock','source_patch','python_executable','redis_executable','worker_receipt','run_dir','runtime_root'}


def load_experiment(path):
    path=Path(path).resolve()
    raw=read_json(path)
    if set(raw) - {'schema_version','common','systems'} or raw.get('schema_version')!='2.1':
        raise BenchmarkError('invalid_experiment_schema')
    common=raw.get('common',{})
    if set(common) - COMMON_FIELDS or (COMMON_FIELDS - {'model_parameters','budget_mode'}) - set(common):
        raise BenchmarkError('common_configuration_fields_mismatch')
    if set(raw.get('systems',{})) != set(ADAPTERS):
        raise BenchmarkError('three_system_configurations_required')
    common={**common,'model_parameters':common.get('model_parameters',{})}
    from .model_parameters import validate_model_parameters
    validate_model_parameters(common['model_parameters'])
    from .budget import validate_budget_policy
    errors=validate_budget_policy(common)
    if errors:
        raise BenchmarkError(';'.join(errors))
    configurations={}
    for system, specific in raw['systems'].items():
        if set(specific) & COMMON_FIELDS:
            raise BenchmarkError('per_system_override_of_common_condition:'+system)
        config={**common,**specific}
        for key in PATH_FIELDS:
            if config.get(key) and not Path(config[key]).is_absolute():
                config[key]=os.path.abspath(path.parent/Path(config[key]).expanduser())
        configurations[system]=config
    return configurations


def preflight_set(path,bundle):
    configurations=load_experiment(path)
    reports={system:preflight(system,bundle,config) for system,config in configurations.items()}
    return {'ok':all(r['ok'] for r in reports.values()),'common_conditions_identical':True,
            'native_integration':'not_run','systems':reports}


def run_set(path,bundle,out):
    """Run against already provisioned native runtimes with one frozen config."""
    from .runner import execute_run
    from .io import atomic_json
    configs=load_experiment(path)
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    results={system:execute_run(system,bundle,configs[system],out/system) for system in ADAPTERS}
    # Compare observations received by each native entry, not copied digests.
    received=[]
    for system in ADAPTERS:
        for file in (out/system/'trace').rglob('*received*.json'):
            data=read_json(file)
            value=data.get('received_task',data.get('shared_task'))
            if value is not None: received.append((system,value))
    expected=(Path(bundle)/'shared_task.txt').read_text(encoding='utf-8')
    equality=set(s for s,_ in received)==set(ADAPTERS) and all(t==expected for _,t in received)
    result={'schema_version':'3.0','common_conditions_identical':True,
            'actual_received_inputs_identical':equality,
            'ok':equality and all(r['outcome']=='completed' for r in results.values()),
            'adaptation_passed':equality and all(r['adapter_status']=='passed' for r in results.values()),'systems':results}
    atomic_json(out/'results.json',result)
    return result
