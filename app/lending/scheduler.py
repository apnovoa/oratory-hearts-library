from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler


def init_scheduler(app):
    scheduler = BackgroundScheduler()
    expiry_interval_minutes = max(1, int(app.config.get("SCHEDULER_EXPIRY_INTERVAL_MINUTES", 5)))
    reminder_interval_minutes = max(1, int(app.config.get("SCHEDULER_REMINDER_INTERVAL_MINUTES", 60)))

    def job_listener(event):
        if event.exception:
            app.logger.error("Scheduler job %s failed: %s", event.job_id, event.exception)
        else:
            app.logger.debug("Scheduler job %s executed successfully", event.job_id)

    scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    def run_expiry():
        with app.app_context():
            from .service import expire_loans

            expire_loans()

    def run_reminders():
        with app.app_context():
            from .service import send_reminders

            send_reminders()

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

            send_new_acquisitions_digest()

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

            send_birthday_greetings()

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
