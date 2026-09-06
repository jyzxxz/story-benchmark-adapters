"""评分聚合 + 报告输出。

产出两种形态, 供调优前后对比:
- JSON: 机器可读, 方便 diff / 入库
- markdown: 人读摘要

评分口径(单条 run 的 0-100 分):
- 有 judge 的: 用 judge.score
- 没 judge 的: 通过=100, 失败=0
综合分(overall_score)= 所有 run 得分的均值; 另单独报 checker 通过率(确定性, 稳定)
和 judge 平均分(质量, 有噪声), 调优时三个数一起看。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.types import EvalResult


def _family(task_id: str) -> str:
    return task_id.split(".")[0]


def build_report(results: list[EvalResult], meta: dict[str, Any]) -> dict[str, Any]:
    per_task: dict[str, dict[str, Any]] = {}
    for r in results:
        entry = per_task.setdefault(r.task_id, {
            "task_id": r.task_id,
            "family": _family(r.task_id),
            "runs": 0,
            "passed_runs": 0,
            "judge_scores": [],
            "failed_checks": [],
        })
        entry["runs"] += 1
        if r.passed:
            entry["passed_runs"] += 1
        if r.judge is not None:
            entry["judge_scores"].append(r.judge.score)
        if not r.passed:
            for c in r.checks:
                if not c.passed:
                    entry["failed_checks"].append(f"{c.name}: {c.detail[:120]}")

    tasks: list[dict[str, Any]] = []
    total_runs = 0
    passed_runs = 0
    all_judge_scores: list[int] = []
    for _task_id, entry in sorted(per_task.items()):
        total_runs += entry["runs"]
        passed_runs += entry["passed_runs"]
        all_judge_scores.extend(entry["judge_scores"])
        tasks.append({
            "task_id": entry["task_id"],
            "family": entry["family"],
            "runs": entry["runs"],
            "passed_runs": entry["passed_runs"],
            "pass_rate": round(entry["passed_runs"] / entry["runs"], 3),
            "avg_judge_score": (
                round(sum(entry["judge_scores"]) / len(entry["judge_scores"]), 1)
                if entry["judge_scores"] else None
            ),
            "failed_checks": entry["failed_checks"],
        })

    checker_pass_rate = round(passed_runs / total_runs, 3) if total_runs else 0.0
    avg_judge = (
        round(sum(all_judge_scores) / len(all_judge_scores), 1)
        if all_judge_scores else None
    )

    run_scores = [
        r.judge.score if r.judge is not None else (100 if r.passed else 0)
        for r in results
    ]
    overall = round(sum(run_scores) / len(run_scores), 1) if run_scores else 0.0

    return {
        "meta": meta,
        "summary": {
            "total_tasks": len(tasks),
            "total_runs": total_runs,
            "passed_runs": passed_runs,
            "checker_pass_rate": checker_pass_rate,
            "judged_runs": len(all_judge_scores),
            "avg_judge_score": avg_judge,
            "overall_score": overall,
        },
        "per_task": tasks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    meta = report["meta"]
    lines = [
        "# Eval Report",
        "",
        f"- 时间: {meta.get('generated_at', '')}",
        f"- 模式: {meta.get('mode', '')}   模型: {meta.get('model', '')}",
        "",
        "## 汇总",
        "",
        f"- 任务 {s['total_tasks']} / run {s['total_runs']} / 通过 {s['passed_runs']}",
        f"- checker 通过率: **{s['checker_pass_rate']:.1%}**",
        f"- judge 平均分: **{s['avg_judge_score']}** (judged {s['judged_runs']})" if s["avg_judge_score"] is not None else "- judge 平均分: N/A",
        f"- 综合分: **{s['overall_score']}/100**",
        "",
        "## 明细",
        "",
        "| task | family | pass@k | avg judge |",
        "|---|---|---|---|",
    ]
    for t in report["per_task"]:
        judge = str(t["avg_judge_score"]) if t["avg_judge_score"] is not None else "-"
        lines.append(f"| {t['task_id']} | {t['family']} | {t['passed_runs']}/{t['runs']} | {judge} |")

    failed = [t for t in report["per_task"] if t["passed_runs"] < t["runs"]]
    if failed:
        lines += ["", "## 失败明细", ""]
        for t in failed:
            lines.append(f"### {t['task_id']} ({t['passed_runs']}/{t['runs']})")
            for c in t["failed_checks"]:
                lines.append(f"- {c}")
            lines.append("")
    return "\n".join(lines)


def write_report(
    results: list[EvalResult],
    meta: dict[str, Any],
    out_dir: str | Path,
) -> tuple[Path, Path]:
    report = build_report(results, meta)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"report-{ts}.json"
    md_path = out_dir / f"report-{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
