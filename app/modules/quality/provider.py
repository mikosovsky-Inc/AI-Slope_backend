from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.quality.schemas import CheckOutcome


class VisualQualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: UUID
    frames: list[Path] = Field(max_length=5)
    visual_prompt: str


class VisualQualityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: CheckOutcome
    # A provider maps its response to a safe code, never returning raw external text.
    code: str = Field(default="visual_quality", pattern=r"^[a-z_]+$", max_length=80)


class VisualQualityUnavailable(Exception):
    """Do not treat provider outages as evidence that a scene needs regeneration."""


class VisualQualityProvider(Protocol):
    name: str
    requires_frames: bool

    def check(self, request: VisualQualityRequest) -> VisualQualityResult: ...
    def close(self) -> None: ...


class DisabledVisualQualityProvider:
    name = "disabled"
    requires_frames = False

    def check(self, request: VisualQualityRequest) -> VisualQualityResult:
        return VisualQualityResult(outcome=CheckOutcome.SKIPPED)

    def close(self) -> None:
        pass


def create_visual_quality_provider() -> VisualQualityProvider:
    """Extension point for vision adapters; no paid external calls in this stage."""
    return DisabledVisualQualityProvider()
