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
# Includes system, schema, personality, memories, history and counting margin.
MAX_INPUT_TOKENS: Final = 3072
MAX_CONVERSATION_TURNS: Final = 8
GENERATIONS_PER_AGENT: Final = MAX_CONVERSATION_TURNS // 2
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


def session_generation_reservation() -> Decimal:
    """Whole-session bound per AI: four generations; MVP summaries use no extra LLM.

    Provider retries are allowed only when confirmed unbilled; unknown billing stops
    the session and retains its reservation. Additional billable moderation/summary
    calls must be bounded separately within the same session/day/global limits.
    """
    return model_price().cost(MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS) * GENERATIONS_PER_AGENT


# Independent safety classification has its own tight, separately counted bound.
MODERATION_INPUT_TOKENS: Final = 512
MODERATION_OUTPUT_TOKENS: Final = 16


def call_reservation() -> Decimal:
    """One generation and one independent moderation request, including thinking."""
    return model_price().cost(MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS) + model_price().cost(
        MODERATION_INPUT_TOKENS, MODERATION_OUTPUT_TOKENS
    )


def session_reservation() -> Decimal:
    return call_reservation() * GENERATIONS_PER_AGENT
