from decimal import Decimal
from uuid import UUID

import pytest
from sqlmodel import select

from app.modules.costs.models import CostEvent
from app.modules.costs.service import BudgetExceeded, BudgetService
from app.modules.costs.visual import prepare_visual
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Scene, Video, VisualType


def prepare(client, db, story_factory):
    h, path, identifier, _ = story_factory(client)
    client.post(path + "/script/generate", headers=h).raise_for_status()
    owner = UUID(client.get("/api/v1/auth/me", headers=h).json()["id"])
    return h, path, owner, db.get(Video, UUID(identifier)), db.exec(select(Scene)).first()


def reserve(db, video, amount, key="test"):
    result = BudgetService.reserve(
        db,
        video.id,
        key=key,
        amount=Decimal(amount),
        provider="test",
        operation="test",
        model="test",
    )
    db.commit()
    return result


def test_reservations_idempotency_actual_zero_and_limit(client, db, story_factory):
    _, _, _, video, _ = prepare(client, db, story_factory)
    first = reserve(db, video, "0.15")
    assert reserve(db, video, "0.15").id == first.id
    assert BudgetService.remaining(db, video) == Decimal("0.05")
    with pytest.raises(BudgetExceeded):
        reserve(db, video, "0.06", "too-much")
    db.rollback()
    assert len(db.exec(select(CostEvent)).all()) == 1
    first.actual_cost_usd = Decimal(0)
    db.add(first)
    db.commit()
    assert BudgetService.remaining(db, video) == Decimal("0.20")
    for amount in ("-1", "NaN", "Infinity"):
        with pytest.raises(ValueError):
            BudgetService.can_spend(db, video, Decimal(amount))


def test_director_uses_remaining_budget(client, db, story_factory):
    h, path, _, video, _ = prepare(client, db, story_factory)
    reserve(db, video, "0.175")
    response = client.post(path + "/direct", headers=h)
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["visual_budget_usd"]) == Decimal("0.025")
    assert all(s["visual_type"] == "image" for s in response.json()["scenes"])


def test_visual_fallback_is_persisted_and_replay_does_not_charge_twice(
    client, db, story_factory, settings
):
    _, _, owner, video, scene = prepare(client, db, story_factory)
    reserve(db, video, "0.19")
    scene.visual_type = VisualType.VIDEO
    task = Task(
        owner_id=owner,
        video_id=video.id,
        scene_id=scene.id,
        kind=TaskKind.VIDEO,
        idempotency_key="visual",
    )
    db.add(scene)
    db.add(task)
    db.commit()
    live = settings.model_copy(update={"external_providers_mode": "live"})
    prepare_visual(db, task, live)
    assert scene.visual_type == VisualType.IMAGE and scene.camera_motion == "zoom_in"
    assert task.kind == TaskKind.VIDEO  # Original HTTP idempotency payload remains unchanged.
    assert task.checkpoint["budget_visual_kind"] == "image"
    prepare_visual(db, task, live)
    assert BudgetService.spent(db, video.id) == Decimal("0.195")
    from app.modules.render.visuals import output_key

    assert output_key(task).endswith(".png")


def test_budget_exhaustion_and_qc_do_not_silently_change_visual_type(
    client, db, story_factory, settings
):
    _, _, owner, video, scene = prepare(client, db, story_factory)
    reserve(db, video, "0.19")
    scene.visual_type = VisualType.VIDEO
    task = Task(
        owner_id=owner,
        video_id=video.id,
        scene_id=scene.id,
        kind=TaskKind.VIDEO,
        idempotency_key="qc",
        parameters={"quality_check_id": "guard-only"},
    )
    db.add(scene)
    db.add(task)
    db.commit()
    with pytest.raises(BudgetExceeded):
        prepare_visual(db, task, settings.model_copy(update={"external_providers_mode": "live"}))
    db.rollback()
    assert scene.visual_type == VisualType.VIDEO
    assert len(db.exec(select(CostEvent)).all()) == 1


def test_tts_budget_blocks_before_provider_and_generation_job(
    client, db, story_factory, settings, tmp_path
):
    from app.integrations.storage.local import LocalStorageProvider
    from app.modules.assets.models import GenerationJob
    from app.modules.audio.service import generate_scene_audio

    class NeverCalled:
        name = "elevenlabs"
        model = "test"

        def synthesize(self, request):
            pytest.fail("Provider must not be called")

    _, _, owner, video, scene = prepare(client, db, story_factory)
    reserve(db, video, "0.20")
    configured = settings.model_copy(
        update={"tts_usd_per_1000_characters": Decimal(1), "elevenlabs_voice_id": "test-voice"}
    )
    with pytest.raises(BudgetExceeded):
        generate_scene_audio(
            db, owner, video.id, scene.id, NeverCalled(), LocalStorageProvider(tmp_path), configured
        )
    assert not db.exec(select(GenerationJob)).all()


