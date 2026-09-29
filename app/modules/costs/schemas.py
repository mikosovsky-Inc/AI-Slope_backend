from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class VideoBudget(BaseModel):
    video_id: UUID
    currency: Literal["USD"] = "USD"
    limit_usd: Decimal
    committed_usd: Decimal
    remaining_usd: Decimal
