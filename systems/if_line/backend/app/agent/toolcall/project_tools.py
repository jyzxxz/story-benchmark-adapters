"""项目管理工具。"""
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    create_authoring_project,
    delete_authoring_project,
    update_authoring_project,
)
from app.core.errors import AppError
from app.models import Asset, ChapterContent, ChapterOutline, Project
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)
from app.schemas_authoring import (
    ProjectCreate as AuthoringProjectCreate,
    ProjectPatch as AuthoringProjectPatch,
)


PROJECT_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "列出当前用户自己的小说项目，用于先找到 project_id、项目标题和最近状态。默认按最近更新时间倒序返回。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "可选。按项目标题做模糊搜索。",
                    },
                    "status": {
                        "type": "string",
                        "description": "可选。按项目创作状态过滤，例如 draft_input、outline_approved、completed。",
                    },
                    "visibility": {
                        "type": "string",
                        "description": "可选。按可见性过滤：private 或 public。",
                        "enum": ["private", "public"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少个项目。默认 20。",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "从第几个项目开始返回。默认 0。",
                        "default": 0,
                        "minimum": 0,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_project",
            "description": "查看某个小说项目的轻量摘要。默认只返回少量章节、素材和正文长度；Story Bible 与正文全文需要显式请求。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "要查看的项目 ID。",
                    },
                    "chapter_index": {
                        "type": "integer",
                        "description": "可选。只查看某一章相关的 outline、正文元信息和素材。",
                    },
                    "chapter_limit": {
                        "type": "integer",
                        "description": "最多返回多少章的摘要。默认 2。",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "asset_limit": {
                        "type": "integer",
                        "description": "最多返回多少个素材。默认 3。",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 30,
                    },
                    "include_story_bible": {
                        "type": "boolean",
                        "description": "是否返回 Story Bible 摘要。默认 false；需要世界观、角色设定或风格约束时再打开。",
                        "default": False,
                    },
                    "include_chapter_contents": {
                        "type": "boolean",
                        "description": "是否返回章节正文片段。默认 false，只返回正文长度和状态。",
                        "default": False,
                    },
                    "include_long_text": {
                        "type": "boolean",
                        "description": "是否返回较长正文。默认 false，只返回摘要。",
                        "default": False,
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "创建一个新的私有小说项目，同时建立根故事路线和空的创作版本入口。除 title 外，其余创作输入均可稍后补充。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "项目标题，不能为空。",
                    },
                    "characters": {
                        "type": "array",
                        "description": "可选。核心人物列表，每项是包含 name 等人物信息的对象。",
                        "items": {"type": "object"},
                    },
                    "story_start": {
                        "type": "string",
                        "description": "可选。故事开场或初始局面。",
                    },
                    "story_end": {
                        "type": "string",
                        "description": "可选。预期结局或终局方向。",
                    },
                    "style": {
                        "type": "string",
                        "description": "可选。叙事风格。",
                    },
                    "source_work": {
                        "type": "string",
                        "description": "可选。二创所依据的原作；原创项目不填写。",
                    },
                    "pace": {
                        "type": "string",
                        "enum": ["fast", "medium", "slow"],
                        "description": "可选。故事节奏：fast 紧凑、medium 均衡、slow 舒缓。",
                    },
                    "extra_requirements": {
                        "type": "string",
                        "description": "可选。其他长期创作要求。",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_project",
            "description": "修改当前用户项目的基础创作输入。只传需要修改的字段，未传字段保持不变；不会修改发布状态和创作版本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "要修改的项目 ID。",
                    },
                    "title": {"type": "string", "description": "可选。新的项目标题。"},
                    "characters": {
                        "type": "array",
                        "description": "可选。替换后的完整核心人物列表。",
                        "items": {"type": "object"},
                    },
                    "story_start": {"type": "string", "description": "可选。新的故事开场。"},
                    "story_end": {"type": "string", "description": "可选。新的预期结局。"},
                    "style": {"type": "string", "description": "可选。新的叙事风格。"},
                    "source_work": {
                        "type": ["string", "null"],
                        "description": "可选。新的二创原作；传 null 表示改为原创。",
                    },
                    "pace": {
                        "type": ["string", "null"],
                        "enum": ["fast", "medium", "slow", None],
                        "description": "可选。新的故事节奏；传 null 表示清空。fast 紧凑、medium 均衡、slow 舒缓。",
                    },
                    "extra_requirements": {
                        "type": ["string", "null"],
                        "description": "可选。新的其他创作要求。",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_project",
            "description": "从当前用户的项目列表中删除项目。项目数据会保留，但当前不提供恢复入口。只有用户明确指定项目并要求删除时才能调用；存在有效发布版本的项目必须先撤回。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "要删除的项目 ID。",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
]


def execute_project_tool(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    agent: Any = None,
) -> Optional[Dict[str, Any]]:
    owner_id = getattr(agent, "owner_id", None)
    if name == "list_projects":
        if owner_id is None:
            return {"ok": False, "error": "缺少当前用户上下文，不能列出项目"}
        return list_projects(
            db=db,
            owner_id=owner_id,
            keyword=arguments.get("keyword"),
            status=arguments.get("status"),
            visibility=arguments.get("visibility"),
            limit=_parse_int(arguments.get("limit"), default=20, min_value=1, max_value=50),
            offset=_parse_int(arguments.get("offset"), default=0, min_value=0, max_value=5000),
        )

    if name == "view_project":
        try:
            project_id = int(arguments.get("project_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "project_id 必须是整数"}
        return view_project(
            db=db,
            project_id=project_id,
            owner_id=owner_id,
            chapter_index=_parse_optional_int(arguments.get("chapter_index")),
            chapter_limit=_parse_int(arguments.get("chapter_limit"), default=2, min_value=1, max_value=10),
            asset_limit=_parse_int(arguments.get("asset_limit"), default=3, min_value=1, max_value=30),
            include_story_bible=_parse_bool(arguments.get("include_story_bible", False)),
            include_chapter_contents=_parse_bool(arguments.get("include_chapter_contents", False)),
            include_long_text=_parse_bool(arguments.get("include_long_text", False)),
        )

    if name not in {"create_project", "update_project", "delete_project"}:
        return None
    if owner_id is None:
        return {"ok": False, "error": "缺少当前用户上下文，不能修改项目"}

    if name == "create_project":
        return create_project(db, owner_id=owner_id, arguments=arguments)

    try:
        project_id = int(arguments.get("project_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "project_id 必须是整数"}
    if name == "update_project":
        return update_project(db, owner_id=owner_id, project_id=project_id, arguments=arguments)
    return delete_project(db, owner_id=owner_id, project_id=project_id)


def create_project(
    db: Session,
    *,
    owner_id: int,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        body = AuthoringProjectCreate.model_validate(arguments)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": "创建项目参数无效",
            "invalid_fields": [".".join(str(part) for part in item["loc"]) for item in exc.errors()],
        }
    try:
        project, root = create_authoring_project(
            db,
            user_id=owner_id,
            values=body.model_dump(),
        )
    except AppError as exc:
        return _app_error_result(exc)
    return {
        "ok": True,
        "operation": "created",
        "project": _project_list_item_to_dict(project),
        "root_story_path_id": root.id,
        "committed": True,
    }


def update_project(
    db: Session,
    *,
    owner_id: int,
    project_id: int,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    changes = {key: value for key, value in arguments.items() if key != "project_id"}
    if not changes:
        return {"ok": False, "error": "至少提供一个要修改的项目字段"}
    try:
        body = AuthoringProjectPatch.model_validate(changes)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": "修改项目参数无效",
            "invalid_fields": [".".join(str(part) for part in item["loc"]) for item in exc.errors()],
        }
    try:
        project = update_authoring_project(
            db,
            project_id=project_id,
            user_id=owner_id,
            changes=body.model_dump(exclude_unset=True),
        )
    except AppError as exc:
        return _app_error_result(exc)
    return {
        "ok": True,
        "operation": "updated",
        "updated_fields": sorted(changes),
        "project": _project_list_item_to_dict(project),
        "committed": True,
    }


def delete_project(
    db: Session,
    *,
    owner_id: int,
    project_id: int,
) -> Dict[str, Any]:
    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.owner_id == owner_id,
        )
        .one_or_none()
    )
    if project is None:
        return {"ok": False, "error": "项目不存在", "project_id": project_id}
    title = project.title
    try:
        deleted_now = delete_authoring_project(
            db,
            project_id=project_id,
            user_id=owner_id,
        )
    except AppError as exc:
        return _app_error_result(exc)
    return {
        "ok": True,
        "operation": "deleted",
        "soft_deleted": True,
        "already_deleted": not deleted_now,
        "project_id": project_id,
        "title": title,
        "committed": True,
    }


def _app_error_result(error: AppError) -> Dict[str, Any]:
    messages = {
        "project.not_found": "项目不存在",
        "project.published": "项目存在有效发布版本，请先撤回后再删除",
        "project.patch_invalid": "项目包含不可修改的字段",
    }
    return {
        "ok": False,
        "error": messages.get(error.code, "项目操作失败"),
        "error_code": error.code,
        "status_code": error.status_code,
        "details": error.details,
    }


def list_projects(
    db: Session,
    owner_id: int,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    visibility: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    query = db.query(Project).filter(
        Project.owner_id == owner_id,
        Project.deleted_at.is_(None),
    )

    keyword_text = str(keyword or "").strip()
    if keyword_text:
        query = query.filter(Project.title.ilike(f"%{keyword_text}%"))

    status_text = str(status or "").strip()
    if status_text:
        query = query.filter(Project.status == status_text)

    visibility_text = str(visibility or "").strip()
    if visibility_text:
        if visibility_text not in {"private", "public"}:
            return {"ok": False, "error": "visibility 必须是 private 或 public"}
        query = query.filter(Project.visibility == visibility_text)

    total = query.count()
    projects = (
        query.order_by(Project.updated_at.desc(), Project.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "query": {
            "keyword": keyword_text or None,
            "status": status_text or None,
            "visibility": visibility_text or None,
            "limit": limit,
            "offset": offset,
        },
        "projects": [_project_list_item_to_dict(project) for project in projects],
        "returned_count": len(projects),
        "total": total,
        "has_more": offset + len(projects) < total,
    }


def view_project(
    db: Session,
    project_id: int,
    owner_id: Optional[int] = None,
    chapter_index: Optional[int] = None,
    chapter_limit: int = 2,
    asset_limit: int = 3,
    include_story_bible: bool = False,
    include_chapter_contents: bool = False,
    include_long_text: bool = False,
) -> Dict[str, Any]:
    query = db.query(Project).filter(
        Project.id == project_id,
        Project.deleted_at.is_(None),
    )
    if owner_id is not None:
        query = query.filter(Project.owner_id == owner_id)
    project = query.first()
    if not project:
        return {"ok": False, "error": "项目不存在", "project_id": project_id}

    head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .first()
    )
    bible = _current_bible_revision(db, project_id, head)
    outline = _current_outline_revision(db, project_id, head)
    outlines = _current_outline_chapters(db, outline, project_id)
    contents = _current_chapter_revisions(db, project_id, chapter_index)
    assets = (
        db.query(Asset)
        .filter(Asset.project_id == project_id)
        .order_by(Asset.id.asc())
        .all()
    )
    selected_outlines = _filter_by_chapter(outlines, chapter_index)[:chapter_limit]
    selected_contents = _filter_by_chapter(contents, chapter_index)[:chapter_limit]
    selected_assets = _filter_by_chapter(assets, chapter_index)[:asset_limit]

    text_limit = 4000 if include_long_text else 300
    return {
        "ok": True,
        "query": {
            "project_id": project_id,
            "chapter_index": chapter_index,
            "chapter_limit": chapter_limit,
            "asset_limit": asset_limit,
            "include_story_bible": include_story_bible,
            "include_chapter_contents": include_chapter_contents,
            "include_long_text": include_long_text,
        },
        "project": {
            "id": project.id,
            "title": project.title,
            "characters": _clip_json(project.characters or [], text_limit),
            "story_start": _clip(project.story_start, text_limit),
            "story_end": _clip(project.story_end, text_limit),
            "style": project.style,
            "pace": project.pace,
            "extra_requirements": _clip(project.extra_requirements, text_limit),
            "status": project.status,
            "created_at": _datetime_to_text(project.created_at),
            "updated_at": _datetime_to_text(project.updated_at),
        },
        "story_bible": _story_bible_revision_to_dict(bible, text_limit) if bible and include_story_bible else None,
        "content_head": {
            "current_bible_revision_id": head.current_bible_revision_id if head else None,
            "current_outline_revision_id": head.current_outline_revision_id if head else None,
            "lock_version": head.lock_version if head else None,
        },
        "chapter_outlines": [
            {
                "chapter_index": item.chapter_index,
                "display_index": item.display_index,
                "title": item.title,
                "summary": _clip(item.summary, text_limit),
                "conflict": _clip(item.conflict, text_limit),
                "characters": _clip_json(item.characters or [], text_limit),
                "scene": item.scene,
                "emotion": item.emotion,
                "visual_keywords": _clip_json(item.visual_keywords or [], text_limit),
                "outline_revision_id": item.outline_revision_id,
                "story_path_chapter_id": item.story_path_chapter_id,
            }
            for item in selected_outlines
        ],
        "chapter_contents": [
            _chapter_content_to_dict(
                item,
                text_limit=text_limit,
                include_chapter_contents=include_chapter_contents,
            )
            for item in selected_contents
        ],
        "assets": [
            {
                "id": item.id,
                "chapter_index": item.chapter_index,
                "asset_type": item.asset_type,
                "target_name": item.target_name,
                "status": item.status,
                "image_url": item.image_url,
                "emotion": item.emotion,
                "mood": item.mood,
                "genre": item.genre,
            }
            for item in selected_assets
        ],
        "counts": {
            "chapter_outline_count": len(outlines),
            "chapter_content_count": len(contents),
            "asset_count": len(assets),
        },
        "returned_counts": {
            "chapter_outline_count": len(selected_outlines),
            "chapter_content_count": len(selected_contents),
            "asset_count": len(selected_assets),
        },
    }


def _current_bible_revision(
    db: Session,
    project_id: int,
    head: Optional[ProjectContentHead],
) -> Optional[StoryBibleRevision]:
    if not head or not head.current_bible_revision_id:
        return None
    return (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == head.current_bible_revision_id,
            StoryBibleRevision.project_id == project_id,
        )
        .first()
    )


def _current_outline_revision(
    db: Session,
    project_id: int,
    head: Optional[ProjectContentHead],
) -> Optional[OutlineRevision]:
    revision_id = head.current_outline_revision_id if head else None
    if not revision_id:
        # 双 head 体系回落：自动生成链（桥接/finalize/AutoCreator）只激活
        # 根 StoryPath 的大纲 head，项目级 head 常年为空（pitfalls 坑 14）。
        revision_id = _root_path_outline_revision_id(db, project_id)
    if not revision_id:
        return None
    return (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == revision_id,
            OutlineRevision.project_id == project_id,
        )
        .first()
    )


def _root_path_outline_revision_id(db: Session, project_id: int) -> Optional[str]:
    path = (
        db.query(StoryPath)
        .filter(
            StoryPath.project_id == project_id,
            StoryPath.parent_path_id.is_(None),
        )
        .order_by(StoryPath.created_at, StoryPath.id)
        .first()
    )
    if not path:
        return None
    path_head = (
        db.query(StoryPathOutlineHead)
        .filter(StoryPathOutlineHead.story_path_id == path.id)
        .first()
    )
    return path_head.current_revision_id if path_head else None


def _current_outline_chapters(
    db: Session,
    outline: Optional[OutlineRevision],
    project_id: int,
) -> List[Any]:
    if outline:
        return (
            db.query(OutlineChapter)
            .filter(OutlineChapter.outline_revision_id == outline.id)
            .order_by(OutlineChapter.display_index.asc(), OutlineChapter.chapter_index.asc())
            .all()
        )
    # 两套 v2 head 都为空时（fresh 项目：只跑过 plan/workflow、尚未桥接），
    # 回落 legacy chapter_outlines，避免 Agent 看到 0 条大纲（pitfalls 坑 14）。
    rows = (
        db.query(ChapterOutline)
        .filter(ChapterOutline.project_id == project_id)
        .order_by(ChapterOutline.chapter_index.asc())
        .all()
    )
    return [
        SimpleNamespace(
            chapter_index=row.chapter_index,
            display_index=row.chapter_index,
            title=row.title,
            summary=row.summary,
            conflict=row.conflict,
            characters=row.characters,
            scene=row.scene,
            emotion=row.emotion,
            visual_keywords=row.visual_keywords,
            outline_revision_id=None,
            story_path_chapter_id=None,
        )
        for row in rows
    ]


def _current_chapter_revisions(
    db: Session,
    project_id: int,
    chapter_index: Optional[int],
) -> List[Any]:
    query = db.query(ChapterHead).filter(ChapterHead.project_id == project_id)
    if chapter_index is not None:
        query = query.filter(ChapterHead.chapter_index == chapter_index)
    heads = query.order_by(ChapterHead.chapter_index.asc()).all()
    revision_ids = [item.current_revision_id for item in heads if item.current_revision_id]
    ordered_by = "chapter_heads"
    if not revision_ids:
        # 双 head 体系回落：自动生成链把章节修订激活在 story_path_chapters 上，
        # 项目级 chapter_heads 恒为空（pitfalls 坑 14）。
        path = (
            db.query(StoryPath)
            .filter(
                StoryPath.project_id == project_id,
                StoryPath.parent_path_id.is_(None),
            )
            .order_by(StoryPath.created_at, StoryPath.id)
            .first()
        )
        if path:
            placement_query = db.query(StoryPathChapter).filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.current_revision_id.isnot(None),
            )
            if chapter_index is not None:
                placement_query = placement_query.filter(
                    StoryPathChapter.display_index == chapter_index
                )
            placements = placement_query.order_by(
                StoryPathChapter.display_index.asc(),
                StoryPathChapter.created_at.asc(),
            ).all()
            # 历史重激活会产生同 display_index 的旧挂载点；按章去重保留最新。
            latest_by_index: Dict[int, str] = {}
            for item in placements:
                latest_by_index[item.display_index] = item.current_revision_id
            revision_ids = [
                latest_by_index[index] for index in sorted(latest_by_index)
            ]
            ordered_by = "path_placements"
    if not revision_ids:
        # 仍未命中：fresh 项目回落 legacy chapter_contents。
        content_query = db.query(ChapterContent).filter(
            ChapterContent.project_id == project_id
        )
        if chapter_index is not None:
            content_query = content_query.filter(ChapterContent.chapter_index == chapter_index)
        rows = content_query.order_by(ChapterContent.chapter_index.asc()).all()
        return [
            SimpleNamespace(
                id=str(row.id),
                chapter_index=row.chapter_index,
                revision_no=row.version or 1,
                status=row.status,
                content=row.content,
                bible_revision_id=None,
                outline_revision_id=None,
            )
            for row in rows
        ]
    revisions = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id.in_(revision_ids))
        .all()
    )
    by_id = {item.id: item for item in revisions}
    return [by_id[item] for item in revision_ids if item in by_id]


