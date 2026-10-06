"""One time code sign in: request a code, verify it (plan R5, ADR 2.7).

Flow. request_otp(phone) normalizes the landline, looks the customer up
(the MCP hub first when MCP_HUB_CUSTOMER_LOOKUP_TOOL is set and the hub
answers, otherwise the local users table with its mobile_e164 and email
columns), creates a challenge and hands the code to the sender. The person
types the code, verify_otp() checks it and returns the same
auth.users.VerifiedUser the PIN sign in returns; routes.py then issues the
same session token.

The code: OTP_LENGTH digits from `secrets`, stored only as an HMAC (see
hashing.py), valid OTP_TTL_SECONDS, at most OTP_MAX_ATTEMPTS tries and then
the challenge is locked, single use. The plain code exists in memory for
the length of one request and in the sender's hands; it is never stored and
never logged (the console sender, development only, is the sole exception).

Brakes, all kept in the database so they survive a restart and need no
shared memory: a resend cooldown per number, a request limit per number per
hour and per client address per hour. A request refused by a brake creates
nothing, so waiting out Retry-After is always enough.

Anti enumeration (ADR 2.7). An attacker must not learn which landlines are
customers, so a number that is unknown, or known but with no contact on
record, gets a DECOY challenge: the same response shape, a stable plausible
masked destination derived from the number's hash, a real stored challenge
row (so cooldown, the hourly limits, the three attempt lock and expiry
behave identically and the 423 "locked" answer cannot tell the two apart),
and a random code that is never sent and can never succeed (a challenge
without a user_id always fails). Real challenges send the message on a
worker thread and return immediately, so the response time does not depend
on how long the SMS gateway takes. The price: a delivery failure cannot be
reported to the person asking (it is logged as an outcome); they simply do
not receive a code and can request another after the cooldown.

What is deliberately NOT done: an OTP sign in ignores the PIN lockout of the
account. The lockout exists to stop PIN guessing; a correct one time code is
proof of holding the registered mobile or mailbox, which is a different and
stronger proof, and blocking it would let someone lock a customer out of
the only other way in by guessing PINs on purpose.

Logging: only outcomes and log_id() of the challenge id. No number, no
destination, no code, no address.
"""

import json
import os
import secrets
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import config
from auth.passwords import hash_secret
from auth.phone import normalize_sl_landline
from auth.ratelimit import SlidingWindowRateLimiter, as_utc
from auth.users import VerifiedUser, get_user
from db.base import session_scope
from mcp_client.errors import HubError, HubToolError
from otp import hashing, masking
from otp.hub_backend import GENERIC_DESTINATION, HubOtpBackend, HubOtpStart, HubOtpUnknownResponse
from otp.sender import OtpSender, get_sender
from otp.tables import challenges, users

KNOWN_METHODS = ("pin", "otp")
_RETAIN = timedelta(days=1)  # challenge rows older than this are deleted


