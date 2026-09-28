from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field


class VisualEstimates(BaseModel):
    image_usd: Decimal = Field(gt=0, le=100, max_digits=12, decimal_places=6)
    video_second_usd: Decimal = Field(gt=0, le=100, max_digits=12, decimal_places=6)


class VisualCostEstimator(Protocol):
    def image_cost(self) -> Decimal: ...
    def video_cost(self, duration: Decimal) -> Decimal: ...
    def snapshot(self) -> VisualEstimates: ...


class ConfiguredVisualEstimator:
    """Planning estimates supplied by the operator, not provider billing/prices."""

    def __init__(self, estimates: VisualEstimates) -> None:
        self.estimates = estimates

    def image_cost(self) -> Decimal:
        return self.estimates.image_usd

    def video_cost(self, duration: Decimal) -> Decimal:
        return (duration * self.estimates.video_second_usd).quantize(Decimal("0.000001"))

    def snapshot(self) -> VisualEstimates:
        return self.estimates.model_copy()
