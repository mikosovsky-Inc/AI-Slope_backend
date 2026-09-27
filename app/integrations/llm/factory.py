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

        return MockLLMProvider({ChannelAnalysis: mock_analysis, GeneratedIdeas: mock_ideas})
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
