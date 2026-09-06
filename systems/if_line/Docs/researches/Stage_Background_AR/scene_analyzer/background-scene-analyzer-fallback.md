# A11 — background-scene-analyzer-fallback

## 背景

LLM 调用可能因以下原因失败：
1. `SEGMENTER_API_KEY` 缺失或过期
2. API rate limit (429)
3. 超时（8s 仍无响应）
4. JSON 解析失败（模型偶发输出非 JSON）
5. Pydantic schema 校验失败（字段类型/长度不符）

当前 `scene_segmenter_service.py` 已有 `_fallback_single`（用 regex 抽 location），但只覆盖单 scene case。新 analyzer 需要**多 scene fallback**——既能保证可用性，又不破坏"环境 schema 驱动"主线。

## SOTA 实践

**Circuit breaker pattern**（[`martinfowler.com/bliki/CircuitBreaker.html`](https://martinfowler.com/bliki/CircuitBreaker.html)）：连续 N 次失败后短时间熔断，避免雪崩。

**Graceful degradation best practices**（[`cloud.google.com/architecture/best-practices-for-operating-on-failure`](https://cloud.google.com/architecture/best-practices-for-operating-on-failure)）：失败时降级到"语义最接近、最简单"的实现，不直接报错。

**OpenAI cookbook "Error handling"**（[`cookbook.openai.com/examples/error_handling`](https://cookbook.openai.com/examples/error_handling)）：分类处理 timeout / rate_limit / invalid_request，每类用不同 retry/fallback 策略。

## 落地建议

```python
async def analyze_chapter(self, ...):
    cache_key = self._cache_key(...)
    if cached := self._cache_get(cache_key):
        return cached

    try:
        raw = await self._call_llm(...)
        specs = [BackgroundSceneSpec(**s) for s in raw["scenes"]]
        self._validate_no_plot(specs)
        self._validate_split_reasons(specs)
        self._infer_people_policy(specs)
        self._build_scene_selector(specs)
        self._cache_set(cache_key, specs)
        self._log("analyzer_ok", n_scenes=len(specs))
        return specs
    except (asyncio.TimeoutError, json.JSONDecodeError, ValidationError) as e:
        self._log_warn("analyzer_fallback", reason=type(e).__name__)
        return await self._fallback_to_segmenter(chapter_index, chapter_content, outline)
    except (AuthError, RateLimitError) as e:
        self._log_error("analyzer_auth_or_rate", err=str(e))
        return await self._fallback_to_segmenter(chapter_index, chapter_content, outline)

async def _fallback_to_segmenter(self, chapter_index, chapter_content, outline):
    # 复用现有 segmenter 的切场景逻辑
    segments = await self._segmenter.segment_chapter(chapter_index, chapter_content, outline)
    # 但 environment_description 留空，由下游 assembler 补 LLM 蒸馏
    specs = []
    for i, seg in enumerate(segments):
        specs.append(BackgroundSceneSpec(
            scene_id=f"fallback_s{i+1}",
            scene_name=seg.location,
            scene_selector="",  # 留空，下游 _build_scene_selector 补
            scene_type=self._guess_scene_type(seg.location),
            split_reason="opening" if i == 0 else "location_change",
            evidence_spans=[],
            environment_description="",  # 留空，下游 assembler 走 env_hint 蒸馏
            architecture=[], props=[], lighting="", weather="", time_of_day="",
            atmosphere="", camera_shot_type="wide_establishing",
            composition_constraints=[], people_policy=PeoplePolicy(mode="empty_required"),
            style_tags=[], forbidden_characters=outline.characters,
        ))
    return specs
```

**多层 fallback 优先级**（每层失败再降级到下一层）：

```
1. analyzer LLM + cache          [首选]
2. segmenter LLM (现有逻辑)      [保 scene 切分]
3. segmenter regex fallback      [无 LLM 也能跑]
4. 单 scene 默认 empty_required  [最后兜底]
```

日志埋点：每层 fallback 都记 `fallback_layer` 字段，便于监控 fallback 频率（>20% 触发告警）。

## 风险与权衡

1. **fallback 模式产出质量低**：segmenter 不输出环境描述，下游 assembler 必须调 LLM 蒸馏 → 双 LLM 调用。权衡：fallback 命中率应 <5%，可接受额外成本。
2. **fallback 与主路径产出的 spec schema 字段不一致**：fallback 走 `environment_description=""`，可能让下游 assembler 报错。权衡：assembler 必须处理 `description` 为空的情况，触发 `_distill_env_hint_from_outline`（已存在）。

## 完成判据自检

- ✅ 围绕"fallback 多层路径"，不跑题到 cache 或 system prompt。
- ✅ 引用 Circuit Breaker、Google graceful degradation、OpenAI error handling。
- ✅ 落到 `background_scene_analyzer_service.py::_fallback_to_segmenter`。
- ✅ 符合哲学：失败时降级但不破坏主线，不引入新供应商。
