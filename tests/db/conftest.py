"""DB 統合テストの共通設定。

``ALTER_TEST_DATABASE_URL`` が設定されているときだけ実行する（CI の Migrations ジョブ）。
**招待 2 表と監査を空にするため、使い捨ての DB（ローカル Supabase など）にだけ向けること。**
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

TEST_DB_ENV = "ALTER_TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        if os.environ.get("ALTER_REQUIRE_DB_TESTS") == "1":
            pytest.fail(f"{TEST_DB_ENV} is required when ALTER_REQUIRE_DB_TESTS=1")
        pytest.skip(f"{TEST_DB_ENV} が未設定のため DB 統合テストを省略")
    eng = create_engine(url, hide_parameters=True, pool_size=20, max_overflow=0)
    yield eng
    eng.dispose()


@pytest.fixture
def created_users(engine: Engine) -> Iterator[list[uuid.UUID]]:
    """テスト中に作った auth.users を最後に消す（profiles / agents は CASCADE）。"""
    users: list[uuid.UUID] = []
    yield users
    with engine.begin() as conn:
        for user_id in users:
            conn.execute(text("delete from auth.users where id = :id"), {"id": user_id})


@pytest.fixture(autouse=True)
def clean_invitations(engine: Engine) -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text("delete from public.invitations"))
        conn.execute(text("delete from public.audit_events where resource_type = 'invitation'"))
    yield
    with engine.begin() as conn:
        conn.execute(text("delete from public.invitations"))
        conn.execute(text("delete from public.audit_events where resource_type = 'invitation'"))
