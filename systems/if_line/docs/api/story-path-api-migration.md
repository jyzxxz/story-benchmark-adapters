# StoryPath API Replacement and Review Matrix

## 1. Cutover Policy

The replacement API is mounted directly at `/api`. At final cutover:

- old routes are unregistered and absent from generated OpenAPI;
- there are no redirects, aliases, or request adapters for removed contracts;
- an old route returns 404;
- backend, worker, and frontend switch as one deployment unit;
- stable ID-based Task, Asset, Voice, Media, ReadingSession, and continuation
  APIs remain available.

The target machine-readable contract is
`docs/api/story-path-authoring.openapi.yaml`.

## 2. Identity and Concurrency Mapping

| Legacy concept | Replacement |
|---|---|
| `project_id + chapter_index` | `story_path_id + path_chapter_id` |
| `chapter_index` join key | UUID relation; `display_index` returned for display only |
| project-wide outline | StoryPath-specific OutlineRevision and Head |
| project-wide chapter Head | Head on StoryPathChapter |
| project-wide Script/VNGraph Head | Head keyed by exact source Revision |
| one candidate set per checkpoint | versioned CandidateSetRevision plus Head |
| activate/approve action route | `PUT .../head` with required `If-Match` |
| source-hash global dedup | provenance only; idempotency is scoped to `Idempotency-Key` |
| visibility/draft flags | computed authoring/publication projections |
| release create plus publication flags | atomic `POST /projects/{id}/publish` |

Generation idempotency rules:

1. Same Idempotency-Key and same canonical request returns the original Task.
2. Same key and different request returns `409 idempotency.conflict`.
3. Different key may create another draft Revision from the same source.
4. Generation never changes a Head.

Head resources are readable before their first selection: `revision_id` is
null and `lock_version` starts at 1. Every `PUT .../head`, including the first,
must send the version returned by the corresponding `GET`. A stale version
returns `409 head.version_conflict`; re-selecting the current revision with a
matching version is an idempotent no-op.

## 3. Project and Bible Routes

| Legacy route | Replacement | Cutover action |
|---|---|---|
| `POST /projects/` | `POST /projects` | rebuild schema; create root StoryPath |
| `GET /projects/` | `GET /projects` | replace visibility filters with `publication_state` |
| `GET /projects/{project_id}` | same path | owner-only authoring projection; public uses `/public` |
| none | `PATCH /projects/{project_id}` | add metadata update |
| `DELETE /projects/{project_id}` | same path | rebuild against new dependency graph |
| `GET /projects/{project_id}/status` | `GET /projects/{project_id}` | status becomes computed projection |
| `POST /projects/{project_id}/claim` | none | delete hidden legacy claim route |
| `GET /projects/{project_id}/readiness` | `GET /projects/{project_id}/publication-readiness` | return fingerprint and blockers |
| `GET /projects/{project_id}/stats` | `GET /projects/{project_id}/metrics` | stable-ID aggregation |
| `GET /projects/{project_id}/stats/breakdown` | `GET /projects/{project_id}/metrics/artifacts` | stable-ID artifact aggregation |
| `GET /projects/{id}/bible/current` | `GET /projects/{id}/bible-head` | return Head envelope |
| `GET /projects/{id}/bible/revisions` | `GET /projects/{id}/bible-revisions` | path rebuilt with new schema |
| `POST /projects/{id}/bible/revisions` | `POST /projects/{id}/bible-revisions` | creates unselected Revision |
| `POST /projects/{id}/bible/generations` | `POST /projects/{id}/bible-generations` | async; no auto activation |
| `POST .../bible/revisions/{revision_id}/activate` | `PUT /projects/{id}/bible-head` | `If-Match` required |

## 4. Outline and Chapter Routes

