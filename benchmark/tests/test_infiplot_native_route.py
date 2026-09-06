"""Opt-in real native route with LOCAL signed-auth and model response fixtures.

INFIPLOT_ROUTE_TEST=1 python -m unittest discover -s tests -p test_infiplot_native_route.py -v
This verifies routing/adapter mechanics; it is not a real-model story result.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import shutil
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import patch
from urllib import error, request

from story_benchmark.adapters.infiplot import BASE_COMMIT, InfiPlotAdapter


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@unittest.skipUnless(os.environ.get("INFIPLOT_ROUTE_TEST") == "1", "opt-in local native route fixture")
class NativeRouteTests(unittest.TestCase):
    def test_authenticated_json_start_trace_native_export_and_cleanup(self):
        root = Path(__file__).resolve().parents[2]
        repo = Path(os.environ.get("INFIPLOT_REPO", str(root.parent / "pristine/infiplot")))
        commit = BASE_COMMIT
        secret = b"LOCAL-FIXTURE-SIGNING-KEY"
        user = {"id": "00000000-0000-0000-0000-000000000001", "aud": "authenticated", "role": "authenticated"}
        expires = int(time.time()) + 3600
        signed = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps({"sub": user["id"], "exp": expires, "role": "authenticated"}).encode())
        token = signed + "." + b64(hmac.new(secret, signed.encode(), hashlib.sha256).digest())
        cookie = "sb-127-auth-token=base64-" + b64(json.dumps({"access_token": token, "refresh_token": "fixture-refresh-token", "expires_at": expires, "token_type": "bearer", "user": user}).encode())
        auth_calls, model_calls, media_calls = [], [], []

        class Auth(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                auth_calls.append(self.path)
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                status = 200 if supplied == token and self.path == "/auth/v1/user" else 401
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(user if status == 200 else {"error": "invalid_token"}).encode())

        plan = {"sceneSummary": "本地固定响应测试", "entryBeatId": "b1", "cast": [], "entryActiveCharacters": [],
                "sceneKey": "fixture-room", "storyBible": {"logline": "fixture", "genreTags": ["fixture"], "protagonist": "你", "castNotes": []}}
        prose = "你仍站在门前，窗外的雨没有停。\n\n你把手从门把上移开，等待下一步行动。"
        choices = [{"id": "c1", "label": "继续等待", "effect": {"kind": "change-scene", "nextSceneSeed": "等待"}},
                   {"id": "c2", "label": "离开", "effect": {"kind": "change-scene", "nextSceneSeed": "离开"}}]
        writer = "<plan>" + json.dumps(plan, ensure_ascii=False) + "</plan><story>" + prose + "</story><choices>" + json.dumps(choices, ensure_ascii=False) + "</choices>"

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != "/v1/chat/completions":
                    media_calls.append(self.path)
                    self.send_response(500)
                    self.end_headers()
                    return
                model_calls.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
                self.send_header("x-request-id", "local-fixture-" + str(len(model_calls)))
                self.end_headers()
                if body.get("stream"):
                    records = [
                        {"id": "local-fixture", "model": body["model"], "choices": [{"index": 0, "delta": {"content": writer}, "finish_reason": None}]},
                        {"id": "local-fixture", "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                    ]
                    raw = "".join("data: " + json.dumps(row, ensure_ascii=False) + "\n\n" for row in records) + "data: [DONE]\n\n"
                else:
                    raw = json.dumps({"id": "local-fixture", "model": body["model"], "choices": [{"message": {"content": json.dumps({"shotType": "wide", "integratedPrompt": "fixture room"})}, "finish_reason": "stop"}]})
                self.wfile.write(raw.encode())

        auth_server, auth_thread = start_server(Auth)
        model_server, model_thread = start_server(Model)
        adapter, handle = None, None
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        try:
            with tempfile.TemporaryDirectory(prefix="infiplot-route-fixture-") as tmp:
                folder = Path(tmp)
                bundle = folder / "bundle"
                if os.environ.get("BENCH_SHARED_BUNDLE"):
                    bundle = Path(os.environ["BENCH_SHARED_BUNDLE"]).resolve()
                    shared = (bundle / "shared_task.txt").read_text(encoding="utf-8")
                else:
                    (bundle / "payloads").mkdir(parents=True)
                    shared = "<<<SHARED_TASK_V2_BEGIN>>>\n【共同任务】\n这是本地入口测试固定题目。\n【固定开头】\n你已站在门前，雨没有停。\n<<<SHARED_TASK_V2_END>>>"
                    (bundle / "shared_task.txt").write_text(shared)
                    (bundle / "opening.txt").write_text("你已站在门前，雨没有停。")
                    (bundle / "payloads/infiplot.json").write_text(json.dumps({"worldSetting": shared, "styleGuide": "fixture", "playerName": "你", "language": "zh-CN"}, ensure_ascii=False))
                env = {"NEXT_PUBLIC_SUPABASE_URL": f"http://127.0.0.1:{auth_server.server_port}",
                       "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "local-fixture-publishable-key", "TEXT_API_KEY": "local-fixture-text-key", "INFIPLOT_COOKIE": cookie}
                config = {"repo_path": str(repo), "expected_commit": commit, "base_url": f"http://127.0.0.1:{port}",
                          "model_base_url": f"http://127.0.0.1:{model_server.server_port}", "model": "local-fixture-model",
                          "live": True, "max_calls": 3, "max_output_tokens": 1000, "max_input_chars": 100000,
                          "timeout_seconds": 120, "root_run_id": "local-fixture-only"}
                if os.environ.get("INFIPLOT_SOURCE_LOCK"):
                    config["source_lock"] = os.environ["INFIPLOT_SOURCE_LOCK"]
                with patch.dict(os.environ, env):
                    adapter = InfiPlotAdapter(config)
                    handle = adapter.prepare(bundle, folder / "run")
                    adapter._launch(handle)
                    req = request.Request(config["base_url"] + "/api/start", data=b"{}", headers={"Content-Type": "application/json"})
                    with self.assertRaises(error.HTTPError) as missing_auth:
                        request.urlopen(req, timeout=30)
                    self.assertEqual(missing_auth.exception.code, 401)
                    self.assertEqual(list(Path(handle["trace_dir"]).glob("received-*.json")), [])
                    self.assertEqual(model_calls, [])
                    with patch.object(adapter, "_launch"):
                        result = adapter.generate_first_artifact(handle)
                    response = json.loads(Path(result["response_file"]).read_text())
                    self.assertTrue(response["imageUrl"].startswith("data:image/svg+xml"))
                    self.assertEqual(media_calls, [])
                    self.assertEqual(len(model_calls), 2)
                    self.assertTrue(auth_calls)
                    self.assertTrue(all(row["model"] == config["model"] and row["max_tokens"] == 1000 for row in model_calls))
                    self.assertEqual(sum(shared in message["content"] for message in model_calls[0]["messages"]), 1)
                    self.assertIn("固定开头已经发生", model_calls[0]["messages"][1]["content"])
                    export = adapter.export_first_artifact(handle)
                    self.assertEqual([row["text"] for row in export["segments"]], prose.split("\n\n"))
                    self.assertEqual(len(export["choices"]), 2)
                    self.assertEqual(export["generation_issue_codes"], [])
                    receipt = json.loads(next(Path(handle["trace_dir"]).glob("received-*.json")).read_text())
                    self.assertEqual(receipt["received_task"], shared)
                    self.assertEqual(receipt["boundary"], "native_sdk_task_block")
                    self.assertFalse(receipt["direct_native_route_receiver_observed"])
                    events = [json.loads(line) for file in Path(handle["trace_dir"]).glob("*.jsonl") for line in file.read_text().splitlines()]
                    completed = [row for row in events if row["event"] == "completed"]
                    self.assertEqual(len(completed), 2)
                    self.assertEqual(completed[0]["boundary"], "http")
                    self.assertTrue(any(row["usage"] is None for row in completed))
                    writer_calls = [row for row in completed if row["stage"] == "writer"]
                    self.assertEqual(len(writer_calls), 1)
                    before = writer_calls[0]["native_sdk_request_messages"]
                    self.assertIn("第一幕的冷开场", before[1]["content"])
                    self.assertEqual(sum(shared in row["content"] for row in before), 1)
                    self.assertEqual(export["native_fallback_observation"], "unknown_without_exhaustive_native_hooks")
                    for path in (folder / "run").rglob("*"):
                        if path.is_file():
                            if "runtime" in path.parts:
                                continue
                            text = path.read_text()
                            self.assertNotIn(cookie, text)
                            self.assertNotIn(env["TEXT_API_KEY"], text)
                    adapter.close(handle)
                    attestation = json.loads((Path(handle["native_dir"]) / "source-attestation.json").read_text())
                    self.assertTrue(attestation["upstream_source_unchanged"])
                    self.assertTrue(attestation["runtime_source_unchanged_except_native_generated_files"])
                    self.assertFalse(Path(handle["runtime_dir"]).exists())
                    with socket.socket() as probe:
                        self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
                    if os.environ.get("BENCH_EVIDENCE_DIR"):
                        evidence = Path(os.environ["BENCH_EVIDENCE_DIR"]).resolve()
                        shutil.copytree(folder / "run", evidence)
                        shutil.copyfile(bundle / "shared_task.txt", evidence / "shared_task.txt")
                        (evidence / "export").mkdir()
                        shutil.copyfile(bundle / "opening.txt", evidence / "export/provided_prefix.txt")
                        (evidence / "export/generated.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in export["segments"]))
                        (evidence / "export/choices.json").write_text(json.dumps(export["choices"], ensure_ascii=False, indent=2))
                        (evidence / "evidence.json").write_text(json.dumps({
                            "evidence_kind": "native_route_local_auth_and_provider_fixture", "formal_experiment": False,
                            "source_snapshot": str(repo), "source_lock": config.get("source_lock"),
                            "shared_task_sha256_from_native_sdk": hashlib.sha256(receipt["received_task"].encode()).hexdigest(),
                            "direct_native_route_receiver_observed": False, "receipt_boundary": receipt["boundary"],
                            "native_calls_observed": len(completed), "usage_values_are_provider_fixtures": True,
                            "source_attestation": attestation, "fallback_observation": export["native_fallback_observation"],
                        }, ensure_ascii=False, indent=2))
        finally:
            if adapter and handle:
                adapter.close(handle)
            auth_server.shutdown()
            model_server.shutdown()
            auth_server.server_close()
            model_server.server_close()
            auth_thread.join(5)
            model_thread.join(5)


if __name__ == "__main__":
    unittest.main()
