"""Multithreaded PostgreSQL concurrency test suite for Yashoda FEFO Inventory (T-045).

Verifies core transactional invariants under high contention on PostgreSQL:
- Invariant B4: Stock never goes negative; reservations never exceed available stock.
- Invariant B3: Earliest-expiring eligible batch is allocated first (FEFO) without overselling.
- Invariant B6: Atomic Compare-And-Swap (CAS) ensures cancel vs dispatch race resolves cleanly.
- Invariants B1, B2: Batches on hold cannot be reserved even under concurrent race conditions.

Note:
Requires live PostgreSQL with row-level locking (SELECT ... FOR UPDATE).
Skipped on SQLite as SQLite uses database-level locks rather than row-level locks.
"""

import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

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
    InsufficientStockError,
    ReservationAlreadyProcessedError,
    cancel_reservation,
    dispatch_reservation,
    reserve_stock_fefo,
)
from tests.conftest import _engine, _TestSession

if _engine.dialect.name != "postgresql":
    pytest.skip(
        "Multithreaded concurrency tests require PostgreSQL with row-level FOR UPDATE locking. "
        "Set TEST_DATABASE_URL to run.",
        allow_module_level=True,
    )


def _create_variant_and_warehouse(db, prefix: str = "concur"):
    """Helper to set up basic catalog and warehouse structures."""
    cat = Category(name=f"Category_{prefix}_{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Product_{prefix}_{uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id, sku=f"SKU_{prefix}_{uuid.uuid4().hex[:8]}", pack_size_g=100
    )
    wh = Warehouse(code=f"WH_{prefix}_{uuid.uuid4().hex[:8]}", name="Concurrency Warehouse")
    db.add_all([var, wh])
    db.flush()
    return prod, var, wh


# ── Test 1: Two orders, one unit left (Invariant B4) ──────────────────────────


