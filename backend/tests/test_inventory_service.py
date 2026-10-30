"""Comprehensive unit and invariant tests for the FEFO inventory service (T-041 & T-043).

Verifies core business invariants:
- B1: Unreleased/held/rejected batches cannot be reserved or dispatched.
- B2 & B3: FEFO selection picks earliest-expiring eligible batch first,
          splits across batches, and respects min_shelf_days.
- B4: Stock never goes negative; insufficient stock rolls back completely with zero side-effects.
- B5: Append-only stock movements ledger reconciles perfectly with balance on_hand and reserved.
- B6: Reservation cancellation is strictly exactly-once via atomic CAS.
"""

import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import User
from app.catalog.models import Category, Product, ProductVariant
from app.inventory.models import (
    InventoryBalance,
    Order,
    OrderItem,
    ProductionBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)
from app.inventory.service import (
    BatchNotFoundError,
    BatchNotReleasedError,
    InsufficientStockError,
    InvalidAdjustmentError,
    InventoryError,
    OrderItemNotFoundError,
    ReservationAlreadyProcessedError,
    ReservationNotFoundError,
    WarehouseNotFoundError,
    adjust_stock,
    cancel_reservation,
    dispatch_reservation,
    receive_stock,
    reserve_stock_fefo,
)
from tests.conftest import _engine, _make_user, _TestSession


@pytest.fixture
def test_user(db: Session) -> User:
    """Provide a real test user for audit logs."""
    return _make_user(
        db,
        email=f"warehouse-{uuid.uuid4().hex[:6]}@example.com",
        role_name="warehouse",
    )


@pytest.fixture
def warehouse(db: Session) -> Warehouse:
    """Provide a test warehouse."""
    wh = Warehouse(
        code=f"WH-TEST-{uuid.uuid4().hex[:6]}",
        name="Pune Test Depot",
        is_primary=True,
    )
    db.add(wh)
    db.commit()
    return wh


@pytest.fixture
def variant(db: Session) -> ProductVariant:
    """Provide a test product variant."""
    cat = Category(name=f"Spices-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Garam Masala {uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id,
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        pack_size_g=250,
    )
    db.add(var)
    db.commit()
    return var


@pytest.fixture
def order_item(db: Session, variant: ProductVariant) -> OrderItem:
    """Provide a test order item with an associated order."""
    order = Order(order_number=f"ORD-{uuid.uuid4().hex[:8]}")
    db.add(order)
    db.flush()

    item = OrderItem(
        order_id=order.id,
        variant_id=variant.id,
        quantity=Decimal("100.000"),
    )
    db.add(item)
    db.commit()
    return item


def _create_batch_and_balance(
    db: Session,
    warehouse: Warehouse,
    variant: ProductVariant,
    *,
    batch_code: str,
    days_to_expiry: int,
    status: str = "released",
    on_hand: Decimal = Decimal("50.000"),
    reserved: Decimal = Decimal("0"),
) -> tuple[ProductionBatch, InventoryBalance]:
    """Helper to create a batch and its inventory balance."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=batch_code,
        manufactured_at=today - timedelta(days=10),
        expiry_date=today + timedelta(days=days_to_expiry),
        status=status,
    )
    db.add(batch)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=warehouse.id,
        batch_id=batch.id,
        on_hand=on_hand,
        reserved=reserved,
    )
    db.add(balance)
    db.commit()
    return batch, balance


# ==============================================================================
# Invariant B1 Tests: Unreleased batches cannot be reserved or dispatched
# ==============================================================================


def test_b1_unreleased_batches_cannot_be_reserved(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Invariant B1: batches in 'on_hold' or 'rejected' cannot be reserved."""
    # Batch in 'on_hold' status
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-HOLD",
        days_to_expiry=30,
        status="on_hold",
        on_hand=Decimal("50.000"),
    )
    # Batch in 'rejected' status
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-REJ",
        days_to_expiry=40,
        status="rejected",
        on_hand=Decimal("50.000"),
    )
    # Batch in 'on_hold' status
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-REC",
        days_to_expiry=50,
        status="on_hold",
        on_hand=Decimal("50.000"),
    )

    # All available stock is in unreleased batches, reservation must fail
    with pytest.raises(InsufficientStockError) as exc_info:
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("10.000"),
            order_item_id=order_item.id,
        )
    assert "available 0" in str(exc_info.value)

    # Verify no reservations or movements exist
    reservations = db.scalars(select(StockReservation)).all()
    assert len(reservations) == 0


