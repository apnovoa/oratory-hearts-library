import threading
import time
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler


def init_scheduler(app):
    scheduler = BackgroundScheduler()
    expiry_interval_minutes = max(1, int(app.config.get("SCHEDULER_EXPIRY_INTERVAL_MINUTES", 5)))
    reminder_interval_minutes = max(1, int(app.config.get("SCHEDULER_REMINDER_INTERVAL_MINUTES", 60)))
    state_lock = threading.Lock()
    app.scheduler_state_lock = state_lock
    app.scheduler_state = {"updated_at": None, "jobs": {}}

    def _record_job_result(job_id, *, status, duration_ms, error=None):
        now = datetime.now(UTC).isoformat()
        with state_lock:
            jobs = app.scheduler_state.setdefault("jobs", {})
            entry = jobs.setdefault(job_id, {"consecutive_failures": 0})
            entry["last_status"] = status
            entry["last_run_at"] = now
            entry["last_duration_ms"] = round(duration_ms, 2)
            if status == "ok":
                entry["last_success_at"] = now
                entry["last_error"] = None
                entry["consecutive_failures"] = 0
            else:
                entry["last_error_at"] = now
                entry["last_error"] = (error or "unknown")[:500]
                entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
            app.scheduler_state["updated_at"] = now

    def _run_job(job_id, fn, *, success_log_message):
        started = time.perf_counter()
        try:
            fn()
        except Exception:
            # Keep a broad boundary here: scheduler jobs call multiple subsystems
            # and must never crash the scheduler thread.
            app.logger.exception("Scheduler job %s crashed.", job_id)
            _record_job_result(
                job_id,
                status="error",
                duration_ms=(time.perf_counter() - started) * 1000,
                error="Unhandled exception",
            )
            return

        duration_ms = (time.perf_counter() - started) * 1000
        _record_job_result(job_id, status="ok", duration_ms=duration_ms)
        app.logger.info(success_log_message, duration_ms)

    def run_expiry():
        with app.app_context():
            from .service import expire_loans

            _run_job(
                "expire_loans",
                expire_loans,
                success_log_message="Scheduler job expire_loans completed in %.2f ms.",
            )

    def run_reminders():
        with app.app_context():
            from .service import send_reminders

            _run_job(
                "send_reminders",
                send_reminders,
                success_log_message="Scheduler job send_reminders completed in %.2f ms.",
            )

    scheduler.add_job(
        func=run_expiry,
        trigger="interval",
        minutes=expiry_interval_minutes,
        id="expire_loans",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        func=run_reminders,
        trigger="interval",
        minutes=reminder_interval_minutes,
        id="send_reminders",
        max_instances=1,
        coalesce=True,
    )

    def run_new_acquisitions_digest():
        with app.app_context():
            from ..email_service import send_new_acquisitions_digest

            _run_job(
                "new_acquisitions_digest",
                send_new_acquisitions_digest,
                success_log_message="Scheduler job new_acquisitions_digest completed in %.2f ms.",
            )

    scheduler.add_job(
        func=run_new_acquisitions_digest,
        trigger="cron",
        day_of_week="mon",
        hour=8,
        minute=0,
        id="new_acquisitions_digest",
        max_instances=1,
        coalesce=True,
    )

    def run_birthday_greetings():
        with app.app_context():
            from ..email_service import send_birthday_greetings

            _run_job(
                "birthday_greetings",
                send_birthday_greetings,
                success_log_message="Scheduler job birthday_greetings completed in %.2f ms.",
            )

    scheduler.add_job(
        func=run_birthday_greetings,
        trigger="cron",
        hour=7,
        minute=0,
        id="birthday_greetings",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.scheduler = scheduler
