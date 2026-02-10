from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from .. import limiter
from ..audit import log_event
from ..models import Book, Loan, WaitlistEntry, db
from .service import checkout_book

lending_bp = Blueprint("lending", __name__)


@lending_bp.route("/borrow/<book_public_id>", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def borrow(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    if not book.is_visible or book.is_disabled:
        abort(404)

    if book.is_public_domain:
        return redirect(url_for("lending.download_free", book_public_id=book.public_id))

    if book.restricted_access:
        flash("This title has restricted access. Please contact a librarian.", "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    if not book.is_available:
        waitlist_position = WaitlistEntry.query.filter_by(book_id=book.id, is_fulfilled=False).count()
        return render_template(
            "lending/unavailable.html",
            book=book,
            waitlist_position=waitlist_position,
        )

    try:
        loan = checkout_book(current_user, book)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Send loan email (best-effort)
    from ..email_service import send_loan_email

    if not send_loan_email(loan, current_user, book):
        current_app.logger.warning("Loan email was not sent for loan %s", loan.public_id)

    return render_template("lending/borrow_confirm.html", loan=loan, book=book)


@lending_bp.route("/read/<access_token>")
@login_required
def reader(access_token):
    loan = Loan.query.filter_by(access_token=access_token).first_or_404()

    if loan.user_id != current_user.id:
        abort(403)

    if not loan.is_active or loan.invalidated:
        return render_template("lending/loan_expired.html", loan=loan), 410

    if loan.is_expired:
        return render_template("lending/loan_expired.html", loan=loan), 410

    return render_template("lending/reader.html", loan=loan)


@lending_bp.route("/loan/<access_token>/download")
@login_required
@limiter.limit("30 per hour")
def download(access_token):
    loan = Loan.query.filter_by(access_token=access_token).first_or_404()

    # Verify the requesting user is the loan's patron
    if loan.user_id != current_user.id:
        abort(403)

    # Check if loan has expired (but access_token is still valid)
    if not loan.is_active or loan.invalidated:
        return render_template("lending/loan_expired.html", loan=loan), 410

    if loan.is_expired:
        return render_template("lending/loan_expired.html", loan=loan), 410

    # Serve the download page (GET without ?file param) vs actual file
    if request.args.get("file") == "1":
        if not loan.circulation_filename:
            flash("The circulation copy is not yet available. Please try again shortly.", "warning")
            return redirect(url_for("lending.download", access_token=access_token))

        circ_dir = Path(current_app.config["CIRCULATION_STORAGE"]).resolve()
        file_path = (circ_dir / loan.circulation_filename).resolve()
        try:
            file_path.relative_to(circ_dir)
        except ValueError:
            abort(403)
        if not file_path.is_file():
            flash("The circulation file could not be found. Please contact the librarian.", "danger")
            return redirect(url_for("lending.download", access_token=access_token))

        # Increment download count
        loan.download_count += 1
        db.session.commit()

        log_event(
            action="loan_download",
            target_type="loan",
            target_id=loan.id,
            detail=f"Downloaded circulation copy (count: {loan.download_count})",
            user_id=current_user.id,
        )

        raw_title = loan.book_title_snapshot or "book"
        safe_stem = secure_filename(raw_title).strip("._") or "book"
        download_name = f"{safe_stem}.pdf"
        return send_from_directory(
            str(circ_dir),
            loan.circulation_filename,
            as_attachment=True,
            download_name=download_name,
        )

    return render_template("lending/download.html", loan=loan)


@lending_bp.route("/download-free/<book_public_id>")
@login_required
@limiter.limit("30 per hour")
def download_free(book_public_id):
    """Download a public domain book directly (no loan required)."""
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    if not book.is_public_domain or not book.is_visible or book.is_disabled:
        abort(404)

    if not book.master_filename:
        flash("This title is not currently available for download.", "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Serve download page (GET without ?file param) vs actual file
    if request.args.get("file") == "1":
        # Generate or use cached library-edition PDF
        circ_dir = Path(current_app.config["CIRCULATION_STORAGE"]).resolve()

        if not book.public_domain_filename:
            from ..pdf_service import generate_public_domain_copy

            try:
                book.public_domain_filename = generate_public_domain_copy(book)
                db.session.commit()
            except (FileNotFoundError, ValueError, OSError) as exc:
                current_app.logger.error("Failed to generate public domain PDF for book %d: %s", book.id, exc)
                flash("Could not generate the download file. Please contact a librarian.", "danger")
                return redirect(url_for("catalog.detail", public_id=book.public_id))

        file_path = (circ_dir / book.public_domain_filename).resolve()
        try:
            file_path.relative_to(circ_dir)
        except ValueError:
            abort(403)
        if not file_path.is_file():
            # Regenerate if the cached file was deleted
            from ..pdf_service import generate_public_domain_copy

            try:
                book.public_domain_filename = generate_public_domain_copy(book)
                db.session.commit()
            except (FileNotFoundError, ValueError, OSError) as exc:
                current_app.logger.error("Failed to regenerate public domain PDF for book %d: %s", book.id, exc)
                flash("Could not generate the download file. Please contact a librarian.", "danger")
                return redirect(url_for("catalog.detail", public_id=book.public_id))

        book.download_count += 1
        db.session.commit()

        log_event(
            action="public_domain_download",
            target_type="book",
            target_id=book.id,
            detail=f"Downloaded public domain copy (count: {book.download_count})",
            user_id=current_user.id,
        )

        raw_title = book.title or "book"
        safe_stem = secure_filename(raw_title).strip("._") or "book"
        download_name = f"{safe_stem}.pdf"
        return send_from_directory(
            str(circ_dir),
            book.public_domain_filename,
            as_attachment=True,
            download_name=download_name,
        )

    return render_template("lending/download_free.html", book=book)


@lending_bp.route("/waitlist/<book_public_id>", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def join_waitlist(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    if not book.is_visible or book.is_disabled:
        abort(404)

    if book.restricted_access:
        flash("This title has restricted access and cannot be waitlisted.", "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    if book.is_available:
        flash("This book is currently available. You can borrow it now.", "info")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    if not book.master_filename:
        flash("This title is not currently available for circulation.", "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    if not current_user.can_borrow:
        flash("Your account is not eligible to join the waitlist.", "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Check if already on waitlist
    existing = WaitlistEntry.query.filter_by(user_id=current_user.id, book_id=book.id, is_fulfilled=False).first()
    if existing:
        flash("You are already on the waitlist for this book.", "info")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Check if patron already has an active loan for this book
    active_loan = Loan.query.filter_by(user_id=current_user.id, book_id=book.id, is_active=True).first()
    if active_loan:
        flash("You already have an active loan for this book.", "info")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Remove any previously fulfilled waitlist entry so re-join works
    fulfilled = WaitlistEntry.query.filter_by(user_id=current_user.id, book_id=book.id, is_fulfilled=True).first()
    if fulfilled:
        db.session.delete(fulfilled)

    entry = WaitlistEntry(user_id=current_user.id, book_id=book.id)
    db.session.add(entry)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You are already on the waitlist for this book.", "info")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    position = WaitlistEntry.query.filter_by(book_id=book.id, is_fulfilled=False).count()

    log_event(
        action="waitlist_join",
        target_type="book",
        target_id=book.id,
        detail=f"Joined waitlist for '{book.title}' (position {position})",
        user_id=current_user.id,
    )

    flash(
        f'You have been added to the waitlist for "{book.title}" '
        f"(position {position}). We will notify you when a copy is available.",
        "success",
    )
    return redirect(url_for("catalog.detail", public_id=book.public_id))
