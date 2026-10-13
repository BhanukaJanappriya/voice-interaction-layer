"""Who is asking. A caller is a guest unless an OTP verification finished.

Verified requires the session token claim amr to contain "otp" (ADR 3.4).
An old PIN login keeps working for the greeting but is a guest here.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional

GUEST_LEVEL = "guest"
VERIFIED_LEVEL = "verified"


@dataclass(frozen=True)
class AccessContext:
    level: str = GUEST_LEVEL
    user_id: Optional[int] = None
    display_name: Optional[str] = None
    phone_e164: Optional[str] = None
    language: Optional[str] = None
    # True only for the guest a hub login that is no longer alive is turned
    # into (see auth.access_context): the chat says "verify again".
    session_expired: bool = False

    @property
    def is_verified(self) -> bool:
        return self.level == VERIFIED_LEVEL

    @classmethod
    def verified(
        cls,
        user_id: Optional[int],
        display_name: Optional[str],
        phone_e164: Optional[str],
        language: Optional[str] = None,
    ) -> "AccessContext":
        return cls(VERIFIED_LEVEL, user_id, display_name, phone_e164, language)

    @classmethod
    def from_claims(
        cls,
        amr: Optional[Iterable[Any]],
        user_id: Optional[int] = None,
        display_name: Optional[str] = None,
        phone_e164: Optional[str] = None,
        language: Optional[str] = None,
    ) -> "AccessContext":
        """Verified only when amr contains "otp" AND a phone number is known;
        anything else is a guest (fails closed)."""
        try:
            methods = {str(m) for m in (amr or [])}
        except TypeError:
            methods = set()
        if "otp" in methods and phone_e164:
            return cls.verified(user_id, display_name, phone_e164, language)
        return cls(GUEST_LEVEL, user_id, display_name, None, language)


GUEST = AccessContext()
GUEST_EXPIRED = AccessContext(session_expired=True)
