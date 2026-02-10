"""Tests for OPDS feed routes."""

from tests.conftest import _make_book


def test_opds_catalog_requires_login(client):
    rv = client.get("/opds/catalog.xml", follow_redirects=False)
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


def test_opds_catalog_returns_atom_feed(patron_client):
    rv = patron_client.get("/opds/catalog.xml")
    assert rv.status_code == 200
    assert rv.headers.get("Content-Type", "").startswith("application/atom+xml")
    assert b"<feed" in rv.data
    assert b"All Books" in rv.data


def test_opds_all_feed_escapes_xml_content(patron_client, db):
    _make_book(
        title='Title with <tag> & "quote"',
        author="Author & Co.",
    )
    rv = patron_client.get("/opds/all.xml")
    assert rv.status_code == 200
    assert b"Title with &lt;tag&gt; &amp; &quot;quote&quot;" in rv.data
    assert b"Author &amp; Co." in rv.data


def test_opds_all_feed_has_next_link_when_paginated(patron_client, db):
    for idx in range(51):
        _make_book(title=f"OPDS Book {idx}", author="OPDS Author")

    rv = patron_client.get("/opds/all.xml?page=1")
    assert rv.status_code == 200
    assert b'rel="next"' in rv.data
