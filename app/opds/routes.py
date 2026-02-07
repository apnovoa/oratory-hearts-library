from datetime import datetime, timezone

from flask import Blueprint, Response, request, url_for
from flask_login import login_required

from ..models import Book, db

OPDS_PAGE_SIZE = 50

opds_bp = Blueprint("opds", __name__)


def _utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_xml_response(xml_string):
    return Response(
        xml_string,
        mimetype="application/atom+xml;profile=opds-catalog;kind=acquisition",
        headers={"Content-Type": "application/atom+xml; charset=utf-8"},
    )


@opds_bp.route("/opds/catalog.xml")
@login_required
def catalog():
    updated = _utcnow_iso()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:bibliotheca:oratorii:root</id>
  <title>Bibliotheca Oratorii Sacratissimorum Cordium</title>
  <subtitle>Library of the Oratory of the Most Sacred Hearts</subtitle>
  <updated>{updated}</updated>
  <author>
    <name>Bibliotheca Oratorii</name>
  </author>
  <link rel="self" href="/opds/catalog.xml" type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
  <link rel="start" href="/opds/catalog.xml" type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
  <entry>
    <title>All Books</title>
    <id>urn:bibliotheca:oratorii:all</id>
    <updated>{updated}</updated>
    <content type="text">Browse all books in the library collection.</content>
    <link rel="subsection" href="/opds/all.xml" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  </entry>
</feed>"""
    return _make_xml_response(xml)


def _escape_xml(text):
    """Escape special XML characters."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@opds_bp.route("/opds/all.xml")
@login_required
def all_books():
    page = max(1, request.args.get("page", 1, type=int))

    query = (
        Book.query.filter(
            Book.is_visible == True,   # noqa: E712
            Book.is_disabled == False,  # noqa: E712
        )
        .order_by(Book.title.asc())
    )
    pagination = query.paginate(page=page, per_page=OPDS_PAGE_SIZE, error_out=False)
    books = pagination.items

    updated = _utcnow_iso()
    entries = []
    for book in books:
        entry_updated = book.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if book.updated_at else updated
        summary = _escape_xml(book.description or "")
        title = _escape_xml(book.title)
        author = _escape_xml(book.author)
        lang = _escape_xml(book.language or "en")

        links = ""
        if book.cover_filename:
            links += f'  <link rel="http://opds-spec.org/image" href="/covers/{_escape_xml(book.cover_filename)}" type="image/jpeg"/>\n'
        if book.master_filename:
            links += f'  <link rel="http://opds-spec.org/acquisition" href="/catalog/{_escape_xml(book.public_id)}" type="text/html"/>\n'

        entry = f"""  <entry>
    <title>{title}</title>
    <id>urn:bibliotheca:oratorii:book:{_escape_xml(book.public_id)}</id>
    <updated>{entry_updated}</updated>
    <author>
      <name>{author}</name>
    </author>
    <dc:language>{lang}</dc:language>
    <summary>{summary}</summary>
    <link rel="alternate" href="/catalog/{_escape_xml(book.public_id)}" type="text/html"/>
{links}  </entry>"""
        entries.append(entry)

    # OPDS pagination links
    nav_links = ""
    if pagination.has_next:
        nav_links += f'  <link rel="next" href="/opds/all.xml?page={pagination.next_num}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>\n'
    if pagination.has_prev:
        nav_links += f'  <link rel="previous" href="/opds/all.xml?page={pagination.prev_num}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>\n'

    entries_xml = "\n".join(entries)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:bibliotheca:oratorii:all</id>
  <title>All Books — Bibliotheca Oratorii</title>
  <updated>{updated}</updated>
  <author>
    <name>Bibliotheca Oratorii</name>
  </author>
  <link rel="self" href="/opds/all.xml?page={page}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start" href="/opds/catalog.xml" type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
  <link rel="up" href="/opds/catalog.xml" type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
{nav_links}{entries_xml}
</feed>"""
    return _make_xml_response(xml)
