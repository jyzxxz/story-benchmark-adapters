"""从指定项目/显示章节当前选中的 Script 重新编译 VNGraph。

用法::

    venv/bin/python -m app.scripts.recompile_vngraph 27 1
    venv/bin/python -m app.scripts.recompile_vngraph 27 1 --no-dispatch   # 只入队，不主动触发 worker

默认会通过 celery_app.send_task 立即触发 vngraph_compile 任务并等待结果。
结果保持 ready，必须另行激活精确 Script 的 VNGraph Head。
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.application.vn_graph_service import create_vn_graph_compile_task
from app.database import SessionLocal
from app.models import Project
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    GenerationTask,
    VNGraphRevision,
)
from app.workers.celery_app import celery_app


TERMINAL = {"succeeded", "failed", "timeout", "cancelled"}


def _resolve_script_revision(
    db: Session,
    project_id: int,
    chapter_index: int,
) -> ChapterScriptRevision:
    head = (
        db.query(ChapterHead)
        .filter(
            ChapterHead.project_id == project_id,
            ChapterHead.chapter_index == chapter_index,
        )
        .first()
    )
    if head and head.current_revision_id:
        rev = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.id == head.current_revision_id)
            .first()
        )
        if rev and rev.status == "complete" and rev.content:
            script_head = (
                db.query(ChapterScriptHead)
                .filter(ChapterScriptHead.chapter_revision_id == rev.id)
                .first()
            )
            script = (
                db.query(ChapterScriptRevision)
                .filter(
                    ChapterScriptRevision.id == (
                        script_head.current_revision_id if script_head else None
                    ),
                    ChapterScriptRevision.chapter_revision_id == rev.id,
                )
                .first()
            )
            if script and script.status in {"ready", "complete"}:
                return script
    raise RuntimeError(
        f"project {project_id} chapter {chapter_index} 没有已选择的 ChapterScriptRevision"
    )


def recompile(
    project_id: int,
    chapter_index: int,
    *,
    dispatch: bool = True,
    poll_timeout_s: int = 600,
) -> tuple[str, VNGraphRevision]:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise RuntimeError(f"project {project_id} 不存在")
        owner_id = project.owner_id
        if not owner_id:
            raise RuntimeError(f"project {project_id} 没有 owner_id，无法 enqueue task")

        script = _resolve_script_revision(db, project_id, chapter_index)
        idempotency_key = f"recompile-{script.id}-{int(time.time())}"

        task, _created = create_vn_graph_compile_task(
            db,
            user_id=owner_id,
            script_revision_id=script.id,
            idempotency_key=idempotency_key,
        )
        db.commit()
        task_id = task.id
        print(f"[recompile] enqueued task_id={task_id} script_revision={script.id}")

        if not dispatch:
            return task_id, _await_revision(db, task_id, poll_timeout_s)

        celery_app.send_task("if_line.vngraph_compile", args=[task_id])
        print(f"[recompile] dispatched to celery: {task_id}")
        return task_id, _await_revision(db, task_id, poll_timeout_s)
    finally:
        db.close()


def _await_revision(
    db: Session,
    task_id: str,
    timeout_s: int,
) -> VNGraphRevision:
    deadline = time.monotonic() + timeout_s
    last_status: Optional[str] = None
    while time.monotonic() < deadline:
        db.expire_all()
        task = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
        status = task.status if task else "?"
        if status != last_status:
            print(f"[recompile] task status: {status}")
            last_status = status
        if status in TERMINAL:
            if status != "succeeded":
                raise RuntimeError(
                    f"task {task_id} 终态={status} "
                    f"error_code={getattr(task, 'error_code', None)} "
                    f"detail={getattr(task, 'error_detail', None)}"
                )
            break
        time.sleep(2)

    revision_id = (task.result_refs or {}).get("vngraph_revision_id") if task else None
    if not revision_id:
        raise RuntimeError("compile 完成但任务没有 vngraph_revision_id")
    revision = (
        db.query(VNGraphRevision)
        .filter(VNGraphRevision.id == revision_id)
        .first()
    )
    if not revision:
        raise RuntimeError(f"VNGraphRevision {revision_id} 不存在")

    graph = revision.graph_json or {}
    nodes = graph.get("Nodes") or []
    para_nodes = [n for n in nodes if n.get("SubType") == 2]
    total_lines = sum(
        len((n.get("Data") or {}).get("Lines", {}).get("Items") or []) for n in para_nodes
    )
    compiler = (graph.get("Meta") or {}).get("compiler") or {}
    print(
        f"[recompile] OK revision_id={revision.id} revision_no={revision.revision_no}\n"
        f"  nodes={len(nodes)} paragraph_nodes={len(para_nodes)} total_lines={total_lines}\n"
        f"  compiler_version={compiler.get('compiler_version')}\n"
        f"  graph_hash={revision.graph_hash}\n"
        "  status=ready (use VNGraph Head activation to select it)"
    )
    return revision


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Re-compile VNGraph from the chapter's selected Script revision"
    )
    parser.add_argument("project_id", type=int)
    parser.add_argument("chapter_index", type=int)
    parser.add_argument("--no-dispatch", action="store_true", help="只入队不主动触发 celery")
    parser.add_argument("--timeout", type=int, default=600, help="轮询超时秒数")
    args = parser.parse_args(argv)

    try:
        recompile(
            args.project_id,
            args.chapter_index,
            dispatch=not args.no_dispatch,
            poll_timeout_s=args.timeout,
        )
        return 0
    except Exception as exc:
        print(f"[recompile] FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
