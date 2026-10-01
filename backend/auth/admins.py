"""Admin accounts for the /admin portal: creation, sign in, and the first
admin seeded from the environment.

Same protections as user sign in (users.py): bcrypt hashes, one generic
"invalid" answer for an unknown username and a wrong password (with a
bcrypt check either way, so timing matches), a lockout per account after
LOGIN_MAX_ATTEMPTS consecutive failures persisted on the admins row, and
a rate limit per client address. Passwords are never logged or printed.

The first admin: on a fresh install nobody can sign in to create one, so
server startup calls seed_first_admin_from_env(), which creates
ADMIN_USERNAME with ADMIN_PASSWORD only while the admins table is empty.
Once any admin exists the environment values are ignored, so changing or
leaving ADMIN_PASSWORD in .env later cannot reset or add an account.
"""

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import config
from auth.passwords import hash_secret, verify_secret
from auth.ratelimit import SlidingWindowRateLimiter, lock_seconds_left, settle_attempt
from auth.users import AuthError
from db.base import session_scope
from db.models import Admin

MIN_PASSWORD_LENGTH = 10
# bcrypt only looks at the first 72 bytes and bcrypt 5 refuses longer input.
MAX_PASSWORD_BYTES = 72
_USERNAME = re.compile(r"[A-Za-z0-9_.@-]{3,64}")

admin_rate_limiter = SlidingWindowRateLimiter(config.LOGIN_RATE_LIMIT_PER_MINUTE, 60.0)


@dataclass(frozen=True)
class VerifiedAdmin:
    id: int
    username: str


class AdminAlreadyExists(ValueError):
    pass


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The admin password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"The admin password can be at most {MAX_PASSWORD_BYTES} bytes.")
    return password


def _validate_username(username: str) -> str:
    name = (username or "").strip()
    if not _USERNAME.fullmatch(name):
        raise ValueError("The username must be 3 to 64 letters, digits or . _ - @")
    return name


def create_admin(username: str, password: str) -> VerifiedAdmin:
    """Raises ValueError for a bad username or password (min length 10) and
    AdminAlreadyExists when the username is taken."""
    name = _validate_username(username)
    password_hash = hash_secret(validate_password(password))
    try:
        with session_scope() as session:
            if session.scalar(select(Admin.id).where(Admin.username == name)) is not None:
                raise AdminAlreadyExists(f"Admin {name!r} already exists.")
            admin = Admin(username=name, password_hash=password_hash, failed_attempts=0)
            session.add(admin)
            session.flush()
            return VerifiedAdmin(id=admin.id, username=admin.username)
    except IntegrityError as exc:
        raise AdminAlreadyExists(f"Admin {name!r} already exists.") from exc


def verify_admin(username: str, password: str, client_ip: str) -> VerifiedAdmin:
    """Returns the VerifiedAdmin or raises users.AuthError ("invalid",
    "locked" with retry_after, "rate_limited" with retry_after). Blocking."""
    retry_after = admin_rate_limiter.hit(client_ip or "unknown")
    if retry_after is not None:
        raise AuthError("rate_limited", retry_after)

    name = (username or "").strip()
    with session_scope() as session:
        admin = session.scalar(select(Admin).where(Admin.username == name)) if name else None
        if admin is None:
            admin_id = password_hash = locked_until = None
        else:
            admin_id, password_hash, locked_until = admin.id, admin.password_hash, admin.locked_until

    if admin_id is None:
        verify_secret(password or "", None)
        raise AuthError("invalid")

    seconds_left = lock_seconds_left(locked_until)
    if seconds_left:
        raise AuthError("locked", seconds_left)

    password_ok = verify_secret(password or "", password_hash)

    outcome, retry_after = settle_attempt(
        Admin, admin_id, password_ok, config.LOGIN_MAX_ATTEMPTS, config.LOGIN_LOCKOUT_MINUTES
    )
    if outcome == "ok":
        verified = get_admin(admin_id)
        if verified is not None:
            return verified
        raise AuthError("invalid")
    if outcome == "locked_now":
        print(f"Admin {name!r} locked for {config.LOGIN_LOCKOUT_MINUTES} min after {config.LOGIN_MAX_ATTEMPTS} failed sign ins.")
        raise AuthError("locked", retry_after)
    if outcome == "locked":
        raise AuthError("locked", retry_after)
    raise AuthError("invalid")


def get_admin(admin_id: int) -> Optional[VerifiedAdmin]:
    with session_scope() as session:
        admin = session.get(Admin, admin_id)
        return VerifiedAdmin(id=admin.id, username=admin.username) if admin is not None else None


def seed_first_admin_from_env() -> bool:
    """Creates ADMIN_USERNAME / ADMIN_PASSWORD when no admin exists yet.
    Returns True only when it created one. Never prints the password."""
    with session_scope() as session:
        admin_count = session.scalar(select(func.count()).select_from(Admin)) or 0
    if admin_count:
        return False
    if not config.ADMIN_PASSWORD:
        print(
            "No admin account exists and ADMIN_PASSWORD is not set, so the admin portal has no login yet. "
            "Set ADMIN_USERNAME and ADMIN_PASSWORD in .env and restart."
        )
        return False
    try:
        admin = create_admin(config.ADMIN_USERNAME, config.ADMIN_PASSWORD)
    except AdminAlreadyExists:
        # Another process seeded it between the count and the insert.
        return False
    except ValueError as exc:
        print(f"First admin not created from ADMIN_USERNAME/ADMIN_PASSWORD: {exc}")
        return False
    print(f"Created the first admin account {admin.username!r} from ADMIN_USERNAME/ADMIN_PASSWORD.")
    return True
