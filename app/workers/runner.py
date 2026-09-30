import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.observability import categorize_error, log_context
from app.models.user import User
from app.modules.audio.service import AudioConflict
from app.modules.costs.service import BudgetExceeded
from app.modules.quality.provider import VisualQualityUnavailable
from app.modules.quality.service import QualityPending, enqueue_quality
from app.modules.render.engine import RenderError
from app.modules.tasks.models import QUEUES, Task, TaskKind, TaskStatus
from app.modules.tasks.service import enqueue
from app.shared.generation import GenerationSubmissionUnknown, GenerationUnavailable
from app.shared.storage import StorageError
from app.shared.tts import TTSOutcomeUnknown
from app.workers.operations import PollLater, ReviewRequired, execute_operation, recover_result

logger = logging.getLogger("app.worker")


@contextmanager
def execution_lock(engine, task_id: UUID):
    # Session-level advisory lock survives service commits; OS/process death releases it.
    if engine.dialect.name != "postgresql":
        yield True  # SQLite is used only by serial unit tests.
        return
    key = int.from_bytes(task_id.bytes[:8], "big", signed=True)
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()


def run_task(engine, task_id: str, *, settings=None) -> None:
    with Session(engine) as db:
        task = db.get(Task, UUID(task_id))
        if task is None:
            return
        fields = {
            "request_id": task.request_id or f"task-{task.id}",
            "task_id": str(task.id),
            "task_kind": task.kind.value,
            "queue": QUEUES[task.kind],
            "video_id": str(task.video_id) if task.video_id else None,
            "channel_id": str(task.channel_id) if task.channel_id else None,
            "scene_id": str(task.scene_id) if task.scene_id else None,
        }
    start = perf_counter()
    with log_context(**fields):
        try:
            _run_task(engine, task_id, settings=settings)
        finally:
            logger.info(
                "Worker invocation completed",
                extra={
                    "event": "worker_execution",
                    "duration_ms": round((perf_counter() - start) * 1000, 3),
                },
            )


