import json
import time
from collections.abc import Callable

import httpx
from pydantic import BaseModel, Field, JsonValue, ValidationError

from app.core.config import Settings
from app.shared.generation import (
    GenerationInvalidOutput,
    GenerationJobRef,
    GenerationNotFound,
    GenerationRejected,
    GenerationResult,
    GenerationSubmissionUnknown,
    GenerationUnavailable,
    Identifier,
    ImageGenerationRequest,
    ProviderJobStatus,
    VideoGenerationRequest,
)

STATUS_MAP = {
    "IN_QUEUE": ProviderJobStatus.QUEUED,
    "IN_PROGRESS": ProviderJobStatus.RUNNING,
    "COMPLETED": ProviderJobStatus.SUCCEEDED,
    "FAILED": ProviderJobStatus.FAILED,
    "CANCELLED": ProviderJobStatus.CANCELLED,
    "TIMED_OUT": ProviderJobStatus.TIMED_OUT,
}


class RunpodResponse(BaseModel):
    id: Identifier
    status: str
    output: JsonValue = None
    # Intentionally omit raw provider errors from the application result.
    executionTime: int | None = Field(default=None, ge=0)


class RunpodProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._client = httpx.Client(
            base_url="https://api.runpod.ai/v2/",
            timeout=settings.runpod_timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def _request(self, method: str, path: str, *, body: dict | None = None) -> dict:
        secret = self.settings.runpod_api_key
        if not secret or not secret.get_secret_value().strip():
            raise GenerationRejected("RUNPOD_API_KEY is required for Runpod operations")
        attempts = 1 + self.settings.runpod_status_max_retries if method == "GET" else 1
        for attempt in range(attempts):
            try:
                # Bound JSON response memory; media must live in object storage.
                with self._client.stream(
                    method,
                    path,
                    json=body,
                    headers={"Authorization": f"Bearer {secret.get_secret_value()}"},
                ) as response:
                    code = response.status_code
                    if code == 404:
                        raise GenerationNotFound("Runpod endpoint or job not found")
                    if code == 429 or code >= 500:
                        if method == "POST" and path.endswith("/run") and code >= 500:
                            raise GenerationSubmissionUnknown("Runpod submission outcome unknown")
                        if attempt + 1 == attempts:
                            raise GenerationUnavailable("Runpod temporarily unavailable")
                    elif not 200 <= code < 300:
                        raise GenerationRejected("Runpod request rejected")
                    else:
                        content = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            content.extend(chunk)
                            if len(content) > 1024 * 1024:
                                raise GenerationInvalidOutput("Runpod response too large")
                        try:
                            data = json.loads(content)
                        except (ValueError, UnicodeError, RecursionError):
                            raise GenerationInvalidOutput("Invalid Runpod response") from None
                        if not isinstance(data, dict):
                            raise GenerationInvalidOutput("Invalid Runpod response")
                        return data
            except httpx.RequestError:
                if method == "POST" and path.endswith("/run"):
                    raise GenerationSubmissionUnknown("Runpod submission outcome unknown") from None
                if attempt + 1 == attempts:
                    raise GenerationUnavailable("Runpod temporarily unavailable") from None
            self._sleep(min(0.5 * 2**attempt, 4))
        raise GenerationUnavailable("Runpod temporarily unavailable")

    def _result(
        self, data: dict, endpoint: str, expected_id: str | None = None
    ) -> GenerationResult:
        try:
            parsed = RunpodResponse.model_validate(data)
            status = STATUS_MAP[parsed.status]
            if expected_id is not None and parsed.id != expected_id:
                raise ValueError("Mismatched job ID")
            return GenerationResult(
                job=GenerationJobRef(provider="runpod", endpoint_id=endpoint, job_id=parsed.id),
                status=status,
                output=parsed.output if status == ProviderJobStatus.SUCCEEDED else None,
                error=(
                    f"generation_{status.value}"
                    if status
                    in (
                        ProviderJobStatus.FAILED,
                        ProviderJobStatus.TIMED_OUT,
                    )
                    else None
                ),
            )
        except (ValidationError, ValueError, KeyError):
            raise GenerationInvalidOutput("Invalid Runpod response") from None

    def _submit(self, request: ImageGenerationRequest, kind: str) -> GenerationResult:
        endpoint = getattr(self.settings, f"runpod_{kind}_endpoint_id")
        model = getattr(self.settings, f"runpod_{kind}_model")
        if not endpoint:
            raise GenerationRejected(f"RUNPOD_{kind.upper()}_ENDPOINT_ID is required")
        payload = request.model_dump(mode="json", exclude_none=True) | {"type": kind}
        if model:
            payload["model"] = model
        # request_id is a worker correlation key, NOT a Runpod API idempotency guarantee.
        body = {
            "input": payload,
            "policy": {
                "executionTimeout": self.settings.runpod_execution_timeout_ms,
                "ttl": self.settings.runpod_job_ttl_ms,
            },
        }
        if len(request.model_dump_json().encode()) > 256 * 1024:
            raise GenerationRejected("Generation request too large")
        try:
            data = self._request("POST", f"{endpoint}/run", body=body)
            return self._result(data, endpoint)
        except GenerationInvalidOutput:
            raise GenerationSubmissionUnknown("Runpod submission outcome unknown") from None

    def generate_image(self, request: ImageGenerationRequest) -> GenerationResult:
        return self._submit(request, "image")

    def generate_video(self, request: VideoGenerationRequest) -> GenerationResult:
        return self._submit(request, "video")

    def _check_job(self, job: GenerationJobRef) -> None:
        if job.provider != "runpod":
            raise GenerationRejected("Job belongs to another provider")

    def get_status(self, job: GenerationJobRef) -> GenerationResult:
        self._check_job(job)
        return self._result(
            self._request("GET", f"{job.endpoint_id}/status/{job.job_id}"),
            job.endpoint_id,
            expected_id=job.job_id,
        )

    def cancel(self, job: GenerationJobRef) -> None:
        self._check_job(job)
        self._request("POST", f"{job.endpoint_id}/cancel/{job.job_id}")

    def close(self) -> None:
        self._client.close()
