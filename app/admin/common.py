import os
from datetime import UTC, datetime
from functools import wraps

from flask import Blueprint, abort
from flask_login import current_user, login_required

admin_bp = Blueprint("admin", __name__)

_PDF_MAGIC = b"%PDF-"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_RIFF_MAGIC = b"RIFF"
_WEBP_WEBP_MAGIC = b"WEBP"


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def _utcnow():
    return datetime.now(UTC)


def _uploaded_file_size(file_storage):
    """Return uploaded file size in bytes without consuming the stream."""
    try:
        stream = file_storage.stream
        pos = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(pos)
        return size
    except (AttributeError, OSError):
        return None


def _is_valid_cover_image(file_storage):
    """Validate JPEG/PNG/WebP by magic bytes."""
    try:
        header = file_storage.read(12)
        file_storage.seek(0)
    except OSError:
        return False

    if header.startswith(_JPEG_MAGIC):
        return True
    if header.startswith(_PNG_MAGIC):
        return True
    return header[:4] == _WEBP_RIFF_MAGIC and header[8:12] == _WEBP_WEBP_MAGIC
