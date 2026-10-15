"""The voice_router block for /health.

    {"order": [...], "active": "livekit",
     "tiers": {name: {"state": ready|tripped|not_configured,
                      "last_error": reason class or None,
                      "last_ok_at": epoch seconds or None,
                      "retry_in": seconds}}}

Only reason class names appear, never provider text, keys or URLs.
"""

from .types import FAILURE_REASONS

_SAFE_STATES = ("ready", "tripped", "not_configured")


def health_status(router=None) -> dict:
    """Never raises: a broken router reports itself as unavailable."""
    try:
        if router is None:
            from . import get_router

            router = get_router()
        raw = router.status()
        tiers = {}
        for name, item in raw.get("tiers", {}).items():
            error = item.get("last_error")
            tiers[str(name)] = {
                "state": item.get("state") if item.get("state") in _SAFE_STATES else "ready",
                "last_error": error if error in FAILURE_REASONS else None,
                "last_ok_at": item.get("last_ok_at"),
                "retry_in": item.get("retry_in", 0),
            }
        return {"order": list(raw.get("order", [])), "active": raw.get("active"), "tiers": tiers}
    except Exception:  # noqa: BLE001
        return {"order": [], "active": None, "tiers": {}, "error": "unavailable"}
