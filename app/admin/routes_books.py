import csv
import io
import os
import uuid

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from .. import limiter
from ..audit import log_event
from ..cover_service import fetch_cover
from ..models import Book, Tag, db
from .book_helpers import sync_tags
from .common import _PDF_MAGIC, _is_valid_cover_image, _uploaded_file_size, admin_bp, admin_required
from .forms import BookForm, BookSearchForm

def _normalize_authors(raw):
    """Convert newline-separated author input to ``||``-delimited storage.

    Also accepts ``;`` as a delimiter (useful for CSV import).  Single-author
    values pass through unchanged.
    """
    if not raw:
        return raw
    # Prefer newlines, fall back to semicolons
    if "\n" in raw:
        parts = raw.split("\n")
    elif ";" in raw:
        parts = raw.split(";")
    else:
        return raw.strip()
    cleaned = [p.strip() for p in parts if p.strip()]
    return "||".join(cleaned) if cleaned else raw.strip()


# ── Books ──────────────────────────────────────────────────────────


@admin_bp.route("/books")
@admin_required
def books():
    form = BookSearchForm(request.args)
    page = request.args.get("page", 1, type=int)
    query = Book.query

    if form.q.data:
        search = f"%{form.q.data}%"
        query = query.filter(db.or_(Book.title.ilike(search), Book.author.ilike(search)))

    query = query.order_by(Book.title)
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        "admin/books.html",
        books=pagination.items,
        pagination=pagination,
        form=form,
    )


