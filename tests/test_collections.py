"""Tests for public collections routes."""

from app.models import ReadingList, ReadingListItem
from tests.conftest import _make_book, _make_user


def test_collections_requires_login(client):
    rv = client.get("/collections", follow_redirects=False)
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


def test_collections_index_lists_public_collections(patron_client, db):
    creator = _make_user(email="collector@test.com", role="admin")
    reading_list = ReadingList(
        name="Seasonal Classics",
        description="Featured readings",
        is_public=True,
        is_featured=True,
        created_by=creator.id,
    )
    db.session.add(reading_list)
    db.session.commit()

    rv = patron_client.get("/collections")
    assert rv.status_code == 200
    assert b"Seasonal Classics" in rv.data


def test_collections_detail_shows_books_for_public_list(patron_client, db):
    creator = _make_user(email="collector2@test.com", role="admin")
    book = _make_book(title="Collected Work", author="Collection Author")
    reading_list = ReadingList(
        name="Collected Shelf",
        is_public=True,
        created_by=creator.id,
    )
    db.session.add(reading_list)
    db.session.commit()

    item = ReadingListItem(reading_list_id=reading_list.id, book_id=book.id, position=1)
    db.session.add(item)
    db.session.commit()

    rv = patron_client.get(f"/collections/{reading_list.public_id}")
    assert rv.status_code == 200
    assert b"Collected Shelf" in rv.data
    assert b"Collected Work" in rv.data


def test_collections_detail_returns_404_for_private_list(patron_client, db):
    creator = _make_user(email="collector3@test.com", role="admin")
    private_list = ReadingList(
        name="Private Shelf",
        is_public=False,
        created_by=creator.id,
    )
    db.session.add(private_list)
    db.session.commit()

    rv = patron_client.get(f"/collections/{private_list.public_id}")
    assert rv.status_code == 404
