"""Tests for lending: checkout, return, renew, waitlist."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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


def test_checkout_requires_login(client, db):
    book = _make_book()
    rv = client.post(f"/borrow/{book.public_id}", follow_redirects=False)
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


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
