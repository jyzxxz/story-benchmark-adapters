"""
回填历史 ChapterOutline 数据的 scene / emotion 字段。

旧的大纲生成 prompt 输出 `scene_locations` / `emotion_shift`，但 ORM 字段是
`scene` / `emotion`，Pydantic schema 又把这俩设成 Optional，于是历史数据这俩字段全是 None。

这个脚本对每个 None scene / emotion 的 outline，用 summary / conflict 兜底填充。
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal
from app.models import ChapterOutline


def backfill():
    db = SessionLocal()
    try:
        outlines = db.query(ChapterOutline).filter(
            (ChapterOutline.scene.is_(None)) | (ChapterOutline.emotion.is_(None))
        ).all()
        print(f"[Backfill] 找到 {len(outlines)} 条需要回填的 outline")

        updated = 0
        for o in outlines:
            changed = False
            if not o.scene:
                # 用 summary 前 60 字兜底
                fallback = (o.summary or "").strip()
                if fallback:
                    o.scene = fallback[:60]
                    changed = True
            if not o.emotion:
                fallback = (o.conflict or "").strip() or (o.summary or "").strip()
                if fallback:
                    o.emotion = fallback[:60]
                    changed = True
            if changed:
                updated += 1
                print(f"  [+] ch{o.chapter_index} (project={o.project_id}) scene={o.scene[:40] if o.scene else None!r}")

        if updated:
            db.commit()
            print(f"[Backfill] 提交 {updated} 条更新")
        else:
            print("[Backfill] 无需更新")
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
