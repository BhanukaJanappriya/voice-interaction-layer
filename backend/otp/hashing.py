"""Keyed hashes for everything the OTP tables hold about a person or a code.

Why HMAC-SHA256 keyed with JWT_SECRET and not bcrypt for the code: a six
digit code has only a million values, so ANY unkeyed or fast hash of it is
reversed in a blink by trying them all. Keying with a server secret means a
copy of the database alone reveals nothing, and the challenge id is part of
the message so two challenges that happen to share a code do not share a
hash and a hash cannot be moved to another challenge. bcrypt would add
about 100 ms to every attempt for no gain here: the real protection is the
3 attempt limit and the 5 minute expiry, which cap a guesser at 3 in a
million per challenge.

The same keyed construction (different purpose labels, so one kind of
value can never be mistaken for another) is used for phone numbers, client
addresses and destinations. The tables therefore hold no phone number, no
address and no code, only values that identify them to this server; and the
logs carry only log_id(), a short hash of the challenge id.
"""

import hashlib
import hmac

import config


def _mac(purpose: str, *parts: str) -> str:
    message = "\x1f".join((purpose, *parts)).encode("utf-8")
    return hmac.new(config.JWT_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def hash_code(challenge_id: str, code: str) -> str:
    return _mac("otp-code", challenge_id, code)


def verify_code(challenge_id: str, code: str, stored_hash: str) -> bool:
    """Constant time comparison."""
    return hmac.compare_digest(hash_code(challenge_id, code), stored_hash or "")


def hash_phone(phone_e164: str) -> str:
    return _mac("otp-phone", phone_e164)


def hash_ip(client_ip: str) -> str:
    return _mac("otp-ip", client_ip or "unknown")


def hash_destination(destination: str) -> str:
    return _mac("otp-destination", destination)


def log_id(challenge_id: str) -> str:
    """What may appear in a log line instead of the challenge id."""
    return _mac("otp-log", challenge_id)[:10]
