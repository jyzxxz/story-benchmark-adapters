"""Observe/control the unmodified InfiPlot browser, including native prefetch."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from threading import Timer
from urllib import request
from urllib.parse import unquote_to_bytes

from ..adapters.infiplot import InfiPlotAdapter, InfiPlotError
from ..budget import validate_budget_policy
from native_shims.infiplot.batch_runtime import LocalIdentity, NativeBatchRuntime

DEFAULT_PLAYWRIGHT = str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright")


def preflight(config: dict, bundle: Path, policy: dict) -> dict:
    checks, errors = [], []
    cfg = {**config, "live": False, "base_url": "http://127.0.0.1:3217"}
    result = InfiPlotAdapter(cfg).preflight(Path(bundle))
    checks.extend(result["checks"])
    errors.extend(result["errors"])
    errors.extend(validate_budget_policy(config))
    if config.get("budget_mode") != "unlimited":
        errors.append("batch_requires_explicit_unlimited_budget")
    if type(policy.get("window_chars")) is not int or policy["window_chars"] <= 0:
        errors.append("positive_window_chars_required")
    indices = policy.get("choice_indices")
    if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 for i in indices):
        errors.append("nonnegative_choice_indices_required")
    if policy.get("render_mode", "offscreen_native") != "offscreen_native":
        errors.append("infiplot_requires_offscreen_native_renderer")
    if config.get("prefetch_policy", "native_browser") != "native_browser":
        errors.append("only_unchanged_native_browser_prefetch_supported")
    for command in (config.get("node_executable") or "node", config.get("pnpm_executable") or "pnpm"):
        if not shutil.which(command):
            errors.append("missing_executable:" + command)
    pw = Path(config.get("playwright_module") or DEFAULT_PLAYWRIGHT)
    if not (pw / "package.json").is_file():
        errors.append("playwright_module_required:" + str(pw))
    if config.get("identity_mode", "local_fixture") != "local_fixture":
        errors.append("only_disclosed_local_fixture_identity_currently_supported")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        errors.append("shared_text_model_required")
    if config.get("route_compatibility") not in (None, "none", "native_render_entry"):
        errors.append("unknown_route_compatibility")
    checks.append("original_browser_prefetch_and_real_dom_actions")
    return {"ok": not errors, "errors": errors, "checks": checks,
            "identity_mode": "local_fixture", "production_auth_verified": False}


def visible_fields(beat):
    """Exactly the native PlayCanvas visible order; never expose a hidden line."""
    for key in ("narration", "line"):
        if key == "line" and not beat.get("speaker"):
            continue
        value = beat.get(key)
        if value is not None and not isinstance(value, str):
            raise InfiPlotError("invalid_prose", "Native visible field is not a string")
        if value:
            yield key, value, beat.get("speaker") if key == "line" else None


def selected_index(policy, count, options):
    indices = policy["choice_indices"]
    index = indices[min(count, len(indices) - 1)]
    if not 0 <= index < len(options):
        raise InfiPlotError("choice_index_out_of_range", "Configured strategy has no corresponding native option")
    return index


def selection_committed(pending, state):
    choice = pending["options"][pending["index"]]
    effect = choice.get("effect", {})
    history = state.get("session", {}).get("history", [])
    if effect.get("kind") == "advance-beat":
        return bool(history and history[-1]["scene"]["id"] == pending["scene_id"] and state.get("beat", {}).get("id") == effect.get("targetBeatId"))
    if effect.get("kind") != "change-scene":
        raise InfiPlotError("invalid_choice", "Unsupported native choice effect")
    expected = {"kind": "choice", "choiceId": choice["id"], "label": choice["label"], "nextSceneSeed": effect.get("nextSceneSeed")}
    return any(h.get("scene", {}).get("id") == pending["scene_id"] and h.get("exit") == expected for h in history[:-1])


class Browser:
    def __init__(self, config, recorder):
        script = Path(__file__).resolve().parents[2] / "native_shims/infiplot/batch_browser.cjs"
        self.recorder = recorder
        self.process = subprocess.Popen([config.get("node_executable") or "node", str(script)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        start_new_session=True)
        self.number, self.responses, self.requests, self.failures = 0, {}, {}, []

    def observe(self, item):
        kind, ident = item.get("kind"), item.get("request_id")
        rec = self.recorder
        if kind == "native_request":
            self.requests[ident] = item
            path = rec.save_json(f"native/browser-wire/{ident}.request.json", item["data"])
            rec.append("native/browser-wire/index.jsonl", {"event": kind, "request_id": ident, "route": item["route"], "file": path})
            rec.event("native_request_submitted", observer="offscreen_native_network", native_request_id=ident, route=item["route"],
                      selection_status="not_inferred_from_prefetch_request")
            if item["route"] == "/api/start":
                rec.save_json("native/route-transmitted-task.json", {"boundary": "native_browser_fetch_request_body", "received_task": item["data"].get("worldSetting"),
                    "direct_native_route_receiver_observed": False, "request_file": path})
        elif kind == "native_response":
            self.responses[ident] = item
            path = rec.save_json(f"native/browser-wire/{ident}.response.json", item["data"] if item["data"] is not None else {"raw_utf8": item["raw_utf8"]})
            rec.append("native/browser-wire/index.jsonl", {"event": kind, "request_id": ident, "status": item["status"], "file": path,
                "native_operation_id": item.get("native_operation_id")})
            rec.event("native_response_available", observer="offscreen_native_network", native_request_id=ident, status=item["status"],
                      potentially_unselected_prefetch=True)
            if item["status"] >= 400:
                self.failures.append(item)
                rec.error("native_http_error", f"Native route returned HTTP {item['status']}", native_request_id=ident, source=path)
        elif kind == "native_navigation_response":
            rec.append("native/browser-wire/navigation.jsonl", item)
        elif kind in ("native_request_failed", "native_response_error", "page_error"):
            self.failures.append(item)
            rec.append("native/browser-wire/index.jsonl", item)
            rec.event("native_browser_failure", observer="offscreen_native_network", **item)

    def call(self, op, **values):
        self.number += 1
        self.process.stdin.write(json.dumps({"id": self.number, "op": op, **values}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise InfiPlotError("browser_exited", "Native browser observer exited without a response")
            row = json.loads(line)
            if row.get("kind") == "reply":
                if row.get("id") != self.number:
                    raise InfiPlotError("observer_protocol_error", "Unexpected browser response sequence")
                if row.get("error"):
                    raise InfiPlotError("native_browser_error", row["error"])
                return row.get("result")
            self.observe(row)

    def close(self):
        import signal
        def force_cleanup():
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        timer = Timer(20, force_cleanup)
        timer.daemon = True
        timer.start()
        try:
            if self.process.poll() is None:
                self.call("close")
                self.process.stdin.close()
                self.process.wait(timeout=10)
        finally:
            timer.cancel()
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                if pipe and not pipe.closed:
                    pipe.close()


def _native_asset(url, recorder, gateway, native_source, role, cache):
    if not url:
        return None

    if url in cache:
        return cache[url]
    try:
        if url.startswith("data:"):
            header, encoded = url.split(",", 1)
            raw = base64.b64decode(encoded) if ";base64" in header else unquote_to_bytes(encoded)
            mime = header.split(";", 1)[0][5:]
        else:
            with request.urlopen(url, timeout=None) as response:
                raw = response.read()
                mime = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(mime, ".bin")
        digest = hashlib.sha256(raw).hexdigest()
        path = recorder.save_bytes("native/asset-bytes/" + digest + suffix, raw)
        output = gateway.output_for_hash(digest)
        asset = recorder.asset(recorder.root / path, origin="generated",
                               output_id=output.get("output_id") if isinstance(output, dict) else None,
                               native_source=native_source, role=role)
        recorder.append("native/asset-linkage.jsonl", {"asset_id": asset.get("asset_id"), "native_source": native_source,
                        "candidate_linkage": "unique_gateway_bytes" if output else "unknown_or_ambiguous", "image_uuid_is_provider_id": False})
        cache[url] = asset
        return asset
    except (ValueError, OSError) as exc:
        recorder.error("native_image_download_failed", type(exc).__name__, native_source=native_source)
        return None


def writer_lineage(scene_id, responses, calls):
    matches = [row for row in responses.values() if isinstance(row.get("data"), dict) and
               row["data"].get("scene", {}).get("id") == scene_id]
    if len(matches) != 1 or not matches[0].get("native_operation_id"):
        return {"source_call_ids": None, "status": "unavailable_without_unique_native_operation_header"}
    operation = matches[0]["native_operation_id"]
    writers = [row["call_id"] for row in calls if row.get("native_operation_id") == operation and row.get("native_stage") == "writer"]
    return {"source_call_ids": writers or None, "native_operation_id": operation,
        "native_request_id": matches[0]["request_id"], "native_scene_id": scene_id,
        "status": "exact_native_operation_writer_attempts" if writers else "unavailable_without_gateway_writer_operation",
        "accepted_response_among_attempts": "not_inferred", "text_similarity_matching": False}

def run(config: dict, bundle: Path, run_dir: Path, recorder, gateway, policy: dict) -> dict:
    check = preflight(config, bundle, policy)
    if not check["ok"]:
        raise InfiPlotError("preflight_failed", "; ".join(check["errors"]))
    run_dir, bundle = Path(run_dir).resolve(), Path(bundle).resolve()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = {**config, "live": False, "base_url": f"http://127.0.0.1:{port}",
           "root_run_id": recorder.run_id, "trace_dir": str(run_dir / "native/text-relay")}
    identity = LocalIdentity()
    adapter = NativeBatchRuntime(cfg, gateway, identity)
    handle = browser = pending = None
    errors, seen, assets, characters = [], [], {}, set()
    choices_taken = 0
    action_epoch, last_recorded_epoch, waiting_for = 0, -1, None
    stop_reason = "native_error"
    try:
        handle = adapter.prepare(bundle, run_dir)
        adapter._launch(handle)
        recorder.save_json("native/batch-policy.json", {"prefetch": "native_browser_unchanged", "render_mode": "offscreen_native",
            "input_delivery": "native_custom_ui_storage_then_original_start_route", "native_source_modified": False,
            "player_actions": "native_dom_clicks", "tts": "disabled_text_and_image_profile", "identity": "local_fixture",
            "generation_deadline": None, "window_chars": policy["window_chars"], "choice_indices": policy["choice_indices"],
            "native_end_supported": False, "model_gateway_roles": ["text", "image", "vision"]})
        browser = Browser(cfg, recorder)
        payload = json.loads((bundle / "payloads/infiplot.json").read_text())
        payload.pop("clientTts", None)
        browser.call("open", base_url=cfg["base_url"], payload=payload, cookie=identity.cookie,
                     playwright_module=cfg.get("playwright_module") or DEFAULT_PLAYWRIGHT,
                     chromium_executable=cfg.get("chromium_executable"), startup_timeout_ms=int(cfg.get("startup_timeout_seconds", 120) * 1000))
        shown, decoded_observations = set(), {}
        while not recorder.scope_reached:
            state = browser.call("state")
            beat, session = state.get("beat"), state.get("session")
            if state.get("nativeError"):
                recorder.save_json("native/native-ui-error.json", state)
                raise InfiPlotError("native_ui_error", state["nativeError"])
            if state.get("imageFailed"):
                recorder.save_json("native/native-image-error.json", state)
                raise InfiPlotError("native_image_failed", "Native img completed with no decoded bitmap")
            if not beat or not session or not state.get("imageReady") or state.get("phase") != "ready":
                # An unrelated speculative request can fail while the selected
                # branch remains healthy. Only native UI errors stop playback.
                time.sleep(.1)
                continue
            scene = session["history"][-1]["scene"]
            key = (scene["id"], beat["id"])
            if waiting_for:
                if key != waiting_for:
                    time.sleep(.05)
                    continue
                recorder.event("native_advance_committed", observer="offscreen_native_dom", native_scene_id=key[0], native_beat_id=key[1])
                waiting_for = None
            if pending and not selection_committed(pending, state):
                time.sleep(.05)
                continue
            observation_key = (action_epoch, key)
            if observation_key not in decoded_observations:
                decoded_observations[observation_key] = recorder.event("native_frame_decoded_observed", observer="offscreen_native_dom",
                    native_scene_id=key[0], native_beat_id=key[1], browser_monotonic_ms=state.get("browser_monotonic_ms"))
            if (action_epoch, key) not in shown and state.get("someTextVisible"):
                recorder.event("story_text_presented", observer="offscreen_native_dom", native_scene_id=key[0], native_beat_id=key[1],
                               browser_monotonic_ms=state.get("browser_monotonic_ms"), measurement="observed_visible_text_prefix")
                shown.add((action_epoch, key))
            if not state.get("fullTextVisible"):
                time.sleep(.05)
                continue
            if pending:
                if not selection_committed(pending, state):
                    time.sleep(.05)
                    continue
                recorder.choice(pending["options"], selected_index=pending["index"], native_source=pending["source"], native_id=pending["native_id"])
                recorder.event("choice_committed", observer="offscreen_native_dom", interaction_id=pending["interaction_id"],
                               native_scene_id=scene["id"], native_beat_id=beat["id"])
                choices_taken += 1
                pending = None
            if action_epoch == last_recorded_epoch:
                # Deduplicate stationary observations, not native revisits.
                # A committed real action can return to an earlier beat; its
                # actual visible text belongs in the trajectory again.
                time.sleep(.05)
                continue
            seen.append(key)
            last_recorded_epoch = action_epoch
            snapshot = {k: state[k] for k in ("beat", "session", "phase", "imageUrl", "orientation", "playerName")}
            source = recorder.save_json(f"native/observations/visible-{len(seen):06d}.json", snapshot)
            revision = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            # Native Session versions, including actual visited IDs and chosen
            # exits, are observed rather than reconstructed by the adapter.
            recorder.append("native/state-revisions.jsonl", {"revision_id": revision, "file": source, "native_scene_id": scene["id"], "native_beat_id": beat["id"]})
            for pos, character in enumerate(session["characters"]):
                fingerprint = json.dumps(character, ensure_ascii=False, sort_keys=True)
                if fingerprint in characters:
                    continue
                characters.add(fingerprint)
                pointer = {"file": source, "pointer": f"/session/characters/{pos}"}
                portrait = _native_asset(character.get("basePortraitUrl"), recorder, gateway, pointer, "character_reference", assets)
                recorder.character(character["name"], character, native_source=pointer,
                                   reference_asset_ids=[portrait["asset_id"]] if portrait else [])
            segments = []
            from ..recording import jsonl
            lineage = writer_lineage(scene["id"], browser.responses, jsonl(recorder.root / "telemetry/calls.jsonl"))
            recorder.save_json(f"native/observations/visible-{len(seen):06d}.lineage.json", lineage)
            for field, text, speaker in visible_fields(beat):
                segment = recorder.story(text, speaker=speaker, kind="dialogue" if field == "line" else "narration",
                    native_source={"file": source, "pointer": "/beat/" + field}, revision_id=revision, native_id=beat["id"], source_call_ids=lineage["source_call_ids"])
                if segment:
                    segments.append(segment["segment_id"])
            if segments or not list(visible_fields(beat)):
                image_source = {"file": source, "pointer": "/session/history/" + str(len(session["history"]) - 1) + "/scene/imageUrl"}
                asset = _native_asset(scene.get("imageUrl"), recorder, gateway, image_source, "scene_background", assets)
                captures = run_dir / "native/captures"
                captures.mkdir(parents=True, exist_ok=True)
                clean, ui = captures / f"{len(seen):06d}.png", captures / f"{len(seen):06d}.ui.png"
                captured = browser.call("capture", clean_path=str(clean), ui_path=str(ui))
                recorder.save_json(f"native/captures/{len(seen):06d}.json", captured)
                if not asset:
                    asset = recorder.asset(clean, origin="derived", native_source=image_source, role="native_decoded_scene_background")
                frame = recorder.frame(segment_ids=segments, asset_ids=[asset["asset_id"]], clean_path=clean, ui_path=ui,
                    character_ids=[x["name"] for x in beat.get("activeCharacters", []) if x.get("name")], native_source=image_source,
                    capture_method="offscreen_native_playcanvas_single_background", placeholder=asset.get("origin") == "placeholder")
                observed = decoded_observations[observation_key]
                recorder.event("frame_presented", observer="offscreen_native_dom", segment_ids=segments, frame_id=frame["frame_id"],
                               monotonic_ns=observed["monotonic_ns"], utc=observed["utc"], observed_event_id=observed["event_id"],
                               browser_monotonic_ms=captured.get("browser_monotonic_ms"), measurement="decoded_native_img_and_actual_ui_capture")
            edge = beat.get("next", {})
            options = edge.get("choices", []) if edge.get("type") == "choice" else []
            choice_source = {"file": source, "pointer": "/beat/next/choices"}
            if options:
                recorder.event("choice_available", observer="offscreen_native_dom", native_scene_id=scene["id"], native_beat_id=beat["id"])
            if recorder.scope_reached:
                if options:
                    recorder.choice(options, native_source=choice_source, native_id=beat["id"])
                stop_reason = "reading_window"
                break
            delay = policy.get("reading_delay_seconds", 0)
            if delay:
                recorder.event("simulated_reading_started", seconds=delay)
                time.sleep(delay)
                recorder.event("simulated_reading_finished", seconds=delay)
            if edge.get("type") == "continue":
                if not edge.get("nextBeatId") or edge["nextBeatId"] == beat["id"]:
                    raise InfiPlotError("invalid_next", "Native continue points nowhere or to itself")
                browser.call("advance")
                waiting_for = (scene["id"], edge["nextBeatId"])
                action_epoch += 1
            elif edge.get("type") == "choice" and options:
                index = selected_index(policy, choices_taken, options)
                interaction = "choice-" + str(choices_taken + 1)
                pending = {"options": deepcopy(options), "index": index, "source": choice_source, "native_id": beat["id"],
                           "scene_id": scene["id"], "interaction_id": interaction}
                recorder.event("choice_selected", observer="offscreen_native_dom", interaction_id=interaction,
                               native_choice_id=options[index]["id"], selected_index=index, boundary="immediately_before_real_dom_click")
                browser.call("choose", index=index)
                action_epoch += 1
            else:
                raise InfiPlotError("native_no_choices", "Native graph has no executable continuation; no end was inferred")
        recorder.save_json("native/final-observed-state.json", state)
    except Exception as exc:
        code = getattr(exc, "code", "infiplot_driver_error")
        errors.append({"code": code, "message": str(exc)})
        recorder.error(code, str(exc), native_error_preserved=True)
        stop_reason = "delivery_unknown" if code in ("delivery_unknown", "browser_exited") else "native_error"
        if pending:
            recorder.choice(pending["options"], native_source=pending["source"], native_id=pending["native_id"])
            recorder.append("native/selection-outcomes.jsonl", {"interaction_id": pending["interaction_id"], "native_effect_committed": None,
                "selection_intent_observed": True, "failure_code": code})
    finally:
        try:
            if browser:
                browser.close()
        except Exception as exc:
            errors.append({"code": "browser_cleanup_failed", "message": str(exc)})
            recorder.error("browser_cleanup_failed", str(exc))
        try:
            if handle:
                adapter.close(handle)
        except Exception as exc:
            errors.append({"code": getattr(exc, "code", "cleanup_failed"), "message": str(exc)})
            recorder.error(getattr(exc, "code", "cleanup_failed"), str(exc))
        identity.close()
        recorder.save_json("native/auth-provenance.json", identity.provenance())
        with socket.socket() as probe:
            probe.settimeout(1)
            listener_closed = probe.connect_ex(("127.0.0.1", port)) != 0
        if not listener_closed:
            errors.append({"code": "cleanup_failed", "message": "Owned native listener is still reachable"})
            recorder.error("cleanup_failed", "Owned native listener is still reachable")
        recorder.save_json("native/cleanup.json", {"native_listener_port": port, "native_process_stopped": adapter.process is None and listener_closed,
            "native_listener_closed": listener_closed,
            "browser_process_stopped": browser is None or browser.process.poll() is not None,
            "gateway_inflight_accounting": "root_gateway_drains_all_already_sent_provider_requests", "errors": errors})
    return {"stop_reason": stop_reason, "native_ended": None, "errors": errors,
            "choices_executed": choices_taken, "prefetch_policy": "native_browser_unchanged",
            "render_mode": "offscreen_native", "production_auth_verified": False,
            "native_fallback_status": "inspect_native_text_relay_observations_absence_is_unknown"}
