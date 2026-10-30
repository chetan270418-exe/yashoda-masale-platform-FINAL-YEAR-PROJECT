"""Pydantic schemas for the procurement module (Milestone M3 / T-030).

Adheres to:
- Invariant B12: money and quantities are Decimal, serialized as strings.
- Rule 40: every input is validated before it reaches the service layer.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SupplierCreate(BaseModel):
    """Payload for registering a raw-material supplier."""

    code: str = Field(..., min_length=1, max_length=60, description="Unique supplier code")
    name: str = Field(..., min_length=1, max_length=160, description="Supplier display name")
    contact_email: str | None = Field(None, max_length=320, description="Contact email address")
    phone: str | None = Field(None, max_length=40, description="Contact phone number")
    address: str | None = Field(None, max_length=500, description="Postal address")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "SUP-TUR-01",
                "name": "Sangli Turmeric Growers Cooperative",
                "contact_email": "procurement@example.invalid",
                "phone": "+91-00000-00000",
            }
        }
    )


class SupplierRead(BaseModel):
    """Read schema for a supplier."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Supplier UUID")
    code: str = Field(..., description="Unique supplier code")
    name: str = Field(..., description="Supplier display name")
    contact_email: str | None = Field(None, description="Contact email address")
    phone: str | None = Field(None, description="Contact phone number")
    address: str | None = Field(None, description="Postal address")
    is_active: bool = Field(..., description="Inactive suppliers cannot receive goods")
    created_at: datetime = Field(..., description="UTC creation timestamp")


class RawMaterialCreate(BaseModel):
    """Payload for registering a raw material."""

    code: str = Field(..., min_length=1, max_length=60, description="Unique raw material code")
    name: str = Field(..., min_length=1, max_length=160, description="Raw material display name")
    unit: str = Field(
        ...,
        min_length=1,
        max_length=30,
        description="Unit of measure, e.g. 'kg', 'g', 'litre'",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "RM-TURMERIC",
                "name": "Turmeric Powder",
                "unit": "kg",
            }
        }
    )


class RawMaterialRead(BaseModel):
    """Read schema for a raw material."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Raw material UUID")
    code: str = Field(..., description="Unique raw material code")
    name: str = Field(..., description="Raw material display name")
    unit: str = Field(..., description="Unit of measure")
    is_active: bool = Field(..., description="Inactive materials cannot be received")
    created_at: datetime = Field(..., description="UTC creation timestamp")


class MaterialLotRead(BaseModel):
    """Read schema for a received material lot."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Material lot UUID")
    lot_code: str = Field(..., description="Unique lot code")
    supplier_id: uuid.UUID = Field(..., description="Supplier UUID")
    raw_material_id: uuid.UUID = Field(..., description="Raw material UUID")
    quantity: Decimal = Field(..., description="Received quantity (Numeric 14, 3)")
    unit_cost: Decimal = Field(..., description="Unit cost in INR (Numeric 12, 2)")
    received_on: date = Field(..., description="Date the goods were received")
    expiry_date: date | None = Field(None, description="Optional material expiry date")
    is_active: bool = Field(..., description="Whether the lot can still be consumed")
    created_at: datetime = Field(..., description="UTC creation timestamp")


class MaterialReceiptLineCreate(BaseModel):
    """One line of a goods-received note; creates exactly one material lot."""

    raw_material_id: uuid.UUID = Field(..., description="Raw material being received")
    lot_code: str | None = Field(
        None,
        max_length=80,
        description="Optional lot code; generated from the receipt number when omitted",
    )
    quantity: Decimal = Field(
        ...,
        gt=Decimal("0"),
        decimal_places=3,
        description="Received quantity, strictly positive (Numeric 14, 3)",
    )
    unit_cost: Decimal = Field(
        ...,
        ge=Decimal("0"),
        decimal_places=2,
        description="Cost per unit in INR, never negative (Numeric 12, 2)",
    )
    expiry_date: date | None = Field(None, description="Optional expiry date of the material")

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "raw_material_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "quantity": "250.000",
                "unit_cost": "180.50",
            }
        },
    )


class MaterialReceiptCreate(BaseModel):
    """Payload for recording a goods-received note."""

    supplier_id: uuid.UUID = Field(..., description="Supplier delivering the goods")
    received_on: date = Field(..., description="Date the goods were received")
    notes: str | None = Field(None, max_length=1000, description="Optional receipt notes")
    lines: list[MaterialReceiptLineCreate] = Field(
        ...,
        min_length=1,
        description="Receipt lines. A receipt must contain at least one line.",
    )

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "supplier_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380b22",
                "received_on": "2026-10-30",
                "lines": [
                    {
                        "raw_material_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                        "quantity": "250.000",
                        "unit_cost": "180.50",
                    }
                ],
            }
        },
    )


class MaterialReceiptLineRead(BaseModel):
    """Read schema for one line of a goods-received note."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Receipt line UUID")
    receipt_id: uuid.UUID = Field(..., description="Parent receipt UUID")
    material_lot_id: uuid.UUID = Field(..., description="Material lot created by this line")
    lot_code: str | None = Field(None, description="Lot code of the created material lot")
    quantity: Decimal = Field(..., description="Received quantity (Numeric 14, 3)")
    unit_cost: Decimal = Field(..., description="Unit cost in INR (Numeric 12, 2)")


class MaterialReceiptRead(BaseModel):
    """Read schema for a goods-received note."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Receipt UUID")
    receipt_no: str = Field(..., description="Unique receipt number")
    supplier_id: uuid.UUID = Field(..., description="Supplier UUID")
    supplier_name: str | None = Field(None, description="Supplier display name")
    received_on: date = Field(..., description="Date the goods were received")
    received_by: uuid.UUID = Field(..., description="User UUID who recorded the receipt")
    notes: str | None = Field(None, description="Optional receipt notes")
    created_at: datetime = Field(..., description="UTC creation timestamp")
    lines: list[MaterialReceiptLineRead] = Field(
        default_factory=list, description="Lines of the receipt"
    )
