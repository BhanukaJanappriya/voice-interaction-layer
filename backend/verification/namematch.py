"""Name comparison for the wizard (plan 3.3): NFC, casefold, collapsed
whitespace, punctuation stripped. Combining marks are kept, because in
Sinhala and Tamil they are part of the letters.

The comparison is constant time over the encoded bytes, and compare() takes
a stored value of any shape, so a decoy comparison costs the same as a real
one.
"""

import hmac
import unicodedata


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFC", name or "").casefold()
    kept = []
    for ch in text:
        category = unicodedata.category(ch)
        if category.startswith("P"):
            continue  # punctuation, including . , - ' and the like
        kept.append(" " if ch.isspace() or category.startswith("Z") else ch)
    return " ".join("".join(kept).split())


def names_match(typed: str, stored_normalized: str) -> bool:
    """typed is raw input, stored_normalized is already normalized. An empty
    typed name never matches."""
    candidate = normalize_name(typed)
    equal = hmac.compare_digest(candidate.encode("utf-8"), (stored_normalized or "").encode("utf-8"))
    return equal and bool(candidate)
