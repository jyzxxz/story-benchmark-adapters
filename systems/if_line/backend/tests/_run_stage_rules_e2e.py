"""E2E driver: 表演规范一期全链路验证（真实 worker、真实 LLM）。

用法:
  venv/bin/python tests/_run_stage_rules_e2e.py script     # A: 触发 script 生成并等待
  venv/bin/python tests/_run_stage_rules_e2e.py inspect    #    检查最新 script revision / IR / 槽
  venv/bin/python tests/_run_stage_rules_e2e.py render     # B: 渲染 portrait+background 并等待
  venv/bin/python tests/_run_stage_rules_e2e.py graph      # C: 拉取编译产物并汇总节点统计
  venv/bin/python tests/_run_stage_rules_e2e.py release    # D: 发布并打印公开 vn-graph URL
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")

from app.database import SessionLocal  # noqa: E402
from app.models_v2 import (  # noqa: E402
    ChapterRevision,
    ChapterScriptRevision,
    GenerationTask,
    ScriptResourceSlot,
    StoryPathChapter,
    VNGraphRevision,
)

USER_ID = 83
PROJECT_ID = 52
PATH_ID = "05395995-0a62-4cd8-9396-228a63626a1e"


def _chapter_revision_id(db) -> str:
    ch = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == PATH_ID,
            StoryPathChapter.display_index == 1,
        )
        .one()
    )
    assert ch.current_revision_id, "chapter 1 无 current_revision"
    return ch.current_revision_id


def _latest_script_revision(db):
    cr_id = _chapter_revision_id(db)
    return (
        db.query(ChapterScriptRevision)
        .filter(ChapterScriptRevision.chapter_revision_id == cr_id)
        .order_by(ChapterScriptRevision.created_at.desc())
        .first()
    )


def _wait_task(db, task_id: str, timeout: float = 600.0) -> GenerationTask:
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.expire_all()
        task = db.query(GenerationTask).filter(GenerationTask.id == task_id).one()
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task
        time.sleep(3)
    raise TimeoutError(f"task {task_id} 未在 {timeout}s 内完成")


def phase_script() -> None:
    from app.application.chapter_script_service import create_chapter_script_generation_task

    db = SessionLocal()
    cr_id = _chapter_revision_id(db)
    key = f"e2e-stage-v3:{int(time.time())}"
    task, created = create_chapter_script_generation_task(
        db, user_id=USER_ID, chapter_revision_id=cr_id, idempotency_key=key
    )
    db.commit()
    print(f"task {task.id} created={created} status={task.status}")
    task = _wait_task(db, task.id, timeout=900)
    print(f"final status={task.status} error={task.error_code} {str(task.error_detail)[:300]}")
    db.close()


def phase_inspect() -> None:
    db = SessionLocal()
    srev = _latest_script_revision(db)
    print(f"script revision {srev.id}")
    print(f"  generator_version = {srev.generator_version}")
    script = srev.script_json or {}
    ann = script.get("annotations") or {}
    paragraphs = ann.get("paragraphs") or []
    with_display = sum(1 for p in paragraphs if p.get("speaker_display_name"))
    with_events = sum(1 for p in paragraphs if p.get("stage_events"))
    emotions = Counter(str(p.get("emotion") or "") for p in paragraphs)
    print(f"  paragraphs={len(paragraphs)} display_name={with_display} stage_events={with_events}")
    print(f"  emotions={dict(emotions)}")
    display_samples = [
        p.get("speaker_display_name") for p in paragraphs if p.get("speaker_display_name")
    ][:8]
    print(f"  display samples={display_samples}")
    event_samples = [
        (p["paragraph_id"], p["stage_events"]) for p in paragraphs if p.get("stage_events")
    ][:5]
    print(f"  event samples={event_samples}")
    slots = (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == srev.id)
        .all()
    )
    print(f"  slots={len(slots)} roles={Counter(s.role for s in slots)}")
    for s in slots:
        print(f"    {s.slot_key}  status={s.status}")
    db.close()


def phase_render() -> None:
    from app.application.chapter_script_service import request_script_resource_render

    db = SessionLocal()
    srev = _latest_script_revision(db)
    for role in ("portrait", "background", "keyframe"):
        result = request_script_resource_render(
            db,
            user_id=USER_ID,
            project_id=PROJECT_ID,
            script_revision_id=srev.id,
            role=role,
            idempotency_key=f"e2e-render-{role}:{int(time.time())}",
        )
        db.commit()
        print(f"{role}: parent={result['parent'].id} slots={result.get('slot_ids')} compile={result.get('vngraph_task_id')}")
        task = _wait_task(db, result["parent"].id, timeout=1800)
        print(f"{role}: final={task.status} error={task.error_code} {str(task.error_detail)[:300]}")
    db.expire_all()
    slots = (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == srev.id)
        .all()
    )
    print("slot states:", Counter(s.status for s in slots))
    db.close()


def phase_graph() -> None:
    db = SessionLocal()
    srev = _latest_script_revision(db)
    graphs = (
        db.query(VNGraphRevision)
        .filter(VNGraphRevision.script_revision_id == srev.id)
        .order_by(VNGraphRevision.created_at.desc())
        .all()
    )
    if not graphs:
        print("no vn graph revision yet")
        return
    g = graphs[0]
    graph = g.graph_json or {}
    meta = graph.get("Meta") or {}
    print(f"vn graph revision {g.id} created={g.created_at}")
    print(f"  compiler={meta.get('compiler_version')} policy={meta.get('tachi_policy_version')} manifest={meta.get('binding_manifest_version')}")
    counts = Counter(meta.get("performance_node_counts") or {})
    print(f"  node counts={dict(counts)}")
    print(f"  stage_warnings={meta.get('stage_warnings')}")
    nodes = graph.get("Nodes") or []
    print(f"  total nodes={len(nodes)} subtypes={Counter(n.get('SubType') for n in nodes)}")
    for n in nodes[:40]:
        data = n.get("Data") or {}
        brief = {k: data[k] for k in ("TachiID", "TachiIamge", "TargetPosition", "EnterType", "ExitType", "ClearType", "BackgroundImage", "ChangeType", "HighlightAlpha", "DimAlpha") if k in data}
        print(f"    [{n.get('SubType'):>2}] {n.get('SubTypeName','')} {brief}")
    db.close()


def phase_release() -> None:
    db = SessionLocal()
    from app.models_v2 import ProjectRelease

    releases = (
        db.query(ProjectRelease)
        .filter(ProjectRelease.project_id == PROJECT_ID)
        .order_by(ProjectRelease.created_at.desc())
        .all()
    )
    for r in releases:
        print(f"release {r.id} status={r.status} version={getattr(r, 'version', None)}")
    db.close()


PHASES = {
    "script": phase_script,
    "inspect": phase_inspect,
    "render": phase_render,
    "graph": phase_graph,
    "release": phase_release,
}

if __name__ == "__main__":
    PHASES[sys.argv[1]]()
