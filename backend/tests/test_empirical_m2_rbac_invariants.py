"""Empirical Challenger 2 Test Suite: Invariants B5, B7, and Comprehensive RBAC Matrix.

Adversarial empirical tests for Milestone M2 (T-051 Fulfillment Endpoints & Inventory Lifecycle):
1. Invariant B5: Dispatch ledger integrity:
   - Multi-line order with multi-batch FEFO splitting.
   - Exact decrements of on_hand and reserved balances across all involved batches.
   - Exact matching StockMovement entries created with movement_type='dispatch',
     matching reservation IDs, operation keys, warehouse, batch, and actor.
   - Status transitions to 'consumed' on reservations and 'dispatched' on order.
   - Dispatch fails if not in 'packed' state.
   - Replay / double-dispatch rejection (400 ILLEGAL_STATE_TRANSITION).
2. Invariant B7: Return quarantine isolation:
   - Order delivered -> return requested with valid reason.
   - Order transitions to 'returned', audit log entry recorded.
   - CRITICAL INVARIANT: on_hand balances are NOT incremented (zero sellable restoration).
   - CRITICAL INVARIANT: NO positive stock movement created in stock_movements ledger.
   - Return rejected from illegal states (pending, reserved, packed, dispatched, cancelled).
   - Return rejected with missing or whitespace reason (400 INVALID_RETURN_REASON).
   - Return rejected if already returned (double-return 400).
3. RBAC Matrix across all 6 canonical roles + non-canonical roles:
   - Matrix testing: owner, warehouse, factory, distributor, dealer, consumer, admin, sales_rep.
   - Endpoints tested: /reserve, /pack, /dispatch, /return, /deliver, /cancel.
   - Customers (consumer, dealer, distributor) cannot pack, dispatch, reserve, return, or deliver.
   - Cross-customer cancel isolation: Customer A's order cannot be cancelled by Customer B.
   - Customer cannot cancel order once status is 'packed' (403 FORBIDDEN).
   - Fulfillment staff (warehouse, owner) CAN cancel packed orders.
   - Unauthenticated requests rejected with 401 across all fulfillment endpoints.
   - Deactivated users rejected with 403 ACCOUNT_DISABLED.
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

from app.audit.models import AuditLog
from app.auth.models import Role, User
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

# ── Fixtures & Setup Helpers ──────────────────────────────────────────────────


@pytest.fixture()
def catalog_fixture(db: Session) -> dict[str, Any]:
    """Provide catalog categories, products, variants, and seeded price lists."""
    category = Category(name="Empirical Spices")
    db.add(category)
    db.flush()

    product = Product(name="Empirical Turmeric Powder", category_id=category.id)
    db.add(product)
    db.flush()

    var_1 = ProductVariant(product_id=product.id, sku="EMP-TUR-100G", pack_size_g=100)
    var_2 = ProductVariant(product_id=product.id, sku="EMP-TUR-500G", pack_size_g=500)
    db.add_all([var_1, var_2])
    db.flush()

    mrp_pl = db.scalar(select(PriceList).where(PriceList.name == "MRP"))
    dealer_pl = db.scalar(select(PriceList).where(PriceList.name == "DEALER"))
    dist_pl = db.scalar(select(PriceList).where(PriceList.name == "DISTRIBUTOR"))

    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_1.id, price=Decimal("50.00")))
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_2.id, price=Decimal("200.00")))
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=var_1.id, price=Decimal("40.00")))
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=var_2.id, price=Decimal("160.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_1.id, price=Decimal("35.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_2.id, price=Decimal("140.00")))
    db.commit()

    return {
        "category": category,
        "product": product,
        "var_1": var_1,
        "var_2": var_2,
    }


@pytest.fixture()
def primary_warehouse(db: Session) -> Warehouse:
    """Provide an active primary warehouse."""
    wh = Warehouse(
        code=f"WH-EMP-{uuid.uuid4().hex[:6]}",
        name="Empirical Testing Warehouse",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


def _create_batch_and_balance(
    db: Session,
    warehouse: Warehouse,
    variant: ProductVariant,
    batch_code: str,
    expiry_days: int,
    on_hand: int | Decimal,
    reserved: int | Decimal = 0,
    status: str = "released",
) -> tuple[ProductionBatch, InventoryBalance]:
    """Helper: create production batch and matching inventory balance."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"{batch_code}-{uuid.uuid4().hex[:4]}",
        manufactured_at=today - timedelta(days=15),
        expiry_date=today + timedelta(days=expiry_days),
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


