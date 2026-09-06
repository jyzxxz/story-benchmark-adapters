# resilient_llm.py — learn note

> Source: `backend/app/agent/resilient_llm.py` (101 LOC)
> Route: `standard` | Reuse target: **P4.3 rewrite_hint 参数穿这里**
> Status: `[_]` → master-validated `[x]`

## 职责
对 `llm_service` 4 个方法的薄包装，加 `_with_retry`（重试 + 指数退避）。AutoCreator 走这层而不是直接调 llm_service。

## ⭐ 异常分类
- **可重试**：`APIError / APITimeoutError / APIConnectionError / RateLimitError / JSONDecodeError / asyncio.TimeoutError`
- **不可重试**：`AuthenticationError / BadRequestError`（配置/认证问题）

## P4.3 改造点：`generate_chapter_content`

当前签名透传：
```python
async def generate_chapter_content(*args, **kwargs):
    return await _with_retry("chapter", lambda: llm_service.generate_chapter_content(*args, **kwargs))
```

**P4.3 改造**：直接给 `generate_chapter_content` 加显式 `rewrite_hint` kwarg，避免 kwargs 漂移：
```python
async def generate_chapter_content(*args, rewrite_hint: Optional[str] = None, **kwargs):
    return await _with_retry(
        "chapter",
        lambda: llm_service.generate_chapter_content(*args, rewrite_hint=rewrite_hint, **kwargs),
    )
```

然后 `llm_service.generate_chapter_content` 也加同参数，透传到 `prompt_templates.get_chapter_content_prompt(..., rewrite_hint=rewrite_hint)`。

## worker 提示
- 不动 `_with_retry`（重试逻辑成熟）。
- 改 `generate_chapter_content` 时记得 `from typing import Optional` 已在文件头。
