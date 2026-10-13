"""Hub mode of the verification wizard (plan 4.3, VERIFY_OTP_BACKEND=hub).

The hub looks the customer up AND sends the code, so this mode keeps no
customer directory and generates no code. What stays OURS is every rule
around the code: 120 s from the send on the server clock, three attempts
counted before the hub is asked, single use, a new send retires the old
challenge, the resend cooldown and the hourly limits. The hub is asked only
after those rules pass.

lookup():  format and rate limits only. One generic "sms" method, no email.
           A name is optional and is used for the greeting only.
send():    HubOtpBackend.request (parsing lives in otp.hub_backend, reused).
           "Not a customer" becomes a DECOY challenge that returns the same
           response, writes the same kind of row and is counted by the same
           limits. The hub session id is sealed with Fernet in
           otp_challenges.hub_ref (the same sealing otp.service uses).
verify():  our rules first, then HubOtpBackend.verify. A decoy challenge
           asks the hub too (with a random session id it cannot know), so a
           wrong code costs the same time for a customer and a non customer.
           Hub trouble, a timeout or an unrecognised answer is "unavailable"
           (503) and never "invalid"; the one attempt already counted stays
           counted, nothing more is used up.

Logs and audit rows carry keyed hashes only: never a code, a session id, a
full number, a name or anything the hub answered.
"""

import json
import unicodedata
import uuid
from datetime import timedelta
from typing import Optional

import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy.exc import SQLAlchemyError

import config
import i18n
from auth.phone import normalize_sl_landline
from auth.ratelimit import as_utc
from auth.tokens import TokenError, decode_token, issue_token
from db.base import session_scope
from mcp_client.errors import HubToolError
from otp import hashing
from otp.hub_backend import HubOtpBackend
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

HUB_METHODS = [{"id": "sms", "masked": None, "generic": True}]
# The code column is NOT NULL. This value is not a six digit string, so no
# typed code can ever match it: hub challenges are never compared locally.
_NO_LOCAL_CODE = "hub"
_NEUTRAL_FALLBACK = "Customer"


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


def _backend(service: VerificationService) -> HubOtpBackend:
    backend = getattr(service, "hub_otp", None)
    if backend is None:
        backend = HubOtpBackend()
        service.hub_otp = backend
    return backend


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
    if method != "sms":
        audit.record("send", "no_contact", now, phone_hash, ip_hash, method if method in ("sms", "email") else None)
        raise VerifyError("no_contact")
    chosen = _language(language, claims.get("lang"))
    typed_name = clean_name(claims.get("nm"))

    try:
        brake = _brake_now(service, phone_hash, ip_hash)  # cheap refusal before the hub can send an SMS
    except SQLAlchemyError as exc:
        print(f"VERIFY send failed: database error {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    if brake is not None:
        audit.record("send", brake[0], service.clock(), phone_hash, ip_hash, "sms")
        raise VerifyError(brake[0], brake[1])

    try:
        try:
            start = _backend(service).request(phone_e164)
        except HubToolError:
            start = None  # the hub refuses the number: the same as "not a customer", as in otp.service
    except Exception as exc:  # noqa: BLE001  (HubError family, timeout, unrecognised answer)
        print(f"VERIFY hub request failed: {type(exc).__name__}")
        audit.record("send", "unavailable", service.clock(), phone_hash, ip_hash, "sms")
        raise VerifyError("unavailable") from None

    decoy = start is None
    sealed = _seal(
        {
            "p": phone_e164,
            "s": uuid.uuid4().hex if decoy else start.session_id,
            "hn": None if decoy else start.display_name,
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
                        channel="sms",
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
        print(f"VERIFY send failed: database error {type(exc).__name__}")
        raise VerifyError("unavailable") from None
    if brake is not None:
        audit.record("send", brake[0], now, phone_hash, ip_hash, "sms")
        raise VerifyError(brake[0], brake[1])
    audit.record("send", "decoy" if decoy else "sent", service.clock(), phone_hash, ip_hash, "sms")
    _log("send", challenge_id, "decoy, hub said not a customer" if decoy else "hub sent the code")
    # masked is always None: a mask the hub sent would exist only for real
    # customers, so it is never shown (the page uses a generic sentence).
    return SendResult(
        challenge_id=challenge_id,
        expires_in=config.VERIFY_OTP_TTL_SECONDS,
        resend_in=config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS,
        masked=None,
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
        else:
            try:
                accepted, verify_name = _backend(service).verify(sealed["p"], sealed["s"], code)
            except Exception as exc:  # noqa: BLE001  (HubError family, timeout, unrecognised answer)
                print(f"VERIFY hub verify failed: {type(exc).__name__}")
                outcome = "unavailable"
            else:
                if accepted and not sealed.get("d"):
                    if _consume(challenge_id, now):
                        outcome = "ok"
                        hub_name = verify_name or sealed.get("hn")
                        typed_name = sealed.get("tn")
                    else:
                        outcome, attempts = "invalid", config.VERIFY_OTP_MAX_ATTEMPTS
                else:
                    outcome = "locked" if (attempts or 0) >= config.VERIFY_OTP_MAX_ATTEMPTS else "invalid"

    audit.record("verify", outcome, now, phone_hash, ip_hash, info.get("channel"))
    _log("verify", challenge_id, outcome)
    if outcome == "ok":
        result = _finish(sealed["p"], info.get("ref_language"), hub_name, typed_name, now)
        return result
    if outcome == "unavailable":
        raise VerifyError("unavailable")
    if outcome == "expired":
        raise VerifyError("expired")
    if outcome == "locked":
        raise VerifyError("locked", max(1, config.VERIFY_OTP_RESEND_COOLDOWN_SECONDS))
    left = 0 if attempts is None else max(0, config.VERIFY_OTP_MAX_ATTEMPTS - attempts)
    raise VerifyError("invalid", attempts_left=left)


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

