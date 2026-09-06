# A03 — background-scene-extractor-system-prompt

## 背景

System prompt 决定 analyzer LLM 的行为上限。当前 `prompt_rewriter_service.py` 的 system prompt 是 free-form 重写风格，没分区、没硬规则编号、没 few-shot，导致 LLM 把 outline.summary 当主体段重写。如果直接复用，新 analyzer 也会复制同样的剧情污染问题。

需要在 `image_generation_profiles.json::rewriter.background_scene_extractor` 节点写一个**身份清晰、规则编号化、5 genre few-shot 覆盖**的新 system prompt。

## SOTA 实践

**OpenAI prompt engineering guide**（[`platform.openai.com/docs/guides/prompt-engineering`](https://platform.openai.com/docs/guides/prompt-engineering)）推荐 system message 的 4 段式：

```
1. Identity（你是谁）
2. Instructions（你的任务 / 硬规则 / 不能做什么）
3. Examples（few-shot 输入输出对）
4. Context（本次具体输入）
```

**Anthropic context engineering** 文档（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags)）强调：用 XML 标签分区 `<Identity>`、`<Rules>`、`<Examples>`、`<Context>`，让 LLM 更稳定地区分上下文层次。

OpenAI cookbook "Structured Outputs"（[`cookbook.openai.com/examples/structured_outputs_overview`](https://cookbook.openai.com/examples/structured_outputs_overview)）建议 few-shot 至少覆盖 3 种不同 genre，否则模型在 OOD 输入上会漂移。

## 落地建议

`image_generation_profiles.json` 新增节：

```json
"rewriter": {
  "background_scene_extractor": {
    "model": "${SEGMENTER_MODEL}",
    "temperature": 0.2,
    "max_tokens": 2500,
    "system_prompt": "<Identity>你是章节背景场景分析器，只输出环境 schema...</Identity>\n<Rules>\n1. ...\n2. ...\n</Rules>\n<Examples>\n[genre: 古风] chapter: ...\noutput: {...}\n[genre: 现代] ...\n[genre: 科幻] ...\n[genre: 奇幻] ...\n[genre: 动漫] ...\n</Examples>",
    "response_format": {"type": "json_object"}
  }
}
```

**硬规则编号化**（每个规则一行，便于后置校验引用）：

```
1. 只输出 BackgroundSceneSpec[]，不输出任何解释文字
2. 严禁输出故事概要 / 对白 / 角色冲突 / 人物动作
3. 默认 people_policy.mode = empty_required；只有 public activity 场景（market/street/gate/harbor/banquet）才允许 background_people_optional 或 background_groups_required
4. 切场景的硬规则：location/time/weather/crowd/physical_state 任一变化必须切
5. environment_description ≥ 60 字，必须含建筑/陈设/光照/天气/时间中至少 3 项
6. scene_selector 字段留空字符串，由下游 deterministic slug builder 填
7. forbidden_characters 字段：只复制输入 StoryBible 角色列表，不增不减
8. 永远禁止命名角色出现在 environment_description 中
```

5 genre few-shot 必须每个 genre 一个，覆盖：古风（野战营帐）、现代（深夜办公室）、科幻（轨道空间站走廊）、奇幻（法师塔内室）、动漫（雨后黄昏学校天台）。

## 风险与权衡

1. **system prompt 过长 → 单次调用 token 超限**：5 个 few-shot × 每个 ~300 token ≈ 1500 token。权衡：把 few-shot 压缩到"输入 30 字 + 输出 60 字"的极简形式，靠规则编号承载约束力。
2. **不同 genre 共享同一 system prompt → 模型混淆**：例如古风场景里 LLM 自由加入科幻词。权衡：在 user prompt 里塞 `<ProjectGenre>` 标签，让 system prompt 保持中性，genre 信息走 context 区。

## 完成判据自检

- ✅ 围绕"system prompt 设计"，不跑题到 schema 或 service 实现。
- ✅ 引用 OpenAI / Anthropic 官方 prompt engineering 指南。
- ✅ 落到 `image_generation_profiles.json::rewriter.background_scene_extractor`。
- ✅ 符合哲学：通过 system prompt 硬规则化约束 LLM，不引入 VLM、不替换 CogView-4。
