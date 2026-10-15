"""What moves with the conversation when the voice tier changes.

build_handover() gathers the session facts. The access object is kept by
reference (never copied, never logged). A pending confirmation is read from
backend/confirmations through its public pending_for(); only the token free
view is carried (title, lines, seconds left), so the new tier can remind the
customer that an action still waits for a yes. The token itself stays with
the confirmations store, which holds it per customer.
"""

from typing import Optional

from .types import HandoverState

_FALLBACK_NOTICE = "One moment, reconnecting."
NOTICE_KEY = "voice.reconnecting"
_SUMMARY_LIMIT = 600


def _safe_view(pending: Optional[dict]) -> Optional[dict]:
    """The pending action without its token."""
    if not isinstance(pending, dict):
        return None
    return {
        "tool": pending.get("tool"),
        "title": pending.get("title"),
        "lines": [dict(line) for line in pending.get("lines", []) if isinstance(line, dict)],
        "expires_in": pending.get("expires_in"),
    }


def _pending_for(access) -> Optional[dict]:
    if access is None:
        return None
    try:
        import confirmations

        return _safe_view(confirmations.pending_for(access))
    except Exception:  # noqa: BLE001
        return None


def summarize(turns) -> str:
    """The last few turns as one short plain string, newest last. `turns` is a
    list of strings or (role, text) pairs. Bounded in length."""
    parts = []
    for turn in list(turns or [])[-6:]:
        if isinstance(turn, (tuple, list)) and len(turn) == 2:
            parts.append(f"{turn[0]}: {turn[1]}")
        else:
            parts.append(str(turn))
    text = " | ".join(parts)
    return text[-_SUMMARY_LIMIT:]


def build_handover(
    language: Optional[str] = "en",
    voice_style: Optional[str] = "female_natural",
    access=None,
    turns=None,
    summary: Optional[str] = None,
    pending_confirmation: Optional[dict] = None,
) -> HandoverState:
    """pending_confirmation may be passed explicitly; otherwise it is read from
    the confirmations store for this customer."""
    pending = _safe_view(pending_confirmation) if pending_confirmation else _pending_for(access)
    text = summary if summary is not None else summarize(turns)
    return HandoverState(
        language=(language or "en"),
        voice_style=(voice_style or "female_natural"),
        access=access,
        summary=text[-_SUMMARY_LIMIT:],
        pending_confirmation=pending,
    )


def make_notice(language: Optional[str] = "en") -> str:
    """The one short spoken notice, localized by the i18n key voice.reconnecting.
    Falls back to English text if the key or the module is missing. Never raises."""
    try:
        from i18n import t

        found = t(NOTICE_KEY, language or "en")
        if isinstance(found, str) and found.strip() and found != NOTICE_KEY:
            return found
    except Exception:  # noqa: BLE001
        pass
    return _FALLBACK_NOTICE
