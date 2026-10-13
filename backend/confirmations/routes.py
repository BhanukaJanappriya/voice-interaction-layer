"""HTTP endpoints of the confirmation step, prefix /confirm.

    GET  /confirm/pending          {confirmation: {id, tool, title, lines, expires_in, state} | null}
    POST /confirm/{token}/accept   {kind, text, state}   runs the action once
    POST /confirm/{token}/cancel   {kind, text, state}

The customer is identified from the session cookie, never from the body. A
token that does not exist, is spent, or belongs to someone else answers the
same generic 404, so nothing says which. Every answer is JSON with
Cache-Control: no-store. Plain def handlers: confirm() blocks on the hub, so
FastAPI runs it on its thread pool. The token is never logged.
"""

import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auth.access_context import access_from_request
from auth.dependencies import client_ip

from . import api, store

router = APIRouter(prefix="/confirm", tags=["confirmations"])

_NO_STORE = {"Cache-Control": "no-store"}
RATE_LIMIT = 20  # requests
RATE_WINDOW = 60.0  # seconds, per customer or per address for a guest
_hits: "dict[str, list[float]]" = {}
_hits_lock = threading.Lock()

_STATE = {"executed": "executed", "failed": "failed", "expired": "expired", "cancelled": "cancelled"}


def _json(status: int, content: dict, extra: dict = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=content, headers={**_NO_STORE, **(extra or {})})


def _limited(key: str) -> bool:
    now = time.monotonic()
    with _hits_lock:
        recent = [t for t in _hits.get(key, []) if now - t < RATE_WINDOW]
        if len(recent) >= RATE_LIMIT:
            _hits[key] = recent
            return True
        recent.append(now)
        _hits[key] = recent
        if len(_hits) > 2000:
            for stale in [k for k, v in _hits.items() if not v or now - v[-1] >= RATE_WINDOW]:
                _hits.pop(stale, None)
    return False


def clear_limits() -> None:
    with _hits_lock:
        _hits.clear()


def _not_found() -> JSONResponse:
    return _json(404, {"detail": "Not found"})


def _answer(result) -> JSONResponse:
    if result.kind == "invalid":
        return _not_found()
    return _json(200, {"kind": result.kind, "text": result.text, "state": _STATE.get(result.kind, result.kind)})


def _who(request: Request):
    access = access_from_request(request)
    return access, (store.owner_key(access) or "ip:" + (client_ip(request) or "?"))


@router.get("/pending")
def pending(request: Request):
    access, key = _who(request)
    if _limited(key):
        return _json(429, {"detail": "Too many requests"}, {"Retry-After": "30"})
    return _json(200, {"confirmation": api.pending_for(access)})


@router.post("/{token}/accept")
def accept(token: str, request: Request):
    access, key = _who(request)
    if _limited(key):
        return _json(429, {"detail": "Too many requests"}, {"Retry-After": "30"})
    return _answer(api.confirm(access, token))


@router.post("/{token}/cancel")
def cancel(token: str, request: Request):
    access, key = _who(request)
    if _limited(key):
        return _json(429, {"detail": "Too many requests"}, {"Retry-After": "30"})
    return _answer(api.cancel(access, token))
