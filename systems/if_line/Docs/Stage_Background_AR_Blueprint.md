# Stage_Background_AR_Blueprint

> **权威 AR 蓝图**。本文件是优化 cron 的唯一真理来源。每项 `[ ]` 只能在对应的 `Docs/researches/Stage_Background_AR/<section>/<slug>.md` 存在且符合 `00_philosophy.md` 完成判据时被改为 `[x]`。
>
> **设计哲学**: 把章节背景图链路从"故事概要倒推图片"重构为"从章节里抽取环境场景 schema，再组装环境 prompt"——两步式（结构化 scene 抽取 + deterministic assembler），最后用生成后验收 + 自动重试保证"环境是主体、无人物主体化、无命名角色"。详见 [`Docs/researches/Stage_Background_AR/00_philosophy.md`](researches/Stage_Background_AR/00_philosophy.md)。

---

## 源范围（Source Scope）

| 关注点 | 当前位置 | 目标位置 |
|---|---|---|
| 编排入口 | `backend/app/services/asset_management_service.py::generate_chapter_backgrounds` | 同上，但流程改为 analyzer→classifier→assembler→generator→validator |
| 场景分段 | `backend/app/services/scene_segmenter_service.py::segment_chapter` | 同上扩展，或新增 `BackgroundSceneAnalyzerService` |
| Prompt 构建 | `backend/app/services/prompt_builder_service.py::build_background_prompts_async` | 改为 `build_background_prompt_from_scene_spec` |
| Rewriter | `backend/app/services/prompt_rewriter_service.py`（background profile） | 限制为 schema-driven assembler，禁止自由发挥剧情 |
| 生成 + enforce + sanitize + fallback | `backend/app/services/image_generation_service.py::generate_background`、`_enforce_background_no_human_subject`、`_sanitize_prompt`、`_build_safety_fallback_background_prompt` | 统一收口 `_enforce_background_environment_focus(scene_spec, ...)`，scene-aware fallback |
| 验收 | `backend/app/services/background_image_validator_service.py` | 重构为 bbox + 多模态混合验收 |
| 配置 | `backend/app/config/image_generation_profiles.json` | 新增 `background_scene_taxonomy`、`background_style_taxonomy`、`background_camera_shots`、`background_people_policy_keywords`、`background_negative_clauses` |
| 背景匹配 | `backend/app/services/llm_vn_graph_generator.py`（`scene_location` 匹配） | 改为按 `scene_selector` slug 匹配 |

LLM 入口：复用现有 `REWRITER_API_KEY` / `REWRITER_BASE_URL` / `SEGMENTER_MODEL`（GLM/zhipu）。
环境变量：`SCENE_SEGMENTER_ENABLED`、`SCENE_SEGMENTER_API_KEY`、`PROMPT_REWRITER_ENABLED`。

---

## Section A — SceneAnalyzer（场景分析器，worker A owns this section）

> 范围：`Docs/researches/Stage_Background_AR/scene_analyzer/`
> 关注：从章节正文 + outline 抽取结构化 `BackgroundSceneSpec[]`，禁止剧情摘要，按可视环境变化切场景。

