from app.modules.ideas.models import ContentFormat
from app.modules.videos.models import VideoStatus as S


class InvalidVideoTransition(Exception):
    pass


NEXT = {
    S.DRAFT: {S.IDEA_GENERATED},
    S.IDEA_GENERATED: {S.RESEARCHING, S.SCRIPTING},
    S.RESEARCHING: {S.RESEARCHED},
    S.RESEARCHED: {S.SCRIPTING},
    S.SCRIPTING: {S.SCRIPT_READY},
    S.SCRIPT_READY: {S.GENERATING_ASSETS},
    S.GENERATING_ASSETS: {S.ASSETS_READY},
    S.ASSETS_READY: {S.GENERATING_AUDIO},
    S.GENERATING_AUDIO: {S.READY_TO_RENDER},
    S.READY_TO_RENDER: {S.RENDERING},
    S.RENDERING: {S.QUALITY_CHECK},
    S.QUALITY_CHECK: {S.READY, S.GENERATING_ASSETS},
    S.READY: {S.PUBLISHED},
    S.FAILED: set(),
    S.PUBLISHED: set(),
}


def validate_transition(source: S, target: S, format: ContentFormat) -> None:
    if source == target:
        return
    allowed = NEXT[source] | ({S.FAILED} if source not in (S.FAILED, S.PUBLISHED) else set())
    if target not in allowed:
        raise InvalidVideoTransition
    if source == S.IDEA_GENERATED and target != S.FAILED:
        expected = S.RESEARCHING if format == ContentFormat.TOP5 else S.SCRIPTING
        if target != expected:
            raise InvalidVideoTransition
