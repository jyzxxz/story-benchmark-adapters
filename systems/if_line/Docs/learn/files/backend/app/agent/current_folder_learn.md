# backend/app/agent/ — folder learn

> Auto-creator agent 层。5 个文件，4 个在 subset。

## 文件角色
- `auto_creator.py` —— ⭐ 主编排器，P4.4 直接挂进 `_step_generate_chapters`。
- `quality_check.py` —— 规则版质量门，P4 `ai_flavor_check` 与之同级。
- `presets.py` —— 常量集中处，P4.2 加阈值。
- `resilient_llm.py` —— LLM 重试包装，P4.3 加 rewrite_hint 透传。
- `theme_brainstorm.py` —— 选题 LLM（不在 subset，无需改）。

## ⭐ 章节生成主循环（P4.4 唯一改造点）
`auto_creator._step_generate_chapters` 的 `while content_output is None` 循环：
- `attempts` 计数器，上限 `MAX_CHAPTER_FAILURES = 3`。
- LLM 失败、quality_check 失败、**P4 后 AI 味失败** 都共用这一个计数器。
- 配额用尽就接受最后一次输出，不让整篇死掉。

## ⭐ 上下文窗口太短是 AI 味根因之一
`previous_chapters=[{"summary": (c.content or "")[:200]}]` —— 只给前序章节 200 字摘要。P4 顺手扩到 500-800 字。

## 状态机（stub 方法）
`_generate_assets / _generate_vn_graph / _generate_tts` 当前是 stub（`ENABLE_*=False`）。P1/P2 落地后可以把对应 ENABLE 翻 True 并实现，让 auto_creator 一次跑完全链路（brainstorm → bible → outline → chapters → assets → vngraph → tts）。

## 进度/调试件
- `progress.write_json(run_dir, name, obj)` —— 落 `runs/<run_id>/<name>.json`，P4 AI 味结果也走这个。
- `progress.write_manifest / write_error` —— run 级元数据。
