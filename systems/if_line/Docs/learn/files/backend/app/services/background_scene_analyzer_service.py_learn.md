# background_scene_analyzer_service.py — learn note

> Source: `backend/app/services/background_scene_analyzer_service.py` (730 LOC)
> Route: `high_reasoning` | Reuse targets: **P1 portrait_demand_analyzer, P1 keyframe_moment_selector, P2 chapter_voice_service 切片, P4 ai_flavor_check**
> Status: `[_]` → master-validated `[x]`

## 整体职责
输入章节正文 + 大纲 + Story Bible，输出 `List[BackgroundSceneSpec]`。每个 spec 是一个「环境场景」结构化描述（environment_description / lighting / atmosphere / camera_shot_type / people_policy），**绝不输出剧情/对白/角色动作**。失败 fallback 到 `SceneSegmenterService`。

## ⭐ 核心复用件：`_call_llm` (L154-185)

这是全仓库最值得复刻的 LLM 调用模式。**P1/P2/P4 所有新 analyzer 直接照抄这段**：

```python
async def _call_llm(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    last_err = None
    for attempt in range(ANALYZER_RETRIES + 1):
        try:
            resp = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=ANALYZER_MODEL,
                    temperature=ANALYZER_TEMPERATURE,
                    max_tokens=ANALYZER_MAX_TOKENS,
                    response_format={"type": "json_object"},  # 关键：强制 JSON
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                ),
                timeout=ANALYZER_TIMEOUT,
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
            last_err = e
            logger.warning(...)
        if attempt < ANALYZER_RETRIES:
            await asyncio.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"... failed after {ANALYZER_RETRIES + 1} attempts: {last_err}")
```

**4 个要点 worker 必须照抄**：
1. `response_format={"type": "json_object"}` 强制 LLM 输出 JSON。
2. `asyncio.wait_for(..., timeout=ANALYZER_TIMEOUT)` 外层兜超时。
3. 重试 + 指数退避（`0.5 * 2**attempt`）。
4. 三类异常分别 catch：TimeoutError / JSONDecodeError / 其他 Exception。

## ⭐ 缓存模式 (L307-340)

hash 输入做 key（章节正文 + outline + story_bible 一起 hash），存到 `backend/.cache/bg_scene_analyzer/<hash>.json`。下次相同输入直接命中。**P1/P4 新 analyzer 必须照抄这套缓存**，否则 LLM 成本会爆。

```python
def _cache_key(chapter_content, outline, ...) -> str:
    h = hashlib.sha256()
    h.update(chapter_content.encode("utf-8"))
    h.update(json.dumps(outline, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    ...
    return h.hexdigest()
```

## env 变量（L39-61）
所有 LLM 配置走 env，有 fallback 链：
- `BG_ANALYZER_MODEL` → `PROMPT_REWRITER_MODEL` → `"glm-4-plus"`
- `BG_ANALYZER_API_KEY` → `REWRITER_API_KEY` → `AI_IMAGE_API_KEY` → `OPENAI_API_KEY`
- `BG_ANALYZER_BASE_URL` → … → `https://open.bigmodel.cn/api/paas/v4`

P4 `ai_flavor_check.py` 建议复用同一组 env 变量（前缀改成 `AI_FLAVOR_*`），保持部署一致性。

## 验证器套路（L187+）
- `_validate_no_plot(spec_dict, forbidden_chars)`：检查 LLM 输出是否漏入剧情关键词 / 角色名。P4 `ai_flavor_check` 可参考这种「黑名单词表 + 失败原因字符串」模式。

## 配置文件依赖
`backend/app/config/image_generation_profiles.json` —— scene taxonomy（scene_category → people_policy 默认值）。P1 立绘需求分析可定义类似的角色 emotion taxonomy JSON。

## worker 提示
- 不要重写 `_call_llm`，直接 import 或复制这段。
- 章节正文可能很长（3000+ 字），LLM `max_tokens=8000` 是经验值，P4 AI 味评审可能要更高。
- 失败 fallback 是设计原则：P1/P2 analyzer 失败时也要有 fallback 路径，不能让整章死掉。
