"""Orders service layer (Milestone M1 / T-050).

Implements:
- Order state machine transitions (Rule 49)
- Idempotency key handling and conflict detection (Rule 48)
- Role-based price snapshots and Decimal money arithmetic (Invariants B8, B9, B12)
- Customer order scoping and listing
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.audit.service import log_action
from app.auth.models import User
from app.catalog import repository as catalog_repo
from app.catalog.models import PriceListItem
from app.catalog.service import resolve_price_list_name_for_role
from app.core.errors import AppError
from app.orders.models import Invoice, Order, OrderItem, Shipment, ShipmentEvent
from app.orders.schemas import OrderCreate

if TYPE_CHECKING:
    from app.inventory.models import Warehouse


class OrderStatus(str, Enum):
    """Canonical lifecycle states for an order."""

    PENDING = "pending"
    RESERVED = "reserved"
    PACKED = "packed"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING.value: {OrderStatus.RESERVED.value, OrderStatus.CANCELLED.value},
    OrderStatus.RESERVED.value: {OrderStatus.PACKED.value, OrderStatus.CANCELLED.value},
    OrderStatus.PACKED.value: {OrderStatus.DISPATCHED.value, OrderStatus.CANCELLED.value},
    OrderStatus.DISPATCHED.value: {OrderStatus.DELIVERED.value},
    OrderStatus.DELIVERED.value: {OrderStatus.RETURNED.value},
    OrderStatus.CANCELLED.value: set(),
    OrderStatus.RETURNED.value: set(),
}


def validate_transition(current_status: str, target_status: str) -> None:
    """Validate whether transitioning from current_status to target_status is permitted."""
    current = current_status.strip().lower()
    target = target_status.strip().lower()

    allowed = ALLOWED_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise AppError(
            code="ILLEGAL_STATE_TRANSITION",
            message=f"Cannot transition order from '{current}' to '{target}'.",
            status_code=400,
            details={
                "current_status": current,
                "target_status": target,
                "allowed_transitions": sorted(list(allowed)) if allowed is not None else [],
            },
        )


def transition_order_status(
    order: Order,
    target_status: str,
    actor_id: uuid.UUID | None = None,
) -> Order:
    """Validate and transition an in-memory or persisted order to a new state."""
    validate_transition(order.status, target_status)
    order.status = target_status.strip().lower()
    return order


def _payload_matches(order: Order, customer_id: uuid.UUID, data: OrderCreate) -> bool:
    """Check if incoming order creation payload matches an existing order."""
    if order.customer_id != customer_id:
        return False

    if getattr(data, "order_type", None) and order.order_type != data.order_type:
        return False

    incoming_quantities: dict[uuid.UUID, Decimal] = {}
    for item in data.items:
        incoming_quantities[item.variant_id] = incoming_quantities.get(
            item.variant_id, Decimal("0")
        ) + Decimal(str(item.quantity))

    existing_quantities: dict[uuid.UUID, Decimal] = {}
    for item in order.items:
        existing_quantities[item.variant_id] = existing_quantities.get(
            item.variant_id, Decimal("0")
        ) + Decimal(str(item.quantity))

    return incoming_quantities == existing_quantities


def generate_order_number() -> str:
    """Generate human-readable unique order number."""
    ts = datetime.now(UTC).strftime("%Y%m%d")
    unique_suffix = uuid.uuid4().hex[:8].upper()
    return f"ORD-{ts}-{unique_suffix}"


def create_order(
    db: Session,
    user: User,
    payload: OrderCreate,
    idempotency_key: str | None = None,
) -> tuple[Order, bool]:
    """Create a new order with role-based price snapshots and Rule 48 idempotency.

    Invariants enforced:
    - B8: Immutable price snapshot frozen on order items.
    - B9: Server resolves role-specific price list (MRP, DEALER, DISTRIBUTOR).
    - B12: All monetary calculations in Decimal(12, 2).
    - Rule 48: Idempotent replay for identical parameters, 409 conflict for parameter mismatch.

    Returns:
        tuple[Order, bool]: The order object and a boolean indicating whether it was replayed.
    """
    key = idempotency_key or getattr(payload, "idempotency_key", None)
    key = key.strip() if key and key.strip() else None

    # 1. Check idempotency key if provided (scoped per customer)
    if key:
        existing = (
            db.scalars(
                select(Order)
                .options(joinedload(Order.items))
                .where(
                    Order.customer_id == user.id,
                    Order.idempotency_key == key,
                )
            )
            .unique()
            .first()
        )

        if existing:
            if _payload_matches(existing, user.id, payload):
                return (existing, True)
            raise AppError(
                code="IDEMPOTENCY_CONFLICT",
                message="Idempotency key reuse with different parameters",
                status_code=409,
            )

    if not payload.items:
        raise AppError("EMPTY_ORDER", "Order must contain at least one item.", 400)

    if len(payload.items) > 50:
        raise AppError("TOO_MANY_ITEMS", "An order can contain at most 50 items.", 400)

    # 2. Resolve price list by user role (B9)
    role_name = user.role.name if user.role else None
    price_list_name = resolve_price_list_name_for_role(role_name)
    price_list = catalog_repo.get_price_list_by_name(db, price_list_name)
    if not price_list:
        raise AppError(
            "PRICE_LIST_NOT_FOUND", f"Price list '{price_list_name}' does not exist.", 404
        )

    # 3. Resolve prices and calculate line totals using Decimal (B8, B12)
    order_id = uuid.uuid4()
    order_number = generate_order_number()
    order_items: list[OrderItem] = []
    total_amount = Decimal("0.00")

    for item in payload.items:
        qty = Decimal(str(item.quantity))
        if qty <= Decimal("0"):
            raise AppError(
                "INVALID_QUANTITY",
                f"Quantity for variant '{item.variant_id}' must be greater than zero.",
                400,
            )
        if qty > Decimal("10000"):
            raise AppError(
                "QUANTITY_TOO_LARGE",
                f"Quantity for variant '{item.variant_id}' cannot exceed 10,000.",
                400,
            )

        variant = catalog_repo.get_variant_by_id(db, item.variant_id)
        if not variant:
            raise AppError("VARIANT_NOT_FOUND", f"Variant '{item.variant_id}' not found.", 404)

        if not variant.is_active or not variant.product.is_active:
            raise AppError("VARIANT_INACTIVE", f"Variant '{variant.sku}' is inactive.", 400)

        price_stmt = (
            select(PriceListItem.price)
            .where(
                PriceListItem.price_list_id == price_list.id,
                PriceListItem.variant_id == variant.id,
                PriceListItem.valid_from <= datetime.now(UTC),
            )
            .order_by(PriceListItem.valid_from.desc(), PriceListItem.id.desc())
            .limit(1)
        )
        price = db.scalars(price_stmt).first()

        if price is None:
            raise AppError(
                code="PRICE_NOT_FOUND",
                message=(
                    f"No active price found for variant '{variant.sku}' in "
                    f"price list '{price_list_name}'."
                ),
                status_code=404,
            )

        unit_price_snapshot = Decimal(str(price)).quantize(Decimal("0.01"))
        line_total = (unit_price_snapshot * qty).quantize(Decimal("0.01"))
        total_amount += line_total

        order_item = OrderItem(
            id=uuid.uuid4(),
            order_id=order_id,
            variant_id=variant.id,
            quantity=qty,
            unit_price_snapshot=unit_price_snapshot,
            price_list_code=price_list_name,
            line_total=line_total,
        )
        order_items.append(order_item)

    order = Order(
        id=order_id,
        order_number=order_number,
        customer_id=user.id,
        order_type=getattr(payload, "order_type", "sales") or "sales",
        status=OrderStatus.PENDING.value,
        idempotency_key=key,
        total_amount=total_amount.quantize(Decimal("0.01")),
        currency="INR",
        items=order_items,
    )

    db.add(order)

    # 4. Atomic commit with concurrency collision protection
    try:
        db.commit()
        db.refresh(order)
        return (order, False)
    except IntegrityError as exc:
        db.rollback()
        if key:
            existing = (
                db.scalars(
                    select(Order)
                    .options(joinedload(Order.items))
                    .where(
                        Order.customer_id == user.id,
                        Order.idempotency_key == key,
                    )
                )
                .unique()
                .first()
            )
            if existing:
                if _payload_matches(existing, user.id, payload):
                    return (existing, True)
                raise AppError(
                    code="IDEMPOTENCY_CONFLICT",
                    message="Idempotency key reuse with different parameters",
                    status_code=409,
                ) from exc
        raise


def get_order_by_id(db: Session, order_id: uuid.UUID) -> Order | None:
    """Retrieve an order with loaded order items."""
    return (
        db.scalars(select(Order).options(joinedload(Order.items)).where(Order.id == order_id))
        .unique()
        .first()
    )


def list_orders(
    db: Session,
    customer_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Order]:
    """List orders with optional customer ownership and status filters."""
    stmt = select(Order).options(joinedload(Order.items))
    if customer_id is not None:
        stmt = stmt.where(Order.customer_id == customer_id)
    if status is not None:
        stmt = stmt.where(Order.status == status.strip().lower())
    stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).unique().all())


def update_order_status(
    db: Session,
    order_id: uuid.UUID,
    target_status: str,
    actor_id: uuid.UUID | None = None,
    commit: bool = True,
) -> Order:
    """Transition an order to a new state enforcing the lifecycle state machine."""
    order = db.scalars(select(Order).where(Order.id == order_id).with_for_update(of=Order)).first()
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_id}' not found.", 404)

    old_status = order.status
    transition_order_status(order, target_status, actor_id=actor_id)

    if actor_id:
        log_action(
            db,
            actor_id=actor_id,
            action="UPDATE_ORDER_STATUS",
            entity="orders",
            entity_id=order.id,
            before={"status": old_status},
            after={"status": order.status},
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(order)
    return order


# -----------------------------------------------------------------------------
# Milestone M2 / T-051 Fulfillment Service Methods
# -----------------------------------------------------------------------------


def resolve_target_warehouse(
    db: Session,
    warehouse_id: uuid.UUID | str | None = None,
) -> Warehouse:
    """Resolve target warehouse for fulfillment: explicit ID, primary, or default fallback."""
    from app.inventory.models import Warehouse

    if warehouse_id is not None:
        wh_uuid = (
            uuid.UUID(str(warehouse_id))
            if not isinstance(warehouse_id, uuid.UUID)
            else warehouse_id
        )
        wh = db.scalar(
            select(Warehouse).where(Warehouse.id == wh_uuid, Warehouse.is_active.is_(True))
        )
        if not wh:
            raise AppError(
                code="WAREHOUSE_NOT_FOUND",
                message=f"Warehouse '{wh_uuid}' not found or inactive.",
                status_code=404,
            )
        return wh

    # 1. Primary active warehouse
    wh = db.scalar(
        select(Warehouse).where(Warehouse.is_primary.is_(True), Warehouse.is_active.is_(True))
    )
    if wh:
        return wh

    # 2. Known default warehouse codes (WH-CENTRAL-01, WH-MH-01)
    wh = db.scalar(
        select(Warehouse).where(
            Warehouse.code.in_(["WH-CENTRAL-01", "WH-MH-01"]),
            Warehouse.is_active.is_(True),
        )
    )
    if wh:
        return wh

    # 3. First active warehouse
    wh = db.scalar(
        select(Warehouse).where(Warehouse.is_active.is_(True)).order_by(Warehouse.created_at.asc())
    )
    if wh:
        return wh

    raise AppError(
        code="WAREHOUSE_NOT_FOUND",
        message="No active warehouse found to fulfill order.",
        status_code=404,
    )


def reserve_order(
    db: Session,
    order_id: uuid.UUID | str,
    warehouse_id: uuid.UUID | str | None = None,
    actor_id: uuid.UUID | str | None = None,
    commit: bool = True,
) -> Order:
    """Reserve inventory for an order using FEFO batch selection (Invariants B1, B2, B3, B4).

    - Locks the Order row (FOR UPDATE OF orders) to serialize concurrent fulfillment.
    - Validates order status is 'pending'.
    - Resolves target warehouse.
    - Reserves stock for each order line item via inventory_service.reserve_stock_fefo.
    - Every line item is allocated inside ONE transaction. If any line fails, the
      whole reservation is rolled back, so no partial reservation can survive
      (Invariant B4). No compensating cancels are used.
    - Transitions order status to 'reserved'.

    When ``commit=False`` the caller owns the transaction and must commit or roll
    it back. This lets an order and its stock reservation be persisted atomically
    together, for example when an AI order draft is approved.
    """
    from app.inventory import service as inventory_service

    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    order = (
        db.scalars(
            select(Order)
            .options(joinedload(Order.items))
            .where(Order.id == order_uuid)
            .with_for_update(of=Order)
        )
        .unique()
        .first()
    )

    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    # Validate state transition
    normalized_status = order.status.strip().lower()
    if normalized_status != OrderStatus.PENDING.value:
        validate_transition(normalized_status, OrderStatus.RESERVED.value)

    if not order.items:
        raise AppError(
            "EMPTY_ORDER", f"Order '{order_uuid}' contains no line items to reserve.", 400
        )

    target_wh = resolve_target_warehouse(db, warehouse_id=warehouse_id)

    # Multi-item FEFO allocation inside ONE transaction.
    # Each line item only flushes its allocations; the commit happens once, after
    # every item has succeeded. If any item fails the whole transaction is rolled
    # back, so a partial reservation can never survive (Invariant B4).
    #
    # Lock ordering: line items are processed in variant_id order, and each item
    # locks its balances by (expiry_date, id) inside reserve_stock_fefo. That
    # gives every concurrent reservation the same global lock order, so two
    # orders touching the same variants in opposite line order cannot deadlock.
    try:
        for item in sorted(order.items, key=lambda it: it.variant_id):
            inventory_service.reserve_stock_fefo(
                db=db,
                warehouse_id=target_wh.id,
                variant_id=item.variant_id,
                requested_qty=item.quantity,
                order_item_id=item.id,
                actor_id=actor_uuid,
                commit=False,
            )
    except Exception:
        db.rollback()
        raise

    old_status = order.status
    order.status = OrderStatus.RESERVED.value

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="RESERVE_ORDER",
            entity="orders",
            entity_id=order.id,
            before={"status": old_status},
            after={"status": OrderStatus.RESERVED.value, "warehouse_id": str(target_wh.id)},
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(order)
    return order


def pack_order(
    db: Session,
    order_id: uuid.UUID | str,
    actor_id: uuid.UUID | str | None = None,
    commit: bool = True,
) -> Order:
    """Transition order status from 'reserved' to 'packed'."""
    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    order = db.scalars(
        select(Order).where(Order.id == order_uuid).with_for_update(of=Order)
    ).first()
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    validate_transition(order.status, OrderStatus.PACKED.value)

    old_status = order.status
    order.status = OrderStatus.PACKED.value

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="PACK_ORDER",
            entity="orders",
            entity_id=order.id,
            before={"status": old_status},
            after={"status": OrderStatus.PACKED.value},
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(order)
    return order


def dispatch_order(
    db: Session,
    order_id: uuid.UUID | str,
    carrier: str | None = None,
    tracking_ref: str | None = None,
    actor_id: uuid.UUID | str | None = None,
    commit: bool = True,
) -> Order:
    """Dispatch packed order, decrement on_hand & reserved,
    and record stock_movements ledger (B5).
    """
    from app.inventory import service as inventory_service
    from app.inventory.models import StockReservation

    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    order = db.scalars(
        select(Order).where(Order.id == order_uuid).with_for_update(of=Order)
    ).first()
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    validate_transition(order.status, OrderStatus.DISPATCHED.value)

    active_reservations = list(
        db.scalars(
            select(StockReservation)
            .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
            .where(
                OrderItem.order_id == order_uuid,
                StockReservation.status == "active",
            )
        ).all()
    )

    if not active_reservations:
        raise AppError(
            code="NO_ACTIVE_RESERVATIONS",
            message=f"Order '{order_uuid}' has no active reservations to dispatch.",
            status_code=400,
        )

    for res in active_reservations:
        inventory_service.dispatch_reservation(
            db=db,
            reservation_id=res.id,
            actor_id=actor_uuid,
            commit=False,
        )

    old_status = order.status
    order.status = OrderStatus.DISPATCHED.value

    # Auto-generate Invoice on dispatch if not yet issued (Milestone M5 / T-052)
    existing_inv = db.scalar(select(Invoice).where(Invoice.order_id == order_uuid))
    if not existing_inv:
        auto_inv = Invoice(
            id=uuid.uuid4(),
            order_id=order_uuid,
            customer_id=order.customer_id,
            invoice_number=generate_invoice_number(),
            total_amount=order.total_amount,
            currency=order.currency,
            payment_status="unpaid",
            notes="Auto-generated on order dispatch",
        )
        db.add(auto_inv)

    # Auto-create Shipment if carrier provided
    if carrier:
        shipment = Shipment(
            id=uuid.uuid4(),
            order_id=order_uuid,
            shipment_number=generate_shipment_number(),
            carrier=carrier,
            tracking_ref=tracking_ref,
            status="in_transit",
            dispatched_at=datetime.now(UTC),
        )
        db.add(shipment)
        event = ShipmentEvent(
            id=uuid.uuid4(),
            shipment_id=shipment.id,
            event_type="dispatched",
            description=f"Consignment dispatched via {carrier}. Tracking: {tracking_ref or 'N/A'}",
            location="Warehouse Facility",
        )
        db.add(event)

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="DISPATCH_ORDER",
            entity="orders",
            entity_id=order.id,
            before={"status": old_status},
            after={
                "status": OrderStatus.DISPATCHED.value,
                "carrier": carrier,
                "tracking_ref": tracking_ref,
            },
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(order)
    return order


def cancel_order(
    db: Session,
    order_id: uuid.UUID | str,
    reason: str | None = None,
    actor_id: uuid.UUID | str | None = None,
    is_staff: bool = False,
    customer_id: uuid.UUID | str | None = None,
    commit: bool = True,
) -> Order:
    """Cancel order, release active reservations exactly once via CAS,
    and record stock_movements (B6).
    """
    from app.inventory import service as inventory_service
    from app.inventory.models import StockReservation

    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None
    cust_uuid = uuid.UUID(str(customer_id)) if customer_id else None

    order = db.scalars(
        select(Order).where(Order.id == order_uuid).with_for_update(of=Order)
    ).first()
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    normalized_status = order.status.strip().lower()

    # Disallow terminal or dispatched states
    if normalized_status in (
        OrderStatus.DISPATCHED.value,
        OrderStatus.DELIVERED.value,
        OrderStatus.RETURNED.value,
        OrderStatus.CANCELLED.value,
    ):
        raise AppError(
            code="ILLEGAL_STATE_TRANSITION",
            message=f"Cannot cancel order in '{normalized_status}' status.",
            status_code=400,
            details={
                "current_status": normalized_status,
                "target_status": OrderStatus.CANCELLED.value,
            },
        )

    # Customer permission check: must own order and cannot cancel once packed
    if not is_staff:
        if cust_uuid is not None and order.customer_id != cust_uuid:
            raise AppError("FORBIDDEN", "You do not have permission to cancel this order.", 403)
        if normalized_status == OrderStatus.PACKED.value:
            raise AppError(
                "FORBIDDEN",
                "Customers cannot cancel an order once packed. Please contact support.",
                403,
            )

    validate_transition(normalized_status, OrderStatus.CANCELLED.value)

    # Release any active reservations exactly once via CAS (B6)
    active_reservations = list(
        db.scalars(
            select(StockReservation)
            .join(OrderItem, StockReservation.order_item_id == OrderItem.id)
            .where(
                OrderItem.order_id == order_uuid,
                StockReservation.status == "active",
            )
        ).all()
    )

    for res in active_reservations:
        inventory_service.cancel_reservation(
            db=db,
            reservation_id=res.id,
            actor_id=actor_uuid,
            commit=False,
        )

    old_status = order.status
    order.status = OrderStatus.CANCELLED.value

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="CANCEL_ORDER",
            entity="orders",
            entity_id=order.id,
            before={"status": old_status},
            after={"status": OrderStatus.CANCELLED.value, "reason": reason},
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(order)
    return order


def return_order(
    db: Session,
    order_id: uuid.UUID | str,
    reason: str,
    actor_id: uuid.UUID | str | None = None,
    commit: bool = True,
) -> Order:
    """Mark order as returned. Quarantines returned goods without
    modifying sellable stock balances (B7).
    """
    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    order = db.scalars(
        select(Order).where(Order.id == order_uuid).with_for_update(of=Order)
    ).first()
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    validate_transition(order.status, OrderStatus.RETURNED.value)

    if not reason or not reason.strip():
        raise AppError("INVALID_RETURN_REASON", "A valid return reason is required.", 400)

    # Invariant B7: Goods are quarantined. on_hand is NOT incremented; no call to receive_stock!
    old_status = order.status
    order.status = OrderStatus.RETURNED.value

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="RETURN_ORDER",
            entity="orders",
            entity_id=order.id,
            before={"status": old_status},
            after={"status": OrderStatus.RETURNED.value, "reason": reason.strip()},
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(order)
    return order


# -----------------------------------------------------------------------------
# Milestone M5 / T-052: Invoices and Shipments Service Methods
# -----------------------------------------------------------------------------


def generate_invoice_number() -> str:
    """Generate unique human-readable invoice code."""
    ts = datetime.now(UTC).strftime("%Y%m%d")
    unique_suffix = uuid.uuid4().hex[:8].upper()
    return f"INV-{ts}-{unique_suffix}"


def generate_shipment_number() -> str:
    """Generate unique human-readable shipment consignment code."""
    ts = datetime.now(UTC).strftime("%Y%m%d")
    unique_suffix = uuid.uuid4().hex[:8].upper()
    return f"SHP-{ts}-{unique_suffix}"


def create_invoice_for_order(
    db: Session,
    order_id: uuid.UUID | str,
    notes: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> Invoice:
    """Issue a billing invoice for an existing order (Milestone M5 / T-052)."""
    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    order = db.scalar(select(Order).where(Order.id == order_uuid))
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    existing = db.scalar(select(Invoice).where(Invoice.order_id == order_uuid))
    if existing:
        return existing

    invoice = Invoice(
        id=uuid.uuid4(),
        order_id=order.id,
        customer_id=order.customer_id,
        invoice_number=generate_invoice_number(),
        total_amount=order.total_amount,
        currency=order.currency,
        payment_status="unpaid",
        notes=notes,
    )
    db.add(invoice)

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="CREATE_INVOICE",
            entity="invoices",
            entity_id=invoice.id,
            before=None,
            after={
                "invoice_number": invoice.invoice_number,
                "order_id": str(order.id),
                "total_amount": str(invoice.total_amount),
            },
        )

    db.commit()
    db.refresh(invoice)
    return invoice


def record_invoice_payment(
    db: Session,
    invoice_id: uuid.UUID | str,
    amount: Decimal | int | float,
    payment_method: str = "simulated_upi",
    reference: str | None = None,
    notes: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> Invoice:
    """Record a payment for an invoice.

    Payments are simulated; card data is never accepted or stored (Rule 91, D-009).
    """
    inv_uuid = uuid.UUID(str(invoice_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None
    pay_amount = Decimal(str(amount)).quantize(Decimal("0.01"))

    invoice = db.scalar(select(Invoice).where(Invoice.id == inv_uuid).with_for_update())
    if not invoice:
        raise AppError("INVOICE_NOT_FOUND", f"Invoice '{inv_uuid}' was not found.", 404)

    if invoice.payment_status == "paid":
        raise AppError("ALREADY_PAID", f"Invoice '{invoice.invoice_number}' is already paid.", 400)

    if pay_amount < invoice.total_amount:
        raise AppError(
            "INSUFFICIENT_PAYMENT",
            f"Payment of {pay_amount} is less than invoice total {invoice.total_amount}.",
            400,
        )

    old_status = invoice.payment_status
    invoice.payment_status = "paid"
    invoice.payment_method = payment_method
    invoice.payment_reference = reference or f"SIM-PAY-{uuid.uuid4().hex[:8].upper()}"
    invoice.paid_at = datetime.now(UTC)
    if notes:
        invoice.notes = f"{invoice.notes or ''}; {notes}".strip("; ")

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="RECORD_PAYMENT",
            entity="invoices",
            entity_id=invoice.id,
            before={"payment_status": old_status},
            after={
                "payment_status": "paid",
                "payment_method": invoice.payment_method,
                "payment_reference": invoice.payment_reference,
                "amount": str(pay_amount),
            },
        )

    db.commit()
    db.refresh(invoice)
    return invoice


def get_invoice_by_id(db: Session, invoice_id: uuid.UUID | str) -> Invoice | None:
    """Retrieve an invoice by UUID."""
    inv_uuid = uuid.UUID(str(invoice_id))
    return db.scalar(select(Invoice).where(Invoice.id == inv_uuid))


def get_invoice_by_order_id(db: Session, order_id: uuid.UUID | str) -> Invoice | None:
    """Retrieve an invoice by its associated Order UUID."""
    order_uuid = uuid.UUID(str(order_id))
    return db.scalar(select(Invoice).where(Invoice.order_id == order_uuid))


def list_invoices(
    db: Session,
    customer_id: uuid.UUID | None = None,
    payment_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Invoice]:
    """List invoices with optional customer ownership and payment status filter."""
    stmt = select(Invoice)
    if customer_id is not None:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if payment_status is not None:
        stmt = stmt.where(Invoice.payment_status == payment_status.strip().lower())
    stmt = stmt.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def create_shipment(
    db: Session,
    order_id: uuid.UUID | str,
    carrier: str,
    tracking_ref: str | None = None,
    notes: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> Shipment:
    """Create a consignment shipment record for an order (Milestone M5 / T-052)."""
    order_uuid = uuid.UUID(str(order_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    order = db.scalar(select(Order).where(Order.id == order_uuid))
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_uuid}' was not found.", 404)

    shipment = Shipment(
        id=uuid.uuid4(),
        order_id=order.id,
        shipment_number=generate_shipment_number(),
        carrier=carrier,
        tracking_ref=tracking_ref,
        status="manifested",
        notes=notes,
    )
    db.add(shipment)
    db.flush()

    event = ShipmentEvent(
        id=uuid.uuid4(),
        shipment_id=shipment.id,
        event_type="manifested",
        description=f"Shipment created with carrier {carrier}",
        location="Packaging Center",
    )
    db.add(event)

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="CREATE_SHIPMENT",
            entity="shipments",
            entity_id=shipment.id,
            before=None,
            after={
                "shipment_number": shipment.shipment_number,
                "carrier": carrier,
                "tracking_ref": tracking_ref,
            },
        )

    db.commit()
    db.refresh(shipment)
    return shipment


def update_shipment_status(
    db: Session,
    shipment_id: uuid.UUID | str,
    status: str,
    location: str | None = None,
    description: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> Shipment:
    """Update shipment tracking status and record timeline event."""
    ship_uuid = uuid.UUID(str(shipment_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None
    target_status = status.strip().lower()

    valid_statuses = {
        "manifested",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "failed",
        "returned",
    }
    if target_status not in valid_statuses:
        raise AppError(
            "INVALID_SHIPMENT_STATUS",
            f"Invalid status '{target_status}'. Must be one of: {sorted(valid_statuses)}.",
            400,
        )

    shipment = db.scalar(
        select(Shipment)
        .options(joinedload(Shipment.events))
        .where(Shipment.id == ship_uuid)
        .with_for_update(of=Shipment)
    )
    if not shipment:
        raise AppError("SHIPMENT_NOT_FOUND", f"Shipment '{ship_uuid}' was not found.", 404)

    old_status = shipment.status
    shipment.status = target_status

    now = datetime.now(UTC)
    if target_status in ("dispatched", "in_transit") and not shipment.dispatched_at:
        shipment.dispatched_at = now
    elif target_status == "delivered":
        shipment.delivered_at = now

    event_desc = description or f"Shipment marked {target_status}"
    event = ShipmentEvent(
        id=uuid.uuid4(),
        shipment_id=shipment.id,
        event_type=target_status,
        description=event_desc,
        location=location or "Transit Hub",
    )
    db.add(event)

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="UPDATE_SHIPMENT_STATUS",
            entity="shipments",
            entity_id=shipment.id,
            before={"status": old_status},
            after={"status": target_status, "location": location},
        )

    db.commit()
    db.refresh(shipment)
    return shipment


def add_shipment_event(
    db: Session,
    shipment_id: uuid.UUID | str,
    event_type: str,
    description: str,
    location: str | None = None,
    actor_id: uuid.UUID | str | None = None,
) -> ShipmentEvent:
    """Append a tracking checkpoint event to a shipment timeline."""
    ship_uuid = uuid.UUID(str(shipment_id))
    actor_uuid = uuid.UUID(str(actor_id)) if actor_id else None

    shipment = db.scalar(select(Shipment).where(Shipment.id == ship_uuid))
    if not shipment:
        raise AppError("SHIPMENT_NOT_FOUND", f"Shipment '{ship_uuid}' was not found.", 404)

    event = ShipmentEvent(
        id=uuid.uuid4(),
        shipment_id=shipment.id,
        event_type=event_type.strip().lower(),
        description=description,
        location=location,
    )
    db.add(event)

    if actor_uuid:
        log_action(
            db,
            actor_id=actor_uuid,
            action="ADD_SHIPMENT_EVENT",
            entity="shipment_events",
            entity_id=event.id,
            before=None,
            after={"shipment_id": str(shipment.id), "event_type": event.event_type},
        )

    db.commit()
    db.refresh(event)
    return event


def get_shipment_by_id(db: Session, shipment_id: uuid.UUID | str) -> Shipment | None:
    """Retrieve shipment by UUID with its timeline events."""
    ship_uuid = uuid.UUID(str(shipment_id))
    return db.scalar(
        select(Shipment).options(joinedload(Shipment.events)).where(Shipment.id == ship_uuid)
    )


def get_shipments_by_order_id(db: Session, order_id: uuid.UUID | str) -> list[Shipment]:
    """Retrieve all shipments created for an order."""
    order_uuid = uuid.UUID(str(order_id))
    return list(
        db.scalars(
            select(Shipment)
            .options(joinedload(Shipment.events))
            .where(Shipment.order_id == order_uuid)
        )
        .unique()
        .all()
    )
