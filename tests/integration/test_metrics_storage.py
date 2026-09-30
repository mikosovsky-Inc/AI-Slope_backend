from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlmodel import Session, select

from app.models.user import User
from app.modules.tasks.models import Task, TaskKind, TaskStatus


@pytest.fixture(autouse=True)
def metrics_settings(settings):
    settings.metrics_enabled = True
    settings.metrics_token = SecretStr("postgres-metrics-test-" + "x" * 48)


def test_metrics_aggregate_postgres_worker_state(
    pg_client, postgres_engine, story_factory, settings
):
    _, _, video, _ = story_factory(pg_client)
    with Session(postgres_engine) as db:
        owner = db.exec(select(User)).one()
        db.add(
            Task(
                owner_id=owner.id,
                video_id=UUID(video),
                kind=TaskKind.RENDER,
                status=TaskStatus.QUEUED,
                idempotency_key="metrics",
                available_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        db.commit()
    response = pg_client.get(
        "/metrics", headers={"Authorization": "Bearer " + settings.metrics_token.get_secret_value()}
    )
    assert response.status_code == 200
    assert 'ai_slop_tasks{kind="render",queue="render",status="queued"} 1.0' in response.text
    assert 'ai_slop_videos{status="IDEA_GENERATED"} 1.0' in response.text
    assert video not in response.text