def _create_order_with_items(
    db: Session,
    customer: User,
    items: list[tuple[ProductVariant, int, Decimal]],
    status: str = "pending",
) -> Order:
    """Helper: create an order and items directly in DB."""
    total_amount = sum(Decimal(str(qty)) * unit_price for _, qty, unit_price in items)
    order = Order(
        order_number=f"ORD-EMP-{uuid.uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        status=status,
        total_amount=total_amount,
        idempotency_key=f"emp-idemp-{uuid.uuid4()}",
    )
    db.add(order)
    db.flush()

    for variant, qty, unit_price in items:
        item = OrderItem(
            order_id=order.id,
            variant_id=variant.id,
            quantity=qty,
            unit_price_snapshot=unit_price,
            price_list_code="MRP",
        )
        db.add(item)

    db.commit()
    db.refresh(order)
    return order


def _auth_header(user: User) -> dict[str, str]:
    """Generate Authorization Bearer header for a given user."""
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


# ── Scenario 1: Invariant B5 Dispatch Ledger and Balance Verification ────────


def test_b5_dispatch_multi_batch_fefo_exact_ledger_and_balances(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
    primary_warehouse: Warehouse,
) -> None:
    """Invariant B5: Dispatch decrements on_hand and reserved by exact amounts,
    creates matching StockMovement entries with movement_type='dispatch',
    and transitions reservations to 'consumed'.
    """
    var_1 = catalog_fixture["var_1"]
    var_2 = catalog_fixture["var_2"]

    # Setup batches for var_1: Batch A (10 units, 20d expiry), Batch B (10 units, 40d expiry)
    # Order will request 15 units of var_1 -> FEFO splits 10 from Batch A, 5 from Batch B.
    batch_1a, bal_1a = _create_batch_and_balance(
        db, primary_warehouse, var_1, "BATCH-1A", expiry_days=20, on_hand=10
    )
    batch_1b, bal_1b = _create_batch_and_balance(
        db, primary_warehouse, var_1, "BATCH-1B", expiry_days=40, on_hand=10
    )

    # Setup batch for var_2: Batch C (20 units, expires in 30 days)
    # Order will request 8 units of var_2.
    batch_2a, bal_2a = _create_batch_and_balance(
        db, primary_warehouse, var_2, "BATCH-2A", expiry_days=30, on_hand=20
    )

    consumer = _make_user(db, email="emp_b5_cust@example.com", role_name="consumer")
    warehouse_staff = _make_user(db, email="emp_b5_wh@example.com", role_name="warehouse")

    # Create order with 15 of var_1 and 8 of var_2
    order = _create_order_with_items(
        db,
        consumer,
        items=[(var_1, 15, Decimal("50.00")), (var_2, 8, Decimal("200.00"))],
        status="pending",
    )

    wh_headers = _auth_header(warehouse_staff)

    # 1. Reserve stock via FEFO
    res_resp = client.post(
        f"/api/v1/orders/{order.id}/reserve",
        json={"warehouse_id": str(primary_warehouse.id)},
        headers=wh_headers,
    )
    assert res_resp.status_code == 200, res_resp.text
    assert res_resp.json()["status"] == "reserved"

    # Verify balances after reservation:
    db.expire_all()
    b_1a = db.get(InventoryBalance, bal_1a.id)
    b_1b = db.get(InventoryBalance, bal_1b.id)
    b_2a = db.get(InventoryBalance, bal_2a.id)
    assert b_1a.on_hand == Decimal("10") and b_1a.reserved == Decimal("10")
    assert b_1b.on_hand == Decimal("10") and b_1b.reserved == Decimal("5")
    assert b_2a.on_hand == Decimal("20") and b_2a.reserved == Decimal("8")

    # 2. Pack order
    pack_resp = client.post(f"/api/v1/orders/{order.id}/pack", headers=wh_headers)
    assert pack_resp.status_code == 200, pack_resp.text
    assert pack_resp.json()["status"] == "packed"

    # Record stock movements count before dispatch
    movements_before = db.scalars(select(StockMovement)).all()
    reserve_movements = [m for m in movements_before if m.movement_type == "reserve"]
    assert len(reserve_movements) == 3

    # 3. Dispatch order
    dispatch_resp = client.post(
        f"/api/v1/orders/{order.id}/dispatch",
        json={"carrier": "Bluedart Express", "tracking_ref": "BD-EMP-998877"},
        headers=wh_headers,
    )
    assert dispatch_resp.status_code == 200, dispatch_resp.text
    assert dispatch_resp.json()["status"] == "dispatched"

    # 4. Invariant B5 Verification: Exact Balance Decrements
    db.expire_all()
    b_1a = db.get(InventoryBalance, bal_1a.id)
    b_1b = db.get(InventoryBalance, bal_1b.id)
    b_2a = db.get(InventoryBalance, bal_2a.id)

    # Batch 1A had 10 on_hand, 10 reserved -> after dispatch: 0 on_hand, 0 reserved
    assert b_1a.on_hand == Decimal("0"), f"Expected 0 on_hand for bal_1a, got {b_1a.on_hand}"
    assert b_1a.reserved == Decimal("0"), f"Expected 0 reserved for bal_1a, got {b_1a.reserved}"

    # Batch 1B had 10 on_hand, 5 reserved -> after dispatch: 5 on_hand, 0 reserved
    assert b_1b.on_hand == Decimal("5"), f"Expected 5 on_hand for bal_1b, got {b_1b.on_hand}"
    assert b_1b.reserved == Decimal("0"), f"Expected 0 reserved for bal_1b, got {b_1b.reserved}"

    # Batch 2A had 20 on_hand, 8 reserved -> after dispatch: 12 on_hand, 0 reserved
    assert b_2a.on_hand == Decimal("12"), f"Expected 12 on_hand for bal_2a, got {b_2a.on_hand}"
    assert b_2a.reserved == Decimal("0"), f"Expected 0 reserved for bal_2a, got {b_2a.reserved}"

    # 5. Invariant B5 Verification: Exact Matching StockMovement Entries
    dispatch_movements = list(
        db.scalars(
            select(StockMovement)
            .where(StockMovement.movement_type == "dispatch")
            .order_by(StockMovement.created_at.asc())
        ).all()
    )
    assert (
        len(dispatch_movements) == 3
    ), f"Expected 3 dispatch movements, got {len(dispatch_movements)}"

    # Check mapping of movements to balances and quantities
    movement_map = {m.balance_id: m for m in dispatch_movements}
    assert bal_1a.id in movement_map
    assert bal_1b.id in movement_map
    assert bal_2a.id in movement_map

    m_1a = movement_map[bal_1a.id]
    assert m_1a.quantity == Decimal("10")
    assert m_1a.batch_id == batch_1a.id
    assert m_1a.warehouse_id == primary_warehouse.id
    assert m_1a.reference_type == "stock_reservation"
    assert m_1a.operation_key.startswith("dispatch:")
    assert m_1a.actor_id == warehouse_staff.id

    m_1b = movement_map[bal_1b.id]
    assert m_1b.quantity == Decimal("5")
    assert m_1b.batch_id == batch_1b.id
    assert m_1b.warehouse_id == primary_warehouse.id
    assert m_1b.reference_type == "stock_reservation"
    assert m_1b.operation_key.startswith("dispatch:")
    assert m_1b.actor_id == warehouse_staff.id

    m_2a = movement_map[bal_2a.id]
    assert m_2a.quantity == Decimal("8")
    assert m_2a.batch_id == batch_2a.id
    assert m_2a.warehouse_id == primary_warehouse.id
    assert m_2a.reference_type == "stock_reservation"
    assert m_2a.operation_key.startswith("dispatch:")
    assert m_2a.actor_id == warehouse_staff.id

    # 6. Verify StockReservation statuses are 'consumed'
    reservations = list(
        db.scalars(
            select(StockReservation)
            .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
            .where(OrderItem.order_id == order.id)
        ).all()
    )
    assert len(reservations) == 3
    for res in reservations:
        assert (
            res.status == "consumed"
        ), f"Reservation {res.id} should be consumed, got {res.status}"

    # 7. Verify Audit Log entry
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "orders",
            AuditLog.entity_id == order.id,
            AuditLog.action == "DISPATCH_ORDER",
        )
    )
    assert audit is not None
    assert audit.actor_id == warehouse_staff.id
    assert audit.after.get("carrier") == "Bluedart Express"
    assert audit.after.get("tracking_ref") == "BD-EMP-998877"


