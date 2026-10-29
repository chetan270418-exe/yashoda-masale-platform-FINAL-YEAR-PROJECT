"""Adversarial Empirical Stress Tests for Milestone M2: Order Fulfillment & Inventory Lifecycle.

Authored by EMPIRICAL CHALLENGER 1 for Milestone M2 (T-051).

Empirically tests and verifies:
1. Comprehensive Illegal State Transition Attacks:
   - State-matrix attacks across endpoints (/reserve, /pack, /dispatch, /deliver, /return, /cancel).
   - Attempting invalid transitions (e.g. dispatching pending/cancelled, returning undelivered).
2. Multi-Item Partial Reservation Failure & Atomic Rollback (Invariants B1-B4):
   - Multi-item order where Item 1 and Item 2 have stock, but Item 3 fails stock check.
   - Verifies HTTP 400 INSUFFICIENT_STOCK, order status remains 'pending'.
   - Verifies DB rollback: items 1 and 2 reservations released, balances restored to 0.
3. Idempotency & Double-Cancellation Defense (Invariant B6 & CAS):
   - Cancelling order twice: first succeeds, second rejected with 400 ILLEGAL_STATE_TRANSITION.
   - Verifies CAS prevents duplicate inventory release (reserved balance does NOT drop negative).
   - Verifies direct service-level CAS isolation (RESERVATION_NOT_ACTIVE).
4. Non-Negative Stock Invariants (Invariant B4):
   - Over-reservation rejected, stock never negative.
   - Complete stock exhaustion to exactly 0.00 upon dispatch; second dispatch rejected.
   - Cancellation on pending order with 0 reserved does not drive reserved negative.
5. Invariant B7 Quarantine Integrity:
   - Returned goods are quarantined and NOT added back to on_hand / sellable inventory.
   - Blank return reason validation.
   - Double-return attack rejection.
6. RBAC Isolation & Ownership Hijack Attacks:
   - Customer role forbidden from staff endpoints (/reserve, /pack, /dispatch, /deliver, /return).
   - Customer cannot cancel another customer's order.
   - Customer cannot cancel once packed; staff can cancel up to packed.
   - Unauthenticated caller rejected with 401.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.core.errors import AppError
from app.inventory import service as inventory_service
from app.inventory.models import (
    InventoryBalance,
    ProductionBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)
from app.orders.models import Order, OrderItem
from tests.conftest import _make_user

# ── Fixtures & Setup Helpers ──────────────────────────────────────────────────


@pytest.fixture()
def empirical_catalog(db: Session) -> dict[str, Any]:
    """Seed test category, products, 3 variants, and role price lists."""
    cat = Category(name=f"Empirical Masale {uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Empirical Special Chilli {uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var_1 = ProductVariant(
        product_id=prod.id, sku=f"EMP-CH-100G-{uuid.uuid4().hex[:4]}", pack_size_g=100
    )
    var_2 = ProductVariant(
        product_id=prod.id, sku=f"EMP-CH-250G-{uuid.uuid4().hex[:4]}", pack_size_g=250
    )
    var_3 = ProductVariant(
        product_id=prod.id, sku=f"EMP-CH-500G-{uuid.uuid4().hex[:4]}", pack_size_g=500
    )
    db.add_all([var_1, var_2, var_3])
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    dealer_pl = db.query(PriceList).filter_by(name="DEALER").first()

    for v, p in [(var_1, Decimal("50.00")), (var_2, Decimal("110.00")), (var_3, Decimal("210.00"))]:
        db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=v.id, price=p))
        db.add(
            PriceListItem(price_list_id=dealer_pl.id, variant_id=v.id, price=p * Decimal("0.85"))
        )
    db.commit()

    return {
        "cat": cat,
        "prod": prod,
        "var_1": var_1,
        "var_2": var_2,
        "var_3": var_3,
        "mrp_pl": mrp_pl,
        "dealer_pl": dealer_pl,
    }


@pytest.fixture()
def empirical_warehouse(db: Session) -> Warehouse:
    """Provide a designated primary test warehouse."""
    wh = Warehouse(
        code=f"WH-EMP-{uuid.uuid4().hex[:6]}",
        name="Empirical Central Depot",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.commit()
    return wh


def _auth_header(user: User) -> dict[str, str]:
    """Generate Bearer authorization header for a test user."""
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


def _seed_batch_balance(
    db: Session,
    warehouse: Warehouse,
    variant: ProductVariant,
    batch_code: str,
    days_to_expiry: int,
    on_hand: int | Decimal,
    reserved: int | Decimal = 0,
    status: str = "released",
) -> tuple[ProductionBatch, InventoryBalance]:
    """Helper to create a batch and its inventory balance."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"{batch_code}-{uuid.uuid4().hex[:4]}",
        manufactured_at=today - timedelta(days=5),
        expiry_date=today + timedelta(days=days_to_expiry),
        status=status,
    )
    db.add(batch)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal(str(on_hand)),
        reserved=Decimal(str(reserved)),
    )
    db.add(balance)
    db.commit()
    db.refresh(batch)
    db.refresh(balance)
    return batch, balance


