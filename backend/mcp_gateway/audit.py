"""One audit line per hub call.

    GATEWAY tool=show_gb_balance outcome=ok ms=412 session=3fa91c0d

The line holds the tool name, the outcome, the milliseconds and a short hash
of the customer's number so one customer's calls can be followed. Never an
argument, a result, a code or the number itself. The last 200 lines are kept
in memory for the admin page.
"""

import hashlib
import threading
import time
from collections import deque
from typing import Optional

KEEP = 200

_entries: "deque[dict]" = deque(maxlen=KEEP)
_lock = threading.Lock()


def session_ref(number: Optional[str]) -> str:
    """8 hex characters of the sha256 of the number, "-" for no number."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if not digits:
        return "-"
    return hashlib.sha256(digits.encode("ascii")).hexdigest()[:8]


def record(session: Optional[str], tool: str, outcome: str, ms: float) -> None:
    """session is the customer's number (hashed here, so a caller cannot print
    it by mistake) or None for a guest call. Never raises."""
    try:
        ref = session_ref(session)
        name = "".join(ch for ch in str(tool) if ch.isalnum() or ch == "_")[:64] or "-"
        kind = "".join(ch for ch in str(outcome) if ch.isalnum() or ch == "_")[:24] or "-"
        millis = int(max(0, ms))
        with _lock:
            _entries.append({"at": time.time(), "tool": name, "outcome": kind, "ms": millis, "session": ref})
        print(f"GATEWAY tool={name} outcome={kind} ms={millis} session={ref}", flush=True)
    except Exception:  # noqa: BLE001  (an audit line must never break a call)
        pass


def recent(limit: int = KEEP) -> list:
    """Newest last, at most limit entries."""
    with _lock:
        items = list(_entries)
    return items[-max(0, int(limit)):] if limit else []


def clear() -> None:
    with _lock:
        _entries.clear()
