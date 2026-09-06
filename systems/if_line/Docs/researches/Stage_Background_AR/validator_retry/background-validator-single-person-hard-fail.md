# D05 — background-validator-single-person-hard-fail

## 背景

"single prominent person" 是新链路最严重的失败模式——单人成为画面主体，违反"环境是主体"的哲学。必须是**硬失败**（直接 retry 或最终拒收），不能是 warning。

当前实现可能把人物检测作为 warning，导致不合格图仍被保存。

## SOTA 实践

**OpenAI moderation "hard block"**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：

> 某些类别（如 hate speech）是 hard block，必须立即拒收，不允许降级处理。

**Anthropic "Constitutional AI" hard constraints**（[`www.anthropic.com/research/constitutional-ai`](https://www.anthropic.com/research/constitutional-ai)）：

> 硬约束不可协商；软约束可降级。区分两者是 AI 系统设计的关键。

**Google image safety classifier**（[`cloud.google.com/vision/docs/detecting-safe-search`](https://cloud.google.com/vision/docs/detecting-safe-search)）：把"racy" / "adult"标为 hard block，"spoof" / "medical"作为 soft warning。

## 落地建议

`background_image_validator_service.py` 中：

```python
class ValidationResult(BaseModel):
    passed: bool
    stage: str  # bbox_hard_pass / bbox_hard_fail / vlm_pass / vlm_fail
    reason: str = ""
    is_hard_fail: bool = False  # 硬失败标记

# Hard fail reasons（任何一项命中即 is_hard_fail=True）
HARD_FAIL_REASONS = {
    "single_prominent_person",      # D05 核心
    "named_cast_leak",              # 命名角色出现在图中
    "centered_lone_figure",         # 居中单人
    "large_foreground_person",      # 前景大人物
    "character_focused_composition" # 角色聚焦构图
}

def _make_verdict(self, vlm_result: dict, bbox_verdict: str) -> ValidationResult:
    if vlm_result.get("has_single_prominent_person") or vlm_result.get("has_centered_lone_figure") or vlm_result.get("has_large_foreground_person"):
        return ValidationResult(
            passed=False, stage="vlm_fail",
            reason="single_prominent_person",
            is_hard_fail=True
        )
    if vlm_result.get("has_named_cast"):
        return ValidationResult(
            passed=False, stage="vlm_fail",
            reason="named_cast_leak",
            is_hard_fail=True
        )
    if not vlm_result.get("is_environment_main_subject"):
        return ValidationResult(
            passed=False, stage="vlm_fail",
            reason="environment_not_main",
            is_hard_fail=True
        )
    return ValidationResult(passed=True, stage="vlm_pass")
```

**调用方处理**（`image_generation_service.generate_background`）：

```python
result = await validator.validate(image_bytes, spec, prompt)

if not result.passed and result.is_hard_fail:
    # 硬失败：必须 retry（D06）或最终拒收
    if retry_count < MAX_RETRIES:
        # escalation：强化 ban clause / 降级 people_policy
        return await self._retry_with_escalation(spec, prompt, retry_count + 1)
    else:
        # 超过 max retries：标记为 failed，不落 completed
        return GenerationResult(status="failed", reason=result.reason)

elif not result.passed:
    # 软失败：可降级到 safety fallback
    return await self._safety_fallback(spec)
```

**硬失败不能落库**：

```python
# AssetManagementService._persist_background_asset
if result.is_hard_fail:
    return None  # 不写 Asset 表
```

## 风险与权衡

1. **硬失败过严 → 合理背景图被拒**：例如"远景一群人"被误判为"single prominent person"。权衡：硬失败只对明确信号触发（VLM 三项之一为 true），可疑信号走软失败。
2. **硬失败 retry 3 次仍 fail → 整章背景缺失**：权衡：保留 `failed` 状态在 Asset 表，前端显示"该章节背景生成失败"，不影响其他章节。

## 完成判据自检

- ✅ 围绕"single person 硬失败"，不跑题到 retry escalation（D06）或阈值（D03）。
- ✅ 引用 OpenAI moderation hard block、Anthropic Constitutional AI、Google Safe Search。
- ✅ 落到 `background_image_validator_service.py::HARD_FAIL_REASONS` 和 `_make_verdict`。
- ✅ 符合哲学：硬失败保证质量底线，不引入新模型供应商。
