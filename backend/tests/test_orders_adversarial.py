"""Empirical adversarial test suite for Orders Module (Milestone M1 / T-050).

Exhaustively stress-tests:
1. Idempotency Edge Cases (Rule 48):
   - Identical replay vs conflicting payloads (variant, quantity, order_type, customer, lines).
   - Equivalent payloads (item order shuffling, decimal formatting).
   - Header vs body precedence and whitespace handling.
   - Concurrent race conditions.
   - Empty items, negative quantities, inactive variants/products, unpriced variants.
2. State Machine Invariants (Rule 49):
   - Exhaustive 64-transition matrix across all 8 states.
   - Case sensitivity and whitespace normalization.
   - Audit logging verification.
3. Price Snapshot Immutability (B8) & Decimal Precision (B12):
   - Rapid catalog price updates after order placement.
   - Randomized odd price / fractional quantity boundary stress testing.
   - Exact string serialization in JSON responses.
"""

import concurrent.futures
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.core.errors import AppError
from app.orders.models import Order
from app.orders.schemas import OrderCreate, OrderItemCreate
from app.orders.service import (
    ALLOWED_TRANSITIONS,
    OrderStatus,
    create_order,
    transition_order_status,
    update_order_status,
    validate_transition,
)
from tests.conftest import _engine, _make_user, _TestSession


@pytest.fixture()
def catalog_setup(db: Session):
    """Seed catalog category, product, variants, and price lists for stress testing."""
    cat = Category(name="Adversarial Test Category")
    db.add(cat)
    db.flush()

    prod_active = Product(name="Active Pepper", category_id=cat.id, is_active=True)
    prod_inactive = Product(name="Inactive Cardamom", category_id=cat.id, is_active=False)
    db.add_all([prod_active, prod_inactive])
    db.flush()

    var1 = ProductVariant(
        product_id=prod_active.id, sku="ADV-PEP-100G", pack_size_g=100, is_active=True
    )
    var2 = ProductVariant(
        product_id=prod_active.id, sku="ADV-PEP-500G", pack_size_g=500, is_active=True
    )
    var_inactive = ProductVariant(
        product_id=prod_active.id, sku="ADV-PEP-INACT", pack_size_g=200, is_active=False
    )
    var_in_inact_prod = ProductVariant(
        product_id=prod_inactive.id, sku="ADV-CARD-50G", pack_size_g=50, is_active=True
    )
    var_unpriced = ProductVariant(
        product_id=prod_active.id, sku="ADV-PEP-NOPRICE", pack_size_g=50, is_active=True
    )
    db.add_all([var1, var2, var_inactive, var_in_inact_prod, var_unpriced])
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    dealer_pl = db.query(PriceList).filter_by(name="DEALER").first()
    dist_pl = db.query(PriceList).filter_by(name="DISTRIBUTOR").first()

    # MRP prices
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var1.id, price=Decimal("49.99")))
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var2.id, price=Decimal("219.50")))
    db.add(
        PriceListItem(price_list_id=mrp_pl.id, variant_id=var_inactive.id, price=Decimal("99.00"))
    )
    db.add(
        PriceListItem(
            price_list_id=mrp_pl.id, variant_id=var_in_inact_prod.id, price=Decimal("150.00")
        )
    )

    # DEALER prices
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=var1.id, price=Decimal("39.99")))
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=var2.id, price=Decimal("189.50")))

    # DISTRIBUTOR prices
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var1.id, price=Decimal("34.99")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var2.id, price=Decimal("169.50")))

    db.commit()

    return {
        "var1": var1,
        "var2": var2,
        "var_inactive": var_inactive,
        "var_in_inact_prod": var_in_inact_prod,
        "var_unpriced": var_unpriced,
        "mrp_pl": mrp_pl,
        "dealer_pl": dealer_pl,
        "dist_pl": dist_pl,
    }


def _auth(user) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# SECTION 1: Exhaustive State Machine Invariants (Rule 49)
# ==============================================================================


