# InfiPlot external input adapter

This page documents the v3 first-choice/text-only entry. The full-image readable-window batch driver, original browser scheduling and its disclosed entry/auth compatibility rules are documented in [BATCH.md](BATCH.md).

Upstream: `https://github.com/zonghaoyuan/infiplot`, frozen at `a60e18bc663caaa134d9323a2b89159b7cc9bd05`.

The original source files are not edited. `story_benchmark.adapters.infiplot.InfiPlotAdapter` creates an independent runtime copy, installs the frozen pnpm lockfile offline, and starts the original Next.js server there. The adapter uses the original authenticated JSON `POST /api/start`. No frontend, authentication guard, history, story state, or story-generation code is replaced.

The frozen implementation directly runs one Writer `<plan> → <story> → <choices>` stream. Its older Architect/two-call comments do not describe the actual pipeline at this SHA. Character/media translators continue to follow the native flow.

## What the external relay changes

The native `TEXT_BASE_URL` configuration points at a loopback relay. The relay's final upstream endpoint comes only from the frozen `config.model_base_url`; `config.model` is assigned to native `TEXT_MODEL` and checked again at the outgoing boundary.

For a Writer request, the relay requires the complete public task exactly once in the actual SDK messages, extracted between `<<<SHARED_TASK_V2_BEGIN>>>` and `<<<SHARED_TASK_V2_END>>>`. It compares that extracted value with the compiled file, then replaces exactly one frozen no-history transition sentence:

```
这是故事的开场。请按【故事档案】里的 nextHook 把第一幕的冷开场设计出来——开场即抓人，别花笔墨铺垫世界观。
```

with:

```
这是本系统的第一次生成，但共同任务中的固定开头已经发生。正文从固定开头末尾继续，不重新设计开场。
```

The public task itself, its original system-message position, all other message content, native planning, native memories, and native response schemas remain intact. Missing/duplicated signatures or changed public text cause rejection before a provider request. `shared_opening=false` disables the sentence replacement; the messages then remain identical.

The common `model_parameters` object is applied to every native text request after the exact message adaptation. For the v2 non-reasoning validation, the shared setting is `{"thinking":{"type":"disabled"}}`; the relay places this at the HTTP payload's top level, including auxiliary character and cinematography calls. It does not modify `messages`. An empty `{}` leaves native model settings untouched. The shared helper validates supported parameters before sending; systems cannot override this common condition independently.

The default `budget_mode="bounded"` policy sets an absent output cap to `max_output_tokens`, preserving any lower native cap. `max_calls` limits actual provider sends for one root run. `max_input_chars` measures Unicode codepoints of the complete compact JSON HTTP payload **after** message adaptation, common model parameter injection, and the output cap. It does not claim equal monetary cost. Request logs disclose the prompt replacement, common parameter changes, and output-cap changes separately.

For a shared experiment with no adapter budget, use these exact common values:

```json
{
  "budget_mode": "unlimited",
  "max_calls": null,
  "max_output_tokens": null,
  "max_input_chars": null,
  "timeout_seconds": null
}
```

Unlimited mode does not inject or lower `max_tokens`/`max_completion_tokens`, does not reject input or calls by an adapter threshold, and does not create a total generation deadline. Existing native cap fields pass through unchanged; an absent native cap stays absent. The relay's provider transport and the original `/api/start` request explicitly use `timeout=None`; response capture waits without a generation deadline. There is no hidden 180-second transport fallback or large numeric substitute for null. Calls, usage, complete-payload character counts and before/after hashes remain recorded.

Native SDK, project and provider limits remain in force; unlimited does not modify their code or guarantee unbounded provider output. Startup (default 120 seconds), dependency installation (180 seconds) and cleanup (10-second graceful/handler waits, followed by the existing process termination procedure) are infrastructure limits, not generation budgets. Readiness probes have a 5-second timeout and never generate a story. User interruption still closes active relay connections and preserves incomplete evidence; it does not add a retry.

