from typing import Annotated, Protocol

from fastapi import Depends

from app.modules.research.schemas import ResearchQuery, SourceDocument


class ResearchUnavailable(Exception):
    pass


class ResearchProvider(Protocol):
    def search(self, query: ResearchQuery) -> list[SourceDocument]: ...


class LocalResearchProvider:
    """Explicit corpus only: never invents URLs or fetches the network."""

    def __init__(self, documents: list[SourceDocument] | None = None) -> None:
        self.documents = [d.model_copy(deep=True) for d in documents or []]

    def search(self, query: ResearchQuery) -> list[SourceDocument]:
        terms = query.text.casefold().split()
        return [
            d.model_copy(deep=True)
            for d in self.documents
            if d.language == query.language
            and any(t in (d.source_title + " " + d.content).casefold() for t in terms)
        ][: query.limit]


def get_research_provider() -> ResearchProvider:
    return LocalResearchProvider()


CurrentResearch = Annotated[ResearchProvider, Depends(get_research_provider)]