- [x] **A01** background-scene-analyzer-service-skeleton — 新增 `BackgroundSceneAnalyzerService` 服务骨架。研究 SOTA 结构化抽取服务的接口设计（OpenAI Structured Outputs / Anthropic tool use / GLM response_format），给出 Pydantic schema 定义 + LLM 调用入口 + 缓存 + 失败降级路径。落到 `backend/app/services/background_scene_analyzer_service.py`。
[2026-06-15 16:20:38] AUTO-CHECK A01 (background-scene-analyzer-service-skeleton)
- [x] **A02** background-scene-spec-schema — 定义 `BackgroundSceneSpec` Pydantic 模型：`scene_id / scene_name / scene_selector / scene_type / split_reason / evidence_spans / environment_description / architecture / props / lighting / weather / time_of_day / atmosphere / camera_shot_type / composition_constraints / people_policy / style_tags / forbidden_characters`。研究每个字段的语义边界、必填性、长度限制，参考 Stable Diffusion Structured Prompt / SDXL Refiner JSON schema。
[2026-06-15 16:20:38] AUTO-CHECK A02 (background-scene-spec-schema)
- [x] **A03** background-scene-extractor-system-prompt — 场景提取器 system prompt 设计。研究 OpenAI/Anthropic 官方推荐的 developer message 分区结构（identity / instructions / examples / context），落到 `app/config/image_generation_profiles.json` 的 `rewriter.background_scene_extractor` 节点。要求：硬性规则编号化、few-shot 覆盖古风/现代/科幻/奇幻/动漫 5 个 genre、禁止输出剧情摘要。
[2026-06-15 16:20:38] AUTO-CHECK A03 (background-scene-extractor-system-prompt)
- [x] **A04** background-scene-split-rules — 场景切分硬规则：物理地点变化、时间变化、天气变化、人群状态变化、物理状态变化（整洁→狼藉）必须切。研究如何用 few-shot 让 LLM 稳定遵守，参考 Anthropic context engineering 的"显式约束 + 示例"原则。
[2026-06-15 16:20:38] AUTO-CHECK A04 (background-scene-split-rules)
- [x] **A05** background-scene-no-plot-contamination — 反剧情污染机制。研究在 system prompt 里如何写"绝对禁止输出故事概要/对白转述/人物冲突摘要"，并在后置校验加 `_has_plot_keywords(scene_spec)` 检查（如检测到"主角/发现/到达/对白"等剧情词就拒绝）。落到 `BackgroundSceneAnalyzerService._validate_no_plot`。
[2026-06-15 16:20:38] AUTO-CHECK A05 (background-scene-no-plot-contamination)
- [x] **A06** background-scene-fingerprint-dedup — 按 `scene_fingerprint`（location + time_of_day + weather + crowd_state + physical_state）去重，不再只按 location。研究 fingerprint hash 设计：哪些字段进 hash、哪些是描述性字段，参考 perceptual hashing 思路。落到 `asset_management_service.py::generate_chapter_backgrounds` 的 dedup 段。
[2026-06-15 16:20:38] AUTO-CHECK A06 (background-scene-fingerprint-dedup)
- [x] **A07** background-scene-type-taxonomy — 场景类型受控词表（`background_scene_taxonomy`）。研究覆盖古风/现代/科幻/奇幻/动漫 5 genre 的 30-50 个场景原型（market/street/bedroom/forest/dream/...），每个原型带 `default_people_policy`。落到 `image_generation_profiles.json`。
[2026-06-15 16:20:38] AUTO-CHECK A07 (background-scene-type-taxonomy)
- [x] **A08** background-people-policy-classification — people_policy 三档分类规则：`empty_required` / `background_people_optional` / `background_groups_required`。研究场景类型→people_policy 的映射规则，public activity 场景（market/street/gate/harbor/banquet hall）允许背景群体，private/natural/abandoned/dreamlike 强制 empty_required。落到 `BackgroundSceneAnalyzerService._infer_people_policy`。
[2026-06-15 16:20:38] AUTO-CHECK A08 (background-people-policy-classification)
- [x] **A09** background-scene-analyzer-llm-call — LLM 调用实现：温度 0.2、response_format=json_object、6s 超时、失败降级到 segmenter 现有逻辑。研究 GLM-4-flash / glm-4.6 在 structured output 任务上的稳定性差异，参考智谱官方文档。
[2026-06-15 16:20:38] AUTO-CHECK A09 (background-scene-analyzer-llm-call)
- [x] **A10** background-scene-analyzer-cache-strategy — 缓存 key 设计：`(chapter_index, chapter_content_hash, outline_hash, story_bible_hash)` → `BackgroundSceneSpec[]`。研究 LLM 调用结果缓存的失效策略，参考 `prompt_rewriter_service._cache_key` 现有实现。
[2026-06-15 16:20:38] AUTO-CHECK A10 (background-scene-analyzer-cache-strategy)
- [x] **A11** background-scene-analyzer-fallback — 失败降级路径：API key 缺失 / 超时 / JSON 解析失败 / schema 校验失败时，回退到现有 `scene_segmenter_service._fallback_single`，但 `environment_hint_en` 留空（让下游 assembler 补 LLM 蒸馏）。研究多层 fallback 的优先级和日志埋点。
[2026-06-15 16:20:38] AUTO-CHECK A11 (background-scene-analyzer-fallback)
- [x] **A12** background-scene-analyzer-regression-test-set — 10 章固定"黄金章节正文"，覆盖单场景/双场景/多场景/同地点多状态。研究 golden set 测试的组织形式（输入 JSON + 期望 scene count + 关键环境词），参考 OpenAI Evals 框架。
[2026-06-15 16:20:38] AUTO-CHECK A12 (background-scene-analyzer-regression-test-set)

## Section B — StyleClassifier（风格分类器，worker B owns this section）

> 范围：`Docs/researches/Stage_Background_AR/style_classifier/`
> 关注：对每个 scene spec 从受控词表选 3-5 个 style tags，禁止新增剧情/人物。

