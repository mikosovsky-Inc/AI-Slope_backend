"""Explicit offline example strategy, not an AI or market-research result."""

import json

from app.shared.llm import LLMRequest


def mock_analysis(request: LLMRequest) -> dict:
    data = json.loads(request.prompt)
    polish = data["language"] == "pl"
    return {
        "configuration": {
            "niche_description": (
                "Przykładowa strategia offline: " if polish else "Example offline strategy: "
            )
            + data["idea"][:1500],
            "target_audience": "Dorośli zainteresowani tematyką kanału"
            if polish
            else "Adults interested in the channel topic",
            "tone": "Przystępny i angażujący" if polish else "Accessible and engaging",
            "formats": {"top5": 0.7, "story": 0.3},
            "video_style": {"duration_target": 45, "pace": "fast", "hook_max_seconds": 2},
            "hook_style": "Pytanie otwierające" if polish else "Opening question",
            "visual_style": {
                "description": "Ilustracje tematyczne" if polish else "Thematic illustrations",
                "video_scene_ratio": 0.25,
            },
            "suggested_posting_strategy": {
                "videos_per_day": 2,
                "rationale": "Przykładowy rytm do przetestowania"
                if polish
                else "Example cadence to test",
            },
            "seed_keywords": ["ciekawostki", "opowieści"] if polish else ["facts", "stories"],
        },
        "content_pillars": [
            {
                "name": "Odkrycia" if polish else "Discoveries",
                "description": "Wprowadzenie do tematyki kanału"
                if polish
                else "Introduction to the channel topic",
            }
        ],
    }
