"""Inventory HTTP endpoints: expiry alerts, low-stock alerts, manual adjustments."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.auth.models import User
from app.core.db import get_db
from app.inventory import service
from app.inventory.schemas import (
    ExpiryAlertResponse,
    LowStockAlertResponse,
    StockAdjustmentRequest,
    StockAdjustmentResponse,
)

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

# Singletons to prevent FastAPI dependency re-instantiation / Ruff B008 warnings
require_inventory_reader = require_roles("owner", "warehouse", "factory")
require_inventory_adjuster = require_roles("owner", "warehouse")


@router.get("/alerts/expiry", response_model=ExpiryAlertResponse)
def get_expiry_alerts(
    warehouse_id: uuid.UUID | None = Query(None, description="Optional warehouse ID filter"),
    days_ahead: int = Query(30, ge=1, le=365, description="Lookahead window in days (1 to 365)"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_inventory_reader),
) -> ExpiryAlertResponse:
    """Query released batches nearing expiry within days_ahead in active warehouses (B1, B2)."""
    items = service.get_expiry_alerts(db, warehouse_id=warehouse_id, days_ahead=days_ahead)
    return ExpiryAlertResponse(days_ahead=days_ahead, total=len(items), items=items)


@router.get("/alerts/low-stock", response_model=LowStockAlertResponse)
def get_low_stock_alerts(
    warehouse_id: uuid.UUID | None = Query(None, description="Optional warehouse ID filter"),
    threshold: Decimal = Query(Decimal("50"), ge=0, description="Available stock alert threshold"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_inventory_reader),
) -> LowStockAlertResponse:
    """Query product variants with available stock at or below threshold across released batches."""
    items = service.get_low_stock_alerts(db, warehouse_id=warehouse_id, threshold=threshold)
    return LowStockAlertResponse(threshold=threshold, total=len(items), items=items)


@router.post("/adjustments", response_model=StockAdjustmentResponse, status_code=200)
def adjust_inventory_stock(
    body: StockAdjustmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_inventory_adjuster),
) -> StockAdjustmentResponse:
    """Manually adjust stock balance with reason category and audit trail (T-043, B5)."""
    reason = (
        body.reason_category.value
        if hasattr(body.reason_category, "value")
        else str(body.reason_category)
    )
    balance = service.adjust_stock(
        db=db,
        warehouse_id=body.warehouse_id,
        batch_id=body.batch_id,
        qty_delta=body.qty_delta,
        reason_category=reason,
        notes=body.notes,
        actor_id=current_user.id,
    )
    return StockAdjustmentResponse(
        balance_id=balance.id,
        warehouse_id=balance.warehouse_id,
        batch_id=balance.batch_id,
        on_hand=balance.on_hand,
        reserved=balance.reserved,
        available=balance.available,
    )
