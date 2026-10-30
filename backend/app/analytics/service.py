"""Sales analytics service layer (M6 / T-062).

Pure reporting over the transactional tables. Every function returns plain
data structures (dicts and lists of Decimal), so the router can serialise the
same result as JSON or as CSV without recomputing anything.

Behavioural notes:
- Cancelled orders are excluded, exactly like the AI tool
  ``tool_get_sales_summary`` in ``app/ai/tools.py`` does.
- Money is Decimal everywhere (B12); no float is used for a monetary value.
- Ordering is fully deterministic: revenue descending with a stable key
  tiebreak for the sales summary, units ascending with a SKU tiebreak for the
  slow movers.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import Category, Product, ProductVariant
from app.inventory.models import InventoryBalance, Order, OrderItem, ProductionBatch

# Supported reporting windows.
PERIOD_DAYS: dict[str, int] = {
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 90,
}
PERIOD_ALL_TIME = "all_time"

# Supported grouping dimensions.
GROUP_BY_VARIANT = "variant"
GROUP_BY_PRODUCT = "product"
GROUP_BY_CATEGORY = "category"
GROUP_BY_VALUES: tuple[str, ...] = (GROUP_BY_VARIANT, GROUP_BY_PRODUCT, GROUP_BY_CATEGORY)

# Reporting scales. Money is rounded to paise, quantities to the storage scale
# of order_items.quantity (Numeric 14, 3).
REVENUE_SCALE = Decimal("0.01")
QUANTITY_SCALE = Decimal("0.001")

_CANCELLED = "cancelled"


def _quantize_money(value: Any) -> Decimal:
    """Coerce a database numeric into a 2 decimal place money value."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(REVENUE_SCALE)


def _quantize_quantity(value: Any) -> Decimal:
    """Coerce a database numeric into a 3 decimal place quantity."""
    if value is None:
        return Decimal("0.000")
    return Decimal(str(value)).quantize(QUANTITY_SCALE)


