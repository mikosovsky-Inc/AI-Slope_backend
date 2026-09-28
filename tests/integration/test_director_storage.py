from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.modules.director.models import DirectorPlan
from app.modules.videos.models import Scene


def test_concurrent_direction_and_cascade(pg_client, postgres_engine, story_factory):
    headers, path, _, channel_path = story_factory(pg_client)
    pg_client.post(path + "/script/generate", headers=headers).raise_for_status()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pg_client.post, path + "/direct", headers=headers) for _ in range(2)]
        results = [f.result(timeout=20) for f in futures]
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    with Session(postgres_engine) as db:
        assert db.exec(select(func.count()).select_from(DirectorPlan)).one() == 1
    assert pg_client.delete(channel_path, headers=headers).status_code == 204
    with Session(postgres_engine) as db:
        assert db.exec(select(func.count()).select_from(DirectorPlan)).one() == 0


def test_direction_rollback_leaves_original_scenes(pg_client, postgres_engine, story_factory):
    headers, path, _, _ = story_factory(pg_client)
    script = pg_client.post(path + "/script/generate", headers=headers).json()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO director_plans"):
            raise SQLAlchemyError("Simulated write failure")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        assert pg_client.post(path + "/direct", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    assert pg_client.get(path + "/script", headers=headers).json() == script
    with Session(postgres_engine) as db:
        assert not db.exec(select(DirectorPlan)).all()
        assert all(
            s.importance is None and s.generation_priority is None
            for s in db.exec(select(Scene)).all()
        )
    assert pg_client.post(path + "/direct", headers=headers).status_code == 200
