import csv
import io
import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from .. import limiter
from ..audit import log_event
from ..cover_service import fetch_cover
from ..models import (
    AuditLog,
    Book,
    BookRequest,
    Loan,
    ReadingList,
    ReadingListItem,
    StagedBook,
    Tag,
    User,
    db,
)
from .forms import (
    AdminChangePasswordForm,
    AuditFilterForm,
    BookForm,
    BookRequestResolveForm,
    BookSearchForm,
    LoanExtendForm,
    LoanInvalidateForm,
    LoanSearchForm,
    ReadingListForm,
    StagedBookForm,
    UserBlockForm,
    UserRoleForm,
    UserSearchForm,
)

admin_bp = Blueprint("admin", __name__)

_PDF_MAGIC = b"%PDF-"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_RIFF_MAGIC = b"RIFF"
_WEBP_WEBP_MAGIC = b"WEBP"


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def _utcnow():
    return datetime.now(UTC)


def _uploaded_file_size(file_storage):
    """Return uploaded file size in bytes without consuming the stream."""
    try:
        stream = file_storage.stream
        pos = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(pos)
        return size
    except (AttributeError, OSError):
        return None


def _is_valid_cover_image(file_storage):
    """Validate JPEG/PNG/WebP by magic bytes."""
    try:
        header = file_storage.read(12)
        file_storage.seek(0)
    except OSError:
        return False

    if header.startswith(_JPEG_MAGIC):
        return True
    if header.startswith(_PNG_MAGIC):
        return True
    return header[:4] == _WEBP_RIFF_MAGIC and header[8:12] == _WEBP_WEBP_MAGIC


# ── Dashboard ──────────────────────────────────────────────────────


@admin_bp.route("/")
@admin_required
def dashboard():
    total_books = Book.query.count()
    total_patrons = User.query.filter_by(role="patron").count()
    active_loans = Loan.query.filter_by(is_active=True).count()

    month_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    loans_this_month = Loan.query.filter(Loan.borrowed_at >= month_start).count()

    top_borrowed = (
        db.session.query(Book.title, func.count(Loan.id).label("loan_count"))
        .join(Loan, Loan.book_id == Book.id)
        .group_by(Book.id)
        .order_by(func.count(Loan.id).desc())
        .limit(5)
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        total_books=total_books,
        total_patrons=total_patrons,
        active_loans=active_loans,
        loans_this_month=loans_this_month,
        top_borrowed=top_borrowed,
    )


# ── Change Password ────────────────────────────────────────────────


@admin_bp.route("/change-password", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def change_password():
    form = AdminChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "danger")
            return render_template("admin/change_password.html", form=form)

        current_user.set_password(form.new_password.data)
        current_user.force_logout_before = datetime.now(UTC)
        db.session.commit()

        log_event(
            action="password_changed",
            target_type="user",
            target_id=current_user.id,
            detail="Admin changed their password.",
        )
        flash("Password changed successfully. Please log in again.", "success")
        return redirect(url_for("auth.login"))

    return render_template("admin/change_password.html", form=form)


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
            author=form.author.data.strip(),
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
        _sync_tags(book, form.tags_text.data)

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

    if form.validate_on_submit():
        book.title = form.title.data.strip()
        book.author = form.author.data.strip()
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
        _sync_tags(book, form.tags_text.data)

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


def _sync_tags(book, tags_text):
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


# ── Loans ──────────────────────────────────────────────────────────


@admin_bp.route("/loans")
@admin_required
def loans():
    form = LoanSearchForm(request.args)
    page = request.args.get("page", 1, type=int)
    query = Loan.query

    if form.q.data:
        search = f"%{form.q.data}%"
        query = (
            query.join(User, Loan.user_id == User.id)
            .join(Book, Loan.book_id == Book.id)
            .filter(
                db.or_(
                    User.email.ilike(search),
                    Book.title.ilike(search),
                    Loan.book_title_snapshot.ilike(search),
                )
            )
        )

    status = form.status.data
    if status == "active":
        query = query.filter(Loan.is_active == True)
    elif status == "expired":
        query = query.filter(Loan.is_active == True, Loan.due_at < _utcnow())
    elif status == "returned":
        query = query.filter(Loan.is_active == False)

    query = query.order_by(Loan.borrowed_at.desc())
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        "admin/loans.html",
        loans=pagination.items,
        pagination=pagination,
        form=form,
    )


