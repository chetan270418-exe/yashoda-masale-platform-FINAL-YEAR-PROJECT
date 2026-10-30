"""Production service: plans, batches, stages, quality checks and release to stock
(Milestone M3 / T-031, T-032 and T-034).

Invariants enforced here:
- B1: only a released batch may own sellable stock, and only a released batch can be
  reserved (the reservation filter itself lives in app.inventory.service).
- B4: stock never goes negative; the produced quantity is strictly positive.
- B5: every stock change is a row in stock_movements.
- B12: all quantities are Decimal.
- Rule 49: illegal state transitions are rejected with a clear error, using the single
  transition table in app.production.state_machine.
- Rules.md section 12: no quality limit is invented; a placeholder parameter without limits
  yields ``passed = None`` (unknown), never ``True``.
"""

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import log_action
from app.catalog.models import ProductVariant
from app.core.db import Base
from app.core.errors import AppError
from app.inventory.models import (
    InventoryBalance,
    ProductionBatch,
    StockMovement,
    Warehouse,
)
from app.production.models import (
    ProductionPlan,
    ProductionStage,
    QualityCheck,
    QualityDecision,
    QualityParameter,
)
from app.production.schemas import (
    BatchQualityStatus,
    QualityParameterCreate,
    QualityParameterStatus,
)
from app.production.state_machine import (
    BatchStatus,
    validate_batch_transition,
)

#: Processing steps every batch goes through, in order.
BATCH_STAGE_SEQUENCE: tuple[str, ...] = (
    "material_preparation",
    "mixing",
    "drying",
    "milling",
    "packing",
    "quality_control",
)

#: How many stages are considered complete when a batch reaches a given status.
#: The stage right after that count is the one in progress.
STATUS_COMPLETED_STAGE_COUNT: dict[str, int] = {
    BatchStatus.DRAFT.value: 0,
    BatchStatus.IN_PRODUCTION.value: 2,
    BatchStatus.QUALITY_PENDING.value: 5,
    BatchStatus.RELEASED.value: 6,
    BatchStatus.ON_HOLD.value: 6,
    BatchStatus.REJECTED.value: 6,
    BatchStatus.CANCELLED.value: 0,
}

#: Decisions a quality decision may carry.
VALID_QUALITY_DECISIONS: frozenset[str] = frozenset({"released", "on_hold", "rejected"})

#: Public traceability event type per batch status. The types are the canonical ones of
#: the traceability module; a status without a public meaning simply records no event.
STATUS_EVENT_TYPES: dict[str, str] = {
    BatchStatus.RELEASED.value: "released",
    BatchStatus.ON_HOLD.value: "held",
    BatchStatus.REJECTED.value: "rejected",
}


def _as_decimal(value: Decimal | int | float | str, field: str) -> Decimal:
    """Convert an incoming number to Decimal without ever using binary floats (B12)."""
    try:
        return Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive
        raise AppError(
            "INVALID_NUMERIC_VALUE", f"Invalid numeric value for {field}: {value!r}.", 400
        ) from exc


