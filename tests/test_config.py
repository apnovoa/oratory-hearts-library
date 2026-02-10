"""Configuration hardening tests."""

import pytest

from app.config import ProductionConfig


class _DummyApp:
    logger = None


def test_production_requires_https_library_domain(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "prod-secret")
    monkeypatch.setenv("LIBRARY_DOMAIN", "http://library.example.org")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)

    with pytest.raises(RuntimeError, match="LIBRARY_DOMAIN must be set to a valid https:// URL"):
        ProductionConfig.init_app(_DummyApp())


def test_production_accepts_valid_https_library_domain(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "prod-secret")
    monkeypatch.setenv("LIBRARY_DOMAIN", "https://library.example.org")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)

    # Should not raise.
    ProductionConfig.init_app(_DummyApp())


def test_production_rejects_non_integer_web_concurrency(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "prod-secret")
    monkeypatch.setenv("LIBRARY_DOMAIN", "https://library.example.org")
    monkeypatch.setenv("WEB_CONCURRENCY", "many")

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY must be an integer"):
        ProductionConfig.init_app(_DummyApp())


def test_production_rejects_zero_web_concurrency(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "prod-secret")
    monkeypatch.setenv("LIBRARY_DOMAIN", "https://library.example.org")
    monkeypatch.setenv("WEB_CONCURRENCY", "0")

    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY must be at least 1"):
        ProductionConfig.init_app(_DummyApp())
