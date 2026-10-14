"""Table session_ratings (revision 0004). Registered on Base.metadata through
db/models.py, the same way as the verification tables."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionRatingRow(Base):
    __tablename__ = "session_ratings"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="rating_range"),
        CheckConstraint("channel IN ('chat', 'voice')", name="channel_values"),
        CheckConstraint("language IN ('en', 'si', 'ta')", name="language_values"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    channel: Mapped[str] = mapped_column(String(8))
    language: Mapped[str] = mapped_column(String(2), default="en", server_default="en")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    turns: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
