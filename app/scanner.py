"""Metadata extraction pipeline and background scan thread for bulk PDF import.

Processes PDF files placed in storage/staging/, extracts metadata from embedded
PDF properties, filenames, and the Open Library API, then creates StagedBook
records for admin review.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import requests
from sqlalchemy.exc import SQLAlchemyError

try:
    import fcntl
except ImportError:  # pragma: no cover - platform-specific fallback
    fcntl = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File-based scan progress (shared across gunicorn workers)
# ---------------------------------------------------------------------------

_PROGRESS_DEFAULTS = {
    "running": False,
    "batch_id": None,
    "total": 0,
    "processed": 0,
    "current_file": "",
    "errors": 0,
    "started_at": None,
    "finished_at": None,
}

_progress_lock = threading.Lock()
_SCAN_FILE_TIMEOUT_DEFAULT_SECONDS = 300


def _progress_file_path():
    """Return path to the progress JSON file inside the staging directory's parent."""
    from flask import current_app

    try:
        storage = Path(current_app.config["STAGING_STORAGE"]).parent
    except RuntimeError:
        storage = Path("storage")
    storage.mkdir(parents=True, exist_ok=True)
    return storage / ".scan_progress.json"


def _scan_lock_file_path():
    """Return path to the cross-process scan lock file."""
    return _progress_file_path().with_suffix(".lock")


def _read_progress(path=None):
    """Read progress from disk. Returns defaults if file missing/corrupt."""
    if path is None:
        path = _progress_file_path()
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_PROGRESS_DEFAULTS)


def _write_progress(data, path=None):
    """Atomically write progress to disk."""
    if path is None:
        path = _progress_file_path()
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, str(path))


def _acquire_scan_lock():
    """Acquire cross-process scan lock. Returns fd on success, None if locked."""
    if fcntl is None:
        return -1

    lock_path = _scan_lock_file_path()
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _release_scan_lock(fd):
    """Release cross-process scan lock."""
    if fd is None:
        return
    if fd == -1:
        return
    if fcntl is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def get_scan_progress():
    """Return a snapshot of the current scan progress."""
    return _read_progress()


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------


