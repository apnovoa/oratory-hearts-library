"""Unit tests for cover_service security and fallback behavior."""

from pathlib import Path

from app import cover_service


class _DummyResponse:
    def __init__(self, *, content=b"", content_type="image/jpeg", json_payload=None):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self._json_payload = json_payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_payload


def test_fetch_cover_by_isbn_uses_timeout_and_disables_redirects(monkeypatch, tmp_path):
    captured = {}

    def _fake_get(url, timeout, allow_redirects):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["allow_redirects"] = allow_redirects
        return _DummyResponse(content=b"x" * 2048, content_type="image/jpeg")

    monkeypatch.setattr(cover_service.requests, "get", _fake_get)
    monkeypatch.setattr(cover_service.time, "sleep", lambda _: None)

    dest = tmp_path / "cover.jpg"
    ok = cover_service._fetch_cover_by_isbn("9781234567890", str(dest))

    assert ok is True
    assert dest.exists() is True
    assert captured["allow_redirects"] is False
    assert captured["timeout"] == cover_service.REQUEST_TIMEOUT


def test_fetch_cover_by_isbn_rejects_placeholder_image(monkeypatch, tmp_path):
    def _fake_get(url, timeout, allow_redirects):
        return _DummyResponse(content=b"x" * 32, content_type="image/jpeg")

    monkeypatch.setattr(cover_service.requests, "get", _fake_get)
    monkeypatch.setattr(cover_service.time, "sleep", lambda _: None)

    dest = tmp_path / "tiny.jpg"
    ok = cover_service._fetch_cover_by_isbn("9781234567890", str(dest))
    assert ok is False
    assert dest.exists() is False


def test_search_isbn_by_title_author_limits_query_and_disables_redirects(monkeypatch):
    captured = {}

    def _fake_get(url, params, timeout, allow_redirects):
        captured["params"] = params
        captured["timeout"] = timeout
        captured["allow_redirects"] = allow_redirects
        return _DummyResponse(json_payload={"docs": [{"isbn": ["1111111111"]}]})

    monkeypatch.setattr(cover_service.requests, "get", _fake_get)

    isbn = cover_service._search_isbn_by_title_author("My Title", "My Author")

    assert isbn == "1111111111"
    assert captured["allow_redirects"] is False
    assert captured["timeout"] == cover_service.REQUEST_TIMEOUT
    assert captured["params"]["limit"] == 1
    assert captured["params"]["fields"] == "isbn"


def test_fetch_cover_falls_back_to_generated_cover(monkeypatch, tmp_path):
    monkeypatch.setattr(cover_service, "_fetch_cover_by_isbn", lambda isbn, dest: False)
    monkeypatch.setattr(cover_service, "_search_isbn_by_title_author", lambda title, author: None)
    monkeypatch.setattr(cover_service, "generate_cover", lambda **kwargs: "generated.jpg")

    filename = cover_service.fetch_cover(
        isbn=None,
        title="Fallback Book",
        author="Fallback Author",
        public_id="pubid123",
        cover_storage_dir=str(tmp_path),
    )
    assert filename == "generated.jpg"


def test_fetch_cover_requires_public_id_and_storage_dir():
    assert cover_service.fetch_cover(title="X", author="Y", public_id=None, cover_storage_dir="storage/covers") is None
    assert cover_service.fetch_cover(title="X", author="Y", public_id="abc", cover_storage_dir=None) is None


def test_generate_cover_returns_none_when_required_fields_missing(tmp_path):
    assert (
        cover_service.generate_cover(
            title=None,
            author="A",
            public_id="id1",
            cover_storage_dir=str(tmp_path),
            font_path=str(Path("/no/font.ttf")),
        )
        is None
    )