- [x] **B01** background-style-classifier-service-skeleton — 新增 `BackgroundStyleClassifierService` 骨架。输入 `BackgroundSceneSpec`，输出 `style_tags[3..5]`。研究 SOTA 受控词表分类的接口设计（参考 OpenAI function calling 的 enum 参数），落到 `backend/app/services/background_style_classifier_service.py`。
[2026-06-15 16:20:38] AUTO-CHECK B01 (background-style-classifier-service-skeleton)
- [x] **B02** background-style-taxonomy-config — `background_style_taxonomy` 5 维受控词表：`art_style / color_palette / lens_or_camera_feel / texture_or_rendering / mood`，每维 5-10 个标签。研究如何按 genre 区分（古风偏水墨/写意；现代偏写实/电影感；科幻偏赛博朋克；奇幻偏厚涂；动漫偏新海诚），落到 `image_generation_profiles.json`。
[2026-06-15 16:20:38] AUTO-CHECK B02 (background-style-taxonomy-config)
- [x] **B03** background-style-classifier-system-prompt — 风格分类器 system prompt。研究"只从受控词表选标签、不自造新词"的硬约束写法，给出 3 个 genre 的 few-shot。落到 `image_generation_profiles.json::rewriter.background_style_classifier`。
[2026-06-15 16:20:38] AUTO-CHECK B03 (background-style-classifier-system-prompt)
- [x] **B04** background-style-coverage-rule — 至少覆盖 3 个维度的硬规则。研究如何在 schema validator 层（Pydantic validator）强制 `len(set(tag.dimension for tag in style_tags)) >= 3`，落到 `BackgroundStyleClassifierService._validate_coverage`。
[2026-06-15 16:20:38] AUTO-CHECK B04 (background-style-coverage-rule)
- [x] **B05** background-style-llm-call — LLM 调用：温度 0.1（更稳定）、response_format=json_object、3s 超时、失败时用 deterministic fallback（按 scene_type 默认配置选 tags）。研究低温度在分类任务上的稳定性，参考 OpenAI structured output 文档。
[2026-06-15 16:20:38] AUTO-CHECK B05 (background-style-llm-call)
- [x] **B06** background-style-deterministic-fallback — 失败时的 deterministic fallback：按 `(genre, scene_type)` 从配置里查默认 tags。研究如何让 fallback 仍输出语义合理的 3-5 tags，落到 `image_generation_profiles.json::background_style_fallback`。
[2026-06-15 16:20:38] AUTO-CHECK B06 (background-style-deterministic-fallback)
- [x] **B07** background-style-cache-strategy — 缓存 key：`(scene_spec_hash, genre, taxonomy_version)`。研究 taxonomy 更新时如何让旧 cache 自动失效（版本号机制）。
[2026-06-15 16:20:38] AUTO-CHECK B07 (background-style-cache-strategy)
- [x] **B08** background-style-batch-call — 批量分类：一个章节通常 1-4 scene，研究是否一次 LLM 调用批量输出（一次 round-trip）vs 串行（简单）。落到 `BackgroundStyleClassifierService.classify_many`。
[2026-06-15 16:20:38] AUTO-CHECK B08 (background-style-batch-call)
- [x] **B09** background-style-no-character-rule — 风格分类器硬规则：不得新增人物、动作、对白、角色关系。研究后置校验如何检测（如 `style_tags` 出现人名/动词就拒绝），落到 `_validate_no_character_tags`。
[2026-06-15 16:20:38] AUTO-CHECK B09 (background-style-no-character-rule)
- [x] **B10** background-style-regression-test-set — 30 个固定 `BackgroundSceneSpec`，每个期望 3-5 tags 且覆盖 ≥3 维度。研究 golden set 的组织形式和断言写法。
[2026-06-15 16:20:38] AUTO-CHECK B10 (background-style-regression-test-set)

## Section C — Assembler+Enforce（Prompt 组装 + 统一收口，worker C owns this section）

> 范围：`Docs/researches/Stage_Background_AR/assembler_enforce/`
> 关注：deterministic assembler 按固定顺序拼 prompt；统一收口函数追加固定 ban clause。

- [x] **C01** background-prompt-assembler-design — `BackgroundPromptAssembler.assemble(scene_spec, style_tags)` deterministic 实现。固定顺序：`scene_name → environment_description → architecture/props → lighting/weather/time_of_day → camera_shot_type → composition_constraints → people_policy → style_tags → ban_clause`。研究 OpenAI/Anthropic 图像 prompt 指南关于"scene → key details → composition → constraints"的推荐顺序。
[2026-06-15 16:20:38] AUTO-CHECK C01 (background-prompt-assembler-design)
- [x] **C02** background-environment-focus-function — `_enforce_background_environment_focus(scene_spec, prompt, forbidden_characters)` 统一收口函数设计。研究如何让函数读取 `scene_spec.people_policy.mode / scene_type / camera_shot_type / forbidden_characters` 做分支式追加，参考 Anthropic prompt engineering 的"invariant repetition" 原则。
[2026-06-15 16:20:38] AUTO-CHECK C02 (background-environment-focus-function)
- [x] **C03** background-fixed-ban-clause — 固定 ban clause 内容设计。研究哪些 phrase 在 CogView-4 上最有效（`No human subject` vs `no person as the focal point` vs `no centered lone figure`），落到 `image_generation_profiles.json::background_negative_clauses`。
[2026-06-15 16:20:38] AUTO-CHECK C03 (background-fixed-ban-clause)
- [x] **C04** background-empty-required-subclause — `empty_required` 模式追加严格无人子句（`No people, no humans, no silhouettes, no pedestrians, no guards, no soldiers, no servants, no crowd. Empty environment only.`）。研究 CogView-4 对比测试结果（控制变量：相同 scene spec，仅 people_policy 不同）。
[2026-06-15 16:20:38] AUTO-CHECK C04 (background-empty-required-subclause)
- [x] **C05** background-groups-allowed-subclause — `background_people_optional` / `background_groups_required` 模式追加"tiny distant anonymous groups only"子句。研究如何让 LLM 既允许远景人群又不出现 single-person subject，落到 `_enforce_background_environment_focus` 的分支逻辑。
[2026-06-15 16:20:38] AUTO-CHECK C05 (background-groups-allowed-subclause)
- [x] **C06** background-enforce-on-all-paths — 所有背景 prompt 路径（normal / rewriter / fallback / sanitize retry / safety fallback）都必须过 `_enforce_background_environment_focus`。研究如何用单元测试覆盖所有路径（每个路径写一个 test case），落到 `tests/test_background_enforce_coverage.py`。
[2026-06-15 16:20:38] AUTO-CHECK C06 (background-enforce-on-all-paths)
- [x] **C07** background-rewriter-schema-only — 背景 rewriter 改造：只接收结构化 scene spec 字段，禁止看 `outline.summary / segment_summary / story synopsis`。研究如何在 `prompt_builder_service.build_background_prompts_async` 移除 summary 兜底，落到该方法的字段构造。
[2026-06-15 16:20:38] AUTO-CHECK C07 (background-rewriter-schema-only)
- [x] **C08** background-cn-plot-strip-fallback — 兜底机制：若 rewriter 失败走 fallback builder 且 `scene_description` 含中文剧情，按逗号/句号切片丢弃含中文字符的片段。研究切片规则的边界（保留 named cast exclusion 段的中文名），落到 `image_generation_service._strip_chinese_segments`（已存在，需正式文档化设计依据）。
[2026-06-15 16:20:38] AUTO-CHECK C08 (background-cn-plot-strip-fallback)
- [x] **C09** background-camera-shot-vocabulary — `background_camera_shots` 受控词表：`24mm wide establishing / 35mm environment wide / high angle far / interior integrated wide / long compressed far / eye-level wide`。研究每个 shot 在背景图上的实际效果差异，落到 `image_generation_profiles.json`。
[2026-06-15 16:20:38] AUTO-CHECK C09 (background-camera-shot-vocabulary)
- [x] **C10** background-assembler-regression-test-set — 30 个 `(scene_spec, style_tags)` → 期望 final prompt 的 golden set。研究断言：必含关键 phrase、不含剧情词、不含角色名（除 named cast exclusion 段）。
[2026-06-15 16:20:38] AUTO-CHECK C10 (background-assembler-regression-test-set)

