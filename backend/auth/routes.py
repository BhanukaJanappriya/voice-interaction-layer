"""HTTP endpoints for user verification and admin sign in.

    POST /auth/verify        {phone, pin} -> {display_name, token, expires_at}
                             + httpOnly cookie user_session (SameSite=Lax)
    POST /auth/logout        clears user_session
    GET  /auth/me            {display_name} or 401
    POST /auth/admin/login   {username, password} -> {username, expires_at}
                             + httpOnly cookie admin_session (SameSite=Strict)
    POST /auth/admin/logout  clears admin_session
    GET  /auth/admin/me      {username} or 401

Failures: 401 for any wrong number/PIN or username/password (one generic
message), 423 with Retry-After when the account is locked, 429 with
Retry-After when this address is rate limited. A phone number that is not
a valid Sri Lankan landline gets 422 with the specific reason ("that looks
like a mobile number"): the format rules are public, so saying which rule
failed reveals nothing about which numbers are registered, and it saves a
real customer from burning PIN attempts on a typo. It is decided before
any account lookup, and it does not count against the rate limit because
no secret is checked.

The token is also returned in the body for the cross origin development
page (opened from port 5500), which keeps it in memory only and sends it
as Authorization: Bearer or as start_session's session_token. The same
origin page relies on the cookie alone.

Handlers are plain def on purpose: bcrypt and the database are blocking,
and FastAPI runs plain def handlers on its thread pool instead of the
event loop. PINs and passwords are never logged.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from typing import Optional

from pydantic import BaseModel, Field

import config
from auth.admins import verify_admin
from auth.dependencies import ADMIN_COOKIE, USER_COOKIE, client_ip, require_admin, verified_user_from_request
from auth.phone import InvalidPhoneNumber, normalize_sl_landline
from auth.tokens import AUDIENCE_ADMIN, AUDIENCE_USER, issue_token
from auth.users import AuthError, verify_user
from i18n import t

router = APIRouter(prefix="/auth", tags=["auth"])

_NO_STORE = {"Cache-Control": "no-store"}


class VerifyRequest(BaseModel):
    phone: str = Field(..., max_length=40)
    pin: str = Field(..., max_length=12)
    language: Optional[str] = Field(default=None, max_length=8)


class VerifyResponse(BaseModel):
    display_name: str
    token: str
    expires_at: int  # Unix timestamp (seconds)


class MeResponse(BaseModel):
    display_name: str


class AdminLoginRequest(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=200)


class AdminLoginResponse(BaseModel):
    username: str
    expires_at: int


class AdminMeResponse(BaseModel):
    username: str


def request_language(request: Request, body_language: Optional[str] = None) -> str:
    """The language of the visitor: the body's language when the request has one, else
    the first usable Accept-Language entry, else English."""
    wanted = (body_language or "").strip().lower()[:2]
    if wanted in ("en", "si", "ta"):
        return wanted
    for part in (request.headers.get("accept-language") or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in ("en", "si", "ta"):
            return code
    return "en"


def _auth_failure(error: AuthError, invalid_message: str, lang: str = "en") -> JSONResponse:
    if error.reason == "locked":
        minutes = max(1, (error.retry_after or 60) // 60)
        return JSONResponse(
            status_code=423,
            content={
                "detail": t("auth.account_locked", lang, minutes=minutes),
                "reason": "locked",
                "retry_after": error.retry_after,
            },
            headers={"Retry-After": str(error.retry_after or 60), **_NO_STORE},
        )
    if error.reason == "rate_limited":
        return JSONResponse(
            status_code=429,
            content={
                "detail": t("auth.device_limit", lang),
                "reason": "rate_limited",
                "retry_after": error.retry_after,
            },
            headers={"Retry-After": str(error.retry_after or 60), **_NO_STORE},
        )
    return JSONResponse(
        status_code=401, content={"detail": invalid_message, "reason": "invalid"}, headers=_NO_STORE
    )


def _set_cookie(response, name: str, token: str, minutes: int, samesite: str) -> None:
    response.set_cookie(
        key=name,
        value=token,
        max_age=minutes * 60,
        path="/",
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=samesite,
    )


def _clear_cookie(response, name: str, samesite: str) -> None:
    response.delete_cookie(key=name, path="/", httponly=True, secure=config.COOKIE_SECURE, samesite=samesite)


@router.post("/verify", response_model=VerifyResponse)
def verify(body: VerifyRequest, request: Request):
    try:
        normalize_sl_landline(body.phone)
    except InvalidPhoneNumber as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc), "reason": "phone_format"})
    try:
        user = verify_user(body.phone, body.pin, client_ip(request))
    except AuthError as error:
        return _auth_failure(error, t("auth.bad_credentials", request_language(request, body.language)), request_language(request, body.language))

    token, expires_at = issue_token(str(user.id), AUDIENCE_USER, config.USER_SESSION_MINUTES)
    response = JSONResponse(
        content={"display_name": user.display_name, "token": token, "expires_at": expires_at},
        headers=_NO_STORE,
    )
    _set_cookie(response, USER_COOKIE, token, config.USER_SESSION_MINUTES, "lax")
    return response


@router.post("/logout")
def logout():
    # Tokens are stateless (ADR 5), so logging out removes the cookie; a
    # copy of the token held elsewhere stays valid until it expires
    # (USER_SESSION_MINUTES), which is why sessions are short.
    response = JSONResponse(content={"ok": True})
    _clear_cookie(response, USER_COOKIE, "lax")
    return response


@router.get("/me", response_model=MeResponse)
def me(request: Request):
    user = verified_user_from_request(request)
    if user is None:
        return JSONResponse(status_code=401, content={"detail": t("auth.not_signed_in", request_language(request))}, headers=_NO_STORE)
    return JSONResponse(content={"display_name": user.display_name}, headers=_NO_STORE)


@router.post("/admin/login", response_model=AdminLoginResponse)
def admin_login(body: AdminLoginRequest, request: Request):
    try:
        admin = verify_admin(body.username, body.password, client_ip(request))
    except AuthError as error:
        return _auth_failure(error, "The username or password is incorrect.")

    token, expires_at = issue_token(
        str(admin.id), AUDIENCE_ADMIN, config.ADMIN_SESSION_MINUTES, extra={"username": admin.username}
    )
    response = JSONResponse(content={"username": admin.username, "expires_at": expires_at}, headers=_NO_STORE)
    _set_cookie(response, ADMIN_COOKIE, token, config.ADMIN_SESSION_MINUTES, "strict")
    return response


@router.post("/admin/logout")
def admin_logout():
    response = JSONResponse(content={"ok": True})
    _clear_cookie(response, ADMIN_COOKIE, "strict")
    return response


@router.get("/admin/me", response_model=AdminMeResponse)
def admin_me(admin: dict = Depends(require_admin)):
    return JSONResponse(content={"username": admin["username"]}, headers=_NO_STORE)
