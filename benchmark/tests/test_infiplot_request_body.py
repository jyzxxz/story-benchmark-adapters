"""Capacity checks, including opt-in real Next/auth/API requests with no model calls.

INFIPLOT_BODY_TEST=1 python -m unittest discover -s tests -p test_infiplot_request_body.py -v
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Thread
import unittest
from urllib import error, request

from native_shims.infiplot.batch_runtime import LocalIdentity, NativeBatchRuntime
from native_shims.infiplot.request_body_settings import request_body_limit, verify_request_body_config
from story_benchmark.adapters.infiplot import InfiPlotError


class RequestCapacityContractTests(unittest.TestCase):
    def test_default_override_and_explicit_native_default(self):
        self.assertEqual(request_body_limit({}), 64 * 1024 * 1024)
        self.assertIsNone(request_body_limit({"native_request_body_limit_bytes": None}))
        self.assertEqual(request_body_limit({"native_request_body_limit_bytes": 32 * 1024 * 1024}), 32 * 1024 * 1024)

    def test_invalid_capacity_fails_before_starting(self):
        for value in (0, -1, True, "64mb", 1.5, 2**53):
            with self.subTest(value=value), self.assertRaises(InfiPlotError):
                request_body_limit({"native_request_body_limit_bytes": value})

    def test_startup_rejects_only_probe_or_mismatched_effective_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder) / "config.json"
            row = {"before_bytes": 10, "effective_bytes": 64}
            for loads in ([], [row], [row, {"before_bytes": 10, "effective_bytes": 10}]):
                evidence.write_text(json.dumps({"requested_bytes": 64, "loads": loads}))
                with self.assertRaises(InfiPlotError):
                    verify_request_body_config(evidence, 64)
            evidence.write_text(json.dumps({"requested_bytes": 64, "loads": [row, row]}))
            verify_request_body_config(evidence, 64)


@unittest.skipUnless(os.environ.get("INFIPLOT_BODY_TEST") == "1", "opt-in native Next capacity fixture")
class NativeRequestCapacityTests(unittest.TestCase):
    def test_same_large_json_passes_parsing_only_with_runtime_override(self):
        root = Path(__file__).resolve().parents[2]
        folder = (Path(os.environ["INFIPLOT_BODY_EVIDENCE"]) if os.environ.get("INFIPLOT_BODY_EVIDENCE")
                  else Path(tempfile.mkdtemp(prefix="infiplot-body-fixture-")) / "evidence")
        folder.mkdir(parents=True, exist_ok=False)
        provider_calls = []

        class RejectProvider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                provider_calls.append(self.path)
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

        provider = ThreadingHTTPServer(("127.0.0.1", 0), RejectProvider)
        thread = Thread(target=provider.serve_forever, daemon=True)
        thread.start()

        class Gateway:
            api_key = "local-capacity-fixture-only"

            def url(self, role):
                return f"http://127.0.0.1:{provider.server_port}/v1"

        # Intentionally omit a valid Session. "session is required" proves
        # that original auth and complete req.json() parsing succeeded, then
        # the original route stopped before invoking its engine or any model.
        payload = json.dumps({"padding": "A" * (12 * 1024 * 1024), "session": None}, separators=(",", ":")).encode()
        rows = []
        try:
            for name, limit, expected in (("native-default", None, "Invalid JSON"),
                                          ("external-64mib", 64 * 1024 * 1024, "session is required")):
                with socket.socket() as free:
                    free.bind(("127.0.0.1", 0))
                    port = free.getsockname()[1]
                cfg = {"repo_path": str(root / "systems/infiplot"), "source_lock": str(root / "baseline-lock.json"),
                       "live": False, "base_url": f"http://127.0.0.1:{port}", "model": "local-no-model",
                       "budget_mode": "unlimited", "max_calls": None, "max_output_tokens": None,
                       "max_input_chars": None, "timeout_seconds": None,
                       "native_request_body_limit_bytes": limit}
                identity = LocalIdentity()
                adapter = NativeBatchRuntime(cfg, Gateway(), identity)
                handle = None
                try:
                    handle = adapter.prepare(root / "benchmark/examples/CAMPUS-01-V4", folder / name)
                    adapter._launch(handle)
                    response_rows = []
                    for label, raw, headers in (
                        ("unauthenticated", b"{}", {"Content-Type": "application/json"}),
                        ("small-control", b'{"session":null}', adapter._headers()),
                        ("large-json", payload, adapter._headers()),
                    ):
                        req = request.Request(cfg["base_url"] + "/api/scene", data=raw, headers=headers, method="POST")
                        try:
                            with request.urlopen(req, timeout=60) as response:
                                status, body = response.status, json.load(response)
                        except error.HTTPError as response:
                            status, body = response.code, json.load(response)
                        response_rows.append({"case": label, "sent_bytes": len(raw),
                                              "sent_sha256": hashlib.sha256(raw).hexdigest(),
                                              "http_status": status, "response": body})
                    (folder / name / "http-results.json").write_text(json.dumps(response_rows, indent=2))
                    self.assertEqual(response_rows[0]["http_status"], 401)
                    self.assertEqual(response_rows[1]["response"], {"error": "session is required"})
                    self.assertEqual(response_rows[2]["http_status"], 400)
                    self.assertEqual(response_rows[2]["response"], {"error": expected})
                    self.assertEqual(provider_calls, [])
                    rows.append({"mode": name, "requested_bytes": limit, "responses": response_rows})
                finally:
                    if handle is not None:
                        adapter.close(handle)
                    identity.close()
                attestation = json.loads((folder / name / "native/source-attestation.json").read_text())
                self.assertTrue(attestation["upstream_source_unchanged"])
                self.assertTrue(attestation["runtime_source_unchanged_except_native_generated_files"])
                self.assertEqual(attestation["runtime_unexpected_source_changes"], [])
                rows[-1]["source_attestation"] = attestation
                with socket.socket() as closed:
                    self.assertNotEqual(closed.connect_ex(("127.0.0.1", port)), 0)
            (folder / "summary.json").write_text(json.dumps({"scope": "native_next_authenticated_scene_route_capacity_only",
                "synthetic_request": True, "story_generation_tested": False, "provider_calls": len(provider_calls),
                "same_large_request_sha256": hashlib.sha256(payload).hexdigest(), "results": rows}, indent=2))
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