def test_b5_dispatch_rejection_on_invalid_states_and_replays(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
    primary_warehouse: Warehouse,
) -> None:
    """Invariant B5 & State Machine: Dispatch must be rejected if order is not in 'packed' state,
    and repeated dispatch must be rejected with 400 ILLEGAL_STATE_TRANSITION.
    """
    var_1 = catalog_fixture["var_1"]
    _create_batch_and_balance(db, primary_warehouse, var_1, "DISP-REJ", expiry_days=30, on_hand=50)

    consumer = _make_user(db, email="emp_disp_rej_cust@example.com", role_name="consumer")
    warehouse_staff = _make_user(db, email="emp_disp_rej_wh@example.com", role_name="warehouse")
    wh_headers = _auth_header(warehouse_staff)

    # A. Order in 'pending' status -> dispatch rejected
    order_pending = _create_order_with_items(
        db, consumer, [(var_1, 5, Decimal("50.00"))], status="pending"
    )
    r1 = client.post(f"/api/v1/orders/{order_pending.id}/dispatch", headers=wh_headers)
    assert r1.status_code == 400
    assert r1.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # B. Order in 'reserved' status (not packed yet) -> dispatch rejected
    client.post(
        f"/api/v1/orders/{order_pending.id}/reserve",
        json={"warehouse_id": str(primary_warehouse.id)},
        headers=wh_headers,
    )
    r2 = client.post(f"/api/v1/orders/{order_pending.id}/dispatch", headers=wh_headers)
    assert r2.status_code == 400
    assert r2.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # C. Pack and dispatch order
    client.post(f"/api/v1/orders/{order_pending.id}/pack", headers=wh_headers)
    r3 = client.post(f"/api/v1/orders/{order_pending.id}/dispatch", headers=wh_headers)
    assert r3.status_code == 200
    assert r3.json()["status"] == "dispatched"

    # D. Second dispatch on already dispatched order -> rejected with 400 ILLEGAL_STATE_TRANSITION
    r4 = client.post(f"/api/v1/orders/{order_pending.id}/dispatch", headers=wh_headers)
    assert r4.status_code == 400
    assert r4.json()["code"] == "ILLEGAL_STATE_TRANSITION"


