"""Provider-independent contracts; submission never waits for GPU generation."""

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")]


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    request_id: Identifier
    prompt: str = Field(min_length=1, max_length=10000)
    width: int = Field(default=768, ge=64, le=4096)
    height: int = Field(default=1344, ge=64, le=4096)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class VideoGenerationRequest(ImageGenerationRequest):
    duration_seconds: float = Field(gt=0, le=180)


class ProviderJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class GenerationJobRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["runpod", "mock_runpod"]
    endpoint_id: Identifier
    job_id: Identifier


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job: GenerationJobRef
    status: ProviderJobStatus
    # Worker-specific JSON, never fetched as a URL by this adapter.
    output: JsonValue = None
    error: str | None = None

    @model_validator(mode="after")
    def require_completed_output(self) -> "GenerationResult":
        if self.status == ProviderJobStatus.SUCCEEDED and self.output is None:
            raise ValueError("A successful job must contain output")
        if self.status != ProviderJobStatus.SUCCEEDED and self.output is not None:
            raise ValueError("Only a successful job can contain output")
        return self


class GenerationError(Exception):
    """Safe message, without raw provider bodies, prompts or credentials."""


class GenerationUnavailable(GenerationError):
    pass


class GenerationSubmissionUnknown(GenerationError):
    """Submission may have been accepted. Do not blindly resubmit."""


class GenerationInvalidOutput(GenerationError):
    pass


class GenerationNotFound(GenerationError):
    pass


class GenerationRejected(GenerationError):
    pass


class GenerationLifecycle(Protocol):
    def get_status(self, job: GenerationJobRef) -> GenerationResult: ...
    def cancel(self, job: GenerationJobRef) -> None:
        """Request cancellation; query status afterwards to confirm the final state."""
        ...

    def close(self) -> None: ...


class ImageGenerationProvider(GenerationLifecycle, Protocol):
    def generate_image(self, request: ImageGenerationRequest) -> GenerationResult: ...


class VideoGenerationProvider(GenerationLifecycle, Protocol):
    def generate_video(self, request: VideoGenerationRequest) -> GenerationResult: ...


class MediaGenerationProvider(ImageGenerationProvider, VideoGenerationProvider, Protocol):
    pass
