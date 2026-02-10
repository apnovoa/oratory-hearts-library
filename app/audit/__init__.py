from flask import has_request_context, request
from flask_login import current_user

from ..models import AuditLog, db


def _request_user_id():
    if not has_request_context():
        return None
    if current_user and current_user.is_authenticated:
        return current_user.id
    return None


def log_event(action, target_type=None, target_id=None, detail=None, user_id=None, commit=None):
    """Record an audit event.

    If the current session already has pending writes, the audit entry is flushed
    into that transaction rather than committed independently.
    """
    has_pending_writes = bool(db.session.new or db.session.dirty or db.session.deleted)
    entry = AuditLog(
        user_id=user_id or _request_user_id(),
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=request.remote_addr if has_request_context() else None,
    )
    db.session.add(entry)

    should_commit = (not has_pending_writes) if commit is None else commit
    if should_commit:
        db.session.commit()
    else:
        db.session.flush()

    return entry
