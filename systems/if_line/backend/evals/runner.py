"""Eval runner: 驱动 ServerAgent, 采集 tool_trace, 跑断言 + 可选 judge。

每次 run:
1. seed 一个"已知状态"的项目, 得到 ctx(含 project_id 等)
2. 用 ctx 格式化指令模板
3. (可选)应用 patches mock 内部 LLM
4. create_agent + resume 跑一轮, 拿 events/messages/answer
5. 从 events 抽 tool_trace, 逐条跑断言
6. (可选, live)跑 LLM-as-judge 打分
"""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any, Optional

from app.agent.server_agent import ServerAgent, server_agent_manager
from app.agent.server_agent_protocol import EEvent_item_completed, EItem_tool_call
from app.database import SessionLocal

from evals.replay import ReplayClient, assistant_messages_from_result
from evals.types import CheckOutcome, EvalResult, EvalTask, ToolCallRecord


def extract_tool_trace(events: list[dict[str, Any]]) -> list[ToolCallRecord]:
    trace: list[ToolCallRecord] = []
    for event in events:
        if event.get("type") != EEvent_item_completed:
            continue
        item = event.get("item") or {}
        if not isinstance(item, dict) or item.get("type") != EItem_tool_call:
            continue
        trace.append(ToolCallRecord(
            name=str(item.get("name") or ""),
            arguments=item.get("arguments") or {},
            result=item.get("result"),
            status=str(item.get("status") or ""),
        ))
    return trace


async def run_once(
    *,
    task: EvalTask,
    instructions: str,
    owner_id: int,
    client: Any | None = None,
    model: Optional[str] = None,
) -> tuple[ServerAgent, dict[str, Any]]:
    agent = await server_agent_manager.create_agent(
        owner_id=owner_id, agent_kind="server_agent", mode=task.mode
    )
    if client is not None:
        agent.client = client
    if model is not None:
        agent.model = model

    turn_db = SessionLocal()
    try:
        resume_result = await agent.resume(turn_db, instructions)
    finally:
        turn_db.close()
    return agent, resume_result


def _error_result(task: EvalTask, run_index: int, instructions: str, exc: Exception) -> EvalResult:
    return EvalResult(
        task_id=task.id,
        run_index=run_index,
        instructions=instructions,
        turn_id="",
        stop_reason="error",
        final_answer="",
        tool_trace=[],
        checks=[CheckOutcome("run_error", False, f"{type(exc).__name__}: {exc}")],
        passed=False,
        error=str(exc),
    )


async def run_task(
    task: EvalTask,
    *,
    owner_id: int,
    replay_messages: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    judge_enabled: bool = False,
    mock_inner: bool = False,
) -> list[EvalResult]:
    results: list[EvalResult] = []
    for run_index in range(task.runs):
        seed_db = SessionLocal()
        try:
            ctx = task.seed(seed_db, owner_id)
        finally:
            seed_db.close()
        instructions = task.instructions.format(**ctx)

        client = ReplayClient(replay_messages) if replay_messages is not None else None

        # 应用 patches(mock 工具内部的 LLM 调用)。
        # 只在 replay 或显式 --mock-inner 时应用; live 模式默认走真实内层 LLM, 否则 judge 无真材实料可评。
        error: Optional[Exception] = None
        stack = ExitStack()
        apply_patches = (replay_messages is not None) or mock_inner
        try:
            if apply_patches:
                for patch_obj in (task.patches or []):
                    stack.enter_context(patch_obj)
            try:
                _agent, resume_result = await run_once(
                    task=task,
                    instructions=instructions,
                    owner_id=owner_id,
                    client=client,
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批
                error = exc
        finally:
            stack.close()

        if error is not None:
            results.append(_error_result(task, run_index, instructions, error))
            continue

        tool_trace = extract_tool_trace(resume_result.get("events") or [])
        result = EvalResult(
            task_id=task.id,
            run_index=run_index,
            instructions=instructions,
            turn_id=str(resume_result.get("turn_id") or ""),
            stop_reason=str(resume_result.get("stop_reason") or ""),
            final_answer=str(resume_result.get("answer") or ""),
            tool_trace=tool_trace,
            checks=[],
            passed=True,
            assistant_messages=assistant_messages_from_result(resume_result.get("messages") or []),
        )

        for assertion in task.assertions:
            try:
                outcome = assertion(ctx, result)
            except Exception as exc:  # noqa: BLE001
                outcome = CheckOutcome("assertion_error", False, f"{type(exc).__name__}: {exc}")
            result.checks.append(outcome)

        # judge(仅 live 模式)
        if task.judge is not None and judge_enabled:
            judge_db = SessionLocal()
            try:
                prompt = task.judge.build_prompt(judge_db, ctx, result)
            finally:
                judge_db.close()
            if prompt.strip():
                from evals.judge import judge

                result.judge = await judge(task.judge.rubric, prompt)

        result.passed = all(c.passed for c in result.checks) and (
            result.judge is None or result.judge.passed
        )
        results.append(result)
    return results
