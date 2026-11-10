"""招待の共通ドメイン処理（ADR-014、docs/23-invitation-consent-operations.md）。

API の共通認可（``evaluate_access``）と運営専用 CLI（登録・一覧・identity 追加・失効）が
同じ関数を使う。各関数は呼出側が開始したトランザクション（``Connection``）の中で動き、
コミット・ロールバックは呼出側が行う。

監査（``audit_events``）には内部招待 ID・操作種別・運営担当・結果だけを記録し、
provider ID・Supabase sub・トークンを複製しない。
"""

import enum
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from alter_domain.auth_admin import AuthIdentity, AuthUser

MAX_ACTIVE_INVITATIONS = 10
SUPPORTED_PROVIDERS = frozenset({"google", "github"})
_OPERATOR_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


# -- エラー -------------------------------------------------------------------


class InvitationError(Exception):
    """運営操作の業務エラー。``code`` は識別子を含まない固定値（監査に記録する）。"""

    code = "invitation_error"
    message = "Invitation operation failed"

    def __str__(self) -> str:
        return self.message


class CapacityReached(InvitationError):
    code = "capacity_reached"
    message = f"有効な招待が上限（{MAX_ACTIVE_INVITATIONS}人）に達しています"


class AlreadyInvited(InvitationError):
    code = "already_invited"
    message = "この Supabase ユーザーにはすでに招待があります（失効済みを含む）"


class IdentityTaken(InvitationError):
    code = "identity_taken"
    message = "このプロバイダー ID は別の招待に登録済みです"


class InvitationNotFound(InvitationError):
    code = "invitation_not_found"
    message = "招待が見つかりません"


class InvitationRevoked(InvitationError):
    code = "invitation_revoked"
    message = "失効済みの招待は変更できません。再参加は新しい本人照合と招待から行います"


class AuthUserNotFound(InvitationError):
    code = "auth_user_not_found"
    message = "Supabase Auth にユーザーが見つかりません"


class UnsupportedIdentity(InvitationError):
    code = "unsupported_identity"
    message = "Google / GitHub 以外のログイン方法が連携されているため招待できません"


class NoAllowedIdentity(InvitationError):
    code = "no_allowed_identity"
    message = "Google / GitHub のログイン情報がありません"


class IdentityMismatch(InvitationError):
    code = "identity_mismatch"
    message = "招待の Supabase ユーザーと Auth の情報が一致しません"


class NothingToAdd(InvitationError):
    code = "nothing_to_add"
    message = "追加できる新しい Google / GitHub のログイン情報がありません"


class InvalidOperator(InvitationError):
    code = "invalid_operator"
    message = "運営担当名は英数字と _.- の 40 文字以内で指定してください"


class InvitationDataError(Exception):
    """招待データの不整合（認可は 503 で止める）。"""


# -- 結果の型 -----------------------------------------------------------------


class AccessDecision(enum.Enum):
    ALLOWED = "allowed"
    NOT_INVITED = "not_invited"


@dataclass(frozen=True)
class InvitationSummary:
    id: uuid.UUID
    status: str
    revoked_at: datetime | None
    providers: tuple[str, ...]


@dataclass(frozen=True)
class InvitationDetail:
    summary: InvitationSummary
    auth_user_id: uuid.UUID
    identities: tuple[AuthIdentity, ...]


@dataclass(frozen=True)
class RevokeResult:
    invitation_id: uuid.UUID
    already_revoked: bool
    revoked_at: datetime
    paused_agents: int


# -- 共通 ---------------------------------------------------------------------


def validate_operator(operator: str) -> str:
    if not _OPERATOR_PATTERN.fullmatch(operator):
        raise InvalidOperator()
    return operator


def allowed_identities(user: AuthUser) -> tuple[AuthIdentity, ...]:
    """Auth ユーザーの identity がすべて Google/GitHub であることを確認して返す。"""
    if any(identity.provider not in SUPPORTED_PROVIDERS for identity in user.identities):
        raise UnsupportedIdentity()
    if not user.identities:
        raise NoAllowedIdentity()
    providers = [identity.provider for identity in user.identities]
    if len(providers) != len(set(providers)):
        # 同じプロバイダーの複数アカウントは 1 人の招待に登録しない
        raise UnsupportedIdentity()
    return user.identities


def record_audit(
    conn: Connection,
    *,
    action: str,
    invitation_id: uuid.UUID | None,
    operator: str,
    result: str,
    code: str | None = None,
) -> None:
    """運営操作の監査。識別子（sub / provider ID）は書かない。"""
    metadata: dict[str, str] = {"operator": operator, "result": result}
    if code is not None:
        metadata["code"] = code
    conn.execute(
        text(
            "insert into public.audit_events (action, resource_type, resource_id, metadata) "
            "values (:action, 'invitation', :resource_id, cast(:metadata as jsonb))"
        ),
        {
            "action": action,
            "resource_id": str(invitation_id) if invitation_id else "-",
            "metadata": _json(metadata),
        },
    )