| Legacy route | Replacement |
|---|---|
| `GET /projects/{id}/outline/current` | `GET /story-paths/{path_id}/outline-head` |
| `GET /projects/{id}/outline/revisions` | `GET /story-paths/{path_id}/outline-revisions` |
| `POST /projects/{id}/outline/revisions` | `POST /story-paths/{path_id}/outline-revisions` |
| `POST .../outline/revisions/{revision_id}/approve` | `PUT /story-paths/{path_id}/outline-head` |
| `POST /projects/{id}/outline/generations` | `POST /story-paths/{path_id}/outline-generations` |
| `GET .../chapters/{chapter_index}/current` | `GET /path-chapters/{path_chapter_id}/head` |
| `GET .../chapters/{chapter_index}/revisions` | `GET /path-chapters/{path_chapter_id}/revisions` |
| `POST .../chapters/{chapter_index}/revisions` | `POST /path-chapters/{path_chapter_id}/revisions` |
| `POST .../revisions/{revision_id}/activate` | `PUT /path-chapters/{path_chapter_id}/head` |
| `POST .../chapters/{chapter_index}/generations` | `POST /path-chapters/{path_chapter_id}/generations` |
| `POST /projects/{id}/chapter-generation-batches` | `POST /story-paths/{path_id}/chapter-generation-batches` |

`outline-generations` accepts an explicit `chapter_count` from 1 through 500.
Pace remains optional creative guidance and does not choose a fixed count.
`PathChapterCreate` only identifies the current tail through
`after_path_chapter_id`; chapter titles belong to immutable OutlineChapter rows
and are never accepted as disposable PathChapter metadata.

## 5. Candidate and StoryPath Routes

| Legacy route | Replacement |
|---|---|
| `POST /projects/{id}/branch-candidate-generations` | `POST /story-paths/{path_id}/checkpoints/{node_id}/candidate-set-generations` |
| `GET /projects/{id}/checkpoints/{node_id}/candidates` | `GET /candidate-set-revisions/{set_id}/candidates` |
| none | `GET .../candidate-set-revisions` |
| none | `GET .../candidate-set-head` |
| none | `PUT .../candidate-set-head` |
| none | `POST /branch-candidates/{candidate_id}/story-paths` |

`POST /projects/{project_id}/story-paths` accepts a reviewed `candidate_id` and
uses the same promotion application service as the candidate-scoped endpoint.
It does not permit an unproven arbitrary fork. Project creation constructs the
single root path internally.

## 6. Script, Resource, and VNGraph Routes

The legacy Script prefix
`/projects/{project_id}/chapters/{chapter_index}/chapter-scripts` is removed.

| Legacy suffix | Replacement |
|---|---|
| `POST /generations` | `POST /chapter-revisions/{revision_id}/script-generations` |
| `GET /current` | `GET /chapter-revisions/{revision_id}/script-head` |
| `GET /revisions` | `GET /chapter-revisions/{revision_id}/script-revisions` |
| `GET /revisions/{script_id}/resource-slots` | `GET /chapter-script-revisions/{script_id}/resource-slots` |
| `POST /revisions/{script_id}/resource-renders` | `POST /chapter-script-revisions/{script_id}/resource-renders` |
| `PUT /revisions/{script_id}/resource-slots/{slot_id}` | `PUT /chapter-script-revisions/{script_id}/resource-slots/{slot_id}` |

| Legacy VNGraph route | Replacement |
|---|---|
| `POST .../chapters/{index}/vn-graphs/compile` | `POST /chapter-script-revisions/{script_id}/vn-graph-compilations` |
| `GET .../vn-graphs/current` | `GET /chapter-script-revisions/{script_id}/vn-graph-head` |
| `GET .../vn-graphs/revisions` | `GET /chapter-script-revisions/{script_id}/vn-graph-revisions` |
| `GET .../vn-graphs/revisions/{revision_id}` | `GET /vn-graph-revisions/{revision_id}` |
| none | `PUT /chapter-script-revisions/{script_id}/vn-graph-head` |

There is no separate VNGraph skeleton route. ChapterScriptRevision is the
reviewable skeleton and VNGraphRevision is its compiled, resource-bound output.

## 7. Release and Public Routes

| Legacy route | Replacement | Behavior |
|---|---|---|
| `POST /projects/{id}/releases` | `POST /projects/{id}/publish` | validate, create, supersede, activate atomically |
| `GET /projects/{id}/releases` | same path | owner history, rebuilt schema |
| `POST .../releases/{release_id}/withdraw` | `POST /projects/{id}/unpublish` | withdraw current active Release only |
| none | `GET /releases/{release_id}` | owner Release details |
| `GET /public/projects` | same path | only ProjectPublication-backed projects |
| `GET /public/projects/{project_id}` | same path | only active Release metadata |
| `GET /public/releases/{release_id}/manifest` | same path | non-active Release returns 404 |
| `GET .../chapters/{chapter_index}/manifest` | `GET .../path-chapters/{path_chapter_id}/manifest` | UUID lookup |
| `GET .../chapters/{chapter_index}/vn-graph` | `GET .../path-chapters/{path_chapter_id}/vn-graph` | UUID lookup |

