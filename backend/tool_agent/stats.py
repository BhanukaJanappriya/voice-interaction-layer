"""Rolling, thread safe tool timing numbers for /health.

stats() -> {"turns": n, "avg_tool_ms": x, "p95_tool_ms": y, "by_tool": {name: {"calls", "avg_ms"}}}
Only counts and milliseconds are kept, never text.
"""

import threading
from collections import deque

KEEP = 500

_lock = threading.Lock()
_turns: "deque[int]" = deque(maxlen=KEEP)
_total_turns = 0
_by_tool: dict = {}


def record(tool_ms: int, timings: dict) -> None:
    """tool_ms: wall time of the turn's tool phase; timings: tool name -> ms."""
    global _total_turns
    with _lock:
        _total_turns += 1
        _turns.append(int(max(tool_ms, 0)))
        for name, ms in (timings or {}).items():
            key = "".join(ch for ch in str(name) if ch.isalnum() or ch == "_")[:64]
            if not key:
                continue
            entry = _by_tool.setdefault(key, [0, 0.0])
            entry[0] += 1
            entry[1] += float(max(ms, 0))


def stats() -> dict:
    with _lock:
        values = sorted(_turns)
        count = _total_turns
        tools = {k: {"calls": v[0], "avg_ms": round(v[1] / v[0], 1)} for k, v in _by_tool.items() if v[0]}
    if not values:
        return {"turns": count, "avg_tool_ms": 0, "p95_tool_ms": 0, "by_tool": tools}
    p95 = values[min(len(values) - 1, int(0.95 * len(values)))]
    return {"turns": count, "avg_tool_ms": round(sum(values) / len(values), 1), "p95_tool_ms": p95, "by_tool": tools}


def reset() -> None:
    global _total_turns
    with _lock:
        _turns.clear()
        _by_tool.clear()
        _total_turns = 0