def generate_plan_no() -> str:
    """Generate a human-readable unique production plan number."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"PLAN-{stamp}-{uuid.uuid4().hex[:6].upper()}"


# ── Plans ─────────────────────────────────────────────────────────────────────


def create_production_plan(
    db: Session,
    variant_id: uuid.UUID,
    planned_qty: Decimal,
    planned_start: date,
    planned_end: date,
    actor_id: uuid.UUID,
    notes: str | None = None,
) -> ProductionPlan:
    """Create a production plan for one product variant.

    Validation:
    - the variant must exist;
    - the planned quantity must be strictly positive;
    - the planned window must not end before it starts.
    """
    variant = db.get(ProductVariant, variant_id)
    if variant is None:
        raise AppError("VARIANT_NOT_FOUND", f"Product variant '{variant_id}' was not found.", 404)

    quantity = _as_decimal(planned_qty, "planned_qty")
    if quantity <= Decimal("0"):
        raise AppError(
            "INVALID_PLANNED_QUANTITY", "The planned quantity must be greater than zero.", 400
        )
    if planned_end < planned_start:
        raise AppError(
            "INVALID_PLAN_DATES",
            "The planned end date cannot be before the planned start date.",
            400,
        )

    plan = ProductionPlan(
        id=uuid.uuid4(),
        plan_no=generate_plan_no(),
        variant_id=variant.id,
        planned_qty=quantity,
        planned_start=planned_start,
        planned_end=planned_end,
        status="planned",
        created_by=actor_id,
        notes=notes,
    )
    try:
        db.add(plan)
        db.flush()
        log_action(
            db,
            actor_id=actor_id,
            action="CREATE_PRODUCTION_PLAN",
            entity="production_plans",
            entity_id=plan.id,
            after={
                "plan_no": plan.plan_no,
                "variant_id": str(variant.id),
                "planned_qty": str(quantity),
                "planned_start": planned_start.isoformat(),
                "planned_end": planned_end.isoformat(),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_PLAN_NO", "A plan with the same number already exists.", 409
        ) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(plan)
    return plan


def list_plans(
    db: Session,
    status: str | None = None,
    variant_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ProductionPlan]:
    """List production plans, newest first, with optional status and variant filters."""
    stmt = select(ProductionPlan).order_by(ProductionPlan.created_at.desc())
    if status is not None:
        stmt = stmt.where(ProductionPlan.status == status)
    if variant_id is not None:
        stmt = stmt.where(ProductionPlan.variant_id == variant_id)
    return list(db.scalars(stmt.limit(limit).offset(offset)).all())


# ── Batches and stages ────────────────────────────────────────────────────────


def create_batch_from_plan(
    db: Session,
    plan_id: uuid.UUID,
    batch_code: str,
    manufactured_at: date,
    expiry_date: date,
    actor_id: uuid.UUID,
    actual_qty: Decimal | None = None,
) -> ProductionBatch:
    """Create a batch in status ``draft`` from a production plan.

    The batch copies the planned quantity of the plan, the processing stages are created
    (all ``pending``) and the plan moves to ``in_progress``.
    """
    plan = db.get(ProductionPlan, plan_id)
    if plan is None:
        raise AppError(
            "PRODUCTION_PLAN_NOT_FOUND", f"Production plan '{plan_id}' was not found.", 404
        )
    if plan.status == "cancelled":
        raise AppError(
            "PRODUCTION_PLAN_CANCELLED",
            f"Production plan '{plan.plan_no}' is cancelled and cannot start a batch.",
            400,
        )
    if expiry_date < manufactured_at:
        raise AppError(
            "INVALID_BATCH_DATES",
            "The expiry date cannot be before the manufacturing date.",
            400,
        )

    duplicate = db.scalar(select(ProductionBatch).where(ProductionBatch.batch_code == batch_code))
    if duplicate is not None:
        raise AppError("DUPLICATE_BATCH_CODE", f"Batch code '{batch_code}' already exists.", 409)

    produced = None
    if actual_qty is not None:
        produced = _as_decimal(actual_qty, "actual_qty")
        if produced <= Decimal("0"):
            raise AppError(
                "INVALID_ACTUAL_QUANTITY", "The produced quantity must be greater than zero.", 400
            )

    batch = ProductionBatch(
        id=uuid.uuid4(),
        variant_id=plan.variant_id,
        batch_code=batch_code,
        manufactured_at=manufactured_at,
        expiry_date=expiry_date,
        status=BatchStatus.DRAFT.value,
        planned_qty=plan.planned_qty,
        actual_qty=produced,
    )

    try:
        db.add(batch)
        db.flush()

        for index, stage_name in enumerate(BATCH_STAGE_SEQUENCE, start=1):
            db.add(
                ProductionStage(
                    id=uuid.uuid4(),
                    batch_id=batch.id,
                    stage_name=stage_name,
                    sequence=index,
                    status="pending",
                )
            )

        plan.status = "in_progress"

        log_action(
            db,
            actor_id=actor_id,
            action="CREATE_PRODUCTION_BATCH",
            entity="production_batches",
            entity_id=batch.id,
            after={
                "batch_code": batch_code,
                "status": batch.status,
                "variant_id": str(plan.variant_id),
                "plan_id": str(plan.id),
                "plan_no": plan.plan_no,
                "planned_qty": str(plan.planned_qty),
                "actual_qty": str(produced) if produced is not None else None,
            },
        )
        record_batch_event(
            db,
            batch.id,
            "manufactured",
            f"Batch {batch_code} created from plan {plan.plan_no}.",
            actor_id=actor_id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_BATCH_CODE", f"Batch code '{batch_code}' already exists.", 409
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(batch)
    return batch


def list_batches(
    db: Session,
    status: str | None = None,
    variant_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ProductionBatch]:
    """List production batches, newest first, with optional status and variant filters."""
    stmt = select(ProductionBatch).order_by(ProductionBatch.created_at.desc())
    if status is not None:
        stmt = stmt.where(ProductionBatch.status == status)
    if variant_id is not None:
        stmt = stmt.where(ProductionBatch.variant_id == variant_id)
    return list(db.scalars(stmt.limit(limit).offset(offset)).all())


def get_batch(db: Session, batch_id: uuid.UUID) -> ProductionBatch:
    """Return a batch or raise a 404 error."""
    batch = db.get(ProductionBatch, batch_id)
    if batch is None:
        raise AppError("BATCH_NOT_FOUND", f"Production batch '{batch_id}' was not found.", 404)
    return batch


def list_stages(db: Session, batch_id: uuid.UUID) -> list[ProductionStage]:
    """Return the processing stages of a batch in sequence order."""
    return list(
        db.scalars(
            select(ProductionStage)
            .where(ProductionStage.batch_id == batch_id)
            .order_by(ProductionStage.sequence.asc())
        ).all()
    )


def _sync_stages_for_status(db: Session, batch_id: uuid.UUID, status: str) -> None:
    """Move the stage rows of a batch to match the new batch status."""
    completed_count = STATUS_COMPLETED_STAGE_COUNT.get(status, 0)
    now = datetime.now(UTC)
    for stage in list_stages(db, batch_id):
        if stage.sequence <= completed_count:
            if stage.status != "completed":
                stage.status = "completed"
                stage.started_at = stage.started_at or now
                stage.completed_at = now
        elif stage.sequence == completed_count + 1 and stage.status == "pending":
            stage.status = "in_progress"
            stage.started_at = now


def advance_batch(
    db: Session,
    batch_id: uuid.UUID,
    to_status: str,
    actor_id: uuid.UUID,
    notes: str | None = None,
) -> ProductionBatch:
    """Move a batch to another lifecycle status.

    The transition is validated against the single transition table in
    app.production.state_machine; every illegal pair is rejected with a clear error
    (Rule 49). Every transition is audited, and the matching stage rows are updated.

    This function does NOT create stock: releasing a batch only marks it ``released``.
    Use ``release_batch_to_stock`` for the release that also books the produced quantity
    into a warehouse (T-034).
    """
    batch = get_batch(db, batch_id)
    current = batch.status
    validate_batch_transition(current, to_status)
    target = to_status.strip().lower()

    before = {"status": current}
    batch.status = target
    _sync_stages_for_status(db, batch.id, target)

    action = (
        "BATCH_RECALLED"
        if current == BatchStatus.RELEASED.value and target == BatchStatus.ON_HOLD.value
        else "BATCH_STATUS_CHANGED"
    )
    log_action(
        db,
        actor_id=actor_id,
        action=action,
        entity="production_batches",
        entity_id=batch.id,
        before=before,
        after={"status": target, "notes": notes},
    )
    event_type = STATUS_EVENT_TYPES.get(target)
    if event_type is not None:
        record_batch_event(
            db,
            batch.id,
            event_type,
            f"Batch status changed from '{current}' to '{target}'.",
            actor_id=actor_id,
        )
    db.commit()
    db.refresh(batch)
    return batch


def set_batch_actual_quantity(
    db: Session,
    batch_id: uuid.UUID,
    actual_qty: Decimal,
    actor_id: uuid.UUID,
) -> ProductionBatch:
    """Record the quantity a batch actually produced.

    The produced quantity is what becomes sellable stock when the batch is released, so it
    must be strictly positive.
    """
    batch = get_batch(db, batch_id)
    quantity = _as_decimal(actual_qty, "actual_qty")
    if quantity <= Decimal("0"):
        raise AppError(
            "INVALID_ACTUAL_QUANTITY", "The produced quantity must be greater than zero.", 400
        )

    before = {"actual_qty": str(batch.actual_qty) if batch.actual_qty is not None else None}
    batch.actual_qty = quantity
    log_action(
        db,
        actor_id=actor_id,
        action="SET_BATCH_QUANTITY",
        entity="production_batches",
        entity_id=batch.id,
        before=before,
        after={"actual_qty": str(quantity)},
    )
    db.commit()
    db.refresh(batch)
    return batch


def record_batch_event(
    db: Session,
    batch_id: uuid.UUID,
    event_type: str,
    public_text: str,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Append a public traceability event to a batch.

    The ``batch_events`` table belongs to the QR-traceability milestone and is created by
    its own Alembic migration (0008), which may not have been applied yet. The hook is
    therefore defensive:

    - it writes nothing while the table is not part of the metadata or not present in the
      database yet;
    - it writes nothing when the event type is not one the table accepts (the allowed
      values are read from the table's own CHECK constraint), so this module never breaks
      the traceability table's rules.

    Once the traceability migration is applied the same calls start recording events, with
    no change to the calling code.
    """
    table = Base.metadata.tables.get("batch_events")
    if table is None:
        return False

    columns = set(table.c.keys())
    if not {"batch_id", "event_type", "public_text"}.issubset(columns):
        return False

    allowed = _allowed_event_types(table)
    if allowed is not None and event_type not in allowed:
        return False

    try:
        if not inspect(db.connection()).has_table("batch_events"):
            return False
    except SQLAlchemyError:  # pragma: no cover - defensive
        return False

    values: dict[str, object] = {
        "batch_id": str(batch_id),
        "event_type": event_type,
        "public_text": public_text,
    }
    if "id" in columns:
        values["id"] = str(uuid.uuid4())
    if "event_at" in columns:
        values["event_at"] = datetime.now(UTC)
    if "actor_id" in columns:
        values["actor_id"] = str(actor_id) if actor_id else None

    db.execute(table.insert().values(**values))
    return True


