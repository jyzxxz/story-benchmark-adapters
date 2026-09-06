# backend/app/services/ — folder learn

> 服务层目录合成。覆盖本 subset 涉及的 9 个服务文件。

## 角色分类

### A. LLM 调用 / 内容驱动范本（P1/P2/P4 直接复刻）
- `background_scene_analyzer_service.py` —— ⭐ `_call_llm` 模式 + hash 缓存。所有新 analyzer 的模板。
- `background_prompt_assembler_service.py` —— 结构化字段 → 目标字符串装配。
- `scene_segmenter_service.py` —— LLM 切分长文本输出结构化段列表。

### B. 现有底层服务（P1/P2 直接调，不改内部）
- `tts_service.py` —— `synthesize(text, character_voice, emotion, ...)` 返回 audio_url，带缓存。
- `image_generation_service.py` —— `generate_portrait/background_with_validation/keyframe`，并发 + validator 重试。

### C. 编排入口（P1.3 改造点）
- `asset_management_service.py` —— Asset CRUD + 批量生成入口。P1 加 `auto_demand`，P2 voice_line 也走这里落库。

### D. vngraph 生成（P3.1 改造点）
- `vn_graph_generator.py` —— 规则版。
- `llm_vn_graph_generator_simplified.py` —— LLM 版。两个 P3.1 都要扩字段。

### E. Prompt 集中处（P4.3 改造点）
- `prompt_templates.py` —— 反 AI 味负例 + rewrite_hint。

## 横向模式总结

1. **LLM 调用永远走 `_call_llm` 模式**：`response_format=json_object` + `asyncio.wait_for` + 重试 + 指数退避。
2. **缓存永远用 hash key**：把全部输入字段 hash 成 sha256，落 `.cache/<service>/<hash>.json`。
3. **批量永远走 batch_size 切批 + 批内并发**，参考 `generate_all_portraits`。
4. **配置永远走 env 变量 + fallback 链**，参考 analyzer 的 env 解析。
5. **Asset 表是统一资源表**：`asset_type` 区分 `portrait/background/keyframe/voice_line`，`image_url` 字段图/音共用。

## 未在 subset 但相关
- `prompt_builder_service.py` / `prompt_rewriter_service.py` —— 立绘/关键帧 prompt 构建，P1 会用到。
- `background_style_classifier_service.py` / `background_image_validator_service.py` —— v2 链路辅助件。
- `workflow_engine.py` —— 项目状态机，P1/P2/P3 完成后可能要加新状态。
- `llm_service.py` —— LLM 调用底层，P4 rewrite_hint 参数要穿过去。
