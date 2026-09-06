"""
章节正文一致性检查 —— 中等严格度。

判定不合格的情形 (任一命中即视为本次生成"质量不达标",触发重试):

硬错误 (必然重试):
  1. content 为空或非字符串
  2. content 长度 < QUALITY_MIN_LENGTH_ABSOLUTE (默认 300 字)
  3. content 长度 < word_min * QUALITY_MIN_LENGTH_RATIO (默认 70%)
  4. title 与大纲标题完全不一致

软错误 (中等严格度下也重试):
  5. 章节中完全没出现大纲里任何角色名 (角色出场对不上)
  6. ending_hook 提到的事件/物件关键词在章节末尾 N 字内未呼应

判定合格时返回 (True, [])。
判定不合格时返回 (False, [理由列表]), 由 caller 决定是否重试。
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from app.agent.presets import (
    QUALITY_MIN_LENGTH_RATIO,
    QUALITY_MIN_LENGTH_ABSOLUTE,
    QUALITY_ENDING_HOOK_SEARCH_WINDOW,
)


@dataclass
class CheckResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)


def check_chapter_content(
    content_output: Any,
    chapter_outline: Dict[str, Any],
    word_count_min: int,
) -> CheckResult:
    """对一个 generate_chapter_content 的输出做一致性检查。"""
    reasons: List[str] = []

    # ---- 字段提取 (防御性: content_output 可能是 pydantic model, 也可能是 dict) ----
    if hasattr(content_output, "dict"):
        payload = content_output.dict()
    elif isinstance(content_output, dict):
        payload = content_output
    else:
        return CheckResult(False, [f"unexpected content_output type: {type(content_output).__name__}"])

    content = payload.get("content") or ""
    title = payload.get("title") or ""
    ending_hook = payload.get("ending_hook") or ""

    # ---- 硬错误 1: content 为空 ----
    if not isinstance(content, str) or not content.strip():
        reasons.append("content 字段为空")

    # ---- 硬错误 2/3: 字数 ----
    length = len(content) if isinstance(content, str) else 0
    if length < QUALITY_MIN_LENGTH_ABSOLUTE:
        reasons.append(f"字数过短 ({length} < {QUALITY_MIN_LENGTH_ABSOLUTE} 绝对下限)")
    elif word_count_min > 0 and length < word_count_min * QUALITY_MIN_LENGTH_RATIO:
        reasons.append(
            f"字数不达标 ({length} < word_min({word_count_min})×{QUALITY_MIN_LENGTH_RATIO:.0%})"
        )

    # ---- 硬错误 4: 标题 ----
    outline_title = (chapter_outline.get("title") or "").strip()
    if outline_title and title.strip() and title.strip() != outline_title:
        reasons.append(f"标题不匹配 (输出: '{title}', 大纲: '{outline_title}')")

    # ---- 软错误 5: 角色名未出现 ----
    characters = chapter_outline.get("characters") or []
    if characters and isinstance(content, str) and length >= QUALITY_MIN_LENGTH_ABSOLUTE:
        # 只对中文姓名做严格匹配; 英文/单字名容易误判, 跳过
        cn_names = [c for c in characters if isinstance(c, str) and len(c) >= 2]
        if cn_names:
            appeared = [n for n in cn_names if n in content]
            if not appeared:
                reasons.append(
                    f"章节正文未出现大纲里任何角色名 (大纲角色: {cn_names[:3]})"
                )

    # ---- 软错误 6: ending_hook 在章节末尾未呼应 ----
    if ending_hook and isinstance(content, str) and length >= QUALITY_MIN_LENGTH_ABSOLUTE:
        # 从 ending_hook 里抽出 2-4 字的关键 token, 看章节末尾是否出现
        tokens = _extract_keywords(ending_hook)
        tail = content[-QUALITY_ENDING_HOOK_SEARCH_WINDOW:]
        if tokens and not any(tok in tail for tok in tokens):
            reasons.append(
                f"ending_hook 在章节末尾 {QUALITY_ENDING_HOOK_SEARCH_WINDOW} 字内未呼应 "
                f"(hook 关键词: {tokens[:3]})"
            )

    return CheckResult(passed=not reasons, reasons=reasons)


# 抽取 2-4 字中文 token, 过滤标点和单字
_CN_TOKEN_RE = re.compile(r"[一-龥]{2,4}")


def _extract_keywords(text: str, max_tokens: int = 6) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(_CN_TOKEN_RE.findall(text)))[:max_tokens]
