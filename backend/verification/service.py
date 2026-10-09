"""Verification wizard rules (plan 3.3 R3, ADRs 3.1, 3.2, 3.9).

lookup(): name and landline in, a signed ticket and masked methods out.
  A wrong name, an unknown number and an inactive customer give ONE answer
  (VerifyError "invalid_details"), and the same work is done in every case:
  the directory is read, a name comparison runs (against a decoy value when
  there is no customer), a ticket is signed and thrown away, and one audit
  row is written. A number with no row therefore cannot be told from a
  wrong name by content, status or (roughly) timing.

send_code(): six digits from secrets.randbelow, stored only as an HMAC
  (otp.hashing) in otp_challenges with purpose "verify". A new send retires
  the previous code of that number. Brakes: a cooldown per number
  (VERIFY_OTP_RESEND_COOLDOWN_SECONDS), hourly limits per number and per
  address. A request refused by a brake creates nothing.

verify_code(): the attempt is counted BEFORE the comparison by one
  conditional UPDATE (parallel guesses cannot slip under the limit), expiry
  is checked on the SERVER clock (self.clock), the code is single use, and
  after VERIFY_OTP_MAX_ATTEMPTS wrong tries the challenge is locked.

The plain code lives in memory for one request and in the sender's hands.
It is never stored and never logged here (the dev console sender is the one
exception, as in otp.sender). Logs and audit rows carry keyed hashes only.
"""

import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import config
from auth.passwords import hash_secret
from auth.phone import normalize_sl_landline
from auth.ratelimit import SlidingWindowRateLimiter, as_utc
from auth.tokens import TokenError, decode_token, issue_token
from db.base import session_scope
from otp import hashing, masking
from otp.sender import OtpSender
from verification import audit, senders
from verification.lookup import CustomerDirectory, CustomerLookup, CustomerRecord
from verification.models import PURPOSE, challenges, users
from verification.namematch import names_match

AUDIENCE_TICKET = "voice-assistant:verify-ticket"
_DECOY_NAME = "decoy value that no person can match \x1f"
_RETAIN = timedelta(days=1)
_LOOKUP_PHONE_FACTOR = 3  # a few typos in the name are normal before a code is asked for
_EMAIL_MASK = "X" * 9  # fixed width: the length of the address is not revealed


