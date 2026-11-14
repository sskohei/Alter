"""#36: 警告のしきい値（DB なし）。"""

import copy
from typing import Any

import pytest
from alter_domain import ops
from alter_domain.cli import ops as cli

QUIET: dict[str, Any] = {
    "jobs": {
        "conversation": {
            "due": 1,
            "oldest_due_minutes": 29,
            "lease_expired": 0,
            "failed_24h": 0,
            "completed_24h": 5,
        }
    },
    "llm_unsettled": dict.fromkeys(ops.UNSETTLED_LLM, 0) | {"generating": 2},
    "llm_invocations": {"started": 1},
    "cost": {"exposure_usd": "0.799999", "daily_limit_usd": "1.00"},
    "discord": {
        "posts": {"pending": 3, "sending": 1, "needs_review": 0},
        "dms": {"pending": 0, "sending": 0, "needs_review": 0},
        "thread_cleanup": {"forbidden": 4},
    },
    "deletion": {"open": 1, "failed": 0, "oldest_open_days": 24},
}


def _with(path: str, value: Any) -> dict[str, Any]:
    snap = copy.deepcopy(QUIET)
    *parents, last = path.split(".")
    node = snap
    for key in parents:
        node = node[key]
    node[last] = value
    return snap


def test_quiet_state_has_no_alerts() -> None:
    # 進行中の呼び出し・送信中・スレッド削除の権限不足（forbidden）は警告にしない
    assert ops.alerts(QUIET) == []


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        ("jobs.conversation.oldest_due_minutes", 30, "jobs_waiting:conversation"),
        ("jobs.conversation.lease_expired", 1, "lease_expired:conversation"),
        ("jobs.conversation.failed_24h", 1, "jobs_failed:conversation"),
        ("llm_unsettled.unknown", 1, "llm_unknown_outcome"),
        ("llm_invocations.unknown", 1, "llm_unknown_outcome"),
        ("cost.exposure_usd", "0.800000", "budget_near_limit"),
        ("discord.posts.needs_review", 1, "discord_needs_review"),
        ("discord.dms.needs_review", 1, "discord_needs_review"),
        ("discord.thread_cleanup.failed", 1, "discord_cleanup_failed"),
        ("deletion.failed", 1, "deletion_failed"),
        ("deletion.oldest_open_days", 25, "deletion_near_deadline"),
    ],
)
def test_each_threshold_raises_its_alert(path: str, value: Any, code: str) -> None:
    assert ops.alerts(_with(path, value)) == [code]


def test_zero_limit_does_not_alert_on_budget() -> None:
    # 上限 0（LLM を使わない設定）では割合を出さない
    snap = _with("cost.daily_limit_usd", "0")
    assert ops.alerts(snap) == []


def test_cli_rejects_bad_operator(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--env", "local", "--operator", "a b", "status"]) == cli.EXIT_USAGE
    assert "--operator" in capsys.readouterr().err