def _project_list_item_to_dict(project: Project) -> Dict[str, Any]:
    characters = project.characters if isinstance(project.characters, list) else []
    return {
        "id": project.id,
        "title": project.title,
        "status": project.status,
        "visibility": project.visibility,
        "style": project.style,
        "pace": project.pace,
        "source_work": project.source_work,
        "character_count": len(characters),
        "characters_preview": _clip_json(characters, text_limit=120, list_limit=3),
        "story_start_preview": _clip(project.story_start, 120),
        "story_end_preview": _clip(project.story_end, 120),
        "created_at": _datetime_to_text(project.created_at),
        "updated_at": _datetime_to_text(project.updated_at),
    }


def _story_bible_revision_to_dict(bible: StoryBibleRevision, text_limit: int) -> Dict[str, Any]:
    content = bible.content_json or {}
    return {
        "revision_id": bible.id,
        "revision_no": bible.revision_no,
        "status": bible.status,
        "content_hash": bible.content_hash,
        "worldview": _clip(content.get("worldview"), text_limit),
        "characters": _clip_json(content.get("characters") or [], text_limit),
        "character_relations": _clip(content.get("character_relations"), text_limit),
        "main_conflict": _clip(content.get("main_conflict"), text_limit),
        "emotional_line": _clip(content.get("emotional_line"), text_limit),
        "style_rules": _clip(content.get("style_rules"), text_limit),
        "ending_constraints": _clip(content.get("ending_constraints"), text_limit),
        "forbidden_points": _clip_json(content.get("forbidden_points") or [], text_limit),
        "writing_notes": _clip_json(content.get("writing_notes") or [], text_limit),
    }


def _chapter_content_to_dict(
    item: ChapterRevision,
    text_limit: int,
    include_chapter_contents: bool,
) -> Dict[str, Any]:
    result = {
        "revision_id": item.id,
        "chapter_index": item.chapter_index,
        "revision_no": item.revision_no,
        "status": item.status,
        "content_length": len(item.content or ""),
        "bible_revision_id": item.bible_revision_id,
        "outline_revision_id": item.outline_revision_id,
    }
    if include_chapter_contents:
        result["content"] = _clip(item.content, text_limit)
    return result


def _filter_by_chapter(items: List[Any], chapter_index: Optional[int]) -> List[Any]:
    if chapter_index is None:
        return items
    return [item for item in items if item.chapter_index == chapter_index]


def _parse_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(min_value, min(max_value, result))


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[已截断]"


def _clip_json(value: Any, text_limit: int, list_limit: int = 5) -> Any:
    if isinstance(value, str):
        return _clip(value, text_limit)
    if isinstance(value, list):
        return [_clip_json(item, text_limit, list_limit) for item in value[:list_limit]]
    if isinstance(value, dict):
        return {
            key: _clip_json(item, text_limit, list_limit)
            for key, item in value.items()
        }
    return value


def _datetime_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None
