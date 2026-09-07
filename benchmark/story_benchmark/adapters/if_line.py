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


def candidate_input_mapping(bundle):
    """Mechanical native-instructions mapping, without adding story content."""
    case = _read(Path(bundle) / "case.json")
    decision = case["decisions"][0]
    boundary = case.get("output_boundary", {})
    scope = case["scope_map"]["第一次选择的行动顺序"]
    if (boundary != {"kind": "first_choice", "decision_id": decision["id"],
                     "include_options": True, "execute_choice": False}
            or len(decision["options"]) != 2 or not isinstance(scope, str)):
        raise IFLineError("unsupported_candidate_input_contract")
    # All creative strings are copied verbatim; keys only describe their origin.
    values = {"decision": decision, "scope": scope}
    instructions = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return {"instructions": instructions, "candidate_count": 2,
            "mapping_kind": "verbatim_case_field_mapping",
            "source_file": "case.json", "source_sha256": hashlib.sha256((Path(bundle)/"case.json").read_bytes()).hexdigest(),
            "sources": [{"target": "/decision", "source_pointer": "/decisions/0", "value": decision},
                        {"target": "/scope", "source_pointer": "/scope_map/第一次选择的行动顺序", "value": scope}]}


def first_choice_input_contract(bundle):
    """V3 maps only an output count; never re-injects story instructions."""
    case = _read(Path(bundle) / "case.json")
    contract = case.get("output_contract", {})
    if not isinstance(contract, dict):
        raise IFLineError("unsupported_v3_first_choice_contract")
    count = contract.get("choice_count")
    if (case.get("input_contract") != {"version": "3.0", "task_delivery": "verbatim_first_creative_request",
            "character_policy": "exact_declared_cast", "adapter_story_reinjection": "none"}
            or contract.get("version") != "3.0" or contract.get("scope") != "first_unselected_choice"
            or contract.get("selection_executed") is not False or contract.get("allow_empty_body") is not True
            or contract.get("native_choice_previews") != "separate" or not contract.get("decision_id")
            or isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 4
            or "output_boundary" in case):
        raise IFLineError("unsupported_v3_first_choice_contract")
    return {"candidate_count": count, "decision_id": contract["decision_id"], "instructions": None,
            "mapping_kind": "common_contract_count_only", "instructions_policy": "omitted",
            "source_file": "case.json", "source_sha256": hashlib.sha256((Path(bundle)/"case.json").read_bytes()).hexdigest(),
            "sources": [{"target": "/candidate_count", "source_pointer": "/output_contract/choice_count", "value": count}]}


def export_candidate_previews(candidates):
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise IFLineError("invalid_native_candidate_count")
    previews, choices = [], []
    for i, row in enumerate(candidates):
        if (not row.get("id") or not isinstance(row.get("preview_text"), str)
                or not row["preview_text"] or not isinstance(row.get("state_delta"), dict)
                or not row.get("option_key")):
            raise IFLineError("invalid_native_candidate")
        previews.append({"segment_id": row["id"], "kind": "branch_preview", "speaker": None,
                         "text": row["preview_text"], "native_source": "native/candidate_previews.json",
                         "native_pointer": f"/{i}/preview_text"})
        choices.append({"choice_id": row["id"], "option_key": row["option_key"], "label": None,
                        "preview_text": row["preview_text"], "state_delta": row["state_delta"],
                        "selected": False, "native_source": "native/candidate_previews.json",
                        "native_pointer": f"/{i}"})
    return {"segments": [], "choices": choices, "unselected_previews": previews,
            "generation_issue_codes": ["unsupported_output_boundary", "no_visible_continuation", "missing_choice_labels"],
            "native_capability_status": "unsupported_output_boundary", "stop_reason": "unselected_candidates",
            "selection_executed": False,
            "boundary": "provided_prefix_candidates", "choice_support": "native_unselected_previews_without_action_labels"}


