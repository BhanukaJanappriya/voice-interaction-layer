"""A per tier circuit breaker.

After a tier fails, the next turns skip it for a fixed time instead of paying
for the same failing call (and its timeout) again. A dead key or used up
credit rarely fixes itself within a conversation. When the time is up the next
turn simply tries the tier again; success resets it, another failure re-trips.
"""

import threading
import time
from typing import Callable, Optional


class Breaker:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.seconds = float(seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._until = 0.0
        self.reason: Optional[str] = None

    def trip(self, reason: str) -> None:
        with self._lock:
            self._until = self._clock() + self.seconds
            self.reason = reason

    def seconds_left(self) -> float:
        with self._lock:
            return max(0.0, self._until - self._clock())

    def open(self) -> bool:
        """True while the tier must be skipped."""
        return self.seconds_left() > 0.0

    def reset(self) -> None:
        with self._lock:
            self._until = 0.0
            self.reason = None
