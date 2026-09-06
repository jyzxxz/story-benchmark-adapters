# IF Line external adapter

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
- Explicit experiment budgets cap each native output limit at the common cap and
  add it if native code omits one. Messages are not changed. HTTP attempts share
  a file-locked root call budget. Input size is the Unicode length of the compact
  complete JSON request. Every actual SDK retry is separately observed.
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
  "python_executable": "/path/to/ifline-venv/bin/python",
  "isolated_deployment": true,
  "database_url_env": "IFLINE_BENCH_DB_URL",
  "model_api_key_env": "IFLINE_MODEL_KEY",
  "redis_executable": "/path/to/redis-server",
  "model": "<common-request-model>",
  "model_base_url": "https://provider.example/v1",
  "live": true,
  "chapter_count": 1,
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

The native Script IR v3 stores linear paragraphs and no player choices. The
exporter preserves its display text and JSON pointers, checks chapter source
spans, and exports an empty choice list with that limitation stated. It does
not invent options or claim verified player-reachable branches.
