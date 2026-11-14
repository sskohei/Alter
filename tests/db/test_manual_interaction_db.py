from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain.conversation import run_one
from alter_domain.interaction import pair_allowed
from alter_domain.llm import FakeLLM
from alter_domain.llm_selection import model_price
from alter_domain.scheduling import select_due
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import text


class PricedFake(FakeLLM):
    price = model_price()


def setup_manual(engine, ai_pair):
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.agent_settings set autonomy_enabled=true,frequency_per_day=1,"
                "daily_budget_usd=1"
            )
        )
        return conn.execute(
            text("select owner_id from public.agents where id=:id"), {"id": ai_pair[0]}
        ).scalar_one()


def start(engine, owner, key, limit=Decimal("10")):
    with engine.begin() as conn:
        return select_due(conn, datetime.now(UTC), limit, owner_id=owner, manual_key=key)


def test_manual_repeats_beyond_daily_limit_and_same_slot_with_real_worker(engine, ai_pair):
    owner = setup_manual(engine, ai_pair)
    jobs = []
    for i in range(3):
        job = start(engine, owner, f"request-{i}")
        assert job is not None
        jobs.append(job)
        assert run_one(engine, PricedFake())
        assert start(engine, owner, f"request-{i}") == job
    assert len(set(jobs)) == 3
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("select count(*) from public.conversations where status='completed'")
            ).scalar_one()
            == 3
        )
        assert not pair_allowed(conn, tuple(ai_pair), datetime.now(UTC))


def test_concurrent_manual_requests_create_one_job_and_busy_pair_cannot_restart(engine, ai_pair):
    owner = setup_manual(engine, ai_pair)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: start(engine, owner, "same-request"), range(2)))
    assert jobs[0] is not None and jobs[0] == jobs[1]
    assert start(engine, owner, "new-request") is None
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("select count(*) from public.usage_records where kind='reserve'")
            ).scalar_one()
            == 2
        )


@pytest.mark.parametrize("condition", ["off", "blocked", "frequency_zero", "unapproved", "budget"])
def test_manual_preserves_consent_blocks_approval_opt_out_and_budget(engine, ai_pair, condition):
    owner = setup_manual(engine, ai_pair)
    with engine.begin() as conn:
        if condition == "off":
            conn.execute(text("update public.agent_settings set autonomy_enabled=false"))
        elif condition == "frequency_zero":
            conn.execute(text("update public.agent_settings set frequency_per_day=0"))
        elif condition == "unapproved":
            conn.execute(
                text("update public.agents set status='draft',approved_profile_version=null")
            )
        elif condition == "blocked":
            conn.execute(
                text(
                    "insert into public.agent_blocks (blocker_agent_id,blocked_agent_id) "
                    "values (:a,:b)"
                ),
                {"a": ai_pair[1], "b": ai_pair[0]},
            )
    assert (
        start(engine, owner, "request", Decimal("0") if condition == "budget" else Decimal("10"))
        is None
    )
    with engine.begin() as conn:
        assert conn.execute(text("select count(*) from public.conversations")).scalar_one() == 0
        assert conn.execute(text("select count(*) from public.usage_records")).scalar_one() == 0


def test_manual_cannot_schedule_a_pair_without_the_caller(engine, ai_pair):
    setup_manual(engine, ai_pair)
    assert start(engine, uuid4(), "request") is None


@pytest.mark.parametrize("invited", [True, False])
def test_manual_api_checks_real_invitation_and_commits_only_for_its_owner(engine, ai_pair, invited):
    owner = setup_manual(engine, ai_pair) if invited else uuid4()

    def auth(request):
        uid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "id": uid,
                "identities": [{"provider": "google", "id": uid, "identity_data": {"sub": uid}}],
            },
        )

    settings = Settings(
        _env_file=None,
        alter_mode="live",
        demo_manual_interactions_enabled=True,
        llm_enabled=True,
        anthropic_api_key="synthetic",
        supabase_secret_key="synthetic",
        database_url=engine.url.render_as_string(hide_password=False),
    )
    with httpx.Client(transport=httpx.MockTransport(auth)) as http:
        app = create_app(settings, http_client=http)
        app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
        with TestClient(app) as client:
            headers = {"Idempotency-Key": "api-request"}
            response = client.post("/api/v1/conversations/start", headers=headers)
            assert response.status_code == (202 if invited else 403)
            if invited:
                assert (
                    client.post("/api/v1/conversations/start", headers=headers).json()
                    == response.json()
                )
                with engine.connect() as conn:
                    assert conn.execute(
                        text(
                            "select 1 from public.conversation_participants p "
                            "join public.agents a on a.id=p.agent_id "
                            "where p.conversation_id=:id and a.owner_id=:owner"
                        ),
                        {"id": response.json()["id"], "owner": owner},
                    ).first()
            else:
                with engine.connect() as conn:
                    assert (
                        conn.execute(text("select count(*) from public.conversations")).scalar_one()
                        == 0
                    )
