"""确定性 checker。

每个 checker 是一个工厂, 返回断言函数 (ctx, EvalResult) -> CheckOutcome。
四类:
- sequence_check   工具调用序列(子序列匹配) + 禁止工具
- tool_arg_check   某工具的调用参数是否匹配
- db_check         跑完后再查库做状态断言(自开新 session)
- answer_check     最终答案文本断言(含 contains / not_contains 便捷封装)
"""
from __future__ import annotations

from typing import Any, Callable

from app.database import SessionLocal

from evals.types import CheckOutcome, EvalResult


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    i = 0
    for x in haystack:
        if i < len(needle) and x == needle[i]:
            i += 1
    return i == len(needle)


def sequence_check(
    expected: list[str] | None,
    forbidden: list[str] | None = None,
):
    def _check(ctx: dict[str, Any], result: EvalResult) -> CheckOutcome:
        names = [t.name for t in result.tool_trace]
        if expected and not _is_subsequence(expected, names):
            return CheckOutcome(
                "tool_sequence",
                False,
                f"期望工具序列(子序列) {expected} 未按序出现, 实际 {names}",
            )
        if forbidden:
            hit = [n for n in names if n in forbidden]
            if hit:
                return CheckOutcome("tool_forbidden", False, f"调用了被禁止的工具 {hit}")
        return CheckOutcome("tool_sequence", True, f"工具序列 ok: {names}")
    return _check


def at_least_one_tool():
    def _check(ctx: dict[str, Any], result: EvalResult) -> CheckOutcome:
        if not result.tool_trace:
            return CheckOutcome("tool_called", False, "agent 未调用任何工具")
        return CheckOutcome("tool_called", True, f"调用了 {[t.name for t in result.tool_trace]}")
    return _check


def tool_arg_check(
    tool_name: str,
    expected_args: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]] | None = None,
):
    def _check(ctx: dict[str, Any], result: EvalResult) -> CheckOutcome:
        expected = expected_args(ctx) if callable(expected_args) else expected_args
        for t in result.tool_trace:
            if t.name != tool_name:
                continue
            if expected is None:
                return CheckOutcome(f"tool_arg:{tool_name}", True, f"调用了 {tool_name}")
            bad = [
                f"{k} 期望 {v!r} 实际 {t.arguments.get(k)!r}"
                for k, v in expected.items()
                if t.arguments.get(k) != v
            ]
            if bad:
                return CheckOutcome(f"tool_arg:{tool_name}", False, "; ".join(bad))
            return CheckOutcome(f"tool_arg:{tool_name}", True, f"参数正确 {expected}")
        return CheckOutcome(f"tool_arg:{tool_name}", False, f"未调用工具 {tool_name}")
    return _check


def db_check(
    description: str,
    predicate: Callable[[Any, dict[str, Any]], tuple[bool, str]],
):
    def _check(ctx: dict[str, Any], result: EvalResult) -> CheckOutcome:
        fresh = SessionLocal()
        try:
            ok, detail = predicate(fresh, ctx)
        except Exception as exc:  # noqa: BLE001 - 断言内部异常也要报出来
            return CheckOutcome(description, False, f"{type(exc).__name__}: {exc}")
        finally:
            fresh.close()
        return CheckOutcome(description, ok, detail)
    return _check


def answer_check(
    description: str,
    predicate: Callable[[str, dict[str, Any]], tuple[bool, str]],
):
    def _check(ctx: dict[str, Any], result: EvalResult) -> CheckOutcome:
        ok, detail = predicate(result.final_answer, ctx)
        return CheckOutcome(description, ok, detail)
    return _check


def answer_contains(keyword: str):
    return answer_check(
        f"answer_contains:{keyword!r}",
        lambda answer, ctx: (keyword in answer, f"期望答案包含 {keyword!r}"),
    )


def answer_not_contains(keyword: str):
    return answer_check(
        f"answer_not_contains:{keyword!r}",
        lambda answer, ctx: (keyword not in answer, f"期望答案不包含 {keyword!r}"),
    )
