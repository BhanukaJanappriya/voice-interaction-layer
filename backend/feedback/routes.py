"""POST /feedback/rating and GET /feedback/config.

    POST /feedback/rating {rating 1..5, channel chat|voice, language?, turns?}
        201 {"ok": true}; 422 invalid body; 404 when RATING_ENABLED is off;
        429 + Retry-After over RATING_RATE_LIMIT_PER_HOUR per client; 503 on
        database trouble. The verified flag comes from the session cookie.
    GET /feedback/config -> {"enabled": bool}

Plain def handlers: the database blocks, FastAPI runs them on its thread pool.
Nothing about the person is logged.
"""

import threading
from typing import Literal, Optional

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

import config
from auth.dependencies import client_ip
from auth.ratelimit import SlidingWindowRateLimiter
from feedback import service

router = APIRouter(prefix="/feedback", tags=["feedback"])

_NO_STORE = {"Cache-Control": "no-store"}
_limiter: Optional[SlidingWindowRateLimiter] = None
_limiter_lock = threading.Lock()


class RatingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: StrictInt = Field(..., ge=1, le=5)
    channel: Literal["chat", "voice"]
    language: Optional[str] = Field(None, max_length=8)
    turns: Optional[StrictInt] = None


def _json(status: int, content: dict, extra: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=content, headers={**_NO_STORE, **(extra or {})})


def _get_limiter() -> SlidingWindowRateLimiter:
    global _limiter
    with _limiter_lock:
        limit = config.RATING_RATE_LIMIT_PER_HOUR
        if _limiter is None or _limiter.limit != limit:
            _limiter = SlidingWindowRateLimiter(limit, 3600.0)
        return _limiter


def reset_limiter() -> None:
    global _limiter
    with _limiter_lock:
        _limiter = None


@router.get("/config")
def feedback_config():
    return _json(200, {"enabled": bool(config.RATING_ENABLED)})


@router.post("/rating")
def post_rating(body: RatingBody, request: Request):
    if not config.RATING_ENABLED:
        return _json(404, {"detail": "Not found"})
    from otp import hashing

    ip = client_ip(request)
    retry = _get_limiter().hit(hashing.hash_ip(ip))
    if retry is not None:
        return _json(429, {"code": "rate_limited", "retry_after": retry}, {"Retry-After": str(retry)})
    language = (body.language or "en").strip().lower()[:2]
    try:
        from auth.access_context import access_from_request

        verified = bool(access_from_request(request).is_verified)
        service.record(body.rating, body.channel, language, verified, body.turns, ip)
    except sa.exc.SQLAlchemyError as exc:
        print(f"RATING store failed ({type(exc).__name__})")
        return _json(503, {"code": "unavailable"})
    return _json(201, {"ok": True})
