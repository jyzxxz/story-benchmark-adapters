"""LLM JSON 输出解析与观测。

这个文件只处理一件事：模型声称返回 JSON 时，如何解析、容错修复，并把
失败位置、response_id、原始输出 preview 写进日志和 span，方便在日志平台
和 trace 面板之间互相搜索。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from app.utils import logging as xlog


def parse_llm_json(
    content: str,
    uuid: Any = 0,
    *,
    operation: str = "",
    model: str = "",
    provider_request_id: str | None = None,
) -> Any:
    """解析 LLM 返回的 JSON，失败时尝试自动修复。

    日志和 span 都带 operation/model/response_id，同一段 LLM 输出能在日志
    平台和 trace 面板之间互相反查。日志只放 preview/tail；trace input
    固定保存完整原始输出，方便排查 JSON 解析问题。
    """
    if content is None:
        raise ValueError("LLM 返回内容为空")
    span_handle = _start_json_parse_span(content, operation, model, provider_request_id)
    try:
        try:
            result = json.loads(content)
            _finish_json_parse_span(span_handle, "ok")
            return result
        except json.JSONDecodeError as first_err:
            xlog.warn(
                uuid,
                "[llm] json parse failed, try repair operation=%s model=%s response_id=%s "
                "err=%s line=%s column=%s pos=%s raw_chars=%d raw_preview=%s raw_tail=%s",
                operation or "unknown",
                model or "unknown",
                provider_request_id or "",
                first_err.msg,
                first_err.lineno,
                first_err.colno,
                first_err.pos,
                len(content),
                _json_log_preview(content),
                _json_log_tail(content),
            )
            repaired = _repair_json_text(content)
            try:
                result = json.loads(repaired)
                xlog.info(
                    uuid,
                    "[llm] json repair ok operation=%s model=%s response_id=%s repaired_chars=%d",
                    operation or "unknown",
                    model or "unknown",
                    provider_request_id or "",
                    len(repaired),
                )
                _finish_json_parse_span(span_handle, "repair.ok")
                return result
            except json.JSONDecodeError as second_err:
                # 最后一次尝试：删除报错位置所在行，用于回收少量控制字符污染的输出。
                lines = repaired.splitlines()
                if isinstance(second_err.pos, int) and 0 <= second_err.pos < len(repaired):
                    line_no = second_err.lineno - 1 if second_err.lineno else None
                    if line_no is not None and 0 <= line_no < len(lines):
                        del lines[line_no]
                        third = "\n".join(lines)
                        try:
                            result = json.loads(third)
                            xlog.info(
                                uuid,
                                "[llm] json repair drop line ok operation=%s model=%s response_id=%s line_no=%d",
                                operation or "unknown",
                                model or "unknown",
                                provider_request_id or "",
                                line_no,
                            )
                            _finish_json_parse_span(span_handle, "repair.drop_line.ok")
                            return result
                        except json.JSONDecodeError:
                            pass
                xlog.warn(
                    uuid,
                    "[llm] json repair failed operation=%s model=%s response_id=%s "
                    "first_err=%s repair_err=%s line=%s column=%s pos=%s raw_chars=%d raw_preview=%s raw_tail=%s",
                    operation or "unknown",
                    model or "unknown",
                    provider_request_id or "",
                    first_err.msg,
                    second_err.msg,
                    second_err.lineno,
                    second_err.colno,
                    second_err.pos,
                    len(content),
                    _json_log_preview(content),
                    _json_log_tail(content),
                )
                _fail_json_parse_span(span_handle, second_err)
                span_handle = None
                raise
    except Exception:
        _end_span(span_handle)
        raise


def _extract_json_block(text: str) -> str:
    """从 LLM 输出中抽取最外层 JSON 对象/数组片段。"""
    text = text.strip()
    start = text.find("{")
    arr_start = text.find("[")
    if start == -1 and arr_start == -1:
        return text
    if start == -1:
        open_ch, close_ch, begin = "[", "]", arr_start
    elif arr_start == -1 or start < arr_start:
        open_ch, close_ch, begin = "{", "}", start
    else:
        open_ch, close_ch, begin = "[", "]", arr_start

    depth = 0
    in_str = False
    escape = False
    for i in range(begin, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[begin:i + 1]
    return text[begin:]


def _repair_json_text(text: str) -> str:
    """对 LLM 返回的常见非法 JSON 进行容错修复。

    单遍扫描：
    - 字符串外的全角引号 “ ” 视作字符串分隔符，归一化为 "
    - 字符串内的裸换行/制表符/回车，转义为 \\n / \\t / \\r
    - 不间断空格 NBSP 归一化成普通空格
    """
    text = _extract_json_block(text)
    text = text.replace("\u00a0", " ")
    out = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                escape = False
                out.append(ch)
                continue
            if ch == "\\":
                escape = True
                out.append(ch)
                continue
            if ch == '"' or ch == "”":
                in_str = False
                out.append('"')
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
        else:
            if ch == '"' or ch == "“":
                in_str = True
                out.append('"')
            elif ch == "”":
                in_str = False
                out.append('"')
            else:
                out.append(ch)
    text = "".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _start_json_parse_span(
    content: str,
    operation: str,
    model: str,
    provider_request_id: str | None,
) -> tuple[Any, Any] | None:
    try:
        from app.observability import provider_span

        manager = provider_span(
            "llm.json.parse",
            {
                "gen_ai.operation.name": "json.parse",
                "llm.response.model": model,
                "llm.response.id": provider_request_id,
                "ifline.llm.operation": operation,
                "ifline.raw_chars": len(content),
                "ifline.raw_preview": _json_text_preview(content),
                "ifline.raw_tail": _json_text_tail(content),
                "input.mime_type": "text/plain",
                "input.value": content or "<empty>",
            },
        )
        span = manager.__enter__()
        return span, manager
    except Exception:
        return None


def _finish_json_parse_span(span_handle: tuple[Any, Any] | None, outcome: str) -> None:
    if span_handle is None:
        return
    span, _ = span_handle
    if span is None:
        _end_span(span_handle)
        return
    try:
        span.set_attribute("ifline.parse_outcome", outcome)
        span.set_attribute("output.mime_type", "application/json")
        span.set_attribute("output.value", json.dumps({"ok": True, "outcome": outcome}, ensure_ascii=False))
    finally:
        _end_span(span_handle)


def _fail_json_parse_span(span_handle: tuple[Any, Any] | None, err: json.JSONDecodeError) -> None:
    if span_handle is None:
        return
    span, _ = span_handle
    if span is None:
        _end_span(span_handle)
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_attribute("ifline.parse_outcome", "failed")
        span.set_attribute("ifline.error.line", err.lineno)
        span.set_attribute("ifline.error.column", err.colno)
        span.set_attribute("ifline.error.pos", err.pos)
        span.set_status(Status(StatusCode.ERROR, err.msg))
        span.set_attribute("output.mime_type", "application/json")
        span.set_attribute(
            "output.value",
            json.dumps(
                {
                    "ok": False,
                    "error": err.msg,
                    "line": err.lineno,
                    "column": err.colno,
                    "pos": err.pos,
                },
                ensure_ascii=False,
            ),
        )
    finally:
        _end_span(span_handle)


def _end_span(span_handle: tuple[Any, Any] | None) -> None:
    if span_handle is None:
        return
    _, manager = span_handle
    manager.__exit__(None, None, None)


def _json_log_preview(content: str) -> str:
    return json.dumps(_json_text_preview(content), ensure_ascii=False)


def _json_log_tail(content: str) -> str:
    return json.dumps(_json_text_tail(content), ensure_ascii=False)


def _json_text_preview(content: str) -> str:
    return _limit_text(content, _env_int("LLM_JSON_LOG_PREVIEW_CHARS", 800))


def _json_text_tail(content: str) -> str:
    limit = _env_int("LLM_JSON_LOG_TAIL_CHARS", 800)
    if not content:
        return "<empty>"
    if len(content) <= limit:
        return content
    return content[-limit:]


def _limit_text(content: str, limit: int) -> str:
    if not content:
        return "<empty>"
    if len(content) <= limit:
        return content
    return content[:limit] + "...[已截断]"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
