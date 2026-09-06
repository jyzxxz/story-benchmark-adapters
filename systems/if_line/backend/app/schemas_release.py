from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ReleaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    version: int
    status: str
    bible_revision_id: str
    outline_revision_id: str
    manifest_hash: str
    created_at: datetime
    published_at: datetime | None
    withdrawn_at: datetime | None


class ReleaseCreated(ReleaseRead):
    created: bool


class ReleaseManifest(BaseModel):
    release_id: str
    version: int
    manifest_hash: str
    manifest: dict[str, Any]


class PublicProjectRead(BaseModel):
    """A release-backed project card safe for anonymous discovery."""

    id: int
    title: str
    style: str | None
    summary: str
    release_id: str
    release_version: int
    manifest_hash: str
    published_at: datetime | None
    chapter_count: int
    cover_url: str | None
    entry_chapter_index: int | None
