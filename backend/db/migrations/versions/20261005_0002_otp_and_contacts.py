"""One time code sign in: contact columns on users and the otp_challenges table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

users.mobile_e164 and users.email are where a customer's codes are sent;
both nullable, so every existing row stays valid (a customer without
contact details is treated like an unknown number by the OTP flow).

otp_challenges holds one row per requested code. It stores no phone number,
no address, no destination and no code, only keyed hashes (see
backend/otp/hashing.py). user_id is NULL for decoy challenges, which exist
so that unknown numbers behave exactly like customers (ADR 2.7). Beyond the
columns named in the plan (id, user_id, code_hash, destination_hash,
channel, attempts, expires_at, consumed_at, created_at, client_ip_hash) it
has phone_hash, the keyed hash of the landline, used for the resend
cooldown and the per number hourly limit, and hub_ref, a Fernet sealed
blob (landline and hub session id) that only the hub OTP backend fills.

SQLite cannot add a constraint in place, so users is altered in batch mode
(env.py turns render_as_batch on for SQLite); the two new columns are plain
nullable ones, which is also valid as an ordinary ALTER on PostgreSQL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("mobile_e164", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("email", sa.String(length=254), nullable=True))

    op.create_table(
        "otp_challenges",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("destination_hash", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=False),
        sa.Column("hub_ref", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_otp_challenges_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_challenges")),
    )
    op.create_index(op.f("ix_otp_challenges_phone_hash"), "otp_challenges", ["phone_hash"], unique=False)
    op.create_index(op.f("ix_otp_challenges_client_ip_hash"), "otp_challenges", ["client_ip_hash"], unique=False)
    op.create_index(op.f("ix_otp_challenges_created_at"), "otp_challenges", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_otp_challenges_created_at"), table_name="otp_challenges")
    op.drop_index(op.f("ix_otp_challenges_client_ip_hash"), table_name="otp_challenges")
    op.drop_index(op.f("ix_otp_challenges_phone_hash"), table_name="otp_challenges")
    op.drop_table("otp_challenges")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email")
        batch.drop_column("mobile_e164")
