from datetime import timedelta

from flask import abort, flash, redirect, render_template, request, url_for

from .. import limiter
from ..audit import log_event
from ..models import Book, Loan, User, db
from .common import _utcnow, admin_bp, admin_required
from .forms import LoanExtendForm, LoanInvalidateForm, LoanSearchForm

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
