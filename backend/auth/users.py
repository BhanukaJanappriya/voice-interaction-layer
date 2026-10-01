"""Existing customers: registration and verification by landline number
plus PIN.

Why a PIN and not the number alone (ADR 4 in docs/IMPLEMENTATION_PLAN.md):
a landline number is printed in directories and on bills, so knowing it
proves nothing. Landlines cannot receive SMS, so an SMS one time code is
not an option either. A 4 to 6 digit PIN chosen at registration is the
proof; because such a PIN is short, guessing is slowed by two brakes (see
ratelimit.py): a lockout per account after LOGIN_MAX_ATTEMPTS consecutive
failures, persisted on the users row, and a rate limit per client address.

What a caller can learn from a failure is kept to a minimum: an unknown
number and a wrong PIN give the same "invalid" reason, and an unknown
number still runs a full bcrypt check so both take the same time. The one
exception is "locked": a person who is locked out needs to be told to wait,
and that answer does reveal the number is registered. Reaching it takes
LOGIN_MAX_ATTEMPTS tries against one number while the per address rate
limit applies, which makes enumerating numbers this way slow.

PINs and hashes are never logged or printed.
"""

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

import config
from auth.passwords import hash_secret, verify_secret
from auth.phone import InvalidPhoneNumber, normalize_sl_landline
from auth.ratelimit import SlidingWindowRateLimiter, lock_seconds_left, settle_attempt
from db.base import session_scope
from db.models import User

_PIN = re.compile(r"[0-9]{4,6}")

# One limiter for every user sign in in this process, keyed by client address.
login_rate_limiter = SlidingWindowRateLimiter(config.LOGIN_RATE_LIMIT_PER_MINUTE, 60.0)


@dataclass(frozen=True)
class VerifiedUser:
    """A user whose PIN was checked (or whose session token was). Plain
    data, safe to pass between threads and outside a database session."""

    id: int
    display_name: str
    phone_e164: str


