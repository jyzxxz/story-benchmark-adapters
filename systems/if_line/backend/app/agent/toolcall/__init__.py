"""
服务端 Agent 可调用工具包。
"""
from app.agent.agent_spec import AgentToolHandle
from app.agent.toolcall.chapter_tools import (
    CHAPTER_TOOL_SCHEMAS,
    execute_chapter_tool_async,
)
from app.agent.toolcall.story_bible_tools import (
    STORY_BIBLE_TOOL_SCHEMAS,
    execute_story_bible_tool_async,
)
from app.agent.toolcall.task_tools import TASK_TOOL_SCHEMAS, execute_task_tool
from app.agent.toolcall.project_tools import PROJECT_TOOL_SCHEMAS, execute_project_tool
from app.agent.toolcall.publish_tools import PUBLISH_TOOL_SCHEMAS, execute_publish_tool_async
from app.agent.toolcall.vngraph_tools import (
    VNGRAPH_TOOL_SCHEMAS,
    execute_vngraph_tool,
)


STORY_AGENT_TOOLCALL_SCHEMAS = [
    *PROJECT_TOOL_SCHEMAS,
    *VNGRAPH_TOOL_SCHEMAS,
    *TASK_TOOL_SCHEMAS,
    *STORY_BIBLE_TOOL_SCHEMAS,
    *CHAPTER_TOOL_SCHEMAS,
    *PUBLISH_TOOL_SCHEMAS,
]

CHAT_AGENT_TOOLCALL_SCHEMAS = []


def _build_toolcall_result(result=None, events=None):
    return {
        "result": result,
        "events": events or [],
    }


def _split_result_events(result):
    if not isinstance(result, dict):
        return result, []
    events = result.pop("_events", []) or []
    return result, events


async def execute_story_agent_toolcall(
    db,
    name,
    arguments,
    tool_call_id="",
    agent: AgentToolHandle | None = None,
):
    result = execute_project_tool(db, name, arguments, agent=agent)
    if result is not None:
        result, events = _split_result_events(result)
        return _build_toolcall_result(result=result, events=events)

    result = await execute_vngraph_tool(
        db,
        name,
        arguments,
        tool_call_id=tool_call_id,
        agent=agent,
    )
    if result is not None:
        result, events = _split_result_events(result)
        return _build_toolcall_result(result=result, events=events)

    result = await execute_task_tool(db, name, arguments, agent=agent)
    if result is not None:
        result, events = _split_result_events(result)
        return _build_toolcall_result(result=result, events=events)

    result = await execute_story_bible_tool_async(
        db,
        name,
        arguments,
        tool_call_id=tool_call_id,
        agent=agent,
    )
    if result is not None:
        result, events = _split_result_events(result)
        return _build_toolcall_result(result=result, events=events)

    result = await execute_chapter_tool_async(
        db,
        name,
        arguments,
        tool_call_id=tool_call_id,
        agent=agent,
    )
    if result is not None:
        result, events = _split_result_events(result)
        return _build_toolcall_result(result=result, events=events)

    result = await execute_publish_tool_async(db, name, arguments, agent=agent)
    if result is not None:
        result, events = _split_result_events(result)
        return _build_toolcall_result(result=result, events=events)

    return _build_toolcall_result(result={"ok": False, "error": f"未知工具: {name}"})


async def execute_chat_agent_toolcall(
    db,
    name,
    arguments,
    tool_call_id="",
    agent: AgentToolHandle | None = None,
):
    return _build_toolcall_result(
        result={
            "ok": False,
            "error": f"chat_agent 不支持工具调用: {name}",
        }
    )
