"""Evals CLI。

用法(在 backend 目录下):
  python -m evals.run_eval --list
  python -m evals.run_eval --live                              # 全量, 真实 LLM + judge(需 key)
  python -m evals.run_eval --replay                            # 全量回放, 自动按 task id 找 cassette
  python -m evals.run_eval --task query.view_project --replay  # 只跑某一族/某一条
  python -m evals.run_eval --task query.view_project --live --record   # 跑完写 cassette

--live      真实 LLM(agent + 内层 LLM 都真实)
--replay    cassette 回放(agent LLM 回放 + 内层 LLM mock), 免费确定性, 用于打磨 grader
--mock-inner  live 下也 mock 内层 LLM(省成本, 只测工具编排)
--no-judge   live 下跳过 LLM-judge
--record     跑完把 assistant 消息写到 cassettes/<task_id>.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_CASSETTES_DIR = Path(__file__).resolve().parent / "cassettes"


def _default_sqlite_url() -> str:
    path = Path(tempfile.gettempdir()) / f"if-line-eval-{os.getpid()}.sqlite3"
    path.unlink(missing_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _configure_env(db_url: str | None) -> None:
    """必须在 import 任何 app 模块之前调用。"""
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = db_url or _default_sqlite_url()
    os.environ["CHECK_DEPENDENCIES_ON_STARTUP"] = "false"
    # 加载 backend/.env(不覆盖上面已设的 DATABASE_URL), 让 live 模式能拿到 key。
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    except Exception:
        pass


def _render_result(r) -> None:
    status = "PASS" if r.passed else "FAIL"
    print(f"[{status}] run={r.run_index} turn={r.turn_id} stop={r.stop_reason}")
    if r.error:
        print(f"    error: {r.error}")
    print(f"    instructions: {r.instructions}")
    print(f"    tools: {[t.name for t in r.tool_trace]}")
    print(f"    answer: {r.final_answer[:200]}")
    for c in r.checks:
        mark = "ok" if c.passed else "XX"
        print(f"    [{mark}] {c.name}: {c.detail[:200]}")
    if r.judge is not None:
        j = r.judge
        print(f"    [judge:{j.name}] score={j.score} passed={j.passed} dims={j.dimensions}")
        for issue in j.issues[:5]:
            print(f"        - {issue}")
        if j.reason:
            print(f"        reason: {j.reason[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="if_line Agent evals")
    parser.add_argument("--task", help="task id 前缀匹配; 缺省跑全部")
    parser.add_argument("--list", action="store_true", help="列出所有 task")
    parser.add_argument("--live", action="store_true", help="真实 LLM 跑(需 key)")
    parser.add_argument("--replay", action="store_true", help="cassette 回放(自动按 task id 找 cassette)")
    parser.add_argument("--cassettes-dir", default=str(DEFAULT_CASSETTES_DIR), help="cassette 目录")
    parser.add_argument("--record", action="store_true", help="live 模式跑完写 cassette 到 cassettes/<task_id>.json")
    parser.add_argument("--db-url", help="覆盖 DATABASE_URL(默认临时 sqlite)")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--no-judge", action="store_true", help="live 模式下跳过 LLM-judge")
    parser.add_argument("--mock-inner", action="store_true", help="live 模式下也 mock 内部 LLM(省成本, 只测工具编排)")
    parser.add_argument("--report-dir", default="evals/reports", help="报告输出目录")
    args = parser.parse_args()

    _configure_env(args.db_url)

    # 延迟 import, 保证 app.database 用上面配置的环境创建 engine。
    import evals.sqlite_compat  # noqa: F401  BigInteger 在 sqlite 下自增兼容
    from app.database import Base, engine
    import app.models  # noqa: F401  注册表
    import app.models_v2  # noqa: F401

    from evals.replay import load_cassette, save_cassette
    from evals.runner import run_task
    from evals.seeders import ensure_user
    from evals.tasks import ALL_TASKS

    Base.metadata.create_all(bind=engine)

    if args.list:
        for t in ALL_TASKS:
            print(f"{t.id:44} {t.name}")
        return 0

    if args.live and args.replay:
        print("--live 与 --replay 互斥")
        return 2

    tasks = [t for t in ALL_TASKS if args.task is None or t.id.startswith(args.task)]
    if not tasks:
        print(f"没有匹配的任务: {args.task!r}")
        return 1

    cassettes_dir = Path(args.cassettes_dir)
    judge_enabled = args.live and not args.no_judge
    model = args.model or None

    owner_db = None
    try:
        from app.database import SessionLocal

        owner_db = SessionLocal()
        owner_id = ensure_user(owner_db)
    finally:
        if owner_db is not None:
            owner_db.close()

    all_results = []
    skipped = 0
    for task in tasks:
        replay_messages = None
        if args.replay:
            cassette_path = cassettes_dir / f"{task.id}.json"
            if not cassette_path.exists():
                print(f"[skip] {task.id}: 缺 cassette {cassette_path}")
                skipped += 1
                continue
            cassette = load_cassette(cassette_path)
            replay_messages = cassette["assistant_messages"]
            print(f"[replay] {task.id}: {len(replay_messages)} 条 assistant 消息")

        print(f"\n=== {task.id} ({task.name}) mode={task.mode} runs={task.runs} ===")
        results = asyncio.run(run_task(
            task,
            owner_id=owner_id,
            replay_messages=replay_messages,
            model=model,
            judge_enabled=judge_enabled,
            mock_inner=args.mock_inner,
        ))
        all_results.extend(results)
        for r in results:
            _render_result(r)

        if args.record and args.live and results and not results[0].error:
            out_path = cassettes_dir / f"{task.id}.json"
            save_cassette(
                out_path,
                task.id,
                results[0].instructions,
                model or "",
                results[0].assistant_messages,
            )
            print(f"[record] 已写入 {out_path}")

    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    judged = sum(1 for r in all_results if r.judge is not None)
    print(f"\n=== 汇总: {passed}/{total} passed, judged={judged}, skipped={skipped} ===")

    # 评分报告(JSON + markdown), 供调优前后对比
    from datetime import datetime, timezone

    from evals.report import build_report, write_report

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "replay",
        "model": model or "default",
        "judge_enabled": judge_enabled,
        "mock_inner": args.mock_inner,
        "task_filter": args.task or "(all)",
    }
    json_path, md_path = write_report(all_results, meta, args.report_dir)
    s = build_report(all_results, meta)["summary"]
    print(
        f"[score] checker_pass_rate={s['checker_pass_rate']:.1%} "
        f"avg_judge={s['avg_judge_score']} overall={s['overall_score']}/100 "
        f"(judged={s['judged_runs']}/{s['total_runs']})"
    )
    print(f"[report] {md_path}")
    print(f"[report] {json_path}")

    if total == 0:
        return 1
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
