"""Inserts clearly FAKE customers for trying the wizard:

    cd backend
    python -m verification.seed_demo

Every row is invented (landlines 0112000001 to 0112000003, names starting
with "Test", mobiles and emails on the example.invalid domain or the
0770000xxx test range). Nothing here is a real person. Replace the table
contents with the real directory before any real use; until then the
directory holds fake rows only (plan 3.7).

Run it against a database that has revision 0003 applied (python -m
db.migrate). For a scratch database without it, ensure_schema() creates
what is missing.
"""

from db.base import engine
from verification.lookup import CustomerDirectory
from verification.models import ensure_schema

DEMO_ROWS = [
    # landline, display name, mobile, email, language
    ("+94112000001", "Test Customer", "+94770000123", "demo.customer@example.invalid", "en"),
    ("+94112000002", "Test Sinhala Customer", "+94770000456", None, "si"),
    ("+94112000003", "Test Email Only", None, "email.only@example.invalid", "ta"),
]


def seed() -> int:
    ensure_schema(engine)
    directory = CustomerDirectory()
    for phone, name, mobile, email, language in DEMO_ROWS:
        directory.upsert(phone, name, mobile, email, language)
    return len(DEMO_ROWS)


if __name__ == "__main__":
    count = seed()
    print(f"Seeded {count} FAKE directory rows (0112000001 'Test Customer' and two more).")
