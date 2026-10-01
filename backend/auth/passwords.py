"""One way hashing for every secret a person or client proves they know:
user PINs, admin passwords and MCP client secrets. bcrypt, because it is
deliberately slow and salted per value, so a copied database does not
give the secrets away."""

import bcrypt

# A valid hash of a random value, checked against when the account does
# not exist, so a wrong phone number takes as long to reject as a wrong
# PIN and response timing does not reveal which numbers are registered.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-secret", bcrypt.gensalt()).decode("ascii")


def hash_secret(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_secret(plain: str, hashed: str | None) -> bool:
    """False for a wrong value or a missing/malformed hash; never raises."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), (hashed or _DUMMY_HASH).encode("ascii")) and bool(hashed)
    except ValueError:
        return False
