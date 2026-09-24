from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from turnstile_core.config import Settings, get_settings
from turnstile_core.persistence.auth_store import AuthStore

from ..services.auth_service import (
    AuthError,
    EntraTokenVerifier,
    hash_session_token,
    new_session_token,
    session_expiry,
    verify_password,
)
from .entra_roles import console_role
from .session import (
    AccessVerifier,
    Config,
    CurrentSession,
    Store,
    _bearer_token,
    identity_for_caller,
    require_allowed_write_origin,
    verify_caller,
)

router = APIRouter()


@lru_cache
def get_entra_verifier() -> EntraTokenVerifier:
    settings = get_settings()
    return EntraTokenVerifier(
        client_id=settings.entra_client_id,
        allowed_email_domains=tuple(settings.entra_allowed_email_domains),
        tenant_ids=tuple(settings.entra_tenant_ids),
    )


Verifier = Annotated[EntraTokenVerifier, Depends(get_entra_verifier)]


class PasswordLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class EntraLogin(BaseModel):
    id_token: str = Field(min_length=1)


class Profile(BaseModel):
    id: str
    email: str
    name: str | None
    role: Literal["owner", "member"]
    method: Literal["password", "entra"]
    session_expires_at: datetime


# Long enough to open a browser from a terminal, short enough that a link left in a history
# or a chat is already useless.
LOGIN_CODE_SECONDS = 60


class LoginCode(BaseModel):
    """A single-use code that opens a browser session, and when it stops working."""

    code: str
    expires_at: datetime


class LoginCodeRedeem(BaseModel):
    code: str = Field(min_length=20, max_length=200)


def _issue(
    response: Response, store: AuthStore, user: dict[str, Any], method: str, settings: Settings
) -> Profile:
    token, digest = new_session_token()
    authenticated_at = datetime.now(UTC)
    expires_at = session_expiry(
        settings.session_ttl_hours_for(str(user["role"])),
        authenticated_at,
    )
    store.delete_expired_sessions()
    store.create_session(
        user_id=user["id"],
        token_sha256=digest,
        method=method,
        authenticated_at=authenticated_at,
        expires_at=expires_at,
    )
    store.touch_last_login(user["id"])
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours_for(str(user["role"])) * 3600,
        httponly=True,
        secure=settings.production,
        samesite="lax",
        path="/",
    )
    return Profile(
        id=str(user["id"]),
        email=user["email"],
        name=user["display_name"],
        role=user["role"],
        method=method,  # type: ignore[arg-type]
        session_expires_at=expires_at,
    )


@router.post(
    "/api/v1/auth/login",
    response_model=Profile,
    dependencies=[Depends(require_allowed_write_origin)],
)
def login(body: PasswordLogin, response: Response, store: Store, settings: Config) -> Profile:
    user = store.find_user_by_email(body.email)
    if not user or not user["enabled"] or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="邮箱或密码不正确，请重新输入。")
    return _issue(response, store, user, "password", settings)


@router.post(
    "/api/v1/auth/entra",
    response_model=Profile,
    dependencies=[Depends(require_allowed_write_origin)],
)
def login_with_entra(
    body: EntraLogin, response: Response, store: Store, verifier: Verifier, settings: Config
) -> Profile:
    try:
        identity = verifier.verify(body.id_token)
    except AuthError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    role: str | None = None
    if settings.entra_admin_role:
        # Checked before any row is written: someone without a console role must not be
        # left with an account, even a disabled one, because the role assignment in Entra
        # is the only record of who may use this console.
        role = console_role(settings, identity.roles)
        if role is None:
            raise HTTPException(status_code=403, detail="此控制台仅限管理员使用。")
    user = store.upsert_entra_user(identity.email, identity.display_name, role=role)
    if not user.get("enabled", True):
        raise HTTPException(status_code=403, detail="该账户已被停用。")
    return _issue(response, store, user, "entra", settings)


@router.post(
    "/api/v1/auth/cli",
    response_model=LoginCode,
    dependencies=[Depends(require_allowed_write_origin)],
)
def begin_cli_sign_in(
    request: Request, store: Store, settings: Config, verifier: AccessVerifier
) -> LoginCode:
    """A single-use code that opens a browser session for the person holding this token.

    For a tenant where the web sign-in has no consent yet. The Azure CLI is pre-authorized
    on Turnstile's API, so `az account get-access-token` needs no consent, and its token is
    exchanged here for a code the browser redeems once, within a minute. The token is
    checked exactly as a script's is -- pinned tenant, a console role -- and only a person's
    token opens a browser session: a workload identity has no one to sign in.
    """
    token = _bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="未登录。")
    caller = verify_caller(token, settings, verifier)
    if not caller.delegated:
        raise HTTPException(
            status_code=403, detail="A workload identity cannot sign in to the console."
        )
    identity = identity_for_caller(caller, store, settings)
    code, digest = new_session_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=LOGIN_CODE_SECONDS)
    store.create_login_code(UUID(identity.id), digest, expires_at)
    return LoginCode(code=code, expires_at=expires_at)


@router.post(
    "/api/v1/auth/code",
    response_model=Profile,
    dependencies=[Depends(require_allowed_write_origin)],
)
def redeem_login_code(
    body: LoginCodeRedeem, response: Response, store: Store, settings: Config
) -> Profile:
    """Open the browser session a sign-in code was issued for. A code works once."""
    user = store.consume_login_code(hash_session_token(body.code))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "This sign-in link has expired or was already used. "
                "Run the sign-in command again."
            ),
        )
    return _issue(response, store, user, "entra", settings)


@router.get("/api/v1/auth/me", response_model=Profile)
def whoami(identity: CurrentSession) -> Profile:
    return Profile(
        id=identity.id,
        email=identity.email,
        name=identity.name,
        role=identity.role,
        method=identity.method,
        session_expires_at=identity.session_expires_at,
    )


@router.post(
    "/api/v1/auth/logout",
    status_code=204,
    dependencies=[Depends(require_allowed_write_origin)],
)
def logout(
    request: Request,
    response: Response,
    store: Store,
    settings: Config,
) -> None:
    session = request.cookies.get(settings.session_cookie_name)
    if session:
        store.delete_session(hash_session_token(session))
    response.delete_cookie(settings.session_cookie_name, path="/")
