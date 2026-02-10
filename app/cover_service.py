"""Cover image auto-fetch service using the Open Library Covers API.

Includes a Pillow-based fallback that generates a styled cover when no
online image is available.
"""

import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds per HTTP request

COVER_API_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
SEARCH_API_URL = "https://openlibrary.org/search.json"


def _fetch_cover_by_isbn(isbn, dest_path):
    """Download a cover image by ISBN from Open Library.

    Returns True if a valid image was saved, False otherwise.
    """
    time.sleep(0.5)  # Respect Open Library rate limits
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
        logger.info("ISBN %s returned a placeholder image (%d bytes), skipping.", isbn, len(resp.content))
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
        logger.warning("Open Library search failed for title=%r author=%r: %s", title, author, exc)
        return None

    docs = data.get("docs", [])
    if not docs:
        return None

    isbn_list = docs[0].get("isbn", [])
    if isbn_list:
        return isbn_list[0]

    return None


def fetch_cover(isbn=None, title=None, author=None, public_id=None, cover_storage_dir=None):
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
    if discovered_isbn and _fetch_cover_by_isbn(discovered_isbn, dest_path):
        logger.info("Cover fetched via discovered ISBN %s -> %s", discovered_isbn, filename)
        return filename

    # Strategy 3: Generate a cover with Pillow
    font_path = str(Path(__file__).resolve().parent / "static" / "fonts" / "CormorantGaramond-Regular.ttf")
    generated = generate_cover(
        title=title,
        author=author,
        public_id=public_id,
        cover_storage_dir=cover_storage_dir,
        font_path=font_path,
    )
    if generated:
        logger.info("Generated cover for title=%r -> %s", title, generated)
        return generated

    logger.info("No cover found for isbn=%r title=%r author=%r", isbn, title, author)
    return None


# ---------------------------------------------------------------------------
# Pillow-based cover generation
# ---------------------------------------------------------------------------

# Design constants
_COVER_WIDTH = 600
_COVER_HEIGHT = 900
_BG_COLOR = (255, 255, 255)  # white
_TITLE_COLOR = (107, 29, 42)  # burgundy          #6b1d2a
_AUTHOR_COLOR = (107, 29, 42)  # burgundy (same)
_GOLD_COLOR = (197, 153, 62)  # gold              #c5993e

_SEAL_PATH = Path(__file__).resolve().parent / "static" / "img" / "Sacred-Hearts-plain.png"


def _wrap_text(text, font, max_width, draw):
    """Word-wrap *text* so each line fits within *max_width* pixels.

    Returns a list of strings, one per line.
    """
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def generate_cover(title=None, author=None, public_id=None, cover_storage_dir=None, font_path=None):
    """Generate a styled book cover using Pillow.

    Creates a 600x900 white cover with burgundy title, gold separator,
    author name, and the library seal at the bottom.

    Returns the saved filename on success, or None on failure.
    """
    if not title or not public_id or not cover_storage_dir:
        return None

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow is not installed; cover generation unavailable.")
        return None

    os.makedirs(cover_storage_dir, exist_ok=True)

    img = Image.new("RGB", (_COVER_WIDTH, _COVER_HEIGHT), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    # --- Load fonts ---
    try:
        title_font = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default()
        author_font = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()
    except OSError:
        title_font = ImageFont.load_default()
        author_font = ImageFont.load_default()

    # --- Title ---
    max_text_width = _COVER_WIDTH - 100
    title_lines = _wrap_text(title, title_font, max_text_width, draw)
    y = 150
    for line in title_lines[:5]:  # cap at 5 lines
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_w = bbox[2] - bbox[0]
        x = (_COVER_WIDTH - line_w) // 2
        draw.text((x, y), line, fill=_TITLE_COLOR, font=title_font)
        y += (bbox[3] - bbox[1]) + 12

    # --- Gold separator line ---
    sep_y = y + 20
    draw.line([150, sep_y, _COVER_WIDTH - 150, sep_y], fill=_GOLD_COLOR, width=2)

    # --- Author ---
    if author:
        author_lines = _wrap_text(author, author_font, max_text_width, draw)
        y = sep_y + 30
        for line in author_lines[:3]:
            bbox = draw.textbbox((0, 0), line, font=author_font)
            line_w = bbox[2] - bbox[0]
            x = (_COVER_WIDTH - line_w) // 2
            draw.text((x, y), line, fill=_AUTHOR_COLOR, font=author_font)
            y += (bbox[3] - bbox[1]) + 8

    # --- Sacred Hearts image at bottom ---
    try:
        hearts = Image.open(str(_SEAL_PATH)).convert("RGBA")
        # Scale to fit width while preserving aspect ratio
        target_w = 300
        aspect = hearts.height / hearts.width
        target_h = int(target_w * aspect)
        hearts = hearts.resize((target_w, target_h), Image.LANCZOS)
        hearts_x = (_COVER_WIDTH - target_w) // 2
        hearts_y = _COVER_HEIGHT - target_h - 50
        img.paste(hearts, (hearts_x, hearts_y), hearts)
    except Exception as exc:
        logger.debug("Could not load Sacred Hearts image: %s", exc)

    # --- Save ---
    filename = f"{public_id}.jpg"
    dest_path = os.path.join(cover_storage_dir, filename)
    try:
        img.save(dest_path, "JPEG", quality=90)
        return filename
    except Exception as exc:
        logger.warning("Failed to save generated cover to %s: %s", dest_path, exc)
        return None
