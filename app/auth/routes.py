import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from authlib.integrations.base_client.errors import OAuthError
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import limiter, oauth
from ..audit import log_event
from ..models import User, db
from ..url_utils import is_safe_redirect_target, public_base_url
from .forms import JoinForm, LoginForm, RegistrationForm, RequestPasswordResetForm, ResetPasswordForm

auth_bp = Blueprint("auth", __name__)

PASSWORD_RESET_MAX_AGE = 3600  # 1 hour


def _get_reset_serializer():
    from flask import current_app

    return URLSafeTimedSerializer(str(current_app.config["SECRET_KEY"]) + "-password-reset")


def _generate_reset_token(email):
    s = _get_reset_serializer()
    return s.dumps(email, salt="password-reset")


def _verify_reset_token(token):
    s = _get_reset_serializer()
    try:
        email = s.loads(token, salt="password-reset", max_age=PASSWORD_RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return email


def _library_absolute_url(endpoint, **values):
    """Build absolute URLs from configured public domain only."""
    path = url_for(endpoint, _external=False, **values)
    return f"{public_base_url(current_app.config, current_app.logger)}{path}"


# ── Login ──────────────────────────────────────────────────────────


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("catalog.index"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()

        # Check account lockout before anything else
        if user and user.locked_until and user.locked_until > datetime.now(UTC):
            # Perform dummy check to prevent timing oracle
            bcrypt.checkpw(b"dummy-password", bcrypt.gensalt())
            log_event("login_locked", "user", user.id)
            flash(
                "Account temporarily locked due to repeated failed login attempts. Please try again later.", "warning"
            )
            return render_template("auth/login.html", form=form)

        if user is None:
            # Equalize timing — perform a dummy bcrypt check
            bcrypt.checkpw(b"dummy", bcrypt.gensalt(rounds=13))
            log_event("login_failed", detail=f"email={email}")
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=form)

        if not user.check_password(form.password.data):
            # Atomic increment of failed login count
            User.query.filter_by(id=user.id).update(
                {"failed_login_count": db.func.coalesce(User.failed_login_count, 0) + 1}
            )
            db.session.commit()
            db.session.refresh(user)
            from flask import current_app

            max_failures = current_app.config.get("MAX_FAILED_LOGINS", 5)
            if user.failed_login_count >= max_failures:
                lockout_minutes = current_app.config.get("ACCOUNT_LOCKOUT_MINUTES", 15)
                user.locked_until = datetime.now(UTC) + timedelta(minutes=lockout_minutes)
                db.session.commit()
                log_event(
                    "account_locked",
                    "user",
                    user.id,
                    detail=f"Locked for {lockout_minutes} minutes after {user.failed_login_count} failed attempts",
                )
            log_event("login_failed", detail=f"email={email}")
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=form)

        if user.is_blocked:
            log_event("login_blocked", "user", user.id)
            flash("Your account has been suspended. Please contact the librarian.", "danger")
            return render_template("auth/login.html", form=form)

        if not user.is_active_account:
            log_event("login_inactive", "user", user.id)
            flash("Your account is not active. Please contact the librarian.", "danger")
            return render_template("auth/login.html", form=form)

        # Successful login — reset lockout state
        user.failed_login_count = 0
        user.locked_until = None

        login_user(user, remember=form.remember_me.data)
        session["login_time"] = datetime.now(UTC).isoformat()

        user.last_login_at = datetime.now(UTC)
        db.session.commit()

        log_event("login_success", "user", user.id)

        next_page = request.args.get("next")
        if not is_safe_redirect_target(next_page, request.host_url):
            next_page = None
        return redirect(next_page or url_for("catalog.index"))

    return render_template("auth/login.html", form=form)


# ── Registration (redirect to /join) ──────────────────────────────


@auth_bp.route("/register")
def register():
    return redirect(url_for("auth.join"), code=301)


# ── Join the Oratory (unified form) ──────────────────────────────