# ── Scenario 1: Illegal State Transition Attacks ─────────────────────────────


def test_adversarial_illegal_state_transition_matrix(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Attempt illegal transitions across all lifecycle endpoints.

    Verifies that every illegal transition raises HTTP 400 with ILLEGAL_STATE_TRANSITION.
    """
    consumer = _make_user(db, email="adv_trans_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_trans_staff@test.com", role_name="warehouse")
    var_1 = empirical_catalog["var_1"]

    # 1. Dispatching a PENDING order directly -> ILLEGAL
    create_payload = {
        "items": [{"variant_id": str(var_1.id), "quantity": "3.000"}],
        "idempotency_key": f"adv-pending-{uuid.uuid4().hex[:6]}",
    }
    resp = client.post("/api/v1/orders", json=create_payload, headers=_auth_header(consumer))
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    resp_disp = client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff))
    assert resp_disp.status_code == 400
    assert resp_disp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 2. Packing a PENDING order directly -> ILLEGAL
    resp_pack = client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))
    assert resp_pack.status_code == 400
    assert resp_pack.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 3. Delivering a PENDING order directly -> ILLEGAL
    resp_deliv = client.post(f"/api/v1/orders/{order_id}/deliver", headers=_auth_header(staff))
    assert resp_deliv.status_code == 400
    assert resp_deliv.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 4. Returning an undelivered PENDING order -> ILLEGAL
    resp_ret = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "Premature return attempt"},
        headers=_auth_header(staff),
    )
    assert resp_ret.status_code == 400
    assert resp_ret.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 5. Cancel the order -> status becomes CANCELLED
    resp_cancel = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Testing cancellation"},
        headers=_auth_header(staff),
    )
    assert resp_cancel.status_code == 200
    assert resp_cancel.json()["status"] == "cancelled"

    # 6. Attempting any forward transition from CANCELLED is strictly ILLEGAL
    for ep, payload in [
        ("/reserve", None),
        ("/pack", None),
        ("/dispatch", None),
        ("/deliver", None),
        ("/return", {"reason": "Return after cancel"}),
    ]:
        r = client.post(f"/api/v1/orders/{order_id}{ep}", json=payload, headers=_auth_header(staff))
        assert (
            r.status_code == 400
        ), f"Expected 400 for {ep} on cancelled order, got {r.status_code}"
        assert r.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 7. Re-reserving an already RESERVED order -> ILLEGAL
    _seed_batch_balance(db, empirical_warehouse, var_1, "ADV-RES", 60, on_hand=20)
    resp2 = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "2.000"}],
            "idempotency_key": f"adv-reres-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    order2_id = resp2.json()["id"]

    r_res1 = client.post(f"/api/v1/orders/{order2_id}/reserve", headers=_auth_header(staff))
    assert r_res1.status_code == 200
    assert r_res1.json()["status"] == "reserved"

    # Re-reserve attempt
    r_res2 = client.post(f"/api/v1/orders/{order2_id}/reserve", headers=_auth_header(staff))
    assert r_res2.status_code == 400
    assert r_res2.json()["code"] == "ILLEGAL_STATE_TRANSITION"


# ── Scenario 2: Multi-Item Partial Reservation Failure (B4 + Atomicity) ───────


def test_adversarial_multi_item_partial_reservation_failure_rollback(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Multi-item order with 3 items where item 3 fails stock check.

    Invariants tested:
    - B4: Stock never goes negative; reservations never exceed available stock.
    - Atomicity: Reservations for items 1 and 2 must be genuinely cancelled and unreserved in DB.
    - Order status remains 'pending', and active reservation count is strictly 0.
    """
    consumer = _make_user(db, email="adv_multi_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_multi_staff@test.com", role_name="warehouse")

    var_1 = empirical_catalog["var_1"]
    var_2 = empirical_catalog["var_2"]
    var_3 = empirical_catalog["var_3"]

    # Seed stock:
    # Item 1: 25 on hand
    # Item 2: 20 on hand
    # Item 3: only 4 on hand (we will request 15!)
    _batch_1, bal_1 = _seed_batch_balance(db, empirical_warehouse, var_1, "ADV-M1", 40, on_hand=25)
    _batch_2, bal_2 = _seed_batch_balance(db, empirical_warehouse, var_2, "ADV-M2", 50, on_hand=20)
    _batch_3, bal_3 = _seed_batch_balance(db, empirical_warehouse, var_3, "ADV-M3", 60, on_hand=4)

    # Create 3-item order
    payload = {
        "items": [
            {"variant_id": str(var_1.id), "quantity": "10.000"},  # available 25 -> passes
            {"variant_id": str(var_2.id), "quantity": "8.000"},  # available 20 -> passes
            {"variant_id": str(var_3.id), "quantity": "15.000"},  # available 4  -> FAILS!
        ],
        "idempotency_key": f"adv-multi-fail-{uuid.uuid4().hex[:6]}",
    }
    create_resp = client.post("/api/v1/orders", json=payload, headers=_auth_header(consumer))
    assert create_resp.status_code == 201
    order_id = create_resp.json()["id"]

    # Staff attempts to reserve order
    reserve_resp = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert reserve_resp.status_code == 400
    assert reserve_resp.json()["code"] == "INSUFFICIENT_STOCK"

    # EMPIRICAL DB VERIFICATION:
    # 1. Order status MUST remain 'pending'
    order_db = db.scalar(select(Order).where(Order.id == uuid.UUID(order_id)))
    assert order_db is not None
    assert order_db.status == "pending"

    # 2. Inventory balances for items 1 and 2 must have reserved == 0 (fully rolled back!)
    db.refresh(bal_1)
    db.refresh(bal_2)
    db.refresh(bal_3)

    assert bal_1.on_hand == Decimal("25.000")
    assert bal_1.reserved == Decimal("0.000"), f"Expected bal_1.reserved=0, got {bal_1.reserved}"

    assert bal_2.on_hand == Decimal("20.000")
    assert bal_2.reserved == Decimal("0.000"), f"Expected bal_2.reserved=0, got {bal_2.reserved}"

    assert bal_3.on_hand == Decimal("4.000")
    assert bal_3.reserved == Decimal("0.000"), f"Expected bal_3.reserved=0, got {bal_3.reserved}"

    # 3. Active reservations count in DB for this order MUST be 0
    active_res = db.scalars(
        select(StockReservation)
        .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
        .where(
            OrderItem.order_id == uuid.UUID(order_id),
            StockReservation.status == "active",
        )
    ).all()
    assert len(active_res) == 0, f"Found {len(active_res)} unreleased active reservations!"

    # 4. Any intermediate reservations created for items 1 & 2 must be marked 'released'
    all_res = db.scalars(
        select(StockReservation)
        .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
        .where(OrderItem.order_id == uuid.UUID(order_id))
    ).all()
    for res in all_res:
        assert (
            res.status == "released"
        ), f"Reservation {res.id} status is {res.status}, expected 'released'"

    # 5. Invariant B5: Verify stock_movements ledger contains balanced reserve and release entries
    movements = db.scalars(
        select(StockMovement)
        .where(StockMovement.warehouse_id == empirical_warehouse.id)
        .order_by(StockMovement.created_at.asc())
    ).all()
    reserve_moves = [m for m in movements if m.movement_type == "reserve"]
    release_moves = [m for m in movements if m.movement_type == "release"]
    assert len(reserve_moves) == len(
        release_moves
    ), f"Mismatched ledger: {len(reserve_moves)} reserves vs {len(release_moves)} releases"


# ── Scenario 3: Idempotency & Double-Cancellation (B6 + CAS) ──────────────────


def test_adversarial_double_cancellation_cas_protection(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Attempting to cancel an order twice.

    Invariants tested:
    - B6: Cancelling an order releases its reservation exactly once.
    - CAS: Second cancellation call must be rejected with 400.
    - Service-layer CAS: direct call on already released reservation fails.
    """
    consumer = _make_user(db, email="adv_cancel_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_cancel_staff@test.com", role_name="warehouse")
    var_1 = empirical_catalog["var_1"]
    var_2 = empirical_catalog["var_2"]

    # Seed stock: 15 on hand each
    _b1, bal_1 = _seed_batch_balance(db, empirical_warehouse, var_1, "ADV-C1", 45, on_hand=15)
    _b2, bal_2 = _seed_batch_balance(db, empirical_warehouse, var_2, "ADV-C2", 55, on_hand=15)

    # Create 2-item order
    payload = {
        "items": [
            {"variant_id": str(var_1.id), "quantity": "5.000"},
            {"variant_id": str(var_2.id), "quantity": "7.000"},
        ],
        "idempotency_key": f"adv-dbl-cancel-{uuid.uuid4().hex[:6]}",
    }
    resp = client.post("/api/v1/orders", json=payload, headers=_auth_header(consumer))
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    # Reserve the order
    r_res = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert r_res.status_code == 200

    db.refresh(bal_1)
    db.refresh(bal_2)
    assert bal_1.reserved == Decimal("5.000")
    assert bal_2.reserved == Decimal("7.000")

    # 1. First Cancellation Call -> Must succeed
    r_cancel1 = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Customer cancellation request"},
        headers=_auth_header(staff),
    )
    assert r_cancel1.status_code == 200
    assert r_cancel1.json()["status"] == "cancelled"

    db.refresh(bal_1)
    db.refresh(bal_2)
    assert bal_1.reserved == Decimal("0.000")
    assert bal_2.reserved == Decimal("0.000")

    # Count release movements so far
    moves_after_first = db.scalars(
        select(StockMovement).where(StockMovement.movement_type == "release")
    ).all()
    count_releases_first = len(moves_after_first)
    assert count_releases_first >= 2

    # 2. Second Cancellation Call -> Attack: Attempt double release
    r_cancel2 = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Attacker trying to double-release inventory"},
        headers=_auth_header(staff),
    )
    # Must be rejected with 400 ILLEGAL_STATE_TRANSITION
    assert r_cancel2.status_code == 400
    assert r_cancel2.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # EMPIRICAL INVARIANT B6 & B4 VERIFICATION:
    # Reserved balances must still be exactly 0.000 (NOT -5.000 or -7.000!)
    db.refresh(bal_1)
    db.refresh(bal_2)
    assert bal_1.reserved == Decimal(
        "0.000"
    ), f"Invariant B4/B6 violated! bal_1.reserved={bal_1.reserved}"
    assert bal_2.reserved == Decimal(
        "0.000"
    ), f"Invariant B4/B6 violated! bal_2.reserved={bal_2.reserved}"
    assert bal_1.on_hand == Decimal("15.000")
    assert bal_2.on_hand == Decimal("15.000")

    # Ledger must not have received any additional release movements
    moves_after_second = db.scalars(
        select(StockMovement).where(StockMovement.movement_type == "release")
    ).all()
    assert len(moves_after_second) == count_releases_first, (
        f"Double cancel generated duplicate movements! "
        f"{len(moves_after_second)} > {count_releases_first}"
    )

    # 3. Direct Service Layer Attack: Try to cancel an already released reservation directly
    released_res = db.scalars(
        select(StockReservation)
        .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
        .where(OrderItem.order_id == uuid.UUID(order_id))
    ).first()
    assert released_res is not None
    assert released_res.status == "released"

    with pytest.raises(AppError) as exc_info:
        inventory_service.cancel_reservation(
            db=db,
            reservation_id=released_res.id,
            actor_id=staff.id,
        )
    assert exc_info.value.code == "RESERVATION_ALREADY_PROCESSED"
    assert exc_info.value.status_code == 409


# ── Scenario 4: Non-Negative Stock Invariants (B4) ───────────────────────────


def test_adversarial_non_negative_stock_under_all_operations(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Stock balances (on_hand, reserved) must never become negative.

    Invariants tested:
    - B4: Stock never goes negative under reservation, dispatch, or cancellation.
    - Zero boundary: Complete stock exhaustion to 0.00 on dispatch is valid.
    - Unreserved order cancellation: Cancelling pending order with 0 reserved stays 0.
    """
    consumer = _make_user(db, email="adv_nonneg_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_nonneg_staff@test.com", role_name="warehouse")
    var_1 = empirical_catalog["var_1"]

    # 1. Seed exactly 6 units of stock
    _b, bal = _seed_batch_balance(db, empirical_warehouse, var_1, "ADV-NN", 35, on_hand=6)

    # Subcase A: Over-reservation attempt (asking for 7 when 6 available)
    r_over = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "7.000"}],
            "idempotency_key": f"adv-over-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    over_order_id = r_over.json()["id"]

    r_res_over = client.post(f"/api/v1/orders/{over_order_id}/reserve", headers=_auth_header(staff))
    assert r_res_over.status_code == 400
    assert r_res_over.json()["code"] == "INSUFFICIENT_STOCK"

    db.refresh(bal)
    assert bal.on_hand == Decimal("6.000")
    assert bal.reserved == Decimal("0.000")
    assert bal.on_hand >= bal.reserved >= Decimal("0.000")

    # Subcase B: Stock exhaustion to exact 0.00 upon dispatch
    r_exact = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "6.000"}],
            "idempotency_key": f"adv-exact-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    exact_order_id = r_exact.json()["id"]

    # Reserve all 6 units
    r_res_exact = client.post(
        f"/api/v1/orders/{exact_order_id}/reserve", headers=_auth_header(staff)
    )
    assert r_res_exact.status_code == 200

    db.refresh(bal)
    assert bal.on_hand == Decimal("6.000")
    assert bal.reserved == Decimal("6.000")

    # Pack order
    r_pack = client.post(f"/api/v1/orders/{exact_order_id}/pack", headers=_auth_header(staff))
    assert r_pack.status_code == 200

    # Dispatch order -> exhausts stock completely
    r_disp = client.post(
        f"/api/v1/orders/{exact_order_id}/dispatch",
        json={"carrier": "Express Cargo", "tracking_ref": "TRK-0001"},
        headers=_auth_header(staff),
    )
    assert r_disp.status_code == 200
    assert r_disp.json()["status"] == "dispatched"

    db.refresh(bal)
    # Empirically verify balances are exactly 0.000, NOT negative!
    assert bal.on_hand == Decimal("0.000"), f"Expected on_hand=0.000, got {bal.on_hand}"
    assert bal.reserved == Decimal("0.000"), f"Expected reserved=0.000, got {bal.reserved}"
    assert bal.on_hand >= Decimal("0.000")
    assert bal.reserved >= Decimal("0.000")

    # Attack: Calling dispatch a second time on the already dispatched order -> REJECTED
    r_disp_again = client.post(
        f"/api/v1/orders/{exact_order_id}/dispatch", headers=_auth_header(staff)
    )
    assert r_disp_again.status_code == 400
    assert r_disp_again.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    db.refresh(bal)
    assert bal.on_hand == Decimal("0.000")
    assert bal.reserved == Decimal("0.000")

    # Subcase C: Cancellation on unreserved pending order
    r_pend = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "2.000"}],
            "idempotency_key": f"adv-unres-cancel-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    pend_order_id = r_pend.json()["id"]

    # Cancel directly from pending (reserved is 0)
    r_cancel_pend = client.post(
        f"/api/v1/orders/{pend_order_id}/cancel",
        json={"reason": "Customer cancelled unreserved order"},
        headers=_auth_header(consumer),
    )
    assert r_cancel_pend.status_code == 200
    assert r_cancel_pend.json()["status"] == "cancelled"

    db.refresh(bal)
    assert bal.reserved == Decimal("0.000")
    assert bal.on_hand == Decimal("0.000")


