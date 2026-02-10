"""Additional admin route tests for core/reporting/request/import flows."""

from unittest.mock import patch

from app.models import AuditLog, BookRequest, ReadingList, ReadingListItem, StagedBook, User
from tests.conftest import _make_book, _make_user


def _make_staged(filename="staged.pdf", *, status="pending", title="Staged Title", author="Staged Author"):
    staged = StagedBook(
        original_filename=filename,
        file_size=123,
        file_hash=f"hash-{filename}",
        title=title,
        author=author,
        status=status,
    )
    from app.models import db

    db.session.add(staged)
    db.session.commit()
    return staged


def test_admin_change_password_success_forces_logout(admin_client, db):
    admin = User.query.filter_by(role="admin").first()

    rv = admin_client.post(
        "/admin/change-password",
        data={
            "current_password": "AdminPass1",
            "new_password": "NewAdminPass123",
            "confirm_password": "NewAdminPass123",
        },
        follow_redirects=False,
    )

    assert rv.status_code == 302
    assert rv.headers["Location"].endswith("/login")

    db.session.refresh(admin)
    assert admin.check_password("NewAdminPass123")
    assert admin.force_logout_before is not None


def test_admin_change_password_rejects_bad_current_password(admin_client, db):
    admin = User.query.filter_by(role="admin").first()
    old_hash = admin.password_hash

    rv = admin_client.post(
        "/admin/change-password",
        data={
            "current_password": "wrong-password",
            "new_password": "NewAdminPass123",
            "confirm_password": "NewAdminPass123",
        },
        follow_redirects=True,
    )

    assert rv.status_code == 200
    assert b"Current password is incorrect." in rv.data

    db.session.refresh(admin)
    assert admin.password_hash == old_hash


def test_admin_reports_page_loads(admin_client):
    rv = admin_client.get("/admin/reports")
    assert rv.status_code == 200


def test_admin_audit_page_filters_date_range(admin_client, db):
    admin = User.query.filter_by(role="admin").first()
    db.session.add(
        AuditLog(
            user_id=admin.id,
            action="unit_test_action",
            target_type="book",
            detail="testing",
            ip_address="127.0.0.1",
        )
    )
    db.session.commit()

    rv = admin_client.get("/admin/audit?action=unit_test_action&date_from=2024-01-01&date_to=2026-12-31")
    assert rv.status_code == 200
    assert b"unit_test_action" in rv.data


def test_admin_audit_export_sanitizes_formula_cells(admin_client, db):
    admin = User.query.filter_by(role="admin").first()
    db.session.add(
        AuditLog(
            user_id=admin.id,
            action="=HYPERLINK('http://evil')",
            target_type="+book",
            detail="@formula",
            ip_address="\t127.0.0.1",
        )
    )
    db.session.commit()

    rv = admin_client.get("/admin/audit/export")
    assert rv.status_code == 200
    assert rv.headers["Content-Disposition"].endswith("audit_log_export.csv")

    text = rv.data.decode("utf-8")
    assert "'=HYPERLINK('http://evil')" in text
    assert "'+book" in text
    assert "'@formula" in text
    assert "'\t127.0.0.1" in text


def test_admin_book_request_resolve_sets_resolver_and_timestamp(admin_client, db):
    patron = _make_user(email="requester@test.com")
    req = BookRequest(user_id=patron.id, title="Desired Book", author="Desired Author", reason="Need this")
    db.session.add(req)
    db.session.commit()

    admin = User.query.filter_by(role="admin").first()

    rv = admin_client.post(
        f"/admin/requests/{req.id}/resolve",
        data={"status": "approved", "admin_notes": "Will acquire soon"},
        follow_redirects=True,
    )
    assert rv.status_code == 200

    db.session.refresh(req)
    assert req.status == "approved"
    assert req.admin_notes == "Will acquire soon"
    assert req.resolved_by == admin.id
    assert req.resolved_at is not None


