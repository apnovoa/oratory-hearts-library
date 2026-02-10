import csv
import io
from datetime import UTC, datetime

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from .. import limiter
from ..audit import log_event
from ..models import AuditLog, Book, Loan, User, db
from .common import _utcnow, admin_bp, admin_required
from .forms import AdminChangePasswordForm, AuditFilterForm

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
