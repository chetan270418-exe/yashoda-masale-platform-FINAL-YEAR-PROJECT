"""FastAPI endpoints for AI subsystem: Chat (RAG), Agent, Drafts, Forecasts, Reorder, and Audits."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.ai.forecasting.engine import ForecastingPipeline
from app.ai.forecasting.generator import generate_synthetic_history
from app.ai.forecasting.reorder import compute_reorder_recommendation
from app.ai.llm_adapter import get_llm_adapter
from app.ai.models import AIToolRun, ForecastEvaluation, OrderDraft
from app.ai.rag.service import answer_rag_query
from app.ai.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    ChatRequest,
    ChatResponse,
    DraftApprovalResponse,
    ForecastEvaluationItem,
    ReorderRecommendationItem,
    ToolRunLogItem,
)
from app.ai.tools import execute_tool
from app.auth.dependencies import get_current_user, require_roles
from app.auth.models import User
from app.catalog.models import Product, ProductVariant
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.inventory.models import InventoryBalance, Order, OrderItem, ProductionBatch

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

# Rate limiters (20 req/60s for chat, 30 req/60s for agent query)
ai_chat_rate_limiter = RateLimiter(max_requests=20, window_seconds=60, name="ai_chat")
ai_agent_rate_limiter = RateLimiter(max_requests=30, window_seconds=60, name="ai_agent")


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(ai_chat_rate_limiter)])
def chat_with_rag(
    request: ChatRequest,
) -> ChatResponse:
    """Public / Customer RAG assistant with source citations (Rules 63, 64)."""
    result = answer_rag_query(request.query)
    return ChatResponse(
        answer=result["answer"],
        sources=result["sources"],
        cited_sources=result.get("cited_sources", []),
        invalid_citations=result.get("invalid_citations", 0),
    )


@router.post(
    "/agent/query",
    response_model=AgentQueryResponse,
    dependencies=[Depends(ai_agent_rate_limiter)],
)
def execute_agent_query(
    request: AgentQueryRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentQueryResponse:
    """Execute natural language query via whitelisted tools (Rules 60, 61, 65)."""
    llm = get_llm_adapter()
    prompt = (
        f"User role: {current_user.role.name if current_user.role else 'consumer'}\n"
        f"User request: {request.prompt}\n\n"
        "Available whitelisted tools:\n"
        "- check_stock(sku: str | None)\n"
        "- get_sales_summary(period: str, group_by: str) [owner only]\n"
        "- get_slow_movers(threshold_days: int) [owner only]\n"
        "- get_dealer_history(dealer_id: UUID | None) [dealer sees own, owner sees all]\n"
        "- create_order_draft(items: list) [creates draft requiring confirmation]\n\n"
        "Decide which tool to invoke. Output a JSON object with keys 'tool' and 'arguments'."
    )
    decision = llm.generate_json(prompt)

    tool_name = decision.get("tool")
    arguments = decision.get("arguments", {})

    if not tool_name:
        # Fallback to direct conversational response
        text = llm.generate(request.prompt)
        return AgentQueryResponse(message=text, tool_used=None, result=None)

    # Safely execute whitelisted tool with user permissions and automatic audit logging
    tool_result = execute_tool(db=db, user=current_user, tool_name=tool_name, args=arguments)
    return AgentQueryResponse(
        message=f"Executed tool '{tool_name}' successfully.",
        tool_used=tool_name,
        result=tool_result,
    )


@router.post("/drafts/{draft_id}/approve", response_model=DraftApprovalResponse)
def approve_order_draft(
    draft_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DraftApprovalResponse:
    """Human-in-the-loop: dealer or consumer approves AI order draft (Rule 62)."""
    draft = db.scalar(select(OrderDraft).where(OrderDraft.id == draft_id).with_for_update())
    if not draft:
        raise AppError(code="DRAFT_NOT_FOUND", message="Order draft not found.", status_code=404)

    user_role = current_user.role.name if current_user.role else ""
    if user_role != "owner" and draft.customer_id != current_user.id:
        raise AppError(
            code="FORBIDDEN", message="You do not own this order draft.", status_code=403
        )

    if draft.status != "draft":
        raise AppError(
            code="DRAFT_ALREADY_PROCESSED",
            message=f"Draft is already '{draft.status}'.",
            status_code=400,
        )

    from app.audit.service import log_action
    from app.orders.service import reserve_order

    # 1. Update draft status to approved
    draft.status = "approved"

    # 2. Create actual order in orders table (M5 Model)
    order_num = f"ORD-AI-{uuid.uuid4().hex[:8].upper()}"
    draft_total = Decimal(str(draft.total_estimated or "0.00")).quantize(Decimal("0.01"))
    order = Order(
        id=uuid.uuid4(),
        order_number=order_num,
        customer_id=draft.customer_id,
        order_type="sales",
        status="pending",
        idempotency_key=f"ai-draft-{draft.id}",
        total_amount=draft_total,
        currency="INR",
    )
    db.add(order)
    db.flush()

    # 3. Add order items with immutable price snapshot (Invariants B8, B12)
    accumulated_total = Decimal("0.00")
    for it in draft.items:
        v_id = (
            uuid.UUID(it["variant_id"]) if isinstance(it["variant_id"], str) else it["variant_id"]
        )
        qty = Decimal(str(it["quantity"]))
        unit_price = Decimal(str(it.get("unit_price", "0.00"))).quantize(Decimal("0.01"))
        line_total = Decimal(str(it.get("line_total", unit_price * qty))).quantize(Decimal("0.01"))
        price_list_code = str(
            it.get("price_list_code", "DEALER" if user_role == "dealer" else "MRP")
        )
        accumulated_total += line_total

        item = OrderItem(
            id=uuid.uuid4(),
            order_id=order.id,
            variant_id=v_id,
            quantity=qty,
            unit_price_snapshot=unit_price,
            price_list_code=price_list_code,
            line_total=line_total,
        )
        db.add(item)

    if draft_total == Decimal("0.00") and accumulated_total > Decimal("0.00"):
        order.total_amount = accumulated_total

    draft_id = draft.id

    # Persist the order and its line items. The stock reservation below runs as a
    # separate, all-or-nothing transaction: a multi-line order either reserves
    # every line or none of them (Invariant B4).
    db.commit()

    # 4. Initiate FEFO stock reservation upon draft approval (Requirement R3).
    #    An order that cannot be reserved stays in 'open' status and awaits
    #    fulfilment. The outcome is never silent: it is reported in the response
    #    and recorded in the audit log.
    reservation_status = "reserved"
    reservation_message = f"Stock reserved for order {order_num}."
    try:
        reserve_order(db=db, order_id=order.id, actor_id=current_user.id)
    except AppError as exc:
        reservation_status = "not_reserved"
        reservation_message = (
            f"Order {order_num} was created but stock could not be reserved: "
            f"{exc.message} The order stays in 'pending' status until stock is available."
        )
        log_action(
            db,
            actor_id=current_user.id,
            action="APPROVE_DRAFT_RESERVATION_FAILED",
            entity="order_drafts",
            entity_id=draft_id,
            before={"status": "draft"},
            after={
                "status": "approved",
                "reservation_status": reservation_status,
                "error_code": exc.code,
                "reason": exc.message,
            },
        )
        db.commit()

    db.refresh(order)

    return DraftApprovalResponse(
        draft_id=draft.id,
        status="approved",
        message=f"Order {order_num} has been successfully submitted and placed.",
        order_id=order.id,
        reservation_status=reservation_status,
        reservation_message=reservation_message,
    )


@router.get("/forecasts", response_model=list[ForecastEvaluationItem])
def get_forecast_evaluations(
    current_user: Annotated[User, Depends(require_roles("owner", "warehouse", "factory"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[ForecastEvaluationItem]:
    """Staff/Owner: Retrieve latest forecast evaluations comparing models to baselines (Rule 66)."""
    evals = db.scalars(
        select(ForecastEvaluation).order_by(desc(ForecastEvaluation.created_at)).limit(20)
    ).all()

    # If empty, run the pipeline on the fly to generate baseline benchmarks
    if not evals:
        records = generate_synthetic_history(variant_sku="GM-250G", base_demand=500.0)
        pipeline = ForecastingPipeline(records)
        pipeline.run_evaluations(db=db)
        evals = db.scalars(
            select(ForecastEvaluation).order_by(desc(ForecastEvaluation.created_at)).limit(20)
        ).all()

    return [
        ForecastEvaluationItem(
            id=e.id,
            model=e.model,
            mae=e.mae,
            rmse=e.rmse,
            mape=e.mape,
            test_period=e.test_period,
            notes=e.notes,
        )
        for e in evals
    ]


@router.get("/recommendations/reorder", response_model=list[ReorderRecommendationItem])
def get_reorder_recommendations(
    current_user: Annotated[User, Depends(require_roles("owner", "warehouse", "factory"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[ReorderRecommendationItem]:
    """Staff/Owner: Retrieve reorder recommendations with transparent inputs (Rule 129)."""
    # Fetch variants and compute available stock
    variants = db.scalars(select(ProductVariant).join(Product)).all()
    recommendations = []

    for v in variants:
        avail = db.scalar(
            select(
                func.coalesce(
                    func.sum(InventoryBalance.on_hand - InventoryBalance.reserved), Decimal("0")
                )
            )
            .join(ProductionBatch, ProductionBatch.id == InventoryBalance.batch_id)
            .where(ProductionBatch.variant_id == v.id, ProductionBatch.status == "released")
        ) or Decimal("0")

        # Demand forecast proxy (500 packs monthly baseline)
        rec = compute_reorder_recommendation(
            variant_id=str(v.id),
            sku=v.sku,
            product_name=v.product.name if v.product else "Spice",
            available_stock=avail,
            monthly_forecast=500.0,
            lead_time_days=14,
            safety_days=7,
        )
        recommendations.append(
            ReorderRecommendationItem(
                variant_id=rec.variant_id,
                sku=rec.sku,
                product_name=rec.product_name,
                available_stock=rec.available_stock,
                monthly_forecast=rec.monthly_forecast,
                daily_demand=rec.daily_demand,
                lead_time_days=rec.lead_time_days,
                safety_stock=rec.safety_stock,
                reorder_point=rec.reorder_point,
                reorder_needed=rec.reorder_needed,
                recommended_order_quantity=rec.recommended_order_quantity,
            )
        )

    return recommendations


@router.get("/tool-runs", response_model=list[ToolRunLogItem])
def get_tool_runs(
    current_user: Annotated[User, Depends(require_roles("owner"))],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ToolRunLogItem]:
    """Owner only: Audit log of all AI tool executions (Rule 65)."""
    logs = db.scalars(select(AIToolRun).order_by(desc(AIToolRun.created_at)).limit(limit)).all()
    return [
        ToolRunLogItem(
            id=log.id,
            user_id=log.user_id,
            tool=log.tool,
            args=log.args,
            result=log.result,
            status=log.status,
            error_message=log.error_message,
            created_at=str(log.created_at),
        )
        for log in logs
    ]