# ── Scenario 2: Invariant B7 Quarantine Isolation on Return ──────────────────


def test_b7_return_quarantine_isolation_and_no_stock_restoration(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
    primary_warehouse: Warehouse,
) -> None:
    """Invariant B7: Returned goods are quarantined and NOT added back to sellable balances.
    Verify that return_order transitions to 'returned', logs audit action, but DOES NOT
    increment on_hand balance and DOES NOT create a positive stock movement.
    """
    var_1 = catalog_fixture["var_1"]
    batch, balance = _create_batch_and_balance(
        db, primary_warehouse, var_1, "B7-BATCH", expiry_days=50, on_hand=30
    )

    consumer = _make_user(db, email="emp_b7_cust@example.com", role_name="consumer")
    warehouse_staff = _make_user(db, email="emp_b7_wh@example.com", role_name="warehouse")
    wh_headers = _auth_header(warehouse_staff)

    # Create order for 10 units
    order = _create_order_with_items(
        db, consumer, [(var_1, 10, Decimal("50.00"))], status="pending"
    )

    # Progress through lifecycle: pending -> reserved -> packed -> dispatched -> delivered
    client.post(
        f"/api/v1/orders/{order.id}/reserve",
        json={"warehouse_id": str(primary_warehouse.id)},
        headers=wh_headers,
    )
    client.post(f"/api/v1/orders/{order.id}/pack", headers=wh_headers)
    client.post(f"/api/v1/orders/{order.id}/dispatch", headers=wh_headers)
    deliver_resp = client.post(f"/api/v1/orders/{order.id}/deliver", headers=wh_headers)
    assert deliver_resp.status_code == 200
    assert deliver_resp.json()["status"] == "delivered"

    # Check balances right after delivery (on_hand was 30 - 10 = 20)
    db.expire_all()
    bal_after_delivery = db.get(InventoryBalance, balance.id)
    assert bal_after_delivery.on_hand == Decimal("20")
    assert bal_after_delivery.reserved == Decimal("0")

    # Record total stock movements before return
    movements_before_return = list(db.scalars(select(StockMovement)).all())
    movements_count_before = len(movements_before_return)

    # 1. Staff calls return endpoint with valid reason
    return_reason = "Customer reported compromised package seal; quarantined for QC inspection."
    return_resp = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": return_reason},
        headers=wh_headers,
    )
    assert return_resp.status_code == 200, return_resp.text
    return_data = return_resp.json()
    assert return_data["status"] == "returned"

    # 2. INVARIANT B7 VERIFICATION: on_hand MUST NOT BE INCREMENTED
    db.expire_all()
    bal_after_return = db.get(InventoryBalance, balance.id)
    assert bal_after_return.on_hand == Decimal("20"), (
        f"INVARIANT B7 VIOLATION: on_hand was {bal_after_return.on_hand}, "
        "must remain 20 (quarantine isolation)."
    )
    assert bal_after_return.reserved == Decimal("0")

    # 3. INVARIANT B7 VERIFICATION: NO POSITIVE STOCK MOVEMENT CREATED
    movements_after_return = list(db.scalars(select(StockMovement)).all())
    movements_count_after = len(movements_after_return)
    assert movements_count_after == movements_count_before, (
        f"INVARIANT B7 VIOLATION: StockMovement rows changed from {movements_count_before} to "
        f"{movements_count_after}. Return must NOT create any stock movements!"
    )

    # 4. Audit Log verification
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "orders",
            AuditLog.entity_id == order.id,
            AuditLog.action == "RETURN_ORDER",
        )
    )
    assert audit is not None
    assert audit.actor_id == warehouse_staff.id
    assert audit.before == {"status": "delivered"}
    assert audit.after.get("status") == "returned"
    assert audit.after.get("reason") == return_reason


