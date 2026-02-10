"""Tests for conditional ProxyFix wrapping."""

from unittest.mock import patch

from werkzeug.middleware.proxy_fix import ProxyFix


def test_testing_app_does_not_wrap_proxy_by_default(app):
    assert not isinstance(app.wsgi_app, ProxyFix)


def test_production_app_wraps_proxy_when_trusted(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "StrongProductionKey0123456789ABCDEF")
    monkeypatch.setenv("LIBRARY_DOMAIN", "https://library.example.org")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")

    with (
        patch("app.upgrade"),
        patch("app._seed_admin_if_needed"),
    ):
        from app import create_app

        prod_app = create_app("production")

    assert isinstance(prod_app.wsgi_app, ProxyFix)


def test_production_app_can_disable_proxy_trust_via_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "StrongProductionKey0123456789ABCDEF")
    monkeypatch.setenv("LIBRARY_DOMAIN", "https://library.example.org")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("TRUST_PROXY", "false")

    with (
        patch("app.upgrade"),
        patch("app._seed_admin_if_needed"),
    ):
        from app import create_app

        prod_app = create_app("production")

    assert not isinstance(prod_app.wsgi_app, ProxyFix)
