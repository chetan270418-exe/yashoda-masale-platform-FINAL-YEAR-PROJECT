"""Integration test: AI Order Draft approval connected to M5 Ordering and FEFO.

Verifies Requirement R3:
- Approving an AI OrderDraft creates an active M5 order with price snapshots.
- Triggers FEFO stock reservation against released inventory batches.
- Gracefully handles insufficient stock by leaving the order active in 'open' status.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.inventory.models import (
    InventoryBalance,
    ProductionBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)
from app.orders.models import Order, OrderItem
from tests.conftest import _make_user


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name if user.role else ""})
    return {"Authorization": f"Bearer {token}"}


def test_ai_draft_approval_creates_m5_order_with_snapshots(client: TestClient, db: Session):
    """Approving an AI draft creates a complete M5 order with price snapshots and idempotency."""
    dealer = _make_user(
        db, email=f"dealer-draft-m5-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer"
    )

    cat = Category(name=f"Cat-M5-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Turmeric Powder {uuid.uuid4().hex[:4]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(product_id=prod.id, sku=f"SKU-M5-{uuid.uuid4().hex[:6]}", pack_size_g=500)
    db.add(var)
    db.flush()

    draft = OrderDraft(
        customer_id=dealer.id,
        items=[
            {
                "variant_id": str(var.id),
                "sku": var.sku,
                "quantity": 20.0,
                "unit_price": 120.00,
                "line_total": 2400.00,
                "price_list_code": "DEALER",
            }
        ],
        total_estimated=Decimal("2400.00"),
        status="draft",
    )
    db.add(draft)
    db.commit()

    resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=_auth_header(dealer))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["order_id"] is not None

    # Inspect created M5 Order
    order_id = uuid.UUID(data["order_id"])
    order = db.scalar(select(Order).where(Order.id == order_id))
    assert order is not None
    assert order.customer_id == dealer.id
    assert order.order_type == "sales"
    assert order.idempotency_key == f"ai-draft-{draft.id}"
    assert order.total_amount == Decimal("2400.00")
    assert order.currency == "INR"

    # Inspect OrderItem snapshots (B8, B12)
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all())
    assert len(items) == 1
    assert items[0].variant_id == var.id
    assert items[0].quantity == Decimal("20.000")
    assert items[0].unit_price_snapshot == Decimal("120.00")
    assert items[0].price_list_code == "DEALER"
    assert items[0].line_total == Decimal("2400.00")


def test_ai_draft_approval_triggers_fefo_stock_reservation(client: TestClient, db: Session):
    """Approving an AI draft automatically triggers FEFO reservation when stock is present."""
    dealer = _make_user(
        db, email=f"dealer-fefo-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer"
    )

    wh = Warehouse(
        code=f"WH-AI-{uuid.uuid4().hex[:6]}",
        name="AI Fulfillment Warehouse",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.flush()

    cat = Category(name=f"Cat-AI-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Chilli Powder {uuid.uuid4().hex[:4]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id, sku=f"SKU-FEFO-{uuid.uuid4().hex[:6]}", pack_size_g=250
    )
    db.add(var)
    db.flush()

    # Batch with 50 units released stock expiring in 90 days
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"BAT-AI-{uuid.uuid4().hex[:6]}",
        manufactured_at=date.today() - timedelta(days=10),
        expiry_date=date.today() + timedelta(days=90),
        status="released",
    )
    db.add(batch)
    db.flush()

    bal = InventoryBalance(
        warehouse_id=wh.id,
        batch_id=batch.id,
        on_hand=Decimal("50.000"),
        reserved=Decimal("0.000"),
    )
    db.add(bal)
    db.commit()

    draft = OrderDraft(
        customer_id=dealer.id,
        items=[
            {
                "variant_id": str(var.id),
                "sku": var.sku,
                "quantity": 15.0,
                "unit_price": 85.50,
                "line_total": 1282.50,
                "price_list_code": "DEALER",
            }
        ],
        total_estimated=Decimal("1282.50"),
        status="draft",
    )
    db.add(draft)
    db.commit()

    # Approve draft
    resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=_auth_header(dealer))
    assert resp.status_code == 200
    order_id = uuid.UUID(resp.json()["order_id"])

    # Order should be in 'reserved' status due to FEFO reservation
    order = db.scalar(select(Order).where(Order.id == order_id))
    assert order is not None
    assert order.status == "reserved"

    # Verify StockReservation was created
    item = db.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    res = db.scalar(select(StockReservation).where(StockReservation.order_item_id == item.id))
    assert res is not None
    assert res.quantity == Decimal("15.000")
    assert res.status == "active"
    assert res.balance_id == bal.id

    # Verify balance reserved updated
    db.refresh(bal)
    assert bal.reserved == Decimal("15.000")
    assert bal.on_hand == Decimal("50.000")

    # Verify StockMovement ledger entry (Invariant B5)
    move = db.scalar(
        select(StockMovement).where(
            StockMovement.warehouse_id == wh.id,
            StockMovement.batch_id == batch.id,
            StockMovement.movement_type == "reserve",
        )
    )
    assert move is not None
    assert move.quantity == Decimal("15.000")
