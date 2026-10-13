"""Hub mode of the verification wizard (plan 4.3, VERIFY_OTP_BACKEND=hub).

The hub looks the customer up AND sends the code, so this mode keeps no
customer directory and generates no code. What stays OURS is every rule
around the code: 120 s from the send on the server clock, three attempts
counted before the hub is asked, single use, a new send retires the old
challenge, the resend cooldown and the hourly limits. The hub is asked only
after those rules pass.

lookup():  format and rate limits only. Two generic methods (sms, email): the
           hub gives no mask before it has sent a code. A name is optional
           and is used for the greeting only.
send():    mcp_gateway.begin_login with otp_media 1 (sms) or 2 (email). The
           hub login belongs to ONE open hub connection, which the gateway
           keeps. "Not a customer" becomes a DECOY challenge that returns the
           same response shape (with a fabricated mask of the same form),
           writes the same kind of row and is counted by the same limits.
           The hub session id is sealed with Fernet in otp_challenges.hub_ref
           (the same sealing otp.service uses). The mask goes to the browser
           only.
verify():  our rules first, then mcp_gateway.finish_login on the same hub
           connection. A decoy never reaches the hub; it waits about as long
           as the last real check took. Hub trouble, a timeout or an
           unrecognised answer is "unavailable" (503) and never "invalid";
           the one attempt already counted stays counted. On success the
           gateway keeps the logged in connection; nothing about the customer
           except the display name leaves this module.

Logs and audit rows carry keyed hashes only: never a code, a session id, a
full number, a name, a mask or anything the hub answered.
"""

import json
import random
import time
import unicodedata
import uuid
from datetime import timedelta
from typing import Optional

import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy.exc import SQLAlchemyError

import config
import i18n
import mcp_gateway
from auth.phone import normalize_sl_landline
from auth.ratelimit import as_utc
from auth.tokens import TokenError, decode_token, issue_token
from db.base import session_scope
from otp import hashing
from otp.service import OtpService
from verification import audit
from verification.lookup import CustomerRecord
from verification.models import PURPOSE, challenges, users
from verification.service import (
    AUDIENCE_TICKET,
    LookupResult,
    SendResult,
    VerificationService,
    VerifyError,
    VerifyResult,
    _RETAIN,
    _language,
    _log,
)

HUB_METHODS = [
    {"id": "sms", "masked": None, "generic": True},
    {"id": "email", "masked": None, "generic": True},
]
_OTP_MEDIA = {"sms": 1, "email": 2}  # the hub's otp_media: 1 SMS, 2 email
_MASK_CHARS = set("Xx*")
_check_seconds = 0.15  # how long the last real hub check took (a decoy waits about as long)
# The code column is NOT NULL. This value is not a six digit string, so no
# typed code can ever match it: hub challenges are never compared locally.
_NO_LOCAL_CODE = "hub"
_NEUTRAL_FALLBACK = "Customer"

# finish_login reason -> our outcome. Anything not listed is "invalid".
_HUB_REASONS = {
    "unavailable": "unavailable",
    "expired": "expired",
    "no_pending": "expired",  # the hub login was lost (restart or sweep): ask for a new code
    "locked": "locked",
    "too_many_attempts": "locked",
}


def active() -> bool:
    return str(getattr(config, "VERIFY_OTP_BACKEND", "local") or "local").strip().lower() == "hub"


def clean_name(value: Optional[str]) -> str:
    """NFC, no control characters, single spaces, at most 80 characters."""
    text = unicodedata.normalize("NFC", value or "")
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))
    return " ".join(text.split())[:80]


def _neutral_name(language: str) -> str:
    word = i18n.t("verify.neutral_name", language)
    return _NEUTRAL_FALLBACK if word == "verify.neutral_name" else word


def _national(phone_e164: str) -> str:
    return "0" + phone_e164[3:]


def _safe_mask(value: Optional[str]) -> Optional[str]:
    """The hub's mask for the chosen channel, or None. Anything that does not
    look masked is dropped: an unmasked contact never reaches the browser."""
    text = str(value or "").strip()[:80]
    if sum(1 for ch in text if ch in _MASK_CHARS) < 3:
        return None
    return text


def _fake_mask(method: str) -> str:
    """A mask of the same shape the hub returns, for a decoy challenge."""
    if method == "email":
        return "X" * 10 + "".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(4)) + "@gmail.com"
    return "X" * 6 + "".join(random.choice("0123456789") for _ in range(4))


def _remember_check(seconds: float) -> None:
    global _check_seconds
    _check_seconds = min(2.0, max(0.02, 0.5 * _check_seconds + 0.5 * seconds))


def _decoy_wait() -> None:
    time.sleep(_check_seconds * random.uniform(0.9, 1.1))


