import os

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

from .. import limiter
from ..audit import log_event
from ..models import Book, Loan, WaitlistEntry, db
from .service import checkout_book, return_loan

lending_bp = Blueprint("lending", __name__)


@lending_bp.route("/borrow/<book_public_id>", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def borrow(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    if not book.is_available:
        waitlist_position = (
            WaitlistEntry.query.filter_by(book_id=book.id, is_fulfilled=False).count()
        )
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

    # Send loan email
    try:
        from ..email_service import send_loan_email
        send_loan_email(loan, current_user, book)
    except Exception:
        current_app.logger.exception("Failed to send loan email")

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

        circ_dir = current_app.config["CIRCULATION_STORAGE"]
        file_path = os.path.join(circ_dir, loan.circulation_filename)
        if not os.path.isfile(file_path):
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

        download_name = f"{loan.book_title_snapshot or 'book'}.pdf"
        return send_from_directory(
            circ_dir,
            loan.circulation_filename,
            as_attachment=True,
            download_name=download_name,
        )

    return render_template("lending/download.html", loan=loan)


@lending_bp.route("/waitlist/<book_public_id>", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def join_waitlist(book_public_id):
    book = Book.query.filter_by(public_id=book_public_id).first_or_404()

    if not current_user.can_borrow:
        flash("Your account is not eligible to join the waitlist.", "warning")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Check if already on waitlist
    existing = WaitlistEntry.query.filter_by(
        user_id=current_user.id, book_id=book.id, is_fulfilled=False
    ).first()
    if existing:
        flash("You are already on the waitlist for this book.", "info")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    # Check if patron already has an active loan for this book
    active_loan = Loan.query.filter_by(
        user_id=current_user.id, book_id=book.id, is_active=True
    ).first()
    if active_loan:
        flash("You already have an active loan for this book.", "info")
        return redirect(url_for("catalog.detail", public_id=book.public_id))

    entry = WaitlistEntry(user_id=current_user.id, book_id=book.id)
    db.session.add(entry)
    db.session.commit()

    position = WaitlistEntry.query.filter_by(
        book_id=book.id, is_fulfilled=False
    ).count()

    log_event(
        action="waitlist_join",
        target_type="book",
        target_id=book.id,
        detail=f"Joined waitlist for '{book.title}' (position {position})",
        user_id=current_user.id,
    )

    flash(
        f"You have been added to the waitlist for \"{book.title}\" "
        f"(position {position}). We will notify you when a copy is available.",
        "success",
    )
    return redirect(url_for("catalog.detail", public_id=book.public_id))
