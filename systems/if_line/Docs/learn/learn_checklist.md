# Learn Checklist — understand mode, subset `reuse_patterns`

> Dual-cursor protocol. Master lane bootstrapped this run; per-file artifacts self-validated against source on 2026-06-26. Stage 3 execution-cron workers may re-validate and downgrade via EvidenceLint if drift found.

## Counts
- `[ ]` not_done: 0
- `[_]` worker_self_tested: 0
- `[x]` master_accepted: 21

## Per-file artifacts (1:1 with source_manifest.tsv)

- [x] backend/app/services/background_scene_analyzer_service.py → `_learn.md`
- [x] backend/app/services/background_prompt_assembler_service.py → `_learn.md`
- [x] backend/app/services/tts_service.py → `_learn.md`
- [x] backend/app/services/image_generation_service.py → `_learn.md`
- [x] backend/app/services/asset_management_service.py → `_learn.md`
- [x] backend/app/services/vn_graph_generator.py → `_learn.md`
- [x] backend/app/services/llm_vn_graph_generator_simplified.py → `_learn.md`
- [x] backend/app/services/scene_segmenter_service.py → `_learn.md`
- [x] backend/app/agent/quality_check.py → `_learn.md`
- [x] backend/app/agent/auto_creator.py → `_learn.md`
- [x] backend/app/agent/presets.py → `_learn.md`
- [x] backend/app/agent/resilient_llm.py → `_learn.md`
- [x] backend/app/services/prompt_templates.py → `_learn.md`
- [x] backend/app/routers/image_generation.py → `_learn.md`
- [x] backend/app/routers/vn_graph.py → `_learn.md`
- [x] backend/app/routers/tts.py → `_learn.md`

## Folder synthesis

- [x] backend/app/services/ folder learn
- [x] backend/app/agent/ folder learn
- [x] backend/app/routers/ folder learn
- [x] backend/app/ folder learn
- [x] repo root folder learn

## Coverage index files

- [x] `file_learn_index.tsv`
- [x] `folder_learn_index.tsv`

## Validation gates (master passed)

- [x] `source_manifest.tsv` covers exactly the locked subset
- [x] each source file has exactly one `_learn.md`
- [x] folder artifacts cover every represented folder
- [x] no `[_]` treated as `[x]` (n/a — all promoted explicitly)
- [x] workers cannot write `[x]` (master-only bootstrap noted in route_decision.md)
- [x] no out-of-scope files in subset output
- [x] looper logs: n/a — no instrument friction in this run
