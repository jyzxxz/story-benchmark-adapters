"""Opt-in experiment tracing at the actual HTTP boundary, including SDK retries.

Only serial, dedicated experiment deployments may set BENCH_RUN_ID. The API and
worker must share BENCH_TRACE_DIR. Output budget uses conservative reservations;
missing usage never becomes zero and never releases a reservation.
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
    calls = int(os.environ["BENCH_MAX_CALLS"])
    per_call_output = int(os.environ["BENCH_MAX_OUTPUT_TOKENS"])
    output = calls * per_call_output
    max_input = int(os.environ["BENCH_MAX_INPUT_CHARS"])
    requested = body.get("max_completion_tokens", body.get("max_tokens"))
    if not isinstance(requested, int) or requested <= 0:
        raise RuntimeError("benchmark requires explicit positive native max_tokens")
    if requested > per_call_output:
        raise RuntimeError("native max_tokens exceeds benchmark per-call output limit")
    if len(json.dumps(body, ensure_ascii=False, separators=(",", ":"))) > max_input:
        raise RuntimeError("benchmark input character budget exhausted")
    root = _root()
    with (root / "budget.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "budget.json"
        state = json.loads(path.read_text()) if path.exists() else {
            "root_run_id": os.environ["BENCH_RUN_ID"], "calls": 0, "reserved_output_tokens": 0}
        if state["root_run_id"] != os.environ["BENCH_RUN_ID"]:
            raise RuntimeError("trace directory belongs to another root run")
        if state["calls"] + 1 > calls or state["reserved_output_tokens"] + requested > output:
            raise RuntimeError("benchmark call/output budget exhausted")
        state["calls"] += 1
        state["reserved_output_tokens"] += requested
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
        "error": None, "budget_policy": "clamp_native_output_cap_and_root_http_call_cap",
        "budget_output_cap": int(os.environ["BENCH_MAX_OUTPUT_TOKENS"])}
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
        client = factory(timeout=result.get("timeout", DEFAULT_TIMEOUT),
            limits=DEFAULT_LIMITS)
        result["http_client"] = client
    hooks = (("request", on_request_sync), ("response", on_response_sync)) if sync else (("request", on_request), ("response", on_response))
    for key, hook in hooks:
        if hook not in client.event_hooks[key]:
            client.event_hooks[key].append(hook)
    return result


def apply_output_budget(kwargs, path):
    """Explicit experiment budget, separate from observational HTTP hooks."""
    if not enabled():
        return kwargs
    if path != ("chat", "completions"):
        raise RuntimeError("text benchmark forbids media provider calls")
    result = dict(kwargs)
    cap = int(os.environ["BENCH_MAX_OUTPUT_TOKENS"])
    key = "max_completion_tokens" if "max_completion_tokens" in result else "max_tokens"
    native = result.get(key)
    result[key] = min(native, cap) if isinstance(native, int) and native > 0 else cap
    return result


def write_worker_receipt():
    if not enabled():
        return
    receipt = {"role": "worker", "system": "if_line", "pid": os.getpid(),
        "root_run_id": os.environ["BENCH_RUN_ID"], "trace_dir": str(_root()),
        "max_calls": int(os.environ["BENCH_MAX_CALLS"]),
        "max_output_tokens": int(os.environ["BENCH_MAX_OUTPUT_TOKENS"]),
        "max_input_chars": int(os.environ["BENCH_MAX_INPUT_CHARS"]),
        "model": os.environ["LLM_MODEL"], "model_base_url": os.environ["OPENAI_BASE_URL"], "created_at": _now()}
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
