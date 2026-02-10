"""AI-powered metadata extraction using the Anthropic Claude API.

Reads the first few pages of a PDF via PyMuPDF, sends the text (or page
images for scanned PDFs) to Claude, and returns structured metadata
(title, author, year, ISBN, language, tags, and optionally a catalog
description).
"""

import base64
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Rate-limit delay between API calls (seconds)
_API_DELAY = 0.5

_MAX_TEXT_CHARS = 80_000  # ~20K tokens — safe for all Claude models

# Vision fallback settings
_VISION_DPI = 150  # render resolution for page images
_VISION_MAX_PAGES = 4  # pages to render as images


def _extract_text_from_pdf(filepath, max_pages=3):
    """Extract plain text from the first *max_pages* pages of a PDF.

    Uses PyMuPDF (imported as ``fitz``).  Returns an empty string if the
    library is unavailable or the PDF contains only scanned images.
    Text is capped at ``_MAX_TEXT_CHARS`` to stay within API token limits.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF is not installed; AI text extraction unavailable.")
        return ""

    text_parts = []
    total_len = 0
    try:
        with fitz.open(filepath) as doc:
            for page_num in range(min(max_pages, len(doc))):
                page_text = doc[page_num].get_text()
                if page_text:
                    text_parts.append(page_text)
                    total_len += len(page_text)
                    if total_len >= _MAX_TEXT_CHARS:
                        break
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("PyMuPDF failed to read %s: %s", filepath, exc)
        return ""

    full_text = "\n\n".join(text_parts).strip()
    if len(full_text) > _MAX_TEXT_CHARS:
        full_text = full_text[:_MAX_TEXT_CHARS]
    return full_text


def _render_pages_as_images(filepath, max_pages=_VISION_MAX_PAGES):
    """Render the first pages of a PDF as JPEG images for vision input.

    Returns a list of (base64_data, media_type) tuples, or an empty list
    on failure.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []

    images = []
    try:
        with fitz.open(filepath) as doc:
            zoom = _VISION_DPI / 72  # 72 is default PDF DPI
            matrix = fitz.Matrix(zoom, zoom)
            for page_num in range(min(max_pages, len(doc))):
                pix = doc[page_num].get_pixmap(matrix=matrix)
                img_bytes = pix.tobytes("jpeg")
                b64 = base64.standard_b64encode(img_bytes).decode("ascii")
                images.append((b64, "image/jpeg"))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Failed to render PDF pages as images for %s: %s", filepath, exc)

    return images


def _build_metadata_prompt(text, include_description=True):
    """Return (system_prompt, user_prompt) for the Claude API call."""
    system_prompt = (
        "You are a librarian cataloging books for a Catholic theological library. "
        "Extract metadata from the provided text, which comes from the first pages "
        "of a PDF. Respond ONLY with valid JSON — no markdown fences, no commentary."
    )

    fields = [
        '"title": "exact title as printed"',
        '"author": "full author name(s), comma-separated if multiple"',
        '"publication_year": integer or null',
        '"isbn": "ISBN-10 or ISBN-13 if found, else null"',
        '"language": "ISO 639-1 two-letter code (e.g. en, la, es, fr, de, it)"',
        '"tags": ["up to 8 specific subject tags relevant to the content"]',
    ]

    if include_description:
        fields.append('"description": "2-3 sentence catalog description of the book\'s content and significance"')

    json_schema = "{\n  " + ",\n  ".join(fields) + "\n}"

    user_prompt = (
        f"Extract metadata from this book text and return JSON in exactly this format:\n"
        f"{json_schema}\n\n"
        f"--- BOOK TEXT ---\n{text}"
    )

    return system_prompt, user_prompt


def _build_vision_prompt(include_description=True):
    """Return (system_prompt, user_text) for a vision-based API call."""
    system_prompt = (
        "You are a librarian cataloging books for a Catholic theological library. "
        "You are shown images of the first pages of a scanned PDF book. "
        "Read the text visible in these page images and extract metadata. "
        "Respond ONLY with valid JSON — no markdown fences, no commentary."
    )

    fields = [
        '"title": "exact title as printed"',
        '"author": "full author name(s), comma-separated if multiple"',
        '"publication_year": integer or null',
        '"isbn": "ISBN-10 or ISBN-13 if found, else null"',
        '"language": "ISO 639-1 two-letter code (e.g. en, la, es, fr, de, it)"',
        '"tags": ["up to 8 specific subject tags relevant to the content"]',
    ]

    if include_description:
        fields.append('"description": "2-3 sentence catalog description of the book\'s content and significance"')

    json_schema = "{\n  " + ",\n  ".join(fields) + "\n}"

    user_text = (
        "These are images of the first pages of a scanned book. "
        "Read the visible text and extract metadata in exactly this JSON format:\n"
        f"{json_schema}"
    )

    return system_prompt, user_text


