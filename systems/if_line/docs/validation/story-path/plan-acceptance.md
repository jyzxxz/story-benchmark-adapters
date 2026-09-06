# StoryPath Plan Final Acceptance

## Scope

This record closes the requirements copied into `plan.md`. The implementation
status source remains `blueprint.md`: all 41 numbered items from R0.1 through
R8.5 are checked, each has one validation record, and each record was introduced
by one distinct commit authored as `fbh <2697927157@qq.com>`.

The final audit added only acceptance assertions. It did not change production
routes, service behavior, database models, migrations, or deployment settings.

## Refactor goals

| # | Requirement | Accepted evidence |
|---:|---|---|
| 1 | Sequence is display/order only | `test_story_path_identity_architecture.py` rejects active identity joins through `chapter_index`; PathChapter APIs use UUIDs. |
| 2 | Stable UUID artifact joins | `test_three_candidates_create_isolated_display_five_artifact_chains` verifies PathChapter, ChapterRevision, ScriptRevision, and VNGraphRevision UUID chains. |
| 3 | Revision differs from StoryPath | `test_path_chapter_head_is_scoped_by_slot_and_uses_cas` switches between two revisions while the project still has one StoryPath. |
| 4 | Path-local ancestor resolution | The three-path acceptance test resolves chapter 6 three times and rejects both sibling chapter-5 IDs from every manifest. |
| 5 | Arbitrary chapter counts | The real outline worker and activation path run with 8, 13, 20, and 120 chapters. The general accepted range remains 1 through 500. |
| 6 | Manual review Heads | Outline, Chapter, Script, CandidateSet, and VNGraph tests require explicit Head activation and optimistic versions. |
| 7 | ScriptRevision is graph skeleton | VNGraph revisions and Heads are selected under exact ScriptRevision UUIDs; the Script/VNGraph pipeline tests the compiler path. |
| 8 | Draft and public isolation | `test_published_project_remains_frozen_during_continued_authoring` proves later authoring cannot mutate the active Release. |
| 9 | One atomic publish | Publication service tests cover first publish, supersede, stale fingerprint, readiness failure, concurrency, and rollback. No prepare route exists. |
| 10 | Replacement `/api` contract only | Runtime and both OpenAPI schemas contain all 58 frozen operations and none of the 49 removed operations. Removed endpoints return 404 or the documented 405 for legacy release creation. |

## End-to-end gate

| # | Acceptance criterion | Direct proof |
|---:|---|---|
| 1 | One source creates three Candidates and three StoryPaths | `test_three_candidates_create_isolated_display_five_artifact_chains` requests one three-item CandidateSet and promotes all three immutable candidates. |
| 2 | Three branches own display index 5 with distinct Chapter, Script, and VNGraph UUIDs | The same test asserts three distinct PathChapter IDs, ChapterSlot IDs, ChapterRevision IDs, ChapterScriptRevision IDs, and VNGraphRevision IDs. |
| 3 | Generating chapter 6 never reads sibling chapter 5 | The same test checks each frozen ancestor manifest contains its own chapter-5 PathChapter UUID and neither sibling UUID. |
| 4 | Multiple revisions and Head switching do not branch | `test_path_chapter_head_is_scoped_by_slot_and_uses_cas` selects revision 1, switches to revision 2, and requires one StoryPath. |
| 5 | Outlines generate 8, 13, 20, and 120 chapters with stable IDs | `test_generation_uses_requested_count_and_preserves_ids` invokes the worker for every required count, activates it, creates a reviewed revision, and requires the same PathChapter IDs. |
| 6 | Continued editing does not alter the public Release | `test_published_project_remains_frozen_during_continued_authoring`. |
| 7 | One publish activates the new Release and supersedes the old | `test_new_release_supersedes_the_previous_active_release_atomically`. |
| 8 | Failed validation creates no Release or pointer | `test_stale_or_blocked_source_creates_no_release_or_publication` and the publication rollback tests. |
| 9 | Unpublish revokes every public detail entry | `test_atomic_publish_public_reads_and_unpublish_use_one_active_release` requires an empty catalog and 404 from project, manifest, PathChapter manifest, and VNGraph endpoints. |
| 10 | A previously public project remains readable after migration | `test_story_path_release_backfill_verifier_detects_exact_result_and_damage` requires one expected and one actual readable active Release before and after the migration rollback round trip. |
| 11 | OpenAPI contains no old authoring or release operations | `test_legacy_operations_are_absent_from_dynamic_and_snapshot_openapi` and removed-route runtime tests. |
| 12 | Task, Asset, Voice, ReadingSession, and continuation do not regress | Dedicated preserved-domain run: `test_v2_tasks.py`, `test_v2_assets.py`, `test_v2_voice_lines.py`, `test_v2_branching.py`, and `test_visual_asset_workflows.py`. |

