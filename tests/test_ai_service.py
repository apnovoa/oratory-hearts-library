"""Unit tests for AI metadata extraction behavior."""

import sys

from app import ai_service


def test_parse_ai_response_valid_json():
    raw = (
        '{"title":"Book","author":"Author","publication_year":1999,'
        '"isbn":"1234567890","language":"en","tags":["tag1","tag2"],'
        '"description":"desc"}'
    )
    parsed = ai_service._parse_ai_response(raw, "book.pdf")
    assert parsed["title"] == "Book"
    assert parsed["author"] == "Author"
    assert parsed["publication_year"] == 1999
    assert parsed["isbn"] == "1234567890"
    assert parsed["language"] == "en"
    assert parsed["tags"] == "tag1, tag2"


def test_parse_ai_response_invalid_json_returns_none():
    assert ai_service._parse_ai_response("not json", "bad.pdf") is None


def test_parse_ai_response_strips_markdown_fences():
    raw = '```json\n{"title":"Book","author":"Author","publication_year":2001}\n```'
    parsed = ai_service._parse_ai_response(raw, "fenced.pdf")
    assert parsed["title"] == "Book"
    assert parsed["author"] == "Author"
    assert parsed["publication_year"] == 2001


def test_extract_metadata_returns_none_when_disabled():
    cfg = {"AI_EXTRACTION_ENABLED": False, "ANTHROPIC_API_KEY": "x"}
    assert ai_service.extract_metadata_with_ai("book.pdf", cfg) is None


def test_extract_metadata_returns_none_when_api_key_missing():
    cfg = {"AI_EXTRACTION_ENABLED": True, "ANTHROPIC_API_KEY": ""}
    assert ai_service.extract_metadata_with_ai("book.pdf", cfg) is None


def test_extract_metadata_handles_api_error_gracefully(monkeypatch):
    class _DummyAPIError(Exception):
        pass

    class _DummyMessages:
        @staticmethod
        def create(**kwargs):
            raise _DummyAPIError("boom")

    class _DummyAnthropicClient:
        def __init__(self, api_key, timeout=None):
            self.messages = _DummyMessages()

    class _DummyAnthropicModule:
        APIError = _DummyAPIError
        Anthropic = _DummyAnthropicClient

    monkeypatch.setitem(sys.modules, "anthropic", _DummyAnthropicModule())
    monkeypatch.setattr(ai_service, "_extract_text_from_pdf", lambda filepath, max_pages=3: "sample text")
    monkeypatch.setattr(ai_service, "_API_DELAY", 0)

    cfg = {
        "AI_EXTRACTION_ENABLED": True,
        "ANTHROPIC_API_KEY": "key",
        "AI_EXTRACTION_TIER": "tier2",
        "AI_MODEL_TIER2": "dummy",
        "AI_MAX_PAGES_METADATA": 3,
    }
    assert ai_service.extract_metadata_with_ai("book.pdf", cfg) is None


def test_extract_metadata_uses_configured_timeout(monkeypatch):
    captured = {}

    class _DummyMessages:
        @staticmethod
        def create(**kwargs):
            class _Content:
                text = '{"title":"Book","author":"Author","publication_year":2000}'

            class _Response:
                def __init__(self):
                    self.content = [_Content()]

            return _Response()

    class _DummyAnthropicClient:
        def __init__(self, api_key, timeout=None):
            captured["api_key"] = api_key
            captured["timeout"] = timeout
            self.messages = _DummyMessages()

    class _DummyAnthropicModule:
        APIError = Exception
        Anthropic = _DummyAnthropicClient

    monkeypatch.setitem(sys.modules, "anthropic", _DummyAnthropicModule())
    monkeypatch.setattr(ai_service, "_extract_text_from_pdf", lambda filepath, max_pages=3: "sample text")
    monkeypatch.setattr(ai_service, "_API_DELAY", 0)

    cfg = {
        "AI_EXTRACTION_ENABLED": True,
        "ANTHROPIC_API_KEY": "key",
        "AI_EXTRACTION_TIER": "tier2",
        "AI_MODEL_TIER2": "dummy",
        "AI_MAX_PAGES_METADATA": 3,
        "AI_REQUEST_TIMEOUT_SECONDS": 17,
    }
    result = ai_service.extract_metadata_with_ai("book.pdf", cfg)
    assert result is not None
    assert captured["api_key"] == "key"
    assert captured["timeout"] == 17
