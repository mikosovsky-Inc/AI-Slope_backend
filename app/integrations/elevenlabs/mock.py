import wave
from io import BytesIO

from app.shared.tts import CharacterAlignment, TTSRequest, TTSResult


class MockTTSProvider:
    name = "mock_elevenlabs"
    model = "mock-silence-v1"

    def synthesize(self, request: TTSRequest) -> TTSResult:
        # Valid PCM WAV for offline pipelines, deliberately silent (not synthesized speech).
        step = 0.06 / request.settings.speed
        duration = len(request.text) * step
        stream = BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * max(1, round(duration * 16000)))
        return TTSResult(
            audio=stream.getvalue(),
            content_type="audio/wav",
            billed_characters=0,
            alignment=CharacterAlignment(
                characters=list(request.text),
                character_start_times_seconds=[i * step for i in range(len(request.text))],
                character_end_times_seconds=[(i + 1) * step for i in range(len(request.text))],
            ),
        )

    def close(self) -> None:
        pass