class VerifyError(Exception):
    """reason is one of: invalid_details, rate_limited, cooldown, no_contact,
    ticket_invalid, unavailable, invalid, expired, locked."""

    def __init__(self, reason: str, retry_after: Optional[int] = None, attempts_left: Optional[int] = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after
        self.attempts_left = attempts_left


@dataclass(frozen=True)
class LookupResult:
    ticket: str
    methods: list  # [{"id": "sms"|"email", "masked": str}]


@dataclass(frozen=True)
class SendResult:
    challenge_id: str
    expires_in: int
    resend_in: int
    masked: str


@dataclass(frozen=True)
class VerifyResult:
    user_id: int
    display_name: str
    language: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mask_email_local(email: str) -> str:
    """XXXXXXXXX@gmail.com: local part fully masked, domain kept."""
    _, _, domain = email.partition("@")
    return f"{_EMAIL_MASK}@{domain}"


def methods_for(record: CustomerRecord) -> list:
    methods = []
    if record.mobile_e164:
        methods.append({"id": "sms", "masked": masking.mask_mobile(record.mobile_e164)})
    if record.email:
        methods.append({"id": "email", "masked": mask_email_local(record.email)})
    return methods


def _log(event: str, ref: str, outcome: str) -> None:
    print(f"VERIFY {event} {hashing.log_id(ref)}: {outcome}")


def _language(*candidates: Optional[str]) -> str:
    for value in candidates:
        if value in config.SUPPORTED_LANGUAGES:
            return value
    return "en"


class VerificationService:
    def __init__(
        self,
        lookup: Optional[CustomerLookup] = None,
        sender_resolver: Optional[Callable[[str], Optional[OtpSender]]] = None,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.directory: CustomerLookup = lookup or CustomerDirectory()
        self._resolve_sender = sender_resolver or senders.sender_for
        self.clock = clock  # injectable, so tests can move time
        self._lock = threading.Lock()
        self.verify_limiter = SlidingWindowRateLimiter(config.LOGIN_RATE_LIMIT_PER_MINUTE, 60.0)

    def _get(self, record_id: int) -> Optional[CustomerRecord]:
        getter = getattr(self.directory, "get", None)
        return getter(record_id) if getter else None

    # Step 1

    def lookup(self, name: str, phone: str, language: Optional[str], client_ip: str) -> LookupResult:
        """Raises auth.phone.InvalidPhoneNumber for impossible syntax (decided
        before anything is read), VerifyError otherwise."""
        phone_e164 = normalize_sl_landline(phone)
        phone_hash = hashing.hash_phone(phone_e164)
        ip_hash = hashing.hash_ip(client_ip)
        now = self.clock()
        try:
            with self._lock:
                retry = self._lookup_brake(phone_hash, ip_hash, now)
                if retry is not None:
                    audit.record("lookup", "rate_limited", now, phone_hash, ip_hash)
                    raise VerifyError("rate_limited", retry)
                record = self.directory.find(phone_e164)
                stored = record.name_normalized if record is not None else _DECOY_NAME
                name_ok = names_match(name, stored) if config.NAME_MATCH_REQUIRED else True
                ok = record is not None and name_ok
                chosen = _language(language, record.preferred_language if record else None)
                # Signed for every request so the work is the same; only a
                # genuine match keeps the result.
                token, _ = issue_token(
                    str(record.id if record else 0),
                    AUDIENCE_TICKET,
                    config.VERIFY_TICKET_MINUTES,
                    extra={"tel": phone_e164, "lang": chosen},
                )
                audit.record("lookup", "ok" if ok else "invalid_details", now, phone_hash, ip_hash)
        except SQLAlchemyError as exc:
            print(f"VERIFY lookup failed: database error {type(exc).__name__}")
            raise VerifyError("unavailable") from None
        if not ok:
            raise VerifyError("invalid_details")
        return LookupResult(ticket=token, methods=methods_for(record))

    def _lookup_brake(self, phone_hash: str, ip_hash: str, now: datetime) -> Optional[int]:
        with session_scope() as session:
            for column, value, limit in (
                ("phone_hash", phone_hash, config.VERIFY_RATE_LIMIT_PER_PHONE_PER_HOUR * _LOOKUP_PHONE_FACTOR),
                ("ip_hash", ip_hash, config.VERIFY_RATE_LIMIT_PER_IP_PER_HOUR),
            ):
                if limit <= 0:
                    continue
                table = audit.VerificationAuditRow.__table__
                times = sorted(
                    as_utc(t)
                    for t in session.scalars(
                        sa.select(table.c.at).where(
                            table.c.event == "lookup",
                            table.c.outcome != "rate_limited",
                            table.c[column] == value,
                            table.c.at > now - timedelta(hours=1),
                        )
                    )
                )
                if len(times) >= limit:
                    first = times[len(times) - limit]
                    return max(1, int((first + timedelta(hours=1) - now).total_seconds() + 0.999))
        return None

    # Step 2

    def _open_ticket(self, ticket: str) -> tuple:
        try:
            claims = decode_token(ticket, AUDIENCE_TICKET)
            phone_e164 = str(claims["tel"])
        except (TokenError, KeyError):
            raise VerifyError("ticket_invalid") from None
        record = self.directory.find(phone_e164)
        if record is None or str(record.id) != str(claims.get("sub")):
            raise VerifyError("ticket_invalid")
        return record, claims.get("lang")

    def send_code(self, ticket: str, method: str, language: Optional[str], client_ip: str) -> SendResult:
        ip_hash = hashing.hash_ip(client_ip)
        try:
            record, ticket_language = self._open_ticket(ticket)
        except VerifyError:
            audit.record("send", "ticket_invalid", self.clock(), None, ip_hash)
            raise
        phone_hash = hashing.hash_phone(record.phone_e164)
        now = self.clock()
        options = {m["id"]: m for m in methods_for(record)}
        if method not in options:
            audit.record("send", "no_contact", now, phone_hash, ip_hash, method if method in ("sms", "email") else None)
            raise VerifyError("no_contact")
        destination = record.mobile_e164 if method == "sms" else record.email
        sender = self._resolve_sender(method)
        if sender is None or not sender.is_usable():
            audit.record("send", "unavailable", now, phone_hash, ip_hash, method)
            raise VerifyError("unavailable")
        chosen = _language(language, ticket_language, record.preferred_language)
        code = "".join(str(secrets.randbelow(10)) for _ in range(6))
        challenge_id = uuid.uuid4().hex
        brake = None
        try:
            with self._lock, session_scope() as session:
                now = self.clock()
                brake = self._send_brake(session, phone_hash, ip_hash, now)
                if brake is None:
                    session.execute(sa.delete(challenges).where(challenges.c.created_at < now - _RETAIN))
                    self._insert_challenge(session, challenge_id, code, record, method, destination, phone_hash, ip_hash, chosen, now)
        except SQLAlchemyError as exc:
            print(f"VERIFY send failed: database error {type(exc).__name__}")
            raise VerifyError("unavailable") from None
        if brake is not None:
            # Audited after the transaction closed: SQLite allows one writer.
            audit.record("send", brake[0], now, phone_hash, ip_hash, method)
            raise VerifyError(brake[0], brake[1])

        try:
            sender.send(destination, code, chosen)
        except Exception as exc:  # noqa: BLE001  (typed SenderError or anything else)
            _log("send", challenge_id, f"FAILED {type(exc).__name__}")
            try:
                with session_scope() as session:  # a failed send must not start a cooldown
                    session.execute(sa.delete(challenges).where(challenges.c.id == challenge_id))
            except SQLAlchemyError:
                pass
            audit.record("send", "delivery_failed", self.clock(), phone_hash, ip_hash, method)
            raise VerifyError("unavailable") from None
        audit.record("send", "sent", self.clock(), phone_hash, ip_hash, method)
        _log("send", challenge_id, "delivered to the sender")
        return SendResult(
            challenge_id=challenge_id,
            expires_in=config.VERIFY_OTP_TTL_SECONDS,
            resend_in=config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS,
            masked=options[method]["masked"],
        )

    @staticmethod
    def _insert_challenge(session, challenge_id, code, record, method, destination, phone_hash, ip_hash, chosen, now) -> None:
        # Only the newest code works: sending retires the older one.
        session.execute(
            sa.update(challenges)
            .where(
                challenges.c.phone_hash == phone_hash,
                challenges.c.purpose == PURPOSE,
                challenges.c.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        session.execute(
            sa.insert(challenges).values(
                id=challenge_id,
                user_id=None,
                phone_hash=phone_hash,
                code_hash=hashing.hash_code(challenge_id, code),
                destination_hash=hashing.hash_destination(destination),
                channel=method,
                attempts=0,
                expires_at=now + timedelta(seconds=config.VERIFY_OTP_TTL_SECONDS),
                consumed_at=None,
                created_at=now,
                client_ip_hash=ip_hash,
                hub_ref=None,
                purpose=PURPOSE,
                wizard_ref=f"{record.id}:{chosen}",
            )
        )
    @staticmethod
    def _send_brake(session, phone_hash: str, ip_hash: str, now: datetime) -> Optional[tuple]:
        """("cooldown"|"rate_limited", retry_after) or None."""
        cooldown = config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS
        if cooldown > 0:
            latest = session.scalar(
                sa.select(sa.func.max(challenges.c.created_at)).where(
                    challenges.c.phone_hash == phone_hash, challenges.c.purpose == PURPOSE
                )
            )
            if latest is not None:
                left = (as_utc(latest) + timedelta(seconds=cooldown) - now).total_seconds()
                if left > 0:
                    return "cooldown", int(left + 0.999)
        for column, value, limit in (
            (challenges.c.phone_hash, phone_hash, config.VERIFY_RATE_LIMIT_PER_PHONE_PER_HOUR),
            (challenges.c.client_ip_hash, ip_hash, config.VERIFY_RATE_LIMIT_PER_IP_PER_HOUR),
        ):
            if limit <= 0:
                continue
            times = sorted(
                as_utc(t)
                for t in session.scalars(
                    sa.select(challenges.c.created_at).where(
                        column == value, challenges.c.purpose == PURPOSE, challenges.c.created_at > now - timedelta(hours=1)
                    )
                )
            )
            if len(times) >= limit:
                first = times[len(times) - limit]
                return "rate_limited", max(1, int((first + timedelta(hours=1) - now).total_seconds() + 0.999))
        return None

    # Step 3

    def verify_code(self, challenge_id: str, code: str, client_ip: str) -> VerifyResult:
        ip_hash = hashing.hash_ip(client_ip)
        retry = self.verify_limiter.hit(client_ip or "unknown")
        if retry is not None:
            audit.record("verify", "rate_limited", self.clock(), None, ip_hash)
            raise VerifyError("rate_limited", retry)
        challenge_id = (challenge_id or "")[:64]
        code = code or ""
        well_formed = len(code) == 6 and code.isascii() and code.isdigit()
        now = self.clock()
        try:
            outcome, wizard_ref, phone_hash, channel, attempts = self._check(challenge_id, code if well_formed else "", now)
        except SQLAlchemyError as exc:
            print(f"VERIFY verify failed: database error {type(exc).__name__}")
            raise VerifyError("unavailable") from None
        audit.record("verify", outcome, now, phone_hash, ip_hash, channel)
        _log("verify", challenge_id, outcome)
        if outcome == "ok":
            result = self._finish(wizard_ref, now)
            if result is None:
                raise VerifyError("invalid", attempts_left=0)
            return result
        if outcome == "expired":
            raise VerifyError("expired")
        if outcome == "locked":
            raise VerifyError("locked", max(1, config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS))
        left = 0 if attempts is None else max(0, config.VERIFY_OTP_MAX_ATTEMPTS - attempts)
        raise VerifyError("invalid", attempts_left=left)

    def _check(self, challenge_id: str, code: str, now: datetime) -> tuple:
        """(outcome, wizard_ref, phone_hash, channel, attempts) with outcome
        ok, invalid, expired or locked. The attempt is counted first by one
        conditional UPDATE; a correct code is accepted by the one request
        that wins the consume UPDATE (single use)."""
        max_attempts = config.VERIFY_OTP_MAX_ATTEMPTS
        with session_scope() as session:
            counted = session.execute(
                sa.update(challenges)
                .where(
                    challenges.c.id == challenge_id,
                    challenges.c.purpose == PURPOSE,
                    challenges.c.consumed_at.is_(None),
                    challenges.c.attempts < max_attempts,
                )
                .values(attempts=challenges.c.attempts + 1)
            ).rowcount
            row = session.execute(
                sa.select(challenges).where(challenges.c.id == challenge_id, challenges.c.purpose == PURPOSE)
            ).first()
            if row is None:
                hashing.verify_code(challenge_id, code, "")  # same work as a real check
                return "invalid", None, None, None, None
            if as_utc(row.expires_at) <= now:
                return "expired", None, row.phone_hash, row.channel, row.attempts
            if row.consumed_at is not None:
                return "invalid", None, row.phone_hash, row.channel, max_attempts
            if not counted:
                return "locked", None, row.phone_hash, row.channel, row.attempts
            matches = hashing.verify_code(challenge_id, code, row.code_hash) and code != ""
            if matches:
                consumed = session.execute(
                    sa.update(challenges)
                    .where(challenges.c.id == challenge_id, challenges.c.consumed_at.is_(None))
                    .values(consumed_at=now)
                ).rowcount
                if consumed:
                    return "ok", row.wizard_ref, row.phone_hash, row.channel, row.attempts
                return "invalid", None, row.phone_hash, row.channel, max_attempts
            if row.attempts >= max_attempts:
                return "locked", None, row.phone_hash, row.channel, row.attempts
            return "invalid", None, row.phone_hash, row.channel, row.attempts

    def _finish(self, wizard_ref: Optional[str], now: datetime) -> Optional[VerifyResult]:
        """Finds or creates the users row for the verified customer."""
        try:
            record_id, _, lang = (wizard_ref or "").partition(":")
            record = self._get(int(record_id))
        except ValueError:
            return None
        if record is None:
            return None
        language = _language(lang, record.preferred_language)
        try:
            user_id, display_name = self._find_or_create_user(record, language, now)
        except SQLAlchemyError as exc:
            print(f"VERIFY sign in failed: database error {type(exc).__name__}")
            raise VerifyError("unavailable") from None
        return VerifyResult(user_id=user_id, display_name=display_name, language=language)

    @staticmethod
    def _find_or_create_user(record: CustomerRecord, language: str, now: datetime) -> tuple:
        def find():
            with session_scope() as session:
                return session.execute(
                    sa.select(users.c.id, users.c.display_name).where(users.c.phone_e164 == record.phone_e164)
                ).first()

        row = find()
        if row is None:
            try:
                with session_scope() as session:
                    session.execute(
                        sa.insert(users).values(
                            phone_e164=record.phone_e164,
                            display_name=record.display_name[:80],
                            # A random PIN nobody sees: the code is the only way in.
                            pin_hash=hash_secret(secrets.token_urlsafe(24)),
                            preferred_language=language,
                            failed_attempts=0,
                            created_at=now,
                        )
                    )
            except IntegrityError:
                pass  # created by a parallel request
            row = find()
        with session_scope() as session:
            session.execute(sa.update(users).where(users.c.id == row.id).values(last_login_at=now))
        return row.id, row.display_name


# Process wide instance

_service: Optional[VerificationService] = None
_service_lock = threading.Lock()


def get_service() -> VerificationService:
    global _service
    with _service_lock:
        if _service is None:
            _service = VerificationService()
        return _service


def set_service(service: Optional[VerificationService]) -> None:
    """Replaces the process wide service (tests)."""
    global _service
    with _service_lock:
        _service = service
