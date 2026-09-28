import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.runpod.factory import create_generation_provider
from app.integrations.runpod.mock import MockRunpodProvider
from app.integrations.runpod.provider import RunpodProvider
from app.shared.generation import (
    GenerationInvalidOutput,
    GenerationJobRef,
    GenerationNotFound,
    GenerationRejected,
    GenerationSubmissionUnknown,
    GenerationUnavailable,
    ImageGenerationRequest,
    ProviderJobStatus,
    VideoGenerationRequest,
)


@pytest.fixture
def runpod_settings(settings):
    return Settings(
        **(
            settings.model_dump()
            | {
                "external_providers_mode": "live",
                "runpod_api_key": "secret-test-key",
                "runpod_image_endpoint_id": "image-endpoint",
                "runpod_video_endpoint_id": "video-endpoint",
                "runpod_image_model": "configured-image-model",
                "runpod_video_model": "configured-video-model",
            }
        ),
        _env_file=None,
    )


@pytest.fixture
def job():
    return GenerationJobRef(provider="runpod", endpoint_id="image-endpoint", job_id="job-1")


def test_mock_lifecycle_and_replay():
    provider = MockRunpodProvider()
    request = ImageGenerationRequest(request_id="request-1", prompt="A forest")
    submitted = provider.generate_image(request)
    assert submitted.status == ProviderJobStatus.QUEUED
    assert provider.generate_image(request) == submitted
    assert provider.get_status(submitted.job).status == ProviderJobStatus.RUNNING
    result = provider.get_status(submitted.job)
    assert result.status == ProviderJobStatus.SUCCEEDED
    assert result.output["mock"] is True
    result.output["mock"] = False
    assert provider.get_status(submitted.job).output["mock"] is True
    provider.cancel(submitted.job)
    assert provider.get_status(submitted.job).status == ProviderJobStatus.SUCCEEDED
    with pytest.raises(GenerationRejected):
        provider.generate_image(request.model_copy(update={"prompt": "Another prompt"}))
    provider.close()
    with pytest.raises(GenerationNotFound):
        provider.get_status(submitted.job)


def test_mock_video_cancel():
    provider = MockRunpodProvider()
    submitted = provider.generate_video(
        VideoGenerationRequest(
            request_id="video-1",
            prompt="A forest",
            duration_seconds=5,
        )
    )
    provider.cancel(submitted.job)
    provider.cancel(submitted.job)
    assert provider.get_status(submitted.job).status == ProviderJobStatus.CANCELLED


@pytest.mark.parametrize("kind", ["image", "video"])
def test_submit_contract_and_endpoint_selection(runpod_settings, kind):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})

    provider = RunpodProvider(runpod_settings, transport=httpx.MockTransport(handler))
    try:
        fields = {"request_id": "request-1", "prompt": "A forest", "parameters": {"steps": 12}}
        if kind == "image":
            result = provider.generate_image(ImageGenerationRequest(**fields))
        else:
            result = provider.generate_video(VideoGenerationRequest(**fields, duration_seconds=5))
        assert result.status == ProviderJobStatus.QUEUED
        request = requests[0]
        assert request.method == "POST"
        assert str(request.url) == f"https://api.runpod.ai/v2/{kind}-endpoint/run"
        assert request.headers["authorization"] == "Bearer secret-test-key"
        body = json.loads(request.content)
        assert body["input"]["model"] == f"configured-{kind}-model"
        assert body["input"]["parameters"] == {"steps": 12}
        assert body["input"]["request_id"] == "request-1"
        assert body["policy"]["executionTimeout"] == 600000
    finally:
        provider.close()


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("IN_QUEUE", "queued"),
        ("IN_PROGRESS", "running"),
        ("COMPLETED", "succeeded"),
        ("FAILED", "failed"),
        ("CANCELLED", "cancelled"),
        ("TIMED_OUT", "timed_out"),
    ],
)
def test_status_mapping(runpod_settings, job, remote, expected):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v2/image-endpoint/status/job-1"
        return httpx.Response(
            200,
            json={
                "id": "job-1",
                "status": remote,
                "output": {"url": "https://media.example/image.png"}
                if remote == "COMPLETED"
                else None,
                "error": "secret provider details",
            },
        )

    provider = RunpodProvider(runpod_settings, transport=httpx.MockTransport(handler))
    try:
        result = provider.get_status(job)
        assert result.status == expected
        assert "secret provider details" not in result.model_dump_json()
    finally:
        provider.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "job-1", "status": "COMPLETED"},
        {"id": "other-job", "status": "IN_QUEUE"},
        {"id": "job-1", "status": "UNRECOGNIZED"},
        [],
    ],
)
def test_bad_status_response(runpod_settings, job, payload):
    provider = RunpodProvider(
        runpod_settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    try:
        with pytest.raises(GenerationInvalidOutput):
            provider.get_status(job)
    finally:
        provider.close()


def test_status_retries_with_backoff(runpod_settings, job):
    calls, waits = [], []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("private details")
        return httpx.Response(503, text="secret")

    provider = RunpodProvider(
        runpod_settings, transport=httpx.MockTransport(handler), sleep=waits.append
    )
    try:
        with pytest.raises(GenerationUnavailable, match="Runpod temporarily unavailable"):
            provider.get_status(job)
        assert len(calls) == 3
        assert waits == [0.5, 1.0]
    finally:
        provider.close()


@pytest.mark.parametrize("failure", ["timeout", "server", "json", "schema", "oversize"])
def test_submission_is_not_retried_when_outcome_unknown(runpod_settings, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret transport error")
        if failure == "server":
            return httpx.Response(500)
        if failure == "json":
            return httpx.Response(200, text="invalid json")
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        return httpx.Response(200, json={"id": "job-1"})

    provider = RunpodProvider(runpod_settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GenerationSubmissionUnknown):
            provider.generate_image(ImageGenerationRequest(request_id="req", prompt="Forest"))
        assert len(calls) == 1
    finally:
        provider.close()


@pytest.mark.parametrize(
    "code,error",
    [
        (401, GenerationRejected),
        (403, GenerationRejected),
        (404, GenerationNotFound),
        (429, GenerationUnavailable),
        (302, GenerationRejected),
    ],
)
def test_non_success_is_safe_and_bounded(runpod_settings, code, error):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            code, text="secret response", headers={"Location": "https://evil.example"}
        )

    provider = RunpodProvider(runpod_settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(error) as exc:
            provider.generate_image(ImageGenerationRequest(request_id="req", prompt="Forest"))
        assert "secret" not in str(exc.value)
        assert len(calls) == 1
    finally:
        provider.close()


def test_cancel_and_durable_endpoint_reference(runpod_settings, job):
    paths = []

    def handler(request):
        paths.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "CANCELLED"})

    provider = RunpodProvider(
        runpod_settings.model_copy(update={"runpod_image_endpoint_id": "new"}),
        transport=httpx.MockTransport(handler),
    )
    try:
        provider.cancel(job)
        assert paths == [("POST", "/v2/image-endpoint/cancel/job-1")]
        with pytest.raises(GenerationRejected):
            provider.cancel(job.model_copy(update={"provider": "mock_runpod"}))
    finally:
        provider.close()


