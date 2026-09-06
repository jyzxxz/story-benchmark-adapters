# StoryPath Cutover and Rollback Runbook

## 1. Scope and release unit

This runbook is mandatory for the one-time replacement of chapter-index
authoring APIs with StoryPath APIs. The database migrations, backend API,
Celery worker, Celery beat process, and `frontend/dist` are one release unit.
Never run old and new members of that unit together.

The API replacement matrix is
`docs/api/story-path-api-migration.md`. The migration verifier is
`backend/scripts/verify_story_path_release_backfill.py`; the final queue gate is
`backend/scripts/check_story_path_cutover.py`.

Required operators:

| Role | Responsibility |
|---|---|
| release owner | freezes the commit and records every check result |
| database owner | creates and validates the PostgreSQL backup |
| runtime owner | controls ingress, systemd, Redis, worker, and beat |
| product owner | verifies one owner workflow and one active public Release |

## 2. Authoritative port map

| Surface | Bind/exposure | Purpose | Production rule |
|---|---:|---|---|
| reverse proxy | `80/tcp`, `443/tcp` | optional public ingress | repository does not configure it; proxy to `60002` |
| FastAPI and production SPA | `0.0.0.0:60002/tcp` | `/api`, health, metrics, and `frontend/dist` | only application port exposed by this repository |
| Vite | `0.0.0.0:5173/tcp` | frontend development server | development only; proxies `/api` and `/static` to `60002` |
| PostgreSQL | `5432/tcp` | durable application data | private network or loopback only |
| Redis DB 0 | `6379/tcp` | Celery broker and task delivery | private network or loopback only |
| Redis DB 1 | `6379/tcp` | optional auth rate limiting | private network or loopback only |
| EmotiVoice API | `127.0.0.1:5010/tcp` to container `8000` | optional legacy sidecar | never expose publicly |
| EmotiVoice UI | `127.0.0.1:5011/tcp` to container `8501` | optional legacy sidecar UI | never expose publicly |
| Celery worker | none | consumes named Redis queues | no inbound listener |
| Celery beat | none | dispatches Outbox and maintenance jobs | no inbound listener |
| model providers | outbound `443/tcp` | LLM, image, and TTS providers | egress only |

Port `8002` is not an application port. `start_tts.sh` delegates to
`backend/start.sh` and therefore uses `PORT`, defaulting to `60002`.

## 3. Release artifacts and preflight

Set immutable release metadata and keep all generated evidence outside the Git
worktree:

```bash
export RELEASE_COMMIT="$(git rev-parse HEAD)"
export AUDIT_DIR="/var/backups/if-line/story-path-${RELEASE_COMMIT}"
install -d -m 0700 "$AUDIT_DIR"
git status --short --branch
git show --no-patch --format=fuller "$RELEASE_COMMIT"
```

Build and validate the exact frontend artifact before maintenance starts:

```bash
cd frontend
npm ci
npm run type-check
npm run test:unit
npm run build
cd ../backend
venv/bin/python -m pytest -q \
  tests/test_story_path_cutover.py \
  tests/test_story_path_release_backfill_verification.py \
  tests/test_api_route_contracts.py \
  tests/test_deployment_scripts.py
```

Record checksums for the commit, migration files, backend dependency lock input,
and built SPA. Keep the previous backend package and previous `frontend/dist`
available under immutable release names.

The database must be exactly at `0035_script_resource_slot_lock` before the
baseline is captured. If it is earlier, apply only the reviewed additive
migrations through 0035 in a separate maintenance step. If it is later, stop:
do not downgrade production or fabricate a baseline.

```bash
cd backend
venv/bin/python -m alembic -c alembic.ini current
```

## 4. Stop producers and drain old work

1. Put mutating authoring routes behind maintenance mode. Keep public immutable
   Release reads available only if the proxy can separate them safely.
2. Stop `if-line-api` so no old task producer remains.
3. Keep the old worker and beat running until all affected tasks and their
   Outbox events are drained.
4. Inspect provider dashboards for requests that are still in flight. A worker
   process being idle is not sufficient evidence by itself.

