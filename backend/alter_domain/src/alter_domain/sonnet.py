"""Explicit Claude adapter. No SDK retries, redirects, prompt logs or stored provider bodies."""

import copy
import json
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alter_domain.llm import (
    LLMResponse,
    PreparedCall,
    PromptMessage,
    RateLimitedLLMError,
    TemporaryLLMError,
    UnknownLLMOutcome,
    UnsafeOutput,
)
from alter_domain.llm_contract import Utterance
from alter_domain.llm_selection import (
    API_VERSION,
    EFFORT,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    MODERATION_INPUT_TOKENS,
    MODERATION_OUTPUT_TOKENS,
    model_price,
)
from alter_domain.settings import Settings


class LLMUnavailable(Exception):
    pass


class RequestNotSent(Exception):
    """A pre-dispatch authorization guard failed before any provider request."""

    def __init__(self, reason: Exception) -> None:
        super().__init__("Request rejected before dispatch")
        self.reason = reason


class Usage(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)


def wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    def simplify(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: simplify(item)
                for key, item in value.items()
                if key
                not in {
                    "minLength",
                    "maxLength",
                    "minItems",
                    "maxItems",
                    "minimum",
                    "maximum",
                    "default",
                    "title",
                }
            }
        if isinstance(value, list):
            return [simplify(item) for item in value]
        return value

    schema: dict[str, Any] = simplify(model.model_json_schema())
    return schema


class SonnetClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client,
        *,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self.before_request = before_request
        self.price = model_price()
        self._settings = settings
        self._http = http

    def require_enabled(self) -> None:
        if (
            self._settings.alter_mode != "live"
            or not self._settings.llm_enabled
            or not self._settings.anthropic_api_key.get_secret_value()
        ):
            raise LLMUnavailable("LLM is unavailable")

    def _post(self, path: str, body: dict[str, Any], *, billable: bool) -> dict[str, Any]:
        self.require_enabled()
        if self.before_request is not None:
            try:
                self.before_request()
            except Exception as exc:
                raise RequestNotSent(exc) from None
        try:
            response = self._http.post(
                "https://api.anthropic.com/v1/" + path,
                headers={
                    "Authorization": "Bearer "
                    + self._settings.anthropic_api_key.get_secret_value(),
                    "anthropic-version": API_VERSION,
                },
                json=body,
                timeout=httpx.Timeout(30.0, connect=5.0),
                follow_redirects=False,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise TemporaryLLMError() from None  # Connection never established; no request sent.
        except httpx.HTTPError:
            if billable:
                raise UnknownLLMOutcome("Provider outcome is unknown") from None
            raise TemporaryLLMError() from None
        if response.status_code == 429:
            try:
                delay = int(response.headers.get("retry-after", "30"))
            except ValueError:
                delay = 30
            raise RateLimitedLLMError(delay)
        if response.status_code in (400, 401, 403, 404, 413, 422):
            raise LLMUnavailable("Provider rejected the request")
        if response.status_code != 200:
            if billable:
                raise UnknownLLMOutcome("Provider outcome is unknown")
            raise TemporaryLLMError()
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            if billable:
                raise UnknownLLMOutcome("Provider metadata is invalid") from None
            raise LLMUnavailable("Token count is unavailable") from None

    def prepare(
        self, messages: list[PromptMessage], schema: type[BaseModel], *, moderation: bool = False
    ) -> PreparedCall:
        input_limit = MODERATION_INPUT_TOKENS if moderation else MAX_INPUT_TOKENS
        output_limit = MODERATION_OUTPUT_TOKENS if moderation else MAX_OUTPUT_TOKENS
        margin = 128 if moderation else 1024
        body: dict[str, Any] = {
            "model": MODEL,
            "system": "\n".join(m.content for m in messages if m.role == "system"),
            "messages": [m.model_dump() for m in messages if m.role == "user"],
            "thinking": {"type": "between_tools" if moderation else "adaptive"},
            "output_config": {
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": wire_schema(schema)},
            },
        }
        body = copy.deepcopy(body)
        # Only quoted historical context can be dropped. The public profile/system/schema stay.
        while True:
            count = self._post("messages/count_tokens", body, billable=False).get("input_tokens")
            if type(count) is not int or count < 0:
                raise LLMUnavailable("Token count is invalid")
            if count + margin <= input_limit:
                return PreparedCall(
                    {**body, "max_tokens": output_limit}, count + margin, output_limit
                )
            if moderation or len(body["messages"]) != 1:
                raise UnsafeOutput("Input exceeds the token bound")
            try:
                data = json.loads(body["messages"][0]["content"])
                if data.get("quoted_summary"):
                    data["quoted_summary"] = None
                    body["messages"][0]["content"] = json.dumps(data, ensure_ascii=False)
                    continue
                history = data.get("quoted_memories") or data.get("quoted_turns")
                if not isinstance(history, list) or not history:
                    raise ValueError
                if history is data.get("quoted_turns") and len(history) <= 2:
                    raise UnsafeOutput("Recent dialogue exceeds the token bound")
                history.pop(-1 if data.get("quoted_memories") else 0)
                body["messages"][0]["content"] = json.dumps(data, ensure_ascii=False)
            except (ValueError, TypeError, AttributeError):
                raise UnsafeOutput("Input exceeds the token bound") from None

    def complete(self, call: PreparedCall) -> LLMResponse:
        data = self._post("messages", call.body, billable=True)
        try:
            usage = Usage.model_validate(data["usage"])
            if (
                data.get("model") != MODEL
                or usage.cache_creation_input_tokens
                or usage.cache_read_input_tokens
                or usage.input_tokens > call.input_bound
                or usage.output_tokens > call.output_bound
            ):
                raise ValueError
        except (KeyError, ValueError, ValidationError):
            raise UnknownLLMOutcome("Provider usage requires reconciliation") from None
        blocks = data.get("content")
        texts = (
            [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            if isinstance(blocks, list)
            else []
        )
        valid = len(texts) == 1 and isinstance(texts[0], str) and len(texts[0]) <= 16000
        return LLMResponse(
            output=texts[0] if valid and isinstance(texts[0], str) else "",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            completed=valid and data.get("stop_reason") == "end_turn",
        )

    def generate(self, messages: list[PromptMessage]) -> LLMResponse:
        return self.complete(self.prepare(messages, Utterance))