def test_state_machine_exhaustive_64_transition_matrix(db: Session):
    """Exhaustively test all 49 possible transitions across 7 canonical statuses."""
    all_statuses = [
        OrderStatus.PENDING.value,
        OrderStatus.RESERVED.value,
        OrderStatus.PACKED.value,
        OrderStatus.DISPATCHED.value,
        OrderStatus.DELIVERED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.RETURNED.value,
    ]

    for current in all_statuses:
        allowed_targets = ALLOWED_TRANSITIONS.get(current, set())
        for target in all_statuses:
            if target in allowed_targets:
                # Must succeed without error
                validate_transition(current, target)
            else:
                # Must fail with AppError("ILLEGAL_STATE_TRANSITION")
                with pytest.raises(AppError) as exc_info:
                    validate_transition(current, target)
                assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
                assert exc_info.value.status_code == 400
                assert exc_info.value.details["current_status"] == current
                assert exc_info.value.details["target_status"] == target


@pytest.mark.parametrize(
    "current_input,target_input,expected_normalized_target",
    [
        ("  PENDING  ", "  RESERVED  ", "reserved"),
        ("Pending", "Cancelled", "cancelled"),
        ("\tpending\n", "\treserved\t", "reserved"),
        ("RESERVED", "PACKED", "packed"),
        ("packed", "DISPATCHED", "dispatched"),
        ("dispatched", " Delivered ", "delivered"),
        ("delivered", "RETURNED", "returned"),
    ],
)
def test_state_machine_case_and_whitespace_insensitivity(
    db: Session, current_input, target_input, expected_normalized_target
):
    """State transition validation and execution must normalize uppercase
    and surrounding whitespace.
    """
    order = Order(
        order_number=f"ORD-WS-{uuid.uuid4().hex[:6]}",
        status=current_input.strip().lower(),
        total_amount=Decimal("10.00"),
    )
    db.add(order)
    db.commit()

    updated = transition_order_status(order, target_input)
    assert updated.status == expected_normalized_target


def test_update_order_status_writes_audit_log(db: Session):
    """update_order_status must atomically log an audit record with before/after state."""
    actor = _make_user(db, email="actor_audit@test.com", role_name="owner")
    order = Order(
        order_number=f"ORD-AUDIT-{uuid.uuid4().hex[:6]}",
        status="pending",
        total_amount=Decimal("50.00"),
    )
    db.add(order)
    db.commit()

    res = update_order_status(db, order.id, "reserved", actor_id=actor.id)
    assert res.status == "reserved"

    # Verify audit log entry
    audit = (
        db.query(AuditLog)
        .filter_by(entity="orders", entity_id=order.id, action="UPDATE_ORDER_STATUS")
        .first()
    )
    assert audit is not None
    assert audit.actor_id == actor.id
    assert audit.before == {"status": "pending"}
    assert audit.after == {"status": "reserved"}


def test_update_order_status_not_found_raises_404(db: Session):
    """Updating a non-existent order UUID must return 404 ORDER_NOT_FOUND."""
    fake_id = uuid.uuid4()
    with pytest.raises(AppError) as exc_info:
        update_order_status(db, fake_id, "reserved")
    assert exc_info.value.code == "ORDER_NOT_FOUND"
    assert exc_info.value.status_code == 404


# ==============================================================================
# SECTION 2: Idempotency Key Stress & Edge Cases (Rule 48)
# ==============================================================================