def test_factory_lifecycle_and_configuration(client, settings, runpod_settings):
    assert isinstance(client.app.state.generation, MockRunpodProvider)
    mock = create_generation_provider(settings)
    assert isinstance(mock, MockRunpodProvider)
    live = create_generation_provider(runpod_settings)
    assert isinstance(live, RunpodProvider)
    live.close()
    assert "secret-test-key" not in repr(runpod_settings)
    for override in [
        {"runpod_image_endpoint_id": "../escape"},
        {"runpod_timeout_seconds": 0},
        {"runpod_status_max_retries": 99},
        {"runpod_job_ttl_ms": 10000},
    ]:
        with pytest.raises(ValidationError):
            Settings(**(settings.model_dump() | override), _env_file=None)


def test_missing_credentials_never_calls_network(settings):
    def unexpected(request):
        pytest.fail("Unexpected network operation")

    provider = RunpodProvider(settings, transport=httpx.MockTransport(unexpected))
    try:
        with pytest.raises(GenerationRejected):
            provider.generate_image(ImageGenerationRequest(request_id="req", prompt="Forest"))
        with pytest.raises(GenerationRejected):
            provider.get_status(GenerationJobRef(provider="runpod", endpoint_id="ep", job_id="job"))
    finally:
        provider.close()


@pytest.mark.parametrize(
    "kwargs",
    [{"prompt": " "}, {"width": 0}, {"request_id": "../x"}, {"seed": -1}, {"unknown": "value"}],
)
def test_request_validation(kwargs):
    with pytest.raises(ValidationError):
        ImageGenerationRequest(**({"prompt": "Forest", "request_id": "req"} | kwargs))


def test_mock_replay_ignores_parameter_order():
    provider = MockRunpodProvider()
    first = ImageGenerationRequest(request_id="req", prompt="Forest", parameters={"a": 1, "b": 2})
    second = ImageGenerationRequest(request_id="req", prompt="Forest", parameters={"b": 2, "a": 1})
    assert provider.generate_image(first) == provider.generate_image(second)


def test_status_recovers_from_rate_limit(runpod_settings, job):
    calls, waits = [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429)
            if len(calls) == 1
            else httpx.Response(
                200, json={"id": "job-1", "status": "COMPLETED", "output": {"image": "result"}}
            )
        )

    provider = RunpodProvider(
        runpod_settings, transport=httpx.MockTransport(handler), sleep=waits.append
    )
    try:
        assert provider.get_status(job).status == ProviderJobStatus.SUCCEEDED
        assert waits == [0.5]
    finally:
        provider.close()


def test_large_input_is_rejected_before_submission(runpod_settings):
    def unexpected(request):
        pytest.fail("Unexpected network operation")

    provider = RunpodProvider(runpod_settings, transport=httpx.MockTransport(unexpected))
    try:
        with pytest.raises(GenerationRejected, match="too large"):
            provider.generate_image(
                ImageGenerationRequest(
                    request_id="req", prompt="Forest", parameters={"large": "x" * (256 * 1024)}
                )
            )
    finally:
        provider.close()


def test_runpod_settings_from_dotenv(tmp_path, monkeypatch):
    for name in ("RUNPOD_API_KEY", "RUNPOD_IMAGE_ENDPOINT_ID", "RUNPOD_VIDEO_ENDPOINT_ID"):
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "RUNPOD_API_KEY=dotenv-secret\nRUNPOD_IMAGE_ENDPOINT_ID=image-worker\n"
        "RUNPOD_VIDEO_ENDPOINT_ID=video-worker\n"
    )
    config = Settings(
        _env_file=dotenv,
        database_url="postgresql+psycopg://test:test@localhost/test",
        jwt_secret_key="x" * 48,
    )
    assert config.runpod_api_key.get_secret_value() == "dotenv-secret"
    assert config.runpod_image_endpoint_id == "image-worker"
    assert config.runpod_video_endpoint_id == "video-worker"
    assert "dotenv-secret" not in repr(config)
