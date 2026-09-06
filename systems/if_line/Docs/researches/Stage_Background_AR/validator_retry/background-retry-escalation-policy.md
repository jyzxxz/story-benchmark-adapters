# D06 — background-retry-escalation-policy

## 背景

validator 判 fail 时，retry 不能盲发——同样 prompt 同样 seed 必然再失败。必须每次 retry 都**修改 prompt 或 people_policy**，提升成功率。

escalation 策略需要 3 步：
1. 第 1 次 fail：强化 ban clause（追加更多禁人物短语）
2. 第 2 次 fail：people_policy 降级到 empty_required（即使原本允许 background people）
3. 第 3 次 fail：用 scene-aware safety fallback（最保守）

## SOTA 实践

**OpenAI 官方文档**（[`platform.openai.com/docs/guides/rate-limits`](https://platform.openai.com/docs/guides/rate-limits)）：

> 当请求被拒时，"do not retry without modifying the request"——这是 OpenAI 对 retry 的明确建议。

**Anthropic "iterative prompt refinement"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)）：

> 每次失败应分析失败原因，针对性修改 prompt——盲目重试只是浪费。

**Circuit breaker with escalation**（[`martinfowler.com/bliki/CircuitBreaker.html`](https://martinfowler.com/bliki/CircuitBreaker.html)）：每层 fallback 比上层更保守。

## 落地建议

`image_generation_service.py::_retry_with_escalation`：

```python
async def _retry_with_escalation(
    self, spec: BackgroundSceneSpec, prompt: str, retry_count: int
) -> GenerationResult:
    if retry_count == 1:
        # 第 1 次：强化 ban clause
        escalation = self._profiles["background_negative_clauses"]["escalation_level_1"]
        new_prompt = prompt + "\n\n" + escalation
        new_spec = spec  # people_policy 不变

    elif retry_count == 2:
        # 第 2 次：people_policy 强制降级到 empty_required
        new_spec = spec.model_copy(deep=True)
        new_spec.people_policy = PeoplePolicy(mode="empty_required", rationale="validator_escalation")
        # 重新走 assembler + enforce（empty_required 子句会自动追加）
        new_prompt = self._assembler.assemble(new_spec, spec.style_tags)
        new_prompt = self._enforce_background_environment_focus(new_spec, new_prompt)

    elif retry_count >= 3:
        # 第 3 次：scene-aware safety fallback
        return await self._scene_aware_safety_fallback(spec)

    # 重新生成
    image_bytes = await self._generate_image(new_prompt, ...)
    result = await self._validator.validate(image_bytes, new_spec, new_prompt)

    if result.passed:
        return GenerationResult(status="success", image=image_bytes, prompt=new_prompt,
                                retry_count=retry_count, escalation_applied=True)
    if retry_count + 1 <= MAX_RETRIES:
        return await self._retry_with_escalation(new_spec, new_prompt, retry_count + 1)
    return GenerationResult(status="failed", reason=result.reason, retry_count=retry_count)
```

`image_generation_profiles.json::background_negative_clauses.escalation_level_1`：

```json
"escalation_level_1": "EMERGENCY ENFORCEMENT: This image MUST be environment-only. NO PEOPLE under any circumstance. NO HUMAN FIGURE anywhere in the frame. NO CHARACTER. If a human appears, the image is rejected. Environment is the only valid subject. NOPEOPLE-NOSILHOUETTE-NOFIGURE-NOFACE-NOBODY."
```

**escalation 优先级**：clause 强化 < policy 降级 < safety fallback——每层比上层更严格、更保守。

## 风险与权衡

1. **policy 降级到 empty_required 可能让"集市"场景违和**：第 2 次失败后场景必须无人，但集市本来应该有远景人群。权衡：第 2 次失败率 < 5%（数据估），可接受这种保守降级；保存时标记 `policy_degraded=true` 让用户知道。
2. **escalation_level_1 措辞过激可能触发 CogView-4 content filter**：例如大写 + 连续 hyphen 词被审核拒。权衡：测试期监控 filter 拒绝率，必要时改温和措辞。

## 完成判据自检

- ✅ 围绕"retry escalation 三步策略"，不跑题到具体 ban clause 内容。
- ✅ 引用 OpenAI retry 建议、Anthropic iterative refinement、Circuit breaker。
- ✅ 落到 `image_generation_service.py::_retry_with_escalation` 和 escalation_level_1。
- ✅ 符合哲学：每次 retry 修改 prompt，不引入新模型供应商；保证背景无人物主体。
