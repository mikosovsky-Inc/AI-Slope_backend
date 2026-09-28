from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.quality.models import QualityStatus


class CheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z_]+$", max_length=80)
    outcome: CheckOutcome
    scene_id: UUID | None = None
    asset_id: UUID | None = None
    repair: str | None = Field(default=None, pattern=r"^(image|video|audio)$")


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    checks: list[CheckResult] = Field(default_factory=list)
    duration_seconds: float | None = None
    target_seconds: float
    visual_provider: str

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.outcome == CheckOutcome.FAILED]


class QualityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_tolerance_seconds: float = Field(ge=0.05, le=10)
    max_scene_retries: int = Field(ge=0, le=5)
    repair_timeout_seconds: int = Field(ge=30, le=86400)


class QualityCheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    video_id: UUID
    render_task_id: UUID
    task_id: UUID
    final_asset_id: UUID | None
    attempt: int
    status: QualityStatus
    policy_json: QualityPolicy
    report_json: QualityReport | dict
    repair_tasks: list[dict]
    rerender_task_id: UUID | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None