Use a read-only SQL session to require zero rows from both queries:

```sql
SELECT kind, status, COUNT(*)
  FROM generation_tasks
 WHERE kind IN (
       'outline.generate', 'chapter.generate', 'chapter.batch',
       'branch.candidates.generate', 'chapter_script.generate',
       'vngraph.compile'
   )
   AND status IN ('queued', 'running')
 GROUP BY kind, status;

SELECT task.kind, event.status, COUNT(*)
  FROM outbox_events AS event
  JOIN generation_tasks AS task ON task.id = event.aggregate_id
 WHERE event.aggregate_type = 'generation_task'
   AND task.kind IN (
       'outline.generate', 'chapter.generate', 'chapter.batch',
       'branch.candidates.generate', 'chapter_script.generate',
       'vngraph.compile'
   )
   AND event.status IN ('pending', 'publishing', 'failed')
 GROUP BY task.kind, event.status;
```

Do not clear rows or force statuses to make these queries empty. Repair failed
Outbox delivery with the old release, or abort the cutover. Once both are empty:

```bash
sudo systemctl stop if-line-worker if-line-beat
sudo systemctl is-active if-line-api if-line-worker if-line-beat
```

All three services must report inactive before backup or migration.

## 5. Baseline and backups

Capture the privacy-preserving 0035 baseline:

```bash
cd backend
venv/bin/python scripts/verify_story_path_release_backfill.py capture \
  --output "$AUDIT_DIR/story-path-0035.json" \
  | tee "$AUDIT_DIR/story-path-0035-report.json"
```

Create a PostgreSQL custom-format backup using a standard libpq DSN supplied by
the secret manager. Do not pass the SQLAlchemy `postgresql+psycopg://` URL to
`pg_dump`:

```bash
pg_dump --format=custom --file "$AUDIT_DIR/database-pre-cutover.dump" \
  "$PG_DSN"
pg_restore --list "$AUDIT_DIR/database-pre-cutover.dump" \
  > "$AUDIT_DIR/database-pre-cutover.list"
test -s "$AUDIT_DIR/database-pre-cutover.list"
```

Record the Redis persistence state and queue lengths for every configured
worker queue. Use the infrastructure backup mechanism to snapshot the dedicated
Redis broker; do not run `FLUSHDB`:

```bash
redis-cli -n 0 INFO persistence > "$AUDIT_DIR/redis-persistence.txt"
for queue in text image audio compile maintenance celery; do
  redis-cli -n 0 LLEN "$queue"
done > "$AUDIT_DIR/redis-queue-lengths.txt"
```

Also archive the previous backend artifact, previous `frontend/dist`, current
systemd units, environment-file checksum, and reverse-proxy configuration. Do
not archive the plaintext environment file into the audit directory.

## 6. Migrate and verify while offline

Apply all migrations, then run both independent gates:

```bash
cd backend
venv/bin/python -m alembic -c alembic.ini upgrade head
venv/bin/python scripts/verify_story_path_release_backfill.py verify \
  --snapshot "$AUDIT_DIR/story-path-0035.json" \
  | tee "$AUDIT_DIR/story-path-0038-report.json"
venv/bin/python scripts/check_story_path_cutover.py \
  | tee "$AUDIT_DIR/story-path-cutover-report.json"
```

All commands must return zero. The verifier proves exact row, Head, manifest,
and public-read preservation. The cutover report must show:

- revision `0038_backfill_project_publications`;
- zero active affected tasks;
- zero open affected Outbox events;
- `ok: true`.

Terminal tasks with old source envelopes are warnings, not blockers. They remain
visible for audit and the new retry endpoint rejects them with 409 before quota
or Outbox mutation. Create a new generation request from the current StoryPath
UI instead of retrying one of those tasks.

## 7. Deploy and start one version

Install the backend and the already-built `frontend/dist` from
`$RELEASE_COMMIT`. Confirm that every systemd unit points at the same release
directory and loads the same `backend/.env`.

Start consumers before reopening producers:

