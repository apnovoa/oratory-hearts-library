"""Tests for scheduler initialization and in-memory job telemetry."""

from datetime import UTC, datetime

import pytest


class _FakeJob:
    def __init__(self, job_id):
        self.id = job_id
        self.next_run_time = datetime.now(UTC)


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.running = False

    def add_job(self, func, trigger, id, **kwargs):
        self.jobs[id] = {
            "func": func,
            "trigger": trigger,
            "id": id,
            "kwargs": kwargs,
        }

    def start(self):
        self.running = True

    def get_jobs(self):
        return [_FakeJob(job_id) for job_id in self.jobs]


@pytest.fixture(autouse=True)
def _restore_scheduler_attrs(app):
    had_scheduler = hasattr(app, "scheduler")
    original_scheduler = getattr(app, "scheduler", None)
    had_state = hasattr(app, "scheduler_state")
    original_state = getattr(app, "scheduler_state", None)
    had_state_lock = hasattr(app, "scheduler_state_lock")
    original_state_lock = getattr(app, "scheduler_state_lock", None)
    yield
    if had_scheduler:
        app.scheduler = original_scheduler
    elif hasattr(app, "scheduler"):
        delattr(app, "scheduler")
    if had_state:
        app.scheduler_state = original_state
    elif hasattr(app, "scheduler_state"):
        delattr(app, "scheduler_state")
    if had_state_lock:
        app.scheduler_state_lock = original_state_lock
    elif hasattr(app, "scheduler_state_lock"):
        delattr(app, "scheduler_state_lock")


def test_init_scheduler_registers_expected_jobs(app, monkeypatch):
    from app.lending import scheduler as scheduler_module

    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", lambda: fake_scheduler)

    scheduler_module.init_scheduler(app)

    assert app.scheduler is fake_scheduler
    assert app.scheduler.running is True
    assert set(fake_scheduler.jobs) == {
        "expire_loans",
        "send_reminders",
        "new_acquisitions_digest",
        "birthday_greetings",
    }
    for job_data in fake_scheduler.jobs.values():
        kwargs = job_data["kwargs"]
        assert kwargs["max_instances"] == 1
        assert kwargs["coalesce"] is True


def test_scheduler_job_success_updates_state(app, monkeypatch):
    from app.lending import scheduler as scheduler_module

    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", lambda: fake_scheduler)
    monkeypatch.setattr("app.lending.service.expire_loans", lambda: None)

    scheduler_module.init_scheduler(app)
    fake_scheduler.jobs["expire_loans"]["func"]()

    state = app.scheduler_state["jobs"]["expire_loans"]
    assert state["last_status"] == "ok"
    assert state["consecutive_failures"] == 0
    assert state["last_success_at"] is not None
    assert isinstance(state["last_duration_ms"], float)


def test_scheduler_job_failure_increments_counter(app, monkeypatch):
    from app.lending import scheduler as scheduler_module

    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", lambda: fake_scheduler)

    def _boom():
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("app.lending.service.send_reminders", _boom)

    scheduler_module.init_scheduler(app)
    fake_scheduler.jobs["send_reminders"]["func"]()
    fake_scheduler.jobs["send_reminders"]["func"]()

    state = app.scheduler_state["jobs"]["send_reminders"]
    assert state["last_status"] == "error"
    assert state["consecutive_failures"] == 2
    assert state["last_error"] == "Unhandled exception"
