# IF Line external adapter

## Image-enabled batch driver (v4)

`story_benchmark.batch_native.if_line` implements the common batch driver contract.
It requires a v4 `readable_window` bundle and explicit unlimited resource policy.
The common text is delivered once to the first Bible creative request, as in v3.
No later candidate instructions contain excerpts, reminders or extra story text.

The actual chain is native Bible → native 13-chapter total Outline → exact manual
opening import → structural empty-state checkpoint → native two-candidate set →
native candidate-head activation → native candidate-to-StoryPath promotion.
After each promotion, the native child path receives its own native Outline and
the next ungenerated chapter. Chapter generation, Script IR segmentation and
coverage checks, resource planning, portrait/background/keyframe renderers,
native image review/retries, resource binding, and VNGraph compilation remain
native. After a generated chapter, an external structural checkpoint identifies
that exact selected revision and reuses `StoryPath.base_state_snapshot_id`.
It does not extract facts, invent a memory state, or repair native planning.

The driver selects the configured zero-based native option index, repeating the
last index when exhausted, then records the successful promotion receipt. This
is **native authoring path promotion**, not a published-release reading-session
`choose` request. Choices are offered at the imported opening and each subsequent
completed chapter while the common reading window remains unmet. The driver
also executes any choices encountered in the actual compiled native player.
Authoring promotions and player choices share one cumulative policy index.
Player options preserve their native array index, text and target; original
renderer clock/timestamps remain separate from later backend collection time.
Menu-only/empty-text beats still have frames, and the native “（请选择）” UI
marker is excluded from prose counts. Native revisits with story text are
observed again until the common window is reached; no route-count cap is added.
The driver
does not invent a semantic mapping from a generated menu to C1/C2. Candidate
previews remain native option data; they are never appended as current prose.
Native Outline/content deviations remain outputs to evaluate.

The frozen `VNGraphPlayer.vue`, `VisualNovelStage.vue` and `vnGraphPlayer.ts` are
read directly and compiled into an external harness. Headless Chromium renders
the original player, advances actual beats, and captures clean/UI frames. Clean
capture hides dialogue/controls/debug only; original composition, CSS, image
layers and native placeholder behavior remain intact. `offscreen_native` is
explicit; these are not observations of a human's foreground screen. Every
observed paragraph maps to the actual beat, immutable graph/Script IR/chapter,
native asset versions, and original image candidate where linkage is known.
Every paragraph identifies its native Script IR task/revision and that task's
recorded text HTTP attempts. This is task association, not an assertion about
which draft/rewrite won. Its availability anchor is the received Script IR
revision; the earlier chapter-available observation is diagnostic only.
The common Recorder clips only exported text at the first sentence boundary
at/after `window_chars`; the native beat/revision and full screenshot remain
unchanged. No additional native choice is executed after that observed boundary.
Audio is disabled because this batch scope is text plus images.

Each independent run starts its own temporary PostgreSQL cluster/database,
Redis process, API, solo Celery worker and beat. Queues are
`text,image,compile,maintenance`. Source writes remain blocked. Media objects,
images, logs, bytecode/cache settings and browser artifacts stay outside systems/.
The driver stops its own services in `finally`, retains PG/native logs, and
verifies all source hashes before/after. Native SDK/request timeouts and service
startup/cleanup watchdogs remain; there is no adapter generation/call/token/input
deadline or cap. Local parallelism runs separate OS processes and databases.

Additional IF configuration:

- `python_executable`: isolated Python with `requirements-media.txt` installed.
- `node_executable`, `node_modules`: external Node executable and tool modules.
- `rembg_model_dir`: prepopulated native `u2net.onnx` directory; preflight refuses
  a missing model, so model downloads do not happen during paid generation.
- `pg_bin`: directory containing `initdb`, `pg_ctl`, `createdb` (default `/opt/homebrew/bin`).
- `redis_executable`: isolated Redis executable (default PATH lookup).
- `image_model`, `vision_model`: common model names; endpoint/key values come
  exclusively from the run gateway's image/vision/text routes.