def test_b1_dispatch_blocked_if_batch_status_changed_from_released(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Invariant B1: if a batch is placed on hold after reservation, dispatch is blocked."""
    batch, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-REL-TO-HOLD",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
    )

    # Successfully reserve
    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("10.000"),
        order_item_id=order_item.id,
    )
    assert len(reservations) == 1
    res = reservations[0]

    # Batch is subsequently placed on hold (e.g. factory quality alert)
    batch.status = "on_hold"
    db.commit()

    # Attempting to dispatch must raise BatchNotReleasedError
    with pytest.raises(BatchNotReleasedError) as exc_info:
        dispatch_reservation(db, reservation_id=res.id)
    assert "status is 'on_hold', not 'released'" in str(exc_info.value)

    # Verify reservation remains active and balance is untouched
    db.refresh(res)
    assert res.status == "active"
    db.refresh(balance)
    assert balance.on_hand == Decimal("20.000")
    assert balance.reserved == Decimal("10.000")


# ==============================================================================
# Invariants B2 & B3 Tests: FEFO ordering, splitting, and filtering
# ==============================================================================


def test_b2_b3_fefo_picks_earliest_expiring_batch_first(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """FEFO must allocate from the batch with the earliest expiry date first."""
    # Batch 1: expires in 15 days
    b1, bal1 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-EXP-15",
        days_to_expiry=15,
        status="released",
        on_hand=Decimal("30.000"),
    )
    # Batch 2: expires in 45 days
    b2, bal2 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-EXP-45",
        days_to_expiry=45,
        status="released",
        on_hand=Decimal("30.000"),
    )

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("20.000"),
        order_item_id=order_item.id,
    )

    assert len(reservations) == 1
    res = reservations[0]
    assert res.balance_id == bal1.id
    assert res.quantity == Decimal("20.000")

    db.refresh(bal1)
    db.refresh(bal2)
    assert bal1.reserved == Decimal("20.000")
    assert bal2.reserved == Decimal("0")


def test_b2_b3_fefo_splits_across_batches(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """When the earliest batch cannot satisfy total qty, FEFO splits across batches in order."""
    # Batch 1: expires in 10 days, 15 units available
    b1, bal1 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-SPLIT-1",
        days_to_expiry=10,
        status="released",
        on_hand=Decimal("15.000"),
    )
    # Batch 2: expires in 25 days, 20 units available
    b2, bal2 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-SPLIT-2",
        days_to_expiry=25,
        status="released",
        on_hand=Decimal("20.000"),
    )
    # Batch 3: expires in 60 days, 50 units available
    b3, bal3 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-SPLIT-3",
        days_to_expiry=60,
        status="released",
        on_hand=Decimal("50.000"),
    )

    # Request 30 units: should take 15 from B1 and 15 from B2
    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("30.000"),
        order_item_id=order_item.id,
    )

    assert len(reservations) == 2
    r1, r2 = reservations
    assert r1.balance_id == bal1.id
    assert r1.quantity == Decimal("15.000")
    assert r2.balance_id == bal2.id
    assert r2.quantity == Decimal("15.000")

    db.refresh(bal1)
    db.refresh(bal2)
    db.refresh(bal3)
    assert bal1.reserved == Decimal("15.000")  # Fully reserved
    assert bal2.reserved == Decimal("15.000")  # Partially reserved
    assert bal3.reserved == Decimal("0")  # Untouched


def test_b2_b3_fefo_skips_expired_and_held_batches(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """FEFO skips already expired batches and unreleased batches when picking."""
    # Expired batch: expired 5 days ago, 100 units
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-EXPIRED",
        days_to_expiry=-5,
        status="released",
        on_hand=Decimal("100.000"),
    )
    # Held batch: expires in 20 days, 100 units
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-HELD",
        days_to_expiry=20,
        status="on_hold",
        on_hand=Decimal("100.000"),
    )
    # Valid released batch: expires in 30 days, 20 units
    b_valid, bal_valid = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-VALID",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
    )

    # Request 15 units: should pick only from B-VALID
    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("15.000"),
        order_item_id=order_item.id,
    )

    assert len(reservations) == 1
    assert reservations[0].balance_id == bal_valid.id
    assert reservations[0].quantity == Decimal("15.000")


def test_b2_min_shelf_days_filtering(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Batches with shelf life below min_shelf_days must be excluded from FEFO."""
    # Batch 1: expires in 10 days
    b1, bal1 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-SHELF-10",
        days_to_expiry=10,
        status="released",
        on_hand=Decimal("30.000"),
    )
    # Batch 2: expires in 40 days
    b2, bal2 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-SHELF-40",
        days_to_expiry=40,
        status="released",
        on_hand=Decimal("30.000"),
    )

    # Reserve with min_shelf_days=20 -> should skip Batch 1 and allocate from Batch 2
    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("10.000"),
        order_item_id=order_item.id,
        min_shelf_days=20,
    )

    assert len(reservations) == 1
    assert reservations[0].balance_id == bal2.id

    db.refresh(bal1)
    db.refresh(bal2)
    assert bal1.reserved == Decimal("0")
    assert bal2.reserved == Decimal("10.000")


