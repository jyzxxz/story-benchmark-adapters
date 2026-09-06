from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.application.branch_service import get_owned_session
from app.application.visual_asset_service import (
    asset_action_dict,
    cancel_asset_action,
    confirm_asset_action,
    confirm_reading_continuation,
    continuation_dict,
    create_asset_action,
    create_reading_continuation,
    get_library_facets,
    get_library_asset,
    get_owned_asset_action,
    get_owned_continuation,
    library_asset_dict,
    list_public_continuations,
    parse_library_tag_ids,
    prepare_direction_suggestion_request,
    search_library_assets,
    select_public_continuation,
    upload_visual_asset,
)
from app.auth import get_current_user
from app.database import get_db
from app.integrations.llm.continuation_adapter import (
    DIRECTION_SUGGESTION_PROMPT_VERSION,
    LegacyDirectionSuggestionLLMAdapter,
)
from app.models import User
from app.project_permissions import require_project_owner
from app.schemas_visual_asset import (
    AssetActionConfirm,
    AssetActionCreate,
    AssetActionRead,
    ContinuationConfirmResult,
    LibraryAssetPage,
    LibraryAssetRead,
    LibraryFacetResponse,
    PublicContinuationTreeRead,
    ReadingContinuationRead,
    ReadingDirectionCreate,
    ReadingDirectionSuggestionRead,
    ReadingImageCreate,
    VisualAssetUploadRead,
    VisualAssetType,
)
from app.schemas_branch import ReadingSessionRead
from app.schemas_continuation_generation import GeneratedDirectionSuggestion


router = APIRouter(tags=["visual-assets"])
logger = logging.getLogger(__name__)

def _settings(request: Request):
    return request.app.state.settings


