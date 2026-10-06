"""Masked destinations: enough for a person to recognise where the code
went, not enough to learn the contact detail.

    mobile  +94771234567        -> "07X XXX X567"
    email   nimal@example.lk    -> "n***@e***.lk"

Real and decoy destinations (see service.py, anti enumeration) come out of
the same two functions, so their shapes cannot differ.
"""

_DECOY_TLDS = ("com", "lk", "com", "net", "lk", "org")


def mask_mobile(mobile_e164: str) -> str:
    """Keeps the 07 style prefix and the last three digits."""
    digits = "".join(ch for ch in mobile_e164 if ch.isdigit())
    return f"07X XXX X{digits[-3:].rjust(3, '0')}"


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    labels = domain.split(".")
    tld = labels[-1] if len(labels) > 1 else ""
    host = labels[0] if labels and labels[0] else "x"
    masked_domain = f"{host[0]}***" + (f".{tld}" if tld else "")
    return f"{(local or 'x')[0]}***@{masked_domain}"


def decoy_mobile(seed_hex: str) -> str:
    """A plausible masked mobile that is always the same for the same
    seed (the hash of the number asked about), so asking twice does not
    give two different answers."""
    return f"07X XXX X{int(seed_hex[:8], 16) % 1000:03d}"


def decoy_email(seed_hex: str) -> str:
    def letter(position: int) -> str:
        return chr(97 + int(seed_hex[position : position + 2], 16) % 26)

    tld = _DECOY_TLDS[int(seed_hex[4:6], 16) % len(_DECOY_TLDS)]
    return f"{letter(0)}***@{letter(2)}***.{tld}"
