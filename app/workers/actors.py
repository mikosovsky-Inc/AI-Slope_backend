import dramatiq

from app.workers.queue import broker


def execute(task_id: str) -> None:
    from app.db.session import get_engine
    from app.workers.runner import run_task

    run_task(get_engine(), task_id)


# Retries are durable DB state, not Dramatiq's default automatic paid-call retries.
actors = {
    queue: dramatiq.actor(
        execute,
        actor_name=f"execute_{queue}",
        queue_name=queue,
        broker=broker,
        max_retries=0,
        time_limit=3600000,
    )
    for queue in ("content", "research", "image", "video", "audio", "render", "quality")
}
