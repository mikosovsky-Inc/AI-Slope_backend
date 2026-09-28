import base64
import binascii
import json

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.shared.tts import CharacterAlignment, TTSOutcomeUnknown, TTSRejected, TTSRequest, TTSResult


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.model = settings.elevenlabs_model
        self.client = httpx.Client(
            base_url="https://api.elevenlabs.io/v1/",
            timeout=settings.elevenlabs_timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def synthesize(self, request: TTSRequest) -> TTSResult:
        key = self.settings.elevenlabs_api_key
        if not key or not key.get_secret_value().strip() or not self.model:
            raise TTSRejected("ElevenLabs key and model are required")
        payload = {
            "text": request.text,
            "model_id": self.model,
            "voice_settings": request.settings.model_dump(),
        }
        if self.settings.elevenlabs_send_language_code:
            payload["language_code"] = request.language
        try:
            with self.client.stream(
                "POST",
                f"text-to-speech/{request.voice_id}/with-timestamps",
                params={"output_format": "mp3_44100_128"},
                json=payload,
                headers={"xi-api-key": key.get_secret_value()},
            ) as response:
                if 400 <= response.status_code < 500 or 300 <= response.status_code < 400:
                    raise TTSRejected("ElevenLabs request rejected")
                if not 200 <= response.status_code < 300:
                    raise TTSOutcomeUnknown("ElevenLabs outcome unknown")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > self.settings.elevenlabs_max_response_bytes:
                        raise TTSOutcomeUnknown("ElevenLabs response exceeds size limit")
                body = json.loads(data)
                audio = base64.b64decode(body["audio_base64"], validate=True)
                # Lightweight format sanity check; full decoding is part of media quality checks.
                if not (
                    audio.startswith(b"ID3")
                    or (len(audio) > 1 and audio[0] == 255 and audio[1] & 224 == 224)
                ):
                    raise ValueError("Invalid MP3 header")
                return TTSResult(
                    audio=audio,
                    content_type="audio/mpeg",
                    alignment=CharacterAlignment.model_validate(body["alignment"])
                    if body.get("alignment") is not None
                    else None,
                    normalized_alignment=CharacterAlignment.model_validate(
                        body["normalized_alignment"]
                    )
                    if body.get("normalized_alignment") is not None
                    else None,
                    request_id=response.headers.get("request-id"),
                    billed_characters=response.headers.get("character-cost"),
                )
        except (
            httpx.RequestError,
            ValueError,
            TypeError,
            KeyError,
            binascii.Error,
            RecursionError,
            ValidationError,
        ):
            raise TTSOutcomeUnknown("ElevenLabs outcome unknown or invalid result") from None

    def close(self) -> None:
        self.client.close()
