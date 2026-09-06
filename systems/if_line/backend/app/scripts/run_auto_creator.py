"""
CLI 离线脚本入口 —— 跑一次或多次 AutoCreator。

用法:
  python -m app.scripts.run_auto_creator --pace medium
  python -m app.scripts.run_auto_creator --pace fast --chapter-count 24 --theme "科幻+异世界" --count 3
  python -m app.scripts.run_auto_creator --word-min 2000 --word-max 4000
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 确保从 backend/ 目录直接跑也能正确解析 app.* 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal, check_database_connection, check_database_schema
from app.agent.auto_creator import run_many


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="自动化二创文章生成 Agent —— 离线脚本入口",
    )
    p.add_argument("--pace", choices=["fast", "medium", "slow"], default="medium",
                   help="叙事节奏偏好，不决定章节数，默认 medium")
    p.add_argument("--chapter-count", type=int, default=13,
                   help="大纲章节数，范围 1-500，默认 13")
    p.add_argument("--theme", default=None,
                   help="题材偏好, 留空则完全交给 LLM 自由发挥")
    p.add_argument("--count", type=int, default=1,
                   help="连续跑几篇, 默认 1")
    p.add_argument("--word-min", type=int, default=1500,
                   help="单章最少字数, 默认 1500")
    p.add_argument("--word-max", type=int, default=3000,
                   help="单章最多字数, 默认 3000")
    return p.parse_args()


async def main() -> int:
    args = parse_args()

    # Schema changes are a deployment concern. Refuse to run against an
    # un-migrated database instead of silently creating a partial schema.
    check_database_connection()
    check_database_schema()

    db = SessionLocal()
    try:
        project_ids = await run_many(
            db=db,
            count=args.count,
            pace=args.pace,
            chapter_count=args.chapter_count,
            theme_hint=args.theme,
        )
    finally:
        db.close()

    print(f"\n[DONE] 成功 {len(project_ids)}/{args.count} 篇, project_ids={project_ids}")
    return 0 if project_ids else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
