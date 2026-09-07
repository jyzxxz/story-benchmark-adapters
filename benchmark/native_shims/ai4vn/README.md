# External AI4VisualNovel adapter

For the separate v4 full native image/text batch driver and actual selected
trajectory recording, see [BATCH.md](BATCH.md). The first-choice v3 interface
documented below remains available and unchanged.

Frozen native commit: `0faf120244d175866eea3813f053281f5689ab19`.
No native source files are changed. The former patched experimental checkout is
not used by this implementation and must not be included as the native baseline.

`AI4VNAdapter` requires `repo_path` and verifies the frozen source using either
`expected_commit` (exact baseline, clean tracked Git tree) or the shared
`source_lock` (pristine file hashes, including deployments without nested Git).
For each run it copies only frozen source files into `native/ai4vn/source/`,
without `.git`, local `.env`, or old generated data. Original source and run-copy
hashes are checked before generation, after generation, and on close. No Python
module is written into the native tree; bytecode writes are disabled.

The independent `launcher.py` imports the native `main.py`, attaches an observer
to `resolve_design_inputs`, and calls the original `main()` with the original
`design` then `script` CLI options. The observer records the actual native return
value with `boundary=native_requirements_reader` and returns that same value
unchanged. Both agent and engine paths resolve
to the fresh copy, so failed JSON, expression records and logs remain isolated.
Designer, Producer, Actor and Writer algorithms and prompts are native.

The launcher wraps OpenAI SDK constructors and chat calls in memory. It adds
HTTP request/response observers using the SDK's own `DefaultHttpxClient` and
records automatic SDK retries individually. It does not change messages.
`boundary=sdk` records provide correlation; sum usage from completed
`boundary=http` events only. Missing usage and missing actual model remain null.
API headers are omitted and known credential values are redacted.

Execution budgets are explicit adapter settings, separate from observation:

The default `budget_mode="bounded"` retains the following limits. An explicit
`budget_mode="unlimited"` instead requires all four values (`max_calls`,
`max_output_tokens`, `max_input_chars`, `timeout_seconds`) to be present and JSON
`null`. Missing mode never silently enables unlimited behavior, and a large
numeric sentinel is not accepted as unlimited configuration.

- `max_calls`: flock-protected total HTTP attempts for this root run, including
  SDK retries, shared by the native design/script subprocesses.
- `max_output_tokens`: add OpenAI `max_tokens` if absent; preserve any lower
  native `max_tokens` or `max_completion_tokens` using `min(native, cap)`.
- `max_input_chars`: actual HTTP JSON payload Unicode code points after output
  limits apply, using compact JSON with `ensure_ascii=False`.
- `timeout_seconds`: timeout per native CLI stage; terminate its process group,
  preserve evidence and mark delivery unknown. The shared runner also enforces
  this limit over the whole root run, including preparation and both CLI stages.
  Do not automatically resend.

In unlimited mode the external adapter adds no call-count limit, output-token
cap, full-input length limit, or CLI stage deadline. The subprocess wait uses
`timeout=None`. The child receives `BENCH_BUDGET_MODE=unlimited` and no
`BENCH_MAX_*` values; a parent environment's old limits are not inherited.
The HTTP observer checks the explicit mode before parsing any limit values, so
legacy values cannot accidentally re-enable a cap or cause `int('None')`.
The locked HTTP counter, every attempt/response record, raw output and provider
usage still remain recorded. Unlimited mode never injects a missing
`max_tokens`/`max_completion_tokens`; any limit already present in native SDK
arguments is preserved. Original SDK retries, SDK timeouts and provider-side
context/output/service limits remain in force.

The shared experiment may also set `common.model_parameters`, for example
`{"thinking":{"type":"disabled"}}` for the selected DeepSeek provider.
The adapter passes this object as `BENCH_MODEL_PARAMETERS`; the external HTTP
hook applies the shared `story_benchmark.model_parameters` rule to the actual
outgoing JSON before checking the full input budget and reserving a send.
These provider parameters are recorded in HTTP `request_schema_and_sampling`,
including every SDK retry. They are part of the common experiment configuration,
not a native story-prompt change. The empty default `{}` preserves the native
HTTP body; messages and output schema are not changed by this setting.
DeepSeek documents `thinking.type="disabled"` as its non-thinking mode in the
[official thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/).

Configuration fields:

