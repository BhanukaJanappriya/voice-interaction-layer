"""Session resumption: the handle store, the GoAway plan and the reconnect
audio buffer.

The Live API sends a new resumption handle now and then (valid about 2 hours)
and a GoAway message with the time left before it closes the connection. The
session reconnects with the latest handle at the next gap between turns (or
just before the deadline), and keeps at most one second of microphone audio
while the new connection is being opened. Handles are never logged.
"""

import time
from collections import deque
from typing import Deque, Optional

from .config import BYTES_PER_SECOND_IN


class ResumptionStore:
    def __init__(self, handle: Optional[str] = None):
        self._handle = handle or None

    @property
    def handle(self) -> Optional[str]:
        return self._handle

    def update(self, new_handle: Optional[str], resumable: Optional[bool] = True) -> None:
        # A message with resumable false (mid tool call, mid generation) must
        # not replace a good handle.
        if new_handle and resumable is not False:
            self._handle = new_handle

    def clear(self) -> None:
        self._handle = None

    def __repr__(self) -> str:
        return f"ResumptionStore(handle={'set' if self._handle else 'none'})"


class GoAwayPlan:
    """What to do after GoAway(time_left)."""

    def __init__(self, margin: float = 2.0, clock=time.monotonic):
        self._margin = margin
        self._clock = clock
        self._deadline: Optional[float] = None

    def arm(self, seconds: float) -> None:
        self._deadline = self._clock() + max(0.0, float(seconds) - self._margin)

    @property
    def armed(self) -> bool:
        return self._deadline is not None

    def clear(self) -> None:
        self._deadline = None

    def remaining(self) -> Optional[float]:
        """Seconds until the reconnect must happen even in the middle of a turn."""
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - self._clock())


class AudioBuffer:
    """Microphone audio held while the connection is being replaced. Keeps the
    newest `seconds` only (oldest bytes are dropped), aligned to 2 byte samples."""

    def __init__(self, seconds: float = 1.0):
        self._limit = int(BYTES_PER_SECOND_IN * seconds) // 2 * 2
        self._chunks: Deque[bytes] = deque()
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def add(self, pcm: bytes) -> None:
        if not pcm:
            return
        if len(pcm) >= self._limit:
            pcm = pcm[len(pcm) - self._limit:]
        self._chunks.append(pcm)
        self._size += len(pcm)
        while self._size > self._limit:
            extra = self._size - self._limit
            head = self._chunks[0]
            if len(head) <= extra:
                self._chunks.popleft()
                self._size -= len(head)
            else:
                cut = extra + (extra % 2)  # stay on a sample boundary
                self._chunks[0] = head[cut:]
                self._size -= cut

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        self._size = 0
        return data
