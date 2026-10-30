"""Pydantic schemas for Orders module (Milestone M1 / T-050).

Adheres to:
- Invariant B8: Price snapshot immutability
- Invariant B9: Role-based pricing
- Invariant B12: Strict Decimal money types serialized as strings
- Rule 48: Idempotency key uniqueness and replay
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderItemCreate(BaseModel):
    """Schema for creating a line item within an order."""

    variant_id: uuid.UUID = Field(
        ...,
        description="Product variant UUID to order",
    )
    quantity: Decimal = Field(
        ...,
        gt=Decimal("0"),
        le=Decimal("10000"),
        decimal_places=3,
        description="Quantity ordered. Must be positive and <= 10,000 (up to 3 decimal places).",
    )

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "variant_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "quantity": "5.000",
            }
        },
    )


class OrderCreate(BaseModel):
    """Schema for submitting a new order creation request (Rule 48)."""

    items: list[OrderItemCreate] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of order lines. Must contain between 1 and 50 items.",
    )
    idempotency_key: str | None = Field(
        None,
        max_length=128,
        description="Optional client idempotency key (or via 'Idempotency-Key' header).",
    )
    order_type: str = Field(
        "sales",
        max_length=30,
        description="Order classification (e.g. sales, wholesale, sample).",
    )
    warehouse_id: uuid.UUID | None = Field(
        None,
        description="Optional preferred warehouse UUID for stock reservation.",
    )

    @field_validator("items")
    @classmethod
    def validate_unique_variants(cls, items: list[OrderItemCreate]) -> list[OrderItemCreate]:
        """Reject duplicate variant lines in the same order request to avoid ambiguity."""
        variant_ids = [item.variant_id for item in items]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError(
                "Duplicate variant_id in order items. Combine quantities into a single item."
            )
        return items

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "idempotency_key": "order-req-20261029-001",
                "order_type": "standard",
                "items": [
                    {
                        "variant_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                        "quantity": "2.000",
                    }
                ],
            }
        },
    )


class OrderItemRead(BaseModel):
    """Read schema for an order line item with immutable price snapshot (B8, B12)."""

    model_config = ConfigDict(
        from_attributes=True,
        ser_json_decimal="string",
    )

    id: uuid.UUID = Field(..., description="Unique order item line UUID")
    order_id: uuid.UUID = Field(..., description="Parent order UUID")
    variant_id: uuid.UUID = Field(..., description="Product variant UUID")
    quantity: Decimal = Field(..., description="Quantity ordered (Numeric 14, 3)")
    unit_price_snapshot: Decimal = Field(
        ...,
        description="Immutable price snapshot resolved at order creation time (Numeric 12, 2)",
    )
    price_list_code: str = Field(
        ...,
        description="Source price list code used for snapshot ('MRP', 'DEALER', 'DISTRIBUTOR')",
    )
    line_total: Decimal = Field(
        ...,
        description="Calculated line total: unit_price_snapshot * quantity (Numeric 12, 2)",
    )
    created_at: datetime | None = Field(None, description="Line creation timestamp")


class OrderRead(BaseModel):
    """Read schema for a customer order."""

    model_config = ConfigDict(
        from_attributes=True,
        ser_json_decimal="string",
    )

    id: uuid.UUID = Field(..., description="Unique order UUID")
    order_number: str = Field(..., description="Human-readable unique order code, e.g. ORD-XXXX")
    customer_id: uuid.UUID | None = Field(None, description="Owner customer User UUID")
    order_type: str = Field("sales", description="Order type classification")
    status: str = Field(..., description="Order lifecycle status")
    total_amount: Decimal = Field(
        ...,
        description="Total order value in INR (sum of line_total values, Numeric 12, 2)",
    )
    currency: str = Field("INR", description="ISO currency code")
    idempotency_key: str | None = Field(None, description="Idempotency key for request replay")
    created_at: datetime = Field(..., description="UTC creation timestamp")
    updated_at: datetime = Field(..., description="UTC last updated timestamp")
    items: list[OrderItemRead] = Field(
        default_factory=list, description="Snapshotted order line items"
    )


class OrderReserveRequest(BaseModel):
    """Payload for reserving order stock via FEFO (Invariants B1-B4)."""

    warehouse_id: uuid.UUID | None = Field(
        None,
        description=(
            "Target warehouse UUID for stock reservation. "
            "Defaults to primary active warehouse if omitted."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "warehouse_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380b22",
            }
        },
    )


class OrderDispatchRequest(BaseModel):
    """Payload for dispatching a packed order (Invariant B5)."""

    carrier: str | None = Field(
        None,
        max_length=100,
        description="Logistics carrier name (e.g., 'BlueDart', 'Delhivery').",
    )
    tracking_ref: str | None = Field(
        None,
        max_length=128,
        description="Carrier tracking reference or consignment number.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "carrier": "BlueDart",
                "tracking_ref": "BD-98234710-IN",
            }
        },
    )


class OrderCancelRequest(BaseModel):
    """Payload for cancelling an order (Invariant B6)."""

    reason: str | None = Field(
        None,
        max_length=255,
        description="Optional reason for order cancellation.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "Customer requested cancellation before warehouse packaging.",
            }
        },
    )


class OrderReturnRequest(BaseModel):
    """Payload for recording a returned order for quarantine inspection (Invariant B7)."""

    reason: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Mandatory reason for return and quality quarantine inspection (Invariant B7).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": (
                    "Damaged outer packaging upon delivery; quarantined for quality inspection."
                ),
            }
        },
    )


class StockReservationRead(BaseModel):
    """Read schema for a stock reservation allocated to an order line item."""

    model_config = ConfigDict(
        from_attributes=True,
        ser_json_decimal="string",
    )

    id: uuid.UUID = Field(..., description="Unique stock reservation UUID")
    order_item_id: uuid.UUID = Field(..., description="Target order item UUID")
    balance_id: uuid.UUID = Field(..., description="Target inventory balance UUID")
    quantity: Decimal = Field(..., description="Reserved quantity (Numeric 14, 3)")
    status: str = Field(..., description="Reservation status: 'active', 'consumed', 'released'")
    created_at: datetime = Field(..., description="Reservation creation timestamp")


# ── Milestone M5 / T-052: Invoices & Shipments Schemas ───────────────────────


class InvoiceRead(BaseModel):
    """Read schema for a billing invoice."""

    model_config = ConfigDict(
        from_attributes=True,
        ser_json_decimal="string",
    )

    id: uuid.UUID
    order_id: uuid.UUID
    customer_id: uuid.UUID | None = None
    invoice_number: str
    total_amount: Decimal
    currency: str = "INR"
    payment_status: str
    payment_method: str | None = None
    payment_reference: str | None = None
    paid_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class InvoiceCreate(BaseModel):
    """Payload to issue an invoice for an order."""

    notes: str | None = Field(None, max_length=255, description="Optional invoice remarks.")


class PaymentRecordRequest(BaseModel):
    """Payload to record a payment for an invoice (Rule 91, D-009: simulated payment)."""

    amount: Decimal = Field(
        ..., gt=Decimal("0"), decimal_places=2, description="Paid amount in INR"
    )
    payment_method: str = Field(
        "simulated_upi", max_length=50, description="Simulated payment method"
    )
    payment_reference: str | None = Field(
        None, max_length=128, description="External transaction ID"
    )
    notes: str | None = Field(None, max_length=255)

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "amount": "1250.00",
                "payment_method": "simulated_upi",
                "payment_reference": "UPI-REF-98124019",
            }
        },
    )


class ShipmentEventRead(BaseModel):
    """Read schema for a shipment timeline event."""

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    shipment_id: uuid.UUID
    event_type: str
    location: str | None = None
    description: str
    event_at: datetime


class ShipmentEventCreate(BaseModel):
    """Payload to add a timeline event to a shipment."""

    event_type: str = Field(
        ...,
        description="Event type (manifested, dispatched, in_transit, out_for_delivery, delivered)",
    )
    location: str | None = Field(None, max_length=100, description="Checkpoint location")
    description: str = Field(..., max_length=255, description="Event status description")


class ShipmentRead(BaseModel):
    """Read schema for a logistics shipment."""

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    order_id: uuid.UUID
    shipment_number: str
    carrier: str
    tracking_ref: str | None = None
    status: str
    dispatched_at: datetime | None = None
    delivered_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    events: list[ShipmentEventRead] = Field(default_factory=list)


class ShipmentCreate(BaseModel):
    """Payload to create a shipment for an order."""

    carrier: str = Field(
        ..., min_length=1, max_length=100, description="Carrier name (e.g. BlueDart)"
    )
    tracking_ref: str | None = Field(
        None, max_length=128, description="Carrier tracking / consignment reference"
    )
    notes: str | None = Field(None, max_length=255)


class ShipmentStatusUpdateRequest(BaseModel):
    """Payload to advance shipment status."""

    status: str = Field(
        ...,
        description="Target status (in_transit, out_for_delivery, delivered, failed, returned)",
    )
    location: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=255)
