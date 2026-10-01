"""Brakes on guessing: a per address rate limit and the per account
lockout bookkeeping, shared by user (phone + PIN) and admin (username + password)
sign in.

Two separate brakes because they stop different attacks:
- The account lockout (persisted on the account row, so it survives a
  restart) stops someone trying many PINs against one number.
- The rate limit per client address stops one machine trying one or two
  PINs against many numbers, which a per account lockout never notices.

The rate limit is kept in memory: it is only a speed bump, one process
serves the app, and losing it on restart costs nothing. Behind a proxy,
every request would appear to come from the proxy's address; see
dependencies.client_ip for why forwarded headers are not trusted here.
"""

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update

from db.base import session_scope


class SlidingWindowRateLimiter:
    """At most `limit` attempts per `window_seconds` per key. Thread safe:
    FastAPI runs the sync sign in handlers on a thread pool."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> Optional[int]:
        """Records one attempt. Returns None when it is allowed, otherwise
        the number of seconds until the next attempt would be (the attempt
        is then not recorded, so waiting is always enough)."""
        if self.limit <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            window = self._attempts.setdefault(key, deque())
            while window and now - window[0] >= self.window_seconds:
                window.popleft()
            if len(window) >= self.limit:
                return max(1, int(window[0] + self.window_seconds - now + 0.999))
            window.append(now)
            # Forget idle addresses so the table cannot grow without bound.
            if len(self._attempts) > 10_000:
                self._prune(now)
            return None

    def reset(self, key: Optional[str] = None) -> None:
        """Forgets one key, or everything (tests, an operator unblocking)."""
        with self._lock:
            if key is None:
                self._attempts.clear()
            else:
                self._attempts.pop(key, None)

    def _prune(self, now: float) -> None:
        for key in [k for k, w in self._attempts.items() if not w or now - w[-1] >= self.window_seconds]:
            del self._attempts[key]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite has no time zone type and returns naive datetimes even for
    DateTime(timezone=True) columns; PostgreSQL returns aware ones. Every
    value this app writes is UTC, so a naive one read back is UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def lock_seconds_left(locked_until: Optional[datetime]) -> int:
    """Seconds until a lock ends, 0 when not locked."""
    locked_until = as_utc(locked_until)
    if locked_until is None:
        return 0
    remaining = (locked_until - utcnow()).total_seconds()
    return int(remaining + 0.999) if remaining > 0 else 0


def lock_until(minutes: int) -> datetime:
    return utcnow() + timedelta(minutes=minutes)


class _LockedMeanwhile(Exception):
    def __init__(self, seconds_left: int):
        self.seconds_left = seconds_left


def settle_attempt(model, row_id: int, success: bool, max_attempts: int, lockout_minutes: int) -> tuple[str, Optional[int]]:
    """Records the result of one checked secret on a users or admins row
    (both have failed_attempts, locked_until, last_login_at) and returns
    (outcome, retry_after), outcome being one of:
    "ok"          the secret was right; counters reset, last_login_at set
    "invalid"     wrong secret, failure counted
    "locked_now"  wrong secret and this failure locked the account
    "locked"      already locked by a parallel request (retry_after seconds)
    "gone"        the row was deleted meanwhile

    Order matters for requests running in parallel. The first statement is
    an UPDATE, which takes SQLite's write lock (a row lock on PostgreSQL),
    so everything read after it in the same transaction is current. If
    another request locked the account while this one was hashing, the
    answer is "locked" whatever the secret was and the transaction is
    rolled back, so guesses made in that race neither count nor tell the
    caller anything. Without this, a burst of parallel requests could all
    pass the lock check before any of them recorded its failure.
    """
    now = utcnow()
    try:
        with session_scope() as session:
            changes = {"last_login_at": now} if success else {"failed_attempts": model.failed_attempts + 1}
            if session.execute(update(model).where(model.id == row_id).values(**changes)).rowcount == 0:
                return "gone", None
            locked_until, failures = session.execute(
                select(model.locked_until, model.failed_attempts).where(model.id == row_id)
            ).one()
            seconds_left = lock_seconds_left(locked_until)
            if seconds_left:
                raise _LockedMeanwhile(seconds_left)  # rolls the UPDATE back
            if success:
                session.execute(update(model).where(model.id == row_id).values(failed_attempts=0, locked_until=None))
                return "ok", None
            if failures >= max_attempts:
                # The counter restarts, so after the lock ends there are
                # another max_attempts tries before the next lock.
                session.execute(
                    update(model)
                    .where(model.id == row_id)
                    .values(failed_attempts=0, locked_until=lock_until(lockout_minutes))
                )
                return "locked_now", lockout_minutes * 60
            return "invalid", None
    except _LockedMeanwhile as locked:
        return "locked", locked.seconds_left