def test_idempotency_conflicts_matrix(client: TestClient, db: Session, catalog_setup):
    """Stress-test all variations of idempotency parameter conflicts."""
    user_a = _make_user(db, email="adv_user_a@test.com", role_name="consumer")
    user_b = _make_user(db, email="adv_user_b@test.com", role_name="consumer")
    var1 = catalog_setup["var1"]
    var2 = catalog_setup["var2"]
    key = f"adv-key-{uuid.uuid4().hex[:8]}"

    # Baseline order creation: user_a, var1 (qty 2), type standard
    resp = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "2.000"}],
            "idempotency_key": key,
            "order_type": "standard",
        },
        headers=_auth(user_a),
    )
    assert resp.status_code == 201

    # Scenario 1: Different user submitting same key creates order (composite unique key)
    resp_c1 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "2.000"}],
            "idempotency_key": key,
            "order_type": "standard",
        },
        headers=_auth(user_b),
    )
    assert resp_c1.status_code == 201
    assert resp_c1.json()["id"] != resp.json()["id"]

    # Conflict 1: Same user, different variant
    resp_c2 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var2.id), "quantity": "2.000"}],
            "idempotency_key": key,
            "order_type": "standard",
        },
        headers=_auth(user_a),
    )
    assert resp_c2.status_code == 409

    # Conflict 3: Same user, different quantity (2 vs 5)
    resp_c3 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "5.000"}],
            "idempotency_key": key,
            "order_type": "standard",
        },
        headers=_auth(user_a),
    )
    assert resp_c3.status_code == 409

    # Conflict 4: Same user, additional item added
    resp_c4 = client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"variant_id": str(var1.id), "quantity": "2.000"},
                {"variant_id": str(var2.id), "quantity": "1.000"},
            ],
            "idempotency_key": key,
            "order_type": "standard",
        },
        headers=_auth(user_a),
    )
    assert resp_c4.status_code == 409

    # Conflict 5: Same user, different order_type ("wholesale" vs "standard")
    resp_c5 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "2.000"}],
            "idempotency_key": key,
            "order_type": "wholesale",
        },
        headers=_auth(user_a),
    )
    assert resp_c5.status_code == 409


def test_idempotency_equivalent_payload_permutations(
    client: TestClient, db: Session, catalog_setup
):
    """Equivalent payloads (shuffled lines, equivalent Decimal representations)
    must replay successfully.
    """
    user = _make_user(db, email="equiv_user@test.com", role_name="consumer")
    var1 = catalog_setup["var1"]
    var2 = catalog_setup["var2"]
    key = f"adv-equiv-{uuid.uuid4().hex[:8]}"

    # Initial order with var1 then var2
    resp1 = client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"variant_id": str(var1.id), "quantity": "2.000"},
                {"variant_id": str(var2.id), "quantity": "3.500"},
            ],
            "idempotency_key": key,
        },
        headers=_auth(user),
    )
    assert resp1.status_code == 201
    orig_id = resp1.json()["id"]

    # Replay with reversed items: var2 then var1
    resp2 = client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"variant_id": str(var2.id), "quantity": "3.500"},
                {"variant_id": str(var1.id), "quantity": "2.000"},
            ],
            "idempotency_key": key,
        },
        headers=_auth(user),
    )
    assert resp2.status_code in (200, 201)
    assert resp2.json()["id"] == orig_id

    # Replay with equivalent numeric decimals (2 vs 2.000, 3.5 vs 3.500)
    resp3 = client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"variant_id": str(var1.id), "quantity": "2"},
                {"variant_id": str(var2.id), "quantity": "3.5"},
            ],
            "idempotency_key": key,
        },
        headers=_auth(user),
    )
    assert resp3.status_code in (200, 201)
    assert resp3.json()["id"] == orig_id


def test_idempotency_header_vs_payload_precedence(client: TestClient, db: Session, catalog_setup):
    """Header Idempotency-Key must take precedence over body idempotency_key."""
    user = _make_user(db, email="hdr_prec@test.com", role_name="consumer")
    var1 = catalog_setup["var1"]
    header_key = f"hdr-key-{uuid.uuid4().hex[:8]}"
    body_key = f"body-key-{uuid.uuid4().hex[:8]}"

    # 1. Post with both header and body keys
    headers = _auth(user)
    headers["Idempotency-Key"] = header_key
    resp = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "1.000"}],
            "idempotency_key": body_key,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["idempotency_key"] == header_key

    # 2. Replay with header_key only (no body key) -> must succeed and return same order
    resp_replay = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "1.000"}]},
        headers=headers,
    )
    assert resp_replay.status_code in (200, 201)
    assert resp_replay.json()["id"] == resp.json()["id"]

    # 3. Post with body_key alone -> creates a distinct new order because
    # body_key was never registered
    resp_body = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "1.000"}],
            "idempotency_key": body_key,
        },
        headers=_auth(user),
    )
    assert resp_body.status_code == 201
    assert resp_body.json()["idempotency_key"] == body_key
    assert resp_body.json()["id"] != resp.json()["id"]


