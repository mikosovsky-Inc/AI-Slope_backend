from typing import Annotated

from fastapi import Depends

from app.modules.intelligence.research import (
    CompetitorResearchProvider,
    LocalCompetitorResearchProvider,
)


def get_competitor_research_provider() -> CompetitorResearchProvider:
    # Live search is not configured yet. Empty local data never fabricates competitors.
    return LocalCompetitorResearchProvider()


CurrentResearch = Annotated[CompetitorResearchProvider, Depends(get_competitor_research_provider)]