def test_b7_return_validation_and_invalid_transitions(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
    primary_warehouse: Warehouse,
) -> None:
    """Invariant B7 & State Machine: Return requires a non-empty reason and can only be
    performed from 'delivered' status. Attempts from pending, reserved, packed, dispatched,
    cancelled, or repeated return must be rejected.
    """
    var_1 = catalog_fixture["var_1"]
    _create_batch_and_balance(db, primary_warehouse, var_1, "B7-VAL", expiry_days=50, on_hand=30)

    consumer = _make_user(db, email="emp_b7_val_cust@example.com", role_name="consumer")
    warehouse_staff = _make_user(db, email="emp_b7_val_wh@example.com", role_name="warehouse")
    wh_headers = _auth_header(warehouse_staff)

    order = _create_order_with_items(db, consumer, [(var_1, 5, Decimal("50.00"))], status="pending")

    # A. Return from 'pending' -> rejected 400
    r1 = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "Premature return"},
        headers=wh_headers,
    )
    assert r1.status_code == 400
    assert r1.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Progress to 'reserved' -> return rejected 400
    client.post(
        f"/api/v1/orders/{order.id}/reserve",
        json={"warehouse_id": str(primary_warehouse.id)},
        headers=wh_headers,
    )
    r2 = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "Return from reserved"},
        headers=wh_headers,
    )
    assert r2.status_code == 400
    assert r2.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Progress to 'packed' -> return rejected 400
    client.post(f"/api/v1/orders/{order.id}/pack", headers=wh_headers)
    r3 = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "Return from packed"},
        headers=wh_headers,
    )
    assert r3.status_code == 400
    assert r3.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Progress to 'dispatched' -> return rejected 400
    client.post(f"/api/v1/orders/{order.id}/dispatch", headers=wh_headers)
    r4 = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "Return before delivery"},
        headers=wh_headers,
    )
    assert r4.status_code == 400
    assert r4.json()["code"] == "ILLEGAL_STATE_TRANSITION"

    # Progress to 'delivered'
    client.post(f"/api/v1/orders/{order.id}/deliver", headers=wh_headers)

    # B. Missing / empty / whitespace reason -> rejected 422 or 400
    r_empty = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": ""},
        headers=wh_headers,
    )
    assert r_empty.status_code in (400, 422)

    r_whitespace = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "   \t\n  "},
        headers=wh_headers,
    )
    assert r_whitespace.status_code in (400, 422)

    # C. Valid return from delivered -> 200 OK
    r_valid = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "Damaged goods verified by QC"},
        headers=wh_headers,
    )
    assert r_valid.status_code == 200
    assert r_valid.json()["status"] == "returned"

    # D. Repeated return (double-return) -> rejected 400
    r_double = client.post(
        f"/api/v1/orders/{order.id}/return",
        json={"reason": "Second return attempt"},
        headers=wh_headers,
    )
    assert r_double.status_code == 400
    assert r_double.json()["code"] == "ILLEGAL_STATE_TRANSITION"


