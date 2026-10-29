"""Automated Test Suite for Milestone M2: Order Fulfillment & Inventory Lifecycle.

Tests:
1. Invariants B1-B7:
   - Invariant B1: Cannot reserve or dispatch unreleased batches -> BATCH_NOT_RELEASED.
   - Invariant B2 & B3: FEFO chooses earliest-expiring released batch first; splits.
   - Invariant B4: Insufficient stock rolls back transaction atomically; balances untouched.
   - Invariant B5: Dispatch decrements on_hand & reserved, writes StockMovement(dispatch).
   - Invariant B6: Cancellation releases reservation via atomic CAS exactly once.
   - Invariant B7: Return marks 'returned'; does NOT add stock to on_hand / sellable.
2. State Transition Lifecycle:
   - Full happy path: pending -> reserved -> packed -> dispatched -> delivered -> returned.
   - Cancellations from pending, reserved, and packed (with active reservations released).
   - Forbidden transitions: cancel dispatched, cancel delivered, return packed, pack pending.
3. RBAC Matrix Tests:
   - Customer & dealer cannot call /reserve, /pack, /dispatch, /return (HTTP 403).
   - Customer can cancel own pending/reserved order, but not other's order (HTTP 403).
   - Customer cannot cancel packed order (HTTP 403 / 400).
   - Warehouse & owner staff can fulfill and cancel orders.
   - Unauthenticated requests rejected (HTTP 401).
4. Multi-Item Order Atomic Rollback:
   - If one variant in multi-line order has insufficient stock, entire order rolls back.
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
from app.inventory.models import (
    InventoryBalance,
    ProductionBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)
from app.orders.models import Order, OrderItem
from tests.conftest import _make_user

# ── Fixtures & Test Data Helpers ──────────────────────────────────────────────


@pytest.fixture()
def fulfillment_catalog(db: Session) -> dict[str, Any]:
    """Seed test category, product, variants, and role price lists."""
    cat = Category(name="M2 Fulfillment Spices")
    db.add(cat)
    db.flush()

    prod = Product(name="M2 Special Garam Masala", category_id=cat.id)
    db.add(prod)
    db.flush()

    var_a = ProductVariant(product_id=prod.id, sku="M2-SGM-250G", pack_size_g=250)
    var_b = ProductVariant(product_id=prod.id, sku="M2-SGM-500G", pack_size_g=500)
    db.add_all([var_a, var_b])
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    dealer_pl = db.query(PriceList).filter_by(name="DEALER").first()
    dist_pl = db.query(PriceList).filter_by(name="DISTRIBUTOR").first()

    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_a.id, price=Decimal("100.00")))
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_b.id, price=Decimal("190.00")))

    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=var_a.id, price=Decimal("80.00")))
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=var_b.id, price=Decimal("150.00")))

    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_a.id, price=Decimal("70.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_b.id, price=Decimal("130.00")))
    db.commit()

    return {
        "category": cat,
        "product": prod,
        "var_a": var_a,
        "var_b": var_b,
        "mrp_pl": mrp_pl,
        "dealer_pl": dealer_pl,
        "dist_pl": dist_pl,
    }


@pytest.fixture()
def warehouse(db: Session) -> Warehouse:
    """Provide a primary test warehouse."""
    wh = Warehouse(
        code=f"WH-MH-{uuid.uuid4().hex[:6]}",
        name="Maharashtra Central Depot",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.commit()
    return wh


@pytest.fixture()
def secondary_warehouse(db: Session) -> Warehouse:
    """Provide an auxiliary test warehouse."""
    wh = Warehouse(
        code=f"WH-PUNE-{uuid.uuid4().hex[:6]}",
        name="Pune Regional Hub",
        is_primary=False,
        is_active=True,
    )
    db.add(wh)
    db.commit()
    return wh


def _create_batch_and_balance(
    db: Session,
    warehouse: Warehouse,
    variant: ProductVariant,
    batch_code: str,
    expiry_days_from_now: int,
    on_hand: int | Decimal,
    reserved: int | Decimal = 0,
    status: str = "released",
) -> tuple[ProductionBatch, InventoryBalance]:
    """Helper to create a production batch and its inventory balance."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"{batch_code}-{uuid.uuid4().hex[:4]}",
        manufactured_at=today - timedelta(days=10),
        expiry_date=today + timedelta(days=expiry_days_from_now),
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


