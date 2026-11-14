"""#28 × #20: Worker が会話を完了すると、同じトランザクションで Discord の配信行ができる。"""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from alter_domain.conversation import run_one
from alter_domain.llm import FakeLLM
from alter_domain.llm_selection import model_price
from alter_domain.scheduling import schedule
from sqlalchemy import Engine, text

GUILD = "200000000000000031"
CHANNEL = "300000000000000031"


class PricedFake(FakeLLM):
    price = model_price()


@pytest.fixture
def discord_world(engine: Engine, ai_pair: list[UUID]) -> Iterator[list[UUID]]:
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.discord_guilds (id, name, admin_discord_user_id) "
                "values (:g, '鯖', '400000000000000031')"
            ),
            {"g": GUILD},
        )
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, name, configured_by) "
                "values (:c, :g, 'ai', '400000000000000031')"
            ),
            {"c": CHANNEL, "g": GUILD},
        )
        for n, agent in enumerate(ai_pair):
            owner = conn.execute(
                text("select owner_id from public.agents where id = :a"), {"a": agent}
            ).scalar_one()
            conn.execute(
                text("update public.profiles set discord_dm_notifications = true where id = :p"),
                {"p": owner},
            )
            conn.execute(
                text(
                    "insert into public.discord_connections (profile_id, discord_user_id) "
                    "values (:p, :d)"
                ),
                {"p": owner, "d": f"10000000000000003{n}"},
            )
            conn.execute(
                text(
                    "insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g)"
                ),
                {"a": agent, "g": GUILD},
            )
        conn.execute(
            text(
                "update public.agent_settings set autonomy_enabled = true, discord_publish = true "
                "where agent_id = any(:a)"
            ),
            {"a": ai_pair},
        )
    yield ai_pair
    with engine.begin() as conn:
        conn.execute(
            text("delete from public.discord_deliveries where channel_id = :c"), {"c": CHANNEL}
        )
        conn.execute(text("delete from public.discord_channels where guild_id = :g"), {"g": GUILD})
        conn.execute(text("delete from public.discord_guilds where id = :g"), {"g": GUILD})


def _counts(engine: Engine) -> tuple[int, int]:
    with engine.begin() as conn:
        posts = conn.execute(
            text(
                "select count(*) from public.discord_deliveries "
                "where channel_id = :c and status = 'pending'"
            ),
            {"c": CHANNEL},
        ).scalar_one()
        dms = conn.execute(
            text(
                "select count(*) from public.discord_dm_deliveries "
                "where status = 'pending' and discord_user_id like '10000000000000003%'"
            )
        ).scalar_one()
    return int(posts), int(dms)


def test_completed_conversation_enqueues_post_and_dms_once(
    engine: Engine, discord_world: list[UUID]
) -> None:
    assert len(schedule(engine, datetime.now(UTC), Decimal("10"))) == 1
    assert _counts(engine) == (0, 0)  # 完了前は作らない
    assert run_one(engine, PricedFake())
    assert _counts(engine) == (1, 2)
    # 次の実行（完了済みの会話はもう取り出されない）でも増えない
    assert not run_one(engine, PricedFake())
    assert _counts(engine) == (1, 2)


def test_without_publish_consent_only_dms_are_enqueued(
    engine: Engine, discord_world: list[UUID]
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("update public.agent_settings set discord_publish = false where agent_id = :a"),
            {"a": discord_world[0]},
        )
    assert len(schedule(engine, datetime.now(UTC), Decimal("10"))) == 1
    assert run_one(engine, PricedFake())
    # 片方が公開に同意していない会話は投稿しない。DM（リンクだけ）は本人の設定どおり送る
    assert _counts(engine) == (0, 2)
