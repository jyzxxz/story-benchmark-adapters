from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VoiceGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_audio: bool = True


class VoiceTaskAccepted(BaseModel):
    task_id: str
    status: str
    created: bool
    events_url: str


class VoiceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_revision_id: str
    occurrence_id: str
    order_index: int
    kind: str
    text: str
    speaker_character_id: str | None
    speaker_name: str | None
    emotion: str | None
    voice_profile_id: str | None
    voice_profile_version: int | None
    audio_asset_version_id: str | None
    status: str
    created_at: datetime


class VoiceManifest(BaseModel):
    chapter_revision_id: str
    count: int
    items: list[VoiceLineRead]
