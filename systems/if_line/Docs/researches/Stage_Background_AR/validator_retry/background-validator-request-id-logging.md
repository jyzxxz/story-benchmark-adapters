# D10 — background-validator-request-id-logging

## 背景

validator 和 retry 流程涉及多次 LLM + image gen 调用，失败时排查需要完整 trace。当前日志散落各处，难以关联。需要标准化的日志字段，每个生成请求都有 request_id 串联。

OpenAI 官方建议：moderation 失败时必须记录 request_id、moderation_details 等，便于客服/审计。

## SOTA 实践

**OpenAI moderation_blocked error handling**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：

> 记录 request_id、moderation_details、prompt snippet 用于事后排查。

**OpenTelemetry standard spans**（[`opentelemetry.io/docs/concepts/signals/traces/`](https://opentelemetry.io/docs/concepts/signals/traces/)）：

> 分布式 trace 标准：每个 span 含 trace_id、span_id、parent_id、attributes。

**Structured logging best practices**（[`cloud.google.com/logging/docs/structured-logging`](https://cloud.google.com/logging/docs/structured-logging)）：

> JSON 格式日志，每条含 timestamp、severity、message、custom_fields。

## 落地建议

每个生成请求分配 `request_id`（uuid4），所有日志带 request_id：

```python
@dataclass
class GenerationLogContext:
    request_id: str
    chapter_index: int
    scene_id: str
    scene_selector: str
    scene_type: str
    split_reason: str
    people_policy_mode: str
    forbidden_characters: list[str]
    matched_keywords: list[str] = field(default_factory=list)
    final_prompt_preview_500: str = ""
    rewrite_applied: bool = False
    sanitize_applied: bool = False
    fallback_type: str = ""  # scene_aware / generic / none
    moderation_stage: str = ""  # pre_gen / post_gen / validator_bbox / validator_vlm
    validator_result: str = ""  # pass / hard_fail / soft_fail
    retry_count: int = 0
    final_status: str = ""  # success / failed / fallback_used
    saved_asset_id: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v not in ("", [], 0, False) or k in ("retry_count", "final_status")}
```

`image_generation_service.generate_background_with_validation` 改造：

```python
import uuid, logging
logger = logging.getLogger("background_gen")

async def generate_background_with_validation(self, spec, prompt, ...):
    ctx = GenerationLogContext(
        request_id=str(uuid.uuid4()),
        chapter_index=spec.chapter_index,
        scene_id=spec.scene_id,
        scene_selector=spec.scene_selector,
        scene_type=spec.scene_type,
        split_reason=spec.split_reason,
        people_policy_mode=spec.people_policy.mode,
        forbidden_characters=spec.forbidden_characters,
        final_prompt_preview_500=prompt[:500],
    )

    for attempt in range(MAX_RETRIES + 1):
        ctx.retry_count = attempt
        try:
            image_bytes = await self._generate_image(prompt, request_id=ctx.request_id)
            result = await self._validator.validate(image_bytes, spec, prompt, request_id=ctx.request_id)
            ctx.validator_result = result.stage
            ctx.moderation_stage = "validator"

            if result.passed:
                ctx.final_status = "success"
                ctx.saved_asset_id = await self._persist(spec, image_bytes)
                logger.info("background_gen_completed", extra=ctx.to_dict())
                return ...
            # fail
            ctx.final_status = "retrying"
            logger.warning("background_gen_retry", extra=ctx.to_dict())
        except Exception as e:
            ctx.final_status = "error"
            logger.error("background_gen_error", extra={**ctx.to_dict(), "error": str(e)})
            raise

    ctx.final_status = "failed"
    logger.error("background_gen_max_retries", extra=ctx.to_dict())
    return GenerationResult(status="failed", ...)
```

**日志后端**：复用现有 `logging` 模块，输出 JSON 到 stdout（容器化部署）或文件。

## 风险与权衡

1. **日志字段过多 → 单条日志膨胀**：~20 字段。权衡：to_dict 过滤空值，单条 <2KB。
2. **request_id 在并发场景下需 thread-safe**：用 contextvars 而非全局变量。权衡：FastAPI + asyncio 已天然 async-safe。

## 完成判据自检

- ✅ 围绕"标准化日志字段"，不跑题到 retry 或 validator 实现。
- ✅ 引用 OpenAI moderation logging、OpenTelemetry、Google structured logging。
- ✅ 落到 `image_generation_service.py::GenerationLogContext`。
- ✅ 符合哲学：可观测性是质量保证，不引入新模型供应商。
