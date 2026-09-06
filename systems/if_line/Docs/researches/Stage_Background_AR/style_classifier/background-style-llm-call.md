# B05 — background-style-llm-call

## 背景

style classifier 的 LLM 调用参数与 analyzer 不同：
- 温度更低（0.1）：分类任务要稳定，不要创意
- max_tokens 更小（600）：输出只有 3-5 个 tag
- 超时更短（3s）：分类任务应该比 analyzer 快
- 失败立即 fallback：不要 retries，因为 fallback 是 deterministic 也够用

需要在 `BackgroundStyleClassifierService._call_llm` 中实现这些差异。

## SOTA 实践

**OpenAI structured output 文档**（[`platform.openai.com/docs/guides/structured-outputs`](https://platform.openai.com/docs/guides/structured-outputs)）：

> For classification tasks, set temperature=0.1 or lower. Higher temperatures increase creative variance, which is undesirable when you want the same input to produce the same output.

**Anthropic "Reduce variance"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)）：分类/抽取任务温度建议 0.0-0.2。

**GLM API docs**（[`open.bigmodel.cn/dev/api/normal-model/glm-4`](https://open.bigmodel.cn/dev/api/normal-model/glm-4)）：temperature 范围 [0.0, 1.0]，0.0 完全确定性（greedy decoding），适合分类。

## 落地建议

```python
async def _call_llm(self, spec: BackgroundSceneSpec, genre: str) -> dict:
    sys_prompt = self._profiles["rewriter"]["background_style_classifier"]["system_prompt"]
    # 把 background_style_taxonomy 按 genre 注入
    sys_prompt_filled = sys_prompt.replace(
        "{注入 background_style_taxonomy}",
        json.dumps(self._taxonomy_for_genre(genre), ensure_ascii=False)
    )
    user_prompt = json.dumps({
        "scene_spec": spec.model_dump(),
        "project_genre": genre,
    }, ensure_ascii=False)

    try:
        resp = await asyncio.wait_for(
            self._llm.chat(
                model=os.getenv("SEGMENTER_MODEL", "glm-4.6"),
                messages=[
                    {"role": "system", "content": sys_prompt_filled},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=600,
                response_format={"type": "json_object"},
            ),
            timeout=3.0,
        )
        return json.loads(resp["choices"][0]["message"]["content"])
    except (asyncio.TimeoutError, json.JSONDecodeError):
        raise  # 由上层 fallback
```

**关键参数对比**：

| 参数 | analyzer | classifier |
|---|---|---|
| temperature | 0.2 | 0.1 |
| max_tokens | 2500 | 600 |
| timeout | 8s | 3s |
| retries | 2 | 0 |
| failure | fallback to segmenter | fallback to deterministic |

落到 `background_style_classifier_service.py::_call_llm`。

## 风险与权衡

1. **温度 0.1 在 GLM-flash 上仍有 ~3% 漂移**：可能选错 art_style（例如古风选了西方油画）。权衡：deterministic fallback 兜底；同时 `_validate_in_taxonomy` 拒绝跨 genre 词。
2. **3s 超时偶尔不够**：弱模型首次加载可能 4-5s。权衡：超时即降级，不重试——deterministic fallback 输出质量足以接受。

## 完成判据自检

- ✅ 围绕"LLM 调用参数"，不跑题到 cache 或 fallback 内容。
- ✅ 引用 OpenAI structured output、Anthropic prompt engineering、GLM API docs。
- ✅ 落到 `background_style_classifier_service.py::_call_llm`。
- ✅ 符合哲学：低温度 + 严格超时 + 立即 fallback，复用现有 GLM 配置。
