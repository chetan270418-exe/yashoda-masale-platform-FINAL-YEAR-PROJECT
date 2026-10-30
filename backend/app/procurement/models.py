"""Procurement ORM models: Supplier, RawMaterial, MaterialLot, MaterialReceipt,
MaterialReceiptLine (Milestone M3 / T-030).

Implements the inbound half of the factory records described in
spec-pack/implementation.md section 2:
- A supplier delivers raw material.
- Each receipt line creates one material lot (a received quantity of one raw material).
- Money and quantities are stored as Decimal / Numeric (Invariant B12).
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Supplier(Base):
    """Raw-material supplier (e.g. a turmeric farm cooperative)."""

    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lots: Mapped[list["MaterialLot"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", lazy="selectin"
    )
    receipts: Mapped[list["MaterialReceipt"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Supplier {self.code} ({self.name})>"


class RawMaterial(Base):
    """Raw material consumed by production (e.g. turmeric powder, chilli flakes)."""

    __tablename__ = "raw_materials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lots: Mapped[list["MaterialLot"]] = relationship(
        back_populates="raw_material", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<RawMaterial {self.code} ({self.name})>"


class MaterialLot(Base):
    """A received quantity of one raw material from one supplier."""

    __tablename__ = "material_lots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lot_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    raw_material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_materials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    supplier: Mapped[Supplier] = relationship(back_populates="lots")
    raw_material: Mapped[RawMaterial] = relationship(back_populates="lots")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_material_lots_quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="ck_material_lots_unit_cost_nonnegative"),
        Index("ix_material_lots_supplier_material", "supplier_id", "raw_material_id"),
    )

    def __repr__(self) -> str:
        return f"<MaterialLot {self.lot_code} qty={self.quantity}>"


class MaterialReceipt(Base):
    """Goods-received note: what a supplier delivered on a given day."""

    __tablename__ = "material_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    receipt_no: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    received_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    supplier: Mapped[Supplier] = relationship(back_populates="receipts")
    lines: Mapped[list["MaterialReceiptLine"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MaterialReceipt {self.receipt_no} supplier={self.supplier_id}>"


class MaterialReceiptLine(Base):
    """One line of a goods-received note, pointing at the material lot it created."""

    __tablename__ = "material_receipt_lines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("material_receipts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("material_lots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    receipt: Mapped[MaterialReceipt] = relationship(back_populates="lines")
    material_lot: Mapped[MaterialLot] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_material_receipt_lines_quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="ck_material_receipt_lines_cost_nonnegative"),
    )

    def __repr__(self) -> str:
        return f"<MaterialReceiptLine receipt={self.receipt_id} qty={self.quantity}>"


__all__ = [
    "Supplier",
    "RawMaterial",
    "MaterialLot",
    "MaterialReceipt",
    "MaterialReceiptLine",
]
