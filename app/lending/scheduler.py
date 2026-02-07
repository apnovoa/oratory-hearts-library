from apscheduler.schedulers.background import BackgroundScheduler


def init_scheduler(app):
    scheduler = BackgroundScheduler()

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
        minutes=5,
        id="expire_loans",
    )
    scheduler.add_job(
        func=run_reminders,
        trigger="interval",
        minutes=60,
        id="send_reminders",
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
    )
    scheduler.start()
