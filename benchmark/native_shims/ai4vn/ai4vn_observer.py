"""Opt-in benchmark evidence at the SDK and OpenAI HTTP boundaries.

No messages are rewritten. The OpenAI HTTP hook sees SDK retry attempts, and
request caps are explicit experimental execution settings. Disabled by default.
"""
import contextvars
try:
    import fcntl
except ImportError:  # Native mode remains usable on platforms without flock.
    fcntl = None
import inspect
import importlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_current = contextvars.ContextVar('benchmark_call', default=None)
_lock = threading.Lock()


def enabled():
    return bool(os.getenv('BENCH_TRACE_DIR'))


def budget_mode():
    mode = os.getenv('BENCH_BUDGET_MODE', 'bounded')
    if mode not in ('bounded', 'unlimited'):
        raise RuntimeError('benchmark_budget_mode_invalid')
    return mode


def _now():
    return datetime.now(timezone.utc).isoformat()


def _plain(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def redact(value):
    """Never log auth headers or environment credential values."""
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if re.search(r'api.?key|authorization|cookie|secret|password|access.?token', k, re.I) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if len(secret) >= 8 and re.search(r'api.?key|token|secret|password|cookie', key, re.I):
                value = value.replace(secret, '[REDACTED]')
        return re.sub(r'(?i)Bearer\s+[^\s,;]+', 'Bearer [REDACTED]', value)
    return value


def _directory():
    directory = Path(os.environ['BENCH_TRACE_DIR']).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _emit(record):
    directory = _directory()
    with _lock, (directory / f'ai4vn-{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(redact(_plain(record)), ensure_ascii=False) + '\n')


def _base(stage):
    return {'root_run_id': os.getenv('BENCH_RUN_ID'), 'system': 'ai4vn',
            'operation_id': os.getenv('BENCH_OPERATION_ID'),
            'native_project_id': os.getenv('BENCH_NATIVE_ROOT'), 'native_task_id': None,
            'stage': stage, 'usage': None, 'usage_coverage_complete': False,
            'provider_request_id': None, 'finish_reason': None, 'error': None}


def _reserve_http_attempt():
    """A shared locked counter counts every actual send, including SDK retries."""
    if fcntl is None:
        raise RuntimeError('benchmark_platform_lock_unsupported')
    limit = None if budget_mode() == 'unlimited' else int(os.environ['BENCH_MAX_CALLS'])
    with _lock, (_directory() / 'ai4vn-call-count').open('a+', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        count = int(stream.read() or 0)
        if limit is not None and count >= limit:
            raise RuntimeError('benchmark_call_budget_exhausted')
        stream.seek(0)
        stream.truncate()
        stream.write(str(count + 1))
        stream.flush()
        fcntl.flock(stream, fcntl.LOCK_UN)


def http_request(request):
    if os.getenv('BENCH_ALLOW_LIVE') != '1':
        raise RuntimeError('benchmark_live_not_authorized')
    context = _current.get() or _base('unknown')
    try:
        payload = json.loads(request.content)
    except (ValueError, UnicodeError):
        raise RuntimeError('benchmark_non_json_http_payload_unsupported')
    from story_benchmark.model_parameters import apply_model_parameters
    parameters = json.loads(os.getenv('BENCH_MODEL_PARAMETERS', '{}'))
    configured_payload = apply_model_parameters(payload, parameters)
    if configured_payload != payload:
        # This is the actual synchronous SDK request, including retry attempts.
        # Keep HTTPX's cached body, outgoing stream and length consistent.
        wire = json.dumps(configured_payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        httpx_module = importlib.import_module(type(request).__module__.split('.')[0])
        request._content = wire
        request.stream = httpx_module.ByteStream(wire)
        request.headers['Content-Length'] = str(len(wire))
    payload = configured_payload
    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if budget_mode() != 'unlimited' and len(serialized) > int(os.environ['BENCH_MAX_INPUT_CHARS']):
        raise RuntimeError('benchmark_input_budget_exhausted')
    _reserve_http_attempt()
    record = {**context, 'event': 'started', 'boundary': 'http',
              'call_id': uuid.uuid4().hex, 'attempt': int(request.headers.get('x-stainless-retry-count', '0')) + 1,
              'start_time': _now(), 'end_time': None,
              'request_messages': payload.get('messages'), 'requested_model': payload.get('model'), 'actual_model': None,
              'request_schema_and_sampling': {k: v for k, v in payload.items() if k not in ('messages', 'model')},
              'response_file': None}
    request.extensions['benchmark_record'] = record
    _emit(record)


def http_response(response):
    record = response.request.extensions.get('benchmark_record')
    if record is None:
        return
    # Native text clients are non-streaming. Reading caches bytes for the SDK.
    content = response.read()
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError):
        payload = {'unparsed_body': content.decode('utf-8', errors='replace')}
    filename = f"response-{record['call_id']}.json"
    (_directory() / filename).write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2), encoding='utf-8')
    choices = payload.get('choices') or []
    _emit({**record, 'event': 'completed' if response.is_success else 'error',
           'end_time': _now(), 'status_code': response.status_code,
           'response_file': filename, 'usage': payload.get('usage'),
           'usage_coverage_complete': bool(payload.get('usage')),
           'provider_request_id': response.headers.get('x-request-id') or response.headers.get('request-id'),
           'actual_model': payload.get('model'),
           'finish_reason': choices[0].get('finish_reason') if choices else None,
           'error': payload.get('error') if not response.is_success else None})


def openai_client_kwargs():
    if not enabled():
        return {}
    from openai import DefaultHttpxClient
    return {'http_client': DefaultHttpxClient(event_hooks={'request': [http_request], 'response': [http_response]})}


def output_cap(native_limit=None):
    if not enabled() or budget_mode() == 'unlimited':
        return {} if native_limit is None else {'max_tokens': native_limit}
    cap = int(os.environ['BENCH_MAX_OUTPUT_TOKENS'])
    return {'max_tokens': cap if native_limit is None else min(native_limit, cap)}


def execution_arguments(arguments):
    """Explicit experimental execution cap, separate from the observer hooks."""
    if not enabled() or budget_mode() == 'unlimited':
        return arguments
    capped = dict(arguments)
    cap = int(os.environ['BENCH_MAX_OUTPUT_TOKENS'])
    existing_keys = [key for key in ('max_tokens', 'max_completion_tokens') if capped.get(key) is not None]
    if existing_keys:
        for key in existing_keys:
            capped[key] = min(capped[key], cap)
    else:
        capped['max_tokens'] = cap
    return capped


def sdk_error_metadata(error, call_id):
    """Classify observed delivery separately from a native/SDK exception."""
    calls = {}
    trace = _directory() / f'ai4vn-{os.getpid()}.jsonl'
    if trace.is_file():
        for line in trace.read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('boundary') == 'http' and event.get('parent_call_id') == call_id:
                calls.setdefault(event['call_id'], {}).update(event)
    if any(event.get('event') == 'started' for event in calls.values()):
        return {'delivery_status': 'delivery_unknown', 'failure_code': 'delivery_unknown'}
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, RuntimeError) and str(cause) in ('benchmark_call_budget_exhausted', 'benchmark_input_budget_exhausted'):
            return {'delivery_status': 'completed' if calls else 'not_sent', 'failure_code': 'budget_exhausted'}
        cause = cause.__cause__ or cause.__context__
    return {'delivery_status': 'completed' if calls else 'not_sent',
            'failure_code': 'native_http_error' if getattr(error, 'status_code', None) else 'native_sdk_error'}