def test_fefo_deterministic_tie_breaker_on_same_expiry(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """When two batches have identical expiry dates, ordering by batch.id ASC is deterministic."""
    b1, bal1 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-TIE-A",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("10.000"),
    )
    b2, bal2 = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-TIE-B",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("10.000"),
    )

    first_bal = bal1 if b1.id < b2.id else bal2

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("5.000"),
        order_item_id=order_item.id,
    )

    assert len(reservations) == 1
    assert reservations[0].balance_id == first_bal.id


# ==============================================================================
# Invariant B4 Tests: Stock never goes negative; rollback on insufficient stock
# ==============================================================================


def test_b4_insufficient_stock_rolls_back_completely_with_zero_side_effects(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Invariant B4: If stock cannot satisfy the full request, roll back all side-effects."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-PARTIAL",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("10.000"),
    )

    initial_movements = len(db.scalars(select(StockMovement)).all())

    with pytest.raises(InsufficientStockError) as exc_info:
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("15.000"),
            order_item_id=order_item.id,
        )
    assert "short by 5.000" in str(exc_info.value)

    # Verify no partial reservation was committed
    db.refresh(balance)
    assert balance.reserved == Decimal("0")
    assert balance.on_hand == Decimal("10.000")

    # Verify no StockReservation rows exist
    reservations = db.scalars(select(StockReservation)).all()
    assert len(reservations) == 0

    # Verify no StockMovement rows created
    current_movements = len(db.scalars(select(StockMovement)).all())
    assert current_movements == initial_movements


def test_b4_cannot_reserve_already_reserved_stock(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Available stock is on_hand - reserved. Existing reservations reduce available stock."""
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-ALREADY-RES",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
        reserved=Decimal("15.000"),
    )

    # Available is only 5. Requesting 10 must fail
    with pytest.raises(InsufficientStockError):
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("10.000"),
            order_item_id=order_item.id,
        )


def test_b4_adjust_stock_out_cannot_exceed_available(
    db: Session, warehouse: Warehouse, variant: ProductVariant
):
    """Adjusting stock out must not drive available or on_hand negative."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-ADJ-EXCEED",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
        reserved=Decimal("15.000"),
    )

    # Available is 5. Attempting to adjust out 6 must fail
    with pytest.raises(InsufficientStockError) as exc_info:
        adjust_stock(
            db,
            warehouse_id=warehouse.id,
            batch_id=b.id,
            qty_delta=Decimal("-6.000"),
            reason_category="DAMAGE",
        )
    assert "available stock is 5.000" in str(exc_info.value)

    db.refresh(balance)
    assert balance.on_hand == Decimal("20.000")
    assert balance.reserved == Decimal("15.000")


# ==============================================================================
# Invariant B5 Tests: Movements ledger reconciles with balance sum
# ==============================================================================


