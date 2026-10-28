"""Tests for Inventory ORM models and schema constraints (Step 1)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


@pytest.fixture
def test_variant(db: Session) -> ProductVariant:
    """Fixture providing a product variant."""
    cat = Category(name=f"Test Cat {uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name="Test Haldi", category_id=cat.id)
    db.add(prod)
    db.flush()

    variant = ProductVariant(product_id=prod.id, sku=f"SKU-{uuid.uuid4().hex[:6]}", pack_size_g=500)
    db.add(variant)
    db.commit()
    return variant


@pytest.fixture
def test_warehouse(db: Session) -> Warehouse:
    """Fixture providing a test warehouse."""
    wh = Warehouse(
        code=f"WH-{uuid.uuid4().hex[:6]}",
        name="Pune Central Facility",
        is_primary=True,
    )
    db.add(wh)
    db.commit()
    return wh


def test_warehouse_creation_and_unique_code(db: Session, test_warehouse: Warehouse):
    """Warehouse codes must be unique."""
    duplicate = Warehouse(
        code=test_warehouse.code,
        name="Another Facility",
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_production_batch_creation_and_status_constraint(db: Session, test_variant: ProductVariant):
    """Production batches require valid status and expiry >= manufactured_at."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=test_variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        manufactured_at=today,
        expiry_date=today + timedelta(days=365),
        status="released",
    )
    db.add(batch)
    db.commit()

    assert batch.id is not None
    assert batch.status == "released"

    # Test invalid status rejected by check constraint
    invalid_batch = ProductionBatch(
        variant_id=test_variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        manufactured_at=today,
        expiry_date=today + timedelta(days=365),
        status="invalid_status",
    )
    db.add(invalid_batch)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_inventory_balance_check_constraints(
    db: Session, test_warehouse: Warehouse, test_variant: ProductVariant
):
    """InventoryBalance enforces on_hand >= 0, reserved >= 0, and reserved <= on_hand."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=test_variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        manufactured_at=today,
        expiry_date=today + timedelta(days=180),
        status="released",
    )
    db.add(batch)
    db.commit()

    # Valid balance
    balance = InventoryBalance(
        warehouse_id=test_warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal("100.000"),
        reserved=Decimal("20.000"),
    )
    db.add(balance)
    db.commit()

    assert balance.available == Decimal("80.000")

    # Invariant B4: reserved > on_hand must fail check constraint
    balance.reserved = Decimal("150.000")
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_inventory_balance_unique_warehouse_batch(
    db: Session, test_warehouse: Warehouse, test_variant: ProductVariant
):
    """Only one balance row per (warehouse, batch)."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=test_variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        manufactured_at=today,
        expiry_date=today + timedelta(days=180),
        status="released",
    )
    db.add(batch)
    db.commit()

    b1 = InventoryBalance(
        warehouse_id=test_warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal("50.000"),
        reserved=Decimal("0"),
    )
    db.add(b1)
    db.commit()

    b2 = InventoryBalance(
        warehouse_id=test_warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal("10.000"),
        reserved=Decimal("0"),
    )
    db.add(b2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_stock_movement_idempotency_via_operation_key(
    db: Session, test_warehouse: Warehouse, test_variant: ProductVariant
):
    """Duplicate operation_key must fail unique constraint."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=test_variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        manufactured_at=today,
        expiry_date=today + timedelta(days=180),
        status="released",
    )
    db.add(batch)
    db.commit()

    op_key = f"receipt:{batch.id}:{uuid.uuid4()}"
    m1 = StockMovement(
        warehouse_id=test_warehouse.id,
        batch_id=batch.id,
        movement_type="receipt",
        quantity=Decimal("100.000"),
        reference_type="batch_release",
        reference_id=str(batch.id),
        operation_key=op_key,
    )
    db.add(m1)
    db.commit()

    m2 = StockMovement(
        warehouse_id=test_warehouse.id,
        batch_id=batch.id,
        movement_type="receipt",
        quantity=Decimal("100.000"),
        reference_type="batch_release",
        reference_id=str(batch.id),
        operation_key=op_key,
    )
    db.add(m2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_order_and_reservation_linkage(
    db: Session, test_warehouse: Warehouse, test_variant: ProductVariant
):
    """StockReservation cleanly links OrderItem and InventoryBalance."""
    today = date.today()
    batch = ProductionBatch(
        variant_id=test_variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:6]}",
        manufactured_at=today,
        expiry_date=today + timedelta(days=180),
        status="released",
    )
    db.add(batch)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=test_warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal("50.000"),
        reserved=Decimal("10.000"),
    )
    db.add(balance)

    order = Order(order_number=f"ORD-{uuid.uuid4().hex[:8]}")
    db.add(order)
    db.flush()

    item = OrderItem(order_id=order.id, variant_id=test_variant.id, quantity=Decimal("10.000"))
    db.add(item)
    db.flush()

    reservation = StockReservation(
        order_item_id=item.id,
        balance_id=balance.id,
        quantity=Decimal("10.000"),
        status="active",
    )
    db.add(reservation)
    db.commit()

    saved_res = db.scalar(select(StockReservation).where(StockReservation.id == reservation.id))
    assert saved_res is not None
    assert saved_res.status == "active"
    assert saved_res.order_item.order.order_number == order.order_number
    assert saved_res.balance.warehouse.code == test_warehouse.code