def resolve_window(
    *,
    period: str = "last_30_days",
    start_date: date | None = None,
    end_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve the effective reporting window.

    An explicit ``start_date`` / ``end_date`` pair overrides the period
    shortcut. ``all_time`` means no lower bound.
    """
    moment = now or datetime.now(UTC)

    if start_date is not None or end_date is not None:
        effective_start = start_date
        effective_end = end_date
    elif period == PERIOD_ALL_TIME:
        effective_start = None
        effective_end = None
    else:
        days = PERIOD_DAYS.get(period, PERIOD_DAYS["last_30_days"])
        effective_start = (moment - timedelta(days=days)).date()
        effective_end = moment.date()

    start_dt = datetime.combine(effective_start, time.min, tzinfo=UTC) if effective_start else None
    end_dt = (
        datetime.combine(effective_end + timedelta(days=1), time.min, tzinfo=UTC)
        if effective_end
        else None
    )

    return {
        "period": period,
        "start_date": effective_start,
        "end_date": effective_end,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }


def _sales_filters(window: dict[str, Any]) -> list[Any]:
    """Build the shared WHERE clauses of every sales query."""
    filters: list[Any] = [Order.status != _CANCELLED]
    if window["start_dt"] is not None:
        filters.append(Order.created_at >= window["start_dt"])
    if window["end_dt"] is not None:
        filters.append(Order.created_at < window["end_dt"])
    return filters


def _revenue_expression() -> Any:
    """Revenue of a line: quantity multiplied by the frozen unit price (B8)."""
    return func.sum(OrderItem.quantity * OrderItem.unit_price_snapshot)


def get_sales_summary(
    db: Session,
    *,
    period: str = "last_30_days",
    group_by: str = GROUP_BY_VARIANT,
    start_date: date | None = None,
    end_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the sales summary for a window, grouped by variant, product or category.

    Cancelled orders are excluded. The result contains the window totals and
    one entry per group, ordered by revenue descending with the group key as a
    stable tiebreak.
    """
    if group_by not in GROUP_BY_VALUES:
        raise ValueError(f"Unsupported group_by '{group_by}'.")

    window = resolve_window(period=period, start_date=start_date, end_date=end_date, now=now)
    filters = _sales_filters(window)

    totals_row = db.execute(
        select(
            func.coalesce(_revenue_expression(), Decimal("0")).label("revenue"),
            func.coalesce(func.sum(OrderItem.quantity), Decimal("0")).label("units"),
            func.count(func.distinct(OrderItem.order_id)).label("order_count"),
            func.count(OrderItem.id).label("line_count"),
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(*filters)
    ).one()

    revenue_expr = _revenue_expression()
    units_expr = func.sum(OrderItem.quantity)

    if group_by == GROUP_BY_PRODUCT:
        key_expr = Product.name
        label_expr = Product.name
        group_exprs = (Product.id, Product.name)
        joins = [
            (Order, Order.id == OrderItem.order_id),
            (ProductVariant, ProductVariant.id == OrderItem.variant_id),
            (Product, Product.id == ProductVariant.product_id),
        ]
    elif group_by == GROUP_BY_CATEGORY:
        key_expr = Category.name
        label_expr = Category.name
        group_exprs = (Category.id, Category.name)
        joins = [
            (Order, Order.id == OrderItem.order_id),
            (ProductVariant, ProductVariant.id == OrderItem.variant_id),
            (Product, Product.id == ProductVariant.product_id),
            (Category, Category.id == Product.category_id),
        ]
    else:
        key_expr = ProductVariant.sku
        label_expr = Product.name
        group_exprs = (ProductVariant.id, ProductVariant.sku, Product.name)
        joins = [
            (Order, Order.id == OrderItem.order_id),
            (ProductVariant, ProductVariant.id == OrderItem.variant_id),
            (Product, Product.id == ProductVariant.product_id),
        ]

    query = select(
        key_expr.label("group_key"),
        label_expr.label("label"),
        revenue_expr.label("revenue"),
        units_expr.label("units"),
        func.count(func.distinct(OrderItem.order_id)).label("order_count"),
        func.count(OrderItem.id).label("line_count"),
    ).select_from(OrderItem)
    for target, condition in joins:
        query = query.join(target, condition)
    query = query.where(*filters).group_by(*group_exprs)
    query = query.order_by(revenue_expr.desc(), key_expr.asc())

    rows = db.execute(query).all()

    groups = [
        {
            "group_key": row.group_key,
            "label": row.label,
            "revenue": _quantize_money(row.revenue),
            "units_sold": _quantize_quantity(row.units),
            "order_count": int(row.order_count or 0),
            "line_count": int(row.line_count or 0),
        }
        for row in rows
    ]

    return {
        "period": window["period"],
        "group_by": group_by,
        "start_date": window["start_date"],
        "end_date": window["end_date"],
        "total_revenue": _quantize_money(totals_row.revenue),
        "total_units_sold": _quantize_quantity(totals_row.units),
        "total_order_count": int(totals_row.order_count or 0),
        "total_line_count": int(totals_row.line_count or 0),
        "group_count": len(groups),
        "groups": groups,
    }


def get_slow_movers(
    db: Session,
    *,
    period: str = "last_30_days",
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the variants with the lowest sales inside a window.

    ``available_stock`` only counts released batches that have not expired:
    held, rejected, recalled and expired batches are excluded (B2). The
    earliest expiring sellable batch of each variant is reported as well, so
    the report doubles as an expiry warning.
    """
    moment = now or datetime.now(UTC)
    window = resolve_window(period=period, now=moment)
    filters = _sales_filters(window)
    today = moment.date()

    sales_rows = db.execute(
        select(
            OrderItem.variant_id,
            func.coalesce(func.sum(OrderItem.quantity), Decimal("0")).label("units"),
            func.coalesce(_revenue_expression(), Decimal("0")).label("revenue"),
            func.count(func.distinct(OrderItem.order_id)).label("order_count"),
            func.max(Order.created_at).label("last_sold_at"),
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(*filters)
        .group_by(OrderItem.variant_id)
    ).all()
    sales_by_variant = {row.variant_id: row for row in sales_rows}

    stock_rows = db.execute(
        select(
            ProductionBatch.variant_id,
            ProductionBatch.batch_code,
            ProductionBatch.expiry_date,
            func.coalesce(
                func.sum(InventoryBalance.on_hand - InventoryBalance.reserved), Decimal("0")
            ).label("available"),
        )
        .select_from(ProductionBatch)
        .join(InventoryBalance, InventoryBalance.batch_id == ProductionBatch.id)
        .where(
            ProductionBatch.status == "released",
            ProductionBatch.expiry_date >= today,
        )
        .group_by(
            ProductionBatch.variant_id,
            ProductionBatch.batch_code,
            ProductionBatch.expiry_date,
        )
        .order_by(
            ProductionBatch.variant_id.asc(),
            ProductionBatch.expiry_date.asc(),
            ProductionBatch.batch_code.asc(),
        )
    ).all()

    available_by_variant: dict[uuid.UUID, Decimal] = {}
    earliest_batch_by_variant: dict[uuid.UUID, dict[str, Any]] = {}
    for row in stock_rows:
        available = _quantize_quantity(row.available)
        available_by_variant[row.variant_id] = (
            available_by_variant.get(row.variant_id, Decimal("0.000")) + available
        )
        if available > 0 and row.variant_id not in earliest_batch_by_variant:
            earliest_batch_by_variant[row.variant_id] = {
                "batch_code": row.batch_code,
                "expiry_date": row.expiry_date,
            }

    variant_rows = db.execute(
        select(ProductVariant.id, ProductVariant.sku, Product.name)
        .select_from(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.is_active.is_(True), Product.is_active.is_(True))
    ).all()

    items: list[dict[str, Any]] = []
    for variant_id, sku, product_name in variant_rows:
        sales = sales_by_variant.get(variant_id)
        units = _quantize_quantity(sales.units) if sales else Decimal("0.000")
        revenue = _quantize_money(sales.revenue) if sales else Decimal("0.00")
        available = available_by_variant.get(variant_id, Decimal("0.000"))

        if sales is None and available <= 0:
            # Neither sold in the window nor sellable stock: not a slow mover.
            continue

        last_sold_at = sales.last_sold_at if sales else None
        earliest = earliest_batch_by_variant.get(variant_id)
        items.append(
            {
                "variant_id": variant_id,
                "sku": sku,
                "product_name": product_name,
                "units_sold": units,
                "revenue": revenue,
                "order_count": int(sales.order_count or 0) if sales else 0,
                "last_sold_on": last_sold_at.date() if last_sold_at else None,
                "available_stock": available,
                "earliest_expiring_batch_code": earliest["batch_code"] if earliest else None,
                "earliest_expiry_date": earliest["expiry_date"] if earliest else None,
            }
        )

    items.sort(key=lambda item: (item["units_sold"], item["revenue"], item["sku"]))
    return items[:limit]


def sales_summary_csv_rows(summary: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    """Flatten a sales summary into CSV rows.

    The rows are derived from the very same summary dictionary the JSON
    endpoint returns, so the export can never disagree with the screen.
    """
    group_by = summary["group_by"]
    if group_by == GROUP_BY_PRODUCT:
        fields = ["product_name", "revenue", "units_sold", "order_count", "line_count"]
    elif group_by == GROUP_BY_CATEGORY:
        fields = ["category_name", "revenue", "units_sold", "order_count", "line_count"]
    else:
        fields = ["sku", "product_name", "revenue", "units_sold", "order_count", "line_count"]

    rows: list[dict[str, str]] = []
    for group in summary["groups"]:
        row = {
            "revenue": str(group["revenue"]),
            "units_sold": str(group["units_sold"]),
            "order_count": str(group["order_count"]),
            "line_count": str(group["line_count"]),
        }
        if group_by == GROUP_BY_PRODUCT:
            row["product_name"] = group["group_key"]
        elif group_by == GROUP_BY_CATEGORY:
            row["category_name"] = group["group_key"]
        else:
            row["sku"] = group["group_key"]
            row["product_name"] = group["label"]
        rows.append(row)

    return fields, rows


__all__ = [
    "GROUP_BY_CATEGORY",
    "GROUP_BY_PRODUCT",
    "GROUP_BY_VARIANT",
    "GROUP_BY_VALUES",
    "PERIOD_ALL_TIME",
    "PERIOD_DAYS",
    "get_sales_summary",
    "get_slow_movers",
    "resolve_window",
    "sales_summary_csv_rows",
]
