import base64
import json
import wave
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlmodel import select

from app.integrations.elevenlabs.factory import create_tts_provider
from app.integrations.elevenlabs.mock import MockTTSProvider
from app.integrations.elevenlabs.provider import ElevenLabsProvider
from app.integrations.storage.local import LocalStorageProvider
from app.modules.assets.models import Asset, GenerationJob, GenerationStatus
from app.modules.audio.pricing import ConfiguredTTSPricing
from app.modules.audio.service import AudioConflict, generate_scene_audio
from app.modules.costs.models import CostEvent
from app.modules.scripts.service import ScriptNotFound
from app.modules.videos.models import Scene
from app.shared.tts import CharacterAlignment, TTSOutcomeUnknown, TTSRejected, TTSRequest


@pytest.fixture
def tts_request():
    return TTSRequest(text="Hello", voice_id="voice-1", language="en")


@pytest.fixture
def live_settings(settings):
    return settings.model_copy(
        update={
            "elevenlabs_api_key": SecretStr("test-secret"),
            "elevenlabs_model": "configured-model",
        }
    )


def test_mock_produces_valid_audio_and_alignment(tts_request):
    result = MockTTSProvider().synthesize(tts_request)
    with wave.open(BytesIO(result.audio)) as audio:
        assert audio.getframerate() == 16000
        assert audio.getnframes() == 4800
        assert audio.getnchannels() == 1
    assert "".join(result.alignment.characters) == tts_request.text
    assert result.billed_characters == 0
    assert "audio" not in result.model_dump()


def test_elevenlabs_request_and_timestamps(live_settings, tts_request):
    def handle(request):
        assert request.url.path == "/v1/text-to-speech/voice-1/with-timestamps"
        assert request.headers["xi-api-key"] == "test-secret"
        body = json.loads(request.content)
        assert body["model_id"] == "configured-model"
        assert body["language_code"] == "en"
        assert body["voice_settings"]["speed"] == 1
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"ID3test").decode(),
                "alignment": {
                    "characters": ["H"],
                    "character_start_times_seconds": [0],
                    "character_end_times_seconds": [0.1],
                },
                "normalized_alignment": None,
            },
            headers={"request-id": "request-1", "character-cost": "5"},
        )

    provider = ElevenLabsProvider(live_settings, transport=httpx.MockTransport(handle))
    try:
        result = provider.synthesize(tts_request)
        assert result.audio == b"ID3test"
        assert result.billed_characters == 5
        assert result.request_id == "request-1"
    finally:
        provider.close()


@pytest.mark.parametrize(
    "variant", ["timeout", "500", "json", "base64", "empty", "format", "alignment", "large"]
)
def test_invalid_or_uncertain_response_is_not_retried(live_settings, tts_request, variant):
    calls = []

    def handle(request):
        calls.append(request)
        if variant == "timeout":
            raise httpx.ReadTimeout("secret")
        if variant == "500":
            return httpx.Response(500, text="secret")
        if variant == "json":
            return httpx.Response(200, text="not json")
        if variant == "large":
            return httpx.Response(200, content=b"x" * 2048)
        payload = {"audio_base64": base64.b64encode(b"ID3test").decode()}
        if variant == "base64":
            payload["audio_base64"] = "!"
        if variant == "empty":
            payload["audio_base64"] = ""
        if variant == "format":
            payload["audio_base64"] = base64.b64encode(b"html").decode()
        if variant == "alignment":
            payload["alignment"] = {
                "characters": ["x"],
                "character_start_times_seconds": [],
                "character_end_times_seconds": [],
            }
        return httpx.Response(200, json=payload)

    provider = ElevenLabsProvider(
        live_settings.model_copy(update={"elevenlabs_max_response_bytes": 1024}),
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(TTSOutcomeUnknown) as error:
            provider.synthesize(tts_request)
        assert "secret" not in str(error.value)
        assert len(calls) == 1
    finally:
        provider.close()


@pytest.mark.parametrize("code", [302, 401, 403, 422, 429])
def test_rejected_call(live_settings, tts_request, code):
    provider = ElevenLabsProvider(
        live_settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(code, headers={"location": "https://evil.example"})
        ),
    )
    try:
        with pytest.raises(TTSRejected):
            provider.synthesize(tts_request)
    finally:
        provider.close()


def test_optional_language_and_alignment(live_settings, tts_request):
    def handle(request):
        assert "language_code" not in json.loads(request.content)
        return httpx.Response(200, json={"audio_base64": base64.b64encode(b"ID3test").decode()})

    provider = ElevenLabsProvider(
        live_settings.model_copy(update={"elevenlabs_send_language_code": False}),
        transport=httpx.MockTransport(handle),
    )
    try:
        result = provider.synthesize(tts_request)
        assert result.alignment is None and result.billed_characters is None
    finally:
        provider.close()


def test_pricing_and_alignment_validation():
    assert ConfiguredTTSPricing(Decimal("0.123456")).estimate(1) == Decimal("0.000124")
    with pytest.raises(ValidationError):
        CharacterAlignment(
            characters=["x"], character_start_times_seconds=[1], character_end_times_seconds=[0]
        )
    with pytest.raises(ValidationError):
        TTSRequest(text=" ", voice_id="../escape", language="xx")


def prepare(client, db, story_factory):
    headers, path, video, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    owner = UUID(client.get("/api/v1/auth/me", headers=headers).json()["id"])
    return owner, UUID(video), db.exec(select(Scene)).first().id


