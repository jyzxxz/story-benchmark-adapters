from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    require_owned_chapter_revision,
    require_owned_resource_slot,
    require_owned_script_revision,
)
from app.application.chapter_script_service import (
    activate_chapter_script_head,
    bind_script_resource_slot,
    create_chapter_script_generation_task,
    create_manual_chapter_script_revision,
    get_chapter_script_head,
    list_chapter_script_revisions,
    list_script_resource_slots,
    queue_vn_graph_when_resources_ready,
    request_script_resource_render,
)
from app.application.revision_head_service import parse_if_match
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.routers.v2.authoring_common import accepted_task, set_lock_etag
from app.schemas_authoring import HeadUpdate, RevisionHead
from app.schemas_authoring_artifacts import (
    ResourceRenderRequest,
    ResourceSlot,
    ResourceSlotBind,
    ScriptRevision,
    ScriptRevisionCreated,
    ScriptRevisionCreate,
)
from app.schemas_v2 import GenerationRequest, TaskAccepted


router = APIRouter(tags=["Scripts"])


@router.post(
    "/chapter-revisions/{chapter_revision_id}/script-generations",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateChapterScript",
)
def generate_chapter_script(
    chapter_revision_id: str,
    body: GenerationRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_chapter_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        user_id=user.id,
    )
    request = body or GenerationRequest()
    parameters = deepcopy(request.parameters)
    if request.instructions is not None:
        parameters["instructions"] = request.instructions
    task, created = create_chapter_script_generation_task(
        db,
        user_id=user.id,
        chapter_revision_id=chapter_revision_id,
        idempotency_key=idempotency_key,
        parameters=parameters,
    )
    db.commit()
    return accepted_task(task, created=created)


@router.get(
    "/chapter-revisions/{chapter_revision_id}/script-revisions",
    response_model=list[ScriptRevision],
    operation_id="listScriptRevisions",
)
def read_script_revisions(
    chapter_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_chapter_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        user_id=user.id,
    )
    return list_chapter_script_revisions(
        db,
        chapter_revision_id=chapter_revision_id,
    )


@router.post(
    "/chapter-revisions/{chapter_revision_id}/script-revisions",
    response_model=ScriptRevisionCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="createScriptRevision",
)
def add_script_revision(
    chapter_revision_id: str,
    body: ScriptRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_chapter_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        user_id=user.id,
    )
    revision, appended_names = create_manual_chapter_script_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        script_json=body.script_json,
        parent_revision_id=body.parent_revision_id,
        auto_register_characters=body.auto_register_characters,
        user_id=user.id,
    )
    db.commit()
    return ScriptRevisionCreated(
        id=revision.id,
        chapter_revision_id=revision.chapter_revision_id,
        revision_no=revision.revision_no,
        source_hash=revision.source_hash,
        script_hash=revision.script_hash,
        script_json=revision.script_json,
        appended_character_names=appended_names,
    )


@router.get(
    "/chapter-revisions/{chapter_revision_id}/script-head",
    response_model=RevisionHead,
    operation_id="getScriptHead",
)
def get_script_head(
    chapter_revision_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_chapter_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        user_id=user.id,
    )
    value = get_chapter_script_head(db, chapter_revision_id=chapter_revision_id)
    db.commit()
    set_lock_etag(response, value.lock_version)
    return value


@router.put(
    "/chapter-revisions/{chapter_revision_id}/script-head",
    response_model=RevisionHead,
    operation_id="updateScriptHead",
)
def update_script_head(
    chapter_revision_id: str,
    body: HeadUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_chapter_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        user_id=user.id,
    )
    result = activate_chapter_script_head(
        db,
        chapter_revision_id=chapter_revision_id,
        revision_id=body.revision_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    set_lock_etag(response, result.head.lock_version)
    return result.head


@router.get(
    "/chapter-script-revisions/{script_revision_id}/resource-slots",
    response_model=list[ResourceSlot],
    operation_id="listScriptResourceSlots",
)
def read_resource_slots(
    script_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    revision = require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    return list_script_resource_slots(
        db,
        project_id=revision.project_id,
        script_revision_id=revision.id,
    )


@router.post(
    "/chapter-script-revisions/{script_revision_id}/resource-renders",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="renderScriptResources",
)
def render_script_resources(
    script_revision_id: str,
    body: ResourceRenderRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    revision = require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    result = request_script_resource_render(
        db,
        user_id=user.id,
        project_id=revision.project_id,
        script_revision_id=revision.id,
        role=body.role,
        idempotency_key=idempotency_key,
    )
    db.commit()
    return accepted_task(
        result["parent"],
        created=bool(result.get("parent_created")),
    )


@router.put(
    "/chapter-script-revisions/{script_revision_id}/resource-slots/{slot_id}",
    response_model=ResourceSlot,
    operation_id="bindScriptResourceSlot",
)
def bind_resource_slot(
    script_revision_id: str,
    slot_id: str,
    body: ResourceSlotBind,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    revision = require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    require_owned_resource_slot(
        db,
        script_revision_id=revision.id,
        slot_id=slot_id,
        user_id=user.id,
    )
    slot = bind_script_resource_slot(
        db,
        project_id=revision.project_id,
        slot_id=slot_id,
        asset_version_id=body.asset_version_id,
        script_revision_id=revision.id,
        expected_lock_version=parse_if_match(if_match),
    )
    queue_vn_graph_when_resources_ready(
        db,
        user_id=user.id,
        project_id=revision.project_id,
        script_revision_id=revision.id,
    )
    db.commit()
    set_lock_etag(response, slot.lock_version)
    return slot
