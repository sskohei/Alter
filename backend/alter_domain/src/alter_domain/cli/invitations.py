"""運営専用 CLI: 招待の登録・一覧・identity 追加・失効（ADR-014）。

使い方（リポジトリ root で実行）::

    uv run alter-invitations --env local --operator sskohei list
    uv run alter-invitations --env local --operator sskohei register
    uv run alter-invitations --env local --operator sskohei add-identity <招待ID>
    uv run alter-invitations --env local --operator sskohei revoke <招待ID>

* 接続先は ``.env`` / 環境変数の ``DATABASE_URL`` と ``SUPABASE_URL``・``SUPABASE_SECRET_KEY``。
  ``--env`` で対象環境を明示し、接続先と食い違えば中止する。本番は環境名の入力で確認する。
* Supabase のユーザー ID（sub）はコマンド引数ではなく対話入力で受け取り、シェル履歴に残さない。
* 識別子（sub・provider ID）は ``list --show-identifiers`` を指定したときだけ表示する。
  ログファイルには何も書かない。
"""

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO
from urllib.parse import urlsplit

import httpx
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthAdminUnavailable, AuthUser, SupabaseAuthAdmin
from alter_domain.db import create_db_engine
from alter_domain.settings import Settings

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


@dataclass
class Deps:
    settings: Settings
    engine: Engine
    get_auth_user: Callable[[uuid.UUID], AuthUser | None]
    stdin: TextIO
    stdout: TextIO


