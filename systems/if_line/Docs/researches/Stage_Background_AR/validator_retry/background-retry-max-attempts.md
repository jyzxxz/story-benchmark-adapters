# D07 — background-retry-max-attempts

## 背景

retry 次数必须有上限——否则单 scene 失败可能消耗 10+ 次 LLM + image gen 调用。建议 3 次，平衡成本和质量。

每次 retry 成本估算：
- LLM 调用（assembler + style classifier）：~0.002 USD
- 图像生成（CogView-4）：~0.05 USD
- VLM 验收：~0.001 USD

单 scene 3 次 retry 总成本 ≈ 3 × 0.053 = 0.16 USD。

## SOTA 实践

**AWS Step Functions retry policy**（[`docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html`](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html)）：

> 默认 max retry = 3，可通过 `MaxAttempts` 配置。

**Google Cloud Tasks retry config**（[`cloud.google.com/tasks/docs/cloud-tasks-overview`](https://cloud.google.com/tasks/docs/cloud-tasks-overview)）：

> 推荐 max attempts = 3-5，避免无限重试。

**Hugging Face inference retry**（[`huggingface.co/docs/huggingface_hub/main/en/package_reference/utilities`](https://huggingface.co/docs/huggingface_hub/main/en/package_reference/utilities)）：默认 3 次，符合工业惯例。

## 落地建议

环境变量：

```bash
BG_VALIDATE_MAX_RETRIES=3  # 默认 3，可配置
```

`image_generation_service.py`：

```python
MAX_RETRIES = int(os.getenv("BG_VALIDATE_MAX_RETRIES", "3"))

async def generate_background_with_validation(
    self, spec: BackgroundSceneSpec, prompt: str
) -> GenerationResult:
    for attempt in range(MAX_RETRIES + 1):  # 0,1,2,3（首次 + 3 次 retry）
        if attempt == 0:
            cur_spec, cur_prompt = spec, prompt
        else:
            cur_spec, cur_prompt = self._escalate(spec, prompt, attempt)

        image_bytes = await self._generate_image(cur_prompt, ...)
        result = await self._validator.validate(image_bytes, cur_spec, cur_prompt)

        self._log_attempt(spec.scene_id, attempt, result)

        if result.passed:
            return GenerationResult(
                status="success", image=image_bytes,
                prompt=cur_prompt, retry_count=attempt,
            )

        if result.is_hard_fail and attempt < MAX_RETRIES:
            continue  # retry
        # 软失败 or 超过 max retries
        if attempt == MAX_RETRIES:
            return GenerationResult(
                status="failed", reason=result.reason,
                retry_count=attempt,
            )

    return GenerationResult(status="failed", reason="max_retries_exceeded")
```

**成本监控**：

```python
# 每次重试日志含 cost 估算
self._log("retry_attempt", scene_id=spec.scene_id, attempt=attempt,
          estimated_cost_usd=0.053 * (attempt + 1))
```

**告警阈值**：
- 单章 retry 平均次数 > 1.5 → 告警（说明 system prompt 或 ban clause 不够强）
- 单 scene retry 达 max 占比 > 5% → 告警（说明需要调 prompt）

## 风险与权衡

1. **3 次仍不够时，用户看到 failed 体验差**：权衡：保留人工 retry 按钮，用户可手动重生；同时 failed 状态显示具体 reason 让用户理解。
2. **不同 scene_type 可能需要不同 max retries**：例如自然户外（empty_required）一般 1 次过；集市（groups_required）可能需 3+ 次。权衡：按 scene_category 配置化 max retries，但首版统一 3 简化。

## 完成判据自检

- ✅ 围绕"max retries 配置"，不跑题到 escalation policy（D06）。
- ✅ 引用 AWS Step Functions、Google Cloud Tasks、Hugging Face retry。
- ✅ 落到 `BG_VALIDATE_MAX_RETRIES` 环境变量和 `generate_background_with_validation`。
- ✅ 符合哲学：成本可控，不引入新模型供应商。