## Deployment and rollback gate

| # | Requirement | Accepted evidence |
|---:|---|---|
| 1 | Deploy only after all R0-R8 items | Runbook sections 1, 3, and 7 freeze one commit and one release unit after preflight. |
| 2 | Stop producers and drain tasks/Outbox | Runbook section 4 supplies exact blocking queries; `check_story_path_cutover.py` and `test_story_path_cutover.py` enforce zero active work. |
| 3 | Backup, additive migration, verify | Runbook sections 5-6 require PostgreSQL and Redis backups, Alembic upgrade, row/Head/manifest/public checks, and cutover audit. |
| 4 | Backend, worker, and frontend move together | Runbook sections 1 and 7 define one release unit and forbid mixed versions. |
| 5 | Retain legacy schema for one observation cycle but never read it | Runbook section 9 retains columns; the architecture identity test scans active code for index-based joins. |
| 6 | Roll back application, database, queues, and ingress together | Runbook section 10 restores pre-cutover PostgreSQL, Redis, app, SPA, units, and proxy as one boundary. The disposable migration test exercises `0035 -> 0038 -> 0035 -> 0038`. |
| 7 | Drop legacy columns in a later Blueprint | Runbook section 9 explicitly prohibits removal during this release. |

## Commands and results

Focused files changed by this final audit:

```bash
cd backend
venv/bin/python -m pytest -q \
  tests/test_story_path_promotion.py \
  tests/test_story_path_outline_service.py \
  tests/test_revision_head_optimistic_activation.py \
  tests/test_story_path_artifact_routes.py
```

Result: `52 passed`.

Core StoryPath, artifact, publication, migration, and API acceptance:

```bash
cd backend
venv/bin/python -m pytest -q \
  tests/test_story_path_promotion.py \
  tests/test_story_path_outline_service.py \
  tests/test_revision_head_optimistic_activation.py \
  tests/test_story_context_resolver.py \
  tests/test_story_path_chapter_generation.py \
  tests/test_chapter_script_pipeline.py \
  tests/test_v2_vn_graphs.py \
  tests/test_publication_readiness.py \
  tests/test_atomic_publication_service.py \
  tests/test_atomic_publication_lifecycle.py \
  tests/test_public_release_service.py \
  tests/test_story_path_artifact_routes.py \
  tests/test_story_path_release_backfill_verification.py \
  tests/test_api_route_contracts.py \
  tests/test_story_path_identity_architecture.py
```

Result: `124 passed, 1 skipped`.

Preserved domains:

```bash
cd backend
venv/bin/python -m pytest -q \
  tests/test_v2_tasks.py \
  tests/test_v2_assets.py \
  tests/test_v2_voice_lines.py \
  tests/test_v2_branching.py \
  tests/test_visual_asset_workflows.py
```

Result: `55 passed`.

Operations and rollback:

```bash
cd backend
venv/bin/python -m pytest -q \
  tests/test_story_path_cutover.py \
  tests/test_deployment_scripts.py \
  tests/test_story_path_release_backfill_verification.py
venv/bin/python -m compileall -q app scripts \
  tests/test_story_path_promotion.py \
  tests/test_story_path_outline_service.py \
  tests/test_revision_head_optimistic_activation.py \
  tests/test_story_path_artifact_routes.py
```

Result: `13 passed`; compilation passed.

The complete backend-suite baseline remains the R8.5 result: `1193 passed, 46
skipped, 4 failed`. All four independently reproduced failures are unrelated
pre-existing tests documented in `R8.5.md`; no changed or acceptance module is
among them. Frontend type checking, unit tests, production build, and the
11-case StoryPath Playwright workflow also remain recorded as passed in
`R8.5.md` because this final audit changed backend test assertions only.

## Identity review

The added tests use `display_index` only to assert presentation order. Every
source resolution, Head activation, Script relation, graph relation, and public
read is addressed by UUID. No active implementation gained a sequence-based
lookup or join.
