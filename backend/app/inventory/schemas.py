"""Pydantic schemas for inventory alert queries and stock adjustments."""

import uuid
from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ExpiringBatchItem(BaseModel):
    """Details of a production batch nearing its expiry date in an active warehouse."""

    model_config = ConfigDict(from_attributes=True)

    batch_code: str = Field(..., description="Unique production batch code")
    variant_id: uuid.UUID = Field(..., description="Product variant UUID")
    sku: str = Field(..., description="Product variant SKU")
    product_name: str = Field(..., description="Parent product name")
    warehouse_id: uuid.UUID = Field(..., description="Warehouse location UUID")
    warehouse_name: str = Field(..., description="Warehouse location name")
    expiry_date: date = Field(..., description="Batch expiry date")
    days_until_expiry: int = Field(..., description="Calendar days remaining until expiry")
    available_quantity: Decimal = Field(
        ..., description="Available unreserved stock (on_hand - reserved)"
    )


class ExpiryAlertResponse(BaseModel):
    """Response envelope for expiry alert queries."""

    model_config = ConfigDict(from_attributes=True)

    days_ahead: int = Field(..., description="Configured lookahead window in days")
    total: int = Field(..., description="Total count of expiring batch items found")
    items: list[ExpiringBatchItem] = Field(
        default_factory=list, description="List of expiring batch items"
    )


class LowStockVariantItem(BaseModel):
    """Aggregate stock status for a product variant across active warehouses."""

    model_config = ConfigDict(from_attributes=True)

    variant_id: uuid.UUID = Field(..., description="Product variant UUID")
    sku: str = Field(..., description="Product variant SKU")
    product_name: str = Field(..., description="Parent product name")
    pack_size: int = Field(..., description="Pack size in grams")
    pack_size_g: int = Field(..., description="Pack size in grams (alias)")
    total_available: Decimal = Field(
        ..., description="Total available unreserved stock across active warehouses"
    )
    threshold: Decimal = Field(..., description="Alert threshold configured for query")
    status_flag: str = Field(..., description="Stock status: 'LOW_STOCK' or 'OUT_OF_STOCK'")


class LowStockAlertResponse(BaseModel):
    """Response envelope for low-stock alert queries."""

    model_config = ConfigDict(from_attributes=True)

    threshold: Decimal = Field(..., description="Stock threshold evaluated")
    total: int = Field(..., description="Total count of low stock items found")
    items: list[LowStockVariantItem] = Field(
        default_factory=list, description="List of variants at or below threshold"
    )


class AdjustmentReason(str, Enum):
    """Permitted canonical reason categories for manual stock adjustments."""

    DAMAGE = "DAMAGE"
    SPOILAGE = "SPOILAGE"
    COUNT_DISCREPANCY = "COUNT_DISCREPANCY"
    FOUND = "FOUND"


class StockAdjustmentRequest(BaseModel):
    """Payload to request a manual stock balance adjustment (T-043, B5)."""

    warehouse_id: uuid.UUID = Field(..., description="Target warehouse location UUID")
    batch_id: uuid.UUID = Field(..., description="Target production batch UUID")
    qty_delta: Decimal = Field(
        ..., description="Stock delta (positive to increase on_hand, negative to decrease)"
    )
    reason_category: AdjustmentReason = Field(
        ..., description="Standardized adjustment reason code"
    )
    notes: str | None = Field(None, max_length=500, description="Optional explanation notes")


class StockAdjustmentResponse(BaseModel):
    """Result of a manual stock adjustment on an inventory balance."""

    model_config = ConfigDict(from_attributes=True)

    balance_id: uuid.UUID = Field(..., description="Inventory balance record UUID")
    warehouse_id: uuid.UUID = Field(..., description="Warehouse location UUID")
    batch_id: uuid.UUID = Field(..., description="Production batch UUID")
    on_hand: Decimal = Field(..., description="New on_hand balance after adjustment")
    reserved: Decimal = Field(..., description="Current reserved quantity")
    available: Decimal = Field(
        ..., description="New available stock (on_hand - reserved) after adjustment"
    )
