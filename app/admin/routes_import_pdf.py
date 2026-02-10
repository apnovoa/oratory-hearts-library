import os
import shutil
import uuid
from pathlib import Path

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from .. import limiter
from ..audit import log_event
from ..cover_service import fetch_cover
from ..models import Book, StagedBook, db
from .book_helpers import sync_tags
from .common import _PDF_MAGIC, _uploaded_file_size, _utcnow, admin_bp, admin_required
from .forms import StagedBookForm
from .routes_books import _normalize_authors

# ── Bulk PDF Import ──────────────────────────────────────────────


@admin_bp.route("/import-pdf")
@admin_required
def import_pdf_dashboard():
    staging_dir = Path(current_app.config["STAGING_STORAGE"])

    # Count PDFs in staging directory (case-insensitive)
    pdf_count = 0
    total_size = 0
    if staging_dir.is_dir():
        for f in staging_dir.iterdir():
            if f.is_file() and f.suffix.lower() == ".pdf":
                pdf_count += 1
                total_size += f.stat().st_size

    # Count StagedBook records by status
    status_counts = {
        "pending": StagedBook.query.filter_by(status="pending").count(),
        "approved": StagedBook.query.filter_by(status="approved").count(),
        "dismissed": StagedBook.query.filter_by(status="dismissed").count(),
        "error": StagedBook.query.filter_by(status="error").count(),
    }

    # Get scan progress
    from ..scanner import get_scan_progress

    scan_progress = get_scan_progress()

    return render_template(
        "admin/import_pdf.html",
        staging_file_count=pdf_count,
        staging_size_gb=total_size / (1024**3),
        pending_count=status_counts["pending"],
        approved_count=status_counts["approved"],
        dismissed_count=status_counts["dismissed"],
        error_count=status_counts["error"],
        scan_progress=scan_progress,
        config=current_app.config,
    )


