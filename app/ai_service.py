"""AI-powered metadata extraction using the Anthropic Claude API.

Reads the first few pages of a PDF via PyMuPDF, sends the text to Claude,
and returns structured metadata (title, author, year, ISBN, language, tags,
and optionally a catalog description).
"""

import json
import logging
import time

logger = logging.getLogger(__name__)

# Rate-limit delay between API calls (seconds)
_API_DELAY = 0.5


def _extract_text_from_pdf(filepath, max_pages=3):
    """Extract plain text from the first *max_pages* pages of a PDF.

    Uses PyMuPDF (imported as ``fitz``).  Returns an empty string if the
    library is unavailable or the PDF contains only scanned images.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF is not installed; AI text extraction unavailable.")
        return ""

    text_parts = []
    try:
        with fitz.open(filepath) as doc:
            for page_num in range(min(max_pages, len(doc))):
                page_text = doc[page_num].get_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as exc:
        logger.warning("PyMuPDF failed to read %s: %s", filepath, exc)
        return ""

    return "\n\n".join(text_parts).strip()


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
        fields.append(
            '"description": "2-3 sentence catalog description of the book\'s '
            'content and significance"'
        )

    json_schema = "{\n  " + ",\n  ".join(fields) + "\n}"

    user_prompt = (
        f"Extract metadata from this book text and return JSON in exactly this format:\n"
        f"{json_schema}\n\n"
        f"--- BOOK TEXT ---\n{text}"
    )

    return system_prompt, user_prompt


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
        model = app_config.get("AI_MODEL_TIER3", "claude-sonnet-4-5-20250514")
        max_pages = app_config.get("AI_MAX_PAGES_DEEP", 25)
    else:
        model = app_config.get(
            "AI_MODEL_TIER2" if tier == "tier2" else "AI_MODEL_TIER1",
            "claude-haiku-4-5-20251001",
        )
        max_pages = app_config.get("AI_MAX_PAGES_METADATA", 3)

    include_description = tier in ("tier2", "tier3")

    # Extract text
    text = _extract_text_from_pdf(filepath, max_pages=max_pages)
    if not text:
        logger.info("No extractable text in %s — skipping AI extraction.", filepath)
        return None

    system_prompt, user_prompt = _build_metadata_prompt(text, include_description)

    # Rate limiting
    time.sleep(_API_DELAY)

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package is not installed; AI extraction unavailable.")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()

        # Strip markdown fences if the model added them despite instructions
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("AI returned invalid JSON for %s: %s", filepath, exc)
        return None
    except Exception as exc:
        logger.warning("AI API call failed for %s: %s", filepath, exc)
        return None

    # Normalise the result
    normalised = {
        "title": result.get("title") or None,
        "author": result.get("author") or None,
        "description": result.get("description") or None,
        "publication_year": None,
        "isbn": result.get("isbn") or None,
        "language": result.get("language") or None,
        "tags": None,
    }

    year = result.get("publication_year")
    if isinstance(year, int) and 1000 <= year <= 2100:
        normalised["publication_year"] = year

    tags = result.get("tags")
    if isinstance(tags, list):
        normalised["tags"] = ", ".join(str(t) for t in tags if t)

    logger.info("AI extracted metadata for %s: title=%r, author=%r",
                filepath, normalised["title"], normalised["author"])
    return normalised
