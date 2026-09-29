from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings
from app.modules.costs.service import BudgetService
from app.shared.llm import LLMProvider, LLMRequest, LLMResult


class BudgetedLLM:
    """Configured per-request estimate including SDK retries; not a provider invoice."""

    def __init__(
        self, provider: LLMProvider, db: Session, video_id: UUID, settings: Settings
    ) -> None:
        self.provider, self.db, self.video_id, self.settings = provider, db, video_id, settings

    def generate[T: BaseModel](self, request: LLMRequest, response_model: type[T]) -> LLMResult[T]:
        if self.settings.external_providers_mode != "mock":
            amount = self.settings.llm_request_estimate_usd
            if amount is None:
                from app.modules.costs.service import BudgetExceeded

                raise BudgetExceeded(
                    "Configure LLM_REQUEST_ESTIMATE_USD before paid video generation"
                )
            BudgetService.reserve(
                self.db,
                self.video_id,
                key=f"llm:{uuid4()}",
                amount=amount * (1 + self.settings.openai_max_retries),
                provider="openai",
                operation="structured_generation",
                model=self.settings.openai_model,
            )
            self.db.commit()
        return self.provider.generate(request, response_model)

    def close(self) -> None:
        self.provider.close()
