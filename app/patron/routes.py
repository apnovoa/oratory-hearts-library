from datetime import UTC, datetime
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import limiter
from ..audit import log_event
from ..lending.service import renew_loan as service_renew_loan
from ..lending.service import return_loan as service_return_loan
from ..models import Book, BookNote, BookRequest, Favorite, Loan, db
from .forms import BookNoteForm, BookRequestForm, ProfileForm

patron_bp = Blueprint("patron", __name__)


def _require_patron():
    if current_user.role != "patron":
        abort(403)


@patron_bp.before_request
@login_required
def before_request():
    _require_patron()


@patron_bp.route("/dashboard")
def dashboard():
    active_loans = Loan.query.filter_by(user_id=current_user.id, is_active=True).order_by(Loan.due_at.asc()).all()
    past_loans = (
        Loan.query.filter_by(user_id=current_user.id, is_active=False).order_by(Loan.returned_at.desc()).limit(10).all()
    )
    return render_template(
        "patron/dashboard.html",
        active_loans=active_loans,
        past_loans=past_loans,
    )


@patron_bp.route("/loans")
def loans():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    pagination = (
        Loan.query.filter_by(user_id=current_user.id)
        .order_by(Loan.borrowed_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return render_template(
        "patron/dashboard.html",
        active_loans=[],
        past_loans=pagination.items,
        pagination=pagination,
        show_all_loans=True,
    )


@patron_bp.route("/loans/<string:loan_public_id>/return", methods=["POST"])
@limiter.limit("10 per minute")
def patron_return_loan(loan_public_id):
    loan = Loan.query.filter_by(
        public_id=loan_public_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    try:
        service_return_loan(loan)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("patron.dashboard"))

    flash(f'"{loan.book_title_snapshot}" returned successfully. Thank you.', "success")
    return redirect(url_for("patron.dashboard"))


@patron_bp.route("/loans/<string:loan_public_id>/renew", methods=["POST"])
@limiter.limit("10 per minute")
def renew_loan(loan_public_id):
    loan = Loan.query.filter_by(
        public_id=loan_public_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    try:
        service_renew_loan(loan)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("patron.dashboard"))

    flash(
        f'Loan for "{loan.book_title_snapshot}" renewed successfully. '
        f"New due date: {loan.due_at.strftime('%B %d, %Y at %H:%M UTC')}.",
        "success",
    )
    return redirect(url_for("patron.dashboard"))


@patron_bp.route("/profile", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def profile():
    form = ProfileForm(obj=current_user)
    if request.method == "GET":
        form.birth_month.data = current_user.birth_month or 0
        form.birth_day.data = current_user.birth_day or 0

    if form.validate_on_submit():
        current_user.display_name = form.display_name.data.strip()

        # Update birthday if provided
        if form.birth_month.data and form.birth_day.data:
            current_user.birth_month = form.birth_month.data
            current_user.birth_day = form.birth_day.data
        elif not form.birth_month.data and not form.birth_day.data:
            current_user.birth_month = None
            current_user.birth_day = None

        if form.new_password.data:
            if not form.current_password.data:
                flash("Please enter your current password to set a new one.", "error")
                return render_template("patron/profile.html", form=form)

            if not current_user.check_password(form.current_password.data):
                flash("Current password is incorrect.", "error")
                return render_template("patron/profile.html", form=form)

            current_user.set_password(form.new_password.data)
            current_user.force_logout_before = datetime.now(UTC)
            log_event(
                action="password_changed",
                target_type="user",
                target_id=current_user.id,
                detail="Patron changed their password.",
            )

        db.session.commit()
        log_event(
            action="profile_updated",
            target_type="user",
            target_id=current_user.id,
            detail="Patron updated their profile.",
        )
        flash("Profile updated.", "success")
        return redirect(url_for("patron.dashboard"))

    return render_template("patron/profile.html", form=form)


# ── Favorites ─────────────────────────────────────────────────────


@patron_bp.route("/favorites/<string:book_public_id>/toggle", methods=["POST"])
@limiter.limit("30 per minute")
def toggle_favorite(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    existing = Favorite.query.filter_by(
        user_id=current_user.id,
        book_id=book.id,
    ).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        is_favorited = False
        flash(f'"{book.title}" removed from favorites.', "success")
    else:
        fav = Favorite(user_id=current_user.id, book_id=book.id)
        db.session.add(fav)
        db.session.commit()
        is_favorited = True
        flash(f'"{book.title}" added to favorites.', "success")

    # AJAX-friendly: return JSON if requested
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"favorited": is_favorited})

    # Otherwise redirect back to referrer or book detail
    referrer = request.referrer
    if referrer:
        parsed = urlparse(referrer)
        if parsed.netloc and parsed.netloc != request.host:
            referrer = None
    return redirect(referrer or url_for("catalog.detail", public_id=book.public_id))


@patron_bp.route("/favorites")
def favorites():
    favorites_list = Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()
    books = [fav.book for fav in favorites_list if fav.book.is_visible and not fav.book.is_disabled]
    return render_template("patron/favorites.html", books=books)


# ── Reading History ───────────────────────────────────────────────


@patron_bp.route("/history")
def history():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    pagination = (
        Loan.query.filter_by(user_id=current_user.id, is_active=False)
        .order_by(Loan.borrowed_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return render_template(
        "patron/history.html",
        loans=pagination.items,
        pagination=pagination,
    )


# ── Book Requests ─────────────────────────────────────────────────


@patron_bp.route("/requests/new", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def request_new():
    form = BookRequestForm()

    if form.validate_on_submit():
        req = BookRequest(
            user_id=current_user.id,
            title=form.title.data.strip(),
            author=form.author.data.strip() if form.author.data else None,
            reason=form.reason.data.strip() if form.reason.data else None,
        )
        db.session.add(req)
        db.session.commit()

        log_event(
            action="book_requested",
            target_type="book_request",
            target_id=req.id,
            detail=f'Patron requested: "{req.title}"',
        )
        flash("Your book request has been submitted. Thank you!", "success")
        return redirect(url_for("patron.requests"))

    return render_template("patron/request_form.html", form=form)


@patron_bp.route("/requests")
def requests():
    my_requests = BookRequest.query.filter_by(user_id=current_user.id).order_by(BookRequest.created_at.desc()).all()
    return render_template("patron/requests.html", requests=my_requests)


# ── Patron Notes ──────────────────────────────────────────────────


@patron_bp.route("/notes/<string:book_public_id>", methods=["POST"])
@limiter.limit("20 per minute")
def save_note(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()
    form = BookNoteForm()

    if form.validate_on_submit():
        note = BookNote.query.filter_by(
            user_id=current_user.id,
            book_id=book.id,
        ).first()

        if note:
            note.content = form.content.data.strip()
        else:
            note = BookNote(
                user_id=current_user.id,
                book_id=book.id,
                content=form.content.data.strip(),
            )
            db.session.add(note)

        db.session.commit()
        flash("Note saved.", "success")
    else:
        flash("Note cannot be empty.", "warning")

    return redirect(url_for("catalog.detail", public_id=book.public_id))


@patron_bp.route("/notes/<string:book_public_id>/delete", methods=["POST"])
@limiter.limit("20 per minute")
def delete_note(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    note = BookNote.query.filter_by(
        user_id=current_user.id,
        book_id=book.id,
    ).first_or_404()

    db.session.delete(note)
    db.session.commit()
    flash("Note deleted.", "success")

    return redirect(url_for("catalog.detail", public_id=book.public_id))
