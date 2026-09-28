from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.channels.models import Channel
from app.modules.channels.schemas import BlueprintInput, PillarInput
from app.modules.channels.service import detail, owned_channel
from app.modules.ideas.models import ContentIdea, IdeaStatus
from app.modules.ideas.service import IdeaNotFound
from app.modules.videos.models import Video, VideoStatus, VideoStatusEvent
from app.modules.videos.schemas import VideoRead
from app.modules.videos.state import InvalidVideoTransition, validate_transition


class VideoRequiresApprovedIdea(Exception):
    pass


def transition_video(
    db: Session,
    video_id: UUID,
    target: VideoStatus,
    *,
    reason: str,
    expected_status: VideoStatus | None = None,
) -> Video:
    """Internal workflow operation. Caller must commit/rollback the whole transaction.

    Locks the video row; a repeated target is a no-op, including its history.
    No HTTP endpoint exposes arbitrary status transitions.
    """
    if not reason.strip() or len(reason) > 500:
        raise ValueError("Provide a short, safe transition reason (1-500 characters)")
    video = db.exec(
        select(Video)
        .where(Video.id == video_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if expected_status is not None and video.status != expected_status:
        raise InvalidVideoTransition
    validate_transition(video.status, target, video.format)
    if video.status == target:
        return video
    sequence = (
        db.exec(
            select(func.max(VideoStatusEvent.sequence)).where(VideoStatusEvent.video_id == video.id)
        ).one()
        or 0
    )
    db.add(
        VideoStatusEvent(
            video_id=video.id,
            sequence=sequence + 1,
            from_status=video.status,
            to_status=target,
            reason=reason,
        )
    )
    video.status = target
    video.updated_at = datetime.now(UTC)
    db.add(video)
    db.flush()
    return video


def create_video(
    db: Session, owner_id: UUID, idea_id: UUID, *, enqueue_workflow: bool = False
) -> tuple[VideoRead, bool]:
    if enqueue_workflow:
        from app.models.user import User

        db.exec(select(User).where(User.id == owner_id).with_for_update()).one()
    # Same channel-first lock order as approval/rejection and idea generation.
    idea = db.exec(
        select(ContentIdea)
        .join(Channel)
        .where(ContentIdea.id == idea_id, Channel.owner_id == owner_id)
    ).one_or_none()
    if idea is None:
        raise IdeaNotFound
    try:
        channel = owned_channel(db, owner_id, idea.channel_id, lock=True)
        db.refresh(idea)
        existing = db.exec(select(Video).where(Video.idea_id == idea_id)).one_or_none()
        if existing is not None:
            result = VideoRead.model_validate(existing)
            db.commit()
            return result, False
        if idea.status != IdeaStatus.APPROVED:
            raise VideoRequiresApprovedIdea
        blueprint = detail(db, channel).blueprint
        snapshot = BlueprintInput(
            configuration=blueprint.configuration,
            content_pillars=[
                PillarInput(name=p.name, description=p.description)
                for p in blueprint.content_pillars
            ],
        )
        video = Video(
            idea_id=idea_id,
            title=idea.title,
            language=idea.language,
            format=idea.format,
            budget_limit_usd=channel.budget_per_video_usd,
            duration_target=blueprint.configuration.video_style.duration_target,
            blueprint_snapshot=snapshot.model_dump(mode="json"),
        )
        db.add(video)
        db.flush()
        db.add(
            VideoStatusEvent(
                video_id=video.id,
                sequence=1,
                to_status=VideoStatus.DRAFT,
                reason="Video created from approved idea",
            )
        )
        db.flush()
        transition_video(
            db,
            video.id,
            VideoStatus.IDEA_GENERATED,
            reason="Idea attached; awaiting the next workflow stage",
        )
        idea.status = IdeaStatus.USED
        idea.updated_at = datetime.now(UTC)
        channel.updated_at = idea.updated_at
        db.add(idea)
        db.add(channel)
        db.flush()
        db.refresh(video)
        result = VideoRead.model_validate(video)
        if enqueue_workflow:
            from app.modules.tasks.models import TaskKind
            from app.modules.tasks.service import enqueue

            kind = TaskKind.RESEARCH if video.format.value == "top5" else TaskKind.STORY
            enqueue(
                db,
                owner_id,
                kind,
                video_id=video.id,
                parameters={"workflow": True},
                key=f"workflow:{video.id}",
                commit=False,
            )
        db.commit()
        return result, True
    except Exception:
        db.rollback()
        raise
