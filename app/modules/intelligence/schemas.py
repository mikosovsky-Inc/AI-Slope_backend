"""Strict AI response schema; all generated fields are required."""

from typing import Annotated, Literal

from pydantic import Field

from app.modules.channels.schemas import (
    BlueprintConfiguration,
    BlueprintInput,
    FormatMix,
    InputModel,
    PillarInput,
    PostingStrategy,
    VideoStyle,
    VisualStyle,
)

Text = Annotated[str, Field(min_length=1, max_length=2000)]


class GeneratedFormats(FormatMix):
    top5: float = Field(ge=0, le=1)
    story: float = Field(ge=0, le=1)


class GeneratedVideo(VideoStyle):
    duration_target: int = Field(ge=10, le=180)
    pace: Literal["slow", "medium", "fast"]
    hook_max_seconds: float = Field(gt=0, le=5)


class GeneratedVisual(VisualStyle):
    description: Text
    video_scene_ratio: float = Field(ge=0, le=1)


class GeneratedPillar(PillarInput):
    description: Text


class GeneratedConfiguration(BlueprintConfiguration):
    niche_description: Text
    target_audience: Text
    tone: str = Field(min_length=1, max_length=500)
    formats: GeneratedFormats
    video_style: GeneratedVideo
    hook_style: Text
    visual_style: GeneratedVisual
    suggested_posting_strategy: PostingStrategy
    seed_keywords: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        min_length=1, max_length=20
    )


class ChannelAnalysis(BlueprintInput):
    configuration: GeneratedConfiguration
    content_pillars: list[GeneratedPillar] = Field(min_length=1, max_length=20)


class AnalysisInput(InputModel):
    idea: str
    language: str
