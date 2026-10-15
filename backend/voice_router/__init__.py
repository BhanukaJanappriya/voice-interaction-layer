"""Voice tier fallback: Gemini Live > LiveKit > local pipeline (docs/GEMINI_LIVE_PLAN.md section 4).

Nothing here changes behaviour until the server asks the router which tier
to use (VOICE_ROUTER_ENABLED, see docs/patches/B_voice_router.md).
"""

import threading
from typing import Optional

from .handover import build_handover, make_notice, summarize
from .health import health_status
from .reasons import classify
from .router import VoiceRouter
from .types import Failure, HandoverState, NotAFailure, Outcome, Tier

_router: Optional[VoiceRouter] = None
_lock = threading.Lock()


def get_router() -> VoiceRouter:
    """The process wide router, built on first use with the real probes."""
    global _router
    with _lock:
        if _router is None:
            from .probes import default_probes

            order = ("gemini_live", "livekit", "local")
            try:
                import config

                raw = str(getattr(config, "VOICE_TIER_ORDER", "") or "")
                parsed = [part.strip() for part in raw.split(",") if part.strip()]
                if parsed:
                    order = tuple(Tier(part) for part in parsed)
            except Exception:  # noqa: BLE001
                pass
            _router = VoiceRouter(order=order, probes=default_probes())
        return _router


def reset_router() -> None:
    """For tests."""
    global _router
    with _lock:
        _router = None


__all__ = [
    "Failure",
    "HandoverState",
    "NotAFailure",
    "Outcome",
    "Tier",
    "VoiceRouter",
    "build_handover",
    "classify",
    "get_router",
    "health_status",
    "make_notice",
    "reset_router",
    "summarize",
]