## Section D — Validator+Retry（生成后验收 + 自动重试，worker D owns this section）

> 范围：`Docs/researches/Stage_Background_AR/validator_retry/`
> 关注：bbox + 多模态混合验收；single prominent person 是硬失败；每次重试必须修改 prompt。

- [x] **D01** background-image-validator-architecture — `BackgroundImageValidator` 重构架构：bbox 启发式 + 多模态二次复核。研究 A/B 混合验收的层次设计（bbox 先筛 → 可疑进多模态），参考 OpenAI Evals 的 testing_criteria 分层。
[2026-06-15 16:20:38] AUTO-CHECK D01 (background-image-validator-architecture)
- [x] **D02** background-validator-bbox-detection — bbox person detector 选型：YOLOv8 / RT-DETR / OpenCV HOG。研究在 CPU 单机部署的延迟和准确率（背景图通常是绘画风格，YOLO 对真人最优但对绘画人物次优），落到 `background_image_validator_service.py::_detect_persons_bbox`。
[2026-06-15 16:20:38] AUTO-CHECK D02 (background-validator-bbox-detection)
- [x] **D03** background-validator-bbox-thresholds — bbox 验收阈值：`empty_required` 模式任何人物失败；其他模式单人物框面积 > 1-3% 失败、人物中心在图像中心区域失败、总人物面积过大失败。研究阈值调参方法论（golden set A/B），落到 `image_generation_profiles.json::background_validator_thresholds`。
[2026-06-15 16:20:38] AUTO-CHECK D03 (background-validator-bbox-thresholds)
- [x] **D04** background-validator-multimodal-judge — 多模态二次复核：把图作为 `input_image` 喂 GLM-4V，输出 `ValidationResult` schema `{is_environment_main_subject, has_named_cast, has_single_prominent_person, has_centered_lone_figure, has_large_foreground_person, needs_retry}`。研究 Structured Outputs 在视觉任务上的稳定性，落到 `background_image_validator_service.py::_validate_with_vlm`。
[2026-06-15 16:20:38] AUTO-CHECK D04 (background-validator-multimodal-judge)
- [x] **D05** background-validator-single-person-hard-fail — "single prominent person" 必须是硬失败，不是 warning。研究 OpenAI / Anthropic 图像生成文档对人物主体化的处理建议（moderation_blocked / image_generation_user_error），落到 validator 的判定逻辑。
[2026-06-15 16:20:38] AUTO-CHECK D05 (background-validator-single-person-hard-fail)
- [x] **D06** background-retry-escalation-policy — 重试 escalation 策略：第 1 次失败强化 ban clause；第 2 次失败把 people_policy 降级到 `empty_required`；第 3 次失败改用 scene-aware safety fallback。研究每次重试如何"修改 prompt"，参考 OpenAI 官方"不要在不修改请求的情况下盲发"原则。
[2026-06-15 16:20:38] AUTO-CHECK D06 (background-retry-escalation-policy)
- [x] **D07** background-retry-max-attempts — 最大重试次数：建议 3 次。研究 3 次的成本/质量平衡（每次 ~0.05 USD × 3 ≈ 0.15 USD/scene），落到 `BG_VALIDATE_MAX_RETRIES` 环境变量。
[2026-06-15 16:20:38] AUTO-CHECK D07 (background-retry-max-attempts)
- [x] **D08** background-scene-aware-safety-fallback — scene-aware safety fallback：根据当前 `scene_spec.scene_type / lighting / weather` 生成"同场景类型的保守环境版 prompt"（如"雨夜站台"→"空的雨夜站台建立镜头"）。研究如何让 fallback 保留场景语义而不是回落到通用古风庭院，落到 `image_generation_service._build_safety_fallback_background_prompt`。
[2026-06-15 16:20:38] AUTO-CHECK D08 (background-scene-aware-safety-fallback)
- [x] **D09** background-sanitize-then-re-enforce — sanitize 后必须重新执行 `_enforce_background_environment_focus`。研究现有 `_sanitize_prompt` 的位置（image_generation_service.py:207），改造为：sanitize → enforce → retry，落到该方法的调用链。
[2026-06-15 16:20:38] AUTO-CHECK D09 (background-sanitize-then-re-enforce)
- [x] **D10** background-validator-request-id-logging — 日志字段：`chapter_index / scene_id / scene_selector / scene_type / split_reason / people_policy.mode / forbidden_characters / matched_keywords / final_prompt_preview_500 / rewrite_applied / sanitize_applied / fallback_type / request_id / moderation_stage / validator_result / retry_count / final_status / saved_asset_id`。研究 OpenAI moderation_blocked / moderation_details 的记录要求，落到日志打印语句。
[2026-06-15 16:20:38] AUTO-CHECK D10 (background-validator-request-id-logging)

