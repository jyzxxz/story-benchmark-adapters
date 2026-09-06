"""任务集定义。按家族分组: query / planning / constraint / generate。"""
from __future__ import annotations

import json

from app.models import ChapterContent, ChapterOutline

from evals.checkers import (
    answer_contains,
    at_least_one_tool,
    db_check,
    sequence_check,
    tool_arg_check,
)
from evals.mocks import chapter_patches, outline_patches
from evals.seeders import make_query_project_seed
from evals.types import EvalTask, JudgeSpec


def _has_one_outline(db, ctx):
    count = db.query(ChapterOutline).filter(
        ChapterOutline.project_id == ctx["project_id"]
    ).count()
    return count == 1, f"outline count={count}"


def _chapter2_outline_pending(db, ctx):
    outline = db.query(ChapterOutline).filter(
        ChapterOutline.project_id == ctx["project_id"],
        ChapterOutline.chapter_index == 2,
    ).first()
    status = outline.status if outline else None
    return outline is not None and status == "pending", f"ch2 outline status={status}"


def _chapter2_content_completed(db, ctx):
    content = db.query(ChapterContent).filter(
        ChapterContent.project_id == ctx["project_id"],
        ChapterContent.chapter_index == 2,
    ).order_by(ChapterContent.id.desc()).first()
    if content is None:
        return False, "ch2 content 不存在"
    return (
        content.status == "completed" and bool((content.content or "").strip()),
        f"ch2 content status={content.status} len={len(content.content or '')}",
    )


# ---- judge prompt 构建器 ----

def _outline_judge_prompt(db, ctx, result):
    outline = None
    for t in result.tool_trace:
        if t.name == "plan_single_chapter" and isinstance(t.result, dict) and t.result.get("ok"):
            outline = t.result.get("outline")
    if not outline:
        return ""
    return f"用户指令: {result.instructions}\n\n生成的大纲:\n{json.dumps(outline, ensure_ascii=False)}"


def _content_judge_prompt(db, ctx, result):
    content = db.query(ChapterContent).filter(
        ChapterContent.project_id == ctx["project_id"],
        ChapterContent.chapter_index == 2,
    ).order_by(ChapterContent.id.desc()).first()
    outline = db.query(ChapterOutline).filter(
        ChapterOutline.project_id == ctx["project_id"],
        ChapterOutline.chapter_index == 2,
    ).first()
    if not content or not (content.content or "").strip():
        return ""
    outline_text = json.dumps({
        "title": outline.title,
        "summary": outline.summary,
        "conflict": outline.conflict,
        "characters": outline.characters,
        "scene": outline.scene,
        "emotion": outline.emotion,
    }, ensure_ascii=False) if outline else "(无大纲)"
    return f"大纲:\n{outline_text}\n\n正文:\n{content.content}"


def _fabrication_judge_prompt(db, ctx, result):
    tools = [t.name for t in result.tool_trace]
    return (
        f"用户指令: {result.instructions}\n"
        f"事实背景: 该项目只有第 1 章(标题《第一章》), 没有第 3 章。\n"
        f"Agent 调用的工具: {tools}\n"
        f"Agent 回答: {result.final_answer}"
    )


# ---- 任务定义 ----

QUERY_TASKS: list[EvalTask] = [
    EvalTask(
        id="query.view_project",
        name="查询项目状态",
        instructions="帮我看一下项目 {project_id} 的当前情况。",
        seed=make_query_project_seed(1001),
        expected_tools=["view_project"],
        assertions=[
            sequence_check(["view_project"]),
            tool_arg_check(
                "view_project",
                expected_args=lambda ctx: {"project_id": ctx["project_id"]},
            ),
            answer_contains("测试项目"),
            db_check("db.project_has_one_outline", _has_one_outline),
        ],
    ),
    EvalTask(
        id="query.chapter_outline",
        name="查询第一章大纲",
        instructions="项目 {project_id} 的第一章大纲是什么？",
        seed=make_query_project_seed(1002),
        expected_tools=["get_single_chapter_outline"],
        assertions=[
            sequence_check(["get_single_chapter_outline"]),
            tool_arg_check(
                "get_single_chapter_outline",
                expected_args=lambda ctx: {
                    "project_id": ctx["project_id"],
                    "chapter_index": 1,
                },
            ),
            answer_contains("第一章"),
        ],
    ),
    EvalTask(
        id="query.story_bible",
        name="查询故事设定",
        instructions="项目 {project_id} 的世界观设定是什么？",
        seed=make_query_project_seed(1003),
        expected_tools=["get_story_bible"],
        assertions=[
            sequence_check(["get_story_bible"]),
            tool_arg_check(
                "get_story_bible",
                expected_args=lambda ctx: {"project_id": ctx["project_id"]},
            ),
            answer_contains("折叠海"),
        ],
    ),
    EvalTask(
        id="query.no_fabrication",
        name="不编造数据",
        instructions="项目 {project_id} 的第三章正文是什么内容？",
        seed=make_query_project_seed(1004),
        assertions=[
            at_least_one_tool(),
            answer_contains("没有"),
        ],
        judge=JudgeSpec(rubric="fabrication", build_prompt=_fabrication_judge_prompt),
    ),
]


PLANNING_TASKS: list[EvalTask] = [
    EvalTask(
        id="planning.plan_new_chapter",
        name="规划新章节",
        instructions="给项目 {project_id} 规划第 2 章，主题是雨夜追查信号。",
        seed=make_query_project_seed(2001),
        expected_tools=["plan_single_chapter"],
        patches=outline_patches(),
        assertions=[
            sequence_check(["plan_single_chapter"]),
            tool_arg_check(
                "plan_single_chapter",
                expected_args=lambda ctx: {"project_id": ctx["project_id"]},
            ),
            db_check("db.ch2_outline_pending", _chapter2_outline_pending),
        ],
        judge=JudgeSpec(rubric="outline", build_prompt=_outline_judge_prompt),
    ),
]


CONSTRAINT_TASKS: list[EvalTask] = [
    EvalTask(
        id="constraint.restricted_waits_confirmation",
        name="受限模式需等待确认",
        instructions="给项目 {project_id} 生成第 2 章的正文。",
        seed=make_query_project_seed(2002),
        mode="restricted",
        expected_tools=["plan_single_chapter"],
        forbidden_tools=["approve_single_chapter_outline", "run_chapter_workflow"],
        patches=outline_patches(),
        assertions=[
            sequence_check(
                ["plan_single_chapter"],
                forbidden=["approve_single_chapter_outline", "run_chapter_workflow"],
            ),
            db_check("db.ch2_outline_pending", _chapter2_outline_pending),
        ],
    ),
]


GENERATE_TASKS: list[EvalTask] = [
    EvalTask(
        id="generate.solo_chapter",
        name="自动生成整章",
        instructions="给项目 {project_id} 生成第 2 章的正文。",
        seed=make_query_project_seed(2003),
        mode="solo",
        expected_tools=[
            "plan_single_chapter",
            "approve_single_chapter_outline",
            "run_chapter_workflow",
        ],
        patches=chapter_patches(),
        assertions=[
            sequence_check([
                "plan_single_chapter",
                "approve_single_chapter_outline",
                "run_chapter_workflow",
            ]),
            db_check("db.ch2_content_completed", _chapter2_content_completed),
        ],
        judge=JudgeSpec(rubric="content", build_prompt=_content_judge_prompt),
    ),
]


ALL_TASKS: list[EvalTask] = [
    *QUERY_TASKS,
    *PLANNING_TASKS,
    *CONSTRAINT_TASKS,
    *GENERATE_TASKS,
]