`MOCK_IMAGE=true` uses the native image placeholders. Empty server TTS configuration and `clientTts=true` disable server TTS. Image/vision configuration points at a disabled loopback endpoint; failures are not used as a substitute for disabling media. Native text calls by character and cinematography translators are still observed under the same shared budget mode.

## Configuration

Alongside the runner's common settings:

| Field | Meaning |
| --- | --- |
| `repo_path` | Pristine InfiPlot source directory; no Git metadata is required with a source lock. |
| `source_lock` | Aggregate `baseline-lock.json`; verifies every published source file. |
| `expected_commit` | Development checkout alternative; must equal the frozen baseline SHA. |
| `base_url` | Adapter-owned loopback Next.js address, default `http://127.0.0.1:3217`. Existing services are never reused. |
| `model_base_url` | Explicit final text-provider endpoint; distinct from `base_url`. |
| `model` | Exact model sent by all native text requests. |
| `model_parameters` | Shared provider settings, applied to the complete HTTP body; `{}` preserves native settings. |
| `cookie_env` | Environment variable holding the native Supabase session cookie; default `INFIPLOT_COOKIE`. |
| `shared_opening` | Whether to apply the exact transition adaptation; default true in this dedicated adapter. |
| `dependency_timeout_seconds` | Frozen offline installation timeout, default 180. |
| `startup_timeout_seconds` | Native readiness ceiling, default 120. In bounded mode the smaller generation timeout also applies during startup. |
| `keep_runtime` | Retain the temporary source/build directory for diagnostics; default false. |

Real generation requires `live=true`, an explicit shared budget policy, `TEXT_API_KEY`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, and the session cookie environment variable. Unlimited mode requires all four budget fields present and null. The adapter does not disable or bypass authentication. Provider keys and cookies stay in process memory/environment and are omitted or redacted from evidence.

Use Node >=22 and pnpm 9.12.0. Runtime installation runs `pnpm install --frozen-lockfile --offline`; populate the dependency cache in a separate disposable directory if necessary. The native startup command is `pnpm dev --hostname 127.0.0.1 --port <port>` inside the temporary runtime copy.

## Evidence and limitations

