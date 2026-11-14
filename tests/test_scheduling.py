from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID

import pytest
from alter_domain.matcher import pair_rank
from alter_domain.scheduling import utc_slot
from alter_domain.settings import Settings
from alter_scheduler.main import main


def test_slot_uses_utc_quarter_hour():
    jst = timezone(timedelta(hours=9))
    assert utc_slot(datetime(2026, 11, 15, 0, 14, tzinfo=jst)) == (
        datetime(2026, 11, 14, 15, 14, tzinfo=UTC),
        60,
    )
    assert utc_slot(datetime(2026, 11, 15, 0, 15, tzinfo=jst))[1] == 61
    with pytest.raises(ValueError):
        utc_slot(datetime(2026, 11, 14))


def test_matcher_prefers_common_interests_then_novelty_and_replays_seed():
    pair = UUID(int=1), UUID(int=2)
    rank = pair_rank(pair, (["books", "books"], ["books"]), 0, 19)
    assert rank < pair_rank(pair, (["books"], ["music"]), 0, 19)
    assert rank < pair_rank(pair, (["books"], ["books"]), 1, 19)
    assert rank == pair_rank(pair[::-1], (["books"], ["books"]), 0, 19)
    assert rank[1] != pair_rank(pair, (["books"], ["books"]), 0, 20)[1]


@pytest.mark.parametrize("mode,enabled", [("mock", True), ("live", False)])
def test_scheduler_mock_or_disabled_never_connects(monkeypatch, mode, enabled):
    monkeypatch.setattr(
        "alter_scheduler.main.Settings",
        lambda: Settings(_env_file=None, alter_mode=mode, scheduler_enabled=enabled),
    )
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("alter_scheduler.main.create_db_engine", connect)
    main()
    connect.assert_not_called()


def test_scheduler_live_runs_once_without_llm_credentials(monkeypatch):
    settings = Settings(_env_file=None, alter_mode="live", scheduler_enabled=True)
    monkeypatch.setattr("alter_scheduler.main.Settings", lambda: settings)
    engine = Mock()
    monkeypatch.setattr("alter_scheduler.main.create_db_engine", Mock(return_value=engine))
    schedule = Mock(return_value=[])
    monkeypatch.setattr("alter_scheduler.main.schedule", schedule)
    main()
    schedule.assert_called_once()
    args, kwargs = schedule.call_args
    assert args[0] is engine
    assert args[1].tzinfo == UTC
    assert args[2] == settings.max_daily_cost_usd
    assert kwargs == {"batch_size": 10}
    engine.dispose.assert_called_once()


def test_scheduler_disposes_engine_and_propagates_failure(monkeypatch):
    monkeypatch.setattr(
        "alter_scheduler.main.Settings",
        lambda: Settings(_env_file=None, alter_mode="live", scheduler_enabled=True),
    )
    engine = Mock()
    monkeypatch.setattr("alter_scheduler.main.create_db_engine", Mock(return_value=engine))
    monkeypatch.setattr("alter_scheduler.main.schedule", Mock(side_effect=RuntimeError("DB")))
    with pytest.raises(RuntimeError, match="DB"):
        main()
    engine.dispose.assert_called_once()
