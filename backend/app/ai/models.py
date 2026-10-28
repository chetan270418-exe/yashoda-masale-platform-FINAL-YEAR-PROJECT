"""AI subsystem ORM models: AIToolRun, SalesForecast, ForecastEvaluation, OrderDraft.

Implements spec-pack/implementation.md lines 38-40 and Rules 60-66:
- AIToolRun: Immutable audit log of every model tool execution.
- SalesForecast: Variant demand predictions per period.
- ForecastEvaluation: Chronological evaluation metrics comparing models to baselines.
- OrderDraft: Human-in-the-loop pending orders created by AI agents (Rule 62).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.db import Base

# Dialect-agnostic JSON column (native JSONB on PostgreSQL, JSON on SQLite)
JSON_TYPE = JSON().with_variant(postgresql.JSONB, "postgresql")


class AIToolRun(Base):
    """Audit record for every AI agent tool execution (Rule 65)."""

    __tablename__ = "ai_tool_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    args: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="success"
    )  # success, error, denied
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint("status IN ('success', 'error', 'denied')", name="ck_ai_tool_run_status"),
    )

    def __repr__(self) -> str:
        return f"<AIToolRun tool={self.tool} status={self.status} user={self.user_id}>"


class SalesForecast(Base):
    """Predicted sales demand for a product variant over a monthly period."""

    __tablename__ = "sales_forecasts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # e.g. "2026-11"
    model: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # seasonal_naive, moving_average, regression
    predicted_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "predicted_quantity >= 0", name="ck_sales_forecast_predicted_quantity_nonneg"
        ),
        Index("ix_sales_forecasts_variant_period_model", "variant_id", "period", "model"),
    )

    def __repr__(self) -> str:
        return (
            f"<SalesForecast var={self.variant_id} period={self.period} "
            f"qty={self.predicted_quantity}>"
        )


class ForecastEvaluation(Base):
    """Backtesting evaluation metrics comparing model performance against baselines (D-015)."""

    __tablename__ = "forecast_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mae: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    rmse: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    mape: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    test_period: Mapped[str] = mapped_column(String(60), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ForecastEvaluation model={self.model} mae={self.mae} rmse={self.rmse}>"


class OrderDraft(Base):
    """AI-generated order draft requiring human dealer/consumer approval (Rule 62)."""

    __tablename__ = "order_drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    items: Mapped[list] = mapped_column(
        JSON_TYPE, nullable=False, default=list
    )  # list of {variant_id, sku, qty, unit_price}
    total_estimated: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft"
    )  # draft, approved, rejected
    created_by_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')", name="ck_order_drafts_status"
        ),
        CheckConstraint("total_estimated >= 0", name="ck_order_drafts_total_estimated_nonnegative"),
    )

    def __repr__(self) -> str:
        return f"<OrderDraft id={self.id} customer={self.customer_id} status={self.status}>"
