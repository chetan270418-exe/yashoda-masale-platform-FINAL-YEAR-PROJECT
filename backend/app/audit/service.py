"""Audit service — append-only log writer (rules.md section 5)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import AuditLog


def log_action(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an auditable action. Flushes but does NOT commit.

    The caller should commit the surrounding transaction so the audit
    row is saved atomically with the action itself.
    """
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    db.add(entry)
    db.flush()
    return entry