def _seal(data: dict) -> str:
    return OtpService._fernet().encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def _unseal(token: str) -> Optional[dict]:
    try:
        data = json.loads(OtpService._fernet().decrypt(token.encode("ascii")))
    except (InvalidToken, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


# Step 1

def lookup(service: VerificationService, name: str, phone: str, language: Optional[str], client_ip: str) -> LookupResult:
    phone_e164 = normalize_sl_landline(phone)  # raises InvalidPhoneNumber before anything else
    phone_hash = hashing.hash_phone(phone_e164)
    ip_hash = hashing.hash_ip(client_ip)
    now = service.clock()
    chosen = _language(language)
    try:
        with service._lock:
            retry = service._lookup_brake(phone_hash, ip_hash, now)
            if retry is not None:
                audit.record("lookup", "rate_limited", now, phone_hash, ip_hash)
                raise VerifyError("rate_limited", retry)
            token, _ = issue_token(
                "hub",
                AUDIENCE_TICKET,
                config.VERIFY_TICKET_MINUTES,
                extra={"tel": phone_e164, "lang": chosen, "hub": True, "nm": clean_name(name)},
            )
            audit.record("lookup", "ok", now, phone_hash, ip_hash)
    except SQLAlchemyError as exc:
        print(f"VERIFY lookup failed: {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    return LookupResult(ticket=token, methods=[dict(m) for m in HUB_METHODS], mode="hub")


# Step 2

def _open_ticket(ticket: str) -> dict:
    try:
        claims = decode_token(ticket, AUDIENCE_TICKET)
        if claims.get("hub") is not True or not claims.get("tel"):
            raise KeyError("not a hub ticket")
        return claims
    except (TokenError, KeyError):
        raise VerifyError("ticket_invalid") from None


def send_code(service: VerificationService, ticket: str, method: str, language: Optional[str], client_ip: str) -> SendResult:
    ip_hash = hashing.hash_ip(client_ip)
    try:
        claims = _open_ticket(ticket)
    except VerifyError:
        audit.record("send", "ticket_invalid", service.clock(), None, ip_hash)
        raise
    phone_e164 = str(claims["tel"])
    phone_hash = hashing.hash_phone(phone_e164)
    now = service.clock()
    if method not in _OTP_MEDIA:
        audit.record("send", "no_contact", now, phone_hash, ip_hash, None)
        raise VerifyError("no_contact")
    chosen = _language(language, claims.get("lang"))
    typed_name = clean_name(claims.get("nm"))

    try:
        brake = _brake_now(service, phone_hash, ip_hash)  # cheap refusal before the hub can send a code
    except SQLAlchemyError as exc:
        print(f"VERIFY send failed: database error {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    if brake is not None:
        audit.record("send", brake[0], service.clock(), phone_hash, ip_hash, method)
        raise VerifyError(brake[0], brake[1])

    start = mcp_gateway.begin_login(_national(phone_e164), _OTP_MEDIA[method])
    if not start.ok and start.reason in ("unavailable", "refused"):
        print(f"VERIFY hub request failed: {start.reason}")
        audit.record("send", "unavailable", service.clock(), phone_hash, ip_hash, method)
        raise VerifyError("unavailable") from None
    # The hub refusing the number (rejected, invalid_number) is "not a customer".
    decoy = not start.ok
    if decoy:
        masked = _fake_mask(method)
    else:
        masked = _safe_mask(start.masked_email if method == "email" else start.masked_mobile)
    sealed = _seal(
        {
            "p": phone_e164,
            "s": uuid.uuid4().hex if decoy else start.hub_session_id,
            "tn": typed_name,
            "d": decoy,
        }
    )
    challenge_id = uuid.uuid4().hex
    brake = None
    try:
        with service._lock, session_scope() as session:
            now = service.clock()
            brake = VerificationService._send_brake(session, phone_hash, ip_hash, now)  # again: another request may have got in
            if brake is None:
                session.execute(sa.delete(challenges).where(challenges.c.created_at < now - _RETAIN))
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
                        code_hash=hashing.hash_code(challenge_id, _NO_LOCAL_CODE),
                        destination_hash=hashing.hash_destination("hub"),
                        channel=method,
                        attempts=0,
                        expires_at=now + timedelta(seconds=config.VERIFY_OTP_TTL_SECONDS),
                        consumed_at=None,
                        created_at=now,
                        client_ip_hash=ip_hash,
                        hub_ref=sealed,
                        purpose=PURPOSE,
                        wizard_ref=f"hub:{chosen}",
                    )
                )
    except SQLAlchemyError as exc:
        if not decoy:
            mcp_gateway.end_login(_national(phone_e164))
        print(f"VERIFY send failed: database error {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    if brake is not None:
        if not decoy:
            mcp_gateway.end_login(_national(phone_e164))
        audit.record("send", brake[0], now, phone_hash, ip_hash, method)
        raise VerifyError(brake[0], brake[1])
    audit.record("send", "decoy" if decoy else "sent", service.clock(), phone_hash, ip_hash, method)
    _log("send", challenge_id, "decoy, hub said not a customer" if decoy else "hub sent the code")
    # masked: the hub's mask for the chosen channel (a fabricated one of the
    # same shape for a decoy). It goes to the browser only, never to a log.
    return SendResult(
        challenge_id=challenge_id,
        expires_in=config.VERIFY_OTP_TTL_SECONDS,
        resend_in=config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS,
        masked=masked,
    )


def _brake_now(service: VerificationService, phone_hash: str, ip_hash: str):
    with session_scope() as session:
        return VerificationService._send_brake(session, phone_hash, ip_hash, service.clock())


# Step 3

def verify_code(service: VerificationService, challenge_id: str, code: str, client_ip: str) -> VerifyResult:
    """Called after the per address verify limiter has already been hit."""
    ip_hash = hashing.hash_ip(client_ip)
    challenge_id = (challenge_id or "")[:64]
    code = code or ""
    well_formed = len(code) == 6 and code.isascii() and code.isdigit()
    now = service.clock()
    try:
        outcome, info = _check(challenge_id, well_formed, now)
    except SQLAlchemyError as exc:
        print(f"VERIFY verify failed: database error {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    phone_hash = info.get("phone_hash")
    attempts = info.get("attempts")

    typed_name = hub_name = None
    if outcome == "ask":
        sealed = _unseal(info["ref"])
        if sealed is None:
            outcome = "unavailable"
        elif sealed.get("d"):
            _decoy_wait()  # a decoy has no hub login to check
            outcome = "locked" if (attempts or 0) >= config.VERIFY_OTP_MAX_ATTEMPTS else "invalid"
        else:
            started = time.perf_counter()
            result = mcp_gateway.finish_login(_national(sealed["p"]), sealed["s"], code)
            _remember_check(time.perf_counter() - started)
            if result.ok:
                if _consume(challenge_id, now):
                    outcome = "ok"
                    hub_name = result.name
                    typed_name = sealed.get("tn")
                else:
                    _drop_login(sealed["p"])  # a parallel request used the challenge first
                    outcome, attempts = "invalid", config.VERIFY_OTP_MAX_ATTEMPTS
            else:
                outcome = _HUB_REASONS.get(result.reason, "invalid")
                if outcome == "invalid" and (attempts or 0) >= config.VERIFY_OTP_MAX_ATTEMPTS:
                    outcome = "locked"

    audit.record("verify", outcome, now, phone_hash, ip_hash, info.get("channel"))
    _log("verify", challenge_id, outcome)
    if outcome == "ok":
        return _finish(sealed["p"], info.get("ref_language"), hub_name, typed_name, now)
    if outcome == "unavailable":
        raise VerifyError("unavailable")
    if outcome == "expired":
        raise VerifyError("expired")
    if outcome == "locked":
        raise VerifyError("locked", max(1, config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS))
    left = 0 if attempts is None else max(0, config.VERIFY_OTP_MAX_ATTEMPTS - attempts)
    raise VerifyError("invalid", attempts_left=left)


def _drop_login(phone_e164: str) -> None:
    from access import AccessContext

    mcp_gateway.drop_session(AccessContext.verified(None, None, phone_e164))


def _check(challenge_id: str, well_formed: bool, now) -> tuple:
    """(outcome, info) with outcome ask, invalid, expired or locked. The
    attempt is counted FIRST by one conditional UPDATE, then expiry and the
    attempt limit are applied; only "ask" means the hub may be called."""
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
            hashing.verify_code(challenge_id, "", "")  # a similar amount of work
            return "invalid", {}
        info = {
            "phone_hash": row.phone_hash,
            "channel": row.channel,
            "attempts": row.attempts,
            "ref": row.hub_ref,
            "ref_language": (row.wizard_ref or "").partition(":")[2],
        }
        if as_utc(row.expires_at) <= now:
            return "expired", info
        if row.consumed_at is not None:
            return "invalid", {**info, "attempts": max_attempts}
        if not counted:
            return "locked", info
        if well_formed and row.hub_ref:
            return "ask", info
        if row.attempts >= max_attempts:
            return "locked", info
        return "invalid", info


def _consume(challenge_id: str, now) -> bool:
    with session_scope() as session:
        return bool(
            session.execute(
                sa.update(challenges)
                .where(challenges.c.id == challenge_id, challenges.c.consumed_at.is_(None))
                .values(consumed_at=now)
            ).rowcount
        )


def _finish(phone_e164: str, language: Optional[str], hub_name: Optional[str], typed_name: Optional[str], now) -> VerifyResult:
    chosen = _language(language)
    name = clean_name(hub_name) or clean_name(typed_name) or _neutral_name(chosen)
    record = CustomerRecord(
        id=0, phone_e164=phone_e164, display_name=name, name_normalized="", mobile_e164=None, email=None,
        preferred_language=chosen,
    )
    try:
        user_id, display_name = VerificationService._find_or_create_user(record, chosen, now)
        if clean_name(hub_name) and display_name != name:
            with session_scope() as session:  # the hub's name wins over an older one
                session.execute(sa.update(users).where(users.c.id == user_id).values(display_name=name))
            display_name = name
    except SQLAlchemyError as exc:
        print(f"VERIFY sign in failed: database error {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    return VerifyResult(user_id=user_id, display_name=display_name, language=chosen, hub=True)
