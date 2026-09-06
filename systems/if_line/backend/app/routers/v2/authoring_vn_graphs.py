from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    require_owned_script_revision,
    require_owned_vn_graph_revision,
)
from app.application.revision_head_service import parse_if_match
from app.application.vn_graph_service import (
    activate_vn_graph_head,
    create_manual_vn_graph_revision,
    create_vn_graph_compile_task,
    get_vn_graph_head,
    list_script_vn_graph_revisions,
)
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.routers.v2.authoring_common import accepted_task, set_lock_etag
from app.schemas_authoring import HeadUpdate, RevisionHead
from app.schemas_authoring_artifacts import (
    VNGraphCompileRequest,
    VNGraphRevision,
    VNGraphRevisionCreate,
)
from app.schemas_v2 import TaskAccepted
from app.services.vn_graph_compiler import (
    VNGRAPH_COMPILER_VERSION,
    VNGRAPH_SCHEMA_VERSION,
    VNGRAPH_TACHI_POLICY_VERSION,
)


router = APIRouter(tags=["VNGraphs"])


@router.post(
    "/chapter-script-revisions/{script_revision_id}/vn-graph-compilations",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="compileVNGraph",
)
def compile_vn_graph(
    script_revision_id: str,
    body: VNGraphCompileRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    request = body or VNGraphCompileRequest()
    task, created = create_vn_graph_compile_task(
        db,
        user_id=user.id,
        script_revision_id=script_revision_id,
        idempotency_key=idempotency_key,
        schema_version=request.schema_version or VNGRAPH_SCHEMA_VERSION,
        compiler_version=request.compiler_version or VNGRAPH_COMPILER_VERSION,
        tachi_policy_version=VNGRAPH_TACHI_POLICY_VERSION,
    )
    db.commit()
    return accepted_task(task, created=created)


@router.get(
    "/chapter-script-revisions/{script_revision_id}/vn-graph-revisions",
    response_model=list[VNGraphRevision],
    operation_id="listVNGraphRevisions",
)
def read_vn_graph_revisions(
    script_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    return list_script_vn_graph_revisions(
        db,
        script_revision_id=script_revision_id,
    )


@router.post(
    "/chapter-script-revisions/{script_revision_id}/vn-graph-revisions",
    response_model=VNGraphRevision,
    status_code=status.HTTP_201_CREATED,
    operation_id="createVNGraphRevision",
)
def add_vn_graph_revision(
    script_revision_id: str,
    body: VNGraphRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    revision, _created = create_manual_vn_graph_revision(
        db,
        script_revision_id=script_revision_id,
        graph_json=body.graph_json,
        parent_revision_id=body.parent_revision_id,
    )
    db.commit()
    return revision


@router.get(
    "/chapter-script-revisions/{script_revision_id}/vn-graph-head",
    response_model=RevisionHead,
    operation_id="getVNGraphHead",
)
def get_graph_head(
    script_revision_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    value = get_vn_graph_head(db, script_revision_id=script_revision_id)
    db.commit()
    set_lock_etag(response, value.lock_version)
    return value


@router.put(
    "/chapter-script-revisions/{script_revision_id}/vn-graph-head",
    response_model=RevisionHead,
    operation_id="updateVNGraphHead",
)
def update_graph_head(
    script_revision_id: str,
    body: HeadUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_script_revision(
        db,
        script_revision_id=script_revision_id,
        user_id=user.id,
    )
    result = activate_vn_graph_head(
        db,
        script_revision_id=script_revision_id,
        revision_id=body.revision_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    set_lock_etag(response, result.head.lock_version)
    return result.head


@router.get(
    "/vn-graph-revisions/{vn_graph_revision_id}",
    response_model=VNGraphRevision,
    operation_id="getVNGraphRevision",
)
def get_graph_revision(
    vn_graph_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return require_owned_vn_graph_revision(
        db,
        vn_graph_revision_id=vn_graph_revision_id,
        user_id=user.id,
    )
