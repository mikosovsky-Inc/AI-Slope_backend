import json

from app.shared.llm import LLMRequest


def mock_queries(request: LLMRequest) -> dict:
    data = json.loads(request.prompt)
    return {"queries": [data["title"][:500]]}


def mock_facts(request: LLMRequest) -> dict:
    data = json.loads(request.prompt)
    return {
        "facts": [
            {"document_id": d["id"], "quote": line, "confidence": 0.9}
            for d in data["documents"]
            for line in d["content"].splitlines()
            if line.strip()
        ][:30]
    }


def mock_top5(request: LLMRequest) -> dict:
    data = json.loads(request.prompt)
    # Round-robin across documents to ensure fixture plans use multiple sources.
    groups = {}
    for fact in data["facts"]:
        groups.setdefault(fact["document_id"], []).append(fact)
    selected = []
    while len(selected) < 5:
        for group in groups.values():
            if group and len(selected) < 5:
                selected.append(group.pop(0))
    duration = round((data["duration_target"] - data["hook_seconds"]) / 5, 3)
    return {
        "items": [
            {
                "fact_id": f["id"],
                "duration": duration,
                "visual_prompt": "Illustration: " + f["statement"],
            }
            for f in selected
        ]
    }
