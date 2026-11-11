from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID

import pytest
from alter_domain.budget import BudgetExceeded, exposure, reserve
from alter_domain.llm import FakeLLM
from sqlalchemy import Engine, text


def test_parallel_reservation_never_exceeds_global_cap(engine: Engine, ai_pair: list[UUID]):
    def attempt(aid):
        try:
            with engine.begin() as conn:
                reserve(conn, aid, None, FakeLLM.price, str(aid), Decimal(".05"), Decimal(".05"))
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ai_pair)) == [False, True]
    with engine.begin() as conn:
        assert exposure(conn) == Decimal(".05")
        # A hold from yesterday still counts; unknown external outcomes are not auto-released.
        conn.execute(text("update public.usage_records set created_at=now()-interval '1 day'"))
        assert exposure(conn) == Decimal(".05")
        with pytest.raises(BudgetExceeded):
            reserve(
                conn, ai_pair[0], None, FakeLLM.price, "another", Decimal(".05"), Decimal(".05")
            )
