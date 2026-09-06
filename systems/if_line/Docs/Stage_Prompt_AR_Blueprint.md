 

# Stage_Prompt_AR_Blueprint

> **权威 AR 蓝图**。本文件是优化 cron 的唯一真理来源。每项 `[ ]` 只能在对应的 `Docs/researches/Stage_Prompt_AR/<section>/<slug>.md` 存在且符合 `00_philosophy.md` 完成判据时被改为 `[x]`。
>
> **设计哲学**: 把中文叙事文本通过 LLM 重写为信息密度高、视觉可解析、CogView-4 友好的结构化英文 prompt，覆盖 portrait / background / keyframe 三类资产。详见 [`Docs/researches/Stage_Prompt_AR/00_philosophy.md`](researches/Stage_Prompt_AR/00_philosophy.md)。

---

## 源范围（Source Scope）

| 资产类型   | Builder 方法                                                                            | Prompt 构建方法                                                                             | 输出尺寸               |
| ---------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------- |
| Portrait   | `prompt_builder_service.build_portrait_prompts` (`prompt_builder_service.py:23`)    | `image_generation_service._build_portrait_prompt` (`image_generation_service.py:498`)   | 1024×1024 / 768×1280 |
| Background | `prompt_builder_service.build_background_prompts` (`prompt_builder_service.py:120`) | `image_generation_service._build_background_prompt` (`image_generation_service.py:614`) | 1920×1080             |
| Keyframe   | `prompt_builder_service.build_keyframe_prompts` (`prompt_builder_service.py:202`)   | `image_generation_service._build_keyframe_prompt` (`image_generation_service.py:703`)   | 1280×720              |

LLM 入口：`backend/app/services/llm_service.py`（已存在，复用）。
配置文件：`backend/app/config/image_generation_profiles.json`。
环境变量：`AI_IMAGE_API_KEY` / `AI_IMAGE_BASE_URL` / `AI_IMAGE_MODEL=cogview-4`。

---

## Section A — Portrait（角色立绘，worker A owns this section）

> 范围：`Docs/researches/Stage_Prompt_AR/portrait/`

- [X] **A01** portrait-llm-rewriter-design — 设计 `build_portrait_prompts` 后置的 LLM Rewriter 步骤：输入 `{character_id, name, appearance_zh, emotion, outfit, pose, genre}`，输出 `{english_subject, english_visual_details, english_negative_minimal}`。研究 SOTA 结构化 prompt 重写范式（如 SDXL 的 structured prompts、DALL-E 3 的 natural language rewriting）。
- [X] **A02** portrait-translation-fusion — 中英翻译与视觉化提炼合流。研究如何让 LLM 把"身着素白丧服的年轻女子"直接转成 `young woman in flowing white mourning hanfu, pale complexion, somber expression`，而不是先翻译再拼接。
- [X] **A03** portrait-shot-composition — 立绘景别（半身/全身）的英文表达最佳实践。当前 `FULL BODY SHOT, entire character visible from HEAD to FEET` 是否最优？参考 NovelAI/ComfyUI 社区的 shot list 规范。
- [X] **A04** portrait-emotion-injection — 情绪 prompt 注入顺序与权重。当前 emotion 段在 prompt 中段，CogView-4 是否对前置 emotion 更敏感？参考 SD 系 attention 研究。
- [X] **A05** portrait-outfit-disambiguation — 装束词歧义消除。`hanfu` 在 CogView-4 上常被画成和服/汉化影楼装；研究如何用 `tang dynasty hanfu, crossed collar, wide sleeves` 等具体术语消歧。
- [X] **A06** portrait-pose-vocabulary — 姿态词库扩充。当前只有 6 个 pose (`standing/pointing/combat/kneeling/reaching/defensive`)，研究 VN/Galgame 立绘常用姿态词典。
- [X] **A07** portrait-transparent-bg-strategy — 透明背景策略。当前 prompt 同时说"transparent background"和"simple solid background"——冲突。研究 CogView-4 下哪种表述能让 rembg 后处理产出最佳透明 PNG。
- [X] **A08** portrait-no-text-strategy — 禁文字策略重构。当前在 prompt 头部塞 200 token 否定词，研究是否应改为：(a) 移到 prompt 尾部，(b) 缩短到 10 词以内，(c) 完全依赖 rembg/crop 后处理。
- [X] **A09** portrait-seed-consistency — seed 一致性真相。CogView-4 官方文档对 seed 支持的实际语义；当前代码注释说"可能不支持"，需查证到底支不支持，给出正确实现。
- [X] **A10** portrait-cache-key-impact — 缓存 key 当前包含 emotion 但不含 LLM rewriter 版本。研究引入 rewriter 后如何让缓存 key 仍稳定可命中。
- [X] **A11** portrait-variation-matrix — 情绪×装束×姿态变体矩阵的合理规模。当前默认 3 个变体，研究一致性 vs 成本的甜点。
- [X] **A12** portrait-regression-test-set — 设计 10 个固定角色的"黄金 prompt 集合"，用于 LLM Rewriter 上线前后的 A/B 评估。

