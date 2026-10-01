"""auth/tokens.py: audience separation, expiry, tampering."""

import base64
import json

import jwt
import pytest

import config
from auth import tokens
from auth.tokens import AUDIENCE_ADMIN, AUDIENCE_MCP, AUDIENCE_USER, TokenError, decode_token, issue_token


def test_round_trip_returns_claims():
    token, expires_at = issue_token("42", AUDIENCE_USER, 5, extra={"name": "Nimal"})
    claims = decode_token(token, AUDIENCE_USER)
    assert claims["sub"] == "42"
    assert claims["exp"] == expires_at
    assert claims["iss"] == config.PUBLIC_BASE_URL
    assert claims["name"] == "Nimal"


def test_extra_cannot_override_registered_claims():
    token, _ = issue_token("42", AUDIENCE_USER, 5, extra={"sub": "1", "aud": AUDIENCE_ADMIN})
    claims = decode_token(token, AUDIENCE_USER)
    assert claims["sub"] == "42"
    with pytest.raises(TokenError):
        decode_token(token, AUDIENCE_ADMIN)


@pytest.mark.parametrize(
    "issued_for, used_as",
    [
        (AUDIENCE_ADMIN, AUDIENCE_USER),
        (AUDIENCE_ADMIN, AUDIENCE_MCP),
        (AUDIENCE_USER, AUDIENCE_ADMIN),
        (AUDIENCE_USER, AUDIENCE_MCP),
        (AUDIENCE_MCP, AUDIENCE_ADMIN),
        (AUDIENCE_MCP, AUDIENCE_USER),
    ],
)
def test_audiences_are_not_interchangeable(issued_for, used_as):
    token, _ = issue_token("1", issued_for, 5)
    with pytest.raises(TokenError):
        decode_token(token, used_as)


def test_audiences_are_distinct():
    assert len({AUDIENCE_USER, AUDIENCE_ADMIN, AUDIENCE_MCP}) == 3


def test_expired_token_rejected():
    token, expires_at = issue_token("1", AUDIENCE_USER, -1)
    with pytest.raises(TokenError):
        decode_token(token, AUDIENCE_USER)


def test_missing_token_rejected():
    for empty in (None, ""):
        with pytest.raises(TokenError):
            decode_token(empty, AUDIENCE_USER)


def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_tampered_payload_rejected():
    token, _ = issue_token("1", AUDIENCE_USER, 5)
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = "2"
    forged = ".".join([header, _b64(claims), signature])
    with pytest.raises(TokenError):
        decode_token(forged, AUDIENCE_USER)


def test_wrong_secret_rejected():
    token, _ = issue_token("1", AUDIENCE_USER, 5)
    claims = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode(claims, "some-other-secret-that-is-long-enough-000", algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(forged, AUDIENCE_USER)


def test_alg_none_rejected():
    token, _ = issue_token("1", AUDIENCE_USER, 5)
    claims = jwt.decode(token, options={"verify_signature": False})
    unsigned = _b64({"alg": "none", "typ": "JWT"}) + "." + _b64(claims) + "."
    with pytest.raises(TokenError):
        decode_token(unsigned, AUDIENCE_USER)


def test_wrong_issuer_rejected():
    token, _ = issue_token("1", AUDIENCE_USER, 5)
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["iss"] = "http://evil.example"
    forged = jwt.encode(claims, config.JWT_SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(forged, AUDIENCE_USER)


def test_garbage_rejected():
    with pytest.raises(TokenError):
        decode_token("not.a.token", AUDIENCE_USER)


def test_scopes():
    token, _ = issue_token("c1", AUDIENCE_MCP, 5, scopes=["knowledge:read", "assistant:ask"])
    assert tokens.token_scopes(decode_token(token, AUDIENCE_MCP)) == {"knowledge:read", "assistant:ask"}
    token, _ = issue_token("c1", AUDIENCE_MCP, 5)
    assert tokens.token_scopes(decode_token(token, AUDIENCE_MCP)) == set()


def test_passwords_hash_and_verify():
    from auth.passwords import hash_secret, verify_secret

    hashed = hash_secret("1234")
    assert hashed != "1234"
    assert verify_secret("1234", hashed)
    assert not verify_secret("1235", hashed)
    assert not verify_secret("1234", None)
    assert not verify_secret("1234", "not-a-bcrypt-hash")