@admin_bp.route("/import-pdf/upload", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_upload():
    files = request.files.getlist("pdf_files")
    files = [f for f in files if f and f.filename]
    if not files:
        flash("No files selected.", "warning")
        return redirect(url_for("admin.import_pdf_dashboard"))

    max_files = current_app.config.get("MAX_FILES_PER_UPLOAD", 20)
    if len(files) > max_files:
        flash(f"You can upload at most {max_files} files per request.", "danger")
        return redirect(url_for("admin.import_pdf_dashboard"))

    max_pdf_size = current_app.config.get("MAX_PDF_FILE_SIZE", 25 * 1024 * 1024)
    staging_dir = Path(current_app.config["STAGING_STORAGE"])
    uploaded, skipped = 0, []

    for f in files:
        size = _uploaded_file_size(f)
        if size and size > max_pdf_size:
            skipped.append(f"{f.filename} (exceeds {max_pdf_size // (1024 * 1024)} MB limit)")
            continue

        # Validate PDF magic bytes
        header = f.read(5)
        f.seek(0)
        if header != _PDF_MAGIC:
            skipped.append(f"{f.filename} (not a valid PDF)")
            continue

        # Preserve original filename (scanner uses it for metadata parsing)
        safe_name = secure_filename(f.filename)
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"

        # Handle filename collisions
        dest = staging_dir / safe_name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 2
            while dest.exists():
                dest = staging_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        f.save(dest)
        uploaded += 1

    if uploaded:
        flash(f"Uploaded {uploaded} PDF(s) to staging.", "success")
        log_event("pdf_upload", detail=f"Uploaded {uploaded} file(s) via GUI")
    if skipped:
        flash(f"Skipped {len(skipped)} file(s): {'; '.join(skipped)}", "warning")

    return redirect(url_for("admin.import_pdf_dashboard"))


@admin_bp.route("/import-pdf/scan", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_scan():
    from ..scanner import start_scan

    result = start_scan(current_app._get_current_object())
    if result is False:
        flash("A scan is already in progress.", "warning")
    else:
        batch_id = result
        flash(f"Scan started (batch {batch_id[:8]}...).", "success")

    return redirect(url_for("admin.import_pdf_dashboard"))


@admin_bp.route("/import-pdf/scan-status")
@admin_required
def import_pdf_scan_status():
    from ..scanner import get_scan_progress

    progress = get_scan_progress()
    return jsonify(progress)


@admin_bp.route("/import-pdf/review")
@admin_required
def import_pdf_review():
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status", "pending")
    confidence = request.args.get("confidence")
    q = request.args.get("q")
    batch = request.args.get("batch")

    query = StagedBook.query

    if status and status != "all":
        query = query.filter(StagedBook.status == status)

    if confidence:
        query = query.filter(StagedBook.confidence == confidence)

    if q:
        search = f"%{q}%"
        query = query.filter(
            db.or_(
                StagedBook.original_filename.ilike(search),
                StagedBook.title.ilike(search),
                StagedBook.author.ilike(search),
            )
        )

    if batch:
        query = query.filter(StagedBook.scan_batch_id == batch)

    query = query.order_by(StagedBook.scanned_at.desc())
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        "admin/import_pdf_review.html",
        staged_books=pagination.items,
        pagination=pagination,
        status_filter=status,
        confidence_filter=confidence,
        q=q,
        batch=batch,
    )


@admin_bp.route("/import-pdf/staged/<int:staged_id>/edit", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_staged_edit(staged_id):
    staged = db.session.get(StagedBook, staged_id)
    if not staged:
        abort(404)

    form = StagedBookForm(obj=staged)

    if form.validate_on_submit():
        staged.title = form.title.data.strip() if form.title.data else None
        staged.author = _normalize_authors(form.author.data) if form.author.data else None
        staged.description = form.description.data or None
        staged.language = form.language.data.strip() if form.language.data else None
        staged.publication_year = form.publication_year.data
        staged.isbn = form.isbn.data.strip() if form.isbn.data else None
        staged.tags_text = form.tags_text.data.strip() if form.tags_text.data else None
        db.session.commit()

        flash("Staged book metadata updated.", "success")
        return redirect(url_for("admin.import_pdf_review"))

    return render_template(
        "admin/import_pdf_staged_edit.html",
        form=form,
        staged=staged,
    )


@admin_bp.route("/import-pdf/staged/<int:staged_id>/approve", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_staged_approve(staged_id):
    staged = db.session.get(StagedBook, staged_id)
    if not staged:
        abort(404)

    if not staged.title or not staged.author:
        flash("Cannot approve: title and author are required.", "danger")
        return redirect(url_for("admin.import_pdf_review"))

    staging_dir = Path(current_app.config["STAGING_STORAGE"])
    master_dir = Path(current_app.config["MASTER_STORAGE"])

    safe_name = secure_filename(staged.original_filename)
    master_filename = f"{uuid.uuid4().hex}_{safe_name}"
    src_path = (staging_dir / staged.original_filename).resolve()
    dst_path = master_dir / master_filename

    if not src_path.is_file() or not str(src_path).startswith(str(staging_dir.resolve()) + os.sep):
        current_app.logger.error(f"Staging file not found or path traversal blocked: {src_path}")
        flash("Staging file not found. Cannot approve.", "danger")
        return redirect(url_for("admin.import_pdf_review"))

    book = Book(
        title=staged.title,
        author=staged.author,
        description=staged.description,
        language=staged.language or "en",
        publication_year=staged.publication_year,
        isbn=staged.isbn,
        master_filename=master_filename,
        cover_filename=staged.cover_filename,
    )

    sync_tags(book, staged.tags_text)

    db.session.add(book)
    db.session.flush()

    staged.status = "approved"
    staged.approved_at = _utcnow()
    staged.imported_book_id = book.id

    # Move file BEFORE commit so a crash between these two operations
    # leaves the DB unchanged rather than pointing to a missing file.
    shutil.move(str(src_path), str(dst_path))
    try:
        db.session.commit()
    except SQLAlchemyError:
        # Roll back the file move on DB failure
        shutil.move(str(dst_path), str(src_path))
        db.session.rollback()
        flash("A database error occurred during approval.", "danger")
        return redirect(url_for("admin.import_pdf_review"))

    log_event(
        "staged_book_approved",
        target_type="book",
        target_id=book.id,
        detail=f"Imported from staging: {staged.original_filename}",
    )
    flash(f'Approved and imported "{book.title}".', "success")
    return redirect(url_for("admin.import_pdf_review"))


@admin_bp.route("/import-pdf/staged/<int:staged_id>/dismiss", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_staged_dismiss(staged_id):
    staged = db.session.get(StagedBook, staged_id)
    if not staged:
        abort(404)

    staged.status = "dismissed"
    db.session.commit()

    flash("Staged book dismissed.", "success")
    return redirect(url_for("admin.import_pdf_review"))


@admin_bp.route("/import-pdf/bulk-approve", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_bulk_approve():
    staging_dir = Path(current_app.config["STAGING_STORAGE"])
    master_dir = Path(current_app.config["MASTER_STORAGE"])

    if "approve_all" in request.form:
        staged_books = StagedBook.query.filter_by(status="pending").all()
    else:
        staged_ids = request.form.getlist("staged_ids", type=int)
        staged_books = StagedBook.query.filter(
            StagedBook.id.in_(staged_ids),
            StagedBook.status == "pending",
        ).all()

    approved_count = 0
    skipped_count = 0
    for staged in staged_books:
        if not staged.title or not staged.author:
            skipped_count += 1
            continue

        safe_name = secure_filename(staged.original_filename)
        master_filename = f"{uuid.uuid4().hex}_{safe_name}"
        src_path = (staging_dir / staged.original_filename).resolve()
        dst_path = master_dir / master_filename

        if not src_path.is_file() or not str(src_path).startswith(str(staging_dir.resolve()) + os.sep):
            current_app.logger.error(f"Staging file not found or path traversal blocked: {src_path}")
            skipped_count += 1
            continue

        try:
            book = Book(
                title=staged.title,
                author=staged.author,
                description=staged.description,
                language=staged.language or "en",
                publication_year=staged.publication_year,
                isbn=staged.isbn,
                master_filename=master_filename,
                cover_filename=staged.cover_filename,
            )

            sync_tags(book, staged.tags_text)

            db.session.add(book)
            db.session.flush()

            staged.status = "approved"
            staged.approved_at = _utcnow()
            staged.imported_book_id = book.id

            # Move file BEFORE commit (see H1 in review)
            shutil.move(str(src_path), str(dst_path))
            try:
                db.session.commit()
            except SQLAlchemyError:
                shutil.move(str(dst_path), str(src_path))
                raise

            approved_count += 1

            log_event(
                "staged_book_approved",
                target_type="book",
                target_id=book.id,
                detail=f"Bulk imported from staging: {staged.original_filename}",
            )
        except (OSError, SQLAlchemyError, ValueError) as exc:
            db.session.rollback()
            current_app.logger.error(f"Bulk approve failed for '{staged.original_filename}': {exc}")
            skipped_count += 1
    msg = f"Bulk approve complete: {approved_count} book(s) imported."
    if skipped_count:
        msg += f" {skipped_count} skipped."
    flash(msg, "success" if approved_count else "warning")
    return redirect(url_for("admin.import_pdf_review"))


@admin_bp.route("/import-pdf/bulk-dismiss", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_bulk_dismiss():
    staged_ids = request.form.getlist("staged_ids", type=int)

    count = StagedBook.query.filter(
        StagedBook.id.in_(staged_ids),
        StagedBook.status == "pending",
    ).update({"status": "dismissed"}, synchronize_session="fetch")

    db.session.commit()
    flash(f"Dismissed {count} staged book(s).", "success")
    return redirect(url_for("admin.import_pdf_review"))


@admin_bp.route("/import-pdf/ai-enrich", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_ai_enrich():
    if not current_app.config.get("AI_EXTRACTION_ENABLED"):
        flash("AI extraction is disabled. Enable AI_EXTRACTION_ENABLED first.", "warning")
        return redirect(url_for("admin.import_pdf_review"))

    staged_ids = request.form.getlist("staged_ids", type=int)
    if not staged_ids:
        flash("No books selected.", "warning")
        return redirect(url_for("admin.import_pdf_review"))

    from ..ai_service import extract_metadata_with_ai
    from ..scanner import _compute_confidence

    staging_dir = Path(current_app.config["STAGING_STORAGE"])
    enriched = 0
    skip_reasons = []

    staged_books = StagedBook.query.filter(
        StagedBook.id.in_(staged_ids),
        StagedBook.status == "pending",
    ).all()

    for staged in staged_books:
        # Skip if already enriched with AI
        sources = [s.strip() for s in (staged.metadata_sources or "").split(",") if s.strip()]
        if "ai_claude" in sources:
            skip_reasons.append(f"{staged.original_filename}: already AI-enriched")
            continue

        # Find the PDF file
        src_path = (staging_dir / staged.original_filename).resolve()
        if not src_path.is_file() or not str(src_path).startswith(str(staging_dir.resolve()) + os.sep):
            skip_reasons.append(f"{staged.original_filename}: staging file not found")
            continue

        ai_meta = extract_metadata_with_ai(src_path, current_app.config)

        if not ai_meta or not any(v for v in ai_meta.values()):
            skip_reasons.append(f"{staged.original_filename}: no extractable text")
            continue

        # Merge: AI values override existing where AI provides non-null data
        if ai_meta.get("title"):
            staged.title = ai_meta["title"]
        if ai_meta.get("author"):
            staged.author = ai_meta["author"]
        if ai_meta.get("description"):
            staged.description = ai_meta["description"]
        if ai_meta.get("publication_year"):
            staged.publication_year = ai_meta["publication_year"]
        if ai_meta.get("isbn"):
            staged.isbn = ai_meta["isbn"]
        if ai_meta.get("language"):
            staged.language = ai_meta["language"]
        if ai_meta.get("tags"):
            staged.tags_text = ai_meta["tags"]

        sources.append("ai_claude")
        staged.metadata_sources = ",".join(sources)
        staged.confidence = _compute_confidence(
            staged.metadata_sources,
            bool(staged.title),
            bool(staged.author),
            bool(staged.isbn),
        )

        # Regenerate cover with updated title/author/isbn
        cover_dir = current_app.config["COVER_STORAGE"]
        # Reuse existing cover public_id stem, or generate a new one
        cover_public_id = staged.cover_filename.rsplit(".", 1)[0] if staged.cover_filename else uuid.uuid4().hex
        new_cover = fetch_cover(
            isbn=staged.isbn,
            title=staged.title,
            author=staged.author,
            public_id=cover_public_id,
            cover_storage_dir=cover_dir,
        )
        if new_cover:
            staged.cover_filename = new_cover

        db.session.commit()
        enriched += 1

    msg = f"Enriched {enriched} book(s) with AI."
    if skip_reasons:
        msg += f" Skipped {len(skip_reasons)}: {'; '.join(skip_reasons)}"
    flash(msg, "success" if enriched else "warning")
    return redirect(url_for("admin.import_pdf_review"))


@admin_bp.route("/import-pdf/refresh-covers", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def import_pdf_refresh_covers():
    staged_ids = request.form.getlist("staged_ids", type=int)
    if not staged_ids:
        flash("No books selected.", "warning")
        return redirect(url_for("admin.import_pdf_review"))

    cover_dir = current_app.config["COVER_STORAGE"]
    refreshed = 0
    failed = 0

    staged_books = StagedBook.query.filter(
        StagedBook.id.in_(staged_ids),
    ).all()

    for staged in staged_books:
        if not staged.title:
            failed += 1
            continue

        cover_public_id = staged.cover_filename.rsplit(".", 1)[0] if staged.cover_filename else uuid.uuid4().hex

        new_cover = fetch_cover(
            isbn=staged.isbn,
            title=staged.title,
            author=staged.author,
            public_id=cover_public_id,
            cover_storage_dir=cover_dir,
        )
        if new_cover:
            staged.cover_filename = new_cover
            refreshed += 1
        else:
            failed += 1

    db.session.commit()

    msg = f"Refreshed {refreshed} cover(s)."
    if failed:
        msg += f" {failed} failed."
    flash(msg, "success" if refreshed else "warning")
    return redirect(url_for("admin.import_pdf_review"))
