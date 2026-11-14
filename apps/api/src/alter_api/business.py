"""Per-request invitation authorization, locked with writes; no mock authorization bypass."""

from collections.abc import Iterator
from typing import Annotated

from alter_domain.auth_admin import AuthAdminUnavailable
from alter_domain.invitations import AccessDecision, InvitationDataError, evaluate_access
from fastapi import Depends, Request
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from alter_api.auth import CurrentPrincipal
from alter_api.auth.jwt import authorization_unavailable
from alter_api.errors import ApiError


def business_connection(request: Request, principal: CurrentPrincipal) -> Iterator[Connection]:
    settings = request.app.state.settings
    if settings.alter_mode != "live":
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Business persistence requires live mode")
    try:
        auth_user = request.app.state.auth_admin.get_user(principal.sub)
        with request.app.state.db_engine.begin() as conn:
            # Match invitation revocation's lock order; writes cannot race past revocation.
            invitation = conn.execute(
                text("select id,status from public.invitations where auth_user_id=:id for update"),
                {"id": principal.sub},
            ).first()
            profile = conn.execute(
                text("select deleted_at from public.profiles where id=:id for update"),
                {"id": principal.sub},
            ).first()
            if (profile is not None and profile.deleted_at is not None) or (
                invitation is not None and invitation.status == "revoked"
            ):
                raise ApiError(403, "ACCOUNT_INACTIVE", "Account is inactive")
            if evaluate_access(conn, principal.sub, auth_user) != AccessDecision.ALLOWED:
                raise ApiError(403, "INVITATION_REQUIRED", "An active invitation is required")
            # First authorized business request may bootstrap the profile, never a rejected request.
            conn.execute(
                text(
                    "insert into public.profiles (id,display_name) values (:id,'Alter user') "
                    "on conflict (id) do nothing"
                ),
                {"id": principal.sub},
            )
            yield conn
    except (AuthAdminUnavailable, InvitationDataError, SQLAlchemyError):
        raise authorization_unavailable() from None


BusinessConnection = Annotated[Connection, Depends(business_connection, scope="function")]
