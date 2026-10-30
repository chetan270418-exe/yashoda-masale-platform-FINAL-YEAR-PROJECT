"""Pydantic schemas for AI endpoints."""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)


class SourceCitation(BaseModel):
    document: str
    section: str
    excerpt: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceCitation] = Field(default_factory=list)
    # The subset of `sources` the answer actually cited, and the number of
    # citation labels the model invented. A non-zero count means part of the
    # answer was not grounded in the approved context.
    cited_sources: list[SourceCitation] = Field(default_factory=list)
    invalid_citations: int = 0


class AgentQueryRequest(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=500)


class AgentQueryResponse(BaseModel):
    message: str
    tool_used: str | None = None
    result: dict[str, Any] | None = None


class DraftApprovalResponse(BaseModel):
    draft_id: uuid.UUID
    status: str
    message: str
    order_id: uuid.UUID | None = None
    # Reservation outcome for the approved order. "reserved" means stock was
    # allocated by FEFO; "not_reserved" means the order was created but could not
    # be fulfilled from current stock and stays in 'pending' status. The outcome is
    # never silent: it is reported here and written to the audit log.
    reservation_status: str = "reserved"
    reservation_message: str | None = None


class ForecastEvaluationItem(BaseModel):
    id: uuid.UUID
    model: str
    mae: Decimal
    rmse: Decimal
    mape: Decimal | None
    test_period: str
    notes: str | None
    # Scale-free skill score against the seasonal naive baseline. Below 1 means
    # the model beat the baseline.
    mase: Decimal | None = None


class ReorderRecommendationItem(BaseModel):
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


class ToolRunLogItem(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    tool: str
    args: dict[str, Any]
    result: dict[str, Any] | None
    status: str
    error_message: str | None
    created_at: str