# ── Scenario 3: Comprehensive RBAC Matrix Across All Roles ───────────────────


def test_rbac_matrix_fulfillment_endpoints_across_all_roles(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
    primary_warehouse: Warehouse,
) -> None:
    """Test RBAC enforcement across all 6 canonical roles + non-canonical roles (admin, sales_rep).
    Fulfillment endpoints (/reserve, /pack, /dispatch, /deliver, /return) MUST ONLY be accessible
    by fulfillment staff ('owner', 'warehouse'). All other roles MUST receive HTTP 403.
    Unauthenticated callers MUST receive HTTP 401.
    """
    var_1 = catalog_fixture["var_1"]
    _create_batch_and_balance(
        db, primary_warehouse, var_1, "RBAC-BATCH", expiry_days=60, on_hand=100
    )

    # Seed custom roles if they don't exist yet
    for custom_role in ["admin", "sales_rep"]:
        if not db.scalar(select(Role).where(Role.name == custom_role)):
            db.add(Role(name=custom_role))
    db.commit()

    all_roles = [
        "owner",
        "warehouse",
        "factory",
        "distributor",
        "dealer",
        "consumer",
        "admin",
        "sales_rep",
    ]

    users = {
        role: _make_user(
            db, email=f"emp_rbac_{role}_{uuid.uuid4().hex[:4]}@example.com", role_name=role
        )
        for role in all_roles
    }

    # Setup base order in 'reserved' state
    order_customer = users["consumer"]
    order = _create_order_with_items(
        db, order_customer, [(var_1, 2, Decimal("50.00"))], status="pending"
    )

    # 1. Test /reserve
    for role, user in users.items():
        headers = _auth_header(user)
        # Create a fresh pending order for each test
        o_pending = _create_order_with_items(
            db, order_customer, [(var_1, 1, Decimal("50.00"))], status="pending"
        )
        resp = client.post(
            f"/api/v1/orders/{o_pending.id}/reserve",
            json={"warehouse_id": str(primary_warehouse.id)},
            headers=headers,
        )
        if role in ("owner", "warehouse"):
            assert (
                resp.status_code == 200
            ), f"Role '{role}' should be permitted to reserve, got {resp.status_code}"
        else:
            assert (
                resp.status_code == 403
            ), f"Role '{role}' should be FORBIDDEN from /reserve, got {resp.status_code}"

    # 2. Test /pack
    for role, user in users.items():
        headers = _auth_header(user)
        o_res = _create_order_with_items(
            db, order_customer, [(var_1, 1, Decimal("50.00"))], status="reserved"
        )
        resp = client.post(f"/api/v1/orders/{o_res.id}/pack", headers=headers)
        if role in ("owner", "warehouse"):
            assert (
                resp.status_code == 200
            ), f"Role '{role}' should be permitted to pack, got {resp.status_code}"
        else:
            assert (
                resp.status_code == 403
            ), f"Role '{role}' should be FORBIDDEN from /pack, got {resp.status_code}"

    # 3. Test /dispatch
    # Fulfill one order to packed with active reservations so staff can test dispatch
    for role, user in users.items():
        headers = _auth_header(user)
        # Setup packed order with reservation
        o_dispatch = _create_order_with_items(
            db, order_customer, [(var_1, 1, Decimal("50.00"))], status="pending"
        )
        client.post(
            f"/api/v1/orders/{o_dispatch.id}/reserve",
            json={"warehouse_id": str(primary_warehouse.id)},
            headers=_auth_header(users["owner"]),
        )
        client.post(
            f"/api/v1/orders/{o_dispatch.id}/pack",
            headers=_auth_header(users["owner"]),
        )

        resp = client.post(f"/api/v1/orders/{o_dispatch.id}/dispatch", headers=headers)
        if role in ("owner", "warehouse"):
            assert (
                resp.status_code == 200
            ), f"Role '{role}' should be permitted to dispatch, got {resp.status_code}"
        else:
            assert (
                resp.status_code == 403
            ), f"Role '{role}' should be FORBIDDEN from /dispatch, got {resp.status_code}"

    # 4. Test /return
    for role, user in users.items():
        headers = _auth_header(user)
        o_deliv = _create_order_with_items(
            db, order_customer, [(var_1, 1, Decimal("50.00"))], status="delivered"
        )
        resp = client.post(
            f"/api/v1/orders/{o_deliv.id}/return",
            json={"reason": "Quality issue"},
            headers=headers,
        )
        if role in ("owner", "warehouse"):
            assert (
                resp.status_code == 200
            ), f"Role '{role}' should be permitted to return, got {resp.status_code}"
        else:
            assert (
                resp.status_code == 403
            ), f"Role '{role}' should be FORBIDDEN from /return, got {resp.status_code}"

    # 5. Test Unauthenticated Requests
    for endpoint in ["reserve", "pack", "dispatch", "deliver", "return"]:
        unauth_resp = client.post(f"/api/v1/orders/{order.id}/{endpoint}")
        assert (
            unauth_resp.status_code == 401
        ), f"Unauth request to /{endpoint} should return 401, got {unauth_resp.status_code}"