## Section E — Integration+Tests（集成 + 测试，worker E owns this section）

> 范围：`Docs/researches/Stage_Background_AR/integration_tests/`
> 关注：编排层接入新链路；forbidden_characters 多源合并；vn_graph scene_selector 匹配；测试覆盖。

- [x] **E01** background-asset-mgmt-orchestration — `AssetManagementService.generate_chapter_backgrounds` 改造为 5 步编排：analyzer → classifier → assembler → generator → validator → persist。研究如何最小破坏现有接口（保留返回结构 `{total, generated, failed, results}`），落到 `asset_management_service.py`。
[2026-06-15 16:20:38] AUTO-CHECK E01 (background-asset-mgmt-orchestration)
- [x] **E02** background-forbidden-chars-multi-source — `forbidden_characters` 合并：`outline.characters + SceneSegment.characters_present + StoryBible.characters(name+english_name+aliases+nicknames) + chapter text 命中`。研究别名命中扫描的性能优化（Aho-Corasick / 简单 substring），落到 `_collect_forbidden_characters`。
[2026-06-15 16:20:38] AUTO-CHECK E02 (background-forbidden-chars-multi-source)
- [x] **E03** background-cn-en-name-pairs — 在最终 prompt 里保留中文名 + 英文别名双版本（中文模型识别中文角色名，英文 negative clause 对英文别名敏感）。研究 prompt 拼接格式，落到 `_enforce_background_environment_focus` 的 named cast exclusion 段。
[2026-06-15 16:20:38] AUTO-CHECK E03 (background-cn-en-name-pairs)
- [x] **E04** background-scene-selector-stable-slug — `scene_selector` 由代码生成稳定 ASCII slug（如 `old_station__night_rain__empty`），不让 LLM 自由发挥。研究 slug 生成规则（scene_type + time_of_day + weather + people_policy.mode），落到 `BackgroundSceneAnalyzerService._build_scene_selector`。
[2026-06-15 16:20:38] AUTO-CHECK E04 (background-scene-selector-stable-slug)
- [x] **E05** background-vn-graph-scene-selector-matching — `LLMVNGraphGenerator` 改为按 `scene_selector` slug 匹配背景资产，不再按 `scene_location`。研究 slug 匹配的容错（前缀匹配 / fuzzy matching），落到 `llm_vn_graph_generator.py`。
[2026-06-15 16:20:38] AUTO-CHECK E05 (background-vn-graph-scene-selector-matching)
- [x] **E06** background-asset-variation-info — 把 `BackgroundSceneSpec` 全字段塞进 `Asset.variation_info` JSON（不改 schema）。研究 variation_info 现有字段和新字段的兼容性，落到 `AssetManagementService._persist_background_asset`。
[2026-06-15 16:20:38] AUTO-CHECK E06 (background-asset-variation-info)
- [x] **E07** background-test-split-by-visual-state — 测试：同 location 但日夜/天气变化 → 切成 2 scene。研究断言写法（输入章节正文 + 期望 scene count + 期望 split_reason），落到 `tests/test_background_scene_split.py`。
[2026-06-15 16:20:38] AUTO-CHECK E07 (background-test-split-by-visual-state)
- [x] **E08** background-test-no-plot-in-output — 测试：所有背景 prompt 不含剧情动词（发现/到达/看见/听见）和角色名（除 named cast exclusion 段）。研究黑名单的覆盖度（每跑一次 golden set 都断言），落到 `tests/test_background_no_plot.py`。
[2026-06-15 16:20:39] AUTO-CHECK E08 (background-test-no-plot-in-output)
- [x] **E09** background-test-forbidden-chars-merge — 测试：`forbidden_characters` 合并 4 个来源 + 别名命中。研究 mock 数据组织（outline / segment / bible / chapter text 各给一份），落到 `tests/test_background_forbidden_chars.py`。
[2026-06-15 16:20:39] AUTO-CHECK E09 (background-test-forbidden-chars-merge)
- [x] **E10** background-test-validator-retry — 测试：validator 拒绝 single-person 图 → 自动重试 → 第 2 次强化 ban → 通过。研究 mock validator 输出的方式（第 1 次返回 needs_retry=True，第 2 次返回 passed=True），落到 `tests/test_background_validator_retry.py`。
[2026-06-15 16:20:39] AUTO-CHECK E10 (background-test-validator-retry)
- [x] **E11** background-test-portrait-keyframe-unaffected — 测试：portrait / keyframe 链路完全不受本次重构影响。研究对比 baseline 输出（重构前的 prompt 和重构后的 prompt 完全一致），落到 `tests/test_portrait_keyframe_regression.py`。
[2026-06-15 16:20:39] AUTO-CHECK E11 (background-test-portrait-keyframe-unaffected)
- [x] **E12** background-test-end-to-end-regeneration — 端到端测试：通过 API 端点（POST `/api/image-generation/{projectId}/backgrounds/generate-chapter/{chapterIndex}`）对已有项目重生背景，验证新链路全跑通。研究 API 集成测试的组织形式（mock CogView-4 + 真 LLM 调用），落到 `tests/test_background_e2e.py`。
[2026-06-15 16:20:39] AUTO-CHECK E12 (background-test-end-to-end-regeneration)