def _json(values: dict[str, str]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def _lock_capacity(conn: Connection) -> None:
    conn.execute(text("select public.lock_invitation_capacity()"))


def _registered_identities(conn: Connection, invitation_id: uuid.UUID) -> set[tuple[str, str]]:
    rows = conn.execute(
        text(
            "select provider, provider_user_id from public.invitation_identities "
            "where invitation_id = :id"
        ),
        {"id": invitation_id},
    )
    return {(row.provider, row.provider_user_id) for row in rows}


def _insert_identities(
    conn: Connection, invitation_id: uuid.UUID, identities: tuple[AuthIdentity, ...]
) -> None:
    for identity in identities:
        try:
            with conn.begin_nested():
                conn.execute(
                    text(
                        "insert into public.invitation_identities "
                        "(invitation_id, provider, provider_user_id) "
                        "values (:invitation_id, :provider, :provider_user_id)"
                    ),
                    {
                        "invitation_id": invitation_id,
                        "provider": identity.provider,
                        "provider_user_id": identity.provider_user_id,
                    },
                )
        except IntegrityError as exc:
            if "invitation_identities_provider_user_key" in str(exc.orig):
                raise IdentityTaken() from None
            raise


# -- 運営操作 -----------------------------------------------------------------


def register_invitation(conn: Connection, user: AuthUser, *, operator: str) -> uuid.UUID:
    """本人照合済みの Auth ユーザーを招待として有効化する。

    Auth ユーザーの Google/GitHub identity をすべて登録する。10 人枠は共通ロックの下で確認する。
    """
    validate_operator(operator)
    identities = allowed_identities(user)
    _lock_capacity(conn)

    existing = conn.execute(
        text("select id from public.invitations where auth_user_id = :sub"), {"sub": user.id}
    ).first()
    if existing is not None:
        raise AlreadyInvited()

    active = conn.execute(
        text("select count(*) from public.invitations where status = 'active'")
    ).scalar_one()
    if active >= MAX_ACTIVE_INVITATIONS:
        raise CapacityReached()

    invitation_id = uuid.uuid4()
    conn.execute(
        text("insert into public.invitations (id, auth_user_id) values (:id, :sub)"),
        {"id": invitation_id, "sub": user.id},
    )
    _insert_identities(conn, invitation_id, identities)
    record_audit(
        conn,
        action="invitation.register",
        invitation_id=invitation_id,
        operator=operator,
        result="success",
    )
    return invitation_id


def get_invitation(conn: Connection, invitation_id: uuid.UUID) -> InvitationDetail:
    row = conn.execute(
        text("select id, auth_user_id, status, revoked_at from public.invitations where id = :id"),
        {"id": invitation_id},
    ).first()
    if row is None:
        raise InvitationNotFound()
    identities = tuple(
        AuthIdentity(provider=provider, provider_user_id=provider_user_id)
        for provider, provider_user_id in sorted(_registered_identities(conn, invitation_id))
    )
    return InvitationDetail(
        summary=InvitationSummary(
            id=row.id,
            status=row.status,
            revoked_at=row.revoked_at,
            providers=tuple(identity.provider for identity in identities),
        ),
        auth_user_id=row.auth_user_id,
        identities=identities,
    )


def add_identities(
    conn: Connection, invitation_id: uuid.UUID, user: AuthUser, *, operator: str
) -> tuple[str, ...]:
    """有効な招待に、同じ Supabase ユーザーへ新しく連携された Google/GitHub を追加する。"""
    validate_operator(operator)
    row = conn.execute(
        text("select auth_user_id, status from public.invitations where id = :id for update"),
        {"id": invitation_id},
    ).first()
    if row is None:
        raise InvitationNotFound()
    if row.status != "active":
        raise InvitationRevoked()
    if row.auth_user_id != user.id:
        raise IdentityMismatch()

    identities = allowed_identities(user)
    registered = _registered_identities(conn, invitation_id)
    registered_providers = {provider for provider, _ in registered}
    new = tuple(
        identity
        for identity in identities
        if (identity.provider, identity.provider_user_id) not in registered
    )
    if any(identity.provider in registered_providers for identity in new):
        # 同じプロバイダーの別アカウントへの差し替えは失効と再招待で行う
        raise IdentityMismatch()
    if not new:
        raise NothingToAdd()
    _insert_identities(conn, invitation_id, new)
    record_audit(
        conn,
        action="invitation.add_identity",
        invitation_id=invitation_id,
        operator=operator,
        result="success",
    )
    return tuple(identity.provider for identity in new)


def revoke_invitation(conn: Connection, invitation_id: uuid.UUID, *, operator: str) -> RevokeResult:
    """招待を失効し、同じトランザクションで AI を停止して監査を残す。

    再実行は冪等で、最初の失効日時を変えない。
    """
    validate_operator(operator)
    row = conn.execute(
        text(
            "select auth_user_id, status, revoked_at from public.invitations "
            "where id = :id for update"
        ),
        {"id": invitation_id},
    ).first()
    if row is None:
        raise InvitationNotFound()

    if row.status == "revoked":
        record_audit(
            conn,
            action="invitation.revoke",
            invitation_id=invitation_id,
            operator=operator,
            result="noop",
        )
        return RevokeResult(invitation_id, True, row.revoked_at, 0)

    revoked_at: datetime = conn.execute(
        text(
            "update public.invitations set status = 'revoked', revoked_at = now() "
            "where id = :id returning revoked_at"
        ),
        {"id": invitation_id},
    ).scalar_one()
    paused = stop_agents_for_owner(conn, row.auth_user_id)
    cancel_pending_deliveries(conn, row.auth_user_id)
    record_audit(
        conn,
        action="invitation.revoke",
        invitation_id=invitation_id,
        operator=operator,
        result="success",
    )
    return RevokeResult(invitation_id, False, revoked_at, paused)


def stop_agents_for_owner(conn: Connection, owner_id: uuid.UUID) -> int:
    """利用者の AI を止める（自律交流 OFF、交流中なら paused）。停止した AI の数を返す。"""
    conn.execute(
        text(
            "update public.agent_settings s set autonomy_enabled = false "
            "from public.agents a where a.id = s.agent_id and a.owner_id = :owner"
        ),
        {"owner": owner_id},
    )
    result = conn.execute(
        text(
            "update public.agents set status = 'paused' "
            "where owner_id = :owner and status = 'active'"
        ),
        {"owner": owner_id},
    )
    return result.rowcount


def cancel_pending_deliveries(conn: Connection, owner_id: uuid.UUID) -> None:
    """未配信の取消。対象テーブルを追加する #13 / #26 / #28 でここに処理を足す。

    失効・削除要求と同じトランザクションで呼ばれることを前提にする。
    """
    del conn, owner_id


def list_invitations(conn: Connection) -> list[InvitationSummary]:
    rows = conn.execute(
        text(
            "select i.id, i.status, i.revoked_at, "
            "coalesce(array_agg(ii.provider order by ii.provider) "
            "filter (where ii.provider is not null), '{}') as providers "
            "from public.invitations i "
            "left join public.invitation_identities ii on ii.invitation_id = i.id "
            "group by i.id order by i.status, i.id"
        )
    )
    return [
        InvitationSummary(
            id=row.id, status=row.status, revoked_at=row.revoked_at, providers=tuple(row.providers)
        )
        for row in rows
    ]


# -- API の共通認可 -------------------------------------------------------------


def evaluate_access(conn: Connection, sub: uuid.UUID, auth_user: AuthUser | None) -> AccessDecision:
    """有効な招待と Auth 側 identity を照合する（毎リクエスト、キャッシュしない）。

    * Auth ユーザーがいない・招待がない・失効済み → NOT_INVITED
    * Auth 側に Google/GitHub 以外の identity がある → NOT_INVITED（判定不能も拒否）
    * Auth 側の Google/GitHub identity が 1 つでも招待に未登録 → NOT_INVITED
    * 招待データの不整合 → InvitationDataError（呼出側で 503）
    """
    if auth_user is None:
        return AccessDecision.NOT_INVITED
    if auth_user.id != sub:
        raise InvitationDataError("Auth user does not match the token subject")

    row = conn.execute(
        text("select id, status from public.invitations where auth_user_id = :sub"),
        {"sub": sub},
    ).first()
    if row is None or row.status != "active":
        return AccessDecision.NOT_INVITED

    registered = _registered_identities(conn, row.id)
    if not registered:
        raise InvitationDataError("active invitation has no identities")
    if not auth_user.identities:
        return AccessDecision.NOT_INVITED
    for identity in auth_user.identities:
        if identity.provider not in SUPPORTED_PROVIDERS:
            return AccessDecision.NOT_INVITED
        if (identity.provider, identity.provider_user_id) not in registered:
            return AccessDecision.NOT_INVITED
    return AccessDecision.ALLOWED
