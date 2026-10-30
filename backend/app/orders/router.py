"""Orders HTTP Router (Milestone M1 / T-050).

Endpoints:
- POST /api/v1/orders/      : Create order with price snapshot & idempotency replay
- GET  /api/v1/orders/      : List orders (scoped by customer for users; all for staff)
- GET  /api/v1/orders/{id}  : Retrieve order with ownership verification
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_roles
from app.auth.models import User
from app.core.db import get_db
from app.core.errors import AppError
from app.orders import service
from app.orders.schemas import (
    InvoiceCreate,
    InvoiceRead,
    OrderCancelRequest,
    OrderCreate,
    OrderDispatchRequest,
    OrderRead,
    OrderReserveRequest,
    OrderReturnRequest,
    PaymentRecordRequest,
    ShipmentCreate,
    ShipmentEventCreate,
    ShipmentEventRead,
    ShipmentRead,
    ShipmentStatusUpdateRequest,
)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

STAFF_ROLES = {"owner", "warehouse", "factory"}
CUSTOMER_ROLES = {"consumer", "dealer", "distributor"}
ORDER_CREATION_ROLES = {"consumer", "dealer", "distributor", "owner"}
FULFILLMENT_ROLES = {"owner", "warehouse"}

require_fulfillment_staff = require_roles("owner", "warehouse")


@router.post(
    "",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new order with price snapshot (B8, B12, Rule 48)",
)
def create_order(
    payload: OrderCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*ORDER_CREATION_ROLES))],
    idempotency_key_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OrderRead:
    """Create an order or replay an existing order with identical idempotency key.

    RBAC Policy:
    - Allowed roles: consumer, dealer, distributor, owner
    - Forbidden roles: warehouse, factory (HTTP 403)
    - Unauthenticated: HTTP 401
    """
    effective_idempotency_key = idempotency_key_header or payload.idempotency_key

    order, _ = service.create_order(
        db=db,
        user=current_user,
        payload=payload,
        idempotency_key=effective_idempotency_key,
    )
    return OrderRead.model_validate(order)


@router.get(
    "",
    response_model=list[OrderRead],
    summary="List orders with role-based scoping (Invariant B10)",
)
def list_orders(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: str | None = Query(None, description="Filter by order status"),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    customer_id: uuid.UUID | None = Query(None, description="Filter by customer UUID (staff only)"),
) -> list[OrderRead]:
    """List orders visible to the caller:

    - Customers (consumer, dealer, distributor) ONLY see their own orders.
    - Staff (owner, warehouse, factory) see all orders, with optional customer_id filter.
    """
    user_role = current_user.role.name if current_user.role else ""

    if user_role in CUSTOMER_ROLES:
        effective_customer_id = current_user.id
    elif user_role in STAFF_ROLES:
        effective_customer_id = customer_id
    else:
        raise AppError("FORBIDDEN", f"Role '{user_role}' cannot list orders.", 403)

    orders = service.list_orders(
        db=db,
        customer_id=effective_customer_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [OrderRead.model_validate(o) for o in orders]


@router.get(
    "/{order_id}",
    response_model=OrderRead,
    summary="Get order by ID with ownership verification",
)
def get_order(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OrderRead:
    """Retrieve an order by UUID.

    - Customers can only retrieve their own orders (raises 403 on unowned).
    - Staff can retrieve any order.
    - Raises 404 if order does not exist.
    """
    order = service.get_order_by_id(db, order_id)
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_id}' was not found.", 404)

    user_role = current_user.role.name if current_user.role else ""
    if user_role not in STAFF_ROLES and order.customer_id != current_user.id:
        raise AppError("FORBIDDEN", "You do not have permission to view this order.", 403)

    return OrderRead.model_validate(order)


class OrderStatusUpdateRequest(BaseModel):
    """Payload for administrative / delivery order status update."""

    status: str = Field(..., description="Target order status")


@router.post(
    "/{order_id}/reserve",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Reserve stock for order items via FEFO (Invariants B1, B2, B3, B4)",
)
def reserve_order(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
    payload: Annotated[OrderReserveRequest | None, Body()] = None,
) -> OrderRead:
    """Reserve inventory for order line items using FEFO batch allocation."""
    warehouse_id = payload.warehouse_id if payload else None
    order = service.reserve_order(
        db=db,
        order_id=order_id,
        warehouse_id=warehouse_id,
        actor_id=current_user.id,
    )
    return OrderRead.model_validate(order)


@router.post(
    "/{order_id}/pack",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Transition order from reserved to packed",
)
def pack_order(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> OrderRead:
    """Transition an order to packed status once items have been picked and packaged."""
    order = service.pack_order(
        db=db,
        order_id=order_id,
        actor_id=current_user.id,
    )
    return OrderRead.model_validate(order)


@router.post(
    "/{order_id}/dispatch",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Dispatch order and record inventory movements (Invariants B1, B5)",
)
def dispatch_order(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
    payload: Annotated[OrderDispatchRequest | None, Body()] = None,
) -> OrderRead:
    """Dispatch packed order, decrement on_hand and reserved balances,
    and append stock movements.
    """
    carrier = payload.carrier if payload else None
    tracking_ref = payload.tracking_ref if payload else None
    order = service.dispatch_order(
        db=db,
        order_id=order_id,
        carrier=carrier,
        tracking_ref=tracking_ref,
        actor_id=current_user.id,
    )
    return OrderRead.model_validate(order)


@router.post(
    "/{order_id}/cancel",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Cancel order and release inventory reservations (Invariant B6)",
)
def cancel_order(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    payload: Annotated[OrderCancelRequest | None, Body()] = None,
) -> OrderRead:
    """Cancel an order and atomically release any active inventory reservations."""
    user_role = current_user.role.name if current_user.role else ""
    is_staff = user_role in FULFILLMENT_ROLES
    is_customer = user_role in CUSTOMER_ROLES

    if not is_staff and not is_customer:
        raise AppError("FORBIDDEN", f"Role '{user_role}' cannot cancel orders.", 403)

    reason = payload.reason if payload else None
    order = service.cancel_order(
        db=db,
        order_id=order_id,
        reason=reason,
        actor_id=current_user.id,
        is_staff=is_staff,
        customer_id=current_user.id if not is_staff else None,
    )
    return OrderRead.model_validate(order)


@router.post(
    "/{order_id}/return",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Record order return and quarantine goods (Invariant B7)",
)
def return_order(
    order_id: uuid.UUID,
    payload: OrderReturnRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> OrderRead:
    """Mark a delivered order as returned."""
    order = service.return_order(
        db=db,
        order_id=order_id,
        reason=payload.reason,
        actor_id=current_user.id,
    )
    return OrderRead.model_validate(order)


@router.post(
    "/{order_id}/deliver",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Transition order from dispatched to delivered",
)
def deliver_order(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> OrderRead:
    """Transition a dispatched order to delivered status upon confirmed carrier delivery."""
    order = service.update_order_status(
        db=db,
        order_id=order_id,
        target_status=service.OrderStatus.DELIVERED.value,
        actor_id=current_user.id,
    )
    return OrderRead.model_validate(order)


@router.patch(
    "/{order_id}/status",
    response_model=OrderRead,
    status_code=status.HTTP_200_OK,
    summary="Update order status (administrative transition)",
)
def update_status(
    order_id: uuid.UUID,
    payload: OrderStatusUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> OrderRead:
    """Update order status directly. Only 'delivered' is permitted via PATCH.

    For reserve, pack, dispatch, cancel and return, dedicated endpoints must be used
    so inventory balance and ledger movements are correctly executed.
    """
    target = payload.status.strip().lower()
    if target != service.OrderStatus.DELIVERED.value:
        raise AppError(
            code="USE_DEDICATED_ENDPOINT",
            message=(
                f"Status '{target}' cannot be updated via PATCH /orders/{{id}}/status. "
                "Use the dedicated lifecycle endpoint: "
                "/reserve, /pack, /dispatch, /cancel, or /return."
            ),
            status_code=400,
            details={
                "target_status": target,
                "allowed": [service.OrderStatus.DELIVERED.value],
            },
        )

    order = service.update_order_status(
        db=db,
        order_id=order_id,
        target_status=service.OrderStatus.DELIVERED.value,
        actor_id=current_user.id,
    )
    return OrderRead.model_validate(order)


# -----------------------------------------------------------------------------
# Milestone M5 / T-052: Invoices Endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/{order_id}/invoices",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an invoice for an order (Milestone M5 / T-052)",
)
def create_order_invoice(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
    payload: Annotated[InvoiceCreate | None, Body()] = None,
) -> InvoiceRead:
    """Issue a billing invoice for an order. Fulfillment staff only."""
    notes = payload.notes if payload else None
    invoice = service.create_invoice_for_order(
        db=db,
        order_id=order_id,
        notes=notes,
        actor_id=current_user.id,
    )
    return InvoiceRead.model_validate(invoice)


@router.get(
    "/{order_id}/invoices",
    response_model=InvoiceRead,
    summary="Get invoice for an order",
)
def get_order_invoice(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> InvoiceRead:
    """Retrieve invoice for an order. Customers can only view own invoices."""
    order = service.get_order_by_id(db, order_id)
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_id}' was not found.", 404)

    user_role = current_user.role.name if current_user.role else ""
    if user_role not in STAFF_ROLES and order.customer_id != current_user.id:
        raise AppError("FORBIDDEN", "You do not have permission to view this invoice.", 403)

    invoice = service.get_invoice_by_order_id(db, order_id)
    if not invoice:
        raise AppError("INVOICE_NOT_FOUND", f"No invoice issued for order '{order_id}'.", 404)

    return InvoiceRead.model_validate(invoice)


@router.get(
    "/invoices/all",
    response_model=list[InvoiceRead],
    summary="List invoices with role scoping",
)
def list_invoices(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    payment_status: str | None = Query(None, description="Filter by payment status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[InvoiceRead]:
    """List invoices. Customers see only their own; staff see all."""
    user_role = current_user.role.name if current_user.role else ""
    effective_customer_id = current_user.id if user_role in CUSTOMER_ROLES else None

    invoices = service.list_invoices(
        db=db,
        customer_id=effective_customer_id,
        payment_status=payment_status,
        limit=limit,
        offset=offset,
    )
    return [InvoiceRead.model_validate(inv) for inv in invoices]


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceRead,
    summary="Get invoice by ID",
)
def get_invoice(
    invoice_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> InvoiceRead:
    """Retrieve invoice by UUID."""
    invoice = service.get_invoice_by_id(db, invoice_id)
    if not invoice:
        raise AppError("INVOICE_NOT_FOUND", f"Invoice '{invoice_id}' was not found.", 404)

    user_role = current_user.role.name if current_user.role else ""
    if user_role not in STAFF_ROLES and invoice.customer_id != current_user.id:
        raise AppError("FORBIDDEN", "You do not have permission to view this invoice.", 403)

    return InvoiceRead.model_validate(invoice)


@router.post(
    "/invoices/{invoice_id}/pay",
    response_model=InvoiceRead,
    summary="Record simulated payment for an invoice (Rule 91, D-009)",
)
def pay_invoice(
    invoice_id: uuid.UUID,
    payload: PaymentRecordRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> InvoiceRead:
    """Record simulated payment for an invoice.

    Payments are simulated; no card data is stored or accepted (Rule 91, D-009).
    """
    invoice = service.get_invoice_by_id(db, invoice_id)
    if not invoice:
        raise AppError("INVOICE_NOT_FOUND", f"Invoice '{invoice_id}' was not found.", 404)

    user_role = current_user.role.name if current_user.role else ""
    if user_role not in STAFF_ROLES and invoice.customer_id != current_user.id:
        raise AppError("FORBIDDEN", "You do not have permission to pay this invoice.", 403)

    updated_invoice = service.record_invoice_payment(
        db=db,
        invoice_id=invoice_id,
        amount=payload.amount,
        payment_method=payload.payment_method,
        reference=payload.payment_reference,
        notes=payload.notes,
        actor_id=current_user.id,
    )
    return InvoiceRead.model_validate(updated_invoice)


# -----------------------------------------------------------------------------
# Milestone M5 / T-052: Shipments Endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/{order_id}/shipments",
    response_model=ShipmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create shipment for an order (Milestone M5 / T-052)",
)
def create_shipment(
    order_id: uuid.UUID,
    payload: ShipmentCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> ShipmentRead:
    """Create a logistics consignment shipment for an order."""
    shipment = service.create_shipment(
        db=db,
        order_id=order_id,
        carrier=payload.carrier,
        tracking_ref=payload.tracking_ref,
        notes=payload.notes,
        actor_id=current_user.id,
    )
    return ShipmentRead.model_validate(shipment)


@router.get(
    "/{order_id}/shipments",
    response_model=list[ShipmentRead],
    summary="Get shipments for an order",
)
def get_order_shipments(
    order_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ShipmentRead]:
    """Retrieve all shipments for an order with event timeline."""
    order = service.get_order_by_id(db, order_id)
    if not order:
        raise AppError("ORDER_NOT_FOUND", f"Order '{order_id}' was not found.", 404)

    user_role = current_user.role.name if current_user.role else ""
    if user_role not in STAFF_ROLES and order.customer_id != current_user.id:
        raise AppError("FORBIDDEN", "You do not have permission to view these shipments.", 403)

    shipments = service.get_shipments_by_order_id(db, order_id)
    return [ShipmentRead.model_validate(s) for s in shipments]


@router.get(
    "/shipments/{shipment_id}",
    response_model=ShipmentRead,
    summary="Get shipment details by ID",
)
def get_shipment(
    shipment_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ShipmentRead:
    """Retrieve shipment details and timeline events."""
    shipment = service.get_shipment_by_id(db, shipment_id)
    if not shipment:
        raise AppError("SHIPMENT_NOT_FOUND", f"Shipment '{shipment_id}' was not found.", 404)

    order = service.get_order_by_id(db, shipment.order_id)
    user_role = current_user.role.name if current_user.role else ""
    if user_role not in STAFF_ROLES and (not order or order.customer_id != current_user.id):
        raise AppError("FORBIDDEN", "You do not have permission to view this shipment.", 403)

    return ShipmentRead.model_validate(shipment)


@router.patch(
    "/shipments/{shipment_id}/status",
    response_model=ShipmentRead,
    summary="Update shipment status and append checkpoint event",
)
def update_shipment_status(
    shipment_id: uuid.UUID,
    payload: ShipmentStatusUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> ShipmentRead:
    """Update shipment status and add checkpoint event. Staff only."""
    shipment = service.update_shipment_status(
        db=db,
        shipment_id=shipment_id,
        status=payload.status,
        location=payload.location,
        description=payload.description,
        actor_id=current_user.id,
    )
    return ShipmentRead.model_validate(shipment)


@router.post(
    "/shipments/{shipment_id}/events",
    response_model=ShipmentEventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a checkpoint event to a shipment",
)
def add_shipment_event(
    shipment_id: uuid.UUID,
    payload: ShipmentEventCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_fulfillment_staff)],
) -> ShipmentEventRead:
    """Add a tracking event to a shipment timeline. Staff only."""
    event = service.add_shipment_event(
        db=db,
        shipment_id=shipment_id,
        event_type=payload.event_type,
        description=payload.description,
        location=payload.location,
        actor_id=current_user.id,
    )
    return ShipmentEventRead.model_validate(event)
