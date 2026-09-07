# Batch implementation contract (implementation coordination, v1)

Native source files remain unchanged. All work in benchmark/; v3 behavior and historical runs remain reproducible. User authorized implementation, final adapter recheck, instructions and repository publication. Shared image model gpt-image-2; preserve original native retries/review/fallback and record failures. Default real text DeepSeek flash, vision gpt-5.4-mini from existing config; model/API keys configurable common fields, never inline secrets.

## Native driver boundary

Each owner creates `story_benchmark/batch_native/{if_line,ai4vn,infiplot}.py` with:

```python
def preflight(config: dict, bundle: Path, policy: dict) -> dict:
    # read-only, no generation; return {ok: bool, errors: list[str], checks: list}

def run(config: dict, bundle: Path, run_dir: Path, recorder, gateway, policy: dict) -> dict:
    # all native processes isolated; must finally stop them and preserve native files
    # return {stop_reason: 'reading_window'|'native_end'|'native_error'|'delivery_unknown',
    #         native_ended: bool|None, errors: list[dict], ...diagnostic metadata}
```

Root executes each run in its own OS process, thus safe global native env/singletons. Drivers own isolated runtime/ports/DB/browser. Driver native artifacts live under run_dir/native, never source tree. Return only after capture/cleanup. Original source lock verification before/after is required.

`config` is system-specific config merged with existing common model/budget settings. Batch is unlimited; four explicit null limits. `policy` contains `window_chars` (positive integer), `choice_indices` (list of zero-based integers; repeat last when exhausted), `reading_delay_seconds` (nonnegative; common simulated reader delay, excluded from model response latency), `evidence_kind` ('live'|'fixture'), `render_mode` ('offscreen_native' default; never claim actual desktop presented).

New common v4 bundle will preserve case identity fields and exact brief/opening, input_contract v3; output_contract version 4.0, scope readable_window, window_chars, media images. profile FULL_VN. Root updates compiler/contract validation; drivers should not modify shared text or require v3 first-choice output contract. First creative request must contain shared task once. Continue native context only, never reinject story requirements.

## Recorder facade (root owns implementation)

`recorder.root`, `recorder.run_id`, `recorder.window_chars`, `recorder.scope_reached`, `recorder.visible_chars`, `recorder.clock_id`.

- `recorder.event(event_type, **fields) -> dict`: observed now with UTC + monotonic_ns, observer defaults adapter_backend; frontend/offscreen observers named explicitly. Do NOT claim presented unless actual renderer surface displayed/observed and observer identifies offscreen vs real UI. Model/collector timestamps distinct.
- `recorder.append(relative_jsonl_path, record: dict) -> dict`: adds root_run_id, preserves original supplied IDs; locked thread-safe append in the root worker. Child processes send observations to this worker. Use spec directories.
- `recorder.save_json(relative_path, data) -> str` / `save_bytes(relative_path, bytes) -> str`: persist and return root-relative path. Sensitive fields/URLs are redacted in JSON, credentials never persisted.
- `recorder.story(text, *, speaker=None, kind='narration', native_source: dict, revision_id: str, native_id=None, trajectory_id='main', source_call_ids=None) -> dict|None`: records actual visible text in order; clips only by common sentence rule at threshold, stores source text offsets and no invented content. Returns segment with segment_id, text, revision_id. This emits story_text_available at adapter backend observation; native available events can additionally be recorded accurately. Stop advancing when scope_reached; do not modify native generated text/files.
- `recorder.choice(options: list[dict], *, selected_index=None, native_source=None, trajectory_id='main', native_id=None) -> dict`: options require id,label, may include raw native data. With selected_index records actual selection only AFTER native operation succeeds; before selection use event choice_available/choice_selected at actual boundary. Use policy index, fail honestly if out of range. No semantic fabrication mapping native menus to C1/C2.
- `recorder.asset(path: Path, *, origin='generated'|'derived'|'library'|'placeholder', output_id=None, parent_asset_id=None, native_source=None, role=None) -> dict`: copy asset by file hash to images/files; link original candidate via output_id or matching gateway output file hash. Keeps file hash vs pixel hash distinct; conversion does not count new candidate.
- `recorder.frame(*, segment_ids: list[str], asset_ids: list[str], clean_path: Path|None, ui_path: Path|None, character_ids=None, native_source=None, capture_method='offscreen_native', placeholder=False) -> dict`: saves captures and mapping, marks missing images explicitly; actual frame_ready event. No synthetic AI artwork.
- `recorder.character(entity_id, version: dict, *, native_source=None, reference_asset_ids=None) -> dict`.
- `recorder.error(code, message, **fields)` append failure evidence.

When same static frame covers many paragraphs, pass full actual interval or record each observed segment mapping to same frame/assets. Character mappings are candidate identities only. Keep early visible revision, not later replacement. Source call IDs unknown -> null, not guessed. Readable sample cutoff differs from native story end.

## Shared model gateway (root owns implementation)

Root creates `gateway` before driver.run. Native clients use `gateway.url(role)` returning OpenAI-compatible base e.g http://127.0.0.1:PORT/text/v1 (`role`: text, vision, image); and `gateway.api_key` (local run-only token, env only). Actual provider keys held inside gateway env memory. `gateway.output_for_hash(file_sha256)` returns candidate linkage when available.

Gateway supports chat/completions JSON + SSE, images/generations JSON and images/edits multipart; persists real request attempts, raw usage and ALL returned image candidates before native processing. Gateway does not retry, cap, rewrite prompt, or fabricate outputs. It sets/verifies role's common model and applies role-appropriate common model controls; do not send thinking to image payloads. It downloads returned candidates to stable local files while retaining candidate records on download failure; provider URL sensitive query removed from evidence only, not live native response. It preserves all responses delivered to native methods. Drivers must route every text/vision/image native client through gateway (and disable old text-only shim blocks for v4 only). Fixtures use local providers; same actual wire recording.

Gateway only knows call role/path; drivers should also preserve native task traces for call->operation linkage. Unknown linkage stays explicit. Root supplies usage/metric aggregation, constraints source references, blinded text evaluator packages, top manifests and seal verification.

## Ownership

Root: batch scheduler/CLI examples, Recorder+metrics, shared gateway, compiler v4/shared profile, overall regression/publish. Agents own only their native driver, native shim changes for their system, associated tests and usage notes under native_shims. Do not change public shared modules without coordination.

## Verification

Each owner tests real native workflow with deterministic local model providers including images, path selection and actual native rendering/offscreen equivalent, >1 independent run where useful. No claim of live paid generation from fixture. Root runs common transport/recording tests, same-input batch concurrency and sealed run recovery. Original project defects remain failures; no retries to cherry-pick success.
