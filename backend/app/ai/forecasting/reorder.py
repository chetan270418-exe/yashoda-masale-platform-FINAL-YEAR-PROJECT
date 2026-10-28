"""Reorder recommendation and batch expiry risk engine (spec-pack/implementation.md Section 9).

Formulas:
- daily_demand = forecast_monthly_demand / 30.0
- lead_time_demand = daily_demand * lead_time_days
- safety_stock = daily_demand * safety_days
- reorder_point = lead_time_demand + safety_stock
- if available <= reorder_point: recommend_qty = target_cover_demand - available
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class ReorderRecommendation:
    variant_id: str
    sku: str
    product_name: str
    available_stock: float
    monthly_forecast: float
    daily_demand: float
    lead_time_days: int
    safety_stock: float
    reorder_point: float
    reorder_needed: bool
    recommended_order_quantity: float


def compute_reorder_recommendation(
    variant_id: str,
    sku: str,
    product_name: str,
    available_stock: Decimal | float,
    monthly_forecast: Decimal | float,
    lead_time_days: int = 14,
    safety_days: int = 7,
    target_cover_days: int = 45,
) -> ReorderRecommendation:
    """Compute reorder recommendation with completely transparent visible inputs (Rule 129)."""
    avail = float(available_stock)
    forecast = max(1.0, float(monthly_forecast))

    daily_demand = forecast / 30.0
    lead_time_demand = daily_demand * lead_time_days
    safety_stock = daily_demand * safety_days
    reorder_point = lead_time_demand + safety_stock

    reorder_needed = avail <= reorder_point
    target_cover_demand = daily_demand * target_cover_days

    if reorder_needed:
        recommended_qty = max(0.0, round(target_cover_demand - avail, 1))
    else:
        recommended_qty = 0.0

    return ReorderRecommendation(
        variant_id=str(variant_id),
        sku=sku,
        product_name=product_name,
        available_stock=round(avail, 1),
        monthly_forecast=round(forecast, 1),
        daily_demand=round(daily_demand, 2),
        lead_time_days=lead_time_days,
        safety_stock=round(safety_stock, 1),
        reorder_point=round(reorder_point, 1),
        reorder_needed=reorder_needed,
        recommended_order_quantity=recommended_qty,
    )


def compute_batch_expiry_risk(
    batch_code: str,
    expiry_date: date,
    available_stock: Decimal | float,
    monthly_forecast: Decimal | float,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Flag batches likely to remain unsold before expiry and recommend action."""
    today = reference_date or date.today()
    avail = float(available_stock)
    forecast = max(1.0, float(monthly_forecast))

    days_until_expiry = (expiry_date - today).days
    daily_sales = forecast / 30.0
    projected_consumption = max(0.0, daily_sales * max(0, days_until_expiry))
    potential_unsold = max(0.0, avail - projected_consumption)

    if days_until_expiry <= 0:
        risk_level = "CRITICAL_EXPIRED"
        suggested_action = "QUARANTINE_AND_WRITE_OFF"
    elif potential_unsold > 0.3 * avail:
        risk_level = "HIGH"
        suggested_action = "PRIORITY_DISPATCH_AND_PROMOTION"
    elif potential_unsold > 0:
        risk_level = "MEDIUM"
        suggested_action = "MONITOR_AND_DISCOUNT_OFFER"
    else:
        risk_level = "LOW"
        suggested_action = "STANDARD_FEFO_ROTATION"

    return {
        "batch_code": batch_code,
        "expiry_date": str(expiry_date),
        "days_until_expiry": days_until_expiry,
        "available_stock": round(avail, 1),
        "projected_consumption_until_expiry": round(projected_consumption, 1),
        "estimated_unsold_quantity": round(potential_unsold, 1),
        "risk_level": risk_level,
        "suggested_action": suggested_action,
    }