class AuthError(Exception):
    """Sign in refused. reason is one of:
    "invalid"       unknown number, wrong PIN, or malformed input (one
                    generic answer on purpose)
    "locked"        too many failures; retry_after seconds until unlocked
    "rate_limited"  too many attempts from this address; retry_after seconds
    """

    def __init__(self, reason: str, retry_after: Optional[int] = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class UserAlreadyExists(ValueError):
    pass


def validate_pin(pin: str) -> str:
    if not isinstance(pin, str) or not _PIN.fullmatch(pin):
        raise ValueError("The PIN must be 4 to 6 digits.")
    return pin


def _validate_display_name(display_name: str) -> str:
    name = " ".join((display_name or "").split())
    if not name:
        raise ValueError("A display name is required.")
    if len(name) > 80:
        raise ValueError("The display name can be at most 80 characters.")
    return name


def _validate_language(language: Optional[str]) -> Optional[str]:
    if language is None or language == "":
        return None
    if language not in config.SUPPORTED_LANGUAGES:
        raise ValueError(f"Preferred language must be one of: {', '.join(config.SUPPORTED_LANGUAGES)}.")
    return language


def _as_verified(user: User) -> VerifiedUser:
    return VerifiedUser(id=user.id, display_name=user.display_name, phone_e164=user.phone_e164)


def create_user(
    phone: str, display_name: str, pin: str, preferred_language: Optional[str] = None
) -> VerifiedUser:
    """Registers a customer. Raises InvalidPhoneNumber (a ValueError) for
    a bad number, ValueError for a bad PIN, name or language, and
    UserAlreadyExists when the number is already registered."""
    phone_e164 = normalize_sl_landline(phone)
    name = _validate_display_name(display_name)
    validate_pin(pin)
    language = _validate_language(preferred_language)
    pin_hash = hash_secret(pin)
    try:
        with session_scope() as session:
            if session.scalar(select(User.id).where(User.phone_e164 == phone_e164)) is not None:
                raise UserAlreadyExists(f"{phone_e164} is already registered.")
            user = User(
                phone_e164=phone_e164,
                display_name=name,
                pin_hash=pin_hash,
                preferred_language=language,
                failed_attempts=0,
            )
            session.add(user)
            session.flush()
            return _as_verified(user)
    except IntegrityError as exc:
        # Two registrations of the same number at the same moment.
        raise UserAlreadyExists(f"{phone_e164} is already registered.") from exc


def verify_user(phone: str, pin: str, client_ip: str) -> VerifiedUser:
    """Checks a number and PIN. Returns the VerifiedUser, or raises
    AuthError (see its docstring for the reasons). Blocking: bcrypt and a
    database round trip, so call it from a thread, not the event loop."""
    retry_after = login_rate_limiter.hit(client_ip or "unknown")
    if retry_after is not None:
        raise AuthError("rate_limited", retry_after)

    try:
        phone_e164 = normalize_sl_landline(phone)
    except InvalidPhoneNumber:
        verify_secret(pin or "", None)
        raise AuthError("invalid") from None

    # Read what is needed, then run bcrypt with no database session open,
    # so a slow hash never holds a connection or a SQLite lock.
    with session_scope() as session:
        user = session.scalar(select(User).where(User.phone_e164 == phone_e164))
        if user is None:
            user_id = pin_hash = locked_until = None
        else:
            user_id, pin_hash, locked_until = user.id, user.pin_hash, user.locked_until

    if user_id is None:
        verify_secret(pin or "", None)
        raise AuthError("invalid")

    seconds_left = lock_seconds_left(locked_until)
    if seconds_left:
        raise AuthError("locked", seconds_left)

    # bcrypt runs even for a malformed PIN, so timing does not tell the two apart.
    pin_ok = verify_secret(pin or "", pin_hash) and bool(_PIN.fullmatch(pin or ""))

    outcome, retry_after = settle_attempt(
        User, user_id, pin_ok, config.LOGIN_MAX_ATTEMPTS, config.LOGIN_LOCKOUT_MINUTES
    )
    if outcome == "ok":
        verified = get_user(user_id)
        if verified is not None:
            return verified
        raise AuthError("invalid")
    if outcome == "locked_now":
        print(f"User {user_id} locked for {config.LOGIN_LOCKOUT_MINUTES} min after {config.LOGIN_MAX_ATTEMPTS} failed PIN attempts.")
        raise AuthError("locked", retry_after)
    if outcome == "locked":
        raise AuthError("locked", retry_after)
    raise AuthError("invalid")


def get_user(user_id: int) -> Optional[VerifiedUser]:
    """The user as stored now, or None if the account no longer exists."""
    with session_scope() as session:
        user = session.get(User, user_id)
        return _as_verified(user) if user is not None else None


def get_preferred_language(user_id: int) -> Optional[str]:
    with session_scope() as session:
        return session.scalar(select(User.preferred_language).where(User.id == user_id))


def list_users() -> list[dict]:
    """For the command line tool: no hashes, ever."""
    with session_scope() as session:
        return [
            {
                "id": user.id,
                "phone_e164": user.phone_e164,
                "display_name": user.display_name,
                "preferred_language": user.preferred_language,
                "failed_attempts": user.failed_attempts,
                "locked_seconds_left": lock_seconds_left(user.locked_until),
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
            }
            for user in session.scalars(select(User).order_by(User.id))
        ]


def unlock_user(phone: str) -> bool:
    """Clears a lockout and the failure count. False if no such user."""
    phone_e164 = normalize_sl_landline(phone)
    with session_scope() as session:
        result = session.execute(
            update(User).where(User.phone_e164 == phone_e164).values(failed_attempts=0, locked_until=None)
        )
        return result.rowcount > 0


def set_pin(phone: str, pin: str) -> bool:
    """Replaces a user's PIN (and clears any lockout). False if no such user."""
    phone_e164 = normalize_sl_landline(phone)
    pin_hash = hash_secret(validate_pin(pin))
    with session_scope() as session:
        result = session.execute(
            update(User)
            .where(User.phone_e164 == phone_e164)
            .values(pin_hash=pin_hash, failed_attempts=0, locked_until=None)
        )
        return result.rowcount > 0
