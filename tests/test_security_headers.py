"""Security header and cache-control behavior tests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models import Loan
from tests.conftest import _make_book


def test_login_page_includes_security_headers(client):
    rv = client.get("/login")
    assert rv.status_code == 200
    assert rv.headers.get("X-Content-Type-Options") == "nosniff"
    assert rv.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert rv.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert rv.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert rv.headers.get("Cross-Origin-Resource-Policy") == "same-origin"
    assert rv.headers.get("Permissions-Policy") == "geolocation=(), camera=(), microphone=()"
    csp = rv.headers.get("Content-Security-Policy", "")
    assert "script-src 'self' 'nonce-" in csp


def test_authenticated_html_is_not_cacheable(patron_client):
    rv = patron_client.get("/catalog")
    assert rv.status_code == 200
    assert rv.headers.get("Cache-Control") == "private, no-store"
    assert rv.headers.get("Pragma") == "no-cache"
    assert rv.headers.get("Expires") == "0"


def test_authenticated_opds_feed_is_not_cacheable(patron_client):
    rv = patron_client.get("/opds/catalog.xml")
    assert rv.status_code == 200
    assert rv.headers.get("Cache-Control") == "private, no-store"


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_authenticated_pdf_download_is_not_cacheable(mock_pdf, mock_del, patron_client, patron, app, db):
    book = _make_book(title="No Cache PDF")
    filename = "cache-test.pdf"
    file_path = f"{app.config['CIRCULATION_STORAGE']}/{filename}"
    with open(file_path, "wb") as f:
        f.write(b"%PDF-1.4\n%test\n")

    loan = Loan(
        user_id=patron.id,
        book_id=book.id,
        is_active=True,
        due_at=datetime.now(UTC) + timedelta(days=7),
        circulation_filename=filename,
        book_title_snapshot=book.title,
    )
    db.session.add(loan)
    db.session.commit()

    rv = patron_client.get(f"/loan/{loan.access_token}/download?file=1")
    assert rv.status_code == 200
    assert rv.headers.get("Cache-Control") == "private, no-store"
    assert rv.headers.get("Pragma") == "no-cache"
    assert rv.headers.get("Expires") == "0"
