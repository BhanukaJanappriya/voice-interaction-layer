"""Verification wizard: customer_directory, verification_audit and two
nullable columns on otp_challenges.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09

Place this file in backend/db/migrations/versions/ (it follows
20261005_0002_otp_and_contacts.py, revision "0002").

customer_directory holds the people the wizard may verify: landline,
normalized name, optional mobile and email the code goes to. It starts empty
(python -m verification.seed_demo adds FAKE rows for trials).

verification_audit holds one row per wizard event with keyed hashes only: no
code, no landline, no mobile, no email, no client address.

otp_challenges.purpose is "verify" for wizard codes and NULL for the older
/auth/otp flow, so the two never share a cooldown or a limit. wizard_ref
holds "<directory id>:<language>". Both nullable, so every existing row
stays valid. SQLite adds nullable columns in place; batch mode (env.py) is
used anyway to match revision 0002.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_directory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("name_normalized", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("mobile_e164", sa.String(length=16), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("preferred_language", sa.String(length=8), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_directory")),
    )
    op.create_index(op.f("ix_customer_directory_phone_e164"), "customer_directory", ["phone_e164"], unique=True)

    op.create_table(
        "verification_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.String(length=24), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_audit")),
    )
    op.create_index(op.f("ix_verification_audit_at"), "verification_audit", ["at"], unique=False)
    op.create_index(op.f("ix_verification_audit_phone_hash"), "verification_audit", ["phone_hash"], unique=False)
    op.create_index(op.f("ix_verification_audit_ip_hash"), "verification_audit", ["ip_hash"], unique=False)

    with op.batch_alter_table("otp_challenges") as batch:
        batch.add_column(sa.Column("purpose", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("wizard_ref", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("otp_challenges") as batch:
        batch.drop_column("wizard_ref")
        batch.drop_column("purpose")
    op.drop_index(op.f("ix_verification_audit_ip_hash"), table_name="verification_audit")
    op.drop_index(op.f("ix_verification_audit_phone_hash"), table_name="verification_audit")
    op.drop_index(op.f("ix_verification_audit_at"), table_name="verification_audit")
    op.drop_table("verification_audit")
    op.drop_index(op.f("ix_customer_directory_phone_e164"), table_name="customer_directory")
    op.drop_table("customer_directory")
