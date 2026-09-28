import hashlib
import json
from threading import Lock

from app.shared.generation import (
    GenerationJobRef,
    GenerationNotFound,
    GenerationRejected,
    GenerationResult,
    ImageGenerationRequest,
    ProviderJobStatus,
    VideoGenerationRequest,
)


class MockRunpodProvider:
    """In-memory job simulator, without network, media files or paid calls."""

    def __init__(self) -> None:
        self._jobs: dict[GenerationJobRef, GenerationResult] = {}
        self._requests: dict[GenerationJobRef, str] = {}
        self._lock = Lock()

    def _submit(self, request: ImageGenerationRequest, kind: str) -> GenerationResult:
        job = GenerationJobRef(provider="mock_runpod", endpoint_id=kind, job_id=request.request_id)
        fingerprint = hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        with self._lock:
            if job in self._jobs:
                if self._requests[job] != fingerprint:
                    raise GenerationRejected("Request ID reused with different input")
                return self._jobs[job].model_copy(deep=True)
            result = GenerationResult(job=job, status=ProviderJobStatus.QUEUED)
            self._requests[job] = fingerprint
            self._jobs[job] = result
            return result

    def generate_image(self, request: ImageGenerationRequest) -> GenerationResult:
        return self._submit(request, "image")

    def generate_video(self, request: VideoGenerationRequest) -> GenerationResult:
        return self._submit(request, "video")

    def get_status(self, job: GenerationJobRef) -> GenerationResult:
        with self._lock:
            if job not in self._jobs:
                raise GenerationNotFound("Generation job not found")
            result = self._jobs[job]
            if result.status == ProviderJobStatus.QUEUED:
                result = GenerationResult(job=job, status=ProviderJobStatus.RUNNING)
            elif result.status == ProviderJobStatus.RUNNING:
                result = GenerationResult(
                    job=job,
                    status=ProviderJobStatus.SUCCEEDED,
                    output={"mock": True, "type": job.endpoint_id, "request_id": job.job_id},
                )
            self._jobs[job] = result
            return result.model_copy(deep=True)

    def cancel(self, job: GenerationJobRef) -> None:
        with self._lock:
            if job not in self._jobs:
                raise GenerationNotFound("Generation job not found")
            if self._jobs[job].status in (ProviderJobStatus.QUEUED, ProviderJobStatus.RUNNING):
                self._jobs[job] = GenerationResult(job=job, status=ProviderJobStatus.CANCELLED)

    def close(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._requests.clear()
