import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import get_settings
from app.modules.tasks.models import ALL_QUEUES


def build_broker() -> RedisBroker:
    settings = get_settings()
    broker = RedisBroker(
        url=str(settings.redis_url),
        namespace="ai_slop_tasks",
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
    )
    for queue in ALL_QUEUES:
        broker.declare_queue(queue)
    return broker


broker = build_broker()
dramatiq.set_broker(broker)
