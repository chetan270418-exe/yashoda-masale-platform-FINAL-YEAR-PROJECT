"""Endpoint integration tests for AI router (RAG, Agent, Draft Approval, Forecasts, Audits)."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
from app.catalog.models import Category, Product, ProductVariant
from app.inventory.models import Order
from tests.conftest import _make_user


@pytest.fixture
def auth_headers(client: TestClient, db: Session):
    def _headers(role_name: str) -> dict[str, str]:
        email = f"user-{role_name}-{uuid.uuid4().hex[:6]}@example.com"
        _make_user(db, email=email, role_name=role_name, password="TestPassword123!")
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPassword123!"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _headers


def test_public_chat_rag_endpoint(client: TestClient):
    """POST /api/v1/ai/chat returns answer and sources."""
    resp = client.post(
        "/api/v1/ai/chat",
        json={"query": "What is the curcumin percentage in Yashoda Turmeric?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert len(data["sources"]) > 0


def test_agent_query_endpoint(client: TestClient, auth_headers):
    """POST /api/v1/ai/agent/query executes tools for authenticated users."""
    headers = auth_headers("dealer")
    resp = client.post(
        "/api/v1/ai/agent/query",
        json={"prompt": "Can you check stock of our turmeric powders?"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_used"] == "check_stock"
    assert "stock" in data["result"]


def test_draft_approval_lifecycle_creates_order(client: TestClient, auth_headers, db: Session):
    """POST /api/v1/ai/drafts/{id}/approve approves draft and creates real order (Rule 62)."""
    dealer_headers = auth_headers("dealer")

    # Create variant
    cat = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()
    prod = Product(name="Mirchi", category_id=cat.id)
    db.add(prod)
    db.flush()
    var = ProductVariant(product_id=prod.id, sku=f"SKU-{uuid.uuid4().hex[:6]}", pack_size_g=500)
    db.add(var)
    db.commit()

    # Get user
    me_resp = client.get("/api/v1/auth/me", headers=dealer_headers)
    dealer_id = uuid.UUID(me_resp.json()["id"])

    # Create order draft
    draft = OrderDraft(
        customer_id=dealer_id,
        items=[{"variant_id": str(var.id), "sku": var.sku, "quantity": 10.0, "unit_price": 150.0}],
        total_estimated=Decimal("1500.00"),
        status="draft",
    )
    db.add(draft)
    db.commit()

    # Approve draft
    appr_resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=dealer_headers)
    assert appr_resp.status_code == 200
    res_data = appr_resp.json()
    assert res_data["status"] == "approved"
    assert res_data["order_id"] is not None

    # Check order table
    order_id = uuid.UUID(res_data["order_id"])
    created_order = db.scalar(select(Order).where(Order.id == order_id))
    assert created_order is not None
    assert created_order.status == "pending"


def test_forecasts_and_reorder_recommendations_rbac(client: TestClient, auth_headers):
    """GET /api/v1/ai/forecasts & /recommendations/reorder allowed for staff, denied for dealer."""
    owner_headers = auth_headers("owner")
    dealer_headers = auth_headers("dealer")

    # Owner access allowed
    fc_resp = client.get("/api/v1/ai/forecasts", headers=owner_headers)
    assert fc_resp.status_code == 200
    assert len(fc_resp.json()) > 0

    reorder_resp = client.get("/api/v1/ai/recommendations/reorder", headers=owner_headers)
    assert reorder_resp.status_code == 200

    # Dealer access forbidden
    assert client.get("/api/v1/ai/forecasts", headers=dealer_headers).status_code == 403
    assert (
        client.get("/api/v1/ai/recommendations/reorder", headers=dealer_headers).status_code == 403
    )


def test_tool_runs_audit_endpoint_owner_only(client: TestClient, auth_headers):
    """GET /api/v1/ai/tool-runs accessible only to owner."""
    owner_headers = auth_headers("owner")
    dealer_headers = auth_headers("dealer")

    assert client.get("/api/v1/ai/tool-runs", headers=owner_headers).status_code == 200
    assert client.get("/api/v1/ai/tool-runs", headers=dealer_headers).status_code == 403