def test_b5_movements_ledger_reconciles_with_balance_sum(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Invariant B5: Ledger of stock movements exactly reconciles with current balance."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-RECONCILE",
        days_to_expiry=60,
        status="released",
        on_hand=Decimal("0"),
        reserved=Decimal("0"),
    )

    # 1. Receipt: receive 100 units
    receive_stock(db, warehouse_id=warehouse.id, batch_id=b.id, quantity=Decimal("100.000"))

    # 2. Reservation: reserve 30 units
    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("30.000"),
        order_item_id=order_item.id,
    )
    res = reservations[0]

    # 3. Manual adjustment out: 5 units damaged
    adjust_stock(
        db,
        warehouse_id=warehouse.id,
        batch_id=b.id,
        qty_delta=Decimal("-5.000"),
        reason_category="DAMAGE",
    )

    # 4. Dispatch reservation: dispatch the 30 reserved units
    dispatch_reservation(db, reservation_id=res.id)

    # 5. Manual adjustment in: found 10 units
    adjust_stock(
        db,
        warehouse_id=warehouse.id,
        batch_id=b.id,
        qty_delta=Decimal("10.000"),
        reason_category="FOUND",
    )

    db.refresh(balance)
    # Expected on_hand: 100 (receipt) - 5 (adj_out) - 30 (dispatch) + 10 (adj_in) = 75
    # Expected reserved: 0
    assert balance.on_hand == Decimal("75.000")
    assert balance.reserved == Decimal("0.000")

    # Verify ledger sum from stock_movements
    movements = db.scalars(
        select(StockMovement).where(StockMovement.balance_id == balance.id)
    ).all()

    ledger_on_hand = Decimal("0")
    ledger_reserved = Decimal("0")

    for m in movements:
        if m.movement_type in ("receipt", "adjustment_in"):
            ledger_on_hand += m.quantity
        elif m.movement_type in ("dispatch", "adjustment_out"):
            ledger_on_hand -= m.quantity

        if m.movement_type == "reserve":
            ledger_reserved += m.quantity
        elif m.movement_type in ("release", "dispatch"):
            ledger_reserved -= m.quantity

    assert ledger_on_hand == balance.on_hand
    assert ledger_reserved == balance.reserved


# ==============================================================================
# Invariant B6 Tests: Cancellation is strictly exactly-once (CAS)
# ==============================================================================


def test_b6_cancellation_releases_reservation_exactly_once(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Invariant B6: Cancelling releases reservation exactly once; duplicate raises error."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-CANCEL",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("50.000"),
    )

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("20.000"),
        order_item_id=order_item.id,
    )
    res = reservations[0]

    db.refresh(balance)
    assert balance.reserved == Decimal("20.000")

    # First cancel call: must succeed
    cancelled_res = cancel_reservation(db, reservation_id=res.id)
    assert cancelled_res.status == "released"

    db.refresh(balance)
    assert balance.reserved == Decimal("0.000")

    # Verify release movement created
    release_movements = db.scalars(
        select(StockMovement).where(
            StockMovement.reference_id == str(res.id),
            StockMovement.movement_type == "release",
        )
    ).all()
    assert len(release_movements) == 1
    assert release_movements[0].quantity == Decimal("20.000")

    # Second cancel call: must raise ReservationAlreadyProcessedError
    with pytest.raises(ReservationAlreadyProcessedError) as exc_info:
        cancel_reservation(db, reservation_id=res.id)
    assert "already in 'released' status" in str(exc_info.value)

    # Verify balance was NOT decremented again (would violate check constraint if it did)
    db.refresh(balance)
    assert balance.reserved == Decimal("0.000")

    # Verify no second movement created
    release_movements_after = db.scalars(
        select(StockMovement).where(
            StockMovement.reference_id == str(res.id),
            StockMovement.movement_type == "release",
        )
    ).all()
    assert len(release_movements_after) == 1


# ==============================================================================
# Dispatch Lifecycle Tests
# ==============================================================================


