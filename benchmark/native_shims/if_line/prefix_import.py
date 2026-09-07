"""Explicit structural checkpoint initialization; never story-state extraction.

The opening revision is created through the native manual-revision API. This
external route only initializes the missing checkpoint/empty-state structure.
"""
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class PrefixCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_revision_id: str = Field(min_length=36, max_length=36)
    opening_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def install_routes(app, config):
    if config.get("entry_mode", "first_chapter") != "provided_prefix_candidates":
        return
    from app.application.authoring_resource_service import require_owned_path_chapter
    from app.application.branch_service import _snapshot
    from app.application.hashing import content_hash
    from app.auth import get_current_user
    from app.database import get_db
    from app.models_v2 import ChapterRevision, ChoiceDecision, StoryNode, StoryPath, StoryPathChapter

    bundle = Path(config["bundle_dir"])
    opening = (bundle / "opening.txt").read_text(encoding="utf-8")
    opening_hash = hashlib.sha256(opening.encode()).hexdigest()
    shared_hash = hashlib.sha256((bundle / "shared_task.txt").read_bytes()).hexdigest()
    if shared_hash != config["shared_sha256"] or not opening:
        raise ValueError("external prefix bundle differs from frozen run")
    # Native branch context includes at most its last 8,000 characters. Reject a
    # larger prefix instead of adding a second copy in the checkpoint payload.
    from app.application.story_branch_service import CHAPTER_TAIL_CHARS
    if len(opening) > CHAPTER_TAIL_CHARS:
        raise ValueError("provided opening exceeds native candidate chapter tail")

    @app.post("/__benchmark__/path-chapters/{path_chapter_id}/prefix-checkpoints")
    def initialize_prefix_checkpoint(path_chapter_id: str, body: PrefixCheckpointRequest,
                                     idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
                                     db=Depends(get_db), user=Depends(get_current_user)):
        placement = require_owned_path_chapter(db, path_chapter_id=path_chapter_id, user_id=user.id)
        # Serialize structural initialization against this native chapter head.
        placement = db.query(StoryPathChapter).filter_by(id=placement.id).with_for_update().one()
        revision = db.query(ChapterRevision).filter_by(id=body.chapter_revision_id).one_or_none()
        if (not revision or revision.chapter_slot_id != placement.chapter_slot_id
                or placement.current_revision_id != revision.id
                or revision.generation_task_id is not None
                or revision.parent_revision_id is not None
                or revision.content != opening or revision.content_hash != opening_hash
                or body.opening_sha256 != opening_hash):
            raise HTTPException(409, "external prefix must be the exact selected manual input revision")
        path = db.query(StoryPath).filter_by(id=placement.story_path_id).one()
        identity = json.dumps([config["root_run_id"], path_chapter_id, idempotency_key], separators=(",", ":"))
        node_id = str(uuid5(UUID("62c8f032-1ce1-48c5-9d7d-1fb636202a43"), identity))
        payload = {"origin": "external_provided_prefix_import", "root_run_id": config["root_run_id"],
                   "path_chapter_id": path_chapter_id, "chapter_revision_id": revision.id,
                   "opening_sha256": opening_hash, "shared_sha256": shared_hash}
        checkpoint = db.query(StoryNode).filter_by(id=node_id).one_or_none()
        if checkpoint is not None:
            if (checkpoint.project_id != path.project_id or checkpoint.node_type != "checkpoint"
                    or checkpoint.content_revision_id != revision.id or checkpoint.payload != payload):
                raise HTTPException(409, "external prefix initialization idempotency conflict")
        else:
            checkpoint = StoryNode(id=node_id, project_id=path.project_id, node_type="checkpoint",
                                   checkpoint_key="provided-prefix-" + node_id,
                                   content_revision_id=revision.id, payload=payload)
            db.add(checkpoint)
            db.flush()
        state = _snapshot(db, path.project_id, {}, None)
        if state.state_json != {} or state.state_hash != content_hash({}):
            raise HTTPException(409, "external prefix state is not empty")
        result = {"origin": "external_provided_prefix_import", "source_modified": False,
                  "root_run_id": config["root_run_id"], "story_path_id": path.id,
                  "path_chapter_id": placement.id, "chapter_revision_id": revision.id,
                  "generation_task_id": revision.generation_task_id, "opening_sha256": opening_hash,
                  "content": revision.content, "current_revision_id": placement.current_revision_id,
                  "checkpoint": {"id": checkpoint.id, "node_type": checkpoint.node_type,
                                 "content_revision_id": checkpoint.content_revision_id, "payload": checkpoint.payload},
                  "state_snapshot_id": state.id, "state_json": state.state_json, "state_hash": state.state_hash,
                  "story_path_count": db.query(StoryPath).filter_by(project_id=path.project_id).count(),
                  "choice_decision_count": db.query(ChoiceDecision).join(
                      StoryNode, StoryNode.id == ChoiceDecision.checkpoint_node_id).filter(
                      StoryNode.project_id == path.project_id).count()}
        db.commit()
        return result