---

## 完成判据（重申）

每个 `[ ]` 项打 `[x]` 必须满足：

1. 在 `Docs/researches/Stage_Background_AR/<section>/<slug>.md` 存在一篇文档。
2. 文档完全围绕该项主题，引用 ≥1 个 SOTA 实践（论文/官方文档/权威指南）。
3. 文档明确把推荐做法落到本仓库的具体文件和方法（最好到行号）。
4. 文档所有推荐与 [`00_philosophy.md`](researches/Stage_Background_AR/00_philosophy.md) 一致——结构化 schema 驱动、不引入新模型供应商、bbox+多模态混合验收、不动 portrait/keyframe/数据库/前端。


<!-- EXECUTION_CHECKLIST_BEGIN -->

---

## Execution Checklist（执行清单 — cron 自动维护）

> **本节由 execution-cron-builder 注入并维护**。研究阶段（A-E）已完成；本节是真正的实现 checklist。
>
> **Layer 顺序硬约束**：L1 → L2 → L3 → L4 → L5。任何低层有 `[ ]` 未完成时，高层 `[x]` 会被自动 reset。
>
> **完成判据**：每个 `[ ]` 项打 `[x]` 必须满足：(1) 对应代码/测试文件真实存在 (2) `cd backend && pytest` 通过（相关 test 不 fail）(3) 不能是 mock/placeholder/fake-success。
> **反 mock 规则**：纯文档、stub handler、fake response、mocked download、schema without runnable path 都不算完成。

### Layer 1 — Foundation（基础：config + schemas）

- [x] **L1.01** `backend/app/config/image_generation_profiles.json` 新增 `background_scene_taxonomy` 节（6 大类，每类 4-8 个原型，每个原型带 default_people_policy）
- [x] **L1.02** `backend/app/config/image_generation_profiles.json` 新增 `background_style_taxonomy` 节（5 维度，每维度按 genre 分桶 4-10 标签）
- [x] **L1.03** `backend/app/config/image_generation_profiles.json` 新增 `background_camera_shots` 节（6 个 shot 类型 + prompt_phrase）
- [x] **L1.04** `backend/app/config/image_generation_profiles.json` 新增 `background_negative_clauses` 节（base / empty_required / background_people_optional / background_groups_required / establishing_shot_enforcement / escalation_level_1）
- [x] **L1.05** `backend/app/config/image_generation_profiles.json` 新增 `background_people_policy_keywords` 节（public_activity / empty_signal 两组关键词）
- [x] **L1.06** `backend/app/config/image_generation_profiles.json` 新增 `background_style_forbidden_keywords` 节（character_words / action_words / relationship_words / dialogue_words）
- [x] **L1.07** `backend/app/config/image_generation_profiles.json` 新增 `background_style_fallback` 节（每 genre default + by_scene_category）
- [x] **L1.08** `backend/app/config/image_generation_profiles.json` 新增 `background_safety_fallback_templates` 节（每 scene_type 一个模板 + default）
- [x] **L1.09** `backend/app/config/image_generation_profiles.json` 新增 `background_validator_thresholds` 节（empty_required / background_people_optional / background_groups_required 三档阈值 + image_center_region）
- [x] **L1.10** `backend/app/config/image_generation_profiles.json` 新增 `rewriter.background_scene_extractor` 节（model / temperature=0.2 / system_prompt 5 段分区 / response_format）
- [x] **L1.11** `backend/app/config/image_generation_profiles.json` 新增 `rewriter.background_style_classifier` 节（model / temperature=0.1 / system_prompt / response_format）
- [x] **L1.12** `backend/app/config/image_generation_profiles.json` 顶层新增 `_version` 字段（语义版本，便于 cache 失效）
- [x] **L1.13** `backend/app/config/image_generation_profiles.json` 配置 JSON 通过 `python -c "import json; json.load(open(...))"` 语法校验

### Layer 2 — Schemas（Pydantic 模型）

- [x] **L2.01** `backend/app/schemas.py` 新增 `PeoplePolicy` Pydantic 模型（mode: Literal[3 档] + rationale: str）
- [x] **L2.02** `backend/app/schemas.py` 新增 `StyleTag` Pydantic 模型（dimension: Literal[5 维] + value: str）
- [x] **L2.03** `backend/app/schemas.py` 新增 `BackgroundSceneSpec` Pydantic 模型（18 字段，含 Field 约束 min/max_length、Literal camera_shot_type、Pattern scene_selector）
- [x] **L2.04** `backend/app/schemas.py` 新增 `BBox` Pydantic 模型（x1/y1/x2/y2/conf）
- [x] **L2.05** `backend/app/schemas.py` 新增 `ValidationResult` Pydantic 模型（passed / stage / reason / is_hard_fail）
- [x] **L2.06** `backend/app/schemas.py` 新增 `GenerationResult` dataclass/Pydantic（status / image / prompt / retry_count / fallback_type / reason）
- [x] **L2.07** `backend/app/schemas.py` 新增 `BackgroundPromptData` Pydantic（schema-only 字段，不含 summary/plot）
- [x] **L2.08** `backend/tests/test_background_schemas.py` 测试 BackgroundSceneSpec 各字段 validator（pattern / length / Literal）触发正确的 ValidationError
- [x] **L2.09** `backend/tests/test_background_schemas.py` 测试 PeoplePolicy mode 三档枚举拒绝非法值
- [x] **L2.10** `backend/app/schemas.py` 新增异常类 `PlotContaminationError / StyleCoverageError / StyleCharacterLeakError`

