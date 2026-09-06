# InfiPlot external input adapter

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

The independent budget policy sets an absent output cap to `max_output_tokens`, preserving any lower native cap. `max_calls` limits actual provider sends for one root run. `max_input_chars` measures Unicode codepoints of the complete compact JSON HTTP payload **after** adaptation and the output cap. It does not claim equal monetary cost. Request logs disclose both the prompt replacement and sampling changes.

`MOCK_IMAGE=true` uses the native image placeholders. Empty server TTS configuration and `clientTts=true` disable server TTS. Image/vision configuration points at a disabled loopback endpoint; failures are not used as a substitute for disabling media. Native text calls by character and cinematography translators still consume the same call budget.

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
| `cookie_env` | Environment variable holding the native Supabase session cookie; default `INFIPLOT_COOKIE`. |
| `shared_opening` | Whether to apply the exact transition adaptation; default true in this dedicated adapter. |
| `dependency_timeout_seconds` | Frozen offline installation timeout, default 180. |
| `keep_runtime` | Retain the temporary source/build directory for diagnostics; default false. |

Real generation requires `live=true`, explicit budgets, `TEXT_API_KEY`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, and the session cookie environment variable. The adapter does not disable or bypass authentication. Provider keys and cookies stay in process memory/environment and are omitted or redacted from evidence.

Use Node >=22 and pnpm 9.12.0. Runtime installation runs `pnpm install --frozen-lockfile --offline`; populate the dependency cache in a separate disposable directory if necessary. The native startup command is `pnpm dev --hostname 127.0.0.1 --port <port>` inside the temporary runtime copy.

## Evidence and limitations

- `native/source-before.json`, `source-after.json`, and `source-attestation.json` compare the original source byte hashes before and after the run.
- The runtime copy receives identical source bytes. Only native generated files (`next-env.d.ts`, `tsconfig.tsbuildinfo`) may differ and are listed separately. Unexpected source changes fail attestation and preserve the runtime for inspection.
- `trace/requests/*.before.json` records the unmodified native SDK payload; `*.after.json` records the exact adapted/capped provider payload. HTTP records include hashes, changed message index/codepoint offset, requested/actual model, provider request id, finish reason, raw JSON/SSE response, and usage. Missing usage is null. Streaming cumulative usage is not summed repeatedly.
- `trace/received-*.json` is explicitly a **`native_sdk_task_block`** observation. Its text is extracted from the real SDK request, not copied from the bundle. It is not a direct observation of the native route's decoded `worldSetting` field; `direct_native_route_receiver_observed=false` remains explicit.
- Native fallback/degradation console signatures can establish a positive observation. Their absence does not prove absence of native fallback. Normal-looking exports remain `generated_unreviewed_fallback_unknown` unless stronger evidence exists. All other console output is discarded to prevent credential leakage.
- `native/start-response.json` retains the complete native response. Export walks native `entryBeatId`/continue edges to the first choice and applies the actual PlayCanvas visibility rule. It does not use `scenePrompt`, internal memory, or other branches to fill output.
- A durable `delivery-started.json` prevents silent resending after timeout or interruption. A saved valid response can be exported again. The relay preserves native streaming fallback attempts; it adds no retry.
- Closing terminates the adapter's process group and relay, records source attestations, and removes only the disposable runtime once its source matches. Native responses and trace evidence remain.
- This is phase-one engineering integration. No branch replay, story-quality evaluation, or formal benchmark result is claimed.

## Validation

Default offline suite:

```bash
python3 -m unittest discover -s tests -p 'test_infiplot*.py' -v
```

This covers exact/default-off adaptation, untouched auxiliary messages/schema, rejected signature/input drift, JSON/SSE forwarding, usage, missing usage, budgets, model mismatch, native graph/visibility export, and ambiguous-delivery no-resend behavior. The expensive native route fixture is opt-in.

To test the actual original route with only local auth/provider fixtures, a published no-Git source snapshot, and the shared compiled case:

```bash
INFIPLOT_ROUTE_TEST=1 \
INFIPLOT_REPO=/absolute/path/systems/infiplot \
INFIPLOT_SOURCE_LOCK=/absolute/path/baseline-lock.json \
BENCH_SHARED_BUNDLE=/absolute/path/compiled/CAMPUS-01 \
BENCH_EVIDENCE_DIR=/absolute/path/new-evidence-directory \
python3 -m unittest discover -s tests -p test_infiplot_native_route.py -v
```

This verifies actual 401/200 native authentication/routing, SDK transport, precise common input propagation, native placeholder behavior, visible output, source hashes, and process cleanup. Text, auth, and usage values are local test fixtures, explicitly labelled; it never evaluates real model quality or bills a model provider.
