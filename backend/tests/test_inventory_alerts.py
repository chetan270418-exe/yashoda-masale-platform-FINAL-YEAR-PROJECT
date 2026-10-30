"""Tests for inventory alert query endpoints and manual stock adjustments (T-042, T-043).

Covers:
- GET /api/v1/inventory/alerts/expiry
  - Filtering by days_ahead and warehouse_id
  - Excludes unreleased (hold, rejected) batches (B1, B2)
  - Excludes inactive warehouses
  - Excludes batches with zero available stock
  - Accurate calculation of days_until_expiry and available_quantity
  - Ordering by expiry_date ASC, batch_code ASC
  - Input validation (days_ahead must be 1 to 365)
- GET /api/v1/inventory/alerts/low-stock
  - Aggregating total available stock across released batches
  - Correct status flag ('LOW_STOCK' vs 'OUT_OF_STOCK')
  - Filtering by threshold and warehouse_id
  - Ordering by total_available ASC, sku ASC
  - Input validation (threshold >= 0)
- POST /api/v1/inventory/adjustments
  - Positive stock adjustments (adds on_hand, records audit log)
  - Negative stock adjustments (deducts on_hand, records audit log)
  - Negative adjustment exceeding available stock rejected (400)
  - Zero delta rejected (400)
  - Invalid reason category rejected (422)
  - Nonexistent warehouse/batch rejected (404)
- RBAC validation:
  - Alerts: owner, warehouse, factory (200); dealer, distributor, consumer (403); unauth (401)
  - Adjustments: owner, warehouse (200); factory, dealer, distributor, consumer (403); unauth (401)
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.audit.models import AuditLog
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.inventory.models import InventoryBalance, ProductionBatch, StockMovement, Warehouse
from tests.conftest import _make_user


def _auth_header(db, role_name: str, email_prefix: str = "user") -> dict[str, str]:
    """Helper to create a test user with a given role and return Authorization header."""
    user = _make_user(
        db,
        email=f"{email_prefix}_{uuid.uuid4().hex[:8]}@example.com",
        role_name=role_name,
    )
    token = create_access_token({"sub": str(user.id), "role": role_name})
    return {"Authorization": f"Bearer {token}"}


def _seed_inventory_data(db):
    """Seed sample catalog and inventory data for alert testing."""
    cat = Category(name="Alert Spices", description="Testing alerts")
    db.add(cat)
    db.flush()

    prod = Product(name="Turmeric Powder", category_id=cat.id)
    db.add(prod)
    db.flush()

    v100 = ProductVariant(product_id=prod.id, sku="TUR-100G", pack_size_g=100)
    v500 = ProductVariant(product_id=prod.id, sku="TUR-500G", pack_size_g=500)
    db.add_all([v100, v500])
    db.flush()

    wh_pune = Warehouse(code="WH-PUNE-TEST", name="Pune Warehouse", is_active=True)
    wh_mumbai = Warehouse(code="WH-MUM-TEST", name="Mumbai Warehouse", is_active=True)
    wh_inactive = Warehouse(code="WH-INACTIVE-TEST", name="Inactive Warehouse", is_active=False)
    db.add_all([wh_pune, wh_mumbai, wh_inactive])
    db.flush()

    today = date.today()

    # Batch 1: Expiring in 10 days, released, in Pune, on_hand=10, reserved=2 -> avail=8
    b1 = ProductionBatch(
        variant_id=v100.id,
        batch_code="B-TUR-001",
        expiry_date=today + timedelta(days=10),
        status="released",
    )
    # Batch 2: Expiring in 45 days, released, in Pune, on_hand=20, reserved=0 -> avail=20
    b2 = ProductionBatch(
        variant_id=v100.id,
        batch_code="B-TUR-002",
        expiry_date=today + timedelta(days=45),
        status="released",
    )
    # Batch 3: Expiring in 90 days, released, in Mumbai, on_hand=100, reserved=10 -> avail=90
    b3 = ProductionBatch(
        variant_id=v500.id,
        batch_code="B-TUR-003",
        expiry_date=today + timedelta(days=90),
        status="released",
    )
    # Batch 4: Expiring in 5 days, HOLD status (unreleased), on_hand=15, reserved=0
    b4 = ProductionBatch(
        variant_id=v100.id,
        batch_code="B-TUR-004-HOLD",
        expiry_date=today + timedelta(days=5),
        status="on_hold",
    )
    # Batch 5: Expiring in 15 days, released, on_hand=5, reserved=5 -> avail=0
    b5 = ProductionBatch(
        variant_id=v500.id,
        batch_code="B-TUR-005-ZERO",
        expiry_date=today + timedelta(days=15),
        status="released",
    )
    # Batch 6: Expiring in 8 days, released, in Inactive Warehouse
    b6 = ProductionBatch(
        variant_id=v100.id,
        batch_code="B-TUR-006-INACT",
        expiry_date=today + timedelta(days=8),
        status="released",
    )

    db.add_all([b1, b2, b3, b4, b5, b6])
    db.flush()

    bal1 = InventoryBalance(
        warehouse_id=wh_pune.id, batch_id=b1.id, on_hand=Decimal("10"), reserved=Decimal("2")
    )
    bal2 = InventoryBalance(
        warehouse_id=wh_pune.id, batch_id=b2.id, on_hand=Decimal("20"), reserved=Decimal("0")
    )
    bal3 = InventoryBalance(
        warehouse_id=wh_mumbai.id, batch_id=b3.id, on_hand=Decimal("100"), reserved=Decimal("10")
    )
    bal4 = InventoryBalance(
        warehouse_id=wh_pune.id, batch_id=b4.id, on_hand=Decimal("15"), reserved=Decimal("0")
    )
    bal5 = InventoryBalance(
        warehouse_id=wh_pune.id, batch_id=b5.id, on_hand=Decimal("5"), reserved=Decimal("5")
    )
    bal6 = InventoryBalance(
        warehouse_id=wh_inactive.id, batch_id=b6.id, on_hand=Decimal("30"), reserved=Decimal("0")
    )

    db.add_all([bal1, bal2, bal3, bal4, bal5, bal6])
    db.commit()

    return {
        "wh_pune": wh_pune,
        "wh_mumbai": wh_mumbai,
        "wh_inactive": wh_inactive,
        "v100": v100,
        "v500": v500,
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "b4": b4,
        "b5": b5,
        "b6": b6,
    }


# ── Expiry Alert Endpoint Tests ───────────────────────────────────────────────


def test_get_expiry_alerts_default_30_days(client, db):
    """Default 30 days ahead returns eligible released batches with positive available stock."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "warehouse")

    res = client.get("/api/v1/inventory/alerts/expiry", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["days_ahead"] == 30
    assert body["total"] == 1
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["batch_code"] == "B-TUR-001"
    assert item["sku"] == "TUR-100G"
    assert item["product_name"] == "Turmeric Powder"
    assert item["warehouse_id"] == str(data["wh_pune"].id)
    assert item["warehouse_name"] == "Pune Warehouse"
    assert item["days_until_expiry"] == 10
    assert Decimal(str(item["available_quantity"])) == Decimal("8.000")


def test_get_expiry_alerts_60_days_lookahead(client, db):
    """60 days lookahead returns both b1 (10 days) and b2 (45 days) in ASC order."""
    _seed_inventory_data(db)
    headers = _auth_header(db, "owner")

    res = client.get("/api/v1/inventory/alerts/expiry?days_ahead=60", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["days_ahead"] == 60
    assert body["total"] == 2
    batch_codes = [it["batch_code"] for it in body["items"]]
    assert batch_codes == ["B-TUR-001", "B-TUR-002"]


def test_get_expiry_alerts_filter_by_warehouse(client, db):
    """Filtering by warehouse_id returns only batches from that warehouse."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "factory")

    # Mumbai warehouse has b3 expiring in 90 days
    res = client.get(
        f"/api/v1/inventory/alerts/expiry?warehouse_id={data['wh_mumbai'].id}&days_ahead=100",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["batch_code"] == "B-TUR-003"
    assert body["items"][0]["warehouse_name"] == "Mumbai Warehouse"


def test_get_expiry_alerts_inactive_warehouse_rejected(client, db):
    """Querying alerts for an inactive warehouse raises 400 WAREHOUSE_INACTIVE."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "warehouse")

    res = client.get(
        f"/api/v1/inventory/alerts/expiry?warehouse_id={data['wh_inactive'].id}",
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["code"] == "WAREHOUSE_INACTIVE"


def test_get_expiry_alerts_nonexistent_warehouse(client, db):
    """Querying alerts for a nonexistent warehouse UUID returns 404."""
    _seed_inventory_data(db)
    headers = _auth_header(db, "warehouse")
    random_id = uuid.uuid4()

    res = client.get(
        f"/api/v1/inventory/alerts/expiry?warehouse_id={random_id}",
        headers=headers,
    )
    assert res.status_code == 404
    assert res.json()["code"] == "WAREHOUSE_NOT_FOUND"


def test_get_expiry_alerts_invalid_days_ahead_validation(client, db):
    """days_ahead < 1 or > 365 returns 422 validation error."""
    _seed_inventory_data(db)
    headers = _auth_header(db, "owner")

    res_zero = client.get("/api/v1/inventory/alerts/expiry?days_ahead=0", headers=headers)
    assert res_zero.status_code == 422

    res_large = client.get("/api/v1/inventory/alerts/expiry?days_ahead=366", headers=headers)
    assert res_large.status_code == 422


# ── Low Stock Alert Endpoint Tests ────────────────────────────────────────────


def test_get_low_stock_alerts_threshold_50(client, db):
    """Aggregates released available stock across active warehouses and applies threshold.

    v100 total avail: 8 (b1) + 20 (b2) = 28 (<= 50) -> LOW_STOCK
    v500 total avail: 90 (b3) + 0 (b5) = 90 (> 50) -> Excluded
    """
    _seed_inventory_data(db)
    headers = _auth_header(db, "owner")

    res = client.get("/api/v1/inventory/alerts/low-stock?threshold=50", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["sku"] == "TUR-100G"
    assert item["pack_size"] == 100
    assert item["pack_size_g"] == 100
    assert Decimal(str(item["total_available"])) == Decimal("28.000")
    assert item["status_flag"] == "LOW_STOCK"


def test_get_low_stock_alerts_out_of_stock_status_flag(client, db):
    """A variant with 0 available stock receives status_flag='OUT_OF_STOCK'."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "warehouse")

    # In Pune, v500 only has b5 (5 on hand, 5 reserved -> 0 available)
    res = client.get(
        f"/api/v1/inventory/alerts/low-stock?warehouse_id={data['wh_pune'].id}&threshold=50",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    # Ordered by total_available ASC: v500 (0) then v100 (28)
    assert body["items"][0]["sku"] == "TUR-500G"
    assert Decimal(str(body["items"][0]["total_available"])) == Decimal("0.000")
    assert body["items"][0]["status_flag"] == "OUT_OF_STOCK"

    assert body["items"][1]["sku"] == "TUR-100G"
    assert Decimal(str(body["items"][1]["total_available"])) == Decimal("28.000")
    assert body["items"][1]["status_flag"] == "LOW_STOCK"


def test_get_low_stock_alerts_negative_threshold_validation(client, db):
    """threshold < 0 returns 422 validation error."""
    _seed_inventory_data(db)
    headers = _auth_header(db, "factory")

    res = client.get("/api/v1/inventory/alerts/low-stock?threshold=-1", headers=headers)
    assert res.status_code == 422


# ── Alert RBAC Tests ──────────────────────────────────────────────────────────


def test_alert_endpoints_rbac_matrix(client, db):
    """Permits owner, warehouse, factory; rejects dealer, distributor, consumer."""
    _seed_inventory_data(db)
    endpoints = [
        "/api/v1/inventory/alerts/expiry",
        "/api/v1/inventory/alerts/low-stock",
    ]

    for ep in endpoints:
        # Unauthenticated: 401
        res_unauth = client.get(ep)
        assert res_unauth.status_code == 401

        # Allowed roles: 200
        for role in ["owner", "warehouse", "factory"]:
            headers = _auth_header(db, role)
            res_allowed = client.get(ep, headers=headers)
            assert res_allowed.status_code == 200, f"Role {role} should be allowed on {ep}"

        # Forbidden roles: 403
        for role in ["dealer", "distributor", "consumer"]:
            headers = _auth_header(db, role)
            res_forbidden = client.get(ep, headers=headers)
            assert res_forbidden.status_code == 403, f"Role {role} should be forbidden on {ep}"


# ── Stock Adjustments Endpoint Tests ──────────────────────────────────────────


def test_post_stock_adjustment_positive_and_negative(client, db):
    """Manual adjustment updates stock and records StockMovement and AuditLog (T-043, B5)."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "warehouse")

    # 1. Positive adjustment: +15 units (FOUND)
    payload_pos = {
        "warehouse_id": str(data["wh_pune"].id),
        "batch_id": str(data["b1"].id),
        "qty_delta": "15.000",
        "reason_category": "FOUND",
        "notes": "Found misplaced carton during audit",
    }
    res_pos = client.post("/api/v1/inventory/adjustments", json=payload_pos, headers=headers)
    assert res_pos.status_code == 200
    pos_data = res_pos.json()
    assert Decimal(str(pos_data["on_hand"])) == Decimal("25.000")  # 10 + 15
    assert Decimal(str(pos_data["reserved"])) == Decimal("2.000")
    assert Decimal(str(pos_data["available"])) == Decimal("23.000")

    # Verify StockMovement ledger
    mv_pos = (
        db.query(StockMovement)
        .filter_by(batch_id=data["b1"].id, movement_type="adjustment_in")
        .first()
    )
    assert mv_pos is not None
    assert mv_pos.quantity == Decimal("15.000")
    assert mv_pos.reason_category == "FOUND"

    # Verify AuditLog
    audit_pos = (
        db.query(AuditLog).filter_by(action="STOCK_ADJUSTMENT", entity="inventory_balances").first()
    )
    assert audit_pos is not None

    # 2. Negative adjustment: -5 units (DAMAGE)
    payload_neg = {
        "warehouse_id": str(data["wh_pune"].id),
        "batch_id": str(data["b1"].id),
        "qty_delta": "-5.000",
        "reason_category": "DAMAGE",
        "notes": "Water leak damaged bags",
    }
    res_neg = client.post("/api/v1/inventory/adjustments", json=payload_neg, headers=headers)
    assert res_neg.status_code == 200
    neg_data = res_neg.json()
    assert Decimal(str(neg_data["on_hand"])) == Decimal("20.000")  # 25 - 5
    assert Decimal(str(neg_data["reserved"])) == Decimal("2.000")
    assert Decimal(str(neg_data["available"])) == Decimal("18.000")


def test_post_stock_adjustment_exceeding_available_rejected(client, db):
    """Negative adjustment cannot exceed available stock (B4)."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "owner")

    # b1 has 10 on hand, 2 reserved -> available is 8
    payload = {
        "warehouse_id": str(data["wh_pune"].id),
        "batch_id": str(data["b1"].id),
        "qty_delta": "-9.000",
        "reason_category": "SPOILAGE",
        "notes": "Trying to deduct more than available",
    }
    res = client.post("/api/v1/inventory/adjustments", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.json()["code"] == "INSUFFICIENT_STOCK"


def test_post_stock_adjustment_zero_delta_rejected(client, db):
    """Zero delta is rejected by adjust_stock with 400 INVALID_ADJUSTMENT."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "owner")

    payload = {
        "warehouse_id": str(data["wh_pune"].id),
        "batch_id": str(data["b1"].id),
        "qty_delta": "0.000",
        "reason_category": "COUNT_DISCREPANCY",
    }
    res = client.post("/api/v1/inventory/adjustments", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_ADJUSTMENT"


def test_post_stock_adjustment_invalid_reason_category(client, db):
    """Unknown reason category is rejected with 422 validation error."""
    data = _seed_inventory_data(db)
    headers = _auth_header(db, "owner")

    payload = {
        "warehouse_id": str(data["wh_pune"].id),
        "batch_id": str(data["b1"].id),
        "qty_delta": "5.000",
        "reason_category": "UNKNOWN_REASON",
    }
    res = client.post("/api/v1/inventory/adjustments", json=payload, headers=headers)
    assert res.status_code == 422


def test_post_stock_adjustment_rbac(client, db):
    """Adjustments permitted for owner and warehouse; forbidden for other roles."""
    data = _seed_inventory_data(db)
    endpoint = "/api/v1/inventory/adjustments"
    payload = {
        "warehouse_id": str(data["wh_pune"].id),
        "batch_id": str(data["b1"].id),
        "qty_delta": "1.000",
        "reason_category": "FOUND",
    }

    # Unauthenticated: 401
    assert client.post(endpoint, json=payload).status_code == 401

    # Permitted roles: owner, warehouse (200)
    for role in ["owner", "warehouse"]:
        headers = _auth_header(db, role)
        assert (
            client.post(endpoint, json=payload, headers=headers).status_code == 200
        ), f"Role {role} should be permitted to adjust stock"

    # Forbidden roles: factory, dealer, distributor, consumer (403)
    for role in ["factory", "dealer", "distributor", "consumer"]:
        headers = _auth_header(db, role)
        assert (
            client.post(endpoint, json=payload, headers=headers).status_code == 403
        ), f"Role {role} should be forbidden to adjust stock"


# ── Edge Cases: Invariant B2 and Held Batch Isolation in Alerts ───────────────


def test_get_low_stock_alerts_excludes_expired_batches_from_available_stock(client, db):
    """Expired released batches must NOT be counted as available stock (Invariant B2).

    If a warehouse has 100 units of expired batch and 5 units of unexpired batch,
    the available stock is 5 units, NOT 105. For threshold=20, it must be alerted as LOW_STOCK.
    """
    cat = Category(name=f"ExpiryAlertCat_{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"ExpiryAlertProd_{uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(product_id=prod.id, sku=f"SKU-EXP-{uuid.uuid4().hex[:6]}", pack_size_g=200)
    wh = Warehouse(code=f"WH-EXP-{uuid.uuid4().hex[:6]}", name="Expiry Test WH", is_active=True)
    db.add_all([var, wh])
    db.flush()

    today = date.today()

    # Expired released batch: 100 units
    b_expired = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-EXPIRED-{uuid.uuid4().hex[:6]}",
        expiry_date=today - timedelta(days=5),
        status="released",
    )
    # Valid unexpired released batch: 5 units
    b_valid = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-VALID-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=60),
        status="released",
    )
    db.add_all([b_expired, b_valid])
    db.flush()

    bal_expired = InventoryBalance(
        warehouse_id=wh.id, batch_id=b_expired.id, on_hand=Decimal("100.000"), reserved=Decimal("0")
    )
    bal_valid = InventoryBalance(
        warehouse_id=wh.id, batch_id=b_valid.id, on_hand=Decimal("5.000"), reserved=Decimal("0")
    )
    db.add_all([bal_expired, bal_valid])
    db.commit()

    headers = _auth_header(db, "warehouse")
    res = client.get(
        f"/api/v1/inventory/alerts/low-stock?warehouse_id={wh.id}&threshold=20",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["sku"] == var.sku
    # Total available must be 5.000 (excluding 100 expired units)
    assert Decimal(str(item["total_available"])) == Decimal("5.000")
    assert item["status_flag"] == "LOW_STOCK"


def test_get_low_stock_alerts_variant_with_only_held_batches_reported_as_out_of_stock(client, db):
    """When a variant only has held/quarantined batches, available stock is 0 (OUT_OF_STOCK)."""
    cat = Category(name=f"HeldAlertCat_{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"HeldAlertProd_{uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id, sku=f"SKU-HELD-{uuid.uuid4().hex[:6]}", pack_size_g=250
    )
    wh = Warehouse(code=f"WH-HELD-{uuid.uuid4().hex[:6]}", name="Held Test WH", is_active=True)
    db.add_all([var, wh])
    db.flush()

    today = date.today()
    b_held = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-HELD-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=60),
        status="on_hold",
    )
    db.add(b_held)
    db.flush()

    bal = InventoryBalance(
        warehouse_id=wh.id, batch_id=b_held.id, on_hand=Decimal("50.000"), reserved=Decimal("0")
    )
    db.add(bal)
    db.commit()

    headers = _auth_header(db, "warehouse")
    res = client.get(
        f"/api/v1/inventory/alerts/low-stock?warehouse_id={wh.id}&threshold=10",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["sku"] == var.sku
    assert Decimal(str(item["total_available"])) == Decimal("0.000")
    assert item["status_flag"] == "OUT_OF_STOCK"


def test_get_expiry_alerts_includes_already_expired_batches_with_available_stock(client, db):
    """Expired released batches with positive stock include negative days_until_expiry."""
    cat = Category(name=f"ExpAlertCat_{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"ExpAlertProd_{uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id, sku=f"SKU-EXPA-{uuid.uuid4().hex[:6]}", pack_size_g=500
    )
    wh = Warehouse(code=f"WH-EXPA-{uuid.uuid4().hex[:6]}", name="ExpAlert WH", is_active=True)
    db.add_all([var, wh])
    db.flush()

    today = date.today()
    b_past = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-PAST-{uuid.uuid4().hex[:6]}",
        expiry_date=today - timedelta(days=3),
        status="released",
    )
    db.add(b_past)
    db.flush()

    bal = InventoryBalance(
        warehouse_id=wh.id, batch_id=b_past.id, on_hand=Decimal("12.000"), reserved=Decimal("0")
    )
    db.add(bal)
    db.commit()

    headers = _auth_header(db, "owner")
    res = client.get(
        f"/api/v1/inventory/alerts/expiry?warehouse_id={wh.id}&days_ahead=30",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["batch_code"] == b_past.batch_code
    assert item["days_until_expiry"] == -3
    assert Decimal(str(item["available_quantity"])) == Decimal("12.000")