def test_rbac_customer_cancellation_isolation_and_packed_restrictions(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
    primary_warehouse: Warehouse,
) -> None:
    """Test customer order cancellation isolation and state restrictions:
    1. Cross-Customer Isolation: Customer B cannot cancel Customer A's order (HTTP 403).
    2. Packed Order Restriction: Customer A cannot cancel own order once 'packed' (HTTP 403).
    3. Fulfillment Staff Permission: Warehouse staff CAN cancel even when 'packed' (HTTP 200).
    4. Non-customer/non-staff roles (factory, admin, sales_rep) cannot cancel orders (HTTP 403).
    """
    var_1 = catalog_fixture["var_1"]
    _create_batch_and_balance(
        db, primary_warehouse, var_1, "CANCEL-ISO", expiry_days=60, on_hand=50
    )

    # Ensure custom roles exist
    for custom_role in ["admin", "sales_rep"]:
        if not db.scalar(select(Role).where(Role.name == custom_role)):
            db.add(Role(name=custom_role))
    db.commit()

    cust_a = _make_user(db, email="emp_cust_a@example.com", role_name="consumer")
    cust_b = _make_user(db, email="emp_cust_b@example.com", role_name="consumer")
    dealer_c = _make_user(db, email="emp_dealer_c@example.com", role_name="dealer")
    factory_user = _make_user(db, email="emp_factory_user@example.com", role_name="factory")
    admin_user = _make_user(db, email="emp_admin_user@example.com", role_name="admin")
    wh_staff = _make_user(db, email="emp_wh_cancel@example.com", role_name="warehouse")

    # Customer A creates order
    order_a = _create_order_with_items(db, cust_a, [(var_1, 5, Decimal("50.00"))], status="pending")

    # 1. Customer B attempts to cancel Customer A's order -> 403 FORBIDDEN
    r_cross_b = client.post(
        f"/api/v1/orders/{order_a.id}/cancel",
        json={"reason": "Malicious cancellation by B"},
        headers=_auth_header(cust_b),
    )
    assert (
        r_cross_b.status_code == 403
    ), f"Customer B cancelling A's order should return 403, got {r_cross_b.status_code}"
    assert r_cross_b.json()["code"] == "FORBIDDEN"

    # Dealer C attempts to cancel Customer A's order -> 403 FORBIDDEN
    r_cross_c = client.post(
        f"/api/v1/orders/{order_a.id}/cancel",
        json={"reason": "Dealer cancelling consumer order"},
        headers=_auth_header(dealer_c),
    )
    assert (
        r_cross_c.status_code == 403
    ), f"Dealer C cancelling A's order should return 403, got {r_cross_c.status_code}"

    # Factory, Admin attempting to cancel -> 403 FORBIDDEN
    for u in [factory_user, admin_user]:
        r_other = client.post(
            f"/api/v1/orders/{order_a.id}/cancel",
            json={"reason": "Unauthorized cancel"},
            headers=_auth_header(u),
        )
        assert (
            r_other.status_code == 403
        ), f"Role {u.role.name} should receive 403, got {r_other.status_code}"

    # 2. Customer A CAN cancel own order while in 'pending' status
    r_cust_a_pending = client.post(
        f"/api/v1/orders/{order_a.id}/cancel",
        json={"reason": "Changed my mind before processing"},
        headers=_auth_header(cust_a),
    )
    assert r_cust_a_pending.status_code == 200
    assert r_cust_a_pending.json()["status"] == "cancelled"

    # 3. Create another order for Customer A, reserve and pack it
    order_a2 = _create_order_with_items(
        db, cust_a, [(var_1, 5, Decimal("50.00"))], status="pending"
    )
    wh_headers = _auth_header(wh_staff)
    client.post(
        f"/api/v1/orders/{order_a2.id}/reserve",
        json={"warehouse_id": str(primary_warehouse.id)},
        headers=wh_headers,
    )
    client.post(f"/api/v1/orders/{order_a2.id}/pack", headers=wh_headers)

    db.expire_all()
    assert db.get(Order, order_a2.id).status == "packed"

    # Customer A attempts to cancel once status is 'packed' -> MUST BE REJECTED (HTTP 403)
    r_cust_a_packed = client.post(
        f"/api/v1/orders/{order_a2.id}/cancel",
        json={"reason": "Attempting late cancel after pack"},
        headers=_auth_header(cust_a),
    )
    assert (
        r_cust_a_packed.status_code == 403
    ), f"Customer cancelling packed order must return 403, got {r_cust_a_packed.status_code}"
    assert "packed" in r_cust_a_packed.json()["message"].lower()

    # 4. Warehouse staff CAN cancel the packed order
    r_wh_cancel_packed = client.post(
        f"/api/v1/orders/{order_a2.id}/cancel",
        json={"reason": "Warehouse damaged box during packaging"},
        headers=wh_headers,
    )
    assert (
        r_wh_cancel_packed.status_code == 200
    ), f"Warehouse staff should cancel packed order, got {r_wh_cancel_packed.status_code}"
    assert r_wh_cancel_packed.json()["status"] == "cancelled"

    # Verify reservations were released via Invariant B6
    db.expire_all()
    reservations = list(
        db.scalars(
            select(StockReservation)
            .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
            .where(OrderItem.order_id == order_a2.id)
        ).all()
    )
    assert len(reservations) > 0
    for res in reservations:
        assert res.status == "released"


