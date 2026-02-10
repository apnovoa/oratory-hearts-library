"""Utilities for building trusted public URLs."""

from urllib.parse import urljoin, urlparse

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


def is_safe_redirect_target(target, host_url):
    """Validate redirect targets against the current host URL.

    Accepts either:
    - same-origin absolute URLs (http/https only), or
    - root-relative paths ("/..."), excluding "//..." protocol-relative forms.
    """
    if not target or not host_url:
        return False

    target = target.strip()
    if not target:
        return False
    if "\\" in target:
        return False
    if any(ord(ch) < 32 for ch in target):
        return False

    parsed_host = urlparse(host_url)
    if parsed_host.scheme not in {"http", "https"} or not parsed_host.netloc:
        return False

    parsed_target = urlparse(target)
    if parsed_target.scheme or parsed_target.netloc:
        return parsed_target.scheme in {"http", "https"} and parsed_target.netloc == parsed_host.netloc

    if not target.startswith("/") or target.startswith("//"):
        return False

    resolved = urlparse(urljoin(host_url, target))
    return resolved.scheme in {"http", "https"} and resolved.netloc == parsed_host.netloc
