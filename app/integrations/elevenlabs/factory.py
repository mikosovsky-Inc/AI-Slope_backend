from app.core.config import Settings
from app.integrations.elevenlabs.mock import MockTTSProvider
from app.integrations.elevenlabs.provider import ElevenLabsProvider
from app.shared.tts import TTSProvider


def create_tts_provider(settings: Settings) -> TTSProvider:
    if settings.external_providers_mode == "mock":
        return MockTTSProvider()
    return ElevenLabsProvider(settings)