There is no prepared Release API or state. Publishing requires
`expected_authoring_fingerprint` and an Idempotency-Key. A source change returns
`409 release.source_changed` and leaves the old active Release untouched.

## 8. Preserved ID APIs

These domains keep their existing public contracts unless a later Blueprint
explicitly changes them:

```text
/tasks/*
/assets/*
/voice-lines/*
/media/*
/reading-sessions/*
/continuations/*
```

Their internal chapter references must eventually use stable artifact IDs, but
that internal migration does not justify breaking an already ID-oriented route.

## 9. Backend Review

Final cutover state:

- `authoring_story_paths.py`, `authoring_outlines.py`,
  `authoring_chapters.py`, `authoring_candidates.py`, `authoring_scripts.py`,
  `authoring_vn_graphs.py`, and `authoring_releases.py` are the registered
  authoring routers.
- Every owner check follows a UUID relation back to Project. `display_index`
  remains response and provider-prompt metadata only.
- The old Branch, Script, and VNGraph router modules are deleted. The retained
  rollback sources `revisions.py` and `releases.py` are not imported by the
  application factory and expose no runtime operation.
- Generated FastAPI OpenAPI contains all 58 frozen replacement operations and
  no removed authoring operation. Shared history paths reject removed methods
  with 405; fully removed paths return 404.
- The architecture test rejects any modern Outline, Chapter, CandidateSet,
  Script, VNGraph, or publication dependency that queries identity by
  `chapter_index`.

## 10. Frontend Review

Final cutover state:

1. `frontend/src/api/storyPathApi.ts` is the authoring client and carries
   `story_path_id`, `path_chapter_id`, and exact Revision IDs.
2. The old chapter, revision, workflow, script-asset, visual-asset, voice,
   release, project, and stats clients were deleted rather than aliased.
3. Revision selectors retain the Head ETag/lock version and generation screens
   refresh history without assuming automatic Head activation.
4. The publication panel performs one atomic publish command after readiness
   review. Owner and public reads use separate client surfaces.
5. Unit and Playwright tests reject imports of deleted clients and cover path
   switching, independent sibling continuations, Head conflicts, task polling,
   resource binding, and atomic publication.

## 11. Data Migration Review

Required bridge mapping:

| Legacy data | Target data |
|---|---|
| one project | one root StoryPath |
| `(project_id, chapter_index)` revision family | ChapterSlot + root StoryPathChapter |
| ChapterHead | StoryPathChapter.current_revision_id |
| project Outline Head | root StoryPathOutlineHead |
| ChapterScriptHead | ChapterScriptHead keyed by exact ChapterRevision |
| VNGraphHead | VNGraphHead keyed by exact ScriptRevision |
| BranchCandidate rows | CandidateSetRevision under root path |
| published_release_id | ProjectPublication.active_release_id |

Migration must preserve counts, exact Head IDs, manifest hashes, and current
public reachability. Ambiguous legacy candidates are migrated as unselected sets
and never inferred as promoted StoryPaths. Legacy immutable Release manifests
are read through an internal schema adapter rather than rewritten.

## 12. Review Decision

The backend, frontend, and migration surfaces are consistent with the target
identity model under these locked decisions:

- no old authoring API compatibility layer;
- one atomic public publish operation;
- manual Head activation after generation;
- ChapterScriptRevision is the VNGraph skeleton;
- current public Releases remain public through migration;
- project authoring stage and publication state are computed projections.
- the publication projection exposes `lock_version` as the source for the
  unpublish `If-Match` precondition.

Any change to these decisions requires updating the architecture document,
OpenAPI contract, migration matrix, and Blueprint before implementation.

Deployment sequencing, task compatibility, rollback, smoke checks, and the
authoritative port map are in
`docs/operations/story-path-cutover-runbook.md`.
