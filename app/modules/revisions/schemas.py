from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.channels.schemas import InputModel
from app.modules.panel.schemas import SceneRead
from app.modules.revisions.models import RevisionStatus
from app.shared.generation import Identifier


class RevisionPatch(InputModel):
    narration: str | None = Field(default=None, min_length=1, max_length=4000)
    visual_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    camera_motion: Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "static"] | None = None

    @model_validator(mode="after")
    def supplied(self):
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null field")
        return self


class RevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    video_id: UUID
    number: int
    status: RevisionStatus
    base_render_id: UUID
    render_task_id: UUID | None
    final_asset_id: UUID | None
    scenes: list[SceneRead]
    created_at: datetime


class RecoveryInput(InputModel):
    action: Literal["resume", "use_asset", "abandon"] = "resume"
    note: str = Field(min_length=1, max_length=500)
    provider_job_id: Identifier | None = None
    asset_id: UUID | None = None

    @model_validator(mode="after")
    def evidence_matches_action(self):
        if (self.action == "use_asset") != (self.asset_id is not None):
            raise ValueError("use_asset requires asset_id; resume does not accept it")
        if self.action == "use_asset" and self.provider_job_id is not None:
            raise ValueError("Choose one recovery method")
        if self.action == "abandon" and self.provider_job_id is not None:
            raise ValueError("abandon does not accept a provider job ID")
        return self


class RecoveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    task_id: UUID
    actor_id: UUID
    number: int
    action: str
    note: str
    evidence: dict
    created_at: datetime
