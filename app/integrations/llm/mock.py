from collections.abc import Callable, Mapping

from pydantic import BaseModel, ValidationError

from app.shared.llm import LLMInvalidOutput, LLMRequest, LLMResult, LLMUsage


class MockLLMProvider:
    """Deterministic offline fixtures keyed by response model, with real validation."""

    def __init__(
        self, fixtures: Mapping[type[BaseModel], dict | Callable[[LLMRequest], dict]] | None = None
    ) -> None:
        self._fixtures = dict(fixtures or {})

    def generate[T: BaseModel](self, request: LLMRequest, response_model: type[T]) -> LLMResult[T]:
        if response_model not in self._fixtures:
            raise LLMInvalidOutput("No mock fixture registered for response model")
        try:
            fixture = self._fixtures[response_model]
            data = response_model.model_validate(fixture(request) if callable(fixture) else fixture)
        except ValidationError:
            raise LLMInvalidOutput("Mock output failed schema validation") from None
        return LLMResult(
            data=data,
            provider="mock",
            model="mock",
            response_id="mock-response",
            usage=LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0),
        )

    def close(self) -> None:
        pass
