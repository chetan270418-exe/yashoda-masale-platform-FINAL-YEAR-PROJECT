"""End-to-end factory stock tests (Milestone M3 / T-034).

The milestone acceptance condition: a batch goes from plan to Released and creates sellable
stock, and only a released batch may ever own stock.

Covers:
- the happy path plan -> draft -> in_production -> quality_pending -> released -> stock;
- a batch that is not released never creates stock (B1);
- a released batch with stock can be reserved with FEFO, a batch that is not released can
  not, and expired or held batches are excluded from FEFO (B1, B2, B3);
- every illegal batch state transition is rejected;
- quality: a batch cannot be released while a check is outstanding or failed, and a
  placeholder parameter without limits yields "passed = None";
- role permissions.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.core.errors import AppError
from app.inventory.models import (
    InventoryBalance,
    Order,
    OrderItem,
    ProductionBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)
from app.inventory.service import InsufficientStockError, reserve_stock_fefo
from app.main import app as main_app
from app.procurement import router as procurement_router
from app.production import router as production_router
from app.production import service
from app.production.models import QualityParameter
from app.production.schemas import QualityParameterCreate
from app.production.state_machine import ALLOWED_TRANSITIONS, BATCH_STATUS_VALUES, BatchStatus
from tests.conftest import _make_user


def _ensure_routers_registered() -> None:
    """Register the milestone routers when app/main.py does not include them yet."""
    registered = {getattr(route, "path", "") for route in main_app.routes}
    if not any(path.startswith("/api/v1/production") for path in registered):
        main_app.include_router(production_router.router)
    if not any(path.startswith("/api/v1/procurement") for path in registered):
        main_app.include_router(procurement_router.router)


_ensure_routers_registered()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name if user.role else ""})
    return {"Authorization": f"Bearer {token}"}


#: Every (current, target) pair that the state machine must reject.
ILLEGAL_TRANSITIONS = [
    (current, target)
    for current in ALLOWED_TRANSITIONS
    for target in BATCH_STATUS_VALUES
    if target not in ALLOWED_TRANSITIONS[current]
]


@pytest.fixture()
def factory_user(db: Session) -> User:
    return _make_user(db, email=f"factory-{uuid.uuid4().hex[:6]}@example.com", role_name="factory")


@pytest.fixture()
def dealer_user(db: Session) -> User:
    return _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")


@pytest.fixture()
def consumer_user(db: Session) -> User:
    return _make_user(
        db, email=f"consumer-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )


@pytest.fixture()
def variant(db: Session) -> ProductVariant:
    category = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(category)
    db.flush()
    product = Product(name=f"Turmeric Powder {uuid.uuid4().hex[:4]}", category_id=category.id)
    db.add(product)
    db.flush()
    sku_variant = ProductVariant(
        product_id=product.id, sku=f"SKU-STOCK-{uuid.uuid4().hex[:8]}", pack_size_g=500
    )
    db.add(sku_variant)
    db.commit()
    return sku_variant


@pytest.fixture()
def warehouse(db: Session) -> Warehouse:
    depot = Warehouse(code=f"WH-{uuid.uuid4().hex[:8]}", name="Pune Central Depot", is_primary=True)
    db.add(depot)
    db.commit()
    return depot


@pytest.fixture()
def order_item(db: Session, variant: ProductVariant) -> OrderItem:
    order = Order(order_number=f"ORD-{uuid.uuid4().hex[:10]}")
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, variant_id=variant.id, quantity=Decimal("100.000"))
    db.add(item)
    db.commit()
    return item


@pytest.fixture()
def quality_parameter(db: Session, factory_user: User) -> QualityParameter:
    """A parameter with a synthetic test limit.

    The limits are TEST DATA only: the application never seeds a quality limit, because
    no sponsor limit exists yet (rules.md section 12).
    """
    return service.create_quality_parameter(
        db,
        QualityParameterCreate(
            code="MOISTURE_TEST",
            name="Moisture content (synthetic test limit)",
            unit="%",
            min_value=Decimal("5.0000"),
            max_value=Decimal("10.0000"),
        ),
        actor_id=factory_user.id,
    )


def _make_batch(
    db: Session,
    variant: ProductVariant,
    factory_user: User,
    *,
    status: str = BatchStatus.DRAFT.value,
    actual_qty: Decimal | None = Decimal("100.000"),
    days_to_expiry: int = 180,
) -> ProductionBatch:
    """Create a batch directly in the given status (test setup helper)."""
    batch = ProductionBatch(
        id=uuid.uuid4(),
        variant_id=variant.id,
        batch_code=f"STOCK-BATCH-{uuid.uuid4().hex[:8]}",
        manufactured_at=date.today() - timedelta(days=5),
        expiry_date=date.today() + timedelta(days=days_to_expiry),
        status=status,
        planned_qty=Decimal("120.000"),
        actual_qty=actual_qty,
    )
    db.add(batch)
    db.commit()
    return batch


def _plan_and_batch(db: Session, variant: ProductVariant, factory_user: User):
    """Create a plan and a batch in draft status from it."""
    plan = service.create_production_plan(
        db,
        variant_id=variant.id,
        planned_qty=Decimal("120.000"),
        planned_start=date.today(),
        planned_end=date.today() + timedelta(days=2),
        actor_id=factory_user.id,
    )
    batch = service.create_batch_from_plan(
        db,
        plan_id=plan.id,
        batch_code=f"FLOW-{uuid.uuid4().hex[:8]}",
        manufactured_at=date.today(),
        expiry_date=date.today() + timedelta(days=180),
        actor_id=factory_user.id,
        actual_qty=Decimal("100.000"),
    )
    return plan, batch


# ── 1. The happy path: plan -> released -> sellable stock ────────────────────


def test_batch_goes_from_plan_to_released_and_creates_stock(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    factory_user: User,
    quality_parameter,
):
    """A batch travels the whole factory flow and only then becomes sellable stock."""
    plan, batch = _plan_and_batch(db, variant, factory_user)

    assert plan.status == "in_progress"
    assert batch.status == BatchStatus.DRAFT.value
    assert len(service.list_stages(db, batch.id)) == 6

    # The stages move with the batch.
    service.advance_batch(
        db, batch_id=batch.id, to_status="in_production", actor_id=factory_user.id
    )
    service.advance_batch(
        db, batch_id=batch.id, to_status="quality_pending", actor_id=factory_user.id
    )

    # Quality: a passing check, then the release decision.
    check = service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=quality_parameter.id,
        measured_value=Decimal("7.5000"),
        actor_id=factory_user.id,
    )
    assert check.passed is True

    service.record_quality_decision(
        db, batch_id=batch.id, decision="released", remarks="Released", actor_id=factory_user.id
    )
    db.refresh(batch)
    assert batch.status == BatchStatus.RELEASED.value

    # The release creates the stock.
    balance = service.release_batch_to_stock(
        db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
    )

    assert balance.on_hand == Decimal("100.000")
    assert balance.reserved == Decimal("0")
    assert balance.available == Decimal("100.000")

    movement = db.scalar(
        select(StockMovement).where(
            StockMovement.batch_id == batch.id,
            StockMovement.warehouse_id == warehouse.id,
        )
    )
    assert movement is not None
    assert movement.movement_type == "receipt"
    assert movement.quantity == Decimal("100.000")
    assert movement.balance_id == balance.id

    db.refresh(batch)
    assert batch.status == BatchStatus.RELEASED.value


def test_released_batch_can_be_reserved_with_fefo(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    order_item: OrderItem,
    factory_user: User,
    quality_parameter,
):
    """A released batch with stock can be reserved by the FEFO engine (B1, B3)."""
    _, batch = _plan_and_batch(db, variant, factory_user)
    service.advance_batch(
        db, batch_id=batch.id, to_status="in_production", actor_id=factory_user.id
    )
    service.advance_batch(
        db, batch_id=batch.id, to_status="quality_pending", actor_id=factory_user.id
    )
    service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=quality_parameter.id,
        measured_value=Decimal("6.0000"),
        actor_id=factory_user.id,
    )
    service.record_quality_decision(
        db, batch_id=batch.id, decision="released", remarks=None, actor_id=factory_user.id
    )
    service.release_batch_to_stock(
        db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
    )

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("40.000"),
        order_item_id=order_item.id,
        actor_id=factory_user.id,
    )

    assert len(reservations) == 1
    assert reservations[0].quantity == Decimal("40.000")
    assert reservations[0].status == "active"

    balance = db.scalar(
        select(InventoryBalance).where(
            InventoryBalance.batch_id == batch.id,
            InventoryBalance.warehouse_id == warehouse.id,
        )
    )
    assert balance.on_hand == Decimal("100.000")
    assert balance.reserved == Decimal("40.000")
    assert balance.available == Decimal("60.000")


# ── 2. A batch that is not released never creates stock (B1) ──────────────────


@pytest.mark.parametrize(
    "target_status",
    [
        BatchStatus.DRAFT.value,
        BatchStatus.IN_PRODUCTION.value,
        BatchStatus.QUALITY_PENDING.value,
        BatchStatus.ON_HOLD.value,
        BatchStatus.REJECTED.value,
    ],
)
def test_not_released_batches_never_create_stock(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    factory_user: User,
    target_status: str,
):
    """Only a released batch may create stock; every other status is refused."""
    batch = _make_batch(db, variant, factory_user, status=target_status)

    with pytest.raises(AppError) as excinfo:
        service.release_batch_to_stock(
            db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
        )
    assert excinfo.value.code == "BATCH_NOT_RELEASABLE"
    assert excinfo.value.details["batch_status"] == target_status

    balances = db.scalars(
        select(InventoryBalance).where(InventoryBalance.batch_id == batch.id)
    ).all()
    assert balances == []

    movements = db.scalars(select(StockMovement).where(StockMovement.batch_id == batch.id)).all()
    assert movements == []


def test_a_batch_without_produced_quantity_cannot_be_released(
    db: Session, variant: ProductVariant, warehouse: Warehouse, factory_user: User
):
    """A released batch without a produced quantity cannot book stock."""
    batch = _make_batch(
        db, variant, factory_user, status=BatchStatus.RELEASED.value, actual_qty=None
    )

    with pytest.raises(AppError) as excinfo:
        service.release_batch_to_stock(
            db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
        )
    assert excinfo.value.code == "MISSING_ACTUAL_QUANTITY"

    assert (
        db.scalar(
            select(func.count())
            .select_from(InventoryBalance)
            .where(InventoryBalance.batch_id == batch.id)
        )
        == 0
    )


def test_the_same_batch_cannot_be_booked_twice(
    db: Session, variant: ProductVariant, warehouse: Warehouse, factory_user: User
):
    """A released batch is booked into a warehouse exactly once."""
    batch = _make_batch(db, variant, factory_user, status=BatchStatus.RELEASED.value)
    service.release_batch_to_stock(
        db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
    )

    with pytest.raises(AppError) as excinfo:
        service.release_batch_to_stock(
            db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
        )
    assert excinfo.value.code == "BATCH_STOCK_ALREADY_BOOKED"

    movements = db.scalars(select(StockMovement).where(StockMovement.batch_id == batch.id)).all()
    assert len(movements) == 1


# ── 3. FEFO only ever picks a released batch (B1, B2, B3) ────────────────────


def test_fefo_excludes_batches_that_are_not_released(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    order_item: OrderItem,
    factory_user: User,
):
    """Stock of a batch that is not released is invisible to the FEFO reservation (B1, B2)."""
    held = _make_batch(db, variant, factory_user, status=BatchStatus.ON_HOLD.value)
    draft = _make_batch(db, variant, factory_user, status=BatchStatus.DRAFT.value)
    rejected = _make_batch(db, variant, factory_user, status=BatchStatus.REJECTED.value)

    for batch in (held, draft, rejected):
        balance = InventoryBalance(
            id=uuid.uuid4(),
            warehouse_id=warehouse.id,
            batch_id=batch.id,
            on_hand=Decimal("50.000"),
            reserved=Decimal("0"),
        )
        db.add(balance)
    db.commit()

    with pytest.raises(InsufficientStockError):
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("10.000"),
            order_item_id=order_item.id,
            actor_id=factory_user.id,
        )

    assert db.scalar(select(func.count()).select_from(StockReservation)) == 0


def test_fefo_excludes_expired_batches(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    order_item: OrderItem,
    factory_user: User,
):
    """An expired released batch is excluded from FEFO (B2)."""
    expired = _make_batch(
        db, variant, factory_user, status=BatchStatus.RELEASED.value, days_to_expiry=-5
    )
    balance = InventoryBalance(
        id=uuid.uuid4(),
        warehouse_id=warehouse.id,
        batch_id=expired.id,
        on_hand=Decimal("50.000"),
        reserved=Decimal("0"),
    )
    db.add(balance)
    db.commit()

    with pytest.raises(InsufficientStockError):
        reserve_stock_fefo(
            db,
            warehouse_id=warehouse.id,
            variant_id=variant.id,
            requested_qty=Decimal("10.000"),
            order_item_id=order_item.id,
            actor_id=factory_user.id,
        )


def test_fefo_picks_the_earliest_expiring_released_batch(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    order_item: OrderItem,
    factory_user: User,
):
    """FEFO chooses the earliest-expiring released batch first (B3)."""
    later = _make_batch(
        db, variant, factory_user, status=BatchStatus.RELEASED.value, days_to_expiry=120
    )
    sooner = _make_batch(
        db, variant, factory_user, status=BatchStatus.RELEASED.value, days_to_expiry=30
    )
    for batch in (later, sooner):
        db.add(
            InventoryBalance(
                id=uuid.uuid4(),
                warehouse_id=warehouse.id,
                batch_id=batch.id,
                on_hand=Decimal("50.000"),
                reserved=Decimal("0"),
            )
        )
    db.commit()

    reservations = reserve_stock_fefo(
        db,
        warehouse_id=warehouse.id,
        variant_id=variant.id,
        requested_qty=Decimal("10.000"),
        order_item_id=order_item.id,
        actor_id=factory_user.id,
    )

    assert len(reservations) == 1
    balance = db.scalar(
        select(InventoryBalance).where(InventoryBalance.id == reservations[0].balance_id)
    )
    assert balance.batch_id == sooner.id


# ── 4. Every illegal state transition is rejected ────────────────────────────


def test_every_illegal_transition_is_rejected(
    db: Session, variant: ProductVariant, factory_user: User
):
    """Every illegal pair of the canonical state machine is refused."""
    batch = _make_batch(db, variant, factory_user, status=BatchStatus.DRAFT.value)

    for current, target in ILLEGAL_TRANSITIONS:
        batch.status = current
        db.commit()
        with pytest.raises(AppError) as excinfo:
            service.advance_batch(db, batch_id=batch.id, to_status=target, actor_id=factory_user.id)
        assert excinfo.value.code == "ILLEGAL_STATE_TRANSITION"


# ── 5. Quality gates the release ─────────────────────────────────────────────


def test_a_batch_cannot_be_released_while_a_check_is_outstanding(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    factory_user: User,
    quality_parameter,
):
    """An unchecked parameter blocks the release of the stock."""
    batch = _make_batch(db, variant, factory_user, status=BatchStatus.RELEASED.value)

    with pytest.raises(AppError) as excinfo:
        service.release_batch_to_stock(
            db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
        )
    assert excinfo.value.code == "QUALITY_RELEASE_BLOCKED"
    assert excinfo.value.details["outstanding"] == ["MOISTURE_TEST"]

    assert (
        db.scalar(
            select(func.count())
            .select_from(InventoryBalance)
            .where(InventoryBalance.batch_id == batch.id)
        )
        == 0
    )


def test_a_batch_cannot_be_released_after_a_failed_check(
    db: Session,
    variant: ProductVariant,
    warehouse: Warehouse,
    factory_user: User,
    quality_parameter,
):
    """A failed check blocks the release of the stock."""
    batch = _make_batch(db, variant, factory_user, status=BatchStatus.RELEASED.value)
    service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=quality_parameter.id,
        measured_value=Decimal("99.0000"),
        actor_id=factory_user.id,
    )

    with pytest.raises(AppError) as excinfo:
        service.release_batch_to_stock(
            db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
        )
    assert excinfo.value.code == "QUALITY_RELEASE_BLOCKED"
    assert excinfo.value.details["failed"] == ["MOISTURE_TEST"]


def test_placeholder_parameter_without_limits_is_never_a_pass(
    db: Session, variant: ProductVariant, warehouse: Warehouse, factory_user: User
):
    """A placeholder parameter has no verdict, so it blocks the release (never 'passed')."""
    placeholder = service.create_quality_parameter(
        db,
        QualityParameterCreate(code="COLOUR_TEST", name="Colour (placeholder)", unit="scale"),
        actor_id=factory_user.id,
    )
    batch = _make_batch(db, variant, factory_user, status=BatchStatus.RELEASED.value)

    check = service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=placeholder.id,
        measured_value=Decimal("3.0000"),
        actor_id=factory_user.id,
    )
    assert check.passed is None

    with pytest.raises(AppError) as excinfo:
        service.release_batch_to_stock(
            db, batch_id=batch.id, warehouse_id=warehouse.id, actor_id=factory_user.id
        )
    assert excinfo.value.code == "QUALITY_RELEASE_BLOCKED"
    assert excinfo.value.details["outstanding"] == ["COLOUR_TEST"]


# ── 6. Permissions ───────────────────────────────────────────────────────────


def test_dealer_and_consumer_cannot_create_a_batch_or_release_one(
    client: TestClient, db: Session, dealer_user: User, consumer_user: User, warehouse: Warehouse
):
    """Only factory staff and the owner may create or release a batch."""
    for user in (dealer_user, consumer_user):
        create_resp = client.post(
            "/api/v1/production/batches",
            json={
                "plan_id": str(uuid.uuid4()),
                "batch_code": f"NOPE-{uuid.uuid4().hex[:8]}",
                "manufactured_at": "2026-11-01",
                "expiry_date": "2027-05-01",
            },
            headers=_auth_header(user),
        )
        assert create_resp.status_code == 403
        assert create_resp.json()["code"] == "FORBIDDEN"

        release_resp = client.post(
            f"/api/v1/production/batches/{uuid.uuid4()}/release",
            json={"warehouse_id": str(warehouse.id)},
            headers=_auth_header(user),
        )
        assert release_resp.status_code == 403


def test_dealer_and_consumer_cannot_record_a_material_receipt(
    client: TestClient, db: Session, dealer_user: User, consumer_user: User
):
    """Only factory staff and the owner may record goods receipts."""
    for user in (dealer_user, consumer_user):
        resp = client.post(
            "/api/v1/procurement/receipts",
            json={
                "supplier_id": str(uuid.uuid4()),
                "received_on": "2026-10-30",
                "lines": [
                    {
                        "raw_material_id": str(uuid.uuid4()),
                        "quantity": "5.000",
                        "unit_cost": "10.00",
                    }
                ],
            },
            headers=_auth_header(user),
        )
        assert resp.status_code == 403
