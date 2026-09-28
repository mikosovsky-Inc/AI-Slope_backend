from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

from sqlalchemy import event, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.modules.research.models import ResearchDocument, ResearchFact, SceneResearchFact
from app.modules.research.provider import LocalResearchProvider, get_research_provider
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatus


def test_top5_concurrent_research_persistence_and_cascade(
    pg_client, postgres_engine, top5_factory, source_documents
):
    headers, path, video_id, channel_path = top5_factory(pg_client)
    started, release = Event(), Event()

    class Provider(LocalResearchProvider):
        def search(self, query):
            started.set()
            assert release.wait(timeout=15)
            return super().search(query)

    pg_client.app.dependency_overrides[get_research_provider] = lambda: Provider(source_documents)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(pg_client.post, path + "/research", headers=headers)
        try:
            assert started.wait(timeout=10)
            assert pg_client.post(path + "/research", headers=headers).status_code == 409
        finally:
            release.set()
        response = first.result(timeout=20)
    assert response.status_code == 200, response.text
    result = pg_client.post(path + "/top5-script/generate", headers=headers)
    assert result.status_code == 200, result.text
    assert pg_client.get(path + "/script", headers=headers).json() == result.json()
    assert pg_client.post(path + "/top5-script/generate", headers=headers).json() == result.json()
    with Session(postgres_engine) as db:
        assert db.get(Video, UUID(video_id)).status == VideoStatus.SCRIPT_READY
        assert db.exec(select(func.count()).select_from(SceneResearchFact)).one() == 5
    assert pg_client.delete(channel_path, headers=headers).status_code == 204
    with Session(postgres_engine) as db:
        for model in [ResearchDocument, ResearchFact, SceneResearchFact]:
            assert db.exec(select(func.count()).select_from(model)).one() == 0


def test_top5_citation_failure_rolls_back_script(
    pg_client, postgres_engine, top5_factory, source_documents
):
    headers, path, video_id, _ = top5_factory(pg_client)
    pg_client.app.dependency_overrides[get_research_provider] = lambda: LocalResearchProvider(
        source_documents
    )
    pg_client.post(path + "/research", headers=headers).raise_for_status()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO scene_research_facts"):
            raise SQLAlchemyError("Simulated citation storage error")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        assert pg_client.post(path + "/top5-script/generate", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    with Session(postgres_engine) as db:
        assert db.get(Video, UUID(video_id)).status == VideoStatus.FAILED
        for model in [VideoScript, Scene, SceneResearchFact]:
            assert db.exec(select(func.count()).select_from(model)).one() == 0
        assert db.exec(select(func.count()).select_from(ResearchFact)).one() == 6
