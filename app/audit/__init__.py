from flask import request
from flask_login import current_user

from ..models import AuditLog, db


def log_event(action, target_type=None, target_id=None, detail=None, user_id=None):
    entry = AuditLog(
        user_id=user_id or (current_user.id if current_user and current_user.is_authenticated else None),
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=request.remote_addr if request else None,
    )
    db.session.add(entry)
    db.session.commit()
    return entry
