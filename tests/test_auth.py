"""Tests for authentication: login, registration, logout."""

import time

from app.auth.routes import _generate_reset_token
from app.models import User
from tests.conftest import _login, _make_user

# ── Login ──────────────────────────────────────────────────────────


def test_login_page_renders(client):
    rv = client.get("/login")
    assert rv.status_code == 200
    assert b"Sign" in rv.data or b"sign" in rv.data or b"login" in rv.data.lower()


def test_login_success_redirects(client, patron):
    rv = client.post(
        "/login",
        data={"email": patron.email, "password": "TestPass1"},
        follow_redirects=False,
    )
    assert rv.status_code == 302


def test_login_wrong_password(client, patron):
    rv = _login(client, patron.email, "WrongPass1")
    assert b"Invalid email or password" in rv.data


def test_login_nonexistent_email_same_error(client):
    rv = _login(client, "nobody@test.com", "Whatever1")
    assert b"Invalid email or password" in rv.data


def test_login_blocked_user(client, db):
    user = _make_user(email="blocked@test.com", is_blocked=True)
    rv = _login(client, user.email, "TestPass1")
    assert b"suspended" in rv.data


def test_login_inactive_account(client, db):
    user = _make_user(email="inactive@test.com", is_active_account=False)
    rv = _login(client, user.email, "TestPass1")
    assert b"not active" in rv.data


def test_lockout_after_max_failures(client, patron, app, db):
    max_failures = app.config["MAX_FAILED_LOGINS"]
    for _ in range(max_failures):
        _login(client, patron.email, "WrongPass1")
    db.session.refresh(patron)
    assert patron.failed_login_count >= max_failures
    assert patron.locked_until is not None


def test_failed_count_increments(client, patron, db):
    _login(client, patron.email, "WrongPass1")
    db.session.refresh(patron)
    assert patron.failed_login_count == 1


# ── Registration ───────────────────────────────────────────────────


def test_register_creates_patron(client, db):
    rv = client.post(
        "/register",
        data={
            "first_name": "New",
            "last_name": "User",
            "email": "new@test.com",
            "password": "GoodPass1",
            "password_confirm": "GoodPass1",
        },
        follow_redirects=True,
    )
    assert b"Account created successfully" in rv.data
    user = User.query.filter_by(email="new@test.com").first()
    assert user is not None
    assert user.role == "patron"


def test_register_duplicate_email_same_success(client, patron):
    rv = client.post(
        "/register",
        data={
            "first_name": "Dup",
            "last_name": "User",
            "email": patron.email,
            "password": "GoodPass1",
            "password_confirm": "GoodPass1",
        },
        follow_redirects=True,
    )
    # Same message shown for both new and duplicate to prevent enumeration
    assert b"Account created successfully" in rv.data


def test_register_weak_password_rejected(client):
    client.post(
        "/register",
        data={
            "first_name": "Weak",
            "last_name": "User",
            "email": "weak@test.com",
            "password": "short",
            "password_confirm": "short",
        },
        follow_redirects=True,
    )
    assert User.query.filter_by(email="weak@test.com").first() is None


# ── Logout ─────────────────────────────────────────────────────────


def test_logout_clears_session(patron_client):
    patron_client.post("/logout", follow_redirects=True)
    # After logout, accessing a protected page should redirect to login
    rv = patron_client.get("/catalog", follow_redirects=False)
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


# ── Password reset security flows ──────────────────────────────────


def test_password_reset_updates_password_and_force_logout(client, patron, db):
    patron.password_changed_at = None
    db.session.commit()

    token = _generate_reset_token(patron.email)

    rv = client.post(
        f"/reset-password/{token}",
        data={"password": "NewStrong1", "password_confirm": "NewStrong1"},
        follow_redirects=True,
    )
    assert rv.status_code == 200

    db.session.refresh(patron)
    assert patron.check_password("NewStrong1") is True
    assert patron.force_logout_before is not None


def test_password_reset_token_cannot_be_reused(client, patron):
    from app.models import db

    patron.password_changed_at = None
    db.session.commit()

    token = _generate_reset_token(patron.email)

    # First use: valid reset
    rv = client.post(
        f"/reset-password/{token}",
        data={"password": "NewStrong1", "password_confirm": "NewStrong1"},
        follow_redirects=True,
    )
    assert rv.status_code == 200
    assert rv.request.path == "/login"

    # Second use: must be rejected as already used
    rv = client.get(f"/reset-password/{token}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"already been used" in rv.data


def test_password_reset_token_expires(client, patron, monkeypatch):
    from app.models import db

    patron.password_changed_at = None
    db.session.commit()

    token = _generate_reset_token(patron.email)

    # Force tiny token lifetime for deterministic expiry in test.
    monkeypatch.setattr("app.auth.routes.PASSWORD_RESET_MAX_AGE", 1)
    time.sleep(2)

    rv = client.get(f"/reset-password/{token}", follow_redirects=True)
    assert rv.status_code == 200
    assert b"invalid or has expired" in rv.data