def _allowed_event_types(table) -> set[str] | None:
    """Read the event types the batch_events table accepts from its CHECK constraint.

    Returns None when the table has no such constraint, in which case the caller accepts
    the event type as it is.
    """
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and "event_type" in str(constraint.sqltext):
            return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))
    return None


# ── Quality parameters, checks and decisions (T-032) ──────────────────────────


def create_quality_parameter(
    db: Session,
    data: QualityParameterCreate,
    actor_id: uuid.UUID,
) -> QualityParameter:
    """Register a quality parameter.

    No limit is invented: ``min_value`` and ``max_value`` stay NULL when the sponsor has
    not supplied them, and the parameter is flagged as a placeholder (rules.md section 12).
    """
    existing = db.scalar(select(QualityParameter).where(QualityParameter.code == data.code))
    if existing is not None:
        raise AppError(
            "DUPLICATE_QUALITY_PARAMETER",
            f"Quality parameter '{data.code}' already exists.",
            409,
        )

    minimum = _as_decimal(data.min_value, "min_value") if data.min_value is not None else None
    maximum = _as_decimal(data.max_value, "max_value") if data.max_value is not None else None
    if minimum is not None and maximum is not None and minimum > maximum:
        raise AppError(
            "INVALID_QUALITY_RANGE",
            "The minimum value cannot be greater than the maximum value.",
            400,
        )
    # A parameter that carries limits is no longer a placeholder; one without limits stays
    # marked "to be confirmed with sponsor" (rules.md section 12).
    is_placeholder = data.is_placeholder and minimum is None and maximum is None
    parameter = QualityParameter(
        id=uuid.uuid4(),
        code=data.code,
        name=data.name,
        unit=data.unit,
        min_value=minimum,
        max_value=maximum,
        is_placeholder=is_placeholder,
        is_active=True,
    )
    try:
        db.add(parameter)
        db.flush()
        log_action(
            db,
            actor_id=actor_id,
            action="CREATE_QUALITY_PARAMETER",
            entity="quality_parameters",
            entity_id=parameter.id,
            after={
                "code": parameter.code,
                "unit": parameter.unit,
                "min_value": str(minimum) if minimum is not None else None,
                "max_value": str(maximum) if maximum is not None else None,
                "is_placeholder": parameter.is_placeholder,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_QUALITY_PARAMETER",
            f"Quality parameter '{data.code}' already exists.",
            409,
        ) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(parameter)
    return parameter


def list_quality_parameters(db: Session, include_inactive: bool = False) -> list[QualityParameter]:
    """List quality parameters, active ones only unless inactive ones are requested."""
    stmt = select(QualityParameter).order_by(QualityParameter.code.asc())
    if not include_inactive:
        stmt = stmt.where(QualityParameter.is_active.is_(True))
    return list(db.scalars(stmt).all())


def record_quality_check(
    db: Session,
    batch_id: uuid.UUID,
    parameter_id: uuid.UUID,
    measured_value: Decimal,
    actor_id: uuid.UUID,
    notes: str | None = None,
) -> QualityCheck:
    """Record the measured value of one quality parameter on a batch.

    The verdict is computed from the parameter limits when they exist. A placeholder
    parameter without limits cannot be judged, so ``passed`` stays NULL ("unknown"); it is
    never silently reported as ``True``.
    """
    batch = get_batch(db, batch_id)
    parameter = db.get(QualityParameter, parameter_id)
    if parameter is None:
        raise AppError(
            "QUALITY_PARAMETER_NOT_FOUND", f"Quality parameter '{parameter_id}' was not found.", 404
        )
    if not parameter.is_active:
        raise AppError(
            "QUALITY_PARAMETER_INACTIVE",
            f"Quality parameter '{parameter.code}' is inactive and cannot be checked.",
            400,
        )

    value = _as_decimal(measured_value, "measured_value")
    minimum = parameter.min_value
    maximum = parameter.max_value
    if minimum is None and maximum is None:
        passed: bool | None = None
    else:
        passed = True
        if minimum is not None and value < Decimal(str(minimum)):
            passed = False
        if maximum is not None and value > Decimal(str(maximum)):
            passed = False

    check = QualityCheck(
        id=uuid.uuid4(),
        batch_id=batch.id,
        parameter_id=parameter.id,
        measured_value=value,
        passed=passed,
        checked_by=actor_id,
        notes=notes,
    )
    db.add(check)
    db.flush()
    log_action(
        db,
        actor_id=actor_id,
        action="RECORD_QUALITY_CHECK",
        entity="quality_checks",
        entity_id=check.id,
        after={
            "batch_id": str(batch.id),
            "parameter_code": parameter.code,
            "measured_value": str(value),
            "passed": passed,
        },
    )
    record_batch_event(
        db,
        batch.id,
        "quality_checked",
        f"Quality check recorded for parameter {parameter.code}.",
        actor_id=actor_id,
    )
    db.commit()
    db.refresh(check)
    return check


def get_batch_quality_status(db: Session, batch_id: uuid.UUID) -> BatchQualityStatus:
    """Return the latest result per active quality parameter and the release roll-up.

    ``released_allowed`` is True only when every active parameter has a passing check and
    no check failed. A parameter that was checked but has no verdict (placeholder without
    limits) still counts as outstanding, because "no limit" is not a pass.
    """
    batch = get_batch(db, batch_id)
    parameters = list(
        db.scalars(
            select(QualityParameter)
            .where(QualityParameter.is_active.is_(True))
            .order_by(QualityParameter.code.asc())
        ).all()
    )
    checks = list(
        db.scalars(
            select(QualityCheck)
            .where(QualityCheck.batch_id == batch.id)
            .order_by(QualityCheck.checked_at.asc(), QualityCheck.id.asc())
        ).all()
    )

    latest: dict[uuid.UUID, QualityCheck] = {}
    for check in checks:
        latest[check.parameter_id] = check

    results: list[QualityParameterStatus] = []
    outstanding: list[str] = []
    failed: list[str] = []

    for parameter in parameters:
        check = latest.get(parameter.id)
        if check is None:
            outstanding.append(parameter.code)
        elif check.passed is False:
            failed.append(parameter.code)
        elif check.passed is None:
            outstanding.append(parameter.code)
        results.append(
            QualityParameterStatus(
                parameter_id=parameter.id,
                code=parameter.code,
                name=parameter.name,
                unit=parameter.unit,
                is_placeholder=parameter.is_placeholder,
                checked=check is not None,
                measured_value=check.measured_value if check else None,
                passed=check.passed if check else None,
                checked_at=check.checked_at if check else None,
            )
        )

    return BatchQualityStatus(
        batch_id=batch.id,
        batch_status=batch.status,
        released_allowed=not outstanding and not failed,
        parameters=results,
        outstanding=outstanding,
        failed=failed,
    )


def record_quality_decision(
    db: Session,
    batch_id: uuid.UUID,
    decision: str,
    remarks: str | None,
    actor_id: uuid.UUID,
) -> QualityDecision:
    """Record the release / hold / reject decision on a batch and move the batch.

    A ``released`` decision is refused while a quality check is outstanding or failed.
    The batch transition is validated against the shared state machine, so an illegal
    transition (for example releasing a batch that is still in ``draft``) is rejected.
    """
    normalized = (decision or "").strip().lower()
    if normalized not in VALID_QUALITY_DECISIONS:
        raise AppError(
            "INVALID_QUALITY_DECISION",
            f"Invalid quality decision '{decision}'. Must be one of: "
            f"{sorted(VALID_QUALITY_DECISIONS)}.",
            400,
        )

    batch = get_batch(db, batch_id)

    if normalized == BatchStatus.RELEASED.value:
        status = get_batch_quality_status(db, batch_id)
        if not status.released_allowed:
            raise AppError(
                "QUALITY_RELEASE_BLOCKED",
                "The batch cannot be released: every active quality parameter needs a "
                "passing check first.",
                400,
                details={
                    "outstanding": status.outstanding,
                    "failed": status.failed,
                    "batch_status": batch.status,
                },
            )

    # Validate the transition before writing anything, so an illegal move changes nothing.
    validate_batch_transition(batch.status, normalized)

    try:
        row = QualityDecision(
            id=uuid.uuid4(),
            batch_id=batch.id,
            decision=normalized,
            remarks=remarks,
            decided_by=actor_id,
        )
        db.add(row)
        db.flush()

        before = {"status": batch.status}
        batch.status = normalized
        _sync_stages_for_status(db, batch.id, normalized)

        log_action(
            db,
            actor_id=actor_id,
            action="RECORD_QUALITY_DECISION",
            entity="quality_decisions",
            entity_id=row.id,
            before=before,
            after={"decision": normalized, "batch_id": str(batch.id), "remarks": remarks},
        )
        event_type = STATUS_EVENT_TYPES.get(normalized)
        if event_type is not None:
            record_batch_event(
                db,
                batch.id,
                event_type,
                f"Quality decision: {normalized}.",
                actor_id=actor_id,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    return row


# ── Release to stock (T-034) ──────────────────────────────────────────────────


def release_batch_to_stock(
    db: Session,
    batch_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> InventoryBalance:
    """Turn the produced quantity of a released batch into sellable stock.

    One transaction covers the whole operation:
    1. the batch must already be ``released`` - the status is set by the quality decision
       (T-032) or by an audited state transition. A draft, in-production, quality-pending,
       held or rejected batch can therefore never create stock (B1);
    2. the quality roll-up must still allow the release;
    3. the ``InventoryBalance`` row for (warehouse, batch) is created or updated;
    4. a ``StockMovement`` of type ``receipt`` records the produced quantity (B5).

    Booking the same batch into the same warehouse twice is refused, so the produced
    quantity can never be counted twice.
    """
    batch = get_batch(db, batch_id)
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise AppError("WAREHOUSE_NOT_FOUND", f"Warehouse '{warehouse_id}' was not found.", 404)
    if not warehouse.is_active:
        raise AppError("WAREHOUSE_INACTIVE", f"Warehouse '{warehouse.code}' is inactive.", 400)

    if batch.status != BatchStatus.RELEASED.value:
        raise AppError(
            "BATCH_NOT_RELEASABLE",
            f"A batch in status '{batch.status}' cannot create stock: only a released "
            "batch may be booked into a warehouse.",
            400,
            details={"batch_status": batch.status},
        )

    quality = get_batch_quality_status(db, batch_id)
    if not quality.released_allowed:
        raise AppError(
            "QUALITY_RELEASE_BLOCKED",
            "The batch cannot be released: every active quality parameter needs a "
            "passing check first.",
            400,
            details={"outstanding": quality.outstanding, "failed": quality.failed},
        )

    produced = batch.actual_qty
    if produced is None or Decimal(str(produced)) <= Decimal("0"):
        raise AppError(
            "MISSING_ACTUAL_QUANTITY",
            "The produced quantity of the batch is unknown; record it before releasing.",
            400,
        )
    quantity = Decimal(str(produced))

    try:
        balance = db.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse.id,
                InventoryBalance.batch_id == batch.id,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if balance is None:
            balance = InventoryBalance(
                id=uuid.uuid4(),
                warehouse_id=warehouse.id,
                batch_id=batch.id,
                on_hand=Decimal("0"),
                reserved=Decimal("0"),
            )
            db.add(balance)
            db.flush()

        before_on_hand = balance.on_hand
        balance.on_hand = balance.on_hand + quantity

        movement = StockMovement(
            id=uuid.uuid4(),
            warehouse_id=warehouse.id,
            batch_id=batch.id,
            balance_id=balance.id,
            movement_type="receipt",
            quantity=quantity,
            reference_type="batch_release",
            reference_id=str(batch.id),
            operation_key=f"batch_release:{batch.id}:{warehouse.id}",
            notes=f"Released batch {batch.batch_code} into {warehouse.name}.",
            actor_id=actor_id,
        )
        db.add(movement)

        log_action(
            db,
            actor_id=actor_id,
            action="RELEASE_BATCH_TO_STOCK",
            entity="inventory_balances",
            entity_id=balance.id,
            before={"on_hand": str(before_on_hand), "reserved": str(balance.reserved)},
            after={
                "on_hand": str(balance.on_hand),
                "reserved": str(balance.reserved),
                "batch_id": str(batch.id),
                "warehouse_id": str(warehouse.id),
                "quantity": str(quantity),
                "batch_status": batch.status,
            },
        )
        record_batch_event(
            db,
            batch.id,
            "released",
            f"Batch {batch.batch_code} released into {warehouse.name}.",
            actor_id=actor_id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "BATCH_STOCK_ALREADY_BOOKED",
            "This batch is already booked into that warehouse.",
            409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(balance)
    return balance
