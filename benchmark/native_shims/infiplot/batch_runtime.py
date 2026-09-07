"""Isolated full-media deployment and a disclosed local identity provider."""
from __future__ import annotations

import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from threading import Thread
from urllib import error, request

from story_benchmark.adapters.infiplot import InfiPlotAdapter, InfiPlotError, _save
from .runtime import copy_source, source_hashes, OBSERVATIONS
from .relay import Relay


def observe_batch_console(process, handle, environment):
    from story_benchmark.recording import redact_evidence
    secret_values = [value for name, value in environment.items() if value and
                     any(part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))]
    def consume():
        with (Path(handle["native_dir"]) / "console.jsonl").open("a", encoding="utf-8") as output:
            for line in process.stdout:
                safe = line.rstrip("\r\n")
                for value in sorted(secret_values, key=len, reverse=True):
                    safe = safe.replace(value, "[REDACTED]")
                output.write(json.dumps({"line": redact_evidence(safe)}, ensure_ascii=False) + "\n")
                output.flush()
                for signature, code in OBSERVATIONS.items():
                    if line.startswith(signature):
                        with (Path(handle["trace_dir"]) / "native-observations.jsonl").open("a") as file:
                            file.write(json.dumps({"root_run_id": handle["root_run_id"], "system": "infiplot", "boundary": "native_console_signature",
                                "generation_issue_code": code, "signature": signature}) + "\n")
    thread = Thread(target=consume, daemon=True)
    thread.start()
    return thread


