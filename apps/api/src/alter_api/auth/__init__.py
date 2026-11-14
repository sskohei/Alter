"""認証: Supabase access token を検証し、本人（Principal）を業務処理へ渡す。

``CurrentPrincipal`` は JWT の検証とレート制限（#32、``alter_api.rate_limit``）だけを行う。
一般業務ルーターは、招待・停止/削除の判定（docs/23）まで済ませた
``alter_api.business.BusinessConnection`` も引数に取る。
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from alter_api import rate_limit
from alter_api.auth.jwt import JwtVerifier, Principal, unauthenticated
from alter_api.errors import ApiError

__all__ = ["CurrentPrincipal", "JwtVerifier", "Principal", "get_principal"]

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="SupabaseBearer",
    description="Supabase Auth の access token（refresh token は送らない）",
)


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    # 認証に失敗し続けている接続元は、JWT を検証する（外部へ問い合わせる）前に止める
    rate_limit.check_unauthenticated(request, count=False)
    if credentials is None:
        rate_limit.check_unauthenticated(request, count=True)
        raise unauthenticated()
    verifier: JwtVerifier = request.app.state.jwt_verifier
    try:
        principal = verifier.verify(credentials.credentials)
    except ApiError as exc:
        if exc.status_code == 401:
            rate_limit.check_unauthenticated(request, count=True)
        raise
    rate_limit.check_user(request, str(principal.sub))
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
