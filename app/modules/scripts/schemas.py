from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.modules.channels.schemas import InputModel
from app.modules.videos.schemas import SceneInput, ScriptInput

Text = Annotated[str, Field(min_length=1, max_length=4000)]


class StoryBeats(InputModel):
    hook: Text
    setup: Text
    escalation: Text
    reveal: Text
    twist: Text


class StoryOutline(StoryBeats):
    pass


class StoryNarrative(StoryBeats):
    pass


class GeneratedScene(SceneInput):
    duration: float = Field(gt=0, le=180)
    visual_prompt: str = Field(min_length=1, max_length=4000)


class StoryScenes(ScriptInput):
    scenes: list[GeneratedScene] = Field(min_length=5, max_length=50)


class ScriptRead(ScriptInput):
    id: UUID
    video_id: UUID
    outline: StoryOutline
    story: StoryNarrative
