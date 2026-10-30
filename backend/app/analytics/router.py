"""Sales analytics HTTP endpoints (M6 / T-062).

All endpoints are owner only: they expose revenue figures, so they are not
available to dealers, distributors or consumers.

    GET /api/v1/analytics/sales-summary       - revenue / units / orders for a window
    GET /api/v1/analytics/sales-summary.csv   - the same summary as a CSV download
    GET /api/v1/analytics/slow-movers         - lowest selling variants and their stock
"""

import csv
import io
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.analytics import service
from app.analytics.schemas import SalesSummaryResponse, SlowMoverItem, SlowMoversResponse
from app.auth.dependencies import require_roles
from app.auth.models import User
from app.core.db import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

PeriodLiteral = Literal["last_7_days", "last_30_days", "last_90_days", "all_time"]
GroupByLiteral = Literal["variant", "product", "category"]

# Reusable role dependency (singleton avoids the Ruff B008 warning).
require_analytics_owner = require_roles("owner")


def _summary_response(summary: dict) -> SalesSummaryResponse:
    """Build the response model from the service result (single source of truth)."""
    return SalesSummaryResponse(
        period=summary["period"],
        group_by=summary["group_by"],
        start_date=summary["start_date"],
        end_date=summary["end_date"],
        total_revenue=summary["total_revenue"],
        total_units_sold=summary["total_units_sold"],
        total_order_count=summary["total_order_count"],
        total_line_count=summary["total_line_count"],
        group_count=summary["group_count"],
        groups=summary["groups"],
    )


@router.get(
    "/sales-summary",
    response_model=SalesSummaryResponse,
    summary="Revenue, units and orders for a period (owner only)",
)
def sales_summary(
    current_user: Annotated[User, Depends(require_analytics_owner)],
    db: Annotated[Session, Depends(get_db)],
    period: Annotated[PeriodLiteral, Query(description="Reporting window")] = "last_30_days",
    group_by: Annotated[GroupByLiteral, Query(description="Grouping dimension")] = "variant",
    start_date: Annotated[
        date | None, Query(description="Explicit first day of the window (overrides period)")
    ] = None,
    end_date: Annotated[
        date | None, Query(description="Explicit last day of the window (overrides period)")
    ] = None,
) -> SalesSummaryResponse:
    """Owner only: sales summary over a period, grouped by variant, product or category.

    Cancelled orders are excluded. The results are ordered by revenue
    descending with a stable tiebreak, so repeated calls return the same
    order.
    """
    summary = service.get_sales_summary(
        db,
        period=period,
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
    )
    return _summary_response(summary)


@router.get(
    "/sales-summary.csv",
    response_class=StreamingResponse,
    summary="Sales summary as CSV download (owner only)",
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def sales_summary_csv(
    current_user: Annotated[User, Depends(require_analytics_owner)],
    db: Annotated[Session, Depends(get_db)],
    period: Annotated[PeriodLiteral, Query(description="Reporting window")] = "last_30_days",
    group_by: Annotated[GroupByLiteral, Query(description="Grouping dimension")] = "variant",
    start_date: Annotated[
        date | None, Query(description="Explicit first day of the window (overrides period)")
    ] = None,
    end_date: Annotated[
        date | None, Query(description="Explicit last day of the window (overrides period)")
    ] = None,
) -> StreamingResponse:
    """Owner only: the very same summary as the JSON endpoint, exported as CSV.

    The rows are produced by the same service call as the JSON endpoint, so
    the download can never disagree with the screen.
    """
    summary = service.get_sales_summary(
        db,
        period=period,
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
    )
    fields, rows = service.sales_summary_csv_rows(summary)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)

    filename = f"sales-summary-{group_by}-{period}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/slow-movers",
    response_model=SlowMoversResponse,
    summary="Lowest selling variants with their sellable stock (owner only)",
)
def slow_movers(
    current_user: Annotated[User, Depends(require_analytics_owner)],
    db: Annotated[Session, Depends(get_db)],
    period: Annotated[PeriodLiteral, Query(description="Reporting window")] = "last_30_days",
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of variants")] = 20,
) -> SlowMoversResponse:
    """Owner only: variants with the lowest sales inside the window.

    The reported stock only counts released batches that have not expired;
    held and expired batches are excluded (B2). The earliest expiring
    sellable batch of every variant is reported as well.
    """
    items = service.get_slow_movers(db, period=period, limit=limit)
    return SlowMoversResponse(
        period=period,
        total_variants=len(items),
        items=[SlowMoverItem(**item) for item in items],
    )


__all__ = ["router"]