def test_two_orders_one_unit_left_concurrency(db):
    """10 concurrent threads attempt to reserve 1 unit against a balance with on_hand=1.

    Enforces B4:
    - Exactly 1 thread succeeds in acquiring the reservation.
    - Exactly 9 threads fail with InsufficientStockError.
    - Final inventory balance: on_hand=1, reserved=1, available=0.
    - Exactly 1 StockReservation exists in status 'active'.
    - Exactly 1 StockMovement exists for 'reserve'.
    """
    _, var, wh = _create_variant_and_warehouse(db, prefix="single_unit")

    today = date.today()
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"BATCH-1UNIT-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=60),
        status="released",
    )
    db.add(batch)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=wh.id,
        batch_id=batch.id,
        on_hand=Decimal("1.000"),
        reserved=Decimal("0.000"),
    )
    db.add(balance)

    # Pre-create 10 distinct orders and order items
    order_items = []
    for i in range(10):
        order = Order(order_number=f"ORD-1U-{i}-{uuid.uuid4().hex[:6]}", status="pending")
        db.add(order)
        db.flush()
        oi = OrderItem(order_id=order.id, variant_id=var.id, quantity=Decimal("1.000"))
        db.add(oi)
        order_items.append(oi)

    db.commit()

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    successes: list[tuple[uuid.UUID, list[StockReservation]]] = []
    failures: list[tuple[uuid.UUID, Exception]] = []
    lock = threading.Lock()

    def _worker(item_id: uuid.UUID):
        session = _TestSession()
        try:
            barrier.wait()  # Synchronize threads to hit PostgreSQL at the exact same instant
            res = reserve_stock_fefo(
                db=session,
                warehouse_id=wh.id,
                variant_id=var.id,
                requested_qty=Decimal("1.000"),
                order_item_id=item_id,
            )
            with lock:
                successes.append((item_id, res))
        except Exception as exc:
            with lock:
                failures.append((item_id, exc))
        finally:
            session.close()

    threads = [
        threading.Thread(target=_worker, args=(order_items[i].id,)) for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariant B4 assertions
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(failures) == 9, f"Expected 9 failures, got {len(failures)}"

    for _, exc in failures:
        assert isinstance(
            exc, InsufficientStockError
        ), f"Expected InsufficientStockError, got {type(exc)}: {exc}"

    # Verify database state
    verify_session = _TestSession()
    try:
        final_bal = verify_session.scalar(
            select(InventoryBalance).where(InventoryBalance.id == balance.id)
        )
        assert final_bal.on_hand == Decimal("1.000")
        assert final_bal.reserved == Decimal("1.000")
        assert final_bal.available == Decimal("0.000")

        reservations = verify_session.scalars(
            select(StockReservation).where(StockReservation.balance_id == balance.id)
        ).all()
        assert len(reservations) == 1
        assert reservations[0].status == "active"
        assert reservations[0].quantity == Decimal("1.000")

        movements = verify_session.scalars(
            select(StockMovement).where(
                StockMovement.balance_id == balance.id,
                StockMovement.movement_type == "reserve",
            )
        ).all()
        assert len(movements) == 1
        assert movements[0].quantity == Decimal("1.000")
    finally:
        verify_session.close()


# ── Test 2: Parallel multi-batch race (Invariants B3, B4) ─────────────────────


def test_parallel_multi_batch_race_concurrency(db):
    """20 threads simultaneously reserving varying quantities across 3 batches.

    Enforces B3 (FEFO order) and B4 (Stock never negative, reserved <= on_hand).
    Total stock across batches: 10 + 15 + 20 = 45 units.
    Total requested across 20 threads: 60 units (exceeds total stock).
    """
    _, var, wh = _create_variant_and_warehouse(db, prefix="multi_batch")

    today = date.today()
    # 3 released batches with distinct expiries
    b1 = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B1-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=10),
        status="released",
    )
    b2 = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B2-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=20),
        status="released",
    )
    b3 = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B3-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=30),
        status="released",
    )
    db.add_all([b1, b2, b3])
    db.flush()

    bal1 = InventoryBalance(
        warehouse_id=wh.id, batch_id=b1.id, on_hand=Decimal("10.000"), reserved=Decimal("0.000")
    )
    bal2 = InventoryBalance(
        warehouse_id=wh.id, batch_id=b2.id, on_hand=Decimal("15.000"), reserved=Decimal("0.000")
    )
    bal3 = InventoryBalance(
        warehouse_id=wh.id, batch_id=b3.id, on_hand=Decimal("20.000"), reserved=Decimal("0.000")
    )
    db.add_all([bal1, bal2, bal3])

    # 20 orders requesting quantities summing to 60 (exceeds total 45)
    quantities = [
        Decimal("3"),
        Decimal("2"),
        Decimal("4"),
        Decimal("1"),
        Decimal("5"),
        Decimal("2"),
        Decimal("3"),
        Decimal("4"),
        Decimal("1"),
        Decimal("3"),
        Decimal("2"),
        Decimal("4"),
        Decimal("5"),
        Decimal("2"),
        Decimal("3"),
        Decimal("1"),
        Decimal("4"),
        Decimal("2"),
        Decimal("4"),
        Decimal("5"),
    ]
    assert sum(quantities) == Decimal("60")

    order_items = []
    for i, qty in enumerate(quantities):
        order = Order(order_number=f"ORD-MB-{i}-{uuid.uuid4().hex[:6]}", status="pending")
        db.add(order)
        db.flush()
        oi = OrderItem(order_id=order.id, variant_id=var.id, quantity=qty)
        db.add(oi)
        order_items.append((oi, qty))

    db.commit()

    num_threads = 20
    barrier = threading.Barrier(num_threads)
    results_lock = threading.Lock()
    successful_reservations: list[tuple[uuid.UUID, Decimal, list[StockReservation]]] = []
    failed_reservations: list[tuple[uuid.UUID, Decimal, Exception]] = []

    def _worker(item_id: uuid.UUID, req_qty: Decimal):
        session = _TestSession()
        try:
            barrier.wait()
            res = reserve_stock_fefo(
                db=session,
                warehouse_id=wh.id,
                variant_id=var.id,
                requested_qty=req_qty,
                order_item_id=item_id,
            )
            with results_lock:
                successful_reservations.append((item_id, req_qty, res))
        except Exception as exc:
            with results_lock:
                failed_reservations.append((item_id, req_qty, exc))
        finally:
            session.close()

    threads = [threading.Thread(target=_worker, args=(oi.id, qty)) for oi, qty in order_items]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All failures must be InsufficientStockError
    for _, _, exc in failed_reservations:
        assert isinstance(exc, InsufficientStockError)

    # Sum of successful quantities
    total_successful_qty = sum((qty for _, qty, _ in successful_reservations), Decimal("0"))
    assert total_successful_qty <= Decimal("45.000")

    # Verify inventory balances in database
    verify_session = _TestSession()
    try:
        final_b1 = verify_session.scalar(
            select(InventoryBalance).where(InventoryBalance.id == bal1.id)
        )
        final_b2 = verify_session.scalar(
            select(InventoryBalance).where(InventoryBalance.id == bal2.id)
        )
        final_b3 = verify_session.scalar(
            select(InventoryBalance).where(InventoryBalance.id == bal3.id)
        )

        # Invariant B4: reserved <= on_hand on each balance
        for bal in [final_b1, final_b2, final_b3]:
            assert bal.reserved <= bal.on_hand, f"Balance {bal.id} oversold: reserved > on_hand"
            assert bal.reserved >= Decimal("0.000")

        total_db_reserved = final_b1.reserved + final_b2.reserved + final_b3.reserved
        assert total_db_reserved == total_successful_qty

        # Verify FEFO ordering: earliest batch (b1) should be exhausted before later ones
        if final_b2.reserved > Decimal("0"):
            assert (
                final_b1.reserved == final_b1.on_hand
            ), "FEFO violated: b2 used before b1 exhausted"
        if final_b3.reserved > Decimal("0"):
            assert (
                final_b2.reserved == final_b2.on_hand
            ), "FEFO violated: b3 used before b2 exhausted"
    finally:
        verify_session.close()


