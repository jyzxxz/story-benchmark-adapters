# Stage_Background_AR — 设计哲学

## 一句话哲学

**把章节背景图链路从"故事概要倒推图片"重构为"从章节里抽取环境场景 schema，再组装环境 prompt"——分两步：第一步用 LLM 输出结构化 `BackgroundSceneSpec`，第二步用 deterministic assembler 拼 prompt，最后用生成后验收 + 自动重试保证"环境是主体、无人物主体化、无命名角色"。**

## 为什么是这条主线

当前背景图链路（`AssetManagementService.generate_chapter_backgrounds` → `SceneSegmenterService.segment_chapter` → `PromptBuilder.build_background_prompts_async` → `PromptRewriter` → `ImageGenerationService.generate_background`）的根本问题是**目标函数错了**：

1. **以 `outline/summary` 为输入**: prompt 主体段含"陆沉与苏晚晴到达南城公园现场，死者是一名音乐家..."这种剧情摘要，CogView-4 拿到中文剧情当主体，画出来就是剧情插画而不是环境背景。
2. **按 `location` 去重**: 同一地点"黄昏空荡走廊"和"深夜灭灯狼藉"被压成一张背景。
3. **rewriter 自由发挥**: rewriter 看到中文剧情容易把对白/动作/角色关系翻译进 prompt。
4. **forbidden_characters 来源单薄**: 只用 `outline.characters`，不合并 story_bible/aliases/正文命中，命名角色和别名漏排除。
5. **通用 safety fallback**: 审核失败时回落到"古风庭院"，场景语义被冲淡。
6. **无生成后验收**: 单一突出前景人物图仍被保存。

哲学主张用**结构化 schema 驱动 + 统一收口函数 + 生成后验收**三件事一次性解决，而不是继续在 prompt 字符串上打补丁。

## 这条哲学下什么值得做、什么不做

**值得做**:
- 新增 `BackgroundSceneAnalyzerService`，调用 LLM 对章节正文做"环境场景分析"，输出严格 JSON schema（`BackgroundSceneSpec[]`）。
- 新增 `StyleClassifierService`，从受控词表为每个 scene 选 3-5 个 style tags。
- 用 deterministic `BackgroundPromptAssembler` 按"scene → details → composition → constraints"固定顺序拼 prompt，不让 LLM 自由发挥剧情。
- 新增统一收口函数 `_enforce_background_environment_focus(scene_spec, prompt, forbidden_characters)`，所有路径（normal/rewriter/fallback/sanitize/safety）必须过它。
- 新增 `BackgroundImageValidator`，生成后用 bbox + 多模态混合验收，"single prominent person" 是硬失败条件。
- 重试时必须修改 prompt 或 people policy，不能盲发。
- 按 `scene_fingerprint`（location+time+weather+state）去重，不再只按 location。
- 把所有规则配置化进 `app/config/image_generation_profiles.json`。

**不做**:
- 不替换底层图像模型（CogView-4 调用层工作正常）。
- 不动 portrait / keyframe 链路。
- 不改数据库 schema（用 `Asset.variation_info` JSON 扩展）。
- 不动前端 / API 路由签名。
- 不引入新 LLM 供应商（复用现有 GLM/zhipu 配置）。
- **研究产出只是文档**。AR 阶段不写产品代码——实现留给后续 execution-cron。

## 完成判据（每个 AR 项的硬性要求）

每个 checklist 项只有在以下三个条件全部满足时才能打 `[x]`：

1. 在 `Docs/researches/Stage_Background_AR/{section}/` 下存在一篇以该项 slug 命名的 `.md` 文档。
2. 该文档：完全围绕该项主题（不跑题），引用至少 1 个 SOTA 实践（论文/官方文档/权威指南），明确把推荐做法落到本仓库的具体文件和方法（最好到行号）。
3. 该文档的所有推荐必须与本哲学一致——结构化 schema 驱动、不引入新模型供应商、不引入 VLM-only 闭环（必须是 bbox+多模态混合）、不动 portrait/keyframe/数据库/前端。

## 边界

- **涉及的核心源代码文件**:
  - `backend/app/services/asset_management_service.py`（编排层，`generate_chapter_backgrounds`）
  - `backend/app/services/scene_segmenter_service.py`（场景分段，已有；要扩展或新增 `BackgroundSceneAnalyzerService`）
  - `backend/app/services/prompt_builder_service.py`（背景 prompt 构建）
  - `backend/app/services/prompt_rewriter_service.py`（背景 rewriter）
  - `backend/app/services/image_generation_service.py`（生成 + enforce + sanitize + fallback）
  - `backend/app/services/background_image_validator_service.py`（已有；要重构为混合验收）
  - `backend/app/config/image_generation_profiles.json`（配置中心）
  - `backend/app/services/llm_vn_graph_generator.py`（背景匹配 scene_selector）
- **不动**: 前端、数据库 schema、API 路由签名、CogView-4 调用层、portrait / keyframe。
- **研究产出只是文档**。
