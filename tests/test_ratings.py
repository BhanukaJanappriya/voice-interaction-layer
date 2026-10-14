"""Five star session rating: POST /feedback/rating, the summary, the admin
endpoint and migration 0004. A small FastAPI app mounts the router."""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from conftest import make_client, require_path


@pytest.fixture(autouse=True)
def _wipe(db_schema):
    from feedback import routes
    from feedback.models import SessionRatingRow

    def wipe():
        with db_schema.engine.begin() as conn:
            conn.execute(sa.delete(SessionRatingRow.__table__))
        routes.reset_limiter()

    wipe()
    yield
    wipe()


@pytest.fixture
def table(db_schema):
    from feedback.models import SessionRatingRow

    return SessionRatingRow.__table__


@pytest.fixture
def rows(db_schema, table):
    def read():
        with db_schema.engine.begin() as conn:
            return [dict(r._mapping) for r in conn.execute(sa.select(table).order_by(table.c.id))]

    return read


@pytest.fixture
def app_client():
    from feedback import routes

    app = FastAPI()
    app.include_router(routes.router)
    c = make_client(app)
    yield c
    c.close()


def _post(c, **body):
    payload = {"rating": 5, "channel": "chat"}
    payload.update(body)
    return c.post("/feedback/rating", json=payload)


@pytest.mark.parametrize("stars", [1, 2, 3, 4, 5])
def test_each_rating_is_stored(app_client, rows, stars):
    r = _post(app_client, rating=stars, channel="voice", language="si", turns=7)
    assert r.status_code == 201 and r.json() == {"ok": True}
    assert r.headers["cache-control"] == "no-store"
    (row,) = rows()
    assert (row["rating"], row["channel"], row["language"], row["turns"]) == (stars, "voice", "si", 7)
    assert row["verified"] in (False, 0)
    assert row["ip_hash"] and "127.0.0.1" not in row["ip_hash"]


@pytest.mark.parametrize(
    "body",
    [
        {"rating": 0, "channel": "chat"},
        {"rating": 6, "channel": "chat"},
        {"rating": 3.5, "channel": "chat"},
        {"rating": "5", "channel": "chat"},
        {"rating": True, "channel": "chat"},
        {"channel": "chat"},
        {"rating": 5, "channel": "chat", "comment": "hello"},
        {"rating": 5, "channel": "email"},
        {"rating": 5},
        {"rating": 5, "channel": "chat", "turns": "x"},
    ],
)
def test_invalid_bodies_are_422(app_client, rows, body):
    assert app_client.post("/feedback/rating", json=body).status_code == 422
    assert rows() == []


def test_turns_clamped_and_language_defaults(app_client, rows):
    _post(app_client, turns=99999, language="xx")
    _post(app_client, turns=-4)
    first, second = rows()
    assert first["turns"] == 1000 and first["language"] == "en"
    assert second["turns"] == 0


def test_verified_only_with_real_verified_session(app_client, rows, make_user):
    from auth.tokens import AUDIENCE_USER, issue_token

    user_id = make_user(phone_e164="+94112000044")
    _post(app_client)  # guest
    app_client.cookies.set("user_session", "forged.token.value")
    _post(app_client)
    plain, _ = issue_token(str(user_id), AUDIENCE_USER, 5)  # no otp claim
    app_client.cookies.set("user_session", plain)
    _post(app_client)
    token, _ = issue_token(str(user_id), AUDIENCE_USER, 5, extra={"amr": ["otp"]})
    app_client.cookies.set("user_session", token)
    _post(app_client)
    assert [bool(r["verified"]) for r in rows()] == [False, False, False, True]


def test_body_cannot_set_verified(app_client):
    assert _post(app_client, verified=True).status_code == 422


def test_rate_limit(app_client, monkeypatch):
    import config

    monkeypatch.setattr(config, "RATING_RATE_LIMIT_PER_HOUR", 2)
    assert _post(app_client).status_code == 201
    assert _post(app_client).status_code == 201
    r = _post(app_client)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["cache-control"] == "no-store"


def test_disabled_is_404(app_client, rows, monkeypatch):
    import config

    monkeypatch.setattr(config, "RATING_ENABLED", False)
    assert _post(app_client).status_code == 404
    assert rows() == []
    assert app_client.get("/feedback/config").json() == {"enabled": False}


