from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings
from app.integrations.llm.mock import MockLLMProvider
from app.shared.llm import LLMProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    if settings.external_providers_mode == "mock":
        from app.modules.ideas.mock import mock_ideas
        from app.modules.ideas.schemas import GeneratedIdeas
        from app.modules.intelligence.mock import mock_analysis
        from app.modules.intelligence.schemas import ChannelAnalysis
        from app.modules.research.mock import mock_facts, mock_queries, mock_top5
        from app.modules.research.schemas import ExtractedFacts, ResearchQueries, Top5Plan
        from app.modules.scripts.mock import mock_beats, mock_scenes
        from app.modules.scripts.schemas import StoryNarrative, StoryOutline, StoryScenes

        return MockLLMProvider(
            {
                ResearchQueries: mock_queries,
                ExtractedFacts: mock_facts,
                Top5Plan: mock_top5,
                ChannelAnalysis: mock_analysis,
                GeneratedIdeas: mock_ideas,
                StoryOutline: mock_beats,
                StoryNarrative: mock_beats,
                StoryScenes: mock_scenes,
            }
        )
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value().strip():
        raise ValueError("OPENAI_API_KEY is required in live mode")
    if not settings.openai_model.strip():
        raise ValueError("OPENAI_MODEL is required in live mode")
    from app.integrations.llm.openai import OpenAIProvider

    return OpenAIProvider.create(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )


def get_llm_provider(request: Request) -> LLMProvider:
    return request.app.state.llm


CurrentLLM = Annotated[LLMProvider, Depends(get_llm_provider)]
