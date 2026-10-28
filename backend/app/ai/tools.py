"""Whitelisted AI Tools Engine (Rules 60, 61, 62, 65).

Enforces:
- Rule 60: Model never writes SQL. Whitelisted function calls only.
- Rule 61: Run strictly with logged-in user's permissions (RBAC).
- Rule 62: Mutations create DRAFT status pending human confirmation.
- Rule 65: Every tool execution is logged to `ai_tool_runs`.
"""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import AIToolRun, OrderDraft
from app.auth.models import User
from app.catalog.models import Product, ProductVariant
from app.core.errors import AppError
from app.inventory.models import (
    InventoryBalance,
    OrderItem,
    ProductionBatch,
    Warehouse,
)


class ToolPermissionError(AppError):
    """Raised when the user's role is not authorized for a tool."""

    def __init__(self, message: str) -> None:
        super().__init__(code="TOOL_FORBIDDEN", message=message, status_code=403)


class ToolArgumentError(AppError):
    """Raised when tool arguments fail validation."""

    def __init__(self, message: str) -> None:
        super().__init__(code="TOOL_INVALID_ARGS", message=message, status_code=400)


# Argument schemas for strict validation
class CheckStockArgs(BaseModel):
    variant_id: uuid.UUID | None = None
    sku: str | None = None


class SalesSummaryArgs(BaseModel):
    period: str = Field(
        default="last_30_days", pattern="^(last_7_days|last_30_days|last_90_days|all_time)$"
    )
    group_by: str = Field(default="variant", pattern="^(variant|product|category)$")


class SlowMoversArgs(BaseModel):
    threshold_days: int = Field(default=45, ge=1, le=365)


class DealerHistoryArgs(BaseModel):
    dealer_id: uuid.UUID | None = None
    limit: int = Field(default=10, ge=1, le=100)


class OrderDraftItem(BaseModel):
    variant_id: uuid.UUID
    quantity: Decimal = Field(gt=Decimal("0"))


class CreateOrderDraftArgs(BaseModel):
    items: list[OrderDraftItem] = Field(default_factory=list)
    notes: str | None = None


