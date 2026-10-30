"""Production ORM models: ProductionPlan, ProductionStage, QualityParameter,
QualityCheck, QualityDecision (Milestone M3 / T-031 and T-032).

The batch itself (``ProductionBatch``) is NOT redefined here: it lives in
app.inventory.models and is imported, so there is exactly one batch table and the FEFO
queries in app.inventory.service keep working against it.

Quality parameters carry NO invented limits (rules.md section 12): ``min_value`` and
``max_value`` are nullable and every parameter is flagged ``is_placeholder`` until the
sponsor supplies real numbers.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.inventory.models import ProductionBatch

#: Marker stored against every quality parameter whose limits are still unknown.
PLACEHOLDER_SOURCE = "to_be_confirmed_with_sponsor"


def _attach_batch_quantity_columns() -> None:
    """Attach the two quantity columns of migration 0007 to the existing batch model.

    ``ProductionBatch`` lives in app.inventory.models, so the columns required by
    Alembic migration 0007_production_records (``production_batches.planned_qty``
    and ``production_batches.actual_qty``) are attached to the mapped class from
    here. The ORM, the generated DDL and the migration therefore describe the
    same table.

    The status column and its CHECK constraint are already canonical in
    app.inventory.models, so the ORM and the migrated database agree exactly.
    """
    if not hasattr(ProductionBatch, "planned_qty"):
        ProductionBatch.planned_qty = mapped_column(Numeric(14, 3), nullable=True)
        ProductionBatch.actual_qty = mapped_column(Numeric(14, 3), nullable=True)


_attach_batch_quantity_columns()


class ProductionPlan(Base):
    """A planned production run for one product variant."""

    __tablename__ = "production_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_no: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    planned_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    planned_start: Mapped[date] = mapped_column(Date, nullable=False)
    planned_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="planned", server_default="planned", nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("planned_qty > 0", name="ck_production_plans_qty_positive"),
        CheckConstraint("planned_end >= planned_start", name="ck_production_plans_dates_ordered"),
        Index("ix_production_plans_variant_status", "variant_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<ProductionPlan {self.plan_no} qty={self.planned_qty} status={self.status}>"


class ProductionStage(Base):
    """One processing step of a batch (mixing, drying, packing, ...)."""

    __tablename__ = "production_stages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_name: Mapped[str] = mapped_column(String(60), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending", nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("sequence >= 0", name="ck_production_stages_sequence_nonnegative"),
        UniqueConstraint("batch_id", "sequence", name="uq_production_stages_batch_sequence"),
        Index("ix_production_stages_batch_sequence", "batch_id", "sequence"),
    )

    def __repr__(self) -> str:
        return f"<ProductionStage {self.stage_name} seq={self.sequence} status={self.status}>"


class QualityParameter(Base):
    """A quality attribute measured on a batch (moisture, colour, ...).

    Limits are NOT invented: ``min_value`` and ``max_value`` stay NULL until the sponsor
    supplies them, and ``is_placeholder`` marks the parameter as "to be confirmed with
    sponsor" (rules.md section 12, decision D-008).
    """

    __tablename__ = "quality_parameters"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_quality_parameters_range_ordered",
        ),
    )

    def __repr__(self) -> str:
        return f"<QualityParameter {self.code} placeholder={self.is_placeholder}>"


class QualityCheck(Base):
    """One measured value of a quality parameter on a batch.

    ``passed`` is NULLABLE: a placeholder parameter without limits cannot be judged, and
    "no limit" must never be silently treated as "passed".
    """

    __tablename__ = "quality_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parameter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quality_parameters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    measured_value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    checked_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_quality_checks_batch_parameter", "batch_id", "parameter_id"),)

    def __repr__(self) -> str:
        return (
            f"<QualityCheck batch={self.batch_id} parameter={self.parameter_id} "
            f"passed={self.passed}>"
        )


class QualityDecision(Base):
    """The release / hold / reject decision taken on a batch."""

    __tablename__ = "quality_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('released', 'on_hold', 'rejected')",
            name="ck_quality_decisions_decision",
        ),
        Index("ix_quality_decisions_batch_decided", "batch_id", "decided_at"),
    )

    def __repr__(self) -> str:
        return f"<QualityDecision batch={self.batch_id} decision={self.decision}>"


__all__ = [
    "PLACEHOLDER_SOURCE",
    "ProductionBatch",
    "ProductionPlan",
    "ProductionStage",
    "QualityCheck",
    "QualityDecision",
    "QualityParameter",
]
