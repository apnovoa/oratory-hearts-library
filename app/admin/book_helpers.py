from ..models import Tag, db


def sync_tags(book, tags_text):
    """Synchronize a book's tags from a comma-separated string."""
    book.tags.clear()
    if not tags_text:
        return
    for raw in tags_text.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        tag = Tag.query.filter_by(name=name).first()
        if not tag:
            tag = Tag(name=name)
            db.session.add(tag)
        book.tags.append(tag)