def _parse_ai_response(raw_text, filepath):
    """Parse and normalise a JSON response from the AI."""
    raw = raw_text.strip()

    # Strip markdown fences if the model added them despite instructions
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("AI returned invalid JSON for %s: %s", filepath, exc)
        return None

    # Normalise and validate lengths to prevent oversized DB writes
    def _safe_str(val, max_len):
        if not val or not isinstance(val, str):
            return None
        return val[:max_len] or None

    normalised = {
        "title": _safe_str(result.get("title"), 500),
        "author": _safe_str(result.get("author"), 500),
        "description": _safe_str(result.get("description"), 5000),
        "publication_year": None,
        "isbn": _safe_str(result.get("isbn"), 20),
        "language": _safe_str(result.get("language"), 10),
        "tags": None,
    }

    year = result.get("publication_year")
    if isinstance(year, int) and 1000 <= year <= 2100:
        normalised["publication_year"] = year

    tags = result.get("tags")
    if isinstance(tags, list):
        joined = ", ".join(str(t)[:100] for t in tags[:15] if t)
        normalised["tags"] = joined[:2000] or None

    return normalised


def extract_metadata_with_ai(filepath, app_config):
    """Orchestrate AI metadata extraction for a single PDF.

    Args:
        filepath: Path to the PDF file.
        app_config: Flask app.config dict (needs ANTHROPIC_API_KEY, tier, etc.).

    Returns:
        A dict with keys: title, author, description, publication_year, isbn,
        language, tags.  Returns None if AI extraction is disabled, the text is
        empty, or the API call fails.
    """
    if not app_config.get("AI_EXTRACTION_ENABLED"):
        return None

    api_key = app_config.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("AI extraction enabled but ANTHROPIC_API_KEY is not set.")
        return None

    tier = app_config.get("AI_EXTRACTION_TIER", "tier2")

    # Determine model and page limit
    if tier == "tier3":
        model = app_config.get("AI_MODEL_TIER3", "claude-sonnet-4-5-20250929")
        max_pages = app_config.get("AI_MAX_PAGES_DEEP", 25)
    else:
        model = app_config.get(
            "AI_MODEL_TIER2" if tier == "tier2" else "AI_MODEL_TIER1",
            "claude-haiku-4-5-20251001",
        )
        max_pages = app_config.get("AI_MAX_PAGES_METADATA", 3)

    include_description = tier in ("tier2", "tier3")

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package is not installed; AI extraction unavailable.")
        return None

    configured_timeout = app_config.get("AI_REQUEST_TIMEOUT_SECONDS", 30)
    try:
        timeout_seconds = max(1, int(configured_timeout))
    except (TypeError, ValueError):
        timeout_seconds = 30
        logger.warning("Invalid AI_REQUEST_TIMEOUT_SECONDS value %r; defaulting to %s.", configured_timeout, 30)

    # Extract text
    text = _extract_text_from_pdf(filepath, max_pages=max_pages)

    # Rate limiting
    time.sleep(_API_DELAY)

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    if text:
        # ---- Text-based extraction ----
        system_prompt, user_prompt = _build_metadata_prompt(text, include_description)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except (anthropic.APIError, httpx.TimeoutException, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("AI API call failed for %s: %s", filepath, exc)
            return None

        result = _parse_ai_response(response.content[0].text, filepath)

    else:
        # ---- Vision fallback for scanned/image-only PDFs ----
        logger.info("No extractable text in %s — trying vision fallback.", filepath)
        page_images = _render_pages_as_images(filepath)
        if not page_images:
            logger.info("Could not render page images for %s — skipping.", filepath)
            return None

        system_prompt, user_text = _build_vision_prompt(include_description)

        content_blocks = []
        for b64_data, media_type in page_images:
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                }
            )
        content_blocks.append({"type": "text", "text": user_text})

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": content_blocks}],
            )
        except (anthropic.APIError, httpx.TimeoutException, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("AI vision call failed for %s: %s", filepath, exc)
            return None

        result = _parse_ai_response(response.content[0].text, filepath)

    if result:
        logger.info("AI extracted metadata for %s: title=%r, author=%r", filepath, result["title"], result["author"])
    return result
