from uuid import UUID

from sqlmodel import Session

from app.modules.tasks.models import Task


def test_admin_read_and_correlation_in_postgres(
    pg_client, postgres_engine, settings, story_factory
):
    h, path, _, _ = story_factory(pg_client)
    settings.tasks_eager = False
    response = pg_client.post(path + "/script/generate", headers=h | {"X-Request-ID": "pg-trace"})
    assert response.status_code == 202
    with Session(postgres_engine) as db:
        task = db.get(Task, UUID(response.json()["id"]))
        assert task.request_id == "pg-trace"
        task.error_category = "provider"
        db.add(task)
        db.commit()
    page = pg_client.get("/api/v1/admin/jobs?kind=story", headers=h).json()
    assert page["total"] == 1
    assert page["items"][0]["request_id"] == "pg-trace"
    assert page["items"][0]["error_category"] == "provider"
    assert "parameters" not in page["items"][0]
