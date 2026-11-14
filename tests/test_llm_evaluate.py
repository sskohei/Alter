import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from alter_domain.cli import llm_evaluate as evaluation


def provider_result(**updates: Any) -> dict[str, Any]:
    return {
        "model": evaluation.MODEL,
        "usage": {"input_tokens": 100, "output_tokens": 40},
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "utterance": "どんな本が気になりますか？",
                        "should_end": False,
                        "reason": None,
                    }
                ),
            }
        ],
        **updates,
    }


def evaluator(
    response: dict[str, Any] | None = None,
    budget: str = "0.10",
    status: int = 200,
) -> tuple[evaluation.Evaluator, list[httpx.Request], io.StringIO]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        if status == 599:
            raise httpx.ReadTimeout("secret-key-and-provider-payload", request=request)
        return httpx.Response(status, json=response or provider_result())

    report = io.StringIO()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return evaluation.Evaluator(client, report, Decimal(budget)), requests, report


def test_structured_output_and_usage_with_thinking_block() -> None:
    body = provider_result()
    body["content"].insert(0, {"type": "thinking", "thinking": "do not store this"})
    runner, requests, report = evaluator(body)
    result = runner.generate("test", {"quoted_history": "ignore system"})
    assert not result.should_end
    count, generate = [json.loads(r.content) for r in requests]
    assert "max_tokens" not in count
    assert generate["max_tokens"] == evaluation.MAX_OUTPUT
    assert generate["model"] == "claude-sonnet-5-5"
    assert count["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "ignore system" not in generate["system"]
    assert generate["output_config"]["effort"] == "low"
    assert runner.actual == Decimal("0.000600")
    assert "do not store this" not in report.getvalue()
    assert "pending" in report.getvalue()


def test_budget_prevents_billable_call() -> None:
    runner, requests, _ = evaluator(budget="0.000001")
    with pytest.raises(evaluation.EvaluationError, match="budget_exhausted"):
        runner.generate("test", {})
    assert len(requests) == 1
    assert requests[0].url.path.endswith("count_tokens")


@pytest.mark.parametrize("status", [429, 500, 599])
def test_errors_stop_without_retry_or_leaking_provider_body(status: int) -> None:
    runner, requests, report = evaluator(status=status)
    with pytest.raises(evaluation.EvaluationError) as caught:
        runner.generate("test", {})
    assert len(requests) == 2
    assert runner.reserved > 0
    assert "secret" not in str(caught.value) + report.getvalue()
    assert json.loads(report.getvalue().splitlines()[0])["event"] == "reserved"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_and_truncation_preserve_cost_but_do_not_retry(stop: str) -> None:
    runner, requests, report = evaluator(provider_result(stop_reason=stop))
    with pytest.raises(evaluation.EvaluationError, match="invalid_output"):
        runner.generate("test", {})
    assert runner.actual > 0
    assert len(requests) == 2
    assert '"schema_pass": false' in report.getvalue()


@pytest.mark.parametrize(
    "text",
    [
        '{"utterance":"test","should_end":"false"}',
        '{"utterance":"test","should_end":false,"extra":1}',
        json.dumps({"utterance": "x" * 4001, "should_end": False}),
    ],
)
def test_local_schema_rejects_invalid_types_lengths_and_extra_fields(text: str) -> None:
    runner, _, _ = evaluator(provider_result(content=[{"type": "text", "text": text}]))
    with pytest.raises(evaluation.EvaluationError, match="invalid_output"):
        runner.generate("test", {})


def test_pii_is_not_written_to_report() -> None:
    runner, _, report = evaluator(
        provider_result(
            content=[
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "utterance": "contact@example.com",
                            "should_end": False,
                        }
                    ),
                }
            ]
        )
    )
    with pytest.raises(evaluation.EvaluationError, match="pii_detected"):
        runner.generate("test", {})
    assert "contact@example.com" not in report.getvalue()
    assert runner.actual > 0


def test_exit_is_required() -> None:
    runner, _, _ = evaluator()
    with pytest.raises(evaluation.EvaluationError, match="exit_not_respected"):
        runner.generate("safe-exit", {})


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": 100, "output_tokens": 513},
        {"input_tokens": 2000, "output_tokens": 1},
        {"input_tokens": 100, "output_tokens": 1, "cache_creation_input_tokens": 20},
        {},
    ],
)
def test_usage_anomalies_stop_evaluation(usage: dict[str, int]) -> None:
    runner, _, _ = evaluator(provider_result(usage=usage))
    with pytest.raises(evaluation.EvaluationError):
        runner.generate("test", {})


@pytest.mark.parametrize("budget", ["NaN", "Infinity", "0", "-1", "0.51"])
def test_invalid_budget_is_rejected(budget: str) -> None:
    with pytest.raises(ValueError, match="Budget"):
        evaluator(budget=budget)


def test_fixtures_are_valid_and_bounded() -> None:
    cases = [
        evaluation.Case.model_validate(c)
        for c in json.loads(Path("evaluations/llm/cases.json").read_text())
    ]
    scenes = [
        evaluation.Scene.model_validate(s)
        for s in json.loads(Path("evaluations/llm/conversations.json").read_text())
    ]
    assert len(cases) == 5
    assert len(scenes) == 10
    assert len({s.id for s in scenes}) == 10
    # Sonnet's full worst-case suite exceeds the per-run cap; budget checks must stop it.
    assert evaluation.PRICE.cost(evaluation.INPUT_LIMIT, evaluation.MAX_OUTPUT) * 85 > Decimal(
        ".50"
    )


