"""Transactional agent operations. The HTTP caller supplies an authorized owner and transaction."""

import uuid

from sqlalchemy import Connection, text

from alter_domain.agent_models import Agent, AgentDraftInput, ProfileInput, ProfileRevision
from alter_domain.profile_crypto import ProfileCipher

PROFILE_FIELDS = tuple(ProfileInput.model_fields)


class AgentNotFound(Exception):
    pass


class AgentConflict(Exception):
    pass


def owned(conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID, *, lock: bool = False) -> Agent:
    row = (
        conn.execute(
            text(
                "select id, name, status, approved_profile_version, created_at from public.agents "
                "where id=:id and owner_id=:owner and status <> 'deleted'"
                + (" for update" if lock else "")
            ),
            {"id": agent_id, "owner": owner},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AgentNotFound()
    return Agent.model_validate(dict(row))


def latest(conn: Connection, agent_id: uuid.UUID) -> ProfileRevision:
    row = (
        conn.execute(
            text(
                f"select {', '.join(PROFILE_FIELDS)}, version, approved_at "
                "from public.agent_profiles "
                "where agent_id=:id order by version desc limit 1"
            ),
            {"id": agent_id},
        )
        .mappings()
        .one()
    )
    return ProfileRevision.model_validate(dict(row))


def save_revision(
    conn: Connection,
    agent_id: uuid.UUID,
    version: int,
    data: AgentDraftInput,
    cipher: ProfileCipher | None,
) -> ProfileRevision:
    encrypted = None
    if data.private_info:
        if cipher is None:
            raise ValueError("Profile encryption is unavailable")
        encrypted = cipher.encrypt(data.private_info, agent_id, version)
    values = {k: getattr(data, k) for k in PROFILE_FIELDS}
    values.update(
        agent=agent_id,
        version=version,
        ciphertext=encrypted,
        key_version=1 if encrypted is not None else None,
    )
    conn.execute(
        text(
            f"insert into public.agent_profiles (agent_id, version, {', '.join(PROFILE_FIELDS)}, "
            "private_info_ciphertext, private_info_key_version) "
            f"values (:agent, :version, {', '.join(':' + k for k in PROFILE_FIELDS)}, "
            ":ciphertext, :key_version)"
        ),
        values,
    )
    return latest(conn, agent_id)


def create_agent(
    conn: Connection, owner: uuid.UUID, data: AgentDraftInput, cipher: ProfileCipher | None
) -> Agent:
    # Parent profile lock serializes concurrent creations for MVP's one-agent constraint.
    conn.execute(text("select id from public.profiles where id=:id for update"), {"id": owner})
    if conn.execute(text("select 1 from public.agents where owner_id=:id"), {"id": owner}).first():
        raise AgentConflict()
    agent_id = uuid.uuid4()
    conn.execute(
        text("insert into public.agents (id, owner_id, name) values (:id,:owner,:name)"),
        {"id": agent_id, "owner": owner, "name": data.name},
    )
    save_revision(conn, agent_id, 1, data, cipher)
    conn.execute(
        text("insert into public.agent_settings (agent_id) values (:id)"), {"id": agent_id}
    )
    return owned(conn, owner, agent_id)


def approve(conn: Connection, owner: uuid.UUID, agent_id: uuid.UUID, version: int) -> Agent:
    agent = owned(conn, owner, agent_id, lock=True)
    profile = latest(conn, agent_id)
    if profile.version != version:
        raise AgentConflict()
    if agent.approved_profile_version == version:
        return agent
    conn.execute(
        text(
            "update public.agent_profiles set approved_at=now() "
            "where agent_id=:id and version=:v and approved_at is null"
        ),
        {"id": agent_id, "v": version},
    )
    # Approval never opts the user into autonomous interaction.
    conn.execute(
        text(
            "update public.agents set approved_profile_version=:v, "
            "status=case when status='draft' then 'active' else status end where id=:id"
        ),
        {"id": agent_id, "v": version},
    )
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id,action,resource_type,resource_id,metadata) "
            "values (:owner,'profile.approve','agent',:id,"
            "jsonb_build_object('version',cast(:v as int)))"
        ),
        {"owner": owner, "id": str(agent_id), "v": version},
    )
    return owned(conn, owner, agent_id)
