"""Search boundary for stage 5; local records are supplied explicitly, never scraped."""

from typing import Annotated, Literal, Protocol

from pydantic import AnyHttpUrl, ConfigDict, Field

from app.modules.channels.schemas import InputModel, Language


class CompetitorResearchQuery(InputModel):
    language: Language
    seed_keywords: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)


class CompetitorResearchResult(InputModel):
    model_config = ConfigDict(revalidate_instances="always")
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["youtube", "tiktok", "instagram"]
    url: Annotated[AnyHttpUrl, Field(max_length=2000)]
    language: Language
    niche: str = Field(min_length=1, max_length=2000)
    example_titles: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    observed_formats: list[Literal["top5", "story"]] = Field(default_factory=list)
    typical_length_seconds: int | None = Field(default=None, gt=0)
    publishing_frequency: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)


class CompetitorResearchProvider(Protocol):
    def research(self, query: CompetitorResearchQuery) -> list[CompetitorResearchResult]: ...


class LocalCompetitorResearchProvider:
    def __init__(self, records: list[CompetitorResearchResult] | None = None) -> None:
        self._records = [record.model_copy(deep=True) for record in records or []]

    def research(self, query: CompetitorResearchQuery) -> list[CompetitorResearchResult]:
        keywords = [keyword.casefold() for keyword in query.seed_keywords]
        return [
            record.model_copy(deep=True)
            for record in self._records
            if record.language == query.language
            and any(keyword in f"{record.name} {record.niche}".casefold() for keyword in keywords)
        ][: query.limit]
