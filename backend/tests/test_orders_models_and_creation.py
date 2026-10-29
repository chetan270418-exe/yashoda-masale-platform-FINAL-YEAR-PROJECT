"""Automated tests for M1 Orders Engine: Models, Creation, Price Snapshot, Idempotency, and RBAC.

Verifies:
- Invariant B8: Price snapshot immutability across catalog price updates
- Invariant B9: Role-based price resolution (MRP, DEALER, DISTRIBUTOR)
- Invariant B10: Customer isolation and ownership scoping
- Invariant B12: Strict Decimal precision arithmetic (no float drift)
- Rule 48: Idempotency replay and 409 conflict detection
- Rule 49: State machine allowed and illegal transitions
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.core.errors import AppError
from app.inventory.models import Order as InvOrder
from app.inventory.models import OrderItem as InvOrderItem
from app.orders.models import Order, OrderItem
from app.orders.service import transition_order_status
from tests.conftest import _make_user


@pytest.fixture()
def catalog_fixture(db: Session):
    """Seed test category, product, variants, and role-based price lists."""
    cat = Category(name="M1 Test Spices")
    db.add(cat)
    db.flush()

    prod = Product(name="M1 Turmeric", category_id=cat.id)
    db.add(prod)
    db.flush()

    var_100g = ProductVariant(product_id=prod.id, sku="M1-TUR-100G", pack_size_g=100)
    var_500g = ProductVariant(product_id=prod.id, sku="M1-TUR-500G", pack_size_g=500)
    db.add_all([var_100g, var_500g])
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    dealer_pl = db.query(PriceList).filter_by(name="DEALER").first()
    dist_pl = db.query(PriceList).filter_by(name="DISTRIBUTOR").first()

    # MRP prices: 100g = 50.00, 500g = 220.00
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_100g.id, price=Decimal("50.00")))
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_500g.id, price=Decimal("220.00")))

    # DEALER prices: 100g = 40.00, 500g = 180.00
    db.add(
        PriceListItem(price_list_id=dealer_pl.id, variant_id=var_100g.id, price=Decimal("40.00"))
    )
    db.add(
        PriceListItem(price_list_id=dealer_pl.id, variant_id=var_500g.id, price=Decimal("180.00"))
    )

    # DISTRIBUTOR prices: 100g = 35.00, 500g = 160.00
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_100g.id, price=Decimal("35.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_500g.id, price=Decimal("160.00")))
    db.commit()

    return {
        "category": cat,
        "product": prod,
        "var_100g": var_100g,
        "var_500g": var_500g,
        "mrp_pl": mrp_pl,
        "dealer_pl": dealer_pl,
        "dist_pl": dist_pl,
    }


def _auth_header(user) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


# ── Test 1: Price Snapshot Immutability (Invariant B8) ──────────────────────────


def test_order_price_snapshot_immutability(client: TestClient, db: Session, catalog_fixture):
    """Verify catalog price changes after order creation do not alter existing order items (B8)."""
    consumer = _make_user(db, email="b8_consumer@test.com", role_name="consumer")
    owner = _make_user(db, email="b8_owner@test.com", role_name="owner")
    var_100g = catalog_fixture["var_100g"]

    # 1. Consumer places order for 2 units of var_100g at MRP 50.00
    order_payload = {
        "items": [{"variant_id": str(var_100g.id), "quantity": "2.000"}],
        "idempotency_key": f"b8-order-{uuid.uuid4().hex[:6]}",
    }
    resp = client.post("/api/v1/orders", json=order_payload, headers=_auth_header(consumer))
    assert resp.status_code == 201
    order_data = resp.json()
    order_id = order_data["id"]
    assert order_data["total_amount"] == "100.00"
    assert order_data["items"][0]["unit_price_snapshot"] == "50.00"
    assert order_data["items"][0]["line_total"] == "100.00"

    # 2. Owner updates MRP catalog price to 75.00
    price_resp = client.put(
        f"/api/v1/products/variants/{var_100g.id}/prices/MRP",
        json={"price": 75.00},
        headers=_auth_header(owner),
    )
    assert price_resp.status_code == 200

    # 3. Retrieve original order: price snapshot must remain frozen at 50.00 and 100.00 total
    get_resp = client.get(f"/api/v1/orders/{order_id}", headers=_auth_header(consumer))
    assert get_resp.status_code == 200
    unchanged_data = get_resp.json()
    assert unchanged_data["total_amount"] == "100.00"
    assert unchanged_data["items"][0]["unit_price_snapshot"] == "50.00"
    assert unchanged_data["items"][0]["line_total"] == "100.00"

    # 4. A subsequent order places at the new price 75.00
    new_order_payload = {
        "items": [{"variant_id": str(var_100g.id), "quantity": "1.000"}],
        "idempotency_key": f"b8-new-{uuid.uuid4().hex[:6]}",
    }
    resp_new = client.post("/api/v1/orders", json=new_order_payload, headers=_auth_header(consumer))
    assert resp_new.status_code == 201
    assert resp_new.json()["total_amount"] == "75.00"
    assert resp_new.json()["items"][0]["unit_price_snapshot"] == "75.00"


# ── Test 2: Role-Based Pricing Resolution (Invariant B9) ───────────────────────


def test_order_creation_role_based_pricing(client: TestClient, db: Session, catalog_fixture):
    """Verify dealers receive DEALER prices and distributors receive DISTRIBUTOR prices (B9)."""
    dealer = _make_user(db, email="dealer_pricing@test.com", role_name="dealer")
    distributor = _make_user(db, email="dist_pricing@test.com", role_name="distributor")
    var_100g = catalog_fixture["var_100g"]

    # Dealer order: 10 units @ DEALER price 40.00 = 400.00
    resp_dealer = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_100g.id), "quantity": "10.000"}],
            "idempotency_key": f"dealer-order-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(dealer),
    )
    assert resp_dealer.status_code == 201
    assert resp_dealer.json()["items"][0]["price_list_code"] == "DEALER"
    assert resp_dealer.json()["items"][0]["unit_price_snapshot"] == "40.00"
    assert resp_dealer.json()["total_amount"] == "400.00"

    # Distributor order: 100 units @ DISTRIBUTOR price 35.00 = 3500.00
    resp_dist = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_100g.id), "quantity": "100.000"}],
            "idempotency_key": f"dist-order-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(distributor),
    )
    assert resp_dist.status_code == 201
    assert resp_dist.json()["items"][0]["price_list_code"] == "DISTRIBUTOR"
    assert resp_dist.json()["items"][0]["unit_price_snapshot"] == "35.00"
    assert resp_dist.json()["total_amount"] == "3500.00"


# ── Test 3: Idempotency Key Replay (Rule 48) ───────────────────────────────────


def test_order_idempotency_replay_identical_payload(
    client: TestClient, db: Session, catalog_fixture
):
    """Identical submission with same idempotency key returns order without duplicate DB rows."""
    consumer = _make_user(db, email="idemp_consumer@test.com", role_name="consumer")
    var_100g = catalog_fixture["var_100g"]
    idemp_key = f"idemp-key-{uuid.uuid4().hex[:8]}"

    payload = {
        "items": [{"variant_id": str(var_100g.id), "quantity": "3.000"}],
        "idempotency_key": idemp_key,
    }

    # First request
    resp1 = client.post("/api/v1/orders", json=payload, headers=_auth_header(consumer))
    assert resp1.status_code == 201
    order1 = resp1.json()

    # Repeated request with same idempotency key and identical payload
    resp2 = client.post("/api/v1/orders", json=payload, headers=_auth_header(consumer))
    assert resp2.status_code in (200, 201)
    order2 = resp2.json()

    assert order1["id"] == order2["id"]
    assert order1["order_number"] == order2["order_number"]
    assert order1["total_amount"] == order2["total_amount"]

    # Verify exactly 1 order row exists in DB with this idempotency key
    db_orders = db.query(Order).filter_by(idempotency_key=idemp_key).all()
    assert len(db_orders) == 1


# ── Test 4: Idempotency Conflict Detection (Rule 48) ───────────────────────────


def test_order_idempotency_conflict_detection(client: TestClient, db: Session, catalog_fixture):
    """Reusing an idempotency key with conflicting parameters must reject with HTTP 409."""
    consumer = _make_user(db, email="conflict_consumer@test.com", role_name="consumer")
    var_100g = catalog_fixture["var_100g"]
    var_500g = catalog_fixture["var_500g"]
    idemp_key = f"conflict-key-{uuid.uuid4().hex[:8]}"

    # 1. Create order with var_100g (qty: 1)
    resp1 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_100g.id), "quantity": "1.000"}],
            "idempotency_key": idemp_key,
        },
        headers=_auth_header(consumer),
    )
    assert resp1.status_code == 201

    # 2. Resubmit same key with different quantity (qty: 5)
    resp2 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_100g.id), "quantity": "5.000"}],
            "idempotency_key": idemp_key,
        },
        headers=_auth_header(consumer),
    )
    assert resp2.status_code == 409
    assert resp2.json()["code"] == "IDEMPOTENCY_CONFLICT"

    # 3. Resubmit same key with different variant
    resp3 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_500g.id), "quantity": "1.000"}],
            "idempotency_key": idemp_key,
        },
        headers=_auth_header(consumer),
    )
    assert resp3.status_code == 409
    assert resp3.json()["code"] == "IDEMPOTENCY_CONFLICT"


# ── Test 5: Idempotency Key via Header ─────────────────────────────────────────


def test_order_idempotency_via_http_header(client: TestClient, db: Session, catalog_fixture):
    """Verify Idempotency-Key HTTP header works seamlessly without body key."""
    consumer = _make_user(db, email="header_idemp@test.com", role_name="consumer")
    var_100g = catalog_fixture["var_100g"]
    idemp_key = f"hdr-key-{uuid.uuid4().hex[:8]}"

    headers = _auth_header(consumer)
    headers["Idempotency-Key"] = idemp_key

    payload = {"items": [{"variant_id": str(var_100g.id), "quantity": "2.000"}]}

    resp1 = client.post("/api/v1/orders", json=payload, headers=headers)
    assert resp1.status_code == 201

    resp2 = client.post("/api/v1/orders", json=payload, headers=headers)
    assert resp2.status_code in (200, 201)
    assert resp1.json()["id"] == resp2.json()["id"]


# ── Test 6: Decimal Precision & Money Arithmetic (Invariant B12) ───────────────


def test_order_decimal_precision_no_float_drift(client: TestClient, db: Session, catalog_fixture):
    """Verify calculations avoid floating-point artifacts and maintain Decimal format (B12)."""
    owner = _make_user(db, email="precision_owner@test.com", role_name="owner")
    consumer = _make_user(db, email="precision_consumer@test.com", role_name="consumer")
    var_100g = catalog_fixture["var_100g"]

    # Set price to 19.99
    client.put(
        f"/api/v1/products/variants/{var_100g.id}/prices/MRP",
        json={"price": 19.99},
        headers=_auth_header(owner),
    )

    # Order with fractional quantity 3.333
    # 19.99 * 3.333 = 66.62667 -> quantizes to 66.63
    resp = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_100g.id), "quantity": "3.333"}],
            "idempotency_key": f"dec-test-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    assert resp.status_code == 201
    data = resp.json()

    assert data["items"][0]["unit_price_snapshot"] == "19.99"
    assert data["items"][0]["quantity"] == "3.333"
    assert data["items"][0]["line_total"] == "66.63"
    assert data["total_amount"] == "66.63"


# ── Test 7: RBAC Matrix and Customer Ownership Isolation (Invariant B10) ────────


def test_order_rbac_matrix_and_customer_scoping(client: TestClient, db: Session, catalog_fixture):
    """Verify ordering permissions: customers see own only; staff see all."""
    var_100g = catalog_fixture["var_100g"]

    consumer_a = _make_user(db, email="consumer_a@test.com", role_name="consumer")
    consumer_b = _make_user(db, email="consumer_b@test.com", role_name="consumer")
    warehouse = _make_user(db, email="wh_staff@test.com", role_name="warehouse")
    factory = _make_user(db, email="factory_staff@test.com", role_name="factory")
    owner = _make_user(db, email="owner_boss@test.com", role_name="owner")

    # 1. Warehouse and Factory staff cannot create orders
    resp_wh = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_100g.id), "quantity": "1.000"}]},
        headers=_auth_header(warehouse),
    )
    assert resp_wh.status_code == 403

    resp_fac = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_100g.id), "quantity": "1.000"}]},
        headers=_auth_header(factory),
    )
    assert resp_fac.status_code == 403

    # 2. Consumer A creates Order A
    resp_a = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_100g.id), "quantity": "1.000"}]},
        headers=_auth_header(consumer_a),
    )
    assert resp_a.status_code == 201
    order_a_id = resp_a.json()["id"]

    # 3. Consumer B creates Order B
    resp_b = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_100g.id), "quantity": "2.000"}]},
        headers=_auth_header(consumer_b),
    )
    assert resp_b.status_code == 201
    order_b_id = resp_b.json()["id"]

    # 4. Consumer A lists orders: only Order A is visible
    list_a = client.get("/api/v1/orders", headers=_auth_header(consumer_a)).json()
    order_ids_a = [o["id"] for o in list_a]
    assert order_a_id in order_ids_a
    assert order_b_id not in order_ids_a

    # 5. Consumer A tries to GET Order B -> HTTP 403
    resp_unowned = client.get(f"/api/v1/orders/{order_b_id}", headers=_auth_header(consumer_a))
    assert resp_unowned.status_code == 403

    # 6. Staff (warehouse, owner) lists orders: both Order A and Order B visible
    list_wh = client.get("/api/v1/orders", headers=_auth_header(warehouse)).json()
    order_ids_wh = [o["id"] for o in list_wh]
    assert order_a_id in order_ids_wh
    assert order_b_id in order_ids_wh

    list_owner = client.get("/api/v1/orders", headers=_auth_header(owner)).json()
    order_ids_owner = [o["id"] for o in list_owner]
    assert order_a_id in order_ids_owner
    assert order_b_id in order_ids_owner

    # 7. Staff can view any order
    resp_wh_get = client.get(f"/api/v1/orders/{order_a_id}", headers=_auth_header(warehouse))
    assert resp_wh_get.status_code == 200


# ── Test 8: State Machine Transitions (Rule 49) ────────────────────────────────


def test_order_state_machine_transitions(db: Session, catalog_fixture):
    """Verify legal state transitions and rejection of illegal transitions (Rule 49)."""
    consumer = _make_user(db, email="sm_consumer@test.com", role_name="consumer")

    order = Order(
        order_number=f"ORD-SM-{uuid.uuid4().hex[:6]}",
        customer_id=consumer.id,
        status="pending",
        total_amount=Decimal("100.00"),
    )
    db.add(order)
    db.commit()

    # Valid progression: pending -> reserved -> packed -> dispatched -> delivered -> returned
    transition_order_status(order, "reserved")
    assert order.status == "reserved"

    transition_order_status(order, "packed")
    assert order.status == "packed"

    transition_order_status(order, "dispatched")
    assert order.status == "dispatched"

    transition_order_status(order, "delivered")
    assert order.status == "delivered"

    transition_order_status(order, "returned")
    assert order.status == "returned"

    # Terminal state: cannot transition from returned
    with pytest.raises(AppError) as exc_info:
        transition_order_status(order, "pending")
    assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
    assert exc_info.value.status_code == 400

    # Test illegal jumps
    order2 = Order(
        order_number=f"ORD-SM2-{uuid.uuid4().hex[:6]}",
        customer_id=consumer.id,
        status="pending",
        total_amount=Decimal("50.00"),
    )
    db.add(order2)
    db.commit()

    # Cannot skip from pending directly to dispatched
    with pytest.raises(AppError) as exc_info:
        transition_order_status(order2, "dispatched")
    assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"

    # Cancellation allowed from pending, reserved, or packed
    transition_order_status(order2, "cancelled")
    assert order2.status == "cancelled"

    # Cannot transition from cancelled
    with pytest.raises(AppError) as exc_info:
        transition_order_status(order2, "reserved")
    assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"


# ── Test 9: Validation and Error Cases ─────────────────────────────────────────


def test_order_creation_validation_errors(client: TestClient, db: Session, catalog_fixture):
    """Verify input validation: duplicate items, invalid quantities, missing variants."""
    consumer = _make_user(db, email="val_consumer@test.com", role_name="consumer")
    var_100g = catalog_fixture["var_100g"]

    # 1. Duplicate variant IDs in items list rejected by Pydantic
    resp_dup = client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"variant_id": str(var_100g.id), "quantity": "1.000"},
                {"variant_id": str(var_100g.id), "quantity": "2.000"},
            ]
        },
        headers=_auth_header(consumer),
    )
    assert resp_dup.status_code == 422

    # 2. Non-positive quantity rejected by Pydantic
    resp_qty = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_100g.id), "quantity": "0.000"}]},
        headers=_auth_header(consumer),
    )
    assert resp_qty.status_code == 422

    # 3. Non-existent variant UUID rejected by service
    fake_id = str(uuid.uuid4())
    resp_missing = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": fake_id, "quantity": "1.000"}]},
        headers=_auth_header(consumer),
    )
    assert resp_missing.status_code == 404
    assert resp_missing.json()["code"] == "VARIANT_NOT_FOUND"


# ── Test 10: Model Backward Compatibility via app.inventory.models ─────────────


def test_orders_model_backward_compatibility(db: Session):
    """Verify that importing Order and OrderItem from app.inventory.models works identically."""
    assert InvOrder is Order
    assert InvOrderItem is OrderItem

    legacy_order = InvOrder(order_number=f"ORD-LEGACY-{uuid.uuid4().hex[:6]}")
    db.add(legacy_order)
    db.commit()
    assert legacy_order.id is not None
    assert legacy_order.status == "pending"