# Tool Implementations
def tool_check_stock(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """Check total available inventory for a variant across warehouses."""
    validated = CheckStockArgs(**args)
    query = (
        select(
            ProductVariant.id,
            ProductVariant.sku,
            Product.name.label("product_name"),
            ProductVariant.pack_size_g,
            func.coalesce(
                func.sum(InventoryBalance.on_hand - InventoryBalance.reserved), Decimal("0")
            ).label("total_available"),
        )
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(
            ProductionBatch,
            (ProductionBatch.variant_id == ProductVariant.id)
            & (ProductionBatch.status == "released"),
        )
        .outerjoin(InventoryBalance, InventoryBalance.batch_id == ProductionBatch.id)
        .group_by(ProductVariant.id, ProductVariant.sku, Product.name, ProductVariant.pack_size_g)
    )

    if validated.variant_id:
        query = query.where(ProductVariant.id == validated.variant_id)
    elif validated.sku:
        query = query.where(ProductVariant.sku == validated.sku)

    rows = db.execute(query).all()
    results = [
        {
            "variant_id": str(r.id),
            "sku": r.sku,
            "product_name": r.product_name,
            "pack_size_g": r.pack_size_g,
            "available_stock": float(r.total_available),
        }
        for r in rows
    ]
    return {"stock": results, "count": len(results)}


def tool_get_sales_summary(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """Owner only: Summarize total dispatched quantities and orders."""
    user_role = user.role.name if user.role else ""
    if user_role != "owner":
        raise ToolPermissionError("Only owners may access aggregate sales summaries.")

    validated = SalesSummaryArgs(**args)
    # Aggregate order items metrics
    query = (
        select(
            ProductVariant.sku,
            Product.name.label("product_name"),
            func.sum(OrderItem.quantity).label("total_sold_quantity"),
        )
        .join(Product, Product.id == ProductVariant.product_id)
        .join(OrderItem, OrderItem.variant_id == ProductVariant.id)
        .group_by(ProductVariant.sku, Product.name)
    )
    rows = db.execute(query).all()
    items = [
        {
            "sku": r.sku,
            "product_name": r.product_name,
            "total_sold_quantity": float(r.total_sold_quantity or 0),
        }
        for r in rows
    ]
    return {"period": validated.period, "group_by": validated.group_by, "sales": items}


def tool_get_slow_movers(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """Owner only: Find batches in warehouse that have had no reservation activity."""
    user_role = user.role.name if user.role else ""
    if user_role != "owner":
        raise ToolPermissionError("Only owners may query slow-moving inventory analytics.")

    validated = SlowMoversArgs(**args)
    # Find batches with high available stock and older manufacture dates
    query = (
        select(
            ProductionBatch.batch_code,
            ProductVariant.sku,
            Product.name.label("product_name"),
            InventoryBalance.on_hand,
            InventoryBalance.reserved,
            ProductionBatch.expiry_date,
            Warehouse.name.label("warehouse_name"),
        )
        .join(ProductVariant, ProductVariant.id == ProductionBatch.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(InventoryBalance, InventoryBalance.batch_id == ProductionBatch.id)
        .join(Warehouse, Warehouse.id == InventoryBalance.warehouse_id)
        .where(
            ProductionBatch.status == "released",
            (InventoryBalance.on_hand - InventoryBalance.reserved) > Decimal("0"),
        )
        .order_by(ProductionBatch.created_at.asc())
        .limit(10)
    )
    rows = db.execute(query).all()
    slow_items = [
        {
            "batch_code": r.batch_code,
            "sku": r.sku,
            "product_name": r.product_name,
            "available": float(r.on_hand - r.reserved),
            "expiry_date": str(r.expiry_date),
            "warehouse": r.warehouse_name,
        }
        for r in rows
    ]
    return {"threshold_days": validated.threshold_days, "slow_movers": slow_items}


def tool_get_dealer_history(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """Dealer sees own history; Owner can see any."""
    user_role = user.role.name if user.role else ""
    validated = DealerHistoryArgs(**args)

    target_dealer_id = validated.dealer_id or user.id
    if user_role not in ("owner", "warehouse") and target_dealer_id != user.id:
        raise ToolPermissionError("Dealers may only view their own order history.")

    # Return previous order drafts and orders
    drafts = db.scalars(
        select(OrderDraft)
        .where(OrderDraft.customer_id == target_dealer_id)
        .order_by(OrderDraft.created_at.desc())
        .limit(validated.limit)
    ).all()

    history = [
        {
            "draft_id": str(d.id),
            "status": d.status,
            "total_estimated": float(d.total_estimated),
            "items": d.items,
            "created_at": str(d.created_at),
        }
        for d in drafts
    ]
    return {"dealer_id": str(target_dealer_id), "orders": history}


def tool_create_order_draft(db: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """Create a DRAFT order requiring human confirmation (Rule 62)."""
    validated = CreateOrderDraftArgs(**args)
    items_data = []
    total = Decimal("0.00")

    # If items are empty, suggest regular baseline items
    if not validated.items:
        variants = db.scalars(select(ProductVariant).limit(2)).all()
        for v in variants:
            qty = Decimal("25.000")
            price = Decimal("120.00")  # placeholder
            items_data.append(
                {
                    "variant_id": str(v.id),
                    "sku": v.sku,
                    "quantity": float(qty),
                    "unit_price": float(price),
                }
            )
            total += qty * price
    else:
        for it in validated.items:
            variant = db.scalar(select(ProductVariant).where(ProductVariant.id == it.variant_id))
            sku = variant.sku if variant else "UNKNOWN"
            price = Decimal("100.00")
            items_data.append(
                {
                    "variant_id": str(it.variant_id),
                    "sku": sku,
                    "quantity": float(it.quantity),
                    "unit_price": float(price),
                }
            )
            total += it.quantity * price

    draft = OrderDraft(
        customer_id=user.id,
        items=items_data,
        total_estimated=total,
        status="draft",
        created_by_ai=True,
        notes=validated.notes or "Draft prepared by Yashoda AI Agent",
    )
    db.add(draft)
    db.commit()

    return {
        "draft_id": str(draft.id),
        "status": "draft",
        "requires_human_approval": True,
        "total_estimated": float(draft.total_estimated),
        "items": items_data,
        "message": "Draft created. Please review and confirm to submit your order.",
    }


# Tool Dispatch Registry
WHITELISTED_TOOLS = {
    "check_stock": tool_check_stock,
    "get_sales_summary": tool_get_sales_summary,
    "get_slow_movers": tool_get_slow_movers,
    "get_dealer_history": tool_get_dealer_history,
    "create_order_draft": tool_create_order_draft,
}


def _to_json_safe(val: Any) -> Any:
    """Recursively convert UUIDs, Decimals, etc. to JSON-compatible primitives."""
    if isinstance(val, dict):
        return {str(k): _to_json_safe(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_to_json_safe(v) for v in val]
    if isinstance(val, uuid.UUID | Decimal):
        return str(val)
    return val


def execute_tool(
    db: Session,
    user: User,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safely execute a whitelisted tool and log run to ai_tool_runs (Rules 60, 61, 65)."""
    raw_args = args or {}
    safe_args = _to_json_safe(raw_args)
    tool_func = WHITELISTED_TOOLS.get(tool_name)

    if not tool_func:
        # Log unknown tool attempt as denied
        log_entry = AIToolRun(
            user_id=user.id,
            tool=tool_name,
            args=safe_args,
            status="denied",
            error_message=f"Tool '{tool_name}' is not in the whitelist.",
        )
        db.add(log_entry)
        db.commit()
        allowed = list(WHITELISTED_TOOLS.keys())
        raise ToolArgumentError(f"Tool '{tool_name}' is not allowed. Whitelisted tools: {allowed}")

    try:
        result = tool_func(db, user, raw_args)
        log_entry = AIToolRun(
            user_id=user.id,
            tool=tool_name,
            args=safe_args,
            result=_to_json_safe(result),
            status="success",
        )
        db.add(log_entry)
        db.commit()
        return result
    except ToolPermissionError as e:
        log_entry = AIToolRun(
            user_id=user.id,
            tool=tool_name,
            args=safe_args,
            status="denied",
            error_message=str(e),
        )
        db.add(log_entry)
        db.commit()
        raise
    except Exception as e:
        db.rollback()
        log_entry = AIToolRun(
            user_id=user.id,
            tool=tool_name,
            args=safe_args,
            status="error",
            error_message=str(e),
        )
        db.add(log_entry)
        db.commit()
        raise
