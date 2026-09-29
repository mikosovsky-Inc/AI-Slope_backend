from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings
from app.core.observability import ObservedProvider
from app.integrations.runpod.mock import MockRunpodProvider
from app.integrations.runpod.provider import RunpodProvider
from app.shared.generation import MediaGenerationProvider


def create_generation_provider(settings: Settings) -> MediaGenerationProvider:
    if settings.external_providers_mode == "mock":
        return MockRunpodProvider()
    return RunpodProvider(settings)


def get_generation_provider(request: Request) -> MediaGenerationProvider:
    return ObservedProvider(request.app.state.generation, "generation")


CurrentGeneration = Annotated[MediaGenerationProvider, Depends(get_generation_provider)]
