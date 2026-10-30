"""Production HTTP endpoints under /api/v1/production (Milestone M3 / T-031, T-032, T-034).

RBAC policy:
- owner and factory may write (plans, batches, quality checks, decisions, releases);
- owner, factory and warehouse may read.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.auth.models import User
from app.core.db import get_db
from app.production import service
from app.production.schemas import (
    BatchCreate,
    BatchQualityStatus,
    BatchQuantityRequest,
    BatchRead,
    BatchReleaseRequest,
    BatchReleaseResponse,
    BatchTransitionRequest,
    ProductionPlanCreate,
    ProductionPlanRead,
    ProductionStageRead,
    QualityCheckCreate,
    QualityCheckRead,
    QualityDecisionCreate,
    QualityDecisionRead,
    QualityParameterCreate,
    QualityParameterRead,
)
from app.production.state_machine import ALLOWED_TRANSITIONS

router = APIRouter(prefix="/api/v1/production", tags=["production"])

# Singletons to prevent FastAPI dependency re-instantiation / Ruff B008 warnings
require_production_writer = require_roles("owner", "factory")
require_production_reader = require_roles("owner", "factory", "warehouse")


def _batch_read(batch, db: Session) -> BatchRead:
    """Build the read model of a batch, including its processing stages."""
    read = BatchRead.model_validate(batch)
    read.stages = [
        ProductionStageRead.model_validate(stage) for stage in service.list_stages(db, batch.id)
    ]
    return read


@router.post(
    "/plans",
    response_model=ProductionPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a production plan",
)
def create_production_plan(
    payload: ProductionPlanCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> ProductionPlanRead:
    """Plan a production run for a product variant. Owner and factory only."""
    plan = service.create_production_plan(
        db,
        variant_id=payload.variant_id,
        planned_qty=payload.planned_qty,
        planned_start=payload.planned_start,
        planned_end=payload.planned_end,
        actor_id=current_user.id,
        notes=payload.notes,
    )
    return ProductionPlanRead.model_validate(plan)


@router.get(
    "/plans",
    response_model=list[ProductionPlanRead],
    summary="List production plans",
)
def list_production_plans(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_production_reader)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    variant_id: uuid.UUID | None = Query(None, description="Filter by product variant UUID"),
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
) -> list[ProductionPlanRead]:
    """List production plans, newest first. Owner, factory and warehouse may read."""
    plans = service.list_plans(
        db,
        status=status_filter,
        variant_id=variant_id,
        limit=limit,
        offset=offset,
    )
    return [ProductionPlanRead.model_validate(p) for p in plans]


@router.post(
    "/batches",
    response_model=BatchRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a production batch from a plan (status: draft)",
)
def create_production_batch(
    payload: BatchCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> BatchRead:
    """Create a batch in status 'draft' with its processing stages. Owner and factory only."""
    batch = service.create_batch_from_plan(
        db,
        plan_id=payload.plan_id,
        batch_code=payload.batch_code,
        manufactured_at=payload.manufactured_at,
        expiry_date=payload.expiry_date,
        actor_id=current_user.id,
        actual_qty=payload.actual_qty,
    )
    return _batch_read(batch, db)


@router.get(
    "/batches",
    response_model=list[BatchRead],
    summary="List production batches",
)
def list_production_batches(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_production_reader)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    variant_id: uuid.UUID | None = Query(None, description="Filter by product variant UUID"),
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
) -> list[BatchRead]:
    """List production batches, newest first. Owner, factory and warehouse may read."""
    batches = service.list_batches(
        db,
        status=status_filter,
        variant_id=variant_id,
        limit=limit,
        offset=offset,
    )
    return [_batch_read(b, db) for b in batches]


@router.get(
    "/batches/{batch_id}",
    response_model=BatchRead,
    summary="Get one production batch",
)
def get_production_batch(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_production_reader)],
) -> BatchRead:
    """Get one batch with its processing stages."""
    batch = service.get_batch(db, batch_id)
    return _batch_read(batch, db)


@router.post(
    "/batches/{batch_id}/transition",
    response_model=BatchRead,
    summary="Move a batch to another lifecycle status",
)
def advance_production_batch(
    batch_id: uuid.UUID,
    payload: BatchTransitionRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> BatchRead:
    """Move a batch along the canonical state machine.

    Illegal transitions are rejected with a 400 error listing the allowed ones.
    """
    batch = service.advance_batch(
        db,
        batch_id=batch_id,
        to_status=payload.to_status,
        actor_id=current_user.id,
        notes=payload.notes,
    )
    return _batch_read(batch, db)


@router.post(
    "/batches/{batch_id}/quantity",
    response_model=BatchRead,
    summary="Record the quantity a batch actually produced",
)
def set_production_batch_quantity(
    batch_id: uuid.UUID,
    payload: BatchQuantityRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> BatchRead:
    """Record the produced quantity, which becomes the sellable stock on release."""
    batch = service.set_batch_actual_quantity(
        db, batch_id=batch_id, actual_qty=payload.actual_qty, actor_id=current_user.id
    )
    return _batch_read(batch, db)


@router.post(
    "/batches/{batch_id}/release",
    response_model=BatchReleaseResponse,
    summary="Release a batch and create sellable stock (T-034)",
)
def release_production_batch(
    batch_id: uuid.UUID,
    payload: BatchReleaseRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> BatchReleaseResponse:
    """Book a released batch into a warehouse as sellable stock.

    The batch must already be 'released' - the quality decision (or an audited state
    transition) sets that status - and every active quality parameter must have a passing
    check. The produced quantity is written to the inventory balance and recorded as a
    stock movement (B5). A batch that is not released never creates stock (B1).
    """
    balance = service.release_batch_to_stock(
        db,
        batch_id=batch_id,
        warehouse_id=payload.warehouse_id,
        actor_id=current_user.id,
    )
    return BatchReleaseResponse(
        balance_id=balance.id,
        warehouse_id=balance.warehouse_id,
        batch_id=balance.batch_id,
        batch_status=service.get_batch(db, batch_id).status,
        on_hand=balance.on_hand,
        reserved=balance.reserved,
        available=balance.available,
    )


@router.post(
    "/batches/{batch_id}/quality-checks",
    response_model=QualityCheckRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a quality check on a batch",
)
def record_quality_check(
    batch_id: uuid.UUID,
    payload: QualityCheckCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> QualityCheckRead:
    """Record one measured value of a quality parameter.

    A placeholder parameter without limits yields 'passed = null' (unknown), never true.
    """
    check = service.record_quality_check(
        db,
        batch_id=batch_id,
        parameter_id=payload.parameter_id,
        measured_value=payload.measured_value,
        actor_id=current_user.id,
        notes=payload.notes,
    )
    return QualityCheckRead.model_validate(check)


@router.get(
    "/batches/{batch_id}/quality-status",
    response_model=BatchQualityStatus,
    summary="Get the quality roll-up of a batch",
)
def get_quality_status(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_production_reader)],
) -> BatchQualityStatus:
    """Return the latest result per active parameter and whether the batch may be released."""
    return service.get_batch_quality_status(db, batch_id)


@router.post(
    "/batches/{batch_id}/quality-decisions",
    response_model=QualityDecisionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record the release / hold / reject decision on a batch",
)
def record_quality_decision(
    batch_id: uuid.UUID,
    payload: QualityDecisionCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> QualityDecisionRead:
    """Record the quality decision and move the batch accordingly.

    A 'released' decision is refused while a quality check is outstanding or failed.
    """
    decision = service.record_quality_decision(
        db,
        batch_id=batch_id,
        decision=payload.decision,
        remarks=payload.remarks,
        actor_id=current_user.id,
    )
    return QualityDecisionRead.model_validate(decision)


@router.post(
    "/quality-parameters",
    response_model=QualityParameterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a quality parameter (limits stay optional)",
)
def create_quality_parameter(
    payload: QualityParameterCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_production_writer)],
) -> QualityParameterRead:
    """Register a quality parameter. No limit is invented: the limits stay optional and
    the parameter is marked as a placeholder until the sponsor confirms them.
    """
    parameter = service.create_quality_parameter(db, payload, actor_id=current_user.id)
    return QualityParameterRead.model_validate(parameter)


@router.get(
    "/quality-parameters",
    response_model=list[QualityParameterRead],
    summary="List quality parameters",
)
def list_quality_parameters(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_production_reader)],
    include_inactive: bool = Query(False, description="Include deactivated parameters"),
) -> list[QualityParameterRead]:
    """List quality parameters with their (possibly empty) limits."""
    parameters = service.list_quality_parameters(db, include_inactive=include_inactive)
    return [QualityParameterRead.model_validate(p) for p in parameters]


@router.get(
    "/state-machine",
    response_model=dict[str, list[str]],
    summary="Show the allowed batch status transitions",
)
def get_batch_state_machine(
    _current_user: Annotated[User, Depends(require_production_reader)],
) -> dict[str, list[str]]:
    """Return the single transition table used to validate every batch status change."""
    return {status: sorted(targets) for status, targets in ALLOWED_TRANSITIONS.items()}