def _auth_header(user: User) -> dict[str, str]:
    """Generate Bearer authorization header for a test user."""
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


def _create_order_via_api(
    client: TestClient,
    user: User,
    variant_id: uuid.UUID,
    quantity: str = "5.000",
) -> dict[str, Any]:
    """Helper to place an order via POST /api/v1/orders."""
    payload = {
        "items": [{"variant_id": str(variant_id), "quantity": quantity}],
        "idempotency_key": f"m2-test-{uuid.uuid4().hex[:8]}",
    }
    resp = client.post("/api/v1/orders", json=payload, headers=_auth_header(user))
    assert resp.status_code == 201, f"Failed to create order: {resp.text}"
    return resp.json()


def _deliver_order(client: TestClient, db: Session, order_id: str, staff: User) -> None:
    """Helper to mark order delivered (via API if available or via direct service update)."""
    resp = client.patch(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "delivered"},
        headers=_auth_header(staff),
    )
    if resp.status_code == 200:
        return
    from app.orders.service import update_order_status
    update_order_status(db, uuid.UUID(order_id), "delivered", actor_id=staff.id)


# ── Group 1: Business Invariant Tests (B1 to B7) ─────────────────────────────


def test_invariant_b1_cannot_reserve_or_dispatch_unreleased_batch(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """B1: A batch that is not Released can never be reserved or dispatched."""
    consumer = _make_user(db, email="b1_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="b1_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    # 1. Batches with status 'hold' and 'rejected'
    _create_batch_and_balance(db, warehouse, var_a, "B1-HOLD", 30, on_hand=10, status="hold")
    _create_batch_and_balance(db, warehouse, var_a, "B1-REJ", 40, on_hand=10, status="rejected")

    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="2.000")
    order_id = order_data["id"]

    # Staff attempts to reserve unreleased stock
    resp = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert resp.status_code == 400
    err = resp.json()
    assert err["code"] in ("INSUFFICIENT_STOCK", "BATCH_NOT_RELEASED")

    # Order remains pending with 0 reservations
    order_in_db = db.scalar(select(Order).where(Order.id == uuid.UUID(order_id)))
    assert order_in_db.status == "pending"

    reservations = db.scalars(
        select(StockReservation).join(OrderItem).where(OrderItem.order_id == uuid.UUID(order_id))
    ).all()
    assert len(reservations) == 0

    # 2. Part B: Batch released during reservation, but held before dispatch
    batch_rel, balance_rel = _create_batch_and_balance(
        db, warehouse, var_a, "B1-VALID", 50, on_hand=10, status="released"
    )
    order_b_data = _create_order_via_api(client, consumer, var_a.id, quantity="2.000")
    order_b_id = order_b_data["id"]

    # Reserve succeeds
    resp_res = client.post(f"/api/v1/orders/{order_b_id}/reserve", headers=_auth_header(staff))
    assert resp_res.status_code == 200
    assert resp_res.json()["status"] == "reserved"

    # Pack succeeds
    resp_pack = client.post(f"/api/v1/orders/{order_b_id}/pack", headers=_auth_header(staff))
    assert resp_pack.status_code == 200
    assert resp_pack.json()["status"] == "packed"

    # Quality hold placed on batch after packing
    batch_rel.status = "hold"
    db.commit()

    # Staff attempts dispatch -> MUST FAIL with BATCH_NOT_RELEASED (Invariant B1)
    resp_disp = client.post(f"/api/v1/orders/{order_b_id}/dispatch", headers=_auth_header(staff))
    assert resp_disp.status_code == 400
    assert resp_disp.json()["code"] == "BATCH_NOT_RELEASED"

    # Verify balances were NOT decremented
    db.refresh(balance_rel)
    assert balance_rel.on_hand == Decimal("10.000")
    assert balance_rel.reserved == Decimal("2.000")


def test_invariants_b2_and_b3_fefo_allocation_and_multi_batch_split(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """B2 & B3: First-Expired, First-Out (FEFO) allocation and multi-batch splitting."""
    consumer = _make_user(db, email="b2b3_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="b2b3_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    # Expired batch: expiry -5 days, on_hand=10 (MUST BE EXCLUDED)
    _create_batch_and_balance(db, warehouse, var_a, "FEFO-EXP", -5, on_hand=10, status="released")
    # Held batch: expiry +5 days, on_hand=10 (MUST BE EXCLUDED)
    _create_batch_and_balance(db, warehouse, var_a, "FEFO-HELD", 5, on_hand=10, status="hold")
    # Batch R1: expiry +10 days, on_hand=5 (EARLIEST ELIGIBLE)
    _, bal_r1 = _create_batch_and_balance(
        db, warehouse, var_a, "FEFO-R1", 10, on_hand=5, status="released"
    )
    # Batch R2: expiry +20 days, on_hand=10 (SECOND ELIGIBLE)
    _, bal_r2 = _create_batch_and_balance(
        db, warehouse, var_a, "FEFO-R2", 20, on_hand=10, status="released"
    )
    # Batch R3: expiry +30 days, on_hand=10 (THIRD ELIGIBLE - UNTOUCHED)
    _, bal_r3 = _create_batch_and_balance(
        db, warehouse, var_a, "FEFO-R3", 30, on_hand=10, status="released"
    )

    # Order requests 8 units: needs 5 from R1 and 3 from R2
    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="8.000")
    order_id = order_data["id"]

    resp = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert resp.status_code == 200
    assert resp.json()["status"] == "reserved"

    # Verify reservation split in database
    reservations = db.scalars(
        select(StockReservation)
        .join(OrderItem)
        .where(OrderItem.order_id == uuid.UUID(order_id))
        .order_by(StockReservation.created_at.asc())
    ).all()
    assert len(reservations) == 2

    # Reservation 1: from Batch R1 (5.000 units)
    assert reservations[0].balance_id == bal_r1.id
    assert reservations[0].quantity == Decimal("5.000")
    assert reservations[0].status == "active"

    # Reservation 2: from Batch R2 (3.000 units)
    assert reservations[1].balance_id == bal_r2.id
    assert reservations[1].quantity == Decimal("3.000")
    assert reservations[1].status == "active"

    # Verify inventory balances
    db.refresh(bal_r1)
    db.refresh(bal_r2)
    db.refresh(bal_r3)
    assert bal_r1.reserved == Decimal("5.000")
    assert bal_r1.available == Decimal("0.000")

    assert bal_r2.reserved == Decimal("3.000")
    assert bal_r2.available == Decimal("7.000")

    assert bal_r3.reserved == Decimal("0.000")
    assert bal_r3.available == Decimal("10.000")


def test_invariant_b4_insufficient_stock_rollback(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """B4: Stock never goes negative; insufficient stock rolls back transaction completely."""
    consumer = _make_user(db, email="b4_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="b4_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    # Only 6 units available in warehouse
    _, balance = _create_batch_and_balance(
        db, warehouse, var_a, "B4-STOCK", 30, on_hand=6, reserved=0, status="released"
    )

    # Customer orders 10 units
    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="10.000")
    order_id = order_data["id"]

    resp = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert resp.status_code == 400
    assert resp.json()["code"] == "INSUFFICIENT_STOCK"

    # Verify complete rollback: order status remains pending, reserved remains 0
    order_in_db = db.scalar(select(Order).where(Order.id == uuid.UUID(order_id)))
    assert order_in_db.status == "pending"

    db.refresh(balance)
    assert balance.reserved == Decimal("0.000")
    assert balance.on_hand == Decimal("6.000")

    # Zero reservations and zero ledger movements created
    res_count = db.scalar(
        select(StockReservation)
        .join(OrderItem)
        .where(OrderItem.order_id == uuid.UUID(order_id))
    )
    assert res_count is None


def test_invariant_b5_dispatch_decrements_balances_and_records_movements(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """B5: Dispatch decrements on_hand & reserved, marks consumed, logs movement."""
    consumer = _make_user(db, email="b5_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="b5_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    batch, balance = _create_batch_and_balance(
        db, warehouse, var_a, "B5-DISP", 30, on_hand=10, reserved=0, status="released"
    )

    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="4.000")
    order_id = order_data["id"]

    # Reserve -> Pack -> Dispatch
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))

    disp_payload = {"carrier": "SpiceExpress", "tracking_ref": "SE-123456"}
    resp = client.post(
        f"/api/v1/orders/{order_id}/dispatch",
        json=disp_payload,
        headers=_auth_header(staff),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "dispatched"

    # Check inventory balance: on_hand and reserved both decremented by 4
    db.refresh(balance)
    assert balance.on_hand == Decimal("6.000")
    assert balance.reserved == Decimal("0.000")
    assert balance.available == Decimal("6.000")

    # Check reservation status: consumed
    reservation = db.scalar(
        select(StockReservation)
        .join(OrderItem)
        .where(OrderItem.order_id == uuid.UUID(order_id))
    )
    assert reservation.status == "consumed"

    # Check stock movement ledger entry (Invariant B5)
    movement = db.scalar(
        select(StockMovement).where(
            StockMovement.reference_type == "stock_reservation",
            StockMovement.reference_id == str(reservation.id),
            StockMovement.movement_type == "dispatch",
        )
    )
    assert movement is not None
    assert movement.quantity == Decimal("4.000")
    assert movement.warehouse_id == warehouse.id
    assert movement.batch_id == batch.id
    assert movement.actor_id == staff.id
    assert movement.operation_key == f"dispatch:{reservation.id}"


def test_invariant_b6_cancellation_releases_reservations_cas_exactly_once(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """B6: Cancelling an order releases its reservation exactly once via atomic CAS."""
    consumer = _make_user(db, email="b6_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="b6_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _, balance = _create_batch_and_balance(
        db, warehouse, var_a, "B6-CANCEL", 30, on_hand=10, reserved=0, status="released"
    )

    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="4.000")
    order_id = order_data["id"]

    # Reserve 4 units
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    db.refresh(balance)
    assert balance.reserved == Decimal("4.000")

    # First cancel call
    cancel_payload = {"reason": "Customer cancellation request"}
    resp = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json=cancel_payload,
        headers=_auth_header(consumer),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Reserved stock released back to available
    db.refresh(balance)
    assert balance.on_hand == Decimal("10.000")
    assert balance.reserved == Decimal("0.000")
    assert balance.available == Decimal("10.000")

    # Reservation status updated to released
    reservation = db.scalar(
        select(StockReservation)
        .join(OrderItem)
        .where(OrderItem.order_id == uuid.UUID(order_id))
    )
    assert reservation.status == "released"

    # Exactly one release movement in ledger
    release_movements = db.scalars(
        select(StockMovement).where(
            StockMovement.reference_type == "stock_reservation",
            StockMovement.reference_id == str(reservation.id),
            StockMovement.movement_type == "release",
        )
    ).all()
    assert len(release_movements) == 1

    # Second cancel call (Repeat cancellation)
    resp2 = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json=cancel_payload,
        headers=_auth_header(consumer),
    )
    assert resp2.status_code in (400, 409)

    # CRITICAL: balance.reserved must NOT go below 0 (to -4.000)
    db.refresh(balance)
    assert balance.reserved == Decimal("0.000")

    # Ledger movements must NOT have duplicated
    movements_after = db.scalars(
        select(StockMovement).where(
            StockMovement.reference_type == "stock_reservation",
            StockMovement.reference_id == str(reservation.id),
            StockMovement.movement_type == "release",
        )
    ).all()
    assert len(movements_after) == 1


def test_invariant_b7_return_quarantines_and_does_not_increment_sellable_stock(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """B7: Returned goods are not sellable until a warehouse/quality decision."""
    consumer = _make_user(db, email="b7_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="b7_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _, balance = _create_batch_and_balance(
        db, warehouse, var_a, "B7-RETURN", 30, on_hand=10, reserved=0, status="released"
    )

    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="3.000")
    order_id = order_data["id"]

    # Fulfill order to dispatched
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))
    client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff))

    db.refresh(balance)
    assert balance.on_hand == Decimal("7.000")
    assert balance.reserved == Decimal("0.000")

    # Mark order delivered
    _deliver_order(client, db, order_id, staff)

    # Return order
    return_payload = {"reason": "Seal broken during transit; quarantine for QA testing"}
    resp = client.post(
        f"/api/v1/orders/{order_id}/return",
        json=return_payload,
        headers=_auth_header(staff),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "returned"

    # INVARIANT B7 ASSERTION: on_hand MUST REMAIN 7.000 (NOT 10.000!)
    db.refresh(balance)
    assert balance.on_hand == Decimal("7.000")
    assert balance.reserved == Decimal("0.000")
    assert balance.available == Decimal("7.000")

    # Verify no unexpected inventory movements added sellable stock
    inward_movements = db.scalars(
        select(StockMovement).where(
            StockMovement.warehouse_id == warehouse.id,
            StockMovement.movement_type.in_(["receive", "adjustment_in", "return"]),
        )
    ).all()
    assert len(inward_movements) == 0


# ── Group 2: State Transition Lifecycle Tests ────────────────────────────────


def test_order_full_happy_path_lifecycle(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Test full sequential lifecycle: create -> reserve -> pack -> dispatch -> delivered."""
    consumer = _make_user(db, email="hp_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="hp_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _create_batch_and_balance(db, warehouse, var_a, "HP-BATCH", 30, on_hand=10, status="released")

    # 1. Create -> pending
    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="2.000")
    order_id = order_data["id"]
    assert order_data["status"] == "pending"

    # 2. Reserve -> reserved
    resp_res = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert resp_res.status_code == 200
    assert resp_res.json()["status"] == "reserved"

    # 3. Pack -> packed
    resp_pack = client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))
    assert resp_pack.status_code == 200
    assert resp_pack.json()["status"] == "packed"

    # 4. Dispatch -> dispatched
    resp_disp = client.post(
        f"/api/v1/orders/{order_id}/dispatch",
        json={"carrier": "DHL", "tracking_ref": "DHL-999"},
        headers=_auth_header(staff),
    )
    assert resp_disp.status_code == 200
    assert resp_disp.json()["status"] == "dispatched"

    # 5. Deliver -> delivered
    _deliver_order(client, db, order_id, staff)
    order_in_db = db.scalar(select(Order).where(Order.id == uuid.UUID(order_id)))
    assert order_in_db.status == "delivered"

    # 6. Return -> returned
    resp_ret = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "Customer return"},
        headers=_auth_header(staff),
    )
    assert resp_ret.status_code == 200
    assert resp_ret.json()["status"] == "returned"


def test_cancellation_from_various_lifecycle_states(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Test cancellations from pending, reserved, and packed states."""
    consumer = _make_user(db, email="cancel_states_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="cancel_states_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _, balance = _create_batch_and_balance(
        db, warehouse, var_a, "CS-BATCH", 30, on_hand=20, status="released"
    )

    # Case A: Cancel from 'pending'
    ord_pending = _create_order_via_api(client, consumer, var_a.id, quantity="2.000")
    resp_c1 = client.post(
        f"/api/v1/orders/{ord_pending['id']}/cancel",
        json={"reason": "Pending cancel"},
        headers=_auth_header(consumer),
    )
    assert resp_c1.status_code == 200
    assert resp_c1.json()["status"] == "cancelled"

    # Case B: Cancel from 'reserved'
    ord_reserved = _create_order_via_api(client, consumer, var_a.id, quantity="3.000")
    client.post(f"/api/v1/orders/{ord_reserved['id']}/reserve", headers=_auth_header(staff))
    resp_c2 = client.post(
        f"/api/v1/orders/{ord_reserved['id']}/cancel",
        json={"reason": "Reserved cancel"},
        headers=_auth_header(consumer),
    )
    assert resp_c2.status_code == 200
    assert resp_c2.json()["status"] == "cancelled"

    # Case C: Cancel from 'packed' (authorized staff cancel)
    ord_packed = _create_order_via_api(client, consumer, var_a.id, quantity="4.000")
    client.post(f"/api/v1/orders/{ord_packed['id']}/reserve", headers=_auth_header(staff))
    client.post(f"/api/v1/orders/{ord_packed['id']}/pack", headers=_auth_header(staff))
    resp_c3 = client.post(
        f"/api/v1/orders/{ord_packed['id']}/cancel",
        json={"reason": "Warehouse damaged box during staging"},
        headers=_auth_header(staff),
    )
    assert resp_c3.status_code == 200
    assert resp_c3.json()["status"] == "cancelled"

    # Check that balance.reserved is 0 after all 3 cancellations
    db.refresh(balance)
    assert balance.reserved == Decimal("0.000")


def test_forbidden_state_transitions(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Test that illegal transitions are strictly rejected with HTTP 400."""
    consumer = _make_user(db, email="forbid_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="forbid_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _create_batch_and_balance(
        db, warehouse, var_a, "FORBID-BATCH", 30, on_hand=10, status="released"
    )

    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="2.000")
    order_id = order_data["id"]

    # 1. Pack from pending -> 400
    resp = client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 2. Dispatch from pending -> 400
    resp = client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff))
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # 3. Return from pending -> 400
    resp = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "test"},
        headers=_auth_header(staff),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Move to reserved
    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))

    # 4. Dispatch from reserved (without packing) -> 400
    resp = client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff))
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Move to packed
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))

    # 5. Return from packed -> 400
    resp = client.post(
        f"/api/v1/orders/{order_id}/return",
        json={"reason": "test"},
        headers=_auth_header(staff),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Move to dispatched
    client.post(f"/api/v1/orders/{order_id}/dispatch", headers=_auth_header(staff))

    # 6. Cancel from dispatched -> 400
    resp = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "test"},
        headers=_auth_header(staff),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Move to delivered
    _deliver_order(client, db, order_id, staff)

    # 7. Cancel from delivered -> 400 (Delivered orders must be returned, not cancelled)
    resp = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "test"},
        headers=_auth_header(staff),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"


# ── Group 3: RBAC Matrix Tests ───────────────────────────────────────────────


def test_rbac_customer_and_dealer_cannot_access_fulfillment_endpoints(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Verify customer & dealer roles cannot call /reserve, /pack, /dispatch, /return (HTTP 403)."""
    consumer = _make_user(db, email="rbac_consumer@test.com", role_name="consumer")
    dealer = _make_user(db, email="rbac_dealer@test.com", role_name="dealer")
    _make_user(db, email="rbac_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _create_batch_and_balance(
        db, warehouse, var_a, "RBAC-BATCH", 30, on_hand=10, status="released"
    )
    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="1.000")
    order_id = order_data["id"]

    for actor in [consumer, dealer]:
        h = _auth_header(actor)

        # /reserve -> 403
        r1 = client.post(f"/api/v1/orders/{order_id}/reserve", headers=h)
        assert r1.status_code == 403
        assert r1.json()["code"] == "FORBIDDEN"

        # /pack -> 403
        r2 = client.post(f"/api/v1/orders/{order_id}/pack", headers=h)
        assert r2.status_code == 403
        assert r2.json()["code"] == "FORBIDDEN"

        # /dispatch -> 403
        r3 = client.post(f"/api/v1/orders/{order_id}/dispatch", headers=h)
        assert r3.status_code == 403
        assert r3.json()["code"] == "FORBIDDEN"

        # /return -> 403
        r4 = client.post(
            f"/api/v1/orders/{order_id}/return", json={"reason": "bad"}, headers=h
        )
        assert r4.status_code == 403
        assert r4.json()["code"] == "FORBIDDEN"


def test_rbac_customer_can_cancel_own_order_but_not_others(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Customer can cancel own order, but cannot cancel another customer's order."""
    cust_a = _make_user(db, email="owner_a@test.com", role_name="consumer")
    cust_b = _make_user(db, email="owner_b@test.com", role_name="consumer")
    var_a = fulfillment_catalog["var_a"]

    _create_batch_and_balance(db, warehouse, var_a, "OWN-BATCH", 30, on_hand=10, status="released")

    ord_a = _create_order_via_api(client, cust_a, var_a.id, quantity="1.000")

    # Customer B attempts to cancel Customer A's order -> 403
    resp_unauth = client.post(
        f"/api/v1/orders/{ord_a['id']}/cancel",
        json={"reason": "Sneaky cancel"},
        headers=_auth_header(cust_b),
    )
    assert resp_unauth.status_code == 403
    assert resp_unauth.json()["code"] == "FORBIDDEN"

    # Customer A cancels own order -> 200
    resp_auth = client.post(
        f"/api/v1/orders/{ord_a['id']}/cancel",
        json={"reason": "Legitimate cancel"},
        headers=_auth_header(cust_a),
    )
    assert resp_auth.status_code == 200
    assert resp_auth.json()["status"] == "cancelled"


def test_rbac_customer_cannot_cancel_packed_order(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Customer cancellation window closes when order reaches 'packed' status."""
    consumer = _make_user(db, email="pack_cancel_cust@test.com", role_name="consumer")
    staff = _make_user(db, email="pack_cancel_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]

    _create_batch_and_balance(db, warehouse, var_a, "PC-BATCH", 30, on_hand=10, status="released")
    order_data = _create_order_via_api(client, consumer, var_a.id, quantity="2.000")
    order_id = order_data["id"]

    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(staff))

    # Customer attempts cancel on packed order -> 403 Forbidden
    resp = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Customer changed mind too late"},
        headers=_auth_header(consumer),
    )
    assert resp.status_code in (400, 403)

    # Warehouse staff CAN cancel packed order
    resp_staff = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Warehouse damaged box"},
        headers=_auth_header(staff),
    )
    assert resp_staff.status_code == 200
    assert resp_staff.json()["status"] == "cancelled"


def test_rbac_staff_can_fulfill_and_cancel_any_order(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Warehouse and owner staff have full authorization across fulfillment and cancellation."""
    dealer = _make_user(db, email="staff_test_dealer@test.com", role_name="dealer")
    wh_staff = _make_user(db, email="staff_wh@test.com", role_name="warehouse")
    owner_staff = _make_user(db, email="staff_owner@test.com", role_name="owner")
    var_a = fulfillment_catalog["var_a"]

    _create_batch_and_balance(
        db, warehouse, var_a, "STAFF-BATCH", 30, on_hand=10, status="released"
    )

    # Order 1 fulfilled by warehouse staff
    ord1 = _create_order_via_api(client, dealer, var_a.id, quantity="1.000")
    r1 = client.post(f"/api/v1/orders/{ord1['id']}/reserve", headers=_auth_header(wh_staff))
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/orders/{ord1['id']}/pack", headers=_auth_header(wh_staff))
    assert r2.status_code == 200
    r3 = client.post(f"/api/v1/orders/{ord1['id']}/dispatch", headers=_auth_header(wh_staff))
    assert r3.status_code == 200

    # Order 2 cancelled by owner staff
    ord2 = _create_order_via_api(client, dealer, var_a.id, quantity="1.000")
    r4 = client.post(f"/api/v1/orders/{ord2['id']}/reserve", headers=_auth_header(owner_staff))
    assert r4.status_code == 200
    assert client.post(
        f"/api/v1/orders/{ord2['id']}/cancel",
        json={"reason": "Owner cancelled"},
        headers=_auth_header(owner_staff),
    ).status_code == 200


def test_rbac_unauthenticated_requests_rejected(client: TestClient, fulfillment_catalog):
    """All fulfillment endpoints reject unauthenticated requests with HTTP 401."""
    fake_id = uuid.uuid4()
    for endpoint in ["reserve", "pack", "dispatch", "cancel", "return"]:
        resp = client.post(f"/api/v1/orders/{fake_id}/{endpoint}")
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHORIZED"


# ── Group 4: Multi-Item Order Atomic Rollback ─────────────────────────────────


def test_multi_item_order_atomic_reservation_rollback(
    client: TestClient, db: Session, fulfillment_catalog, warehouse
):
    """Multi-item order: if one variant has insufficient stock, entire order rolls back."""
    consumer = _make_user(db, email="multi_consumer@test.com", role_name="consumer")
    staff = _make_user(db, email="multi_staff@test.com", role_name="warehouse")
    var_a = fulfillment_catalog["var_a"]
    var_b = fulfillment_catalog["var_b"]

    # var_a has ample stock (10 units)
    _, bal_a = _create_batch_and_balance(
        db, warehouse, var_a, "MULTI-A", 30, on_hand=10, reserved=0, status="released"
    )
    # var_b has only 1 unit (insufficient for 3 requested)
    _, bal_b = _create_batch_and_balance(
        db, warehouse, var_b, "MULTI-B", 30, on_hand=1, reserved=0, status="released"
    )

    payload = {
        "items": [
            {"variant_id": str(var_a.id), "quantity": "5.000"},
            {"variant_id": str(var_b.id), "quantity": "3.000"},
        ],
        "idempotency_key": f"multi-order-{uuid.uuid4().hex[:6]}",
    }
    resp = client.post("/api/v1/orders", json=payload, headers=_auth_header(consumer))
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    # Staff attempts to reserve order
    resp_res = client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(staff))
    assert resp_res.status_code == 400
    assert resp_res.json()["code"] == "INSUFFICIENT_STOCK"

    # Verify atomic rollback: neither var_a nor var_b has reserved stock
    db.refresh(bal_a)
    db.refresh(bal_b)
    assert bal_a.reserved == Decimal("0.000")
    assert bal_b.reserved == Decimal("0.000")

    order_in_db = db.scalar(select(Order).where(Order.id == uuid.UUID(order_id)))
    assert order_in_db.status == "pending"
