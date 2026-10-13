"""The customer sessions: one open hub connection per signed in number.

Keyed by the customer's national number (0112345678). A second login for the
same number replaces the first and closes the old connection. Idle sessions
are closed after HUB_SESSION_IDLE_SECONDS, checked lazily on every access and
by a daemon sweeper. At most HUB_SESSION_MAX sessions are kept; the least
recently used one is closed to make room.

Thread safe: callers are web worker threads. Closing a session is a blocking
network step, so it always happens outside the lock.
"""

import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

import config
from mcp_gateway.session import HubSession


class SessionPool:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: Optional[float] = None,
        max_sessions: Optional[int] = None,
        factory: Callable[[], HubSession] = HubSession,
        sweep: bool = True,
    ):
        self._clock = clock
        self._idle = idle_seconds
        self._max = max_sessions
        self._factory = factory
        self._items: "OrderedDict[str, list]" = OrderedDict()  # key -> [session, last touched]
        self._lock = threading.Lock()
        self._sweep_enabled = sweep
        self._sweeper: Optional[threading.Thread] = None
        self._hooks: list = []

    @property
    def idle_seconds(self) -> float:
        return float(self._idle if self._idle is not None else config.HUB_SESSION_IDLE_SECONDS)

    @property
    def max_sessions(self) -> int:
        return max(1, int(self._max if self._max is not None else config.HUB_SESSION_MAX))

    def add_sweep_hook(self, hook: Callable[[], None]) -> None:
        """Called by every sweep, for state the pool does not own (pending logins)."""
        self._hooks.append(hook)

    # Opening

    def open_detached(self) -> HubSession:
        """A new connection that is not in the pool yet (the login wizard
        keeps it until the code is verified). Raises what HubSession.open raises."""
        session = self._factory()
        session.open()
        self._start_sweeper()
        return session

    def open(self, key: str) -> HubSession:
        """Connects and stores the session under key, replacing any earlier one."""
        session = self.open_detached()
        self.put(key, session)
        return session

    def put(self, key: str, session: HubSession) -> None:
        stale: list = []
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                stale.append(old[0])
            self._items[key] = [session, self._clock()]
            while len(self._items) > self.max_sessions:
                _, evicted = self._items.popitem(last=False)
                stale.append(evicted[0])
        for item in stale:
            if item is not session:
                self._close_quietly(item)
        self._start_sweeper()

    # Reading

    def get(self, key: str) -> Optional[HubSession]:
        """The live session for key, or None when missing, dead or idle for too long."""
        stale = None
        found = None
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                session, touched = entry
                if not session.alive or self._clock() - touched > self.idle_seconds:
                    del self._items[key]
                    stale = session
                else:
                    entry[1] = self._clock()
                    self._items.move_to_end(key)
                    found = session
        if stale is not None:
            self._close_quietly(stale)
        return found

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def keys(self) -> list:
        with self._lock:
            return list(self._items)

    # Closing

    def close(self, key: str) -> None:
        with self._lock:
            entry = self._items.pop(key, None)
        if entry is not None:
            self._close_quietly(entry[0])

    def discard(self, key: str, session: HubSession) -> None:
        """Closes and drops session, but only while it is still the one stored
        under key (a newer login for the same number stays)"""
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and entry[0] is session:
                del self._items[key]
        self._close_quietly(session)

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._items.values())
            self._items.clear()
        for entry in entries:
            self._close_quietly(entry[0])

    def sweep(self) -> int:
        """Closes idle and dead sessions now. Returns how many went."""
        now = self._clock()
        with self._lock:
            gone = [k for k, (s, t) in self._items.items() if not s.alive or now - t > self.idle_seconds]
            sessions = [self._items.pop(k)[0] for k in gone]
        for session in sessions:
            self._close_quietly(session)
        for hook in list(self._hooks):
            try:
                hook()
            except Exception:  # noqa: BLE001
                pass
        return len(sessions)

    @staticmethod
    def _close_quietly(session: HubSession) -> None:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    def _start_sweeper(self) -> None:
        if not self._sweep_enabled:
            return
        with self._lock:
            if self._sweeper is not None and self._sweeper.is_alive():
                return

            def loop() -> None:
                while True:
                    time.sleep(max(1.0, min(60.0, self.idle_seconds / 2)))
                    try:
                        self.sweep()
                    except Exception:  # noqa: BLE001
                        pass

            self._sweeper = threading.Thread(target=loop, name="mcp-gateway-sweeper", daemon=True)
            self._sweeper.start()


_pool: Optional[SessionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> SessionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = SessionPool()
        return _pool


def set_pool(pool: Optional[SessionPool]) -> None:
    """Replaces (or with None, clears) the process wide pool. For tests."""
    global _pool
    with _pool_lock:
        _pool = pool
