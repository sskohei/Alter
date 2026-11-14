"""#32: 注入文の固定評価セット（evaluations/injection/cases.json）を外部通信なしで確かめる。

- ローカル検査（SafetyModerator）の判定が各ケースの ``expect`` と一致する（止めすぎも回帰）
- ``block`` の文は、どの入口（人格・相手の発言・提案の回答・記憶の候補）から入っても
  LLM を呼ぶ前に止まり、モデルが従ってしまった場合の出力も止まる
- ``allow`` の文（普通の会話と、モデルに任せるもの）は入口でも出力でも止めない
- どの入口でも、文は user の引用データにだけ入り、system 指示は変わらない
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from alter_domain import memory
from alter_domain.agent_models import ProfileInput
from alter_domain.llm import (
    SYSTEM,
    FakeLLM,
    LLMResponse,
    SafetyModerator,
    UnsafeOutput,
    build_prompt,
    checked_generate,
)
from alter_domain.llm_contract import Utterance
from alter_domain.llm_execution import parse_result, screen, suggestion_prompt

CASES_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "injection" / "cases.json"
CASES: list[dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
BLOCK = [case for case in CASES if case["expect"] == "block"]
ALLOW = [case for case in CASES if case["expect"] == "allow"]


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [case["id"] for case in cases]


def _profile(text: str) -> ProfileInput:
    return ProfileInput(
        personality=text,
        values_text="誠実",
        interests=["読書"],
        speaking_style="丁寧",
        interaction_policy="尊重",
        shareable_info="",
        restrictions="",
    )


def _entry_prompts(text: str) -> dict[str, list[Any]]:
    """文が入りうる入口ごとの、LLM に渡す直前のプロンプト。"""
    return {
        "profile": build_prompt(_profile(text), ["こんにちは"]),
        "turn": build_prompt(_profile("穏やか"), ["こんにちは", text]),
        "memory": build_prompt(_profile("穏やか"), [], memories=[text]),
        "summary": build_prompt(_profile("穏やか"), [], summary=text),
        "suggestion": suggestion_prompt({"personality": text}),
    }


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def _echo(text: str) -> LLMResponse:
    """注入に従ってしまったモデルの出力（文をそのまま発言する）。"""
    output = Utterance(utterance=text, should_end=False).model_dump_json()
    return LLMResponse(output=output, input_tokens=1, output_tokens=1)


class _EchoLLM(FakeLLM):
    def __init__(self, text: str) -> None:
        self.text = text

    def generate(self, messages: list[Any]) -> LLMResponse:
        return _echo(self.text)


def test_case_file_is_well_formed() -> None:
    ids = _ids(CASES)
    assert len(ids) == len(set(ids))
    assert {case["expect"] for case in CASES} == {"block", "allow"}
    categories = Counter(case["category"] for case in CASES)
    # 注入の種類ごとと、誤検知を見る普通の文がそろっている
    for category in ("override", "secret", "role_spoof", "obfuscation", "credential", "harm"):
        assert categories[category] >= 1, category
    assert categories["benign"] >= 10
    for case in CASES:
        assert (case["category"] == "benign" or case["category"] == "model") == (
            case["expect"] == "allow"
        ), case["id"]
        if case["category"] == "model":
            assert case.get("note"), case["id"]  # 任せる理由を残す


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_local_verdict_matches_expectation(case: dict[str, Any]) -> None:
    assert SafetyModerator().allowed(case["text"]) is (case["expect"] == "allow")


@pytest.mark.parametrize("case", BLOCK, ids=_ids(BLOCK))
def test_blocked_text_stops_before_any_call_from_every_entry(case: dict[str, Any]) -> None:
    for entry, messages in _entry_prompts(case["text"]).items():
        assert messages[0].role == "system"
        assert case["text"] not in messages[0].content, entry
        with pytest.raises(UnsafeOutput):
            screen(messages, ())
    assert not memory.safe(case["text"])


@pytest.mark.parametrize("case", BLOCK, ids=_ids(BLOCK))
def test_blocked_text_is_rejected_when_the_model_repeats_it(case: dict[str, Any]) -> None:
    with pytest.raises(UnsafeOutput):
        checked_generate(_EchoLLM(case["text"]), [], SafetyModerator())
    with pytest.raises(UnsafeOutput):
        parse_result(_echo(case["text"]), Utterance, ())


@pytest.mark.parametrize("case", ALLOW, ids=_ids(ALLOW))
def test_allowed_text_passes_every_entry_and_output(case: dict[str, Any]) -> None:
    for entry, messages in _entry_prompts(case["text"]).items():
        # system 指示は固定で、文は user の引用データにだけ入る
        assert messages[0].content in (SYSTEM, suggestion_prompt({})[0].content), entry
        assert [m.role for m in messages[1:]] == ["user"], entry
        assert case["text"] in _strings(json.loads(messages[1].content)), entry
        screen(messages, ())
    assert memory.safe(case["text"])
    checked_generate(_EchoLLM(case["text"]), [], SafetyModerator())
    parse_result(_echo(case["text"]), Utterance, ())
