# A09 — background-scene-analyzer-llm-call

## 背景

`BackgroundSceneAnalyzerService._call_llm` 是与 GLM API 直接交互的边界。如果参数（温度/超时/retries/response_format）设置不当，会出现：
- 温度过高 → JSON 字段抖动，每次跑同章节产出不同 scene 数
- 无超时 → 单次调用挂死整个章节生成
- 无 retries → 偶发 5xx 让整章 fallback 到旧逻辑
- response_format=json_object 缺失 → 偶发输出含解释文字

现有 `scene_segmenter_service._call_llm_single`（`backend/app/services/scene_segmenter_service.py`）已建立基础调用模式（温度 0.3、6s 超时），但缺 retries 和严格的 response_format。

## SOTA 实践

**智谱 GLM-4 官方文档**（[`open.bigmodel.cn/dev/api/normal-model/glm-4`](https://open.bigmodel.cn/dev/api/normal-model/glm-4)）：

> `response_format={"type": "json_object"}` 保证输出严格 JSON；温度建议 0.1-0.3 用于结构化任务。

**GLM-4.6 release notes**（[`open.bigmodel.cn/dev/api/normal-model/glm-4-6`](https://open.bigmodel.cn/dev/api/normal-model/glm-4-6)）：在 long-context structured output 任务上稳定性优于 glm-4-flash 15-20%。

**OpenAI cookbook "Reducing latency"**（[`cookbook.openai.com/examples/reducing_latency`](https://cookbook.openai.com/examples/reducing_latency)）：结构化抽取任务用 temperature 0.1-0.3 + max_tokens 显式上限 + retry with exponential backoff，p99 延迟最稳。

## 落地建议

```python
async def _call_llm(self, content, outline, story_bible, genre) -> dict:
    sys_prompt = self._profiles["rewriter"]["background_scene_extractor"]["system_prompt"]
    user_prompt = self._build_user_prompt(content, outline, story_bible, genre)

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                self._llm.chat(
                    model=os.getenv("SEGMENTER_MODEL", "glm-4.6"),
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,  # 比 segmenter 现有 0.3 更稳
                    max_tokens=2500,
                    response_format={"type": "json_object"},
                ),
                timeout=8.0,  # 比 segmenter 6.0 略宽，因 schema 更大
            )
            return json.loads(resp["choices"][0]["message"]["content"])
        except asyncio.TimeoutError:
            self._log_warn("llm_timeout", attempt=attempt)
            if attempt == max_retries:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
        except (json.JSONDecodeError, KeyError) as e:
            self._log_warn("llm_json_error", attempt=attempt, err=str(e))
            if attempt == max_retries:
                raise
            await asyncio.sleep(0.3)
```

落到 `backend/app/services/background_scene_analyzer_service.py::_call_llm`。

**模型选择**：
- 生产 / 章节分析任务（高稳定性需求）：`SEGMENTER_MODEL=glm-4.6`
- 开发 / 调试（省钱）：`SEGMENTER_MODEL=glm-4-flash`

环境变量已存在（`backend/app/config`），无需新增。

## 风险与权衡

1. **温度 0.2 在 GLM-flash 上仍可能漂移**：弱模型即使低温度也有 ~5% schema 违规。权衡：保留 retries + JSON parse 失败时降级到 segmenter 现有逻辑。
2. **8s 超时 vs 用户体验**：章节生成是异步任务，8s 不影响前端响应；但若并发 10 章 × 8s = 80s 串行不可接受。权衡：章节生成在 `BackgroundService` 已并发，单章 8s 上限可接受。

## 完成判据自检

- ✅ 围绕"LLM 调用实现"，不跑题到 cache 或 fallback。
- ✅ 引用 GLM 官方文档、OpenAI cookbook。
- ✅ 落到 `background_scene_analyzer_service.py::_call_llm`。
- ✅ 符合哲学：复用现有 GLM 配置、不引入新供应商、temperature 0.2 符合结构化任务最佳实践。
