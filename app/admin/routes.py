"""Admin blueprint and route registration."""

# Import modules for route registration side effects.
from . import routes_books, routes_core, routes_import_pdf, routes_loans, routes_requests, routes_users  # noqa: F401
from .common import admin_bp, admin_required

__all__ = ["admin_bp", "admin_required"]