def test_orders_without_idempotency_key_succeed_independently(
    client: TestClient, db: Session, catalog_setup
):
    """Orders submitted without an idempotency key create independent orders without colliding."""
    user = _make_user(db, email="no_idemp@test.com", role_name="consumer")
    var1 = catalog_setup["var1"]

    resp1 = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "1.000"}]},
        headers=_auth(user),
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "1.000"}]},
        headers=_auth(user),
    )
    assert resp2.status_code == 201
    assert resp1.json()["id"] != resp2.json()["id"]
    assert resp1.json()["idempotency_key"] is None
    assert resp2.json()["idempotency_key"] is None


def test_validation_inactive_and_unpriced_variants(client: TestClient, db: Session, catalog_setup):
    """Orders must reject inactive variants, active variants of inactive
    products, and unpriced variants.
    """
    user = _make_user(db, email="inact_val@test.com", role_name="consumer")
    var_inactive = catalog_setup["var_inactive"]
    var_in_inact_prod = catalog_setup["var_in_inact_prod"]
    var_unpriced = catalog_setup["var_unpriced"]

    # Inactive variant
    resp_inact = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_inactive.id), "quantity": "1.000"}]},
        headers=_auth(user),
    )
    assert resp_inact.status_code == 400
    assert resp_inact.json()["code"] == "VARIANT_INACTIVE"

    # Active variant of inactive product
    resp_prod_inact = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_in_inact_prod.id), "quantity": "1.000"}]},
        headers=_auth(user),
    )
    assert resp_prod_inact.status_code == 400
    assert resp_prod_inact.json()["code"] == "VARIANT_INACTIVE"

    # Variant with no price in consumer's MRP list
    resp_unpriced = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_unpriced.id), "quantity": "1.000"}]},
        headers=_auth(user),
    )
    assert resp_unpriced.status_code == 404
    assert resp_unpriced.json()["code"] == "PRICE_NOT_FOUND"


def test_validation_extreme_quantities_and_empty_items(
    client: TestClient, db: Session, catalog_setup
):
    """Validate boundary rejection for negative, zero, fractional overflow, and empty items."""
    user = _make_user(db, email="qty_val@test.com", role_name="consumer")
    var1 = catalog_setup["var1"]

    # 1. Negative quantity
    resp_neg = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "-2.500"}]},
        headers=_auth(user),
    )
    assert resp_neg.status_code == 422

    # 2. Zero quantity
    resp_zero = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "0.000"}]},
        headers=_auth(user),
    )
    assert resp_zero.status_code == 422

    # 3. 4 decimal places (> 3 allowed)
    resp_four_dec = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "1.2345"}]},
        headers=_auth(user),
    )
    assert resp_four_dec.status_code == 422

    # 4. Empty items list
    resp_empty = client.post(
        "/api/v1/orders",
        json={"items": []},
        headers=_auth(user),
    )
    assert resp_empty.status_code == 422


# ==============================================================================
# SECTION 3: Price Snapshot Immutability (B8) & Decimal Precision (B12)
# ==============================================================================


