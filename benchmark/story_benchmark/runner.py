import importlib
import json
import os
import re
import shutil
import signal
import threading
import time
from urllib.parse import urlsplit
from pathlib import Path
from .audit import audit_trace, validate_source_map
from .compiler import verify_bundle
from .io import BenchmarkError, atomic_json, atomic_write, read_json, redact, sha256, safe_child

ADAPTERS = {'ai4visualnovel': ('ai4vn', 'AI4VNAdapter'), 'infiplot': ('infiplot', 'InfiPlotAdapter'), 'if_line': ('if_line', 'IFLineAdapter')}


class RootRunTimeout(BaseException):
    """Must cross native network retry handlers, which catch TimeoutError/Exception."""
    code = 'delivery_unknown'


def adapter_source_inventory():
    root=Path(__file__).resolve().parents[1]
    return {str(p.relative_to(root)):sha256(p.read_bytes())
            for folder in ('story_benchmark','native_shims') for p in (root/folder).rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.ts','.js','.cjs','.json','.txt')}


def seal_evidence(run_dir, manifest):
    run_dir=Path(run_dir)
    manifest['evidence_files']={str(p.relative_to(run_dir)):sha256(p.read_bytes()) for p in run_dir.rglob('*')
                                if p.is_file() and p != run_dir/'manifest.json'}
    atomic_json(run_dir/'manifest.json',manifest)


def verify_saved_run(run_dir):
    root=Path(run_dir).resolve()
    manifest=read_json(root/'manifest.json')
    if not manifest.get('evidence_files'):
        raise BenchmarkError('saved_evidence_inventory_missing')
    actual_files={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p != root/'manifest.json'}
    if actual_files != set(manifest['evidence_files']):
        raise BenchmarkError('saved_evidence_file_set_changed')
    for relative,digest in manifest['evidence_files'].items():
        path=safe_child(root,relative)
        if not path.is_file() or sha256(path.read_bytes())!=digest:
            raise BenchmarkError('saved_evidence_changed:'+relative)
    config=read_json(root/'config.json')
    if sha256(json.dumps(config,sort_keys=True))!=manifest.get('config_sha256'):
        raise BenchmarkError('saved_configuration_changed')
    if manifest.get('adapter_source_sha256')!=adapter_source_inventory():
        raise BenchmarkError('adapter_version_changed_reaudit_requires_explicit_new_run')
    return manifest


def adapter_for(system, config):
    module, name = ADAPTERS[system]
    return getattr(importlib.import_module('.adapters.' + module, __package__), name)(config)


def validate_config(config):
    errors = []
    from .model_parameters import validate_model_parameters
    try:
        validate_model_parameters(config.get('model_parameters', {}))
    except BenchmarkError as exc:
        errors.append(str(exc))
    for field in ('timeout_seconds', 'max_calls', 'max_output_tokens', 'max_input_chars'):
        if type(config.get(field)) not in (int, float) or config[field] <= 0:
            errors.append('missing_positive_limit: ' + field)
    for field in ('max_calls', 'max_output_tokens', 'max_input_chars'):
        if field in config and type(config[field]) is not int:
            errors.append('integer_limit_required: ' + field)
    if config.get('live') is not True:
        errors.append('live_run_not_configured')
    if config.get('managed_runtime') == 'engineering_fixed_response' or config.get('transport') is not None:
        errors.append('fixture_execution_forbidden_in_live_runner')
    if not config.get('model'):
        errors.append('common_model_not_frozen')
    endpoint=urlsplit(config.get('model_base_url') or '')
    if endpoint.scheme not in ('http','https') or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.query:
        errors.append('common_model_endpoint_not_frozen_or_contains_credentials')
    # Actual credentials belong in named environment variables, not this persisted configuration.
    if redact(config) != config:
        errors.append('inline_credentials_not_allowed_use_env_names')
    return errors


def preflight(system, bundle_dir, config, adapter=None):
    check = verify_bundle(bundle_dir)
    errors = validate_config(config)
    bundle = read_json(Path(bundle_dir) / 'manifest.json')
    if bundle['profile'] != 'TEXT_CONTINUATION_DEV':
        errors.append('first_phase_runner_supports_text_continuation_dev_only')
    if bundle['review_status'] != 'approved' and not (config.get('allow_pilot') is True and bundle['profile'] == 'TEXT_CONTINUATION_DEV'):
        errors.append('unapproved_case_requires_explicit_dev_mode')
    checks = [{'payload_check': check['payload_check']}]
    try:
        native = (adapter or adapter_for(system, config)).preflight(Path(bundle_dir))
    except (OSError,ValueError,TypeError,RuntimeError) as exc:
        native={'ok':False,'checks':[],'errors':['native_preflight_unavailable:'+type(exc).__name__+':'+str(exc)]}
    checks += native.get('checks', [])
    errors += native.get('errors', [])
    if not native.get('ok') and not native.get('errors'):
        errors.append('native_preflight_failed')
    return {'ok': not errors, 'system': system, 'checks': checks, 'errors': errors,
            'native_integration': 'not_run', 'generation_status': 'not_run'}


def _state(run_dir, manifest, state, **updates):
    manifest.update(updates)
    manifest['state'] = state
    manifest['updated_at'] = time.time()
    atomic_json(Path(run_dir) / 'manifest.json', manifest)


def _export(run_dir, adapter, handle, manifest):
    export = adapter.export_first_artifact(handle)
    segments = export.get('segments', [])
    choices = export.get('choices', [])
    valid, issues = validate_source_map(run_dir, segments)
    previews = export.get('unselected_previews', [])
    preview_valid, preview_issues = validate_source_map(run_dir, previews) if previews else (False, [])
    # A native system may only expose future branch previews. Preserve them as
    # such; they never enter generated.jsonl or satisfy the visible-body contract.
    if previews:
        atomic_write(run_dir / 'export/unselected_previews.jsonl', ''.join(json.dumps(s, ensure_ascii=False) + '\n' for s in previews))
    atomic_write(run_dir / 'export/generated.jsonl', ''.join(json.dumps(s, ensure_ascii=False) + '\n' for s in segments))
    atomic_json(run_dir / 'export/choices.json', choices)
    atomic_json(run_dir / 'export/metadata.json',{k:v for k,v in export.items() if k not in ('segments','choices','unselected_previews')})
    atomic_json(run_dir / 'export/source_map.json', [{k: s[k] for k in ('segment_id', 'native_source', 'native_pointer')} for s in segments])
    shared = (run_dir / 'shared_task.txt').read_text()
    opening = (run_dir / 'export/provided_prefix.txt').read_text()
    audit = audit_trace(run_dir, shared, opening, materialize=True)
    from .output_boundary import audit_output_boundary
    case = read_json(run_dir/'case.json') if (run_dir/'case.json').is_file() else {}
    boundary = audit_output_boundary(run_dir, case, export)
    atomic_json(run_dir / 'export/boundary_audit.json', boundary)
    preview_only = (not segments and bool(previews) and preview_valid
                    and export.get('native_capability_status') == 'unsupported_output_boundary')
    issue_codes=sorted(set(export.get('generation_issue_codes', [])+audit.get('native_issue_codes',[])))
    audit.update(source_mapping_valid=valid, source_mapping_issues=issues,
                 unselected_preview_source_mapping_valid=preview_valid if previews else None,
                 unselected_preview_source_mapping_issues=preview_issues,
                 output_boundary=boundary,
                 generation_issue_codes=issue_codes,
                 evidence_kind=manifest['evidence_kind'])
    atomic_json(run_dir / 'audit.json', audit)
    native_proven = (audit['task_entry_request_contains_shared'] is True
                     and audit['external_input_equal'] is True
                     and audit['first_prose_request_observed'] and (valid or preview_only)
                     and audit['task_entry_shared_occurrences'] == 1
                     and audit['call_context_valid'] and not audit['delivery_unknown_calls']
                     and audit['configured_model_matches_requests'] is True
                     and audit['configured_model_parameters_match_requests'] is True
                     and not audit['trace_parse_errors'] and not audit['missing_response_files'])
    generation_status = export.get('generation_status', 'generated_unreviewed' if segments else 'empty')
    if boundary['requested'] and not boundary['technical_boundary_passed']:
        generation_status = 'unsupported_output_boundary' if preview_only else 'output_boundary_not_satisfied'
    if 'budget_exhausted' in issue_codes: generation_status='budget_exhausted'
    elif any('fallback' in code or 'degraded' in code for code in issue_codes): generation_status='degraded'
    _state(run_dir, manifest, 'EXPORTED', adapter_status='completed' if valid or preview_only else 'failed',
           generation_status=generation_status, audit_status='passed' if native_proven else 'incomplete',
           native_integration=('verified_candidate_previews_only' if preview_only and manifest['evidence_kind'] == 'live'
                               else 'verified' if manifest['evidence_kind'] == 'live' else 'verified_with_fixture_provider') if native_proven else 'not_verified',
           output_boundary_status=('observed_requires_content_review' if boundary.get('technical_boundary_passed')
                                   else 'unsupported' if preview_only else 'not_satisfied') if boundary['requested'] else 'not_requested')
    return manifest


def run_once(system, bundle_dir, config, run_dir, adapter=None, mock=False):
    bundle_dir, run_dir = Path(bundle_dir).resolve(), Path(run_dir).resolve()
    config = dict(config)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', run_dir.name):
        raise BenchmarkError('invalid_run_id')
    config.update(root_run_id=run_dir.name, trace_dir=str(run_dir / 'trace'))
    if not mock:
        early_errors=validate_config(config)
        if early_errors:
            raise BenchmarkError(json.dumps({'ok':False,'errors':early_errors},ensure_ascii=False))
    instance = adapter or adapter_for(system, config)
    if mock:
        if adapter is None or config.get('live'):
            raise BenchmarkError('mock_requires_injected_adapter_and_no_live')
        verify_bundle(bundle_dir)
        check = {'ok': True, 'evidence_kind': 'mock'}
    else:
        check = preflight(system, bundle_dir, config, instance)
    if not check['ok']:
        raise BenchmarkError(json.dumps(check, ensure_ascii=False))
    # No prepare side effects until all input and runtime gates have passed.
    run_dir.mkdir(parents=True, exist_ok=False)
    for subdir in ('requests', 'responses', 'trace', 'native', 'export'):
        (run_dir / subdir).mkdir()
    shutil.copyfile(bundle_dir / 'shared_task.txt', run_dir / 'shared_task.txt')
    shutil.copyfile(bundle_dir / 'opening.txt', run_dir / 'export/provided_prefix.txt')
    shutil.copyfile(bundle_dir / 'case.json', run_dir / 'case.json')
    atomic_json(run_dir / 'config.json', redact(config))
    atomic_json(run_dir / 'preflight.json', check)
    atomic_write(run_dir / 'errors.jsonl', '')
    manifest = {'schema_version': '2.1', 'system': system, 'root_run_id': run_dir.name,
                'evidence_kind': 'mock' if mock else 'live', 'adapter_status': 'running',
                'generation_status': 'not_run', 'audit_status': 'not_run', 'native_integration': 'not_run',
                'shared_sha256': sha256((bundle_dir / 'shared_task.txt').read_bytes()),
                'bundle_dir': str(bundle_dir), 'config_sha256': sha256(json.dumps(redact(config), sort_keys=True)),
                'budget_policy': 'root HTTP call cap; per-call output cap; compact complete JSON Unicode codepoint cap; no monetary equality claim',
                'adapter_source_sha256':adapter_source_inventory(),
                'native_source_modified':False}
    _state(run_dir, manifest, 'VALIDATED')
    handle = None
    previous_alarm=None
    alarm_started=time.monotonic()
    if config.get('timeout_seconds'):
        if not hasattr(signal,'setitimer') or threading.current_thread() is not threading.main_thread():
            raise BenchmarkError('root_timeout_requires_posix_main_thread')
        previous_alarm=(signal.getsignal(signal.SIGALRM),signal.getitimer(signal.ITIMER_REAL))
        def expire(signum,frame):
            raise RootRunTimeout('root_run_timeout')
        signal.signal(signal.SIGALRM,expire)
        signal.setitimer(signal.ITIMER_REAL,float(config['timeout_seconds']))
    try:
        _state(run_dir, manifest, 'PREPARING')
        handle = instance.prepare(bundle_dir, run_dir)
        # The serialized handle must not persist auth headers or secret values.
        if redact(handle) != handle:
            raise BenchmarkError('handle_contains_credentials')
        atomic_json(run_dir / 'handle.json', handle)
        _state(run_dir, manifest, 'PREPARED')
        _state(run_dir, manifest, 'RUNNING', generation_status='running')
        result = instance.generate_first_artifact(handle)
        atomic_json(run_dir / 'handle.json', handle)
        atomic_json(run_dir / 'operation_result.json', redact(result))
        _state(run_dir, manifest, 'ARTIFACT_SAVED')
        return _export(run_dir, instance, handle, manifest)
    except BaseException as exc:
        explicit_code = getattr(exc, 'code', None)
        if explicit_code is None and isinstance(exc, RuntimeError):
            prefix = str(exc).split(':',1)[0]
            if prefix in ('delivery_unknown','native_exit','native_artifact_missing','native_artifact_missing_or_empty'):
                explicit_code=prefix
        uncertain = (explicit_code in ('delivery_unknown','task_timeout') or isinstance(exc,(KeyboardInterrupt,TimeoutError))
                     or explicit_code is None and manifest['state'] in ('PREPARING','RUNNING'))
        code = explicit_code or ('delivery_unknown' if uncertain else 'adapter_error')
        atomic_write(run_dir / 'errors.jsonl', json.dumps(redact({'code': code, 'exception_type': type(exc).__name__, 'detail': str(exc)}), ensure_ascii=False) + '\n')
        _state(run_dir, manifest, 'INTERRUPTED' if uncertain else 'FAILED', adapter_status='failed',
               generation_status=code if uncertain else 'failed', failure_code=code, audit_status='not_evaluated')
        raise
    finally:
        if previous_alarm is not None:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous_alarm[0])
            remaining,interval=previous_alarm[1]
            if remaining:
                signal.setitimer(signal.ITIMER_REAL,max(0.001,remaining-(time.monotonic()-alarm_started)),interval)
        if handle is not None:
            try:
                instance.close(handle)
            except Exception as close_error:
                atomic_json(run_dir/'close_error.json',redact({'exception_type':type(close_error).__name__,'detail':str(close_error)}))
                _state(run_dir,manifest,manifest['state'],cleanup_status='failed', adapter_status='failed',
                       audit_status='incomplete', native_integration='not_verified')
            else:
                manifest['cleanup_status']='completed'
        seal_evidence(run_dir,manifest)


def resume_export(run_dir, adapter=None):
    """Resume only a saved artifact; ambiguous delivery never silently regenerates."""
    run_dir = Path(run_dir).resolve()
    manifest = verify_saved_run(run_dir)
    if manifest['state'] == 'EXPORTED':
        return manifest
    if manifest['state'] not in ('ARTIFACT_SAVED', 'FAILED') or not (run_dir / 'operation_result.json').exists():
        raise BenchmarkError('delivery_unknown_requires_native_reconciliation; no request resent')
    instance = adapter or adapter_for(manifest['system'], read_json(run_dir / 'config.json'))
    result = _export(run_dir, instance, read_json(run_dir / 'handle.json'), manifest)
    seal_evidence(run_dir,result)
    return result
