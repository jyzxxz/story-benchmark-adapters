"""
Agent 上下文压缩。

实现取法参考 Pi：超过阈值后保留最近窗口，旧消息压成摘要；后续模型请求只看
system + 摘要 + 最近消息。这里不删除历史 message，数据库仍保留完整审计链路。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from app.services.text_llm_config import resolve_request_model, structured_json_request_options


EMessageKind_context_compaction = "context_compaction"
DEFAULT_CONTEXT_WINDOW_TOKENS = 1_000_000
DEFAULT_RESERVE_TOKENS = 16_000
DEFAULT_KEEP_RECENT_TOKENS = 20_000
_TOOL_RESULT_PREVIEW_CHARS = 2_000


@dataclass
class CompactionSettings:
    enabled: bool
    context_window_tokens: int
    reserve_tokens: int
    keep_recent_tokens: int
    summary_max_tokens: int

    @property
    def trigger_tokens(self) -> int:
        return max(1, self.context_window_tokens - self.reserve_tokens)


@dataclass
class PreparedCompaction:
    messages_to_summarize: list[dict[str, Any]]
    turn_prefix_messages: list[dict[str, Any]]
    kept_messages: list[dict[str, Any]]
    kept_db_indexes: list[int]
    first_kept_message_index: int
    turn_start_message_index: int | None
    is_split_turn: bool
    tokens_before: int
    tokens_after_estimate: int


@dataclass
class CompactionResult:
    summary_message: dict[str, Any]
    summary_text: str
    kept_messages: list[dict[str, Any]]
    kept_db_indexes: list[int]
    first_kept_message_index: int
    turn_start_message_index: int | None
    is_split_turn: bool
    turn_prefix_message_count: int
    summarized_message_count: int
    tokens_before: int
    tokens_after_estimate: int
    summary_chars: int
    model: str


@dataclass
class CutPoint:
    first_kept_pos: int
    turn_start_pos: int
    is_split_turn: bool


def compaction_settings_from_env() -> CompactionSettings:
    return CompactionSettings(
        enabled=_env_bool("AGENT_CONTEXT_COMPACTION_ENABLED", True),
        context_window_tokens=_env_int("AGENT_CONTEXT_WINDOW_TOKENS", DEFAULT_CONTEXT_WINDOW_TOKENS),
        reserve_tokens=_env_int("AGENT_CONTEXT_COMPACTION_RESERVE_TOKENS", DEFAULT_RESERVE_TOKENS),
        keep_recent_tokens=_env_int("AGENT_CONTEXT_COMPACTION_KEEP_RECENT_TOKENS", DEFAULT_KEEP_RECENT_TOKENS),
        summary_max_tokens=_env_int("AGENT_CONTEXT_COMPACTION_SUMMARY_MAX_TOKENS", 2_000),
    )


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return max(1, sum(_estimate_message_tokens(message) for message in messages))


def should_compact(messages: list[dict[str, Any]], settings: CompactionSettings) -> bool:
    if not settings.enabled:
        return False
    if len(messages) <= 3:
        return False
    return estimate_messages_tokens(messages) > settings.trigger_tokens


def prepare_compaction(
    messages: list[dict[str, Any]],
    message_db_indexes: list[int],
    settings: CompactionSettings,
) -> PreparedCompaction | None:
    # messages 是当前要喂给模型的“活跃窗口”，不是数据库全量历史。
    # message_db_indexes 与 messages 一一对应，用来把内存下标翻译回数据库 message_index。
    if len(messages) != len(message_db_indexes) or len(messages) <= 3:
        return None

    tokens_before = estimate_messages_tokens(messages)
    if tokens_before <= settings.trigger_tokens:
        return None

    # cut_point 只描述内存窗口里的下标：
    #   first_kept_pos：最近原文窗口从 messages[first_kept_pos] 开始保留；
    #   turn_start_pos：如果切在一个大 turn 中间，这个 turn 的 user 起点；
    #   is_split_turn：是否需要把当前 turn 前缀也塞进摘要。
    cut_point = _find_cut_point(messages, settings.keep_recent_tokens)
    first_kept_pos = cut_point.first_kept_pos
    # messages[0] 固定是 system prompt，不能被摘要掉；如果切点退到 0/1，就没有可压缩历史。
    if first_kept_pos <= 1:
        return None

    # Python slice 右边界不包含。
    #
    # 普通压缩：
    #   messages[0]                    system，保留
    #   messages[1:first_kept_pos]      旧历史，摘要
    #   messages[first_kept_pos:]       最近窗口，原文保留
    #
    # split-turn 压缩：
    #   messages[0]                         system，保留
    #   messages[1:turn_start_pos]          更早历史，摘要
    #   messages[turn_start_pos:first_kept_pos] 当前大 turn 前缀，摘要
    #   messages[first_kept_pos:]           当前大 turn 后半段，原文保留
    history_end_pos = cut_point.turn_start_pos if cut_point.is_split_turn else first_kept_pos
    messages_to_summarize = messages[1:history_end_pos]
    turn_prefix_messages = messages[cut_point.turn_start_pos:first_kept_pos] if cut_point.is_split_turn else []
    kept_messages = messages[first_kept_pos:]
    kept_db_indexes = message_db_indexes[first_kept_pos:]
    # 一次有效压缩必须同时具备两件事：
    #   1. 有东西可以摘要：普通旧历史，或 split-turn 的当前 turn 前缀；
    #   2. 有东西可以原文保留：最近窗口及其数据库编号。
    # split-turn 允许 messages_to_summarize 为空，因为它可能只压当前大 turn 的前缀。
    has_summary_source = bool(messages_to_summarize or turn_prefix_messages)
    has_kept_window = bool(kept_messages and kept_db_indexes)
    if not has_summary_source or not has_kept_window:
        return None

    # first_kept_message_index 是数据库编号，不是内存下标。恢复会话时用它重新拼出：
    # system + 最新 context_compaction 摘要 + message_index >= first_kept_message_index 的普通消息。
    first_kept_message_index = kept_db_indexes[0]
    turn_start_message_index = message_db_indexes[cut_point.turn_start_pos] if cut_point.is_split_turn else None
    tokens_after_estimate = _estimate_message_tokens(messages[0]) + estimate_messages_tokens(kept_messages)
    return PreparedCompaction(
        messages_to_summarize=messages_to_summarize,
        turn_prefix_messages=turn_prefix_messages,
        kept_messages=kept_messages,
        kept_db_indexes=kept_db_indexes,
        first_kept_message_index=first_kept_message_index,
        turn_start_message_index=turn_start_message_index,
        is_split_turn=cut_point.is_split_turn,
        tokens_before=tokens_before,
        tokens_after_estimate=tokens_after_estimate,
    )


async def run_compaction(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    message_db_indexes: list[int],
    settings: CompactionSettings,
) -> CompactionResult | None:
    prepared = prepare_compaction(messages, message_db_indexes, settings)
    if prepared is None:
        return None

    summary_messages = build_summary_request_messages(prepared)
    return await run_prepared_compaction(
        client=client,
        model=model,
        prepared=prepared,
        settings=settings,
        summary_messages=summary_messages,
    )


def build_summary_request_messages(prepared: PreparedCompaction) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "你负责压缩 Agent 历史上下文。只输出中文结构化摘要，不要继续对话。",
        },
        {
            "role": "user",
            "content": _build_summary_prompt(prepared),
        },
    ]

def _build_summary_prompt(prepared: PreparedCompaction) -> str:
    history_text = _serialize_messages(prepared.messages_to_summarize) if prepared.messages_to_summarize else "无更早历史。"
    split_turn_block = ""
    if prepared.is_split_turn:
        split_turn_block = f"""

