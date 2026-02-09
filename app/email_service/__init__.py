import time

from flask import current_app, render_template

# Seconds to pause between emails in bulk sends to avoid rate limits
_BULK_SEND_DELAY = 0.2


def _send_email(subject, recipient, html_body):
    brevo_key = current_app.config.get("BREVO_API_KEY")
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")
    sender_name = current_app.config.get("MAIL_DEFAULT_SENDER_NAME")
    if not brevo_key:
        current_app.logger.debug("Email skipped (BREVO_API_KEY not configured): %s", subject)
        return
    try:
        import httpx
        resp = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": brevo_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": {"name": sender_name, "email": sender},
                "to": [{"email": recipient}],
                "subject": subject,
                "htmlContent": html_body,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        current_app.logger.error(f"Failed to send email to {recipient}: {e}")


def send_loan_email(loan, user, book):
    domain = current_app.config["LIBRARY_DOMAIN"]
    download_url = f"{domain}/loan/{loan.access_token}/download"
    html = render_template(
        "email/loan_issued.html",
        user=user,
        book=book,
        loan=loan,
        download_url=download_url,
        library_name=current_app.config["LIBRARY_NAME_LATIN"],
        library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
    )
    _send_email(
        subject=f"Loan Issued: {book.title}",
        recipient=user.email,
        html_body=html,
    )


def send_reminder_email(loan, user, book):
    domain = current_app.config["LIBRARY_DOMAIN"]
    download_url = f"{domain}/loan/{loan.access_token}/download"
    html = render_template(
        "email/reminder.html",
        user=user,
        book=book,
        loan=loan,
        download_url=download_url,
        library_name=current_app.config["LIBRARY_NAME_LATIN"],
        library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
    )
    _send_email(
        subject=f"Reminder: {book.title} due soon",
        recipient=user.email,
        html_body=html,
    )


def send_expiration_email(loan, user, book):
    html = render_template(
        "email/expired.html",
        user=user,
        book=book,
        loan=loan,
        library_name=current_app.config["LIBRARY_NAME_LATIN"],
        library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
    )
    _send_email(
        subject=f"Loan Expired: {book.title}",
        recipient=user.email,
        html_body=html,
    )


def send_waitlist_notification(user, book):
    domain = current_app.config["LIBRARY_DOMAIN"]
    catalog_url = f"{domain}/catalog/{book.public_id}"
    html = render_template(
        "email/waitlist_available.html",
        user=user,
        book=book,
        catalog_url=catalog_url,
        library_name=current_app.config["LIBRARY_NAME_LATIN"],
        library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
    )
    _send_email(
        subject=f"Now Available: {book.title}",
        recipient=user.email,
        html_body=html,
    )


def send_password_reset_email(user, reset_url):
    html = render_template(
        "email/password_reset.html",
        user=user,
        reset_url=reset_url,
        library_name=current_app.config["LIBRARY_NAME_LATIN"],
        library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
    )
    _send_email(
        subject="Password Reset Request",
        recipient=user.email,
        html_body=html,
    )


def send_birthday_greetings():
    """Send birthday emails and prayers to patrons whose birthday is today."""
    from datetime import datetime, timezone
    from ..models import User

    today = datetime.now(timezone.utc)
    month, day = today.month, today.day

    birthday_patrons = User.query.filter_by(
        role="patron",
        is_active_account=True,
        is_blocked=False,
        birth_month=month,
        birth_day=day,
    ).all()

    if not birthday_patrons:
        current_app.logger.info("No patron birthdays today.")
        return

    sent_count = 0
    for patron in birthday_patrons:
        html = render_template(
            "email/birthday.html",
            user=patron,
            library_name=current_app.config["LIBRARY_NAME_LATIN"],
            library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
        )
        _send_email(
            subject="Happy Birthday from the Library!",
            recipient=patron.email,
            html_body=html,
        )
        sent_count += 1
        if sent_count < len(birthday_patrons):
            time.sleep(_BULK_SEND_DELAY)

    current_app.logger.info(f"Birthday greetings sent to {sent_count} patron(s).")


def send_new_acquisitions_digest():
    """Send a weekly digest of newly added books to all active patrons.

    Queries books created within the last 7 days and emails each
    active patron a summary of the new titles.
    """
    from datetime import datetime, timedelta, timezone
    from ..models import Book, User

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    new_books = (
        Book.query
        .filter(
            Book.created_at >= cutoff,
            Book.is_visible == True,   # noqa: E712
            Book.is_disabled == False,  # noqa: E712
        )
        .order_by(Book.created_at.desc())
        .all()
    )

    if not new_books:
        current_app.logger.info("No new acquisitions in the last 7 days; digest skipped.")
        return

    domain = current_app.config["LIBRARY_DOMAIN"]
    catalog_url = f"{domain}/catalog"

    active_patrons = User.query.filter_by(
        role="patron", is_active_account=True, is_blocked=False
    ).all()

    sent_count = 0
    for patron in active_patrons:
        html = render_template(
            "email/new_acquisitions.html",
            user=patron,
            books=new_books,
            catalog_url=catalog_url,
            library_name=current_app.config["LIBRARY_NAME_LATIN"],
            library_name_en=current_app.config["LIBRARY_NAME_ENGLISH"],
        )
        _send_email(
            subject="New Acquisitions This Week",
            recipient=patron.email,
            html_body=html,
        )
        sent_count += 1
        if sent_count < len(active_patrons):
            time.sleep(_BULK_SEND_DELAY)

    current_app.logger.info(
        f"New acquisitions digest sent: {len(new_books)} books, {sent_count} patrons."
    )
