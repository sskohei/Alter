"""Optional public draft; paid execution never persists or approves a personality revision."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from alter_domain.agent_models import ProfileInput, SuggestionInput
from alter_domain.agents import owned
from alter_domain.budget import BudgetExceeded
from alter_domain.llm import (
    RateLimitedLLMError,
    TemporaryLLMError,
    UnknownLLMOutcome,
    UnsafeOutput,
)
from alter_domain.llm_execution import (
    LLMExecution,
    SuggestionLimit,
    UnsettledLLMCall,
    suggestion_prompt,
)
from alter_domain.sonnet import LLMUnavailable
from fastapi import APIRouter, Request
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from alter_api.auth import CurrentPrincipal
from alter_api.business import business_connection
from alter_api.errors import ApiError, ErrorResponse

router = APIRouter(
    tags=["agents"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 429, 503)},
)


@router.post(
    "/agents/{agent_id}/profile/suggest",
    response_model=ProfileInput,
    operation_id="suggest_agent_profile",
)
def suggest(
    agent_id: UUID, data: SuggestionInput, request: Request, principal: CurrentPrincipal
) -> ProfileInput:
    @contextmanager
    def authorized() -> Iterator[Connection]:
        with contextmanager(business_connection)(request, principal) as conn:
            owned(conn, principal.sub, agent_id)
            yield conn

    # Keep ownership/error behavior even when paid generation is disabled.
    with authorized():
        pass
    settings = request.app.state.settings
    forbidden = (
        settings.anthropic_api_key.get_secret_value(),
        settings.supabase_secret_key,
        settings.supabase_storage_secret_key.get_secret_value(),
        request.headers.get("authorization", "").partition(" ")[2],
        settings.profile_encryption_key_v1.get_secret_value(),
        settings.discord_client_secret,
        *(key.get_secret_value() for key in settings.api_idempotency_hmac_keys.values()),
    )
    execution = LLMExecution(
        request.app.state.db_engine,
        request.app.state.llm_client,
        authorized,
        settings.max_daily_cost_usd,
        forbidden=forbidden,
    )
    try:
        return execution.execute(agent_id, suggestion_prompt(data.answers), ProfileInput)
    except SuggestionLimit:
        now = datetime.now(UTC)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        raise ApiError(
            429,
            "RATE_LIMITED",
            "Daily suggestion limit reached",
            headers={"Retry-After": str(max(1, int((tomorrow - now).total_seconds())))},
        ) from None
    except RateLimitedLLMError as exc:
        raise ApiError(
            429,
            "RATE_LIMITED",
            "LLM rate limit reached",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except TemporaryLLMError as exc:
        raise ApiError(
            503,
            "SERVICE_UNAVAILABLE",
            "LLM temporarily unavailable",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except UnsafeOutput:
        raise ApiError(422, "VALIDATION_ERROR", "Input or output failed safety checks") from None
    except (BudgetExceeded, UnsettledLLMCall, UnknownLLMOutcome, LLMUnavailable, SQLAlchemyError):
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", "LLM unavailable or budget requires review"
        ) from None
