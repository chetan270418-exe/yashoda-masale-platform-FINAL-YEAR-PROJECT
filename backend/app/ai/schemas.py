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


class ForecastEvaluationItem(BaseModel):
    id: uuid.UUID
    model: str
    mae: Decimal
    rmse: Decimal
    mape: Decimal | None
    test_period: str
    notes: str | None


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
