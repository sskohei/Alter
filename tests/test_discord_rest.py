"""#28 レビュー対応: 投稿・DM の書き込みを内部で再送しない REST クライアント（通信しない）。"""

import asyncio
from typing import Any

import aiohttp
import pytest
from alter_discord_bot import rest


class _Response:
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def json(self, content_type: str | None = None) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _Session:
    def __init__(self, *responses: _Response | BaseException) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        return None


def _client(session: _Session, slept: list[float] | None = None) -> rest.DiscordRest:
    async def sleep(seconds: float) -> None:
        (slept if slept is not None else []).append(seconds)

    return rest.DiscordRest("token", session=session, sleep=sleep)  # type: ignore[arg-type]


def test_send_message_disables_mentions_and_embeds() -> None:
    session = _Session(_Response(200, {"id": "500000000000000001"}))
    message_id = asyncio.run(_client(session).send_message("600000000000000001", "やあ"))
    assert message_id == "500000000000000001"
    sent = session.requests[0]
    assert sent["method"] == "POST"
    assert sent["url"].endswith("/channels/600000000000000001/messages")
    assert sent["json"] == {"content": "やあ", "allowed_mentions": {"parse": []}, "flags": 4}
    assert sent["headers"]["Authorization"] == "Bot token"
    assert sent["allow_redirects"] is False


def test_create_thread_and_open_dm_payloads() -> None:
    session = _Session(_Response(201, {"id": "1"}), _Response(200, {"id": "2"}))
    client = _client(session)
    assert asyncio.run(client.create_thread("300000000000000001", "Alter: 旅", 1440)) == "1"
    assert asyncio.run(client.open_dm("100000000000000001")) == "2"
    assert session.requests[0]["json"] == {
        "name": "Alter: 旅",
        "type": 11,
        "auto_archive_duration": 1440,
    }
    assert session.requests[1]["url"].endswith("/users/@me/channels")
    assert session.requests[1]["json"] == {"recipient_id": "100000000000000001"}


@pytest.mark.parametrize(
    "failure",
    [
        _Response(500, {"message": "x"}),
        _Response(502, ValueError("not json")),
        aiohttp.ClientConnectionError(),
        TimeoutError(),
    ],
)
def test_server_errors_and_disconnects_are_never_resent(failure: Any) -> None:
    # 2 回目の応答（成功）が使われないこと = 書き込みを 1 回しか送っていない
    session = _Session(failure, _Response(200, {"id": "should-not-be-used"}))
    with pytest.raises((rest.RestError, aiohttp.ClientError, TimeoutError)):
        asyncio.run(_client(session).create_thread("300000000000000001", "t", 1440))
    assert len(session.requests) == 1


def test_rate_limit_waits_retry_after_then_sends_once_more() -> None:
    slept: list[float] = []
    session = _Session(
        _Response(429, {"retry_after": 1.5, "global": False}),
        _Response(200, {"id": "9"}),
    )
    assert asyncio.run(_client(session, slept).send_message("6", "x")) == "9"
    assert slept == [1.5] and len(session.requests) == 2


def test_rate_limit_gives_up_and_caps_wait() -> None:
    slept: list[float] = []
    responses = [_Response(429, {}, {"Retry-After": "999"}) for _ in range(5)]
    session = _Session(*responses)
    with pytest.raises(rest.RestError) as caught:
        asyncio.run(_client(session, slept).send_message("6", "x"))
    assert caught.value.status == 429
    assert slept == [rest.MAX_RATE_LIMIT_SECONDS] * rest.MAX_RATE_LIMIT_WAITS


def test_error_carries_status_and_code_but_not_body_or_token() -> None:
    session = _Session(_Response(403, {"code": 50007, "message": "Cannot send messages to user"}))
    with pytest.raises(rest.RestError) as caught:
        asyncio.run(_client(session).send_message("6", "本文"))
    assert (caught.value.status, caught.value.code) == (403, 50007)
    assert "本文" not in str(caught.value) and "token" not in str(caught.value)
