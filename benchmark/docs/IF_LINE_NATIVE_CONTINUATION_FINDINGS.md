# Historical findings: frozen IF Line continuation and first-choice entrypoints

> **Archived source inspection and design proposals, 2026-09-07.** This note applies to baseline `572407f` and the early first-unselected-choice work beginning at `545e29d`; its proposed checks and “next” steps are historical, not instructions for a new production run. Current v4 records actual choices and a common image/text reading window. Start with [OPERATOR_HANDOFF.md](../../docs/OPERATOR_HANDOFF.md) and use the [IF Line v4 driver reference](../native_shims/if_line/README.md) for implemented details. Later source identity includes the disclosed [HTML patch](../../docs/IFLINE_HTML_PATCH.md); the unmodified-source statement below describes this inspection only.

Read-only source inspection, 2026-09-07. Native baseline: `572407fce9b648a4206ac37da6a9f6ed22631da8`; adapter worktree starts at `545e29db55ff9af4d5d2b52a6ec97809b3402845`. This document describes inspected interfaces and proposed external input initialization, not an executed model run. Native files remain unchanged.

Follow-up: the structural prefix/candidate chain described below is now implemented
under explicit `entry_mode=provided_prefix_candidates` and has passed a real
isolated PostgreSQL/Redis/Celery test with localhost fixed model responses. The
same-chapter cold-start proposal was not implemented. See the external shim
README for the implemented behavior and its unsupported visible-output boundary.

## Supplied prose can be stored truthfully

`POST /api/path-chapters/{path_chapter_id}/revisions` accepts `{content, parent_revision_id?}`. See `systems/if_line/backend/app/routers/v2/authoring_chapters.py:188` and `schemas_authoring.py:274`.

The service `create_manual_story_path_chapter_revision` (`application/story_chapter_service.py:305`) freezes/validates the existing Bible and outline context and marks its intermediate source `origin=manual_edit`. Persistence receives no task ID, so the stored revision has `generation_task_id=None` (`story_chapter_service.py:698`). The public revision response includes the exact content, content hash, immutable context manifest and parent ID, but does not expose the task ID or origin. An external import receipt must therefore identify this revision as `provided_prefix`, with the exact opening SHA and native ID, and exclude it from generated prose statistics.

The native head activation endpoint can select the manual opening revision with its normal `If-Match` lock. No generation or fictional content is needed to import this input.

## Editing ancestry and continuation ancestry differ

Regenerating the same chapter after importing an opening does not itself read the parent revision's prose. `parent_revision_id` is recorded in the source as revision lineage, while `previous_chapters` is built from earlier selected chapter placements (`story_chapter_service.py:518`). Each predecessor contributes its final 1,200 characters. The main prompt includes that full final slice in its mandatory previous-ending block (`services/prompt_templates.py:495–520`); its separate summary fallback is truncated to 400 characters. Rewrite/expansion prompts have their own shorter summaries.

Generating the next chapter would use the native continuation context but would also advance to the next outline chapter. Treating the public opening as a complete first planned chapter and skipping its remaining outline events would need an explicit experiment decision. Importing the opening into the first slot alone does not repair the existing first-chapter cold start.

A smaller external cold-start rule could feed the imported, verified immutable parent content into the existing `previous_chapters` input for the first generated chapter only. This must be disclosed as an external input rule, anchored to its native revision and exact content hash, and must apply consistently to the existing generation/rewrite/expansion calls. It must not create new state summaries, rewrite model output or alter native review algorithms. This is a proposal, not an existing native feature.

## Native candidate generation exists; checkpoint creation is absent from the HTTP surface

`POST /api/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-generations` requires an idempotency key and `{chapter_revision_id,state_snapshot_id,candidate_count,instructions?}`. Candidate count is required and bounded to 2–4. GET candidate-set revisions/head and `GET /api/candidate-set-revisions/{id}/candidates` provide the generated records. See `routers/v2/authoring_candidates.py:37` and `schemas_authoring_artifacts.py:11`.

`build_candidate_set_generation_source` verifies the chapter is the path's selected head, resolves native Bible/outline context, validates their hashes, checks the StateSnapshot hash and requires an existing `StoryNode` of type `checkpoint` tied to that exact ChapterRevision (`application/story_branch_service.py:304–400`). The provider sees native Bible, outline chapter, chapter tail, checkpoint payload and state (`:411`). No review or hash constraint needs to be disabled.

A source-wide search found no runtime creator of `StoryNode(node_type="checkpoint")`. The native branch tests seed it directly through the ORM, together with StateSnapshot (`tests/test_story_path_candidate_set_generation.py:155–198`). The front end exposes manual checkpoint/state UUID input boxes (`frontend/src/views/WorkflowView.vue:752`). Generated branch preview nodes are `paragraph` nodes (`story_branch_service.py:801`); the VN Graph compiler does not create this authoring checkpoint.

An explicitly named **external prefix import** can bridge this structural initialization without inventing story facts:

1. Create/select the exact opening through the native manual revision API.
2. Reuse native `branch_service._snapshot(db, project_id, {}, None)`. This validates JSON, hashes and deduplicates a genuinely empty state. StateSnapshot requires only project ID, state hash and JSON object; no character knowledge, inventory or memory fields are mandatory (`models_v2.py:1448`).
3. Create a native ORM `StoryNode` with `project_id`, `node_type="checkpoint"`, `content_revision_id` equal to the imported revision, a structural checkpoint key, and a payload containing only the exact supplied opening and/or structural source identities. There is no native creation service/API to claim here. The adapter must make its external origin visible and reject an inconsistent repeat.
4. Use the ordinary native candidate-generation HTTP endpoint and actual Celery worker. Export the actual returned candidates with source pointers. Do not call candidate promotion or reading-session choices.

This would generate future possibilities without updating the current path or applying their `state_delta`. The empty state must remain empty; narrative state must not be inferred by the adapter. Branch calls use a different native worker (`workers/branch_tasks.py:145`), so the external trace context must cover this entrypoint as well as the existing story worker.

## A meaningful capability limit remains

Native candidate schema has only `option_key`, `preview_text` and `state_delta`; it has no separate human-facing action label. The native prompt explicitly defines `preview_text` as the short prose read **after choosing** (`integrations/llm/branch_adapter.py:63`). Therefore these are unselected branch previews, not current-path events. They must never be concatenated into the already-happened story or presented as an executed choice. The adapter cannot manufacture Chinese choice labels by copying C1 from the task and report them as native model output.

The clarified `CAMPUS-01-C1` task asks for observable prose before the choice and two unexecuted options. Native branch previews can be retained as internal/unselected artifacts and their actual option keys can be displayed, but this alone does not establish that the native output satisfies that requested boundary. Any absent labels, missing newly generated pre-choice prose, or outcome leakage must remain an explicit generation/capability finding.

There is also a native reading-continuation subsystem (`integrations/llm/continuation_adapter.py`, `application/visual_asset_service.py:3093`), but it requires a real released story and reading-session context and follows a user-selected direction. Faking a release/session or selecting C1 to reach it would not be a valid shortcut for this initial unchosen-choice test.

## Minimum next engineering check

Use an isolated PostgreSQL/Redis/API/worker runtime and localhost fixed responses. Import the real shared opening, verify byte equality and manual origin, initialize only empty state plus structural checkpoint, and invoke native candidate generation. Assert source fingerprint unchanged, zero ChoiceDecision/promoted paths, empty state unchanged, candidate provenance pointers, native source integrity checks retained, task trace coverage, HTTP budgets and idempotent external import. Only after that succeeds should a coordinated paid run be considered.
