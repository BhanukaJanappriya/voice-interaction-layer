"""Warm shared hub connections for the public, read only tools.

A guest's question used to open a new hub connection, which on the live hub costs 0.7 to 1.4 s to
connect, 0.5 to 1.9 s to initialise and a slower first call (measured 2.8 to 3.4 s against 1.4 s on a
connection that is already open). These sessions stay open and are reused, so a question pays only the
call itself. Used for tools that do not change an account and need no login; the customer's own
connection (pool.py) and every state changing call are untouched.

A few sessions are kept (one call at a time on each). A session that fails, times out or goes idle too
long is dropped, never repaired in place; the caller then falls back to the short lived connection it
used before, so this can only make a call faster, never make one fail that would have worked.
"""

import threading
import time
from typing import Callable, Optional

import config
from mcp_client.errors import HubError, HubToolError
from mcp_gateway.session import CallOutput, HubSession, SessionDead


class WarmUnavailable(Exception):
    """No warm session could serve this call; the caller uses a short lived connection."""


class _Slot:
    __slots__ = ("session", "busy", "last_used")

    def __init__(self) -> None:
        self.session: Optional[HubSession] = None
        self.busy = False
        self.last_used = 0.0


class WarmPool:
    def __init__(
        self,
        size: Optional[int] = None,
        idle_seconds: Optional[float] = None,
        factory: Callable[[], HubSession] = HubSession,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._size = size
        self._idle = idle_seconds
        self._factory = factory
        self._clock = clock
        self._slots: list = []
        self._lock = threading.Lock()
        self.opened = 0
        self.dropped = 0

    @property
    def size(self) -> int:
        return max(1, int(self._size if self._size is not None else config.HUB_WARM_POOL_SIZE))

    @property
    def idle_seconds(self) -> float:
        return float(self._idle if self._idle is not None else config.HUB_WARM_IDLE_SECONDS)

    # Public

    def call(self, tool: str, arguments: dict, budget: float) -> CallOutput:
        """One call on a warm session. Raises HubToolError (the hub refused this request, the connection is
        fine) or WarmUnavailable (anything else: the caller falls back)."""
        slot = self._acquire()
        if slot is None:
            raise WarmUnavailable("all warm sessions are busy")
        try:
            session = self._ready(slot, budget)
            output = session.call(tool, arguments, timeout=budget)
            slot.last_used = self._clock()
            return output
        except HubToolError:
            raise  # the hub refused this request; the connection itself is fine
        except (SessionDead, TimeoutError, HubError) as exc:
            self._drop(slot)
            raise WarmUnavailable(type(exc).__name__) from None
        except BaseException:
            self._drop(slot)
            raise WarmUnavailable("unexpected failure") from None
        finally:
            with self._lock:
                slot.busy = False

    def prewarm(self, count: int = 2, budget: float = 8.0) -> int:
        """Opens up to `count` sessions now (start up), so the first question finds them open."""
        done = 0
        for _ in range(min(count, self.size)):
            slot = self._acquire(force_new=True)
            if slot is None:
                break
            try:
                self._ready(slot, budget)
                slot.last_used = self._clock()
                done += 1
            except Exception:  # noqa: BLE001  start up must never fail because of this
                self._drop(slot)
                break
            finally:
                with self._lock:
                    slot.busy = False
        return done

    def close_all(self) -> None:
        with self._lock:
            slots, self._slots = self._slots, []
        for slot in slots:
            self._close(slot)

    def status(self) -> dict:
        with self._lock:
            alive = sum(1 for s in self._slots if s.session is not None and s.session.alive)
            return {"size": self.size, "open": alive, "opened": self.opened, "dropped": self.dropped}

    # Internals

    def _acquire(self, force_new: bool = False) -> Optional[_Slot]:
        with self._lock:
            if not force_new:
                idle = [s for s in self._slots if not s.busy]
                if idle:
                    # most recently used first: a hot session stays hot, _ready() replaces a stale one
                    slot = max(idle, key=lambda s: s.last_used)
                    slot.busy = True
                    return slot
            if len(self._slots) < self.size:
                slot = _Slot()
                slot.busy = True
                self._slots.append(slot)
                return slot
            return None

    def _ready(self, slot: _Slot, budget: float) -> HubSession:
        session = slot.session
        stale = session is not None and self._clock() - slot.last_used > self.idle_seconds
        if session is None or stale or not session.alive:
            if session is not None:
                self._close(slot)
            session = self._factory()
            session.open(timeout=max(1.0, budget))
            slot.session = session
            slot.last_used = self._clock()
            self.opened += 1
        return session

    def _drop(self, slot: _Slot) -> None:
        self._close(slot)
        self.dropped += 1
        with self._lock:
            if slot in self._slots:
                self._slots.remove(slot)

    @staticmethod
    def _close(slot: _Slot) -> None:
        session, slot.session = slot.session, None
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


_pool: Optional[WarmPool] = None
_pool_lock = threading.Lock()


def get_pool() -> WarmPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = WarmPool()
        return _pool


def set_pool(pool: Optional[WarmPool]) -> None:
    """Tests replace or clear it."""
    global _pool
    with _pool_lock:
        old, _pool = _pool, pool
    if old is not None and old is not pool:
        old.close_all()
