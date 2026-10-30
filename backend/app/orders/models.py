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
    UniqueConstraint,
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
        String(128), nullable=True, index=True, default=None
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
    invoice: Mapped[Invoice | None] = relationship(
        "Invoice", back_populates="order", uselist=False, cascade="all, delete-orphan"
    )
    shipments: Mapped[list[Shipment]] = relationship(
        "Shipment", back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'reserved', 'packed', 'dispatched', "
            "'delivered', 'cancelled', 'returned')",
            name="ck_orders_status",
        ),
        CheckConstraint("total_amount >= 0", name="ck_orders_total_amount_nonnegative"),
        UniqueConstraint("customer_id", "idempotency_key", name="uq_order_customer_idempotency"),
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


class Invoice(Base):
    """Customer invoice generated for fulfilled orders (Milestone M5 / T-052).

    Simulates payments securely: no real card data stored (Rule 91, D-009).
    """

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0.00", nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", server_default="INR", nullable=False
    )
    payment_status: Mapped[str] = mapped_column(
        String(30), default="unpaid", server_default="unpaid", nullable=False, index=True
    )
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship("Order", back_populates="invoice")
    customer: Mapped[User | None] = relationship("User", lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "payment_status IN ('unpaid', 'paid', 'refunded', 'void')",
            name="ck_invoices_payment_status",
        ),
        CheckConstraint("total_amount >= 0", name="ck_invoices_total_amount_nonnegative"),
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice {self.invoice_number} order={self.order_id} "
            f"status={self.payment_status} total={self.total_amount}>"
        )


class Shipment(Base):
    """Logistics shipment dispatch and tracking record (Milestone M5 / T-052)."""

    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shipment_number: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    carrier: Mapped[str] = mapped_column(String(100), nullable=False)
    tracking_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="manifested", server_default="manifested", nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship("Order", back_populates="shipments")
    events: Mapped[list[ShipmentEvent]] = relationship(
        "ShipmentEvent", back_populates="shipment", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('manifested', 'in_transit', 'out_for_delivery', "
            "'delivered', 'failed', 'returned')",
            name="ck_shipments_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<Shipment {self.shipment_number} carrier={self.carrier} status={self.status}>"


class ShipmentEvent(Base):
    """Timeline tracking event for a shipment (Milestone M5 / T-052)."""

    __tablename__ = "shipment_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    shipment: Mapped[Shipment] = relationship("Shipment", back_populates="events")

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('manifested', 'dispatched', 'in_transit', "
            "'out_for_delivery', 'delivered', 'failed', 'returned')",
            name="ck_shipment_events_event_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<ShipmentEvent {self.id} shipment={self.shipment_id} type={self.event_type}>"