def test_dispatch_reservation_updates_balances_and_records_movement(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Dispatch decrements both on_hand and reserved and creates dispatch movement."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-DISPATCH",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("50.000"),
    )

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("15.000"),
        order_item_id=order_item.id,
    )
    res = reservations[0]

    dispatched_res = dispatch_reservation(db, reservation_id=res.id)
    assert dispatched_res.status == "consumed"

    db.refresh(balance)
    assert balance.on_hand == Decimal("35.000")
    assert balance.reserved == Decimal("0.000")

    # Verify dispatch movement
    m = db.scalar(
        select(StockMovement).where(
            StockMovement.reference_id == str(res.id),
            StockMovement.movement_type == "dispatch",
        )
    )
    assert m is not None
    assert m.quantity == Decimal("15.000")
    assert m.operation_key == f"dispatch:{res.id}"

    # Second dispatch call raises ReservationAlreadyProcessedError
    with pytest.raises(ReservationAlreadyProcessedError):
        dispatch_reservation(db, reservation_id=res.id)

    # Cancel after dispatch raises ReservationAlreadyProcessedError
    with pytest.raises(ReservationAlreadyProcessedError):
        cancel_reservation(db, reservation_id=res.id)


def test_dispatch_after_cancel_raises_error(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Dispatch cannot proceed on an already cancelled reservation."""
    _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-CANCEL-FIRST",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("30.000"),
    )

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("10.000"),
        order_item_id=order_item.id,
    )
    res = reservations[0]

    cancel_reservation(db, reservation_id=res.id)

    with pytest.raises(ReservationAlreadyProcessedError):
        dispatch_reservation(db, reservation_id=res.id)


# ==============================================================================
# Adjust Stock & Audit Log Tests (T-043)
# ==============================================================================


def test_adjust_stock_positive_creates_movement_and_audit_log(
    db: Session, warehouse: Warehouse, variant: ProductVariant, test_user: User
):
    """Positive stock adjustment updates on_hand, creates adjustment_in movement and audit log."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-ADJ-POS",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
    )

    updated_bal = adjust_stock(
        db,
        warehouse_id=warehouse.id,
        batch_id=b.id,
        qty_delta=Decimal("15.000"),
        reason_category="FOUND",
        notes="Audit found unopened case",
        actor_id=test_user.id,
    )

    assert updated_bal.on_hand == Decimal("35.000")

    # Verify StockMovement
    movement = db.scalar(
        select(StockMovement).where(
            StockMovement.balance_id == balance.id,
            StockMovement.movement_type == "adjustment_in",
        )
    )
    assert movement is not None
    assert movement.quantity == Decimal("15.000")
    assert movement.reason_category == "FOUND"
    assert movement.actor_id == test_user.id

    # Verify AuditLog
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "inventory_balances",
            AuditLog.entity_id == balance.id,
        )
    )
    assert audit is not None
    assert audit.actor_id == test_user.id
    assert audit.action == "STOCK_ADJUSTMENT"
    assert audit.before["on_hand"] == "20.000"
    assert audit.after["on_hand"] == "35.000"
    assert audit.after["reason_category"] == "FOUND"


def test_adjust_stock_negative_creates_movement_and_audit_log(
    db: Session, warehouse: Warehouse, variant: ProductVariant
):
    """Negative stock adjustment updates on_hand, creates adjustment_out movement and audit log."""
    b, balance = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-ADJ-NEG",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("50.000"),
    )

    updated_bal = adjust_stock(
        db,
        warehouse_id=warehouse.id,
        batch_id=b.id,
        qty_delta=Decimal("-12.000"),
        reason_category="SPOILAGE",
        notes="High humidity shelf damage",
    )

    assert updated_bal.on_hand == Decimal("38.000")

    movement = db.scalar(
        select(StockMovement).where(
            StockMovement.balance_id == balance.id,
            StockMovement.movement_type == "adjustment_out",
        )
    )
    assert movement is not None
    assert movement.quantity == Decimal("12.000")
    assert movement.reason_category == "SPOILAGE"


def test_adjust_stock_invalid_reason_category_rejected(
    db: Session, warehouse: Warehouse, variant: ProductVariant
):
    """Adjustment with invalid reason category is rejected."""
    b, _ = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-INVALID-REASON",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
    )

    with pytest.raises(InvalidAdjustmentError) as exc_info:
        adjust_stock(
            db,
            warehouse_id=warehouse.id,
            batch_id=b.id,
            qty_delta=Decimal("5.000"),
            reason_category="THEFT",  # Not in allowed categories
        )
    assert "Invalid reason_category 'THEFT'" in str(exc_info.value)


