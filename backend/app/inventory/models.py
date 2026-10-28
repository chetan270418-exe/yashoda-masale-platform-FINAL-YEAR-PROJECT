"""Inventory ORM models: Warehouse, ProductionBatch, Order, OrderItem,
InventoryBalance, StockReservation, StockMovement.

Implementation matches spec-pack/implementation.md and FEFO Engine Step 1 template,
adapted to the project's UUID and Base conventions.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Warehouse(Base):
    """Warehouse location (e.g., Central Warehouse Pune)."""

    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    balances: Mapped[list["InventoryBalance"]] = relationship(
        back_populates="warehouse", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Warehouse {self.code} ({self.name})>"


class ProductionBatch(Base):
    """Production batch record holding expiry dates and release status."""

    __tablename__ = "production_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False
    )
    batch_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    manufactured_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="hold", server_default="hold", nullable=False
    )
    public_token: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    balances: Mapped[list["InventoryBalance"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('hold','released','rejected','recalled')",
            name="ck_production_batches_status",
        ),
        CheckConstraint(
            "manufactured_at IS NULL OR expiry_date >= manufactured_at",
            name="ck_batch_expiry_after_manufacture",
        ),
        Index("ix_batches_variant_expiry", "variant_id", "expiry_date", "id"),
    )

    def __repr__(self) -> str:
        return f"<ProductionBatch {self.batch_code} status={self.status}>"


class Order(Base):
    """Customer order record (minimal schema for inventory reservation linkage)."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), default="open", server_default="open", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','reserved','partially_shipped','shipped','cancelled')",
            name="ck_orders_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<Order {self.order_number} status={self.status}>"


class OrderItem(Base):
    """Item line in a customer order."""

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="items")
    reservations: Mapped[list["StockReservation"]] = relationship(
        back_populates="order_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        Index("ix_order_items_order", "order_id"),
    )

    def __repr__(self) -> str:
        return f"<OrderItem {self.id} variant={self.variant_id} qty={self.quantity}>"


class InventoryBalance(Base):
    """Current stock aggregate by warehouse and batch with CHECK constraints."""

    __tablename__ = "inventory_balances"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id", ondelete="RESTRICT"), nullable=False
    )
    on_hand: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), default=Decimal("0"), server_default="0", nullable=False
    )
    reserved: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), default=Decimal("0"), server_default="0", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    warehouse: Mapped[Warehouse] = relationship(back_populates="balances")
    batch: Mapped[ProductionBatch] = relationship(back_populates="balances")
    reservations: Mapped[list["StockReservation"]] = relationship(back_populates="balance")

    __table_args__ = (
        UniqueConstraint("warehouse_id", "batch_id", name="uq_balance_warehouse_batch"),
        CheckConstraint("on_hand >= 0", name="ck_balance_on_hand_nonnegative"),
        CheckConstraint("reserved >= 0", name="ck_balance_reserved_nonnegative"),
        CheckConstraint("reserved <= on_hand", name="ck_balance_reserved_lte_on_hand"),
        Index("ix_balance_warehouse_batch", "warehouse_id", "batch_id"),
    )

    @property
    def available(self) -> Decimal:
        """Available stock ready for reservation."""
        return self.on_hand - self.reserved

    def __repr__(self) -> str:
        return (
            f"<InventoryBalance wh={self.warehouse_id} batch={self.batch_id} "
            f"on_hand={self.on_hand} reserved={self.reserved}>"
        )


class StockReservation(Base):
    """Stock reservation allocated to a specific order item from an inventory balance."""

    __tablename__ = "stock_reservations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False
    )
    balance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_balances.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order_item: Mapped[OrderItem] = relationship(back_populates="reservations")
    balance: Mapped[InventoryBalance] = relationship(back_populates="reservations")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
        CheckConstraint("status IN ('active','released','consumed')", name="ck_reservation_status"),
        Index("ix_reservation_order_item_status", "order_item_id", "status"),
        Index("ix_reservation_balance_status", "balance_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<StockReservation {self.id} qty={self.quantity} status={self.status}>"


class StockMovement(Base):
    """Immutable ledger recording every single stock movement."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id", ondelete="RESTRICT"), nullable=False
    )
    balance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_balances.id", ondelete="RESTRICT"), nullable=True
    )
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    reason_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_movement_quantity_positive"),
        CheckConstraint(
            "movement_type IN ("
            "'receipt', 'reserve', 'release', 'dispatch', 'adjustment_in', 'adjustment_out'"
            ")",
            name="ck_movement_type",
        ),
        Index("ix_movement_batch_created", "batch_id", "created_at"),
        Index("ix_movement_warehouse_created", "warehouse_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<StockMovement {self.movement_type} qty={self.quantity} op={self.operation_key}>"
