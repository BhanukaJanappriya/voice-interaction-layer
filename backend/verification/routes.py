"""HTTP endpoints of the verification wizard (plan 3.3 R3), prefix /verify.

    POST /verify/lookup    {name, phone, language}
        200 {ticket, methods: [{id: "sms"|"email", masked}]}
        422 {code: "invalid_details"}   wrong name or unknown number: ONE answer
        422 {code: "phone_format"}      impossible syntax only
        429 {code: "rate_limited", retry_after}
    POST /verify/otp/send  {ticket, method, language}
        200 {challenge_id, expires_in, resend_in, masked}
        409 {code: "no_contact"}
        429 {code: "cooldown" | "rate_limited", retry_after}
        401 {code: "ticket_invalid"}    ticket missing, forged or expired
        503 {code: "unavailable"}       no usable sender or delivery failed
    POST /verify/otp/verify {challenge_id, code}
        200 {display_name, language} + httpOnly user_session cookie (amr otp)
        401 {code: "invalid", attempts_left}
        410 {code: "expired"}
        423 {code: "locked", retry_after}
        429 {code: "rate_limited", retry_after}
    GET  /verify/session   {verified, display_name?}
    POST /verify/logout    {ok: true}, clears the cookie

Every response is JSON with Cache-Control: no-store. Unmasked contacts never
leave the server. Plain def handlers: the database blocks, FastAPI runs them
on its thread pool.
"""

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from auth.dependencies import USER_COOKIE, client_ip
from auth.phone import InvalidPhoneNumber
from auth.tokens import AUDIENCE_USER, TokenError, decode_token, issue_token
from auth.users import get_user
from verification import audit, service
from verification.service import VerifyError

router = APIRouter(prefix="/verify", tags=["verification"])

_NO_STORE = {"Cache-Control": "no-store"}


class LookupBody(BaseModel):
    name: str = Field("", max_length=120)
    phone: str = Field("", max_length=40)
    language: Optional[str] = Field(None, max_length=8)


class SendBody(BaseModel):
    ticket: str = Field("", max_length=2000)
    method: str = Field("", max_length=16)
    language: Optional[str] = Field(None, max_length=8)


class VerifyBody(BaseModel):
    challenge_id: str = Field("", max_length=64)
    code: str = Field("", max_length=12)


def _json(status: int, content: dict, extra: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=content, headers={**_NO_STORE, **(extra or {})})


_STATUS = {
    "invalid_details": 422,
    "ticket_invalid": 401,
    "no_contact": 409,
    "cooldown": 429,
    "rate_limited": 429,
    "unavailable": 503,
    "invalid": 401,
    "expired": 410,
    "locked": 423,
}


def _failure(error: VerifyError) -> JSONResponse:
    content: dict = {"code": error.reason}
    headers = {}
    if error.retry_after is not None:
        content["retry_after"] = error.retry_after
        headers["Retry-After"] = str(error.retry_after)
    if error.reason == "invalid":
        content["attempts_left"] = error.attempts_left if error.attempts_left is not None else 0
    return _json(_STATUS.get(error.reason, 400), content, headers)


@router.post("/lookup")
def lookup(body: LookupBody, request: Request):
    try:
        result = service.get_service().lookup(body.name, body.phone, body.language, client_ip(request))
    except InvalidPhoneNumber:
        return _json(422, {"code": "phone_format"})
    except VerifyError as error:
        return _failure(error)
    return _json(200, {"ticket": result.ticket, "methods": result.methods})


@router.post("/otp/send")
def otp_send(body: SendBody, request: Request):
    try:
        result = service.get_service().send_code(body.ticket, body.method, body.language, client_ip(request))
    except VerifyError as error:
        return _failure(error)
    return _json(
        200,
        {
            "challenge_id": result.challenge_id,
            "expires_in": result.expires_in,
            "resend_in": result.resend_in,
            "masked": result.masked,
        },
    )


@router.post("/otp/verify")
def otp_verify(body: VerifyBody, request: Request):
    try:
        result = service.get_service().verify_code(body.challenge_id, body.code, client_ip(request))
    except VerifyError as error:
        return _failure(error)
    # Same audience, lifetime and cookie as POST /auth/otp/verify, plus the
    # claim that makes this session "verified" for the access layer.
    token, _ = issue_token(
        str(result.user_id), AUDIENCE_USER, config.USER_SESSION_MINUTES, extra={"amr": ["otp"]}
    )
    response = _json(200, {"display_name": result.display_name, "language": result.language})
    response.set_cookie(
        key=USER_COOKIE,
        value=token,
        max_age=config.USER_SESSION_MINUTES * 60,
        path="/",
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
    )
    return response


@router.get("/session")
def session(request: Request):
    token = request.cookies.get(USER_COOKIE)
    if not token:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        token = value.strip() if scheme.lower() == "bearer" else None
    try:
        claims = decode_token(token, AUDIENCE_USER)
        if "otp" not in (claims.get("amr") or []):
            return _json(200, {"verified": False})
        user = get_user(int(claims["sub"]))
    except (TokenError, KeyError, TypeError, ValueError):
        return _json(200, {"verified": False})
    if user is None:
        return _json(200, {"verified": False})
    return _json(200, {"verified": True, "display_name": user.display_name})


@router.post("/logout")
def logout(request: Request):
    audit.record("logout", "ok", service.get_service().clock())
    response = _json(200, {"ok": True})
    response.delete_cookie(key=USER_COOKIE, path="/", httponly=True, secure=config.COOKIE_SECURE, samesite="lax")
    return response