### Layer 3 — Services（核心服务实现）

- [x] **L3.01** `backend/app/services/background_scene_analyzer_service.py` 新建 `BackgroundSceneAnalyzerService` 类骨架（__init__ / analyze_chapter 入口）
- [x] **L3.02** 实现 `_call_llm` 方法（temperature=0.2, max_tokens=2500, timeout=8s, response_format=json_object, 2 retries with exponential backoff）
- [x] **L3.03** 实现 `_validate_no_plot` 方法（PLOT_KEYWORDS_ZH 黑名单 + forbidden_characters 漏入检测）
- [x] **L3.04** 实现 `_validate_split_reasons` 方法（同 fingerprint 连续 scene 拒绝）
- [x] **L3.05** 实现 `_infer_people_policy` 方法（按 scene_category 查表 + public_activity/empty_signal 关键词覆盖）
- [x] **L3.06** 实现 `_build_scene_selector` deterministic slug（scene_type + time_bucket + weather + mode + physical_state）
- [x] **L3.07** 实现 `_cache_key / _cache_get / _cache_set` 磁盘缓存（backend/.cache/bg_scene_analyzer/）
- [x] **L3.08** 实现 `_fallback_to_segmenter` 方法（复用现有 SceneSegmenterService + environment_description 留空）
- [x] **L3.09** `backend/app/services/background_style_classifier_service.py` 新建 `BackgroundStyleClassifierService` 骨架
- [x] **L3.10** classifier 实现 `_call_llm`（temperature=0.1, timeout=3s, no retries, failure→fallback）
- [x] **L3.11** classifier 实现 `_validate_coverage`（≥3 维度 + 每维度≤1 + 总数 3-5）
- [x] **L3.12** classifier 实现 `_validate_in_taxonomy`（按 genre 查词表拒绝）
- [x] **L3.13** classifier 实现 `_validate_no_character_tags`（forbidden_keywords 黑名单）
- [x] **L3.14** classifier 实现 `_deterministic_fallback`（按 genre + scene_category 查默认 tags）
- [x] **L3.15** classifier 实现 `classify_many` batch 模式 + 串行 fallback
- [x] **L3.16** classifier 实现 `_cache_key` + taxonomy_version 失效
- [x] **L3.17** `backend/app/services/background_prompt_assembler_service.py` 新建 `BackgroundPromptAssembler.assemble`（9 段 deterministic 顺序拼接）
- [x] **L3.18** assembler 实现 9 个 `_section_*` 私有方法（scene_name / environment / architecture_props / lighting_weather_time / camera / composition / people_policy / style_tags / ban_clause）
- [x] **L3.19** `backend/app/services/image_generation_service.py` 新增 `_enforce_background_environment_focus(spec, prompt, forbidden_characters)` 统一收口函数
- [x] **L3.20** enforce 实现 `_format_named_cast_exclusion`（中英双版本分段）
- [x] **L3.21** enforce 实现 mode 分支追加（empty_required / optional / groups_required 子句）
- [x] **L3.22** enforce 实现 idempotent 检测（已含 ban clause 不重复追加）
- [x] **L3.23** `backend/app/services/image_generation_service.py` 改造 `_sanitize_prompt` 末尾调用 enforce（sanitize → enforce）
- [x] **L3.24** `backend/app/services/image_generation_service.py` 改造 `_build_safety_fallback_background_prompt` 为 scene-aware（用 spec.scene_type 查模板）
- [x] **L3.25** `backend/app/services/image_generation_service.py` 新增 `_scene_aware_safety_fallback` 方法（强制 empty_required + 不再 retry）
- [x] **L3.26** `backend/app/services/image_generation_service.py` 新增 `generate_background_with_validation` 方法（含 retry escalation：1 强化 ban / 2 降 policy / 3 safety fallback）
- [x] **L3.27** `backend/app/services/image_generation_service.py` 新增 `_retry_with_escalation` 方法
- [x] **L3.28** `backend/app/services/image_generation_service.py` 新增 `GenerationLogContext` dataclass + 标准化日志埋点（request_id 串联）
- [x] **L3.29** `backend/app/services/prompt_builder_service.py` 改造 `build_background_prompts_async` 接收 specs 参数，不再从 summary 兜底
- [x] **L3.30** `backend/app/services/prompt_rewriter_service.py` 改造 `to_cogview_prompt` 接收 BackgroundPromptData（schema-only），禁止访问 outline/summary

### Layer 3b — Validator Service（验收服务）

- [x] **L3b.01** `backend/app/services/background_image_validator_service.py` 重构为 `BackgroundImageValidator` 类（bbox + VLM 双层）
- [x] **L3b.02** validator 实现 `_detect_persons_bbox`（YOLOv8n，CPU 推理，confidence_threshold 配置化）
- [x] **L3b.03** validator 实现 `_bbox_verdict`（按 mode 应用 background_validator_thresholds，返回 hard_pass/hard_fail/suspicious）
- [x] **L3b.04** validator 实现 `_validate_with_vlm`（GLM-4V，input_image + JSON schema 输出 ValidationResult）
- [x] **L3b.05** validator 实现 `validate` 主方法（bbox 先筛 → suspicious 进 VLM → 返回 ValidationResult with is_hard_fail）
- [x] **L3b.06** validator 实现 `HARD_FAIL_REASONS` 常量集合 + `_make_verdict` 硬失败判定
- [x] **L3b.07** `backend/models/yolov8n.pt` 模型文件真实下载（首次启动自动 fetch from ultralytics CDN；非 mock 占位）
- [x] **L3b.08** validator 支持环境变量 `BG_VALIDATE_MAX_RETRIES=3` 配置

