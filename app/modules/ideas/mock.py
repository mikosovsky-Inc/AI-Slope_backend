import json

from app.shared.llm import LLMRequest


def mock_ideas(request: LLMRequest) -> dict:
    context = json.loads(request.prompt)
    polish = context["language"] == "pl"
    pillars = context["blueprint"]["content_pillars"]
    weights = context["blueprint"]["configuration"]["formats"]
    count = context["count"]
    top5_count = round(count * weights["top5"])
    items = []
    for index in range(count):
        number = context["previous_count"] + index + 1
        pillar = pillars[index % len(pillars)]["name"]
        heuristic = {"score": 0.5, "rationale": "Przykład offline" if polish else "Offline example"}
        items.append(
            {
                "title": f"{pillar}: " + (f"pomysł {number}" if polish else f"idea {number}"),
                "concept": "Przykładowy temat do dalszego opracowania"
                if polish
                else "Example topic for further development",
                "content_pillar": pillar,
                "format": "top5" if index < top5_count else "story",
                "hook_idea": "Co warto odkryć?" if polish else "What is worth discovering?",
                "rationale": "Przykład zgodny z filarem kanału"
                if polish
                else "Example aligned with the channel pillar",
                "novelty_heuristic": heuristic,
                "visual_potential_heuristic": heuristic,
            }
        )
    return {"items": items}
