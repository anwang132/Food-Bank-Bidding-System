from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.deps import Db, SessionDep, SettingsDep, commit_and_publish
from app.errors import Forbidden
from app.models import Organization
from app.schemas import LoginIn, MeOut, RegisterIn, UserOut
from app.security import SESSION_COOKIE
from app.services import auth
from app.services.common import Changes
from app.services.queries import org_out, user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _me(db: Db, resolved: auth.ResolvedSession) -> MeOut:
    org = None
    if resolved.user.organization_id is not None:
        o = await db.get(Organization, resolved.user.organization_id)
        org = org_out(o) if o else None
        if org is not None:  # members see their organization's name only
            org = org.model_copy(
                update={"default_weight": None, "default_priority": None, "version": None}
            )
    return MeOut(
        user=user_out(resolved.user),
        organization=org,
        csrf_token=resolved.session.csrf_token,
        session_expires_at=resolved.session.expires_at,
    )


@router.post("/login", response_model=MeOut)
async def login(body: LoginIn, response: Response, db: Db, settings: SettingsDep) -> MeOut:
    result = await auth.login(db, settings, body.email, body.password)
    await db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        result.token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return await _me(db, auth.ResolvedSession(result.session, result.user))


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: Db, resolved: SessionDep, settings: SettingsDep
) -> Response:
    changes = Changes()
    await auth.logout(db, resolved, changes)
    await commit_and_publish(request, db, changes)
    out = Response(status_code=204)
    out.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return out


@router.get("/me", response_model=MeOut)
async def me(db: Db, resolved: SessionDep) -> MeOut:
    return await _me(db, resolved)


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, db: Db, settings: SettingsDep) -> UserOut:
    if not settings.allow_registration:
        raise Forbidden("Self-registration is disabled", code="REGISTRATION_DISABLED")
    user = await auth.register_viewer(db, body.email, body.display_name, body.password)
    await db.commit()
    return user_out(user)
