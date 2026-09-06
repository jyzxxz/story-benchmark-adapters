# presets.py — learn note

> Source: `backend/app/agent/presets.py` (43 LOC)
> Route: `standard` | Reuse target: **P4.2 加阈值常量**
> Status: `[_]` → master-validated `[x]`

## 职责
AutoCreator 所有可调参数集中处。纯常量模块。

## 现有常量
- `PACE_CHAPTER_COUNT = {"fast":8, "medium":13, "slow":20}`
- `DEFAULT_WORD_COUNT_MIN = 1500` / `MAX = 3000`
- `MAX_CHAPTER_FAILURES = 3` —— **P4.4 共享配额就是指这个**
- `MAX_LLM_RETRIES = 3` / `LLM_RETRY_BASE_DELAY = 1.0`
- `QUALITY_MIN_LENGTH_RATIO = 0.7` / `QUALITY_MIN_LENGTH_ABSOLUTE = 300` / `QUALITY_ENDING_HOOK_SEARCH_WINDOW = 300`
- `ENABLE_ASSETS / ENABLE_VN_GRAPH / ENABLE_TTS = False` —— 后期开关
- `BRAINSTORM_TEMPERATURE = 1.1`

## P4.2 改造：新增 AI 味阈值
```python
AI_FLAVOR_MIN_SCORE = 70
"""章节正文 AI 味评审分数低于此值视为不达标,触发重写。"""

AI_FLAVOR_MAX_RETRIES = 2
"""AI 味重写最大次数。与 quality_check 共享 MAX_CHAPTER_FAILURES 总配额,
这两个常量只是子类目参考上限,实际还是看 MAX_CHAPTER_FAILURES。"""
```

## worker 提示
- 全部常量都带 docstring 注释，保持风格。
- 改这个文件零风险，纯加常量。