def test_config_enabled(app_client):
    r = app_client.get("/feedback/config")
    assert r.status_code == 200 and r.json() == {"enabled": True}


def test_database_trouble_is_503(app_client, monkeypatch, capsys):
    from feedback import service

    def boom(*a, **k):
        raise sa.exc.OperationalError("INSERT secret-detail", {}, Exception("x"))

    monkeypatch.setattr(service, "record", boom)
    r = _post(app_client)
    assert r.status_code == 503 and r.json() == {"code": "unavailable"}
    assert "Traceback" not in r.text and "secret-detail" not in r.text
    assert "secret-detail" not in capsys.readouterr().out


def test_summary_math(db_schema, table):
    from feedback import service

    empty = service.summary()
    assert empty["count"] == 0 and empty["average"] is None
    assert empty["distribution"] == {str(n): 0 for n in range(1, 6)}
    assert empty["by_channel"]["chat"] == {"count": 0, "average": None}

    service.record(5, "chat", "en", True, 3, "10.0.0.1")
    service.record(4, "chat", "si", False, None, "10.0.0.1")
    service.record(2, "voice", "ta", True, 9, "10.0.0.2")
    # an old row falls outside the 30 day window
    with db_schema.engine.begin() as conn:
        conn.execute(
            sa.insert(table).values(
                created_at=datetime.now(timezone.utc) - timedelta(days=60),
                rating=1, channel="chat", language="en", verified=False, turns=None, ip_hash="x",
            )
        )
    s = service.summary(30)
    assert s["count"] == 3
    assert s["average"] == 3.7
    assert s["distribution"] == {"1": 0, "2": 1, "3": 0, "4": 1, "5": 1}
    assert s["by_channel"] == {"chat": {"count": 2, "average": 4.5}, "voice": {"count": 1, "average": 2.0}}
    assert s["verified_count"] == 2
    assert len(s["recent"]) == 3
    assert set(s["recent"][0]) == {"created_at", "rating", "channel", "language", "verified"}
    assert s["recent"][0]["rating"] == 2  # newest first
    assert "10.0.0" not in str(s)
    assert service.summary(365)["count"] == 4


def test_recent_is_limited_to_20(db_schema):
    from feedback import service

    for _ in range(25):
        service.record(3, "chat", "en", False, None, "10.0.0.9")
    assert len(service.summary()["recent"]) == 20


def test_admin_endpoint(api_app, admin_client, new_client):
    require_path(api_app, "/admin/api/ratings")
    from feedback import service

    service.record(4, "chat", "en", False, 2, "10.0.0.1")
    assert new_client().get("/admin/api/ratings").status_code in (401, 403)
    r = admin_client.get("/admin/api/ratings")
    assert r.status_code == 200
    assert r.json()["count"] == 1 and r.json()["average"] == 4.0
    assert admin_client.get("/admin/api/ratings?days=0").status_code == 422
    assert admin_client.get("/admin/api/ratings?days=366").status_code == 422
    assert admin_client.get("/admin/api/ratings?days=7").status_code == 200


def test_migration_up_and_down(tmp_path):
    from alembic import command

    from db import migrate

    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    cfg = migrate._alembic_config()
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0003")
        connection.commit()
        assert "session_ratings" not in sa.inspect(connection).get_table_names()
        command.upgrade(cfg, "0004")
        connection.commit()
        inspector = sa.inspect(connection)
        assert "session_ratings" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("session_ratings")}
        assert columns == {"id", "created_at", "rating", "channel", "language", "verified", "turns", "ip_hash"}
        assert any(i["column_names"] == ["created_at"] for i in inspector.get_indexes("session_ratings"))
        insert = "INSERT INTO session_ratings (created_at, rating, channel, language, verified) VALUES ('2026-10-14', {}, '{}', 'en', 0)"
        connection.execute(sa.text(insert.format(5, "chat")))
        for bad in ((0, "chat"), (6, "chat"), (3, "email")):
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(sa.text(insert.format(*bad)))
            connection.rollback()
        command.downgrade(cfg, "0003")
        connection.commit()
        assert "session_ratings" not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_nothing_printed_by_a_rating(app_client, capsys):
    _post(app_client, turns=3, language="ta")
    out = capsys.readouterr()
    assert "127.0.0.1" not in out.out + out.err
    assert "testclient" not in (out.out + out.err).lower()