def sdk_call(provider, function, arguments):
    if not enabled():
        return function(**arguments)
    if os.getenv('BENCH_ALLOW_LIVE') != '1':
        raise RuntimeError('benchmark_live_not_authorized')
    # Google SDK retry coverage is not implemented, so first-phase live runs are
    # explicitly OpenAI-only instead of silently reporting incomplete budgets.
    if provider != 'openai':
        raise RuntimeError('benchmark_provider_http_budget_unsupported')
    arguments = execution_arguments(arguments)
    arguments_plain = _plain(arguments)
    callers = [f'{Path(f.filename).stem}.{f.function}' for f in inspect.stack() if Path(f.filename).name.endswith('_agent.py') and Path(f.filename).stem != 'base_agent']
    stage = callers[0] if callers else os.getenv('BENCH_OPERATION_ID', 'unknown')
    record = {**_base(stage), 'boundary': 'sdk', 'call_id': uuid.uuid4().hex, 'attempt': 1,
              'event': 'started', 'start_time': _now(), 'end_time': None,
              'request_messages': arguments_plain.get('messages'), 'requested_model': arguments_plain.get('model'), 'actual_model': None,
              'request_schema_and_sampling': {k: v for k, v in arguments_plain.items() if k not in ('messages', 'model')},
              'response_file': None}
    token = _current.set({**_base(stage), 'parent_call_id': record['call_id']})
    _emit(record)
    try:
        result = function(**arguments)
        payload = _plain(result)
        choices = payload.get('choices') or []
        filename = f"sdk-response-{record['call_id']}.json"
        (_directory() / filename).write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        _emit({**record, 'event': 'completed', 'end_time': _now(), 'response_file': filename,
               'usage': payload.get('usage'), 'provider_request_id': getattr(result, '_request_id', None),
               'finish_reason': choices[0].get('finish_reason') if choices else None,
               'usage_coverage_complete': False})
        return result
    except Exception as error:
        _emit({**record, 'event': 'error', 'end_time': _now(), 'error': {'type': type(error).__name__, 'message': str(error)},
               **sdk_error_metadata(error, record['call_id'])})
        raise
    finally:
        _current.reset(token)
