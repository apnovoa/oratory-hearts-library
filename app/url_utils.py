"""Utilities for building trusted public URLs."""

from urllib.parse import urlparse

SAFE_PUBLIC_BASE_FALLBACK = "http://localhost:8080"


def public_base_url(config, logger=None):
    """Return a validated public base URL from LIBRARY_DOMAIN.

    Falls back to localhost when configuration is missing or malformed.
    """
    raw = str(config.get("LIBRARY_DOMAIN", "")).strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"

    if logger is not None:
        logger.warning(
            "Invalid LIBRARY_DOMAIN value %r; using safe fallback %s.",
            raw,
            SAFE_PUBLIC_BASE_FALLBACK,
        )
    return SAFE_PUBLIC_BASE_FALLBACK
