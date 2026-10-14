"""Session ratings: the five star rating asked when a session ends.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14

One row per rating: stars, channel, language, whether the session was
verified, turn count, and a keyed hash of the client address used only for
the rate limit. No name, number or free text is stored.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_ratings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("language", sa.String(length=2), server_default="en", nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("turns", sa.SmallInteger(), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name=op.f("ck_session_ratings_rating_range")),
        sa.CheckConstraint("channel IN ('chat', 'voice')", name=op.f("ck_session_ratings_channel_values")),
        sa.CheckConstraint("language IN ('en', 'si', 'ta')", name=op.f("ck_session_ratings_language_values")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_ratings")),
    )
    op.create_index(op.f("ix_session_ratings_created_at"), "session_ratings", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_session_ratings_created_at"), table_name="session_ratings")
    op.drop_table("session_ratings")
