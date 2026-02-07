"""Cover image auto-fetch service using the Open Library Covers API."""

import logging
import os

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds per HTTP request

COVER_API_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
SEARCH_API_URL = "https://openlibrary.org/search.json"


def _fetch_cover_by_isbn(isbn, dest_path):
    """Download a cover image by ISBN from Open Library.

    Returns True if a valid image was saved, False otherwise.
    """
    url = COVER_API_URL.format(isbn=isbn)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Cover fetch failed for ISBN %s: %s", isbn, exc)
        return False

    content_type = resp.headers.get("Content-Type", "")
    if "image" not in content_type:
        logger.info("ISBN %s returned non-image content type: %s", isbn, content_type)
        return False

    # Open Library returns a 1x1 pixel placeholder for missing covers.
    # Reject responses smaller than 1 KB as likely placeholders.
    if len(resp.content) < 1024:
        logger.info("ISBN %s returned a placeholder image (%d bytes), skipping.",
                     isbn, len(resp.content))
        return False

    with open(dest_path, "wb") as f:
        f.write(resp.content)

    return True


def _search_isbn_by_title_author(title, author):
    """Search Open Library for an ISBN using title and author.

    Returns the first ISBN found, or None.
    """
    params = {}
    if title:
        params["title"] = title
    if author:
        params["author"] = author

    if not params:
        return None

    # Limit results to 1 to keep the response small and fast.
    params["limit"] = 1
    params["fields"] = "isbn"

    try:
        resp = requests.get(SEARCH_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Open Library search failed for title=%r author=%r: %s",
                       title, author, exc)
        return None

    docs = data.get("docs", [])
    if not docs:
        return None

    isbn_list = docs[0].get("isbn", [])
    if isbn_list:
        return isbn_list[0]

    return None


def fetch_cover(isbn=None, title=None, author=None, public_id=None,
                cover_storage_dir=None):
    """Fetch a cover image from Open Library and save it locally.

    Tries ISBN-based lookup first. If no ISBN is provided (or the lookup
    fails), falls back to searching by title/author to discover an ISBN.

    Args:
        isbn: ISBN string (ISBN-10 or ISBN-13).
        title: Book title for fallback search.
        author: Book author for fallback search.
        public_id: The book's public_id, used as the filename stem.
        cover_storage_dir: Absolute path to the cover storage directory.

    Returns:
        The saved filename (e.g. "abc123def456.jpg") on success, or None
        on failure.
    """
    if not public_id or not cover_storage_dir:
        logger.error("fetch_cover called without public_id or cover_storage_dir")
        return None

    os.makedirs(cover_storage_dir, exist_ok=True)

    filename = f"{public_id}.jpg"
    dest_path = os.path.join(cover_storage_dir, filename)

    # Strategy 1: Direct ISBN lookup
    if isbn:
        clean_isbn = isbn.strip().replace("-", "")
        if _fetch_cover_by_isbn(clean_isbn, dest_path):
            logger.info("Cover fetched via ISBN %s -> %s", clean_isbn, filename)
            return filename

    # Strategy 2: Search by title/author to find an ISBN, then fetch cover
    discovered_isbn = _search_isbn_by_title_author(title, author)
    if discovered_isbn:
        if _fetch_cover_by_isbn(discovered_isbn, dest_path):
            logger.info("Cover fetched via discovered ISBN %s -> %s",
                        discovered_isbn, filename)
            return filename

    logger.info("No cover found for isbn=%r title=%r author=%r", isbn, title, author)
    return None
