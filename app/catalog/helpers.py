"""Helper utilities for the catalog module."""

from sqlalchemy import func

from ..models import Book, book_tags, db


def get_related_books(book, limit=4):
    """Return books that share the most tags with the given book.

    The results are ordered by the number of shared tags (descending),
    then alphabetically by title.  The source book is excluded from
    the results.  Only visible, non-disabled books are returned.
    """
    if not book.tags:
        return []

    tag_ids = [t.id for t in book.tags]

    # Count how many of the current book's tags each other book shares
    shared_count = (
        db.session.query(
            book_tags.c.book_id,
            func.count(book_tags.c.tag_id).label("shared"),
        )
        .filter(
            book_tags.c.tag_id.in_(tag_ids),
            book_tags.c.book_id != book.id,
        )
        .group_by(book_tags.c.book_id)
        .subquery()
    )

    related = (
        Book.query.join(shared_count, Book.id == shared_count.c.book_id)
        .filter(
            Book.is_visible == True,
            Book.is_disabled == False,
        )
        .order_by(shared_count.c.shared.desc(), Book.title.asc())
        .limit(limit)
        .all()
    )

    return related
