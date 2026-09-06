# C02 — background-environment-focus-function

## 背景

新链路有 5 条背景 prompt 路径：normal builder / rewriter / sanitize retry / fallback builder / safety fallback。**任何一条路径输出 prompt 前都必须经过统一收口函数**，否则就会出现"normal 路径禁人，但 sanitize 路径漏禁"这种漏洞。

历史教训：用户在早期反馈"重生 retry 后仍有剧情残留"——就是因为 retry path 走 fallback builder，绕过了 enforce 函数。

`_enforce_background_environment_focus` 是**所有路径的最后一道闸门**。

## SOTA 实践

**Anthropic "invariant repetition"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts)）：

> 重要 invariant 应在 prompt 末尾重复，而不是只在开头说一次。重复约束让模型在生成过程中持续遵守。

**OpenAI moderation funnel pattern**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：所有 user-generated content 在落库前必须过同一 moderation 入口——单一收口点是工业级安全实践。

**SD community "always-on negative prompt"**（[`github.com/AUTOMATIC1111/stable-diffusion-webui`](https://github.com/AUTOMATIC1111/stable-diffusion-webui)）：所有 prompt 自动追加固定 negative 段，是 SD WebUI 默认行为——证明"末段固定追加"的可行性。

## 落地建议

新增 `image_generation_service.py::_enforce_background_environment_focus`：

```python
def _enforce_background_environment_focus(
    self,
    spec: BackgroundSceneSpec,
    prompt: str,
    forbidden_characters: list[str] = None,
) -> str:
    cfg = self._profiles["background_negative_clauses"]
    parts = [prompt.strip()]

    # 1. 通用 ban clause（所有模式都追加）
    parts.append(cfg["base"])

    # 2. 按 people_policy.mode 分支追加
    mode = spec.people_policy.mode
    if mode == "empty_required":
        parts.append(cfg["empty_required"])
    elif mode == "background_people_optional":
        parts.append(cfg["background_people_optional"])
    elif mode == "background_groups_required":
        parts.append(cfg["background_groups_required"])

    # 3. 按 camera_shot_type 追加（环境镜头强化）
    if "establishing" in spec.camera_shot_type or "wide" in spec.camera_shot_type:
        parts.append(cfg["establishing_shot_enforcement"])

    # 4. 命名角色显式排除（中英双版本，见 E03）
    if forbidden_characters:
        cn_names = [n for n in forbidden_characters if re.search(r"[一-鿿]", n)]
        en_names = [n for n in forbidden_characters if n and not re.search(r"[一-鿿]", n)]
        if cn_names:
            parts.append("Named cast exclusion (CN): " + ", ".join(cn_names))
        if en_names:
            parts.append("Named cast exclusion (EN): " + ", ".join(en_names))

    # 5. 最后兜底：CN-LEAK-GUARD / CN-PLOT-STRIP（见 C08）
    parts.append(self._strip_chinese_segments(parts[-1]))

    return "\n\n".join(p for p in parts if p and p.strip())
```

**所有调用点必须过它**：

```python
# normal builder
prompt = self._build_background_prompt(spec)
prompt = self._enforce_background_environment_focus(spec, prompt)

# rewriter 输出
prompt = await self._rewriter.rewrite(...)
prompt = self._enforce_background_environment_focus(spec, prompt)

# sanitize retry
prompt = self._sanitize_prompt(prompt)
prompt = self._enforce_background_environment_focus(spec, prompt)

# fallback builder
prompt = self._build_safety_fallback_background_prompt(spec)
prompt = self._enforce_background_environment_focus(spec, prompt)
```

## 风险与权衡

1. **enforce 多次调用导致 ban clause 重复**：retry 流程可能 enforce 2-3 次。权衡：enforce 函数先用 regex 检查 `base clause` 是否已存在，已存在则跳过追加。
2. **forbidden_characters 中英文混合时格式混乱**：见 E03 处理。

## 完成判据自检

- ✅ 围绕"统一收口函数"，不跑题到具体 ban clause 内容（C03）。
- ✅ 引用 Anthropic invariant repetition、OpenAI moderation funnel、SD always-on negative。
- ✅ 落到 `image_generation_service.py::_enforce_background_environment_focus`。
- ✅ 符合哲学：单一收口保证所有路径同质，不引入新模型供应商。
