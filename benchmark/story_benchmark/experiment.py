"""A shared configuration prevents per-system model and budget overrides."""
from pathlib import Path
from urllib.parse import urlsplit
from .io import BenchmarkError, read_json
from .runner import ADAPTERS, preflight

COMMON_FIELDS = {'live','model','model_base_url','timeout_seconds','max_calls','max_output_tokens','max_input_chars','allow_pilot','model_parameters'}
PATH_FIELDS = {'repo_path','repo_dir','source_lock','python_executable','redis_executable','worker_receipt','run_dir','runtime_root'}


def load_experiment(path):
    path=Path(path).resolve()
    raw=read_json(path)
    if set(raw) - {'schema_version','common','systems'} or raw.get('schema_version')!='2.1':
        raise BenchmarkError('invalid_experiment_schema')
    common=raw.get('common',{})
    if set(common) - COMMON_FIELDS or (COMMON_FIELDS - {'model_parameters'}) - set(common):
        raise BenchmarkError('common_configuration_fields_mismatch')
    if set(raw.get('systems',{})) != set(ADAPTERS):
        raise BenchmarkError('three_system_configurations_required')
    common={**common,'model_parameters':common.get('model_parameters',{})}
    from .model_parameters import validate_model_parameters
    validate_model_parameters(common['model_parameters'])
    configurations={}
    for system, specific in raw['systems'].items():
        if set(specific) & COMMON_FIELDS:
            raise BenchmarkError('per_system_override_of_common_condition:'+system)
        config={**common,**specific}
        for key in PATH_FIELDS:
            if config.get(key) and not Path(config[key]).is_absolute():
                config[key]=str((path.parent/config[key]).resolve())
        configurations[system]=config
    return configurations


def preflight_set(path,bundle):
    configurations=load_experiment(path)
    reports={system:preflight(system,bundle,config) for system,config in configurations.items()}
    return {'ok':all(r['ok'] for r in reports.values()),'common_conditions_identical':True,
            'native_integration':'not_run','systems':reports}