def test_price_snapshot_rapid_catalog_mutations(client: TestClient, db: Session, catalog_setup):
    """Ensure price snapshot on order items remains completely frozen
    through rapid catalog updates.
    """
    consumer = _make_user(db, email="freeze_consumer@test.com", role_name="consumer")
    owner = _make_user(db, email="freeze_owner@test.com", role_name="owner")
    var1 = catalog_setup["var1"]

    # Initial price is 49.99
    order_resp = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var1.id), "quantity": "4.000"}],
            "idempotency_key": f"freeze-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth(consumer),
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["id"]
    # 49.99 * 4 = 199.96
    assert order_resp.json()["total_amount"] == "199.96"

    # Rapid catalog mutations: 49.99 -> 59.99 -> 79.50 -> 12.00
    for new_price in [59.99, 79.50, 12.00]:
        client.put(
            f"/api/v1/products/variants/{var1.id}/prices/MRP",
            json={"price": new_price},
            headers=_auth(owner),
        )

    # Re-fetch order: must remain frozen at 49.99 and 199.96
    get_resp = client.get(f"/api/v1/orders/{order_id}", headers=_auth(consumer))
    assert get_resp.status_code == 200
    frozen_data = get_resp.json()
    assert frozen_data["total_amount"] == "199.96"
    assert frozen_data["items"][0]["unit_price_snapshot"] == "49.99"
    assert frozen_data["items"][0]["line_total"] == "199.96"

    # New order gets the latest price (12.00)
    new_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": "4.000"}]},
        headers=_auth(consumer),
    )
    assert new_resp.status_code == 201
    assert new_resp.json()["total_amount"] == "48.00"
    assert new_resp.json()["items"][0]["unit_price_snapshot"] == "12.00"


@pytest.mark.parametrize(
    "unit_price,quantity,expected_line_total",
    [
        (Decimal("10.55"), Decimal("0.005"), Decimal("0.05")),  # 0.05275 -> 0.05
        (Decimal("10.55"), Decimal("0.001"), Decimal("0.01")),  # 0.01055 -> 0.01
        (Decimal("0.01"), Decimal("0.001"), Decimal("0.00")),  # 0.00001 -> 0.00
        (Decimal("33.33"), Decimal("3.333"), Decimal("111.09")),  # 111.08889 -> 111.09
        (
            Decimal("999.99"),
            Decimal("10.500"),
            Decimal("10499.90"),
        ),  # 10499.895 -> 10499.90 (even round)
        (Decimal("7.13"), Decimal("1.414"), Decimal("10.08")),  # 10.08182 -> 10.08
    ],
)
def test_decimal_precision_rounding_boundaries(
    client: TestClient, db: Session, catalog_setup, unit_price, quantity, expected_line_total
):
    """Stress-test odd decimal prices and fractional quantities at Banker's rounding boundaries."""
    owner = _make_user(db, email=f"dec_owner_{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    consumer = _make_user(
        db, email=f"dec_cons_{uuid.uuid4().hex[:6]}@test.com", role_name="consumer"
    )
    var1 = catalog_setup["var1"]

    # Set precise catalog price
    client.put(
        f"/api/v1/products/variants/{var1.id}/prices/MRP",
        json={"price": float(unit_price)},
        headers=_auth(owner),
    )

    resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var1.id), "quantity": str(quantity)}]},
        headers=_auth(consumer),
    )
    assert resp.status_code == 201
    item = resp.json()["items"][0]
    assert item["line_total"] == str(expected_line_total)
    assert resp.json()["total_amount"] == str(expected_line_total)


def test_multi_item_order_total_reconciliation(client: TestClient, db: Session, catalog_setup):
    """Test multi-line order: total_amount must exactly equal sum of
    line_totals with zero float drift.
    """
    consumer = _make_user(db, email="reconcile_cons@test.com", role_name="consumer")
    var1 = catalog_setup["var1"]  # MRP 49.99
    var2 = catalog_setup["var2"]  # MRP 219.50

    # 49.99 * 3.333 = 166.61667 -> 166.62
    # 219.50 * 1.125 = 246.9375 -> 246.94
    # Expected total = 166.62 + 246.94 = 413.56
    resp = client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"variant_id": str(var1.id), "quantity": "3.333"},
                {"variant_id": str(var2.id), "quantity": "1.125"},
            ]
        },
        headers=_auth(consumer),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["items"][0]["line_total"] == "166.62"
    assert data["items"][1]["line_total"] == "246.94"
    assert data["total_amount"] == "413.56"


# ==============================================================================
# SECTION 4: Direct Service Layer Concurrency and Invariant Stress
# ==============================================================================


