"""Tests for /ping and /health endpoints."""


def test_sqlite_foreign_keys_enabled(app):
    from app.models import db

    with app.app_context():
        pragma = db.session.execute(db.text("PRAGMA foreign_keys")).scalar()
        assert pragma == 1


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


def test_health_scheduler_failure_threshold_marks_degraded(app, client, monkeypatch):
    class _DummyJob:
        id = "expire_loans"
        next_run_time = None

    class _DummyScheduler:
        running = True

        @staticmethod
        def get_jobs():
            return [_DummyJob()]

    app.config["SCHEDULER_MAX_CONSECUTIVE_FAILURES"] = 3
    monkeypatch.setattr(app, "scheduler", _DummyScheduler(), raising=False)
    monkeypatch.setattr(app, "scheduler_state_lock", None, raising=False)
    monkeypatch.setattr(
        app,
        "scheduler_state",
        {
            "updated_at": "2026-02-10T00:00:00+00:00",
            "jobs": {
                "expire_loans": {
                    "last_status": "error",
                    "last_run_at": "2026-02-10T00:00:00+00:00",
                    "last_error": "boom",
                    "consecutive_failures": 3,
                }
            },
        },
        raising=False,
    )

    rv = client.get("/health")
    data = rv.get_json()
    assert rv.status_code == 503
    assert data["status"] == "degraded"
    assert data["scheduler"]["running"] is True
    assert "expire_loans" in data["scheduler"]["failing_jobs"]


def test_health_scheduler_below_failure_threshold_is_ok(app, client, monkeypatch):
    class _DummyJob:
        id = "send_reminders"
        next_run_time = None

    class _DummyScheduler:
        running = True

        @staticmethod
        def get_jobs():
            return [_DummyJob()]

    app.config["SCHEDULER_MAX_CONSECUTIVE_FAILURES"] = 3
    monkeypatch.setattr(app, "scheduler", _DummyScheduler(), raising=False)
    monkeypatch.setattr(app, "scheduler_state_lock", None, raising=False)
    monkeypatch.setattr(
        app,
        "scheduler_state",
        {
            "updated_at": "2026-02-10T00:00:00+00:00",
            "jobs": {
                "send_reminders": {
                    "last_status": "error",
                    "last_run_at": "2026-02-10T00:00:00+00:00",
                    "last_error": "temporary timeout",
                    "consecutive_failures": 2,
                }
            },
        },
        raising=False,
    )

    rv = client.get("/health")
    data = rv.get_json()
    assert rv.status_code == 200
    assert data["status"] == "ok"
    assert data["scheduler"]["failing_jobs"] == []


def test_health_scheduler_probe_failure_is_sanitized(app, client, monkeypatch):
    class _BrokenScheduler:
        @property
        def running(self):
            raise RuntimeError("scheduler probe crash")

    monkeypatch.setattr(app, "scheduler", _BrokenScheduler(), raising=False)

    rv = client.get("/health")
    data = rv.get_json()
    assert rv.status_code == 503
    assert data["status"] == "degraded"
    assert data["scheduler"]["reason"] == "probe_failed"
