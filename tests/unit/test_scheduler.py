from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.models.user import User
from app.modules.channels.models import AutopilotMode, Channel, ChannelStatus
from app.modules.ideas.models import ContentIdea, IdeaStatus
from app.modules.scheduler.models import DailyPlan, PlanStatus
from app.modules.scheduler.service import day_window, plan_channel, plan_once
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Video, VideoStatus
from app.workers.runner import run_task


@pytest.fixture
def channel_fixture(client, db):
    credentials = {"email": "planner@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    channel = client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "idea": "Fictional stories and historical facts",
            "language": "en",
            "videos_per_day": 2,
            "autopilot_mode": "semi_auto",
        },
    ).json()
    path = "/api/v1/channels/" + channel["id"]
    client.post(path + "/analyze", headers=headers).raise_for_status()
    client.post(path + "/activate", headers=headers).raise_for_status()
    return headers, path, db.get(Channel, UUID(channel["id"]))


def run(db, settings, task_id):
    run_task(db.get_bind(), str(task_id), settings=settings)
    db.expire_all()
    return db.get(Task, UUID(str(task_id)))


def generate(client, headers, path):
    response = client.post(path + "/ideas/generate", headers=headers)
    response.raise_for_status()
    return response.json()["items"]


def test_semi_auto_daily_deficit_and_restart(client, db, settings, channel_fixture):
    _, _, channel = channel_fixture
    result = plan_channel(db, channel.id, settings)
    assert result.status == PlanStatus.WAITING_TASK
    assert result.queued_idea_task_id
    assert not db.exec(select(Video)).all()
    assert plan_channel(db, channel.id, settings).queued_idea_task_id is None
    assert run(db, settings, result.queued_idea_task_id).status == TaskStatus.SUCCEEDED
    result = plan_channel(db, channel.id, settings)
    assert result.status == PlanStatus.COMPLETE
    assert len(result.created_video_ids) == 2
    assert result.videos_today == 2
    assert len(db.exec(select(DailyPlan)).all()) == 1
    video_tasks = db.exec(select(Task).where(Task.video_id.is_not(None))).all()
    assert len(video_tasks) == 2
    assert all(t.parameters["workflow"] for t in video_tasks)
    assert {t.kind for t in video_tasks} == {TaskKind.STORY, TaskKind.RESEARCH}
    # Workflows are handed to the existing workers; the planner does not publish.
    assert all(v.status == VideoStatus.IDEA_GENERATED for v in db.exec(select(Video)).all())
    assert plan_once(db.get_bind(), settings).videos_created == 0
    assert len(db.exec(select(Video)).all()) == 2
    assert len(db.exec(select(Task)).all()) == 3


def test_manual_waits_for_approval_without_growing_backlog(client, db, settings, channel_fixture):
    headers, path, channel = channel_fixture
    client.patch(path, headers=headers, json={"autopilot_mode": "manual"}).raise_for_status()
    result = plan_channel(db, channel.id, settings)
    run(db, settings, result.queued_idea_task_id)
    for _ in range(3):
        assert plan_channel(db, channel.id, settings).status == PlanStatus.WAITING_APPROVAL
    assert len(db.exec(select(Task)).all()) == 1
    assert not db.exec(select(Video)).all()
    idea = db.exec(select(ContentIdea)).first()
    client.post(f"/api/v1/ideas/{idea.id}/approve", headers=headers).raise_for_status()
    result = plan_channel(db, channel.id, settings)
    assert len(result.created_video_ids) == 1
    assert result.status == PlanStatus.WAITING_APPROVAL
    assert all(
        i.status == IdeaStatus.CANDIDATE
        for i in db.exec(select(ContentIdea).where(ContentIdea.id != idea.id)).all()
    )
    assert db.get(ContentIdea, idea.id).status == IdeaStatus.USED


