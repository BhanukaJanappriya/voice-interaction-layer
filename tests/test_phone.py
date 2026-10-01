"""auth/phone.py: Sri Lankan landline normalization to E.164."""

import pytest

from conftest import require_module


@pytest.fixture(scope="module")
def phone():
    return require_module("auth.phone")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("011 234 5678", "+94112345678"),
        ("0112345678", "+94112345678"),
        ("011-234-5678", "+94112345678"),
        ("+94 11 234 5678", "+94112345678"),
        ("+94112345678", "+94112345678"),
        ("0094 11 234 5678", "+94112345678"),
        ("0094112345678", "+94112345678"),
        ("  011 2345678  ", "+94112345678"),
        ("081 222 3333", "+94812223333"),
        ("091-2345678", "+94912345678"),
        ("021 222 1234", "+94212221234"),
        ("025 222 1234", "+94252221234"),
        ("033 222 1234", "+94332221234"),
        ("038 222 1234", "+94382221234"),
        ("041 222 1234", "+94412221234"),
        ("066 222 1234", "+94662221234"),
        ("063 222 1234", "+94632221234"),
    ],
)
def test_valid_forms_normalize_to_e164(phone, raw, expected):
    assert phone.normalize_sl_landline(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "012 345 6789",  # 012 is not an area code
        "022 222 1234",
        "028 222 1234",
        "039 222 1234",
        "099 123 4567",
        "069 222 1234",
        "+94 12 345 6789",
    ],
)
def test_unknown_area_codes_refused(phone, raw):
    with pytest.raises(phone.InvalidPhoneNumber):
        phone.normalize_sl_landline(raw)


@pytest.mark.parametrize(
    "raw",
    ["077 123 4567", "0712345678", "+94 77 123 4567", "0094761234567", "070 123 4567", "078-1234567"],
)
def test_mobile_numbers_refused(phone, raw):
    with pytest.raises(phone.InvalidPhoneNumber):
        phone.normalize_sl_landline(raw)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "abc", "011 234", "011 234 56789", "+1 212 555 0100", "011 234 567a", "+94 11 234 567"],
)
def test_malformed_refused(phone, raw):
    with pytest.raises(phone.InvalidPhoneNumber):
        phone.normalize_sl_landline(raw)


def test_invalid_phone_number_is_an_exception(phone):
    assert issubclass(phone.InvalidPhoneNumber, Exception)