def test_adjust_stock_zero_delta_rejected(
    db: Session, warehouse: Warehouse, variant: ProductVariant
):
    """Adjustment with zero delta is rejected."""
    b, _ = _create_batch_and_balance(
        db,
        warehouse,
        variant,
        batch_code="B-ZERO-DELTA",
        days_to_expiry=30,
        status="released",
        on_hand=Decimal("20.000"),
    )

    with pytest.raises(InvalidAdjustmentError) as exc_info:
        adjust_stock(
            db,
            warehouse_id=warehouse.id,
            batch_id=b.id,
            qty_delta=Decimal("0"),
            reason_category="COUNT_DISCREPANCY",
        )
    assert "delta cannot be zero" in str(exc_info.value)


# ==============================================================================
# Edge Cases & Error Handling Tests
# ==============================================================================


def test_nonexistent_reservation_raises_not_found(db: Session):
    """Attempting to cancel/dispatch a missing reservation raises ReservationNotFoundError."""
    random_id = uuid.uuid4()
    with pytest.raises(ReservationNotFoundError):
        cancel_reservation(db, reservation_id=random_id)

    with pytest.raises(ReservationNotFoundError):
        dispatch_reservation(db, reservation_id=random_id)


def test_nonexistent_warehouse_or_batch_raises_not_found(
    db: Session, variant: ProductVariant, order_item: OrderItem
):
    """Operations with non-existent warehouse or batch raise domain-specific not-found errors."""
    random_id = uuid.uuid4()

    with pytest.raises(WarehouseNotFoundError):
        reserve_stock_fefo(
            db,
            warehouse_id=random_id,
            variant_id=variant.id,
            requested_qty=Decimal("10.000"),
            order_item_id=order_item.id,
        )

    with pytest.raises(OrderItemNotFoundError):
        wh = Warehouse(code=f"WH-{uuid.uuid4().hex[:6]}", name="WH")
        db.add(wh)
        db.commit()
        reserve_stock_fefo(
            db,
            warehouse_id=wh.id,
            variant_id=variant.id,
            requested_qty=Decimal("10.000"),
            order_item_id=random_id,
        )

    with pytest.raises(BatchNotFoundError):
        adjust_stock(
            db,
            warehouse_id=wh.id,
            batch_id=random_id,
            qty_delta=Decimal("5.000"),
            reason_category="FOUND",
        )


def test_invalid_requested_qty_rejected(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Negative or zero reservation quantity is rejected."""
    with pytest.raises(ValueError):
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("0"),
            order_item_id=order_item.id,
        )

    with pytest.raises(ValueError):
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("-5.000"),
            order_item_id=order_item.id,
        )


def test_variant_mismatch_rejected(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Attempting to reserve with an order item for another variant is rejected."""
    other_var = ProductVariant(
        product_id=variant.product_id,
        sku=f"SKU-OTHER-{uuid.uuid4().hex[:6]}",
        pack_size_g=100,
    )
    db.add(other_var)
    db.commit()

    with pytest.raises(InventoryError) as exc_info:
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=other_var.id,
            requested_qty=Decimal("5.000"),
            order_item_id=order_item.id,
        )
    assert exc_info.value.code == "VARIANT_MISMATCH"


def test_negative_min_shelf_days_rejected(
    db: Session, warehouse: Warehouse, variant: ProductVariant, order_item: OrderItem
):
    """Negative min_shelf_days is rejected with ValueError to protect Invariant B2."""
    with pytest.raises(ValueError) as exc_info:
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("5.000"),
            order_item_id=order_item.id,
            min_shelf_days=-10,
        )
    assert "min_shelf_days cannot be negative" in str(exc_info.value)