@admin_bp.route("/loans/<int:loan_id>")
@admin_required
def loan_detail(loan_id):
    loan = db.session.get(Loan, loan_id)
    if not loan:
        abort(404)
    extend_form = LoanExtendForm()
    invalidate_form = LoanInvalidateForm()
    return render_template(
        "admin/loan_detail.html",
        loan=loan,
        extend_form=extend_form,
        invalidate_form=invalidate_form,
    )


@admin_bp.route("/loans/<int:loan_id>/extend", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def loan_extend(loan_id):
    loan = db.session.get(Loan, loan_id)
    if not loan:
        abort(404)
    if not loan.is_active or loan.invalidated:
        flash("Only active, non-invalidated loans can be extended.", "warning")
        return redirect(url_for("admin.loan_detail", loan_id=loan.id))
    form = LoanExtendForm()
    if form.validate_on_submit():
        days = form.days.data
        loan.due_at = loan.due_at + timedelta(days=days)
        db.session.commit()
        log_event(
            "loan_extended",
            target_type="loan",
            target_id=loan.id,
            detail=f"Extended by {days} days. New due: {loan.due_at.isoformat()}",
        )
        flash(f"Loan extended by {days} days.", "success")
    else:
        flash("Invalid extension request.", "danger")
    return redirect(url_for("admin.loan_detail", loan_id=loan.id))


@admin_bp.route("/loans/<int:loan_id>/terminate", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def loan_terminate(loan_id):
    loan = db.session.get(Loan, loan_id)
    if not loan:
        abort(404)
    try:
        from ..lending.service import return_loan

        return_loan(loan)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("admin.loan_detail", loan_id=loan.id))
    log_event("loan_terminated", target_type="loan", target_id=loan.id, detail="Loan terminated by admin")
    flash("Loan terminated.", "success")
    return redirect(url_for("admin.loan_detail", loan_id=loan.id))


@admin_bp.route("/loans/<int:loan_id>/invalidate", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def loan_invalidate(loan_id):
    loan = db.session.get(Loan, loan_id)
    if not loan:
        abort(404)
    form = LoanInvalidateForm()
    if form.validate_on_submit():
        loan.invalidated = True
        loan.invalidated_reason = form.reason.data.strip()
        loan.is_active = False
        loan.returned_at = _utcnow()
        db.session.commit()
        log_event(
            "loan_invalidated", target_type="loan", target_id=loan.id, detail=f"Invalidated: {loan.invalidated_reason}"
        )
        # Clean up circulation file and process waitlist (same as return)
        from ..lending.service import _delete_circulation_file, process_waitlist

        _delete_circulation_file(loan)
        if loan.book:
            process_waitlist(loan.book)
        flash("Loan invalidated.", "success")
    else:
        flash("Please provide a reason for invalidation.", "danger")
    return redirect(url_for("admin.loan_detail", loan_id=loan.id))


# ── Users ──────────────────────────────────────────────────────────


@admin_bp.route("/users")
@admin_required
def users():
    form = UserSearchForm(request.args)
    page = request.args.get("page", 1, type=int)
    query = User.query

    if form.q.data:
        search = f"%{form.q.data}%"
        query = query.filter(db.or_(User.email.ilike(search), User.display_name.ilike(search)))

    query = query.order_by(User.created_at.desc())
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        "admin/users.html",
        users=pagination.items,
        pagination=pagination,
        form=form,
    )


def _is_last_admin(user):
    """Return True if *user* is the only active, unblocked admin."""
    return (
        user.role == "admin"
        and User.query.filter_by(role="admin", is_active_account=True, is_blocked=False).count() <= 1
    )


@admin_bp.route("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    page = request.args.get("page", 1, type=int)
    loan_pagination = user.loans.order_by(Loan.borrowed_at.desc()).paginate(page=page, per_page=20, error_out=False)
    block_form = UserBlockForm()
    role_form = UserRoleForm()
    role_form.role.data = user.role
    return render_template(
        "admin/user_detail.html",
        user=user,
        loans=loan_pagination.items,
        pagination=loan_pagination,
        block_form=block_form,
        role_form=role_form,
        is_last_admin=_is_last_admin(user),
    )


@admin_bp.route("/users/<int:user_id>/block", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def user_block(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if _is_last_admin(user):
        flash("Cannot block the only active admin account.", "danger")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    form = UserBlockForm()
    if form.validate_on_submit():
        user.is_blocked = True
        user.block_reason = form.reason.data.strip()
        user.force_logout_before = _utcnow()
        db.session.commit()
        log_event("user_blocked", target_type="user", target_id=user.id, detail=f"Blocked: {user.block_reason}")
        flash(f"User {user.email} has been blocked.", "success")
    else:
        flash("Please provide a reason for blocking.", "danger")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/unblock", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def user_unblock(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    user.is_blocked = False
    user.block_reason = None
    db.session.commit()
    log_event("user_unblocked", target_type="user", target_id=user.id, detail="User unblocked")
    flash(f"User {user.email} has been unblocked.", "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def user_deactivate(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if _is_last_admin(user):
        flash("Cannot deactivate the only active admin account.", "danger")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    user.is_active_account = False
    user.force_logout_before = _utcnow()
    db.session.commit()
    log_event("user_deactivated", target_type="user", target_id=user.id, detail="Account deactivated")
    flash(f"User {user.email} has been deactivated.", "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/activate", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def user_activate(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    user.is_active_account = True
    db.session.commit()
    log_event("user_activated", target_type="user", target_id=user.id, detail="Account activated")
    flash(f"User {user.email} has been activated.", "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/force-logout", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def user_force_logout(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if _is_last_admin(user):
        flash("Cannot force-logout the only active admin account.", "danger")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    user.force_logout_before = _utcnow()
    db.session.commit()
    log_event("user_force_logout", target_type="user", target_id=user.id, detail="Forced logout of all sessions")
    flash(f"All sessions for {user.email} have been invalidated.", "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/change-role", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def user_change_role(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    form = UserRoleForm()
    if form.validate_on_submit():
        old_role = user.role
        new_role = form.role.data
        if old_role == "admin" and new_role != "admin" and _is_last_admin(user):
            flash("Cannot demote the only active admin account.", "danger")
            return redirect(url_for("admin.user_detail", user_id=user.id))
        if new_role in ("patron", "librarian"):
            user.role = new_role
            db.session.commit()
            log_event(
                "user_role_changed",
                target_type="user",
                target_id=user.id,
                detail=f"Role changed from {old_role} to {new_role}",
            )
            flash(f"Role for {user.email} changed to {new_role}.", "success")
        else:
            flash("Invalid role.", "danger")
    return redirect(url_for("admin.user_detail", user_id=user.id))


# ── Audit Log ──────────────────────────────────────────────────────


@admin_bp.route("/audit")
@admin_required
def audit():
    form = AuditFilterForm(request.args)
    page = request.args.get("page", 1, type=int)
    query = AuditLog.query

    if form.action.data:
        query = query.filter(AuditLog.action.ilike(f"%{form.action.data}%"))

    if form.date_from.data:
        try:
            dt_from = datetime.strptime(form.date_from.data, "%Y-%m-%d").replace(tzinfo=UTC)
            query = query.filter(AuditLog.timestamp >= dt_from)
        except ValueError:
            pass

    if form.date_to.data:
        try:
            dt_to = datetime.strptime(form.date_to.data, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=UTC)
            query = query.filter(AuditLog.timestamp <= dt_to)
        except ValueError:
            pass

    query = query.order_by(AuditLog.timestamp.desc())
    pagination = query.paginate(page=page, per_page=50, error_out=False)

    return render_template(
        "admin/audit.html",
        logs=pagination.items,
        pagination=pagination,
        form=form,
    )


# ── Reports ────────────────────────────────────────────────────────


@admin_bp.route("/reports")
@admin_required
def reports():
    # Most borrowed titles -- all time
    most_borrowed_all = (
        db.session.query(Book.title, Book.author, func.count(Loan.id).label("loan_count"))
        .join(Loan, Loan.book_id == Book.id)
        .group_by(Book.id)
        .order_by(func.count(Loan.id).desc())
        .limit(20)
        .all()
    )

    # Most borrowed -- this month
    month_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    most_borrowed_month = (
        db.session.query(Book.title, Book.author, func.count(Loan.id).label("loan_count"))
        .join(Loan, Loan.book_id == Book.id)
        .filter(Loan.borrowed_at >= month_start)
        .group_by(Book.id)
        .order_by(func.count(Loan.id).desc())
        .limit(20)
        .all()
    )

    # Loans per month -- last 12 months
    loans_per_month = []
    now = _utcnow()
    for i in range(11, -1, -1):
        # Calculate month start
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        m_start = datetime(year, month, 1, tzinfo=UTC)
        m_end = datetime(year + 1, 1, 1, tzinfo=UTC) if month == 12 else datetime(year, month + 1, 1, tzinfo=UTC)
        count = Loan.query.filter(
            Loan.borrowed_at >= m_start,
            Loan.borrowed_at < m_end,
        ).count()
        loans_per_month.append(
            {
                "month": m_start.strftime("%b %Y"),
                "count": count,
            }
        )

    # Active loans summary
    active_loans_count = Loan.query.filter_by(is_active=True).count()
    overdue_count = Loan.query.filter(
        Loan.is_active == True,
        Loan.due_at < _utcnow(),
    ).count()

    # Patron activity -- top 20 most active patrons
    patron_activity = (
        db.session.query(
            User.display_name,
            User.email,
            func.count(Loan.id).label("loan_count"),
        )
        .join(Loan, Loan.user_id == User.id)
        .filter(User.role == "patron")
        .group_by(User.id)
        .order_by(func.count(Loan.id).desc())
        .limit(20)
        .all()
    )

    return render_template(
        "admin/reports.html",
        most_borrowed_all=most_borrowed_all,
        most_borrowed_month=most_borrowed_month,
        loans_per_month=loans_per_month,
        active_loans_count=active_loans_count,
        overdue_count=overdue_count,
        patron_activity=patron_activity,
    )


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
        author = (row.get("author") or "").strip()[:300]
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


# ── Audit Log CSV Export ──────────────────────────────────────────


def _sanitize_csv_value(val):
    """Prevent CSV formula injection."""
    if val and isinstance(val, str) and val[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + val
    return val


@admin_bp.route("/audit/export")
@admin_required
def audit_export():
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["id", "timestamp", "user_id", "user_email", "action", "target_type", "target_id", "detail", "ip_address"]
        )
        yield buf.getvalue()

        page = 1
        page_size = 1000
        while True:
            logs = (
                AuditLog.query.order_by(AuditLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size).all()
            )
            if not logs:
                break
            for log in logs:
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow(
                    [
                        log.id,
                        log.timestamp.isoformat() if log.timestamp else "",
                        log.user_id or "",
                        _sanitize_csv_value(log.user.email if log.user else ""),
                        _sanitize_csv_value(log.action),
                        _sanitize_csv_value(log.target_type or ""),
                        log.target_id or "",
                        _sanitize_csv_value(log.detail or ""),
                        _sanitize_csv_value(log.ip_address or ""),
                    ]
                )
                yield buf.getvalue()
            page += 1

    return Response(
        generate(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=audit_log_export.csv",
        },
    )


# ── Book Requests ─────────────────────────────────────────────────


@admin_bp.route("/requests")
@admin_required
def book_requests():
    status_filter = request.args.get("status", "pending")
    page = request.args.get("page", 1, type=int)

    query = BookRequest.query

    if status_filter and status_filter != "all":
        query = query.filter(BookRequest.status == status_filter)

    query = query.order_by(BookRequest.created_at.desc())
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    # Count pending for the badge
    pending_count = BookRequest.query.filter_by(status="pending").count()

    resolve_form = BookRequestResolveForm()

    return render_template(
        "admin/requests.html",
        requests=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        pending_count=pending_count,
        resolve_form=resolve_form,
    )


@admin_bp.route("/requests/<int:request_id>/resolve", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def book_request_resolve(request_id):
    book_req = db.session.get(BookRequest, request_id)
    if not book_req:
        abort(404)

    form = BookRequestResolveForm()
    if form.validate_on_submit():
        book_req.status = form.status.data
        book_req.admin_notes = form.admin_notes.data.strip() if form.admin_notes.data else None
        book_req.resolved_by = current_user.id
        book_req.resolved_at = _utcnow()
        db.session.commit()

        log_event(
            "book_request_resolved",
            target_type="book_request",
            target_id=book_req.id,
            detail=f'Request for "{book_req.title}" {book_req.status} by admin',
        )
        flash(
            f'Request for "{book_req.title}" has been {book_req.status}.',
            "success",
        )
    else:
        flash("Invalid form submission.", "danger")

    return redirect(url_for("admin.book_requests"))


# ── Reading Lists ─────────────────────────────────────────────────


@admin_bp.route("/reading-lists")
@admin_required
def reading_lists():
    all_lists = ReadingList.query.order_by(ReadingList.name.asc()).all()
    return render_template("admin/reading_lists.html", reading_lists=all_lists)


@admin_bp.route("/reading-lists/new", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def reading_list_new():
    form = ReadingListForm()
    if form.validate_on_submit():
        rl = ReadingList(
            name=form.name.data.strip(),
            description=form.description.data or None,
            is_public=form.is_public.data,
            is_featured=form.is_featured.data,
            season=form.season.data or None,
            created_by=current_user.id,
        )
        db.session.add(rl)
        db.session.commit()

        log_event(
            "reading_list_created",
            target_type="reading_list",
            target_id=rl.id,
            detail=f"Created reading list: {rl.name}",
        )
        flash("Reading list created. You can now add books to it.", "success")
        return redirect(url_for("admin.reading_list_edit", list_id=rl.id))

    return render_template(
        "admin/reading_list_edit.html",
        form=form,
        reading_list=None,
        all_books=[],
    )


@admin_bp.route("/reading-lists/<int:list_id>/edit", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def reading_list_edit(list_id):
    rl = db.session.get(ReadingList, list_id)
    if not rl:
        abort(404)

    form = ReadingListForm(obj=rl)

    # Books available to add (exclude those already in the list)
    existing_book_ids = [item.book_id for item in rl.items]
    all_books = (
        Book.query.filter(~Book.id.in_(existing_book_ids) if existing_book_ids else True).order_by(Book.title).all()
    )

    if form.validate_on_submit():
        rl.name = form.name.data.strip()
        rl.description = form.description.data or None
        rl.is_public = form.is_public.data
        rl.is_featured = form.is_featured.data
        rl.season = form.season.data or None

        # Update positions and notes, process removals
        for item in list(rl.items):
            # Check for removal
            if request.form.get(f"remove_{item.id}"):
                db.session.delete(item)
                continue

            # Update position
            new_pos = request.form.get(f"position_{item.id}", type=int)
            if new_pos is not None:
                item.position = new_pos

            # Update note
            new_note = request.form.get(f"note_{item.id}", "").strip()
            item.note = new_note or None

        # Add new book if selected
        add_book_id = request.form.get("add_book_id", type=int)
        if add_book_id:
            book = db.session.get(Book, add_book_id)
            if book:
                max_pos = max((item.position for item in rl.items), default=0)
                new_item = ReadingListItem(
                    reading_list_id=rl.id,
                    book_id=book.id,
                    position=max_pos + 1,
                )
                db.session.add(new_item)

        db.session.commit()

        log_event(
            "reading_list_updated",
            target_type="reading_list",
            target_id=rl.id,
            detail=f"Updated reading list: {rl.name}",
        )
        flash("Reading list updated.", "success")
        return redirect(url_for("admin.reading_list_edit", list_id=rl.id))

    return render_template(
        "admin/reading_list_edit.html",
        form=form,
        reading_list=rl,
        all_books=all_books,
    )


@admin_bp.route("/reading-lists/<int:list_id>/delete", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def reading_list_delete(list_id):
    rl = db.session.get(ReadingList, list_id)
    if not rl:
        abort(404)

    name = rl.name
    db.session.delete(rl)
    db.session.commit()

    log_event(
        "reading_list_deleted", target_type="reading_list", target_id=list_id, detail=f"Deleted reading list: {name}"
    )
    flash(f'Reading list "{name}" deleted.', "success")
    return redirect(url_for("admin.reading_lists"))


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
        staged.author = form.author.data.strip() if form.author.data else None
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

    _sync_tags(book, staged.tags_text)

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

            _sync_tags(book, staged.tags_text)

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