class OtpError(Exception):
    """Refused. reason is one of:
    "invalid"       wrong, expired, used or unknown code (one answer on purpose)
    "expired"       reported by the service, shown like "invalid" by the routes
    "locked"        too many wrong codes on this challenge; request a new one
    "rate_limited"  an hourly limit (per number or per address) or the
                    verification rate limit; retry_after seconds
    "cooldown"      a code was requested a moment ago; retry_after seconds
    "unavailable"   no usable sender, or the database failed
    """

    def __init__(self, reason: str, retry_after: Optional[int] = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


@dataclass(frozen=True)
class OtpChallengeInfo:
    challenge_id: str
    masked_destination: str
    channel: str  # "sms" or "email"
    expires_in: int  # seconds
    resend_in: int  # seconds until another code may be requested


@dataclass(frozen=True)
class _Contact:
    user_id: int
    destination: str
    channel: str
    language: Optional[str]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _log(event: str, challenge_id: str, outcome: str) -> None:
    print(f"OTP {event} {hashing.log_id(challenge_id)}: {outcome}")


def known_methods() -> list[str]:
    """AUTH_METHODS filtered to the values this server understands, order
    and first occurrence kept; "pin" when nothing valid is left."""
    methods: list[str] = []
    for method in config.AUTH_METHODS:
        if method in KNOWN_METHODS and method not in methods:
            methods.append(method)
    return methods or ["pin"]


def otp_enabled() -> bool:
    return "otp" in known_methods()


def _default_hub_lookup(phone_e164: str) -> Optional[dict]:
    if not config.MCP_HUB_CUSTOMER_LOOKUP_TOOL:
        return None
    try:
        from mcp_client.tools import lookup_customer_via_hub
    except Exception:  # noqa: BLE001  (the hub client is optional)
        return None
    return lookup_customer_via_hub(phone_e164)


class OtpService:
    def __init__(
        self,
        sender: Optional[OtpSender] = None,
        clock: Callable[[], datetime] = utcnow,
        hub_lookup: Optional[Callable[[str], Optional[dict]]] = None,
        send_in_background: bool = True,
        hub_otp: Optional[HubOtpBackend] = None,
    ):
        # A service built with its own sender (tests, embedding) never
        # reaches the hub unless a hub backend is handed in too: otherwise
        # a developer's real hub key would turn a test into a real SMS.
        self._force_local = sender is not None and hub_otp is None
        self._hub_otp_explicit = hub_otp is not None
        self._hub_otp = hub_otp or HubOtpBackend()
        self._hub_masks_seen = False
        self._sender = sender
        self.clock = clock  # injectable, so tests can move time
        self._hub_lookup = hub_lookup or _default_hub_lookup
        self._background = send_in_background
        self._lock = threading.Lock()  # one writer at a time for the limit checks
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="otp-send")
        self._pending: list[Future] = []
        self.verify_limiter = SlidingWindowRateLimiter(config.LOGIN_RATE_LIMIT_PER_MINUTE, 60.0)

    @property
    def sender(self) -> OtpSender:
        return self._sender if self._sender is not None else get_sender()

    # Request

    def request_otp(self, phone: str, client_ip: str, language: Optional[str] = None) -> OtpChallengeInfo:
        """Raises auth.phone.InvalidPhoneNumber for a malformed number
        (decided before any lookup, like /auth/verify), OtpError otherwise."""
        phone_e164 = normalize_sl_landline(phone)
        sender = self.sender
        backend = self._choose_backend(sender)
        if backend == "local" and not sender.is_usable():
            raise OtpError("unavailable")
        phone_hash = hashing.hash_phone(phone_e164)
        ip_hash = hashing.hash_ip(client_ip)

        try:
            with session_scope() as session:
                self._enforce_brakes(session, phone_hash, ip_hash)  # cheap refusal before any hub call
            hub_start: Optional[HubOtpStart] = None
            contact: Optional[_Contact] = None
            if backend == "hub":
                try:
                    hub_start = self._hub_request(phone_e164)
                except (HubError, HubOtpUnknownResponse, TimeoutError) as exc:
                    print(f"OTP hub request failed: {type(exc).__name__}")
                    if self._mode() == "auto" and sender.is_usable():
                        backend = "local"  # the hub is down: use our own codes
                    else:
                        raise OtpError("unavailable") from None
            if backend == "local":
                contact = self._resolve_contact(phone_e164, sender)
            with self._lock, session_scope() as session:
                now = self.clock()
                session.execute(sa.delete(challenges).where(challenges.c.created_at < now - _RETAIN))
                self._enforce_brakes(session, phone_hash, ip_hash)  # again: another request may have got in
                challenge_id = uuid.uuid4().hex
                code = self._new_code()
                hub_ref: Optional[str] = None
                if backend == "hub":
                    channel = "sms"
                    if hub_start is not None:
                        user_id = session.scalar(sa.select(users.c.id).where(users.c.phone_e164 == phone_e164))
                        hub_ref = self._seal({"p": phone_e164, "s": hub_start.session_id, "n": hub_start.display_name})
                        # Decoys copy whatever the hub did last time, so a
                        # hub that stops sending masks cannot expose them.
                        self._hub_masks_seen = bool(hub_start.masked_destination)
                        masked = hub_start.masked_destination or GENERIC_DESTINATION
                    else:
                        user_id = None
                        masked = masking.decoy_mobile(phone_hash) if self._hub_masks_seen else GENERIC_DESTINATION
                    destination = None
                elif contact is not None:
                    destination, channel, user_id = contact.destination, contact.channel, contact.user_id
                    masked = masking.mask_email(destination) if channel == "email" else masking.mask_mobile(destination)
                else:
                    destination, channel, user_id = None, self._decoy_channel(sender, phone_hash), None
                    masked = (
                        masking.decoy_email(phone_hash) if channel == "email" else masking.decoy_mobile(phone_hash)
                    )
                # Only the newest code works: resending retires the older one.
                session.execute(
                    sa.update(challenges)
                    .where(challenges.c.phone_hash == phone_hash, challenges.c.consumed_at.is_(None))
                    .values(consumed_at=now)
                )
                session.execute(
                    sa.insert(challenges).values(
                        id=challenge_id,
                        user_id=user_id,
                        phone_hash=phone_hash,
                        code_hash=hashing.hash_code(challenge_id, code),
                        destination_hash=hashing.hash_destination(destination or masked),
                        channel=channel,
                        attempts=0,
                        expires_at=now + timedelta(seconds=config.OTP_TTL_SECONDS),
                        consumed_at=None,
                        created_at=now,
                        client_ip_hash=ip_hash,
                        hub_ref=hub_ref,
                    )
                )
        except OtpError:
            raise
        except SQLAlchemyError as exc:
            print(f"OTP request failed: database error {type(exc).__name__}")
            raise OtpError("unavailable") from None

        if backend == "hub":
            _log("request", challenge_id, "hub sent the code" if hub_ref else "decoy, hub said not a customer")
        elif contact is not None:
            chosen_language = language if language in config.SUPPORTED_LANGUAGES else (contact.language or "en")
            self._dispatch(sender, challenge_id, contact.destination, code, chosen_language)
        else:
            _log("request", challenge_id, "decoy, nothing sent")
        return OtpChallengeInfo(
            challenge_id=challenge_id,
            masked_destination=masked,
            channel=channel,
            expires_in=config.OTP_TTL_SECONDS,
            resend_in=config.OTP_RESEND_COOLDOWN_SECONDS,
        )

    # Backend choice and hub plumbing

    @staticmethod
    def _mode() -> str:
        return (getattr(config, "OTP_BACKEND", "auto") or "auto").strip().lower()

    def hub_backend_available(self) -> bool:
        if self._force_local or (os.environ.get("PYTEST_CURRENT_TEST") and not self._hub_otp_explicit):
            return False
        return self._hub_otp.available()

    def _choose_backend(self, sender: OtpSender) -> str:
        """"hub" or "local". auto: the hub when configured and its breaker
        is closed, else local. hub: the hub or a 503. local: never the hub."""
        mode = self._mode()
        if mode == "local":
            return "local"
        if mode == "hub":
            if not self.hub_backend_available():
                raise OtpError("unavailable")
            return "hub"
        return "hub" if self.hub_backend_available() else "local"

    def _hub_request(self, phone_e164: str) -> Optional[HubOtpStart]:
        try:
            return self._hub_otp.request(phone_e164)
        except HubToolError:
            return None  # the hub refuses the number: treat as not a customer (decoy)

    @staticmethod
    def _fernet() -> Fernet:
        key = config.FIELD_ENCRYPTION_KEY
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)

    def _seal(self, data: dict) -> str:
        return self._fernet().encrypt(json.dumps(data).encode("utf-8")).decode("ascii")

    def _unseal(self, token: str) -> Optional[dict]:
        try:
            return json.loads(self._fernet().decrypt(token.encode("ascii")))
        except (InvalidToken, ValueError):
            return None

    @staticmethod
    def _new_code() -> str:
        return "".join(secrets.choice("0123456789") for _ in range(config.OTP_LENGTH))

    @staticmethod
    def _decoy_channel(sender: OtpSender, phone_hash: str) -> str:
        """The channel a decoy claims: what the sender really uses, and
        for the console (either) a stable pick from the number's hash."""
        if sender.channel in ("sms", "email"):
            return sender.channel
        return "email" if int(phone_hash[8:10], 16) % 2 else "sms"

    def _enforce_brakes(self, session, phone_hash: str, ip_hash: str) -> None:
        now = self.clock()
        cooldown = config.OTP_RESEND_COOLDOWN_SECONDS
        if cooldown > 0:
            latest = session.scalar(
                sa.select(sa.func.max(challenges.c.created_at)).where(challenges.c.phone_hash == phone_hash)
            )
            if latest is not None:
                left = (as_utc(latest) + timedelta(seconds=cooldown) - now).total_seconds()
                if left > 0:
                    raise OtpError("cooldown", int(left + 0.999))
        for column, value, limit in (
            (challenges.c.phone_hash, phone_hash, config.OTP_RATE_LIMIT_PER_NUMBER_PER_HOUR),
            (challenges.c.client_ip_hash, ip_hash, config.OTP_RATE_LIMIT_PER_IP_PER_HOUR),
        ):
            if limit <= 0:
                continue
            window_start = now - timedelta(hours=1)
            times = sorted(
                as_utc(t)
                for t in session.scalars(
                    sa.select(challenges.c.created_at).where(column == value, challenges.c.created_at > window_start)
                )
            )
            if len(times) >= limit:
                # The oldest request leaves the window first; once it has, one more is allowed.
                oldest_of_limit = times[len(times) - limit]
                raise OtpError("rate_limited", max(1, int((oldest_of_limit + timedelta(hours=1) - now).total_seconds() + 0.999)))

    # Customer lookup

    def _resolve_contact(self, phone_e164: str, sender: OtpSender) -> Optional[_Contact]:
        """Where this customer's code goes, or None (unknown number or no
        usable contact: both become a decoy)."""
        hub_customer: Optional[dict] = None
        try:
            hub_customer = self._hub_lookup(phone_e164)
        except Exception:  # noqa: BLE001  (the hub must never break sign in)
            hub_customer = None
        with session_scope() as session:
            row = session.execute(
                sa.select(
                    users.c.id, users.c.display_name, users.c.preferred_language, users.c.mobile_e164, users.c.email
                ).where(users.c.phone_e164 == phone_e164)
            ).first()
        mobile = (hub_customer or {}).get("mobile") or (row.mobile_e164 if row else None)
        email = (hub_customer or {}).get("email") or (row.email if row else None)
        chosen = sender.accepts(mobile, email)
        if chosen is None:
            return None
        user_id = row.id if row else None
        language = row.preferred_language if row else None
        if user_id is None:
            # The hub knows this customer but nobody has registered a local
            # account yet: create one that has no usable PIN (a random one
            # nobody sees), so the one time code is the only way in.
            user_id = self._provision_user(phone_e164, (hub_customer or {}).get("display_name"), mobile, email)
            if user_id is None:
                return None
        return _Contact(user_id=user_id, destination=chosen[0], channel=chosen[1], language=language)

    @staticmethod
    def _provision_user(phone_e164: str, display_name: Optional[str], mobile: Optional[str], email: Optional[str]) -> Optional[int]:
        pin_hash = hash_secret(secrets.token_urlsafe(24))
        values = dict(
            phone_e164=phone_e164,
            display_name=(display_name or "Customer")[:80],
            pin_hash=pin_hash,
            preferred_language=None,
            mobile_e164=mobile,
            email=email,
            failed_attempts=0,
            created_at=utcnow(),
        )
        try:
            with session_scope() as session:
                session.execute(sa.insert(users).values(**values))
                return session.scalar(sa.select(users.c.id).where(users.c.phone_e164 == phone_e164))
        except IntegrityError:
            with session_scope() as session:  # created by a parallel request
                return session.scalar(sa.select(users.c.id).where(users.c.phone_e164 == phone_e164))

    # Sending

    def _dispatch(self, sender: OtpSender, challenge_id: str, destination: str, code: str, language: str) -> None:
        def work() -> None:
            try:
                sender.send(destination, code, language)
                _log("send", challenge_id, "delivered to the sender")
            except Exception as exc:  # noqa: BLE001  (typed SenderError or anything else)
                _log("send", challenge_id, f"FAILED {type(exc).__name__}")

        if not self._background:
            work()
            return
        self._pending = [f for f in self._pending if not f.done()]
        self._pending.append(self._pool.submit(work))

    def wait_for_sends(self, timeout: float = 10.0) -> None:
        """Blocks until queued messages have been handed to the sender
        (for tests and orderly shutdown)."""
        for future in list(self._pending):
            future.result(timeout)

    # Verify

    def verify_otp(self, challenge_id: str, code: str, client_ip: str) -> VerifiedUser:
        retry_after = self.verify_limiter.hit(client_ip or "unknown")
        if retry_after is not None:
            raise OtpError("rate_limited", retry_after)
        challenge_id = (challenge_id or "")[:64]
        code = code or ""
        well_formed = len(code) == config.OTP_LENGTH and code.isascii() and code.isdigit()
        now = self.clock()
        try:
            outcome, user_id, hub_row = self._check(challenge_id, code if well_formed else "", now)
            if outcome == "hub":
                outcome, user_id = self._finish_hub(challenge_id, code, hub_row, now)
        except SQLAlchemyError as exc:
            print(f"OTP verify failed: database error {type(exc).__name__}")
            raise OtpError("unavailable") from None

        if outcome == "ok":
            verified = get_user(user_id) if user_id is not None else None
            if verified is None:
                raise OtpError("invalid")
            try:
                with session_scope() as session:
                    session.execute(sa.update(users).where(users.c.id == user_id).values(last_login_at=now))
            except SQLAlchemyError:
                pass  # the sign in itself succeeded
            _log("verify", challenge_id, "ok")
            return verified
        _log("verify", challenge_id, outcome)
        if outcome == "unavailable":
            raise OtpError("unavailable")
        if outcome == "locked":
            raise OtpError("locked", max(1, config.OTP_RESEND_COOLDOWN_SECONDS))
        raise OtpError("expired" if outcome == "expired" else "invalid")

    def _check(self, challenge_id: str, code: str, now: datetime) -> tuple[str, Optional[int], Optional[dict]]:
        """(outcome, user_id, hub_row) with outcome in ok, invalid,
        expired, locked, or "hub" (a hub challenge that still has to be
        verified by the hub; hub_row then carries what that needs).

        The attempt is counted BEFORE the code is compared, by a single
        conditional UPDATE, so parallel guesses cannot all slip under the
        limit, and a correct code is accepted only by the one request that
        wins the consume UPDATE (single use). Everything runs in one
        transaction: the first UPDATE takes SQLite's write lock (a row lock
        elsewhere), so what is read after it is current. A hub challenge
        leaves the transaction before the hub is called, so a slow hub never
        holds the database lock. Expiry and the attempt limit are enforced
        here for hub challenges too, in front of the hub's own."""
        with session_scope() as session:
            counted = session.execute(
                sa.update(challenges)
                .where(
                    challenges.c.id == challenge_id,
                    challenges.c.consumed_at.is_(None),
                    challenges.c.attempts < config.OTP_MAX_ATTEMPTS,
                )
                .values(attempts=challenges.c.attempts + 1)
            ).rowcount
            row = session.execute(sa.select(challenges).where(challenges.c.id == challenge_id)).first()
            if row is None:
                hashing.verify_code(challenge_id, code, "")  # same work as a real check
                return "invalid", None, None
            if as_utc(row.expires_at) <= now:
                return "expired", None, None
            if row.consumed_at is not None:
                return "invalid", None, None
            if not counted:
                return "locked", None, None  # attempts already at the limit
            if row.hub_ref and code != "":
                return "hub", None, {"ref": row.hub_ref, "attempts": row.attempts, "user_id": row.user_id}
            matches = hashing.verify_code(challenge_id, code, row.code_hash) and code != "" and not row.hub_ref
            if matches and row.user_id is not None:
                consumed = session.execute(
                    sa.update(challenges)
                    .where(challenges.c.id == challenge_id, challenges.c.consumed_at.is_(None))
                    .values(consumed_at=now)
                ).rowcount
                return ("ok", row.user_id, None) if consumed else ("invalid", None, None)
            if row.attempts >= config.OTP_MAX_ATTEMPTS:
                return "locked", None, None  # this wrong code was the last allowed one
            return "invalid", None, None

    def _finish_hub(self, challenge_id: str, code: str, hub_row: dict, now: datetime) -> tuple[str, Optional[int]]:
        """Asks the hub whether the code is right for this challenge's
        session, then consumes the challenge (single use) on success."""
        sealed = self._unseal(hub_row["ref"])
        if sealed is None:
            return "invalid", None
        try:
            accepted, name = self._hub_otp.verify(sealed["p"], sealed["s"], code)
        except (HubError, HubOtpUnknownResponse, TimeoutError) as exc:
            print(f"OTP hub verify failed: {type(exc).__name__}")
            return "unavailable", None
        if not accepted:
            return ("locked" if hub_row["attempts"] >= config.OTP_MAX_ATTEMPTS else "invalid"), None
        user_id = hub_row["user_id"]
        if user_id is None:
            user_id = self._provision_user(sealed["p"], name or sealed.get("n"), None, None)
        with session_scope() as session:
            consumed = session.execute(
                sa.update(challenges)
                .where(challenges.c.id == challenge_id, challenges.c.consumed_at.is_(None))
                .values(consumed_at=now)
            ).rowcount
        return ("ok", user_id) if consumed and user_id is not None else ("invalid", None)


