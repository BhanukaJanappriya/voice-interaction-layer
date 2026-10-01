"""Demo data: brings the schema up to date, then adds a few fictional
customers so the "Existing user" sign in can be tried straight away.

    python -m backend.db.seed        (from the repository root)

Idempotent: a number that is already registered is left exactly as it is
(its PIN is not reset), so running this twice changes nothing.

DEMO ONLY. The names are invented, the numbers are only chosen to be in a
valid landline format, and the PINs are printed below on purpose so a
tester can use them. Never run this against a database with real
customers; register real users with backend/auth/manage_users.py, which
takes a PIN the customer chose.
"""

from auth.phone import format_for_display, normalize_sl_landline
from auth.users import UserAlreadyExists, create_user
from db.migrate import upgrade_to_head

DEMO_USERS = [
    # (phone, display name, demo PIN, preferred language)
    ("011 234 5678", "Nimal Perera", "1234", "en"),
    ("081 222 3344", "Kumari Jayasinghe", "2468", "si"),
    ("021 222 5566", "Selvi Tharmalingam", "1357", "ta"),
]


def seed_demo_users() -> list[tuple[str, str, str, str]]:
    """Returns (E.164 phone, name, demo PIN, "added" | "already present")."""
    results = []
    for phone, name, pin, language in DEMO_USERS:
        try:
            user = create_user(phone, name, pin, preferred_language=language)
            results.append((user.phone_e164, name, pin, "added"))
        except UserAlreadyExists:
            results.append((normalize_sl_landline(phone), name, pin, "already present"))
    return results


if __name__ == "__main__":
    upgrade_to_head()
    print("\nDemo users (fictional, for trying the sign in only; PINs shown on purpose):")
    for phone_e164, name, pin, state in seed_demo_users():
        print(f"  {format_for_display(phone_e164):14} {phone_e164:14} PIN {pin:6} {name:20} {state}")
    print("\nIf a demo number was already present with another PIN, reset it with:")
    print("  python -m backend.auth.manage_users set-pin --phone \"011 234 5678\" --pin 1234")
