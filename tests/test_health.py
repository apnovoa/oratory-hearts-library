"""Tests for /ping and /health endpoints."""


def test_ping_returns_200(client):
    rv = client.get("/ping")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["status"] == "ok"


def test_ping_content_type_is_json(client):
    rv = client.get("/ping")
    assert rv.content_type.startswith("application/json")


def test_health_database_ok(client):
    rv = client.get("/health")
    data = rv.get_json()
    assert data["database"]["status"] == "ok"


def test_health_scheduler_disabled(client):
    rv = client.get("/health")
    data = rv.get_json()
    assert data["scheduler"]["running"] is False
    assert data["scheduler"]["reason"] == "disabled"


def test_health_database_error_is_sanitized(client, monkeypatch):
    from app.models import db

    def _raise_db_error(*args, **kwargs):
        raise RuntimeError("sqlite:///private/path/db.sqlite is unreachable")

    monkeypatch.setattr(db.session, "execute", _raise_db_error)

    rv = client.get("/health")
    data = rv.get_json()

    assert rv.status_code == 503
    assert data["database"]["status"] == "error"
    assert data["database"]["error"] == "unavailable"
