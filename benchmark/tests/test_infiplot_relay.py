"""External relay invariants with only local HTTP response fixtures."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib import error, request

from native_shims.infiplot.relay import COLD_START, SHARED_START, Relay, adapt_messages, compact, digest, extract_task, upstream_endpoint

SHARED = "<<<SHARED_TASK_V2_BEGIN>>>\nbrief🗝️\nopening\n<<<SHARED_TASK_V2_END>>>"


def payload(stream=False):
    return {"model": "fixture-model", "messages": [
        {"role": "system", "content": "原生系统规则\n\n世界观：" + SHARED + "\n\n画风：fixture"},
        {"role": "user", "content": "编剧，下面是当前情境：\n\n" + COLD_START + "\n\n保留全部其他原生要求"},
    ], "temperature": 0.9, "stream": stream, "response_format": {"type": "json_object"}}


class AdaptationTests(unittest.TestCase):
    def test_exact_scoped_replacement_and_schema_preservation(self):
        original = payload()
        snapshot = deepcopy(original)
        after, replacements, receipt, stage = adapt_messages(original, SHARED)
        self.assertEqual(original, snapshot)
        self.assertEqual(stage, "writer")
        self.assertEqual(after["messages"][0], original["messages"][0])
        self.assertEqual(after["messages"][1]["content"], original["messages"][1]["content"].replace(COLD_START, SHARED_START, 1))
        self.assertEqual({k: v for k, v in after.items() if k != "messages"}, {k: v for k, v in original.items() if k != "messages"})
        self.assertEqual(receipt["received_task"], extract_task(original["messages"])[0])
        self.assertEqual(receipt["boundary"], "native_sdk_task_block")
        self.assertEqual(len(replacements), 1)

    def test_disabled_prompt_path_byte_identical(self):
        original = payload()
        after, replacements, _, _ = adapt_messages(original, SHARED, enabled=False)
        self.assertEqual(compact(after), compact(original))
        self.assertEqual(replacements, [])

    def test_ambiguous_or_changed_inputs_fail_closed(self):
        for condition in ("duplicate_cold", "missing_cold", "duplicate_shared", "modified_shared"):
            original = payload()
            if condition == "duplicate_cold":
                original["messages"][1]["content"] += COLD_START
            elif condition == "missing_cold":
                original["messages"][1]["content"] = original["messages"][1]["content"].replace(COLD_START, "漂移版本")
            elif condition == "duplicate_shared":
                original["messages"][0]["content"] += SHARED
            else:
                original["messages"][0]["content"] = original["messages"][0]["content"].replace("opening", "changed")
            with self.assertRaises(ValueError):
                adapt_messages(original, SHARED)

    def test_auxiliary_messages_remain_identical(self):
        original = {"model": "fixture-model", "messages": [{"role": "user", "content": "原生分镜请求"}], "temperature": 0.6}
        after, replacements, receipt, stage = adapt_messages(original, SHARED)
        self.assertEqual(after, original)
        self.assertEqual(replacements, [])
        self.assertIsNone(receipt)
        self.assertEqual(stage, "auxiliary_text")

    def test_endpoint_normalization_matches_native(self):
        self.assertEqual(upstream_endpoint("https://example.invalid"), "https://example.invalid/v1/chat/completions")
        self.assertEqual(upstream_endpoint("https://example.invalid/v1/chat/completions"), "https://example.invalid/v1/chat/completions")
        self.assertEqual(upstream_endpoint("https://example.invalid/beta"), "https://example.invalid/beta/chat/completions")


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="infiplot-relay-fixture-")
        self.sent = []
        self.provider_mode = "normal"
        self.provider_started = Event()
        self.provider_release = Event()
        sent = self.sent
        fixture = self

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                sent.append(body)
                if fixture.provider_mode == "stall_headers":
                    fixture.provider_started.set()
                    fixture.provider_release.wait(10)
                    self.close_connection = True
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
                self.send_header("x-request-id", "provider-fixture-id")
                if fixture.provider_mode == "stall_body":
                    self.send_header("Content-Length", "1024")
                    self.end_headers()
                    self.wfile.write(b'{"partial":')
                    self.wfile.flush()
                    fixture.provider_started.set()
                    fixture.provider_release.wait(10)
                    self.close_connection = True
                    return
                if fixture.provider_mode.startswith("truncated"):
                    partial = b'{"id":"partial fixture-private-key'
                    if fixture.provider_mode == "truncated_utf8":
                        partial += b"\xe4\xb8"
                    if fixture.provider_mode == "truncated_length":
                        self.send_header("Content-Length", str(len(partial) + 12))
                        self.end_headers()
                        self.wfile.write(partial)
                    else:
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        self.wfile.write(b"7\r\n" + partial[:7] + b"\r\n")
                        self.wfile.write(format(len(partial) + 12, "x").encode() + b"\r\n" + partial[7:])
                    self.close_connection = True
                    return
                self.end_headers()
                if body.get("stream"):
                    chunks = [
                        {"model": "reported-fixture-alias", "choices": [{"delta": {"content": "text"}, "finish_reason": None}]},
                        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 3}},
                        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 3}},
                    ]
                    raw = "".join("data: " + compact(row) + "\n\n" for row in chunks) + "data: [DONE]\n\n"
                else:
                    raw = compact({"model": "reported-fixture-alias", "choices": [{"message": {"content": "text"}, "finish_reason": "stop"}]})
                self.wfile.write(raw.encode())

        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.provider_thread = Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.env = patch.dict(os.environ, {"TEXT_API_KEY": "fixture-private-key"})
        self.env.start()
        self.config = {"model": "fixture-model", "model_base_url": f"http://127.0.0.1:{self.provider.server_port}",
                       "max_calls": 2, "max_input_chars": 100000, "max_output_tokens": 77, "timeout_seconds": 3}
        self.handle = {"trace_dir": self.tmp.name, "root_run_id": "fixture-run"}
        self.relay = Relay(self.config, self.handle, SHARED)
        self.url = self.relay.start()

    def tearDown(self):
        self.provider_release.set()
        self.relay.close()
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join(5)
        self.env.stop()
        self.tmp.cleanup()

    def send(self, body):
        req = request.Request(self.url + "/v1/chat/completions", data=compact(body).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer fixture-private-key"})
        with request.urlopen(req, timeout=5) as response:
            raw = response.read()
        self.relay.wait_for_idle()
        return raw

    def events(self):
        return [json.loads(line) for file in Path(self.tmp.name).glob("calls-*.jsonl") for line in file.read_text().splitlines()]

    def test_json_sse_usage_budget_receipt_and_hashes(self):
        first = payload()
        first["max_tokens"] = 7
        self.send(first)
        self.assertIn(b"[DONE]", self.send(payload(True)))
        with self.assertRaises(error.HTTPError) as limited:
            self.send(payload())
        self.assertEqual(limited.exception.code, 400)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[0]["max_tokens"], 7)
        self.assertEqual(self.sent[1]["max_tokens"], 77)
        completed = [row for row in self.events() if row["event"] == "completed"]
        self.assertEqual(completed[0]["usage"], None)
        self.assertEqual(completed[1]["usage"]["completion_tokens"], 3)
        self.assertEqual(completed[1]["actual_model"], "reported-fixture-alias")
        self.assertEqual(completed[1]["requested_model"], "fixture-model")
        self.assertEqual(completed[1]["finish_reason"], "stop")
        self.assertEqual(completed[1]["provider_request_id"], "provider-fixture-id")
        self.assertEqual(completed[0]["before_request_sha256"], digest(compact(first)))
        self.assertEqual(completed[0]["after_request_sha256"], digest(compact(self.sent[0])))
        self.assertEqual(completed[0]["input_chars"], len(compact(self.sent[0])))
        self.assertNotEqual(completed[0]["input_chars"], len(compact(self.sent[0]).encode("utf-16-le")) // 2)
        self.assertNotIn("fixture-private-key", json.dumps(self.events()))
        receipts = [json.loads(file.read_text()) for file in Path(self.tmp.name).glob("received-*.json")]
        # The budget-blocked third SDK request was still actually received.
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(row["received_task"] == SHARED and row["boundary"] == "native_sdk_task_block" for row in receipts))

    def test_input_limit_is_full_http_payload_after_cap(self):
        self.config["max_input_chars"] = len(SHARED) + 1
        with self.assertRaises(error.HTTPError):
            self.send(payload())
        self.assertEqual(self.sent, [])
        self.assertTrue(any(row.get("error") == "input_budget_exceeded" for row in self.events()))
        self.assertEqual(len(list(Path(self.tmp.name).glob("requests/*.before.json"))), 1)
        self.assertEqual(len(list(Path(self.tmp.name).glob("received-*.json"))), 1)

    def test_close_interrupts_pending_provider_and_freezes_trace(self):
        for mode in ("stall_headers", "stall_body"):
            # A new relay per phase; the first one is the setUp-owned instance.
            if mode == "stall_body":
                self.provider_started.clear()
                self.provider_release.clear()
                self.relay = Relay(self.config, self.handle, SHARED)
                self.url = self.relay.start()
            self.provider_mode = mode
            self.config["timeout_seconds"] = 30
            failure = []
            def send_pending():
                try:
                    self.send(payload())
                except Exception as exc:
                    failure.append(type(exc).__name__)
            sender = Thread(target=send_pending)
            sender.start()
            self.assertTrue(self.provider_started.wait(3))
            started = time.monotonic()
            self.relay.close()
            self.assertLess(time.monotonic() - started, 5)
            sender.join(3)
            self.assertFalse(sender.is_alive())
            self.assertEqual(self.relay.active, 0)
            saved = {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob("*") if p.is_file()}
            self.provider_release.set()
            self.relay.emit({"event": "error", "error": "late callback"})
            self.assertEqual(saved, {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob("*") if p.is_file()})
            self.assertTrue(any(row.get("event") == "error" for row in self.events()))

    def test_wrong_model_does_not_contact_provider(self):
        original = payload()
        original["model"] = "different-model"
        with self.assertRaises(error.HTTPError):
            self.send(original)
        self.assertEqual(self.sent, [])

    def test_common_parameters_reach_wire_and_preserve_messages_and_original(self):
        self.config["model_parameters"] = {"thinking": {"type": "disabled"}}
        original = payload()
        original["thinking"] = {"type": "enabled"}
        snapshot = deepcopy(original)
        self.send(original)
        adapted, _, _, _ = adapt_messages(original, SHARED)
        self.assertEqual(original, snapshot)
        self.assertEqual(self.sent[0]["messages"], adapted["messages"])
        self.assertEqual(self.sent[0]["thinking"], {"type": "disabled"})
        completed = next(row for row in self.events() if row["event"] == "completed")
        self.assertEqual(completed["model_parameters"], {"thinking": {"type": "disabled"}})
        self.assertEqual(completed["model_parameter_changes"], [
            {"field": "thinking", "before": {"type": "enabled"}, "after": {"type": "disabled"}}
        ])
        self.assertEqual(completed["before_request_sha256"], digest(compact(original)))
        self.assertEqual(completed["after_prompt_adaptation_sha256"], digest(compact(adapted)))
        adapted["thinking"] = {"type": "disabled"}
        self.assertEqual(completed["after_model_parameters_sha256"], digest(compact(adapted)))
        self.assertEqual(completed["after_request_sha256"], digest(compact(self.sent[0])))
        before = json.loads(next(Path(self.tmp.name).glob("requests/*.before.json")).read_text())
        after = json.loads(next(Path(self.tmp.name).glob("requests/*.after.json")).read_text())
        self.assertEqual(before, original)
        self.assertEqual(after, self.sent[0])

    def test_empty_common_parameters_preserve_native_model_settings(self):
        self.config["model_parameters"] = {}
        original = payload()
        original["thinking"] = {"type": "enabled"}
        original["max_tokens"] = 7
        self.send(original)
        expected, _, _, _ = adapt_messages(original, SHARED)
        self.assertEqual(self.sent[0], expected)
        completed = next(row for row in self.events() if row["event"] == "completed")
        self.assertEqual(completed["model_parameter_changes"], [])
        self.assertEqual(completed["after_prompt_adaptation_sha256"], completed["after_model_parameters_sha256"])

    def test_parameters_are_counted_in_full_wire_payload_budget(self):
        original = payload()
        expected, _, _, _ = adapt_messages(original, SHARED)
        expected["max_tokens"] = self.config["max_output_tokens"]
        self.config["max_input_chars"] = len(compact(expected))
        self.config["model_parameters"] = {"thinking": {"type": "disabled"}}
        with self.assertRaises(error.HTTPError):
            self.send(original)
        self.assertEqual(self.sent, [])
        self.assertTrue(any(row.get("error") == "input_budget_exceeded" for row in self.events()))
        expected["thinking"] = {"type": "disabled"}
        self.config["max_input_chars"] = len(compact(expected))
        self.send(original)
        completed = next(row for row in self.events() if row["event"] == "completed")
        self.assertEqual(completed["input_chars"], len(compact(self.sent[0])))
        self.assertEqual(completed["input_chars"], self.config["max_input_chars"])

    def test_auxiliary_request_receives_common_parameters_without_prompt_change(self):
        self.config["model_parameters"] = {"thinking": {"type": "disabled"}}
        original = {"model": "fixture-model", "messages": [{"role": "user", "content": "角色卡请求"}]}
        self.send(original)
        self.assertEqual(self.sent[0]["messages"], original["messages"])
        self.assertEqual(self.sent[0]["thinking"], {"type": "disabled"})

    def test_incomplete_chunked_response_preserved_as_error_without_retry(self):
        self._assert_partial_response("truncated_chunked")

    def test_incomplete_content_length_response_preserved_as_error_without_retry(self):
        self._assert_partial_response("truncated_length")

    def test_incomplete_utf8_tail_keeps_binary_error_evidence(self):
        self._assert_partial_response("truncated_utf8")

    def _assert_partial_response(self, mode):
        self.provider_mode = mode
        self.send(payload())
        self.assertEqual(len(self.sent), 1)
        events = self.events()
        self.assertFalse(any(row["event"] == "completed" for row in events))
        failed = next(row for row in events if row["event"] == "error")
        self.assertEqual(failed["error"], "IncompleteRead")
        self.assertEqual(failed["delivery_status"], "delivery_unknown")
        self.assertFalse(failed["response_complete"])
        self.assertIsNone(failed["usage"])
        self.assertEqual(failed["provider_request_id"], "provider-fixture-id")
        self.assertEqual(failed["http_status"], 200)
        raw = b'{"id":"partial fixture-private-key'
        if mode == "truncated_utf8":
            raw += b"\xe4\xb8"
        stored = (Path(self.tmp.name) / failed["response_file"]).read_bytes()
        self.assertEqual(stored, raw.replace(b"fixture-private-key", b"[REDACTED]"))
        self.assertEqual(failed["partial_response_bytes_observed"], len(raw))
        self.assertEqual(failed["partial_response_sha256"], digest(raw))
        self.assertEqual(failed["partial_response_saved_sha256"], digest(stored))
        self.assertTrue(failed["partial_response_redacted"])
        if mode == "truncated_utf8":
            with self.assertRaises(UnicodeDecodeError):
                stored.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