## Section B — Background（场景背景，worker B owns this section）

> 范围：`Docs/researches/Stage_Prompt_AR/background/`

- [X] **B01** background-llm-rewriter-design — `build_background_prompts` 后置 LLM Rewriter。输入 `{scene_zh, visual_keywords, mood, genre, summary_zh}`，输出 `english_environment_description`（80-120 词，主体是空间和氛围，不是事件）。
- [X] **B02** background-scene-extraction-improvement — 当前 `build_background_prompts` 从 summary 兜底找"帐中/营帐/战场"等关键词；研究用 LLM 直接抽取场景地点（一次调用同时翻译+提炼+规范化）。
- [X] **B03** background-no-humans-strategy — "严禁出现人物"策略。当前用 `ZERO PEOPLE, ZERO FIGURES, no humans...` 一长串，研究 CogView-4 是否对正面描述（`empty courtyard, abandoned hall`）更有效。
- [X] **B04** background-mood-vocabulary — 氛围词库扩充。当前 8 个 mood 粒度粗，研究 VN 背景常用的"时间段×天气×情绪氛围"三维组合。
- [X] **B05** background-architecture-vocabulary — 古风/现代/科幻建筑词汇库。`宫殿`该被翻译成 `imperial palace, vermilion walls, golden roof tiles` 而不是单字 `palace`。
- [X] **B06** background-soldier-colocation — 当前 `soldier_co_location_words` 是 hardcoded 列表。研究改为 LLM 判定"该场景是否需要严禁人物"。
- [X] **B07** background-cinematic-composition — 16:9 背景的构图表述最佳实践。`Wide cinematic establishing shot` 是否最优？参考新海诚风格背景的 SD prompt 模板。
- [X] **B08** background-lighting-injection — 光照与氛围的英文表达。Mood 段当前混入光照词，研究是否独立成 `lighting` 字段。
- [X] **B09** background-multi-mood-per-scene — 同一场景多氛围变体（白天/夜晚/雨天）共享什么、差异化什么，研究 LLM 一次重写多输出。
- [X] **B10** background-cache-strategy — 背景 LLM 重写后的缓存 key 设计（scene + mood 还是 LLM 输出 hash？）。
- [X] **B11** background-watermark-crop-validation — 当前默认裁剪底部 80px，研究 CogView-4 实际水印高度，避免误裁画面。
- [X] **B12** background-regression-test-set — 10 个固定场景的黄金集合。

## Section C — Keyframe（关键帧，worker C owns this section）

> 范围：`Docs/researches/Stage_Prompt_AR/keyframe/`

- [X] **C01** keyframe-llm-rewriter-design — `build_keyframe_prompts` 后置 LLM Rewriter。输入 `{event_zh, scene_zh, characters[], action_zh, emotion, genre}`，输出一段融合所有人/物/动作/场景的英文叙事 prompt（不是字段拼接）。
- [X] **C02** keyframe-action-extraction-llm — 当前 `_extract_action_from_conflict` 用硬编码 `{"打": "fighting"}` 单字映射；研究用 LLM 把冲突文本转成英文动作描述。
- [X] **C03** keyframe-multi-character-coherence — 多角色关键帧的一致性。当前 `characters[:3]` 外观直接拼接，研究 LLM 如何描述多角色互动。
- [X] **C04** keyframe-emotion-mapping-rewrite — 当前 emotion_map 把章节情绪粗映射到 4 档；研究 LLM 直接生成情绪描述。
- [X] **C05** keyframe-composition-vocabulary — 关键帧构图表述。`cinematic shot, dramatic scene` 太泛，研究 CG 关键帧常用的镜头语言。
- [X] **C06** keyframe-character-appearance-from-storybible — `_find_character_appearance` 现在直接取 StoryBible.appearance（中文），研究如何在 keyframe prompt 中融入该角色的英文 appearance。
- [X] **C07** keyframe-event-extraction-multi — 当前一章只生成 1 个关键帧；研究用 LLM 从 chapter_content 提取 3-5 个关键时刻。
- [X] **C08** keyframe-action-verb-strength — 动作词的力度。`fighting` vs `clashing swords, sparks flying`，研究 LLM 如何把弱动词替换成视觉强动词。
- [X] **C09** keyframe-no-text-strategy — 同 A08，但针对关键帧场景。
- [X] **C10** keyframe-cache-strategy — 关键帧缓存 key 设计（事件名+角色组合 hash）。
- [X] **C11** keyframe-regression-test-set — 10 个固定关键帧场景的黄金集合。
- [X] **C12** keyframe-failure-fallback — 关键帧生成失败（内容审核/超 token）时的 prompt 退化策略。