def test_inactive_warehouse_rejected(db: Session, variant: ProductVariant, order_item: OrderItem):
    """Operations on inactive warehouses are rejected."""
    wh = Warehouse(
        code=f"WH-INACTIVE-{uuid.uuid4().hex[:6]}",
        name="Decommissioned WH",
        is_active=False,
    )
    db.add(wh)
    db.commit()

    with pytest.raises(InventoryError) as exc_info:
        reserve_stock_fefo(
            db,
            warehouse_id=wh.id,
            variant_id=variant.id,
            requested_qty=Decimal("5.000"),
            order_item_id=order_item.id,
        )
    assert exc_info.value.code == "WAREHOUSE_INACTIVE"


def test_cancelled_order_reservation_rejected(
    db: Session, warehouse: Warehouse, variant: ProductVariant
):
    """Cannot reserve stock for order items belonging to a cancelled order."""
    order = Order(order_number=f"ORD-CANC-{uuid.uuid4().hex[:6]}", status="cancelled")
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, variant_id=variant.id, quantity=Decimal("10.000"))
    db.add(item)
    db.commit()

    with pytest.raises(InventoryError) as exc_info:
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("5.000"),
            order_item_id=item.id,
        )
    assert exc_info.value.code == "ORDER_CANCELLED"


def test_concurrency_race_condition_no_overselling(warehouse: Warehouse, variant: ProductVariant):
    """Two concurrent reservation attempts on 1 available unit must never oversell (B4)."""
    if _engine.dialect.name != "postgresql":
        pytest.skip(
            "Multithreaded concurrency tests require PostgreSQL row-level locks. "
            "Set TEST_DATABASE_URL to run."
        )
    setup_session = _TestSession()
    today = date.today()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"B-CONCUR-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=45),
        status="released",
    )
    setup_session.add(batch)
    setup_session.flush()

    balance = InventoryBalance(
        warehouse_id=warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal("1.000"),
        reserved=Decimal("0"),
    )
    setup_session.add(balance)

    o1 = Order(order_number=f"ORD-C1-{uuid.uuid4().hex[:6]}")
    o2 = Order(order_number=f"ORD-C2-{uuid.uuid4().hex[:6]}")
    setup_session.add(o1)
    setup_session.add(o2)
    setup_session.flush()

    oi1 = OrderItem(order_id=o1.id, variant_id=variant.id, quantity=Decimal("1.000"))
    oi2 = OrderItem(order_id=o2.id, variant_id=variant.id, quantity=Decimal("1.000"))
    setup_session.add(oi1)
    setup_session.add(oi2)
    setup_session.commit()
    setup_session.close()

    results: list[str] = []

    def worker(order_item_id: uuid.UUID) -> None:
        worker_db = _TestSession()
        try:
            reserve_stock_fefo(
                worker_db,
                warehouse_id=warehouse.id,
                variant_id=variant.id,
                requested_qty=Decimal("1.000"),
                order_item_id=order_item_id,
            )
            results.append("success")
        except InsufficientStockError:
            results.append("insufficient_stock")
        finally:
            worker_db.close()

    t1 = threading.Thread(target=worker, args=(oi1.id,))
    t2 = threading.Thread(target=worker, args=(oi2.id,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one must succeed and exactly one must fail with InsufficientStockError
    assert results.count("success") == 1
    assert results.count("insufficient_stock") == 1

    verify_session = _TestSession()
    bal = verify_session.scalar(select(InventoryBalance).where(InventoryBalance.id == balance.id))
    assert bal is not None
    assert bal.reserved == Decimal("1.000")
    assert bal.on_hand == Decimal("1.000")
    verify_session.close()


def test_receive_stock_idempotent_reference_id(
    db: Session, warehouse: Warehouse, variant: ProductVariant
):
    """Calling receive_stock twice with the same reference_id raises IntegrityError."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"B-IDEMP-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=60),
        status="released",
    )
    db.add(batch)
    db.commit()

    ref = f"GRN-{uuid.uuid4().hex[:8]}"
    receive_stock(
        db,
        warehouse_id=warehouse.id,
        batch_id=batch.id,
        quantity=Decimal("50.000"),
        reference_id=ref,
    )

    with pytest.raises(IntegrityError):
        receive_stock(
            db,
            warehouse_id=warehouse.id,
            batch_id=batch.id,
            quantity=Decimal("50.000"),
            reference_id=ref,
        )
