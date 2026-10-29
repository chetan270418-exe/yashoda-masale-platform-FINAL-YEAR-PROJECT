"""Order ORM models: Order and OrderItem (Milestone M1 / T-050).

Enforces invariants:
- B8: Immutable unit price snapshot taken at order placement.
- B12: All monetary amounts stored as Decimal / Numeric(12, 2).
- Rule 48: Idempotency key uniqueness across order submissions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.auth.models import User
    from app.catalog.models import ProductVariant
    from app.inventory.models import StockReservation


class Order(Base):
    """Customer / Dealer order record (spec-pack/implementation.md)."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True, default=None
    )
    order_type: Mapped[str] = mapped_column(
        String(30), default="sales", server_default="sales", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending", nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True, default=None
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0.00", nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", server_default="INR", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    customer: Mapped[User | None] = relationship("User", lazy="joined")
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending', 'reserved', 'packed', 'dispatched', 'delivered', "
            "'cancelled', 'returned', 'open', 'shipped', 'partially_shipped'"
            ")",
            name="ck_orders_status",
        ),
        CheckConstraint("total_amount >= 0", name="ck_orders_total_amount_nonnegative"),
    )

    def __repr__(self) -> str:
        return f"<Order {self.order_number} status={self.status} total={self.total_amount}>"


class OrderItem(Base):
    """Line item in an order with immutable unit price snapshot (B8, B12)."""

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0.00", nullable=False
    )
    price_list_code: Mapped[str] = mapped_column(
        String(30), default="MRP", server_default="MRP", nullable=False
    )
    line_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0.00", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship("Order", back_populates="items")
    variant: Mapped[ProductVariant | None] = relationship("ProductVariant", lazy="joined")
    reservations: Mapped[list[StockReservation]] = relationship(
        "StockReservation", back_populates="order_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price_snapshot >= 0", name="ck_order_items_price_nonnegative"),
        CheckConstraint("line_total >= 0", name="ck_order_items_line_total_nonnegative"),
    )

    def __repr__(self) -> str:
        return (
            f"<OrderItem {self.id} order={self.order_id} variant={self.variant_id} "
            f"qty={self.quantity} price={self.unit_price_snapshot}>"
        )