@pytest.mark.skipif(
    _engine.dialect.name != "postgresql",
    reason="Multithreaded SQLite in-memory engine cannot support concurrent write transactions.",
)
def test_service_create_order_race_collision(db: Session, catalog_setup):
    """Simulate multithreaded collision where two threads race on the same idempotency key."""
    var1 = catalog_setup["var1"]
    shared_key = f"race-key-{uuid.uuid4().hex[:8]}"
    pre_user = _make_user(db, email="adv_race_user@test.com", role_name="consumer")
    user_id = pre_user.id

    def _worker(thread_idx: int):
        session = _TestSession()
        try:
            user = session.get(User, user_id)
            payload = OrderCreate(
                items=[OrderItemCreate(variant_id=var1.id, quantity=Decimal("2.000"))],
                idempotency_key=shared_key,
            )
            order, is_replayed = create_order(
                db=session, user=user, payload=payload, idempotency_key=shared_key
            )
            return (order.id, is_replayed)
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_worker, 1)
        f2 = executor.submit(_worker, 2)
        r1 = f1.result()
        r2 = f2.result()

    # Both threads must succeed and return the EXACT same order UUID
    assert r1[0] == r2[0]
    # Exactly one thread created and one thread replayed
    replayed_flags = [r1[1], r2[1]]
    assert True in replayed_flags
    assert False in replayed_flags


def test_service_create_order_integrity_error_recovery_identical(
    db: Session, catalog_setup, monkeypatch
):
    """Verify create_order atomic commit IntegrityError recovery when key was
    inserted in parallel.
    """
    from sqlalchemy.exc import IntegrityError

    var1 = catalog_setup["var1"]
    user = _make_user(db, email="integ_user@test.com", role_name="consumer")
    key = f"integ-key-{uuid.uuid4().hex[:8]}"
    payload = OrderCreate(
        items=[OrderItemCreate(variant_id=var1.id, quantity=Decimal("2.000"))],
        idempotency_key=key,
    )

    # 1. Create the existing order first
    orig_order, _ = create_order(db=db, user=user, payload=payload, idempotency_key=key)

    # 2. Simulate parallel race condition: bypass pre-check and force IntegrityError on commit
    original_commit = db.commit
    committed_once = False

    def _fake_commit():
        nonlocal committed_once
        if not committed_once:
            committed_once = True
            raise IntegrityError(
                "UNIQUE constraint failed: orders.idempotency_key", params=None, orig=Exception()
            )
        original_commit()

    monkeypatch.setattr(db, "commit", _fake_commit)

    # Calling create_order now should catch IntegrityError and replay existing order
    replayed_order, is_replayed = create_order(
        db=db, user=user, payload=payload, idempotency_key=key
    )
    assert is_replayed is True
    assert replayed_order.id == orig_order.id


def test_service_create_order_integrity_error_recovery_conflict(
    db: Session, catalog_setup, monkeypatch
):
    """Verify create_order atomic commit IntegrityError raises 409 if parallel
    order has different payload.
    """
    from sqlalchemy.exc import IntegrityError

    var1 = catalog_setup["var1"]
    var2 = catalog_setup["var2"]
    user = _make_user(db, email="integ_conf_user@test.com", role_name="consumer")
    key = f"integ-conf-{uuid.uuid4().hex[:8]}"

    # 1. First order creates with var1
    payload1 = OrderCreate(
        items=[OrderItemCreate(variant_id=var1.id, quantity=Decimal("2.000"))],
        idempotency_key=key,
    )
    create_order(db=db, user=user, payload=payload1, idempotency_key=key)

    # 2. Parallel request tries to create with var2 and hits IntegrityError on commit
    payload2 = OrderCreate(
        items=[OrderItemCreate(variant_id=var2.id, quantity=Decimal("2.000"))],
        idempotency_key=key,
    )

    def _fake_commit():
        raise IntegrityError(
            "UNIQUE constraint failed: orders.idempotency_key", params=None, orig=Exception()
        )

    monkeypatch.setattr(db, "commit", _fake_commit)

    with pytest.raises(AppError) as exc_info:
        create_order(db=db, user=user, payload=payload2, idempotency_key=key)
    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc_info.value.status_code == 409
