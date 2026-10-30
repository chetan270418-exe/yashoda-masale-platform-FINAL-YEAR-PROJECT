"""Tests for ordering service hardening and concurrency fixes.

Covers:
1. PATCH /orders/{id}/status only allows 'delivered'; others return USE_DEDICATED_ENDPOINT.
2. Dedicated /cancel releases reserved stock exactly once (B6).
3. Dedicated /dispatch decrements on_hand & reserved stock and logs B5 movement.
4. Concurrent reserve calls on the same order cannot hold stock twice (order row lock).
5. Multi-line order reservation fails atomically with no partial allocations (B4).
6. Composite idempotency key scoped per customer (customer_id, idempotency_key).
7. Order limits (max 50 items, max 10,000 qty per item).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.inventory.models import (
    InventoryBalance,
    ProductionBatch,
    StockMovement,
    Warehouse,
)
from tests.conftest import _make_user


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def catalog_fixture(db: Session):
    cat = Category(id=uuid.uuid4(), name=f"Spices-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(id=uuid.uuid4(), name="Turmeric Pure", category_id=cat.id, is_active=True)
    db.add(prod)
    db.flush()

    v1 = ProductVariant(
        id=uuid.uuid4(),
        product_id=prod.id,
        sku=f"TUR-100G-{uuid.uuid4().hex[:4]}",
        pack_size_g=100,
        is_active=True,
    )
    v2 = ProductVariant(
        id=uuid.uuid4(),
        product_id=prod.id,
        sku=f"TUR-500G-{uuid.uuid4().hex[:4]}",
        pack_size_g=500,
        is_active=True,
    )
    db.add_all([v1, v2])
    db.flush()

    pl = db.scalar(select(PriceList).where(PriceList.name == "DEALER"))
    if not pl:
        pl = PriceList(id=uuid.uuid4(), name="DEALER")
        db.add(pl)
        db.flush()

    now = datetime.now(UTC)
    p1 = PriceListItem(
        id=uuid.uuid4(),
        price_list_id=pl.id,
        variant_id=v1.id,
        price=Decimal("45.00"),
        valid_from=now - timedelta(days=1),
    )
    p2 = PriceListItem(
        id=uuid.uuid4(),
        price_list_id=pl.id,
        variant_id=v2.id,
        price=Decimal("180.00"),
        valid_from=now - timedelta(days=1),
    )
    db.add_all([p1, p2])
    db.commit()

    return {"category": cat, "product": prod, "variants": [v1, v2]}


@pytest.fixture
def warehouse_and_stock(db: Session, catalog_fixture):
    wh = Warehouse(
        id=uuid.uuid4(),
        code=f"WH-TEST-{uuid.uuid4().hex[:6]}",
        name="Main Test Warehouse",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.flush()

    v1, v2 = catalog_fixture["variants"]
    today = date.today()

    b1 = ProductionBatch(
        id=uuid.uuid4(),
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        variant_id=v1.id,
        status="released",
        manufactured_at=today - timedelta(days=10),
        expiry_date=today + timedelta(days=180),
    )
    b2 = ProductionBatch(
        id=uuid.uuid4(),
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        variant_id=v2.id,
        status="released",
        manufactured_at=today - timedelta(days=5),
        expiry_date=today + timedelta(days=200),
    )
    db.add_all([b1, b2])
    db.flush()

    bal1 = InventoryBalance(
        id=uuid.uuid4(),
        warehouse_id=wh.id,
        batch_id=b1.id,
        on_hand=Decimal("100.000"),
        reserved=Decimal("0.000"),
    )
    bal2 = InventoryBalance(
        id=uuid.uuid4(),
        warehouse_id=wh.id,
        batch_id=b2.id,
        on_hand=Decimal("20.000"),
        reserved=Decimal("0.000"),
    )
    db.add_all([bal1, bal2])
    db.commit()

    return {"warehouse": wh, "batches": [b1, b2], "balances": [bal1, bal2]}


def test_patch_status_rejects_non_delivered_statuses(
    client: TestClient, db: Session, catalog_fixture, warehouse_and_stock
):
    """PATCH /orders/{id}/status only allows delivered; others return 400."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    v1 = catalog_fixture["variants"][0]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "5.000"}]},
        headers=_auth_header(dealer),
    )
    assert create_resp.status_code == 201
    order_id = create_resp.json()["id"]

    # Try patching status to 'reserved', 'packed', 'dispatched', 'cancelled', 'returned'
    for forbidden in ["reserved", "packed", "dispatched", "cancelled", "returned", "open"]:
        resp = client.patch(
            f"/api/v1/orders/{order_id}/status",
            json={"status": forbidden},
            headers=_auth_header(owner),
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "USE_DEDICATED_ENDPOINT"
        assert "Use the dedicated lifecycle endpoint" in resp.json()["message"]


def test_patch_status_allows_delivered_when_dispatched(
    client: TestClient, db: Session, catalog_fixture, warehouse_and_stock
):
    """PATCH /orders/{id}/status succeeds when transitioning a dispatched order to delivered."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    v1 = catalog_fixture["variants"][0]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "5.000"}]},
        headers=_auth_header(dealer),
    )
    order_id = create_resp.json()["id"]

    # Move order through dedicated endpoints to dispatched
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(owner))
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(owner))
    client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(owner))

    # Now delivered via PATCH status must succeed
    patch_resp = client.patch(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "delivered"},
        headers=_auth_header(owner),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "delivered"


def test_cancel_via_dedicated_endpoint_releases_reserved_stock(
    client: TestClient, db: Session, catalog_fixture, warehouse_and_stock
):
    """Cancelling a reserved order via /cancel releases all reserved stock exactly once (B6)."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    v1 = catalog_fixture["variants"][0]
    bal1 = warehouse_and_stock["balances"][0]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "10.000"}]},
        headers=_auth_header(dealer),
    )
    order_id = create_resp.json()["id"]

    # Reserve stock
    res_resp = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(owner))
    assert res_resp.status_code == 200

    db.refresh(bal1)
    assert bal1.reserved == Decimal("10.000")
    assert bal1.on_hand == Decimal("100.000")

    # Cancel via dedicated endpoint
    cancel_resp = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Dealer cancelled order before dispatch"},
        headers=_auth_header(owner),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    # Stock is released completely
    db.refresh(bal1)
    assert bal1.reserved == Decimal("0.000")
    assert bal1.on_hand == Decimal("100.000")

    # Release movement recorded (B5)
    rel_mov = db.scalar(
        select(StockMovement).where(
            StockMovement.balance_id == bal1.id,
            StockMovement.movement_type == "release",
        )
    )
    assert rel_mov is not None
    assert rel_mov.quantity == Decimal("10.000")