def _compute_sha256(filepath):
    """Compute the SHA-256 hex digest of a file, reading in 8 KB chunks."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# PDF embedded metadata extraction
# ---------------------------------------------------------------------------

_ISBN_RE = re.compile(r"(?:ISBN[-:]?\s*)?(\d{9}[\dXx]|\d{13})", re.IGNORECASE)


def _extract_pdf_metadata(filepath):
    """Read embedded metadata from a PDF via pikepdf.

    Returns a dict with keys: title, author, subject, keywords, isbn.
    All values are strings or None.
    """
    result = {
        "title": None,
        "author": None,
        "subject": None,
        "keywords": None,
        "isbn": None,
    }

    try:
        import pikepdf
    except ImportError:
        logger.warning("pikepdf is not installed; skipping embedded PDF metadata.")
        return result

    try:
        with pikepdf.open(filepath) as pdf:
            meta = pdf.open_metadata()
            # Dublin Core / XMP fields
            dc_title = str(meta.get("dc:title", "")).strip() or None
            dc_creator = str(meta.get("dc:creator", "")).strip() or None
            dc_subject = str(meta.get("dc:subject", "")).strip() or None
            dc_description = str(meta.get("dc:description", "")).strip() or None

            # Legacy Info dict fields as fallback
            info = pdf.docinfo
            info_title = str(info.get("/Title", "")).strip() or None if info else None
            info_author = str(info.get("/Author", "")).strip() or None if info else None
            info_subject = str(info.get("/Subject", "")).strip() or None if info else None
            info_keywords = str(info.get("/Keywords", "")).strip() or None if info else None

            result["title"] = dc_title or info_title
            result["author"] = dc_creator or info_author
            result["subject"] = dc_subject or dc_description or info_subject
            result["keywords"] = info_keywords

            # Hunt for ISBN in subject, keywords, and description fields
            searchable = " ".join(s for s in [result["subject"], result["keywords"], dc_description] if s)
            isbn_match = _ISBN_RE.search(searchable)
            if isbn_match:
                result["isbn"] = isbn_match.group(1)

    except (pikepdf.PdfError, OSError, ValueError, RuntimeError) as exc:
        logger.warning("Failed to read PDF metadata from %s: %s", filepath, exc)

    return result


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

_YEAR_PAREN_RE = re.compile(r"\s*\(\d{4}\)\s*")
_YEAR_BRACKET_RE = re.compile(r"\s*\[\d{4}\]\s*")


def _looks_like_name(text):
    """Heuristic: does the string look like a person's name?

    True if it contains 2-4 space-separated capitalised words and no very
    long word (> 20 chars, likely a title fragment).
    """
    parts = text.split()
    if not 2 <= len(parts) <= 4:
        return False
    if any(len(p) > 20 for p in parts):
        return False
    return all(p[0].isupper() for p in parts if p)


def _clean_text(text):
    """Normalise underscores, excess whitespace, and stray punctuation."""
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .-,")


def _parse_filename(filename):
    """Extract title and author from common PDF filename patterns.

    Returns a dict with keys: title, author (both may be None).
    """
    stem = Path(filename).stem
    stem = _clean_text(stem)

    # Strip year patterns like (1962) or [2003]
    stem = _YEAR_PAREN_RE.sub(" ", stem)
    stem = _YEAR_BRACKET_RE.sub(" ", stem)
    stem = stem.strip()

    title = None
    author = None

    # Pattern: "Title (Author)"
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", stem)
    if m:
        title = _clean_text(m.group(1))
        candidate = _clean_text(m.group(2))
        if _looks_like_name(candidate):
            author = candidate
        return {"title": title, "author": author}

    # Pattern: "Title by Author"
    m = re.match(r"^(.+?)\s+by\s+(.+)$", stem, re.IGNORECASE)
    if m:
        title = _clean_text(m.group(1))
        author = _clean_text(m.group(2))
        return {"title": title, "author": author}

    # Pattern: "Part1 - Part2"  (could be Author - Title or Title - Author)
    if " - " in stem:
        parts = stem.split(" - ", 1)
        left = _clean_text(parts[0])
        right = _clean_text(parts[1])

        if _looks_like_name(left) and not _looks_like_name(right):
            # Author - Title
            author = left
            title = right
        elif _looks_like_name(right) and not _looks_like_name(left):
            # Title - Author
            title = left
            author = right
        else:
            # Ambiguous; treat left as author, right as title (common convention)
            author = left
            title = right

        return {"title": title, "author": author}

    # Fallback: entire stem is the title
    title = stem if stem else None
    return {"title": title, "author": None}


# ---------------------------------------------------------------------------
# Open Library enrichment
# ---------------------------------------------------------------------------

_OL_SEARCH_URL = "https://openlibrary.org/search.json"
_OL_ISBN_URL = "https://openlibrary.org/isbn/{isbn}.json"
_OL_TIMEOUT = 10  # seconds


def _lookup_openlibrary(isbn=None, title=None, author=None):
    """Query the Open Library API for book metadata.

    Returns a dict with keys: title, author, description, year, language,
    subjects, isbn.  All values may be None on failure.
    """
    result = {
        "title": None,
        "author": None,
        "description": None,
        "year": None,
        "language": None,
        "subjects": None,
        "isbn": None,
    }

    # Respect Open Library rate limits
    time.sleep(0.5)

    # --- ISBN-based lookup (more precise) ---
    if isbn:
        clean = isbn.strip().replace("-", "")
        try:
            resp = requests.get(
                _OL_ISBN_URL.format(isbn=clean),
                timeout=_OL_TIMEOUT,
                allow_redirects=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                result["title"] = data.get("title")
                authors = data.get("authors", [])
                if authors:
                    # Each author entry has a {"key": "/authors/..."} reference;
                    # the full_title field sometimes contains the author name in
                    # the search endpoint, but not here.  Fall through to search
                    # below to fill in author name.
                    pass
                desc = data.get("description")
                if isinstance(desc, dict):
                    desc = desc.get("value")
                result["description"] = desc if isinstance(desc, str) else None
                result["isbn"] = clean
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Open Library ISBN lookup failed for %s: %s", isbn, exc)

    # --- Search-based lookup (title / author) ---
    params = {"limit": 1, "fields": "title,author_name,first_publish_year,subject,isbn,description"}
    if isbn:
        params["q"] = f"isbn:{isbn.strip().replace('-', '')}"
    elif title:
        params["q"] = title
        if author:
            params["author"] = author
    else:
        return result

    try:
        resp = requests.get(_OL_SEARCH_URL, params=params, timeout=_OL_TIMEOUT, allow_redirects=False)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Open Library search failed: %s", exc)
        return result

    docs = data.get("docs", [])
    if not docs:
        return result

    doc = docs[0]

    if not result["title"]:
        result["title"] = doc.get("title")
    author_names = doc.get("author_name", [])
    if author_names:
        result["author"] = author_names[0]
    result["year"] = doc.get("first_publish_year")

    subjects = doc.get("subject", [])
    if subjects:
        result["subjects"] = ", ".join(subjects[:10])

    isbn_list = doc.get("isbn", [])
    if isbn_list and not result["isbn"]:
        result["isbn"] = isbn_list[0]

    desc = doc.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value")
    if isinstance(desc, str) and not result["description"]:
        result["description"] = desc

    return result


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _compute_confidence(metadata_sources, has_title, has_author, has_isbn):
    """Return a confidence level: 'high', 'medium', or 'low'.

    - high:   title + author + at least one enrichment source (openlibrary,
              ai_claude, or pdf_metadata with isbn)
    - medium: title + author from any source
    - low:    only filename-derived title, or missing fields
    """
    sources = set(s.strip() for s in (metadata_sources or "").split(",") if s.strip())

    if has_title and has_author:
        enriched = "ai_claude" in sources or "openlibrary" in sources or ("pdf_metadata" in sources and has_isbn)
        if enriched:
            return "high"
        return "medium"

    return "low"


# ---------------------------------------------------------------------------
# Single-file pipeline
# ---------------------------------------------------------------------------


def _scan_single_file(filepath, batch_id, app):
    """Process one PDF file through the full metadata extraction pipeline.

    Creates a StagedBook record on success.  Returns True on success, False on
    error.
    """
    from .cover_service import fetch_cover
    from .models import Book, StagedBook, db

    filename = os.path.basename(filepath)

    try:
        # ---- Hash & duplicate-staged check ----
        file_hash = _compute_sha256(filepath)

        existing = StagedBook.query.filter_by(file_hash=file_hash).first()
        if existing:
            logger.info("Skipping %s — already staged (hash match, id=%d).", filename, existing.id)
            return True  # not an error, just a skip

        file_size = os.path.getsize(filepath)

        # ---- Extract metadata from PDF ----
        pdf_meta = _extract_pdf_metadata(filepath)

        # ---- Parse filename ----
        fn_meta = _parse_filename(filename)

        # ---- Merge: prefer PDF metadata, filename as fallback ----
        merged_title = pdf_meta["title"] or fn_meta["title"]
        merged_author = pdf_meta["author"] or fn_meta["author"]
        merged_isbn = pdf_meta["isbn"]
        merged_description = None

        sources = []
        if pdf_meta["title"] or pdf_meta["author"] or pdf_meta["isbn"]:
            sources.append("pdf_metadata")
        if fn_meta["title"] or fn_meta["author"]:
            sources.append("filename")

        # ---- AI metadata extraction (Claude) ----
        from .ai_service import extract_metadata_with_ai

        ai_meta = extract_metadata_with_ai(filepath, app.config)

        if ai_meta and any(v for v in ai_meta.values()):
            sources.append("ai_claude")

        # ---- Open Library enrichment ----
        # Use AI-improved title/author/isbn for the Open Library lookup
        lookup_title = (ai_meta or {}).get("title") or merged_title
        lookup_author = (ai_meta or {}).get("author") or merged_author
        lookup_isbn = (ai_meta or {}).get("isbn") or merged_isbn

        ol_meta = {
            "title": None,
            "author": None,
            "description": None,
            "year": None,
            "language": None,
            "subjects": None,
            "isbn": None,
        }

        if lookup_isbn:
            ol_meta = _lookup_openlibrary(isbn=lookup_isbn)
        elif lookup_title and lookup_author:
            ol_meta = _lookup_openlibrary(title=lookup_title, author=lookup_author)
        elif lookup_title:
            ol_meta = _lookup_openlibrary(title=lookup_title)

        if any(v for v in ol_meta.values()):
            sources.append("openlibrary")

        # ---- Merge: AI > Open Library > PDF metadata > filename ----
        ai = ai_meta or {}
        final_title = ai.get("title") or ol_meta["title"] or merged_title
        final_author = ai.get("author") or ol_meta["author"] or merged_author
        final_isbn = ai.get("isbn") or ol_meta["isbn"] or merged_isbn
        final_year = ai.get("publication_year") or ol_meta["year"]
        final_language = ai.get("language") or ol_meta["language"]

        # Description: prefer Open Library (editorially written), AI as fallback
        final_description = ol_meta["description"] or ai.get("description") or merged_description

        # Tags: AI first (content-specific), then Open Library subjects, capped at 15
        ai_tags = ai.get("tags") or ""
        ol_subjects = ol_meta["subjects"] or ""
        all_tags = [t.strip() for t in (ai_tags + ", " + ol_subjects).split(",") if t.strip()]
        seen = set()
        unique_tags = []
        for tag in all_tags:
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                unique_tags.append(tag)
        final_subjects = ", ".join(unique_tags[:15]) if unique_tags else None

        # ---- Cover fetch ----
        cover_filename = None
        if final_title or final_isbn:
            # Generate a temporary public_id for the staged book cover
            cover_public_id = uuid4().hex
            cover_dir = app.config.get("COVER_STORAGE", "storage/covers")
            cover_filename = fetch_cover(
                isbn=final_isbn,
                title=final_title,
                author=final_author,
                public_id=cover_public_id,
                cover_storage_dir=cover_dir,
            )

        # ---- Duplicate detection against existing Book records ----
        duplicate_of_book_id = None
        duplicate_type = None

        if final_isbn:
            dup_book = Book.query.filter_by(isbn=final_isbn).first()
            if dup_book:
                duplicate_of_book_id = dup_book.id
                duplicate_type = "isbn"

        # ---- Confidence scoring ----
        metadata_sources_str = ",".join(sources)
        confidence = _compute_confidence(
            metadata_sources_str,
            has_title=bool(final_title),
            has_author=bool(final_author),
            has_isbn=bool(final_isbn),
        )

        # ---- Public domain assessment (from AI) ----
        pd_confidence = ai.get("public_domain_confidence")
        pd_reasoning = ai.get("public_domain_reasoning")

        # ---- Create StagedBook record ----
        staged = StagedBook(
            original_filename=filename,
            file_size=file_size,
            file_hash=file_hash,
            title=final_title,
            author=final_author,
            description=final_description,
            language=final_language or "en",
            publication_year=final_year,
            isbn=final_isbn,
            tags_text=final_subjects,
            public_domain_confidence=pd_confidence,
            public_domain_reasoning=pd_reasoning,
            metadata_sources=metadata_sources_str,
            cover_filename=cover_filename,
            confidence=confidence,
            status="pending",
            duplicate_of_book_id=duplicate_of_book_id,
            duplicate_type=duplicate_type,
            scan_batch_id=batch_id,
            scanned_at=datetime.now(UTC),
        )

        db.session.add(staged)
        db.session.commit()

        logger.info("Staged %s (confidence=%s, sources=%s)", filename, confidence, metadata_sources_str)
        return True

    except Exception as exc:
        # Keep broad catch here intentionally: one malformed file must not
        # crash the entire batch scan.
        db.session.rollback()
        logger.error("Error scanning %s: %s", filename, exc, exc_info=True)

        # Attempt to record the error in a StagedBook row so it is visible
        # in the admin panel.
        try:
            staged = StagedBook(
                original_filename=filename,
                file_size=os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                file_hash=file_hash if "file_hash" in dir() else "",
                status="error",
                error_message=str(exc)[:2000],
                scan_batch_id=batch_id,
                scanned_at=datetime.now(UTC),
            )
            db.session.add(staged)
            db.session.commit()
        except (SQLAlchemyError, OSError, RuntimeError, ValueError):
            # Best-effort error visibility: if this write fails we still
            # return False so the batch can continue.
            logger.exception("Failed to persist scan error row for %s", filename)
            db.session.rollback()

        return False


# ---------------------------------------------------------------------------
# Background scan worker
# ---------------------------------------------------------------------------


def _is_valid_pdf(path):
    """Check that a file starts with the PDF magic bytes (%PDF-)."""
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _scan_single_file_with_timeout(filepath, batch_id, app, timeout_seconds):
    """Run one file scan with a watchdog timeout.

    The scan executes in a daemon thread so a hung parser/API call does not
    stall the batch indefinitely.
    """
    result = {"success": False}
    finished = threading.Event()

    def _target():
        with app.app_context():
            try:
                result["success"] = _scan_single_file(filepath, batch_id, app)
            except (SQLAlchemyError, OSError, RuntimeError, ValueError):
                # Isolate per-file operational failures so the batch continues.
                logger.exception("Unhandled scanner crash for %s", filepath)
                result["success"] = False
            finally:
                finished.set()

    worker = threading.Thread(
        target=_target,
        daemon=True,
        name=f"scan-file-{Path(filepath).stem[:16]}",
    )
    worker.start()

    if not finished.wait(timeout=timeout_seconds):
        logger.error("Timed out scanning %s after %s seconds", os.path.basename(filepath), timeout_seconds)
        return False

    return result["success"]


def _scan_worker(staging_dir, batch_id, app, lock_fd=None):
    """Background thread target that processes all PDFs in the staging dir."""
    with app.app_context():
        progress_path = _progress_file_path()

        try:
            pdf_files = sorted(
                p
                for p in Path(staging_dir).iterdir()
                if p.is_file() and p.suffix.lower() == ".pdf" and _is_valid_pdf(p)
            )

            progress = _read_progress(progress_path)
            progress["total"] = len(pdf_files)
            _write_progress(progress, progress_path)

            logger.info("Scan batch %s started: %d PDF(s) in %s", batch_id, len(pdf_files), staging_dir)

            configured_timeout = app.config.get("SCAN_FILE_TIMEOUT_SECONDS", _SCAN_FILE_TIMEOUT_DEFAULT_SECONDS)
            try:
                timeout_seconds = max(30, int(configured_timeout))
            except (TypeError, ValueError):
                timeout_seconds = _SCAN_FILE_TIMEOUT_DEFAULT_SECONDS
                logger.warning(
                    "Invalid SCAN_FILE_TIMEOUT_SECONDS value %r; defaulting to %s seconds.",
                    configured_timeout,
                    timeout_seconds,
                )

            for filepath in pdf_files:
                progress = _read_progress(progress_path)
                progress["current_file"] = filepath.name
                _write_progress(progress, progress_path)

                success = _scan_single_file_with_timeout(str(filepath), batch_id, app, timeout_seconds)

                progress = _read_progress(progress_path)
                progress["processed"] += 1
                if not success:
                    progress["errors"] += 1
                _write_progress(progress, progress_path)
        except (SQLAlchemyError, OSError, RuntimeError, ValueError):
            # Keep worker alive long enough to publish final progress + release lock.
            logger.exception("Scan batch %s failed with unhandled error.", batch_id)
        finally:
            try:
                progress = _read_progress(progress_path)
                progress["finished_at"] = datetime.now(UTC).isoformat()
                progress["running"] = False
                progress["current_file"] = ""
                _write_progress(progress, progress_path)
            finally:
                _release_scan_lock(lock_fd)

        logger.info(
            "Scan batch %s finished: %d processed, %d errors.", batch_id, progress["processed"], progress["errors"]
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def start_scan(app):
    """Kick off a background scan of the staging directory.

    Returns the batch_id string on success, or False if a scan is already
    running.
    """
    with app.app_context():
        progress_path = _progress_file_path()
        lock_fd = _acquire_scan_lock()
        if lock_fd is None:
            logger.warning("Scan already in progress (lock is held by another process).")
            return False
        progress = _read_progress(progress_path)

        if progress["running"]:
            if lock_fd == -1:
                logger.warning("Scan already in progress (batch %s).", progress["batch_id"])
                _release_scan_lock(lock_fd)
                return False
            logger.warning(
                "Detected stale scan state (batch %s marked running but no lock held). Resetting state.",
                progress["batch_id"],
            )

        batch_id = uuid4().hex

        progress = dict(_PROGRESS_DEFAULTS)
        progress["running"] = True
        progress["batch_id"] = batch_id
        progress["started_at"] = datetime.now(UTC).isoformat()
        _write_progress(progress, progress_path)

    staging_dir = app.config.get("STAGING_STORAGE", "storage/staging")
    os.makedirs(staging_dir, exist_ok=True)

    thread = threading.Thread(
        target=_scan_worker,
        args=(staging_dir, batch_id, app, lock_fd),
        daemon=True,
        name=f"scan-{batch_id[:8]}",
    )
    try:
        thread.start()
    except RuntimeError:
        # Thread startup can fail if Python refuses to create a new thread.
        _release_scan_lock(lock_fd)
        raise

    logger.info("Launched scan thread for batch %s (staging: %s).", batch_id, staging_dir)
    return batch_id