- `chapter_count`: total native planning length (default original UI's 13).

Install Node media tools in a separate folder by copying `renderer-package.json`
there as `package.json` and running `npm install --prefix <tools-directory>`.
Use `PLAYWRIGHT_BROWSERS_PATH=<external-browser-cache>` when installing/running
Chromium; avoid automatic cleanup of a different project's shared browser cache.
Never run dependency installation inside the frozen system source.

The worker preserves extra `native-image-writes.jsonl` and
`native-media-results.jsonl` records before asset registration. They link native
normalization/alpha processing to provider candidate bytes; transformed files
are `derived` assets, never additional candidates. The gateway retains every
provider candidate and real attempt, including those discarded by native code.
Each native image response binds the gateway call ID and the native selected
`data[0]` candidate. This identity survives the native asyncio/thread boundaries;
equal pixels from separate candidates are not merged. Explicit native cache hits
can reuse the prior receipt for that same cache file. A unique-hash fallback is
labelled and cannot resolve ambiguous equal-pixel candidates. The referenced
image path's native aiohttp request is observed at the same gateway as SDK calls.
Gateway accounting is authoritative; shim records use `boundary=gateway_client`
to avoid counting the same request twice. Native asset credits are native
application accounting, not measured provider currency charges.

V4 trace responses and, after native services stop, generated JSON/JSONL/log/text
evidence use the common `recording.redact_evidence` policy. Signed retrieval URL
queries are removed from saved evidence while the live native downloader gets
the original response. Image bytes and every frozen-source file are untouched;
the redaction report lists changed generated files before the run is sealed.
Legacy v3 tracing does not opt into this media evidence policy.

V4 also removes the disposable application's daily/total quota gate. Upstream
has no credit-grant service: `usage_service.reserve_usage/reopen_usage` uses
`User.quota_total/quota_daily` as compatibility counters. Only in the loopback
`if_line_bench_*` deployment, an external wrapper raises an insufficient counter
by the exact native request reservation units, then calls the original service.
It neither uses a huge ceiling nor changes used counters/reservations/settlement.
`internal-credit-topups.jsonl` records the native amount source, task/user, before
and after counters, transactional scope, and `provider_currency_amount: null`.
`native/if_line_quota_final.json` retains the actual committed native quota and
usage ledger. This is a test deployment quota adjustment, not provider recharge.

Local multimodal verification is opt-in via `IFLINE_BATCH_E2E=1` and
`test_if_line_batch.py`. Evidence output must be new; old runs are never merged.
The fixture may use a short reading window to exercise mechanics, so it is not
reported as a paid common 4000-character story experiment. In live mode bundle
and policy reading windows must match. Native failures remain failures: for
example, the current upstream keyframe semantic retry can raise
`KeyError('validation_results')` after replacing its result; this is retained,
not patched or bypassed by the driver.

Verified locally on 2026-09-07: IF unittest discovery ran 35 tests (31 passed,
4 opt-in skips), existing HTTP/trace pytest ran 18 passed (1.68 seconds).
The additional opt-in boundary suite passed all 6 tests, including the actual
Vue player performing two native choices with a story revisit, five frame pairs
(two menu-only) and one cumulative choice-policy index. The final opt-in batch
suite passed 5 tests including actual isolated PostgreSQL/Redis/Celery/Vue
workflow in 41.414 seconds. Its finite signed-URL/equal-candidate fixture produced
19 localhost calls (13 text, 3 image, 3 vision), 6 provider candidates,
3 original plus 3 derived
assets, two successful choices `[0,1]`, 3 clean/UI frame pairs and exactly 20
sample characters. All 1200 source files were unchanged; all calls had native
task IDs and all recorded assets had an output or parent link. Actual native
candidate index 0 was retained despite duplicated pixels; saved JSON/JSONL/log/text
files contained neither fixture URL signing value. Those numbers
describe engineering fixture evidence only, not a paid 4000-character result.

## Historical v3 text adapter and shared runtime foundation

The following text-only entry points and bounded-mode examples describe v3.
The v4 batch entry above always uses the common unlimited policy and images.

The source is the immutable upstream revision `572407fce9b648a4206ac37da6a9f6ed22631da8`.
No native source file is edited. API, worker and beat load the same external bootstrap.
Python bytecode is disabled, a Python audit hook rejects writes beneath the native
source tree, and every process records source fingerprints before and after use.
The published source-lock works without a per-system `.git` directory.

The adapter changes only these explicit runtime rules:

- The Bible prompt's **基础输入信息** block contains the exact `extra_requirements`
  task. Its other instructions/schema remain native. The mode is carried in the
  native task's `parameters.benchmark_input_mode=shared_task`; Bible `instructions`
  must be absent. With that mode absent, the prompt is byte-for-byte native.
- The native worker dispatcher exposes task/project/stage context to observation.
  Native source-hash checks, leases, task result refs, revision heads and ETags
  remain in force. The adapter selects generated artifacts by that task's refs.
- In the default `budget_mode: "bounded"`, experiment budgets cap each native output limit at the common cap and
  add it if native code omits one. Messages are not changed. HTTP attempts share
  a file-locked root call budget. Input size is the Unicode length of the compact
  complete JSON request. Every actual SDK retry is separately observed.
- Explicit `budget_mode: "unlimited"` removes those adapter caps and the adapter's
  generation/task-poll deadline. Its four budget fields are all JSON `null`.
  Native output parameters stay unchanged or absent; no large substitute limit
  is used. HTTP attempts and usage are still recorded.
- The shared `model_parameters` object is applied to the final provider JSON,
  before the input-size budget check. For example, `thinking.type=disabled`
  reaches the provider despite the old native SDK lacking that keyword. Empty
  parameters preserve the original request bytes; messages are unchanged.
- Async HTTP clients use zero keepalive connections. Native workers repeatedly
  call `asyncio.run` while caching clients; reusing an old connection can send a
  request and then fail with `Event loop is closed`. The external transport
  setting prevents that reuse while preserving native SDK retries and all
  model/prompt parameters. Receipts and traces record the setting. Transport
  errors preserve their real exception type and unknown delivery, never zero
  usage or an invented assertion that the request was not sent.
- Native logs, TTS/cache directories and static paths point to the run directory.
  TTS is disabled and the text adapter never requests media rendering. The native
  visual-style **text** classifier used by Script IR resource planning is retained
  and uses the common model/endpoint.

`trace.py` records actual wire requests and response data, including streaming,
provider IDs, actual model (null when absent), and real usage (null when absent).
No zero-token placeholders or native reserved credit amounts are treated as
supplier usage/cost. Headers are not persisted; credential-valued fields, known
sensitive environment values, bearer strings, cookies and credential URL query
parameters are redacted. Streaming usage is counted once rather than summed per
chunk. Responses lost before shutdown become `delivery_unknown`.

## Dependency environment

Create the dependency environment outside `systems/if_line` and install
`native_shims/if_line/requirements-text.txt`. Tests additionally need pytest and
pytest-asyncio. These are text-runtime dependencies, not a complete media install.
Python 3.12, OpenAI 1.6.1 and HTTPX 0.25.2 were used for the engineering checks.

## Managed actual native services

Use the root runner with this IF Line configuration (absolute paths or paths
resolved by the root runner). `prepare()` starts services only after the runner
creates the fresh run directory, so no prior receipt or occupied run directory
is required.

```json
{
  "repo_path": "/path/to/systems/if_line",
  "source_lock": "/path/to/baseline-lock.json",
  "managed_runtime": "native_services",
  "entry_mode": "shared_first_choice",
  "budget_mode": "bounded",
  "python_executable": "/path/to/ifline-venv/bin/python",
  "isolated_deployment": true,
  "database_url_env": "IFLINE_BENCH_DB_URL",
  "model_api_key_env": "IFLINE_MODEL_KEY",
  "redis_executable": "/path/to/redis-server",
  "model": "<common-request-model>",
  "model_base_url": "https://provider.example/v1",
  "live": true,
  "chapter_count": 13,
  "model_parameters": {"thinking": {"type": "disabled"}},
  "timeout_seconds": 600,
  "max_calls": 12,
  "max_output_tokens": 32768,
  "max_input_chars": 100000
}
```

Set `IFLINE_BENCH_DB_URL` only to an isolated PostgreSQL database named
`if_line_bench_*`, for example a fresh loopback-only PostgreSQL cluster. Set
`IFLINE_MODEL_KEY` privately in the environment. Neither value belongs in the
configuration, task, handle, Git repository or output report.

### Unlimited adapter budget

To run without adapter budget limits, use this exact policy in the shared
configuration. All four null fields must be present; a non-null leftover cap is
rejected before generation.

```json
{
  "budget_mode": "unlimited",
  "max_calls": null,
  "max_output_tokens": null,
  "max_input_chars": null,
  "timeout_seconds": null
}
```

The API and worker receive `BENCH_BUDGET_MODE=unlimited`. The launcher removes
every inherited `BENCH_MAX_*` variable and `BENCH_TIMEOUT_SECONDS` before native
startup. The worker receipt confirms the mode, four null values and the absence
of active adapter-cap environment variables. A bounded trace directory cannot
be resumed as unlimited or the reverse.

In this mode, task polling has no 900-second default or other generation deadline.
The adapter does not add `max_tokens`, reduce an existing native token limit,
reject a request for input length, or stop after a fixed number of calls. Native
SDK/request timeouts, retries, context/schema constraints and original output
settings remain native. For example, the frozen branch provider still requests
`max_tokens=6000` with its original 90-second request timeout. These are original
method settings, not an experiment budget supplied by the adapter.

Separate service watchdogs remain: one local authoring API request may take up
to 60 seconds, managed API/worker readiness up to 60 seconds, and shutdown waits
up to 40 seconds for the managed parent or 10 seconds per child before forced
termination, with a further 5-second kill wait. They detect an unresponsive
service or close owned processes; they do not limit how long a healthy native
generation task may remain running. Bounded mode can shorten the API/readiness
watchdogs to its configured timeout. These values are recorded separately in
the worker receipt.

The output scope is still the first unselected choice. Unlimited budget changes
resource limits, not the requested story boundary or the shared prompt. The
counter file records actual request ordinals and retry attempts; unlimited mode
sets `reserved_output_tokens` to null because it makes no output reservation.

`chapter_count` is the total planned outline length, not the number of chapters
generated by `generate_first_artifact`. Its default is 13, matching the frozen
native new-project UI; the native API itself requires an explicit value. The
legacy `first_chapter` mode generates only the first chapter. V3 imports the
provided prefix and generates unselected native candidates. Setting the total to 1 invokes
native final-chapter rules that force a complete ending, so it must not be used
to mean “first chapter only.” This scope correction does not guarantee that the
native planning stages preserve every shared opening fact or player choice.

An empty database is initialized with native `alembic upgrade head`. A nonempty
one must already pass the native schema-version check; the launcher does not
upgrade arbitrary existing data. The launcher starts a fresh Redis process on
an unused loopback port when `redis_executable` is provided. An explicit isolated
`redis_url_env` is also supported. API, Celery `--pool=solo --concurrency=1` worker,
and Celery beat/outbox use the same deployment. The adapter waits for the worker
receipt and uses a real native `/api/auth/guest` session (`sid` Cookie).

`close()` shuts down only the processes it owns and verifies source equality.
Project creation has no native idempotency key: an uncertain creation response
is not retried. Generation requests reuse stable native idempotency keys.
Activation recovery checks the target head, using native `If-Match` locks.
After a managed deployment closes, reopening an ambiguous run requires explicit
session reconciliation; artifact-only export can resume without generation.

For independently managed isolated services the external launcher also has
`api`, `worker` and `beat` modes. They require the same explicit runtime/model/
database/broker configuration. An existing native session can be supplied via
`auth_cookie_env` (default `IFLINE_BENCH_SID`); the credential is never saved in a
handle. Managed services are the recommended route with the root runner.

## Engineering mode and actual checks

`managed_runtime=engineering_fixed_response` creates an isolated SQLite database,
real native API/sid session, and a serial thread invoking the native worker task.
Its provider is a localhost deterministic fixture. SQLite schema setup uses
native ORM metadata plus Alembic stamp and is not a migration/broker test.
The root runner must label these outputs as fixture evidence.

The separate `test_if_line_native_services.py` test creates a disposable PG16
cluster, uses actual native migrations, and runs the real Redis/Celery worker/
beat path while keeping the provider a localhost fixed-response server. Both
modes passed on the published snapshot and the shared CAMPUS-01 bundle. The
complete first-artifact path makes **5** HTTP model calls, including native
visual-style text classification. Repeating the completed operation adds no
calls. Source fingerprints remained identical. Fixture prose has no quality
meaning and is not a formal story-generation result.

From the benchmark directory, using the isolated Python environment:

```bash
python -m unittest discover -s tests -p test_if_line.py -v
python -m pytest -q tests/test_if_line_trace.py
```

Set `IFLINE_PRISTINE_REPO`, `IFLINE_SOURCE_LOCK`, `IFLINE_PYTHON`, and
`BENCH_SHARED_BUNDLE`, then select a separate, nonexistent evidence directory
for each execution. Existing evidence directories are rejected before any
service starts; reruns never merge traces or overwrite earlier evidence.

```bash
IFLINE_ENGINEERING_EVIDENCE_DIR=/path/to/new-external-evidence python -m unittest discover -s tests -p test_if_line_external.py -v
IFLINE_ENGINEERING_EVIDENCE_DIR=/path/to/new-native-evidence IFLINE_NATIVE_SERVICES_TEST=1 python -m unittest discover -s tests -p test_if_line_native_services.py -v
```

The latter also requires `initdb`, `pg_ctl`, and `createdb` (directory selected
with `IFLINE_PG_BIN`) plus `redis-server`. It starts and stops its own cluster;
it never connects to the user's daily project database.

The native-services fixture also accepts `IFLINE_BUDGET_MODE=unlimited` with
`IFLINE_ENTRY_MODE=shared_first_choice` and the v3 bundle. It checks real API →
PostgreSQL/Redis/Celery → localhost provider delivery, removal of inherited caps,
null receipts, unchanged native branch `max_tokens=6000`, and source fingerprints.
`test_if_line_unlimited.py` separately verifies the absence of a task-poll deadline
and rejection of ambiguous null/non-null configuration. Trace tests exercise
requests with no output limit, native limits and input exceeding stale caps;
these remain fixed-response engineering checks with no paid generation.

The native Script IR v3 stores linear paragraphs and no player choices. The
exporter preserves its display text and JSON pointers, checks chapter source
spans, and exports an empty choice list with that limitation stated. It does
not invent options or claim verified player-reachable branches.
## V3: shared input to the first unselected choice

Use `entry_mode: "shared_first_choice"` with a v3 bundle such as
`examples/CAMPUS-01-V3`. Preflight rejects a v3 case with either legacy entry mode.
The adapter sends the complete shared task to the first creative request once.
Subsequent candidate API requests **omit `instructions`**; the native service
records an empty string and supplies it unchanged to its provider. No C1 clause,
choice text, or scope reminder is extracted and appended later. Only
`output_contract.choice_count` is mapped to the native `candidate_count` field.
`native/candidate_input_mapping.json` records that count's exact source pointer.

The required native dependency chain remains Bible → outline → manual opening
revision → structural checkpoint/empty state → native candidates. The exact
provided opening reaches the branch request once, through `chapter_tail`.
The adapter cannot skip the native outline: candidate source validation requires
its active revision and chapter hash, and manual chapter creation also resolves
Bible/outline context. Regenerating an outline after importing the opening does
not solve this automatically; the native outline-source builder does not load
chapter prose. No plan is silently removed, replaced or repaired by an extra AI.

`native/native_context.json` preserves the generation order, revision/task IDs,
full outline and actual branch-input pointers. It explicitly reports semantic
consistency as `not_evaluated`. Native source checks establish revision identity,
not agreement between the outline, Bible and opening. Any story contradiction
in those native artifacts remains available for content evaluation.

The normalized output uses each native candidate's `option_key` as its `label`,
matching the frozen UI's candidate-card title. Its source pointer targets that
exact field. Future `preview_text` is stored only in `unselected_previews`, with
the same candidate ID as `choice_id`; it is never current-path prose. The current
body may be empty, as the shared v3 contract permits. The adapter stops with
`stop_reason: first_choice` and `selection_executed: false`; no candidate is
promoted and no state delta is applied. A structurally supported choice response
does not establish that its content correctly realizes C1 or follows the opening.

The opt-in native-services test accepts `IFLINE_ENTRY_MODE=shared_first_choice`
and the v3 bundle. It verifies actual API-body omission, decoded HTTP branch
instructions, one opening occurrence, empty state, native source pointers,
unselected status, response-loss recovery and source fingerprints. It uses a
local fixed provider with real isolated PostgreSQL/Redis/Celery services; it
does not evaluate generated story quality.

## Legacy provided prefix and native candidates

Set the explicit IF Line config `entry_mode: "provided_prefix_candidates"` to
use the frozen native authoring candidate pipeline. The default remains
`first_chapter`; this option does not replace the native prompts or algorithms.
Use the compiled `examples/CAMPUS-01-C1` bundle (or a bundle with the same declared
first-choice boundary and two first-decision options).

The chain is native Bible generation and activation, native 13-chapter outline
generation and activation, native manual revision import of exact `opening.txt`
into the first chapter placement, native head activation, an **external structural
checkpoint import**, and native candidate generation with count 2. It stops after
fetching the native candidate records. It does not generate a new chapter, promote
a candidate, choose an option, publish a release, or apply candidate state changes.

The checkpoint creation route is supplied by the external launcher, not by the
frozen repository. It requires the normal native session cookie and chapter
ownership, checks the exact selected manual revision and content hash, and uses
the native ORM only to register a checkpoint tied to that revision. Its payload
contains structural IDs and hashes, without a duplicate opening. The existing
native `_snapshot` service supplies an empty `{}` state. The adapter does not
infer character knowledge, inventory or other story state. The native candidate
service then applies all of its normal source/hash/head checks. Idempotent
checkpoint initialization uses a stable key and a deterministic UUID; a mismatch
is rejected. A lost manual-import response can only recover a unique exact native
revision; it cannot blindly create a replacement.

For historical reproduction only, candidate `instructions` is a JSON serialization of `case.decisions[0]` and
the verbatim `scope_map["第一次选择的行动顺序"]`. The input mapping file records the case
file hash and exact JSON pointers. This selective scope copy was found to conflict
with native internal preview generation; v3 removes it. The original shared task
still enters the first creative request once. The native branch context receives
the provided opening once in `chapter_tail`; openings over the native 8,000-char
tail limit are rejected.

`native/provided_prefix_revision.json` and `provided_prefix_import.json` identify
the imported text with `generation_task_id: null`. `native/candidates_task.json`
preserves the actual frozen candidate source, and `native/candidate_previews.json`
contains the exact native `option_key`, `preview_text` and `state_delta` records.
`provided_prefix_after_candidates.json` proves the original head, empty state,
single root path and zero choice decisions stayed unchanged. A separate branch
worker observation wrapper records `stage: branch.candidates.generate` and the
native task ID while retaining its existing source validation, leases and retry
behavior. HTTP/model/budget rules are unchanged.

The legacy output contract is deliberately explicit: `segments` is empty, the native
preview text is only in `unselected_previews`, and native choices retain
`label: null` and `selected: false`. The frozen native schema defines previews as
post-choice prose, and the old benchmark required a separate action label while
discarding the native `option_key` card title. Consequently this legacy mode
reports `native_capability_status: unsupported_output_boundary` and
`stop_reason: unselected_candidates`; generating candidates is not evidence of
new player-visible pre-choice prose or a complete C1 menu. Neither supplied text
nor alternative outcomes are counted as newly generated current-path text.

Local verification uses the existing PostgreSQL/Redis/Celery fixture test with
`IFLINE_ENTRY_MODE=provided_prefix_candidates`; the evidence directory must not
already exist. The corresponding first-chapter regression uses
`IFLINE_ENTRY_MODE=first_chapter`. Both are engineering tests with localhost
fixed responses, not real-model story-quality evidence.
