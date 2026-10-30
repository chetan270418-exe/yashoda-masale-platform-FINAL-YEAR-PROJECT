"""Traceability ORM models: BatchEvent and VerificationScan (M6 / T-060).

Matches spec-pack/implementation.md section 2:
    batch_events(id, batch_id, event_type, event_at, public_text)
    verification_scans(id, batch_id nullable, token_hash, scanned_at, ip_hash, result)

Security notes:
- ``public_text`` is plain-language, public-safe copy. It must never contain
  supplier names, customer data, costs or internal identifiers.
- ``verification_scans`` stores SHA-256 hashes of the scanned token and of the
  scanner IP address, never the raw values (rules.md section 4: never log
  secrets, tokens or passwords).
"""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BatchEventType(str, Enum):
    """Lifecycle events a batch can record.

    ``QR_ISSUED`` marks the moment a public trace link was issued (or
    re-issued) for a batch. It is part of the allow-listed public timeline and
    makes no claim about the authenticity of any pack.
    """

    MANUFACTURED = "manufactured"
    QUALITY_CHECKED = "quality_checked"
    RELEASED = "released"
    HELD = "held"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    QR_ISSUED = "qr_issued"


BATCH_EVENT_TYPE_VALUES: tuple[str, ...] = tuple(event.value for event in BatchEventType)

_EVENT_TYPE_CHECK_SQL = "event_type IN ({})".format(
    ", ".join(f"'{value}'" for value in BATCH_EVENT_TYPE_VALUES)
)

SCAN_RESULTS: tuple[str, ...] = ("found", "not_found")


class BatchEvent(Base):
    """One recorded, publicly readable event in a batch's history."""

    __tablename__ = "batch_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    public_text: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(_EVENT_TYPE_CHECK_SQL, name="ck_batch_events_event_type"),
        Index("ix_batch_events_batch_event_at", "batch_id", "event_at", "id"),
    )

    def __repr__(self) -> str:
        return f"<BatchEvent {self.event_type} batch={self.batch_id}>"


class VerificationScan(Base):
    """Audit row for one QR scan.

    ``batch_id`` is NULL when the scanned token did not resolve to a batch:
    the scan is still recorded so that probing attempts are visible, but the
    row reveals nothing about whether the token ever existed.
    """

    __tablename__ = "verification_scans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("production_batches.id", ondelete="CASCADE"), nullable=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "result IN ('found','not_found')",
            name="ck_verification_scans_result",
        ),
    )

    def __repr__(self) -> str:
        return f"<VerificationScan result={self.result} batch={self.batch_id}>"


__all__ = [
    "BATCH_EVENT_TYPE_VALUES",
    "SCAN_RESULTS",
    "BatchEvent",
    "BatchEventType",
    "VerificationScan",
]
