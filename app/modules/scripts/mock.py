import json

from app.shared.llm import LLMRequest


def mock_beats(request: LLMRequest) -> dict:
    context = json.loads(request.prompt)
    if context["language"] == "pl":
        texts = [
            "Kto puka?",
            "Nocą Lena usłyszała pukanie do zamkniętego pokoju.",
            "Pod drzwiami pojawiła się kartka z jej imieniem.",
            "Rozpoznała własne pismo i datę następnego dnia.",
            "Za drzwiami stała starsza Lena, prosząc, by tym razem nie otwierała.",
        ]
    else:
        texts = [
            "Who knocked?",
            "At midnight Lena heard knocking from the locked room.",
            "A note bearing her name slipped under the door.",
            "She recognized her handwriting and tomorrow's date.",
            "An older Lena stood outside, begging her not to open it this time.",
        ]
    return dict(zip(["hook", "setup", "escalation", "reveal", "twist"], texts, strict=True))


def mock_scenes(request: LLMRequest) -> dict:
    context = json.loads(request.prompt)
    texts = list(context["story"].values())
    target_ms = context["duration_target"] * 1000
    hook_ms = min(
        2000, int(context["blueprint"]["configuration"]["video_style"]["hook_max_seconds"] * 1000)
    )
    remaining = target_ms - hook_ms
    durations = [hook_ms] + [remaining // 4] * 3 + [remaining - 3 * (remaining // 4)]
    return {
        "title": context["title"],
        "hook": texts[0],
        "language": context["language"],
        "duration_target": context["duration_target"],
        "scenes": [
            {
                "position": i + 1,
                "duration": durations[i] / 1000,
                "narration": text,
                "visual_prompt": f"Fictional illustration, scene {i + 1}: {text}",
                "visual_type": "image",
                "camera_motion": "slow_push",
                "mood": "mysterious",
                "caption_emphasis": [],
            }
            for i, text in enumerate(texts)
        ],
    }
