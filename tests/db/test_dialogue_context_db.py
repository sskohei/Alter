import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alter_domain.conversation import run_one
from alter_domain.llm import FakeLLM, LLMResponse
from alter_domain.llm_selection import model_price
from alter_domain.scheduling import schedule
from sqlalchemy import text


class ContextCapture(FakeLLM):
    price = model_price()

    def __init__(self):
        self.contexts = []

    def prepare(self, messages, schema, *, moderation=False):
        if not moderation:
            self.contexts.append(json.loads(messages[1].content))
        return super().prepare(messages, schema, moderation=moderation)

    def generate(self, messages):
        turn = self.contexts[-1]["conversation"]["next_turn"]
        return LLMResponse(
            output=json.dumps({"utterance": f"本についての発言{turn}", "should_end": False}),
            input_tokens=10,
            output_tokens=10,
        )


def queue(engine, ai_pair):
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
        for n, agent in enumerate(sorted(ai_pair), 1):
            conn.execute(
                text("update public.agents set name=:name where id=:id"),
                {"id": agent, "name": f"AI{n}"},
            )
    assert schedule(engine, now, Decimal("10"))
    return now


def test_worker_sends_each_speaker_their_own_view_of_the_complete_dialogue(engine, ai_pair):
    queue(engine, ai_pair)
    client = ContextCapture()
    assert run_one(engine, client)
    assert len(client.contexts) == 8
    for turn, data in enumerate(client.contexts, 1):
        own = (turn - 1) % 2 + 1
        assert data["participants"] == {"self": f"AI{own}", "partner": f"AI{3 - own}"}
        assert data["conversation"]["next_turn"] == turn
        assert data["conversation"]["has_spoken"] == (turn >= 3)
        assert data["conversation"]["reply_to_turn"] == (turn - 1 if turn > 1 else None)
        assert len(data["quoted_turns"]) == turn - 1
        for past in data["quoted_turns"]:
            assert past["speaker"] == ("self" if past["turn_no"] % 2 == turn % 2 else "partner")
            assert past["content"] == f"本についての発言{past['turn_no']}"
        assert not any(str(agent) in json.dumps(data) for agent in ai_pair)


def test_resumed_worker_uses_persisted_speakers_instead_of_restarting_introductions(
    engine, ai_pair, monkeypatch
):
    import alter_domain.conversation as worker

    now = queue(engine, ai_pair)
    real_prepare = worker.prepare
    calls = []

    class Crash(BaseException):
        pass

    def prepare(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise Crash()
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(worker, "prepare", prepare)
    with pytest.raises(Crash):
        run_one(engine, ContextCapture(), clock=lambda: now)
    monkeypatch.setattr(worker, "prepare", real_prepare)
    resumed = ContextCapture()
    assert run_one(engine, resumed, clock=lambda: now + timedelta(seconds=91))
    first = resumed.contexts[0]
    assert first["conversation"]["next_turn"] == 3
    assert first["conversation"]["has_spoken"] is True
    assert first["conversation"]["reply_to_turn"] == 2
    assert [turn["speaker"] for turn in first["quoted_turns"]] == ["self", "partner"]
