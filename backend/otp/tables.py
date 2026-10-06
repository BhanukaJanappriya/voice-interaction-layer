"""SQLAlchemy Core definitions of the OTP table and the users columns this
package touches.

Why Core tables here when db/models.py is the project's home for tables:
db/models.py and the migrations belong to the lead, so the OtpChallenge
ORM class and revision 0002 ship as a proposed patch (backend/otp/proposed).
These definitions describe exactly the same columns, on a private
MetaData that is never used to create tables (Alembic does that), so this
package works the moment revision 0002 is applied, whether or not the ORM
patch has been merged yet, and merging it later changes nothing here.
"""

import sqlalchemy as sa

_metadata = sa.MetaData()
_TIME = sa.DateTime(timezone=True)

challenges = sa.Table(
    "otp_challenges",
    _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    # NULL for a decoy challenge (a number that is unknown or has no contact).
    sa.Column("user_id", sa.Integer, nullable=True),
    sa.Column("phone_hash", sa.String(64), nullable=False),
    sa.Column("code_hash", sa.String(64), nullable=False),
    sa.Column("destination_hash", sa.String(64), nullable=False),
    sa.Column("channel", sa.String(8), nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("expires_at", _TIME, nullable=False),
    sa.Column("consumed_at", _TIME, nullable=True),
    sa.Column("created_at", _TIME, nullable=False),
    sa.Column("client_ip_hash", sa.String(64), nullable=False),
    # Hub backend only: Fernet sealed {phone, session id} needed to ask the
    # hub to verify. NULL for local and decoy challenges.
    sa.Column("hub_ref", sa.Text, nullable=True),
)

users = sa.table(
    "users",
    sa.column("id", sa.Integer),
    sa.column("phone_e164", sa.String),
    sa.column("display_name", sa.String),
    sa.column("pin_hash", sa.String),
    sa.column("preferred_language", sa.String),
    sa.column("mobile_e164", sa.String),
    sa.column("email", sa.String),
    sa.column("failed_attempts", sa.Integer),
    sa.column("created_at", _TIME),
    sa.column("last_login_at", _TIME),
)