```json
{
  "repo_path": "/absolute/path/to/pristine/AI4VisualNovel",
  "expected_commit": "0faf120244d175866eea3813f053281f5689ab19",
  "python_executable": "/absolute/path/to/AI4VisualNovel-python",
  "text_provider": "openai",
  "model": "the-frozen-common-model",
  "model_base_url": "https://the-selected-provider/v1",
  "model_parameters": {},
  "api_key_env": "OPENAI_API_KEY",
  "live": false,
  "max_calls": 200,
  "max_output_tokens": 8192,
  "max_input_chars": 200000,
  "timeout_seconds": 1800
}
```

Model, endpoint and budget values above are examples, not an approved experiment.
The shared runner requires explicit live configuration before it sends requests.
For a vendor source snapshot, supply `source_lock` instead of depending on a
nested `.git`. `model_base_url` takes precedence over legacy `base_url`.
Credentials are read from the named environment variable, never from config JSON.

Explicit unlimited fields (combine with the selected model, endpoint and source
configuration above):

```json
{
  "budget_mode": "unlimited",
  "max_calls": null,
  "max_output_tokens": null,
  "max_input_chars": null,
  "timeout_seconds": null
}
```

The exporter uses the native parser and follows only unconditional `<jump>`
instructions present in `story.txt`, matching `game_engine/scenes.py`'s native
automatic node transition. It stops before the first player choice and exports
the complete consecutive option group without choosing an option. It records
`selection_executed=false`, `traversed_node_ids` and `automatic_transitions`; every prose/choice pointer retains
the physical source line and the actual node ID. Missing jump targets and cycles
fail explicitly. File order or story-graph edges never imply a transition.
Conditional tags remain an explicit `unsupported_condition_boundary`; the adapter
does not invent a condition evaluator or claim complete route replay. Only the
OpenAI-compatible text provider is enabled for instrumented runs; Google and
streaming need separate retry/usage coverage before support can be claimed.

For v3 `output_contract.scope=first_unselected_choice` with
`allow_empty_body=true`, a native first choice with no preceding prose is valid:
the adapter returns an empty `segments` array and the original option labels,
without creating a lead-in. Legacy bundles retain the `empty_prose` issue when
there is no prose. Both versions retain the original `segments`, `choices`,
`stop_reason`, `first_choice_reached` and `selection_executed` fields. Additional
observational metadata is:

```json
{
  "export_scope": "first_unselected_choice",
  "native_capability_status": "first_unselected_choice_available",
  "prechoice_body_status": "present",
  "native_step": {
    "native_source": "native/ai4vn/source/data/story.txt",
    "native_pointer": {"node_id": "decision", "parsed_line_index": 1, "line": 8},
    "instruction_type": "choice_start"
  },
  "traversed_node_ids": ["root", "decision"],
  "automatic_transitions": [],
  "selection_executed": false
}
```

`native_step` identifies the last actual instruction observed, not a generated
story stage. `prechoice_body_status` may be `empty_native`.
`native_capability_status` is `boundary_not_reached` when no native choice is
reachable through supported automatic jumps, or `unsupported_output_boundary`
at a conditional instruction. Missing choices always carry
`native_choice_boundary_missing`; missing root also keeps the legacy
`missing_entry_node`, and a blank story adds `native_story_empty`.
An unsupported condition keeps `control_flow_not_executed` and
`unsupported_native_condition_boundary`. These issues do not become successful
first-choice returns. A node with no choice does not imply that the whole native
project has no choice capability.

Later pre-generated node scripts remain internal native artifacts. AI4 does not
expose them as current-path prose or native option-card previews. The first
native option group is exported unchanged; this adapter does not limit a story
graph to two nodes, rename options into C1, or claim semantic equivalence of an
arbitrary pair of options.

The shared continuation task is read once by native `resolve_design_inputs`.
The native outline prompt permits a first group to establish an opening, and
native Producer review includes the full user requirements. The common task must
therefore distinguish internal planning/review from player-visible prose and
state that the supplied opening already happened. This adapter does not rewrite
the native outline/review prompts or replenish the opening in later Writer calls.

Native node-count validation happens inside `Designer.generate_story_graph_from_outline`
before that candidate reaches the workflow's Producer graph-review loop. A graph
with 13 nodes when 12 are required still exits natively; no existing CLI recovery
entry point handles this error. The adapter retains the exit and original logs,
adds `native/ai4vn/native_failure.json` with the observed expected/actual counts,
and reports `native_graph_node_count_mismatch`. It does not retry, delete nodes,
relax the count, or move the validation into a new repair loop. Earlier graph
review, if visible in the native log, is recorded separately from rejection of
the failing candidate.