# ── Scenario 5: Invariant B7 Quarantine Integrity & Return Hardening ─────────


def test_adversarial_return_quarantine_integrity(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Return lifecycle and Invariant B7 enforcement.

    Invariants tested:
    - B7: Returned goods are quarantined and NOT added to sellable stock balances (on_hand).
    - Validation: Empty or whitespace return reason rejected with 400.
    - Double return attack: Repeated return calls rejected with 400.
    """
    consumer = _make_user(db, email="adv_ret_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_ret_staff@test.com", role_name="warehouse")
    var_1 = empirical_catalog["var_1"]

    # Seed 10 units
    _b, bal = _seed_batch_balance(db, empirical_warehouse, var_1, "ADV-RET", 50, on_hand=10)

    # Place order for 4 units
    resp = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "4.000"}],
            "idempotency_key": f"adv-ret-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    order_id = resp.json()["id"]

    # Progress to delivered: reserve -> pack -> dispatch -> deliver
    assert (
        client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/orders/{order_id}/deliver", headers=_auth_header(staff)).status_code
        == 200
    )

    db.refresh(bal)
    # After dispatch of 4 units: on_hand must be 6.000, reserved must be 0.000
    assert bal.on_hand == Decimal("6.000")
    assert bal.reserved == Decimal("0.000")

    # 1. Attack: Empty return reason -> REJECTED
    r_empty_reason = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "   "},
        headers=_auth_header(staff),
    )
    assert r_empty_reason.status_code == 400
    assert r_empty_reason.json()["code"] == "INVALID_RETURN_REASON"

    # 2. Legitimate return
    r_ret = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "Broken container during shipment, return to quarantine"},
        headers=_auth_header(staff),
    )
    assert r_ret.status_code == 200
    assert r_ret.json()["status"] == "returned"

    # EMPIRICAL INVARIANT B7 VERIFICATION:
    # on_hand balance MUST REMAIN 6.000 (NOT incremented to 10.000!)
    db.refresh(bal)
    assert bal.on_hand == Decimal(
        "6.000"
    ), f"Invariant B7 violated! Returned stock leaked into sellable on_hand: {bal.on_hand}"
    assert bal.reserved == Decimal("0.000")

    # No 'receive' or restock movements logged
    moves = db.scalars(
        select(StockMovement).where(
            StockMovement.warehouse_id == empirical_warehouse.id,
            StockMovement.movement_type.in_(["receive", "adjustment"]),
        )
    ).all()
    assert len(moves) == 0

    # 3. Attack: Double return on already returned order -> REJECTED
    r_ret_again = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "Repeated return attempt"},
        headers=_auth_header(staff),
    )
    assert r_ret_again.status_code == 400
    assert r_ret_again.json()["code"] == "ILLEGAL_STATE_TRANSITION"


# ── Scenario 6: RBAC Isolation & Ownership Hijack Attacks ────────────────────


def test_adversarial_rbac_privilege_escalation_and_isolation(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Customer cannot perform staff fulfillment or hijack orders.

    Tests:
    - Customer calling staff endpoints (/reserve, /pack, /dispatch, etc.) -> 403.
    - Customer A cannot cancel Customer B's order -> 403.
    - Customer cannot cancel once order is packed -> 403.
    - Unauthenticated request -> 401.
    """
    cust_a = _make_user(db, email="adv_cust_a@test.com", role_name="consumer")
    cust_b = _make_user(db, email="adv_cust_b@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_rbac_staff@test.com", role_name="warehouse")
    var_1 = empirical_catalog["var_1"]

    _b, _bal = _seed_batch_balance(db, empirical_warehouse, var_1, "ADV-RBAC", 30, on_hand=10)

    # Customer A creates order
    r_ord = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "2.000"}],
            "idempotency_key": f"adv-rbac-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(cust_a),
    )
    order_id = r_ord.json()["id"]

    # 1. Customer A tries to call fulfillment endpoints on their own order -> 403 FORBIDDEN
    for ep, payload in [
        ("/reserve", None),
        ("/pack", None),
        ("/dispatch", None),
        ("/deliver", None),
        ("/return", {"reason": "Customer return attempt"}),
    ]:
        resp = client.post(
            f"/api/v1/orders/{order_id}{ep}", json=payload, headers=_auth_header(cust_a)
        )
        assert resp.status_code == 403, f"Expected 403 for customer on {ep}, got {resp.status_code}"

    # Customer A tries to call status patch -> 403
    resp_patch = client.patch(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "delivered"},
        headers=_auth_header(cust_a),
    )
    assert resp_patch.status_code == 403

    # 2. Customer B attempts to cancel Customer A's order -> 403 FORBIDDEN
    resp_hijack = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Customer B hijack cancel"},
        headers=_auth_header(cust_b),
    )
    assert resp_hijack.status_code == 403
    assert resp_hijack.json()["code"] == "FORBIDDEN"

    # 3. Staff reserves and packs order
    assert (
        client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff)).status_code
        == 200
    )

    # 4. Customer A attempts to cancel once packed -> 403 FORBIDDEN
    resp_pack_cancel = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Customer cancelling packed order"},
        headers=_auth_header(cust_a),
    )
    assert resp_pack_cancel.status_code == 403
    assert resp_pack_cancel.json()["code"] == "FORBIDDEN"

    # 5. Unauthenticated caller -> 401 UNAUTHORIZED
    for ep in ["/reserve", "/pack", "/dispatch", "/cancel", "/return"]:
        r_unauth = client.post(f"/api/v1/orders/{order_id}{ep}")
        assert r_unauth.status_code == 401


# ── Scenario 7: Unreserved Dispatch Edge Attack ──────────────────────────────


def test_adversarial_dispatch_unreserved_order_failure(
    client: TestClient, db: Session, empirical_catalog, empirical_warehouse
):
    """Adversarial stress-test: Calling dispatch when no active reservations exist.

    If an order was somehow manually transitioned to 'packed' without reservations,
    dispatch_order must reject with 400 NO_ACTIVE_RESERVATIONS.
    """
    consumer = _make_user(db, email="adv_unres_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="adv_unres_staff@test.com", role_name="warehouse")
    var_1 = empirical_catalog["var_1"]

    resp = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_1.id), "quantity": "1.000"}],
            "idempotency_key": f"adv-unres-disp-{uuid.uuid4().hex[:6]}",
        },
        headers=_auth_header(consumer),
    )
    order_id = resp.json()["id"]

    # Manually force order status to 'packed' without creating reservations
    order_obj = db.scalar(select(Order).where(Order.id == uuid.UUID(order_id)))
    order_obj.status = "packed"
    db.commit()

    # Staff calls /dispatch
    r_disp = client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff))
    assert r_disp.status_code == 400
    assert r_disp.json()["code"] == "NO_ACTIVE_RESERVATIONS"
