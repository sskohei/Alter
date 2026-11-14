"""Provider independent limits and price calculation; no network or implicit model choice."""

from decimal import ROUND_CEILING, Decimal

from pydantic import BaseModel, ConfigDict, Field


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts must be nonnegative")
        return (
            (self.input_per_million * input_tokens + self.output_per_million * output_tokens)
            / Decimal(1_000_000)
        ).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


class Utterance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    utterance: str = Field(min_length=1, max_length=4000)
    should_end: bool
    reason: str | None = Field(default=None, max_length=200)
