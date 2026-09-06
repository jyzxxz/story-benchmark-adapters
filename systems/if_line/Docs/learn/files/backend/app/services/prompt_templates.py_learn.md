# prompt_templates.py — learn note

> Source: `backend/app/services/prompt_templates.py` (~23KB, 大量 prompt 字符串)
> Route: `high_reasoning` | Reuse target: **P4.3 反 AI 味负例 + rewrite_hint 段**
> Status: `[_]` → master-validated `[x]`

## 职责
所有 LLM prompt 字符串集中处：Story Bible / Outline / 章节正文修订 / 章节正文生成 / 视觉 prompt。

## P4.3 改造点：`get_chapter_content_prompt(...)`

当前函数返回章节正文 prompt（英文，要求 LLM 输出 JSON `{title, content, ending_hook}`）。

**P4.3 改造两处**：

### 1. 加反 AI 味负例清单（prompt base 永久加）
在 prompt 末尾追加：
```
## Anti-AI-flavor rules (mandatory):
- Never use these cliché fillers: 不禁, 仿佛, 宛如, 一抹, 流转, 似乎在诉说, 莫名的, 淡淡的, 一缕, 微微的
- Never use template sentences like: "他知道，这一刻...", "时间仿佛静止", "心中涌起一股..."
- Avoid abstract emotional summary; use concrete sensory details instead
- Vary sentence rhythm; do not stack 4 parallel clauses in a row
- Show emotion through action/dialogue, not narrator exposition
```

### 2. 加可选 `rewrite_hint` 段（仅重写时追加）
```python
def get_chapter_content_prompt(..., rewrite_hint: Optional[str] = None) -> str:
    prompt = <existing base>
    if rewrite_hint:
        prompt += f"\n\n## REWRITE CONSTRAINTS:\nPrevious draft was rejected for these reasons: {rewrite_hint}\nRewrite avoiding them specifically."
    return prompt
```

## worker 提示
- prompt 全英文（LLM 英文 prompt 输出更稳）。
- 反 AI 味词表是中文（因为目标文本是中文），但指令本身用英文。
- `get_story_bible_prompt` / `get_chapter_outline_prompt` 不动，P4 只改章节正文那个。
- 文件里有 `_revision` 版本的 prompt（章节大纲修订），P4 不碰。