# ── Test 3: Atomic CAS race (cancel vs dispatch) ──────────────────────────────


def test_atomic_cas_cancel_vs_dispatch_race(db):
    """Simultaneous cancellation and dispatch on the same active reservation (B6).

    Uses atomic Compare-And-Swap (CAS):
    - Exactly 1 operation succeeds.
    - Exactly 1 operation fails with ReservationAlreadyProcessedError.
    - Final reservation status is either 'released' or 'consumed', never active.
    """
    _, var, wh = _create_variant_and_warehouse(db, prefix="cas_race")

    today = date.today()
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-CAS-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=60),
        status="released",
    )
    db.add(batch)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=wh.id,
        batch_id=batch.id,
        on_hand=Decimal("10.000"),
        reserved=Decimal("0.000"),
    )
    db.add(balance)

    order = Order(order_number=f"ORD-CAS-{uuid.uuid4().hex[:6]}", status="pending")
    db.add(order)
    db.flush()

    order_item = OrderItem(order_id=order.id, variant_id=var.id, quantity=Decimal("4.000"))
    db.add(order_item)
    db.commit()

    # Create active reservation
    res_list = reserve_stock_fefo(
        db=db,
        warehouse_id=wh.id,
        variant_id=var.id,
        requested_qty=Decimal("4.000"),
        order_item_id=order_item.id,
    )
    reservation = res_list[0]
    res_id = reservation.id

    barrier = threading.Barrier(2)
    results: dict[str, str | Exception] = {}
    lock = threading.Lock()

    def _cancel_worker():
        session = _TestSession()
        try:
            barrier.wait()
            cancel_reservation(session, res_id)
            with lock:
                results["cancel"] = "success"
        except Exception as exc:
            with lock:
                results["cancel"] = exc
        finally:
            session.close()

    def _dispatch_worker():
        session = _TestSession()
        try:
            barrier.wait()
            dispatch_reservation(session, res_id)
            with lock:
                results["dispatch"] = "success"
        except Exception as exc:
            with lock:
                results["dispatch"] = exc
        finally:
            session.close()

    t_cancel = threading.Thread(target=_cancel_worker)
    t_dispatch = threading.Thread(target=_dispatch_worker)

    t_cancel.start()
    t_dispatch.start()
    t_cancel.join()
    t_dispatch.join()

    # CAS assertion: exactly one success, one ReservationAlreadyProcessedError
    success_count = sum(1 for val in results.values() if val == "success")
    assert success_count == 1, f"Expected exactly 1 success in CAS race, got {results}"

    for op, res in results.items():
        if res != "success":
            assert isinstance(res, ReservationAlreadyProcessedError), (
                f"Failed op '{op}' should raise ReservationAlreadyProcessedError, "
                f"got {type(res)}: {res}"
            )

    verify_session = _TestSession()
    try:
        final_res = verify_session.scalar(
            select(StockReservation).where(StockReservation.id == res_id)
        )
        assert final_res.status in {"released", "consumed"}

        final_bal = verify_session.scalar(
            select(InventoryBalance).where(InventoryBalance.id == balance.id)
        )
        assert final_bal.reserved == Decimal("0.000")

        if final_res.status == "released":
            # Cancel won: stock released back to on_hand
            assert final_bal.on_hand == Decimal("10.000")
        else:
            # Dispatch won: stock consumed from on_hand
            assert final_bal.on_hand == Decimal("6.000")
    finally:
        verify_session.close()


