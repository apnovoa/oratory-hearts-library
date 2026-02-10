"""Tests for admin management routes."""

import io
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models import Book, Loan, User
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
    assert user.force_logout_before is not None


def test_admin_unblock_user(admin_client, db):
    user = _make_user(email="tounblock@test.com", is_blocked=True)
    rv = admin_client.post(f"/admin/users/{user.id}/unblock", follow_redirects=True)
    assert rv.status_code == 200
    db.session.refresh(user)
    assert user.is_blocked is False


def test_admin_deactivate_user_forces_logout(admin_client, db):
    user = _make_user(email="deactivate@test.com")
    rv = admin_client.post(
        f"/admin/users/{user.id}/deactivate",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(user)
    assert user.is_active_account is False
    assert user.force_logout_before is not None


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


# ── Last-admin guard ─────────────────────────────────────────────


def test_last_admin_cannot_be_demoted(admin_client, db):
    """Demoting the sole admin must be rejected."""
    admin = User.query.filter_by(role="admin").first()
    rv = admin_client.post(
        f"/admin/users/{admin.id}/change-role",
        data={"role": "patron"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(admin)
    assert admin.role == "admin"
    assert b"Cannot demote the only active admin" in rv.data


def test_last_admin_cannot_be_deactivated(admin_client, db):
    """Deactivating the sole admin must be rejected."""
    admin = User.query.filter_by(role="admin").first()
    rv = admin_client.post(
        f"/admin/users/{admin.id}/deactivate",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(admin)
    assert admin.is_active_account is True
    assert b"Cannot deactivate the only active admin" in rv.data


def test_last_admin_cannot_be_blocked(admin_client, db):
    """Blocking the sole admin must be rejected."""
    admin = User.query.filter_by(role="admin").first()
    rv = admin_client.post(
        f"/admin/users/{admin.id}/block",
        data={"reason": "test"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(admin)
    assert admin.is_blocked is False
    assert b"Cannot block the only active admin" in rv.data


def test_last_admin_cannot_be_force_logged_out(admin_client, db):
    """Force-logging out the sole admin must be rejected."""
    admin = User.query.filter_by(role="admin").first()
    rv = admin_client.post(
        f"/admin/users/{admin.id}/force-logout",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(admin)
    assert admin.force_logout_before is None
    assert b"Cannot force-logout the only active admin" in rv.data


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


def test_admin_extend_inactive_loan_rejected(admin_client, db):
    user = _make_user(email="inactiveextend@test.com")
    book = _make_book()
    loan = Loan(
        user_id=user.id,
        book_id=book.id,
        is_active=False,
        returned_at=datetime.now(UTC),
        due_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.session.add(loan)
    db.session.commit()
    original_due = loan.due_at

    rv = admin_client.post(
        f"/admin/loans/{loan.id}/extend",
        data={"days": "7"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"Only active, non-invalidated loans can be extended." in rv.data
    db.session.refresh(loan)
    assert loan.due_at == original_due


# ── Upload hardening ──────────────────────────────────────────────


def test_admin_add_book_rejects_invalid_cover_magic(admin_client, db):
    rv = admin_client.post(
        "/admin/books/add",
        data={
            "title": "Invalid Cover Book",
            "author": "Cover Author",
            "language": "en",
            "owned_copies": "1",
            "watermark_mode": "standard",
            "cover_file": (io.BytesIO(b"not-an-image"), "cover.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"not a valid JPEG, PNG, or WebP" in rv.data
    assert Book.query.filter_by(title="Invalid Cover Book").first() is None


def test_admin_add_book_accepts_valid_cover_magic(admin_client, db):
    # Minimal PNG signature + padding; route validates magic bytes only.
    png_payload = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    rv = admin_client.post(
        "/admin/books/add",
        data={
            "title": "Valid Cover Book",
            "author": "Cover Author",
            "language": "en",
            "owned_copies": "1",
            "watermark_mode": "standard",
            "cover_file": (io.BytesIO(png_payload), "cover.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    book = Book.query.filter_by(title="Valid Cover Book").first()
    assert book is not None
    assert book.cover_filename is not None


def test_admin_add_book_rejects_oversized_master_pdf(admin_client):
    admin_client.application.config["MAX_PDF_FILE_SIZE"] = 10
    oversized_pdf = b"%PDF-" + b"0" * 64
    rv = admin_client.post(
        "/admin/books/add",
        data={
            "title": "Oversized Master",
            "author": "Admin Author",
            "language": "en",
            "owned_copies": "1",
            "watermark_mode": "standard",
            "master_file": (io.BytesIO(oversized_pdf), "master.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"Master PDF is too large" in rv.data
    assert Book.query.filter_by(title="Oversized Master").first() is None


def test_admin_edit_book_rejects_oversized_master_pdf(admin_client, db):
    admin_client.application.config["MAX_PDF_FILE_SIZE"] = 10
    book = _make_book(title="Edit Oversized Master")
    oversized_pdf = b"%PDF-" + b"0" * 64
    rv = admin_client.post(
        f"/admin/books/{book.id}/edit",
        data={
            "title": book.title,
            "author": book.author,
            "language": "en",
            "owned_copies": "1",
            "watermark_mode": "standard",
            "master_file": (io.BytesIO(oversized_pdf), "master.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"Master PDF is too large" in rv.data

    db.session.refresh(book)
    assert book.master_filename == "test-master.pdf"


def test_import_pdf_upload_enforces_file_count_limit(admin_client):
    admin_client.application.config["MAX_FILES_PER_UPLOAD"] = 2
    pdf = b"%PDF-1.7\n%%EOF"
    rv = admin_client.post(
        "/admin/import-pdf/upload",
        data={
            "pdf_files": [
                (io.BytesIO(pdf), "a.pdf"),
                (io.BytesIO(pdf), "b.pdf"),
                (io.BytesIO(pdf), "c.pdf"),
            ]
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"at most 2 files" in rv.data


def test_import_pdf_upload_enforces_per_file_size_limit(admin_client):
    admin_client.application.config["MAX_PDF_FILE_SIZE"] = 10
    oversized_pdf = b"%PDF-" + b"0" * 64
    rv = admin_client.post(
        "/admin/import-pdf/upload",
        data={"pdf_files": [(io.BytesIO(oversized_pdf), "oversized.pdf")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"exceeds" in rv.data
