"""Opt-in experiment tracing at the actual HTTP boundary, including SDK retries.

Only serial, dedicated experiment deployments may set BENCH_RUN_ID. The API and
worker must share BENCH_TRACE_DIR. Bounded mode uses conservative reservations;
unlimited mode counts requests without imposing caps or changing native limits.
Missing usage never becomes zero.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import zlib

_context = ContextVar("benchmark_task", default={})
_SECRET = re.compile(r"(?i)(authorization|cookie|api[_-]?key|access[_-]?token|secret|password)")


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _SECRET.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if re.search(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|COOKIE|SID)", key) and len(secret) >= 8:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|secret|password|sid)=)[^&\s\"']+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)(Cookie\s*:\s*)[^\r\n]+", r"\1[REDACTED]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
    return value


def enabled():
    return bool(os.getenv("BENCH_RUN_ID"))


def budget_mode():
    mode = os.getenv("BENCH_BUDGET_MODE", "bounded")
    if mode not in {"bounded", "unlimited"}:
        raise RuntimeError("unknown benchmark budget mode")
    return mode


def validate_shared_input(request, instructions, mode):
    if not enabled() or mode != "shared_task":
        raise ValueError("shared input requires a dedicated BENCH_RUN_ID deployment")
    if instructions is not None and str(instructions).strip():
        raise ValueError("shared input forbids Bible instructions; task is already in extra_requirements")
    if request.get("story_start") or request.get("story_end"):
        raise ValueError("shared input requires empty story_start and story_end")
    shared = request.get("extra_requirements")
    if not isinstance(shared, str) or not shared or shared != shared.strip():
        raise ValueError("invalid shared_task")


@contextmanager
def task_context(task):
    token = _context.set({"native_task_id": task.id, "native_project_id": task.project_id,
                          "stage": task.kind, "native_attempt": task.attempt})
    try:
        yield
    finally:
        _context.reset(token)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _root():
    root = Path(os.environ["BENCH_TRACE_DIR"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _emit(event):
    with (_root() / f"if_line-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(redact(event), ensure_ascii=False) + "\n")


def _reserve(body):
    """Cross-process reservation before send; no request/model parameter changes."""
    mode = budget_mode()
    unlimited = mode == "unlimited"
    calls = None if unlimited else int(os.environ["BENCH_MAX_CALLS"])
    per_call_output = None if unlimited else int(os.environ["BENCH_MAX_OUTPUT_TOKENS"])
    output = None if unlimited else calls * per_call_output
    max_input = None if unlimited else int(os.environ["BENCH_MAX_INPUT_CHARS"])
    requested = body.get("max_completion_tokens", body.get("max_tokens"))
    if not unlimited and (not isinstance(requested, int) or requested <= 0):
        raise RuntimeError("benchmark requires explicit positive native max_tokens")
    if not unlimited and requested > per_call_output:
        raise RuntimeError("native max_tokens exceeds benchmark per-call output limit")
    if not unlimited and len(json.dumps(body, ensure_ascii=False, separators=(",", ":"))) > max_input:
        raise RuntimeError("benchmark input character budget exhausted")
    root = _root()
    with (root / "budget.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "budget.json"
        state = json.loads(path.read_text()) if path.exists() else {
            "root_run_id": os.environ["BENCH_RUN_ID"], "budget_mode": mode,
            "calls": 0, "reserved_output_tokens": None if unlimited else 0}
        if state["root_run_id"] != os.environ["BENCH_RUN_ID"]:
            raise RuntimeError("trace directory belongs to another root run")
        if state.get("budget_mode", "bounded") != mode:
            raise RuntimeError("trace directory budget mode differs from run")
        if not unlimited and (state["calls"] + 1 > calls or state["reserved_output_tokens"] + requested > output):
            raise RuntimeError("benchmark call/output budget exhausted")
        state["calls"] += 1
        if not unlimited: state["reserved_output_tokens"] += requested
        signature = hashlib.sha256(json.dumps([_context.get().get("native_task_id"), body],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        attempts = state.setdefault("request_attempts", {})
        attempts[signature] = attempts.get(signature, 0) + 1
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
        return state["calls"], attempts[signature]


def on_request_sync(request):
    body = json.loads(request.content)
    parameters = json.loads(os.getenv("BENCH_MODEL_PARAMETERS", "{}"))
    from story_benchmark.model_parameters import apply_model_parameters
    effective = apply_model_parameters(body, parameters)
    if effective != body:
        import httpx
        # The old native OpenAI SDK does not accept provider-specific keyword
        # arguments. Apply the shared policy to the final JSON wire body.
        raw = json.dumps(effective, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request._content = raw
        request.stream = httpx.ByteStream(raw)
        request.headers["Content-Length"] = str(len(raw))
        request.headers.pop("Transfer-Encoding", None)
    body = effective
    try:
        if os.getenv("LLM_MODEL") and body.get("model") != os.environ["LLM_MODEL"]:
            raise RuntimeError("requested model differs from frozen common model")
        ordinal, attempt = _reserve(body)
    except RuntimeError as exc:
        import sys
        _emit({"event":"error", "boundary":"budget", "system":"if_line",
            "root_run_id":os.environ["BENCH_RUN_ID"], **_context.get(),
            "call_id":str(uuid.uuid4()), "requested_model":body.get("model"), "actual_model":None,
            "usage":None, "error":{"code":"budget_exhausted" if "budget" in str(exc) or "limit" in str(exc) else "model_config_mismatch",
            "detail":str(exc)}, "wire_sent":False, "end_time":_now()})
        sys.stderr.write("[benchmark] request blocked before provider send: " + str(exc) + "\n")
        raise
    context = _context.get()
    event = {"event": "started", "root_run_id": os.environ["BENCH_RUN_ID"],
        "system": "if_line", "boundary": "http", "call_id": str(uuid.uuid4()),
        "operation_id": context.get("native_task_id") or os.getenv("BENCH_OPERATION_ID"),
        "attempt": attempt,
        "http_call_ordinal": ordinal, "stage": "unclassified", **context,
        "requested_model": body.get("model"), "actual_model": None, "request_messages": body.get("messages"),
        "request_schema_and_sampling": {k: v for k, v in body.items() if k != "messages"},
        "provider_request_id": None, "response_file": None, "finish_reason": None,
        "usage": None, "usage_complete": False, "start_time": _now(), "end_time": None,
        "error": None, "budget_mode": budget_mode(),
        "budget_policy": "observe_only_native_limits_unchanged" if budget_mode()=="unlimited" else "clamp_native_output_cap_and_root_http_call_cap",
        "model_parameters": parameters,
        "async_transport_keepalive_connections": 0,
        "budget_output_cap": None if budget_mode()=="unlimited" else int(os.environ["BENCH_MAX_OUTPUT_TOKENS"])}
    request.extensions["benchmark_event"] = event
    _emit(event)


async def on_request(request):
    on_request_sync(request)


def _finish(event, response, chunks, error=None):
    raw = b"".join(chunks)
    encoding = response.headers.get("content-encoding", "")
    try:
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw)
        text = raw.decode("utf-8")
        if "text/event-stream" in response.headers.get("content-type", ""):
            objects = [json.loads(line[5:].strip()) for line in text.splitlines()
                       if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")]
            payload = {"stream_events": objects}
        else:
            payload = json.loads(text)
            objects = [payload]
    except (ValueError, UnicodeError, OSError):
        payload = {"unparsed_response": True, "byte_length": len(raw)}
        objects = []
    usage = next((x["usage"] for x in reversed(objects) if x.get("usage") is not None), None)
    finish = next((choice.get("finish_reason") for x in reversed(objects)
                   for choice in x.get("choices", []) if choice.get("finish_reason")), None)
    model = next((x["model"] for x in objects if x.get("model")), None)
    provider_id = response.headers.get("x-request-id") or next((x["id"] for x in objects if x.get("id")), None)
    filename = f"responses/{event['call_id']}.json"
    dest = _root() / filename
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    _emit({**event, "event": "error" if error or response.status_code >= 400 else "completed",
        "actual_model": model, "provider_request_id": provider_id,
        "response_file": filename, "finish_reason": finish, "usage": usage,
        "usage_complete": usage is not None, "end_time": _now(),
        "http_status": response.status_code,
        "error": error or ({"code": "provider_http_error", "status": response.status_code}
                           if response.status_code >= 400 else None)})


async def on_response(response):
    import httpx
    event = response.request.extensions.get("benchmark_event")
    if event is None:
        raise RuntimeError("benchmark response missing request correlation")
    if response.is_stream_consumed:
        _finish(event, response, [response.content])
        return

    class ObservedStream(httpx.AsyncByteStream):
        def __init__(self, inner):
            self.inner, self.chunks, self.done = inner, [], False

        async def __aiter__(self):
            try:
                async for chunk in self.inner:
                    self.chunks.append(chunk)
                    yield chunk
            except BaseException as exc:
                self.done = True
                _finish(event, response, self.chunks, {"code": "stream_error", "type": type(exc).__name__})
                raise
            else:
                self.done = True
                _finish(event, response, self.chunks)

        async def aclose(self):
            try:
                await self.inner.aclose()
            finally:
                if not self.done:
                    self.done = True
                    _finish(event, response, self.chunks, {"code": "stream_closed_early"})

    response.stream = ObservedStream(response.stream)


def on_response_sync(response):
    import httpx
    event = response.request.extensions.get("benchmark_event")
    if event is None:
        raise RuntimeError("benchmark response missing request correlation")
    if response.is_stream_consumed:
        _finish(event, response, [response.content])
        return
    class ObservedStream(httpx.SyncByteStream):
        def __init__(self, inner):
            self.inner, self.chunks, self.done = inner, [], False
        def __iter__(self):
            try:
                for chunk in self.inner:
                    self.chunks.append(chunk)
                    yield chunk
            except BaseException as exc:
                self.done = True
                _finish(event, response, self.chunks, {"code":"stream_error", "type":type(exc).__name__})
                raise
            else:
                self.done = True
                _finish(event, response, self.chunks)
        def close(self):
            try:
                self.inner.close()
            finally:
                if not self.done:
                    self.done = True
                    _finish(event, response, self.chunks, {"code":"stream_closed_early"})
    response.stream = ObservedStream(response.stream)


def instrument_client_kwargs(kwargs, *, sync=False):
    if not enabled():
        return kwargs
    # Frozen openai==1.6.1 creates this exact wrapper internally. Reuse its
    # defaults so instrumentation does not change proxy/timeout/pool behavior.
    from openai._base_client import AsyncHttpxClientWrapper, SyncHttpxClientWrapper
    from openai._constants import DEFAULT_TIMEOUT, DEFAULT_LIMITS
    result = dict(kwargs)
    client = result.get("http_client")
    if client is None:
        factory = SyncHttpxClientWrapper if sync else AsyncHttpxClientWrapper
        limits = DEFAULT_LIMITS
        if not sync:
            import httpx
            # Native workers use asyncio.run repeatedly while caching the same
            # AsyncOpenAI client. Live HTTP/1.1 connections belong to their first
            # loop; retaining one can send a request then fail on the next loop.
            limits = httpx.Limits(max_connections=DEFAULT_LIMITS.max_connections,
                max_keepalive_connections=0, keepalive_expiry=DEFAULT_LIMITS.keepalive_expiry)
        client = factory(timeout=result.get("timeout", DEFAULT_TIMEOUT),
            limits=limits)
        result["http_client"] = client
    _observe_client_transport(client, sync=sync)
    hooks = (("request", on_request_sync), ("response", on_response_sync)) if sync else (("request", on_request), ("response", on_response))
    for key, hook in hooks:
        if hook not in client.event_hooks[key]:
            client.event_hooks[key].append(hook)
    return result


def _transport_error(request, exc):
    event = request.extensions.get("benchmark_event")
    if event is None:
        return
    _emit({**event, "event":"error", "end_time":_now(),
        "actual_model":None, "response_file":None, "usage":None, "usage_complete":False,
        "delivery_status":"delivery_unknown", "wire_sent":None,
        "error":{"code":"transport_error", "type":type(exc).__name__, "detail":str(exc)}})


def _observe_client_transport(client, *, sync=False):
    """Observe exceptions below HTTPX hooks without changing SDK retry behavior.