# ── Test 4: Held batch isolation (Invariants B1, B2) ──────────────────────────


def test_held_batch_isolation_concurrency(db):
    """Concurrent reservation attempts while batch status is transitioned to 'on_hold'.

    Enforces B1 & B2:
    - Batches on hold cannot be reserved.
    - Reservations either succeed while batch is released or fail after hold is committed.
    - Under no circumstances does reserved exceed on_hand.
    """
    _, var, wh = _create_variant_and_warehouse(db, prefix="held_iso")

    today = date.today()
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-HELD-{uuid.uuid4().hex[:6]}",
        expiry_date=today + timedelta(days=60),
        status="released",
    )
    db.add(batch)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=wh.id,
        batch_id=batch.id,
        on_hand=Decimal("10.000"),
        reserved=Decimal("0.000"),
    )
    db.add(balance)

    # Pre-create 6 orders requesting 2 units each (total 12 units > 10)
    order_items = []
    for i in range(6):
        order = Order(order_number=f"ORD-HELD-{i}-{uuid.uuid4().hex[:6]}", status="pending")
        db.add(order)
        db.flush()
        oi = OrderItem(order_id=order.id, variant_id=var.id, quantity=Decimal("2.000"))
        db.add(oi)
        order_items.append(oi)

    db.commit()

    # 6 reservation threads + 1 hold thread = 7 threads
    barrier = threading.Barrier(7)
    res_successes: list[tuple[uuid.UUID, list[StockReservation]]] = []
    res_failures: list[tuple[uuid.UUID, Exception]] = []
    lock = threading.Lock()

    def _res_worker(item_id: uuid.UUID):
        session = _TestSession()
        try:
            barrier.wait()
            res = reserve_stock_fefo(
                db=session,
                warehouse_id=wh.id,
                variant_id=var.id,
                requested_qty=Decimal("2.000"),
                order_item_id=item_id,
            )
            with lock:
                res_successes.append((item_id, res))
        except Exception as exc:
            with lock:
                res_failures.append((item_id, exc))
        finally:
            session.close()

    def _hold_worker():
        session = _TestSession()
        try:
            barrier.wait()
            # Transition batch to hold
            b = session.scalar(select(ProductionBatch).where(ProductionBatch.id == batch.id))
            if b:
                b.status = "on_hold"
                session.commit()
        finally:
            session.close()

    threads = [threading.Thread(target=_res_worker, args=(oi.id,)) for oi in order_items]
    threads.append(threading.Thread(target=_hold_worker))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify that any failures are InsufficientStockError
    for _, exc in res_failures:
        assert isinstance(exc, InsufficientStockError)

    # Verify database state
    verify_session = _TestSession()
    try:
        final_batch = verify_session.scalar(
            select(ProductionBatch).where(ProductionBatch.id == batch.id)
        )
        assert final_batch.status == "on_hold"

        final_bal = verify_session.scalar(
            select(InventoryBalance).where(InventoryBalance.id == balance.id)
        )
        assert final_bal.reserved <= final_bal.on_hand
        assert final_bal.reserved <= Decimal("10.000")

        # Now that batch is confirmed 'on_hold', any further reservation attempt MUST fail
        extra_order = Order(order_number=f"ORD-AFTER-HOLD-{uuid.uuid4().hex[:6]}", status="pending")
        verify_session.add(extra_order)
        verify_session.flush()
        extra_oi = OrderItem(order_id=extra_order.id, variant_id=var.id, quantity=Decimal("1.000"))
        verify_session.add(extra_oi)
        verify_session.commit()

        with pytest.raises(InsufficientStockError):
            reserve_stock_fefo(
                db=verify_session,
                warehouse_id=wh.id,
                variant_id=var.id,
                requested_qty=Decimal("1.000"),
                order_item_id=extra_oi.id,
            )
    finally:
        verify_session.close()