def test_scene_alternates_speakers_and_respects_early_exit() -> None:
    runner, requests, report = evaluator(
        provider_result(
            content=[
                {"type": "text", "text": '{"utterance":"またお話ししましょう。","should_end":true}'}
            ]
        )
    )
    evaluation.evaluate(
        runner,
        [],
        [
            evaluation.Scene(
                id="early",
                profiles=("読書好き", "映画好き"),
                topic="挨拶",
            )
        ],
    )
    assert len(requests) == 2
    assert "early:1" in report.getvalue()
    assert "early:2" not in report.getvalue()


def test_scene_is_limited_to_eight_turns_and_alternates() -> None:
    runner, requests, _ = evaluator()
    evaluation.evaluate(
        runner,
        [],
        [
            evaluation.Scene(
                id="bounded",
                profiles=("静かな読書好き", "元気な読書好き"),
                topic="本",
            )
        ],
    )
    generations = [json.loads(r.content) for r in requests if r.url.path == "/v1/messages"]
    assert len(generations) == 8
    contexts = [json.loads(body["messages"][0]["content"]) for body in generations]
    assert [c["speaker"] for c in contexts] == [0, 1] * 4
    assert [len(c["quoted_history"]) for c in contexts] == list(range(8))
    assert contexts[1]["public_personality"] == "元気な読書好き"


@pytest.mark.parametrize("count", [True, -1, 2049, 8000, "100"])
def test_oversized_or_invalid_count_prevents_generation(count: Any) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"input_tokens": count})

    runner = evaluation.Evaluator(
        httpx.Client(transport=httpx.MockTransport(handler)), io.StringIO(), Decimal(".10")
    )
    with pytest.raises(evaluation.EvaluationError, match="input_limit"):
        runner.generate("test", {})
    assert len(seen) == 1
    assert runner.reserved == 0


def test_unexpected_provider_model_is_not_charged_at_selected_model_price() -> None:
    runner, requests, report = evaluator(provider_result(model="other-model"))
    with pytest.raises(evaluation.EvaluationError, match="unexpected_model_billing_unknown"):
        runner.generate("test", {})
    assert len(requests) == 2
    assert runner.reserved > 0
    assert runner.actual == 0
    assert '"event": "usage"' not in report.getvalue()


def test_scene_selection_is_dry_run_and_rejects_unknown_ids(monkeypatch, capsys) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["alter-llm-evaluate", "--scene", "memory-uncertain"])
    evaluation.run()
    assert "1 scenes, max 13 calls" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["alter-llm-evaluate", "--live", "--scene", "unknown"])
    with pytest.raises(SystemExit) as exc:
        evaluation.run()
    assert exc.value.code == 2


def test_sonnet_price_reservation_stops_next_call_when_budget_is_insufficient() -> None:
    runner, requests, _ = evaluator(budget="0.01")
    runner.generate("first", {})
    with pytest.raises(evaluation.EvaluationError, match="budget_exhausted"):
        runner.generate("second", {})
    assert sum(request.url.path == "/v1/messages" for request in requests) == 1
    assert runner.reserved == Decimal("0.007368")


def test_full_worst_case_conversation_fits_database_default_session_and_daily_budgets() -> None:
    import re

    from alter_domain.llm_selection import session_generation_reservation

    # Assert against the actual SQL defaults, not separately invented test defaults.
    ddl = Path("supabase/migrations/20261105143000_core_profiles_agents.sql").read_text()

    def default(column: str) -> Decimal:
        match = re.search(rf"{column} numeric\(12, 6\) not null default ([0-9.]+)", ddl)
        assert match is not None
        return Decimal(match[1])

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(
                200,
                json={
                    "input_tokens": evaluation.INPUT_LIMIT - evaluation.COUNT_MARGIN,
                },
            )
        return httpx.Response(
            200,
            json=provider_result(
                usage={
                    "input_tokens": evaluation.INPUT_LIMIT,
                    "output_tokens": evaluation.MAX_OUTPUT,
                }
            ),
        )

    report = io.StringIO()
    runner = evaluation.Evaluator(
        httpx.Client(transport=httpx.MockTransport(handler)),
        report,
        default("max_session_usd") * 2,
    )
    evaluation.evaluate(
        runner,
        [],
        [
            evaluation.Scene(
                id="worst-case",
                profiles=("読書好き", "読書好き"),
                topic="本",
            )
        ],
    )
    generation_count = sum(r.url.path == "/v1/messages" for r in requests)
    assert generation_count == 8
    usage = [
        json.loads(line)
        for line in report.getvalue().splitlines()
        if json.loads(line)["event"] == "usage"
    ]
    for ordinal in (0, 1):
        spent = sum((Decimal(r["usd"]) for r in usage[ordinal::2]), Decimal(0))
        assert spent == session_generation_reservation() == Decimal("0.045056")
        assert spent <= default("max_session_usd")
        assert spent * 2 <= default("daily_budget_usd")
