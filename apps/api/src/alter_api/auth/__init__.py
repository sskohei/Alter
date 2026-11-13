"""認証: Supabase access token を検証し、本人（Principal）を業務処理へ渡す。

``CurrentPrincipal`` は JWT の検証だけを行う。一般業務ルーターは、招待・停止/削除の
判定（docs/23）まで済ませた ``alter_api.business.BusinessConnection`` も引数に取る。
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from alter_api.auth.jwt import JwtVerifier, Principal, unauthenticated

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
    if credentials is None:
        raise unauthenticated()
    verifier: JwtVerifier = request.app.state.jwt_verifier
    return verifier.verify(credentials.credentials)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
