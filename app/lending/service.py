import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import current_app
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from ..audit import log_event
from ..models import Book, Loan, User, WaitlistEntry, db

# Process-local lock for in-process contention. Cross-process contention is
# handled by a SQLite BEGIN IMMEDIATE transaction in checkout_book().
_checkout_lock = threading.Lock()


def _utcnow():
    return datetime.now(UTC)


def _begin_checkout_transaction():
    """Acquire a cross-process SQLite write lock for checkout."""
    try:
        db.session.execute(db.text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        db.session.rollback()
        if "database is locked" in str(exc).lower():
            raise ValueError("The library is busy processing another checkout. Please try again in a moment.") from None
        raise


def checkout_book(user, book):
    """Atomically check out a book to a user. Returns the new Loan on success.

    Uses an application-level lock because SQLite lacks SELECT FOR UPDATE.
    """
    with _checkout_lock:
        try:
            _begin_checkout_transaction()

            # Re-query inside the lock to get fresh state
            book = db.session.get(Book, book.id)
            if not book or not book.is_available:
                raise ValueError("This book is not available for borrowing.")

            if book.restricted_access:
                raise ValueError("This title has restricted access. Please contact a librarian.")

            if not user.can_borrow:
                raise ValueError("Your account is not eligible to borrow books.")

            # Check patron loan limit
            active_patron_loans = Loan.query.filter_by(user_id=user.id, is_active=True).count()
            max_loans = current_app.config.get("MAX_LOANS_PER_PATRON", 5)
            if active_patron_loans >= max_loans:
                raise ValueError(f"You have reached the maximum of {max_loans} active loans.")

            # Check if patron already has an active loan for this book
            existing = Loan.query.filter_by(user_id=user.id, book_id=book.id, is_active=True).first()
            if existing:
                raise ValueError("You already have an active loan for this book.")

            # Verify available copies under lock
            active_count = Loan.query.filter_by(book_id=book.id, is_active=True).count()
            if active_count >= book.owned_copies:
                raise ValueError("No copies are currently available.")

            due_date = _utcnow() + timedelta(days=book.loan_days)

            loan = Loan(
                user_id=user.id,
                book_id=book.id,
                due_at=due_date,
                max_renewals=current_app.config.get("MAX_RENEWALS", 2),
                book_title_snapshot=book.title,
                book_author_snapshot=book.formatted_authors,
            )
            db.session.add(loan)
            db.session.commit()

            # Generate circulation PDF — if this fails, roll back the loan so
            # the patron is not charged a loan slot with no downloadable file.
            try:
                from ..pdf_service import generate_circulation_copy

                filename = generate_circulation_copy(loan, book, user)
                loan.circulation_filename = filename
                db.session.commit()
            except Exception as exc:
                # Keep broad rollback boundary here: any PDF-generation failure
                # must release the checked-out slot and remove the loan row.
                current_app.logger.error(f"PDF generation failed for loan {loan.public_id}, rolling back loan: {exc}")
                # Delete the loan record so the copy is released back
                db.session.delete(loan)
                db.session.commit()
                raise ValueError(
                    "Unable to prepare your copy at this time. Please try again later or contact the librarian."
                ) from None

            log_event(
                action="book_checkout",
                target_type="loan",
                target_id=loan.id,
                detail=f"Checked out '{book.title}' (loan {loan.public_id[:8]}), "
                f"due {due_date.strftime('%Y-%m-%d %H:%M UTC')}",
                user_id=user.id,
            )

            # Remove fulfilled waitlist entry if present
            waitlist_entry = WaitlistEntry.query.filter_by(user_id=user.id, book_id=book.id, is_fulfilled=False).first()
            if waitlist_entry:
                waitlist_entry.is_fulfilled = True
                db.session.commit()

            return loan
        except ValueError:
            db.session.rollback()
            raise


def renew_loan(loan):
    """Extend a loan's due date if renewals remain.

    Raises ValueError if the loan cannot be renewed.
    """
    if not loan.is_active:
        raise ValueError("This loan is no longer active and cannot be renewed.")

    if loan.invalidated:
        raise ValueError("This loan has been invalidated and cannot be renewed.")

    if loan.is_expired:
        raise ValueError("This loan has expired and cannot be renewed.")

    if loan.renewal_count >= loan.max_renewals:
        raise ValueError(f"This loan has already been renewed the maximum of {loan.max_renewals} time(s).")

    # Determine how many days to extend
    book = db.session.get(Book, loan.book_id)
    if book:
        extension_days = book.loan_days
    else:
        from flask import current_app as _app

        extension_days = _app.config.get("DEFAULT_LOAN_DAYS", 14)

    # Extend from the current due date (not from now)
    loan.due_at = loan.due_at + timedelta(days=extension_days)
    loan.renewal_count += 1

    # Reset reminder flag so a new reminder can be sent for the extended period
    loan.reminder_sent = False

    db.session.commit()

    log_event(
        action="loan_renewed",
        target_type="loan",
        target_id=loan.id,
        detail=(
            f"Renewed loan {loan.public_id[:8]} for '{loan.book_title_snapshot}' "
            f"(renewal {loan.renewal_count}/{loan.max_renewals}, "
            f"new due date {loan.due_at.strftime('%Y-%m-%d %H:%M UTC')})"
        ),
        user_id=loan.user_id,
    )

    return loan


def return_loan(loan):
    """Mark a loan as returned and release the copy back into inventory."""
    if not loan.is_active:
        raise ValueError("This loan is already returned or expired.")

    loan.is_active = False
    loan.returned_at = _utcnow()
    db.session.commit()

    log_event(
        action="book_return",
        target_type="loan",
        target_id=loan.id,
        detail=f"Returned '{loan.book_title_snapshot}' (loan {loan.public_id[:8]})",
        user_id=loan.user_id,
    )

    # Clean up circulation file
    _delete_circulation_file(loan)

    # Process waitlist for the returned book
    process_waitlist(loan.book)


def expire_loans():
    """Find and expire overdue loans with per-loan transactional isolation."""
    now = _utcnow()
    overdue = Loan.query.filter(
        Loan.is_active == True,
        Loan.due_at <= now,
    ).all()

    expired_count = 0
    failed_count = 0

    for loan in overdue:
        try:
            with db.session.begin_nested():
                loan.is_active = False
                loan.returned_at = now

                log_event(
                    action="loan_expired",
                    target_type="loan",
                    target_id=loan.id,
                    detail=f"Auto-expired loan {loan.public_id[:8]} for '{loan.book_title_snapshot}'",
                    user_id=loan.user_id,
                )

                # Send expiration notice (best-effort; expiry should still persist)
                if not loan.expiration_notice_sent:
                    try:
                        from ..email_service import send_expiration_email

                        user = db.session.get(User, loan.user_id)
                        book = db.session.get(Book, loan.book_id)
                        if user and book:
                            sent = send_expiration_email(loan, user, book)
                            if sent:
                                loan.expiration_notice_sent = True
                            else:
                                current_app.logger.warning(
                                    "Expiration email send returned false for loan %s",
                                    loan.public_id,
                                )
                    except (RuntimeError, OSError, ValueError):
                        current_app.logger.exception("Failed to send expiration email for loan %s", loan.public_id)

                book = db.session.get(Book, loan.book_id)
                if book:
                    process_waitlist(book, commit=False)

            db.session.commit()
            _delete_circulation_file(loan)
            expired_count += 1
        except (RuntimeError, OSError, ValueError, SQLAlchemyError):
            db.session.rollback()
            failed_count += 1
            current_app.logger.exception(
                "Failed to atomically expire loan %s. Changes for that loan were rolled back.",
                loan.public_id,
            )

    if expired_count:
        current_app.logger.info("Expired %d overdue loan(s).", expired_count)
    if failed_count:
        current_app.logger.warning("Skipped %d overdue loan(s) due to processing errors.", failed_count)


def process_waitlist(book, *, commit=True):
    """Notify waitlisted patrons while copies are available.

    Returns the number of successful notifications.
    """
    available_copies = max(0, int(book.available_copies))
    if available_copies <= 0:
        return 0

    max_notifications = min(available_copies, max(0, int(book.owned_copies)))
    notifications_sent = 0

    while notifications_sent < max_notifications:
        next_entry = (
            WaitlistEntry.query.filter_by(book_id=book.id, is_fulfilled=False)
            .filter(WaitlistEntry.notified_at == None)
            .order_by(WaitlistEntry.created_at.asc())
            .first()
        )

        if not next_entry:
            break

        # Send waitlist notification email first; only mark as notified on success.
        try:
            from ..email_service import send_waitlist_notification

            user = db.session.get(User, next_entry.user_id)
            if not user:
                break
            sent = send_waitlist_notification(user, book)
            if not sent:
                current_app.logger.warning(
                    "Waitlist notification send returned false for user %s book '%s'",
                    next_entry.user_id,
                    book.title,
                )
                break
        except (RuntimeError, OSError, ValueError):
            current_app.logger.exception("Failed to send waitlist notification for book '%s'", book.title)
            break

        next_entry.notified_at = _utcnow()
        log_event(
            action="waitlist_notify",
            target_type="book",
            target_id=book.id,
            detail=f"Notified patron {next_entry.user_id} that '{book.title}' is available",
            user_id=next_entry.user_id,
            commit=False,
        )
        notifications_sent += 1

    if notifications_sent:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        current_app.logger.info("Waitlist: notified %d patron(s) for book '%s'", notifications_sent, book.title)

    return notifications_sent


def send_reminders():
    """Send reminder emails for loans due soon."""
    reminder_days = current_app.config.get("REMINDER_DAYS_BEFORE_DUE", 2)
    threshold = _utcnow() + timedelta(days=reminder_days)

    upcoming = Loan.query.filter(
        Loan.is_active == True,
        Loan.due_at <= threshold,
        Loan.due_at > _utcnow(),
        Loan.reminder_sent == False,
    ).all()

    for loan in upcoming:
        try:
            from ..email_service import send_reminder_email

            user = db.session.get(User, loan.user_id)
            book = db.session.get(Book, loan.book_id)
            if user and book:
                sent = send_reminder_email(loan, user, book)
                if sent:
                    loan.reminder_sent = True
                else:
                    current_app.logger.warning("Reminder email send returned false for loan %s", loan.public_id)
        except (RuntimeError, OSError, ValueError):
            current_app.logger.exception(f"Failed to send reminder for loan {loan.public_id}")

    if upcoming:
        db.session.commit()
        current_app.logger.info(f"Sent {len(upcoming)} reminder(s).")


def _delete_circulation_file(loan):
    """Remove the circulation PDF from disk if it exists."""
    if loan.circulation_filename:
        circ_dir = Path(current_app.config["CIRCULATION_STORAGE"]).resolve()
        circ_path = (circ_dir / loan.circulation_filename).resolve()
        try:
            circ_path.relative_to(circ_dir)
        except ValueError:
            current_app.logger.warning(
                f"Blocked path traversal attempt in circulation file deletion: {loan.circulation_filename}"
            )
            return
        try:
            if circ_path.exists():
                circ_path.unlink()
        except OSError as exc:
            current_app.logger.warning(f"Could not delete circulation file {circ_path}: {exc}")
