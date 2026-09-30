"""Explicit synthetic corpus for the opt-in mock demo, never factual research."""

from app.core.config import Settings
from app.modules.research.provider import LocalResearchProvider
from app.modules.research.schemas import SourceDocument


def demo_research(settings: Settings) -> LocalResearchProvider:
    if settings.external_providers_mode != "mock":
        raise ValueError("Demo research requires mock providers")
    return LocalResearchProvider(
        [
            SourceDocument(
                source_url=f"https://demo.invalid/source-{i}",
                source_title=f"DEMO: syntetyczne dane testowe {i}",
                language="pl",
                content="\n".join(f"DEMO: fikcyjny zapis testowy {i}-{j}." for j in range(3)),
                metadata={"fixture": True, "not_factual": True},
            )
            for i in range(2)
        ]
    )
