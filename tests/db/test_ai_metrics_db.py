from alter_domain.metrics import snapshot
from sqlalchemy import Engine, text


def test_snapshot_is_read_only_and_contains_no_user_content(engine: Engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("set transaction isolation level repeatable read, read only")
        result = snapshot(conn)
        assert result.environment == "local"
        assert result.observation_days == 0
        assert not any(
            x in result.model_dump()
            for x in ("messages", "text", "agent_id", "owner_id", "private_info")
        )
        assert conn.execute(text("show transaction_read_only")).scalar_one() == "on"