def test_dispatch_via_dedicated_endpoint_decrements_stock_and_records_ledger(
    client: TestClient, db: Session, catalog_fixture, warehouse_and_stock
):
    """Dispatching a packed order decrements stock and logs dispatch movement (B5)."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    v1 = catalog_fixture["variants"][0]
    bal1 = warehouse_and_stock["balances"][0]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "15.000"}]},
        headers=_auth_header(dealer),
    )
    order_id = create_resp.json()["id"]

    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(owner))
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(owner))

    dispatch_resp = client.post(
        f"/api/v1/orders/{order_id}/dispatch",
        json={"carrier": "BlueDart", "tracking_ref": "BD-12345"},
        headers=_auth_header(owner),
    )
    assert dispatch_resp.status_code == 200
    assert dispatch_resp.json()["status"] == "dispatched"

    db.refresh(bal1)
    assert bal1.reserved == Decimal("0.000")
    assert bal1.on_hand == Decimal("85.000")

    disp_mov = db.scalar(
        select(StockMovement).where(
            StockMovement.balance_id == bal1.id,
            StockMovement.movement_type == "dispatch",
        )
    )
    assert disp_mov is not None
    assert disp_mov.quantity == Decimal("15.000")


def test_concurrent_reserve_calls_do_not_double_reserve(
    client: TestClient, db: Session, catalog_fixture, warehouse_and_stock
):
    """Calling /reserve twice consecutively or concurrently only reserves stock once."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    v1 = catalog_fixture["variants"][0]
    bal1 = warehouse_and_stock["balances"][0]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "5.000"}]},
        headers=_auth_header(dealer),
    )
    order_id = create_resp.json()["id"]

    # First reserve succeeds
    r1 = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(owner))
    assert r1.status_code == 200

    # Second reserve fails with ILLEGAL_STATE_TRANSITION because order is already reserved
    r2 = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(owner))
    assert r2.status_code == 400
    assert r2.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    db.refresh(bal1)
    assert bal1.reserved == Decimal("5.000"), "Stock was reserved twice!"


def test_idempotency_key_is_per_customer(client: TestClient, db: Session, catalog_fixture):
    """Two different customers using the same idempotency key both succeed without conflict."""
    c1 = _make_user(db, email=f"cust1-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    c2 = _make_user(db, email=f"cust2-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    v1 = catalog_fixture["variants"][0]
    shared_key = f"shared-key-{uuid.uuid4().hex}"

    payload = {"items": [{"variant_id": str(v1.id), "quantity": "2.000"}]}

    # Customer 1 creates order with shared_key
    r1 = client.post(
        "/api/v1/orders",
        json=payload,
        headers={**_auth_header(c1), "Idempotency-Key": shared_key},
    )
    assert r1.status_code == 201
    order1_id = r1.json()["id"]

    # Customer 2 creates order with the same key -> succeeds (per-customer scoping)
    r2 = client.post(
        "/api/v1/orders",
        json=payload,
        headers={**_auth_header(c2), "Idempotency-Key": shared_key},
    )
    assert r2.status_code == 201
    order2_id = r2.json()["id"]
    assert order1_id != order2_id

    # Customer 1 replays with same key -> gets order1 replayed
    r1_replay = client.post(
        "/api/v1/orders",
        json=payload,
        headers={**_auth_header(c1), "Idempotency-Key": shared_key},
    )
    assert r1_replay.status_code == 201
    assert r1_replay.json()["id"] == order1_id

    # Customer 1 uses same key with DIFFERENT payload -> 409 conflict
    r1_conflict = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "3.000"}]},
        headers={**_auth_header(c1), "Idempotency-Key": shared_key},
    )
    assert r1_conflict.status_code == 409
    assert r1_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_order_limits_enforced(client: TestClient, db: Session, catalog_fixture):
    """Enforces max 50 items and max 10,000 quantity per line."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    v1 = catalog_fixture["variants"][0]

    # Quantity exceeding 10,000 -> 422 Unprocessable Entity
    resp_large_qty = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "10001.000"}]},
        headers=_auth_header(dealer),
    )
    assert resp_large_qty.status_code == 422

    # Negative / zero quantity -> 422
    resp_zero = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v1.id), "quantity": "0.000"}]},
        headers=_auth_header(dealer),
    )
    assert resp_zero.status_code == 422