def _json_object(value: str | None, field_name: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON object")
    return parsed


def _if_match(value: str | None) -> int:
    if value is None or not value.strip():
        raise HTTPException(status_code=428, detail="必须提供 If-Match 会话版本")
    try:
        result = int(value.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="If-Match 必须是会话 lock_version") from exc
    if result < 1:
        raise HTTPException(status_code=400, detail="If-Match 必须大于 0")
    return result


# POST /api/projects/{project_id}/assets/uploads
@router.post(
    "/projects/{project_id}/assets/uploads",
    response_model=VisualAssetUploadRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_visual(
    project_id: int,
    request: Request,
    file: UploadFile = File(...),
    asset_type: str = Form(...),
    target_name: str = Form(...),
    description: str = Form(default=""),
    ownership_scope: str = Form(default="session"),
    reading_session_id: str | None = Form(default=None),
    target_json: str | None = Form(default=None),
    rights_attested: bool = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = _settings(request)
    if reading_session_id:
        reading = get_owned_session(db, reading_session_id, user.id)
        if reading.project_id != project_id:
            raise HTTPException(status_code=404, detail="阅读会话不属于该项目")
    else:
        require_project_owner(db, project_id, user)
    raw = await file.read(settings.visual_asset_upload_max_bytes + 1)
    if len(raw) > settings.visual_asset_upload_max_bytes:
        raise HTTPException(status_code=413, detail="图片不能超过 25 MB")
    result = upload_visual_asset(
        db,
        user_id=user.id,
        project_id=project_id,
        raw=raw,
        filename=file.filename or "upload",
        declared_content_type=file.content_type or "application/octet-stream",
        asset_type=asset_type,
        target_name=target_name,
        description=description,
        ownership_scope=ownership_scope,
        reading_session_id=reading_session_id,
        target=_json_object(target_json, "target_json"),
        rights_attested=rights_attested,
        idempotency_key=idempotency_key or "",
    )
    db.commit()
    return result


# GET /api/library-assets
@router.get("/library-assets", response_model=LibraryAssetPage)
def list_library(
    request: Request,
    query: str | None = Query(default=None, max_length=2000),
    asset_type: str | None = None,
    tag_ids: str | None = Query(default=None, max_length=2000),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    settings = _settings(request)
    items, total = search_library_assets(
        db,
        catalog_version=settings.visual_asset_catalog_version,
        matcher_version=settings.visual_asset_matcher_version,
        query=query,
        asset_type=asset_type,
        identity_group=None,
        limit=limit,
        offset=offset,
        required_tag_ids=parse_library_tag_ids(tag_ids),
    )
    return LibraryAssetPage(
        items=[LibraryAssetRead(**item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
        catalog_version=settings.visual_asset_catalog_version,
        matcher_version=settings.visual_asset_matcher_version,
    )


# GET /api/library-assets/facets
@router.get("/library-assets/facets", response_model=LibraryFacetResponse)
def list_library_facets(
    request: Request,
    asset_type: VisualAssetType = Query(...),
    tag_ids: str | None = Query(default=None, max_length=2000),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    settings = _settings(request)
    return LibraryFacetResponse(
        **get_library_facets(
            db,
            catalog_version=settings.visual_asset_catalog_version,
            asset_type=asset_type,
            selected_tag_ids=parse_library_tag_ids(tag_ids),
        )
    )


# GET /api/library-assets/{library_asset_id}
@router.get("/library-assets/{library_asset_id}", response_model=LibraryAssetRead)
def read_library_asset(
    library_asset_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    settings = _settings(request)
    asset, storage = get_library_asset(
        db, library_asset_id, catalog_version=settings.visual_asset_catalog_version
    )
    return library_asset_dict(asset, storage)


# POST /api/projects/{project_id}/asset-actions
@router.post(
    "/projects/{project_id}/asset-actions",
    response_model=AssetActionRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_asset_action(
    project_id: int,
    body: AssetActionCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = _settings(request)
    require_project_owner(db, project_id, user)
    if body.mode == "agent_compose":
        raise HTTPException(status_code=501, detail="Agent 编排暂未实现")
    action = create_asset_action(
        db,
        user_id=user.id,
        project_id=project_id,
        mode=body.mode,
        target=body.target.model_dump(mode="json"),
        input_data={**body.input, "generate_on_low_confidence": False},
        idempotency_key=idempotency_key or "",
        catalog_version=settings.visual_asset_catalog_version,
        matcher_version=settings.visual_asset_matcher_version,
        agent_enabled=settings.agent_compose_enabled,
    )
    db.commit()
    return asset_action_dict(db, action)


# GET /api/projects/{project_id}/asset-actions/{action_id}
@router.get("/projects/{project_id}/asset-actions/{action_id}", response_model=AssetActionRead)
def read_asset_action(
    project_id: int,
    action_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    action = get_owned_asset_action(db, action_id, user.id, project_id)
    result = asset_action_dict(db, action)
    db.commit()
    return result


# POST /api/projects/{project_id}/asset-actions/{action_id}/confirm
@router.post(
    "/projects/{project_id}/asset-actions/{action_id}/confirm",
    response_model=AssetActionRead,
)
def confirm_action(
    project_id: int,
    action_id: str,
    body: AssetActionConfirm,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    action = get_owned_asset_action(db, action_id, user.id, project_id)
    confirm_asset_action(
        db,
        action=action,
        graph_json=body.graph_json,
        result_graph_hash=body.result_graph_hash,
    )
    db.commit()
    return asset_action_dict(db, action)


# POST /api/projects/{project_id}/asset-actions/{action_id}/cancel
@router.post(
    "/projects/{project_id}/asset-actions/{action_id}/cancel",
    response_model=AssetActionRead,
)
def cancel_action(
    project_id: int,
    action_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    action = get_owned_asset_action(db, action_id, user.id, project_id)
    cancel_asset_action(db, action)
    db.commit()
    return asset_action_dict(db, action)


# GET /api/public/projects/{project_id}/releases/{release_id}/continuations
@router.get(
    "/public/projects/{project_id}/releases/{release_id}/continuations",
    response_model=PublicContinuationTreeRead,
)
def public_continuation_tree(
    project_id: int,
    release_id: str,
    chapter_number: int = Query(ge=1, le=10000),
    db: Session = Depends(get_db),
):
    return list_public_continuations(
        db,
        project_id=project_id,
        release_id=release_id,
        chapter_number=chapter_number,
    )


# POST /api/reading-sessions/{session_id}/direction-suggestion
@router.post(
    "/reading-sessions/{session_id}/direction-suggestion",
    response_model=ReadingDirectionSuggestionRead,
)
async def suggest_direction(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    suggestion_request = prepare_direction_suggestion_request(
        db,
        session_id=session_id,
        user_id=user.id,
    )
    try:
        provider_result = await LegacyDirectionSuggestionLLMAdapter().suggest_direction(
            suggestion_request
        )
        if provider_result.prompt_version != DIRECTION_SUGGESTION_PROMPT_VERSION:
            raise ValueError("prompt version mismatch")
        generated = GeneratedDirectionSuggestion.model_validate(provider_result.data)
    except (ValidationError, ValueError, TypeError):
        logger.exception("Reading direction suggestion output rejected session_id=%s", session_id)
        raise HTTPException(status_code=502, detail="AI 返回的续写方向格式无效") from None
    except Exception:
        logger.exception("Reading direction suggestion provider failed session_id=%s", session_id)
        raise HTTPException(status_code=503, detail="AI 续写方向建议暂时不可用") from None
    return ReadingDirectionSuggestionRead(
        direction=generated.direction,
        prompt_version=provider_result.prompt_version,
    )


# POST /api/reading-sessions/{session_id}/directions
@router.post(
    "/reading-sessions/{session_id}/directions",
    response_model=ReadingContinuationRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_direction(
    session_id: str,
    body: ReadingDirectionCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = _settings(request)
    continuation = create_reading_continuation(
        db,
        session_id=session_id,
        user_id=user.id,
        direction=body.direction,
        visual_mode=body.visual_mode,
        uploaded_asset_version_ids=body.uploaded_asset_version_ids,
        max_generated_assets=body.max_generated_assets,
        parent_continuation_id=body.parent_continuation_id,
        idempotency_key=idempotency_key or "",
        catalog_version=settings.visual_asset_catalog_version,
        matcher_version=settings.visual_asset_matcher_version,
    )
    db.commit()
    return continuation_dict(db, continuation)


# POST /api/reading-sessions/{session_id}/continuations/{continuation_id}/images
@router.post(
    "/reading-sessions/{session_id}/continuations/{continuation_id}/images",
    response_model=AssetActionRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_continuation_image(
    session_id: str,
    continuation_id: str,
    body: ReadingImageCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a real AI image inside an owned public-reading session."""

    settings = _settings(request)
    reading = get_owned_session(db, session_id, user.id)
    continuation = get_owned_continuation(db, continuation_id, session_id, user.id)
    if continuation.status not in {"preview_ready", "confirmed"}:
        raise HTTPException(status_code=409, detail="请先等待续写正文生成完成")
    action = create_asset_action(
        db,
        user_id=user.id,
        project_id=reading.project_id,
        mode="direct_generate",
        target={
            "source_kind": "reading_continuation",
            "source_id": continuation.id,
            "node_key": "continuation-image",
            "asset_slot": "BackgroundImage",
            "role": "background",
        },
        input_data={
            "prompt": body.prompt,
            "description": body.prompt,
            "target_name": "游客续写场景图",
            "asset_type": "background",
            "width": body.width,
            "height": body.height,
            "style_pack_version": "default-v1",
            "identity_version": None,
            "prompt_template_version": "visual-action-v1",
            "validator_version": "visual-qc-v1",
            "extra_parameters": {},
        },
        idempotency_key=idempotency_key or "",
        catalog_version=settings.visual_asset_catalog_version,
        matcher_version=settings.visual_asset_matcher_version,
        agent_enabled=settings.agent_compose_enabled,
        reading_session_id=reading.id,
    )
    continuation.asset_action_id = action.id
    db.commit()
    return asset_action_dict(db, action)


# GET /api/reading-sessions/{session_id}/continuations/{continuation_id}
@router.get(
    "/reading-sessions/{session_id}/continuations/{continuation_id}",
    response_model=ReadingContinuationRead,
)
def read_continuation(
    session_id: str,
    continuation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    continuation = get_owned_continuation(db, continuation_id, session_id, user.id)
    return continuation_dict(db, continuation)


# POST /api/reading-sessions/{session_id}/continuations/{continuation_id}/select
@router.post(
    "/reading-sessions/{session_id}/continuations/{continuation_id}/select",
    response_model=ReadingSessionRead,
)
def select_continuation(
    session_id: str,
    continuation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reading = select_public_continuation(
        db,
        session_id=session_id,
        user_id=user.id,
        continuation_id=continuation_id,
    )
    db.commit()
    db.refresh(reading)
    return reading


# POST /api/reading-sessions/{session_id}/continuations/{continuation_id}/confirm
@router.post(
    "/reading-sessions/{session_id}/continuations/{continuation_id}/confirm",
    response_model=ContinuationConfirmResult,
)
def confirm_continuation(
    session_id: str,
    continuation_id: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    continuation = get_owned_continuation(db, continuation_id, session_id, user.id)
    reading = confirm_reading_continuation(
        db,
        continuation=continuation,
        user_id=user.id,
        expected_lock_version=_if_match(if_match),
    )
    db.commit()
    return ContinuationConfirmResult(
        continuation=ReadingContinuationRead(**continuation_dict(db, continuation)),
        session_lock_version=reading.lock_version,
    )
