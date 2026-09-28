import json
import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings


class RenderError(Exception):
    """Safe FFmpeg boundary: do not expose command output or local paths."""


class MediaUnavailable(RenderError):
    """Local tool/configuration/deadline failure, not evidence of defective media."""


class InvalidMedia(RenderError):
    """Input cannot be probed as the expected media format."""


class RenderScene(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    visual: Path
    visual_type: Literal["image/png", "image/jpeg", "video/mp4"]
    audio: Path
    audio_type: Literal["audio/wav", "audio/mpeg"]
    duration: float = Field(ge=1 / 30, le=180)
    motion: Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "static"] = "static"
    narration: str = Field(max_length=4000)


DEMUXERS = {
    "image/png": "png_pipe",
    "image/jpeg": "jpeg_pipe",
    "video/mp4": "mov",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}


def media_input(path: Path, mime: str) -> list[str]:
    return ["-protocol_whitelist", "file,pipe", "-f", DEMUXERS[mime], "-i", str(path)]


class FFmpegRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.deadline: float | None = None

    def timeout(self, maximum: float) -> float:
        remaining = maximum if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise MediaUnavailable("Render deadline exceeded")
        return min(maximum, remaining)

    def run(self, args: list[str], *, cwd: Path | None = None) -> None:
        try:
            subprocess.run(
                [
                    self.settings.ffmpeg_binary,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-threads",
                    str(self.settings.render_threads),
                    "-filter_threads",
                    "1",
                    "-filter_complex_threads",
                    "1",
                    *args,
                ],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=self.timeout(self.settings.render_timeout_seconds),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise MediaUnavailable("Media tool unavailable or timed out") from None
        except subprocess.SubprocessError:
            raise RenderError("Media processing failed") from None

    def probe(self, path: Path, mime: str) -> dict:
        try:
            result = subprocess.run(
                [
                    self.settings.ffprobe_binary,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    DEMUXERS[mime],
                    "-show_entries",
                    "stream=codec_type,codec_name,width,height,channels,r_frame_rate,duration:"
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=self.timeout(20),
            )
            data = json.loads(result.stdout)
            for stream in data["streams"]:
                if (
                    stream.get("width", 0) > 8192
                    or stream.get("height", 0) > 8192
                    or stream.get("channels", 0) > 8
                ):
                    raise ValueError("Unsupported media dimensions")
            return data
        except (OSError, subprocess.TimeoutExpired):
            raise MediaUnavailable("Media tool unavailable or timed out") from None
        except (subprocess.SubprocessError, ValueError, KeyError):
            raise InvalidMedia("Invalid media input") from None

    @staticmethod
    def motion_filter(motion: str, frames: int) -> str:
        end = max(frames - 1, 1)
        z, x, y = "1", "0", "0"
        if motion == "zoom_in":
            z, x, y = f"1+0.12*on/{end}", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
        elif motion == "zoom_out":
            z, x, y = f"1.12-0.12*on/{end}", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
        elif motion in ("pan_left", "pan_right"):
            z, y = "1.12", "ih/2-ih/zoom/2"
            x = f"(iw-iw/zoom)*{'1-' if motion == 'pan_left' else ''}on/{end}"
            if motion == "pan_left":
                x = f"(iw-iw/zoom)*(1-on/{end})"
        return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1080x1920:fps=30"

    def render(
        self, scenes: list[RenderScene], workdir: Path, *, music: tuple[Path, str] | None = None
    ) -> Path:
        if not scenes or len(scenes) > 100 or sum(s.duration for s in scenes) > 180.1:
            raise RenderError("Invalid render duration or scene count")
        self.deadline = time.monotonic() + self.settings.render_timeout_seconds
        encoding = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            str(self.settings.render_threads),
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
        for index, scene in enumerate(scenes):
            visual = self.probe(scene.visual, scene.visual_type)
            if not any(s["codec_type"] == "video" for s in visual["streams"]):
                raise RenderError("Visual stream missing")
            audio = self.probe(scene.audio, scene.audio_type)
            if not any(s["codec_type"] == "audio" for s in audio["streams"]):
                raise RenderError("Narration stream missing")
            audio_duration = float(audio["format"].get("duration", 0))
            duration = round(scene.duration * 30) / 30
            if audio_duration <= 0 or audio_duration > duration + 0.15:
                raise RenderError("Narration exceeds planned scene duration")
            scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
            if scene.visual_type.startswith("image/"):
                vf = scale + "," + self.motion_filter(scene.motion, max(1, round(duration * 30)))
                visual_args = media_input(scene.visual, scene.visual_type)
            else:
                vf = scale + ",fps=30"
                visual_args = ["-stream_loop", "-1", *media_input(scene.visual, scene.visual_type)]
            self.run(
                [
                    *visual_args,
                    *media_input(scene.audio, scene.audio_type),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-vf",
                    vf,
                    "-af",
                    "apad",
                    "-t",
                    str(duration),
                    *encoding,
                    "-map_metadata",
                    "-1",
                    str(workdir / f"clip{index}.mp4"),
                ]
            )
        (workdir / "clips.txt").write_text(
            "".join(f"file 'clip{i}.mp4'\n" for i in range(len(scenes)))
        )
        args = ["-protocol_whitelist", "file,pipe", "-f", "concat", "-safe", "1", "-i", "clips.txt"]
        if music:
            self.probe(*music)
            args += [
                "-stream_loop",
                "-1",
                *media_input(*music),
                "-filter_complex",
                "[0:a]asplit=2[voice][side];[1:a]volume=0.12[bg];"
                "[bg][side]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=250[duck];"
                "[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[mix]",
                "-map",
                "0:v:0",
                "-map",
                "[mix]",
            ]
        else:
            args += ["-map", "0:v:0", "-map", "0:a:0"]
        output = workdir / "final.mp4"
        self.run(
            [
                *args,
                "-vf",
                "subtitles=captions.ass",
                *encoding,
                "-r",
                "30",
                "-t",
                str(sum(round(s.duration * 30) / 30 for s in scenes)),
                "-movflags",
                "+faststart",
                "-map_metadata",
                "-1",
                str(output),
            ],
            cwd=workdir,
        )
        return output
