"""運営専用 CLI: 状態の確認と、Discord 配送の要確認の照合（#36、docs/27）。

使い方（リポジトリ root で実行）::

    uv run alter-ops --env local --operator sskohei status
    uv run alter-ops --env local --operator sskohei status --json
    uv run alter-ops --env local --operator sskohei review-list
    uv run alter-ops --env local --operator sskohei resolve-post <配信ID> sent \\
        --thread-id <スレッドID> --message-id <最初の発言ID>
    uv run alter-ops --env local --operator sskohei resolve-post <配信ID> cancelled
    uv run alter-ops --env local --operator sskohei resolve-dm <配信ID> sent --message-id <ID>
    uv run alter-ops --env local --operator sskohei choose-target <会話ID> <チャンネルID>
    uv run alter-ops --env local --operator sskohei llm-list
    uv run alter-ops --env local --operator sskohei llm-settle <実行ID> charged \\
        --input-tokens <入力> --output-tokens <出力>
    uv run alter-ops --env local --operator sskohei llm-settle <実行ID> not_charged

* ``status`` は件数だけを表示し、しきい値を超えたものがあれば終了コード 3 を返す
  （手元 PC の OS の定期実行や監視から呼ぶ）。本番でも確認の入力は求めない（読み取りのみ）
* 照合系は本番では環境名の入力で確認する。Discord へ送り直す操作は持たない
* 本文・表示名・利用者 ID・Discord のユーザー ID は表示しない
"""

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TextIO

from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from alter_domain import llm_reconcile, ops
from alter_domain.db import create_db_engine
from alter_domain.invitations import InvalidOperator, validate_operator
from alter_domain.settings import Settings

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ALERT = 3

_WRITE_COMMANDS = frozenset({"resolve-post", "resolve-dm", "choose-target", "llm-settle"})


@dataclass
class Deps:
    settings: Settings
    engine: Engine
    stdin: TextIO
    stdout: TextIO
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


