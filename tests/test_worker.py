import signal
from unittest.mock import Mock

import pytest
from alter_domain.settings import Settings
from alter_domain.sonnet import LLMUnavailable
from alter_worker.main import main


@pytest.mark.parametrize("mode,enabled", [("mock", True), ("live", False)])
def test_mock_or_disabled_worker_never_connects(monkeypatch, mode, enabled):
    monkeypatch.setattr("sys.argv", ["alter-worker", "--once"])
    monkeypatch.setattr(
        "alter_worker.main.Settings",
        lambda: Settings(
            _env_file=None,
            alter_mode=mode,
            llm_enabled=enabled,
        ),
    )
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("alter_worker.main.create_db_engine", connect)
    main()
    connect.assert_not_called()


def test_live_worker_requires_credentials_before_connecting(monkeypatch):
    monkeypatch.setattr("sys.argv", ["alter-worker", "--once"])
    monkeypatch.setattr(
        "alter_worker.main.Settings",
        lambda: Settings(
            _env_file=None,
            alter_mode="live",
            llm_enabled=True,
        ),
    )
    connect = Mock()
    monkeypatch.setattr("alter_worker.main.create_db_engine", connect)
    with pytest.raises(LLMUnavailable):
        main()
    connect.assert_not_called()


def test_live_entrypoint_runs_independently_and_stops_on_signal(monkeypatch):
    monkeypatch.setattr("sys.argv", ["alter-worker"])
    monkeypatch.setattr(
        "alter_worker.main.Settings",
        lambda: Settings(
            _env_file=None,
            alter_mode="live",
            llm_enabled=True,
            anthropic_api_key="test-only",
        ),
    )
    handlers = {}
    monkeypatch.setattr(
        "alter_worker.main.signal.signal", lambda sig, fn: handlers.update({sig: fn})
    )
    engine, http = Mock(), Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("alter_worker.main.create_db_engine", Mock(return_value=engine))
    monkeypatch.setattr("alter_worker.main.httpx.Client", Mock(return_value=http))

    def process(db, client, **kwargs):
        assert db is engine
        assert client.price.provider == "anthropic"
        assert not kwargs["stopped"].is_set()
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert kwargs["stopped"].is_set()
        return True

    enrich = Mock(return_value=0)
    monkeypatch.setattr("alter_worker.main.enrich_pending", enrich)
    run = Mock(side_effect=process)
    monkeypatch.setattr("alter_worker.main.run_one", run)
    main()
    run.assert_called_once()
    enrich.assert_called_once_with(engine, limit=1, forbidden=("test-only",))
    engine.dispose.assert_called_once()
    http.__exit__.assert_called_once()


def test_heartbeat_renews_every_25_seconds_and_marks_lost_on_failure(monkeypatch):
    from datetime import UTC, datetime
    from uuid import uuid4

    from alter_domain.conversation import Heartbeat, Lease

    now = datetime.now(UTC)
    lease = Lease(uuid4(), uuid4(), uuid4(), 1)
    heartbeat = Heartbeat(Mock(), lease, lambda: now)
    heartbeat._stop = Mock()
    heartbeat._stop.wait.side_effect = [False, False]
    renew = Mock(side_effect=[True, False])
    monkeypatch.setattr("alter_domain.conversation.renew", renew)
    heartbeat._run()
    assert heartbeat.lost.is_set()
    assert renew.call_count == 2
    assert all(call.args == (25,) for call in heartbeat._stop.wait.call_args_list)
