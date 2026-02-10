"""Tests for lending: checkout, return, renew, waitlist."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app.models import Book, Loan, WaitlistEntry
from tests.conftest import _make_book, _make_user

# ── Checkout ───────────────────────────────────────────────────────


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_checkout_success(mock_pdf, mock_del, patron_client, patron, app):
    book = _make_book(title="Checkout Book")
    rv = patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    loan = Loan.query.filter_by(user_id=patron.id, book_id=book.id).first()
    assert loan is not None
    assert loan.is_active is True


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_checkout_uses_configured_max_renewals(mock_pdf, mock_del, patron_client, patron, app):
    app.config["MAX_RENEWALS"] = 4
    book = _make_book(title="Renew Config Book")
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    loan = Loan.query.filter_by(user_id=patron.id, book_id=book.id).first()
    assert loan is not None
    assert loan.max_renewals == 4


def test_checkout_requires_login(client, db):
    book = _make_book()
    rv = client.post(f"/borrow/{book.public_id}", follow_redirects=False)
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


def test_checkout_handles_database_busy_lock(patron_client, patron, monkeypatch):
    book = _make_book(title="Busy Lock Book")

    def _busy_lock():
        raise ValueError("The library is busy processing another checkout. Please try again in a moment.")

    monkeypatch.setattr("app.lending.service._begin_checkout_transaction", _busy_lock)

    rv = patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"library is busy processing another checkout" in rv.data
    assert Loan.query.filter_by(user_id=patron.id, book_id=book.id).count() == 0


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_checkout_refused_no_master(mock_pdf, mock_del, patron_client, db):
    book = _make_book(title="No Master", master_filename=None)
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    # Book without master is not available; should not create a loan
    assert Loan.query.count() == 0


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_checkout_refused_no_copies(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book(owned_copies=1)
    # Create an existing active loan to exhaust copies
    other = _make_user(email="other@test.com")
    loan = Loan(user_id=other.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=7))
    db.session.add(loan)
    db.session.commit()
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    assert Loan.query.filter_by(user_id=patron.id).count() == 0


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_checkout_refused_duplicate_loan(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book(owned_copies=2)
    # First checkout should succeed
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    # Second checkout for same book should be refused
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    assert Loan.query.filter_by(user_id=patron.id, book_id=book.id).count() == 1


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_checkout_refused_max_loans(mock_pdf, mock_del, patron_client, patron, app, db):
    max_loans = app.config["MAX_LOANS_PER_PATRON"]
    for i in range(max_loans):
        _make_book(title=f"Book {i}")
    books = Book.query.all()
    for book in books[:max_loans]:
        patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    # One more should be refused
    extra = _make_book(title="One Too Many")
    patron_client.post(f"/borrow/{extra.public_id}", follow_redirects=True)
    assert Loan.query.filter_by(user_id=patron.id).count() == max_loans


def test_borrow_hidden_book_returns_404(patron_client):
    hidden_book = _make_book(title="Hidden Borrow", is_visible=False)
    rv = patron_client.post(f"/borrow/{hidden_book.public_id}", follow_redirects=False)
    assert rv.status_code == 404


def test_borrow_restricted_book_is_rejected(patron_client, patron, db):
    restricted = _make_book(title="Restricted Borrow")
    restricted.restricted_access = True
    db.session.commit()

    rv = patron_client.post(f"/borrow/{restricted.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"restricted access" in rv.data
    assert Loan.query.filter_by(user_id=patron.id, book_id=restricted.id).count() == 0


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", side_effect=RuntimeError("PDF fail"))
def test_pdf_failure_rolls_back_loan(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book()
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    # Loan should have been rolled back
    assert Loan.query.filter_by(user_id=patron.id).count() == 0


# ── Return ─────────────────────────────────────────────────────────


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_return_marks_inactive(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book()
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    loan = Loan.query.filter_by(user_id=patron.id).first()
    rv = patron_client.post(f"/patron/loans/{loan.public_id}/return", follow_redirects=True)
    assert rv.status_code == 200
    db.session.refresh(loan)
    assert loan.is_active is False
    assert loan.returned_at is not None


# ── Renew ──────────────────────────────────────────────────────────


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_renew_extends_due_date(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book()
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    loan = Loan.query.filter_by(user_id=patron.id).first()
    original_due = loan.due_at
    patron_client.post(f"/patron/loans/{loan.public_id}/renew", follow_redirects=True)
    db.session.refresh(loan)
    assert loan.due_at > original_due
    assert loan.renewal_count == 1


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_max_renewals_enforced(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book()
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    loan = Loan.query.filter_by(user_id=patron.id).first()
    for _ in range(loan.max_renewals):
        patron_client.post(f"/patron/loans/{loan.public_id}/renew", follow_redirects=True)
    # Next renewal should fail
    patron_client.post(f"/patron/loans/{loan.public_id}/renew", follow_redirects=True)
    db.session.refresh(loan)
    assert loan.renewal_count == loan.max_renewals


def test_renew_refused_for_expired_loan(patron_client, patron, db):
    book = _make_book(title="Expired Renew Book")
    expired_loan = Loan(
        user_id=patron.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db.session.add(expired_loan)
    db.session.commit()

    original_due = expired_loan.due_at
    rv = patron_client.post(f"/patron/loans/{expired_loan.public_id}/renew", follow_redirects=True)
    assert rv.status_code == 200
    assert b"expired and cannot be renewed" in rv.data

    db.session.refresh(expired_loan)
    assert expired_loan.renewal_count == 0
    assert expired_loan.due_at == original_due


# ── Waitlist ───────────────────────────────────────────────────────


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_waitlist_join(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book(owned_copies=1)
    # Exhaust copies with another user's loan
    other = _make_user(email="other@test.com")
    loan = Loan(user_id=other.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=7))
    db.session.add(loan)
    db.session.commit()
    rv = patron_client.post(f"/waitlist/{book.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    entry = WaitlistEntry.query.filter_by(user_id=patron.id, book_id=book.id).first()
    assert entry is not None


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_waitlist_duplicate_prevented(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book(owned_copies=1)
    other = _make_user(email="other@test.com")
    loan = Loan(user_id=other.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=7))
    db.session.add(loan)
    db.session.commit()
    patron_client.post(f"/waitlist/{book.public_id}", follow_redirects=True)
    patron_client.post(f"/waitlist/{book.public_id}", follow_redirects=True)
    count = WaitlistEntry.query.filter_by(user_id=patron.id, book_id=book.id, is_fulfilled=False).count()
    assert count == 1


def test_waitlist_join_handles_integrity_race(patron_client, patron, db, monkeypatch):
    book = _make_book(title="Waitlist Race", owned_copies=1)
    other = _make_user(email="race-other@test.com")
    db.session.add(
        Loan(user_id=other.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=7))
    )
    db.session.commit()

    real_commit = db.session.commit
    state = {"raised": False}

    def _flaky_commit():
        if not state["raised"]:
            state["raised"] = True
            raise IntegrityError("INSERT INTO waitlist_entries ...", {}, Exception("duplicate"))
        return real_commit()

    monkeypatch.setattr(db.session, "commit", _flaky_commit)

    rv = patron_client.post(f"/waitlist/{book.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"already on the waitlist" in rv.data
    assert WaitlistEntry.query.filter_by(user_id=patron.id, book_id=book.id).count() == 0


def test_waitlist_hidden_book_returns_404(patron_client):
    hidden_book = _make_book(title="Hidden Waitlist", is_visible=False)
    rv = patron_client.post(f"/waitlist/{hidden_book.public_id}", follow_redirects=False)
    assert rv.status_code == 404


def test_waitlist_restricted_book_is_rejected(patron_client, patron, db):
    restricted = _make_book(title="Restricted Waitlist")
    restricted.restricted_access = True
    db.session.commit()

    rv = patron_client.post(f"/waitlist/{restricted.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"cannot be waitlisted" in rv.data
    assert WaitlistEntry.query.filter_by(user_id=patron.id, book_id=restricted.id).count() == 0


def test_waitlist_available_book_is_rejected(patron_client, patron):
    available_book = _make_book(title="Available Waitlist")
    rv = patron_client.post(f"/waitlist/{available_book.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"currently available" in rv.data
    assert WaitlistEntry.query.filter_by(user_id=patron.id, book_id=available_book.id).count() == 0


def test_waitlist_book_without_master_is_rejected(patron_client, patron):
    no_master = _make_book(title="No Master Waitlist", master_filename=None)
    rv = patron_client.post(f"/waitlist/{no_master.public_id}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"not currently available for circulation" in rv.data
    assert WaitlistEntry.query.filter_by(user_id=patron.id, book_id=no_master.id).count() == 0


# ── Already-returned loan ─────────────────────────────────────────


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_return_already_returned_handled(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book()
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    loan = Loan.query.filter_by(user_id=patron.id).first()
    patron_client.post(f"/patron/loans/{loan.public_id}/return", follow_redirects=True)
    # Second return attempt
    patron_client.post(f"/patron/loans/{loan.public_id}/return", follow_redirects=True)
    # Should not crash; loan remains inactive
    db.session.refresh(loan)
    assert loan.is_active is False


# ── Access control and path traversal ─────────────────────────────


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_reader_idor_forbidden(mock_pdf, mock_del, patron_client, db):
    owner = _make_user(email="owner@test.com")
    book = _make_book(title="Owner Book")
    loan = Loan(
        user_id=owner.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.session.add(loan)
    db.session.commit()

    # Logged in as a different patron; must get 403.
    rv = patron_client.get(f"/read/{loan.access_token}")
    assert rv.status_code == 403


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_download_idor_forbidden(mock_pdf, mock_del, patron_client, db):
    owner = _make_user(email="owner2@test.com")
    book = _make_book(title="Owner Book 2")
    loan = Loan(
        user_id=owner.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.session.add(loan)
    db.session.commit()

    # Logged in as a different patron; must get 403.
    rv = patron_client.get(f"/loan/{loan.access_token}/download")
    assert rv.status_code == 403


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_download_blocks_path_traversal(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book(title="Traversal Book")
    loan = Loan(
        user_id=patron.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) + timedelta(days=7),
        circulation_filename="../outside.pdf",
    )
    db.session.add(loan)
    db.session.commit()

    rv = patron_client.get(f"/loan/{loan.access_token}/download?file=1")
    assert rv.status_code == 403


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_download_sanitizes_attachment_filename(mock_pdf, mock_del, patron_client, patron, app, db):
    book = _make_book(title="Sanitize Download")
    filename = "loan-safe.pdf"
    file_path = f"{app.config['CIRCULATION_STORAGE']}/{filename}"
    with open(file_path, "wb") as f:
        f.write(b"%PDF-1.4\n%test\n")

    loan = Loan(
        user_id=patron.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) + timedelta(days=7),
        circulation_filename=filename,
        book_title_snapshot="../evil\r\nInjected",
    )
    db.session.add(loan)
    db.session.commit()

    rv = patron_client.get(f"/loan/{loan.access_token}/download?file=1")
    assert rv.status_code == 200
    disposition = rv.headers.get("Content-Disposition", "")
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "/" not in disposition
    assert "\\\\" not in disposition


# ── Expiry + waitlist integration ─────────────────────────────────


@patch("app.email_service.send_waitlist_notification")
@patch("app.email_service.send_expiration_email")
def test_expire_loans_marks_expired_and_notifies_waitlist(mock_expiration, mock_waitlist, app, db):
    mock_expiration.return_value = True
    mock_waitlist.return_value = True
    borrower = _make_user(email="exp-borrower@test.com")
    waiting_patron = _make_user(email="exp-waiter@test.com")
    book = _make_book(title="Expiry Book", owned_copies=1)

    overdue_loan = Loan(
        user_id=borrower.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) - timedelta(hours=1),
        book_title_snapshot=book.title,
        book_author_snapshot=book.author,
    )
    db.session.add(overdue_loan)
    db.session.add(WaitlistEntry(user_id=waiting_patron.id, book_id=book.id))
    db.session.commit()

    with app.app_context():
        from app.lending.service import expire_loans

        expire_loans()

    db.session.refresh(overdue_loan)
    waitlist_entry = WaitlistEntry.query.filter_by(user_id=waiting_patron.id, book_id=book.id).first()
    assert overdue_loan.is_active is False
    assert overdue_loan.returned_at is not None
    assert waitlist_entry.notified_at is not None
    mock_waitlist.assert_called_once()


@patch("app.email_service.send_expiration_email")
def test_expire_loans_rolls_back_single_loan_if_waitlist_step_fails(mock_expiration, app, db):
    borrower = _make_user(email="rollback-borrower@test.com")
    book = _make_book(title="Rollback Book", owned_copies=1)
    overdue_loan = Loan(
        user_id=borrower.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    db.session.add(overdue_loan)
    db.session.commit()

    with app.app_context():
        from app.lending.service import expire_loans

        with patch("app.lending.service.process_waitlist", side_effect=RuntimeError("waitlist failed")):
            expire_loans()

    db.session.refresh(overdue_loan)
    assert overdue_loan.is_active is True
    assert overdue_loan.returned_at is None


@patch("app.email_service.send_waitlist_notification", return_value=False)
def test_process_waitlist_does_not_mark_notified_on_email_failure(mock_waitlist, app, db):
    borrower = _make_user(email="wl-borrower@test.com")
    waiting_patron = _make_user(email="wl-waiter@test.com")
    book = _make_book(title="Waitlist Failure Book", owned_copies=1)
    inactive_loan = Loan(
        user_id=borrower.id,
        book_id=book.id,
        is_active=False,
        due_at=datetime.now(UTC) - timedelta(days=1),
    )
    wait_entry = WaitlistEntry(user_id=waiting_patron.id, book_id=book.id, is_fulfilled=False)
    db.session.add_all([inactive_loan, wait_entry])
    db.session.commit()

    with app.app_context():
        from app.lending.service import process_waitlist

        result = process_waitlist(book)
        assert result == 0

    db.session.refresh(wait_entry)
    assert wait_entry.notified_at is None
    mock_waitlist.assert_called_once()


@patch("app.email_service.send_waitlist_notification", return_value=True)
def test_process_waitlist_notifies_multiple_patrons_when_multiple_copies_available(mock_waitlist, app, db):
    waiter_1 = _make_user(email="wl-multi-1@test.com")
    waiter_2 = _make_user(email="wl-multi-2@test.com")
    waiter_3 = _make_user(email="wl-multi-3@test.com")
    book = _make_book(title="Waitlist Multi", owned_copies=3)

    # One active loan => two copies available.
    borrower = _make_user(email="wl-multi-borrower@test.com")
    db.session.add(
        Loan(user_id=borrower.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=2))
    )
    db.session.add_all(
        [
            WaitlistEntry(user_id=waiter_1.id, book_id=book.id, is_fulfilled=False),
            WaitlistEntry(user_id=waiter_2.id, book_id=book.id, is_fulfilled=False),
            WaitlistEntry(user_id=waiter_3.id, book_id=book.id, is_fulfilled=False),
        ]
    )
    db.session.commit()

    with app.app_context():
        from app.lending.service import process_waitlist

        sent_count = process_waitlist(book)
        assert sent_count == 2

    entries = WaitlistEntry.query.filter_by(book_id=book.id).order_by(WaitlistEntry.created_at.asc()).all()
    assert entries[0].notified_at is not None
    assert entries[1].notified_at is not None
    assert entries[2].notified_at is None
    assert mock_waitlist.call_count == 2


@patch("app.email_service.send_waitlist_notification", side_effect=[True, False])
def test_process_waitlist_stops_fifo_on_notification_failure(mock_waitlist, app, db):
    waiter_1 = _make_user(email="wl-fifo-1@test.com")
    waiter_2 = _make_user(email="wl-fifo-2@test.com")
    book = _make_book(title="Waitlist FIFO", owned_copies=2)
    db.session.add_all(
        [
            WaitlistEntry(user_id=waiter_1.id, book_id=book.id, is_fulfilled=False),
            WaitlistEntry(user_id=waiter_2.id, book_id=book.id, is_fulfilled=False),
        ]
    )
    db.session.commit()

    with app.app_context():
        from app.lending.service import process_waitlist

        sent_count = process_waitlist(book)
        assert sent_count == 1

    entries = WaitlistEntry.query.filter_by(book_id=book.id).order_by(WaitlistEntry.created_at.asc()).all()
    assert entries[0].notified_at is not None
    assert entries[1].notified_at is None
    assert mock_waitlist.call_count == 2


@patch("app.email_service.send_reminder_email", return_value=False)
def test_send_reminders_does_not_mark_sent_on_email_failure(mock_reminder, app, db):
    patron = _make_user(email="reminder-patron@test.com")
    book = _make_book(title="Reminder Failure Book")
    loan = Loan(
        user_id=patron.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) + timedelta(hours=12),
        reminder_sent=False,
    )
    db.session.add(loan)
    db.session.commit()

    with app.app_context():
        from app.lending.service import send_reminders

        send_reminders()

    db.session.refresh(loan)
    assert loan.reminder_sent is False
    mock_reminder.assert_called_once()


@patch("app.email_service.send_waitlist_notification", return_value=True)
@patch("app.email_service.send_expiration_email", return_value=False)
def test_expire_loans_does_not_mark_expiration_notice_on_email_failure(mock_expiration, mock_waitlist, app, db):
    borrower = _make_user(email="exp-fail-borrower@test.com")
    waiting_patron = _make_user(email="exp-fail-waiter@test.com")
    book = _make_book(title="Expiry Notice Failure Book", owned_copies=1)
    overdue_loan = Loan(
        user_id=borrower.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) - timedelta(minutes=15),
    )
    db.session.add_all([overdue_loan, WaitlistEntry(user_id=waiting_patron.id, book_id=book.id, is_fulfilled=False)])
    db.session.commit()

    with app.app_context():
        from app.lending.service import expire_loans

        expire_loans()

    db.session.refresh(overdue_loan)
    assert overdue_loan.is_active is False
    assert overdue_loan.expiration_notice_sent is False
    mock_expiration.assert_called_once()
