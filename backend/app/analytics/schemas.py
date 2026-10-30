"""Pydantic schemas for the sales analytics endpoints (M6 / T-062).

Money and quantities are Decimal (B12). Pydantic serialises Decimal as a
string in JSON mode, so no value ever passes through a float.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class SalesSummaryGroup(BaseModel):
    """One group of the sales summary (a variant, a product or a category)."""

    group_key: str = Field(..., description="Stable key of the group (SKU, product or category)")
    label: str = Field(..., description="Human readable label of the group")
    revenue: Decimal = Field(..., description="Revenue in INR, sum of quantity * unit price")
    units_sold: Decimal = Field(..., description="Units sold in the window")
    order_count: int = Field(..., description="Distinct orders that contain this group")
    line_count: int = Field(..., description="Order lines that belong to this group")


class SalesSummaryResponse(BaseModel):
    """Owner-only sales summary for a period, optionally grouped."""

    period: str = Field(..., description="Requested period window")
    group_by: str = Field(..., description="Dimension the results are grouped by")
    start_date: date | None = Field(None, description="Effective first day of the window")
    end_date: date | None = Field(None, description="Effective last day of the window")
    total_revenue: Decimal = Field(..., description="Revenue in INR over the whole window")
    total_units_sold: Decimal = Field(..., description="Units sold over the whole window")
    total_order_count: int = Field(..., description="Distinct non-cancelled orders in the window")
    total_line_count: int = Field(..., description="Order lines in the window")
    group_count: int = Field(..., description="Number of groups returned")
    groups: list[SalesSummaryGroup] = Field(
        default_factory=list, description="Groups ordered by revenue descending, then by key"
    )


class SlowMoverItem(BaseModel):
    """One variant of the slow mover report."""

    variant_id: uuid.UUID = Field(..., description="Product variant UUID")
    sku: str = Field(..., description="Product variant SKU")
    product_name: str = Field(..., description="Parent product name")
    units_sold: Decimal = Field(..., description="Units sold inside the window")
    revenue: Decimal = Field(..., description="Revenue inside the window")
    order_count: int = Field(..., description="Distinct non-cancelled orders in the window")
    last_sold_on: date | None = Field(
        None, description="Date of the most recent sale in the window"
    )
    available_stock: Decimal = Field(
        ...,
        description="Sellable stock now: released and unexpired batches only",
    )
    earliest_expiring_batch_code: str | None = Field(
        None, description="Batch code of the earliest expiring sellable batch"
    )
    earliest_expiry_date: date | None = Field(
        None, description="Expiry date of the earliest expiring sellable batch"
    )


class SlowMoversResponse(BaseModel):
    """Owner-only slow mover report for a period."""

    period: str = Field(..., description="Requested period window")
    total_variants: int = Field(..., description="Number of variants reported")
    items: list[SlowMoverItem] = Field(
        default_factory=list, description="Variants ordered by units sold ascending, then by SKU"
    )


__all__ = [
    "SalesSummaryGroup",
    "SalesSummaryResponse",
    "SlowMoverItem",
    "SlowMoversResponse",
]