class LocalIdentity:
    """Keep requireUser/getClaims unchanged; this is not production auth proof."""
    def __init__(self):
        self.requests = []
        self.user = {"id": "00000000-0000-0000-0000-000000000001", "aud": "authenticated", "role": "authenticated"}
        enc = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        expires = int(time.time()) + 7 * 86400
        signed = enc(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + enc(json.dumps({"sub": self.user["id"], "exp": expires, "role": "authenticated"}).encode())
        self.token = signed + "." + enc(hmac.new(secrets.token_bytes(32), signed.encode(), hashlib.sha256).digest())
        self.cookie = "sb-127-auth-token=base64-" + enc(json.dumps({"access_token": self.token, "refresh_token": secrets.token_urlsafe(32), "expires_at": expires, "token_type": "bearer", "user": self.user}).encode())
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                ok = self.path == "/auth/v1/user" and self.headers.get("Authorization") == "Bearer " + owner.token
                owner.requests.append({"path": self.path, "accepted": ok})
                raw = json.dumps(owner.user if ok else {"error": "invalid_token"}).encode()
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def env(self):
        return {"NEXT_PUBLIC_SUPABASE_URL": f"http://127.0.0.1:{self.server.server_port}",
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "local-fixture-publishable-key", "INFIPLOT_COOKIE": self.cookie}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def provenance(self):
        return {"mode": "local_identity_fixture", "production_account_authentication_verified": False,
                "native_auth_source_unchanged": True, "cookie_isolated_per_run": True,
                "requests": self.requests, "listener_port": self.server.server_port,
                "listener_closed": not self.thread.is_alive()}


class NativeBatchRuntime(InfiPlotAdapter):
    def __init__(self, config, gateway, identity):
        super().__init__(config)
        self.gateway, self.identity = gateway, identity

    def prepare(self, bundle_dir, run_dir):
        handle = super().prepare(bundle_dir, run_dir)
        # This prepared legacy-compatible payload is not a route observation.
        # The real original-browser request is captured separately on the wire.
        native = Path(handle["native_dir"])
        (native / "start-request.json").rename(native / "prepared-start-payload.json")
        return handle

    def _headers(self):
        return {"Content-Type": "application/json", "Accept": "application/json", "Cookie": self.identity.cookie}

    def _launch(self, handle):
        from urllib.parse import urlsplit
        port = urlsplit(handle["base_url"]).port
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise InfiPlotError("port_in_use", "Refusing to reuse another run's native listener")
        source = Path(self.config["repo_path"]).resolve()
        hashes = json.loads((Path(handle["native_dir"]) / "source-before.json").read_text())
        if source_hashes(source) != hashes:
            raise InfiPlotError("source_drift", "Pristine source changed after preflight")
        runtime = Path(handle["runtime_dir"])
        copy_source(source, runtime, hashes)
        _save(Path(handle["native_dir"]) / "runtime-source-before.json", source_hashes(runtime))
        install = subprocess.run([self.config.get("pnpm_executable") or "pnpm", "install", "--frozen-lockfile", "--offline"],
                                 cwd=runtime, capture_output=True, timeout=float(self.config.get("dependency_timeout_seconds", 180)))
        if install.returncode:
            raise InfiPlotError("dependency_install_failed", "Frozen offline pnpm install failed; populate its cache before a live run")
        shared = (Path(handle["bundle_dir"]) / "shared_task.txt").read_text()
        relay_config = {**self.config, "model_base_url": self.gateway.url("text"), "model_parameters": {},
                        "batch_native_continuation": True}
        # Relay retains only this local gateway token; never the real provider key.
        prior = os.environ.get("TEXT_API_KEY")
        os.environ["TEXT_API_KEY"] = self.gateway.api_key
        try:
            self.relay = Relay(relay_config, handle, shared)
        finally:
            if prior is None:
                os.environ.pop("TEXT_API_KEY", None)
            else:
                os.environ["TEXT_API_KEY"] = prior
        text_url = self.relay.start()
        env = {**os.environ, **self.identity.env}
        env.update({"MOCK_IMAGE": "false", "TTS_BASE_URL": "", "TTS_API_KEY": "", "TTS_SPEECH_MODEL": "",
                    "TEXT_BASE_URL": text_url, "TEXT_API_KEY": self.gateway.api_key,
                    "TEXT_MODEL": self.config["model"], "TEXT_PROVIDER": "openai_compatible",
                    "IMAGE_BASE_URL": self.gateway.url("image"), "IMAGE_API_KEY": self.gateway.api_key,
                    "IMAGE_MODEL": self.config.get("image_model", "gpt-image-2"), "IMAGE_PROVIDER": "openai",
                    "VISION_BASE_URL": self.gateway.url("vision"), "VISION_API_KEY": self.gateway.api_key,
                    "VISION_MODEL": self.config.get("vision_model", "gpt-5.4-mini"), "VISION_PROVIDER": "openai_compatible"})
        # Do not inherit unrelated tuning from the operator's shell. Explicit
        # native settings remain optional and visible; unlimited adds no cap.
        for name, key in (("IMAGE_TIMEOUT_MS", "native_image_timeout_ms"), ("IMAGE_HEDGE_MS", "native_image_hedge_ms")):
            env.pop(name, None)
            if self.config.get(key) is not None:
                env[name] = str(self.config[key])
        if "IMAGE_TIMEOUT_MS" not in env:
            # Frozen native code passes timeout: undefined explicitly; SDK 6.42
            # rejects that instead of choosing its own default. Use the SDK's
            # actual built-in default via the native public env configuration.
            raw_default = subprocess.check_output([self.config.get("node_executable") or "node", "-e",
                "process.stdout.write(String(require('openai').default.DEFAULT_TIMEOUT))"], cwd=runtime, text=True, timeout=10)
            if not raw_default.isdigit() or int(raw_default) <= 0:
                raise InfiPlotError("native_sdk_default_timeout_unknown", "Cannot read the frozen OpenAI SDK's own image timeout")
            env["IMAGE_TIMEOUT_MS"] = raw_default
            _save(Path(handle["native_dir"]) / "native-image-settings.json", {"IMAGE_TIMEOUT_MS": int(raw_default),
                "origin": "frozen_openai_sdk_DEFAULT_TIMEOUT", "generation_run_deadline": None,
                "native_dependency_source": "node_modules/openai/client.js: OpenAI.DEFAULT_TIMEOUT = 600000; // 10 minutes",
                "reason": "Native explicit undefined request timeout is rejected; configure the native SDK default through its existing environment setting"})
        else:
            _save(Path(handle["native_dir"]) / "native-image-settings.json", {"IMAGE_TIMEOUT_MS": int(env["IMAGE_TIMEOUT_MS"]),
                "origin": "explicit_native_configuration", "generation_run_deadline": None})
        command = [self.config.get("pnpm_executable") or "pnpm", "dev", "--hostname", "127.0.0.1", "--port", str(port)]
        if (self.config.get("route_compatibility") or "native_render_entry") == "native_render_entry":
            command = [self.config.get("node_executable") or "node", str(Path(__file__).with_name("compatibility_server.cjs")),
                       str(runtime), str(port), str(Path(handle["native_dir"]) / "route-compatibility.json")]
        self.process = subprocess.Popen(command,
                                        cwd=runtime, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.console_thread = observe_batch_console(self.process, handle, env)
        deadline = time.monotonic() + float(self.config.get("startup_timeout_seconds", 120))
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise InfiPlotError("server_exited", "Native Next server exited during startup")
            try:
                req = request.Request(handle["base_url"] + "/api/tts-provider", headers=self._headers())
                with request.urlopen(req, timeout=5) as res:
                    data = json.load(res)
                if data.get("provider") is not None:
                    raise InfiPlotError("tts_not_disabled", "Text/image profile unexpectedly enabled server TTS")
                return
            except error.HTTPError as exc:
                raise InfiPlotError("auth_failed" if exc.code in (401, 403) else "server_config_error", f"Native readiness returned HTTP {exc.code}") from None
            except (error.URLError, TimeoutError, ConnectionError):
                time.sleep(.25)
        raise InfiPlotError("startup_timeout", "Isolated Next startup deadline reached before generation")
