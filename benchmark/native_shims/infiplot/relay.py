"""Loopback OpenAI-compatible relay for frozen InfiPlot a60e18b.

The only prose instruction edit is the exact cold-start transition sentence.
The common task is extracted from actual native SDK messages and never rebuilt.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection, HTTPSConnection, IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
from threading import Condition, Lock, Thread
import time
from urllib import error, request
from urllib.parse import urlsplit
from uuid import uuid4

from story_benchmark.model_parameters import apply_model_parameters

BASE_COMMIT = "a60e18bc663caaa134d9323a2b89159b7cc9bd05"
COLD_START = "这是故事的开场。请按【故事档案】里的 nextHook 把第一幕的冷开场设计出来——开场即抓人，别花笔墨铺垫世界观。"
SHARED_START = "这是本系统的第一次生成，但共同任务中的固定开头已经发生。正文从固定开头末尾继续，不重新设计开场。"
TASK_BEGIN = "<<<SHARED_TASK_V2_BEGIN>>>"
TASK_END = "<<<SHARED_TASK_V2_END>>>"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def extract_task(messages):
    """Extract actual SDK content by fixed compiler markers, not expected hashes."""
    found = []
    for pos, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("non_string_native_message")
        if content.count(TASK_BEGIN) != content.count(TASK_END):
            raise ValueError("unbalanced_shared_task_boundary")
        if TASK_BEGIN in content:
            if content.count(TASK_BEGIN) != 1:
                raise ValueError("duplicated_shared_task_boundary")
            start = content.index(TASK_BEGIN)
            end = content.index(TASK_END, start) + len(TASK_END)
            found.append((content[start:end], pos, start, end))
    if len(found) != 1:
        raise ValueError("shared_task_not_present_exactly_once")
    return found[0]


def adapt_messages(payload, shared, enabled=True, allow_native_continuation=False):
    result = deepcopy(payload)
    messages = result.get("messages")
    if not isinstance(messages, list):
        raise ValueError("missing_messages")
    is_writer = any(isinstance(m.get("content"), str) and "编剧，下面是当前情境：" in m["content"] for m in messages)
    replacements, receipt = [], None
    if is_writer:
        actual, index, start, end = extract_task(messages)
        if actual != shared:
            raise ValueError("native_sdk_shared_task_mismatch")
        receipt = {"boundary": "native_sdk_task_block", "received_task": actual,
                   "direct_native_route_receiver_observed": False,
                   "message_index": index, "start_codepoint": start, "end_codepoint": end}
        if enabled:
            matches = [(i, m["content"].index(COLD_START)) for i, m in enumerate(messages) if COLD_START in m["content"]]
            occurrences = sum(m["content"].count(COLD_START) for m in messages)
            # The batch driver follows the original browser's /api/scene path.
            # Those Writer requests already contain native history and must not
            # receive another opening instruction. Legacy first-scene behavior
            # remains strict unless this explicit batch-only mode is enabled.
            if occurrences == 0 and allow_native_continuation:
                if not any(m.get("role") == "user" and "承接「玩家在上一场选择了：" in m["content"]
                           and "无缝续写下一个场景" in m["content"] for m in messages):
                    raise ValueError("native_continuation_signature_missing")
                return result, replacements, receipt, "writer"
            if len(matches) != 1 or occurrences != 1:
                raise ValueError("frozen_cold_start_signature_not_unique")
            index, start = matches[0]
            if messages[index].get("role") != "user":
                raise ValueError("unexpected_cold_start_role")
            messages[index]["content"] = messages[index]["content"].replace(COLD_START, SHARED_START, 1)
            replacements.append({"boundary": "native_sdk_user_transition", "message_index": index,
                                 "start_codepoint": start, "before": COLD_START, "after": SHARED_START,
                                 "frozen_commit": BASE_COMMIT})
            if extract_task(messages)[0] != actual:
                raise ValueError("adapter_modified_shared_task")
    return result, replacements, receipt, "writer" if is_writer else "auxiliary_text"


def upstream_endpoint(raw):
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_frozen_model_base_url")
    endpoint = raw.strip().rstrip("/")
    endpoint = re.sub(r"/(chat/completions|completions|responses|messages|images/(generations|edits))$", "", endpoint, flags=re.I).rstrip("/")
    if parsed.path in ("", "/"):
        endpoint += "/v1"
    return endpoint + "/chat/completions"


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Relay:
    def __init__(self, config, handle, shared):
        self.config, self.handle, self.shared = config, handle, shared
        self.trace = Path(handle["trace_dir"])
        self.trace.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self.condition = Condition(self.lock)
        self.active = 0
        self.connections = set()
        self.closing = False
        self.evidence_closed = False
        self.endpoint = upstream_endpoint(config["model_base_url"])
        self.api_key = os.environ["TEXT_API_KEY"]
        self.secret_values = [v for k, v in os.environ.items() if re.search(r"KEY|TOKEN|PASSWORD|COOKIE|SECRET", k, re.I) and len(v) >= 8]
        self.count = 0
        for file in self.trace.glob("calls-*.jsonl"):
            for line in file.read_text().splitlines():
                self.count += json.loads(line).get("event") == "started"
        self.op_attempts = {}
        self.server = None
        self.thread = None

    def safe(self, value):
        if isinstance(value, dict):
            return {k: "[REDACTED]" if re.fullmatch(r"authorization|cookie|set-cookie|api[-_]?key|access[-_]?token|refresh[-_]?token|password|secret", k, re.I) else self.safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in self.secret_values:
                value = value.replace(secret, "[REDACTED]")
        return value

    def emit(self, value):
        row = {"root_run_id": self.handle["root_run_id"], "system": "infiplot", "boundary": "http", **value}
        if self.config.get("batch_native_continuation"):
            from story_benchmark.recording import redact_evidence
            row = redact_evidence(row)
        with self.lock:
            if self.evidence_closed:
                return
            with (self.trace / f"calls-{os.getpid()}.jsonl").open("a") as file:
                file.write(compact(self.safe(row)) + "\n")

    def save(self, path, value):
        if self.config.get("batch_native_continuation"):
            from story_benchmark.recording import redact_evidence
            value = redact_evidence(value)
        target = self.trace / path
        with self.lock:
            if self.evidence_closed:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as file:
                file.write(self.safe(value) if isinstance(value, str) else json.dumps(self.safe(value), ensure_ascii=False, indent=2))

    def save_partial(self, path, raw):
        """Retain incomplete wire bytes, redacting secrets before binary storage."""
        stored = raw
        for secret in self.secret_values:
            stored = stored.replace(secret.encode("utf-8"), b"[REDACTED]")
        if self.config.get("batch_native_continuation"):
            from story_benchmark.recording import redact_evidence
            stored = redact_evidence(stored.decode("utf-8", errors="surrogateescape")).encode("utf-8", errors="surrogateescape")
        target = self.trace / path
        with self.lock:
            if not self.evidence_closed:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as file:
                    file.write(stored)
        return {"partial_response_bytes_observed": len(raw),
                "partial_response_sha256": digest(raw),
                "partial_response_saved_sha256": digest(stored),
                "partial_response_redacted": stored != raw}

    def start(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                with relay.condition:
                    relay.active += 1
                try:
                    relay.handle_request(self)
                finally:
                    with relay.condition:
                        relay.active -= 1
                        relay.condition.notify_all()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def wait_for_idle(self):
        timeout = self.generation_timeout()
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            while self.active:
                if deadline is None:
                    self.condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("relay_response_capture_timeout")
                self.condition.wait(remaining)

    def generation_timeout(self):
        return None if self.config.get("budget_mode", "bounded") == "unlimited" else float(self.config.get("timeout_seconds", 180))

    def close(self):
        with self.condition:
            self.closing = True
            connections = list(self.connections)
        for connection in connections:
            self._interrupt_connection(connection)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(10)
        deadline = time.monotonic() + 10
        with self.condition:
            while self.active and time.monotonic() < deadline:
                self.condition.wait(max(0, deadline - time.monotonic()))
            # Even an abnormal stuck connection may never append to a sealed
            # run. The runner records cleanup failure instead of claiming that
            # such a connection was fully reconciled.
            self.evidence_closed = True
            if self.active:
                raise RuntimeError("relay_cleanup_incomplete: active provider handlers did not stop")

    @staticmethod
    def _interrupt_connection(connection):
        # urllib closes its connection socket after handing HTTPResponse to the
        # caller. The response's buffered reader still owns that same fd.
        sock = getattr(connection, "_benchmark_socket", None) or getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    def _connection_type(self, base):
        relay = self

        class ObservedConnection(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                with relay.condition:
                    if relay.closing:
                        raise ValueError("relay_closing")
                    relay.connections.add(self)

            def connect(self):
                super().connect()
                self._benchmark_socket = self.sock
                with relay.condition:
                    closing = relay.closing
                if closing:
                    relay._interrupt_connection(self)
                    raise ValueError("relay_closing")

        return ObservedConnection

    def _opener(self):
        http_type = self._connection_type(HTTPConnection)
        https_type = self._connection_type(HTTPSConnection)

        class ObservedHTTP(request.HTTPHandler):
            def http_open(self, req):
                return self.do_open(http_type, req)

        class ObservedHTTPS(request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(https_type, req, context=self._context,
                                    check_hostname=self._check_hostname)

        return request.build_opener(NoRedirect, ObservedHTTP, ObservedHTTPS)

    def handle_request(self, handler):
        common = None
        sent_headers = False
        chunks = []
        status = None
        provider_id = None
        call_id = str(uuid4())
        try:
            if handler.headers.get("Authorization") != "Bearer " + self.api_key:
                raise ValueError("invalid_local_relay_authorization")
            if handler.path != "/v1/chat/completions":
                raise ValueError("unsupported_native_endpoint")
            length = int(handler.headers.get("Content-Length", "0"))
            unlimited = self.config.get("budget_mode", "bounded") == "unlimited"
            if length <= 0 or (not unlimited and length > self.config["max_input_chars"] * 8):
                raise ValueError("invalid_or_excessive_request_size")
            raw = handler.rfile.read(length)
            before = json.loads(raw)
            self.save(f"requests/{call_id}.before.json", before)
            after, replacements, receipt, stage = adapt_messages(
                before, self.shared, self.config.get("shared_opening", True),
                self.config.get("batch_native_continuation", False))
            if receipt:
                self.save(f"received-{call_id}.json", {"root_run_id": self.handle["root_run_id"], "system": "infiplot", "call_id": call_id, **receipt})
            if before.get("model") != self.config["model"]:
                raise ValueError("native_request_model_mismatch")
            after_prompt_hash = digest(compact(after))
            parameters = deepcopy(self.config.get("model_parameters", {}))
            native_parameters = after
            after = apply_model_parameters(after, parameters)
            parameter_changes = [{"field": key, "before": deepcopy(native_parameters.get(key)), "after": deepcopy(value)}
                                 for key, value in parameters.items() if native_parameters.get(key) != value]
            after_parameters_hash = digest(compact(after))
            cap_key = "max_completion_tokens" if "max_completion_tokens" in after else "max_tokens"
            native_cap = after.get(cap_key)
            sampling_changes = []
            if not unlimited:
                if native_cap is not None and (not isinstance(native_cap, int) or isinstance(native_cap, bool) or native_cap <= 0):
                    raise ValueError("invalid_native_output_cap")
                after[cap_key] = min(native_cap or self.config["max_output_tokens"], self.config["max_output_tokens"])
                if native_cap != after[cap_key]:
                    sampling_changes = [{"field": cap_key, "before": native_cap, "after": after[cap_key]}]
            serialized = compact(after)
            self.save(f"requests/{call_id}.after.json", after)
            if not unlimited and len(serialized) > self.config["max_input_chars"]:
                raise ValueError("input_budget_exceeded")
            with self.lock:
                if not unlimited and self.count >= self.config["max_calls"]:
                    raise ValueError("call_budget_exceeded")
                self.count += 1
                op_hash = digest(compact(before.get("messages", [])))[:16]
                operation = ("native-context:" if self.config.get("batch_native_continuation") else "start:") + op_hash
                self.op_attempts[operation] = self.op_attempts.get(operation, 0) + 1
                attempt = self.op_attempts[operation]
            common = {"call_id": call_id, "operation_id": operation, "attempt": attempt, "stage": stage,
                      "requested_model": before["model"], "actual_model": None,
                      "request_messages": after["messages"], "native_sdk_request_messages": before["messages"],
                      "request_schema_and_sampling": {k: v for k, v in after.items() if k != "messages"},
                      "start_time": datetime.now(timezone.utc).isoformat(), "usage": None,
                      "response_file": None, "provider_request_id": None, "finish_reason": None,
                      "native_project_id": None, "native_task_id": None,
                      "native_operation_id": handler.headers.get("X-Benchmark-Native-Operation-Id") if self.config.get("batch_native_continuation") else None,
                      "sdk_raw_request_sha256": digest(raw), "before_request_sha256": digest(compact(before)),
                      "after_prompt_adaptation_sha256": after_prompt_hash,
                      "after_model_parameters_sha256": after_parameters_hash,
                      "after_request_sha256": digest(serialized),
                      "model_parameters": parameters, "model_parameter_changes": parameter_changes,
                      "prompt_replacements": replacements,
                      "budget_mode": "unlimited" if unlimited else "bounded",
                      "sampling_changes": sampling_changes,
                      "input_chars": len(serialized), "input_character_metric": "compact_http_json_unicode_codepoints_after_model_parameters" if unlimited else "compact_http_json_unicode_codepoints_after_cap"}
            self.emit({**common, "event": "started"})
            upstream = request.Request(self.endpoint, data=serialized.encode(), method="POST", headers={
                "Content-Type": "application/json", "Accept": "text/event-stream" if after.get("stream") else "application/json", "Authorization": "Bearer " + self.api_key})
            if self.config.get("batch_native_continuation"):
                upstream.add_header("X-Benchmark-Native-Task-Id", call_id)
                upstream.add_header("X-Benchmark-Native-Stage", stage)
                if common["native_operation_id"]:
                    upstream.add_header("X-Benchmark-Native-Operation-Id", common["native_operation_id"])
            opener = self._opener()
            try:
                response = opener.open(upstream, timeout=self.generation_timeout())
            except error.HTTPError as exc:
                response = exc
            disconnected = False
            with response:
                status = response.status
                provider_id = response.headers.get("x-request-id")
                handler.send_response(status)
                handler.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
                if provider_id:
                    handler.send_header("x-request-id", provider_id)
                handler.end_headers()
                sent_headers = True
                while True:
                    chunk = response.read1(65536)
                    if not chunk:
                        # HTTPResponse.read1 may return EOF while a declared
                        # Content-Length still has unread bytes.
                        if isinstance(response.length, int) and response.length > 0:
                            raise IncompleteRead(b"", response.length)
                        break
                    chunks.append(chunk)
                    if not disconnected:
                        try:
                            handler.wfile.write(chunk)
                            handler.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            disconnected = True
            raw_response = b"".join(chunks).decode("utf-8")
            response_path = f"responses/{call_id}.{'sse' if after.get('stream') else 'json'}"
            self.save(response_path, raw_response)
            rows = []
            if after.get("stream"):
                for line in raw_response.splitlines():
                    if line.startswith("data:") and line[5:].strip() != "[DONE]":
                        try:
                            rows.append(json.loads(line[5:]))
                        except ValueError:
                            pass
            else:
                try:
                    rows = [json.loads(raw_response)]
                except ValueError:
                    pass
            usage = next((row["usage"] for row in reversed(rows) if row.get("usage") is not None), None)
            actual = next((row["model"] for row in reversed(rows) if row.get("model")), None)
            finish = next((choice["finish_reason"] for row in reversed(rows) for choice in row.get("choices", []) if choice.get("finish_reason")), None)
            self.emit({**common, "event": "completed" if status < 400 else "error", "response_file": response_path,
                       "end_time": datetime.now(timezone.utc).isoformat(), "usage": usage, "usage_complete": usage is not None,
                       "actual_model": actual, "finish_reason": finish, "provider_request_id": provider_id,
                       "http_status": status, "error": None if status < 400 else f"HTTP {status}",
                       "delivery_status": "delivery_unknown" if disconnected else "response_received"})
        except Exception as exc:
            issue = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            partial_evidence = {}
            if common is not None and (status is not None or chunks):
                # read1() can raise with bytes from its final read; previous
                # successful reads are already in chunks. Never mark this as a
                # complete response or invent usage from a partial JSON/SSE.
                tail = exc.partial if isinstance(exc, IncompleteRead) else b""
                raw_partial = b"".join(chunks) + tail
                partial_path = f"responses/{call_id}.partial.bin"
                partial_evidence = self.save_partial(partial_path, raw_partial)
                partial_evidence.update({"response_file": partial_path, "response_complete": False,
                                         "response_capture_status": "partial", "http_status": status,
                                         "provider_request_id": provider_id, "usage_complete": False})
                if isinstance(exc, IncompleteRead):
                    partial_evidence["read_error_expected_additional_bytes"] = exc.expected
            self.emit({"call_id": call_id, **(common or {}), "event": "error" if common else "blocked", "error": issue,
                       "generation_issue_code": issue, "delivery_status": "delivery_unknown" if common else "not_sent",
                       **partial_evidence})
            if not sent_headers:
                handler.send_response(502 if common else 400)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                try:
                    handler.wfile.write(compact({"error": {"message": issue, "type": "benchmark_relay"}}).encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
