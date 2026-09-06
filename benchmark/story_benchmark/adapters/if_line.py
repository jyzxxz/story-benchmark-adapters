"""IF Line frozen v2 authoring client. No model SDK or story rewriting here."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

BASE_COMMIT = "572407fce9b648a4206ac37da6a9f6ed22631da8"


class IFLineError(RuntimeError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def export_script_ir(revision, chapter=None, native_source="native/script_revision.json"):
    """v3 paragraphs are the native linear display order; it has no choices."""
    script = revision.get("script_json", {})
    if script.get("schema_version") != "script-ir-v3":
        raise IFLineError("unsupported_script_schema")
    if chapter is not None and script.get("chapter_revision_id") != chapter.get("id"):
        raise IFLineError("artifact_source_mismatch")
    rows = script.get("paragraphs")
    if not isinstance(rows, list) or not rows:
        return {"segments": [], "choices": [], "generation_issue_codes": ["empty_prose"]}
    segments = []
    expected = chapter.get("content") if chapter else None
    last_end = 0
    for i, row in enumerate(rows):
        if row.get("order_index") != i or not isinstance(row.get("text"), str):
            raise IFLineError("invalid_script_order")
        if expected is not None:
            start, end = row.get("source_start"), row.get("source_end")
            if not isinstance(start, int) or not isinstance(end, int) or not (last_end <= start < end <= len(expected)):
                raise IFLineError("invalid_source_span")
            source = expected[start:end]
            if row.get("source_text") != source or "".join(source.split()) != row["text"]:
                raise IFLineError("source_mapping_mismatch")
            if expected[last_end:start].strip():
                raise IFLineError("source_coverage_gap")
            last_end = end
        kind = row.get("kind", "narration")
        if kind not in {"narration", "dialogue", "monologue", "action"}:
            raise IFLineError("unsupported_script_kind")
        segments.append({"segment_id": row.get("paragraph_id") or f"p{i+1:04}",
            "kind": kind, "speaker": (row.get("speaker_display_name") or row.get("speaker_name"))
            if kind in {"dialogue", "monologue"} else None,
            "text": row["text"], "native_source": native_source,
            "native_pointer": f"/script_json/paragraphs/{i}/text"})
    if expected is not None and expected[last_end:].strip():
        raise IFLineError("source_coverage_gap")
    return {"segments": segments, "choices": [], "generation_issue_codes": [],
            "boundary": "first_chapter_script_ir", "choice_support": "not_in_script_ir_v3"}


class IFLineAdapter:
    def __init__(self, config):
        self.config = dict(config)
        self.base = str(config.get("base_url", "http://127.0.0.1:18081")).rstrip("/")
        self.prefix = str(config.get("api_prefix", "/api"))
        self.timeout = float(config.get("timeout_seconds", 900))
        self.transport = config.get("transport")  # Tests only; never merged into real evidence.
        self._managed_process = None
        self._managed_log = None
        self._cookie = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if config.get("managed_runtime") else urllib.request.build_opener()

    def preflight(self, bundle_dir):
        bundle = Path(bundle_dir)
        errors, checks = [], []
        try:
            shared = (bundle / "shared_task.txt").read_text(encoding="utf-8")
            payload = _read(bundle / "payloads/if_line.json")
            if payload.get("extra_requirements") != shared:
                errors.append("payload_shared_mismatch")
            if payload.get("story_start") or payload.get("story_end"):
                errors.append("duplicate_opening_fields")
            if not shared or shared != shared.strip():
                errors.append("invalid_shared_task")
            if not (bundle / "opening.txt").is_file():
                errors.append("missing_opening")
            checks.append("shared_payload_checked")
        except (OSError, ValueError):
            errors.append("missing_or_invalid_bundle")
        repo = self.config.get("repo_path", self.config.get("repo_dir"))
        if repo:
            from story_benchmark.provenance import verify_repository
            provenance = verify_repository(Path(repo), BASE_COMMIT, self.config)
            errors.extend(provenance["errors"])
            checks.extend(provenance["checks"])
        else:
            errors.append("missing_repo_dir")
        for field in ("max_calls", "max_output_tokens", "max_input_chars"):
            if not isinstance(self.config.get(field), int) or self.config[field] <= 0:
                errors.append("missing_" + field)
        if self.config.get("live"):
            if self.transport:
                errors.append("mock_transport_forbidden_in_live_run")
            managed = self.config.get("managed_runtime")
            if managed and managed not in {"engineering_fixed_response", "native_services"}:
                errors.append("unsupported_managed_runtime")
            if managed and not Path(self.config.get("python_executable", "")).is_file():
                errors.append("missing_native_python_executable")
            if managed == "native_services":
                if not self.config.get("isolated_deployment"):
                    errors.append("isolated_deployment_required")
                if not self.config.get("database_url_env") or not os.environ.get(self.config["database_url_env"]):
                    errors.append("missing_isolated_database_url_env")
                if not os.environ.get(self.config.get("model_api_key_env", "OPENAI_API_KEY")):
                    errors.append("missing_model_key")
                if not self.config.get("redis_executable") and not self.config.get("redis_url_env"):
                    errors.append("missing_isolated_redis_configuration")
            if not managed and not os.environ.get(self.config.get("auth_cookie_env", "IFLINE_BENCH_SID")):
                errors.append("missing_auth_cookie")
            if not managed and not self.config.get("isolated_deployment"):
                errors.append("isolated_deployment_required")
            if not managed and not self.config.get("worker_receipt"):
                errors.append("missing_worker_receipt")
        return {"ok": not errors, "checks": checks, "errors": errors,
                "native_integration": "not_run", "system": "if_line"}

    def _request(self, method, path, body=None, headers=None):
        if self.transport:
            return self.transport(method, path, body, headers or {})
        if not self.config.get("live"):
            raise IFLineError("live_generation_disabled")
        token = self._cookie or os.environ.get(self.config.get("auth_cookie_env", "IFLINE_BENCH_SID"))
        if not token:
            raise IFLineError("missing_auth_cookie")
        if any(c in token for c in "\r\n;"):
            raise IFLineError("invalid_auth_cookie")
        request = urllib.request.Request(self.base + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json", "Cookie": "sid=" + token,
                     **(headers or {})}, method=method)
        try:
            with self._opener.open(request, timeout=min(60, self.timeout)) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise IFLineError("native_http_error", str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise IFLineError("delivery_unknown" if method != "GET" else "native_unreachable") from None
        except ValueError:
            raise IFLineError("invalid_native_response") from None

    def _save_handle(self, handle):
        _write(Path(handle["run_dir"]) / "native/if_line_journal.json", handle)

    def prepare(self, bundle_dir, run_dir):
        bundle, run = Path(bundle_dir).resolve(), Path(run_dir).resolve()
        report = self.preflight(bundle)
        if not report["ok"]:
            raise IFLineError("preflight_failed", ",".join(report["errors"]))
        digest = hashlib.sha256((bundle / "shared_task.txt").read_bytes()).hexdigest()
        journal = run / "native/if_line_journal.json"
        if journal.exists():
            handle = _read(journal)
            if handle["shared_sha256"] != digest or handle["root_run_id"] != self.config["root_run_id"]:
                raise IFLineError("resume_input_conflict")
            if self.config.get("managed_runtime") and self._managed_process is None:
                raise IFLineError("managed_resume_requires_session_reconciliation")
            return handle
        handle = {"system": "if_line", "bundle_dir": str(bundle), "run_dir": str(run),
            "root_run_id": self.config["root_run_id"], "shared_sha256": digest,
            "operations": {}, "simulated": self.transport is not None}
        self._save_handle(handle)
        if self.config.get("managed_runtime"):
            try:
                self._start_managed(handle)
            except BaseException:
                self._stop_managed()
                raise
        return handle

    def _start_managed(self, handle):
        """Create a new isolated native deployment after root runner preparation."""
        runtime = Path(handle["run_dir"]) / "native-runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        benchmark_root = Path(__file__).resolve().parents[2]
        launch_config = {k: v for k, v in self.config.items() if k not in {"transport", "managed_runtime"}}
        launch_config.update(runtime_dir=str(runtime), port=port, chapter_count=int(self.config.get("chapter_count",1)))
        launch_config["repo_path"] = str(Path(self.config.get("repo_path",self.config.get("repo_dir"))).resolve())
        if self.config.get("source_lock"):
            launch_config["source_lock"] = str(Path(self.config["source_lock"]).resolve())
        cfg = runtime / "launcher-config.json"
        _write(cfg, launch_config)
        self._managed_log = (runtime / "launcher.log").open("a", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(benchmark_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self._managed_process = subprocess.Popen([self.config["python_executable"], "-m",
            "native_shims.if_line.launcher", "engineering-server" if self.config["managed_runtime"]=="engineering_fixed_response" else "native-services",
            "--config", str(cfg)],
            cwd=runtime, env=env, stdout=self._managed_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + min(60,self.timeout)
        while True:
            if self._managed_process.poll() is not None:
                raise IFLineError("managed_runtime_exited", str(self._managed_process.returncode))
            try:
                with self._opener.open(self.base + "/__benchmark__/health", timeout=1) as response:
                    health = json.load(response)
                if health.get("root_run_id") != handle["root_run_id"]:
                    raise IFLineError("managed_runtime_run_mismatch")
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                if time.monotonic() >= deadline:
                    raise IFLineError("managed_runtime_start_timeout") from None
                time.sleep(0.1)
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
        request = urllib.request.Request(self.base + "/api/auth/guest", data=b"", method="POST")
        with opener.open(request, timeout=10) as response:
            if response.status != 201:
                raise IFLineError("managed_guest_auth_failed")
        self._cookie = next((cookie.value for cookie in jar if cookie.name == "sid"), None)
        if not self._cookie:
            raise IFLineError("managed_guest_auth_failed")
        self.config["worker_receipt"] = str(Path(self.config["trace_dir"]) / "if_line_worker_receipt.json")
        handle.update(execution_environment=self.config["managed_runtime"], runtime_dir=str(runtime),
                      api_base_url=self.base, managed_pid=self._managed_process.pid)
        self._save_handle(handle)

    def _stop_managed(self):
        if self._managed_process is not None:
            if self._managed_process.poll() is None:
                self._managed_process.send_signal(signal.SIGTERM)
                try:
                    self._managed_process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    self._managed_process.kill()
                    self._managed_process.wait(timeout=5)
                    raise IFLineError("managed_runtime_forced_shutdown")
            self._managed_process = None
        if self._managed_log is not None:
            self._managed_log.close()
            self._managed_log = None

    def _operation(self, handle, name, method, path, body=None, idempotent=False, headers=None):
        ops = handle["operations"]
        previous = ops.get(name)
        signature = hashlib.sha256(json.dumps([method, path, body, headers], sort_keys=True).encode()).hexdigest()
        if previous:
            if previous["signature"] != signature:
                raise IFLineError("operation_conflict", name)
            if previous.get("status") == "completed":
                return previous["result"]
            if not idempotent:
                raise IFLineError("delivery_unknown", name)
        native_headers = dict(headers or {})
        if idempotent:
            native_headers["Idempotency-Key"] = f"{handle['root_run_id']}:if_line:{name}"
        ops[name] = {"signature": signature, "status": "started", "method": method,
                     "path": path, "idempotent": idempotent}
        self._save_handle(handle)
        result = self._request(method, path, body, native_headers)
        ops[name].update(status="completed", result=result)
        self._save_handle(handle)
        return result

    def _wait_task(self, handle, name, task_id):
        started = time.monotonic()
        while True:
            task = self._request("GET", f"{self.prefix}/tasks/{task_id}")
            _write(Path(handle["run_dir"]) / f"native/{name}_task.json", task)
            if not isinstance(task, dict) or task.get("id") != task_id:
                raise IFLineError("invalid_task_response")
            if task.get("status") == "succeeded":
                return task
            if task.get("status") in {"failed", "cancelled", "partial"}:
                raise IFLineError("native_task_failed", task.get("error_code") or task["status"])
            if time.monotonic() - started >= self.timeout:
                raise IFLineError("task_timeout", task_id)
            time.sleep(min(float(self.config.get("poll_seconds", 1)), 5))

    def _generate(self, handle, name, path, body, result_key):
        accepted = self._operation(handle, name, "POST", self.prefix + path, body, idempotent=True)
        if not isinstance(accepted, dict) or not accepted.get("task_id"):
            raise IFLineError("invalid_task_acceptance")
        task = self._wait_task(handle, name, accepted["task_id"])
        ref = task.get("result_refs", {}).get(result_key)
        if not ref:
            raise IFLineError("missing_result_ref", result_key)
        handle[name + "_revision_id"] = ref
        self._save_handle(handle)
        return ref, task

    def _activate(self, handle, name, path, revision):
        if handle["operations"].get(name, {}).get("status") == "completed":
            return
        # GET latest head only for optimistic locking; artifact always task result ref.
        head = self._request("GET", self.prefix + path)
        if head.get("revision_id") == revision:
            return  # Recovery after PUT response loss, no write/re-generation.
        if name in handle["operations"]:
            raise IFLineError("head_delivery_conflict", name)
        self._operation(handle, name, "PUT", self.prefix + path, {"revision_id": revision},
                        headers={"If-Match": f'"{head["lock_version"]}"'})

    def _get_revision(self, handle, path, revision_id, filename):
        rows = self._request("GET", self.prefix + path)
        if not isinstance(rows, list):
            raise IFLineError("invalid_revision_response")
        selected = [r for r in rows if r.get("id") == revision_id]
        if len(selected) != 1:
            raise IFLineError("missing_native_artifact", revision_id)
        _write(Path(handle["run_dir"]) / "native" / filename, selected[0])
        return selected[0]

    def _check_worker(self, handle):
        if self.transport:
            return
        try:
            receipt = _read(self.config["worker_receipt"])
        except (OSError, ValueError, KeyError):
            raise IFLineError("missing_worker_receipt") from None
        expected = {"root_run_id": handle["root_run_id"], "system": "if_line",
                    "trace_dir": str(Path(self.config["trace_dir"]).resolve()),
                    "max_calls": self.config["max_calls"], "max_output_tokens": self.config["max_output_tokens"],
                    "max_input_chars": self.config["max_input_chars"], "role": "worker",
                    "model": self.config["model"]}
        endpoint_key = "requested_model_base_url" if self.config.get("managed_runtime")=="engineering_fixed_response" else "model_base_url"
        expected[endpoint_key] = self.config["model_base_url"]
        if any(receipt.get(k) != v for k, v in expected.items()):
            raise IFLineError("worker_config_mismatch")
        # Local experiment only: stale/dead worker receipts cannot authorize dispatch.
        try:
            os.kill(int(receipt["pid"]), 0)
        except (OSError, KeyError, ValueError):
            raise IFLineError("worker_not_running") from None

    def generate_first_artifact(self, handle):
        self._check_worker(handle)
        payload = _read(Path(handle["bundle_dir"]) / "payloads/if_line.json")
        # Native ProjectCreate forbids extra fields. Mode belongs to Bible parameters.
        payload = {k: v for k, v in payload.items() if k != "benchmark_input_mode"}
        project = self._operation(handle, "create_project", "POST", self.prefix + "/projects", payload)
        shared = (Path(handle["bundle_dir"]) / "shared_task.txt").read_text(encoding="utf-8")
        if project.get("extra_requirements") != shared or not project.get("root_story_path_id"):
            raise IFLineError("receiver_input_mismatch")
        _write(Path(handle["run_dir"]) / "native/project.json", project)
        pid, root = project["id"], project["root_story_path_id"]
        bible, task = self._generate(handle, "bible", f"/projects/{pid}/bible-generations",
            {"parameters": {"benchmark_input_mode": "shared_task"}}, "bible_revision_id")
        if task.get("source_refs", {}).get("project_snapshot", {}).get("extra_requirements") != shared:
            raise IFLineError("task_snapshot_input_mismatch")
        _write(Path(handle["run_dir"]) / "trace/received_if_line.json", {
            "system": "if_line", "boundary": "native_task_snapshot",
            "received_task": task["source_refs"]["project_snapshot"]["extra_requirements"],
            "native_task_id": task["id"], "native_project_id": pid,
            "native_source": "native/bible_task.json",
            "native_pointer": "/source_refs/project_snapshot/extra_requirements",
            "simulated": handle["simulated"]})
        self._get_revision(handle, f"/projects/{pid}/bible-revisions", bible, "bible_revision.json")
        self._activate(handle, "activate_bible", f"/projects/{pid}/bible-head", bible)
        outline, _ = self._generate(handle, "outline", f"/story-paths/{root}/outline-generations",
            {"chapter_count": int(self.config.get("chapter_count", 1 if self.config.get("managed_runtime") else 6)), "bible_revision_id": bible}, "outline_revision_id")
        outline_obj = self._get_revision(handle, f"/story-paths/{root}/outline-revisions", outline, "outline_revision.json")
        self._activate(handle, "activate_outline", f"/story-paths/{root}/outline-head", outline)
        chapters = self._request("GET", f"{self.prefix}/story-paths/{root}/chapters")
        _write(Path(handle["run_dir"]) / "native/path_chapters.json", chapters)
        if not isinstance(chapters, list) or not chapters:
            raise IFLineError("missing_path_chapters")
        first = min(chapters, key=lambda x: x["display_index"])
        if not any(c.get("story_path_chapter_id") == first["id"] for c in outline_obj.get("chapters", [])):
            raise IFLineError("outline_chapter_mismatch")
        chapter_id, _ = self._generate(handle, "chapter", f'/path-chapters/{first["id"]}/generations',
            {"bible_revision_id": bible, "outline_revision_id": outline}, "chapter_revision_id")
        chapter = self._request("GET", f"{self.prefix}/chapter-revisions/{chapter_id}")
        if chapter.get("id") != chapter_id:
            raise IFLineError("chapter_source_mismatch")
        _write(Path(handle["run_dir"]) / "native/chapter_revision.json", chapter)
        self._activate(handle, "activate_chapter", f'/path-chapters/{first["id"]}/head', chapter_id)
        script_id, _ = self._generate(handle, "script", f"/chapter-revisions/{chapter_id}/script-generations",
            {}, "chapter_script_revision_id")
        self._get_revision(handle, f"/chapter-revisions/{chapter_id}/script-revisions", script_id, "script_revision.json")
        return {"adapter_status": "completed", "generation_status": "not_evaluated",
                "native_project_id": pid, "root_story_path_id": root,
                "chapter_revision_id": chapter_id, "script_revision_id": script_id,
                "simulated": handle["simulated"],
                "execution_environment": handle.get("execution_environment", "native_service")}

    def export_first_artifact(self, handle):
        native = Path(handle["run_dir"]) / "native"
        try:
            revision = _read(native / "script_revision.json")
            chapter = _read(native / "chapter_revision.json")
        except (OSError, ValueError):
            raise IFLineError("missing_script_artifact") from None
        return export_script_ir(revision, chapter)

    def close(self, handle):
        self._save_handle(handle)
        self._stop_managed()
        if handle.get("runtime_dir"):
            report = _read(Path(handle["runtime_dir"]) / "source-after.json")
            if not report.get("source_unchanged"):
                raise IFLineError("native_source_modified")
