"""Regression tests for transactional integrity of the FEFO reservation path.

These tests pin the behaviour restored by the transaction-boundary fix:

- `reserve_order` allocates every line item inside ONE transaction
  (Invariant B4). A failure on any line leaves ZERO reservations behind.
  The previous implementation used compensating cancels whose cleanup
  failures were swallowed, which could leave orphaned reservations
  permanently holding stock.
- `reserve_order(commit=False)` hands the transaction to the caller, so an
  order and its reservation can be persisted atomically together.
- AI draft approval never fails silently. An order that cannot be reserved is
  reported in the API response and written to the audit log.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
from app.audit.models import AuditLog
from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.inventory import service as inventory_service
from app.inventory.models import (
    InventoryBalance,
    ProductionBatch,
    StockReservation,
    Warehouse,
)
from app.inventory.service import InsufficientStockError
from app.orders.models import Order, OrderItem
from app.orders.service import reserve_order
from tests.conftest import _make_user


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name if user.role else ""})
    return {"Authorization": f"Bearer {token}"}


def _make_warehouse(db: Session) -> Warehouse:
    wh = Warehouse(
        code=f"WH-ATOM-{uuid.uuid4().hex[:6]}",
        name="Atomicity Warehouse",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.flush()
    return wh


def _make_variant(db: Session, name: str) -> ProductVariant:
    cat = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()
    prod = Product(name=name, category_id=cat.id)
    db.add(prod)
    db.flush()
    var = ProductVariant(product_id=prod.id, sku=f"SKU-{uuid.uuid4().hex[:6]}", pack_size_g=250)
    db.add(var)
    db.flush()
    return var


def _make_stock(db: Session, wh: Warehouse, var: ProductVariant, qty: str) -> InventoryBalance:
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"BAT-{uuid.uuid4().hex[:6]}",
        manufactured_at=date.today() - timedelta(days=10),
        expiry_date=date.today() + timedelta(days=90),
        status="released",
    )
    db.add(batch)
    db.flush()
    bal = InventoryBalance(
        warehouse_id=wh.id,
        batch_id=batch.id,
        on_hand=Decimal(qty),
        reserved=Decimal("0.000"),
    )
    db.add(bal)
    db.flush()
    return bal


def _make_order(db: Session, dealer: User, lines: list[tuple[ProductVariant, str]]) -> Order:
    order = Order(
        id=uuid.uuid4(),
        order_number=f"ORD-ATOM-{uuid.uuid4().hex[:8].upper()}",
        customer_id=dealer.id,
        order_type="sales",
        status="pending",
        idempotency_key=f"atomic-{uuid.uuid4().hex}",
        total_amount=Decimal("0.00"),
        currency="INR",
    )
    db.add(order)
    db.flush()
    for var, qty in lines:
        db.add(
            OrderItem(
                id=uuid.uuid4(),
                order_id=order.id,
                variant_id=var.id,
                quantity=Decimal(qty),
                unit_price_snapshot=Decimal("100.00"),
                price_list_code="DEALER",
                line_total=Decimal("100.00") * Decimal(qty),
            )
        )
    db.flush()
    return order


def _item_ids(db: Session, order_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(OrderItem.id).where(OrderItem.order_id == order_id)).all())


def test_multi_item_reservation_is_atomic_no_partial_reservation_survives(
    db: Session,
):
    """A failure on the second line leaves ZERO reservations behind (Invariant B4)."""
    dealer = _make_user(db, email=f"atomic-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    wh = _make_warehouse(db)
    var_ok = _make_variant(db, "Atomic Turmeric")
    var_short = _make_variant(db, "Atomic Chilli")

    bal_ok = _make_stock(db, wh, var_ok, "100.000")
    bal_short = _make_stock(db, wh, var_short, "5.000")

    order = _make_order(db, dealer, [(var_ok, "10.000"), (var_short, "10.000")])
    db.commit()

    with pytest.raises(InsufficientStockError):
        reserve_order(db=db, order_id=order.id, actor_id=dealer.id)

    db.expire_all()
    item_ids = _item_ids(db, order.id)
    reservations = db.scalars(
        select(StockReservation).where(StockReservation.order_item_id.in_(item_ids))
    ).all()
    assert reservations == [], "a partial reservation survived a failed multi-item reservation"

    bal_ok_row = db.get(InventoryBalance, bal_ok.id)
    bal_short_row = db.get(InventoryBalance, bal_short.id)
    assert bal_ok_row.reserved == Decimal("0.000"), "an orphaned reservation is still holding stock"
    assert bal_short_row.reserved == Decimal("0.000")
    assert bal_ok_row.on_hand == Decimal("100.000")


def test_reserve_order_commit_false_defers_transaction_to_caller(db: Session):
    """With commit=False the caller owns the transaction and may roll it back."""
    dealer = _make_user(db, email=f"atomic-c-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    wh = _make_warehouse(db)
    var = _make_variant(db, "Atomic Cardamom")
    bal = _make_stock(db, wh, var, "50.000")

    order = _make_order(db, dealer, [(var, "10.000")])
    db.commit()

    reserve_order(db=db, order_id=order.id, actor_id=dealer.id, commit=False)

    # Nothing is durable yet: the caller still owns the transaction.
    db.rollback()
    db.expire_all()

    order_row = db.get(Order, order.id)
    assert order_row.status == "pending", "commit=False must not persist the reservation"

    item_ids = _item_ids(db, order.id)
    reservations = db.scalars(
        select(StockReservation).where(StockReservation.order_item_id.in_(item_ids))
    ).all()
    assert reservations == []

    bal_row = db.get(InventoryBalance, bal.id)
    assert bal_row.reserved == Decimal("0.000")
    assert bal_row.on_hand == Decimal("50.000")


def test_reserve_order_allocates_in_deterministic_variant_order(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    """Line items are allocated in variant_id order.

    All line items of one order now share a single transaction, so their row
    locks are held until the final commit. Processing the lines in a stable
    variant_id order gives every concurrent reservation the same global lock
    order and prevents a deadlock between two orders whose line items are
    listed in opposite order.
    """
    dealer = _make_user(
        db, email=f"atomic-lock-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer"
    )
    wh = _make_warehouse(db)
    variants = [_make_variant(db, f"Atomic Lock {i}") for i in range(3)]
    for var in variants:
        _make_stock(db, wh, var, "100.000")

    # Deliberately list the lines in DESCENDING variant_id order.
    lines = sorted([(var, "10.000") for var in variants], key=lambda pair: pair[0].id, reverse=True)
    order = _make_order(db, dealer, lines)
    db.commit()

    processed: list[uuid.UUID] = []
    real_reserve = inventory_service.reserve_stock_fefo

    def spy(*args, **kwargs):
        processed.append(kwargs["variant_id"])
        return real_reserve(*args, **kwargs)

    monkeypatch.setattr(inventory_service, "reserve_stock_fefo", spy)

    reserve_order(db=db, order_id=order.id, actor_id=dealer.id)

    assert processed == sorted(processed), "line items must be allocated in variant_id order"
    assert len(processed) == 3

    db.expire_all()
    item_ids = _item_ids(db, order.id)
    reservations = db.scalars(
        select(StockReservation).where(StockReservation.order_item_id.in_(item_ids))
    ).all()
    assert len(reservations) == 3
    assert all(r.quantity == Decimal("10.000") for r in reservations)


def test_ai_draft_approval_reports_unfulfillable_order_explicitly(client: TestClient, db: Session):
    """Approval never fails silently: an unfulfillable order is reported and audited."""
    dealer = _make_user(db, email=f"atomic-ai-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    wh = _make_warehouse(db)
    var = _make_variant(db, "Atomic Pepper")
    _make_stock(db, wh, var, "5.000")  # draft asks for 20 units

    draft = OrderDraft(
        customer_id=dealer.id,
        items=[
            {
                "variant_id": str(var.id),
                "sku": var.sku,
                "quantity": 20.0,
                "unit_price": 85.50,
            }
        ],
        total_estimated=Decimal("1710.00"),
        status="draft",
    )
    db.add(draft)
    db.commit()
    draft_id = draft.id

    resp = client.post(f"/api/v1/ai/drafts/{draft_id}/approve", headers=_auth_header(dealer))
    assert resp.status_code == 200
    data = resp.json()
    assert data["reservation_status"] == "not_reserved"
    assert "could not be reserved" in data["reservation_message"]

    db.expire_all()
    order = db.get(Order, uuid.UUID(data["order_id"]))
    assert order is not None, "the order must still be created"
    assert order.status == "pending", "an unreserved order stays in 'pending' status"

    audit = db.scalars(
        select(AuditLog).where(AuditLog.action == "APPROVE_DRAFT_RESERVATION_FAILED")
    ).all()
    assert len(audit) == 1, "the reservation failure must be audited"
    assert audit[0].entity_id == draft_id
    assert audit[0].after is not None
    assert audit[0].after["reservation_status"] == "not_reserved"


def test_ai_draft_approval_reserves_when_stock_available(client: TestClient, db: Session):
    """When stock is available the approval reserves it and reports success."""
    dealer = _make_user(db, email=f"atomic-ai2-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    wh = _make_warehouse(db)
    var = _make_variant(db, "Atomic Cinnamon")
    bal = _make_stock(db, wh, var, "50.000")

    draft = OrderDraft(
        customer_id=dealer.id,
        items=[
            {
                "variant_id": str(var.id),
                "sku": var.sku,
                "quantity": 15.0,
                "unit_price": 85.50,
            }
        ],
        total_estimated=Decimal("1282.50"),
        status="draft",
    )
    db.add(draft)
    db.commit()
    draft_id = draft.id

    resp = client.post(f"/api/v1/ai/drafts/{draft_id}/approve", headers=_auth_header(dealer))
    assert resp.status_code == 200
    data = resp.json()
    assert data["reservation_status"] == "reserved"

    db.expire_all()
    order = db.get(Order, uuid.UUID(data["order_id"]))
    assert order is not None
    assert order.status == "reserved"

    item_ids = _item_ids(db, order.id)
    reservation = db.scalar(
        select(StockReservation).where(StockReservation.order_item_id.in_(item_ids))
    )
    assert reservation is not None
    assert reservation.quantity == Decimal("15.000")
    assert reservation.status == "active"

    bal_row = db.get(InventoryBalance, bal.id)
    assert bal_row.reserved == Decimal("15.000")
    assert bal_row.on_hand == Decimal("50.000")