### Layer 4 — Integration（编排与集成）

- [x] **L4.01** `backend/app/services/asset_management_service.py` 改造 `generate_chapter_backgrounds` 为 5 步编排（analyzer → classifier → assembler → generator → validator → persist）
- [x] **L4.02** 实现 `_dedup_scenes` 方法（按 scene_fingerprint 5 字段去重）
- [x] **L4.03** 实现 `_scene_fingerprint` 函数（scene_type + time_bucket + weather + mode + physical_state）
- [x] **L4.04** 实现 `_collect_forbidden_characters`（4 源合并：outline + specs + bible name/aliases/nicknames + chapter_content substring 命中）
- [x] **L4.05** 实现 `_persist_background_asset`（写入 Asset.variation_info JSON：schema_version + 18 spec 字段 + generation metadata）
- [x] **L4.06** 硬失败时不写 Asset 表（验证 `_persist_background_asset_if_passed` 拒收 is_hard_fail 结果）
- [x] **L4.07** `backend/app/services/llm_vn_graph_generator.py` 改造 `_match_background_asset` 按 scene_selector 匹配（exact > prefix > fuzzy 0.7 > scene_type fallback）
- [x] **L4.08** `backend/app/services/llm_vn_graph_generator.py` 实现 `_read_scene_spec_from_asset`（从 variation_info 反序列化 BackgroundSceneSpec）
- [x] **L4.09** `backend/app/services/asset_management_service.py` 接口签名保持兼容（返回 {total, generated, failed, results} 不变）
- [x] **L4.10** `backend/app/services/image_generation_service.py` 保留旧 `_build_background_prompt` 函数（向后兼容旧 caller，标记 deprecated）

### Layer 5 — Tests（测试套件）

- [x] **L5.01** `backend/tests/test_background_enforce_coverage.py` 测试 enforce 在 5 条路径（normal/rewriter/sanitize_retry/fallback/safety_fallback）都被调用
- [x] **L5.02** `backend/tests/test_background_enforce_coverage.py` 测试 enforce idempotent（多次调用不重复追加）
- [x] **L5.03** `backend/tests/test_background_enforce_coverage.py` 测试 enforce 触发 CN-PLOT-STRIP（注入中文剧情 → strip 清除）
- [x] **L5.04** `backend/tests/test_background_scene_split.py` 5 个 SPLIT case（日夜 / 天气 / 物理状态 / 人群状态 / 对话不变）
- [x] **L5.05** `backend/tests/test_background_no_plot.py` 测试所有 background prompt 不含剧情动词 + 角色名只在 Named cast 段
- [x] **L5.06** `backend/tests/test_background_forbidden_chars.py` 测试 4 源合并 + 别名命中 + 去重
- [x] **L5.07** `backend/tests/test_background_validator_retry.py` 测试 validator 第 1 次 fail → retry → 第 2 次通过
- [x] **L5.08** `backend/tests/test_background_validator_retry.py` 测试连续 2 次 fail → 第 3 次 people_policy 降级
- [x] **L5.09** `backend/tests/test_background_validator_retry.py` 测试 retry 3 次都 fail → status='failed'
- [x] **L5.10** `backend/tests/test_background_validator_retry.py` 测试硬失败图不写 Asset 表
- [x] **L5.11** `backend/tests/test_background_analyzer_golden.py` 10 个 GA case（5 genre × 单/双/多场景）
- [x] **L5.12** `backend/tests/test_background_style_golden.py` 30 个 GB case（5 genre × 6 scene_category）
- [x] **L5.13** `backend/tests/test_background_assembler_golden.py` 30 个 GC case（deterministic prompt 断言）
- [x] **L5.14** `backend/tests/test_background_empty_required_ablation.py` 10 case × 3 seed（empty_required 必须 0 人物框）
- [x] **L5.15** `backend/tests/test_portrait_keyframe_regression.py` portrait/keyframe prompt 重构前后完全一致（10+10 case）
- [x] **L5.16** `backend/tests/test_background_e2e.py` 端到端 API 调用（mock CogView-4 + 真 LLM）
- [x] **L5.17** `backend/tests/test_background_schemas.py` Pydantic 字段约束测试
- [x] **L5.18** `cd backend && pytest` 整体绿（包括原有 test_background_environment_focus / test_background_no_human_subject / test_prompt_rewriter / test_scene_segmenter / test_chapter_outline_schema）

<!-- EXECUTION_CHECKLIST_END -->

## Worker 分工

| Worker | Section | 输出目录 | 项数 |
|---|---|---|---|
| A | Section A — SceneAnalyzer | `Docs/researches/Stage_Background_AR/scene_analyzer/` | 12 |
| B | Section B — StyleClassifier | `Docs/researches/Stage_Background_AR/style_classifier/` | 10 |
| C | Section C — Assembler+Enforce | `Docs/researches/Stage_Background_AR/assembler_enforce/` | 10 |
| D | Section D — Validator+Retry | `Docs/researches/Stage_Background_AR/validator_retry/` | 10 |
| E | Section E — Integration+Tests | `Docs/researches/Stage_Background_AR/integration_tests/` | 12 |

**总计 54 项**（远小于 100 上限）。
