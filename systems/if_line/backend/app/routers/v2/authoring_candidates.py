from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    require_owned_branch_candidate,
    require_owned_candidate_set_revision,
    require_owned_story_path,
)
from app.application.candidate_set_review_service import (
    activate_candidate_set_head,
    get_candidate_set_head_value,
    list_candidate_set_candidates,
    list_candidate_set_revisions,
)
from app.application.revision_head_service import parse_if_match
from app.application.story_branch_service import create_candidate_set_generation_task
from app.application.story_path_promotion_service import promote_candidate_to_story_path
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.routers.v2.authoring_common import accepted_task, set_lock_etag
from app.schemas_authoring import HeadUpdate, RevisionHead, StoryPath
from app.schemas_authoring_artifacts import (
    BranchCandidate,
    CandidateSetGenerationRequest,
    CandidateSetRevision,
)
from app.schemas_v2 import StoryPathPromotionRequest, TaskAccepted


router = APIRouter(tags=["Candidates"])


@router.post(
    "/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-generations",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateCandidateSet",
)
def generate_candidate_set(
    story_path_id: str,
    node_id: str,
    body: CandidateSetGenerationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    task, created, _context = create_candidate_set_generation_task(
        db,
        user_id=user.id,
        story_path_id=story_path_id,
        checkpoint_node_id=node_id,
        chapter_revision_id=body.chapter_revision_id,
        state_snapshot_id=body.state_snapshot_id,
        candidate_count=body.candidate_count,
        instructions=body.instructions,
        idempotency_key=idempotency_key,
    )
    db.commit()
    return accepted_task(task, created=created)


@router.get(
    "/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-revisions",
    response_model=list[CandidateSetRevision],
    operation_id="listCandidateSetRevisions",
)
def read_candidate_set_revisions(
    story_path_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    return list_candidate_set_revisions(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=node_id,
    )


@router.get(
    "/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-head",
    response_model=RevisionHead,
    operation_id="getCandidateSetHead",
)
def get_candidate_set_head(
    story_path_id: str,
    node_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    value = get_candidate_set_head_value(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=node_id,
    )
    db.commit()
    set_lock_etag(response, value.lock_version)
    return value


@router.put(
    "/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-head",
    response_model=RevisionHead,
    operation_id="updateCandidateSetHead",
)
def update_candidate_set_head(
    story_path_id: str,
    node_id: str,
    body: HeadUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    result = activate_candidate_set_head(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=node_id,
        revision_id=body.revision_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    set_lock_etag(response, result.head.lock_version)
    return result.head


@router.get(
    "/candidate-set-revisions/{candidate_set_revision_id}/candidates",
    response_model=list[BranchCandidate],
    operation_id="listBranchCandidates",
)
def read_branch_candidates(
    candidate_set_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    revision = require_owned_candidate_set_revision(
        db,
        candidate_set_revision_id=candidate_set_revision_id,
        user_id=user.id,
    )
    return list_candidate_set_candidates(
        db,
        candidate_set_revision_id=revision.id,
        project_id=revision.project_id,
    )


@router.post(
    "/branch-candidates/{candidate_id}/story-paths",
    response_model=StoryPath,
    status_code=status.HTTP_201_CREATED,
    operation_id="promoteBranchCandidate",
)
def promote_branch_candidate(
    candidate_id: str,
    body: StoryPathPromotionRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_branch_candidate(db, candidate_id=candidate_id, user_id=user.id)
    request = body or StoryPathPromotionRequest()
    result = promote_candidate_to_story_path(
        db,
        user_id=user.id,
        candidate_id=candidate_id,
        idempotency_key=idempotency_key,
        title=request.title,
    )
    db.commit()
    return result.path
