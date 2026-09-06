"""
每次 AutoCreator 运行的中间产物落盘。

落盘目录: backend/agent_runs/<timestamp>/
  - idea.json       brainstorm 输出
  - bible.json      Story Bible raw_json
  - outline.json    Outline raw_json
  - chapters.json   每章 chapter_index -> content (摘要前 200 字)
  - error.log       失败时的 traceback
  - manifest.json   本次 run 的元信息(project_id, pace, 起止时间, 状态)

不进数据库,纯文件,便于事后人工查阅和故障排查。
"""
import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


RUNS_ROOT = Path(__file__).parent.parent.parent / "agent_runs"


def new_run_dir() -> Path:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_ROOT / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json(run_dir: Path, name: str, payload: Any) -> None:
    path = run_dir / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_error(run_dir: Optional[Path], exc: BaseException) -> None:
    if run_dir is None:
        return
    with open(run_dir / "error.log", "w", encoding="utf-8") as f:
        f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


def write_manifest(
    run_dir: Path,
    *,
    project_id: Optional[int],
    pace: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    chapter_count: int = 0,
    error: Optional[str] = None,
) -> None:
    manifest = {
        "project_id": project_id,
        "pace": pace,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": status,
        "chapter_count": chapter_count,
        "error": error,
    }
    write_json(run_dir, "manifest.json", manifest)
