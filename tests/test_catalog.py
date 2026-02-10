"""Tests for catalog browsing and book detail."""

import os

from sqlalchemy.exc import SQLAlchemyError

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


def test_search_like_fallback_uses_raw_query_text(patron_client, monkeypatch):
    _make_book(title="Mystic Theology")

    def _raise_fts_unavailable(*args, **kwargs):
        raise SQLAlchemyError("fts unavailable")

    monkeypatch.setattr("app.catalog.routes.db.session.execute", _raise_fts_unavailable)

    rv = patron_client.get("/catalog?q=Mystic Theology")
    assert rv.status_code == 200
    assert b"Mystic Theology" in rv.data


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


def test_patron_cannot_access_hidden_book_cover(patron_client, app, db):
    filename = "hidden-cover.jpg"
    with open(os.path.join(app.config["COVER_STORAGE"], filename), "wb") as f:
        f.write(b"fake-image")

    book = _make_book(title="Hidden Cover", is_visible=False)
    book.cover_filename = filename
    db.session.commit()
    rv = patron_client.get(f"/covers/{filename}")
    assert rv.status_code == 404


def test_admin_can_access_hidden_book_cover(admin_client, app, db):
    filename = "hidden-cover-admin.jpg"
    with open(os.path.join(app.config["COVER_STORAGE"], filename), "wb") as f:
        f.write(b"fake-image")

    book = _make_book(title="Hidden Cover Admin", is_visible=False)
    book.cover_filename = filename
    db.session.commit()
    rv = admin_client.get(f"/covers/{filename}")
    assert rv.status_code == 200
