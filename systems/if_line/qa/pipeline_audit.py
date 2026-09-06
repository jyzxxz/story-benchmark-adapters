#!/usr/bin/env python3
"""确定性管线审计:对一个已生成项目跑文本层/结构层/素材层指标,输出 JSON 评分。

用法(仓库根目录):
  backend/venv/bin/python qa/pipeline_audit.py --project-id 123
  backend/venv/bin/python qa/pipeline_audit.py --latest        # 最近一个项目
  加 --json 只输出 JSON。

本脚本只做确定性检查,不下判断;LLM 审查 agent 以此为数据锚点。
每条 check 带 severity(hard/soft)与 suggestion,供修复路由。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Asset  # noqa: E402
from app.models_v2 import (  # noqa: E402
    ChapterRevision,
    ChapterScriptRevision,
    StoryBibleRevision,
    VNGraphRevision,
)
from app.utils.vn_graph_validator import vn_graph_validator  # noqa: E402

MIN_CHAPTER_WORDS = 600


def _latest_revision(stmt):
    """按 created_at 取每章/script/graph 的最新一条。"""
    return stmt


def audit_project(db, project_id: int) -> dict:
    report: dict = {"project_id": project_id, "checked_at": datetime.now(timezone.utc).isoformat(),
                    "checks": [], "score": 0}
    errors: list[dict] = []

    def check(layer, name, ok, severity, detail, suggestion=""):
        report["checks"].append({"layer": layer, "name": name, "ok": bool(ok),
                                 "severity": severity if not ok else None,
                                 "detail": detail, "suggestion": suggestion})
        if not ok:
            errors.append({"layer": layer, "name": name, "severity": severity, "detail": detail})

    # ---- bible ----
    bible = db.execute(
        select(StoryBibleRevision).where(StoryBibleRevision.project_id == project_id)
        .order_by(StoryBibleRevision.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if not bible:
        check("bible", "bible_exists", False, "hard", "无 story_bible_revision", "检查 bible 生成任务是否成功")
        report["score"] = 0
        return report
    characters = (bible.content_json or {}).get("characters") or []
    char_ids = {c.get("character_id") or c.get("id") for c in characters if isinstance(c, dict)}
    char_ids.discard(None)
    char_names = {c.get("name") for c in characters if isinstance(c, dict) and c.get("name")}
    char_ids = char_ids | char_names
    check("bible", "core_characters", len(characters) >= 2, "soft",
          f"bible 核心角色 {len(characters)} 个(ids={sorted(char_ids)[:8]})", "核心角色少于 2 个,检查 bible prompt")

    # ---- chapters(文本层) ----
    chapters = db.execute(
        select(ChapterRevision).where(ChapterRevision.project_id == project_id)
        .order_by(ChapterRevision.chapter_index, ChapterRevision.created_at.desc())
    ).scalars().all()
    latest_by_index: dict[int, ChapterRevision] = {}
    for ch in chapters:
        latest_by_index.setdefault(ch.chapter_index, ch)
    if not latest_by_index:
        check("text", "chapters_exist", False, "hard", "无 chapter_revisions", "检查章节生成任务")
        report["score"] = 0
        return report
    short = [i for i, ch in latest_by_index.items() if len((ch.content or "").strip()) < MIN_CHAPTER_WORDS]
    check("text", "chapter_length", not short, "soft",
          f"{len(latest_by_index)} 章,字数不足 {MIN_CHAPTER_WORDS} 的: {short[:10]}",
          "短章 → 检查扩写链路字数下限 prompt/参数")
    empty = [i for i, ch in latest_by_index.items() if not (ch.content or "").strip()]
    check("text", "chapter_nonempty", not empty, "hard", f"空章节: {empty}", "空章 → 生成任务失败未重试?")

    # ---- scripts(结构层) ----
    scripts = db.execute(
        select(ChapterScriptRevision).where(ChapterScriptRevision.project_id == project_id)
        .order_by(ChapterScriptRevision.chapter_index, ChapterScriptRevision.created_at.desc())
    ).scalars().all()
    latest_script: dict[int, ChapterScriptRevision] = {}
    for s in scripts:
        latest_script.setdefault(s.chapter_index, s)
    # speaker_character_id 是名字派生的 character-<hash>,以每章 script_json
    # 自带 characters 列表为合法集合;bible 名字作二级参照
    script_char_ids = set()
    for s in latest_script.values():
        for c in ((s.script_json or {}).get("characters") or []):
            if isinstance(c, dict):
                script_char_ids.add(c.get("character_id") or c.get("id"))
    char_refs = {x for x in (char_ids | script_char_ids) if x}
    missing_scripts = sorted(set(latest_by_index) - set(latest_script))
    check("structure", "script_coverage", not missing_scripts, "hard",
          f"缺脚本的章节: {missing_scripts}", "检查 chapter_script 生成任务")

    # 说话人引用 bible 角色(已知降级旁白 Bug 的量化)
    total_dialogue = 0
    orphan = 0
    def _scan_speakers(node):
        nonlocal total_dialogue, orphan
        if isinstance(node, dict):
            sid = node.get("speaker_character_id")
            if sid is not None:
                total_dialogue += 1
                if sid and sid not in char_refs:
                    orphan += 1
            for v in node.values():
                _scan_speakers(v)
        elif isinstance(node, list):
            for v in node:
                _scan_speakers(v)
    for s in latest_script.values():
        _scan_speakers(s.script_json)
    orphan_rate = round(orphan / total_dialogue, 3) if total_dialogue else 0.0
    check("structure", "speaker_refs_bible", orphan_rate <= 0.1, "soft",
          f"对白 {total_dialogue} 条,越界 speaker {orphan} 条(率 {orphan_rate})",
          "越界说话人被静默降级为旁白 → 检查 bible 角色回写链路")

    # ---- vn graphs(结构层) ----
    graphs = db.execute(
        select(VNGraphRevision).where(VNGraphRevision.project_id == project_id)
        .order_by(VNGraphRevision.chapter_index, VNGraphRevision.created_at.desc())
    ).scalars().all()
    latest_graph: dict[int, VNGraphRevision] = {}
    for g in graphs:
        latest_graph.setdefault(g.chapter_index, g)
    missing_graphs = sorted(set(latest_by_index) - set(latest_graph))
    check("structure", "vngraph_coverage", not missing_graphs, "hard",
          f"缺 VN 图的章节: {missing_graphs}", "检查 vn_graph 编译任务")

    validator_errors: list[str] = []
    node_count = 0
    for idx, g in sorted(latest_graph.items()):
        gj = g.graph_json or {}
        node_count += len(gj.get("Nodes") or gj.get("nodes") or [])
        try:
            res = vn_graph_validator.validate_detailed(gj)
            validator_errors.extend(f"ch{idx}: {e}" for e in (res.errors or [])[:3])
        except Exception as exc:  # noqa: BLE001
            validator_errors.append(f"ch{idx}: validator crash: {exc}")
    check("structure", "vngraph_valid", not validator_errors, "hard",
          f"校验错误 {len(validator_errors)} 条: {validator_errors[:5]}",
          "按 vn_graph_validator 错误定位编译器")
    check("structure", "vngraph_nodes", node_count > 0, "hard",
          f"总节点数 {node_count}", "图为空 → 编译输入 IR 可能为空")

    # ---- assets(素材层) ----
    assets = db.execute(select(Asset).where(Asset.project_id == project_id)).scalars().all()
    by_type = Counter(a.asset_type for a in assets)
    no_media = [a.id for a in assets if not a.image_url]
    check("asset", "assets_exist", sum(by_type.values()) > 0, "hard",
          f"素材分布 {dict(by_type)}", "零素材 → 检查素材生成任务")
    check("asset", "assets_media_url", not no_media, "soft",
          f"{len(no_media)} 个素材缺 image_url(status 分布 "
          f"{dict(Counter(a.status for a in assets))})",
          "素材无图片地址 → 检查生图任务失败/未完成后处理")

    # ---- 评分 ----
    weight = {"hard": 25, "soft": 8}
    penalty = sum(weight.get(e["severity"], 5) for e in errors)
    report["score"] = max(0, 100 - penalty)
    report["error_count"] = len(errors)
    report["hard_error_count"] = sum(1 for e in errors if e["severity"] == "hard")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int)
    ap.add_argument("--latest", action="store_true", help="取最近创建的项目")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        pid = args.project_id
        if pid is None:
            from app.models import Project
            pid = db.execute(select(func.max(Project.id))).scalar()
            if pid is None:
                print(json.dumps({"error": "no projects"})); return 1
            if not args.json:
                print(f"[audit] --latest → project_id={pid}")
        report = audit_project(db, pid)
    finally:
        db.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== 管线审计 project={pid} score={report['score']} "
              f"errors={report.get('error_count')} (hard={report.get('hard_error_count')}) ===")
        for c in report["checks"]:
            mark = "PASS" if c["ok"] else f"FAIL[{c['severity']}]"
            print(f"  {mark:10s} {c['layer']:10s} {c['name']:20s} {c['detail']}")
            if not c["ok"] and c["suggestion"]:
                print(f"             ↳ {c['suggestion']}")
    return 0 if report["score"] >= 80 and not report.get("hard_error_count") else 2


if __name__ == "__main__":
    sys.exit(main())