```bash
sudo systemctl daemon-reload
sudo systemctl start if-line-worker
sudo systemctl start if-line-beat
sudo systemctl start if-line-api
sudo systemctl --no-pager --full status \
  if-line-worker if-line-beat if-line-api
```

Do not reopen ingress until readiness and contract smoke checks pass.

## 8. Smoke and acceptance checks

Runtime health and contract:

```bash
curl --fail --silent --show-error http://127.0.0.1:60002/health/live
curl --fail --silent --show-error http://127.0.0.1:60002/health/ready
curl --fail --silent --show-error http://127.0.0.1:60002/openapi.json \
  > "$AUDIT_DIR/runtime-openapi.json"
jq -e '.paths["/api/story-paths/{story_path_id}/outline-generations"]' \
  "$AUDIT_DIR/runtime-openapi.json"
jq -e '.paths["/api/projects/{project_id}/branch-candidate-generations"] == null' \
  "$AUDIT_DIR/runtime-openapi.json"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST http://127.0.0.1:60002/api/projects/1/branch-candidate-generations)" = 404
```

With a designated staging owner and non-billable provider configuration, verify
this full path without reusing an old task ID:

1. Open the owner project and its root StoryPath.
2. Generate an Outline with a non-preset chapter count and select its Head.
3. Generate and select a chapter revision by `path_chapter_id`.
4. Generate a CandidateSet at one checkpoint and promote two candidates into
   sibling StoryPaths.
5. Continue both siblings at the same `display_index` and confirm their UUIDs,
   context, revision history, Script Heads, and VNGraph Heads remain independent.
6. Bind exact resource versions, compile and select each VNGraph.
7. Read publication readiness, publish once with its fingerprint, and confirm
   there is no prepared or partially public state.
8. Read the active public Release by `release_id` and `path_chapter_id`.
9. Generate another draft revision and confirm the active Release remains
   unchanged until the next explicit publish.

Reopen ingress only after the product owner records the active Release ID,
manifest hash, both sibling PathChapter IDs, and the smoke result.

## 9. Observation window

For at least one normal release cycle:

- alert on `/health/ready`, API 5xx, worker task failures, permanent Outbox
  failures, queue depth, database pool saturation, and provider error rates;
- compare public Release manifest hashes with the migration report;
- reject any operational request to manually mutate a Head or publication row;
- retain the pre-cutover database/Redis/app/frontend backups and all legacy
  bridge columns;
- do not run the later legacy-column removal Blueprint.

## 10. Rollback decision and procedure

Rollback immediately when any of these is true and cannot be corrected without
data mutation: readiness stays failed, the verifier no longer passes, public
Release hashes or reachability changed, old routes reappear, the new worker
cannot consume current task envelopes, or publication exposes an intermediate
state.

Production rollback is backup restoration, not Alembic downgrade:

1. Close ingress and stop API, worker, and beat from the new release.
2. Preserve logs, the failed cutover report, current database backup, and Redis
   queue inventory for diagnosis.
3. Restore the pre-cutover PostgreSQL custom backup into a clean database or
   replacement database instance. Validate the restore before changing the
   application connection target.
4. Restore the dedicated Redis DB 0 snapshot from the same boundary. New
   StoryPath messages must never be consumed by an old worker. Do not flush a
   shared Redis instance or retain post-cutover queue messages.
5. Restore the previous backend artifact, `frontend/dist`, systemd units, and
   proxy configuration as one version.
6. Start the previous worker, beat, and API; then run that release's health,
   schema, owner, and public Release checks.
7. Reopen ingress and record the rollback release commit, database backup ID,
   Redis snapshot ID, and public Release hash.

Never point the old application at the 0038 database, the new application at
the restored 0035 database, or either worker at messages produced by the other
version.

## 11. Rollback rehearsal

The repository exercises the reversible migration logic only on a disposable
database:

```bash
cd backend
venv/bin/python -m pytest -q \
  tests/test_story_path_release_backfill_verification.py
```

The test performs `0035 -> 0038 -> 0035 -> 0038`, requires the rollback
snapshot hash to equal the original 0035 hash, and reruns the full verifier.
This proves migration downgrade mechanics for development; it does not replace
the production backup restoration procedure above.
