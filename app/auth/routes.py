import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import bcrypt
from flask import Blueprint, flash, redirect, render_template, request, session, url_for, current_app
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import limiter, oauth
from ..audit import log_event
from ..models import User, db
from .forms import LoginForm, RegistrationForm, RequestPasswordResetForm, ResetPasswordForm

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
        if user and user.locked_until and user.locked_until > datetime.now(timezone.utc):
            # Perform dummy check to prevent timing oracle
            bcrypt.checkpw(b"dummy-password", bcrypt.gensalt())
            log_event("login_locked", "user", user.id)
            flash("Account temporarily locked due to repeated failed login attempts. Please try again later.", "warning")
            return render_template("auth/login.html", form=form)

        if user is None:
            # Equalize timing — perform a dummy bcrypt check
            bcrypt.checkpw(b"dummy", b"$2b$13$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
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
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=lockout_minutes)
                db.session.commit()
                log_event("account_locked", "user", user.id,
                          detail=f"Locked for {lockout_minutes} minutes after {user.failed_login_count} failed attempts")
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
        session["login_time"] = datetime.now(timezone.utc).isoformat()

        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()

        log_event("login_success", "user", user.id)

        next_page = request.args.get("next")
        if next_page:
            parsed = urlparse(next_page)
            if parsed.netloc or parsed.scheme or next_page.startswith("//"):
                next_page = None
        return redirect(next_page or url_for("catalog.index"))

    return render_template("auth/login.html", form=form)


# ── Registration ───────────────────────────────────────────────────

@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("catalog.index"))

    from flask import current_app
    if not current_app.config.get("REGISTRATION_ENABLED", True):
        flash("Registration is currently closed.", "info")
        return redirect(url_for("auth.login"))

    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if existing_user:
            # Silently handle — send a notification email to the existing address
            try:
                from flask import current_app as _ca
                domain = _ca.config.get("LIBRARY_DOMAIN", "")
                login_url = url_for("auth.login", _external=True)
                reset_url = url_for("auth.reset_password", _external=True)
                from ..email_service import _send_email
                _send_email(
                    subject="Registration Attempt",
                    recipient=existing_user.email,
                    html_body=(
                        f"<p>Someone tried to register an account with your email address.</p>"
                        f"<p>If this was you, you can <a href=\"{login_url}\">sign in</a> "
                        f"or <a href=\"{reset_url}\">reset your password</a>.</p>"
                    ),
                )
            except Exception:
                pass
            # Show the same success message to prevent email enumeration
            flash("Account created successfully. You may now sign in.", "success")
            return redirect(url_for("auth.login"))

        display_name = f"{form.first_name.data.strip()} {form.last_name.data.strip()}"
        user = User(
            email=form.email.data.lower().strip(),
            display_name=display_name,
            role="patron",
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        log_event("registration", "user", user.id)
        flash("Account created successfully. You may now sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form=form)


# ── Logout ─────────────────────────────────────────────────────────

@auth_bp.route("/logout", methods=["POST"])
@login_required
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
            from flask import current_app
            reset_url = url_for("auth.reset_password_token", token=token, _external=True)

            try:
                from ..email_service import send_password_reset_email
                send_password_reset_email(user, reset_url)
            except Exception:
                current_app.logger.exception("Failed to send password reset email")

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
        except Exception:
            flash("This reset link is invalid or has expired.", "warning")
            return redirect(url_for("auth.reset_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.force_logout_before = datetime.now(timezone.utc)
        db.session.commit()
        log_event("password_reset_completed", "user", user.id)
        flash("Your password has been reset. You may now sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password_token.html", form=form)


# ── Google OAuth ──────────────────────────────────────────────────

@auth_bp.route("/auth/google")
def google_login():
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google sign-in is not configured.", "danger")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_callback", _external=True, _scheme="https")
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def google_callback():
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google sign-in is not configured.", "danger")
        return redirect(url_for("auth.login"))

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("Google sign-in failed. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("email"):
        flash("Could not retrieve your email from Google.", "danger")
        return redirect(url_for("auth.login"))

    email = userinfo["email"].lower().strip()
    google_id = userinfo["sub"]
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
    session["login_time"] = datetime.now(timezone.utc).isoformat()
    user.last_login_at = datetime.now(timezone.utc)
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()

    log_event("login_google", "user", user.id)
    return redirect(url_for("catalog.index"))