All existing proxy mounts and transports are retained and delegated to. A
transport error does not prove a request stayed local: retain unknown delivery.
"""
    import httpx
    class AsyncObserved(httpx.AsyncBaseTransport):
        _ifline_benchmark_observed = True
        def __init__(self, inner): self.inner = inner
        async def handle_async_request(self, request):
            try: return await self.inner.handle_async_request(request)
            except BaseException as exc:
                _transport_error(request, exc)
                raise
        async def aclose(self): await self.inner.aclose()
    class SyncObserved(httpx.BaseTransport):
        _ifline_benchmark_observed = True
        def __init__(self, inner): self.inner = inner
        def handle_request(self, request):
            try: return self.inner.handle_request(request)
            except BaseException as exc:
                _transport_error(request, exc)
                raise
        def close(self): self.inner.close()
    observed = {}
    def wrap(inner):
        if inner is None or getattr(inner, "_ifline_benchmark_observed", False):
            return inner
        if id(inner) not in observed:
            observed[id(inner)] = (SyncObserved if sync else AsyncObserved)(inner)
        return observed[id(inner)]
    client._transport = wrap(client._transport)
    client._mounts = {pattern:wrap(inner) for pattern,inner in client._mounts.items()}


def apply_output_budget(kwargs, path):
    """Explicit experiment budget, separate from observational HTTP hooks."""
    if not enabled():
        return kwargs
    if path != ("chat", "completions"):
        raise RuntimeError("text benchmark forbids media provider calls")
    if budget_mode() == "unlimited":
        return kwargs
    result = dict(kwargs)
    cap = int(os.environ["BENCH_MAX_OUTPUT_TOKENS"])
    key = "max_completion_tokens" if "max_completion_tokens" in result else "max_tokens"
    native = result.get(key)
    result[key] = min(native, cap) if isinstance(native, int) and native > 0 else cap
    return result


def write_worker_receipt():
    if not enabled():
        return
    task_timeout = None if budget_mode()=="unlimited" else float(os.getenv("BENCH_TIMEOUT_SECONDS", "900"))
    receipt = {"role": "worker", "system": "if_line", "pid": os.getpid(),
        "root_run_id": os.environ["BENCH_RUN_ID"], "trace_dir": str(_root()),
        "budget_mode": budget_mode(),
        "max_calls": None if budget_mode()=="unlimited" else int(os.environ["BENCH_MAX_CALLS"]),
        "max_output_tokens": None if budget_mode()=="unlimited" else int(os.environ["BENCH_MAX_OUTPUT_TOKENS"]),
        "max_input_chars": None if budget_mode()=="unlimited" else int(os.environ["BENCH_MAX_INPUT_CHARS"]),
        "timeout_seconds": task_timeout,
        "model": os.environ["LLM_MODEL"], "model_base_url": os.environ["OPENAI_BASE_URL"], "created_at": _now()}
    receipt.update(model_parameters=json.loads(os.getenv("BENCH_MODEL_PARAMETERS", "{}")),
                   async_transport_keepalive_connections=0,
                   native_request_timeouts="unchanged",
                   active_adapter_budget_env=sorted(k for k in os.environ if k.startswith("BENCH_MAX_") or k=="BENCH_TIMEOUT_SECONDS"),
                   service_watchdogs={"api_request_seconds":60 if task_timeout is None else min(60,task_timeout),
                                      "startup_seconds":60 if task_timeout is None else min(60,task_timeout),
                                      "cleanup_parent_seconds":40,"cleanup_child_seconds":10,"cleanup_kill_wait_seconds":5})
    (_root() / "if_line_worker_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def finalize_pending():
    """A request without a response stays explicitly uncertain on shutdown."""
    if not enabled():
        return
    path = _root() / f"if_line-{os.getpid()}.jsonl"
    if not path.exists():
        return
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("boundary") == "http" and event.get("call_id"):
            latest[event["call_id"]] = event
    for event in latest.values():
        if event.get("event") == "started":
            _emit({**event, "event":"error", "end_time":_now(),
                   "usage":None,"actual_model":None,"error":{"code":"delivery_unknown"}})
