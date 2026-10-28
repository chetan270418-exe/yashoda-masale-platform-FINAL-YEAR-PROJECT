"""Transactional FEFO inventory service (spec-pack/implementation.md Section 5).

Enforces core business invariants:
- B1: Unreleased/held/rejected batches cannot be reserved or dispatched.
- B2 & B3: First-Expired, First-Out (FEFO) ordering and allocation across eligible batches.
- B4: Stock never goes negative; insufficient stock rolls back completely with zero side-effects.
- B5: Append-only stock ledger: every balance change produces a StockMovement.
- B6: Reservation cancellation releases stock exactly once via atomic CAS.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.audit.service import create_audit_log
from app.core.errors import AppError
from app.inventory.models import (
    InventoryBalance,
    OrderItem,
    ProductionBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)

# Canonical reason categories allowed for manual inventory adjustments
VALID_ADJUSTMENT_REASONS: frozenset[str] = frozenset(
    {"DAMAGE", "SPOILAGE", "COUNT_DISCREPANCY", "FOUND"}
)


class InventoryError(AppError):
    """Base exception for inventory domain errors."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(code=code, message=message, status_code=status_code, details=details)


class InsufficientStockError(InventoryError):
    """Raised when available stock is less than requested quantity."""

    def __init__(self, message: str = "Insufficient stock available.") -> None:
        super().__init__(code="INSUFFICIENT_STOCK", message=message, status_code=400)


class ReservationAlreadyProcessedError(InventoryError):
    """Raised when a reservation cannot be transitioned because it is no longer active."""

    def __init__(
        self,
        message: str = "Stock reservation has already been processed.",
        code: str = "RESERVATION_ALREADY_PROCESSED",
        status_code: int = 409,
    ) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


class ReservationNotFoundError(ReservationAlreadyProcessedError):
    """Raised when the specified stock reservation does not exist."""

    def __init__(self, message: str = "Stock reservation not found.") -> None:
        super().__init__(
            message=message,
            code="RESERVATION_NOT_FOUND",
            status_code=404,
        )


class BatchNotReleasedError(InventoryError):
    """Raised when an operation requires a released batch but found another status."""

    def __init__(self, message: str = "Batch is not in 'released' status.") -> None:
        super().__init__(code="BATCH_NOT_RELEASED", message=message, status_code=400)


class InvalidAdjustmentError(InventoryError):
    """Raised when an adjustment input is invalid (e.g. invalid reason or zero qty)."""

    def __init__(self, message: str = "Invalid stock adjustment parameters.") -> None:
        super().__init__(code="INVALID_ADJUSTMENT", message=message, status_code=400)


class WarehouseNotFoundError(InventoryError):
    """Raised when a warehouse ID does not match any record."""

    def __init__(self, message: str = "Warehouse not found.") -> None:
        super().__init__(code="WAREHOUSE_NOT_FOUND", message=message, status_code=404)


class BatchNotFoundError(InventoryError):
    """Raised when a batch ID does not match any record."""

    def __init__(self, message: str = "Production batch not found.") -> None:
        super().__init__(code="BATCH_NOT_FOUND", message=message, status_code=404)


class OrderItemNotFoundError(InventoryError):
    """Raised when an order item ID does not match any record."""

    def __init__(self, message: str = "Order item not found.") -> None:
        super().__init__(code="ORDER_ITEM_NOT_FOUND", message=message, status_code=404)


def _to_uuid(val: uuid.UUID | str, name: str) -> uuid.UUID:
    """Helper to convert string or UUID to uuid.UUID."""
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise InventoryError(
            code="INVALID_UUID",
            message=f"Invalid UUID for {name}: {val}",
            status_code=400,
        ) from exc


def _to_decimal(val: Decimal | int | float | str, name: str) -> Decimal:
    """Helper to convert numeric value to Decimal."""
    try:
        d = Decimal(str(val))
        if not d.is_finite():
            raise ValueError(f"{name} must be a finite number.")
        return d
    except Exception as exc:
        raise InventoryError(
            code="INVALID_QUANTITY",
            message=f"Invalid decimal quantity for {name}: {val}",
            status_code=400,
        ) from exc