def export_first_choice(candidates, contract, native_context):
    count = contract["candidate_count"]
    if not isinstance(candidates, list) or len(candidates) != count:
        raise IFLineError("invalid_native_candidate_count")
    previews, choices = [], []
    for i, row in enumerate(candidates):
        if (not row.get("id") or not isinstance(row.get("preview_text"), str)
                or not row["preview_text"] or not isinstance(row.get("state_delta"), dict)
                or not isinstance(row.get("option_key"), str) or not row["option_key"]):
            raise IFLineError("invalid_native_candidate")
        previews.append({"segment_id": row["id"], "choice_id": row["id"], "kind": "branch_preview", "speaker": None,
                         "text": row["preview_text"], "native_source": "native/candidate_previews.json",
                         "native_pointer": f"/{i}/preview_text"})
        choices.append({"choice_id": row["id"], "option_key": row["option_key"], "label": row["option_key"],
                        "state_delta": row["state_delta"], "selected": False,
                        "native_source": "native/candidate_previews.json", "native_pointer": f"/{i}/option_key"})
    if len({row["id"] for row in candidates}) != count or len({row["option_key"] for row in candidates}) != count:
        raise IFLineError("duplicate_native_candidate_identity")
    return {"segments": [], "choices": choices, "unselected_previews": previews,
            "generation_issue_codes": [], "native_capability_status": "supported",
            "selection_executed": False, "stop_reason": "first_choice", "decision_id": contract["decision_id"],
            "boundary": "first_unselected_choice", "choice_support": "native_option_keys_with_separate_previews",
            "native_context": native_context}


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
        entry_mode = self.config.get("entry_mode", "first_chapter")
        if entry_mode not in {"first_chapter", "provided_prefix_candidates", "shared_first_choice"}:
            errors.append("unsupported_entry_mode")
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
            case = _read(bundle / "case.json") if (bundle/"case.json").exists() else {}
            if any(isinstance(case.get(key), dict) and case[key].get("version") == "3.0"
                   for key in ("input_contract", "output_contract")) and entry_mode != "shared_first_choice":
                errors.append("v3_requires_shared_first_choice")
            if entry_mode in {"provided_prefix_candidates", "shared_first_choice"}:
                mapping = first_choice_input_contract(bundle) if entry_mode == "shared_first_choice" else candidate_input_mapping(bundle)
                opening = (bundle / "opening.txt").read_text(encoding="utf-8")
                if not opening or len(opening) > 8000:
                    errors.append("opening_exceeds_native_candidate_tail")
                if len(mapping["instructions"] or "") > 12000:
                    errors.append("candidate_instructions_exceed_native_contract")
                checks.append("candidate_input_contract_checked")
        except (KeyError, TypeError, IndexError, IFLineError):
            errors.append("invalid_candidate_input_contract")
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
            if (handle["shared_sha256"] != digest or handle["root_run_id"] != self.config["root_run_id"]
                    or handle.get("entry_mode", "first_chapter") != self.config.get("entry_mode", "first_chapter")):
                raise IFLineError("resume_input_conflict")
            if self.config.get("managed_runtime") and self._managed_process is None:
                raise IFLineError("managed_resume_requires_session_reconciliation")
            return handle
        handle = {"system": "if_line", "bundle_dir": str(bundle), "run_dir": str(run),
            "root_run_id": self.config["root_run_id"], "shared_sha256": digest,
            "operations": {}, "simulated": self.transport is not None,
            "entry_mode": self.config.get("entry_mode", "first_chapter")}
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
        # This is the whole outline length. generate_first_artifact still selects
        # only its first chapter. 13 is the frozen native new-project UI default.
        launch_config.update(runtime_dir=str(runtime), port=port, chapter_count=int(self.config.get("chapter_count",13)))
        launch_config.update(bundle_dir=handle["bundle_dir"], shared_sha256=handle["shared_sha256"])
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
        expected["model_parameters"] = self.config.get("model_parameters", {})
        expected["async_transport_keepalive_connections"] = 0
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
            {"chapter_count": int(self.config.get("chapter_count", 13)), "bible_revision_id": bible}, "outline_revision_id")
        outline_obj = self._get_revision(handle, f"/story-paths/{root}/outline-revisions", outline, "outline_revision.json")
        self._activate(handle, "activate_outline", f"/story-paths/{root}/outline-head", outline)
        chapters = self._request("GET", f"{self.prefix}/story-paths/{root}/chapters")
        _write(Path(handle["run_dir"]) / "native/path_chapters.json", chapters)
        if not isinstance(chapters, list) or not chapters:
            raise IFLineError("missing_path_chapters")
        first = min(chapters, key=lambda x: x["display_index"])
        if not any(c.get("story_path_chapter_id") == first["id"] for c in outline_obj.get("chapters", [])):
            raise IFLineError("outline_chapter_mismatch")
        if handle.get("entry_mode") in {"provided_prefix_candidates", "shared_first_choice"}:
            return self._generate_prefix_candidates(handle, pid, root, first)
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

    def _generate_prefix_candidates(self, handle, pid, root, first):
        bundle, native = Path(handle["bundle_dir"]), Path(handle["run_dir"])/"native"
        opening = (bundle/"opening.txt").read_text(encoding="utf-8")
        opening_hash = hashlib.sha256(opening.encode()).hexdigest()
        path = f'{self.prefix}/path-chapters/{first["id"]}/revisions'
        previous = handle["operations"].get("import_provided_prefix")
        if previous and previous.get("status") != "completed":
            matches = [r for r in self._request("GET", path) if r.get("content") == opening
                       and r.get("content_hash") == opening_hash and r.get("parent_revision_id") is None]
            if len(matches) != 1:
                raise IFLineError("delivery_unknown", "manual prefix import requires exact unique revision recovery")
            previous.update(status="completed", result=matches[0], recovered_via="exact_native_revision_list")
            self._save_handle(handle)
        chapter = self._operation(handle, "import_provided_prefix", "POST", path,
                                  {"content": opening, "parent_revision_id": None})
        if chapter.get("content") != opening or chapter.get("content_hash") != opening_hash:
            raise IFLineError("provided_prefix_receipt_mismatch")
        chapter_id = chapter["id"]
        _write(native/"provided_prefix_revision.json", chapter)
        self._activate(handle, "activate_provided_prefix", f'/path-chapters/{first["id"]}/head', chapter_id)
        checkpoint_path = f'/__benchmark__/path-chapters/{first["id"]}/prefix-checkpoints'
        checkpoint_body = {"chapter_revision_id": chapter_id, "opening_sha256": opening_hash}
        receipt = self._operation(handle, "initialize_prefix_checkpoint", "POST", checkpoint_path,
                                  checkpoint_body, idempotent=True)
        self._verify_prefix_receipt(receipt, chapter_id, opening)
        _write(native/"provided_prefix_import.json", receipt)
        v3 = handle.get("entry_mode") == "shared_first_choice"
        mapping = first_choice_input_contract(bundle) if v3 else candidate_input_mapping(bundle)
        _write(native/"candidate_input_mapping.json", mapping)
        checkpoint_id = receipt["checkpoint"]["id"]
        body = {"chapter_revision_id": chapter_id, "state_snapshot_id": receipt["state_snapshot_id"],
                "candidate_count": mapping["candidate_count"]}
        if not v3:
            body["instructions"] = mapping["instructions"]
        candidate_revision, task = self._generate(handle, "candidates",
            f'/story-paths/{root}/checkpoints/{checkpoint_id}/candidate-set-generations',
            body, "candidate_set_revision_id")
        source = task.get("source_refs", {}).get("candidate_set_source", {})
        provider_input = source.get("provider_input", {})
        if (provider_input.get("chapter_tail") != opening or provider_input.get("state") != {}
                or provider_input.get("instructions") != (mapping["instructions"] or "")):
            raise IFLineError("candidate_source_input_mismatch")
        self._get_revision(handle, f'/story-paths/{root}/checkpoints/{checkpoint_id}/candidate-set-revisions',
                           candidate_revision, "candidate_set_revision.json")
        candidates = self._request("GET", f'{self.prefix}/candidate-set-revisions/{candidate_revision}/candidates')
        if (len(candidates) != mapping["candidate_count"] or {c.get("id") for c in candidates} != set(task["result_refs"]["candidate_ids"])
                or any(c.get("candidate_set_revision_id") != candidate_revision for c in candidates)):
            raise IFLineError("candidate_result_refs_mismatch")
        _write(native/"candidate_previews.json", candidates)
        # Re-read the structural receipt after generation. This proves that
        # generation did not promote a path, apply a choice, or mutate the state.
        after = self._request("POST", checkpoint_path, checkpoint_body,
            {"Idempotency-Key": f'{handle["root_run_id"]}:if_line:initialize_prefix_checkpoint'})
        self._verify_prefix_receipt(after, chapter_id, opening)
        if after != receipt:
            raise IFLineError("candidate_generation_changed_current_state")
        _write(native/"provided_prefix_after_candidates.json", after)
        if v3:
            context = self._preserve_native_context(handle, root, first, receipt, task, candidate_revision)
            export_first_choice(candidates, mapping, context)
        else:
            export_candidate_previews(candidates)
        return {"adapter_status": "completed", "generation_status": "unselected_candidates_generated",
                "native_capability_status": "supported" if v3 else "unsupported_output_boundary",
                "stop_reason": "first_choice" if v3 else "unselected_candidates",
                "native_project_id": pid, "root_story_path_id": root, "provided_prefix_revision_id": chapter_id,
                "candidate_set_revision_id": candidate_revision, "candidate_count": mapping["candidate_count"],
                "external_initialization": "provided_prefix_checkpoint_empty_state", "choice_executed": False,
                "simulated": handle["simulated"],
                "execution_environment": handle.get("execution_environment", "native_service")}

    def _preserve_native_context(self, handle, root, first, receipt, task, candidate_revision):
        """Retain native planning dependencies without asserting story agreement."""
        native = Path(handle["run_dir"])/"native"
        bible = _read(native/"bible_revision.json")
        outline = _read(native/"outline_revision.json")
        index = next(i for i, row in enumerate(outline["chapters"]) if row.get("story_path_chapter_id") == first["id"])
        context = {"system": "if_line", "story_path_id": root, "path_chapter_id": first["id"],
            "semantic_consistency": "not_evaluated", "adapter_semantic_rewriting": False,
            "native_planning_retained": True, "source_integrity": "checked_by_native_services",
            "dependency_note": "Native manual chapter import and candidates require an existing Bible and Outline; native outline generation does not read imported chapter prose.",
            "generation_order": [
                {"stage": "bible.generate", "origin": "native_generated", "revision_id": bible["id"],
                 "task_id": _read(native/"bible_task.json")["id"], "native_source": "native/bible_revision.json", "native_pointer": "/content_json"},
                {"stage": "outline.generate", "origin": "native_generated", "revision_id": outline["id"],
                 "task_id": _read(native/"outline_task.json")["id"], "native_source": "native/outline_revision.json", "native_pointer": f"/chapters/{index}"},
                {"stage": "manual_prefix_import", "origin": "provided_prefix", "revision_id": receipt["chapter_revision_id"],
                 "task_id": None, "native_source": "native/provided_prefix_revision.json", "native_pointer": "/content"},
                {"stage": "structural_checkpoint_initialization", "origin": "external_input_structure", "checkpoint_id": receipt["checkpoint"]["id"],
                 "state_snapshot_id": receipt["state_snapshot_id"], "native_source": "native/provided_prefix_import.json", "native_pointer": "/checkpoint"},
                {"stage": "branch.candidates.generate", "origin": "native_generated", "revision_id": candidate_revision,
                 "task_id": task["id"], "native_source": "native/candidates_task.json", "native_pointer": "/source_refs/candidate_set_source/provider_input"}],
            "empty_state_source": {"native_source": "native/provided_prefix_import.json", "native_pointer": "/state_json"},
            "full_outline_source": {"native_source": "native/outline_revision.json", "native_pointer": "/chapters"},
            "content_assessment": "Native planning deviations or conflicts with the provided prefix remain original model outputs for evaluation; no automatic story correction is applied."}
        _write(native/"native_context.json", context)
        return context

    @staticmethod
    def _verify_prefix_receipt(receipt, chapter_id, opening):
        if (receipt.get("origin") != "external_provided_prefix_import"
                or "generation_task_id" not in receipt
                or receipt.get("generation_task_id") is not None or receipt.get("content") != opening
                or receipt.get("chapter_revision_id") != chapter_id or receipt.get("current_revision_id") != chapter_id
                or receipt.get("state_json") != {} or receipt.get("story_path_count") != 1
                or receipt.get("choice_decision_count") != 0):
            raise IFLineError("provided_prefix_initialization_mismatch")

    def export_first_artifact(self, handle):
        native = Path(handle["run_dir"]) / "native"
        if handle.get("entry_mode") in {"provided_prefix_candidates", "shared_first_choice"}:
            try:
                if handle.get("entry_mode") == "shared_first_choice":
                    return export_first_choice(_read(native/"candidate_previews.json"),
                        first_choice_input_contract(Path(handle["bundle_dir"])), _read(native/"native_context.json"))
                return export_candidate_previews(_read(native/"candidate_previews.json"))
            except (OSError, ValueError):
                raise IFLineError("missing_candidate_artifact") from None
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