- `native/source-before.json`, `source-after.json`, and `source-attestation.json` compare the original source byte hashes before and after the run.
- The runtime copy receives identical source bytes. Only native generated files (`next-env.d.ts`, `tsconfig.tsbuildinfo`) may differ and are listed separately. Unexpected source changes fail attestation and preserve the runtime for inspection.
- `trace/requests/*.before.json` records the unmodified native SDK payload; `*.after.json` records the exact adapted/parameterized/capped provider payload. HTTP records include original, post-prompt, post-model-parameter, and final payload hashes; `model_parameters`/`model_parameter_changes`; changed message index/codepoint offset; requested/actual model; provider request id; finish reason; raw JSON/SSE response; and usage. Missing usage is null. Streaming cumulative usage is not summed repeatedly.
- On a response read failure, `trace/responses/*.partial.bin` retains bytes already read plus `IncompleteRead.partial`. Secret values are redacted before binary storage; original and stored hashes, observed byte count, redaction status, HTTP status, and request id are recorded. The call remains an error with `response_complete=false`, `delivery_status=delivery_unknown`, and unknown usage. A truncated declared Content-Length is also rejected. Partial evidence never becomes a successful response and adds no retry.
- `trace/received-*.json` is explicitly a **`native_sdk_task_block`** observation. Its text is extracted from the real SDK request, not copied from the bundle. It is not a direct observation of the native route's decoded `worldSetting` field; `direct_native_route_receiver_observed=false` remains explicit.
- SDK requests received but rejected by a model/budget check retain their unmodified `before` payload and, when extraction succeeds, a receiver record. These are observations of native ingress, not provider sends; only HTTP `started` events count against actual sends. `after` is a prepared payload and is not proof that it was sent.
- Native fallback/degradation console signatures can establish a positive observation. Their absence does not prove absence of native fallback. Normal-looking exports remain `generated_unreviewed_fallback_unknown` unless stronger evidence exists. All other console output is discarded to prevent credential leakage.
- `native/start-response.json` retains the complete native response. Export walks native `entryBeatId`/continue edges to the first choice and applies the actual PlayCanvas visibility rule. It does not use `scenePrompt`, internal memory, or other branches to fill output.
- The first choice beat's visible narration/dialogue and **every native choice** are exported in native order. Export never follows a choice effect, even an `advance-beat` target within the same returned scene. An empty first choice remains an empty-choice issue; the adapter does not skip ahead. This mechanical boundary does not prove that the Writer produced the task's required C1: incorrect or missing C1 options remain a content failure.
- For `case.output_contract.version="3.0"` with `allow_empty_body=true`, a native first-choice menu with zero visible preceding text is allowed. No summary, memory, prompt text, or later branch fills that empty body. Empty menus remain `empty_choice_boundary`; malformed graphs have native artifact error codes. Earlier contracts retain their `empty_prose` diagnostic. Export metadata always records `selection_executed=false` because the adapter never selects a branch.
- `previews=[]`: frozen PlayCanvas shows `choice.label` in its choice buttons, not `effect.nextSceneSeed`. That seed remains internal native metadata with the choice's source pointer; it is never exported as player-visible preview or prose.
- Before the adapter receives a scene, frozen native `director.ts` filters the Writer's `<choices>` to nonempty `change-scene` options and deduplicates labels. The raw Writer response remains in the trace, so compare that response with `native/start-response.json` to locate any dropped option. The adapter does not restore rejected options or select a replacement path.
- `native/choice-provenance.json` is a read-only sidecar written before evidence sealing. With one completed Writer and a strict-JSON `<choices>` block, it compares the raw choices with the exported native boundary and records whether the frozen filter/id/dedup rules exactly explain the result. Predicted removals are only marked confirmed on an exact match. Multiple completed Writers, loose JSON requiring native repair, or missing evidence stay `unknown`; a mismatch requires review. This is not a semantic C1 correctness test and never changes the actual export.
- Streaming observation uses only each chunk's first provider choice, matching frozen `lib/ai-client/chat.ts`; it never concatenates alternative provider candidates into the accepted Writer text.
- The relay still leaves native bible initialization and empty archive/character records in place. The common task must define fixed-opening facts and how to interpret initially empty records; the adapter adds no history or memory. Native pacing (about 1500–2500 characters, 5–8 beats) and 2–3 scene-exit choices remain native method requirements. A C1-only task restricts player-visible continuation, while native planning/memory and unexecuted choice-effect metadata remain preserved evidence.
- A durable `delivery-started.json` prevents silent resending after timeout or interruption. A saved valid response can be exported again. The relay preserves native streaming fallback attempts; it adds no retry.
- Closing terminates the adapter's process group and relay, records source attestations, and removes only the disposable runtime once its source matches. Native responses and trace evidence remain.
- Relay cleanup interrupts its tracked upstream connections, then waits for handlers to finish. Even if a handler cannot stop within the cleanup bound, later evidence writes are disabled and `cleanup_failed` is reported; success is not claimed. Runtime deletion requires successful cleanup and source attestation. Nonexcluded source-directory symlinks are rejected, as are source-file symlinks.
- This is phase-one engineering integration. No branch replay, story-quality evaluation, or formal benchmark result is claimed.

## Failure contract

`InfiPlotError.code` identifies failure origin. No failure causes an adapter story repair or automatic resend. The raw native successful HTTP response is saved before JSON/graph validation, so a malformed artifact remains inspectable.