当前压缩切点落在一轮对话中间。下面这段是被拆分 turn 的前缀，它的后缀会以原文保留在最近窗口里。
请单独总结这段前缀，重点说明原始请求、已经完成的早期工具调用、关键中间结果，以及理解后缀所需上下文。

被拆分 turn 前缀:
{_serialize_messages(prepared.turn_prefix_messages)}
""".rstrip()

    return f"""
请把下面的 Agent 历史上下文压缩成一份可继续工作的中文摘要。

要求:
- 不要继续对话，不要回答历史里的用户问题。
- 保留用户目标、约束、关键决定、已完成进展、未完成事项、重要 ID 和必要数据。
- 如果历史里有工具调用结果，只保留会影响后续判断的字段。
- 输出结构使用这些标题：目标、约束与偏好、已完成、进行中、关键决定、当前大 Turn 前缀、后续步骤、关键上下文。
- 如果没有被拆分 turn，当前大 Turn 前缀写“无”。

压缩前估算 token: {prepared.tokens_before}
压缩后保留窗口从 message_index={prepared.first_kept_message_index} 开始。

待摘要历史:
{history_text}
{split_turn_block}
""".strip()

async def run_prepared_compaction(
    *,
    client: Any,
    model: str,
    prepared: PreparedCompaction,
    settings: CompactionSettings,
    summary_messages: list[dict[str, Any]],
) -> CompactionResult:
    resolved_model = resolve_request_model(model)
    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "messages": summary_messages,
        "temperature": 0.1,
        "max_tokens": settings.summary_max_tokens,
        "stream": False,
    }
    kwargs.update(structured_json_request_options(resolved_model))
    response = await client.chat.completions.create(**kwargs)
    summary_text = _response_text(response).strip()
    if not summary_text:
        raise RuntimeError("上下文压缩摘要为空")

    summary_message = {
        "role": "system",
        "content": _summary_message_content(prepared, summary_text),
        "ifline_kind": EMessageKind_context_compaction,
        "first_kept_message_index": prepared.first_kept_message_index,
        "turn_start_message_index": prepared.turn_start_message_index,
        "is_split_turn": prepared.is_split_turn,
        "turn_prefix_message_count": len(prepared.turn_prefix_messages),
        "summarized_message_count": len(prepared.messages_to_summarize),
        "tokens_before": prepared.tokens_before,
        "tokens_after_estimate": prepared.tokens_after_estimate,
        "source": "pi-style-compaction-v2",
        "model": resolved_model,
    }
    return CompactionResult(
        summary_message=summary_message,
        summary_text=summary_text,
        kept_messages=prepared.kept_messages,
        kept_db_indexes=prepared.kept_db_indexes,
        first_kept_message_index=prepared.first_kept_message_index,
        turn_start_message_index=prepared.turn_start_message_index,
        is_split_turn=prepared.is_split_turn,
        turn_prefix_message_count=len(prepared.turn_prefix_messages),
        summarized_message_count=len(prepared.messages_to_summarize),
        tokens_before=prepared.tokens_before,
        tokens_after_estimate=prepared.tokens_after_estimate,
        summary_chars=len(summary_text),
        model=resolved_model,
    )

# split-turn 是普通上下文压缩的超集：
#   - 普通压缩：只从 user 边界切，保留最近完整 turn；
#   - split-turn：允许从当前大 turn 的合法中间点切，把当前 turn 前缀也压进摘要。
#
# 如果保留窗口刚好从 user 开始，运行时窗口就是：
#   system + 历史摘要 + 最近完整 turn
#
# 如果一个 turn 内已经发生多次 toolcall，例如：
#   user 大 turn 开始
#   assistant tool_call A
#   tool A result
#   assistant tool_call B   <- 从这里开始保留
#   tool B result
#
# split-turn 会把运行时窗口重建成：
#   system
#   context_compaction 摘要：
#     - 更早历史摘要
#     - 当前大 turn 前缀摘要：user + tool_call A + tool A result
#   assistant tool_call B
#   tool B result
#
# 这样不会为了“按 user 边界保留完整 turn”把整个大 turn 都留在上下文里，也不会在 tool
# result 上切出孤立工具结果。对应观测/落库字段：
#   - is_split_turn：本次是否切在 turn 中间；
#   - turn_start_message_index：被拆分 turn 的 user 起点；
#   - turn_prefix_message_count：被摘要掉的当前 turn 前缀消息数；
#   - first_kept_message_index：原文保留窗口从哪条数据库消息开始。
#
# 切点不能落在 tool result 上。聊天协议要求 tool 消息必须跟在对应 assistant tool_call
# 后面；孤立的 tool result 会破坏协议，也会让模型无法判断结果来源。所以合法切点只选
# user 或 assistant。单个超大 tool result 如果没有后续合法切点，仍要交给 tool 层做
# 分页、agent view 或截断报错。
def _find_cut_point(messages: list[dict[str, Any]], keep_recent_tokens: int) -> CutPoint:
    valid_positions = [index for index in range(1, len(messages)) if _is_cut_point_message(messages[index])]
    if not valid_positions:
        return CutPoint(first_kept_pos=1, turn_start_pos=-1, is_split_turn=False)

    total = 0
    first_kept_pos = valid_positions[0]
    for index in range(len(messages) - 1, 0, -1):
        total += _estimate_message_tokens(messages[index])
        if total < keep_recent_tokens:
            continue
        for valid_pos in valid_positions:
            if valid_pos >= index:
                first_kept_pos = valid_pos
                break
        break

    starts_turn = _is_turn_start_message(messages[first_kept_pos])
    turn_start_pos = -1 if starts_turn else _find_turn_start_pos(messages, first_kept_pos)
    is_split_turn = not starts_turn and turn_start_pos > 0
    return CutPoint(
        first_kept_pos=first_kept_pos,
        turn_start_pos=turn_start_pos,
        is_split_turn=is_split_turn,
    )


def _is_cut_point_message(message: dict[str, Any]) -> bool:
    return message.get("role") in {"user", "assistant"}


def _is_turn_start_message(message: dict[str, Any]) -> bool:
    return message.get("role") == "user"


def _find_turn_start_pos(messages: list[dict[str, Any]], start_pos: int) -> int:
    for index in range(start_pos, 0, -1):
        if _is_turn_start_message(messages[index]):
            return index
    return -1




def _summary_message_content(prepared: PreparedCompaction, summary_text: str) -> str:
    return f"""