class CliAbort(Exception):
    """理由を表示して終了する（業務データは変更しない）。"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alter-ops", description="Alter 運用の確認と照合")
    parser.add_argument("--env", required=True, choices=["local", "production"])
    parser.add_argument("--operator", required=True, help="監査に記録する運営担当名")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="件数と警告（警告があれば終了コード 3）")
    status.add_argument("--json", action="store_true", help="JSON で出力する")
    commands.add_parser("review-list", help="Discord 配送の要確認（needs_review）の一覧")

    post = commands.add_parser("resolve-post", help="要確認の投稿を確定する")
    post.add_argument("delivery_id", type=uuid.UUID)
    post.add_argument("outcome", choices=["sent", "cancelled"])
    post.add_argument("--thread-id")
    post.add_argument("--message-id")

    dm = commands.add_parser("resolve-dm", help="要確認の DM を確定する")
    dm.add_argument("delivery_id", type=uuid.UUID)
    dm.add_argument("outcome", choices=["sent", "cancelled"])
    dm.add_argument("--message-id")

    choose = commands.add_parser("choose-target", help="投稿先が複数で保留した会話の投稿先を選ぶ")
    choose.add_argument("conversation_id", type=uuid.UUID)
    choose.add_argument("channel_id")

    commands.add_parser("llm-list", help="LLM 呼び出しの未確定の一覧")
    settle = commands.add_parser("llm-settle", help="結果不明の LLM 呼び出しを確定する")
    settle.add_argument("run_id", type=uuid.UUID)
    settle.add_argument("outcome", choices=["charged", "not_charged"])
    settle.add_argument("--input-tokens", type=int, default=0)
    settle.add_argument("--output-tokens", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None, *, deps: Deps | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        validate_operator(args.operator)
    except InvalidOperator:
        print("エラー: --operator の形式が正しくありません", file=sys.stderr)
        return EXIT_USAGE
    if deps is None:
        settings = Settings()
        deps = Deps(settings, create_db_engine(settings), sys.stdin, sys.stdout)
    out = deps.stdout
    try:
        _check_target(args.env, args.command, deps)
        if args.command == "status":
            return _status(args, deps)
        if args.command == "review-list":
            _review_list(deps)
        elif args.command == "llm-list":
            _llm_list(deps)
        else:
            _resolve(args, deps)
            print("記録しました", file=out)
        return EXIT_OK
    except CliAbort as exc:
        print(f"中止しました: {exc}", file=out)
        return EXIT_FAILED
    except ops.ReviewNotFound:
        print("失敗しました [not_found]: 対象が見つからないか、要確認ではありません", file=out)
        return EXIT_FAILED
    except ops.InvalidResolution as exc:
        print(f"失敗しました [invalid]: {exc}", file=out)
        return EXIT_FAILED
    except llm_reconcile.RunNotFound:
        print("失敗しました [not_found]: 対象が見つからないか、未確定ではありません", file=out)
        return EXIT_FAILED
    except llm_reconcile.CannotSettle as exc:
        print(f"失敗しました [{exc.code}]: 今は確定できません（docs/27 §4）", file=out)
        return EXIT_FAILED


def _check_target(env: str, command: str, deps: Deps) -> None:
    host = make_url(deps.settings.database_url).host
    local = host is None or host in _LOCAL_HOSTS
    if env == "local" and not local:
        raise CliAbort("--env local ですが、DB の接続先がローカルではありません")
    if env == "production":
        if local:
            raise CliAbort("--env production ですが、DB の接続先がローカルです")
        if command in _WRITE_COMMANDS:
            print(f"対象: production（DB ホスト: {host}）", file=deps.stdout)
            print("続けるには production と入力: ", end="", file=deps.stdout, flush=True)
            if deps.stdin.readline().strip() != "production":
                raise CliAbort("確認の入力が一致しません")


def _status(args: argparse.Namespace, deps: Deps) -> int:
    with deps.engine.connect() as conn:
        snap = ops.snapshot(conn, deps.now, max_daily_cost=deps.settings.max_daily_cost_usd)
    found = ops.alerts(snap)
    out = deps.stdout
    if args.json:
        print(json.dumps({**snap, "alerts": found}, ensure_ascii=False, sort_keys=True), file=out)
    else:
        for kind, j in snap["jobs"].items():
            print(
                f"job {kind}: 待ち {j['due']}（最古 {j['oldest_due_minutes']} 分）"
                f" / リース切れ {j['lease_expired']} / 24h 失敗 {j['failed_24h']}"
                f" / 24h 完了 {j['completed_24h']}",
                file=out,
            )
        llm = snap["llm_unsettled"]
        print(
            "LLM 未確定: "
            + ", ".join(f"{k} {v}" for k, v in llm.items())
            + f"（止まっているもの {snap['llm_stale']}）",
            file=out,
        )
        cost = snap["cost"]
        print(
            f"費用（UTC 今日）: 確定 ${cost['charged_today_usd']} / 見込み ${cost['exposure_usd']}"
            f" / 上限 ${cost['daily_limit_usd']}",
            file=out,
        )
        d = snap["discord"]
        print(
            f"Discord 要確認: 投稿 {d['posts']['needs_review']} / DM {d['dms']['needs_review']}",
            file=out,
        )
        dl = snap["deletion"]
        print(
            f"削除要求: 処理中 {dl['open']}（最古 {dl['oldest_open_days']} 日）"
            f" / 失敗 {dl['failed']}",
            file=out,
        )
        print("警告: " + (", ".join(found) if found else "なし"), file=out)
    return EXIT_ALERT if found else EXIT_OK


def _review_list(deps: Deps) -> None:
    with deps.engine.connect() as conn:
        items = ops.list_review(conn)
    if not items:
        print("要確認はありません", file=deps.stdout)
    for item in items:
        print(
            f"{item.kind}\t{item.id}\t会話 {item.conversation_id or '-'}\t"
            f"チャンネル {item.channel_id or '-'}\tスレッド {item.thread_id or '-'}\t"
            f"{item.last_error or '-'}\t試行 {item.attempts}\t{item.updated_at.isoformat()}",
            file=deps.stdout,
        )


def _llm_list(deps: Deps) -> None:
    with deps.engine.connect() as conn:
        runs = llm_reconcile.list_unsettled(conn, deps.now)
    if not runs:
        print("未確定の LLM 呼び出しはありません", file=deps.stdout)
    for run in runs:
        print(
            f"{run.id}\t{run.purpose}\t{run.status}\t段階 {run.phase or '-'}\t"
            f"会話 {run.conversation_id or '-'}（{run.conversation_status or '-'}）\t"
            f"予約 ${run.reserved_usd}\t{'確定可' if run.stale else '進行中の可能性'}\t"
            f"{run.updated_at.isoformat()}",
            file=deps.stdout,
        )


def _resolve(args: argparse.Namespace, deps: Deps) -> None:
    with deps.engine.begin() as conn:
        if args.command == "llm-settle":
            llm_reconcile.settle(
                conn,
                args.run_id,
                args.outcome,
                operator=args.operator,
                now=deps.now,
                input_tokens=args.input_tokens,
                output_tokens=args.output_tokens,
            )
        elif args.command == "resolve-post":
            ops.resolve_post(
                conn,
                args.delivery_id,
                args.outcome,
                operator=args.operator,
                thread_id=args.thread_id,
                message_id=args.message_id,
            )
        elif args.command == "resolve-dm":
            ops.resolve_dm(
                conn,
                args.delivery_id,
                args.outcome,
                operator=args.operator,
                message_id=args.message_id,
            )
        else:
            ops.choose_target(conn, args.conversation_id, args.channel_id, operator=args.operator)


def run() -> None:
    sys.exit(main())
