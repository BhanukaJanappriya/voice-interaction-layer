"""In memory pending actions: one per customer number, single use.

A pending action holds the tool and its arguments on the server only. The
browser sees a token, a title and the summary lines, never the arguments.
Taking an action is atomic under one lock, so two confirms of the same token
can never both win. Nothing here logs.
"""

import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import config
from access.tool_gate import normalize_phone

MAX_PENDING = 500

PENDING, EXECUTED, CANCELLED, EXPIRED = "pending", "executed", "cancelled", "expired"

_clock = time.monotonic  # replaced in tests through set_clock


def set_clock(fn) -> None:
    global _clock
    _clock = fn or time.monotonic


def now() -> float:
    return _clock()


def ttl() -> float:
    try:
        return max(5.0, float(getattr(config, "CONFIRMATION_TTL_SECONDS", 60)))
    except (TypeError, ValueError):
        return 60.0


def owner_key(access) -> Optional[str]:
    """The verified customer's national number (0112345678), or None."""
    if access is None or not getattr(access, "is_verified", False):
        return None
    plus = normalize_phone(getattr(access, "phone_e164", None))
    return "0" + plus[3:] if plus else None


@dataclass
class Action:
    token: str
    tool: str
    arguments: dict
    title: str
    lines: "list[dict]"
    owner: str
    language: str
    created: float
    expires: float
    state: str = PENDING
    extra: dict = field(default_factory=dict)

    def remaining(self) -> int:
        return max(0, int(self.expires - now() + 0.999))


_actions: "dict[str, Action]" = {}
_lock = threading.Lock()


def clear() -> None:
    with _lock:
        _actions.clear()


def put(owner: str, tool: str, arguments: dict, title: str, lines: "list[dict]", language: str) -> Action:
    """Stores a new action for the owner, replacing any earlier one."""
    created = now()
    action = Action(
        token=secrets.token_urlsafe(24),
        tool=tool,
        arguments=dict(arguments or {}),
        title=title,
        lines=[dict(line) for line in lines],
        owner=owner,
        language=language,
        created=created,
        expires=created + ttl(),
    )
    with _lock:
        previous = _actions.get(owner)
        if previous is not None and previous.state == PENDING:
            previous.state = CANCELLED
        _actions[owner] = action
        while len(_actions) > MAX_PENDING:
            oldest = min(_actions, key=lambda k: _actions[k].created)
            if oldest == owner:
                break
            _actions.pop(oldest)
    return action


def current(owner: str) -> Optional[Action]:
    """The owner's live pending action, or None. An action past its time is
    marked expired and kept once so the customer can be told."""
    with _lock:
        action = _actions.get(owner)
        if action is None or action.state != PENDING:
            return None
        if now() >= action.expires:
            action.state = EXPIRED
            return None
        return action


def last_expired(owner: str) -> Optional[Action]:
    """The owner's action if it has just expired (state expired), else None."""
    with _lock:
        action = _actions.get(owner)
        if action is not None and action.state == PENDING and now() >= action.expires:
            action.state = EXPIRED
        return action if action is not None and action.state == EXPIRED else None


def forget(owner: str) -> None:
    with _lock:
        _actions.pop(owner, None)


def take(owner: str, token: str, outcome: str = EXECUTED) -> "tuple[str, Optional[Action]]":
    """Atomically consumes the owner's action when the token matches.
    Returns ("ok", action), ("expired", action) or ("invalid", None). An
    action is consumed at most once: its state leaves pending here."""
    with _lock:
        action = _actions.get(owner)
        if action is None or not isinstance(token, str):
            return "invalid", None
        if not hmac.compare_digest(action.token.encode("utf-8"), token.encode("utf-8")):
            return "invalid", None
        if action.state == EXPIRED:
            return "expired", action
        if action.state != PENDING:
            return "invalid", None
        if now() >= action.expires:
            action.state = EXPIRED
            return "expired", action
        action.state = outcome
        return "ok", action
