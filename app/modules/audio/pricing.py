from decimal import ROUND_UP, Decimal
from typing import Protocol


class TTSPricing(Protocol):
    def estimate(self, characters: int) -> Decimal: ...


class ConfiguredTTSPricing:
    def __init__(self, usd_per_1000_characters: Decimal) -> None:
        if not usd_per_1000_characters.is_finite() or usd_per_1000_characters < 0:
            raise ValueError("TTS rate must be finite and nonnegative")
        self.rate = usd_per_1000_characters

    def estimate(self, characters: int) -> Decimal:
        return (Decimal(characters) * self.rate / 1000).quantize(
            Decimal("0.000001"), rounding=ROUND_UP
        )
