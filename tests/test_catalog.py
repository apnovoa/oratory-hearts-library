"""Tests for catalog browsing and book detail."""

from tests.conftest import _make_book

# ── Auth-required redirects ────────────────────────────────────────


def test_catalog_requires_login(client):
    rv = client.get("/catalog", follow_redirects=False)
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


def test_catalog_detail_requires_login(client, db):
    book = _make_book()
    rv = client.get(f"/catalog/{book.public_id}", follow_redirects=False)
    assert rv.status_code == 302


# ── Browse ─────────────────────────────────────────────────────────


def test_browse_shows_visible_books(patron_client, db):
    _make_book(title="Visible Book")
    rv = patron_client.get("/catalog")
    assert rv.status_code == 200
    assert b"Visible Book" in rv.data


def test_browse_hides_invisible_books(patron_client, db):
    _make_book(title="Hidden Book", is_visible=False)
    rv = patron_client.get("/catalog")
    assert b"Hidden Book" not in rv.data


def test_browse_hides_disabled_books(patron_client, db):
    _make_book(title="Disabled Book", is_disabled=True)
    rv = patron_client.get("/catalog")
    assert b"Disabled Book" not in rv.data


# ── Search ─────────────────────────────────────────────────────────


def test_search_returns_200(patron_client, db):
    """Search page loads without error (FTS5 unavailable in :memory: SQLite)."""
    _make_book(title="Summa Theologica")
    rv = patron_client.get("/catalog?q=Summa")
    assert rv.status_code == 200


# ── Detail ─────────────────────────────────────────────────────────


def test_detail_shows_book_info(patron_client, db):
    book = _make_book(title="Detail Test Book", author="Detail Author")
    rv = patron_client.get(f"/catalog/{book.public_id}")
    assert rv.status_code == 200
    assert b"Detail Test Book" in rv.data
    assert b"Detail Author" in rv.data


def test_detail_404_for_nonexistent(patron_client):
    rv = patron_client.get("/catalog/nonexistent-id-12345")
    assert rv.status_code == 404
