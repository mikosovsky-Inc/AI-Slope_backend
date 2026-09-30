"""Bounded HTTP labels and durable, cross-process task snapshots."""

from collections import Counter
from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, Histogram, generate_latest
from prometheus_client import Counter as PrometheusCounter
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.observability import ErrorCategory
from app.modules.tasks.models import QUEUES, Task, TaskKind, TaskStatus
from app.modules.videos.models import Video, VideoStatus


class HTTPMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.requests = PrometheusCounter(
            "ai_slop_http_requests_total",
            "Completed HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "ai_slop_http_request_duration_seconds",
            "HTTP response duration",
            ["method", "route"],
            buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
            registry=self.registry,
        )

    def observe(self, method: str, route: str, status: int, elapsed: float):
        method = (
            method
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
            else "OTHER"
        )
        self.requests.labels(method, route, str(status)).inc()
        self.duration.labels(method, route).observe(elapsed)


def seconds_since(value: datetime | None, now: datetime) -> float:
    if value is None:
        return 0
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return max(0, (now - value).total_seconds())


def database_metrics(db: Session) -> list[GaugeMetricFamily]:
    now = datetime.now(UTC)
    counts = {
        (kind, status): count
        for kind, status, count in db.exec(
            select(Task.kind, Task.status, func.count(Task.id)).group_by(Task.kind, Task.status)
        ).all()
    }
    tasks = GaugeMetricFamily(
        "ai_slop_tasks",
        "Current persisted tasks, including retained history",
        labels=["kind", "queue", "status"],
    )
    for kind in TaskKind:
        for status in TaskStatus:
            tasks.add_metric(
                [kind.value, QUEUES[kind], status.value], counts.get((kind, status), 0)
            )
    pending = GaugeMetricFamily(
        "ai_slop_task_overdue_seconds",
        "Age past earliest scheduled execution or running lease expiry",
        labels=["kind", "queue", "status"],
    )
    oldest = {
        (kind, status): timestamp
        for kind, status, timestamp in db.exec(
            select(Task.kind, Task.status, func.min(Task.available_at))
            .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]))
            .group_by(Task.kind, Task.status)
        ).all()
    }
    for kind in TaskKind:
        for status in (TaskStatus.QUEUED, TaskStatus.RUNNING):
            pending.add_metric(
                [kind.value, QUEUES[kind], status.value],
                seconds_since(oldest.get((kind, status)), now),
            )
    video_counts = dict(
        db.exec(select(Video.status, func.count(Video.id)).group_by(Video.status)).all()
    )
    videos = GaugeMetricFamily(
        "ai_slop_videos", "Current persisted video states", labels=["status"]
    )
    for status in VideoStatus:
        videos.add_metric([status.value], video_counts.get(status, 0))
    categories = {e.value for e in ErrorCategory} | {"authorization"}
    errors = Counter()
    for kind, category, count in db.exec(
        select(Task.kind, Task.error_category, func.count(Task.id))
        .where(Task.status.in_([TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW]))
        .group_by(Task.kind, Task.error_category)
    ).all():
        errors[kind, category if category in categories else "other"] += count
    failures = GaugeMetricFamily(
        "ai_slop_task_errors",
        "Retained failed or uncertain tasks by safe category",
        labels=["kind", "category"],
    )
    for kind in TaskKind:
        for category in sorted(categories | {"other"}):
            failures.add_metric([kind.value, category], errors[kind, category])
    return [tasks, pending, videos, failures]


def exposition(db: Session, http: HTTPMetrics) -> bytes:
    snapshot = database_metrics(db)

    class Snapshot:
        def collect(self):
            yield from http.registry.collect()
            yield from snapshot

    registry = CollectorRegistry()
    registry.register(Snapshot())
    return generate_latest(registry)
