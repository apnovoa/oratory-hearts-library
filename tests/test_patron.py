"""Tests for patron self-service routes."""

from unittest.mock import patch

from app.models import BookNote, Favorite
from tests.conftest import _make_book

# ── Dashboard ──────────────────────────────────────────────────────


def test_dashboard_loads(patron_client):
    rv = patron_client.get("/patron/dashboard")
    assert rv.status_code == 200


@patch("app.lending.service._delete_circulation_file")
@patch("app.pdf_service.generate_circulation_copy", return_value="test-circ.pdf")
def test_dashboard_shows_loans(mock_pdf, mock_del, patron_client, patron, db):
    book = _make_book(title="Dashboard Book")
    patron_client.post(f"/borrow/{book.public_id}", follow_redirects=True)
    rv = patron_client.get("/patron/dashboard")
    assert b"Dashboard Book" in rv.data


def test_dashboard_empty_for_new_patron(patron_client):
    rv = patron_client.get("/patron/dashboard")
    assert rv.status_code == 200


# ── Access control ─────────────────────────────────────────────────


def test_admin_gets_403_on_patron_routes(admin_client):
    rv = admin_client.get("/patron/dashboard")
    assert rv.status_code == 403


# ── Favorites ──────────────────────────────────────────────────────


def test_favorite_toggle_add(patron_client, patron, db):
    book = _make_book()
    rv = patron_client.post(f"/patron/favorites/{book.public_id}/toggle", follow_redirects=True)
    assert rv.status_code == 200
    fav = Favorite.query.filter_by(user_id=patron.id, book_id=book.id).first()
    assert fav is not None


def test_favorite_toggle_remove(patron_client, patron, db):
    book = _make_book()
    # Add
    patron_client.post(f"/patron/favorites/{book.public_id}/toggle", follow_redirects=True)
    # Remove
    patron_client.post(f"/patron/favorites/{book.public_id}/toggle", follow_redirects=True)
    fav = Favorite.query.filter_by(user_id=patron.id, book_id=book.id).first()
    assert fav is None


def test_favorite_toggle_hidden_book_returns_404(patron_client, db):
    hidden = _make_book(title="Hidden Favorite", is_visible=False)
    rv = patron_client.post(f"/patron/favorites/{hidden.public_id}/toggle", follow_redirects=False)
    assert rv.status_code == 404


def test_save_note_hidden_book_returns_404(patron_client):
    hidden = _make_book(title="Hidden Note", is_visible=False)
    rv = patron_client.post(
        f"/patron/notes/{hidden.public_id}",
        data={"content": "note"},
        follow_redirects=False,
    )
    assert rv.status_code == 404


def test_delete_note_hidden_book_returns_404(patron_client, patron, db):
    hidden = _make_book(title="Hidden Delete Note", is_visible=False)
    db.session.add(BookNote(user_id=patron.id, book_id=hidden.id, content="note"))
    db.session.commit()

    rv = patron_client.post(f"/patron/notes/{hidden.public_id}/delete", follow_redirects=False)
    assert rv.status_code == 404


# ── Profile ────────────────────────────────────────────────────────


def test_profile_loads(patron_client):
    rv = patron_client.get("/patron/profile")
    assert rv.status_code == 200


def test_profile_update_display_name(patron_client, patron, db):
    rv = patron_client.post(
        "/patron/profile",
        data={
            "display_name": "New Display Name",
            "birth_month": "0",
            "birth_day": "0",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200
    db.session.refresh(patron)
    assert patron.display_name == "New Display Name"