def reserve_stock_fefo(
    db: Session,
    warehouse_id: uuid.UUID | str,
    variant_id: uuid.UUID | str,
    requested_qty: Decimal | int | float,
    order_item_id: uuid.UUID | str,
    min_shelf_days: int | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> list[StockReservation]:
    """Reserve stock for an order item using First-Expired, First-Out (FEFO).

    Enforces:
    - B1: Only released batches are eligible.
    - B2: Expired, held, rejected, or recalled batches are excluded.
    - B3: Allocation picks earliest-expiring batch first, splitting if needed.
    - B4: Stock never goes negative; insufficient stock rolls back completely.
    - B5: Creates StockMovement(movement_type='reserve') for every allocation.

    Executes row locking (`FOR UPDATE OF inventory_balances`) in deterministic order
    (`ORDER BY b.expiry_date ASC, b.id ASC`).
    """
    wh_uuid = _to_uuid(warehouse_id, "warehouse_id")
    variant_uuid = _to_uuid(variant_id, "variant_id")
    order_item_uuid = _to_uuid(order_item_id, "order_item_id")
    actor_uuid = _to_uuid(actor_id, "actor_id") if actor_id else None
    qty = _to_decimal(requested_qty, "requested_qty")

    if qty <= Decimal("0"):
        raise ValueError("Requested quantity must be greater than zero.")

    if min_shelf_days is not None and min_shelf_days < 0:
        raise ValueError("min_shelf_days cannot be negative.")

    # Verify warehouse existence and active status
    wh = db.scalar(select(Warehouse).where(Warehouse.id == wh_uuid))
    if not wh:
        raise WarehouseNotFoundError(f"Warehouse {wh_uuid} not found.")
    if not wh.is_active:
        raise InventoryError(
            code="WAREHOUSE_INACTIVE",
            message=f"Warehouse {wh_uuid} is inactive.",
            status_code=400,
        )

    order_item = db.scalar(
        select(OrderItem)
        .options(joinedload(OrderItem.order))
        .where(OrderItem.id == order_item_uuid)
    )
    if not order_item:
        raise OrderItemNotFoundError(f"OrderItem {order_item_uuid} not found.")

    if order_item.variant_id != variant_uuid:
        raise InventoryError(
            code="VARIANT_MISMATCH",
            message=(
                f"OrderItem {order_item_uuid} is for variant {order_item.variant_id}, "
                f"does not match requested variant {variant_uuid}."
            ),
            status_code=400,
        )

    if order_item.order and order_item.order.status == "cancelled":
        raise InventoryError(
            code="ORDER_CANCELLED",
            message=f"Cannot reserve stock for cancelled order {order_item.order_id}.",
            status_code=400,
        )

    today = date.today()
    min_days = min_shelf_days if min_shelf_days is not None else 0
    min_expiry = today + timedelta(days=min_days)

    # Deterministic query with row locking on inventory_balances:
    # 1. Matches warehouse & variant
    # 2. Batch must be 'released' (B1)
    # 3. Batch expiry >= min_expiry (B2: excludes expired batches & satisfies min_shelf_days)
    # 4. Filter for balances having positive available stock
    # 5. ORDER BY b.expiry_date ASC, b.id ASC (B3 & deadlock avoidance)
    # 6. with_for_update(of=InventoryBalance)
    stmt = (
        select(InventoryBalance)
        .join(ProductionBatch, InventoryBalance.batch_id == ProductionBatch.id)
        .options(contains_eager(InventoryBalance.batch))
        .where(
            InventoryBalance.warehouse_id == wh_uuid,
            ProductionBatch.variant_id == variant_uuid,
            ProductionBatch.status == "released",
            ProductionBatch.expiry_date >= min_expiry,
            (InventoryBalance.on_hand - InventoryBalance.reserved) > Decimal("0"),
        )
        .order_by(ProductionBatch.expiry_date.asc(), ProductionBatch.id.asc())
        .with_for_update(of=InventoryBalance)
    )

    balances = list(db.scalars(stmt).all())

    # Multi-batch allocation loop with post-lock availability re-check
    need = qty
    allocations: list[tuple[InventoryBalance, Decimal]] = []
    for bal in balances:
        available = bal.on_hand - bal.reserved
        if available <= Decimal("0"):
            continue
        take = min(need, available)
        allocations.append((bal, take))
        need -= take
        if need <= Decimal("0"):
            break

    # Invariant B4: If total available < requested_qty, complete rollback and 0 reservations
    if need > Decimal("0"):
        db.rollback()
        available_found = qty - need
        raise InsufficientStockError(
            f"Insufficient stock for variant {variant_uuid} in warehouse {wh_uuid}. "
            f"Requested {qty}, short by {need} "
            f"(available {available_found} across eligible released batches)."
        )

    # Persist allocations, reservations, and ledger movements atomically
    reservations: list[StockReservation] = []
    for bal, take in allocations:
        bal.reserved = InventoryBalance.reserved + take
        res_id = uuid.uuid4()
        res = StockReservation(
            id=res_id,
            order_item_id=order_item_uuid,
            balance_id=bal.id,
            quantity=take,
            status="active",
        )
        db.add(res)

        movement = StockMovement(
            warehouse_id=wh_uuid,
            batch_id=bal.batch_id,
            balance_id=bal.id,
            movement_type="reserve",
            quantity=take,
            reference_type="stock_reservation",
            reference_id=str(res_id),
            operation_key=f"reserve:{res_id}",
            actor_id=actor_uuid,
        )
        db.add(movement)
        reservations.append(res)

    try:
        db.commit()
        return reservations
    except (IntegrityError, StaleDataError) as exc:
        db.rollback()
        raise InsufficientStockError(
            f"Insufficient stock for variant {variant_uuid} due to concurrent update."
        ) from exc
    except Exception:
        db.rollback()
        raise


def cancel_reservation(
    db: Session,
    reservation_id: uuid.UUID | str,
    actor_id: uuid.UUID | str | None = None,
) -> StockReservation:
    """Cancel an active reservation and release reserved stock exactly once (B6).

    Uses atomic Compare-And-Swap (CAS) on stock_reservations:
    UPDATE stock_reservations SET status = 'released' WHERE id = :id AND status = 'active'
    """
    res_uuid = _to_uuid(reservation_id, "reservation_id")
    actor_uuid = _to_uuid(actor_id, "actor_id") if actor_id else None
    now = datetime.now(UTC)

    # Atomic CAS update: only succeeds if reservation is currently 'active'
    stmt = (
        update(StockReservation)
        .where(
            StockReservation.id == res_uuid,
            StockReservation.status == "active",
        )
        .values(status="released", updated_at=now)
        .returning(StockReservation)
    )
    updated_res = db.execute(stmt).scalar_one_or_none()

    if not updated_res:
        existing = db.scalar(select(StockReservation).where(StockReservation.id == res_uuid))
        if not existing:
            raise ReservationNotFoundError(f"Stock reservation {res_uuid} not found.")
        raise ReservationAlreadyProcessedError(
            f"Stock reservation {res_uuid} is already in '{existing.status}' "
            "status (cannot cancel)."
        )

    try:
        # Decrement balance.reserved and record release ledger movement
        balance = db.execute(
            select(InventoryBalance)
            .where(InventoryBalance.id == updated_res.balance_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not balance:
            db.rollback()
            raise InventoryError(
                code="BALANCE_NOT_FOUND",
                message=f"InventoryBalance {updated_res.balance_id} not found.",
                status_code=404,
            )

        balance.reserved = InventoryBalance.reserved - updated_res.quantity

        movement = StockMovement(
            warehouse_id=balance.warehouse_id,
            batch_id=balance.batch_id,
            balance_id=balance.id,
            movement_type="release",
            quantity=updated_res.quantity,
            reference_type="stock_reservation",
            reference_id=str(updated_res.id),
            operation_key=f"release:{updated_res.id}",
            actor_id=actor_uuid,
        )
        db.add(movement)
        db.commit()
        return updated_res
    except Exception:
        db.rollback()
        raise


def dispatch_reservation(
    db: Session,
    reservation_id: uuid.UUID | str,
    actor_id: uuid.UUID | str | None = None,
) -> StockReservation:
    """Dispatch an active reservation: consume reservation, deduct on_hand and reserved.

    Enforces B1: A batch that is not released can never be reserved or dispatched.
    Uses atomic Compare-And-Swap (CAS) on stock_reservations:
    UPDATE stock_reservations SET status = 'consumed' WHERE id = :id AND status = 'active'
    """
    res_uuid = _to_uuid(reservation_id, "reservation_id")
    actor_uuid = _to_uuid(actor_id, "actor_id") if actor_id else None
    now = datetime.now(UTC)

    # Atomic CAS update: only succeeds if reservation is currently 'active'
    stmt = (
        update(StockReservation)
        .where(
            StockReservation.id == res_uuid,
            StockReservation.status == "active",
        )
        .values(status="consumed", updated_at=now)
        .returning(StockReservation)
    )
    updated_res = db.execute(stmt).scalar_one_or_none()

    if not updated_res:
        existing = db.scalar(select(StockReservation).where(StockReservation.id == res_uuid))
        if not existing:
            raise ReservationNotFoundError(f"Stock reservation {res_uuid} not found.")
        raise ReservationAlreadyProcessedError(
            f"Stock reservation {res_uuid} is already in '{existing.status}' "
            "status (cannot dispatch)."
        )

    try:
        # Lock balance and verify batch release status (Invariant B1)
        balance = db.execute(
            select(InventoryBalance)
            .where(InventoryBalance.id == updated_res.balance_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not balance:
            db.rollback()
            raise InventoryError(
                code="BALANCE_NOT_FOUND",
                message=f"InventoryBalance {updated_res.balance_id} not found.",
                status_code=404,
            )

        batch = db.scalar(select(ProductionBatch).where(ProductionBatch.id == balance.batch_id))
        if not batch or batch.status != "released":
            db.rollback()
            batch_status = batch.status if batch else "unknown"
            raise BatchNotReleasedError(
                f"Cannot dispatch reservation {res_uuid}: batch {balance.batch_id} "
                f"status is '{batch_status}', not 'released' (Invariant B1)."
            )

        balance.on_hand = InventoryBalance.on_hand - updated_res.quantity
        balance.reserved = InventoryBalance.reserved - updated_res.quantity

        movement = StockMovement(
            warehouse_id=balance.warehouse_id,
            batch_id=balance.batch_id,
            balance_id=balance.id,
            movement_type="dispatch",
            quantity=updated_res.quantity,
            reference_type="stock_reservation",
            reference_id=str(updated_res.id),
            operation_key=f"dispatch:{updated_res.id}",
            actor_id=actor_uuid,
        )
        db.add(movement)
        db.commit()
        return updated_res
    except Exception:
        db.rollback()
        raise


def adjust_stock(
    db: Session,
    warehouse_id: uuid.UUID | str,
    batch_id: uuid.UUID | str,
    qty_delta: Decimal | int | float,
    reason_category: str,
    notes: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> InventoryBalance:
    """Manually adjust stock balance for a warehouse and batch with audit trail (T-043, B5).

    - If qty_delta > 0: balance.on_hand += qty_delta, movement_type='adjustment_in'
    - If qty_delta < 0: ensure balance.available >= abs(qty_delta),
      balance.on_hand -= abs(qty_delta), movement_type='adjustment_out'
    - reason_category must be one of: ('DAMAGE', 'SPOILAGE', 'COUNT_DISCREPANCY', 'FOUND')
    - Records StockMovement with unique operation_key and creates audit log.
    """
    wh_uuid = _to_uuid(warehouse_id, "warehouse_id")
    b_uuid = _to_uuid(batch_id, "batch_id")
    actor_uuid = _to_uuid(actor_id, "actor_id") if actor_id else None
    delta = _to_decimal(qty_delta, "qty_delta")

    if delta == Decimal("0"):
        raise InvalidAdjustmentError("Adjustment quantity delta cannot be zero.")

    if reason_category not in VALID_ADJUSTMENT_REASONS:
        reasons_list = sorted(VALID_ADJUSTMENT_REASONS)
        raise InvalidAdjustmentError(
            f"Invalid reason_category '{reason_category}'. Must be one of: {reasons_list}."
        )

    wh = db.scalar(select(Warehouse).where(Warehouse.id == wh_uuid))
    if not wh:
        raise WarehouseNotFoundError(f"Warehouse {wh_uuid} not found.")
    if not wh.is_active:
        raise InventoryError(
            code="WAREHOUSE_INACTIVE",
            message=f"Warehouse {wh_uuid} is inactive.",
            status_code=400,
        )

    batch = db.scalar(select(ProductionBatch).where(ProductionBatch.id == b_uuid))
    if not batch:
        raise BatchNotFoundError(f"ProductionBatch {b_uuid} not found.")

    try:
        balance = db.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == wh_uuid,
                InventoryBalance.batch_id == b_uuid,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if balance is None:
            if delta < Decimal("0"):
                db.rollback()
                raise InsufficientStockError(
                    f"Cannot adjust out {abs(delta)}: no inventory balance exists for "
                    f"warehouse {wh_uuid} and batch {b_uuid}."
                )
            balance = InventoryBalance(
                id=uuid.uuid4(),
                warehouse_id=wh_uuid,
                batch_id=b_uuid,
                on_hand=Decimal("0"),
                reserved=Decimal("0"),
            )
            db.add(balance)
            db.flush()

        before_on_hand = balance.on_hand
        before_reserved = balance.reserved

        if delta > Decimal("0"):
            movement_type = "adjustment_in"
            abs_qty = delta
            balance.on_hand = balance.on_hand + abs_qty
        else:
            movement_type = "adjustment_out"
            abs_qty = abs(delta)
            if balance.available < abs_qty:
                db.rollback()
                raise InsufficientStockError(
                    f"Cannot adjust out {abs_qty}: available stock is {balance.available} "
                    f"(on_hand={balance.on_hand}, reserved={balance.reserved})."
                )
            balance.on_hand = balance.on_hand - abs_qty

        op_key = f"adjust:{balance.id}:{uuid.uuid4()}"
        movement = StockMovement(
            warehouse_id=wh_uuid,
            batch_id=b_uuid,
            balance_id=balance.id,
            movement_type=movement_type,
            quantity=abs_qty,
            reference_type="manual_adjustment",
            reference_id=str(balance.id),
            operation_key=op_key,
            reason_category=reason_category,
            notes=notes,
            actor_id=actor_uuid,
        )
        db.add(movement)

        create_audit_log(
            db,
            actor_id=actor_uuid,
            action="STOCK_ADJUSTMENT",
            entity="inventory_balances",
            entity_id=balance.id,
            before={"on_hand": str(before_on_hand), "reserved": str(before_reserved)},
            after={
                "on_hand": str(balance.on_hand),
                "reserved": str(balance.reserved),
                "delta": str(delta),
                "reason_category": reason_category,
                "notes": notes,
            },
        )

        db.commit()
        return balance
    except Exception:
        db.rollback()
        raise


def receive_stock(
    db: Session,
    warehouse_id: uuid.UUID | str,
    batch_id: uuid.UUID | str,
    quantity: Decimal | int | float,
    reference_id: str | None = None,
    notes: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> InventoryBalance:
    """Receive newly produced or transferred batch stock into warehouse inventory (B5).

    Adds to balance.on_hand and logs StockMovement(movement_type='receipt').
    """
    wh_uuid = _to_uuid(warehouse_id, "warehouse_id")
    b_uuid = _to_uuid(batch_id, "batch_id")
    actor_uuid = _to_uuid(actor_id, "actor_id") if actor_id else None
    qty = _to_decimal(quantity, "quantity")

    if qty <= Decimal("0"):
        raise ValueError("Receipt quantity must be greater than zero.")

    wh = db.scalar(select(Warehouse).where(Warehouse.id == wh_uuid))
    if not wh:
        raise WarehouseNotFoundError(f"Warehouse {wh_uuid} not found.")
    if not wh.is_active:
        raise InventoryError(
            code="WAREHOUSE_INACTIVE",
            message=f"Warehouse {wh_uuid} is inactive.",
            status_code=400,
        )

    batch = db.scalar(select(ProductionBatch).where(ProductionBatch.id == b_uuid))
    if not batch:
        raise BatchNotFoundError(f"ProductionBatch {b_uuid} not found.")

    try:
        balance = db.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == wh_uuid,
                InventoryBalance.batch_id == b_uuid,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if balance is None:
            balance = InventoryBalance(
                id=uuid.uuid4(),
                warehouse_id=wh_uuid,
                batch_id=b_uuid,
                on_hand=Decimal("0"),
                reserved=Decimal("0"),
            )
            db.add(balance)
            db.flush()

        balance.on_hand = balance.on_hand + qty

        if reference_id:
            ref_id = reference_id
            op_key = f"receipt:{wh_uuid}:{ref_id}"
        else:
            receipt_uuid = uuid.uuid4()
            ref_id = str(receipt_uuid)
            op_key = f"receipt:{wh_uuid}:{b_uuid}:{receipt_uuid}"

        movement = StockMovement(
            warehouse_id=wh_uuid,
            batch_id=b_uuid,
            balance_id=balance.id,
            movement_type="receipt",
            quantity=qty,
            reference_type="batch_receipt",
            reference_id=ref_id,
            operation_key=op_key,
            notes=notes,
            actor_id=actor_uuid,
        )
        db.add(movement)
        db.commit()
        return balance
    except Exception:
        db.rollback()
        raise
