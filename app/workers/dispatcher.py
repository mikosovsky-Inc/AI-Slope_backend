import logging
import signal
import threading
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine
from app.modules.tasks.models import QUEUES, Task, TaskStatus

logger = logging.getLogger("app.dispatcher")


def dispatch_once(engine, send, *, redelivery_seconds: int = 30) -> int:
    now = datetime.now(UTC)
    count = 0
    with Session(engine) as db:
        rows = db.exec(
            select(Task)
            .where(
                Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                Task.available_at <= now,
                Task.delivery_after <= now,
            )
            .order_by(Task.available_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        for task in rows:
            # Enqueue then commit. A crash can duplicate a message, never lose a DB task.
            send(QUEUES[task.kind], str(task.id))
            task.delivery_after = now + timedelta(seconds=redelivery_seconds)
            db.add(task)
            count += 1
        db.commit()
    return count


def main() -> None:
    from app.workers.actors import actors
    from app.workers.queue import broker

    settings = get_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                dispatch_once(get_engine(), lambda queue, task_id: actors[queue].send(task_id))
            except Exception as exc:
                logger.warning(
                    "Task dispatch unavailable", extra={"error_type": type(exc).__name__}
                )
            stop.wait(settings.task_dispatch_interval_seconds)
    finally:
        broker.close()
        get_engine().dispose()


if __name__ == "__main__":
    main()
