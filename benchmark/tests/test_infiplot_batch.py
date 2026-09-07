"""InfiPlot batch: pure contracts plus opt-in original browser/media fixture."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import struct
import tempfile
from threading import Lock, Thread
import unittest
from unittest.mock import patch
import zlib

from story_benchmark.batch_native.infiplot import preflight, run, selected_index, selection_committed, visible_fields, writer_lineage
from story_benchmark.adapters.infiplot import InfiPlotError
from native_shims.infiplot.relay import COLD_START, SHARED_START, adapt_messages


class BatchContractTests(unittest.TestCase):
    def test_isolated_identity_evidence_keeps_boolean_without_exposing_credentials(self):
        from contextlib import ExitStack
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from native_shims.infiplot.batch_runtime import LocalIdentity
        from story_benchmark.recording import Recorder

        with tempfile.TemporaryDirectory() as directory:
            with ExitStack() as cleanup:
                first = LocalIdentity()
                cleanup.callback(first.close)
                second = LocalIdentity()
                cleanup.callback(second.close)
                self.assertNotEqual(first.server.server_port, second.server.server_port)
                self.assertFalse(first.token == second.token)
                self.assertFalse(first.cookie == second.cookie)
                for own, other in ((first, second), (second, first)):
                    endpoint = f"http://127.0.0.1:{own.server.server_port}/auth/v1/user"
                    with urlopen(Request(endpoint, headers={"Authorization": "Bearer " + own.token}), timeout=5) as response:
                        self.assertEqual(response.status, 200)
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(Request(endpoint, headers={"Authorization": "Bearer " + other.token}), timeout=5)
                    self.assertEqual(rejected.exception.code, 401)
                    rejected.exception.close()

            recorder = Recorder(Path(directory) / "recording", "identity-evidence", 20)
            for number, identity in enumerate((first, second)):
                filename = f"native/auth-provenance-{number}.json"
                recorder.save_json(filename, identity.provenance())
                raw = (recorder.root / filename).read_text()
                saved = json.loads(raw)
                self.assertIs(saved["session_isolated_per_run"], True)
                self.assertIs(saved["listener_closed"], True)
                self.assertIs(saved["production_account_authentication_verified"], False)
                self.assertNotIn("cookie_isolated_per_run", saved)
                self.assertEqual([item["accepted"] for item in saved["requests"]], [True, False])
                self.assertFalse(any(secret in raw for secret in (first.token, first.cookie, second.token, second.cookie)))

    def test_lineage_uses_exact_operation_not_same_prose_or_prefetch(self):
        responses = {"r1": {"request_id": "r1", "native_operation_id": "selected-op", "data": {"scene": {"id": "selected"}}}}
        calls = [{"call_id": "a", "native_operation_id": "prefetch-op", "native_stage": "writer"},
                 {"call_id": "b", "native_operation_id": "selected-op", "native_stage": "writer"},
                 {"call_id": "c", "native_operation_id": "selected-op", "native_stage": "auxiliary_text"}]
        self.assertEqual(writer_lineage("selected", responses, calls)["source_call_ids"], ["b"])
        self.assertIsNone(writer_lineage("unknown", responses, calls)["source_call_ids"])
    def test_visible_fields_match_native_hidden_line_rule(self):
        self.assertEqual(list(visible_fields({"narration": "雨。", "line": "不可见"})), [("narration", "雨。", None)])
        self.assertEqual(list(visible_fields({"narration": "门响。", "speaker": "许青", "line": "进来。"})),
                         [("narration", "门响。", None), ("line", "进来。", "许青")])

    def test_strategy_repeats_last_and_never_remaps_unavailable_option(self):
        policy = {"choice_indices": [1, 0]}
        self.assertEqual(selected_index(policy, 5, [{"id": "x"}]), 0)
        with self.assertRaises(InfiPlotError):
            selected_index(policy, 0, [{"id": "x"}])

    def test_actual_choice_commit_requires_native_exit(self):
        choice = {"id": "C2", "label": "等待", "effect": {"kind": "change-scene", "nextSceneSeed": "仍在门口"}}
        pending = {"options": [choice], "index": 0, "scene_id": "one"}
        state = {"session": {"history": [{"scene": {"id": "one"}}, {"scene": {"id": "two"}}]}, "beat": {"id": "b1"}}
        self.assertFalse(selection_committed(pending, state))
        state["session"]["history"][0]["exit"] = {"kind": "choice", "choiceId": "C2", "label": "等待", "nextSceneSeed": "仍在门口"}
        self.assertTrue(selection_committed(pending, state))

    def test_advance_beat_choice_checks_target_without_scene_generation(self):
        pending = {"options": [{"effect": {"kind": "advance-beat", "targetBeatId": "b2"}}], "index": 0, "scene_id": "one"}
        self.assertTrue(selection_committed(pending, {"session": {"history": [{"scene": {"id": "one"}}]}, "beat": {"id": "b2"}}))

    def test_batch_continuation_never_reinjects_opening_and_legacy_stays_strict(self):
        shared = "<<<SHARED_TASK_V2_BEGIN>>>共同题目<<<SHARED_TASK_V2_END>>>"
        payload = {"model": "x", "messages": [{"role": "system", "content": shared},
            {"role": "user", "content": "编剧，下面是当前情境：承接「玩家在上一场选择了：等待」无缝续写下一个场景。"}]}
        with self.assertRaises(ValueError):
            adapt_messages(payload, shared)
        after, changes, receipt, stage = adapt_messages(payload, shared, allow_native_continuation=True)
        self.assertEqual(after, payload)
        self.assertEqual(changes, [])
        self.assertEqual(receipt["received_task"], shared)
        initial = json.loads(json.dumps(payload))
        initial["messages"][1]["content"] += COLD_START
        after, changes, _, _ = adapt_messages(initial, shared, allow_native_continuation=True)
        self.assertEqual(len(changes), 1)
        self.assertIn(SHARED_START, after["messages"][1]["content"])
        self.assertEqual(after["messages"][0], initial["messages"][0])


def fixture_png(number):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    width, height = 96, 64
    pixel = bytes([(number * 47) % 255, 120, 180])
    raw = b"".join(b"\0" + pixel * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@unittest.skipUnless(os.environ.get("INFIPLOT_BATCH_TEST") == "1", "opt-in native Next/browser local multimodal fixture")
class NativeBrowserFixture(unittest.TestCase):
    def test_original_page_prefetch_choice_image_edit_and_source_attestation(self):
        from story_benchmark.gateway import ModelGateway, parse_payload
        from story_benchmark.recording import Recorder, jsonl
        root = Path(__file__).resolve().parents[2]
        source = root / "systems/infiplot"
        calls, lock = [], Lock()
        class Provider(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                payload, parts = parse_payload(raw, self.headers.get("Content-Type", "application/json"))
                with lock:
                    calls.append({"path": self.path, "payload": payload, "reference_files": len([p for p in (parts or []) if p["filename"]])})
                    number = len(calls)
                if "/images/" in self.path:
                    data = {"data": [{"b64_json": base64.b64encode(fixture_png(number)).decode()}], "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}
                else:
                    is_writer = any("编剧，下面是当前情境：" in str(m.get("content")) for m in payload["messages"])
                    if is_writer:
                        text = payload["messages"][-1]["content"]
                        initial = SHARED_START in text
                        plan = {"sceneSummary": "本地固定响应房间", "entryBeatId": "b1", "cast": ["许青"], "entryActiveCharacters": [{"name": "许青"}],
                                "sceneKey": "fixture-room", "storyBible": {"logline": "fixture", "genreTags": ["fixture"], "protagonist": "你", "castNotes": []}}
                        prose = "雨仍落在屋檐，门口的钟声清晰可闻。\n\n许青站在窗边，安静地看向门口。" if initial else "你作出选择后，许青把窗户关好，雨声变得细碎。\n\n屋内的挂钟继续走动，灯光照着门边的椅子。你们仍在同一间屋内，刚才听见的脚步声已经渐渐远去。"
                        choices = [{"id": "c1", "label": "留在窗边", "effect": {"kind": "change-scene", "nextSceneSeed": "继续留在窗边"}},
                                   {"id": "c2", "label": "走向门口", "effect": {"kind": "change-scene", "nextSceneSeed": "走向门口等待"}}]
                        content = "<plan>" + json.dumps(plan, ensure_ascii=False) + "</plan><story>" + prose + "</story><choices>" + json.dumps(choices, ensure_ascii=False) + "</choices>"
                    else:
                        content = json.dumps({"visualDescription": "黑色短发，蓝色外套", "voiceDescription": "平静", "shotType": "wide", "integratedPrompt": "A room with Xu Qing, blue jacket."}, ensure_ascii=False)
                    if payload.get("stream"):
                        chunks = [{"id": f"fixture-{number}", "model": payload["model"], "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]},
                                  {"id": f"fixture-{number}", "model": payload["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}]
                        raw = ("".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
                        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
                        return
                    data = {"id": f"fixture-{number}", "model": payload["model"], "choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        folder = Path(os.environ["INFIPLOT_BATCH_EVIDENCE"]) if os.environ.get("INFIPLOT_BATCH_EVIDENCE") else Path(tempfile.mkdtemp(prefix="infiplot-batch-fixture-")) / "evidence"
        folder.mkdir(parents=True, exist_ok=False)
        bundle = folder / "bundle"; (bundle / "payloads").mkdir(parents=True)
        shared = "<<<SHARED_TASK_V2_BEGIN>>>\n共同题目：你和许青已站在屋内，窗外仍在下雨。按原生方式继续图文故事。\n<<<SHARED_TASK_V2_END>>>"
        (bundle / "shared_task.txt").write_text(shared)
        (bundle / "opening.txt").write_text("你和许青已站在屋内，窗外仍在下雨。")
        (bundle / "payloads/infiplot.json").write_text(json.dumps({"worldSetting": shared, "styleGuide": "清晰的插画风格", "playerName": "你", "language": "zh-CN"}, ensure_ascii=False))
        cfg = {"repo_path": str(source), "source_lock": str(root / "baseline-lock.json"), "budget_mode": "unlimited", "max_calls": None,
               "max_output_tokens": None, "max_input_chars": None, "timeout_seconds": None, "model": "fixture-text", "image_model": "gpt-image-2", "vision_model": "gpt-5.4-mini"}
        if os.environ.get("INFIPLOT_PLAYWRIGHT_MODULE"):
            cfg["playwright_module"] = os.environ["INFIPLOT_PLAYWRIGHT_MODULE"]
        if os.environ.get("INFIPLOT_ROUTE_COMPATIBILITY"):
            cfg["route_compatibility"] = os.environ["INFIPLOT_ROUTE_COMPATIBILITY"]
        rec = Recorder(folder / "run", "local-infiplot-batch", 70)
        providers = {role: {"model": model, "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key_env": "INFIPLOT_LOCAL_FIXTURE_KEY"}
                     for role, model in (("text", "fixture-text"), ("image", "gpt-image-2"), ("vision", "gpt-5.4-mini"))}
        try:
            with patch.dict(os.environ, {"INFIPLOT_LOCAL_FIXTURE_KEY": "fixture-only"}):
                gateway = ModelGateway(rec, providers).start()
                try:
                    result = run(cfg, bundle, rec.root, rec, gateway, {"window_chars": 70, "choice_indices": [1], "reading_delay_seconds": 0,
                        "render_mode": "offscreen_native", "evidence_kind": "fixture"})
                finally:
                    gateway.close()
            (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            self.assertEqual(result["stop_reason"], "reading_window", result)
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["choices_executed"], 1)
            self.assertTrue(any(c["path"].endswith("/images/generations") for c in calls))
            self.assertTrue(any(c["path"].endswith("/images/edits") and c["reference_files"] for c in calls))
            self.assertGreaterEqual(len([c for c in calls if c["payload"].get("stream")]), 3, "native prefetch should retain both initial branches")
            story = jsonl(rec.root / "trajectories/main/story.jsonl")
            self.assertGreaterEqual(sum(len(s["text"]) for s in story), 70)
            self.assertTrue(all((rec.root / s["native_source"]["file"]).exists() for s in story))
            self.assertTrue(all(s["source_call_ids"] for s in story), "real native HTTP operation IDs must correlate Writer calls")
            calls_by_id = {c["call_id"]: c for c in jsonl(rec.root / "telemetry/calls.jsonl")}
            self.assertTrue(all(calls_by_id[cid]["native_stage"] == "writer" for s in story for cid in s["source_call_ids"]))
            self.assertTrue(all(f["clean_file"] and f["ui_file"] for f in jsonl(rec.root / "visuals/frame_map.jsonl")))
            self.assertTrue(jsonl(rec.root / "characters/versions.jsonl"))
            attestation = json.loads((rec.root / "native/source-attestation.json").read_text())
            self.assertTrue(attestation["upstream_source_unchanged"])
            self.assertTrue(attestation["runtime_source_unchanged_except_native_generated_files"])
            self.assertEqual(attestation["source_files"], 927)
            self.assertFalse((rec.root / "native/runtime").exists())
            auth = json.loads((rec.root / "native/auth-provenance.json").read_text())
            self.assertIs(auth["production_account_authentication_verified"], False)
            self.assertIs(auth["session_isolated_per_run"], True)
            self.assertIs(auth["listener_closed"], True)
        finally:
            server.shutdown(); server.server_close(); thread.join(5)


if __name__ == "__main__":
    unittest.main()
