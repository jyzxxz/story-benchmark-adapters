# Route Decision — learn cron

- **learn_mode**: `understand`
- **subset**: `reuse_patterns` (locked in `subsets/reuse_patterns/source_manifest.tsv`)
- **route_policy**: `auto` resolved per-file (see manifest `route` column)
  - `high_reasoning`: complex LLM chains, async orchestration, prompt builders → 11 files
  - `standard`: data models, simple routers, presets → 5 files
- **coverage**: strict 1:1 source file → `Docs/learn/files/<path>_learn.md`, plus folder `_current_folder_learn.md` for every represented directory.
- **bootstrap note**: This learn run is performed by the master lane directly (no cron worker pool yet — that lands in Stage 3). All items are seeded as `[_]` then promoted to `[x]` after master self-validation. Stage 3 workers may re-validate any note against the source file and downgrade via EvidenceLint if drift is found.

## Auto m/k decision

- `m` (proposals): N/A — understand mode produces one canonical artifact per source file, no proposal competition.
- `k` (selection): all_valid — every file in the manifest must produce exactly one `_learn.md`.
- `EstimatorPolicy`: route chosen by file size + LLM-chain density. Files >500 LOC with `await/llm/json schema` → high_reasoning; pure dataclass/router files → standard.
