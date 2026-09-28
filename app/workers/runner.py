import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.user import User
from app.modules.audio.service import AudioConflict
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.service import enqueue
from app.shared.generation import GenerationSubmissionUnknown, GenerationUnavailable
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
                        and task.kind not in (TaskKind.AUDIO, TaskKind.DIRECT)
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
                db.add(task)
                db.commit()
            except Exception as exc:
                db.rollback()
                task = db.get(Task, identifier)
                if task is None or task.run_token != token:
                    return
                if isinstance(exc, PollLater):
                    if datetime.now(UTC) - task.started_at.replace(tzinfo=UTC) > timedelta(
                        milliseconds=settings.runpod_job_ttl_ms
                    ):
                        task.status, task.error = TaskStatus.NEEDS_REVIEW, "provider_poll_deadline"
                    else:
                        task.status = TaskStatus.QUEUED
                        task.available_at = datetime.now(UTC) + timedelta(seconds=5)
                elif (
                    isinstance(exc, GenerationUnavailable)
                    and task.checkpoint.get("provider_job")
                    and task.attempts < task.max_attempts
                ):
                    task.attempts += 1
                    task.status = TaskStatus.QUEUED
                    task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempts)
                else:
                    uncertain = isinstance(
                        exc,
                        (
                            ReviewRequired,
                            AudioConflict,
                            TTSOutcomeUnknown,
                            GenerationSubmissionUnknown,
                            SQLAlchemyError,
                        ),
                    )
                    task.status = TaskStatus.NEEDS_REVIEW if uncertain else TaskStatus.FAILED
                    task.error = "execution_requires_review" if uncertain else "execution_failed"
                if task.status != TaskStatus.QUEUED:
                    task.completed_at = datetime.now(UTC)
                db.add(task)
                db.commit()
                logger.warning(
                    "Task execution stopped",
                    extra={"task_id": str(identifier), "status": task.status.value},
                )
