"""Provider-independent contract for typed model generation."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    instructions: str = Field(min_length=1, max_length=20000)
    prompt: str = Field(min_length=1, max_length=100000)
    max_output_tokens: int = Field(default=2000, ge=16, le=16000)


class LLMUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class LLMResult[T: BaseModel](BaseModel):
    data: T
    provider: str
    model: str
    response_id: str
    usage: LLMUsage | None = None


class LLMError(Exception):
    """Safe message; never contains provider response bodies or credentials."""


class LLMUnavailable(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    pass


class LLMRefusal(LLMError):
    pass


class LLMIncomplete(LLMError):
    pass


class LLMProvider(Protocol):
    def generate[T: BaseModel](
        self, request: LLMRequest, response_model: type[T]
    ) -> LLMResult[T]: ...

    def close(self) -> None: ...
