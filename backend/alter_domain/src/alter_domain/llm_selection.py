"""MVP model selected by the owner on 2026-11-13 (ADR-008).

No network or enablement side effects. #18 uses this selection when wiring its adapter.
Changing the model also requires rechecking prices, data terms and evaluations.
"""

from decimal import Decimal
from typing import Final

from alter_domain.llm_contract import ModelPrice

PROVIDER: Final = "anthropic"
MODEL: Final = "claude-sonnet-5-5"
API_VERSION: Final = "2026-10-21"
EFFORT: Final = "low"
MAX_OUTPUT_TOKENS: Final = 512  # Includes thinking.
MAX_INPUT_TOKENS: Final = 8192
PRICE_CHECKED_ON: Final = "2026-11-13"
PRICE_SOURCE: Final = "https://platform.claude.com/docs/en/models/sonnet-5-5/overview"


def model_price() -> ModelPrice:
    """Fresh price value so callers cannot mutate the selected price for other callers."""
    return ModelPrice(
        provider=PROVIDER,
        model=MODEL,
        input_per_million=Decimal("2.00"),
        output_per_million=Decimal("10.00"),
    )