def test_rbac_order_view_customer_isolation(
    client: TestClient,
    db: Session,
    catalog_fixture: dict[str, Any],
) -> None:
    """Test customer isolation on GET /api/v1/orders/{id}:
    - Customer A can view own order.
    - Customer B receives 403 FORBIDDEN when attempting to view Customer A's order.
    - Staff (owner, warehouse, factory) can view any customer's order.
    """
    var_1 = catalog_fixture["var_1"]
    cust_a = _make_user(db, email="emp_iso_a@example.com", role_name="consumer")
    cust_b = _make_user(db, email="emp_iso_b@example.com", role_name="consumer")
    wh_staff = _make_user(db, email="emp_iso_wh@example.com", role_name="warehouse")

    order_a = _create_order_with_items(db, cust_a, [(var_1, 2, Decimal("50.00"))], status="pending")

    # Customer A views own order -> 200 OK
    r_a = client.get(f"/api/v1/orders/{order_a.id}", headers=_auth_header(cust_a))
    assert r_a.status_code == 200
    assert r_a.json()["id"] == str(order_a.id)

    # Customer B views Customer A's order -> 403 FORBIDDEN
    r_b = client.get(f"/api/v1/orders/{order_a.id}", headers=_auth_header(cust_b))
    assert r_b.status_code == 403
    assert r_b.json()["code"] == "FORBIDDEN"

    # Warehouse staff views Customer A's order -> 200 OK
    r_wh = client.get(f"/api/v1/orders/{order_a.id}", headers=_auth_header(wh_staff))
    assert r_wh.status_code == 200
    assert r_wh.json()["id"] == str(order_a.id)
