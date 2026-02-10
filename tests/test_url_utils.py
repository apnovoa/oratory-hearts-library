"""Tests for URL safety helpers."""

from app.url_utils import is_safe_redirect_target


def test_safe_relative_redirect():
    assert is_safe_redirect_target("/catalog?page=2", "http://localhost:8080") is True


def test_safe_same_origin_absolute_redirect():
    assert is_safe_redirect_target("http://localhost:8080/patron/dashboard", "http://localhost:8080") is True


def test_rejects_cross_origin_redirect():
    assert is_safe_redirect_target("https://evil.example.org/phish", "https://library.example.org") is False


def test_rejects_javascript_scheme_redirect():
    assert is_safe_redirect_target("javascript:alert(1)", "https://library.example.org") is False


def test_rejects_backslash_variant():
    assert is_safe_redirect_target("/\\evil.example.org/path", "https://library.example.org") is False