## Section D — Shared（共享基础设施，worker D owns this section）

> 范围：`Docs/researches/Stage_Prompt_AR/shared/`。本 section 服务 A/B/C 三个 section。

- [X] **D01** llm-rewriter-pipeline-architecture — 设计统一的 LLM Rewriter 接口：`async def rewrite(asset_type, fields) -> dict`，三个 builder 共用。研究放 `llm_service.py` 还是新建 `prompt_rewriter_service.py`。
- [X] **D02** llm-system-prompt-design — LLM Rewriter 的 system prompt 模板。研究如何用 few-shot 示例约束输出 JSON schema、token 长度、英文风格。
- [X] **D03** llm-output-json-schema — 定义输出 JSON schema。字段：`{subject, details[], lighting, composition, style, negative_minimal[]}`。
- [X] **D04** llm-failure-fallback — LLM 调用失败/JSON 解析失败时如何降级到原 prompt builder（保证可用性）。
- [X] **D05** llm-cost-analysis — LLM Rewriter 单次调用成本 × 全项目预期调用量。研究用 GLM-4-flash 还是 deepseek-chat 这类低成本模型。
- [X] **D06** llm-caching-strategy — LLM Rewriter 自身的结果缓存（输入 hash → 输出），避免重复 rewrite。
- [X] **D07** llm-batch-mode — 多个 prompt 一次 LLM 调用（batch rewrite），降低 round-trip。
- [X] **D08** prompt-token-budget — CogView-4 prompt 最佳 token 长度。研究官方文档/实测，给出 60/120/200 词的对比建议。
- [X] **D09** prompt-ordering-impact — prompt 词序对 CogView-4 的影响。当前是"否定词 → 风格 → 主体"，研究是否应为"主体 → 风格 → 细节 → 否定词"。
- [X] **D10** prompt-quality-eval-method — 如何量化评估 prompt 改进效果。研究 CLIP score（文本-图相似度）作为离线指标。
- [X] **D11** prompt-iteration-workflow — A/B 测试工作流：旧 prompt vs LLM-rewritten prompt，生成同 seed 对比。
- [X] **D12** prompt-guard-rails — Rewriter 输出后置校验：必含主体词、长度范围、不含中文残留、negative 不超过 5 词。
- [X] **D13** prompt-logging-format — LLM Rewriter 输入/输出/prompt 模板的日志格式。
- [X] **D14** prompt-config-migration — `image_generation_profiles.json` 是否需要新增 `rewriter` 字段。
- [X] **D15** prompt-doc-writing-standard — AR 研究文档自身的写作规范：必须包含哪些章节。
- [X] **D16** prompt-integration-test-plan — LLM Rewriter 接入主流程后的端到端集成测试方案。
- [X] **D17** prompt-rollback-plan — 上线后若质量回退，如何快速禁用 Rewriter 回到原 builder。
- [X] **D18** prompt-metrics-dashboard — 监控指标：LLM 调用成功率、平均 token、CogView-4 内容审核拒绝率、缓存命中率。

---

## 跨 section 依赖

- A/B/C 三个 section 的 `*-llm-rewriter-design` 项（A01/B01/C01）都依赖 D01（pipeline 架构）。D01 应优先完成。
- 所有 `*-regression-test-set` 项（A12/B12/C11）依赖 D10（评估方法）和 D11（A/B 工作流）。
- D17（rollback）必须在 D01 完成后才能写——没架构就谈不上回滚。

## 总计

48 个 checklist 项，分布在 4 个 section（A:12 / B:12 / C:12 / D:18），均 ≤ 100 上限。
