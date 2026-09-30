"""Idempotent, mock-only demo for an existing account: python -m app.demo."""

import argparse
import time
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.models.user import User
from app.modules.channels.models import Channel, ChannelBlueprint
from app.modules.channels.schemas import (
    BlueprintConfiguration,
    ChannelCreate,
    FormatMix,
    VideoStyle,
)
from app.modules.ideas.models import ContentFormat, ContentIdea, IdeaStatus
from app.modules.ideas.schemas import GeneratedIdea, Heuristic
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.service import enqueue
from app.modules.videos.models import Video, VideoStatus
from app.modules.videos.service import create_video


class DemoItem(BaseModel):
    channel_id: UUID
    idea_id: UUID
    video_id: UUID | None = None


class DemoResult(BaseModel):
    items: list[DemoItem]


def seed_demo(db: Session, settings: Settings, email: str, *, generate: bool = False) -> DemoResult:
    if settings.external_providers_mode != "mock":
        raise ValueError("Demo requires EXTERNAL_PROVIDERS_MODE=mock")
    owner = db.exec(
        select(User).where(User.email == email.strip().lower()).with_for_update()
    ).first()
    if owner is None or not owner.is_active:
        raise ValueError("Register an active account before running the demo")
    items = []
    for format_, language, name in (
        (ContentFormat.TOP5, "pl", "Mroczne ciekawostki historyczne"),
        (ContentFormat.STORY, "en", "Short fictional horror stories with unexpected twists"),
    ):
        channel_id = uuid5(owner.id, f"ai-slop-demo:{format_}")
        idea_id = uuid5(channel_id, "idea")
        channel = db.get(Channel, channel_id)
        if channel is None:
            data = ChannelCreate(name=name, idea=name, language=language)
            channel = Channel(id=channel_id, owner_id=owner.id, **data.model_dump())
            db.add(channel)
            db.flush()
            configuration = BlueprintConfiguration(
                niche_description=name,
                tone="DEMO: synthetic test content",
                formats=FormatMix(
                    top5=float(format_ == ContentFormat.TOP5),
                    story=float(format_ == ContentFormat.STORY),
                ),
                video_style=VideoStyle(duration_target=20),
            )
            db.add(
                ChannelBlueprint(
                    channel_id=channel_id, configuration=configuration.model_dump(mode="json")
                )
            )
        if db.get(ContentIdea, idea_id) is None:
            heuristic = Heuristic(score=0.5, rationale="Synthetic demo fixture")
            data = GeneratedIdea(
                title=f"DEMO: {name}",
                concept="DEMO: synthetic test content, not historical facts.",
                content_pillar="DEMO",
                format=format_,
                hook_idea="DEMO",
                rationale="Mock pipeline demonstration",
                novelty_heuristic=heuristic,
                visual_potential_heuristic=heuristic,
            )
            db.add(
                ContentIdea(
                    id=idea_id,
                    channel_id=channel_id,
                    language=language,
                    title_key=data.title.casefold(),
                    status=IdeaStatus.APPROVED,
                    **data.model_dump(mode="json"),
                )
            )
            db.flush()
        item = DemoItem(channel_id=channel_id, idea_id=idea_id)
        if generate:
            video, created = create_video(db, owner.id, idea_id, commit=False)
            item.video_id = video.id
            if created:
                parameters = {"workflow": True}
                if format_ == ContentFormat.TOP5:
                    parameters["demo_research"] = True
                enqueue(
                    db,
                    owner.id,
                    TaskKind.RESEARCH if format_ == ContentFormat.TOP5 else TaskKind.STORY,
                    video_id=video.id,
                    parameters=parameters,
                    key=f"workflow:{video.id}",
                    commit=False,
                )
        items.append(item)
    db.commit()
    return DemoResult(items=items)


def wait_for_demo(engine, result: DemoResult, timeout: int) -> None:
    ids = [item.video_id for item in result.items if item.video_id]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with Session(engine) as db:
            videos = [db.get(Video, identifier) for identifier in ids]
            failed = db.exec(
                select(Task.id).where(
                    Task.video_id.in_(ids),
                    Task.status.in_([TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW]),
                )
            ).first()
            if failed or any(v.status == VideoStatus.FAILED for v in videos):
                raise RuntimeError("Demo stopped; inspect video task errors in the panel")
            if all(v.status == VideoStatus.READY for v in videos):
                print("READY: " + ", ".join(str(identifier) for identifier in ids), flush=True)
                return
        time.sleep(2)
    raise TimeoutError("Demo still pending; check worker and dispatcher. Jobs remain queued.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.wait and not args.generate:
        parser.error("--wait requires --generate")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    settings = get_settings()
    engine = get_engine()
    with Session(engine) as db:
        result = seed_demo(db, settings, args.owner_email, generate=args.generate)
    print(result.model_dump_json(indent=2), flush=True)
    if args.wait:
        wait_for_demo(engine, result, args.timeout)


if __name__ == "__main__":
    main()