def test_audio_asset_and_cost_replay(client, db, settings, tmp_path, story_factory):
    owner, video, scene = prepare(client, db, story_factory)
    storage = LocalStorageProvider(tmp_path)
    provider = create_tts_provider(settings)
    asset = generate_scene_audio(db, owner, video, scene, provider, storage, settings)
    replay = generate_scene_audio(db, owner, video, scene, provider, storage, settings)
    assert asset.id == replay.id
    assert (tmp_path / asset.object_key).read_bytes().startswith(b"RIFF")
    assert asset.metadata_json["alignment"] is not None
    assert len(db.exec(select(Asset)).all()) == 1
    cost = db.exec(select(CostEvent)).one()
    assert cost.actual_cost_usd == 0
    assert cost.metadata_json["billed_characters"] == 0
    assert db.exec(select(GenerationJob)).one().status == GenerationStatus.SUCCEEDED


def test_audio_ownership_before_external_calls(client, db, settings, tmp_path, story_factory):
    owner, video, scene = prepare(client, db, story_factory)
    for user, scene_id in [(uuid4(), scene), (owner, uuid4())]:
        with pytest.raises(ScriptNotFound):
            generate_scene_audio(
                db,
                user,
                video,
                scene_id,
                MockTTSProvider(),
                LocalStorageProvider(tmp_path),
                settings,
            )
    assert db.exec(select(GenerationJob)).all() == []


def test_failed_provider_is_durable_and_replay_blocked(
    client, db, settings, tmp_path, story_factory
):
    class FailingProvider(MockTTSProvider):
        def synthesize(self, request):
            raise TTSOutcomeUnknown("provider timed out")

    owner, video, scene = prepare(client, db, story_factory)
    storage = LocalStorageProvider(tmp_path)
    with pytest.raises(TTSOutcomeUnknown):
        generate_scene_audio(db, owner, video, scene, FailingProvider(), storage, settings)
    with pytest.raises(AudioConflict):
        generate_scene_audio(db, owner, video, scene, FailingProvider(), storage, settings)
    assert db.exec(select(GenerationJob)).one().status == GenerationStatus.FAILED
    assert len(db.exec(select(CostEvent)).all()) == 1
    assert db.exec(select(Asset)).all() == []


def test_storage_failure_preserves_provider_usage(client, db, settings, tmp_path, story_factory):
    owner, video, scene = prepare(client, db, story_factory)
    storage = LocalStorageProvider(tmp_path, max_bytes=1)
    with pytest.raises(ValueError):
        generate_scene_audio(db, owner, video, scene, MockTTSProvider(), storage, settings)
    assert db.exec(select(CostEvent)).one().metadata_json["outcome"] == "provider_completed"
    assert db.exec(select(Asset)).all() == []
    assert list(tmp_path.rglob("*.wav")) == []


def test_live_cost_is_estimate_not_actual_bill(client, db, settings, tmp_path, story_factory):
    class ReportedUsageProvider(MockTTSProvider):
        name = "elevenlabs"
        model = "test-model"

        def synthesize(self, request):
            return (
                super()
                .synthesize(request)
                .model_copy(update={"billed_characters": 17, "request_id": "remote-id"})
            )

    owner, video, scene = prepare(client, db, story_factory)
    configured = settings.model_copy(
        update={"tts_usd_per_1000_characters": Decimal("0.25"), "elevenlabs_voice_id": "voice"}
    )
    generate_scene_audio(
        db, owner, video, scene, ReportedUsageProvider(), LocalStorageProvider(tmp_path), configured
    )
    cost = db.exec(select(CostEvent)).one()
    assert cost.estimated_cost_usd > 0
    assert cost.actual_cost_usd is None
    assert cost.metadata_json["billed_characters"] == 17
    assert cost.metadata_json["provider_request_id"] == "remote-id"


def test_live_requires_explicit_price(client, db, settings, tmp_path, story_factory):
    class NeverCalledProvider(MockTTSProvider):
        name = "elevenlabs"

        def synthesize(self, request):
            pytest.fail("Unpriced call must not happen")

    owner, video, scene = prepare(client, db, story_factory)
    with pytest.raises(AudioConflict):
        generate_scene_audio(
            db,
            owner,
            video,
            scene,
            NeverCalledProvider(),
            LocalStorageProvider(tmp_path),
            settings.model_copy(update={"elevenlabs_voice_id": "voice"}),
        )
    assert db.exec(select(CostEvent)).all() == []


def test_tts_config_and_factory(settings, tmp_path, monkeypatch, tts_request):
    from app.core.config import Settings

    for key in ("ELEVENLABS_API_KEY", "ELEVENLABS_MODEL", "TTS_USD_PER_1000_CHARACTERS"):
        monkeypatch.delenv(key, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ELEVENLABS_API_KEY=dotenv-secret\nELEVENLABS_MODEL=test-model\nTTS_USD_PER_1000_CHARACTERS=0.25\n"
    )
    config = Settings(
        _env_file=dotenv,
        database_url="postgresql+psycopg://test:test@localhost/test",
        jwt_secret_key="x" * 48,
    )
    assert config.elevenlabs_api_key.get_secret_value() == "dotenv-secret"
    assert "dotenv-secret" not in repr(config)
    assert config.tts_usd_per_1000_characters == Decimal("0.25")
    live = create_tts_provider(config.model_copy(update={"external_providers_mode": "live"}))
    assert isinstance(live, ElevenLabsProvider)
    live.close()

    def unexpected(request):
        pytest.fail("Missing credentials must not call network")

    provider = ElevenLabsProvider(settings, transport=httpx.MockTransport(unexpected))
    try:
        with pytest.raises(TTSRejected):
            provider.synthesize(tts_request)
    finally:
        provider.close()