def test_llm_reserves_each_call_including_retry_allowance(client, db, story_factory, settings):
    from pydantic import BaseModel

    from app.modules.costs.llm import BudgetedLLM
    from app.shared.llm import LLMRequest

    class Output(BaseModel):
        value: str

    class Fails:
        calls = 0

        def generate(self, request, model):
            self.calls += 1
            raise RuntimeError("Provider failed")

    _, _, _, video, _ = prepare(client, db, story_factory)
    settings = settings.model_copy(
        update={
            "external_providers_mode": "live",
            "llm_request_estimate_usd": Decimal("0.04"),
            "openai_max_retries": 2,
        }
    )
    provider = Fails()
    wrapper = BudgetedLLM(provider, db, video.id, settings)
    request = LLMRequest(instructions="test", prompt="test")
    with pytest.raises(RuntimeError):
        wrapper.generate(request, Output)
    with pytest.raises(BudgetExceeded):
        wrapper.generate(request, Output)
    db.rollback()
    assert provider.calls == 1
    assert BudgetService.spent(db, video.id) == Decimal("0.12")


def test_worker_marks_exhausted_budget_without_provider_call(
    client, db, story_factory, settings, monkeypatch
):
    from app.workers.runner import run_task

    _, _, owner, video, scene = prepare(client, db, story_factory)
    reserve(db, video, "0.20")
    task = Task(
        owner_id=owner,
        video_id=video.id,
        scene_id=scene.id,
        kind=TaskKind.IMAGE,
        idempotency_key="blocked",
    )
    db.add(task)
    db.commit()

    class NeverCalled:
        def close(self):
            pass

        def generate_image(self, request):
            pytest.fail("Provider must not be called")

    monkeypatch.setattr(
        "app.workers.operations.create_generation_provider", lambda _: NeverCalled()
    )
    run_task(
        db.get_bind(),
        str(task.id),
        settings=settings.model_copy(update={"external_providers_mode": "live"}),
    )
    db.expire_all()
    assert db.get(Task, task.id).status == TaskStatus.FAILED
    assert db.get(Task, task.id).error == "budget_exceeded"


def test_budget_endpoint_is_owned_and_reports_commitments(client, db, story_factory):
    h, path, _, video, _ = prepare(client, db, story_factory)
    reserve(db, video, "0.15")
    assert client.get(path + "/budget").status_code == 401
    result = client.get(path + "/budget", headers=h)
    result.raise_for_status()
    assert Decimal(result.json()["remaining_usd"]) == Decimal("0.05")
    credentials = {"email": "budget-other@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    assert (
        client.get(path + "/budget", headers={"Authorization": "Bearer " + token}).status_code
        == 404
    )


def test_optional_llm_estimate_from_environment(settings, monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("LLM_REQUEST_ESTIMATE_USD", "null")
    values = {"database_url": settings.database_url, "jwt_secret_key": settings.jwt_secret_key}
    assert Settings(_env_file=None, **values).llm_request_estimate_usd is None
    monkeypatch.setenv("LLM_REQUEST_ESTIMATE_USD", "0.01")
    assert Settings(_env_file=None, **values).llm_request_estimate_usd == Decimal("0.01")


def test_visual_fallback_submits_image_and_only_polls_on_replay(
    client, db, story_factory, settings
):
    from app.shared.generation import GenerationJobRef, GenerationResult, ProviderJobStatus
    from app.workers.operations import PollLater, visual

    class Provider:
        submissions = 0
        polls = 0
        job = GenerationJobRef(provider="runpod", endpoint_id="test", job_id="job")

        def generate_image(self, request):
            self.submissions += 1
            assert request.parameters["output_key"].endswith(".png")
            return GenerationResult(job=self.job, status=ProviderJobStatus.QUEUED)

        def generate_video(self, request):
            pytest.fail("Budget fallback must submit an image")

        def get_status(self, job):
            self.polls += 1
            return GenerationResult(job=self.job, status=ProviderJobStatus.RUNNING)

    _, _, owner, video, scene = prepare(client, db, story_factory)
    reserve(db, video, "0.19")
    scene.visual_type = VisualType.VIDEO
    task = Task(
        owner_id=owner,
        video_id=video.id,
        scene_id=scene.id,
        kind=TaskKind.VIDEO,
        idempotency_key="dispatch-fallback",
    )
    db.add(scene)
    db.add(task)
    db.commit()
    provider = Provider()
    live = settings.model_copy(update={"external_providers_mode": "live"})
    for _ in range(2):
        with pytest.raises(PollLater):
            visual(db, task, provider, live)
    assert provider.submissions == provider.polls == 1
    assert BudgetService.spent(db, video.id) == Decimal("0.195")