class CliAbort(Exception):
    """利用者に理由を表示して終了する（業務データは変更しない）。"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alter-invitations", description="Alter 招待の運営専用 CLI（運営責任者のみ）"
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["local", "production"],
        help="対象環境。DATABASE_URL / SUPABASE_URL の接続先と一致しなければ中止する",
    )
    parser.add_argument("--operator", required=True, help="監査に記録する運営担当名（例: sskohei）")
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="招待の一覧（既定は内部招待IDと状態だけ）")
    listing.add_argument(
        "--show-identifiers",
        action="store_true",
        help="Supabase sub と provider ID も表示する（本人照合の作業中だけ使う）",
    )
    commands.add_parser("register", help="本人照合したうえで招待を登録・有効化する")
    add = commands.add_parser("add-identity", help="有効な招待に Google/GitHub を追加する")
    add.add_argument("invitation_id", type=uuid.UUID)
    revoke = commands.add_parser("revoke", help="招待を失効し、AI を停止する")
    revoke.add_argument("invitation_id", type=uuid.UUID)
    revoke.add_argument("--yes", action="store_true", help="確認の入力を省略する")
    return parser


def main(argv: Sequence[str] | None = None, *, deps: Deps | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        inv.validate_operator(args.operator)
    except inv.InvalidOperator as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if deps is None:
        settings = Settings()
        http = httpx.Client(follow_redirects=False)
        admin = SupabaseAuthAdmin(settings, http)
        deps = Deps(
            settings=settings,
            engine=create_db_engine(settings),
            get_auth_user=admin.get_user,
            stdin=sys.stdin,
            stdout=sys.stdout,
        )
    out = deps.stdout
    try:
        _check_target(args.env, deps)
        handler = {
            "list": _cmd_list,
            "register": _cmd_register,
            "add-identity": _cmd_add_identity,
            "revoke": _cmd_revoke,
        }[args.command]
        handler(args, deps)
        return EXIT_OK
    except CliAbort as exc:
        print(f"中止しました: {exc}", file=out)
        return EXIT_FAILED
    except inv.InvitationError as exc:
        print(f"失敗しました [{exc.code}]: {exc}", file=out)
        return EXIT_FAILED
    except AuthAdminUnavailable:
        print("失敗しました [auth_unavailable]: Supabase Auth に照会できません", file=out)
        return EXIT_FAILED


# -- 対象環境の確認 -------------------------------------------------------------


def _is_local(host: str | None) -> bool:
    return host is not None and host.lower() in _LOCAL_HOSTS


def _check_target(env: str, deps: Deps) -> None:
    db_host = make_url(deps.settings.database_url).host
    auth_host = urlsplit(deps.settings.supabase_url).hostname
    hosts_local = [_is_local(db_host), _is_local(auth_host)]
    if env == "local" and not all(hosts_local):
        raise CliAbort("--env local ですが、DB または Supabase の接続先がローカルではありません")
    if env == "production":
        if any(hosts_local):
            raise CliAbort("--env production ですが、DB または Supabase の接続先がローカルです")
        print(f"対象: production（DB ホスト: {db_host}）", file=deps.stdout)
        if _ask(deps, "本番に接続します。続けるには production と入力: ") != "production":
            raise CliAbort("確認の入力が一致しません")


def _ask(deps: Deps, prompt: str) -> str:
    print(prompt, end="", file=deps.stdout, flush=True)
    line = deps.stdin.readline()
    if not line:
        raise CliAbort("入力がありません")
    return line.strip()


# -- 失敗の監査（ロールバック後に別トランザクションで記録） -----------------------------


def _run_audited(
    deps: Deps,
    *,
    action: str,
    operator: str,
    invitation_id: uuid.UUID | None,
    work: Callable[..., object],
) -> object:
    try:
        with deps.engine.begin() as conn:
            return work(conn)
    except inv.InvitationError as exc:
        _record_failure(deps, action, operator, invitation_id, exc.code)
        raise
    except Exception:
        _record_failure(deps, action, operator, invitation_id, "unexpected_error")
        raise


def _record_failure(
    deps: Deps, action: str, operator: str, invitation_id: uuid.UUID | None, code: str
) -> None:
    try:
        with deps.engine.begin() as conn:
            inv.record_audit(
                conn,
                action=action,
                invitation_id=invitation_id,
                operator=operator,
                result="failure",
                code=code,
            )
    except Exception:
        # 監査の記録自体に失敗しても、元の失敗を隠さない
        print("警告: 失敗の監査を記録できませんでした", file=deps.stdout)


# -- コマンド -----------------------------------------------------------------


def _cmd_list(args: argparse.Namespace, deps: Deps) -> None:
    with deps.engine.connect() as conn:
        summaries = inv.list_invitations(conn)
        details = (
            [inv.get_invitation(conn, s.id) for s in summaries] if args.show_identifiers else []
        )
    active = sum(1 for s in summaries if s.status == "active")
    print(
        f"有効 {active} / {inv.MAX_ACTIVE_INVITATIONS} 人、全 {len(summaries)} 件", file=deps.stdout
    )
    for summary in summaries:
        revoked = f"  失効 {summary.revoked_at:%Y-%m-%d %H:%M}" if summary.revoked_at else ""
        providers = ",".join(summary.providers) or "-"
        print(f"{summary.id}  {summary.status:<7}  {providers}{revoked}", file=deps.stdout)
    for detail in details:
        print(f"\n{detail.summary.id}", file=deps.stdout)
        print(f"  sub: {detail.auth_user_id}", file=deps.stdout)
        for identity in detail.identities:
            print(f"  {identity.provider}: {identity.provider_user_id}", file=deps.stdout)


def _show_auth_user(deps: Deps, user: AuthUser) -> None:
    print("Supabase Auth の連携ログイン:", file=deps.stdout)
    for identity in user.identities:
        hint = f"（{identity.display_hint}）" if identity.display_hint else ""
        print(f"  {identity.provider}: {identity.provider_user_id}{hint}", file=deps.stdout)


def _read_sub(deps: Deps) -> uuid.UUID:
    raw = _ask(deps, "招待する人の Supabase ユーザー ID（sub）: ")
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise CliAbort("UUID の形式ではありません") from None


def _cmd_register(args: argparse.Namespace, deps: Deps) -> None:
    sub = _read_sub(deps)
    user = deps.get_auth_user(sub)
    if user is None:
        _record_failure(deps, "invitation.register", args.operator, None, "auth_user_not_found")
        raise inv.AuthUserNotFound()
    _show_auth_user(deps, user)
    try:
        inv.allowed_identities(user)
    except inv.InvitationError as exc:
        _record_failure(deps, "invitation.register", args.operator, None, exc.code)
        raise
    print(
        "上の情報が、招待した本人と別の連絡経路で確認したものと一致するか照合してください。",
        file=deps.stdout,
    )
    if _ask(deps, "一致していれば register と入力: ") != "register":
        raise CliAbort("確認の入力が一致しません")

    invitation_id = _run_audited(
        deps,
        action="invitation.register",
        operator=args.operator,
        invitation_id=None,
        work=lambda conn: inv.register_invitation(conn, user, operator=args.operator),
    )
    print(f"登録しました。招待ID: {invitation_id}", file=deps.stdout)


def _cmd_add_identity(args: argparse.Namespace, deps: Deps) -> None:
    with deps.engine.connect() as conn:
        detail = inv.get_invitation(conn, args.invitation_id)
    if detail.summary.status != "active":
        raise inv.InvitationRevoked()
    user = deps.get_auth_user(detail.auth_user_id)
    if user is None:
        raise inv.AuthUserNotFound()
    _show_auth_user(deps, user)
    print(
        "新しく追加されたログインが本人のものか、別の連絡経路で照合してください。", file=deps.stdout
    )
    if _ask(deps, "一致していれば add と入力: ") != "add":
        raise CliAbort("確認の入力が一致しません")

    added = _run_audited(
        deps,
        action="invitation.add_identity",
        operator=args.operator,
        invitation_id=args.invitation_id,
        work=lambda conn: inv.add_identities(
            conn, args.invitation_id, user, operator=args.operator
        ),
    )
    assert isinstance(added, tuple)
    print(f"追加しました: {', '.join(added)}", file=deps.stdout)


def _cmd_revoke(args: argparse.Namespace, deps: Deps) -> None:
    if (
        not args.yes
        and _ask(deps, f"招待 {args.invitation_id} を失効します。revoke と入力: ") != "revoke"
    ):
        raise CliAbort("確認の入力が一致しません")
    result = _run_audited(
        deps,
        action="invitation.revoke",
        operator=args.operator,
        invitation_id=args.invitation_id,
        work=lambda conn: inv.revoke_invitation(conn, args.invitation_id, operator=args.operator),
    )
    assert isinstance(result, inv.RevokeResult)
    if result.already_revoked:
        print(
            f"すでに失効済みです（失効日時 {result.revoked_at:%Y-%m-%d %H:%M}）", file=deps.stdout
        )
    else:
        print(f"失効しました。停止した AI: {result.paused_agents} 体", file=deps.stdout)


def run() -> None:
    sys.exit(main())
