import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.core.config import Settings
from app.models.user import User
from app.modules.channels.models import AutopilotMode, Channel, ChannelStatus
from app.modules.channels.service import detail
from app.modules.ideas.models import ContentFormat, ContentIdea, IdeaStatus
from app.modules.scheduler.models import DailyPlan, PlanStatus
from app.modules.scheduler.schemas import PlanResult, TickResult
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.service import enqueue
from app.modules.videos.models import Video
from app.modules.videos.service import create_video

logger = logging.getLogger("app.scheduler")


def day_window(now: datetime, timezone: str) -> tuple[date, datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("Scheduler requires a timezone-aware clock")
    zone = ZoneInfo(timezone)
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return day, start.astimezone(UTC), end.astimezone(UTC)


def daily_video_counts(db: Session, plan: DailyPlan) -> dict[str, int]:
    rows = db.exec(
        select(Video.format, func.count(Video.id))
        .join(ContentIdea)
        .where(
            ContentIdea.channel_id == plan.channel_id,
            Video.created_at >= plan.window_start,
            Video.created_at < plan.window_end,
        )
        .group_by(Video.format)
    ).all()
    return {format.value: count for format, count in rows}


def plan_channel(
    db: Session, channel_id: UUID, settings: Settings, *, now: datetime | None = None
) -> PlanResult | None:
    """One short transaction: User → Channel → plan, videos and task outbox. No external calls."""
    if not settings.scheduler_enabled:
        return None
    now = now or datetime.now(UTC)
    day, start, end = day_window(now, settings.scheduler_timezone)
    now = now.astimezone(UTC)
    channel = db.get(Channel, channel_id)
    if channel is None:
        return None
    try:
        owner = db.exec(
            select(User).where(User.id == channel.owner_id).with_for_update()
        ).one_or_none()
        channel = db.exec(
            select(Channel)
            .where(Channel.id == channel_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if (
            owner is None
            or not owner.is_active
            or channel is None
            or channel.status != ChannelStatus.ACTIVE
        ):
            db.rollback()
            return None
        plan = db.exec(
            select(DailyPlan).where(DailyPlan.channel_id == channel.id, DailyPlan.day == day)
        ).one_or_none()
        if plan is None:
            plan = DailyPlan(
                channel_id=channel.id,
                day=day,
                timezone=settings.scheduler_timezone,
                window_start=start,
                window_end=end,
                target=channel.videos_per_day,
                status=PlanStatus.WAITING_TASK,
                created_at=now,
                updated_at=now,
            )
            db.add(plan)
            db.flush()
        plan.target, plan.updated_at, plan.reason = channel.videos_per_day, now, None
        counts = Counter(daily_video_counts(db, plan))
        result = PlanResult(
            channel_id=channel.id,
            day=day,
            target=plan.target,
            videos_today=sum(counts.values()),
            status=PlanStatus.COMPLETE,
        )
        if sum(counts.values()) >= plan.target:
            return finish(db, plan, result, PlanStatus.COMPLETE)
        current = detail(db, channel)
        if (
            not current.blueprint.content_pillars
            or not current.blueprint.configuration.target_audience
        ):
            return finish(db, plan, result, PlanStatus.BLOCKED, "blueprint_not_ready")
        # Avoid invalidating a generator's channel version while it is making an external call.
        pending = db.exec(
            select(Task.id).where(
                Task.channel_id == channel.id,
                Task.kind.in_([TaskKind.IDEAS, TaskKind.ANALYZE]),
                Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
            )
        ).first()
        if pending:
            return finish(db, plan, result, PlanStatus.WAITING_TASK, "channel_task_pending")
        weights = current.blueprint.configuration.formats.model_dump()
        available = []
        eligible = [IdeaStatus.APPROVED]
        if channel.autopilot_mode == AutopilotMode.SEMI_AUTO:
            eligible.append(IdeaStatus.CANDIDATE)
        remaining = plan.target - sum(counts.values())
        for format in ContentFormat:
            if weights[format.value] <= 0:
                continue
            available.extend(
                db.exec(
                    select(ContentIdea)
                    .where(
                        ContentIdea.channel_id == channel.id,
                        ContentIdea.language == channel.language,
                        ContentIdea.status.in_(eligible),
                        ContentIdea.format == format,
                    )
                    .order_by(
                        (ContentIdea.status == IdeaStatus.APPROVED).desc(),
                        ContentIdea.created_at,
                        ContentIdea.id,
                    )
                    .limit(remaining)
                ).all()
            )
        for _ in range(remaining):
            if not available:
                break
            approved = [i for i in available if i.status == IdeaStatus.APPROVED]
            if approved:
                idea = min(approved, key=lambda i: (i.created_at, i.id))
            else:
                # Prefer an underrepresented enabled format, then the oldest idea.
                preferred = max(
                    {i.format.value for i in available},
                    key=lambda f: (plan.target * weights[f] - counts[f], f),
                )
                idea = min(
                    (i for i in available if i.format.value == preferred),
                    key=lambda i: (i.created_at, i.id),
                )
                idea.status, idea.updated_at = IdeaStatus.APPROVED, now
                db.add(idea)
                db.flush()
            video, created = create_video(
                db, owner.id, idea.id, enqueue_workflow=True, commit=False, now=now
            )
            available.remove(idea)
            if created:
                result.created_video_ids.append(video.id)
                result.videos_today += 1
                counts[idea.format.value] += 1
        if result.videos_today >= plan.target:
            return finish(db, plan, result, PlanStatus.COMPLETE)
        formats = [ContentFormat(f) for f, weight in weights.items() if weight > 0]
        candidates = db.exec(
            select(func.count())
            .select_from(ContentIdea)
            .where(
                ContentIdea.channel_id == channel.id,
                ContentIdea.language == channel.language,
                ContentIdea.status == IdeaStatus.CANDIDATE,
                ContentIdea.format.in_(formats),
            )
        ).one()
        missing = plan.target - result.videos_today - candidates
        if missing <= 0:
            return finish(db, plan, result, PlanStatus.WAITING_APPROVAL)
        previous = [db.get(Task, UUID(identifier)) for identifier in plan.idea_task_ids]
        if any(
            t is None or t.status in (TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW) for t in previous
        ):
            return finish(db, plan, result, PlanStatus.BLOCKED, "idea_generation_requires_review")
        if len(previous) >= settings.scheduler_max_idea_batches_per_day:
            return finish(db, plan, result, PlanStatus.BLOCKED, "daily_idea_batch_limit")
        task = enqueue(
            db,
            owner.id,
            TaskKind.IDEAS,
            channel_id=channel.id,
            parameters={"count": max(10, min(20, missing)), "scheduler_plan_id": str(plan.id)},
            key=f"scheduler-ideas:{plan.id}:{len(previous) + 1}",
            commit=False,
        )
        plan.idea_task_ids = [*plan.idea_task_ids, str(task.id)]
        result.queued_idea_task_id = task.id
        return finish(db, plan, result, PlanStatus.WAITING_TASK)
    except Exception:
        db.rollback()
        raise


def finish(
    db: Session, plan: DailyPlan, result: PlanResult, status: PlanStatus, reason: str | None = None
) -> PlanResult:
    plan.status, plan.reason = status, reason
    result.status, result.reason = status, reason
    db.add(plan)
    db.commit()
    return result


def scheduled_task_skip_reason(db: Session, task: Task, settings: Settings) -> str | None:
    """Revalidate immediately before an automatic idea call, including stale queued work."""
    identifier = task.parameters.get("scheduler_plan_id")
    if not identifier:
        return None
    if not settings.scheduler_enabled:
        return "scheduler_disabled"
    plan = db.get(DailyPlan, UUID(identifier))
    channel = db.get(Channel, task.channel_id)
    if (
        plan is None
        or channel is None
        or plan.channel_id != channel.id
        or channel.owner_id != task.owner_id
        or str(task.id) not in plan.idea_task_ids
    ):
        return "schedule_unavailable"
    if channel.status != ChannelStatus.ACTIVE:
        return "channel_inactive"
    now = datetime.now(UTC)
    start = (
        plan.window_start.replace(tzinfo=UTC)
        if plan.window_start.tzinfo is None
        else plan.window_start
    )
    end = plan.window_end.replace(tzinfo=UTC) if plan.window_end.tzinfo is None else plan.window_end
    if not start <= now < end:
        return "schedule_expired"
    if sum(daily_video_counts(db, plan).values()) >= channel.videos_per_day:
        return "daily_target_reached"
    return None


def plan_once(
    engine: Engine,
    settings: Settings,
    *,
    now: datetime | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> TickResult:
    result = TickResult()
    if not settings.scheduler_enabled:
        return result
    cursor = None
    while not (should_stop and should_stop()):
        with Session(engine) as db:
            query = (
                select(Channel.id)
                .join(User)
                .where(Channel.status == ChannelStatus.ACTIVE, User.is_active.is_(True))
            )
            if cursor is not None:
                query = query.where(Channel.id > cursor)
            identifiers = db.exec(
                query.order_by(Channel.id).limit(settings.scheduler_batch_size)
            ).all()
        if not identifiers:
            break
        for identifier in identifiers:
            if should_stop and should_stop():
                return result
            result.scanned += 1
            try:
                with Session(engine, expire_on_commit=False) as db:
                    outcome = plan_channel(db, identifier, settings, now=now)
                if outcome:
                    result.planned += 1
                    result.videos_created += len(outcome.created_video_ids)
                    result.idea_tasks_created += int(outcome.queued_idea_task_id is not None)
            except Exception as exc:
                result.errors += 1
                logger.warning(
                    "Channel planning failed",
                    extra={"channel_id": str(identifier), "error_type": type(exc).__name__},
                )
        cursor = identifiers[-1]
    return result
