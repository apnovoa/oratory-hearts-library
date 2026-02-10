from flask import abort, flash, redirect, render_template, request, url_for

from .. import limiter
from ..audit import log_event
from ..models import Loan, User, db
from .common import _utcnow, admin_bp, admin_required
from .forms import UserBlockForm, UserRoleForm, UserSearchForm

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