@auth_bp.route("/join", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def join():
    from ..models import MembershipApplication

    # Smart routing for logged-in users
    if current_user.is_authenticated:
        # Already a member (admin/librarian/member roles)?
        if current_user.role in ("admin", "librarian", "member"):
            flash("You are already a member of the Oratory.", "info")
            return redirect(url_for("catalog.index"))
        # Already has a pending application?
        pending = MembershipApplication.query.filter_by(
            user_id=current_user.id, status="pending"
        ).first()
        if pending:
            flash("Your application is already pending review.", "info")
            return redirect(url_for("auth.membership_status"))

    # Registration gate
    if not current_user.is_authenticated:
        if not current_app.config.get("REGISTRATION_ENABLED", True):
            flash("Registration is currently closed.", "info")
            return redirect(url_for("auth.login"))

    is_logged_in = current_user.is_authenticated
    form = JoinForm(skip_account_fields=is_logged_in)

    if form.validate_on_submit():
        if is_logged_in:
            user = current_user
        else:
            # Check for existing email
            email = form.email.data.lower().strip()
            existing_user = User.query.filter_by(email=email).first()
            if existing_user:
                login_url = _library_absolute_url("auth.login")
                reset_url = _library_absolute_url("auth.reset_password")
                from ..email_service import _send_email

                _send_email(
                    subject="Registration Attempt",
                    recipient=existing_user.email,
                    html_body=(
                        f"<p>Someone tried to create an account with your email address.</p>"
                        f'<p>If this was you, you can <a href="{login_url}">sign in</a> '
                        f'or <a href="{reset_url}">reset your password</a>.</p>'
                    ),
                )
                flash(
                    "Your application has been submitted! Please check your email.",
                    "success",
                )
                return redirect(url_for("auth.login"))

            # Create new user account
            display_name = f"{form.first_name.data.strip()} {form.last_name.data.strip()}"
            user = User(
                email=email,
                display_name=display_name,
                role="applicant",
            )
            user.set_password(secrets.token_urlsafe(32))
            db.session.add(user)
            db.session.flush()  # get user.id before creating application

            log_event("registration", "user", user.id)

        # Save birthday if provided
        if form.birth_month.data:
            user.birth_month = int(form.birth_month.data)
        if form.birth_day.data:
            user.birth_day = int(form.birth_day.data)

        # Create membership application
        application = MembershipApplication(
            user_id=user.id,
            state_of_life=form.state_of_life.data,
            religious_institute=form.religious_institute.data.strip() if form.religious_institute.data else None,
            city=form.city.data.strip() if form.city.data else None,
            state_province=form.state_province.data.strip() if form.state_province.data else None,
            country=form.country.data.strip() if form.country.data else None,
            baptismal_status=form.baptismal_status.data,
            denomination=form.denomination.data.strip() if form.denomination.data else None,
            rite=form.rite.data if form.rite.data else None,
            diocese=form.diocese.data.strip() if form.diocese.data else None,
            parish=form.parish.data.strip() if form.parish.data else None,
            sacrament_baptism=form.sacrament_baptism.data,
            sacrament_confirmation=form.sacrament_confirmation.data,
            sacrament_eucharist=form.sacrament_eucharist.data,
            why_join=form.why_join.data.strip(),
            how_heard=form.how_heard.data.strip() if form.how_heard.data else None,
            profession_of_faith=form.profession_of_faith.data,
        )
        db.session.add(application)
        db.session.commit()

        log_event("membership_application", "user", user.id)

        # Send confirmation email to user
        from ..email_service import _send_email

        status_url = _library_absolute_url("auth.membership_status")
        _send_email(
            subject="Application Received — Oratory of the Most Sacred Hearts",
            recipient=user.email,
            html_body=(
                f"<p>Dear {user.display_name},</p>"
                f"<p>Thank you for applying to join the Oratory of the Most Sacred Hearts. "
                f"Your application has been received and is under review.</p>"
                f'<p>You can check your application status at any time: '
                f'<a href="{status_url}">View Status</a></p>'
                f"<p>Pax et bonum,<br>The Oratory</p>"
            ),
        )

        # Notify admins
        admin_emails = [
            a.email for a in User.query.filter_by(role="admin", is_active_account=True).all()
        ]
        for admin_email in admin_emails:
            _send_email(
                subject=f"New Membership Application: {user.display_name}",
                recipient=admin_email,
                html_body=(
                    f"<p>A new membership application has been submitted.</p>"
                    f"<p><strong>Name:</strong> {user.display_name}<br>"
                    f"<strong>Email:</strong> {user.email}<br>"
                    f"<strong>State of Life:</strong> {application.state_of_life}<br>"
                    f"<strong>Profession of Faith:</strong> {application.profession_of_faith}</p>"
                ),
            )

        flash("Your application has been submitted! We will review it shortly.", "success")
        if is_logged_in:
            return redirect(url_for("auth.membership_status"))
        return redirect(url_for("auth.login"))

    return render_template("auth/join.html", form=form, is_logged_in=is_logged_in)


# ── Membership Status ─────────────────────────────────────────────


@auth_bp.route("/membership/status")
@login_required
def membership_status():
    from ..models import MembershipApplication

    application = (
        MembershipApplication.query.filter_by(user_id=current_user.id)
        .order_by(MembershipApplication.created_at.desc())
        .first()
    )
    return render_template("auth/membership_status.html", application=application)


# ── Membership Apply (redirect to /join) ──────────────────────────


@auth_bp.route("/membership/apply")
def membership_apply():
    return redirect(url_for("auth.join"), code=302)


# ── Logout ─────────────────────────────────────────────────────────


@auth_bp.route("/logout", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def logout():
    log_event("logout", "user", current_user.id)
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


# ── Password reset request ─────────────────────────────────────────


@auth_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("catalog.index"))

    form = RequestPasswordResetForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()

        if user:
            token = _generate_reset_token(user.email)
            reset_url = _library_absolute_url("auth.reset_password_token", token=token)
            from ..email_service import send_password_reset_email

            if not send_password_reset_email(user, reset_url):
                current_app.logger.warning("Password reset email was not sent for user id=%s", user.id)

            log_event("password_reset_requested", "user", user.id)

        # Always show the same message to prevent email enumeration
        flash(
            "If an account with that email exists, a password reset link has been sent.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)


# ── Password reset with token ──────────────────────────────────────


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def reset_password_token(token):
    if current_user.is_authenticated:
        return redirect(url_for("catalog.index"))

    email = _verify_reset_token(token)
    if email is None:
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.reset_password"))

    user = User.query.filter_by(email=email).first()
    if user is None:
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.reset_password"))

    # Check if token was issued before the most recent password change
    if user.password_changed_at:
        try:
            _, token_ts = _get_reset_serializer().loads(
                token,
                salt="password-reset",
                max_age=PASSWORD_RESET_MAX_AGE,
                return_timestamp=True,
            )
            if token_ts.replace(tzinfo=None) < user.password_changed_at:
                flash("This reset link has already been used.", "warning")
                return redirect(url_for("auth.reset_password"))
        except (BadSignature, SignatureExpired, TypeError, ValueError):
            flash("This reset link is invalid or has expired.", "warning")
            return redirect(url_for("auth.reset_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.force_logout_before = datetime.now(UTC)
        db.session.commit()
        log_event("password_reset_completed", "user", user.id)
        flash("Your password has been reset. You may now sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password_token.html", form=form)


# ── Google OAuth ──────────────────────────────────────────────────


@auth_bp.route("/auth/google")
@limiter.limit("5 per minute")
def google_login():
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google sign-in is not configured.", "danger")
        return redirect(url_for("auth.login"))
    redirect_uri = _library_absolute_url("auth.google_callback")
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
@limiter.limit("5 per minute")
def google_callback():
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google sign-in is not configured.", "danger")
        return redirect(url_for("auth.login"))

    try:
        token = oauth.google.authorize_access_token()
    except (OAuthError, BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        flash("Google sign-in failed. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("email"):
        flash("Could not retrieve your email from Google.", "danger")
        return redirect(url_for("auth.login"))
    if userinfo.get("email_verified") is not True:
        flash("Google account email is not verified.", "danger")
        return redirect(url_for("auth.login"))

    email = userinfo["email"].lower().strip()
    google_id = userinfo.get("sub")
    if not google_id:
        flash("Could not verify your Google account identity.", "danger")
        return redirect(url_for("auth.login"))
    name = userinfo.get("name", email)

    # Look up by google_id first, then by email
    user = User.query.filter_by(google_id=google_id).first()
    if user is None:
        user = User.query.filter_by(email=email).first()

    if user is None:
        # New user — check if registration is enabled
        if not current_app.config.get("REGISTRATION_ENABLED", True):
            flash("Registration is currently closed. Please contact the librarian.", "info")
            return redirect(url_for("auth.login"))

        user = User(
            email=email,
            display_name=name,
            role="patron",
            google_id=google_id,
        )
        # Set a random password so the bcrypt field is non-null but the user
        # cannot sign in via the password form (they must use Google OAuth).
        user.set_password(secrets.token_urlsafe(32))
        db.session.add(user)
        db.session.commit()
        log_event("registration_google", "user", user.id, detail=f"Registered via Google: {email}")
    else:
        # Existing user — link Google account if not already linked
        if not user.google_id:
            user.google_id = google_id
            db.session.commit()

    # Check if account is blocked or inactive
    if user.is_blocked:
        flash("Your account has been suspended. Please contact the librarian.", "danger")
        return redirect(url_for("auth.login"))
    if not user.is_active_account:
        flash("Your account is not active. Please contact the librarian.", "danger")
        return redirect(url_for("auth.login"))

    # Log in
    login_user(user, remember=True)
    session["login_time"] = datetime.now(UTC).isoformat()
    user.last_login_at = datetime.now(UTC)
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()

    log_event("login_google", "user", user.id)
    return redirect(url_for("catalog.index"))
