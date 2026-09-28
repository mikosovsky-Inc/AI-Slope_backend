from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)
    speed: float = Field(default=1, ge=0.7, le=1.2)


class TTSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=4000)
    voice_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    language: Literal["pl", "en"]
    settings: VoiceSettings = Field(default_factory=VoiceSettings)


class CharacterAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    characters: list[str] = Field(max_length=20000)
    character_start_times_seconds: list[float] = Field(max_length=20000)
    character_end_times_seconds: list[float] = Field(max_length=20000)

    @model_validator(mode="after")
    def validate_times(self) -> "CharacterAlignment":
        starts, ends = self.character_start_times_seconds, self.character_end_times_seconds
        if not len(self.characters) == len(starts) == len(ends):
            raise ValueError("Alignment arrays must have equal lengths")
        if any(s < 0 or e < s for s, e in zip(starts, ends)):
            raise ValueError("Invalid alignment interval")
        if any(b < a for a, b in zip(starts, starts[1:])):
            raise ValueError("Alignment must be ordered")
        return self


class TTSResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio: bytes = Field(min_length=1, repr=False, exclude=True)
    content_type: Literal["audio/mpeg", "audio/wav"]
    alignment: CharacterAlignment | None = None
    normalized_alignment: CharacterAlignment | None = None
    request_id: str | None = Field(default=None, max_length=200)
    billed_characters: int | None = Field(default=None, ge=0)


class TTSError(Exception):
    """Safe error without provider bodies or credentials."""


class TTSRejected(TTSError):
    pass


class TTSOutcomeUnknown(TTSError):
    """The call may have been billed; never automatically resubmit."""


class TTSProvider(Protocol):
    name: str
    model: str

    def synthesize(self, request: TTSRequest) -> TTSResult: ...
    def close(self) -> None: ...
