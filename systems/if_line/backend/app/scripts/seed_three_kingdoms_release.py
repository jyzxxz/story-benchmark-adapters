"""Create the immutable release used by the public Three Kingdoms reader.

The complete 120-chapter reader remains backed by the checked-in VN JSON
files.  This compact release supplies the durable Project/Release identity
needed by guest reading sessions and dynamic continuation tasks.  The selected
chapter text is passed as the reading session's immutable initial state by the
frontend, so a visitor can start from any of the 120 chapters.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.application.chapter_script_service import ensure_backfilled_script_revision
from app.application.hashing import content_hash
from app.application.release_service import create_release
from app.application.revision_service import (
    activate_outline_revision,
    create_bible_revision,
    create_chapter_revision,
    create_outline_revision,
)
from app.database import SessionLocal
from app.models import Project, User
from app.models_v2 import (
    ProjectContentHead,
    StoryNode,
    VNGraphHead,
    VNGraphRevision,
    utcnow,
)


PROJECT_MARKER = "SYSTEM_PUBLIC_THREE_KINGDOMS_GUEST_TEMPLATE_V1"
SYSTEM_EMAIL = "system-three-kingdoms@ifline.local"
REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLIC_ROOT = REPO_ROOT / "frontend" / "public" / "three-kingdoms"


def _chapter_text(graph: dict) -> str:
    paragraphs: list[str] = []
    for node in graph.get("Nodes") or []:
        if node.get("NodeType") != 1 or node.get("SubType") != 2:
            continue
        for item in (((node.get("Data") or {}).get("Lines") or {}).get("Items") or []):
            obj = item.get("ObjectValue") or {}
            speaker = ((obj.get("SpeakerId") or {}).get("StringValue") or "旁白").strip()
            text = ((obj.get("Text") or {}).get("StringValue") or "").strip()
            if text:
                paragraphs.append(f"{speaker}：{text}" if speaker not in {"旁白", "诗赞"} else text)
    return "\n\n".join(paragraphs)


def seed() -> dict[str, object]:
    catalog = json.loads((PUBLIC_ROOT / "catalog.json").read_text(encoding="utf-8"))
    graph = json.loads((PUBLIC_ROOT / "chapters" / "001.json").read_text(encoding="utf-8"))
    first = catalog["chapters"][0]
    first_text = _chapter_text(graph)
    if catalog.get("chapterCount") != 120 or len(catalog.get("chapters") or []) != 120:
        raise RuntimeError("Three Kingdoms public catalog is not the complete 120-chapter edition")
    if not first_text:
        raise RuntimeError("Three Kingdoms first chapter has no readable ParagraphNode text")

    db = SessionLocal()
    try:
        existing = db.query(Project).filter(Project.extra_requirements == PROJECT_MARKER).first()
        if existing:
            head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == existing.id).first()
            if head and head.published_release_id:
                return {
                    "created": False,
                    "project_id": existing.id,
                    "release_id": head.published_release_id,
                    "chapter_count": 120,
                }
            raise RuntimeError("Three Kingdoms template exists without a published release")

        owner = db.query(User).filter(User.email == SYSTEM_EMAIL).first()
        if not owner:
            owner = User(
                email=SYSTEM_EMAIL,
                password_hash="system-login-disabled$three-kingdoms",
                display_name="三国演义公共模板",
                is_active=False,
                quota_total=0,
                quota_daily=0,
                quota_used_total=0,
                quota_used_daily=0,
                quota_reset_at=utcnow(),
            )
            db.add(owner)
            db.flush()

        project = Project(
            owner_id=owner.id,
            title="三国演义·公开互动版",
            characters=["刘备", "关羽", "张飞", "曹操", "孙权", "诸葛亮", "司马懿"],
            story_start="东汉末年，朝纲崩坏，黄巾起义，群雄由此登上历史舞台。",
            story_end="三分归晋，英雄功业尽入后人评说；游客可从任一章回另开支线。",
            style="古典历史演义",
            pace="medium",
            extra_requirements=PROJECT_MARKER,
            status="completed",
            visibility="private",
        )
        db.add(project)
        db.flush()

        bible = create_bible_revision(
            db,
            project_id=project.id,
            content={
                "title": "三国演义 Story Bible",
                "source": "罗贯中《三国演义》公开文本与结构化 VN 节点",
                "worldview": "东汉末年至西晋统一，以史实框架承载忠义、权谋、战争与兴亡。",
                "main_conflict": "汉室秩序瓦解后，魏、蜀、吴及群雄争夺合法性、人才与天下。",
                "themes": ["分合循环", "忠义与权谋", "英雄与时势", "继承与代价"],
                "characters": project.characters,
                "guardrails": [
                    "续写必须延续当前章回的人物关系和时代语境",
                    "游客生成内容属于独立阅读会话，不覆盖公开原文",
                ],
            },
            source={"marker": PROJECT_MARKER, "catalog_chapters": 120},
            user_id=owner.id,
        )
        outline = create_outline_revision(
            db,
            project_id=project.id,
            chapters=[
                {
                    "chapter_index": 1,
                    "title": str(first["title"]),
                    "summary": str(first["excerpt"]),
                    "conflict": "乱世初起，英雄结义并投身平乱。",
                    "characters": ["刘备", "关羽", "张飞"],
                    "scene": "东汉末年与涿郡桃园",
                    "emotion": "乱世慨叹与豪杰奋起",
                    "visual_keywords": ["东汉末年", "桃园结义", "古典历史"],
                }
            ],
            user_id=owner.id,
            bible_revision_id=bible.id,
        )
        activate_outline_revision(db, project.id, outline.id, approve=True)
        db.flush()
        chapter = create_chapter_revision(
            db,
            project_id=project.id,
            chapter_index=1,
            content=first_text,
            user_id=owner.id,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
        )
        db.flush()
        script = ensure_backfilled_script_revision(db, chapter=chapter, activate=True)

        graph_hash = content_hash(graph)
        binding_manifest = {
            "manifest_version": "imported-vngraph-v1",
            "project_id": project.id,
            "chapter_index": 1,
            "chapter_revision": {
                "id": chapter.id,
                "content_hash": chapter.content_hash,
            },
            "script_revision": {"id": script.id, "script_hash": script.script_hash},
            "asset_bindings": [],
            "voice_line_versions": [],
            "versions": {
                "schema": str(graph.get("Version") or 1),
                "compiler": "three-kingdoms-public-import-v1",
                "tachi_policy": "three-kingdoms-text-v1",
            },
            "import_contract": "VNNodeLibrary.txt",
        }
        binding_manifest_hash = content_hash(binding_manifest)
        graph_revision = VNGraphRevision(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=chapter.id,
            script_revision_id=script.id,
            revision_no=1,
            binding_manifest=binding_manifest,
            binding_manifest_hash=binding_manifest_hash,
            source_manifest_hash=binding_manifest_hash,
            graph_hash=graph_hash,
            graph_json=graph,
            schema_version=str(graph.get("Version") or 1),
            compiler_version="three-kingdoms-public-import-v1",
            tachi_policy_version="three-kingdoms-text-v1",
            status="complete",
        )
        db.add(graph_revision)
        db.flush()
        db.add(
            VNGraphHead(
                project_id=project.id,
                chapter_index=1,
                script_revision_id=script.id,
                current_revision_id=graph_revision.id,
            )
        )
        db.add(
            StoryNode(
                project_id=project.id,
                node_type="public_chapter_anchor",
                content_revision_id=chapter.id,
                payload={
                    "title": str(first["title"]),
                    "source": "三国演义公开 120 回",
                    "summary": str(first["excerpt"]),
                    "continuation_note": "具体起点章回正文固定在阅读会话 initial_state 中。",
                },
            )
        )
        db.flush()

        release, _ = create_release(
            db,
            project_id=project.id,
            user_id=owner.id,
            publish=True,
        )
        db.commit()
        return {
            "created": True,
            "project_id": project.id,
            "release_id": release.id,
            "chapter_count": 120,
            "vngraph_hash": graph_hash,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(seed(), ensure_ascii=False, sort_keys=True))
