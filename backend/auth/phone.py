"""Sri Lankan landline numbers: accept the ways people actually type
them, store one canonical form.

Existing users are found by their SLT landline number. People write it in
many shapes (011 234 5678, 0112345678, +94 11 234 5678, 0094-11-2345678,
(011) 234-5678), so every number is normalized to E.164 (+94XXXXXXXXX)
before it is stored or looked up; otherwise the same line could be
registered twice or not be found at all.

Numbering plan: a trunk prefix 0, a two digit area code, then a seven
digit subscriber number (the Wikipedia article writes it as "xxx y zzzzzz",
10 digits in total). Mobile numbers (07x) are refused on purpose: this
verification is for landline customers, and a mobile number typed by
mistake should get a clear message instead of a silent "not found".

This is a pure function (no database, no configuration), so it is easy to
test and can be used for input validation anywhere.

Standalone demo:  python phone.py   (from inside backend/auth/)
"""

import re

# Geographic landline area codes, without the trunk 0. Checked on
# 2026-09-30 against the "Telephone numbers in Sri Lanka" article on
# Wikipedia (https://en.wikipedia.org/wiki/Telephone_numbers_in_Sri_Lanka),
# which lists exactly these 29 codes by province; the list in the
# implementation plan matched it with no change.
SL_LANDLINE_AREA_CODES = {
    "11": "Colombo",
    "21": "Jaffna",
    "23": "Mannar",
    "24": "Vavuniya",
    "25": "Anuradhapura",
    "26": "Trincomalee",
    "27": "Polonnaruwa",
    "31": "Negombo",
    "32": "Chilaw",
    "33": "Gampaha",
    "34": "Kalutara",
    "35": "Kegalle",
    "36": "Avissawella",
    "37": "Kurunegala",
    "38": "Panadura",
    "41": "Matara",
    "45": "Ratnapura",
    "47": "Hambantota",
    "51": "Hatton",
    "52": "Nuwara Eliya",
    "54": "Nawalapitiya",
    "55": "Badulla",
    "57": "Bandarawela",
    "63": "Ampara",
    "65": "Batticaloa",
    "66": "Matale",
    "67": "Kalmunai",
    "81": "Kandy",
    "91": "Galle",
}

_SUBSCRIBER_DIGITS = 7
# Separators people commonly type between digit groups.
_SEPARATORS = re.compile(r"[\s\-.()/]")
_DIGITS = re.compile(r"[0-9]+")


class InvalidPhoneNumber(ValueError):
    """The text is not a Sri Lankan landline number. The message is meant
    to be shown to the person who typed it."""


def normalize_sl_landline(raw: str) -> str:
    """Returns the number as E.164, for example "+94112345678".

    Raises InvalidPhoneNumber with a human readable reason otherwise.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidPhoneNumber("Please enter your landline number.")

    compact = _SEPARATORS.sub("", raw.strip())
    international = True
    if compact.startswith("+"):
        if not compact.startswith("+94"):
            raise InvalidPhoneNumber("Only Sri Lankan numbers (+94) can be used.")
        national = compact[3:]
    elif compact.startswith("0094"):
        national = compact[4:]
    elif compact.startswith("94") and len(compact) == 2 + 2 + _SUBSCRIBER_DIGITS:
        # 94 without the plus, e.g. copied from a form that drops it.
        national = compact[2:]
    elif compact.startswith("0"):
        national = compact[1:]
        international = False
    else:
        raise InvalidPhoneNumber("Enter the number with its area code, for example 011 234 5678.")

    # "+94 (0)11 ..." is a common way to write it; drop that stray trunk 0.
    # Only after a country code: in a national number a second leading 0
    # is simply a typing mistake and is reported as such below.
    if international and national.startswith("0"):
        national = national[1:]

    # ASCII digits only: str.isdigit() would also accept digits from other
    # scripts, which the rest of the system does not expect.
    if not _DIGITS.fullmatch(national):
        raise InvalidPhoneNumber("A phone number can only contain digits, spaces, dashes and a leading +.")

    if national.startswith("7"):
        raise InvalidPhoneNumber(
            "That looks like a mobile number. Please enter your SLT landline number "
            "(it starts with an area code such as 011)."
        )

    area_code = national[:2]
    if area_code not in SL_LANDLINE_AREA_CODES:
        raise InvalidPhoneNumber(f"0{area_code} is not a Sri Lankan landline area code.")

    subscriber = national[2:]
    if len(subscriber) != _SUBSCRIBER_DIGITS:
        raise InvalidPhoneNumber(
            "A landline number has 10 digits including the area code, for example 011 234 5678."
        )

    return f"+94{national}"


def format_for_display(phone_e164: str) -> str:
    """"+94112345678" -> "011 234 5678", for printing in scripts."""
    national = phone_e164[3:]
    return f"0{national[:2]} {national[2:5]} {national[5:]}"


if __name__ == "__main__":
    cases = [
        "011 2345678",
        "0112345678",
        "+94 11 2345678",
        "+94112345678",
        "94112345678",
        "0094 81 222 3344",
        "(081) 222-3344",
        "091.222.3344",
        "+94 (0)11 234 5678",
        "077 1234567",          # mobile
        "+94 71 1234567",       # mobile
        "012 3456789",          # not an area code
        "011 234567",           # too short
        "011 23456789",         # too long
        "+44 20 7946 0000",     # not Sri Lanka
        "011-ABC-DEFG",         # letters
        "00112345678",          # stray extra 0
        "١١ 2345678",  # non ASCII digits
        "",
    ]
    for case in cases:
        try:
            result = normalize_sl_landline(case)
            print(f"  OK   {ascii(case):24} -> {result}  ({SL_LANDLINE_AREA_CODES[result[3:5]]})")
        except InvalidPhoneNumber as exc:
            print(f"  NO   {ascii(case):24} -> {exc}")
