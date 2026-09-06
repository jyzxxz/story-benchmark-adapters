"""Evals 核心数据类型。不 import 任何 app 模块。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ToolCallRecord:
    """一次已完成的工具调用(从 item.completed 事件归一化而来)。"""
    name: str
    arguments: dict[str, Any]
    result: Any
    status: str


@dataclass
class CheckOutcome:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class JudgeOutcome:
    """LLM-as-judge 的评分结果。"""
    name: str
    passed: bool
    score: int
    dimensions: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    reason: str = ""


# 断言函数: (seed ctx, EvalResult) -> CheckOutcome。
# 需要 DB 的 checker 自行开新 session, runner 不持有长连接。
AssertionFn = Callable[[dict[str, Any], "EvalResult"], CheckOutcome]

# seed 函数: (db session, user_id) -> ctx(如 {"project_id": 1}), 用于格式化指令模板。
SeedFn = Callable[[Any, int], dict[str, Any]]


@dataclass
class JudgeSpec:
    """judge 配置: rubric 名 + 从 (db, ctx, result) 拼出 judge prompt。"""
    rubric: str
    build_prompt: Callable[[Any, dict[str, Any], "EvalResult"], str]


@dataclass
class EvalResult:
    task_id: str
    run_index: int
    instructions: str
    turn_id: str
    stop_reason: str
    final_answer: str
    tool_trace: list[ToolCallRecord]
    checks: list[CheckOutcome]
    passed: bool
    error: Optional[str] = None
    judge: Optional[JudgeOutcome] = None
    assistant_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvalTask:
    id: str
    name: str
    # 指令模板, 支持 {key}, 用 seed 返回的 ctx 格式化(例如 project_id)。
    instructions: str
    seed: SeedFn
    expected_tools: Optional[list[str]] = None
    forbidden_tools: Optional[list[str]] = None
    assertions: list[AssertionFn] = field(default_factory=list)
    mode: str = "restricted"
    runs: int = 1
    # unittest.mock.patch 对象列表, 用于 mock 工具内部真正调用 LLM 的部分(replay 用)。
    patches: Optional[list[Any]] = None
    # 若设置, live 模式下在断言后追加 judge 打分。
    judge: Optional[JudgeSpec] = None