Completed failures raise `AI4VNError` (a `RuntimeError` subclass) with a stable
`.code`; `NativeGraphNodeCountError` remains available for existing callers.
The root runner may map these codes into its common result status. Classification
does not alter native retries or make additional model requests:

| Code | Observed condition |
| --- | --- |
| `native_graph_node_count_mismatch` | Native Designer's explicit expected/actual count exception. |
| `native_json_parse_error` | Native CLI JSON decoding exception for a nonempty response. |
| `native_empty_model_response` | CLI fails and its final successful HTTP response proves null/empty content. |
| `native_schema_validation_error` | Native CLI reports its schema validation failure. |
| `native_http_error` | CLI fails after a completed provider HTTP error response. |
| `native_exit` | Other observed nonzero native process exit, with no unfinished observed HTTP send. |
| `native_artifact_missing` / `native_artifact_empty` | Required native artifact absent or empty. |
| `native_artifact_parse_error` / `native_artifact_invalid_shape` | Native artifact is invalid UTF-8/JSON or not a JSON object. |
| `native_automatic_jump_cycle` / `native_jump_target_missing` | Native automatic path loops or targets an absent script node. |
| `native_previous_stage_failed` | Refused redispatch of a known failed stage with no retained specific diagnostic. |
| `budget_exhausted` | External call/input cap exception; no repair or resend. |
| `delivery_unknown` | Timeout/interruption or an observed HTTP send without a terminal response record. |
| `native_process_start_failed` | Local subprocess launch failed before a native process ran; adapter error. |
| `native_source_changed` / `prepared_input_changed` | Source/input integrity failed; adapter error. |
| `live_generation_not_enabled` | Generation requested with live disabled; adapter configuration error. |
| `invalid_budget_policy` | Unlimited configuration omits a required null limit or supplies a numeric limit; adapter configuration error. |

SDK error records distinguish `not_sent`, `completed`, and `delivery_unknown`;
for SDK-only errors they additionally use `native_sdk_error`. Known previous
native failures retain their original code when redispatch is refused, rather
than becoming an invented unknown-delivery state. Native CLI logs and raw HTTP
responses remain the evidence; absent response files never prove empty content.

In v3 the common task declares the exact cast count including the player, so the
native `--character-count` mapping follows the common policy. No extra story
reminder, count repair, or constraint reinjection is added by this adapter.

Offline verification (use the project's installed Python dependencies):

```sh
AI4VN_TEST_REPO=/absolute/path/to/pristine/AI4VisualNovel \
AI4VN_TEST_PYTHON=/absolute/path/to/AI4VisualNovel-python \
/absolute/path/to/AI4VisualNovel-python -m unittest discover \
  -s /absolute/path/to/benchmark/tests -p 'test_ai4vn*.py' -v
```

`test_ai4vn_native_flow.py` runs two complete native CLI design/script flows
against a localhost provider with fixed synthetic responses. It exercises all
four native agent types, validates shared-input receipts, real SDK/HTTP hooks,
source maps, usage fields and unchanged source hashes. These results are marked
`evidence_kind=mock` and are not model-quality or paid-generation results.
Set `AI4VN_TEST_ARTIFACT_DIR` to a fresh directory to retain those mock artifacts.
The root-runner fixture additionally checks that shared `thinking` parameters
arrive at every actual localhost HTTP request and appear in trace records.

For the common compiled case, set `BENCH_SHARED_BUNDLE` to its existing bundle
directory and run only `test_ai4vn_native_flow.py`; this executes one native flow
using that exact shared_task.txt and opening.txt. `AI4VN_TEST_SOURCE_LOCK` may
explicitly select the final snapshot lock when the source has no nested Git.
The v3 fixture suite also runs empty and malformed provider-content cases through
the actual native design CLI, checking known failure codes, retained responses,
unchanged sources and refused redispatch. These remain synthetic local tests.
Unlimited tests additionally send 161 actual localhost HTTP requests despite
legacy cap environment values, verify output-cap absence and native-cap
preservation, and exercise complete native design/script generation and cleanup
with null limits. These are engineering checks with fixed responses, not paid
story generation or evidence of unrestricted provider capacity.