def test_manual_and_failed_videos_count_and_frequency_changes(
    client, db, settings, channel_fixture
):
    headers, path, channel = channel_fixture
    idea = generate(client, headers, path)[0]
    idea_path = "/api/v1/ideas/" + idea["id"]
    client.post(idea_path + "/approve", headers=headers).raise_for_status()
    video = client.post(idea_path + "/create-video", headers=headers).json()
    existing = db.get(Video, UUID(video["id"]))
    existing.status = VideoStatus.FAILED
    db.add(existing)
    db.commit()
    result = plan_channel(db, channel.id, settings)
    assert len(result.created_video_ids) == 1 and result.videos_today == 2
    client.patch(path, headers=headers, json={"videos_per_day": 1}).raise_for_status()
    assert not plan_channel(db, channel.id, settings).created_video_ids
    client.patch(path, headers=headers, json={"videos_per_day": 3}).raise_for_status()
    result = plan_channel(db, channel.id, settings)
    assert len(result.created_video_ids) == 1 and result.videos_today == 3


@pytest.mark.parametrize("state", ["draft", "paused", "disabled_owner", "scheduler_disabled"])
def test_inactive_channels_never_plan(db, settings, channel_fixture, state):
    _, _, channel = channel_fixture
    if state == "disabled_owner":
        owner = db.get(User, channel.owner_id)
        owner.is_active = False
        db.add(owner)
    elif state == "scheduler_disabled":
        settings.scheduler_enabled = False
    else:
        channel.status = ChannelStatus(state)
        db.add(channel)
    db.commit()
    assert plan_channel(db, channel.id, settings) is None
    assert plan_once(db.get_bind(), settings).videos_created == 0
    assert not db.exec(select(Task)).all()
    assert not db.exec(select(DailyPlan)).all()


def test_needs_blueprint(db, settings, channel_fixture):
    from app.modules.channels.models import ContentPillar

    _, _, channel = channel_fixture
    for pillar in db.exec(select(ContentPillar)).all():
        db.delete(pillar)
    db.commit()
    assert plan_channel(db, channel.id, settings).reason == "blueprint_not_ready"
    assert not db.exec(select(Task)).all()


@pytest.mark.parametrize("state", ["paused", "expired", "disabled", "quota_reached"])
def test_queued_automatic_ideas_revalidate_before_provider(
    db, settings, channel_fixture, monkeypatch, state
):
    _, _, channel = channel_fixture
    result = plan_channel(db, channel.id, settings)
    task = db.get(Task, result.queued_idea_task_id)
    if state == "paused":
        channel.status = ChannelStatus.PAUSED
        db.add(channel)
    elif state == "disabled":
        settings.scheduler_enabled = False
    elif state == "expired":
        plan = db.exec(select(DailyPlan)).one()
        plan.window_start -= timedelta(days=1)
        plan.window_end -= timedelta(days=1)
        db.add(plan)
    else:
        # Another caller has filled the quota while the scheduled generation was queued.
        from app.integrations.llm.factory import create_llm_provider
        from app.modules.ideas.service import generate_ideas
        from app.modules.videos.service import create_video

        provider = create_llm_provider(settings)
        try:
            batch = generate_ideas(db, channel.owner_id, channel.id, 10, provider)
        finally:
            provider.close()
        for row in batch.items[:2]:
            idea = db.get(ContentIdea, row.id)
            idea.status = IdeaStatus.APPROVED
            db.add(idea)
            db.commit()
            create_video(db, channel.owner_id, idea.id)
    db.commit()

    def forbidden(_):
        raise AssertionError("Provider must not be opened")

    monkeypatch.setattr("app.workers.operations.create_llm_provider", forbidden)
    task = run(db, settings, task.id)
    assert task.status == TaskStatus.SUCCEEDED
    assert task.result["skipped"] is True


def test_failed_generation_and_batch_limit_do_not_loop(client, db, settings, channel_fixture):
    headers, path, channel = channel_fixture
    result = plan_channel(db, channel.id, settings)
    task = db.get(Task, result.queued_idea_task_id)
    task.status = TaskStatus.NEEDS_REVIEW
    db.add(task)
    db.commit()
    assert plan_channel(db, channel.id, settings).reason == "idea_generation_requires_review"
    assert len(db.exec(select(Task)).all()) == 1
    # Explicitly resolved failure in this fixture; rejection cannot grow the backlog forever.
    task.status = TaskStatus.QUEUED
    db.add(task)
    db.commit()
    run(db, settings, task.id)
    channel.autopilot_mode = AutopilotMode.MANUAL
    db.add(channel)
    db.commit()
    for idea in db.exec(select(ContentIdea)).all():
        idea.status = IdeaStatus.REJECTED
        db.add(idea)
    db.commit()
    second = plan_channel(db, channel.id, settings)
    assert second.queued_idea_task_id is not None
    run(db, settings, second.queued_idea_task_id)
    for idea in db.exec(select(ContentIdea)).all():
        idea.status = IdeaStatus.REJECTED
        db.add(idea)
    db.commit()
    assert plan_channel(db, channel.id, settings).reason == "daily_idea_batch_limit"
    assert len(db.exec(select(Task)).all()) == 2


