# External AI4VisualNovel adapter

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

The exporter uses the native parser and stops at the first choice or a conditional/
jump boundary. It never concatenates branches or reconstructs prose from a graph.
This first-phase implementation does not claim complete route replay. Only the
OpenAI-compatible text provider is enabled for instrumented runs; Google and
streaming need separate retry/usage coverage before support can be claimed.

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