@admin_bp.route("/books/add", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def book_add():
    form = BookForm()
    if form.validate_on_submit():
        book = Book(
            title=form.title.data.strip(),
            author=_normalize_authors(form.author.data),
            description=form.description.data or None,
            language=form.language.data.strip(),
            publication_year=form.publication_year.data,
            isbn=form.isbn.data.strip() if form.isbn.data else None,
            other_identifier=form.other_identifier.data.strip() if form.other_identifier.data else None,
            dewey_decimal=form.dewey_decimal.data.strip() if form.dewey_decimal.data else None,
            loc_classification=form.loc_classification.data.strip() if form.loc_classification.data else None,
            owned_copies=form.owned_copies.data,
            watermark_mode=form.watermark_mode.data,
            loan_duration_override=form.loan_duration_override.data,
            is_visible=form.is_visible.data,
            is_disabled=form.is_disabled.data,
            restricted_access=form.restricted_access.data,
            imprimatur=form.imprimatur.data.strip() if form.imprimatur.data else None,
            nihil_obstat=form.nihil_obstat.data.strip() if form.nihil_obstat.data else None,
            ecclesiastical_approval_date=form.ecclesiastical_approval_date.data.strip()
            if form.ecclesiastical_approval_date.data
            else None,
        )

        # Handle master PDF upload
        master_file = form.master_file.data
        if master_file and master_file.filename:
            max_pdf_size = current_app.config.get("MAX_PDF_FILE_SIZE", 25 * 1024 * 1024)
            master_size = _uploaded_file_size(master_file)
            if master_size and master_size > max_pdf_size:
                flash(
                    f"Master PDF is too large (max {max_pdf_size // (1024 * 1024)} MB).",
                    "danger",
                )
                return render_template("admin/book_edit.html", form=form, book=None)
            header = master_file.read(5)
            master_file.seek(0)
            if header != _PDF_MAGIC:
                flash("The uploaded file does not appear to be a valid PDF.", "danger")
                return render_template("admin/book_edit.html", form=form, book=None)
            filename = f"{uuid.uuid4().hex}_{secure_filename(master_file.filename)}"
            master_file.save(os.path.join(current_app.config["MASTER_STORAGE"], filename))
            book.master_filename = filename

        # Handle cover image upload
        cover_file = form.cover_file.data
        if cover_file and cover_file.filename:
            max_cover_size = current_app.config.get("MAX_COVER_FILE_SIZE", 10 * 1024 * 1024)
            cover_size = _uploaded_file_size(cover_file)
            if cover_size and cover_size > max_cover_size:
                flash(
                    f"Cover image is too large (max {max_cover_size // (1024 * 1024)} MB).",
                    "danger",
                )
                return render_template("admin/book_edit.html", form=form, book=None)
            if not _is_valid_cover_image(cover_file):
                flash("The uploaded cover image is not a valid JPEG, PNG, or WebP file.", "danger")
                return render_template("admin/book_edit.html", form=form, book=None)
            filename = f"{uuid.uuid4().hex}_{secure_filename(cover_file.filename)}"
            cover_file.save(os.path.join(current_app.config["COVER_STORAGE"], filename))
            book.cover_filename = filename

        # Handle tags
        sync_tags(book, form.tags_text.data)

        db.session.add(book)
        db.session.commit()

        log_event("book_created", target_type="book", target_id=book.id, detail=f"Created book: {book.title}")
        flash("Book added successfully.", "success")
        return redirect(url_for("admin.books"))

    return render_template("admin/book_edit.html", form=form, book=None)


@admin_bp.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def book_edit(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    form = BookForm(obj=book)
    if request.method == "GET":
        form.tags_text.data = ", ".join(t.name for t in book.tags)
        # Show ||‑delimited authors as one-per-line in the textarea
        if book.author:
            form.author.data = book.author.replace("||", "\n")

    if form.validate_on_submit():
        book.title = form.title.data.strip()
        book.author = _normalize_authors(form.author.data)
        book.description = form.description.data or None
        book.language = form.language.data.strip()
        book.publication_year = form.publication_year.data
        book.isbn = form.isbn.data.strip() if form.isbn.data else None
        book.other_identifier = form.other_identifier.data.strip() if form.other_identifier.data else None
        book.dewey_decimal = form.dewey_decimal.data.strip() if form.dewey_decimal.data else None
        book.loc_classification = form.loc_classification.data.strip() if form.loc_classification.data else None
        book.owned_copies = form.owned_copies.data
        book.watermark_mode = form.watermark_mode.data
        book.loan_duration_override = form.loan_duration_override.data
        book.is_visible = form.is_visible.data
        book.is_disabled = form.is_disabled.data
        book.restricted_access = form.restricted_access.data
        book.imprimatur = form.imprimatur.data.strip() if form.imprimatur.data else None
        book.nihil_obstat = form.nihil_obstat.data.strip() if form.nihil_obstat.data else None
        book.ecclesiastical_approval_date = (
            form.ecclesiastical_approval_date.data.strip() if form.ecclesiastical_approval_date.data else None
        )

        # Handle master PDF upload
        master_file = form.master_file.data
        if master_file and master_file.filename:
            max_pdf_size = current_app.config.get("MAX_PDF_FILE_SIZE", 25 * 1024 * 1024)
            master_size = _uploaded_file_size(master_file)
            if master_size and master_size > max_pdf_size:
                flash(
                    f"Master PDF is too large (max {max_pdf_size // (1024 * 1024)} MB).",
                    "danger",
                )
                return render_template("admin/book_edit.html", form=form, book=book)
            header = master_file.read(5)
            master_file.seek(0)
            if header != _PDF_MAGIC:
                flash("The uploaded file does not appear to be a valid PDF.", "danger")
                return render_template("admin/book_edit.html", form=form, book=book)
            filename = f"{uuid.uuid4().hex}_{secure_filename(master_file.filename)}"
            master_file.save(os.path.join(current_app.config["MASTER_STORAGE"], filename))
            book.master_filename = filename

        # Handle cover image upload
        cover_file = form.cover_file.data
        if cover_file and cover_file.filename:
            max_cover_size = current_app.config.get("MAX_COVER_FILE_SIZE", 10 * 1024 * 1024)
            cover_size = _uploaded_file_size(cover_file)
            if cover_size and cover_size > max_cover_size:
                flash(
                    f"Cover image is too large (max {max_cover_size // (1024 * 1024)} MB).",
                    "danger",
                )
                return render_template("admin/book_edit.html", form=form, book=book)
            if not _is_valid_cover_image(cover_file):
                flash("The uploaded cover image is not a valid JPEG, PNG, or WebP file.", "danger")
                return render_template("admin/book_edit.html", form=form, book=book)
            filename = f"{uuid.uuid4().hex}_{secure_filename(cover_file.filename)}"
            cover_file.save(os.path.join(current_app.config["COVER_STORAGE"], filename))
            book.cover_filename = filename

        # Handle tags
        sync_tags(book, form.tags_text.data)

        db.session.commit()

        log_event("book_updated", target_type="book", target_id=book.id, detail=f"Updated book: {book.title}")
        flash("Book updated successfully.", "success")
        return redirect(url_for("admin.books"))

    return render_template("admin/book_edit.html", form=form, book=book)


@admin_bp.route("/books/<int:book_id>/toggle-visibility", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def book_toggle_visibility(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)
    book.is_visible = not book.is_visible
    db.session.commit()
    log_event(
        "book_visibility_toggled", target_type="book", target_id=book.id, detail=f"Visibility set to {book.is_visible}"
    )
    flash(f'Visibility {"enabled" if book.is_visible else "disabled"} for "{book.title}".', "success")
    return redirect(url_for("admin.books"))


@admin_bp.route("/books/<int:book_id>/toggle-disabled", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def book_toggle_disabled(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)
    book.is_disabled = not book.is_disabled
    db.session.commit()
    log_event(
        "book_disabled_toggled", target_type="book", target_id=book.id, detail=f"Disabled set to {book.is_disabled}"
    )
    flash(f'Book "{book.title}" {"disabled" if book.is_disabled else "enabled"}.', "success")
    return redirect(url_for("admin.books"))


@admin_bp.route("/books/<int:book_id>/fetch-cover", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def book_fetch_cover(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    cover_dir = current_app.config["COVER_STORAGE"]
    filename = fetch_cover(
        isbn=book.isbn,
        title=book.title,
        author=book.author,
        public_id=book.public_id,
        cover_storage_dir=cover_dir,
    )

    if filename:
        book.cover_filename = filename
        db.session.commit()
        log_event(
            "cover_auto_fetched", target_type="book", target_id=book.id, detail=f"Auto-fetched cover for: {book.title}"
        )
        flash("Cover image fetched successfully from Open Library.", "success")
    else:
        flash("Could not find a cover image on Open Library. Try uploading one manually.", "warning")

    return redirect(url_for("admin.book_edit", book_id=book.id))


# ── CSV Import ────────────────────────────────────────────────────


@admin_bp.route("/books/import-csv", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def books_import_csv():
    if request.method == "GET":
        return render_template("admin/import_csv.html")

    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Please select a CSV file to upload.", "danger")
        return render_template("admin/import_csv.html")

    if not file.filename.lower().endswith(".csv"):
        flash("Only CSV files are accepted.", "danger")
        return render_template("admin/import_csv.html")

    try:
        raw = file.stream.read()
        if len(raw) > 10 * 1024 * 1024:  # 10 MB limit for CSV
            flash("CSV file is too large (max 10 MB).", "danger")
            return render_template("admin/import_csv.html")
        stream = io.StringIO(raw.decode("utf-8-sig"))
        reader = csv.DictReader(stream)
    except (OSError, UnicodeDecodeError, csv.Error):
        flash("Could not read the CSV file. Please check the encoding (UTF-8 expected).", "danger")
        return render_template("admin/import_csv.html")

    imported = 0
    skipped = 0
    errors = []

    MAX_CSV_ROWS = 5000
    row_count = 0

    for row_num, row in enumerate(reader, start=2):
        if row_count >= MAX_CSV_ROWS:
            flash(f"Import limited to {MAX_CSV_ROWS} rows. First {MAX_CSV_ROWS} rows were imported.", "warning")
            break
        row_count += 1

        title = (row.get("title") or "").strip()[:500]
        author = _normalize_authors((row.get("author") or "").strip()[:500])
        description = (row.get("description") or "").strip()[:5000]
        isbn = (row.get("isbn") or "").strip()[:20]
        language = (row.get("language") or "en").strip()[:10]

        if not title or not author:
            errors.append(f"Row {row_num}: missing title or author — skipped.")
            skipped += 1
            continue

        pub_year = None
        if row.get("publication_year"):
            try:
                pub_year = int(row["publication_year"])
                if pub_year < 0 or pub_year > 2100:
                    pub_year = None
            except (ValueError, TypeError):
                pub_year = None

        book = Book(
            title=title,
            author=author,
            description=description or None,
            language=language,
            publication_year=pub_year,
            isbn=isbn or None,
        )

        # Handle tags
        tags_text = (row.get("tags") or "").strip()
        if tags_text:
            for raw_tag in tags_text.split(","):
                tag_name = raw_tag.strip().lower()
                if not tag_name:
                    continue
                tag = Tag.query.filter_by(name=tag_name).first()
                if not tag:
                    tag = Tag(name=tag_name)
                    db.session.add(tag)
                book.tags.append(tag)

        db.session.add(book)
        imported += 1

    try:
        db.session.commit()
        log_event("csv_import", detail=f"Imported {imported} books, skipped {skipped}")
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error(f"CSV import database error: {exc}")
        flash("A database error occurred during import. Please check the server logs for details.", "danger")
        return render_template("admin/import_csv.html")

    if errors:
        for err in errors[:10]:
            flash(err, "warning")
        if len(errors) > 10:
            flash(f"... and {len(errors) - 10} more warnings.", "warning")

    flash(f"Import complete: {imported} books imported, {skipped} skipped.", "success")
    return redirect(url_for("admin.books"))
