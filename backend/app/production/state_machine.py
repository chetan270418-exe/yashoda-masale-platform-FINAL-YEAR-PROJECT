"""Canonical production batch state machine (spec-pack/implementation.md section 3).

The whole lifecycle lives in THIS file so there is exactly one place that decides which
transition is legal:

    draft           -> in_production | cancelled
    in_production   -> quality_pending
    quality_pending -> released | on_hold | rejected
    on_hold         -> released | rejected
    released        -> on_hold        (Factory/Owner recall; audited; blocks new reservations)
    rejected        -> (terminal)
    cancelled       -> (terminal)

``cancelled`` is only reachable from ``draft``: a batch that never started production can be
called off. Once production has started the batch can only be held or rejected, because a
started batch has consumed material and may have stock implications.

Status values are lowercase snake_case on purpose: the FEFO reservation query in
app.inventory.service filters ``ProductionBatch.status == "released"`` (Invariant B1) and the
expiry/low-stock queries filter the same value, so the value written here must match exactly.
"""

from enum import Enum

from app.core.errors import AppError


class BatchStatus(str, Enum):
    """Canonical lifecycle states of a production batch."""

    DRAFT = "draft"
    IN_PRODUCTION = "in_production"
    QUALITY_PENDING = "quality_pending"
    RELEASED = "released"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


#: Every status a production batch may hold. Mirrored by the CHECK constraint added in
#: Alembic migration 0007_production_records.
BATCH_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in BatchStatus)

#: The single source of truth for legal transitions.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    BatchStatus.DRAFT.value: frozenset(
        {BatchStatus.IN_PRODUCTION.value, BatchStatus.CANCELLED.value}
    ),
    BatchStatus.IN_PRODUCTION.value: frozenset({BatchStatus.QUALITY_PENDING.value}),
    BatchStatus.QUALITY_PENDING.value: frozenset(
        {
            BatchStatus.RELEASED.value,
            BatchStatus.ON_HOLD.value,
            BatchStatus.REJECTED.value,
        }
    ),
    BatchStatus.ON_HOLD.value: frozenset({BatchStatus.RELEASED.value, BatchStatus.REJECTED.value}),
    BatchStatus.RELEASED.value: frozenset({BatchStatus.ON_HOLD.value}),
    BatchStatus.REJECTED.value: frozenset(),
    BatchStatus.CANCELLED.value: frozenset(),
}


def allowed_transitions_for(status: str) -> list[str]:
    """Return the sorted list of statuses reachable from ``status``."""
    return sorted(ALLOWED_TRANSITIONS.get((status or "").strip().lower(), frozenset()))


def is_terminal(status: str) -> bool:
    """Return True when no transition leaves ``status``."""
    return not allowed_transitions_for(status)


def validate_batch_transition(current_status: str, target_status: str) -> None:
    """Raise AppError when moving a batch from ``current_status`` to ``target_status`` is illegal.

    Rule 49: illegal state transitions are rejected with a clear error.
    """
    current = (current_status or "").strip().lower()
    target = (target_status or "").strip().lower()

    if current not in ALLOWED_TRANSITIONS:
        raise AppError(
            "UNKNOWN_BATCH_STATUS",
            f"Unknown batch status '{current_status}'.",
            400,
            details={"known_statuses": list(BATCH_STATUS_VALUES)},
        )
    if target not in BATCH_STATUS_VALUES:
        raise AppError(
            "UNKNOWN_BATCH_STATUS",
            f"Unknown target batch status '{target_status}'.",
            400,
            details={"known_statuses": list(BATCH_STATUS_VALUES)},
        )

    allowed = ALLOWED_TRANSITIONS[current]
    if target not in allowed:
        raise AppError(
            "ILLEGAL_STATE_TRANSITION",
            f"Cannot transition batch from '{current}' to '{target}'.",
            400,
            details={
                "current_status": current,
                "target_status": target,
                "allowed_transitions": sorted(allowed),
            },
        )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "BATCH_STATUS_VALUES",
    "BatchStatus",
    "allowed_transitions_for",
    "is_terminal",
    "validate_batch_transition",
]
