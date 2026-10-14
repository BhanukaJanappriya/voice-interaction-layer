"""Stores and summarises session ratings. Core SQL on the shared session."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy as sa

from db.base import session_scope
from feedback.models import SessionRatingRow

_T = SessionRatingRow.__table__
LANGUAGES = ("en", "si", "ta")
CHANNELS = ("chat", "voice")


def _ip_hash(ip: Optional[str]) -> str:
    from otp import hashing

    return hashing.hash_ip(ip or "unknown")


def record(rating: int, channel: str, language: str, verified: bool, turns: Optional[int], ip: Optional[str]) -> None:
    """Raises sqlalchemy errors on database trouble (the route answers 503)."""
    if turns is not None:
        turns = max(0, min(1000, int(turns)))
    language = language if language in LANGUAGES else "en"
    with session_scope() as session:
        session.execute(
            sa.insert(_T).values(
                created_at=datetime.now(timezone.utc),
                rating=int(rating),
                channel=channel,
                language=language,
                verified=bool(verified),
                turns=turns,
                ip_hash=_ip_hash(ip),
            )
        )


def _avg(total, count) -> Optional[float]:
    return round(float(total) / count, 1) if count else None


def _iso(value) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def summary(days: int = 30) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    window = _T.c.created_at >= since
    with session_scope() as session:
        rows = session.execute(
            sa.select(_T.c.channel, _T.c.rating, _T.c.verified, sa.func.count())
            .where(window)
            .group_by(_T.c.channel, _T.c.rating, _T.c.verified)
        ).all()
        recent = session.execute(
            sa.select(_T.c.created_at, _T.c.rating, _T.c.channel, _T.c.language, _T.c.verified)
            .where(window)
            .order_by(_T.c.created_at.desc(), _T.c.id.desc())
            .limit(20)
        ).all()
    distribution = {str(n): 0 for n in range(1, 6)}
    per = {c: [0, 0] for c in CHANNELS}
    count = total = verified_count = 0
    for channel, rating, verified, n in rows:
        count += n
        total += rating * n
        distribution[str(rating)] += n
        if verified:
            verified_count += n
        if channel in per:
            per[channel][0] += n
            per[channel][1] += rating * n
    return {
        "days": days,
        "count": count,
        "average": _avg(total, count),
        "distribution": distribution,
        "by_channel": {c: {"count": v[0], "average": _avg(v[1], v[0])} for c, v in per.items()},
        "verified_count": verified_count,
        "recent": [
            {"created_at": _iso(r[0]), "rating": r[1], "channel": r[2], "language": r[3], "verified": bool(r[4])}
            for r in recent
        ],
    }
