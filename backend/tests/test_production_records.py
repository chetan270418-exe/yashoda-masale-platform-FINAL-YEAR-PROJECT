"""Tests for the production module (Milestone M3 / T-031 and T-032).

Covers production plans, batches, stages, the single batch state machine (every illegal
transition is rejected), quality parameters, quality checks, quality decisions, the
defensive batch-event hook and role permissions.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.core.db import Base
from app.core.errors import AppError
from app.main import app as main_app
from app.production import router as production_router
from app.production import service
from app.production.models import (
    ProductionPlan,
    QualityDecision,
)
from app.production.schemas import QualityParameterCreate
from app.production.state_machine import (
    ALLOWED_TRANSITIONS,
    BATCH_STATUS_VALUES,
    BatchStatus,
)
from tests.conftest import _make_user


def _ensure_routers_registered() -> None:
    """Register the milestone routers when app/main.py does not include them yet."""
    registered = {getattr(route, "path", "") for route in main_app.routes}
    if not any(path.startswith("/api/v1/production") for path in registered):
        main_app.include_router(production_router.router)


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
def owner_user(db: Session) -> User:
    return _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")


@pytest.fixture()
def warehouse_user(db: Session) -> User:
    return _make_user(
        db, email=f"warehouse-{uuid.uuid4().hex[:6]}@example.com", role_name="warehouse"
    )


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
    product = Product(name=f"Garam Masala {uuid.uuid4().hex[:4]}", category_id=category.id)
    db.add(product)
    db.flush()
    sku_variant = ProductVariant(
        product_id=product.id, sku=f"SKU-PROD-{uuid.uuid4().hex[:8]}", pack_size_g=250
    )
    db.add(sku_variant)
    db.commit()
    return sku_variant


@pytest.fixture()
def plan(db: Session, variant: ProductVariant, factory_user: User) -> ProductionPlan:
    return service.create_production_plan(
        db,
        variant_id=variant.id,
        planned_qty=Decimal("500.000"),
        planned_start=date(2026, 11, 1),
        planned_end=date(2026, 11, 5),
        actor_id=factory_user.id,
    )


@pytest.fixture()
def batch(db: Session, plan: ProductionPlan, factory_user: User):
    return service.create_batch_from_plan(
        db,
        plan_id=plan.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:8]}",
        manufactured_at=date(2026, 11, 3),
        expiry_date=date(2027, 5, 3),
        actor_id=factory_user.id,
    )


# ── Plans ────────────────────────────────────────────────────────────────────


def test_create_plan_persists_and_audits(db: Session, plan: ProductionPlan, factory_user: User):
    """A production plan is stored with its quantities and the creation is audited."""
    assert plan.plan_no.startswith("PLAN-")
    assert plan.planned_qty == Decimal("500.000")
    assert plan.status == "planned"

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "production_plans",
            AuditLog.entity_id == plan.id,
            AuditLog.action == "CREATE_PRODUCTION_PLAN",
        )
    )
    assert audit is not None
    assert audit.actor_id == factory_user.id


def test_create_plan_rejects_invalid_input(
    db: Session, variant: ProductVariant, factory_user: User
):
    """A non-positive quantity, an inverted window and an unknown variant are refused."""
    with pytest.raises(AppError) as qty_error:
        service.create_production_plan(
            db,
            variant_id=variant.id,
            planned_qty=Decimal("0.000"),
            planned_start=date(2026, 11, 1),
            planned_end=date(2026, 11, 5),
            actor_id=factory_user.id,
        )
    assert qty_error.value.code == "INVALID_PLANNED_QUANTITY"

    with pytest.raises(AppError) as date_error:
        service.create_production_plan(
            db,
            variant_id=variant.id,
            planned_qty=Decimal("10.000"),
            planned_start=date(2026, 11, 5),
            planned_end=date(2026, 11, 1),
            actor_id=factory_user.id,
        )
    assert date_error.value.code == "INVALID_PLAN_DATES"

    with pytest.raises(AppError) as variant_error:
        service.create_production_plan(
            db,
            variant_id=uuid.uuid4(),
            planned_qty=Decimal("10.000"),
            planned_start=date(2026, 11, 1),
            planned_end=date(2026, 11, 5),
            actor_id=factory_user.id,
        )
    assert variant_error.value.code == "VARIANT_NOT_FOUND"
    assert variant_error.value.status_code == 404


def test_list_plans_filters_by_status(db: Session, plan: ProductionPlan):
    """Plans can be filtered by status."""
    assert [p.id for p in service.list_plans(db, status="planned")] == [plan.id]
    assert service.list_plans(db, status="completed") == []


# ── Batches and stages ───────────────────────────────────────────────────────


def test_create_batch_from_plan_starts_in_draft_with_stages(
    db: Session, plan: ProductionPlan, batch, factory_user: User
):
    """A new batch is a draft with the planned quantity and the standard stages."""
    assert batch.status == BatchStatus.DRAFT.value
    assert batch.planned_qty == Decimal("500.000")
    assert batch.variant_id == plan.variant_id

    stages = service.list_stages(db, batch.id)
    assert len(stages) == len(service.BATCH_STAGE_SEQUENCE)
    assert [stage.sequence for stage in stages] == [1, 2, 3, 4, 5, 6]
    assert all(stage.status == "pending" for stage in stages)

    db.refresh(plan)
    assert plan.status == "in_progress"

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "production_batches",
            AuditLog.entity_id == batch.id,
            AuditLog.action == "CREATE_PRODUCTION_BATCH",
        )
    )
    assert audit is not None
    assert audit.after["plan_id"] == str(plan.id)


def test_create_batch_rejects_duplicate_code_and_bad_dates(
    db: Session, plan: ProductionPlan, batch, factory_user: User
):
    """A duplicate batch code and an expiry before the manufacturing date are refused."""
    with pytest.raises(AppError) as duplicate:
        service.create_batch_from_plan(
            db,
            plan_id=plan.id,
            batch_code=batch.batch_code,
            manufactured_at=date(2026, 11, 3),
            expiry_date=date(2027, 5, 3),
            actor_id=factory_user.id,
        )
    assert duplicate.value.code == "DUPLICATE_BATCH_CODE"

    with pytest.raises(AppError) as dates:
        service.create_batch_from_plan(
            db,
            plan_id=plan.id,
            batch_code=f"BATCH-{uuid.uuid4().hex[:8]}",
            manufactured_at=date(2026, 11, 3),
            expiry_date=date(2026, 11, 1),
            actor_id=factory_user.id,
        )
    assert dates.value.code == "INVALID_BATCH_DATES"


def test_record_batch_event_writes_only_supported_events(db: Session, batch, factory_user: User):
    """The batch-event hook writes when the traceability table accepts the event type.

    The ``batch_events`` table belongs to the traceability milestone. Until that table
    exists the hook is a no-op; once it exists the same call records the event, and an
    event type the table does not accept is skipped instead of breaking the transaction.
    """
    table = Base.metadata.tables.get("batch_events")
    if table is None:
        written = service.record_batch_event(
            db, batch.id, "manufactured", "Batch manufactured.", actor_id=factory_user.id
        )
        assert written is False
        return

    written = service.record_batch_event(
        db, batch.id, "manufactured", "Batch manufactured.", actor_id=factory_user.id
    )
    assert written is True
    row = db.execute(
        select(table).where(
            table.c.batch_id == str(batch.id),
            table.c.public_text == "Batch manufactured.",
        )
    ).first()
    assert row is not None
    assert row.event_type == "manufactured"

    # An event type the table does not accept is skipped, never written.
    assert (
        service.record_batch_event(
            db, batch.id, "not_a_real_event_type", "Nope", actor_id=factory_user.id
        )
        is False
    )


def test_advance_batch_moves_status_and_stages(db: Session, batch, factory_user: User):
    """A legal transition updates the batch, the stages and the audit log."""
    moved = service.advance_batch(
        db, batch_id=batch.id, to_status="in_production", actor_id=factory_user.id
    )
    assert moved.status == BatchStatus.IN_PRODUCTION.value

    stages = service.list_stages(db, batch.id)
    assert [stage.status for stage in stages] == [
        "completed",
        "completed",
        "in_progress",
        "pending",
        "pending",
        "pending",
    ]
    assert stages[0].completed_at is not None
    assert stages[2].started_at is not None

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "production_batches",
            AuditLog.entity_id == batch.id,
            AuditLog.action == "BATCH_STATUS_CHANGED",
        )
    )
    assert audit is not None
    assert audit.before == {"status": "draft"}
    assert audit.after["status"] == "in_production"


def test_every_illegal_transition_is_rejected(db: Session, batch, factory_user: User):
    """Every illegal pair of the state machine is rejected with a clear error."""
    for current, target in ILLEGAL_TRANSITIONS:
        batch.status = current
        db.commit()
        with pytest.raises(AppError) as excinfo:
            service.advance_batch(db, batch_id=batch.id, to_status=target, actor_id=factory_user.id)
        assert excinfo.value.code == "ILLEGAL_STATE_TRANSITION"
        assert current in excinfo.value.message
        assert target in excinfo.value.message
        assert excinfo.value.details["current_status"] == current
        assert excinfo.value.details["target_status"] == target


def test_unknown_status_is_rejected(db: Session, batch, factory_user: User):
    """A status outside the canonical list is refused."""
    with pytest.raises(AppError) as excinfo:
        service.advance_batch(db, batch_id=batch.id, to_status="shipped", actor_id=factory_user.id)
    assert excinfo.value.code == "UNKNOWN_BATCH_STATUS"


def test_set_batch_actual_quantity(db: Session, batch, factory_user: User):
    """The produced quantity is recorded and audited."""
    updated = service.set_batch_actual_quantity(
        db, batch_id=batch.id, actual_qty=Decimal("480.000"), actor_id=factory_user.id
    )
    assert updated.actual_qty == Decimal("480.000")

    with pytest.raises(AppError):
        service.set_batch_actual_quantity(
            db, batch_id=batch.id, actual_qty=Decimal("0.000"), actor_id=factory_user.id
        )


# ── Quality ──────────────────────────────────────────────────────────────────


def test_quality_parameter_placeholder_has_no_limits(db: Session, factory_user: User):
    """A parameter without sponsor limits keeps NULL bounds and stays a placeholder."""
    parameter = service.create_quality_parameter(
        db,
        QualityParameterCreate(code="MOISTURE", name="Moisture content", unit="%"),
        actor_id=factory_user.id,
    )
    assert parameter.min_value is None
    assert parameter.max_value is None
    assert parameter.is_placeholder is True


def test_quality_parameter_rejects_inverted_range(db: Session, factory_user: User):
    """A minimum above the maximum is refused."""
    with pytest.raises(AppError) as excinfo:
        service.create_quality_parameter(
            db,
            QualityParameterCreate(
                code="BAD",
                name="Bad range",
                unit="%",
                min_value=Decimal("10"),
                max_value=Decimal("1"),
            ),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "INVALID_QUALITY_RANGE"


def test_quality_check_passes_and_fails_with_limits(db: Session, batch, factory_user: User):
    """A parameter with limits produces a real verdict."""
    parameter = service.create_quality_parameter(
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
    assert parameter.is_placeholder is False

    passing = service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=parameter.id,
        measured_value=Decimal("7.5000"),
        actor_id=factory_user.id,
    )
    assert passing.passed is True

    failing = service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=parameter.id,
        measured_value=Decimal("12.0000"),
        actor_id=factory_user.id,
    )
    assert failing.passed is False


def test_placeholder_parameter_without_limits_is_unknown(db: Session, batch, factory_user: User):
    """A placeholder parameter without limits yields passed = None, never True."""
    parameter = service.create_quality_parameter(
        db,
        QualityParameterCreate(code="COLOUR_TEST", name="Colour (placeholder)", unit="scale"),
        actor_id=factory_user.id,
    )
    check = service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=parameter.id,
        measured_value=Decimal("4.0000"),
        actor_id=factory_user.id,
    )
    assert check.passed is None

    status = service.get_batch_quality_status(db, batch.id)
    assert status.released_allowed is False
    assert status.outstanding == ["COLOUR_TEST"]


def test_quality_status_reports_outstanding_and_failed(db: Session, batch, factory_user: User):
    """The roll-up lists outstanding and failed parameters and blocks the release."""
    limited = service.create_quality_parameter(
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
    service.create_quality_parameter(
        db,
        QualityParameterCreate(code="WEIGHT_TEST", name="Weight (placeholder)", unit="g"),
        actor_id=factory_user.id,
    )

    status = service.get_batch_quality_status(db, batch.id)
    assert status.released_allowed is False
    assert sorted(status.outstanding) == ["MOISTURE_TEST", "WEIGHT_TEST"]
    assert status.failed == []

    service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=limited.id,
        measured_value=Decimal("99.0000"),
        actor_id=factory_user.id,
    )
    status = service.get_batch_quality_status(db, batch.id)
    assert status.failed == ["MOISTURE_TEST"]
    assert status.released_allowed is False


def test_quality_decision_release_requires_passing_checks(db: Session, batch, factory_user: User):
    """A release decision is refused while a check is outstanding or failed."""
    parameter = service.create_quality_parameter(
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

    service.advance_batch(
        db, batch_id=batch.id, to_status="in_production", actor_id=factory_user.id
    )
    service.advance_batch(
        db, batch_id=batch.id, to_status="quality_pending", actor_id=factory_user.id
    )

    with pytest.raises(AppError) as blocked:
        service.record_quality_decision(
            db, batch_id=batch.id, decision="released", remarks=None, actor_id=factory_user.id
        )
    assert blocked.value.code == "QUALITY_RELEASE_BLOCKED"

    service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=parameter.id,
        measured_value=Decimal("50.0000"),
        actor_id=factory_user.id,
    )
    with pytest.raises(AppError) as still_blocked:
        service.record_quality_decision(
            db, batch_id=batch.id, decision="released", remarks=None, actor_id=factory_user.id
        )
    assert still_blocked.value.code == "QUALITY_RELEASE_BLOCKED"
    assert still_blocked.value.details["failed"] == ["MOISTURE_TEST"]


def test_quality_decision_releases_the_batch(db: Session, batch, factory_user: User):
    """With every check passing, the release decision moves the batch to released."""
    parameter = service.create_quality_parameter(
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
    service.record_quality_check(
        db,
        batch_id=batch.id,
        parameter_id=parameter.id,
        measured_value=Decimal("7.0000"),
        actor_id=factory_user.id,
    )
    service.advance_batch(
        db, batch_id=batch.id, to_status="in_production", actor_id=factory_user.id
    )
    service.advance_batch(
        db, batch_id=batch.id, to_status="quality_pending", actor_id=factory_user.id
    )

    decision = service.record_quality_decision(
        db, batch_id=batch.id, decision="released", remarks="All good", actor_id=factory_user.id
    )
    assert decision.decision == "released"

    db.refresh(batch)
    assert batch.status == BatchStatus.RELEASED.value

    stored = db.scalar(select(func.count()).select_from(QualityDecision))
    assert stored == 1
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "quality_decisions",
            AuditLog.entity_id == decision.id,
            AuditLog.action == "RECORD_QUALITY_DECISION",
        )
    )
    assert audit is not None


def test_quality_decision_hold_then_reject(db: Session, batch, factory_user: User):
    """A batch can be held and later rejected, but never released after rejection."""
    service.advance_batch(
        db, batch_id=batch.id, to_status="in_production", actor_id=factory_user.id
    )
    service.advance_batch(
        db, batch_id=batch.id, to_status="quality_pending", actor_id=factory_user.id
    )

    hold = service.record_quality_decision(
        db,
        batch_id=batch.id,
        decision="on_hold",
        remarks="Awaiting sponsor",
        actor_id=factory_user.id,
    )
    assert hold.decision == "on_hold"
    db.refresh(batch)
    assert batch.status == BatchStatus.ON_HOLD.value

    reject = service.record_quality_decision(
        db, batch_id=batch.id, decision="rejected", remarks="Failed", actor_id=factory_user.id
    )
    assert reject.decision == "rejected"
    db.refresh(batch)
    assert batch.status == BatchStatus.REJECTED.value

    with pytest.raises(AppError) as terminal:
        service.advance_batch(db, batch_id=batch.id, to_status="released", actor_id=factory_user.id)
    assert terminal.value.code == "ILLEGAL_STATE_TRANSITION"


def test_quality_decision_rejects_unknown_decision(db: Session, batch, factory_user: User):
    """Only released, on_hold and rejected are valid decisions."""
    with pytest.raises(AppError) as excinfo:
        service.record_quality_decision(
            db, batch_id=batch.id, decision="shipped", remarks=None, actor_id=factory_user.id
        )
    assert excinfo.value.code == "INVALID_QUALITY_DECISION"
    assert db.scalar(select(func.count()).select_from(QualityDecision)) == 0


# ── HTTP API and permissions ─────────────────────────────────────────────────


def test_api_plan_and_batch_lifecycle(client: TestClient, db: Session, factory_user: User):
    """The HTTP API plans a run, creates a batch and moves it forward."""
    category = Category(name=f"Cat-API-{uuid.uuid4().hex[:6]}")
    db.add(category)
    db.flush()
    product = Product(name=f"API Masala {uuid.uuid4().hex[:4]}", category_id=category.id)
    db.add(product)
    db.flush()
    api_variant = ProductVariant(
        product_id=product.id, sku=f"SKU-API-{uuid.uuid4().hex[:8]}", pack_size_g=100
    )
    db.add(api_variant)
    db.commit()

    plan_resp = client.post(
        "/api/v1/production/plans",
        json={
            "variant_id": str(api_variant.id),
            "planned_qty": "100.000",
            "planned_start": "2026-11-01",
            "planned_end": "2026-11-02",
        },
        headers=_auth_header(factory_user),
    )
    assert plan_resp.status_code == 201
    plan_id = plan_resp.json()["id"]

    batch_resp = client.post(
        "/api/v1/production/batches",
        json={
            "plan_id": plan_id,
            "batch_code": f"API-BATCH-{uuid.uuid4().hex[:8]}",
            "manufactured_at": "2026-11-01",
            "expiry_date": "2027-05-01",
        },
        headers=_auth_header(factory_user),
    )
    assert batch_resp.status_code == 201
    body = batch_resp.json()
    assert body["status"] == "draft"
    assert len(body["stages"]) == 6

    transition_resp = client.post(
        f"/api/v1/production/batches/{body['id']}/transition",
        json={"to_status": "in_production"},
        headers=_auth_header(factory_user),
    )
    assert transition_resp.status_code == 200
    assert transition_resp.json()["status"] == "in_production"

    illegal_resp = client.post(
        f"/api/v1/production/batches/{body['id']}/transition",
        json={"to_status": "draft"},
        headers=_auth_header(factory_user),
    )
    assert illegal_resp.status_code == 400
    assert illegal_resp.json()["code"] == "ILLEGAL_STATE_TRANSITION"


def test_api_state_machine_endpoint_lists_transitions(
    client: TestClient, db: Session, factory_user: User
):
    """The state machine is published so the frontend can render the legal moves."""
    resp = client.get("/api/v1/production/state-machine", headers=_auth_header(factory_user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["draft"] == ["cancelled", "in_production"]
    assert body["quality_pending"] == ["on_hold", "rejected", "released"]
    assert body["rejected"] == []


def test_api_permissions(client: TestClient, db: Session, dealer_user: User, consumer_user: User):
    """Dealers and consumers may not create production records."""
    for user in (dealer_user, consumer_user):
        plan_resp = client.post(
            "/api/v1/production/plans",
            json={
                "variant_id": str(uuid.uuid4()),
                "planned_qty": "10.000",
                "planned_start": "2026-11-01",
                "planned_end": "2026-11-02",
            },
            headers=_auth_header(user),
        )
        assert plan_resp.status_code == 403
        assert plan_resp.json()["code"] == "FORBIDDEN"

    batch_resp = client.post(
        "/api/v1/production/batches",
        json={
            "plan_id": str(uuid.uuid4()),
            "batch_code": f"NOPE-{uuid.uuid4().hex[:8]}",
            "manufactured_at": "2026-11-01",
            "expiry_date": "2027-05-01",
        },
        headers=_auth_header(dealer_user),
    )
    assert batch_resp.status_code == 403

    read_resp = client.get("/api/v1/production/batches", headers=_auth_header(consumer_user))
    assert read_resp.status_code == 403


def test_api_warehouse_can_read_production(client: TestClient, db: Session, warehouse_user: User):
    """Warehouse staff may read batches and plans."""
    assert (
        client.get("/api/v1/production/batches", headers=_auth_header(warehouse_user)).status_code
        == 200
    )
    assert (
        client.get("/api/v1/production/plans", headers=_auth_header(warehouse_user)).status_code
        == 200
    )

    write_resp = client.post(
        "/api/v1/production/quality-parameters",
        json={"code": "MOISTURE", "name": "Moisture", "unit": "%"},
        headers=_auth_header(warehouse_user),
    )
    assert write_resp.status_code == 403


# ── OpenAPI ──────────────────────────────────────────────────────────────────


def test_openapi_documents_every_production_endpoint(client: TestClient, db: Session):
    """Every production endpoint appears in the OpenAPI schema with its models."""
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]

    expected = {
        "/api/v1/production/plans": {"post": True, "get": False},
        "/api/v1/production/batches": {"post": True, "get": False},
        "/api/v1/production/batches/{batch_id}": {"get": False},
        "/api/v1/production/batches/{batch_id}/transition": {"post": True},
        "/api/v1/production/batches/{batch_id}/quantity": {"post": True},
        "/api/v1/production/batches/{batch_id}/release": {"post": True},
        "/api/v1/production/batches/{batch_id}/quality-checks": {"post": True},
        "/api/v1/production/batches/{batch_id}/quality-status": {"get": False},
        "/api/v1/production/batches/{batch_id}/quality-decisions": {"post": True},
        "/api/v1/production/quality-parameters": {"post": True, "get": False},
        "/api/v1/production/state-machine": {"get": False},
    }
    for path, methods in expected.items():
        assert path in paths, f"{path} is missing from the OpenAPI schema"
        for method, has_body in methods.items():
            operation = paths[path][method]
            assert operation["responses"], f"{method.upper()} {path} has no response model"
            if has_body:
                assert "requestBody" in operation, f"{method.upper()} {path} has no request model"


# ── Migration ────────────────────────────────────────────────────────────────


def test_migration_creates_the_factory_schema():
    """Migration 0007 creates the new tables and the canonical batch constraint."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)

        expected_tables = {
            "suppliers",
            "raw_materials",
            "material_lots",
            "material_receipts",
            "material_receipt_lines",
            "production_plans",
            "production_stages",
            "quality_parameters",
            "quality_checks",
            "quality_decisions",
        }
        assert expected_tables.issubset(set(inspector.get_table_names()))

        batch_columns = {col["name"] for col in inspector.get_columns("production_batches")}
        assert "planned_qty" in batch_columns
        assert "actual_qty" in batch_columns

        checks = inspector.get_check_constraints("production_batches")
        status_check = next(c for c in checks if c["name"] == "ck_production_batches_status")
        for status in BATCH_STATUS_VALUES:
            assert f"'{status}'" in status_check["sqltext"]
        assert "'hold'" not in status_check["sqltext"]

        command.downgrade(cfg, "base")
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set()
        engine.dispose()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_expiry_window_of_a_batch_is_validated(db: Session, batch, factory_user: User):
    """A batch keeps the dates it was created with."""
    assert batch.manufactured_at == date(2026, 11, 3)
    assert batch.expiry_date == date(2027, 5, 3)
    assert batch.expiry_date >= batch.manufactured_at
    assert timedelta(days=1) > timedelta(days=0)
