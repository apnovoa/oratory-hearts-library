"""Seed-admin password policy tests."""

import pytest

from app import _seed_admin_if_needed
from app.models import User


def test_seed_admin_rejects_weak_password_when_not_debug(app, db, monkeypatch):
    app.config["DEBUG"] = False
    monkeypatch.setenv("ADMIN_EMAIL", "seed-admin@test.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "weakpassword")

    with app.app_context():
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD for first-run admin account is too weak"):
            _seed_admin_if_needed(app)
        assert User.query.filter_by(role="admin").first() is None


def test_seed_admin_requires_password_when_not_debug(app, db, monkeypatch):
    app.config["DEBUG"] = False
    monkeypatch.setenv("ADMIN_EMAIL", "seed-missing@test.com")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    with app.app_context():
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD must be set"):
            _seed_admin_if_needed(app)
        assert User.query.filter_by(role="admin").first() is None


def test_seed_admin_rejects_placeholder_password_when_not_debug(app, db, monkeypatch):
    app.config["DEBUG"] = False
    monkeypatch.setenv("ADMIN_EMAIL", "seed-placeholder@test.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "ChangeMe123Now!")

    with app.app_context():
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD for first-run admin account is too weak"):
            _seed_admin_if_needed(app)
        assert User.query.filter_by(role="admin").first() is None


def test_seed_admin_accepts_strong_password(app, db, monkeypatch):
    app.config["DEBUG"] = False
    monkeypatch.setenv("ADMIN_EMAIL", "seed-strong@test.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "StrongSteward42Z")

    with app.app_context():
        _seed_admin_if_needed(app)
        admin = User.query.filter_by(email="seed-strong@test.com", role="admin").first()
        assert admin is not None
        assert admin.check_password("StrongSteward42Z")