【上下文压缩摘要】
这条 system message 由后端自动生成，用于替代更早的 Agent 对话上下文。

压缩边界:
- first_kept_message_index: {prepared.first_kept_message_index}
- is_split_turn: {str(prepared.is_split_turn).lower()}
- turn_start_message_index: {prepared.turn_start_message_index}
- turn_prefix_message_count: {len(prepared.turn_prefix_messages)}
- summarized_message_count: {len(prepared.messages_to_summarize)}
- tokens_before_estimate: {prepared.tokens_before}
- tokens_after_estimate: {prepared.tokens_after_estimate}

{summary_text}
""".strip()


def _serialize_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "user":
            parts.append("[User]: " + str(message.get("content") or ""))
        elif role == "assistant":
            parts.append("[Assistant]: " + str(message.get("content") or ""))
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                parts.append("[Assistant tool calls]: " + json.dumps(tool_calls, ensure_ascii=False, default=str))
        elif role == "tool":
            content = str(message.get("content") or "")
            if len(content) > _TOOL_RESULT_PREVIEW_CHARS:
                omitted = len(content) - _TOOL_RESULT_PREVIEW_CHARS
                content = content[:_TOOL_RESULT_PREVIEW_CHARS] + f"...[工具结果已截断 {omitted} 字]"
            parts.append(f"[Tool result {message.get('name') or ''}]: {content}")
        elif role == "system":
            parts.append("[System]: " + str(message.get("content") or ""))
        else:
            parts.append(f"[{role or 'Message'}]: " + json.dumps(message, ensure_ascii=False, default=str))
    return "\n\n".join(parts)


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    try:
        text = json.dumps(message, ensure_ascii=False, default=str)
    except TypeError:
        text = str(message)
    return max(1, _estimate_text_tokens(text))


def _estimate_text_tokens(text: str) -> int:
    """按保守比例估算 token。

    DeepSeek 文档给的直觉比例是中文字符约 0.6 token，但长重复中文在
    实测错误里接近 1 字 1 token。触发压缩宁可早一点，避免压缩前请求撞上
    provider context limit。
    """
    score = 0.0
    for ch in text:
        if ch.isspace():
            continue
        code = ord(ch)
        if (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
        ):
            score += 1.0
        elif code < 128:
            score += 0.3
        else:
            score += 0.5
    return int(score + 0.999)


def _response_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return str(getattr(message, "content", "") or "")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
