"""Frozen InfiPlot JSON start adapter; no retries for ambiguous deliveries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import shutil
import time
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

BASE_COMMIT = "a60e18bc663caaa134d9323a2b89159b7cc9bd05"


class InfiPlotError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def export_scene(response: dict, source: str = "native/start-response.json") -> dict:
    """Follow native continue edges only; never select or flatten other branches."""
    scene = response.get("scene")
    if not isinstance(scene, dict) or not isinstance(scene.get("beats"), list):
        raise InfiPlotError("missing_scene", "Native response has no scene/beat graph")
    beats = scene["beats"]
    index: dict[str, tuple[int, dict]] = {}
    for pos, beat in enumerate(beats):
        if not isinstance(beat, dict) or not isinstance(beat.get("id"), str):
            raise InfiPlotError("invalid_beat", "Beat has no valid id")
        if beat["id"] in index:
            raise InfiPlotError("duplicate_beat_id", "Native beat ids are not unique")
        index[beat["id"]] = (pos, beat)
    current = scene.get("entryBeatId")
    segments, choices, visited, issues = [], [], [], []
    while current:
        if current in visited:
            raise InfiPlotError("beat_cycle", "Continue graph cycles before its first choice")
        if current not in index:
            raise InfiPlotError("missing_beat", "Continue edge points to a missing beat")
        pos, beat = index[current]
        visited.append(current)
        for field, kind in (("narration", "narration"), ("line", "dialogue")):
            # PlayCanvas renders line as its body only when a speaker exists.
            if field == "line" and not beat.get("speaker"):
                if beat.get(field):
                    issues.append("hidden_line_without_speaker")
                continue
            value = beat.get(field)
            if value is not None and not isinstance(value, str):
                raise InfiPlotError("invalid_prose", "Native visible text is not a string")
            if value and value.strip():
                segments.append({
                    "segment_id": f"p{len(segments) + 1:04d}", "kind": kind,
                    "speaker": beat.get("speaker") if field == "line" else None,
                    "text": value, "native_source": source,
                    "native_pointer": f"/scene/beats/{pos}/{field}",
                })
        next_step = beat.get("next")
        if not isinstance(next_step, dict):
            raise InfiPlotError("invalid_next", "Native beat lacks a next edge")
        if next_step.get("type") == "choice":
            options = next_step.get("choices")
            if not isinstance(options, list):
                raise InfiPlotError("invalid_choices", "Native choice boundary is malformed")
            for i, choice in enumerate(options):
                if not isinstance(choice, dict) or not isinstance(choice.get("label"), str):
                    raise InfiPlotError("invalid_choice", "Native choice has no label")
                choices.append({**choice, "native_source": source,
                                "native_pointer": f"/scene/beats/{pos}/next/choices/{i}"})
            if not options:
                issues.append("empty_choice_boundary")
            break
        if next_step.get("type") != "continue" or not next_step.get("nextBeatId"):
            raise InfiPlotError("invalid_next", "Unsupported or empty continue edge")
        current = next_step["nextBeatId"]
    if not visited:
        raise InfiPlotError("missing_entry", "Native scene has no entry beat")
    if not segments:
        issues.append("empty_prose")
    return {"segments": segments, "choices": choices, "generation_issue_codes": issues,
            "visited_beat_ids": visited, "source_mapping_valid": True,
            "export_scope": "entry_to_first_choice", "selection_executed": False}


class InfiPlotAdapter:
    def __init__(self, config: dict):
        self.config = dict(config)
        self.process: subprocess.Popen | None = None
        self.relay = None
        self.console_thread = None

    def preflight(self, bundle_dir: Path) -> dict:
        bundle_dir = Path(bundle_dir)
        checks, errors = [], []
        if self.config.get("shared_opening", True) is not True:
            errors.append("fixed_opening_profile_requires_shared_opening")
        try:
            shared = (bundle_dir / "shared_task.txt").read_text(encoding="utf-8")
            payload = json.loads((bundle_dir / "payloads/infiplot.json").read_text(encoding="utf-8"))
            if not shared or shared != shared.strip() or payload.get("worldSetting") != shared:
                raise ValueError("InfiPlot payload must contain the exact nonempty frozen shared task")
            if payload.get("styleGuide") == "auto" or not payload.get("styleGuide"):
                raise ValueError("An explicit common style is required; automatic style selection adds a model call")
            if any(key in payload for key in ("history", "storyState", "byo")):
                raise ValueError("Initial input may not supply artificial history, memory, or credentials")
            if not (bundle_dir / "opening.txt").is_file():
                raise ValueError("Missing fixed opening")
            checks.append("payload_exact")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        repo = Path(self.config.get("repo_path", ""))
        try:
            from ..provenance import verify_repository
            from native_shims.infiplot.relay import COLD_START, SHARED_START
            if not self.config.get("repo_path"):
                raise ValueError("repo_path is required")
            if self.config.get("expected_commit", BASE_COMMIT) != BASE_COMMIT:
                raise ValueError("Only the pristine frozen baseline is supported")
            provenance = verify_repository(repo, BASE_COMMIT, self.config)
            checks.extend(provenance.get("checks", []))
            errors.extend(provenance.get("errors", []))
            context = (repo / "lib/engine/context/index.ts").read_text()
            if COLD_START not in context or SHARED_START in context or (repo / "lib/benchmark/server.ts").exists():
                raise ValueError("The native source must be unmodified")
            checks.append("pristine_source_external_relay_only")
        except (OSError, ValueError, subprocess.CalledProcessError):
            errors.append("InfiPlot repo must be the pristine frozen baseline; adapters live outside it")
        for key in ("max_calls", "max_output_tokens", "max_input_chars"):
            value = self.config.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"Positive {key} budget is required")
        if self.config.get("live"):
            for key in ("TEXT_API_KEY", "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", self.config.get("cookie_env", "INFIPLOT_COOKIE")):
                if not os.environ.get(key):
                    errors.append(f"Missing environment variable: {key}")
            if not self.config.get("model_base_url"):
                errors.append("Explicit shared model_base_url is required")
            if not isinstance(self.config.get("model"), str) or not self.config["model"].strip():
                errors.append("Explicit shared model is required")
            if not shutil.which("pnpm"):
                errors.append("pnpm is required to install the frozen runtime dependencies")
        parsed = urlsplit(self.config.get("base_url", "http://127.0.0.1:3217"))
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.scheme != "http" or parsed.username or parsed.password:
            errors.append("Phase-one adapter requires its own isolated loopback HTTP deployment")
        return {"ok": not errors, "checks": checks, "errors": errors,
                "native_integration": "not_run", "base_commit": BASE_COMMIT}

    def prepare(self, bundle_dir: Path, run_dir: Path) -> dict:
        bundle_dir, run_dir = Path(bundle_dir).resolve(), Path(run_dir).resolve()
        result = self.preflight(bundle_dir)
        if not result["ok"]:
            raise InfiPlotError("preflight_failed", "; ".join(result["errors"]))
        native_dir = run_dir / "native"
        native_dir.mkdir(parents=True, exist_ok=True)
        payload = json.loads((bundle_dir / "payloads/infiplot.json").read_text(encoding="utf-8"))
        payload["clientTts"] = True
        _save(native_dir / "start-request.json", payload)
        trace_dir = Path(self.config.get("trace_dir") or run_dir / "trace").resolve()
        trace_dir.mkdir(parents=True, exist_ok=True)
        handle = {"bundle_dir": str(bundle_dir), "run_dir": str(run_dir), "native_dir": str(native_dir),
                  "trace_dir": str(trace_dir), "base_url": self.config.get("base_url", "http://127.0.0.1:3217"),
                  "root_run_id": self.config.get("root_run_id") or run_dir.name}
        from native_shims.infiplot.runtime import source_hashes
        hashes = source_hashes(Path(self.config["repo_path"]).resolve())
        _save(native_dir / "source-before.json", hashes)
        handle["runtime_dir"] = str(native_dir / "runtime")
        _save(native_dir / "adapter-handle.json", handle)
        return handle

    def _launch(self, handle: dict) -> None:
        parsed = urlsplit(handle["base_url"])
        port = parsed.port or 80
        with socket.socket() as probe:
            if probe.connect_ex((parsed.hostname or "127.0.0.1", port)) == 0:
                raise InfiPlotError("port_in_use", "Refusing to reuse an existing deployment")
        from native_shims.infiplot.relay import Relay
        from native_shims.infiplot.runtime import copy_source, observe_console, source_hashes
        source = Path(self.config["repo_path"]).resolve()
        hashes = json.loads((Path(handle["native_dir"]) / "source-before.json").read_text())
        if source_hashes(source) != hashes:
            raise InfiPlotError("source_drift", "Pristine source changed after preflight")
        runtime = Path(handle["runtime_dir"])
        copy_source(source, runtime, hashes)
        _save(Path(handle["native_dir"]) / "runtime-source-before.json", source_hashes(runtime))
        install = subprocess.run(["pnpm", "install", "--frozen-lockfile", "--offline"], cwd=runtime,
                                 capture_output=True, timeout=float(self.config.get("dependency_timeout_seconds", 180)))
        if install.returncode:
            raise InfiPlotError("dependency_install_failed", "Frozen offline dependency install failed; populate the pnpm cache first")
        shared = (Path(handle["bundle_dir"]) / "shared_task.txt").read_text(encoding="utf-8")
        self.relay = Relay(self.config, handle, shared)
        relay_url = self.relay.start()
        env = dict(os.environ)
        env.update({"MOCK_IMAGE": "true", "TTS_BASE_URL": "", "TTS_API_KEY": "", "TTS_SPEECH_MODEL": ""})
        env["TEXT_MODEL"] = self.config["model"]
        env["TEXT_BASE_URL"] = relay_url
        env["TEXT_PROVIDER"] = "openai_compatible"
        for role in ("IMAGE", "VISION"):
            env.update({f"{role}_BASE_URL": "http://127.0.0.1:9", f"{role}_API_KEY": "disabled-media-placeholder",
                        f"{role}_MODEL": "disabled-media-placeholder", f"{role}_PROVIDER": "openai_compatible"})
        command = ["pnpm", "dev", "--hostname", "127.0.0.1", "--port", str(port)]
        self.process = subprocess.Popen(command, cwd=runtime, env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        start_new_session=True)
        self.console_thread = observe_console(self.process, handle["trace_dir"], handle["root_run_id"])
        deadline = time.monotonic() + min(float(self.config.get("timeout_seconds", 180)), 120)
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise InfiPlotError("server_exited", "Native deployment exited during startup")
            try:
                # This authenticated native GET loads configuration but never generates text/media.
                probe = request.Request(handle["base_url"] + "/api/tts-provider", headers=self._headers())
                with request.urlopen(probe, timeout=5) as response:
                    status = json.load(response)
                if status.get("provider") is not None:
                    raise InfiPlotError("tts_not_disabled", "Native TTS configuration is active")
                return
            except error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise InfiPlotError("auth_failed", "Native authentication rejected the supplied cookie") from None
                raise InfiPlotError("server_config_error", f"Native readiness returned HTTP {exc.code}") from None
            except (error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.25)
        raise InfiPlotError("startup_timeout", "Native deployment did not become ready")

    def _headers(self) -> dict:
        cookie = os.environ.get(self.config.get("cookie_env", "INFIPLOT_COOKIE"), "")
        if not cookie:
            raise InfiPlotError("missing_auth", "Native authentication cookie is required")
        return {"Content-Type": "application/json", "Accept": "application/json", "Cookie": cookie}

    def generate_first_artifact(self, handle: dict) -> dict:
        native_dir = Path(handle["native_dir"])
        response_file = native_dir / "start-response.json"
        if response_file.exists():
            try:
                saved = json.loads(response_file.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or not saved.get("sessionId"):
                    raise ValueError("missing session")
                export_scene(saved)
            except (ValueError, OSError):
                raise InfiPlotError("invalid_response", "Saved response is invalid; refusing automatic resend") from None
            return {"response_file": str(response_file), "resumed_from_saved_response": True}
        if (native_dir / "delivery-started.json").exists():
            raise InfiPlotError("delivery_unknown", "A start was already attempted; refusing automatic resend")
        if not self.config.get("live"):
            raise InfiPlotError("live_disabled", "Live generation is disabled")
        self._launch(handle)
        body = (native_dir / "start-request.json").read_bytes()
        _save(native_dir / "delivery-started.json", {"time": time.time(), "operation": "POST /api/start"})
        req = request.Request(handle["base_url"] + "/api/start", data=body, headers=self._headers(), method="POST")
        try:
            with request.urlopen(req, timeout=float(self.config.get("timeout_seconds", 180))) as response:
                raw = response.read()
            if self.relay:
                self.relay.wait_for_idle()
            # Preserve exact native bytes before decoding, including malformed/empty JSON.
            with response_file.open("xb") as stream:
                stream.write(raw)
            result = json.loads(raw)
            if not isinstance(result, dict) or not result.get("sessionId"):
                raise InfiPlotError("invalid_response", "Native response lacks a session id")
            export_scene(result)
            return {"response_file": str(response_file), "native_project_id": result["sessionId"]}
        except error.HTTPError as exc:
            # Do not log headers, cookies, or provider exception bodies.
            _save(native_dir / "http-error.json", {"http_status": exc.code})
            code = "auth_failed" if exc.code in (401, 403) else "native_http_error"
            raise InfiPlotError(code, f"Native start returned HTTP {exc.code}; not retried") from None
        except (error.URLError, TimeoutError, ConnectionError, OSError):
            raise InfiPlotError("delivery_unknown", "Native start response was lost or timed out; not retried") from None
        except (ValueError, UnicodeDecodeError):
            raise InfiPlotError("invalid_response", "Saved native response is not valid JSON") from None

    def export_first_artifact(self, handle: dict) -> dict:
        native_dir = Path(handle["native_dir"])
        response = json.loads((native_dir / "start-response.json").read_text(encoding="utf-8"))
        result = export_scene(response)
        trace_files = list(Path(handle["trace_dir"]).glob("calls-*.jsonl"))
        trace_files += list(Path(handle["trace_dir"]).glob("native-observations.jsonl"))
        for path in trace_files:
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("generation_issue_code"):
                    result["generation_issue_codes"].append(event["generation_issue_code"])
        result["generation_issue_codes"] = sorted(set(result["generation_issue_codes"]))
        fallback_observed = any(code.startswith("writer_fallback") or code == "writer_stream_degraded" for code in result["generation_issue_codes"])
        result["native_fallback_observation"] = "positive_native_console_signature" if fallback_observed else "unknown_without_exhaustive_native_hooks"
        result["generation_status"] = "native_fallback_observed" if fallback_observed else "generated_unreviewed_fallback_unknown"
        result["direct_native_route_receiver_observed"] = False
        result["task_receiver_observation_boundary"] = "native_sdk_task_block"
        from native_shims.infiplot.choice_observation import observe_choices
        choice_observation = observe_choices(Path(handle["trace_dir"]), result["choices"])
        result["native_choice_normalization_observation"] = choice_observation["status"]
        if not (native_dir / "choice-provenance.json").exists():
            _save(native_dir / "choice-provenance.json", choice_observation)
        if not (native_dir / "fallback-observation.json").exists():
            _save(native_dir / "fallback-observation.json", {key: result[key] for key in ("native_fallback_observation", "generation_status", "direct_native_route_receiver_observed", "task_receiver_observation_boundary")})
        return result

    def close(self, handle: dict) -> None:
        if self.process is not None and self.process.poll() is None:
            import signal
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self.console_thread:
            self.console_thread.join(10)
        if self.process is not None and self.process.stdout is not None:
            self.process.stdout.close()
        if self.relay:
            self.relay.close()
        from native_shims.infiplot.runtime import GENERATED_FILES, source_hashes
        native_dir = Path(handle["native_dir"])
        before_file = native_dir / "source-before.json"
        if before_file.exists() and not (native_dir / "source-attestation.json").exists():
            before = json.loads(before_file.read_text())
            after = source_hashes(Path(self.config["repo_path"]).resolve())
            runtime = Path(handle["runtime_dir"])
            runtime_after = source_hashes(runtime) if runtime.exists() else {}
            changed = [name for name in set(before) | set(runtime_after) if before.get(name) != runtime_after.get(name)] if runtime.exists() else []
            non_generated = sorted(set(changed) - GENERATED_FILES)
            _save(native_dir / "source-after.json", after)
            _save(native_dir / "runtime-source-after.json", runtime_after)
            _save(native_dir / "source-attestation.json", {"upstream_source_unchanged": before == after,
                  "runtime_source_unchanged_except_native_generated_files": not non_generated,
                  "runtime_generated_file_changes": sorted(set(changed) & GENERATED_FILES),
                  "runtime_unexpected_source_changes": non_generated, "source_files": len(before)})
            if not self.config.get("keep_runtime") and runtime.exists() and not non_generated:
                shutil.rmtree(runtime)
            if before != after or non_generated:
                raise InfiPlotError("source_drift", "Source hash attestation detected unexpected changes")
