"""Tests for admin management routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models import Book, Loan
from tests.conftest import _make_book, _make_user

# ── Access control ─────────────────────────────────────────────────


def test_patron_gets_403_on_admin(patron_client):
    rv = patron_client.get("/admin/")
    assert rv.status_code == 403


def test_admin_dashboard_loads(admin_client):
    rv = admin_client.get("/admin/")
    assert rv.status_code == 200


# ── Book management ────────────────────────────────────────────────


def test_admin_add_book(admin_client, db):
    rv = admin_client.post(
        "/admin/books/add",
        data={
            "title": "New Admin Book",
            "author": "Admin Author",
            "language": "en",
            "owned_copies": "1",
            "watermark_mode": "standard",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    book = Book.query.filter_by(title="New Admin Book").first()
    assert book is not None


def test_admin_edit_book(admin_client, db):
    book = _make_book(title="Before Edit")
    rv = admin_client.post(
        f"/admin/books/{book.id}/edit",
        data={
            "title": "After Edit",
            "author": book.author,
            "language": "en",
            "owned_copies": "1",
            "watermark_mode": "standard",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(book)
    assert book.title == "After Edit"


def test_admin_toggle_visibility(admin_client, db):
    book = _make_book(is_visible=True)
    rv = admin_client.post(f"/admin/books/{book.id}/toggle-visibility", follow_redirects=True)
    assert rv.status_code == 200
    db.session.refresh(book)
    assert book.is_visible is False


# ── User management ────────────────────────────────────────────────


def test_admin_block_user(admin_client, db):
    user = _make_user(email="toblock@test.com")
    rv = admin_client.post(
        f"/admin/users/{user.id}/block",
        data={"reason": "Test block"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(user)
    assert user.is_blocked is True


def test_admin_unblock_user(admin_client, db):
    user = _make_user(email="tounblock@test.com", is_blocked=True)
    rv = admin_client.post(f"/admin/users/{user.id}/unblock", follow_redirects=True)
    assert rv.status_code == 200
    db.session.refresh(user)
    assert user.is_blocked is False


def test_admin_change_role(admin_client, db):
    user = _make_user(email="rolechange@test.com")
    rv = admin_client.post(
        f"/admin/users/{user.id}/change-role",
        data={"role": "librarian"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(user)
    assert user.role == "librarian"


# ── Loan management ───────────────────────────────────────────────


@patch("app.lending.service._delete_circulation_file")
def test_admin_terminate_loan(mock_del, admin_client, db):
    user = _make_user(email="borrower@test.com")
    book = _make_book()
    loan = Loan(user_id=user.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=7))
    db.session.add(loan)
    db.session.commit()
    rv = admin_client.post(f"/admin/loans/{loan.id}/terminate", follow_redirects=True)
    assert rv.status_code == 200
    db.session.refresh(loan)
    assert loan.is_active is False


def test_admin_extend_loan(admin_client, db):
    user = _make_user(email="extendme@test.com")
    book = _make_book()
    loan = Loan(user_id=user.id, book_id=book.id, is_active=True, due_at=datetime.now(UTC) + timedelta(days=7))
    db.session.add(loan)
    db.session.commit()
    original_due = loan.due_at
    rv = admin_client.post(
        f"/admin/loans/{loan.id}/extend",
        data={"days": "7"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(loan)
    assert loan.due_at > original_due