def test_next_day_and_boundaries(client, db, settings, channel_fixture):
    headers, path, channel = channel_fixture
    generate(client, headers, path)
    first_time = datetime(2026, 3, 28, 23, 59, tzinfo=UTC)
    first = plan_channel(db, channel.id, settings, now=first_time)
    again = plan_channel(db, channel.id, settings, now=first_time)
    assert len(first.created_video_ids) == 2 and not again.created_video_ids
    next_day = plan_channel(db, channel.id, settings, now=first_time + timedelta(minutes=1))
    assert len(next_day.created_video_ids) == 2
    assert len(db.exec(select(DailyPlan)).all()) == 2


@pytest.mark.parametrize(
    "instant,hours",
    [
        (datetime(2026, 3, 29, 12, tzinfo=UTC), 23),
        (datetime(2026, 10, 25, 12, tzinfo=UTC), 25),
    ],
)
def test_timezone_dst_windows(instant, hours):
    day, start, end = day_window(instant, "Europe/Warsaw")
    assert (end - start).total_seconds() == hours * 3600
    assert day == instant.date()
    assert start <= instant < end


def test_timezone_configuration_and_naive_clock(settings):
    with pytest.raises(ValueError):
        day_window(datetime(2026, 1, 1), "UTC")
    with pytest.raises(ValidationError):
        type(settings)(
            **(settings.model_dump() | {"scheduler_timezone": "Invalid/Place"}), _env_file=None
        )


def test_scan_paginates_all_channels(client, db, settings, channel_fixture):
    headers, _, channel = channel_fixture
    for index in range(3):
        created = client.post(
            "/api/v1/channels",
            headers=headers,
            json={"idea": f"Other fictional channel {index}", "language": "en"},
        ).json()
        client.post(
            f"/api/v1/channels/{created['id']}/activate", headers=headers
        ).raise_for_status()
    settings.scheduler_batch_size = 1
    result = plan_once(db.get_bind(), settings)
    assert result.scanned == result.planned == 4
    assert result.idea_tasks_created == 1
    assert result.errors == 0


def test_24_video_quota_uses_bounded_batches(client, db, settings, channel_fixture):
    headers, path, channel = channel_fixture
    client.patch(path, headers=headers, json={"videos_per_day": 24}).raise_for_status()
    first = plan_channel(db, channel.id, settings)
    assert db.get(Task, first.queued_idea_task_id).parameters["count"] == 20
    run(db, settings, first.queued_idea_task_id)
    second = plan_channel(db, channel.id, settings)
    assert len(second.created_video_ids) == 20
    assert db.get(Task, second.queued_idea_task_id).parameters["count"] == 10
    run(db, settings, second.queued_idea_task_id)
    third = plan_channel(db, channel.id, settings)
    assert third.status == PlanStatus.COMPLETE
    assert len(third.created_video_ids) == 4 and third.videos_today == 24
    assert len(db.exec(select(DailyPlan)).one().idea_task_ids) == 2


def test_approved_ideas_take_priority_and_disabled_formats_are_skipped(
    client, db, settings, channel_fixture
):
    from app.modules.channels.models import ChannelBlueprint

    headers, path, channel = channel_fixture
    ideas = generate(client, headers, path)
    approved = next(i for i in ideas if i["format"] == "story")
    client.post(f"/api/v1/ideas/{approved['id']}/approve", headers=headers).raise_for_status()
    blueprint = db.exec(select(ChannelBlueprint)).one()
    blueprint.configuration = blueprint.configuration | {"formats": {"top5": 0, "story": 1}}
    db.add(blueprint)
    db.commit()
    result = plan_channel(db, channel.id, settings)
    assert result.status == PlanStatus.COMPLETE
    videos = db.exec(select(Video).order_by(Video.created_at, Video.id)).all()
    assert len(videos) == 2
    assert all(v.format.value == "story" for v in videos)
    assert UUID(approved["id"]) in {v.idea_id for v in videos}