def test_admin_book_request_resolve_invalid_form_is_rejected(admin_client, db):
    patron = _make_user(email="requester2@test.com")
    req = BookRequest(user_id=patron.id, title="Need Book")
    db.session.add(req)
    db.session.commit()

    rv = admin_client.post(
        f"/admin/requests/{req.id}/resolve",
        data={},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert b"Invalid form submission." in rv.data

    db.session.refresh(req)
    assert req.status == "pending"
    assert req.resolved_at is None


def test_admin_reading_list_create_edit_remove_and_delete(admin_client, db):
    book = _make_book(title="Reading List Book")

    rv_create = admin_client.post(
        "/admin/reading-lists/new",
        data={
            "name": "Lent Reading",
            "description": "for lent",
            "is_public": "y",
            "is_featured": "y",
            "season": "lent",
        },
        follow_redirects=True,
    )
    assert rv_create.status_code == 200

    rl = ReadingList.query.filter_by(name="Lent Reading").first()
    assert rl is not None

    rv_edit_add = admin_client.post(
        f"/admin/reading-lists/{rl.id}/edit",
        data={
            "name": "Lent Reading Updated",
            "description": "updated",
            "season": "lent",
            "add_book_id": str(book.id),
        },
        follow_redirects=True,
    )
    assert rv_edit_add.status_code == 200

    db.session.refresh(rl)
    assert rl.name == "Lent Reading Updated"
    item = ReadingListItem.query.filter_by(reading_list_id=rl.id, book_id=book.id).first()
    assert item is not None

    rv_edit_remove = admin_client.post(
        f"/admin/reading-lists/{rl.id}/edit",
        data={
            "name": "Lent Reading Updated",
            "description": "updated",
            "season": "lent",
            f"remove_{item.id}": "1",
        },
        follow_redirects=True,
    )
    assert rv_edit_remove.status_code == 200
    assert ReadingListItem.query.filter_by(reading_list_id=rl.id, book_id=book.id).first() is None

    rv_delete = admin_client.post(f"/admin/reading-lists/{rl.id}/delete", follow_redirects=True)
    assert rv_delete.status_code == 200
    assert ReadingList.query.get(rl.id) is None


def test_import_pdf_scan_status_returns_json(admin_client):
    with patch("app.scanner.get_scan_progress", return_value={"running": True, "processed": 2}):
        rv = admin_client.get("/admin/import-pdf/scan-status")

    assert rv.status_code == 200
    assert rv.get_json() == {"running": True, "processed": 2}


def test_import_pdf_scan_handles_running_and_success(admin_client):
    with patch("app.scanner.start_scan", return_value=False):
        rv_running = admin_client.post("/admin/import-pdf/scan", follow_redirects=True)
    assert rv_running.status_code == 200
    assert b"A scan is already in progress." in rv_running.data

    with patch("app.scanner.start_scan", return_value="abcdef123456"):
        rv_started = admin_client.post("/admin/import-pdf/scan", follow_redirects=True)
    assert rv_started.status_code == 200
    assert b"Scan started" in rv_started.data


def test_import_pdf_bulk_dismiss_only_updates_pending(admin_client, db):
    pending = _make_staged("pending.pdf", status="pending")
    approved = _make_staged("approved.pdf", status="approved")

    rv = admin_client.post(
        "/admin/import-pdf/bulk-dismiss",
        data={"staged_ids": [str(pending.id), str(approved.id)]},
        follow_redirects=True,
    )
    assert rv.status_code == 200

    db.session.refresh(pending)
    db.session.refresh(approved)
    assert pending.status == "dismissed"
    assert approved.status == "approved"


def test_import_pdf_ai_enrich_disabled_short_circuits(admin_client):
    admin_client.application.config["AI_EXTRACTION_ENABLED"] = False

    rv = admin_client.post(
        "/admin/import-pdf/ai-enrich",
        data={"staged_ids": ["1"]},
        follow_redirects=True,
    )

    assert rv.status_code == 200
    assert b"AI extraction is disabled" in rv.data


def test_import_pdf_refresh_covers_requires_selection(admin_client):
    rv = admin_client.post("/admin/import-pdf/refresh-covers", data={}, follow_redirects=True)

    assert rv.status_code == 200
    assert b"No books selected." in rv.data