| Codes | Meaning |
| --- | --- |
| `invalid_response`, `missing_scene`, `missing_entry`, `invalid_beat`, `duplicate_beat_id`, `missing_beat`, `beat_cycle`, `invalid_next`, `invalid_prose`, `invalid_choices`, `invalid_choice` | Native artifact shape/graph failure; exported as a native failure, not an adapter implementation failure. The module publishes `NATIVE_ARTIFACT_ERROR_CODES` for the common classifier. |
| `empty_choice_boundary`, `empty_prose` | Native generation issue codes attached to the preserved export, not thrown exceptions. `empty_prose` is suppressed only when the v3 contract allows an empty body and native choices exist. |
| `native_http_error` | Native `/api/start` returned non-auth HTTP error. `native/http-error.json` records status, safely redacted native JSON error, completeness, original byte count/hash and stored text hash; `http-error-response.txt` preserves safe UTF-8 text. These are not invented provider errors. |
| `auth_failed` | Native readiness or start rejected the supplied identity; production account verification is distinct from the local identity fixture. |
| `delivery_unknown` | Start was attempted but its completion is unknown, including incomplete native HTTP response. Partial failure evidence cannot become a successful artifact. |
| `preflight_failed`, `port_in_use`, `dependency_install_failed`, `server_exited`, `startup_timeout`, `server_config_error`, `tts_not_disabled`, `missing_auth`, `live_disabled`, `source_drift`, `cleanup_failed` | Adapter/runtime configuration, startup or cleanup issue. Preserve evidence and resolve the named condition before an explicitly authorized new run. |

Native error evidence replaces invalid UTF-8 bytes and redacts credentials explicitly; it records the original response hash so this representation is not mistaken for an exact raw byte copy. Failed HTTP body capture retains the known HTTP status and records incomplete evidence.

The v3 exact declared cast is supplied only by the common task. The adapter adds no actor count, character prompt, artificial history or story reminder. Native empty archives, pacing, planning rules and the general prohibition on player psychology versus the format's allowance for inner monologue remain unchanged native behavior; their scope ambiguity is not resolved by the adapter.

## Validation

Default offline suite:

```bash
python3 -m unittest discover -s tests -p 'test_infiplot*.py' -v
```

This covers exact/default-off adaptation, untouched auxiliary messages/schema, shared non-reasoning parameters on real local HTTP sends, empty-parameter defaults, full-payload budget accounting, rejected signature/input drift, JSON/SSE forwarding, partial chunked/Content-Length response evidence, usage, missing usage, budgets, model mismatch, native graph/visibility export, and ambiguous-delivery no-resend behavior. The expensive native route fixture is opt-in. These tests use local providers only and do not establish real-vendor v2 results.

Unlimited regressions send 161 actual loopback provider requests, preserve native caps above 16,384, pass inputs above 200,000 characters and verify both transport timeout arguments are `None`. They use local fixed responses only. To run the original authenticated native-route fixture in this mode, add `INFIPLOT_TEST_BUDGET_MODE=unlimited` to the command below; omit it for the existing bounded fixture.

To test the actual original route with only local auth/provider fixtures, a published no-Git source snapshot, and the shared compiled case:

```bash
INFIPLOT_ROUTE_TEST=1 \
INFIPLOT_REPO=/absolute/path/systems/infiplot \
INFIPLOT_SOURCE_LOCK=/absolute/path/baseline-lock.json \
BENCH_SHARED_BUNDLE=/absolute/path/benchmark/examples/CAMPUS-01-V3 \
BENCH_EVIDENCE_DIR=/absolute/path/new-evidence-directory \
python3 -m unittest discover -s tests -p test_infiplot_native_route.py -v
```

Use a new evidence directory for every execution. This verifies actual 401/200 native authentication/routing, SDK JSON/SSE transport, precise common input propagation, the common non-reasoning parameter, native placeholder behavior, visible output, source hashes, and process cleanup. Text, auth, and usage values are local test fixtures, explicitly labelled; the fixture's story is not rated for compliance with the public story task. It never evaluates real model quality, verifies a production Supabase account, or bills a model provider.