def _run_task(engine, task_id: str, *, settings=None) -> None:
    settings = settings or get_settings()
    identifier = UUID(task_id)
    with execution_lock(engine, identifier) as acquired:
        if not acquired:
            return
        with Session(engine, expire_on_commit=False) as db:
            task = db.get(Task, identifier)
            if task is None or task.status in (
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.NEEDS_REVIEW,
            ):
                return
            recovering = task.status == TaskStatus.RUNNING
            now = datetime.now(UTC)
            # Redis may redeliver an old message before its delayed retry/poll is due.
            if task.started_at and task.status == TaskStatus.QUEUED:
                due = (
                    task.available_at.replace(tzinfo=UTC)
                    if task.available_at.tzinfo is None
                    else task.available_at
                )
                if due > now:
                    return
            user = db.get(User, task.owner_id)
            if user is None or not user.is_active:
                task.status, task.error = TaskStatus.FAILED, "owner_unavailable"
                task.error_category = "authorization"
                task.completed_at = now
                db.add(task)
                db.commit()
                return
            token = uuid4()
            task.run_token = token
            task.status = TaskStatus.RUNNING
            task.started_at = task.started_at or now
            if not task.checkpoint.get("provider_job"):
                task.attempts += 1
            task.available_at = now + timedelta(seconds=settings.task_lease_seconds)
            db.add(task)
            db.commit()
            try:
                recovered = recover_result(db, task) if recovering else None
                if recovered is not None:
                    result = recovered
                else:
                    if (
                        recovering
                        and task.kind
                        not in (TaskKind.AUDIO, TaskKind.DIRECT, TaskKind.RENDER, TaskKind.QUALITY)
                        and not task.checkpoint.get("provider_job")
                    ):
                        raise ReviewRequired
                    result = execute_operation(db, task, settings)
                db.rollback()
                db.exec(select(User).where(User.id == task.owner_id).with_for_update()).one()
                task = db.exec(
                    select(Task).where(Task.id == identifier).with_for_update()
                ).one_or_none()
                if task is None or task.run_token != token:
                    return
                task.result, task.status = result, TaskStatus.SUCCEEDED
                task.completed_at, task.error = datetime.now(UTC), None
                task.error_category = None
                db.add(task)
                db.flush()
                from app.modules.production.service import advance_production

                advance_production(db, task)
                # Complete parent and enqueue child atomically; no broker send in this transaction.
                next_kind = {
                    TaskKind.RESEARCH: TaskKind.TOP5,
                    TaskKind.STORY: TaskKind.DIRECT,
                    TaskKind.TOP5: TaskKind.DIRECT,
                }.get(task.kind)
                if task.parameters.get("workflow") and next_kind:
                    enqueue(
                        db,
                        task.owner_id,
                        next_kind,
                        video_id=task.video_id,
                        parameters={"workflow": True},
                        key=f"workflow:{task.video_id}:{next_kind}",
                        commit=False,
                    )
                if task.kind == TaskKind.RENDER:
                    quality_task = enqueue_quality(db, task.owner_id, task, commit=False)
                    task.result = task.result | {"quality_task_id": str(quality_task.id)}
                db.add(task)
                db.commit()
                logger.info(
                    "Task completed", extra={"event": "task_completed", "status": task.status.value}
                )
            except Exception as exc:
                db.rollback()
                task = db.get(Task, identifier)
                if task is None or task.run_token != token:
                    return
                task.error_category = (
                    "pending"
                    if isinstance(exc, (PollLater, QualityPending))
                    else "review_required"
                    if isinstance(exc, (ReviewRequired, AudioConflict))
                    else categorize_error(exc).value
                )
                if isinstance(exc, BudgetExceeded):
                    task.status, task.error = TaskStatus.FAILED, "budget_exceeded"
                elif isinstance(exc, QualityPending):
                    task.attempts = max(0, task.attempts - 1)
                    task.status = TaskStatus.QUEUED
                    task.available_at = datetime.now(UTC) + timedelta(seconds=5)
                elif (
                    task.kind == TaskKind.QUALITY
                    and isinstance(
                        exc, (StorageError, RenderError, VisualQualityUnavailable, OSError)
                    )
                    and task.attempts < task.max_attempts
                ):
                    task.status = TaskStatus.QUEUED
                    task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempts)
                elif isinstance(exc, PollLater):
                    if datetime.now(UTC) - task.started_at.replace(tzinfo=UTC) > timedelta(
                        milliseconds=settings.runpod_job_ttl_ms
                    ):
                        task.status, task.error = TaskStatus.NEEDS_REVIEW, "provider_poll_deadline"
                        task.error_category = "timeout"
                    else:
                        task.status = TaskStatus.QUEUED
                        task.available_at = datetime.now(UTC) + timedelta(seconds=5)
                elif (
                    task.kind == TaskKind.RENDER
                    and task.checkpoint.get("render_manifest")
                    and isinstance(exc, (RenderError, StorageError, OSError))
                    and task.attempts < task.max_attempts
                ):
                    task.status = TaskStatus.QUEUED
                    task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempts)
                elif (
                    isinstance(exc, GenerationUnavailable)
                    and task.checkpoint.get("provider_job")
                    and task.attempts < task.max_attempts
                ):
                    task.attempts += 1
                    task.status = TaskStatus.QUEUED
                    task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempts)
                else:
                    uncertain = (
                        task.kind == TaskKind.QUALITY
                        or (
                            task.kind == TaskKind.RENDER
                            and bool(task.checkpoint.get("render_manifest"))
                        )
                        or isinstance(
                            exc,
                            (
                                ReviewRequired,
                                AudioConflict,
                                TTSOutcomeUnknown,
                                GenerationSubmissionUnknown,
                                SQLAlchemyError,
                            ),
                        )
                    )
                    task.status = TaskStatus.NEEDS_REVIEW if uncertain else TaskStatus.FAILED
                    task.error = "execution_requires_review" if uncertain else "execution_failed"
                if task.status != TaskStatus.QUEUED:
                    task.completed_at = datetime.now(UTC)
                db.add(task)
                db.commit()
                logger.warning(
                    "Task execution stopped",
                    extra={
                        "task_id": str(identifier),
                        "status": task.status.value,
                        "event": "task_stopped",
                        "error_category": task.error_category,
                        "error_type": type(exc).__name__,
                        "attempts": task.attempts,
                    },
                )
