from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.observability.schemas import AdminJob, AdminJobPage
from app.modules.tasks.models import QUEUES, Task, TaskKind, TaskStatus


def list_jobs(
    db: Session,
    *,
    status: TaskStatus | None,
    kind: TaskKind | None,
    video_id: UUID | None,
    limit: int,
    offset: int,
) -> AdminJobPage:
    filters = []
    if kind:
        filters.append(Task.kind == kind)
    if video_id:
        filters.append(Task.video_id == video_id)
    counts = dict(
        db.exec(
            select(Task.status, func.count(Task.id)).where(*filters).group_by(Task.status)
        ).all()
    )
    statuses = (
        [status]
        if status
        else [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW]
    )
    query = select(Task).where(*filters, Task.status.in_(statuses))
    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    tasks = db.exec(
        query.order_by(Task.created_at.desc(), Task.id.desc()).offset(offset).limit(limit)
    ).all()
    return AdminJobPage(
        items=[
            AdminJob(
                **t.model_dump(include=set(AdminJob.model_fields) - {"queue"}), queue=QUEUES[t.kind]
            )
            for t in tasks
        ],
        total=total,
        limit=limit,
        offset=offset,
        counts={s: counts.get(s, 0) for s in TaskStatus},
    )