# Contact details

def set_contact(phone: str, mobile: Optional[str] = None, email: Optional[str] = None) -> bool:
    """Records where a customer's codes go (operator tool, used by the
    tests and by whatever imports customers). Returns False when the
    landline has no account. Pass None to leave a field alone, "" to clear."""
    from otp.contacts import set_contact as _set

    return _set(phone, mobile, email)


# Process wide instance and the module level API of the contract

_service: Optional[OtpService] = None
_service_lock = threading.Lock()


def get_service() -> OtpService:
    global _service
    with _service_lock:
        if _service is None:
            _service = OtpService()
        return _service


def set_service(service: Optional[OtpService]) -> None:
    """Replaces the process wide service (tests)."""
    global _service
    with _service_lock:
        _service = service


def request_otp(phone: str, client_ip: str, language: Optional[str] = None) -> OtpChallengeInfo:
    return get_service().request_otp(phone, client_ip, language)


def verify_otp(challenge_id: str, code: str, client_ip: str) -> VerifiedUser:
    return get_service().verify_otp(challenge_id, code, client_ip)


def otp_available() -> bool:
    """Whether /auth/otp/request can work now: "otp" is listed and either
    the hub backend or a usable local sender is there."""
    try:
        svc = get_service()
        if not otp_enabled():
            return False
        mode = svc._mode()
        if mode in ("hub", "auto") and svc.hub_backend_available():
            return True
        return mode != "hub" and svc.sender.is_usable()
    except Exception:  # noqa: BLE001
        return False


def status() -> dict:
    """For /health: what the login page will offer and whether codes can
    actually be sent. Never raises, holds no secrets."""
    try:
        sender = get_service().sender
        return {
            "methods": known_methods(),
            "otp_listed": otp_enabled(),
            "backend": get_service()._mode(),
            "hub_backend_available": get_service().hub_backend_available(),
            "sender": sender.name,
            "sender_usable": sender.is_usable(),
            "sender_problem": sender.unusable_reason(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}
