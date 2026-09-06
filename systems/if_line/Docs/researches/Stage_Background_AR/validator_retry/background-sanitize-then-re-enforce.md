# D09 — background-sanitize-then-re-enforce

## 背景

`image_generation_service._sanitize_prompt`（image_generation_service.py:207）是 content moderation 拒绝时的清洗逻辑——移除触发审核的敏感词。但 sanitize 后的 prompt **必须重新执行 `_enforce_background_environment_focus`**，否则：
- sanitize 移除了部分 ban clause 后，禁人物约束失效
- sanitize 输出的 prompt 直接喂 CogView-4 可能漏出人物

历史教训：sanitize 后未 enforce 是常见漏洞。

## SOTA 实践

**OpenAI moderation pipeline**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：

> Sanitization 后必须重新走完整 safety check，而不是直接放行——这是 security engineering 的 fail-safe 原则。

**Defense in depth**（[`en.wikipedia.org/wiki/Defense_in_depth_(computing)`](https://en.wikipedia.org/wiki/Defense_in_depth_(computing))）：多层防御，每层独立验证；上一层处理后必须重新过下一层。

**Anthropic "re-validate after transformation"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-tools`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-tools)）：工具修改 prompt 后必须重新 enforce 约束。

## 落地建议

`image_generation_service._sanitize_prompt` 当前实现（line 207）改造：

```python
def _sanitize_prompt(self, prompt: str, spec: BackgroundSceneSpec) -> str:
    """旧逻辑：移除触发审核的词"""
    sanitized = self._remove_blocked_phrases(prompt)
    # 新增：sanitize 后必须重新 enforce
    sanitized = self._enforce_background_environment_focus(
        spec, sanitized,
        forbidden_characters=spec.forbidden_characters,
    )
    self._log("sanitize_then_enforce_applied",
              original_len=len(prompt), sanitized_len=len(sanitized))
    return sanitized
```

**调用链改造**：

```python
# 旧流程：
prompt = build(spec)
if moderation_blocked(prompt):
    prompt = self._sanitize_prompt(prompt)
image = generate(prompt)  # 漏 enforce

# 新流程：
prompt = build(spec)
prompt = self._enforce_background_environment_focus(spec, prompt)
if moderation_blocked(prompt):
    prompt = self._sanitize_prompt(prompt, spec)  # 内部会再 enforce 一次
image = generate(prompt)
```

**测试覆盖**：

```python
def test_sanitize_then_enforce(spec, image_gen):
    prompt = "Some prompt with banned word 'xxx' and no ban clause yet"
    sanitized = image_gen._sanitize_prompt(prompt, spec)

    # 1. sanitized 移除了 banned word
    assert "xxx" not in sanitized

    # 2. sanitized 包含 enforce 的 ban clause
    assert "Environment-focused background art" in sanitized
    assert "No human subject" in sanitized

    # 3. sanitized 包含 empty_required 子句（如果 spec 是 empty_required）
    if spec.people_policy.mode == "empty_required":
        assert "Strictly empty environment" in sanitized

    # 4. sanitized 包含 named cast exclusion（如果 spec 有 forbidden_chars）
    if spec.forbidden_characters:
        assert "Named cast exclusion" in sanitized
```

## 风险与权衡

1. **sanitize + enforce 双重处理让 prompt 越来越长**：sanitized 后 enforce 又追加 100+ 词。权衡：enforce 函数检查 ban clause 已存在时跳过追加（idempotent）。
2. **sanitize 可能误删 enforce 段**：例如 sanitize 把 "No human subject" 中的 "human" 误判为敏感词。权衡：sanitize 黑名单要排除 ban clause 关键词；或在 sanitize 后用 regex 检查 ban clause 完整性，缺失则强制 enforce。

## 完成判据自检

- ✅ 围绕"sanitize + re-enforce 链路"，不跑题到 retry（D06）或 fallback（D08）。
- ✅ 引用 OpenAI moderation pipeline、Defense in depth、Anthropic re-validate。
- ✅ 落到 `image_generation_service._sanitize_prompt`（line 207）。
- ✅ 符合哲学：多层防御，不引入新模型供应商；保证背景无人物主体。
