"""The only application module allowed to import the OpenAI SDK."""

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.shared.llm import (
    LLMIncomplete,
    LLMInvalidOutput,
    LLMRefusal,
    LLMRequest,
    LLMResult,
    LLMUnavailable,
    LLMUsage,
)


class OpenAIProvider:
    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def create(
        cls, *, api_key: str, model: str, timeout: float, max_retries: int
    ) -> "OpenAIProvider":
        return cls(OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries), model)

    def generate[T: BaseModel](self, request: LLMRequest, response_model: type[T]) -> LLMResult[T]:
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=request.instructions,
                input=request.prompt,
                max_output_tokens=request.max_output_tokens,
                text_format=response_model,
                store=False,
            )
        except ValidationError:
            raise LLMInvalidOutput("Model output failed schema validation") from None
        except OpenAIError:
            raise LLMUnavailable("LLM provider request failed") from None
        if response.status == "incomplete":
            raise LLMIncomplete("Model response is incomplete")
        if response.status != "completed":
            raise LLMUnavailable("Model response did not complete")
        for item in response.output:
            if item.type == "message":
                if any(part.type == "refusal" for part in item.content):
                    raise LLMRefusal("Model declined the request")
        if response.output_parsed is None:
            raise LLMInvalidOutput("Model response contains no structured output")
        return LLMResult(
            data=response.output_parsed,
            provider="openai",
            model=response.model,
            response_id=response.id,
            usage=LLMUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            )
            if response.usage
            else None,
        )

    def close(self) -> None:
        self._client.close()
