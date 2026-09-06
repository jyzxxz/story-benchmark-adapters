# auto_creator.py — learn note

> Source: `backend/app/agent/auto_creator.py` (350 LOC)
> Route: `high_reasoning` | Reuse target: **P4.4 直接挂进 _step_generate_chapters**
> Status: `[_]` → master-validated `[x]`

## 职责
单次全自动跑批编排器。链路：brainstorm → Project → Story Bible → Outline → 全部 ChapterContent。

## ⭐ P4.4 改造点：`_step_generate_chapters` (L213-314)

当前重试循环（L244-294）：
```python
content_output = None
attempts = 0
while content_output is None:
    attempts += 1
    try:
        candidate = await resilient_llm.generate_chapter_content(
            story_bible=bible.raw_json,
            chapter_outline={...},
            previous_chapters=[{"chapter_index", "summary": (c.content or "")[:200]} for c in previous],
            word_count_min=...,
            word_count_max=...,
        )
    except Exception as exc:
        # LLM 失败
        if attempts >= MAX_CHAPTER_FAILURES: raise
        continue

    check = quality_check.check_chapter_content(candidate, ...)
    if check.passed:
        content_output = candidate
    else:
        # 规则检查不通过
        if attempts >= MAX_CHAPTER_FAILURES:
            content_output = candidate  # 接受最后一次
```

**P4.4 改造**：在 `check.passed` 后插入 AI 味评审：

```python
if check.passed:
    flavor = await ai_flavor_check.check(candidate.content, bible.style_rules)
    if flavor.passed:
        content_output = candidate
        progress.write_json(self.run_dir, f"chapter_{ci}_flavor.json",
                            {"score": flavor.score, "issues": flavor.issues, "attempts": attempts})
    else:
        rewrite_hint = flavor.rewrite_hint
        print(f"[AutoCreator]   chapter {ci} AI 味不达标 (score={flavor.score}): {flavor.issues}")
        if attempts >= MAX_CHAPTER_FAILURES:
            content_output = candidate  # 配额用尽，接受
        # 否则 while 循环重试，下次 generate_chapter_content 带 rewrite_hint
```

`rewrite_hint` 通过 `resilient_llm.generate_chapter_content(..., rewrite_hint=rewrite_hint)` 穿到 `prompt_templates.get_chapter_content_prompt`。

## ⭐ 配额共享（关键）
`MAX_CHAPTER_FAILURES = 3`（presets.py）。AI 味重试和 quality_check 失败**共用同一个 `attempts` 计数器**，不各自单独算，避免重试爆炸烧 token。

## `_step_generate_chapters` 的 `previous` 上下文
当前只传前序章节的 `content[:200]` 摘要。这是 **AI 味重的一个根因** —— 上下文太短。P4 顺手把摘要窗口扩大到 500-800 字，或者在 rewrite_hint 里显式要求「不要重复前文用过的形容词」。

## stub 方法（L316-329）
`_generate_assets` / `_generate_vn_graph` / `_generate_tts` 当前是空 stub（`ENABLE_*=False`）。P1/P2 落地后可以把 `presets.ENABLE_TTS=True` 并实现 `_generate_tts`，让 auto_creator 一次跑完全链路。

## worker 提示
- 改 `_step_generate_chapters` 时保留 `print` 调试语句风格。
- `progress.write_json` 落到 `runs/<run_id>/`，AI 味结果也走这个，方便排查。
- 改完跑 `python backend/app/scripts/run_auto_creator.py` 验证。
